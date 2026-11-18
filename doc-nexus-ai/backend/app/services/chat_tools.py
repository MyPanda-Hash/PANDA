"""对话可用的工具（Anthropic tools 格式）。

设计约束：
- **只读**。这里没有任何写操作；要派人做事走待办，不改 MES。
- **按调用者身份取数**。每个工具执行时都带 user 的 MES token，
  由 mes_capability 内部做面板级权限校验（2026-11-17 起）。
- 工具描述用业务语言（销售订单、生产加工单），和 MES 页面上的名字一致。
"""

from __future__ import annotations

from typing import Any, Callable

from app.services import mes_capability, period_reports, web_search
from app.services.mes_capability import MesCapabilityError

MES_PANELS = {
    "SO_ORDER": "销售订单",
    "MANU_ORDER": "生产加工单",
    "PU_ORDER": "采购订单",
    "ARRIVAL_IN": "到货单",
    "STOCK_STATUS": "库存状况表",
}


TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "name": "web_search",
        "description": (
            "联网检索公开网页，拿到标题/摘要/链接。凡是需要实时信息或站外资料——"
            "新闻、政策、行业数据、公司情况、产品资料、天气以外的常识——都先调它，"
            "不要凭记忆回答，也不要声称自己没有联网能力。"
            "一次检索通常就够：除非结果里确实缺关键信息，不要为同一个问题反复检索。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "检索关键词，用业务语言，不要带引号",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "mes_query_panel",
        "description": (
            "查询一张业务单据的列表（只读）。panel 取面板编码；keyword 可填客户名、"
            "供应商名或单号前缀。返回条数、单号与关键字段。"
            "可查面板：" + "、".join(f"{k}（{v}）" for k, v in MES_PANELS.items())
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "panel": {"type": "string", "enum": list(MES_PANELS)},
                "keyword": {"type": "string", "description": "客户/供应商/单号前缀，可留空"},
            },
            "required": ["panel"],
        },
    },
    {
        "name": "mes_doc_detail",
        "description": "看一张单据的表头与明细（只读）。需要面板编码和单号。",
        "input_schema": {
            "type": "object",
            "properties": {
                "panel": {"type": "string", "enum": list(MES_PANELS)},
                "form_no": {"type": "string", "description": "单据编号"},
            },
            "required": ["form_no"],
        },
    },
    {
        "name": "mes_trace_doc",
        "description": "追踪一张单据的流程：走到哪个环节、卡在哪、下一步该做什么（只读）。",
        "input_schema": {
            "type": "object",
            "properties": {"doc_no": {"type": "string", "description": "单据编号"}},
            "required": ["doc_no"],
        },
    },
    {
        "name": "mes_stock",
        "description": "查库存结存（只读）。可按存货编码、仓库过滤，留空表示全部。",
        "input_schema": {
            "type": "object",
            "properties": {
                "item_code": {"type": "string", "description": "存货编码，可留空"},
                "warehouse": {"type": "string", "description": "仓库名，可留空"},
            },
        },
    },
    {
        "name": "generate_period_report",
        "description": (
            "生成日报/周报/月报并投递到当前用户的对话。用户在对话里说「生成本周周报」时调用。"
            "数字由系统口径产出，不要自己心算。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "kind": {"type": "string", "enum": ["daily", "weekly", "monthly"]},
                "anchor": {"type": "string", "description": "指定日期 yyyy-MM-dd，可留空表示当前周期"},
            },
        },
    },
]


def schemas() -> list[dict[str, Any]]:
    return TOOL_SCHEMAS


def run_tool(name: str, args: dict[str, Any], *, token: str, user_id: str) -> str:
    """执行一个工具，返回给模型看的纯文本。失败也返回文本，让模型自己解释。"""
    args = args or {}
    try:
        if name == "web_search":
            query = str(args.get("query") or "").strip()
            # 优先走百炼搜索增强：直接拿到带来源的结论，比标题列表有用得多
            return web_search.answer(query)

        if name == "mes_query_panel":
            out = mes_capability.query_panel(
                {"panel": args.get("panel"), "keyword": args.get("keyword", "")},
                token=token,
            )
            return str(out.get("text") or "")

        if name == "mes_doc_detail":
            out = mes_capability.doc_detail(
                {"panel": args.get("panel", ""), "form_no": args.get("form_no")},
                token=token,
            )
            return str(out.get("text") or "")

        if name == "mes_trace_doc":
            out = mes_capability.trace_doc({"doc_no": args.get("doc_no")}, token=token)
            return str(out.get("text") or "")

        if name == "mes_stock":
            out = mes_capability.stock(
                {
                    "item_code": args.get("item_code", ""),
                    "warehouse": args.get("warehouse", ""),
                },
                token=token,
            )
            return str(out.get("text") or "")

        if name == "generate_period_report":
            kind = str(args.get("kind") or "weekly").strip()
            result = period_reports.generate_sync(_db(), user=_user(user_id), kind=kind)
            title = str(result.get("title") or "")
            # 用 nxs-report: 伪协议把标题变成可点入口：
            # chat-demo 认这个前缀，点一下会 postMessage 给工作台打开报表查看器。
            # 不直接放 http 链接，是因为报表正文在会话里，不是独立页面。
            return (
                "已生成《[" + title + "](nxs-report:" + kind + ")》，点击标题即可查看。"
                + "请把上面的标题连同链接原样输出，不要改写成纯文本。"
            )
    except MesCapabilityError as exc:
        # 权限不足或参数不对：如实告诉模型，它会转告用户
        return "工具执行失败：" + str(exc)
    except Exception as exc:  # noqa: BLE001
        return "工具执行失败：" + str(exc)

    return "未知工具：" + name


def _db():
    from app.core.database import SessionLocal

    return SessionLocal()


def _user(user_id: str):
    from sqlalchemy import select

    from app.models.auth import User

    db = _db()
    return db.scalar(select(User).where(User.id == user_id))