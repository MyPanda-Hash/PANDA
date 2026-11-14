"""群聊里的常驻 AI 助手：读聊天与附件 → 产出待办 → 派给具体成员。

流程（一次 ``analyze_group``）::

    1. 取最近 N 条消息 + 附件正文节选，拼成上下文
    2. 调模型，要求返回严格 JSON
    3. 解析并校验；指派对象按群成员的姓名/账号匹配
    4. 建 ``GroupTodo``，去重，落一条 AI 消息回群
    5. 整个过程写成 ``ProjectGroupAiRun``，失败也留痕便于重试

模型不可用时抛 ``GroupAssistantUnavailable``（不写记录，由调用方返回 503）；
模型调用失败或输出无法解析时**不抛异常**，返回 status=failed 的 run，
让调用方在事务提交后再决定 HTTP 状态——否则回滚会把失败记录一起抹掉。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import new_token, now
from app.models.auth import User
from app.models.group_chat import (
    TODO_PRIORITIES,
    GroupTodo,
    ProjectGroup,
    ProjectGroupAiRun,
    ProjectGroupAttachment,
    ProjectGroupMemory,
    ProjectGroupMessage,
)
from app.services import group_chat

logger = logging.getLogger(__name__)

CONTEXT_MESSAGE_LIMIT = 80
# 有纪要时，只把纪要之后的这些消息给模型，避免上下文无限膨胀
MEMORY_TAIL_MESSAGES = 40
# 已完成的同名待办在这个天数内不再重复创建。
DEDUPE_COMPLETED_DAYS = 7
EXTRACT_TEXT_LIMIT = 1500
VALID_PRIORITIES = set(TODO_PRIORITIES)
AI_MENTION_MARKERS = ("@AI", "@助手", "@小助手")


class GroupAssistantError(RuntimeError):
    pass


class GroupAssistantUnavailable(GroupAssistantError):
    """没有可用模型，无法分析。"""


class GroupAssistantAnalysisFailed(GroupAssistantError):
    """模型返回了无法解析的内容。"""


@dataclass
class ParsedTodo:
    title: str
    detail: str = ""
    assignee_hint: str = ""
    priority: str = "normal"
    due_at: datetime | None = None
    confidence: float | None = None


@dataclass
class ParsedWatch:
    doc_no: str
    step: str = ""
    note: str = ""


@dataclass
class ParsedAnalysis:
    summary: str = ""
    reply: str = ""
    memory: str = ""
    need_query: list[dict[str, str]] = field(default_factory=list)
    watches: list[ParsedWatch] = field(default_factory=list)
    unwatch: list[str] = field(default_factory=list)
    todos: list[ParsedTodo] = field(default_factory=list)
    decisions: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)


SYSTEM_PROMPT = """你是一个项目群的助理，负责把群里的讨论整理成可执行的待办。

规则：
1. 只依据给出的聊天记录和附件正文，不要编造没出现过的信息。
2. 只输出一个 JSON 对象，不要加解释、不要加代码块标记。
3. 每条待办必须是确实需要某个人去做的事；闲聊、通知、已完成的事不要写成待办。
4. assignee 必须从「群成员」里选名字，选不出就留空字符串。
5. priority 只能是 low / normal / high / urgent。
6. due 只能是 YYYY-MM-DD，没有明确日期就留空字符串。

有人在问你时，reply 必须**针对最后那条消息回答**，不是复述背景：
- 问「谁负责 / 谁在跟」：逐个事项点名指派人；没人认领就明说「还没人认领」。
- 问「还差什么 / 还有哪些没做完」：只列未完成的事项，别再报已完成的。
- 问「进度怎么样」：给完成/未完成的对比和卡点，一两句说完。
- 问「什么时候 / 截止」：报截止日期，没有就说没有定。
- 其它问题：直接回答，别把待办清单从头念一遍。
- 严禁原样重复你前面几轮说过的话；上一轮已经说过的细节不要再来一次。
- risks 只放**这轮新发现**的风险；之前已经提过、这次没有变化的不要重复。
- 只依据上下文事实（聊天记录、附件正文、待办清单），不要编造。
- 只是被问问题时不要为了凑数新建待办；没人问你，reply 留空字符串。
- **有人在安排事情时**（"让 XX 去对接""需要采购…""周五必须发货"），
  即使没有问号，也要把这件事整理成待办并指派给合适的人，
  reply 里回一句"已记录，派给 XX"。
