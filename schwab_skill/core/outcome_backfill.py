"""Phase 4 outcome backfill: resolve decision packets with realized returns.

Closes the learning loop. For each packet whose outcome is still ``pending`` and
whose decision is at least ``horizon_days`` trading days old, compute the
realized return from the decision-time entry price to the close ~N trading days
later, label it win/loss/scratch, and write the outcome back.

The price lookup is injected (``history_provider``) so the core logic is pure
and offline-testable; ``run_local_backfill`` wires the default Schwab/yfinance
daily-history provider and the file-backed packet store.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

LOG = logging.getLogger(__name__)

# Realized-return deadband (%) for a "scratch" (neither win nor loss).
_SCRATCH_BAND_PCT = 0.5


def _f(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_dt(raw: Any) -> datetime | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _label(ret_pct: float) -> str:
    if ret_pct > _SCRATCH_BAND_PCT:
        return "win"
    if ret_pct < -_SCRATCH_BAND_PCT:
        return "loss"
    return "scratch"


def compute_outcome(
    packet: dict[str, Any],
    *,
    history_provider: Callable[[str], Any],
    horizon_days: int = 10,
    now: datetime | None = None,
) -> dict[str, Any] | None:
    """Return a resolved outcome dict for one packet, or None if not yet resolvable.

    ``history_provider(ticker)`` must return a pandas DataFrame with a
    DatetimeIndex and a ``close`` column (the project's ``get_daily_history``).
    """
    outcome = packet.get("outcome") or {}
    if str(outcome.get("label") or "pending") not in ("pending", "unknown"):
        return None  # already resolved

    created = _parse_dt(packet.get("created_at"))
    if created is None:
        return None
    now = now or datetime.now(timezone.utc)

    ticker = str(packet.get("ticker") or "").upper()
    if not ticker:
        return None

    try:
        df = history_provider(ticker)
    except Exception as exc:
        LOG.debug("history fetch failed for %s: %s", ticker, exc)
        return None
    if df is None or getattr(df, "empty", True) or "close" not in getattr(df, "columns", []):
        return None

    # Entry: first bar on/after the decision date. Exit: horizon trading days later.
    try:
        import pandas as pd

        idx = pd.to_datetime(df.index).tz_localize(None)
        entry_dt = pd.Timestamp(created).tz_localize(None).normalize()
        positions = [i for i, d in enumerate(idx) if d >= entry_dt]
        if not positions:
            return None
        entry_pos = positions[0]
        exit_pos = entry_pos + int(horizon_days)
        if exit_pos >= len(df):
            return None  # not matured yet

        entry_price = _f(packet.get("entry_price")) or _f(df["close"].iloc[entry_pos])
        exit_price = _f(df["close"].iloc[exit_pos])
    except Exception as exc:
        LOG.debug("outcome compute failed for %s: %s", ticker, exc)
        return None

    if not entry_price or entry_price <= 0 or exit_price is None:
        return None

    ret_pct = round((exit_price - entry_price) / entry_price * 100.0, 4)
    return {
        "label": _label(ret_pct),
        "realized_return_pct": ret_pct,
        "horizon_days": int(horizon_days),
        "realized_slippage_bps": _f((packet.get("outcome") or {}).get("realized_slippage_bps")),
        "resolved_at": now.isoformat(),
    }


def backfill_packets(
    packets: list[dict[str, Any]],
    *,
    history_provider: Callable[[str], Any],
    horizon_days: int = 10,
    now: datetime | None = None,
) -> int:
    """Mutate pending packets in place with resolved outcomes. Returns count resolved."""
    resolved = 0
    for p in packets:
        if not isinstance(p, dict):
            continue
        outcome = compute_outcome(p, history_provider=history_provider, horizon_days=horizon_days, now=now)
        if outcome is not None:
            p["outcome"] = outcome
            resolved += 1
    return resolved


def _default_history_provider(skill_dir: Path | None) -> Callable[[str], Any]:
    from market_data import get_daily_history

    def _provider(ticker: str) -> Any:
        return get_daily_history(ticker, days=120, skill_dir=skill_dir)

    return _provider


def run_local_backfill(skill_dir: Path | None = None, *, horizon_days: int = 10) -> dict[str, Any]:
    """Backfill outcomes for the local file-backed packet store."""
    from core import decision_packet

    packets = decision_packet.load_packets(skill_dir)
    if not packets:
        return {"resolved": 0, "total": 0}
    resolved = backfill_packets(
        packets, history_provider=_default_history_provider(skill_dir), horizon_days=horizon_days
    )
    if resolved:
        decision_packet.overwrite_packets(skill_dir, packets)
    return {"resolved": resolved, "total": len(packets)}
