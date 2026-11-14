from __future__ import annotations

from typing import Any


def reported_total_tokens(
    raw: object, *, input_includes_cache: bool = True
) -> int | None:
    """Read a verifiable raw token total before billing defaults erase absence."""
    if not isinstance(raw, dict):
        return None
    counts = [
        raw.get("input_tokens", raw.get("prompt_tokens")),
        raw.get("output_tokens", raw.get("completion_tokens")),
    ]
    if not input_includes_cache:
        counts.extend(
            (
                raw.get("cache_read_input_tokens", 0),
                raw.get("cache_creation_input_tokens", 0),
            )
        )
    if any(type(value) is not int or value < 0 for value in counts):
        return None
    total = sum(counts)
    if "total_tokens" in raw:
        supplied = raw["total_tokens"]
        if type(supplied) is not int or supplied != total:
            return None
    return total


def _coerce_int(value: object, default: int = 0) -> int:
    try:
        number = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    return max(0, number)


def _nested_int(raw: dict[str, Any], *paths: tuple[str, ...]) -> int | None:
    for path in paths:
        cursor: Any = raw
        ok = True
        for key in path:
            if not isinstance(cursor, dict) or key not in cursor:
                ok = False
                break
            cursor = cursor[key]
        if not ok:
            continue
        try:
            return max(0, int(cursor))
        except (TypeError, ValueError):
            continue
    return None


def extract_cache_buckets(raw: dict[str, Any]) -> tuple[int, int, int | None]:
    """Return (cache_read, cache_write, explicit_uncached_or_none)."""
    cache_read = _nested_int(
        raw,
        ("cache_read_tokens",),
        ("cache_read_input_tokens",),
        ("cached_tokens",),
        ("prompt_tokens_details", "cached_tokens"),
        ("input_tokens_details", "cached_tokens"),
        ("prompt_cache_hit_tokens",),
    )
    cache_write = _nested_int(
        raw,
        ("cache_write_tokens",),
        ("cache_creation_input_tokens",),
        ("cache_creation_tokens",),
        ("prompt_tokens_details", "cache_write_tokens"),
        ("input_tokens_details", "cache_write_tokens"),
        ("prompt_cache_miss_tokens",),
    )
    uncached: int | None = None
    has_explicit_uncached = any(
        key in raw
        for key in ("input_uncached_tokens", "uncached_tokens")
    ) or (
        isinstance(raw.get("prompt_tokens_details"), dict)
        and "uncached_tokens" in raw["prompt_tokens_details"]
    ) or (
        isinstance(raw.get("input_tokens_details"), dict)
        and "uncached_tokens" in raw["input_tokens_details"]
    )
    if has_explicit_uncached:
        uncached = _nested_int(
            raw,
            ("input_uncached_tokens",),
            ("uncached_tokens",),
            ("prompt_tokens_details", "uncached_tokens"),
            ("input_tokens_details", "uncached_tokens"),
        )
    return cache_read or 0, cache_write or 0, uncached


def normalize_usage_buckets(raw: dict[str, Any]) -> dict[str, int]:
    """Normalize provider usage into mutually exclusive billing buckets.

    Rules:
    - ``input_tokens`` remains the provider total input (may include cache read).
    - ``input_uncached_tokens`` + ``cache_read_tokens`` must not exceed ``input_tokens``
      when both are known; uncached is derived as ``input - cache_read`` when needed.
    - ``cache_write_tokens`` is billed separately and is not subtracted from input.
    """
    input_tokens = _coerce_int(raw.get("input_tokens", raw.get("prompt_tokens")))
    output_tokens = _coerce_int(raw.get("output_tokens", raw.get("completion_tokens")))
    cache_read, cache_write, explicit_uncached = extract_cache_buckets(raw)

    if explicit_uncached is not None:
        input_uncached = explicit_uncached
        if cache_read == 0 and input_tokens > 0:
            # Some providers only report uncached; derive read as remainder.
            cache_read = max(0, input_tokens - input_uncached)
        elif input_tokens == 0:
            input_tokens = input_uncached + cache_read
    elif cache_read > 0:
        if cache_read > input_tokens and input_tokens > 0:
            # Prefer trusting total input; clamp cache_read to avoid impossible buckets.
            cache_read = input_tokens
        if input_tokens == 0:
            input_tokens = cache_read
        input_uncached = max(0, input_tokens - cache_read)
    else:
        input_uncached = input_tokens

    # Guarantees exclusive input buckets: uncached + read == input when read is present.
    if cache_read > 0 and input_uncached + cache_read != input_tokens:
        input_uncached = max(0, input_tokens - cache_read)

    total_tokens = _coerce_int(raw.get("total_tokens"), input_tokens + output_tokens)
    return {
        "input_tokens": input_tokens,
        "input_uncached_tokens": input_uncached,
        "cache_read_tokens": cache_read,
        "cache_write_tokens": cache_write,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
    }