- 指派优先找**群成员里职位匹配的人**：销售类给业务员，采购/库存给仓管，
  生产/排产给计划或车间，质检给质检员。找不到合适的人再给组长。

另外，如果这段对话值得长期记住（有决策、有分工、有关键数据），在 memory 里输出一段
不超过 300 字的群纪要，供后续对话使用；已在上下文给出的「已有群纪要」要合并进来，不要丢。
日常寒暄、重复内容不要写进 memory。

**盯单（订阅）**：群成员说「帮我盯着 SO-2026-08-0004」「这单到货告诉我」「别盯了」时，
把要盯的单号写进 watches；取消写进 unwatch。step 是只盯某个环节（如 到货/报工/入库），
不填表示有任何推进都报。note 写一句为什么盯（可为空）。
不要替别人订阅——只订阅最后说话的那个人要盯的单。

输出格式：
{"memory":"群纪要，可为空","reply":"面向群里的一段话，可为空","summary":"一两句话概括这段讨论","todos":[{"title":"动词开头的具体动作","detail":"补充说明，可为空","assignee":"成员姓名","priority":"normal","due":"","confidence":0.0}],"decisions":["已定的事"],"risks":["风险或阻塞"],"watches":[{"doc_no":"SO-2026-08-0004","step":"","note":""}],"unwatch":["PO-2026-08-0001"]}"""


def _new_id(prefix: str) -> str:
    return f"{prefix}_{new_token(10)}"


def _business_lookup_blocks(text: str) -> list[str]:
    """只读业务查询：带单号时先追踪，再按追踪里的面板编码取详情。"""
    from app.services import group_tools

    blocks: list[str] = []
    for spec in group_tools.guess_lookups(text):
        result = group_tools.lookup(spec)
        block = result.render()
        if result.ok and spec.get("kind") == "trace":
            panel = group_tools.lookup_panel_of_trace(result.payload, spec.get("doc_no", ""))
            if panel:
                detail = group_tools.lookup(
                    {"kind": "form", "panel_code": panel, "form_no": spec["doc_no"]}
                )
                block = block + "\n\n" + detail.render()
        blocks.append(block)
    return blocks


def _format_stamp(value: datetime | None) -> str:
    if value is None:
        return ""
    try:
        return value.astimezone().strftime("%m-%d %H:%M")
    except (ValueError, OSError):
        return value.strftime("%m-%d %H:%M")


def _attachment_brief(attachment: ProjectGroupAttachment) -> str:
    parts = [f"附件《{attachment.filename}》"]
    if attachment.extracted_text:
        text = attachment.extracted_text.strip().replace("\n", " ")
        if len(text) > EXTRACT_TEXT_LIMIT:
            text = text[:EXTRACT_TEXT_LIMIT] + "…"
        parts.append(f"正文节选：{text}")
    elif attachment.kind == "voice":
        parts.append("（语音未能转写）")
    else:
        parts.append("（未提取到正文）")
    return "".join(parts)


def load_memory(db: Session, group_id: str) -> ProjectGroupMemory | None:
    return db.scalar(
        select(ProjectGroupMemory).where(ProjectGroupMemory.group_id == group_id)
    )


def save_memory(
    db: Session,
    *,
    group: ProjectGroup,
    summary: str,
    covered_message_id: str | None,
    covered_message_count: int,
) -> ProjectGroupMemory:
    record = load_memory(db, group.id)
    stamp = now()
    if record is None:
        record = ProjectGroupMemory(
            id=_new_id("gmem"),
            group_id=group.id,
            summary="",
            covered_message_count=0,
            updated_at=stamp,
        )
        db.add(record)
    record.summary = summary[:4000]
    record.covered_message_id = covered_message_id
    record.covered_message_count = max(0, covered_message_count)
    record.updated_at = stamp
    db.flush()
    return record


def build_context(
    db: Session,
    *,
    group: ProjectGroup,
    message_limit: int = CONTEXT_MESSAGE_LIMIT,
    focus_message: str | None = None,
    focus_sender: str | None = None,
) -> tuple[str, list[str]]:
    """返回 (说明文本, 参与分析的消息 id 列表)。"""
    members = group_chat.group_member_users(db, group.id)
    name_by_id = {user.id: user.display_name or user.username for _, user in members}
    member_line = "、".join(
        f"{name}（{member.role}）" for member, user in members
        for name in [user.display_name or user.username]
    )

    memory = load_memory(db, group.id)
    memory_section = ""
    if memory is not None and memory.summary.strip():
        memory_section = memory.summary.strip()
        messages = group_chat.list_messages(
            db,
            group_id=group.id,
            after_message_id=memory.covered_message_id,
            limit=min(message_limit, MEMORY_TAIL_MESSAGES),
        )
        if not messages:
            # 纪要之后还没新消息：退一步给最近的，保证模型仍有上下文可读
            messages = group_chat.list_messages(db, group_id=group.id, limit=10)
    else:
        messages = group_chat.list_messages(db, group_id=group.id, limit=message_limit)
    attachment_ids: list[str] = []
    for message in messages:
        attachment_ids.extend(_ids_of(message))

    attachments: dict[str, ProjectGroupAttachment] = {}
    if attachment_ids:
        rows = db.scalars(
            select(ProjectGroupAttachment).where(
                ProjectGroupAttachment.id.in_(attachment_ids)
            )
        )
        attachments = {row.id: row for row in rows}

    lines: list[str] = [f"群名：{group.name}"]
    if group.description:
        lines.append(f"项目说明：{group.description}")
    lines.append(f"群成员：{member_line or '（无）'}")
    if memory_section:
        lines.append("")
        lines.append("【已有群纪要（更早讨论的压缩，事实以它为准）】")
        lines.append(memory_section)
    lines.append(todo_overview(db, group=group, members=members))
    lines.append("")
    lines.append("聊天记录（时间 发送人：内容）：")

    kept_ids: list[str] = []
    for message in messages:
        if message.sender_kind == "system":
            continue
        who = (
            "AI 助手"
            if message.sender_kind == "ai"
            else name_by_id.get(message.sender_user_id or "", "未知成员")
        )
        lines.append(
            f"[{_format_stamp(message.created_at)}] {who}：{message.content or ''}".rstrip()
        )
        for attachment_id in _ids_of(message):
            item = attachments.get(attachment_id)
            if item is not None:
                lines.append(f"    - {_attachment_brief(item)}")
        kept_ids.append(message.id)
    if not kept_ids:
        lines.append("（还没有消息）")
    if focus_message:
        # 放在最后：模型对结尾最敏感，不写这段它就只会复述整段背景。
        lines.append("")
        lines.append("=== 需要你现在回答的这条消息 ===")
        lines.append(f"{focus_sender or '群成员'}：{focus_message.strip()}")
        lines.append(
            "只回答这条消息，不要复述待办清单，不要重复你之前说过的内容。"
        )
    return "\n".join(lines), kept_ids


def _as_aware(value: datetime | None) -> datetime | None:
    """SQLite 读回来的 datetime 没有时区，统一按 UTC 处理后再比较。"""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def todo_overview(
    db: Session, *, group: ProjectGroup, members: list[tuple[object, User]]
) -> str:
    """待办现状，供 AI 回答「进度怎么样」时有事实可用。"""
    name_by_id = {user.id: user.display_name or user.username for _, user in members}
    todos = list(
        db.scalars(
            select(GroupTodo)
            .where(GroupTodo.group_id == group.id, GroupTodo.status != "dismissed")
            .order_by(GroupTodo.created_at)
        )
    )
    open_statuses = ("pending", "accepted", "doing")
    open_todos = [t for t in todos if t.status in open_statuses]
    done_todos = [t for t in todos if t.status == "done"]
    stamp = now()
    overdue = [
        t
        for t in open_todos
        if _as_aware(t.due_at) is not None and _as_aware(t.due_at) < stamp
    ]

    lines = [
        "",
        f"待办概览：未完成 {len(open_todos)} 条，已完成 {len(done_todos)} 条"
        + (f"，其中逾期 {len(overdue)} 条" if overdue else ""),
    ]
    status_label = {"pending": "待处理", "accepted": "已接受", "doing": "进行中"}
    for todo in open_todos[:12]:
        who = name_by_id.get(todo.assignee_user_id, "未指派")
        due = todo.due_at.strftime("%m-%d") if todo.due_at else "无截止"
        late = (
            "（已逾期）"
            if _as_aware(todo.due_at) is not None and _as_aware(todo.due_at) < stamp
            else ""
        )
        lines.append(
            f"  - [{status_label.get(todo.status, todo.status)}] {todo.title}"
            f" / {who} / {due}{late}"
        )
    for todo in done_todos[:5]:
        who = name_by_id.get(todo.assignee_user_id, "未指派")
        lines.append(f"  - [已完成] {todo.title} / {who}")
    if not todos:
        lines.append("  （还没有任何待办）")
    return "\n".join(lines)


# 问情况类的词
_QUESTION_WORDS = (
    "进度", "进展", "如何", "怎么样", "怎样", "多少", "哪些", "谁",
    "什么时候", "待办", "还差", "还缺", "汇报", "状态", "卡在", "风险",
)
# 安排任务类的词：陈述句里出现这些，说明有活要干，AI 该整理成待办
_TASK_WORDS = (
    "对接", "安排", "排产", "跟进", "处理", "补齐", "补发", "确认",
    "下单", "采购", "发货", "交货", "领料", "出库", "入库", "报检",
    "检验", "返修", "加急", "催", "盯", "推进", "落实",
    "发出", "出货", "交期", "必须", "务必", "尽快", "今天", "明天", "周五",
    "对接一下", "去办", "安排一下", "看一看", "核一下",
    "补一下", "催一下", "查一下", "定一下", "改一下", "发一下",
)
# 明确指派给某人的信号
_ASSIGN_WORDS = ("去", "让", "请", "由", "负责")


def should_auto_reply(text: str) -> bool:
    """这句话要不要请 AI 出手。

    三类触发：
    1. 明确 @AI
    2. 在问项目情况（问句 / 问句关键词）
    3. **在安排事情**——陈述句里有任务动词，往往还点了人，
       这种必须整理成待办派下去，否则就等于没说。

    纯闲聊（"好的收到"）不触发，免得刷屏。
    """
    content = (text or "").strip()
    if not content:
        return False
    if any(marker in content for marker in AI_MENTION_MARKERS):
        return True
    if len(content) > 200:
        return False
    if any(word in content for word in _QUESTION_WORDS):
        return True
    if content.endswith(("?", "？")):
        return True
    if any(word in content for word in _TASK_WORDS):
        return True
    return False


def _ids_of(message: ProjectGroupMessage) -> list[str]:
    try:
        raw = json.loads(message.attachments_json or "[]")
    except (TypeError, ValueError):
        return []
    if not isinstance(raw, list):
        return []
    return [str(item) for item in raw if str(item).strip()]


def parse_analysis(raw: str) -> ParsedAnalysis:
    text = (raw or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise GroupAssistantAnalysisFailed("模型没有返回 JSON 对象")
    try:
        data = json.loads(text[start : end + 1])
    except ValueError as exc:
        raise GroupAssistantAnalysisFailed("模型返回的 JSON 解析失败") from exc
    if not isinstance(data, dict):
        raise GroupAssistantAnalysisFailed("模型返回的 JSON 不是对象")

    analysis = ParsedAnalysis(
        summary=str(data.get("summary") or "").strip(),
        reply=str(data.get("reply") or "").strip(),
        memory=str(data.get("memory") or "").strip(),
        need_query=_parse_need_query(data.get("need_query")),
        unwatch=[str(x).strip() for x in (data.get("unwatch") or []) if str(x).strip()][:10],
    )
    for item in data.get("watches") or []:
        if isinstance(item, str):
            if item.strip():
                analysis.watches.append(ParsedWatch(doc_no=item.strip()))
            continue
        if not isinstance(item, dict):
            continue
        doc_no = str(item.get("doc_no") or item.get("doc") or "").strip()
        if not doc_no:
            continue
        analysis.watches.append(
            ParsedWatch(
                doc_no=doc_no[:64],
                step=str(item.get("step") or "").strip()[:64],
                note=str(item.get("note") or "").strip()[:255],
            )
        )
    for item in data.get("todos") or []:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        if not title:
            continue
        priority = str(item.get("priority") or "normal").strip().lower()
        analysis.todos.append(
            ParsedTodo(
                title=title[:255],
                detail=str(item.get("detail") or "").strip(),
                assignee_hint=str(item.get("assignee") or item.get("assignee_hint") or "").strip(),
                priority=priority if priority in VALID_PRIORITIES else "normal",
                due_at=_parse_due(item.get("due") or item.get("due_at")),
                confidence=_parse_confidence(item.get("confidence")),
            )
        )
    analysis.decisions = _str_list(data.get("decisions"))
    analysis.risks = _str_list(data.get("risks"))
    return analysis


def _parse_need_query(value: object) -> list[dict[str, str]]:
    """模型请求的数据查询。只接受已知面板，防止乱指定。"""
    from app.services.group_tools import QUERYABLE_PANELS

    if not isinstance(value, list):
        return []
    out: list[dict[str, str]] = []
    for item in value[:3]:
        if not isinstance(item, dict):
            continue
        panel = str(item.get("panel") or "").strip().upper()
        if panel not in QUERYABLE_PANELS:
            continue
        out.append({"panel": panel, "keyword": str(item.get("keyword") or "").strip()[:60]})
    return out


def _str_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _parse_due(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y-%m-%d %H:%M"):
        try:
            parsed = datetime.strptime(text[: len(text)], fmt)
        except ValueError:
            continue
        return parsed.replace(tzinfo=timezone.utc)
    return None


def _parse_confidence(value: object) -> float | None:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return max(0.0, min(1.0, number))


def _resolve_assignee(
    members: list[tuple[object, User]], hint: str
) -> User | None:
    needle = (hint or "").strip()
    if not needle:
        return None
    lowered = needle.casefold()
    for _, user in members:
        names = {user.display_name or "", user.username or ""}
        if lowered in {name.casefold() for name in names if name}:
            return user
    for _, user in members:
        names = {user.display_name or "", user.username or ""}
        if any(lowered and lowered in name.casefold() for name in names if name):
            return user
    return None


def _already_exists(db: Session, *, group_id: str, assignee_id: str, title: str) -> bool:
    """同群同人同标题：未完成的一律跳过；刚完成（默认 7 天内）的也跳过。

    老聊天记录一直留在上下文里，否则每次分析都会把上周做完的事再派一遍。
    """
    cutoff = now() - timedelta(days=DEDUPE_COMPLETED_DAYS)
    existing = db.scalar(
        select(GroupTodo).where(
            GroupTodo.group_id == group_id,
            GroupTodo.assignee_user_id == assignee_id,
            GroupTodo.title == title[:255],
            or_(
                GroupTodo.status.in_(("pending", "accepted", "doing")),
                and_(
                    GroupTodo.status == "done",
                    GroupTodo.completed_at.is_not(None),
                    GroupTodo.completed_at >= cutoff,
                ),
            ),
        )
    )
    return existing is not None


async def analyze_group(
    db: Session,
    *,
    group: ProjectGroup,
    trigger: str = "manual",
    complete=None,
    focus_message: str | None = None,
    focus_sender: str | None = None,
) -> ProjectGroupAiRun:
    """跑一次分析；``complete`` 可注入假模型用于测试。"""
    if complete is None:
        from app.services.plain_llm import LlmCallFailed, LlmUnavailable
        from app.services.plain_llm import complete_chat as _complete_chat

        async def complete(messages):  # type: ignore[no-redef]
            try:
                reply = await _complete_chat(
                    db, messages, response_format_json=True, disable_thinking=True
                )
            except LlmUnavailable as exc:
                raise GroupAssistantUnavailable(str(exc)) from exc
            except LlmCallFailed as exc:
                raise GroupAssistantError(str(exc)) from exc
            return reply

    context, message_ids = build_context(
        db,
        group=group,
        focus_message=focus_message,
        focus_sender=focus_sender,
    )
    run = ProjectGroupAiRun(
        id=_new_id("gairun"),
        group_id=group.id,
        trigger=trigger if trigger in ("message", "attachment", "manual") else "manual",
        status="running",
        input_refs_json=json.dumps(message_ids, ensure_ascii=False),
        created_at=now(),
    )
    db.add(run)
    db.flush()

    from app.services import group_tools

    system_content = SYSTEM_PROMPT
    business_data = ""
    if settings.mes_open_api_enabled:
        system_content += "\n\n" + group_tools.tool_description()
        # 问题里提到业务对象时，先查好数据再交给模型，不依赖模型主动请求
        requests = group_tools.guess_queries(focus_message or "")
        if requests:
            blocks: list[str] = []
            for request in requests:
                result = group_tools.query_business(
                    panel=request["panel"], keyword=request.get("keyword", ""), limit=5
                )
                blocks.append(result.render())
            if blocks:
                business_data = "\n\n".join(blocks)

        # L2 只读融合：问题里带单号（或问库存）时直接打开放业务接口，
        # 拿追踪结果 + 单据详情/库存结存喂给模型——比让模型"决定要不要查"可靠。
        for block in _business_lookup_blocks(focus_message or ""):
            business_data = (business_data + "\n\n" + block).strip() if business_data else block
    messages = [
        {"role": "system", "content": system_content},
        {"role": "user", "content": context},
    ]
    if business_data:
        messages.append(
            {
                "role": "user",
                "content": "【已为你查到的 MES 业务数据】\n"
                + business_data
                + "\n\n请直接据此回答，不要说查不到、也不要要求别人去系统里查。",
            }
        )
    try:
        reply = await complete(messages)
        # 模型说要查业务数据：去查，把结果塞回去再问一次
        first = parse_analysis(getattr(reply, "text", "") or "")
        if first.need_query:
            from app.services import group_tools

            fetched: list[str] = []
            for request in first.need_query:
                result = group_tools.query_business(
                    panel=request["panel"], keyword=request.get("keyword", ""), limit=5
                )
                fetched.append(result.render())
            if fetched:
                messages = messages + [
                    {"role": "assistant", "content": getattr(reply, "text", "") or ""},
                    {
                        "role": "user",
                        "content": "以下是查询结果，请据此直接回答刚才的问题，"
                        "不要再请求查询，也不要说查不到：\n\n" + "\n\n".join(fetched),
                    },
                ]
                reply = await complete(messages)
    except GroupAssistantUnavailable:
        # 没有模型可用不算「分析失败」，不写脏记录，交给调用方决定 503。
        db.delete(run)
        db.flush()
        raise
    except GroupAssistantError as exc:
        _finish_failed(db, run, str(exc))
        return run
    except Exception as exc:  # noqa: BLE001 - 任何模型侧异常都要留痕
        logger.exception("group_assistant.complete_failed")
        _finish_failed(db, run, f"模型调用异常：{exc}")
        return run

    run.model = getattr(reply, "model", None)
    try:
        analysis = parse_analysis(getattr(reply, "text", "") or "")
    except GroupAssistantAnalysisFailed as exc:
        _finish_failed(db, run, str(exc))
        return run

    run.output_json = json.dumps(
        {
            "summary": analysis.summary,
            "todos": [
                {
                    "title": item.title,
                    "detail": item.detail,
                    "assignee": item.assignee_hint,
                    "priority": item.priority,
                    "confidence": item.confidence,
                }
                for item in analysis.todos
            ],
            "decisions": analysis.decisions,
            "risks": analysis.risks,
        },
        ensure_ascii=False,
    )

    members = group_chat.group_member_users(db, group.id)
    owner = next((user for member, user in members if member.role == "owner"), None)
    created: list[GroupTodo] = []
    stamp = now()
    for item in analysis.todos:
        assignee = _resolve_assignee(members, item.assignee_hint) or owner
        if assignee is None:
            continue
        if _already_exists(
            db, group_id=group.id, assignee_id=assignee.id, title=item.title
        ):
            continue
        detail = item.detail
        if item.assignee_hint and assignee.display_name != item.assignee_hint:
            detail = (detail + f"\n（AI 原指派人：{item.assignee_hint}）").strip()
        # 同一件事拆成几条时并进已有那条（群助手一次也可能把细节拆开说）
        from app.services import todo_topics

        todo, merged = todo_topics.merge_or_create(
            db,
            assignee_id=assignee.id,
            title=item.title,
            detail=detail,
            priority=item.priority,
            due_at=item.due_at,
            group_id=group.id,
            source_message_id=message_ids[-1] if message_ids else None,
        )
        if merged:
            continue
        todo.ai_run_id = run.id
        todo.confidence = item.confidence
        created.append(todo)

    watch_notes: list[str] = []
    if analysis.watches or analysis.unwatch:
        requester = _resolve_requester(members, focus_sender) or owner
        if requester is None:
            watch_notes.append("没找到要盯单的人，订阅没建。")
        else:
            watch_notes = _apply_watches(
                db,
                user_id=requester.id,
                watches=analysis.watches,
                unwatch=analysis.unwatch,
            )

    if analysis.memory:
        previous = load_memory(db, group.id)
        # 累计：这次只看了纪要之后的那几条，覆盖总数要接着上一次往上加
        already = previous.covered_message_count if previous is not None else 0
        save_memory(
            db,
            group=group,
            summary=analysis.memory,
            covered_message_id=message_ids[-1] if message_ids else None,
            covered_message_count=already + len(message_ids),
        )

    run.todos_created = len(created)
    run.status = "done"
    run.finished_at = now()

    group_chat.post_message(
        db,
        group=group,
        sender=None,
        sender_kind="ai",
        kind="ai",
        content=_render_ai_message(analysis, created, members, watch_notes),
        parts=[
            {
                "type": "group_analysis",
                "run_id": run.id,
                "todos": [
                    {
                        "id": todo.id,
                        "title": todo.title,
                        "assignee_user_id": todo.assignee_user_id,
                        "priority": todo.priority,
                        "status": todo.status,
                    }
                    for todo in created
                ],
            }
        ],
    )
    db.flush()
    return run


def _resolve_requester(
    members: list[tuple[object, User]], hint: str | None
) -> User | None:
    """谁说的这句话，就给谁建订阅（提示词要求只订阅最后说话的人）。"""
    needle = (hint or "").strip()
    if not needle:
        return None
    for _, user in members:
        if (user.display_name or "").strip() == needle or (user.username or "").strip() == needle:
            return user
    for _, user in members:
        name = (user.display_name or "").strip()
        if name and (needle in name or name in needle):
            return user
    return None


def _apply_watches(
    db: Session,
    *,
    user_id: str,
    watches: list[ParsedWatch],
    unwatch: list[str],
) -> list[str]:
    """把模型给出的订阅意图落成真实订阅，返回给人看的一行行说明。

    订阅只读：不改 MES 单据；失败（单号不存在 / 没权限 / 已走完）照样说明白，
    不能让群里以为「盯上了」其实没盯。
    """
    from app.services import doc_watch

    notes: list[str] = []
    for doc_no in unwatch[:10]:
        try:
            removed = doc_watch.cancel(db, user_id=user_id, doc_no=doc_no)
        except Exception:  # noqa: BLE001
            logger.exception("group_assistant.unwatch_failed")
            notes.append(f"{doc_no} 取消失败了")
            continue
        notes.append(f"不盯 {doc_no} 了" if removed else f"{doc_no} 本来就没在盯")

    for item in watches[:5]:
        try:
            watch, fallback = doc_watch.create_for_user(
                db,
                user_id=user_id,
                doc_no=item.doc_no,
                step=item.step,
                note=item.note,
            )
        except doc_watch.WatchError as exc:
            notes.append(str(exc))
            continue
        except Exception as exc:  # noqa: BLE001 - 权限/绑定/网络都要说人话
            logger.warning("group_assistant.watch_failed %s: %s", item.doc_no, exc)
            notes.append(str(exc))
            continue
        line = doc_watch.describe(watch)
        if fallback:
            notes.append(fallback)
        notes.append(f"已{line}，有变化我会在这里说")
    return notes


def _render_ai_message(
    analysis: ParsedAnalysis,
    created: list[GroupTodo],
    members: list[tuple[object, User]],
    watch_notes: list[str] | None = None,
) -> str:
    name_by_id = {user.id: user.display_name or user.username for _, user in members}
    lines: list[str] = []
    if analysis.reply:
        lines.append(analysis.reply)
    elif analysis.summary:
        lines.append(analysis.summary)
    for note in (watch_notes or []):
        lines.append(note)
    if created:
        lines.append("")
        lines.append("我整理出这些待办：")
        for todo in created:
            who = name_by_id.get(todo.assignee_user_id, "未指派")
            due = todo.due_at.strftime("%m-%d") if todo.due_at else ""
            suffix = f"（{due} 前）" if due else ""
            lines.append(f"- @{who} {todo.title}{suffix}")
    elif not analysis.reply:
        lines.append("这轮讨论里没有新的可执行待办。")
    fresh_risks = [r for r in analysis.risks if r and r not in (analysis.reply or "")]
    if fresh_risks:
        lines.append("")
        lines.append("风险：" + "；".join(fresh_risks))
    return "\n".join(lines).strip()


def _finish_failed(db: Session, run: ProjectGroupAiRun, error: str) -> None:
    run.status = "failed"
    run.error = error[:2000]
    run.finished_at = now()
    db.flush()


def list_my_todos(
    db: Session, *, user_id: str, status: str | None = None, limit: int = 100
) -> list[GroupTodo]:
    stmt = select(GroupTodo).where(GroupTodo.assignee_user_id == user_id)
    if status:
        stmt = stmt.where(GroupTodo.status == status)
    else:
        stmt = stmt.where(GroupTodo.status != "dismissed")
    return list(
        db.scalars(stmt.order_by(GroupTodo.created_at.desc()).limit(max(1, min(limit, 200))))
    )


def set_todo_status(
    db: Session, *, todo: GroupTodo, actor: User, status: str
) -> GroupTodo:
    if status not in ("pending", "accepted", "doing", "done", "dismissed"):
        raise group_chat.InvalidGroupInput(f"不支持的待办状态：{status}")
    if todo.assignee_user_id != actor.id:
        # 组长可以关掉别人的待办，但普通成员只能动自己的。
        if todo.group_id:
            group = db.get(ProjectGroup, todo.group_id)
            if group is not None and group.owner_user_id != actor.id:
                raise group_chat.GroupPermissionDenied("只能修改分配给自己的待办")
        else:
            raise group_chat.GroupPermissionDenied("只能修改分配给自己的待办")
    todo.status = status
    todo.updated_at = now()
    # 「多久没动」只认人的动作，AI 催办不改这个时间
    todo.last_action_at = now()
    todo.completed_at = now() if status == "done" else None
    db.flush()
    return todo
