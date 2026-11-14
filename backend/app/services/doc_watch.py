"""订阅式盯单：'盯着 PO-2026-08-0001，到货就告诉我'。

和「派待办」的区别：待办是给人的动作，订阅是给 AI 的**持续关注**——
不改任何单据，只在条件命中时推一条。

判定只依赖 MES 的流程追踪（`trace`）：
- 基线存下「已完成数 + 各环节单号」
- 之后每次扫描再取一次，比较：有推进 → progress；出现卡点 → stalled
- 只盯某个环节时（target_keyword），该环节从「没单号」变成「有单号」才算命中

权限：建订阅时先确认这个人能看到该单号所在面板；看不到就拒绝，
不给自己偷偷开一个管理员视角的后台。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import new_token
from app.core.security import now as _now
from app.models.doc_watch import DocWatch

logger = logging.getLogger(__name__)

DEFAULT_TTL_DAYS = 7


class WatchError(RuntimeError):
    """建订阅时的可读错误（单号找不到、没权限等）。"""


@dataclass
class Signal:
    hit: bool
    text: str
    snapshot: dict[str, Any]

    @property
    def key(self) -> str:
        return str(self.text or "")[:160]


def snapshot_of(trace: dict[str, Any]) -> dict[str, Any]:
    """把追踪结果压成可比较的最小快照。"""
    steps = [row for row in (trace.get("steps") or []) if isinstance(row, dict)]
    return {
        "completed": int(trace.get("已完成数") or 0),
        "total": int(trace.get("环节数") or 0),
        "current": str(trace.get("当前环节") or ""),
        "stalled": str(trace.get("卡点单号") or ""),
        "stalled_reason": str(trace.get("卡点原因") or ""),
        "steps": [
            {
                "name": str(row.get("环节") or ""),
                "panel": str(row.get("面板编码") or ""),
                "doc": str(row.get("单号") or ""),
                "status": str(row.get("单据状态") or ""),
            }
            for row in steps
        ],
    }


def diff(
    baseline: dict[str, Any],
    current: dict[str, Any],
    *,
    condition: str = "progress",
    target_keyword: str = "",
) -> Signal:
    """比较两次快照，判断这次该不该报。"""
    if target_keyword:
        keyword = target_keyword.strip()
        before = {
            step["name"]: step.get("doc", "")
            for step in baseline.get("steps", [])
            if keyword in step.get("name", "")
        }
        after = {
            step["name"]: step.get("doc", "")
            for step in current.get("steps", [])
            if keyword in step.get("name", "")
        }
        for name, doc_no in after.items():
            if doc_no and not before.get(name):
                return Signal(
                    hit=True,
                    text=f"{keyword}环节已生成单据 {doc_no}（{name}）",
                    snapshot=current,
                )
        return Signal(hit=False, text="", snapshot=current)

    if condition == "stalled":
        before_stall = str(baseline.get("stalled") or "")
        after_stall = str(current.get("stalled") or "")
        if after_stall and after_stall != before_stall:
            reason = str(current.get("stalled_reason") or "")
            return Signal(
                hit=True,
                text=f"出现卡点：{after_stall}" + (f"（{reason}）" if reason else ""),
                snapshot=current,
            )
        return Signal(hit=False, text="", snapshot=current)

    # 最关键的一种推进：下游环节出了单据（哪怕总完成数没变，比如刚建出来的草稿单）
    before_docs = {
        step.get("name", ""): step.get("doc", "")
        for step in baseline.get("steps", [])
    }
    for step in current.get("steps", []):
        name = step.get("name", "")
        doc_no = step.get("doc", "")
        if doc_no and not before_docs.get(name):
            return Signal(
                hit=True,
                text=f"{name}环节已生成单据 {doc_no}",
                snapshot=current,
            )

    before_done = int(baseline.get("completed") or 0)
    after_done = int(current.get("completed") or 0)
    if after_done > before_done:
        current_step = str(current.get("current") or "")
        return Signal(
            hit=True,
            text=f"流程推进：已完成 {after_done}/{current.get('total')} 环节"
            + (f"，当前 {current_step}" if current_step else ""),
            snapshot=current,
        )
    # 环节数变了也算推进（有的链路会新增下游环节）
    if int(current.get("total") or 0) > int(baseline.get("total") or 0):
        return Signal(
            hit=True,
            text=f"流程出现新环节，共 {current.get('total')} 环节",
            snapshot=current,
        )
    return Signal(hit=False, text="", snapshot=current)


def _new_id() -> str:
    return f"dw_{new_token(10)}"


def create(
    db: Session,
    *,
    user_id: str,
    doc_no: str,
    condition: str = "progress",
    target_keyword: str = "",
    note: str = "",
    ttl_days: int = DEFAULT_TTL_DAYS,
    trace: dict[str, Any] | None = None,
) -> tuple[DocWatch, str]:
    """建订阅。trace 由调用方传（已按权限校验过）。

    返回 (订阅, 给用户的一句说明)。指定环节但该单还没走到那一步时，
    不建一个永远不会触发的哑巴订阅，而是降级成「有推进就报」并说明清楚。
    """
    doc_no = (doc_no or "").strip()
    if not doc_no:
        raise WatchError("要盯哪个单号？给我一个单号，比如 PO-2026-08-0001")
    if condition not in ("progress", "stalled"):
        raise WatchError("只支持两种盯法：推进（progress）或卡点（stalled）")

    payload = trace if trace is not None else {}
    # 没有环节的追踪等于没查到：不能建一个永远等不到信号的订阅
    if not payload or not (payload.get("steps") or []):
        raise WatchError(f"没查到 {doc_no} 的流程，确认一下单号")

    steps = [row for row in (payload.get("steps") or []) if isinstance(row, dict)]
    total = int(payload.get("环节数") or len(steps))
    done = int(payload.get("已完成数") or 0)
    if total and done >= total:
        raise WatchError(f"{doc_no} 的流程已经全部走完，不用盯了")

    # 环节名先对齐到 MES 的说法；还没走到那一步就降级成「有推进就报」
    fallback_note = ""
    if target_keyword:
        spoken = target_keyword
        resolved = resolve_step(payload, spoken)
        if resolved:
            target_keyword = resolved
        else:
            options = step_options(payload)
            fallback_note = (
                f"这单现在还没有「{spoken}」这一步"
                + (f"（当前只有：{options}）" if options else "")
                + "，我先按「有任何推进就告诉你」盯着"
            )
            target_keyword = ""

    panel = ""
    for row in payload.get("steps") or []:
        if isinstance(row, dict) and str(row.get("单号") or "") == doc_no:
            panel = str(row.get("面板编码") or "")
            break

    stamp = _now()
    existing = db.scalars(
        select(DocWatch).where(
            DocWatch.user_id == user_id,
            DocWatch.doc_no == doc_no,
            DocWatch.status == "active",
        )
    ).first()
    if existing and existing.condition == condition and existing.target_keyword == target_keyword:
        return existing, fallback_note

    watch = DocWatch(
        id=_new_id(),
        user_id=user_id,
        doc_no=doc_no,
        panel_code=panel,
        condition=condition,
        target_keyword=target_keyword,
        note=(note or "")[:255],
        baseline_json=json.dumps(snapshot_of(payload), ensure_ascii=False),
        status="active",
        created_at=stamp,
        updated_at=stamp,
        expires_at=stamp + timedelta(days=max(1, min(ttl_days, 30))),
    )
    db.add(watch)
    db.flush()
    return watch, fallback_note


def active_watches(db: Session) -> list[DocWatch]:
    stamp = _now()
    return list(
        db.scalars(
            select(DocWatch).where(
                DocWatch.status == "active",
                (DocWatch.expires_at.is_(None)) | (DocWatch.expires_at > stamp),
            )
        )
    )


def watches_of(db: Session, user_id: str) -> list[DocWatch]:
    return list(
        db.scalars(
            select(DocWatch)
            .where(DocWatch.user_id == user_id)
            .order_by(DocWatch.created_at.desc())
            .limit(50)
        )
    )


def cancel(db: Session, *, user_id: str, doc_no: str) -> int:
    """取消该用户对某个单号的盯单；返回取消条数。"""
    rows = list(
        db.scalars(
            select(DocWatch).where(
                DocWatch.user_id == user_id,
                DocWatch.doc_no == doc_no.strip(),
                DocWatch.status == "active",
            )
        )
    )
    stamp = _now()
    for row in rows:
        row.status = "cancelled"
        row.updated_at = stamp
    return len(rows)


def describe(watch: DocWatch) -> str:
    what = (
        f"{watch.target_keyword}环节出单"
        if watch.target_keyword
        else ("卡点" if watch.condition == "stalled" else "有推进")
    )
    return f"盯 {watch.doc_no}：{what}"


def scan(db: Session, *, client: Any | None = None, trace_fn: Any | None = None) -> dict[str, int]:
    """跑一轮：刷新每条活跃订阅，命中就推通知。

    :param trace_fn: 测试注入用；签名 (doc_no) -> trace dict
    """
    from app.core.timeutil import as_utc
    from app.services import inapp_notifications

    if trace_fn is None:
        from app.services.mes_client import MesClient

        mes = client or MesClient()

        def trace_fn(doc_no: str) -> dict[str, Any]:
            return mes.trace(doc_no)

    checked = hit = expired = failed = 0
    stamp = _now()
    for watch in active_watches(db):
        checked += 1
        expiry = as_utc(watch.expires_at)
        if expiry and expiry <= stamp:
            watch.status = "done"
            watch.updated_at = stamp
            expired += 1
            continue
        try:
            payload = trace_fn(watch.doc_no)
        except Exception as exc:  # noqa: BLE001 - 单条失败不能拖垮整轮
            logger.warning("doc_watch.trace_failed doc=%s: %s", watch.doc_no, exc)
            failed += 1
            continue
        try:
            baseline = json.loads(watch.baseline_json or "{}")
        except ValueError:
            baseline = {}
        signal = diff(
            baseline,
            snapshot_of(payload or {}),
            condition=watch.condition,
            target_keyword=watch.target_keyword,
        )
        if not signal.hit:
            continue
        if signal.key and signal.key == (watch.last_signal or ""):
            # 同一句信号不重复报：他已经被通知过了
            continue
        watch.last_signal = signal.key
        watch.baseline_json = json.dumps(signal.snapshot, ensure_ascii=False)
        watch.updated_at = stamp
        inapp_notifications.push(
            db,
            user_id=watch.user_id,
            kind="notice",
            title=f"盯单提醒：{watch.doc_no}",
            body=(signal.text + ("\n\n备注：" + watch.note if watch.note else ""))[:1500],
            ref_type="watch",
            ref_id=watch.id,
            dedupe_key=f"doc_watch:{watch.id}:{abs(hash(signal.key)) % 10_000_000}",
        )
        hit += 1
    db.flush()
    return {"checked": checked, "hit": hit, "expired": expired, "failed": failed}


# 现场口语 → MES 环节名的别名表。用户说「报工」，面板里叫「工序汇报」。
_STEP_ALIASES: dict[str, tuple[str, ...]] = {
    "报工": ("工序汇报", "汇报"),
    "排产": ("工单排产", "生产加工单"),
    "工单": ("生产加工单",),
    "领料": ("生产领料", "领料"),
    "检验": ("检验单", "检验"),
    "入库": ("入库单", "入库"),
    "到货": ("到货单", "到货"),
    "开票": ("销售发票", "发票"),
    "发货": ("销售出库", "出库"),
}


def resolve_step(trace: dict[str, Any], keyword: str) -> str:
    """把用户说的环节名对到 MES 里的真实环节名。

    现场说法和面板名经常差一两个字（「生产工单」vs「生产加工单」），
    这里用相似度挑最接近的；对不上就返回空串，让调用方给出可选环节。
    """
    keyword = (keyword or "").strip()
    if not keyword:
        return ""
    names = [
        str(row.get("环节") or "")
        for row in (trace.get("steps") or [])
        if isinstance(row, dict) and row.get("环节")
    ]
    if not names:
        return ""
    for name in names:
        if keyword == name:
            return name
    for name in names:
        if keyword in name or name in keyword:
            return name
    # 口语别名：关键词命中别名表里的任一项，就用别名去匹配环节
    for key, aliases in _STEP_ALIASES.items():
        if key not in keyword and keyword not in key:
            continue
        for alias in aliases:
            for name in names:
                if alias in name:
                    return name
    from difflib import SequenceMatcher

    best, score = "", 0.0
    for name in names:
        ratio = SequenceMatcher(None, keyword, name).ratio()
        if ratio > score:
            best, score = name, ratio
    return best if score >= 0.6 else ""


def step_options(trace: dict[str, Any]) -> str:
    """列出可选环节，用于「你要盯哪个」的追问。"""
    names = [
        str(row.get("环节") or "")
        for row in (trace.get("steps") or [])
        if isinstance(row, dict) and row.get("环节")
    ]
    seen: list[str] = []
    for name in names:
        if name not in seen:
            seen.append(name)
    return "、".join(seen[:6])


def _default_trace(doc_no: str) -> dict[str, Any]:
    from app.services.mes_client import MesClient

    return MesClient().trace(doc_no)


def create_for_user(
    db: Session,
    *,
    user_id: str,
    doc_no: str,
    step: str = "",
    note: str = "",
    condition: str = "progress",
    trace_fn: Any | None = None,
) -> tuple[DocWatch, str]:
    """给某个人建订阅：查流程 → 校验面板权限 → 建订阅。

    语音助手和群助手都走这一条，免得两处各写一套权限判断。
    """
    from app.services import mes_user_client
    from app.services.group_tools import lookup_panel_of_trace

    doc_no = (doc_no or "").strip()
    if not doc_no:
        raise WatchError("要盯哪个单号？给我一个单号，比如 PO-2026-08-0001")

    payload = (trace_fn or _default_trace)(doc_no)
    if not payload:
        raise WatchError(f"没查到 {doc_no} 的流程，确认一下单号")

    panel = lookup_panel_of_trace(payload, doc_no) or str(payload.get("面板编码") or "")
    if panel:
        allowed, why = mes_user_client.can_query(panel, user_id)
        if not allowed:
            raise mes_user_client.MesPermissionError(why)

    watch, fallback = create(
        db,
        user_id=user_id,
        doc_no=doc_no,
        condition=condition,
        target_keyword=step,
        note=note,
        trace=payload,
    )
    return watch, fallback
