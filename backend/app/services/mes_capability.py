"""主对话里的 MES 只读能力：查单据、追踪流程（含工序）、看明细、看库存。

设计约束，三条都不能破：

1. **只读**。这里不提供任何写操作，MES 的写能力至今是零（``OpenBizService``
   也不注册写方法）。AI 要派人做事，走的是自有的待办/通知，不是改 MES。
2. **不另写一套取数逻辑**。列表查询复用 ``group_tools.query_business``，
   追踪/详情/库存复用 ``mes_client.MesClient``——和群聊、语音看到的是同一份数据。
   同一张单在两个入口给出不同答案，比给不出答案更糟。
3. **输出先说人话再给结构**。模型读的是 ``text`` 字段，结构化字段留着给前端渲染。

面板名一律以 MES ``panel_config.panel_name`` 为准（生产加工单、库存状况表），
别名（制造单、工单）只放在工具描述里做召回，不放返回值里制造第二套词汇。
"""

from __future__ import annotations

from typing import Any

from app.services.group_tools import QUERYABLE_PANELS, query_business
from app.services.mes_client import MesClient, MesWatchError

# 单次返回的行数上限：够回答"有几条、分别什么状态"，又不至于灌爆上下文
MAX_ROWS = 8


class MesCapabilityError(Exception):
    """MES 侧不可用或参数不合法；调用方翻译成给模型看的提示。"""


def _client() -> MesClient:
    return MesClient()


def _panel_name(panel: str) -> str:
    meta = QUERYABLE_PANELS.get(panel) or {}
    return str(meta.get("name") or panel)


def _safe(value: Any) -> str:
    text = str(value if value is not None else "").strip()
    return text or "—"


def query_panel(arguments: dict[str, Any]) -> dict[str, Any]:
    """按业务单据查列表：``panel`` 面板编码，``keyword`` 关键字（客户/供应商/单号）。"""
    panel = str(arguments.get("panel") or "").strip().upper()
    keyword = str(arguments.get("keyword") or "").strip()
    if panel not in QUERYABLE_PANELS:
        raise MesCapabilityError(
            "不支持查询该面板。可查：" + "、".join(
                f"{code}（{_panel_name(code)}）" for code in QUERYABLE_PANELS
            )
        )
    try:
        result = query_business(panel=panel, keyword=keyword, limit=MAX_ROWS)
    except MesWatchError as exc:
        raise MesCapabilityError(f"查询 MES 失败：{exc}") from exc
    if result.error:
        return {"text": f"{_panel_name(panel)}查询失败：{result.error}", "error": result.error}

    lines = [f"{result.panel_name}：关键字「{keyword or '无'}」，返回 {len(result.rows)} 条。"]
    for index, row in enumerate(result.rows, start=1):
        parts = []
        for field, value in row.items():
            text = _safe(value)
            if text != "—":
                parts.append(f"{field}={text}")
        lines.append(f"{index}. " + "，".join(parts))
    if not result.rows:
        lines.append("没有匹配的单据。可以换客户简称、供应商名或单号前缀再试。")
    return {
        "text": "\n".join(lines),
        "panel": result.panel_name,
        "panelCode": panel,
        "count": len(result.rows),
        "rows": result.rows,
    }


