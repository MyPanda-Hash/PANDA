from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import database
from app.core.config import settings
from app.models.assistant_watch import AssistantEvent
from app.models.auth import AuditEvent, User
from app.models.chat import ChatMessage, ChatSession
from app.models.employee_profile import EmployeeProfile, ProjectExperience
from app.models.group_chat import GroupTodo, ProjectGroup, ProjectGroupMessage
from app.services import employee_profiles as profiles


@pytest.fixture
def prof_db(tmp_path: Path):
    original = settings.db_path, settings.database_url
    path = tmp_path / "employee-profiles.sqlite3"
    database.reset_database(path)
    settings.db_path = path
    settings.database_url = f"sqlite:///{path}"
    database.initialize_database()
    db = database.SessionLocal()
    try:
        admin = User(
            id="usr_admin", username=settings.bootstrap_username, email="a@example.com",
            display_name="管理员", password_hash="x", role="admin", status="active",
            first_login=False, created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        planner = User(
            id="usr_planner", username="planner", email="p@example.com",
            display_name="李计划", password_hash="x", role="user", status="active",
            first_login=False, created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        db.add_all([admin, planner])
        db.commit()
        yield db, admin, planner
    finally:
        db.close()
        database.reset_database(path)
        settings.db_path, settings.database_url = original


def _week(anchor: datetime | None = None):
    """测试统一用「本周至今」窗口，和 distill 默认行为一致。"""
    return profiles.week_period(anchor, current_week=True)


def test_week_period_is_previous_full_week():
    anchor = datetime(2026, 11, 12, 15, 0, tzinfo=timezone.utc)  # 周四
    start, end = profiles.week_period(anchor)
    assert start.weekday.__self__ is not None  # 保证是 datetime
    local_start = start.astimezone()
    local_end = end.astimezone()
    assert local_start.weekday() == 0, "从周一开始"
    assert (local_end - local_start).days == 7


def _seed_activity(db: Session, user_id: str, start: datetime, end: datetime):
    """造一周内的行为数据（放在本周内，配合默认的 current_week）。"""
    mid = start + timedelta(hours=2)

    session = ChatSession(
        id="chs_1", owner_user_id=user_id, title="排产问题",
        messages_revision=0, title_finalized=False,
        created_at=mid, updated_at=mid,
    )
    db.add(session)
    db.flush()
    for i in range(3):
        db.add(ChatMessage(
            id=f"msg_{i}", session_id=session.id, owner_user_id=user_id,
            role="user", content=f"问题{i}", space_ids_json="[]",
            document_ids_json="[]", variant_index=0, variant_active=True,
            references_json="[]", session_references_json="[]",
            writing_references_json="[]", images_json="[]", created_at=mid,
        ))

    group = ProjectGroup(
        id="grp_1", name="销售一部", description="", owner_user_id=user_id,
        status="active", created_at=mid, updated_at=mid,
    )
    db.add(group)
    db.flush()
    for i in range(4):
        db.add(ProjectGroupMessage(
            id=f"gm_{i}", group_id=group.id, sender_user_id=user_id,
            sender_kind="user", kind="text", content=f"发言{i}",
            attachments_json="[]", created_at=mid,
        ))

    db.add(GroupTodo(
        id="gt_1", group_id=group.id, assignee_user_id=user_id, origin="ai",
        title="确认公差", detail="", priority="high", status="done",
        created_at=mid, updated_at=mid, completed_at=mid + timedelta(hours=3),
    ))
    db.add(GroupTodo(
        id="gt_2", group_id=group.id, assignee_user_id=user_id, origin="ai",
        title="补齐记录", detail="", priority="normal", status="pending",
        created_at=mid, updated_at=mid,
    ))

    db.add(AssistantEvent(
        id="aev_1", dedupe_key="k1", kind="so_unstarted",
        title="订单待排产", severity="warning", status="handled",
        target_usernames_json='["planner"]',
        target_user_ids_json=json.dumps([user_id]), payload_json="{}",
        acked_by_user_id=user_id,
        created_at=mid, updated_at=mid + timedelta(hours=2),
        first_seen_at=mid,
    ))

    for kind in ("login_succeeded", "group_analyzed", "group_todo_status"):
        db.add(AuditEvent(
            id=f"aud_{kind}", actor_user_id=user_id, event_type=kind,
            result="success", created_at=mid,
        ))
    db.flush()


def test_collect_stats_counts_real_facts(prof_db):
    db, _admin, planner = prof_db
    start, end = _week()
    _seed_activity(db, planner.id, start, end)

    stats = profiles.collect_stats(
        db, user_id=planner.id, period_start=start, period_end=end
    )
    assert stats.chat_sessions == 1
    assert stats.chat_messages == 3
    assert stats.group_messages == 4
    assert stats.todo_assigned == 2
    assert stats.todo_completed == 1
    assert stats.events_acked == 1
    assert stats.actions_total == 3
    assert stats.action_breakdown["login_succeeded"] == 1
    assert stats.avg_response_hours == 2.0
    assert stats.groups == ["销售一部"]
    assert stats.active_days == 1


def test_digest_renders_human_readable_track(prof_db):
    db, _admin, planner = prof_db
    start, end = _week()
    _seed_activity(db, planner.id, start, end)
    stats = profiles.collect_stats(
        db, user_id=planner.id, period_start=start, period_end=end
    )
    digest = profiles.render_activity_digest(stats)
    assert "群聊发言 4 条" in digest
    assert "接收待办 2 条、完成 1 条" in digest
    assert "认领 AI 派单 1 次" in digest
    assert "登录 1" in digest


def test_distill_creates_profile_with_numbers(prof_db):
    db, _admin, planner = prof_db
    start, end = _week()
    _seed_activity(db, planner.id, start, end)

    profile = profiles.distill_user(db, user=planner)
    assert profile.chat_messages == 3
    assert profile.todo_completed == 1
    assert profile.generated_by == "rules"
    assert "活跃" in profile.summary
    assert json.loads(profile.strengths), "规则版应给出至少一条优势"


def test_model_distillation_overrides_rules(prof_db):
    db, _admin, planner = prof_db
    start, end = _week()
    _seed_activity(db, planner.id, start, end)

    profile = profiles.distill_user(
        db,
        user=planner,
        distilled={
            "summary": "排产执行到位，沟通积极",
            "strengths": ["待办闭环率高"],
            "risks": ["响应偶有延迟"],
            "focus_areas": ["下周期关注材料齐套"],
            "capability_tags": ["生产计划", "任务执行"],
        },
    )
    assert profile.generated_by == "model"
    assert profile.summary == "排产执行到位，沟通积极"
    assert json.loads(profile.capability_tags) == ["生产计划", "任务执行"]


def test_distill_is_idempotent_per_week(prof_db):
    db, _admin, planner = prof_db
    start, end = _week()
    _seed_activity(db, planner.id, start, end)

    profiles.distill_user(db, user=planner)
    profiles.distill_user(db, user=planner)
    rows = list(db.scalars(select(EmployeeProfile)))
    assert len(rows) == 1, "同一周期只应有一条卡片"


def test_non_admin_cannot_list_profiles(prof_db):
    db, _admin, planner = prof_db
    with pytest.raises(profiles.ProfileForbidden):
        profiles.list_profiles(db, actor=planner)


def test_admin_can_list_profiles(prof_db):
    db, admin, planner = prof_db
    start, end = _week()
    _seed_activity(db, planner.id, start, end)
    profiles.distill_user(db, user=planner)

    rows = profiles.list_profiles(db, actor=admin)
    assert len(rows) == 1
    assert rows[0].user_id == planner.id


def test_experience_requires_title(prof_db):
    db, _admin, planner = prof_db
    start, end = _week()
    _seed_activity(db, planner.id, start, end)
    assert profiles.extract_experience(
        db, group_id="grp_1", period_start=start, period_end=end, distilled={}
    ) is None


def test_experience_records_contributors(prof_db):
    db, _admin, planner = prof_db
    start, end = _week()
    _seed_activity(db, planner.id, start, end)

    record = profiles.extract_experience(
        db,
        group_id="grp_1",
        period_start=start,
        period_end=end,
        distilled={
            "title": "机架折弯公差超差处理",
            "problem": "上批孔位偏 0.3mm",
            "resolution": "更换折弯模具并加严抽检",
            "lesson": "新模具上线必须试模验证公差",
            "tags": ["质量", "折弯"],
        },
    )
    assert record is not None
    assert record.title.startswith("机架折弯")
    assert json.loads(record.contributors) == [planner.id]
    assert json.loads(record.tags) == ["质量", "折弯"]


def test_parse_distilled_handles_garbage():
    assert profiles.parse_distilled("没有 JSON") == {}
    parsed = profiles.parse_distilled('```json\n{"summary":"ok","strengths":["a"]}\n```')
    assert parsed["summary"] == "ok"
    assert parsed["strengths"] == ["a"]


def test_experience_search_filters(prof_db):
    db, admin, planner = prof_db
    start, end = _week()
    _seed_activity(db, planner.id, start, end)
    profiles.extract_experience(
        db, group_id="grp_1", period_start=start, period_end=end,
        distilled={"title": "折弯公差问题", "problem": "超差"},
    )
    profiles.extract_experience(
        db, group_id="grp_1", period_start=start, period_end=end,
        distilled={"title": "来料检验流程", "problem": "漏检"},
    )

    assert len(profiles.list_experiences(db, actor=admin)) == 2
    hit = profiles.list_experiences(db, actor=admin, keyword="折弯")
    assert [x.title for x in hit] == ["折弯公差问题"]


def test_silent_user_gets_no_fabricated_tags(prof_db):
    """零活动的人不能让模型凭岗位名编标签，必须明确说没有记录。"""
    db, _admin, planner = prof_db
    profile = profiles.distill_user(
        db,
        user=planner,
        distilled={"summary": "销售能力强", "capability_tags": ["销售", "客户跟进"]},
    )
    assert profile.summary == "本周期没有系统内的操作或协作记录。"
    assert json.loads(profile.capability_tags) == []
    assert json.loads(profile.strengths) == []
    assert "无任何系统活动" in json.loads(profile.risks)[0]


def test_current_week_period_covers_today():
    """首次生成卡片用本周至今，否则新系统里所有人都是零活动。"""
    anchor = datetime(2026, 11, 12, 15, 0, tzinfo=timezone.utc)
    start, end = profiles.week_period(anchor, current_week=True)
    local_start = start.astimezone()
    assert local_start.weekday() == 0
    assert start <= anchor <= end, "本周至今必须包含今天"


def test_previous_week_period_excludes_today():
    anchor = datetime(2026, 11, 12, 15, 0, tzinfo=timezone.utc)
    start, end = profiles.week_period(anchor)
    assert anchor > end, "上一完整周不含今天"


def test_assigned_but_idle_user_keeps_todo_fact(prof_db):
    """被派了待办但一条没动：这本身是最关键的结论，不能当成"无数据"。"""
    db, _admin, planner = prof_db
    start, end = _week()
    mid = start + timedelta(hours=2)
    db.add(GroupTodo(
        id="gt_idle", group_id=None, assignee_user_id=planner.id, origin="ai",
        title="确认机架图纸公差", detail="", priority="high", status="pending",
        created_at=mid, updated_at=mid,
    ))
    db.flush()

    profile = profiles.distill_user(
        db, user=planner, distilled={"summary": "编造的结论", "capability_tags": ["臆测"]}
    )
    assert profile.todo_assigned == 1
    assert profile.summary != "编造的结论", "不该采纳模型臆测"
    assert json.loads(profile.capability_tags) == []
    assert any("未闭环" in x for x in json.loads(profile.focus_areas))


# ---------------- 自动沉淀项目经验 ----------------

def _seed_group_chat(db: Session, user_id: str, start: datetime, count: int = 6):
    mid = start + timedelta(hours=2)
    group = ProjectGroup(
        id="grp_exp", name="机架试产", description="", owner_user_id=user_id,
        status="active", created_at=mid, updated_at=mid,
    )
    db.add(group)
    db.flush()
    texts = [
        "上批孔位偏 0.3mm",
        "换折弯模具试试",
        "新模具试模了，公差压到 0.05",
        "那就按新模具排产",
        "首件检验也过了",
        "把这条写进作业指导书",
    ]
    for i in range(min(count, len(texts))):
        db.add(ProjectGroupMessage(
            id=f"gm_exp_{i}", group_id=group.id, sender_user_id=user_id,
            sender_kind="user", kind="text", content=texts[i],
            attachments_json="[]", created_at=mid + timedelta(minutes=i),
        ))
    db.flush()
    return group


@pytest.mark.asyncio
async def test_extract_from_groups_creates_experience(prof_db, monkeypatch):
    db, _admin, planner = prof_db
    start, end = _week()
    _seed_group_chat(db, planner.id, start)

    class Reply:
        text = json.dumps({
            "skip": False,
            "title": "折弯模具更换后公差改善",
            "problem": "上批孔位偏 0.3mm",
            "resolution": "更换折弯模具并试模，公差压到 0.05",
            "lesson": "新模具上线前必须试模验证公差",
            "tags": ["质量", "折弯"],
        }, ensure_ascii=False)

    async def fake(*args, **kwargs):
        return Reply()

    monkeypatch.setattr("app.services.plain_llm.complete_chat", fake)

    created = await profiles.extract_from_groups(
        db, period_start=start, period_end=end
    )
    assert len(created) == 1
    assert created[0].title.startswith("折弯模具")
    assert json.loads(created[0].tags) == ["质量", "折弯"]


@pytest.mark.asyncio
async def test_extract_skips_when_model_says_no_conclusion(prof_db, monkeypatch):
    db, _admin, planner = prof_db
    start, end = _week()
    _seed_group_chat(db, planner.id, start)

    class Reply:
        text = '{"skip": true}'

    async def fake(*args, **kwargs):
        return Reply()

    monkeypatch.setattr("app.services.plain_llm.complete_chat", fake)
    created = await profiles.extract_from_groups(db, period_start=start, period_end=end)
    assert created == []


@pytest.mark.asyncio
async def test_extract_skips_groups_with_too_few_messages(prof_db, monkeypatch):
    db, _admin, planner = prof_db
    start, end = _week()
    _seed_group_chat(db, planner.id, start, count=2)

    called = {"n": 0}

    async def fake(*args, **kwargs):
        called["n"] += 1
        raise AssertionError("不该调用模型")

    monkeypatch.setattr("app.services.plain_llm.complete_chat", fake)
    created = await profiles.extract_from_groups(db, period_start=start, period_end=end)
    assert created == []
    assert called["n"] == 0


@pytest.mark.asyncio
async def test_extract_does_not_duplicate_same_title(prof_db, monkeypatch):
    db, _admin, planner = prof_db
    start, end = _week()
    _seed_group_chat(db, planner.id, start)

    class Reply:
        text = json.dumps({
            "skip": False, "title": "折弯模具更换", "problem": "超差",
            "resolution": "换模具", "lesson": "先试模", "tags": [],
        }, ensure_ascii=False)

    async def fake(*args, **kwargs):
        return Reply()

    monkeypatch.setattr("app.services.plain_llm.complete_chat", fake)

    await profiles.extract_from_groups(db, period_start=start, period_end=end)
    await profiles.extract_from_groups(db, period_start=start, period_end=end)

    rows = list(db.scalars(select(ProjectExperience)))
    assert len(rows) == 1, "同群同标题不该重复沉淀"


@pytest.mark.asyncio
async def test_extract_survives_model_failure(prof_db, monkeypatch):
    db, _admin, planner = prof_db
    start, end = _week()
    _seed_group_chat(db, planner.id, start)

    async def boom(*args, **kwargs):
        from app.services.plain_llm import LlmUnavailable

        raise LlmUnavailable("没配模型")

    monkeypatch.setattr("app.services.plain_llm.complete_chat", boom)
    assert await profiles.extract_from_groups(db, period_start=start, period_end=end) == []


@pytest.mark.asyncio
async def test_with_model_false_does_not_call_model(prof_db, monkeypatch):
    db, _admin, planner = prof_db
    start, end = _week()
    _seed_group_chat(db, planner.id, start)

    async def fake(*args, **kwargs):
        raise AssertionError("不该调用模型")

    monkeypatch.setattr("app.services.plain_llm.complete_chat", fake)
    created = await profiles.extract_from_groups(
        db, period_start=start, period_end=end, with_model=False
    )
    assert created == []


def test_parse_extracted_handles_skip_and_garbage():
    assert profiles.parse_extracted('{"skip": true}') == {}
    assert profiles.parse_extracted("没有 JSON") == {}
    parsed = profiles.parse_extracted(
        '```json\n{"title":"T","tags":["a","b"]}\n```'
    )
    assert parsed["title"] == "T"
    assert parsed["tags"] == ["a", "b"]


@pytest.mark.asyncio
async def test_group_with_only_ai_messages_is_skipped(prof_db, monkeypatch):
    """全是 AI 通知的群没有讨论价值，不该浪费模型调用。"""
    db, _admin, planner = prof_db
    start, end = _week()
    mid = start + timedelta(hours=2)
    group = ProjectGroup(
        id="grp_ai", name="AI 调度台", description="", owner_user_id=planner.id,
        status="active", created_at=mid, updated_at=mid,
    )
    db.add(group)
    db.flush()
    for i in range(6):
        db.add(ProjectGroupMessage(
            id=f"gm_ai_{i}", group_id=group.id, sender_user_id=None,
            sender_kind="ai", kind="ai", content=f"我在 MES 里发现了这些待办 {i}",
            attachments_json="[]", created_at=mid + timedelta(minutes=i),
        ))
    db.flush()

    called = {"n": 0}

    async def fake(*args, **kwargs):
        called["n"] += 1
        raise AssertionError("不该调用模型")

    monkeypatch.setattr("app.services.plain_llm.complete_chat", fake)
    created = await profiles.extract_from_groups(db, period_start=start, period_end=end)
    assert created == []
    assert called["n"] == 0


@pytest.mark.asyncio
async def test_discussion_with_assignment_counts_as_experience(prof_db, monkeypatch):
    """「分工确定」也算经验，不该被 skip 掉。"""
    db, _admin, planner = prof_db
    start, end = _week()
    _seed_group_chat(db, planner.id, start, count=5)

    class Reply:
        text = json.dumps({
            "skip": False,
            "title": "大单交期分工办法",
            "problem": "200 件订单交期紧，产能不足",
            "resolution": "周四出 120 件、周五补 80 件，李计划盯交期",
            "lesson": "大单先拆两批排产，避免一次性压垮产线",
            "tags": ["交期", "排产"],
        }, ensure_ascii=False)

    async def fake(*args, **kwargs):
        return Reply()

    monkeypatch.setattr("app.services.plain_llm.complete_chat", fake)
    created = await profiles.extract_from_groups(db, period_start=start, period_end=end)
    assert len(created) == 1
    assert "交期" in created[0].title


# ---------------- 证据晋升与去重（借鉴 skill-cairn） ----------------

def _add_card(db, *, cid, title, topic, status="unverified", reusable=False,
              source_ref="销售一部"):
    db.add(ProjectExperience(
        id=cid, group_id=None, title=title, topic=topic, status=status,
        reusable=reusable, source_ref=source_ref, problem="", resolution="",
        lesson="", tags="[]", contributors="[]", source_count=1,
        created_at=datetime.now(timezone.utc),
    ))
    db.flush()


def test_promotion_blocked_with_single_card(prof_db):
    db, _admin, _planner = prof_db
    _add_card(db, cid="e1", title="折弯公差处理", topic="bending", status="verified")

    result = profiles.promotion_readiness(db, topic="bending")
    assert result["ready"] is False
    assert result["checks"]["enough_cards"]["passed"] is False
    assert result["checks"]["enough_verified"]["passed"] is False


def test_promotion_requires_verified_and_sources(prof_db):
    db, _admin, _planner = prof_db
    # 3 张卡但都未验证 → 不通过
    for i in range(3):
        _add_card(db, cid=f"e{i}", title=f"经验{i}", topic="bending")
    result = profiles.promotion_readiness(db, topic="bending")
    assert result["checks"]["enough_cards"]["passed"] is True
    assert result["checks"]["enough_verified"]["passed"] is False
    assert result["ready"] is False


def test_promotion_passes_when_all_checks_met(prof_db):
    db, _admin, _planner = prof_db
    _add_card(db, cid="e1", title="折弯公差处理", topic="bending",
              status="verified", reusable=True, source_ref="销售一部")
    _add_card(db, cid="e2", title="折弯模具试模", topic="bending",
              status="verified", source_ref="机架试产")
    _add_card(db, cid="e3", title="折弯首件检验", topic="bending",
              status="unverified", source_ref="销售一部")

    result = profiles.promotion_readiness(db, topic="bending")
    assert result["ready"] is True
    assert all(c["passed"] for c in result["checks"].values())


def test_promotion_needs_two_independent_sources(prof_db):
    db, _admin, _planner = prof_db
    for i in range(3):
        _add_card(db, cid=f"e{i}", title=f"经验{i}", topic="bending",
                  status="verified", reusable=True, source_ref="同一个群")
    result = profiles.promotion_readiness(db, topic="bending")
    assert result["checks"]["independent_sources"]["passed"] is False
    assert result["ready"] is False


def test_promotion_candidates_sorted_ready_first(prof_db):
    db, _admin, _planner = prof_db
    _add_card(db, cid="a1", title="x1", topic="ready-topic", status="verified",
              reusable=True, source_ref="群A")
    _add_card(db, cid="a2", title="x2", topic="ready-topic", status="verified",
              source_ref="群B")
    _add_card(db, cid="a3", title="x3", topic="ready-topic", status="unverified",
              source_ref="群A")
    _add_card(db, cid="b1", title="y1", topic="weak-topic")

    rows = profiles.list_promotion_candidates(db)
    assert rows[0]["topic"] == "ready-topic"
    assert rows[0]["ready"] is True
    assert rows[1]["ready"] is False


def test_set_experience_status_validates(prof_db):
    db, _admin, _planner = prof_db
    _add_card(db, cid="e1", title="折弯", topic="bending")

    updated = profiles.set_experience_status(
        db, experience_id="e1", status="verified", reusable=True,
        not_applicable="仅适用于冷轧板材",
    )
    assert updated.status == "verified"
    assert updated.reusable is True
    assert "冷轧" in updated.not_applicable

    with pytest.raises(profiles.ProfileError):
        profiles.set_experience_status(db, experience_id="e1", status="瞎写")


def test_similarity_detects_near_duplicates(prof_db):
    assert profiles.similarity("折弯模具更换后公差改善", "折弯模具更换后公差改良") > 0.6
    assert profiles.similarity("折弯公差", "销售订单未排产") < 0.2


def test_find_duplicates_reports_pairs(prof_db):
    db, _admin, _planner = prof_db
    _add_card(db, cid="d1", title="折弯模具更换后公差改善", topic="bending")
    _add_card(db, cid="d2", title="折弯模具更换后公差改良", topic="bending")
    _add_card(db, cid="d3", title="到货检验流程", topic="qc")

    pairs = profiles.find_duplicates(db, threshold=0.5)
    assert len(pairs) == 1
    assert pairs[0]["similarity"] > 0.5
    assert {pairs[0]["left"]["id"], pairs[0]["right"]["id"]} == {"d1", "d2"}


def test_slugify_is_stable_for_same_title(prof_db):
    assert profiles._slugify("折弯模具更换后公差改善") == profiles._slugify("折弯模具更换后公差改善")
    assert profiles._slugify("A/B 测试") == "a-b-测试"
    assert profiles._slugify("") == "untitled"


def test_extracted_experience_defaults_to_unverified(prof_db):
    """模型总结不等于验证过——自动沉淀的默认状态必须是 unverified。"""
    db, _admin, planner = prof_db
    start, end = _week()
    _seed_group_chat(db, planner.id, start)
    record = profiles.extract_experience(
        db, group_id="grp_exp", period_start=start, period_end=end,
        distilled={"title": "折弯处理", "problem": "超差"},
    )
    assert record.status == "unverified"
    assert record.topic == "折弯处理"
    assert record.reusable is False
