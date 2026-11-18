from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import database
from app.core.config import settings
from app.models.auth import User
from app.models.group_chat import GroupTodo, ProjectGroupAiRun, ProjectGroupMessage
from app.services import group_assistant, group_chat


@pytest.fixture
def group_db(tmp_path: Path):
    original = settings.db_path, settings.database_url
    path = tmp_path / "group-assistant.sqlite3"
    database.reset_database(path)
    settings.db_path = path
    settings.database_url = f"sqlite:///{path}"
    database.initialize_database()
    db = database.SessionLocal()
    try:
        yield db
    finally:
        db.close()
        database.reset_database(path)
        settings.db_path, settings.database_url = original


def _user(db: Session, user_id: str, display_name: str) -> User:
    user = User(
        id=user_id,
        username=user_id,
        email=f"{user_id}@example.com",
        display_name=display_name,
        password_hash="unused",
        role="user",
        status="active",
        first_login=False,
        created_at=datetime(2026, 4, 1, 9, 0, tzinfo=timezone.utc),
    )
    db.add(user)
    db.flush()
    return user


def _setup(db: Session):
    owner = _user(db, "u_owner", "张组长")
    manager = _user(db, "u_manager", "李管理")
    member = _user(db, "u_member", "王组员")
    group = group_chat.create_group(db, owner=owner, name="A 型机架项目")
    group_chat.add_member(db, actor=owner, group_id=group.id, user_id=manager.id, role="manager")
    group_chat.add_member(db, actor=owner, group_id=group.id, user_id=member.id)
    group_chat.post_message(
        db, group=group, sender=member, kind="text", content="机架图纸周三前要确认，谁来跟？"
    )
    db.commit()
    return group, owner, manager, member


class FakeReply:
    def __init__(self, text: str, model: str = "fake-model") -> None:
        self.text = text
        self.model = model


def _fake(text: str):
    captured: dict[str, object] = {}

    async def complete(messages):
        captured["messages"] = messages
        return FakeReply(text)

    return complete, captured


def _analysis(**overrides) -> str:
    payload = {
        "summary": "周三前确认机架图纸",
        "todos": [
            {
                "title": "确认机架图纸公差",
                "detail": "重点看安装孔位",
                "assignee": "王组员",
                "priority": "high",
                "due": "2026-11-20",
                "confidence": 0.9,
            },
            {
                "title": "联系供应商补发样品",
                "assignee": "李管理",
                "priority": "normal",
                "due": "",
            },
        ],
        "decisions": ["图纸版本以 v3 为准"],
        "risks": ["供应商交期不稳"],
    }
    payload.update(overrides)
    return json.dumps(payload, ensure_ascii=False)


@pytest.mark.asyncio
async def test_analysis_creates_todos_for_matched_members(group_db: Session):
    group, owner, manager, member = _setup(group_db)
    complete, captured = _fake(_analysis())

    run = await group_assistant.analyze_group(group_db, group=group, complete=complete)

    assert run.status == "done"
    assert run.todos_created == 2
    todos = list(
        group_db.scalars(select(GroupTodo).order_by(GroupTodo.created_at))
    )
    assert {todo.assignee_user_id for todo in todos} == {member.id, manager.id}
    first = next(todo for todo in todos if todo.title == "确认机架图纸公差")
    assert first.priority == "high"
    assert first.status == "pending"
    assert first.origin == "ai"
    assert first.due_at is not None and first.due_at.strftime("%Y-%m-%d") == "2026-11-20"
    assert first.confidence == pytest.approx(0.9)

    # 上下文含成员名单与聊天内容，且系统提示词已注入
    messages = captured["messages"]
    assert messages[0]["role"] == "system"
    assert "机架图纸周三前要确认" in messages[1]["content"]
    assert "王组员" in messages[1]["content"]

    ai_messages = [
        message
        for message in group_db.scalars(select(ProjectGroupMessage))
        if message.kind == "ai"
    ]
    assert len(ai_messages) == 1
    assert "@王组员 确认机架图纸公差" in ai_messages[0].content
    assert json.loads(ai_messages[0].parts_json or "[]")[0]["type"] == "group_analysis"


