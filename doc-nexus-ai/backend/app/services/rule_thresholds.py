"""检测阈值集中定义：改一条规则的门槛，不用翻代码。

这些数字原来散在 `production_insight` / `root_cause` / `weekly_review` /
`assistant_watch` 四个文件里，改一个要在四处找。现在只有这一份，
每个都能用环境变量覆盖，改完重启即可生效。

**为什么阈值要可配**：不同工厂的节奏差别很大——小厂停一天就是大事，
大厂停两天算正常；质检严格的地方 3% 不合格就要停线，试点车间 10% 也许正常。
写死的数字最后一定会被绕过，不如放开让人调。

读的是 `settings` 上的 `mes_rule_*` 字段，每次调用现取，所以测试里
`monkeypatch.setattr(settings, ...)` 能直接生效。
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import now as _now
from app.models.rule_threshold import RuleThresholdChange

logger = logging.getLogger(__name__)

# 进程内的覆盖缓存：规则函数是纯函数、拿不到 db，只能靠这里。
# 单进程部署（uvicorn + 后台线程同进程）没问题；将来多进程要换成共享存储。
_OVERRIDES: dict[str, int] = {}
#: 万分比字段，存整数避免浮点
_RATE_KEYS = {
    "quality_rate",
    "quality_critical_rate",
    "trend_rate",
}

#: 字段 → 环境变量名（和 _FIELDS 表里的保持一致）
_ENV_OF = {
    "stall_days": "MES_RULE_STALL_DAYS",
    "quality_rate": "MES_RULE_QUALITY_RATE",
    "quality_critical_rate": "MES_RULE_QUALITY_CRITICAL_RATE",
    "quality_min_sample": "MES_RULE_QUALITY_MIN_SAMPLE",
    "trend_rate": "MES_RULE_TREND_RATE",
    "trend_min_orders": "MES_RULE_TREND_MIN_ORDERS",
    "trend_min_sample": "MES_RULE_TREND_MIN_SAMPLE",
    "supplier_min_events": "MES_RULE_SUPPLIER_MIN_EVENTS",
    "customer_min_docs": "MES_RULE_CUSTOMER_MIN_DOCS",
    "approver_min_events": "MES_RULE_APPROVER_MIN_EVENTS",
    "due_soon_days": "MES_RULE_DUE_SOON_DAYS",
    "stale_hours": "MES_RULE_STALE_HOURS",
    "repeat_kind_times": "MES_RULE_REPEAT_KIND_TIMES",
    "repeat_doc_times": "MES_RULE_REPEAT_DOC_TIMES",
}


@dataclass(frozen=True)
class Thresholds:
    """一次快照，避免规则函数里到处 `settings.xxx`。"""

    #: 从开工第几天开始报停滞；之后每翻一倍升一级（1→info, 2→warning, 4→critical）
    stall_days: int
    #: 工序不合格率门槛
    quality_rate: float
    #: 不合格率到达这个值直接 critical（不再等天数）
    quality_critical_rate: float
    #: 单张汇报单的样本下限，低于这个数不判比例
    quality_min_sample: int
    #: 跨单趋势：同一工序的不合格率门槛
    trend_rate: float
    #: 跨单趋势：至少跨几张单
    trend_min_orders: int
    #: 跨单趋势：汇总样本下限
    trend_min_sample: int
    #: 供应商链：同一供应商至少几条不同问题
    supplier_min_events: int
    #: 客户链：同一客户至少几张不同单
    customer_min_docs: int
    #: 审批瓶颈：同一审批人至少几张单
    approver_min_events: int
    #: 工单临期提醒窗口（天）
    due_soon_days: int
    #: 待办/事件多久算「没动」（小时）
    stale_hours: int
    #: 周回顾：同一类问题出现几次算「反复」
    repeat_kind_times: int
    #: 周回顾：同一张单出现几次算「反复」
    repeat_doc_times: int


def _raw(default_value: Any, key: str) -> tuple[Any, str]:
    """取一个阈值的生效值和来源（override / env / default）。"""
    if key in _OVERRIDES:
        value = _OVERRIDES[key]
        if key in _RATE_KEYS:
            value = value / 10000.0
        return value, "override"
    env_name = _ENV_OF.get(key, "")
    env_value = getattr(settings, f"mes_rule_{key}", None)
    if env_value is not None and env_value != default_value:
        return env_value, "env"
    return default_value, "default"


def current() -> Thresholds:
    """读当前生效的阈值（DB 覆盖 > 环境变量 > 默认）。"""
    return Thresholds(
        stall_days=int(_raw(1, 'stall_days')[0]),
        quality_rate=float(_raw(0.05, 'quality_rate')[0]),
        quality_critical_rate=float(_raw(0.15, 'quality_critical_rate')[0]),
        quality_min_sample=int(_raw(5, 'quality_min_sample')[0]),
        trend_rate=float(_raw(0.08, 'trend_rate')[0]),
        trend_min_orders=int(_raw(2, 'trend_min_orders')[0]),
        trend_min_sample=int(_raw(10, 'trend_min_sample')[0]),
        supplier_min_events=int(_raw(2, 'supplier_min_events')[0]),
        customer_min_docs=int(_raw(2, 'customer_min_docs')[0]),
        approver_min_events=int(_raw(3, 'approver_min_events')[0]),
        due_soon_days=int(_raw(2, 'due_soon_days')[0]),
        stale_hours=int(_raw(24, 'stale_hours')[0]),
        repeat_kind_times=int(_raw(3, 'repeat_kind_times')[0]),
        repeat_doc_times=int(_raw(2, 'repeat_doc_times')[0]),
    )


#: 给人看的说明：字段 → (中文名, 环境变量, 说明)
_FIELDS: tuple[tuple[str, str, str, str], ...] = (
    ("stall_days", "工序停滞门槛", "MES_RULE_STALL_DAYS",
     "从开工第几天开始报；设 1 即 1 天 info / 2 天 warning / 4 天 critical"),
    ("quality_rate", "不合格率门槛", "MES_RULE_QUALITY_RATE",
     "单张汇报单里某道工序的不合格比例超过它就报"),
    ("quality_critical_rate", "不合格率紧急线", "MES_RULE_QUALITY_CRITICAL_RATE",
     "超过这个比例直接给 critical"),
    ("quality_min_sample", "质量样本下限", "MES_RULE_QUALITY_MIN_SAMPLE",
     "报工件数少于这个数不判比例，避免 1/1 = 100% 的误报"),
    ("trend_rate", "跨单趋势门槛", "MES_RULE_TREND_RATE",
     "同一道工序跨多张单的不合格率超过它就报"),
    ("trend_min_orders", "趋势最少单数", "MES_RULE_TREND_MIN_ORDERS",
     "至少跨几张单才算工序问题，一张单可能只是这批料"),
    ("trend_min_sample", "趋势样本下限", "MES_RULE_TREND_MIN_SAMPLE",
     "汇总报工件数少于这个数不下结论"),
    ("supplier_min_events", "供应商链门槛", "MES_RULE_SUPPLIER_MIN_EVENTS",
     "同一供应商至少几条不同问题才算「这条线断了」"),
    ("customer_min_docs", "客户链门槛", "MES_RULE_CUSTOMER_MIN_DOCS",
     "同一客户至少几张不同单才归成一簇"),
    ("approver_min_events", "审批瓶颈门槛", "MES_RULE_APPROVER_MIN_EVENTS",
     "同一审批人压着几张单算流程瓶颈"),
    ("due_soon_days", "临期提醒窗口", "MES_RULE_DUE_SOON_DAYS",
     "离预完工日还有几天开始提醒"),
    ("stale_hours", "没动判定", "MES_RULE_STALE_HOURS",
     "待办多久没处理算「压着」"),
    ("repeat_kind_times", "反复出现门槛", "MES_RULE_REPEAT_KIND_TIMES",
     "周回顾里同一类问题出现几次标「反复出现」"),
    ("repeat_doc_times", "单据重复门槛", "MES_RULE_REPEAT_DOC_TIMES",
     "周回顾里同一张单出现几次算重复"),
)


_LABEL_OF = {key: label for key, label, _env, _note in _FIELDS}
def _default_of(key: str) -> Any:
    """从 Settings 的字段定义里取代码里的默认值。

    不能用 ``Thresholds.__dataclass_fields__[key].default``——那几个字段都是必填的，
    ``default`` 是 ``dataclasses.MISSING``，拿它去比会把一切判成「环境变量改过」。
    """
    field = type(settings).model_fields.get(f"mes_rule_{key}")
    return field.default if field is not None else None


_DEFAULTS = {key: _default_of(key) for key in _ENV_OF}


def describe() -> list[dict[str, Any]]:
    """给页面或问答用的一张表：现在生效的阈值是什么、怎么改。"""
    values = asdict(current())
    rows: list[dict[str, Any]] = []
    for key, label, env, note in _FIELDS:
        value = values.get(key)
        _effective, source = _raw(value, key)
        rows.append(
            {
                "key": key,
                "label": label,
                "env": env,
                "value": value,
                "display": f"{round(value * 100)}%" if isinstance(value, float) else str(value),
                "note": note,
                # default / env / override —— 出了问题先看这个
                "source": source,
            }
        )
    return rows


def render_table() -> str:
    """把阈值排成一段可读文本。"""
    lines = ["当前检测阈值（改环境变量后重启生效）："]
    for row in describe():
        lines.append(f"- {row['label']}：{row['display']}（{row['env']}）— {row['note']}")
    return "\n".join(lines)

# ---------------------------------------------------------------------------
# 改值与留痕
# ---------------------------------------------------------------------------


class ThresholdError(Exception):
    """值不合法或 key 不认识。"""


def _to_store(key: str, value: float | int) -> int:
    """统一存整数：比例存万分比，计数直接存。"""
    if key in _RATE_KEYS:
        return int(round(float(value) * 10000))
    return int(value)


def _validate(key: str, value: float | int) -> None:
    if key not in _ENV_OF:
        raise ThresholdError(f"不认识的阈值：{key}")
    if key in _RATE_KEYS:
        if not 0 < float(value) <= 1:
            raise ThresholdError(f"{key} 是比例，取值要在 (0, 1] 之间")
        return
    if int(value) < 1:
        raise ThresholdError(f"{key} 是计数，至少为 1")


def load_overrides(db: Session) -> int:
    """把库里最新的覆盖值装进进程缓存。启动时调一次。"""
    _OVERRIDES.clear()
    rows = list(
        db.scalars(
            select(RuleThresholdChange).order_by(RuleThresholdChange.created_at)
        )
    )
    # 只认最后一次「设值」，被 revert 的（new_value 记为回退）不算
    for row in rows:
        if row.source_to == "reverted":
            _OVERRIDES.pop(row.key, None)
        else:
            _OVERRIDES[row.key] = row.new_value
    return len(_OVERRIDES)


def set_value(
    db: Session,
    *,
    key: str,
    value: float | int,
    actor: str = "",
    reason: str = "",
) -> dict[str, Any]:
    """改一个阈值并留痕。传 None 走 revert。"""
    _validate(key, value)
    # 记「改之前的实际生效值」，不是「上一次的覆盖值」——
    # 否则第一次改会记成「从 None 改成 3」，看不出原来是多少
    effective_before, source_from = _raw(_DEFAULTS[key], key)
    stored = _to_store(key, value)
    row = RuleThresholdChange(
        key=key,
        value_kind="rate" if key in _RATE_KEYS else "int",
        old_value=_to_store(key, effective_before) if effective_before is not None else None,
        new_value=stored,
        source_from=source_from,
        source_to="override",
        actor=actor or "",
        reason=(reason or "").strip()[:500],
        created_at=_now(),
    )
    db.add(row)
    db.flush()
    _OVERRIDES[key] = stored
    return {"key": key, "value": value, "source": "override"}


def revert(db: Session, *, key: str, actor: str = "", reason: str = "") -> dict[str, Any]:
    """把阈值恢复成环境变量/默认值（也留痕，否则「谁改回去的」查不到）。"""
    if key not in _ENV_OF:
        raise ThresholdError(f"不认识的阈值：{key}")
    effective_before, _source = _raw(_DEFAULTS[key], key)
    _OVERRIDES.pop(key, None)
    # 恢复后实际变成多少（环境变量或默认值），一起记下来
    restored, _restored_source = _raw(_DEFAULTS[key], key)
    row = RuleThresholdChange(
        key=key,
        value_kind="rate" if key in _RATE_KEYS else "int",
        old_value=_to_store(key, effective_before) if effective_before is not None else None,
        new_value=_to_store(key, restored),
        source_from=_source,
        source_to="reverted",
        actor=actor or "",
        reason=(reason or "").strip()[:500],
        created_at=_now(),
    )
    db.add(row)
    db.flush()
    return {"key": key, "source": "reverted"}


def history(
    db: Session, *, key: str | None = None, limit: int = 50
) -> list[dict[str, Any]]:
    """改动历史，最近的在前面。"""
    stmt = select(RuleThresholdChange).order_by(RuleThresholdChange.created_at.desc())
    if key:
        stmt = stmt.where(RuleThresholdChange.key == key)
    stmt = stmt.limit(max(1, min(limit, 200)))
    out: list[dict[str, Any]] = []
    for row in db.scalars(stmt):
        out.append(
            {
                "id": row.id,
                "key": row.key,
                "oldValue": _from_store(row.key, row.old_value),
                "newValue": _from_store(row.key, row.new_value),
                "sourceFrom": row.source_from,
                "sourceTo": row.source_to,
                "actor": row.actor,
                "reason": row.reason,
                "createdAt": row.created_at.isoformat() if row.created_at else "",
            }
        )
    return out


def _from_store(key: str, stored: int | None) -> float | int | None:
    if stored is None:
        return None
    return round(stored / 10000.0, 4) if key in _RATE_KEYS else stored


def render_history(db: Session, *, limit: int = 20) -> str:
    rows = history(db, limit=limit)
    if not rows:
        return "阈值没有被改过，一直是默认值或环境变量。"
    lines = ["阈值改动记录（最近的在前）："]
    for row in rows:
        label = _LABEL_OF.get(row["key"], row["key"])
        if row["sourceTo"] == "reverted":
            lines.append(
                f"- {row['createdAt'][:16]} {row['actor'] or '未记名'} 把 {label} "
                f"从 {row['oldValue']} 恢复成 {row['newValue']}"
            )
        else:
            lines.append(
                f"- {row['createdAt'][:16]} {row['actor'] or '未记名'} 把 {label} "
                f"从 {row['oldValue']} 改成 {row['newValue']}"
                + (f"（{row['reason']}）" if row["reason"] else "")
            )
    return "\n".join(lines)
