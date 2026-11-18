"""MOSS-Live 全双工语音中继。

为什么要中继，而不是浏览器直连上游：

1. 浏览器原生 WebSocket 无法在握手时设置 ``Authorization`` 头；
2. API Key 不能出现在前端代码或网络请求里；
3. 上游对 ``session.update`` 的字段校验很严，多一个键就直接断连，
   会话配置（人设、指令、工具）只能由服务端构造并清洗；
4. 业务工具必须在服务端执行，MES 凭据不出后端。

因此浏览器只连 ``/api/assistant/voice/ws``，上行是裸 PCM 帧，
下行是中继转发的事件与音频；密钥、人设、工具全部留在服务端。

安全边界（与群助手一致）：**v1 全部只读**。语音不能改 MES 数据，
也不能建待办、不能审批——避免一句话把生产数据说坏。
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import urllib.parse
from typing import Any

from sqlalchemy import select

from app.core.config import settings
from app.services.group_tools import QUERYABLE_PANELS, query_business

logger = logging.getLogger(__name__)

UPSTREAM_OPEN_TIMEOUT = 15.0
HANDSHAKE_TIMEOUT = 15.0
OPENING_TIMEOUT_SECONDS = 12.0
TOOL_TIMEOUT_SECONDS = 20.0
# 上行音频：16 kHz 单声道 PCM16，20 ms 一帧 = 320 采样 = 640 字节
UPLINK_FRAME_BYTES = 640


class VoiceUnavailable(RuntimeError):
    """语音通道不可用（未配置密钥、被关闭）。"""


class VoiceHandshakeError(RuntimeError):
    """与上游建立会话失败。"""


# --------------------------------------------------------------------------- #
# 工具：全部只读，声明为 async_query 让用户能继续说下一句
# --------------------------------------------------------------------------- #

_TOOL_SPECS: tuple[tuple[str, str, str, str], ...] = (
    (
        "query_sales_orders",
        "查询销售订单。按客户名、客户简称或单据号前缀过滤；不传 customer 时返回最近的订单。",
        "customer",
        "客户名称或单据号关键字，例如「中天」「SO-20261112」",
        "SO_ORDER",
    ),
    (
        "query_work_orders",
        "查询生产加工单（车间也叫制造单、工单；MES 面板名是「生产加工单」），可看排产、车间、负责人、状态。",
        "code",
        "生产单号、销售订单号或产品名称关键字",
        "MANU_ORDER",
    ),
    (
        "query_purchase_orders",
        "查询采购订单，可看供应商、交货日期、发货状态。",
        "supplier",
        "供应商名称或采购单号关键字",
        "PU_ORDER",
    ),
    (
        "query_arrivals",
        "查询到货单与来料检验状态。",
        "supplier",
        "供应商名称或到货单号关键字",
        "ARRIVAL_IN",
    ),
    (
        "query_inventory",
        "查询库存状况表（MES 里的名字就是「库存状况表」）：存货编码、名称、仓库、数量、单价、金额。",
        "item",
        "存货编码或存货名称关键字",
        "STOCK_STATUS",
    ),
)


def tool_definitions() -> list[dict[str, Any]]:
    """下发给上游的工具表。

    同一应用的所有会话必须用同一份定义（名称、顺序、描述逐字不变），
    否则上游会认为工具列表被替换，缓存的调用会失效。
    """
    tools: list[dict[str, Any]] = []
    for name, description, param, param_desc, _panel in _TOOL_SPECS:
        tools.append(
            {
                "type": "function",
                "name": name,
                "description": description,
                "parameters": {
                    "type": "object",
                    "properties": {param: {"type": "string", "description": param_desc}},
                    "required": [],
                },
                "execution_mode": "async_query",
                "read_only": True,
            }
        )
    tools.append(
        {
            "type": "function",
            "name": "watch_doc",
            "description": (
                "盯着某张单，一有变化就主动告诉用户（订阅式，不是查询）。"
                "用户说「盯着 PO-2026-08-0001」「这单到货了告诉我」「它一有动静就通知我」时用。"
                "step 填要盯的环节关键字（如 到货/入库/检验/报工），不填表示任何推进都报。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "doc_no": {"type": "string", "description": "要盯的单号"},
                    "step": {"type": "string", "description": "只盯某个环节，如 到货；可留空"},
                    "note": {"type": "string", "description": "附加说明，会一起写进提醒"},
                },
                "required": ["doc_no"],
            },
            "execution_mode": "async_query",
            "read_only": True,
        }
    )
    tools.append(
        {
            "type": "function",
            "name": "list_watches",
            "description": "看当前用户在盯哪些单。用户问「我都在盯什么」时用。",
            "parameters": {"type": "object", "properties": {}, "required": []},
            "execution_mode": "async_query",
            "read_only": True,
        }
    )
    tools.append(
        {
            "type": "function",
            "name": "cancel_watch",
            "description": "取消盯单。用户说「别盯了」「不用跟了」时用。",
            "parameters": {
                "type": "object",
                "properties": {"doc_no": {"type": "string", "description": "取消盯的单号"}},
                "required": ["doc_no"],
            },
            "execution_mode": "async_query",
            "read_only": True,
        }
    )
    tools.append(
        {
            "type": "function",
            "name": "production_briefing",
            "description": (
                "生产概况汇报：今天生产怎么样、有没有超期工单、哪些物料低库存、"
                "有多少待审批。问「汇报一下今天生产」「有什么异常」「产线正常吗」时用这个，"
                "它会一次把几块数据拼成一段话。按当前账号的 MES 权限取数，没权的部分会说明。"
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
            "execution_mode": "async_query",
            "read_only": True,
        }
    )
    tools.append(
        {
            "type": "function",
            "name": "list_my_todos",
            "description": "查询当前登录用户自己名下未完成的待办任务。",
            "parameters": {"type": "object", "properties": {}, "required": []},
            "execution_mode": "async_query",
            "read_only": True,
        }
    )
    tools.append(
        {
            "type": "function",
            "name": "query_todo_status",
            "description": (
                "查待办的进展：谁还没开始、谁卡住了、谁超期了。"
                "person 写人名或岗位；不写就是查自己的。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "person": {
                        "type": "string",
                        "description": "要看谁，姓名或岗位；留空表示自己；管理员可以说「所有人」",
                    },
                    "keyword": {"type": "string", "description": "只看标题含这个词的待办"},
                },
                "required": [],
            },
            "execution_mode": "async_query",
            "read_only": True,
        }
    )
    tools.append(
        {
            "type": "function",
            "name": "trace_doc",
            "description": (
                "单据流程追踪：这单走到哪一步、卡在哪、哪道工序还没做完、下一步该做什么。"
                "doc_no 传任意环节的单号（销售订单号、生产加工单号、入库单号都行），"
                "MES 会自己串出整条链路。问「走到哪了」「卡在哪」「哪道工序」时用这个，"
                "不要用列表查询去猜。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "doc_no": {"type": "string", "description": "单据编号，例如 SO-20261112-003"}
                },
                "required": ["doc_no"],
            },
            "execution_mode": "async_query",
            "read_only": True,
        }
    )
    tools.append(
        {
            "type": "function",
            "name": "doc_detail",
            "description": (
                "看一张单据的表头和明细（物料、数量、交期、单价等）。"
                "只要给单号，系统会自己从流程里认出这是哪种单据。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "form_no": {"type": "string", "description": "单据编号，例如 SO-20261112-003"}
                },
                "required": ["form_no"],
            },
            "execution_mode": "async_query",
            "read_only": True,
        }
    )
    if settings.moss_voice_write_enabled:
        tools.append(
            {
                "type": "function",
                "name": "assign_todo",
                "description": (
                    "给某个人派一条待办。person 写姓名或岗位（例如「张仓管」「仓管」）；"
                    "不写 person 就是派给自己。只写一件具体的事，不要一次派一堆。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "person": {
                            "type": "string",
                            "description": "接收人的姓名或岗位，例如「王销售」「仓管」；留空表示自己",
                        },
                        "title": {
                            "type": "string",
                            "description": "要做什么，一句话，动词开头，例如「对接中天新一批订单」",
                        },
                        "detail": {
                            "type": "string",
                            "description": "补充说明，可省略",
                        },
                        "priority": {
                            "type": "string",
                            "description": "优先级：low / normal / high / urgent，默认 normal",
                        },
                    },
                    "required": ["title"],
                },
                "execution_mode": "async_effect",
                "read_only": False,
            }
        )
        tools.append(
            {
                "type": "function",
                "name": "nudge_todo",
                "description": (
                    "在群里催某个人一次。对方名下有多条待办时先问清是哪一条，不要挨个催。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "person": {"type": "string", "description": "催谁，姓名或岗位"},
                        "keyword": {
                            "type": "string",
                            "description": "待办标题关键字，用来在多条里定位一条",
                        },
                    },
                    "required": [],
                },
                "execution_mode": "async_effect",
                "read_only": False,
            }
        )
        tools.append(
            {
                "type": "function",
                "name": "send_notice",
                "description": (
                    "给对方发一条能看到的通知，落在你们共同的群聊里。"
                    "找不到共同群聊时会失败，那时改用 assign_todo。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "person": {"type": "string", "description": "接收人姓名或岗位"},
                        "message": {"type": "string", "description": "通知内容，一两句话"},
                    },
                    "required": ["person", "message"],
                },
                "execution_mode": "async_effect",
                "read_only": False,
            }
        )
    return tools


_TOOL_BY_NAME = {spec[0]: spec for spec in _TOOL_SPECS}

_INSTRUCTIONS = """你是「{persona}」，MES 制造执行系统的 AI 数字员工，正在和工厂员工语音通话。