@pytest.mark.asyncio
async def test_unknown_assignee_falls_back_to_group_owner(group_db: Session):
    group, owner, *_ = _setup(group_db)
    complete, _ = _fake(_analysis(todos=[{"title": "补一份来料检验记录", "assignee": "老周"}]))

    run = await group_assistant.analyze_group(group_db, group=group, complete=complete)

    todo = group_db.scalar(select(GroupTodo).where(GroupTodo.ai_run_id == run.id))
    assert todo.assignee_user_id == owner.id
    assert "老周" in todo.detail


@pytest.mark.asyncio
async def test_repeated_analysis_does_not_duplicate_open_todos(group_db: Session):
    group, _, _, _ = _setup(group_db)
    first, _ = _fake(_analysis())
    await group_assistant.analyze_group(group_db, group=group, complete=first)

    second, _ = _fake(_analysis())
    run = await group_assistant.analyze_group(group_db, group=group, complete=second)

    assert run.todos_created == 0
    assert len(list(group_db.scalars(select(GroupTodo)))) == 2


@pytest.mark.asyncio
async def test_unparsable_model_output_returns_failed_run_with_reason(group_db: Session):
    """失败要留痕：返回 status=failed 的 run，而不是抛异常把记录回滚掉。"""
    group, *_ = _setup(group_db)
    complete, _ = _fake("我觉得周三能做完，你看呢？")

    run = await group_assistant.analyze_group(group_db, group=group, complete=complete)

    assert run.status == "failed"
    assert "JSON" in (run.error or "")
    assert list(group_db.scalars(select(GroupTodo))) == []


@pytest.mark.asyncio
async def test_unavailable_model_is_reported_without_recording(group_db: Session):
    group, *_ = _setup(group_db)

    async def complete(messages):
        raise group_assistant.GroupAssistantUnavailable("尚未配置对话模型端点")

    with pytest.raises(group_assistant.GroupAssistantUnavailable):
        await group_assistant.analyze_group(group_db, group=group, complete=complete)

    # 没有模型不算一次失败的分析，不写脏记录
    assert list(group_db.scalars(select(ProjectGroupAiRun))) == []
    assert list(group_db.scalars(select(GroupTodo))) == []


def test_parse_analysis_accepts_code_fence_and_bad_priority():
    parsed = group_assistant.parse_analysis(
        "```json\n"
        + json.dumps(
            {"summary": "s", "todos": [{"title": "t", "priority": "紧急"}]},
            ensure_ascii=False,
        )
        + "\n```"
    )
    assert parsed.todos[0].title == "t"
    assert parsed.todos[0].priority == "normal"


def test_parse_analysis_drops_todos_without_title():
    parsed = group_assistant.parse_analysis(
        json.dumps({"todos": [{"title": "  "}, {"title": "留下一"}]}, ensure_ascii=False)
    )
    assert [item.title for item in parsed.todos] == ["留下一"]


def test_assignee_can_close_own_todo_but_not_others(group_db: Session):
    group, owner, manager, member = _setup(group_db)
    other = _user(group_db, "u_other", "孙外员")
    todo = GroupTodo(
        id="gtodo_1",
        group_id=group.id,
        assignee_user_id=member.id,
        origin="ai",
        title="确认机架图纸公差",
        status="pending",
        priority="normal",
        created_at=datetime(2026, 4, 1, 10, 0, tzinfo=timezone.utc),
        updated_at=datetime(2026, 4, 1, 10, 0, tzinfo=timezone.utc),
    )
    group_db.add(todo)
    group_db.flush()

    with pytest.raises(group_chat.GroupPermissionDenied):
        group_assistant.set_todo_status(
            group_db, todo=todo, actor=other, status="done"
        )

    group_assistant.set_todo_status(
        group_db, todo=todo, actor=owner, status="done"
    )
    assert todo.status == "done"
    assert todo.completed_at is not None

    with pytest.raises(group_chat.InvalidGroupInput):
        group_assistant.set_todo_status(
            group_db, todo=todo, actor=owner, status="随便"
        )


