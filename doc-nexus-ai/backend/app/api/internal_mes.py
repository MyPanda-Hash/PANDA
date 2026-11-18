"""给 MES 用的内部通知接口：AI 派发的待办 + 群聊消息。

MES 顶栏的「待办 / 消息」图标要显示数字员工的待办与群聊消息，但两边账号体系不同，
所以由 MES 后端带上共享令牌（`X-Nexus-Internal-Token`）来这里取，再用
`mes_user_bindings` 把 MES 账号映射成 AI 用户。

只读：这里不替人点「完成」，也不替人发消息——动作仍然由人在各自的界面里执行。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Query
from fastapi.responses import JSONResponse
from sqlalchemy import select

from app.core.database import SessionLocal
from app.core.security import as_utc
from app.models.group_chat import GroupTodo, ProjectGroup, ProjectGroupMember, ProjectGroupMessage
from app.models.mes_binding import MesUserBinding
from app.services.init_org_chart import OrgChartError  # noqa: F401  (保持异常风格一致)
from app.api.init_wizard import _expected_token

router = APIRouter(prefix="/internal/v1/mes", tags=["internal-mes-notifications"])

MAX_TODOS = 50
MAX_MESSAGES = 30


def _guard(token: str | None) -> None:
    expected = _expected_token()
    if not expected:
        raise HTTPException(status_code=503, detail="内部接口未启用：未配置内部令牌")
    if (token or "").strip() != expected:
        raise HTTPException(status_code=401, detail="内部令牌无效")


def _text(value: object, limit: int = 400) -> str:
    flat = " ".join(str(value or "").split())
    return flat[:limit]


@router.get("/notifications")
def mes_notifications(
    mes_user: Annotated[str, Query(min_length=1, max_length=64)],
    since: Annotated[str, Query(max_length=32)] = "",
    x_nexus_internal_token: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    """按 MES 账号返回：未完成的 AI 待办 + 相关群聊在 `since` 之后的消息。

    `since` 是 MES 侧的已读游标（`YYYY-MM-DD HH:MM:SS`）：只回游标之后的消息，
    因此 MES 里清空未读之后列表会自然变空，不需要 MES 自己存消息副本。
    """
    _guard(x_nexus_internal_token)

    with SessionLocal() as db:
        binding = db.scalars(
            select(MesUserBinding).where(MesUserBinding.mes_username == mes_user)
        ).first()
        if binding is None:
            # 没绑定不是错误：MES 里的账号可能还没在数字员工这边登录过
            return JSONResponse(
                status_code=200,
                content={"bound": False, "todos": [], "messages": []},
            )

        user_id = binding.user_id
        todos = db.scalars(
            select(GroupTodo)
            .where(
                GroupTodo.assignee_user_id == user_id,
                GroupTodo.origin == "ai",
                GroupTodo.status.in_(("pending", "accepted", "doing")),
            )
            .order_by(GroupTodo.created_at.desc())
            .limit(MAX_TODOS)
        ).all()

        group_names = {
            row.id: row.name for row in db.scalars(select(ProjectGroup)).all()
        }
        memberships = db.scalars(
            select(ProjectGroupMember).where(ProjectGroupMember.user_id == user_id)
        ).all()
        messages = []
        if memberships:
            group_ids = [m.group_id for m in memberships]
            stmt = select(ProjectGroupMessage).where(
                ProjectGroupMessage.group_id.in_(group_ids),
                ProjectGroupMessage.deleted_at.is_(None),
            )
            cutoff = _parse_since(since)
            if cutoff is not None:
                stmt = stmt.where(ProjectGroupMessage.created_at > cutoff)
            rows = db.scalars(
                stmt.order_by(ProjectGroupMessage.created_at.desc()).limit(MAX_MESSAGES)
            ).all()
            for row in rows:
                messages.append(
                    {
                        "id": row.id,
                        "groupId": row.group_id,
                        "groupName": group_names.get(row.group_id, ""),
                        "senderKind": row.sender_kind,
                        "sender": row.sender_user_id or "",
                        "text": _text(row.content, 300),
                        "createdAt": as_utc(row.created_at).strftime("%Y-%m-%d %H:%M:%S")
                        if row.created_at
                        else "",
                    }
                )

    todo_items = []
    for row in todos:
        todo_items.append(
            {
                "id": row.id,
                "title": _text(row.title, 120),
                "detail": _text(row.detail, 400),
                "priority": row.priority or "",
                "status": row.status,
                "groupId": row.group_id or "",
                "groupName": group_names.get(row.group_id or "", ""),
                "refs": _parse_refs(getattr(row, "refs_json", None)),
                "createdAt": as_utc(row.created_at).strftime("%Y-%m-%d %H:%M:%S")
                if row.created_at
                else "",
            }
        )

    return JSONResponse(
        status_code=200,
        content={
            "bound": True,
            "todos": todo_items,
            "messages": messages,
            "since": since,
        },
    )


def _parse_since(raw: str):
    """解析 MES 传来的已读游标；解析不了就当没传（返回全部最近消息）。"""
    from datetime import datetime

    value = (raw or "").strip()
    if not value:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    return None


def filter_assignments(assignments: object, valid_panels: set[str]) -> list[dict]:
    """把模型给的分配结果收敛到「MES 真实存在的面板」上。

    模型偶尔会自造面板编码或写中文名，这里一律丢掉并记进 `droppedPanels`，
    让 MES 侧能看到「它想给但系统里没有」，而不是静默塞进权限表。
    """
    out: list[dict] = []
    for item in assignments or []:
        if not isinstance(item, dict):
            continue
        code = str(item.get("code") or "").strip()
        if not code:
            continue
        panels_raw = item.get("approvalPanels") or []
        kept = [str(p).strip() for p in panels_raw if str(p).strip() in valid_panels]
        dropped = [str(p).strip() for p in panels_raw if str(p).strip() and str(p).strip() not in valid_panels]
        out.append(
            {
                "code": code,
                "template": str(item.get("template") or "").strip(),
                "approvalPanels": kept,
                "reason": str(item.get("reason") or "").strip()[:300],
                "droppedPanels": dropped,
            }
        )
    return out


def _parse_refs(raw: object) -> list[dict]:
    """待办关联的 MES 单据：MES 侧据此拼出跳转路径。"""
    import json

    if not raw:
        return []
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        return []
    out = []
    for item in data if isinstance(data, list) else []:
        if not isinstance(item, dict):
            continue
        doc_no = _text(item.get("docNo") or item.get("doc_no"), 64)
        if not doc_no:
            continue
        out.append(
            {
                "panel": _text(item.get("panel"), 64),
                "docNo": doc_no,
                "label": _text(item.get("label"), 64),
            }
        )
    return out

# ─────────────────────── 初始化：AI 按组织架构与职位分配权限 ───────────────────────

ROLE_PLAN_PROMPT = """你是制造业 MES 的实施顾问，负责给每个员工分配系统权限。