说话规则：
1. 口语、简短。一次回答不超过三句，先给结论再给细节。
2. 这是语音，不要用列表符号、markdown、表格、emoji。用「第一」「第二」这样的口头顺序。
3. 涉及订单、工单、库存、采购、到货、待办这类业务数据时，**必须调用工具查**，不许凭印象编。
4. 查不到就直接说查不到，不要猜，也不要让人自己去系统里查——你能查。
5. 用户打断你时立即停，听他说完再答。

数字读法（很重要，念错等于没说）：
- 单据号逐位念，不要换算成中文数字。例如 SO-20261112-003 念作「S-O 单，二零二六一一一二，零零三」。
- 金额超过一万时说「X 万 Y」，不念完整阿拉伯数字串。
- 数量、单价照原样念，但要带单位。

你能做的事：
- 查询业务单据与库存（只读）。
- 给人派待办（assign_todo）、给人发通知（send_notice）。
- 你**不能**修改 MES 单据、不能审批、不能删除任何东西。用户要求改单据时，
  说明你改不了，让他去系统里操作；如果他的意思是「提醒某人去做」，那就派待办或发通知。

派活的规矩（照做，不要自作主张）：
1. 先确认人，再动手。用户说「让仓管补一下」就查得到张仓管；说不出人名又对不上号时，
   把工具返回的候选人念给他听，问他派给谁，**不要自己挑一个**。
2. 一次只派一件。用户一句话里有多件事，先说「我先派第一件」，做完再问下一件。
3. 派完要回报给谁、派了什么，一句话说清，例如「已经派给张仓管了：补充包装木箱库存」。
4. 工具报错就照实说没派成、为什么，不要假装成功。