def test_completed_todo_is_not_recreated_within_window(group_db: Session):
    """刚做完的同名待办不要立刻又冒出来（老聊天记录还在，AI 会重复发现）。"""
    from datetime import timedelta

    from app.core.security import now as now_fn

    group, owner, _, member = _setup(group_db)
    stamp = now_fn()
    group_db.add(
        GroupTodo(
            id="gtodo_done_recent",
            group_id=group.id,
            assignee_user_id=owner.id,
            origin="ai",
            title="确认机架图纸公差",
            status="done",
            priority="high",
            created_at=stamp - timedelta(days=1),
            updated_at=stamp,
            completed_at=stamp,
        )
    )
    group_db.flush()

    complete, _ = _fake(_analysis(todos=[{"title": "确认机架图纸公差", "assignee": "管理员"}]))
    import asyncio

    run = asyncio.run(group_assistant.analyze_group(group_db, group=group, complete=complete))
    assert run.todos_created == 0


def test_long_finished_todo_can_come_back(group_db: Session):
    from datetime import timedelta

    from app.core.security import now as now_fn

    group, owner, _, member = _setup(group_db)
    stamp = now_fn()
    group_db.add(
        GroupTodo(
            id="gtodo_done_old",
            group_id=group.id,
            assignee_user_id=owner.id,
            origin="ai",
            title="确认机架图纸公差",
            status="done",
            priority="high",
            created_at=stamp - timedelta(days=60),
            updated_at=stamp - timedelta(days=30),
            completed_at=stamp - timedelta(days=30),
        )
    )
    group_db.flush()

    complete, _ = _fake(_analysis(todos=[{"title": "确认机架图纸公差", "assignee": "系统管理员"}]))
    import asyncio

    run = asyncio.run(group_assistant.analyze_group(group_db, group=group, complete=complete))
    assert run.todos_created == 1


def test_context_includes_todo_overview_so_ai_can_report_progress(group_db: Session):
    from datetime import timedelta

    from app.core.security import now as now_fn

    group, owner, _, member = _setup(group_db)
    stamp = now_fn()
    group_db.add(
        GroupTodo(
            id="gtodo_progress_open",
            group_id=group.id,
            assignee_user_id=member.id,
            origin="ai",
            title="补齐来料检验记录",
            status="pending",
            priority="high",
            due_at=stamp - timedelta(days=1),
            created_at=stamp,
            updated_at=stamp,
        )
    )
    group_db.add(
        GroupTodo(
            id="gtodo_progress_done",
            group_id=group.id,
            assignee_user_id=owner.id,
            origin="ai",
            title="确认机架图纸公差",
            status="done",
            priority="normal",
            created_at=stamp,
            updated_at=stamp,
            completed_at=stamp,
        )
    )
    group_db.flush()

    context, _ = group_assistant.build_context(group_db, group=group)

    assert "待办" in context
    assert "补齐来料检验记录" in context
    assert "王组员" in context
    assert "已完成" in context or "done" in context
    assert "逾期" in context


