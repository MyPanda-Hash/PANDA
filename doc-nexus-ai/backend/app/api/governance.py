from __future__ import annotations

from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from app.api.deps import admin_user
from app.core.database import transaction
from app.core.security import iso, new_token, now
from app.models.auth import User
from app.models.governance import BudgetRule, ModelPrice
from app.repositories import audit

router = APIRouter(prefix="/admin", tags=["governance"])


class PriceCreate(BaseModel):
    provider: str = Field(min_length=1, max_length=120)
    model: str = Field(min_length=1, max_length=255)
    currency: str = Field(min_length=3, max_length=8)
    input_per_million: str
    output_per_million: str
    cache_read_per_million: str | None = None
    cache_write_per_million: str | None = None


class BudgetPayload(BaseModel):
    scope: str = Field(pattern="^(system|user|space)$")
    cost_limit: str
    soft_threshold: float = Field(ge=0, le=1, allow_inf_nan=False)
    hard_stop: bool


class BudgetUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cost_limit: str
    soft_threshold: float = Field(ge=0, le=1, allow_inf_nan=False)
    hard_stop: bool

def price_snapshot(item: ModelPrice) -> dict:
    cache_read = item.cache_read_per_million or item.input_per_million
    cache_write = item.cache_write_per_million or item.input_per_million
    return {
        "id": item.id,
        "provider": item.provider,
        "model": item.model,
        "currency": item.currency,
        "input_per_million": item.input_per_million,
        "output_per_million": item.output_per_million,
        "cache_read_per_million": cache_read,
        "cache_write_per_million": cache_write,
        "effective_at": iso(item.effective_at),
        "superseded_at": iso(item.superseded_at) if item.superseded_at else None,
    }


def budget_snapshot(
    item: BudgetRule, audit_event_id: str | None = None, usage: dict | None = None
) -> dict:
    result = {
        "id": item.id, "scope": item.scope, "cost_limit": item.cost_limit,
        "soft_threshold": item.soft_threshold, "hard_stop": item.hard_stop,
        "is_active": item.is_active,
        "enforcement": "hard_stop" if item.hard_stop else "soft_warning",
    }
    if usage is not None:
        result["usage"] = usage
    if audit_event_id:
        result["audit_event_id"] = audit_event_id
    return result


def validate_cost(value: str) -> None:
    try:
        amount = Decimal(value)
        if not amount.is_finite() or amount <= 0 or len(value) > 32:
            raise ValueError
    except (InvalidOperation, ValueError):
        raise HTTPException(status_code=422, detail="费用额度必须为正数") from None


def validate_rate(value: str, *, allow_zero: bool = False) -> None:
    try:
        amount = Decimal(value)
        if amount < 0 or (not allow_zero and amount <= 0):
            raise ValueError
    except (InvalidOperation, ValueError):
        detail = "单价必须为非负数" if allow_zero else "单价必须为正数"
        raise HTTPException(status_code=422, detail=detail) from None


@router.get("/model-prices")
def list_prices(_: User = Depends(admin_user)) -> dict:
    with transaction() as db:
        items = list(db.scalars(select(ModelPrice).order_by(ModelPrice.effective_at)))
        return {"items": [price_snapshot(item) for item in items], "next_cursor": None}


@router.post("/model-prices", status_code=201)
def create_price(payload: PriceCreate, admin: User = Depends(admin_user)) -> dict:
    validate_rate(payload.input_per_million)
    validate_rate(payload.output_per_million)
    cache_read = (payload.cache_read_per_million or "").strip() or payload.input_per_million
    cache_write = (payload.cache_write_per_million or "").strip() or payload.input_per_million
    validate_rate(cache_read, allow_zero=True)
    validate_rate(cache_write, allow_zero=True)
    with transaction() as db:
        created = now()
        active = list(db.scalars(select(ModelPrice).where(ModelPrice.provider == payload.provider, ModelPrice.model == payload.model, ModelPrice.currency == payload.currency, ModelPrice.superseded_at.is_(None))))
        for item in active:
            item.superseded_at = created
        item = ModelPrice(
            id=f"prc_{new_token(10)}",
            provider=payload.provider,
            model=payload.model,
            currency=payload.currency.upper(),
            input_per_million=payload.input_per_million,
            output_per_million=payload.output_per_million,
            cache_read_per_million=cache_read,
            cache_write_per_million=cache_write,
            effective_at=created,
            created_at=created,
        )
        db.add(item)
        audit_event_id = audit.record(db, admin.id, "model_price_version_created", item.id, "success")
        return {**price_snapshot(item), "audit_event_id": audit_event_id}


