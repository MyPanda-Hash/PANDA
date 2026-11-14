from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.governance import ModelPrice
from app.services.usage_normalize import normalize_usage_buckets

MILLION = Decimal("1000000")
COST_QUANT = Decimal("0.000001")


def _as_decimal(value: Any) -> Decimal | None:
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    if amount < 0:
        return None
    return amount


def _has_billable_cost(value: Any) -> bool:
    amount = _as_decimal(value)
    return amount is not None and amount > 0


def find_active_price(
    db: Session,
    *,
    provider: str | None,
    model: str | None,
) -> ModelPrice | None:
    """Resolve the current price version for a provider/model pair.

    Prefer exact provider+model match, then fall back to model-only so chat
    providers like ``agent_chat`` can still bill against the real model name.
    """
    provider_key = (provider or "").strip()
    model_key = (model or "").strip()
    if not provider_key and not model_key:
        return None

    active = list(
        db.scalars(
            select(ModelPrice)
            .where(ModelPrice.superseded_at.is_(None))
            .order_by(ModelPrice.effective_at.desc(), ModelPrice.id.desc())
        )
    )
    if not active:
        return None

    if provider_key and model_key:
        for item in active:
            if item.provider == provider_key and item.model == model_key:
                return item
    if model_key:
        for item in active:
            if item.model == model_key:
                return item
    if provider_key:
        for item in active:
            if item.provider == provider_key:
                return item
    return None


def _rate_or_fallback(primary: Any, fallback: Any) -> Decimal:
    amount = _as_decimal(primary)
    if amount is not None:
        return amount
    return _as_decimal(fallback) or Decimal("0")


def calculate_cost(
    *,
    input_tokens: int = 0,
    output_tokens: int = 0,
    input_uncached_tokens: int | None = None,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
    input_per_million: str | Decimal,
    output_per_million: str | Decimal,
    cache_read_per_million: str | Decimal | None = None,
    cache_write_per_million: str | Decimal | None = None,
) -> Decimal:
    """Bill mutually exclusive buckets.

    Never multiplies the full ``input_tokens`` when cache buckets are present.
    Missing cache rates fall back to the input rate.
    """
    read = max(0, int(cache_read_tokens or 0))
    write = max(0, int(cache_write_tokens or 0))
    if input_uncached_tokens is None:
        uncached = max(0, int(input_tokens or 0) - read) if read else max(0, int(input_tokens or 0))
    else:
        uncached = max(0, int(input_uncached_tokens))

    input_rate = _as_decimal(input_per_million) or Decimal("0")
    output_rate = _as_decimal(output_per_million) or Decimal("0")
    cache_read_rate = _rate_or_fallback(cache_read_per_million, input_rate)
    cache_write_rate = _rate_or_fallback(cache_write_per_million, input_rate)

    cost = (
        Decimal(uncached) / MILLION * input_rate
        + Decimal(read) / MILLION * cache_read_rate
        + Decimal(write) / MILLION * cache_write_rate
        + Decimal(max(0, int(output_tokens or 0))) / MILLION * output_rate
    )
    return cost.quantize(COST_QUANT, rounding=ROUND_HALF_UP)


def format_cost(amount: Decimal) -> str:
    normalized = amount.normalize()
    text = format(normalized, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def ensure_usage_buckets(usage: dict[str, Any]) -> dict[str, Any]:
    """Ensure cache/uncached fields exist on a usage dict (mutates and returns it)."""
    buckets = normalize_usage_buckets(usage)
    usage.update(buckets)
    return usage


def fill_usage_cost(db: Session, usage: dict[str, Any] | None) -> dict[str, Any] | None:
    """Fill ``cost`` from the active model price when missing, zero, or unknown.

    Keeps an upstream-provided positive cost unchanged. Returns the same dict.
    """
    if not usage:
        return usage
    ensure_usage_buckets(usage)
    price = find_active_price(
        db,
        provider=str(usage.get("provider") or "") or None,
        model=str(usage.get("model") or "") or None,
    )
    if _has_billable_cost(usage.get("cost")):
        # Preserve an authoritative provider-reported amount, but still attach
        # the active price snapshot when one is available so every real call
        # remains auditable by model, currency, and price version.
        if price is not None:
            usage.setdefault("price_version_id", price.id)
            usage.setdefault("currency", price.currency)
        return usage
    if price is None:
        raw = usage.get("cost")
        usage["cost"] = "0" if raw in (None, "", "unknown") else str(raw)
        return usage

    amount = calculate_cost(
        input_tokens=int(usage.get("input_tokens") or 0),
        output_tokens=int(usage.get("output_tokens") or 0),
        input_uncached_tokens=int(usage.get("input_uncached_tokens") or 0),
        cache_read_tokens=int(usage.get("cache_read_tokens") or 0),
        cache_write_tokens=int(usage.get("cache_write_tokens") or 0),
        input_per_million=price.input_per_million,
        output_per_million=price.output_per_million,
        cache_read_per_million=getattr(price, "cache_read_per_million", None),
        cache_write_per_million=getattr(price, "cache_write_per_million", None),
    )
    usage["cost"] = format_cost(amount)
    usage["price_version_id"] = price.id
    usage["currency"] = price.currency
    return usage


def priced_usage_snapshot(db: Session, usage: dict[str, Any] | None) -> dict[str, Any]:
    """Return a copy of usage with cost filled from the active price when needed."""
    snapshot = dict(usage or {})
    calls = snapshot.get("calls")
    if isinstance(calls, list) and calls:
        total_cost = Decimal("0")
        totals = {
            "input_tokens": 0,
            "input_uncached_tokens": 0,
            "cache_read_tokens": 0,
            "cache_write_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
        }
        priced_calls: list[dict[str, Any]] = []
        for raw_call in calls[:8]:
            if not isinstance(raw_call, dict):
                continue
            call = dict(raw_call)
            fill_usage_cost(db, call)
            priced_calls.append(call)
            for key in totals:
                try:
                    totals[key] += max(0, int(call.get(key) or 0))
                except (TypeError, ValueError, OverflowError):
                    continue
            total_cost += _as_decimal(call.get("cost")) or Decimal("0")
        if priced_calls:
            snapshot["calls"] = priced_calls
            snapshot.update(totals)
            snapshot["cost"] = format_cost(total_cost)
            return snapshot
    fill_usage_cost(db, snapshot)
    return snapshot