@pytest.mark.asyncio
async def test_reply_from_model_is_posted_instead_of_canned_summary(group_db: Session):
    """模型可以给一段面向群里的回复，直接贴出去，不要只发模板句。"""
    group, *_ = _setup(group_db)
    payload = {
        "reply": "目前 2 项待办里 1 项已确认图纸公差，还有 1 项来料检验记录没交，李管理那边卡着。",
        "summary": "进度询问",
        "todos": [],
        "decisions": [],
        "risks": ["来料检验记录未提交"],
    }
    complete, _ = _fake(json.dumps(payload, ensure_ascii=False))

    await group_assistant.analyze_group(group_db, group=group, complete=complete)

    ai = [
        m for m in group_db.scalars(select(ProjectGroupMessage)) if m.kind == "ai"
    ]
    assert len(ai) == 1
    assert "1 项已确认图纸公差" in ai[0].content
    assert "这轮讨论里没有新的可执行待办" not in ai[0].content


def test_should_auto_reply_detects_questions_and_mentions():
    assert group_assistant.should_auto_reply("@AI 这周要做什么")
    assert group_assistant.should_auto_reply("不知道项目进度怎么样")
    assert group_assistant.should_auto_reply("现在进度如何？")
    assert group_assistant.should_auto_reply("有哪些待办还没做完")
    # 普通陈述不该触发，避免刷屏
    assert not group_assistant.should_auto_reply("好的，收到")
    assert not group_assistant.should_auto_reply("图纸我下午传")


def test_focus_message_is_put_at_the_end_for_the_model(group_db: Session):
    """必须明确告诉模型「要回答哪一句」，否则它会复述整段背景。"""
    group, *_ = _setup(group_db)
    context, _ = group_assistant.build_context(
        group_db, group=group, focus_message="你们谁负责这个项目的？", focus_sender="管理员"
    )
    tail = context[-400:]
    assert "需要你现在回答" in tail
    assert "你们谁负责这个项目的？" in tail
    assert "管理员" in tail


def test_context_without_focus_has_no_focus_block(group_db: Session):
    group, *_ = _setup(group_db)
    context, _ = group_assistant.build_context(group_db, group=group)
    assert "需要你现在回答" not in context


@pytest.mark.asyncio
async def test_focus_question_reaches_the_model_payload(group_db: Session):
    group, *_ = _setup(group_db)
    captured: dict = {}

    async def complete(messages):
        captured["messages"] = messages
        return FakeReply(_analysis())

    await group_assistant.analyze_group(
        group_db,
        group=group,
        complete=complete,
        focus_message="你们谁负责这个项目的？",
        focus_sender="管理员",
    )

    user_turn = captured["messages"][-1]["content"]
    assert user_turn.rstrip().endswith("不要重复你之前说过的内容。")
    assert "你们谁负责这个项目的？" in user_turn


@pytest.mark.asyncio
async def test_different_questions_produce_different_prompts(group_db: Session):
    group, *_ = _setup(group_db)
    prompts = []

    async def complete(messages):
        prompts.append(messages[-1]["content"])
        return FakeReply(_analysis())

    await group_assistant.analyze_group(
        group_db, group=group, complete=complete, focus_message="还差什么没做？", focus_sender="管理员"
    )
    await group_assistant.analyze_group(
        group_db, group=group, complete=complete, focus_message="谁在跟这个项目？", focus_sender="管理员"
    )

    assert prompts[0] != prompts[1]
    assert "还差什么没做？" in prompts[0]
    assert "谁在跟这个项目？" in prompts[1]


@pytest.mark.asyncio
async def test_model_memory_is_saved_and_reused(group_db: Session):
    group, owner, _, member = _setup(group_db)
    payload = {
        "memory": "项目：A 型机架试产。已定：图纸以 v3 为准。分工：王组员确认公差，李管理联系供应商。未决：排产时间未定。",
        "reply": "记下了",
        "summary": "分工确认",
        "todos": [],
        "decisions": [],
        "risks": [],
    }
    complete, _ = _fake(json.dumps(payload, ensure_ascii=False))

    await group_assistant.analyze_group(group_db, group=group, complete=complete)

    memory = group_assistant.load_memory(group_db, group.id)
    assert memory is not None
    assert "图纸以 v3 为准" in memory.summary
    assert memory.covered_message_count >= 1

    context, _ = group_assistant.build_context(group_db, group=group)
    assert "【已有群纪要" in context
    assert "图纸以 v3 为准" in context


