"""Phase 1 pre-trade quality gates.

Computes the ``PreTradeChecks`` block on a decision card:
- spread / liquidity checks
- quote freshness
- event-risk overlay

Shipped behind ``PRE_TRADE_GATES_MODE`` (default ``shadow`` = annotate only,
never block). These are pure functions so they unit-test offline; routes feed
in already-fetched quote/signal data.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from core.contracts.symbol import PreTradeChecks


def _f(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def gates_mode(skill_dir: Path | None = None) -> str:
    try:
        from config import get_pre_trade_gates_mode

        return get_pre_trade_gates_mode(skill_dir)
    except Exception:
        return "shadow"


def _thresholds(skill_dir: Path | None) -> tuple[float, float, float]:
    try:
        from config import (
            get_data_quote_max_age_sec,
            get_pretrade_max_spread_bps,
            get_pretrade_min_dollar_volume,
        )

        return (
            get_pretrade_max_spread_bps(skill_dir),
            get_pretrade_min_dollar_volume(skill_dir),
            get_data_quote_max_age_sec(skill_dir),
        )
    except Exception:
        return (50.0, 2_000_000.0, 600.0)


def spread_bps(bid: float | None, ask: float | None) -> float | None:
    """Bid/ask spread in basis points of the mid price."""
    b, a = _f(bid), _f(ask)
    if b is None or a is None or a <= 0 or b <= 0 or a < b:
        return None
    mid = (a + b) / 2.0
    if mid <= 0:
        return None
    return round((a - b) / mid * 10_000.0, 2)


def compute_checks(
    *,
    price: float | None = None,
    bid: float | None = None,
    ask: float | None = None,
    quote_age_sec: float | None = None,
    avg_vol_50: float | None = None,
    event_risk: dict[str, Any] | None = None,
    skill_dir: Path | None = None,
) -> PreTradeChecks:
    """Build pre-trade checks from already-fetched quote/signal inputs."""
    max_spread, min_dollar_vol, max_age = _thresholds(skill_dir)

    sb = spread_bps(bid, ask)
    spread_ok = (sb is None) or (sb <= max_spread)

    age = _f(quote_age_sec)
    quote_fresh: bool | None = None if age is None else (age <= max_age)

    px = _f(price)
    vol = _f(avg_vol_50)
    dollar_vol = (px * vol) if (px is not None and vol is not None) else None
    liquidity_ok: bool | None = None if dollar_vol is None else (dollar_vol >= min_dollar_vol)

    er = event_risk or {}
    event_label = "none"
    if er.get("flagged"):
        reasons = [str(x) for x in (er.get("reasons") or []) if str(x).strip()]
        event_label = reasons[0] if reasons else "flagged"

    blockers: list[str] = []
    if sb is not None and not spread_ok:
        blockers.append(f"spread_too_wide:{sb}bps>{max_spread}bps")
    if quote_fresh is False:
        blockers.append(f"quote_stale:{int(age or 0)}s>{int(max_age)}s")
    if liquidity_ok is False:
        blockers.append("low_liquidity")
    if event_label not in ("none",):
        blockers.append(f"event_risk:{event_label}")

    return PreTradeChecks(
        spread_bps=sb,
        quote_fresh=quote_fresh,
        quote_age_sec=age,
        liquidity_ok=liquidity_ok,
        event_risk=event_label,
        tradeable=(len(blockers) == 0),
        blockers=blockers,
    )


def from_signal(signal: dict[str, Any], *, skill_dir: Path | None = None) -> PreTradeChecks:
    """Best-effort checks from a scanner signal dict (no live quote).

    The signal carries ``price`` and ``avg_vol_50`` (liquidity) and an
    ``event_risk`` block, but not bid/ask — so spread stays ``None`` until a
    live quote is supplied via :func:`compute_checks`.
    """
    sig = signal or {}
    return compute_checks(
        price=_f(sig.get("price")),
        avg_vol_50=_f(sig.get("avg_vol_50")),
        event_risk=sig.get("event_risk") if isinstance(sig.get("event_risk"), dict) else None,
        skill_dir=skill_dir,
    )
