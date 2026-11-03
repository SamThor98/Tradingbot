"""Phase 4 outcome backfill: resolve decision packets with realized returns.

Closes the learning loop. For each packet whose outcome is still ``pending`` and
whose decision is at least ``horizon_days`` trading days old, compute the
realized return from the decision-time entry price to the close ~N trading days
later, label it win/loss/scratch, and write the outcome back.

The price lookup is injected (``history_provider``) so the core logic is pure
and offline-testable; ``run_local_backfill`` wires the default Schwab/yfinance
daily-history provider and the file-backed packet store.

When it runs
------------
* Every local scan (dashboard ``/api/scan`` and ``main.py`` 09:30 ET
  ``run_scan_and_notify``) records kept signals and labels matured packets.
* ``main.py`` also labels at the 16:00 ET self-study slot and on the Sunday
  18:00 / 18:15 digest jobs. Labeling is once per UTC day unless ``force=True``.
* One-time catch-up: ``python scripts/backfill_decision_outcomes.py --since 2026-09-03``.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

LOG = logging.getLogger(__name__)

# Realized-return deadband (%) for a "scratch" (neither win nor loss).
_SCRATCH_BAND_PCT = 0.5
# First bar may be the next session after a Friday decision plus a holiday.
_MAX_ENTRY_GAP_DAYS = 5
_MIN_HISTORY_DAYS = 90
_MAX_HISTORY_DAYS = 800


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


def _pending_label(packet: dict[str, Any]) -> bool:
    label = str((packet.get("outcome") or {}).get("label") or "pending")
    return label in ("pending", "unknown")


def _naive_daily_index(index: Any) -> Any:
    """Timezone-naive normalized daily index.

    ``tz_localize(None)`` on an already-naive index, and on a tz-aware index
    under current pandas, either no-ops incorrectly or raises. Dropping the
    zone with ``tz_convert`` keeps Schwab (naive) and yfinance (aware) bars
    comparable to the packet timestamp.
    """
    import pandas as pd

    idx = pd.DatetimeIndex(pd.to_datetime(index))
    if idx.tz is not None:
        idx = idx.tz_convert("UTC").tz_localize(None)
    return idx.normalize()


def _entry_timestamp(created: datetime) -> Any:
    import pandas as pd

    entry_ts = pd.Timestamp(created)
    if entry_ts.tzinfo is not None:
        entry_ts = entry_ts.tz_convert("UTC").tz_localize(None)
    return entry_ts.normalize()


def needed_history_days(
    packets: list[dict[str, Any]],
    *,
    horizon_days: int,
    now: datetime | None = None,
) -> int:
    """Calendar-day lookback that still contains the oldest pending decision."""
    now = now or datetime.now(timezone.utc)
    oldest: datetime | None = None
    for packet in packets:
        if not isinstance(packet, dict) or not _pending_label(packet):
            continue
        created = _parse_dt(packet.get("created_at"))
        if created is not None and (oldest is None or created < oldest):
            oldest = created
    if oldest is None:
        return max(_MIN_HISTORY_DAYS, int(horizon_days) * 8)
    span = (now - oldest).days + int(horizon_days) * 3 + 15
    return max(_MIN_HISTORY_DAYS, min(span, _MAX_HISTORY_DAYS))


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
    if not _pending_label(packet):
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

        idx = _naive_daily_index(df.index)
        entry_dt = _entry_timestamp(created)
        positions = [i for i, d in enumerate(idx) if pd.Timestamp(d) >= entry_dt]
        if not positions:
            return None
        entry_pos = positions[0]
        first_bar = pd.Timestamp(idx[entry_pos]).normalize()
        # A window that starts long after the decision is not the entry bar.
        if int((first_bar - entry_dt).days) > _MAX_ENTRY_GAP_DAYS:
            return None
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


def _resolve_horizon(skill_dir: Path | None, horizon_days: int | None) -> int:
    if horizon_days is not None:
        return max(1, int(horizon_days))
    from config import get_decision_packet_horizon_days

    return get_decision_packet_horizon_days(skill_dir)


def _default_history_provider(skill_dir: Path | None, *, days: int) -> Callable[[str], Any]:
    from market_data import get_daily_history

    cache: dict[str, Any] = {}

    def _provider(ticker: str) -> Any:
        key = str(ticker or "").upper()
        if key not in cache:
            cache[key] = get_daily_history(key, days=int(days), skill_dir=skill_dir)
        return cache[key]

    return _provider


def _pending_count(packets: list[dict[str, Any]]) -> int:
    return sum(1 for packet in packets if isinstance(packet, dict) and _pending_label(packet))


def _overdue_pending(packets: list[dict[str, Any]], horizon_days: int, now: datetime) -> bool:
    """True when a pending packet is old enough that a failed fetch should be retried.

    10 trading days is about 14 calendar days; the extra buffer avoids treating a
    packet that is merely unmatured as a stuck label.
    """
    min_age_days = int(horizon_days * 7 / 5) + 4
    for packet in packets:
        if not isinstance(packet, dict) or not _pending_label(packet):
            continue
        created = _parse_dt(packet.get("created_at"))
        if created is not None and (now - created).days >= min_age_days:
            return True
    return False


def run_local_backfill(
    skill_dir: Path | None = None,
    *,
    horizon_days: int | None = None,
    history_provider: Callable[[str], Any] | None = None,
    now: datetime | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """Backfill outcomes for the local file-backed packet store.

    Scheduled callers run at most once per UTC day (the stamp lives in the
    packet file). The manual cockpit button and the catch-up script pass
    ``force=True``. ``history_provider`` is injected by tests; production uses
    daily history long enough to cover the oldest pending packet.
    """
    from core import decision_packet

    now = now or datetime.now(timezone.utc)
    today = now.astimezone(timezone.utc).date().isoformat()
    path = decision_packet._store_path(skill_dir)
    data = decision_packet._load_raw(path)
    packets = [p for p in data.get("packets", []) if isinstance(p, dict)]
    horizon = _resolve_horizon(skill_dir, horizon_days)
    if not packets:
        return {
            "resolved": 0,
            "total": 0,
            "pending": 0,
            "horizon_days": horizon,
            "history_days": None,
            "skipped": None,
        }
    if not force and str(data.get("last_outcome_backfill_on") or "") == today:
        return {
            "resolved": 0,
            "total": len(packets),
            "pending": _pending_count(packets),
            "horizon_days": horizon,
            "history_days": None,
            "skipped": "already_ran_utc_day",
        }

    history_days = needed_history_days(packets, horizon_days=horizon, now=now)
    provider = history_provider or _default_history_provider(skill_dir, days=history_days)
    resolved = backfill_packets(packets, history_provider=provider, horizon_days=horizon, now=now)
    # A packet that is old enough to label, and still pending, means the price
    # fetch missed. Do not stamp the day — the next scan must try again.
    overdue = _overdue_pending(packets, horizon, now)
    data["packets"] = packets
    if not overdue:
        data["last_outcome_backfill_on"] = today
    if resolved or not overdue:
        decision_packet._save_raw(path, data)
    return {
        "resolved": resolved,
        "total": len(packets),
        "pending": _pending_count(packets),
        "horizon_days": horizon,
        "history_days": history_days if history_provider is None else None,
        "skipped": None,
        "overdue_pending": overdue,
    }


def run_scan_outcome_pass(
    skill_dir: Path | None,
    signals: list[dict[str, Any]] | None,
    *,
    diagnostics: dict[str, Any] | None = None,
    created_at: datetime | None = None,
    history_provider: Callable[[str], Any] | None = None,
    horizon_days: int | None = None,
    now: datetime | None = None,
    force_backfill: bool = False,
) -> dict[str, Any]:
    """Record kept scan signals, then label matured packets. Never raises.

    This is the hook on the scan paths that actually run (dashboard and the
    09:30 ET bot scan). Packet creation is idempotent per ticker per UTC day.
    Labeling follows :func:`run_local_backfill` (once per UTC day unless forced).
    """
    recorded: dict[str, Any] = {"recorded": 0, "skipped_duplicate": 0, "skipped_filtered": 0}
    backfill: dict[str, Any] = {"resolved": 0, "total": 0, "pending": 0, "skipped": None}
    try:
        from core.decision_packet import record_scan_packets

        recorded = record_scan_packets(
            skill_dir,
            signals,
            diagnostics=diagnostics,
            created_at=created_at,
        )
    except Exception as exc:
        LOG.warning("Scan decision-packet record failed: %s", exc)
    try:
        backfill = run_local_backfill(
            skill_dir,
            horizon_days=horizon_days,
            history_provider=history_provider,
            now=now,
            force=force_backfill,
        )
    except Exception as exc:
        LOG.warning("Scan outcome backfill failed: %s", exc)
    try:
        from core.debate_committee import review_signals

        review_signals(skill_dir or Path("."), signals, now=now)
    except Exception as exc:
        LOG.warning("Debate committee review skipped: %s", exc)
    LOG.info(
        "Scan outcome pass: recorded=%s skipped_duplicate=%s resolved=%s/%s skipped=%s",
        recorded.get("recorded"),
        recorded.get("skipped_duplicate"),
        backfill.get("resolved"),
        backfill.get("total"),
        backfill.get("skipped"),
    )
    return {"packets": recorded, "backfill": backfill}