@pytest.mark.asyncio
async def test_memory_keeps_old_facts_out_of_raw_history(group_db: Session):
    """有了纪要之后，早先的原始消息不再重复塞进上下文。"""
    group, owner, _, member = _setup(group_db)
    first = group_chat.post_message(
        group_db, group=group, sender=member, kind="text", content="早期细节：夹具用旧的 2 号"
    )
    group_db.flush()

    payload = {
        "memory": "早期结论：夹具暂用 2 号。",
        "reply": "",
        "summary": "",
        "todos": [],
        "decisions": [],
        "risks": [],
    }
    complete, _ = _fake(json.dumps(payload, ensure_ascii=False))
    await group_assistant.analyze_group(group_db, group=group, complete=complete)

    # 纪要之后来一条新消息
    group_chat.post_message(
        group_db, group=group, sender=owner, kind="text", content="新消息：夹具改 3 号"
    )
    group_db.flush()

    context, _ = group_assistant.build_context(group_db, group=group)
    assert "早期结论：夹具暂用 2 号" in context      # 来自纪要
    assert "新消息：夹具改 3 号" in context


@pytest.mark.asyncio
async def test_memory_coverage_accumulates_across_runs(group_db: Session):
    group, owner, _, member = _setup(group_db)
    payload = json.dumps(
        {"memory": "纪要 v1", "reply": "", "summary": "", "todos": [], "decisions": [], "risks": []},
        ensure_ascii=False,
    )
    complete, _ = _fake(payload)
    await group_assistant.analyze_group(group_db, group=group, complete=complete)
    first = group_assistant.load_memory(group_db, group.id).covered_message_count
    assert first >= 1

    group_chat.post_message(group_db, group=group, sender=owner, kind="text", content="新消息一")
    group_chat.post_message(group_db, group=group, sender=member, kind="text", content="新消息二")
    group_db.flush()

    payload2 = json.dumps(
        {"memory": "纪要 v2", "reply": "", "summary": "", "todos": [], "decisions": [], "risks": []},
        ensure_ascii=False,
    )
    complete2, _ = _fake(payload2)
    await group_assistant.analyze_group(group_db, group=group, complete=complete2)

    memory = group_assistant.load_memory(group_db, group.id)
    assert memory.summary == "纪要 v2"
    # 第二轮会把「上一轮的 AI 消息 + 两条新消息」一起覆盖，所以等于当前的存量消息数；
    # 关键是它比第一轮多，而不是每次只记这一轮。
    total = len(list(group_db.scalars(select(ProjectGroupMessage))))
    # -1：这次运行自己刚发出去的那条 AI 消息不在覆盖范围内
    assert memory.covered_message_count == total - 1
    assert memory.covered_message_count > first


def test_task_assignment_sentences_trigger_reply():
    """安排事情的陈述句必须触发——否则等于没人接这件事。"""
    assert group_assistant.should_auto_reply("中天客户又要下单一批订单，销售去对接一下")
    assert group_assistant.should_auto_reply("需要采购 200 件铝型材，明天到货")
    assert group_assistant.should_auto_reply("这批货周五必须发出去")
    assert group_assistant.should_auto_reply("孙质检把这批检验一下")
    assert group_assistant.should_auto_reply("包装木箱快没了，让仓管补一下")


def test_small_talk_still_does_not_trigger():
    for text in ("好的收到", "嗯", "哈哈", "收到", ""):
        assert not group_assistant.should_auto_reply(text), text


def test_questions_still_trigger():
    assert group_assistant.should_auto_reply("最新的销售订单谁提交的？")
    assert group_assistant.should_auto_reply("现在进度怎么样")