@router.get("/budgets")
def list_budgets(_: User = Depends(admin_user)) -> dict:
    from app.services.budget import _held_reservation_cost, cumulative_cost

    with transaction() as db:
        rules = list(db.scalars(select(BudgetRule).order_by(BudgetRule.updated_at.desc(), BudgetRule.id)))
        # 管理端只展示系统维度的真实累计与有效预留；用户/空间规则按请求者匹配，不存在单个全局用量。
        system_usage = None
        if any(rule.scope == "system" for rule in rules):
            system_usage = {
                "current_cost": str(cumulative_cost(db, scope="system", user_id="")),
                "held_cost": str(_held_reservation_cost(
                    db, scope="system", user_id="", space_ids=frozenset()
                )),
            }
        return {
            "items": [
                budget_snapshot(rule, usage={
                    **system_usage,
                    "at_or_above_limit": rule.hard_stop and rule.is_active and (
                        Decimal(system_usage["current_cost"])
                        + Decimal(system_usage["held_cost"]) >= Decimal(rule.cost_limit)
                    ),
                } if rule.scope == "system" and system_usage is not None else None)
                for rule in rules
            ],
            "next_cursor": None,
        }


@router.post("/budgets:preview")
def preview_budget(payload: BudgetPayload, _: User = Depends(admin_user)) -> dict:
    validate_cost(payload.cost_limit)
    return {"affected_resource_count": 1, "enforcement": "hard_stop" if payload.hard_stop else "soft_warning", "message": "规则仅影响后续预算检查。"}


@router.post("/budgets", status_code=201)
def create_budget(payload: BudgetPayload, admin: User = Depends(admin_user)) -> dict:
    from app.services.budget import _begin_serialized

    validate_cost(payload.cost_limit)
    with transaction() as db:
        dialect = _begin_serialized(db)
        statement = select(BudgetRule).where(BudgetRule.scope == payload.scope)
        if dialect == "postgresql":
            statement = statement.with_for_update()
        existing = db.scalar(statement)
        if existing is not None:
            raise HTTPException(status_code=409, detail="该范围已有预算规则，请编辑或删除现有规则")
        created = now()
        item = BudgetRule(id=f"bud_{new_token(10)}", scope=payload.scope, cost_limit=payload.cost_limit, soft_threshold=payload.soft_threshold, hard_stop=payload.hard_stop, is_active=True, created_at=created, updated_at=created)
        db.add(item)
        audit_event_id = audit.record(db, admin.id, "budget_rule_created", item.id, "success")
        return budget_snapshot(item, audit_event_id)


@router.patch("/budgets/{rule_id}")
def update_budget(rule_id: str, payload: BudgetUpdate, admin: User = Depends(admin_user)) -> dict:
    from app.services.budget import _begin_serialized

    validate_cost(payload.cost_limit)
    with transaction() as db:
        dialect = _begin_serialized(db)
        statement = select(BudgetRule).where(BudgetRule.id == rule_id)
        if dialect == "postgresql":
            statement = statement.with_for_update()
        item = db.scalar(statement)
        if item is None:
            raise HTTPException(status_code=404, detail="预算规则不存在")
        item.cost_limit = payload.cost_limit
        item.soft_threshold = payload.soft_threshold
        item.hard_stop = payload.hard_stop
        item.updated_at = now()
        audit_event_id = audit.record(db, admin.id, "budget_rule_updated", item.id, "success")
        return budget_snapshot(item, audit_event_id)


def _set_budget_active(
    rule_id: str, active: bool, admin: User, event_type: str
) -> dict:
    with transaction() as db:
        item = db.get(BudgetRule, rule_id)
        if item is None:
            raise HTTPException(status_code=404, detail="预算规则不存在")
        item.is_active = active
        item.updated_at = now()
        audit_event_id = audit.record(db, admin.id, event_type, item.id, "success")
        return budget_snapshot(item, audit_event_id)


@router.post("/budgets/{rule_id}:enable")
def enable_budget(rule_id: str, admin: User = Depends(admin_user)) -> dict:
    return _set_budget_active(rule_id, True, admin, "budget_rule_enabled")


@router.post("/budgets/{rule_id}:disable")
def disable_budget(rule_id: str, admin: User = Depends(admin_user)) -> dict:
    return _set_budget_active(rule_id, False, admin, "budget_rule_disabled")


@router.delete("/budgets/{rule_id}")
def delete_budget(rule_id: str, admin: User = Depends(admin_user)) -> dict:
    with transaction() as db:
        item = db.get(BudgetRule, rule_id)
        if item is None:
            raise HTTPException(status_code=404, detail="预算规则不存在")
        db.delete(item)
        audit_event_id = audit.record(db, admin.id, "budget_rule_deleted", rule_id, "success")
        return {"id": rule_id, "audit_event_id": audit_event_id}


@router.get("/audit-events")
def list_audit_events(
    limit: int = Query(default=20, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    event_type: str | None = None,
    result: str | None = Query(default=None, pattern="^(success|other)$"),
    _: User = Depends(admin_user),
) -> dict:
    with transaction() as db:
        items, total = audit.list_events(
            db,
            limit=limit,
            offset=offset,
            event_type=(event_type or "").strip() or None,
            result=result,
        )
        next_offset = offset + limit if offset + limit < total else None
        return {
            "items": items,
            "total": total,
            "limit": limit,
            "offset": offset,
            "next_offset": next_offset,
        }