跟进待办的规矩：
- 有人问「那个事做了没」「谁还没开始」，用 query_todo_status 查，**天数由工具给，不要自己算**。
- 用户让你催，用 nudge_todo。对方名下有好几条时先问催哪条，不要一次全催。
- 刚催过会被冷却挡回来，照实说「X 小时前刚催过」，不要换个说法再催一遍。"""


def build_instructions(display_name: str = "", opening: str = "") -> str:
    persona = (settings.moss_voice_persona or "小缔").strip() or "小缔"
    body = _INSTRUCTIONS.format(persona=persona)
    if display_name:
        body += f"\n\n当前对话的用户是「{display_name}」，称呼他时可以带名字。"
    if opening:
        body += (
            "\n\n【开场播报】接通后的第一句话，先把这个概况用口语说给用户听，"
            "一到两句，别念编号、别逐条重复数字，也不用说「根据数据」这类废话；"
            "说完停一下，等他要下一步。概况内容："
            + opening
        )
    return body


def opening_briefing(user_id: str) -> str:
    """通话开始时的概况文本；取不到或关掉开关就返回空串（不带病开场）。"""
    if not settings.moss_voice_briefing_enabled:
        return ""
    try:
        from app.services import production_briefing

        briefing = production_briefing.for_user(user_id)
    except Exception:  # noqa: BLE001 - 播报失败不能拦住通话
        logger.warning("moss_voice.opening_briefing_failed", exc_info=True)
        return ""
    if not briefing.ok:
        return ""
    return _clip_for_speech(briefing.speak(), int(settings.moss_voice_briefing_chars or 220))


def _clip_for_speech(text: str, limit: int) -> str:
    """把概况裁到可念的长度：按分句截，不在数字中间切，截断处补句号。"""
    limit = max(20, limit)
    body = (text or "").strip()
    if len(body) <= limit:
        return body
    head = body[:limit]
    for separator in ("；", "，", "。"):
        index = head.rfind(separator)
        if index >= 20:
            return head[: index + 1]
    return head.rstrip("，、；") + "。"


# --------------------------------------------------------------------------- #
# 工具执行（同步，调用方用 to_thread 包起来）
# --------------------------------------------------------------------------- #




def _panel_of_doc(form_no: str) -> str:
    """从流程追踪里反查单号属于哪个面板。

    语音不该让用户或模型去记内部面板编码，所以面板从 MES 的流程数据里自己认。
    """
    if not form_no:
        return ""
    from app.services.mes_client import MesClient

    try:
        payload = MesClient().trace(form_no)
    except Exception:  # noqa: BLE001 - 追不到就让上层给出提示
        return ""
    for row in payload.get("route") or []:
        if isinstance(row, dict) and str(row.get("单号") or "") == form_no:
            return str(row.get("面板编码") or "")
    return ""


def _mes_read(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """单据追踪 / 详情：复用主对话同一份 MES 只读能力，两个入口不各说各话。"""
    from app.services import mes_capability

    try:
        if name == "trace_doc":
            result = mes_capability.trace_doc(arguments)
            meta = {
                "ok": "error" not in result,
                "panel": "TRACE",
                "panelName": "流程追踪",
                "docNo": result.get("docNo", ""),
                "count": result.get("total", 0),
                "done": result.get("done", 0),
                "pending": result.get("pending", 0),
                "stalled": result.get("stalled", ""),
                "route": result.get("route", []),
            }
        else:
            form_no = str(arguments.get("form_no") or "").strip()
            panel = _panel_of_doc(form_no)
            if not panel:
                return {
                    "text": f"没在流程里找到单号 {form_no}，换个完整单号再试。",
                    "meta": {"ok": False},
                }
            result = mes_capability.doc_detail({"panel": panel, "form_no": form_no})
            meta = {
                "ok": True,
                "panel": panel,
                "panelName": str(result.get("panelCode") or "单据"),
                "docNo": form_no,
                "count": 1,
            }
    except mes_capability.MesCapabilityError as exc:
        return {"text": str(exc), "meta": {"ok": False}}
    return {"text": str(result.get("text") or ""), "meta": meta}


def execute_tool(name: str, arguments_json: str, user_id: str) -> dict[str, Any]:
    """执行一个只读工具，返回 {"text": 给模型, "meta": 给前端卡片}。"""
    try:
        arguments = json.loads(arguments_json or "{}")
        if not isinstance(arguments, dict):
            arguments = {}
    except json.JSONDecodeError:
        arguments = {}

    if name == "watch_doc":
        return _watch_doc(user_id, arguments)
    if name == "list_watches":
        return _list_watches(user_id)
    if name == "cancel_watch":
        return _cancel_watch(user_id, arguments)
    if name == "production_briefing":
        return _production_briefing(user_id)
    if name == "list_my_todos":
        return _list_todos(user_id)
    if name == "query_todo_status":
        return _query_todo_status(user_id, arguments)
    if name in ("trace_doc", "doc_detail"):
        return _mes_read(name, arguments)
    if name in _WRITE_HANDLERS:
        return _WRITE_HANDLERS[name](user_id, arguments)

    spec = _TOOL_BY_NAME.get(name)
    if spec is None:
        return {"text": f"没有名为 {name} 的工具。", "meta": {"ok": False}}

    _name, _desc, param, _param_desc, panel = spec
    keyword = str(arguments.get(param) or "").strip()
    if panel not in QUERYABLE_PANELS:
        return {"text": "该面板不支持查询。", "meta": {"ok": False}}

    # 权限相适配：用这个人自己的 MES 账号查，先按 visiblePanels 挡一次
    from app.services import mes_user_client

    allowed, why = mes_user_client.can_query(panel, user_id)
    if not allowed:
        return {"text": why, "meta": {"ok": False, "kind": "permission", "panel": panel}}

    try:
        client, _ = mes_user_client.client_for_user(user_id)
        result = query_business(panel=panel, keyword=keyword, limit=5, client=client)
    except mes_user_client.MesPermissionError as exc:
        return {"text": str(exc), "meta": {"ok": False, "kind": "permission", "panel": panel}}
    except Exception as exc:  # noqa: BLE001 - 工具失败不能打断通话
        logger.warning("moss_voice.tool_failed %s: %s", name, exc)
        return {"text": "查询失败，稍后再试。", "meta": {"ok": False}}

    rows = result.rows
    meta = {
        "ok": result.ok,
        "panel": panel,
        "panelName": result.panel_name,
        "keyword": keyword,
        "count": len(rows),
        "items": [
            {
                "title": str(row.get(QUERYABLE_PANELS[panel]["no"], "") or ""),
                "subtitle": str(
                    row.get("客户") or row.get("供应商") or row.get("存货") or ""
                ),
                "status": str(row.get("单据状态") or row.get("检验状态") or ""),
                "extra": str(
                    row.get("金额")
                    or row.get("数量")
                    or row.get("预完工日")
                    or row.get("交货日期")
                    or ""
                ),
            }
            for row in rows[:5]
        ],
    }
    return {"text": result.render(limit=5), "meta": meta}


def _production_briefing(user_id: str) -> dict[str, Any]:
    """语音里的「汇报今天生产」：按这个人的 MES 权限取数，拼成可念的一段话。"""
    from app.services import mes_user_client, production_briefing

    try:
        client, _ = mes_user_client.client_for_user(user_id)
    except mes_user_client.MesPermissionError as exc:
        return {"text": str(exc), "meta": {"ok": False, "kind": "permission"}}

    def can(panel: str) -> bool:
        allowed, _ = mes_user_client.can_query(panel, user_id)
        return allowed

    def fetch(panel: str) -> list[dict[str, Any]]:
        return client.query_panel(panel, page_size=100)

    try:
        briefing = production_briefing.build(fetch=fetch, can=can)
    except MesWatchError as exc:
        logger.warning("moss_voice.briefing_failed: %s", exc)
        return {"text": "取数失败，稍后再问一次。", "meta": {"ok": False}}

    return {
        "text": briefing.speak(),
        "meta": {
            "ok": briefing.ok,
            "kind": "briefing",
            "panelName": "生产概况",
            "count": len(briefing.items),
            "items": briefing.items[:5],
            "skipped": briefing.skipped,
        },
    }


# --------------------------------------------------------------------------- #
# 派活：待办与通知
# --------------------------------------------------------------------------- #

# 普通员工只能给自己和同群的人派活；管理员不受限。这样既能协作，又不会被陌生人派单。
def _visible_users(db: Any, caller: Any) -> list[Any]:
    from sqlalchemy import select

    from app.models.auth import User
    from app.models.group_chat import ProjectGroupMember

    if (caller.role or "").strip() == "admin":
        return list(db.scalars(select(User).where(User.status == "active")))

    group_ids = list(
        db.scalars(
            select(ProjectGroupMember.group_id).where(
                ProjectGroupMember.user_id == caller.id
            )
        )
    )
    ids = {caller.id}
    if group_ids:
        ids.update(
            db.scalars(
                select(ProjectGroupMember.user_id).where(
                    ProjectGroupMember.group_id.in_(group_ids)
                )
            )
        )
    return list(db.scalars(select(User).where(User.id.in_(ids), User.status == "active")))


def resolve_people(db: Any, caller: Any, hint: str) -> list[Any]:
    """把「王销售」「仓管」「让李计划去排产」这类说法解析成具体账号。

    解析不出来或有歧义时返回空/多人，由调用方决定是问还是拒绝——绝不猜。
    """
    hint = (hint or "").strip()
    people = _visible_users(db, caller)
    if not hint:
        return [user for user in people if user.id == caller.id]

    lowered = hint.casefold()
    for strip_word in ("麻烦", "请", "让", "给", "通知", "告诉"):
        if lowered.startswith(strip_word):
            lowered = lowered[len(strip_word) :].strip()

    def names(user: Any) -> set[str]:
        return {(user.display_name or "").casefold(), (user.username or "").casefold()} - {""}

    exact = [user for user in people if lowered in names(user)]
    if exact:
        return exact

    # 岗位词命中显示名：「仓管」→「张仓管」
    partial = [user for user in people if (user.display_name or "") and lowered in user.display_name.casefold()]
    if partial:
        return partial

    # 反向命中：「让王销售去对接中天」里的整段话包含完整姓名
    reverse = [
        user
        for user in people
        if (user.display_name or "").strip() and user.display_name.casefold() in hint.casefold()
    ]
    return reverse


def _shared_group(db: Any, left_id: str, right_id: str) -> Any:
    from sqlalchemy import select

    from app.models.group_chat import ProjectGroup, ProjectGroupMember

    left = set(
        db.scalars(
            select(ProjectGroupMember.group_id).where(
                ProjectGroupMember.user_id == left_id
            )
        )
    )
    right = set(
        db.scalars(
            select(ProjectGroupMember.group_id).where(
                ProjectGroupMember.user_id == right_id
            )
        )
    )
    shared = left & right
    if not shared:
        return None
    return db.scalar(
        select(ProjectGroup)
        .where(ProjectGroup.id.in_(shared), ProjectGroup.status == "active")
        .order_by(ProjectGroup.updated_at.desc())
    )


def _candidate_names(db: Any, caller: Any) -> str:
    names = [user.display_name for user in _visible_users(db, caller) if user.display_name]
    return "、".join(sorted(names)[:12])


def _is_everyone(hint: str) -> bool:
    return (hint or "").strip() in ("所有人", "大家", "全部", "全员", "谁", "都")


def _query_todo_status(user_id: str, arguments: dict[str, Any]) -> dict[str, Any]:
    from app.core.database import transaction
    from app.models.auth import User
    from app.services import todo_followup

    person_hint = str(arguments.get("person") or "").strip()
    keyword = str(arguments.get("keyword") or "").strip()

    with transaction() as db:
        caller = db.get(User, user_id)
        if caller is None:
            return {"text": "找不到当前用户。", "meta": {"ok": False}}

        target: Any | None = None
        everyone = False
        if not person_hint:
            target = caller
        elif _is_everyone(person_hint):
            if (caller.role or "") != "admin":
                target = caller
            else:
                everyone = True
        else:
            matches = resolve_people(db, caller, person_hint)
            if not matches:
                return {
                    "text": (
                        f"找不到「{person_hint}」。可选的人是：{_candidate_names(db, caller)}。"
                    ),
                    "meta": {"ok": False, "kind": "unknown_person"},
                }
            if len(matches) > 1:
                options = "、".join(user.display_name for user in matches)
                return {
                    "text": f"「{person_hint}」对得上好几个人：{options}。问清楚看谁的。",
                    "meta": {"ok": False, "kind": "ambiguous"},
                }
            target = matches[0]

        overview = todo_followup.status_overview(
            db, assignee=None if everyone else target, keyword=keyword
        )
        return {
            "text": overview["text"],
            "meta": {
                "ok": True,
                "kind": "todo_status",
                "panelName": "待办进度",
                "count": overview["count"],
                "items": [
                    {
                        "title": item["title"],
                        "subtitle": item["person"],
                        "status": item["label"],
                        "extra": (
                            f"催过 {item['nudgeCount']} 次"
                            if item["nudgeCount"]
                            else f"{int(item['idleHours'] // 24)} 天"
                            if item["idleHours"] >= 24
                            else ""
                        ),
                    }
                    for item in overview["items"][:5]
                ],
            },
        }


def _nudge_todo(user_id: str, arguments: dict[str, Any]) -> dict[str, Any]:
    from app.core.database import transaction
    from app.models.auth import User
    from app.services import todo_followup

    refused = _write_refused()
    if refused:
        return refused

    person_hint = str(arguments.get("person") or "").strip()
    keyword = str(arguments.get("keyword") or "").strip()

    with transaction() as db:
        caller = db.get(User, user_id)
        if caller is None:
            return {"text": "找不到当前用户。", "meta": {"ok": False}}

        matches = resolve_people(db, caller, person_hint)
        if not matches:
            return {
                "text": f"找不到「{person_hint or '这个人'}」。可选的人是：{_candidate_names(db, caller)}。",
                "meta": {"ok": False, "kind": "unknown_person"},
            }
        if len(matches) > 1:
            options = "、".join(user.display_name for user in matches)
            return {
                "text": f"「{person_hint}」对得上好几个人：{options}。问清楚催谁。",
                "meta": {"ok": False, "kind": "ambiguous"},
            }

        target = matches[0]
        rows = todo_followup.open_todos(db, person_id=target.id, keyword=keyword, limit=10)
        if not rows:
            suffix = f"标题含「{keyword}」的" if keyword else ""
            return {
                "text": f"{target.display_name}名下没有{suffix}未完成的待办，不用催。",
                "meta": {"ok": True, "kind": "notice", "person": target.display_name},
            }
        if len(rows) > 1:
            listing = "；".join(todo_followup.describe(row) for row in rows[:3])
            return {
                "text": (
                    f"{target.display_name}名下有好几条：{listing}。问清要催哪一条，先别催。"
                ),
                "meta": {"ok": False, "kind": "ambiguous_todo", "count": len(rows)},
            }

        result = todo_followup.nudge(
            db, todo=rows[0], by_label=caller.display_name or caller.username
        )
        if not result.get("ok"):
            return {"text": str(result.get("reason") or "催不了"), "meta": {"ok": False}}
        return {
            "text": result["text"],
            "meta": {
                "ok": True,
                "kind": "nudge",
                "person": result["person"],
                "title": rows[0].title,
                "count": result["count"],
            },
        }


def _write_refused() -> dict[str, Any] | None:
    if not settings.moss_voice_write_enabled:
        return {
            "text": "语音派活功能当前是关闭的，只能查询。",
            "meta": {"ok": False, "kind": "disabled"},
        }
    return None


def _assign_todo(user_id: str, arguments: dict[str, Any]) -> dict[str, Any]:
    from app.core.database import transaction
    from app.models.auth import User
    from app.models.group_chat import GroupTodo
    from app.services.assistant_watch import ensure_feed_group

    refused = _write_refused()
    if refused:
        return refused

    title = str(arguments.get("title") or "").strip()
    if not title:
        return {"text": "没说清要做什么，让他再说一件具体的事。", "meta": {"ok": False}}
    person_hint = str(arguments.get("person") or "").strip()
    detail = str(arguments.get("detail") or "").strip()
    priority = str(arguments.get("priority") or "normal").strip().lower()
    if priority not in ("low", "normal", "high", "urgent"):
        priority = "normal"

    from app.core.security import new_token as _new_token
    from app.core.security import now as _now

    with transaction() as db:
        caller = db.get(User, user_id)
        if caller is None:
            return {"text": "找不到当前用户。", "meta": {"ok": False}}

        matches = resolve_people(db, caller, person_hint)
        if not matches:
            return {
                "text": (
                    f"找不到「{person_hint or '你说的这个人'}」。"
                    f"可选的人是：{_candidate_names(db, caller)}。问他派给谁。"
                ),
                "meta": {"ok": False, "kind": "unknown_person"},
            }
        if len(matches) > 1:
            options = "、".join(user.display_name for user in matches)
            return {
                "text": f"「{person_hint}」对得上好几个人：{options}。问清楚派给哪一个，先别派。",
                "meta": {"ok": False, "kind": "ambiguous", "candidates": options},
            }

        target = matches[0]
        existing = db.scalar(
            select(GroupTodo).where(
                GroupTodo.assignee_user_id == target.id,
                GroupTodo.title == title[:255],
                GroupTodo.status.in_(("pending", "accepted", "doing")),
            )
        )
        if existing is not None:
            return {
                "text": f"{target.display_name} 名下已经有一条「{title}」了，不用重复派。",
                "meta": {
                    "ok": True,
                    "duplicated": True,
                    "person": target.display_name,
                    "title": title,
                    "todoId": existing.id,
                    "kind": "todo",
                },
            }

        group = ensure_feed_group(db)
        from app.services import todo_topics

        todo, merged = todo_topics.merge_or_create(
            db,
            assignee_id=target.id,
            title=title,
            detail=(detail + "（语音派发，来自「" + (caller.display_name or caller.username) + "」）").strip(),
            priority=priority,
            created_by_user_id=caller.id,
            group_id=group.id if group is not None else None,
        )
        from app.services import inapp_notifications

        inapp_notifications.push(
            db,
            user_id=target.id,
            kind="todo_assigned",
            title=title,
            body=detail,
            ref_type="todo",
            ref_id=todo.id,
            group_id=todo.group_id,
            actor_name=caller.display_name or caller.username,
            dedupe_key=f"todo_assigned:{todo.id}",
        )
        logger.info(
            "moss_voice.todo_created from=%s to=%s title=%s", caller.id, target.id, title
        )
        return {
            "text": (
                f"这件事已经并到{target.display_name}的同一条待办里了：{title}。"
                if merged
                else f"已经派给{target.display_name}了：{title}。"
            ),
            "meta": {
                "ok": True,
                "kind": "todo",
                "person": target.display_name,
                "title": title,
                "todoId": todo.id,
                "priority": priority,
                "merged": merged,
                "mergedInto": todo.title if merged else "",
            },
        }


def _send_notice(user_id: str, arguments: dict[str, Any]) -> dict[str, Any]:
    from app.core.database import transaction
    from app.models.auth import User
    from app.services import group_chat

    refused = _write_refused()
    if refused:
        return refused

    message = str(arguments.get("message") or "").strip()
    if not message:
        return {"text": "没说清要通知什么。", "meta": {"ok": False}}
    person_hint = str(arguments.get("person") or "").strip()

    with transaction() as db:
        caller = db.get(User, user_id)
        if caller is None:
            return {"text": "找不到当前用户。", "meta": {"ok": False}}

        matches = resolve_people(db, caller, person_hint)
        if not matches:
            return {
                "text": (
                    f"找不到「{person_hint or '这个人'}」。"
                    f"可选的人是：{_candidate_names(db, caller)}。问他发给谁。"
                ),
                "meta": {"ok": False, "kind": "unknown_person"},
            }
        if len(matches) > 1:
            options = "、".join(user.display_name for user in matches)
            return {
                "text": f"「{person_hint}」对得上好几个人：{options}。问清楚发给哪一个。",
                "meta": {"ok": False, "kind": "ambiguous"},
            }

        target = matches[0]
        group = _shared_group(db, caller.id, target.id)
        if group is None:
            return {
                "text": (
                    f"你和{target.display_name}没有共同的群聊，通知发不到他那里。"
                    "要么直接给他派待办，要么先拉个群。"
                ),
                "meta": {"ok": False, "kind": "no_shared_group"},
            }

        row = group_chat.post_message(
            db,
            group=group,
            sender=None,
            sender_kind="ai",
            kind="ai",
            content=f"@{target.username} {message}",
            parts=[
                {
                    "type": "voice_notice",
                    "from": caller.display_name or caller.username,
                }
            ],
        )
        from app.services import inapp_notifications

        inapp_notifications.push(
            db,
            user_id=target.id,
            kind="notice",
            title=f"{caller.display_name or caller.username} 给你发了通知",
            body=message,
            ref_type="group",
            ref_id=group.id,
            group_id=group.id,
            actor_name=caller.display_name or caller.username,
            dedupe_key=f"notice:{row.id}",
        )
        logger.info(
            "moss_voice.notice_sent from=%s to=%s group=%s", caller.id, target.id, group.id
        )
        return {
            "text": f"已经在「{group.name}」里通知{target.display_name}了：{message}",
            "meta": {
                "ok": True,
                "kind": "notice",
                "person": target.display_name,
                "message": message,
                "groupId": group.id,
                "messageId": row.id,
            },
        }


_WRITE_HANDLERS = {
    "assign_todo": _assign_todo,
    "send_notice": _send_notice,
    "nudge_todo": _nudge_todo,
}


def _list_todos(user_id: str) -> dict[str, Any]:
    from app.core.database import transaction
    from app.models.group_chat import GroupTodo

    try:
        with transaction() as db:
            rows = (
                db.query(GroupTodo)
                .filter(
                    GroupTodo.assignee_user_id == user_id,
                    GroupTodo.status.in_(("pending", "accepted", "doing")),
                )
                .order_by(GroupTodo.created_at.desc())
                .limit(5)
                .all()
            )
            items = [
                {
                    "title": row.title,
                    "subtitle": (row.detail or "")[:60],
                    "status": row.status,
                    "extra": row.priority,
                }
                for row in rows
            ]
    except Exception as exc:  # noqa: BLE001
        logger.warning("moss_voice.todos_failed: %s", exc)
        return {"text": "待办读取失败。", "meta": {"ok": False}}

    if not items:
        return {
            "text": "你现在没有未完成的待办。",
            "meta": {"ok": True, "panel": "TODOS", "panelName": "我的待办", "count": 0, "items": []},
        }
    lines = [f"{i + 1}. {item['title']}（{item['extra']}）" for i, item in enumerate(items)]
    return {
        "text": "你名下未完成的待办有 " + str(len(items)) + " 条：" + "；".join(lines),
        "meta": {
            "ok": True,
            "panel": "TODOS",
            "panelName": "我的待办",
            "count": len(items),
            "items": items,
        },
    }


# --------------------------------------------------------------------------- #
# 可用性
# --------------------------------------------------------------------------- #


def voice_available() -> tuple[bool, str]:
    if not settings.moss_voice_enabled:
        return False, "语音通道已关闭"
    if not (settings.moss_api_key or "").strip():
        return False, "未配置 MOSS_API_KEY"
    if not (settings.moss_realtime_url or "").startswith("wss://"):
        return False, "MOSS_REALTIME_URL 必须是 wss://"
    return True, ""


def upstream_url(user_id: str) -> str:
    query = urllib.parse.urlencode(
        {"model": settings.moss_realtime_model, "user": f"mes:{user_id}"}
    )
    base = settings.moss_realtime_url.rstrip("/")
    return f"{base}?{query}"


# --------------------------------------------------------------------------- #
# 中继
# --------------------------------------------------------------------------- #

# 只转发给浏览器的事件；其余上游事件（工具汇总、内部确认）留在服务端
_FORWARD_EVENTS = frozenset(
    {
        "session_attached",
        "session.updated",
        "media.configured",
        "client_context.updated",
        "voice_changed",
        "input.speech.started",
        "input.speech.stopped",
        "input.transcript.updated",
        "input.transcript.done",
        "input.transcript.revised",
        "output.transcript.updated",
        "output.transcript.done",
        "response.audio.started",
        "response.audio.context",
        "response.audio.done",
        "response.audio.stop",
        "response.ended",
        "response.interrupted",
        "session.closing",
        "pong",
        "error",
    }
)

# 浏览器可以下发的指令；其余一律丢弃（防止被前端改写会话）
_CLIENT_EVENTS = frozenset({"ping", "mic_state", "video.state"})


class VoiceRelay:
    """一次通话的上下文。一个实例对应一条浏览器连接。"""

    def __init__(self, browser: Any, user: Any) -> None:
        self._browser = browser
        self._user = user
        self._upstream: Any = None
        self._tool_tasks: set[asyncio.Task] = set()
        self._seen_calls: set[str] = set()
        self._writes = 0
        self._closed = False
        self._started_at = time.monotonic()
        self._has_opening = False

    # -- 与浏览器 -------------------------------------------------------- #

    async def _to_browser(self, action: str, data: dict[str, Any]) -> None:
        if self._closed:
            return
        try:
            await self._browser.send_json({"action": action, "data": data})
        except Exception:  # noqa: BLE001 - 浏览器断开是常态
            self._closed = True

    async def _audio_to_browser(self, payload: bytes) -> None:
        if self._closed:
            return
        try:
            await self._browser.send_bytes(payload)
        except Exception:  # noqa: BLE001
            self._closed = True

    # -- 与上游 ---------------------------------------------------------- #

    async def _send_upstream(self, obj: dict[str, Any]) -> None:
        await self._upstream.send(json.dumps(obj, ensure_ascii=False))

    async def _recv_until(self, action: str) -> dict[str, Any]:
        """握手期间等一个特定事件，其余事件照常转发。"""
        deadline = time.monotonic() + HANDSHAKE_TIMEOUT
        while time.monotonic() < deadline:
            remaining = deadline - time.monotonic()
            message = await asyncio.wait_for(self._upstream.recv(), timeout=remaining)
            if isinstance(message, (bytes, bytearray)):
                continue
            try:
                event = json.loads(message)
            except json.JSONDecodeError:
                continue
            if event.get("action") == action:
                return event
            if event.get("action") == "error":
                raise VoiceHandshakeError(str(event.get("data", {}).get("error")))
            await self._forward(event)
        raise VoiceHandshakeError(f"等待 {action} 超时")

    async def _forward(self, event: dict[str, Any]) -> None:
        action = str(event.get("action") or "")
        if action not in _FORWARD_EVENTS:
            return
        await self._to_browser(action, event.get("data") or {})

    # -- 生命周期 -------------------------------------------------------- #

    async def run(self) -> None:
        import websockets

        ok, reason = voice_available()
        if not ok:
            await self._to_browser("pod.error", {"message": reason})
            return

        try:
            async with websockets.connect(
                upstream_url(self._user.id),
                additional_headers={"Authorization": f"Bearer {settings.moss_api_key.strip()}"},
                max_size=None,
                open_timeout=UPSTREAM_OPEN_TIMEOUT,
                close_timeout=2.0,
                proxy=None,  # 本机有 SOCKS 环境变量，上游是直连，必须显式绕过
            ) as upstream:
                self._upstream = upstream
                await self._handshake()
                await self._to_browser(
                    "pod.live",
                    {
                        "maxSeconds": settings.moss_voice_max_seconds,
                        "model": settings.moss_realtime_model,
                    },
                )
                await self._pump()
        except VoiceUnavailable as exc:
            await self._to_browser("pod.error", {"message": str(exc)})
        except (VoiceHandshakeError, asyncio.TimeoutError) as exc:
            logger.warning("moss_voice.handshake_failed: %s", exc)
            await self._to_browser("pod.error", {"message": "实时语音连接失败，请稍后重试"})
        except Exception as exc:  # noqa: BLE001 - 连接层异常统一收敛
            logger.warning("moss_voice.upstream_failed: %s", exc)
            await self._to_browser("pod.error", {"message": "实时语音连接中断"})
        finally:
            self._closed = True
            for task in list(self._tool_tasks):
                task.cancel()

    async def _opening_text(self) -> str:
        """开场播报文本：按这个人的 MES 权限取数，失败就不播（宁可安静也别瞎说）。"""
        try:
            text = await asyncio.wait_for(
                asyncio.to_thread(opening_briefing, self._user.id),
                timeout=OPENING_TIMEOUT_SECONDS,
            )
        except Exception:  # noqa: BLE001 - 含超时，播报失败不能影响通话
            logger.warning("moss_voice.opening_failed", exc_info=True)
            return ""
        self._has_opening = bool(text)
        return text

    async def _kick_off(self) -> None:
        """接通后主动触发第一轮，让它按 instructions 里的开场播报开口。"""
        if not self._has_opening:
            return
        try:
            await self._send_upstream({"action": "response.create", "response": {}})
        except Exception as exc:  # noqa: BLE001 - 上游不支持主动触发就退化成等用户先说话
            logger.info("moss_voice.kickoff_unsupported: %s", exc)

    async def _handshake(self) -> None:
        await self._send_upstream(
            {
                "action": "attach_session",
                "session_id": None,
                "memory_scope": "session",
                "delegation": {"type": "none"},
            }
        )
        attached = await self._recv_until("session_attached")
        session_id = (attached.get("data") or {}).get("session_id", "")

        await self._send_upstream(
            {
                "action": "session.update",
                "session": {
                    "persona": {"name": settings.moss_voice_persona or "小缔"},
                    "instructions": build_instructions(
                        getattr(self._user, "display_name", "") or "",
                        opening=await self._opening_text(),
                    ),
                    "tools": tool_definitions(),
                },
            }
        )
        updated = await self._recv_until("session.updated")
        error = str((updated.get("data") or {}).get("error") or "")
        if error:
            raise VoiceHandshakeError(f"会话配置被拒绝：{error}")

        await self._send_upstream(
            {
                "action": "media.configure",
                "profile": "standard",
                "output_audio": {"format": "pcm16", "sample_rate": 16000},
                "input_audio": {"format": "pcm16"},
                # 服务端按实时速度代为确认播放：悬浮窗只负责排队播放，
                # 不必逐块回执，弱网下也不会因为回执丢失而卡住。
                "playback": "server",
            }
        )
        configured = await self._recv_until("media.configured")
        config_error = str((configured.get("data") or {}).get("error") or "")
        if config_error:
            raise VoiceHandshakeError(f"音频配置被拒绝：{config_error}")

        await self._send_upstream(
            {
                "action": "client_context.update",
                "context": {"time_zone": "Asia/Shanghai"},
            }
        )
        logger.info(
            "moss_voice.session_opened user=%s session=%s opening=%s",
            self._user.id,
            session_id,
            "yes" if self._has_opening else "no",
        )
        # 音频通道就绪后再主动开口，否则第一句可能没有可播放的声道
        if self._has_opening:
            await self._kick_off()

    async def _pump(self) -> None:
        tasks = [
            asyncio.create_task(self._uplink()),
            asyncio.create_task(self._downlink()),
            asyncio.create_task(self._watchdog()),
        ]
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        for task in done:
            exc = task.exception()
            if exc:
                logger.warning("moss_voice.pump_stopped: %s", exc)

    async def _watchdog(self) -> None:
        await asyncio.sleep(max(30, settings.moss_voice_max_seconds))
        await self._to_browser(
            "pod.error", {"message": "本次通话已达时长上限，已自动挂断"}
        )

    async def _uplink(self) -> None:
        while True:
            try:
                message = await self._browser.receive()
            except Exception:  # noqa: BLE001 - 浏览器关闭
                return
            if message.get("type") == "websocket.disconnect":
                return
            if message.get("bytes") is not None:
                await self._upstream.send(message["bytes"])
                continue
            text = message.get("text")
            if not text:
                continue
            try:
                event = json.loads(text)
            except json.JSONDecodeError:
                continue
            action = str(event.get("action") or "")
            if action not in _CLIENT_EVENTS:
                continue
            if action == "ping":
                await self._send_upstream({"action": "ping"})
            elif action == "mic_state":
                await self._send_upstream(
                    {"action": "mic_state", "enabled": bool(event.get("enabled"))}
                )

    async def _downlink(self) -> None:
        while True:
            message = await self._upstream.recv()
            if isinstance(message, (bytes, bytearray)):
                await self._audio_to_browser(bytes(message))
                continue
            try:
                event = json.loads(message)
            except json.JSONDecodeError:
                continue

            action = str(event.get("action") or "")
            if action == "response.function_call_arguments.done":
                self._schedule_tool(event.get("data") or {})
                continue
            if action == "session.delegation.created":
                continue
            await self._forward(event)
            if action == "response.ended":
                await self._to_browser("pod.turn_end", {})

    # -- 工具 ------------------------------------------------------------ #

    def _schedule_tool(self, data: dict[str, Any]) -> None:
        call_id = str(data.get("call_id") or "")
        if not call_id or call_id in self._seen_calls:
            return
        self._seen_calls.add(call_id)
        task = asyncio.create_task(self._run_tool(data))
        self._tool_tasks.add(task)
        task.add_done_callback(self._tool_tasks.discard)

    async def _run_tool(self, data: dict[str, Any]) -> None:
        name = str(data.get("name") or "")
        call_id = str(data.get("call_id") or "")
        await self._to_browser(
            "pod.tool", {"state": "running", "name": name, "callId": call_id}
        )
        if name in _WRITE_HANDLERS and self._writes >= settings.moss_voice_write_limit:
            outcome: dict[str, Any] = {
                "text": "这次通话派的东西已经够多了，先把上面几件做完再说。",
                "meta": {"ok": False, "kind": "limit"},
            }
        else:
            if name in _WRITE_HANDLERS:
                self._writes += 1
            try:
                outcome = await asyncio.wait_for(
                    asyncio.to_thread(
                        execute_tool, name, str(data.get("arguments") or "{}"), self._user.id
                    ),
                    timeout=TOOL_TIMEOUT_SECONDS,
                )
            except asyncio.TimeoutError:
                logger.warning("moss_voice.tool_timeout %s", name)
                outcome = {"text": "操作超时，请再说一次。", "meta": {"ok": False}}
            except Exception as exc:  # noqa: BLE001
                logger.exception("moss_voice.tool_failed %s: %s", name, exc)
                outcome = {"text": "这个操作没做成，换个说法再试。", "meta": {"ok": False}}

        meta = outcome.get("meta", {})
        logger.info(
            "moss_voice.tool user=%s name=%s ok=%s kind=%s",
            self._user.id,
            name,
            meta.get("ok"),
            meta.get("kind") or "query",
        )
        await self._to_browser(
            "pod.tool",
            {
                "state": "done",
                "name": name,
                "callId": call_id,
                "message": outcome.get("text", ""),
                **{k: v for k, v in meta.items()},
            },
        )
        if self._closed:
            return
        try:
            await self._send_upstream(
                {
                    "action": "conversation.item.create",
                    "item": {
                        "type": "function_call_output",
                        "call_id": call_id,
                        "output": outcome.get("text", ""),
                    },
                }
            )
            await self._send_upstream(
                {"action": "response.create", "response": {"call_ids": [call_id]}}
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("moss_voice.tool_return_failed: %s", exc)


# 同一个人同时只允许一条语音连接：避免多标签页重复计费、互相打断。
# 记录 (占用时间, 客户端连接)，用来判断旧槽位是不是已经断了——
# 断线清理要等上游关闭握手，可能比新连接慢几秒，不能因此把人挡在外面。
_active: dict[str, tuple[float, Any]] = {}


def _client_gone(client: Any) -> bool:
    state = getattr(client, "client_state", None)
    if state is None:
        return False
    return getattr(state, "name", "") != "CONNECTED"


def claim_slot(user_id: str, client: Any = None) -> bool:
    """占用该用户的语音槽位；已有活连接时返回 False。"""
    existing = _active.get(user_id)
    if existing is not None:
        claimed_at, previous = existing
        expired = time.monotonic() - claimed_at > settings.moss_voice_max_seconds + 60
        if not expired and not _client_gone(previous):
            return False
        logger.info("moss_voice.steal_slot user=%s expired=%s", user_id, expired)
    _active[user_id] = (time.monotonic(), client)
    return True


def release_slot(user_id: str) -> None:
    _active.pop(user_id, None)


def active_count() -> int:
    return len(_active)

# --------------------------------------------------------------------------- #
# 订阅式盯单：'盯着这单，到货就告诉我'
# --------------------------------------------------------------------------- #


def _watch_trace_for(user_id: str, doc_no: str) -> dict[str, Any]:
    """取追踪前先确认这个人能看到该单号所在的面板（权限相适配）。"""
    from app.services import mes_user_client
    from app.services.group_tools import lookup_panel_of_trace
    from app.services.mes_client import MesClient

    payload = MesClient().trace(doc_no)
    if not payload:
        return {}
    panel = lookup_panel_of_trace(payload, doc_no) or str(payload.get("面板编码") or "")
    if panel:
        allowed, why = mes_user_client.can_query(panel, user_id)
        if not allowed:
            raise mes_user_client.MesPermissionError(why)
    return payload


def _watch_doc(user_id: str, arguments: dict[str, Any]) -> dict[str, Any]:
    from app.core.database import transaction
    from app.services import doc_watch

    doc_no = str(arguments.get("doc_no") or "").strip()
    target = str(arguments.get("step") or "").strip()
    condition = "stalled" if str(arguments.get("condition") or "").startswith("卡") else "progress"
    with transaction() as db:
        try:
            watch, fallback = doc_watch.create_for_user(
                db,
                user_id=user_id,
                doc_no=doc_no,
                step=target,
                note=str(arguments.get("note") or ""),
                condition=condition,
            )
        except doc_watch.WatchError as exc:
            return {"text": str(exc), "meta": {"ok": False}}
        except Exception as exc:  # noqa: BLE001 - 绑定/权限问题说人话
            return {"text": str(exc), "meta": {"ok": False, "kind": "permission"}}
        text = doc_watch.describe(watch)
    opening = f"好，{text}。"
    if fallback:
        opening = f"{fallback}。"
    return {
        "text": opening + "有变化我第一时间告诉你，不用再问。",
        "meta": {"ok": True, "kind": "watch", "panelName": "盯单", "count": 1,
                 "items": [{"title": doc_no, "status": target or condition, "extra": "已订阅"}]},
    }


def _list_watches(user_id: str) -> dict[str, Any]:
    from app.core.database import transaction
    from app.services import doc_watch

    with transaction() as db:
        rows = [w for w in doc_watch.watches_of(db, user_id) if w.status == "active"]
        items = [
            {"title": w.doc_no, "status": w.target_keyword or w.condition, "extra": w.panel_code}
            for w in rows[:8]
        ]
        count = len(rows)
    if not count:
        return {"text": "你现在没有盯任何单据。要盯的话说「盯着 XX 单，有变化告诉我」。",
                "meta": {"ok": True, "kind": "watch", "panelName": "盯单", "count": 0}}
    return {
        "text": f"你正在盯 {count} 张单：" + "、".join(w.doc_no for w in rows[:8]) + "。",
        "meta": {"ok": True, "kind": "watch", "panelName": "盯单", "count": count, "items": items},
    }


def _cancel_watch(user_id: str, arguments: dict[str, Any]) -> dict[str, Any]:
    from app.core.database import transaction
    from app.services import doc_watch

    doc_no = str(arguments.get("doc_no") or "").strip()
    with transaction() as db:
        removed = doc_watch.cancel(db, user_id=user_id, doc_no=doc_no)
    if not removed:
        return {"text": f"没找到你在盯的 {doc_no}。", "meta": {"ok": False}}
    return {"text": f"好，{doc_no} 不再盯了。", "meta": {"ok": True, "kind": "watch", "count": removed}}