@pytest.mark.asyncio
async def test_assignment_creates_todo_for_right_role(group_db: Session):
    """安排任务的陈述句要让 AI 产出待办并派给对的人。"""
    group, owner, manager, member = _setup(group_db)
    payload = {
        "reply": "已记录，派给王组员跟进中天客户的这批订单。",
        "summary": "中天客户追加订单",
        "todos": [{
            "title": "对接中天客户新订单",
            "detail": "确认数量与交期",
            "assignee": "王组员",
            "priority": "high",
            "due": "2026-11-20",
        }],
        "decisions": [],
        "risks": [],
        "memory": "中天客户追加一批订单，由王组员对接。",
    }
    complete, _ = _fake(json.dumps(payload, ensure_ascii=False))
    await group_assistant.analyze_group(group_db, group=group, complete=complete)

    todos = list(group_db.scalars(select(GroupTodo)))
    assert len(todos) == 1
    assert todos[0].assignee_user_id == member.id
    assert "中天" in todos[0].title
    assert todos[0].status == "pending"


@pytest.mark.asyncio
async def test_watch_request_creates_subscription_for_speaker(
    group_db: Session, monkeypatch
) -> None:
    """群里说「帮我盯着 SO-1」，AI 助手要真的把订阅建在那个人名下。"""
    from app.models.doc_watch import DocWatch
    from app.services import doc_watch

    group, owner, manager, member = _setup(group_db)
    group_chat.post_message(
        group_db, group=group, sender=manager, kind="text",
        content="帮我盯着 SO-2026-08-0004，有推进告诉我",
    )
    group_db.commit()

    calls: list[dict] = []

    def fake_trace(doc_no: str) -> dict:
        calls.append({"doc_no": doc_no})
        return {
            "环节数": 4, "已完成数": 1, "当前环节": "销售订单",
            "steps": [{"环节": "销售订单", "面板编码": "SO_ORDER", "单号": doc_no, "单据状态": "已审核"}],
        }

    monkeypatch.setattr(doc_watch, "_default_trace", fake_trace)
    monkeypatch.setattr(
        "app.services.mes_user_client.can_query", lambda panel, uid: (True, "")
    )

    complete, _ = _fake(
        _analysis(watches=[{"doc_no": "SO-2026-08-0004", "step": "", "note": "跟交期"}])
    )
    run = await group_assistant.analyze_group(
        group_db, group=group, complete=complete, focus_sender="李管理"
    )

    assert run.status == "done"
    watches = list(group_db.scalars(select(DocWatch)))
    assert len(watches) == 1
    assert watches[0].doc_no == "SO-2026-08-0004"
    assert watches[0].user_id == manager.id      # 谁说的谁盯，不落到群主头上
    assert calls and calls[0]["doc_no"] == "SO-2026-08-0004"

    message = group_db.scalars(
        select(ProjectGroupMessage).where(ProjectGroupMessage.kind == "ai")
    ).all()[-1]
    assert "盯" in message.content


@pytest.mark.asyncio
async def test_watch_failure_is_reported_not_swallowed(
    group_db: Session, monkeypatch
) -> None:
    """单号查不到时，群里要看到失败原因，不能假装盯上了。"""
    from app.services import doc_watch

    group, owner, manager, member = _setup(group_db)
    monkeypatch.setattr(doc_watch, "_default_trace", lambda doc_no: {})
    monkeypatch.setattr(
        "app.services.mes_user_client.can_query", lambda panel, uid: (True, "")
    )

    complete, _ = _fake(_analysis(watches=[{"doc_no": "SO-NOT-EXIST"}]))
    run = await group_assistant.analyze_group(
        group_db, group=group, complete=complete, focus_sender="张组长"
    )

    assert run.status == "done"
    from app.models.doc_watch import DocWatch

    assert list(group_db.scalars(select(DocWatch))) == []
    message = group_db.scalars(
        select(ProjectGroupMessage).where(ProjectGroupMessage.kind == "ai")
    ).all()[-1]
    assert "没查到" in message.content
