"""群助手的 MES 查询工具：让「谁提交的」这类问题能真的查到答案。

为什么需要这层：
群助手原来只能读「聊天记录 + 待办 + 成员」，问到业务数据时只能说
「我查不到，请去系统里查」——这等于把问题推回给人。这里给它一条只读通道。

安全边界（与主动监控一致）：
- 只读，不写 MES
- 只暴露白名单面板与白名单字段（不回单据正文）
- 走 MES 开放接口的同一套凭据（MES_OPEN_API_TOKEN）
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

from app.core.config import settings
from app.services.mes_client import MesClient, MesWatchError

logger = logging.getLogger(__name__)

# 允许群助手查询的面板：只读、字段固定
QUERYABLE_PANELS: dict[str, dict[str, str]] = {
    "SO_ORDER": {
        "name": "销售订单",
        "no": "单据编号",
        "fields": "单据编号、客户、业务员、提交人、审核人、单据状态、单据日期、金额",
    },
    "MANU_ORDER": {
        # 名字必须和 MES panel_config.panel_name 一致，用户嘴里说的「制造单/工单」放别名
        "name": "生产加工单",
        "no": "编号",
        "fields": "编号、销售订单号、产品名称、生产车间、负责人、单据状态、开工日期、预完工日",
    },
    "PU_ORDER": {
        "name": "采购订单",
        "no": "单据编号",
        "fields": "单据编号、供应商、制单人、单据状态、交货日期、发货状态",
    },
    "ARRIVAL_IN": {
        "name": "到货单",
        "no": "单据编号",
        "fields": "单据编号、供应商、采购订单号、检验状态、到货日期、单据状态",
    },
    "STOCK_STATUS": {
        "name": "库存状况表",
        "no": "存货编码",
        "fields": "存货编码、存货、仓库、数量、单价、金额",
    },
}

# 每张单据只回这些字段（白名单，避免把正文带出去）
SAFE_FIELDS = {
    "SO_ORDER": ("单据编号", "客户", "业务员", "提交人", "审核人", "单据状态", "单据日期"),
    "MANU_ORDER": ("编号", "销售订单号", "产品名称", "生产车间", "负责人", "单据状态",
                   "开工日期", "预完工日期", "预完工日"),
    "PU_ORDER": ("单据编号", "供应商", "制单人", "单据状态", "交货日期", "发货状态"),
    "ARRIVAL_IN": ("单据编号", "供应商", "采购订单号", "检验状态", "到货日期", "单据状态"),
    "STOCK_STATUS": ("存货编码", "存货", "仓库", "数量", "单价", "金额"),
}


@dataclass
class QueryResult:
    panel: str
    panel_name: str
    keyword: str = ""
    rows: list[dict[str, Any]] = field(default_factory=list)
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error

    def render(self, limit: int = 5) -> str:
        if self.error:
            return f"查询{self.panel_name}失败：{self.error}"
        if not self.rows:
            return f"{self.panel_name}里没有匹配「{self.keyword}」的记录。"
        lines = [f"{self.panel_name}查到 {len(self.rows)} 条（最多显示 {limit} 条）："]
        for row in self.rows[:limit]:
            parts = [f"{k}={v}" for k, v in row.items() if v not in (None, "")]
            lines.append("- " + "；".join(parts))
        return "\n".join(lines)


def _safe_row(panel: str, row: dict[str, Any]) -> dict[str, Any]:
    keys = SAFE_FIELDS.get(panel, ())
    return {k: row.get(k) for k in keys if row.get(k) not in (None, "")}


def query_business(
    *,
    panel: str,
    keyword: str = "",
    limit: int = 5,
    client: MesClient | None = None,
) -> QueryResult:
    """查一个面板的业务单据（只读）。"""
    if panel not in QUERYABLE_PANELS:
        return QueryResult(
            panel=panel,
            panel_name=panel,
            keyword=keyword,
            error=f"不支持查询该面板，可查：{'、'.join(QUERYABLE_PANELS)}",
        )
    meta = QUERYABLE_PANELS[panel]
    if not (settings.mes_open_api_enabled or settings.mes_watch_password):
        return QueryResult(
            panel=panel, panel_name=meta["name"], keyword=keyword,
            error="未配置 MES 访问凭据，无法查询",
        )

    try:
        client = client or MesClient()
        rows = client.query_panel(panel, page_size=max(1, min(limit * 4, 50)))
    except MesWatchError as exc:
        logger.warning("group_assistant.query_failed", extra={"panel": panel})
        return QueryResult(panel=panel, panel_name=meta["name"], keyword=keyword, error=str(exc))

    if keyword and keyword.strip():
        needle = keyword.strip()
        no_field = meta["no"]
        rows = [
            row
            for row in rows
            if any(needle in str(value) for value in row.values() if value is not None)
        ]
        # 单号精确前缀优先
        rows.sort(key=lambda r: 0 if str(r.get(no_field, "")).startswith(needle) else 1)

    safe = [_safe_row(panel, row) for row in rows[:limit]]
    return QueryResult(panel=panel, panel_name=meta["name"], keyword=keyword, rows=safe)


# 问题里的业务线索 → 该查哪个面板
_PANEL_HINTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("SO_ORDER", ("销售订单", "销售单", "订单", "客户下单", "交货", "业务员")),
    ("MANU_ORDER", ("生产工单", "工单", "排产", "车间", "加工单", "开工")),
    ("PU_ORDER", ("采购订单", "采购单", "供应商", "到货")),
    ("ARRIVAL_IN", ("到货", "来料", "检验")),
    ("STOCK_STATUS", ("库存", "存量", "仓库", "缺料")),
)


def guess_queries(text: str) -> list[dict[str, str]]:
    """从问题里猜该查哪些面板。

    不做 NLP，只按业务名词匹配——比让模型"决定要不要查"可靠得多：
    模型倾向于回答"我查不到"，而代码一定会去查。
    """
    content = (text or "").strip()
    if not content:
        return []
    hits: list[str] = []
    for panel, words in _PANEL_HINTS:
        if any(word in content for word in words) and panel not in hits:
            hits.append(panel)
    # 单号形如 SO-2026... 直接定位面板
    import re as _re

    for match in _re.findall(r"\b(SO|MO|PO|DH|LL|CL)[-_]?\d{4,}", content.upper()):
        panel = {"SO": "SO_ORDER", "MO": "MANU_ORDER", "PO": "PU_ORDER", "DH": "ARRIVAL_IN"}.get(match)
        if panel and panel not in hits:
            hits.insert(0, panel)

    keyword = ""
    doc = _re.findall(r"\b[A-Z]{2}[-_]?\d{4,}(?:[-_]\d+)*\b", content.upper())
    if doc:
        keyword = doc[0]

    return [{"panel": panel, "keyword": keyword} for panel in hits[:2]]


def tool_description() -> str:
    """给模型看的工具说明（拼进系统提示）。"""
    lines = ["你可以查询 MES 系统的只读业务数据。可用面板："]
    for code, meta in QUERYABLE_PANELS.items():
        lines.append(f"- {code}（{meta['name']}）：{meta['fields']}")
    lines.append(
        "查询时在回答里直接给出结果，不要说「我查不到，请去系统里查」。"
    )
    return "\n".join(lines)


# ---------- L2 只读融合：单据追踪 / 详情 / 库存 ----------

# MES 单号：前缀 2-4 个字母 + 日期/序号段，如 SO-20261110-001、SJ-20261111-003
_DOC_NO_RE = re.compile(r"\b[A-Z]{2,4}-\d{4,}(?:-\d+)*\b")
# 存货编码：字母 + 数字，如 CP001、CL001
_ITEM_CODE_RE = re.compile(r"\b[A-Z]{1,3}\d{3,}\b")
_STOCK_WORDS = ("库存", "存量", "可用量", "缺料", "在库")


@dataclass
class LookupResult:
    """一次只读查询的结果；render() 直接拼进给模型的上下文。"""

    kind: str
    title: str
    payload: dict[str, Any] = field(default_factory=dict)
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error

    def render(self) -> str:
        if self.error:
            return f"{self.title}查询失败：{self.error}"
        if self.kind == "trace":
            return render_trace(self.payload)
        if self.kind == "form":
            return render_form(self.payload)
        if self.kind == "stock":
            return render_stock(self.payload)
        return f"{self.title}：" + str(self.payload)


def guess_lookups(text: str) -> list[dict[str, str]]:
    """从问题里判断要打哪个只读接口：带单号 → 追踪；问库存 → 结存。"""
    content = (text or "").strip()
    if not content:
        return []
    upper = content.upper()
    doc = _DOC_NO_RE.search(upper)
    if doc:
        return [{"kind": "trace", "doc_no": doc.group(0)}]
    if any(word in content for word in _STOCK_WORDS):
        item = _ITEM_CODE_RE.search(upper)
        return [{"kind": "stock", "item_code": item.group(0) if item else ""}]
    return []


def lookup(spec: dict[str, str], client: MesClient | None = None) -> LookupResult:
    """执行一次只读查询；单号形式的先追踪，再从追踪里取面板编码查详情。"""
    kind = spec.get("kind", "")
    if not settings.mes_open_api_enabled:
        return LookupResult(kind=kind, title=kind, error="MES 开放接口未启用")
    client = client or MesClient()
    try:
        if kind == "trace":
            doc_no = spec.get("doc_no", "")
            payload = client.trace(doc_no)
            return LookupResult(kind="trace", title=f"单据 {doc_no} 追踪", payload=payload)
        if kind == "form":
            payload = client.form(spec.get("panel_code", ""), spec.get("form_no", ""))
            return LookupResult(kind="form", title="单据详情", payload=payload)
        if kind == "stock":
            payload = client.stock(item_code=spec.get("item_code", ""))
            return LookupResult(kind="stock", title="库存结存", payload=payload)
    except MesWatchError as exc:
        logger.warning("group_tools.lookup_failed", extra={"kind": kind})
        return LookupResult(kind=kind, title=kind, error=str(exc))
    return LookupResult(kind=kind, title=kind, error=f"不支持的查询类型：{kind}")


def lookup_panel_of_trace(trace_payload: dict[str, Any], doc_no: str) -> str:
    """从追踪结果里找出这个单号属于哪个面板，用于再查详情。"""
    for row in trace_payload.get("steps") or []:
        if isinstance(row, dict) and str(row.get("单号", "")) == doc_no:
            panel = str(row.get("面板编码", ""))
            if panel:
                return panel
    return ""


def render_trace(payload: dict[str, Any]) -> str:
    if not payload:
        return "追踪没有返回数据。"
    doc_no = str(payload.get("单号") or "")
    total = payload.get("环节数")
    done = payload.get("已完成数")
    todo = payload.get("待生成数")
    lines = [f"{doc_no} 追踪：共 {total} 环节，已完成 {done}，待生成 {todo}。"]
    current = str(payload.get("当前环节") or "")
    if current:
        lines.append(f"当前环节：{current}")
    stalled = str(payload.get("卡点单号") or "")
    reason = str(payload.get("卡点原因") or "")
    if stalled:
        lines.append(f"卡点：{stalled}" + (f"（{reason}）" if reason else ""))
    else:
        lines.append("卡点：无")
    pending = [
        row for row in (payload.get("steps") or [])
        if isinstance(row, dict) and str(row.get("单据状态", "")) in ("待生成", "草稿", "审批中")
    ]
    if pending:
        lines.append("未完成环节：")
        for row in pending[:5]:
            lines.append(
                "- {序号}. {环节}（{面板编码}）{单号} {单据状态}".format(
                    序号=row.get("序号", ""),
                    环节=row.get("环节", ""),
                    面板编码=row.get("面板编码", ""),
                    单号=row.get("单号", "") or "未生成",
                    单据状态=row.get("单据状态", ""),
                )
            )
    return "\n".join(lines)


def render_form(payload: dict[str, Any]) -> str:
    if not payload:
        return "没有取到单据详情。"
    panel_name = str(payload.get("panelName") or payload.get("panelCode") or "")
    form_no = str(payload.get("formNo") or "")
    status = str(payload.get("status") or "")
    header = payload.get("header") or {}
    lines = [f"{panel_name} {form_no}" + (f"（{status}）" if status else "")]
    if isinstance(header, dict) and header:
        lines.append("表头：" + "；".join(f"{k}={v}" for k, v in header.items() if v not in (None, "")))
    for tab in payload.get("tabs") or []:
        if not isinstance(tab, dict):
            continue
        rows = [row for row in (tab.get("rows") or []) if isinstance(row, dict)]
        if not rows:
            continue
        label = str(tab.get("label") or tab.get("key") or "明细")
        lines.append(f"{label}（{len(rows)} 行）：")
        for row in rows[:5]:
            lines.append("  - " + "；".join(f"{k}={v}" for k, v in row.items() if v not in (None, "")))
    link = str(payload.get("link") or "")
    if link:
        lines.append(f"在 MES 打开：{link}")
    return "\n".join(lines)


def render_stock(payload: dict[str, Any]) -> str:
    if not payload:
        return "没有取到库存数据。"
    item = str(payload.get("存货编码") or "全部存货")
    quantity = payload.get("数量合计")
    amount = payload.get("金额合计")
    lines = [f"{item} 库存：数量合计 {quantity}，金额合计 {amount}。"]
    rows = [row for row in (payload.get("rows") or []) if isinstance(row, dict)]
    if rows:
        lines.append("分布：")
        for row in rows[:8]:
            lines.append(
                f"  - {row.get('仓库', '')} {row.get('库位', '') or ''}"
                f" 数量={row.get('数量')} 金额={row.get('金额')}"
            )
    return "\n".join(lines)