def trace_doc(arguments: dict[str, Any]) -> dict[str, Any]:
    """单据流程追踪：环节、工序、卡点、下一步。

    MES 已经把这个能力做在 ``/api/open/biz/trace``，和页面上的流程追踪同一份结果；
    这里只负责把它排成模型能读的话，不重算路线。
    """
    doc_no = str(arguments.get("doc_no") or "").strip()
    if not doc_no:
        raise MesCapabilityError("缺少单号")
    try:
        payload = _client().trace(doc_no)
    except MesWatchError as exc:
        raise MesCapabilityError(f"追踪 MES 单据失败：{exc}") from exc
    if not payload:
        return {"text": f"没有找到单号 {doc_no} 的流程。确认单号是否完整。"}

    total = payload.get("环节数")
    done = payload.get("已完成数")
    todo = payload.get("待生成数")
    current = str(payload.get("当前环节") or "")
    stalled_no = str(payload.get("卡点单号") or "")
    stalled_reason = str(payload.get("卡点原因") or "")
    hint = str(payload.get("提示") or "")
    manu_no = str(payload.get("加工单号") or "")

    lines = [f"{doc_no} 流程追踪：共 {total} 个环节，已完成 {done}，待生成 {todo}。"]
    if manu_no:
        lines.append(f"对应生产加工单：{manu_no}")
    if current:
        lines.append(f"当前环节：{current}")
    if stalled_no:
        lines.append(f"卡点：{stalled_no}" + (f"（{stalled_reason}）" if stalled_reason else ""))
    else:
        lines.append("卡点：无")
    # MES 的提示有时只是再报一遍环节数，和上面那行重复，过滤掉
    if hint and "环节" not in hint:
        lines.append(f"提示：{hint}")

    route = [row for row in (payload.get("route") or []) if isinstance(row, dict)]
    if route:
        lines.append("")
        lines.append("标准路线（✓ 已发生，○ 待生成，⚠ 卡点）：")
        for row in route:
            mark = "⚠" if row.get("是否卡点") else ("✓" if row.get("是否已发生") else "○")
            step = _safe(row.get("序号"))
            link = _safe(row.get("环节"))
            process = str(row.get("工序名称") or "").strip()
            no = str(row.get("单号") or "").strip()
            status = _safe(row.get("单据状态"))
            piece = f"{mark} {step}. {link}"
            if process:
                piece += f"〔{process}〕"
            if no:
                piece += f" {no}"
            if status != "—":
                piece += f" · {status}"
            lines.append(piece)

    # 工序级：把同一环节的多个工序汇报压成一行，避免 17 行里 8 行都是「工序汇报单」
    processes: dict[str, list[str]] = {}
    for row in (payload.get("steps") or []):
        if not isinstance(row, dict):
            continue
        process = str(row.get("工序名称") or "").strip()
        if not process:
            continue
        status = _safe(row.get("单据状态"))
        processes.setdefault(process, []).append(status)
    if processes:
        lines.append("")
        lines.append("工序级进展：")
        for process, statuses in processes.items():
            done_count = sum(1 for item in statuses if item in ("已审核", "已完成", "已完工"))
            lines.append(f"- {process}：{done_count}/{len(statuses)} 张单据已完成")

    lanes = [row for row in (payload.get("lanes") or []) if isinstance(row, dict)]
    if len(lanes) > 1:
        lines.append("")
        lines.append(f"并行排产线 {len(lanes)} 条（一个订单行一条）：")
        for lane in lanes:
            lines.append(
                "- {product}（{code}）加工单 {mo}".format(
                    product=_safe(lane.get("产品名称")),
                    code=_safe(lane.get("产品编码")),
                    mo=_safe(lane.get("加工单号")),
                )
            )
    return {
        "text": "\n".join(lines),
        "docNo": doc_no,
        "total": total,
        "done": done,
        "pending": todo,
        "current": current,
        "stalled": stalled_no,
        "route": route,
        "lanes": lanes,
    }


def doc_detail(arguments: dict[str, Any]) -> dict[str, Any]:
    """单据详情：表头 + 明细，字段已由 MES 按面板配置过滤。"""
    panel = str(arguments.get("panel") or "").strip().upper()
    form_no = str(arguments.get("form_no") or "").strip()
    if not form_no:
        raise MesCapabilityError("缺少单号")
    try:
        payload = _client().form(panel, form_no)
    except MesWatchError as exc:
        raise MesCapabilityError(f"读取 MES 单据失败：{exc}") from exc
    if not payload:
        return {"text": f"没有找到 {form_no}。"}

    title = _panel_name(panel) if panel else str(payload.get("panelName") or "单据")
    # 面板名以 MES 返回的为准：传进来的 code 可能只是链路里认出的一环
    title = str(payload.get("panelName") or title)
    status = str(payload.get("status") or "").strip()
    lines = [f"{title} {form_no}" + (f" · {status}" if status else "")]
    header = payload.get("header") or payload.get("data") or payload.get("row") or {}
    if isinstance(header, dict):
        for key, value in list(header.items())[:16]:
            text = _safe(value)
            if text != "—":
                lines.append(f"- {key}：{text}")
    for tab in (payload.get("tabs") or payload.get("details") or []):
        if not isinstance(tab, dict):
            continue
        rows = tab.get("rows") or tab.get("data") or []
        if not rows:
            continue
        lines.append("")
        lines.append(f"明细 · {_safe(tab.get('label') or tab.get('name') or tab.get('tabName'))}（{len(rows)} 行）：")
        for index, row in enumerate(rows[:MAX_ROWS], start=1):
            if not isinstance(row, dict):
                continue
            cells = [f"{k}={_safe(v)}" for k, v in list(row.items())[:8] if _safe(v) != "—"]
            lines.append(f"  {index}. " + "，".join(cells))
    return {"text": "\n".join(lines), "panelCode": panel, "formNo": form_no, "detail": payload}


def stock(arguments: dict[str, Any]) -> dict[str, Any]:
    """库存结存：按存货/仓库过滤。"""
    item_code = str(arguments.get("item_code") or "").strip()
    warehouse = str(arguments.get("warehouse") or "").strip()
    try:
        payload = _client().stock(item_code=item_code, warehouse=warehouse, limit=MAX_ROWS)
    except MesWatchError as exc:
        raise MesCapabilityError(f"读取 MES 库存失败：{exc}") from exc
    rows = payload.get("rows") or []
    if not rows:
        return {"text": "没有匹配的库存记录。", "rows": []}
    lines = [f"库存结存：匹配 {len(rows)} 行。"]
    for index, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            continue
        cells = [f"{k}={_safe(v)}" for k, v in list(row.items())[:7] if _safe(v) != "—"]
        lines.append(f"{index}. " + "，".join(cells))
    total = payload.get("total") or {}
    if isinstance(total, dict) and total:
        lines.append("合计：" + "，".join(f"{k}={_safe(v)}" for k, v in total.items()))
    return {"text": "\n".join(lines), "rows": rows, "total": total}