输入会给你：
- depts：部门树（code/name/parentCode）
- employees：员工（code/name/title 职务/duties 岗位职责/dept 所属部门名称）
- panels：MES 的面板清单（code/name），code 是唯一可以填写的面板编码
- templates：可选岗位模板及该模板默认覆盖的面板

只输出一个 JSON 对象（json_object），不要任何解释文字或代码块。格式：
{
  "assignments": [
    {
      "code": "E001",
      "template": "质检",
      "approvalPanels": ["INSPECTION", "OUTSOURCE_INSPECTION"],
      "reason": "质检主管，职责含来料与委外检验，需要审批这两类检验单"
    }
  ],
  "warnings": ["无法判断的员工说明"]
}

规则：
1. approvalPanels 只能填 panels 里存在的 code，不能自造；不确定就留空数组。
2. 只有需要**审批（审核）**的岗位才给 approvalPanels：主管、经理、主任、负责人、或职责明确写了
   审核/审批/签核/放行。普通执行岗位（操作工、检验员、仓管员、业务员）不填。
3. template 从 templates 里选最贴近的一个；实在判断不了选「通用」。
4. 审批范围要和职能匹配：质检审批检验类、仓管主管审批出入库/盘点/调账、采购审批请购/订单/到货、
   销售审批订单/报价/销货、计划审批加工单/排产、财务审批收付款/发票/费用。
5. 一个人可能跨职能（如「生产部经理」管加工单也管领料），approvalPanels 可以跨板块。
6. 不要给任何人审批管理员类或系统配置面板；不确定就不给。
"""


def _strip_code_fence(text: str) -> str:
    import re

    raw = (text or "").strip()
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.IGNORECASE | re.MULTILINE).strip()
    if raw.startswith("{"):
        return raw
    start, end = raw.find("{"), raw.rfind("}")
    return raw[start : end + 1] if start >= 0 and end > start else raw


@router.post("/role-plan")
async def role_plan(
    body: dict,
    x_nexus_internal_token: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    """按部门树 + 员工职位 + 面板清单，让模型给出每个人的岗位模板与审批面板。"""
    _guard(x_nexus_internal_token)

    employees = body.get("employees") or []
    panels = body.get("panels") or []
    if not employees:
        raise HTTPException(status_code=400, detail="缺少 employees")
    if not panels:
        raise HTTPException(status_code=400, detail="缺少 panels（MES 面板清单）")

    prompt = dict(body)
    prompt.pop("employees", None)
    user_payload = {
        "depts": body.get("depts") or [],
        "panels": panels,
        "templates": body.get("templates") or [],
        "employees": employees,
    }
    import json as _json

    messages = [
        {"role": "system", "content": ROLE_PLAN_PROMPT},
        {"role": "user", "content": _json.dumps(user_payload, ensure_ascii=False)},
    ]

    from app.core.database import SessionLocal as _SessionLocal
    from app.services.plain_llm import LlmCallFailed, LlmUnavailable, complete_chat

    try:
        with _SessionLocal() as db:
            reply = await complete_chat(
                db, messages, temperature=0.1, max_tokens=4000,
                response_format_json=True, disable_thinking=True, timeout_seconds=120,
            )
    except (LlmUnavailable, LlmCallFailed) as exc:
        return JSONResponse(status_code=503, content={"code": "llm_unavailable", "message": str(exc)})

    try:
        data = _json.loads(_strip_code_fence(reply.text))
    except ValueError:
        return JSONResponse(
            status_code=400,
            content={"code": "bad_reply", "message": f"模型没有返回合法 JSON：{_strip_code_fence(reply.text)[:120]}"},
        )

    valid = {str(p.get("code") or "").strip() for p in panels if isinstance(p, dict)}
    out = filter_assignments(data.get("assignments"), valid)

    return JSONResponse(
        status_code=200,
        content={
            "assignments": out,
            "warnings": [str(w) for w in (data.get("warnings") or [])],
            "model": reply.model,
        },
    )
