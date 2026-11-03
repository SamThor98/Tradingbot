"""Phase 4 outcome backfill: resolve decision packets with realized returns.

Closes the learning loop. The primary label is a scale-out over 20 trading
days: sell the configured fraction at the R target and trail the remainder,
then mark whatever is left at the horizon. A 10-day close-to-close return is
stored as an early signal. A 40-day scale-out is stored beside the primary.
None of this submits an order.

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


def _column(df: Any, name: str, fallback: Any) -> Any:
    columns = getattr(df, "columns", [])
    if name in columns:
        return df[name]
    return fallback


def _risk_per_share(packet: dict[str, Any], entry: float) -> float | None:
    """Long-only risk unit. Missing or short stops cannot invent an R multiple."""
    refs = packet.get("refs") if isinstance(packet.get("refs"), dict) else {}
    stop = _f(refs.get("stop_price"))
    if stop is None:
        stop = _f(packet.get("stop_price"))
    if stop is None:
        pct = _f(refs.get("stop_pct"))
        if pct is None:
            pct = _f(packet.get("stop_pct"))
        if pct is not None and pct > 0:
            frac = pct / 100.0 if pct > 1.0 else pct
            stop = entry * (1.0 - frac)
    if stop is None or stop <= 0 or stop >= entry:
        return None
    return entry - stop


def simulate_scale_out(
    df: Any,
    *,
    entry_pos: int,
    horizon_days: int,
    entry: float,
    risk: float,
    fraction: float,
    target_r: float,
    trail_r: float,
) -> dict[str, Any] | None:
    """Walk daily bars. Stop is assumed before the target on a bar that hits both.

    After the partial, the runner stop ratchets to ``trail_r`` under the high
    and never loosens. Analysis only.
    """
    exit_pos = entry_pos + int(horizon_days)
    if exit_pos >= len(df) or entry <= 0 or risk <= 0:
        return None
    close = df["close"]
    high = _column(df, "high", close)
    low = _column(df, "low", close)
    target = entry + float(target_r) * risk
    stop = entry - risk
    scaled = False
    scale_price: float | None = None
    peak = entry
    trail_stop = stop
    runner_price: float | None = None
    runner_reason: str | None = None
    for pos in range(entry_pos + 1, exit_pos + 1):
        hi = _f(high.iloc[pos])
        lo = _f(low.iloc[pos])
        cl = _f(close.iloc[pos])
        if hi is None or lo is None or cl is None:
            return None
        if hi < lo:
            hi, lo = lo, hi
        if not scaled:
            if lo <= stop:
                runner_price = stop
                runner_reason = "stop"
                break
            if hi >= target:
                scaled = True
                scale_price = target
                peak = max(target, hi)
                trail_stop = max(stop, peak - float(trail_r) * risk)
                if lo <= trail_stop:
                    runner_price = trail_stop
                    runner_reason = "trail"
                    break
            else:
                peak = max(peak, hi)
            continue
        peak = max(peak, hi)
        trail_stop = max(trail_stop, peak - float(trail_r) * risk)
        if lo <= trail_stop:
            runner_price = trail_stop
            runner_reason = "trail"
            break
    else:
        runner_price = _f(close.iloc[exit_pos])
        runner_reason = "horizon"
    if runner_price is None:
        return None
    if scaled and scale_price is not None:
        proceeds = float(fraction) * scale_price + (1.0 - float(fraction)) * runner_price
        filled = float(fraction)
    else:
        proceeds = runner_price
        filled = 0.0
    ret_pct = round((proceeds - entry) / entry * 100.0, 4)
    return {
        "label": _label(ret_pct),
        "realized_return_pct": ret_pct,
        "horizon_days": int(horizon_days),
        "exit_model": "scale_out",
        "scale_fraction": round(filled, 4),
        "scale_price": None if scale_price is None else round(scale_price, 4),
        "runner_exit_price": round(float(runner_price), 4),
        "runner_reason": runner_reason,
        "r_multiple": round((proceeds - entry) / risk, 4),
        "stop_price": round(stop, 4),
        "target_r": float(target_r),
        "trail_r": float(trail_r),
    }


def _load_exit_params(skill_dir: Path | None) -> dict[str, Any]:
    from config import (
        get_decision_packet_early_horizon_days,
        get_decision_packet_exit_model,
        get_decision_packet_horizon_days,
        get_decision_packet_position_horizon_days,
        get_decision_packet_scale_out_fraction,
        get_decision_packet_scale_out_target_r,
        get_decision_packet_scale_out_trail_r,
    )

    return {
        "early": get_decision_packet_early_horizon_days(skill_dir),
        "primary": get_decision_packet_horizon_days(skill_dir),
        "position": get_decision_packet_position_horizon_days(skill_dir),
        "model": get_decision_packet_exit_model(skill_dir),
        "fraction": get_decision_packet_scale_out_fraction(skill_dir),
        "target_r": get_decision_packet_scale_out_target_r(skill_dir),
        "trail_r": get_decision_packet_scale_out_trail_r(skill_dir),
    }


def _locate_entry(df: Any, created: datetime) -> int | None:
    import pandas as pd

    idx = _naive_daily_index(df.index)
    entry_dt = _entry_timestamp(created)
    positions = [i for i, d in enumerate(idx) if pd.Timestamp(d) >= entry_dt]
    if not positions:
        return None
    entry_pos = positions[0]
    first_bar = pd.Timestamp(idx[entry_pos]).normalize()
    if int((first_bar - entry_dt).days) > _MAX_ENTRY_GAP_DAYS:
        return None
    return entry_pos


def _mark_fixed(
    df: Any,
    *,
    entry_pos: int,
    horizon_days: int,
    entry: float,
    slippage: float | None,
    now: datetime,
) -> dict[str, Any] | None:
    exit_pos = entry_pos + int(horizon_days)
    if exit_pos >= len(df):
        return None
    exit_price = _f(df["close"].iloc[exit_pos])
    if exit_price is None or entry <= 0:
        return None
    ret_pct = round((exit_price - entry) / entry * 100.0, 4)
    return {
        "label": _label(ret_pct),
        "realized_return_pct": ret_pct,
        "horizon_days": int(horizon_days),
        "realized_slippage_bps": slippage,
        "resolved_at": now.isoformat(),
        "exit_model": "fixed_horizon",
    }


def _mark_horizon(
    packet: dict[str, Any],
    df: Any,
    *,
    entry_pos: int,
    horizon_days: int,
    entry: float,
    role: str,
    params: dict[str, Any],
    slippage: float | None,
    now: datetime,
) -> dict[str, Any] | None:
    use_scale = role != "early" and params["model"] == "scale_out"
    if use_scale:
        risk = _risk_per_share(packet, entry)
        if risk is None:
            marked = _mark_fixed(
                df,
                entry_pos=entry_pos,
                horizon_days=horizon_days,
                entry=entry,
                slippage=slippage,
                now=now,
            )
            if marked is not None:
                marked["exit_model"] = "fixed_horizon_no_stop"
            return marked
        marked = simulate_scale_out(
            df,
            entry_pos=entry_pos,
            horizon_days=horizon_days,
            entry=entry,
            risk=risk,
            fraction=float(params["fraction"]),
            target_r=float(params["target_r"]),
            trail_r=float(params["trail_r"]),
        )
        if marked is None:
            return None
        marked["realized_slippage_bps"] = slippage
        marked["resolved_at"] = now.isoformat()
        marked["role"] = role
        return marked
    marked = _mark_fixed(
        df,
        entry_pos=entry_pos,
        horizon_days=horizon_days,
        entry=entry,
        slippage=slippage,
        now=now,
    )
    if marked is not None:
        marked["role"] = role
    return marked


def _stored_horizon(outcome: dict[str, Any], horizon: int) -> bool:
    by_horizon = outcome.get("by_horizon") if isinstance(outcome.get("by_horizon"), dict) else {}
    if str(horizon) in by_horizon:
        return True
    label = str(outcome.get("label") or "pending")
    if label in ("pending", "unknown", ""):
        return False
    try:
        return int(outcome.get("horizon_days")) == int(horizon)
    except (TypeError, ValueError):
        return False


def label_packet_horizons(
    packet: dict[str, Any],
    *,
    history_provider: Callable[[str], Any],
    params: dict[str, Any],
    now: datetime,
) -> str | None:
    """Fill missing horizons. Returns ``primary`` when the promotion label is new."""
    created = _parse_dt(packet.get("created_at"))
    ticker = str(packet.get("ticker") or "").upper()
    if created is None or not ticker:
        return False
    try:
        df = history_provider(ticker)
    except Exception as exc:
        LOG.debug("history fetch failed for %s: %s", ticker, exc)
        return False
    if df is None or getattr(df, "empty", True) or "close" not in getattr(df, "columns", []):
        return False
    try:
        entry_pos = _locate_entry(df, created)
    except Exception as exc:
        LOG.debug("outcome window failed for %s: %s", ticker, exc)
        return False
    if entry_pos is None:
        return False
    entry = _f(packet.get("entry_price")) or _f(df["close"].iloc[entry_pos])
    if not entry or entry <= 0:
        return False
    outcome = packet.get("outcome") if isinstance(packet.get("outcome"), dict) else {}
    slippage = _f(outcome.get("realized_slippage_bps"))
    by_horizon = dict(outcome.get("by_horizon") or {}) if isinstance(outcome.get("by_horizon"), dict) else {}
    legacy_h = None
    try:
        if str(outcome.get("label") or "pending") not in ("pending", "unknown", ""):
            legacy_h = int(outcome.get("horizon_days"))
    except (TypeError, ValueError):
        legacy_h = None
    if legacy_h is not None and str(legacy_h) not in by_horizon:
        by_horizon[str(legacy_h)] = {
            "label": outcome.get("label"),
            "realized_return_pct": outcome.get("realized_return_pct"),
            "horizon_days": legacy_h,
            "exit_model": outcome.get("exit_model") or "fixed_horizon",
            "role": "legacy",
        }
    wrote = False
    primary_new = False
    for role, horizon in (
        ("early", int(params["early"])),
        ("primary", int(params["primary"])),
        ("position", int(params["position"])),
    ):
        key = str(horizon)
        existing = by_horizon.get(key)
        if isinstance(existing, dict) and existing.get("role") not in (None, "legacy"):
            continue
        if isinstance(existing, dict) and existing.get("role") == "legacy" and role == "early":
            continue
        marked = _mark_horizon(
            packet,
            df,
            entry_pos=entry_pos,
            horizon_days=horizon,
            entry=entry,
            role=role,
            params=params,
            slippage=slippage,
            now=now,
        )
        if marked is None:
            continue
        by_horizon[key] = marked
        wrote = True
        if role == "primary":
            primary_new = True
    if not wrote:
        return False
    primary = by_horizon.get(str(int(params["primary"])))
    if isinstance(primary, dict) and primary.get("label") not in (None, "pending", "unknown"):
        packet["outcome"] = {
            "label": primary.get("label"),
            "realized_return_pct": primary.get("realized_return_pct"),
            "horizon_days": int(params["primary"]),
            "realized_slippage_bps": slippage,
            "resolved_at": primary.get("resolved_at") or now.isoformat(),
            "exit_model": primary.get("exit_model"),
            "by_horizon": by_horizon,
        }
        return "primary" if primary_new else "partial"
    kept = outcome if str(outcome.get("label") or "pending") not in ("pending", "unknown", "") else {}
    packet["outcome"] = {
        "label": kept.get("label") or "pending",
        "realized_return_pct": kept.get("realized_return_pct"),
        "horizon_days": kept.get("horizon_days") or int(params["primary"]),
        "realized_slippage_bps": slippage,
        "resolved_at": kept.get("resolved_at"),
        "exit_model": kept.get("exit_model"),
        "by_horizon": by_horizon,
    }
    return "partial"


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
    explicit_horizon = horizon_days is not None
    params = None if explicit_horizon else _load_exit_params(skill_dir)
    horizon = _resolve_horizon(skill_dir, horizon_days) if explicit_horizon else int(params["primary"])
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

    history_span = horizon if explicit_horizon else max(int(params["early"]), int(params["primary"]), int(params["position"]))
    history_days = needed_history_days(packets, horizon_days=history_span, now=now)
    provider = history_provider or _default_history_provider(skill_dir, days=history_days)
    changed = False
    if explicit_horizon:
        resolved = backfill_packets(packets, history_provider=provider, horizon_days=horizon, now=now)
    else:
        resolved = 0
        changed = False
        for packet in packets:
            if not isinstance(packet, dict):
                continue
            wrote = label_packet_horizons(packet, history_provider=provider, params=params, now=now)
            if wrote:
                changed = True
            if wrote == "primary":
                resolved += 1
    # A packet that is old enough to label, and still pending, means the price
    # fetch missed. Do not stamp the day — the next scan must try again.
    overdue = _overdue_pending(packets, horizon, now)
    data["packets"] = packets
    if not overdue:
        data["last_outcome_backfill_on"] = today
    if resolved or changed or not overdue:
        decision_packet._save_raw(path, data)
    return {
        "resolved": resolved,
        "total": len(packets),
        "pending": _pending_count(packets),
        "horizon_days": horizon,
        "early_horizon_days": None if params is None else int(params["early"]),
        "position_horizon_days": None if params is None else int(params["position"]),
        "exit_model": None if params is None else params["model"],
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
    LOG.info(
        "Scan outcome pass: recorded=%s skipped_duplicate=%s resolved=%s/%s skipped=%s",
        recorded.get("recorded"),
        recorded.get("skipped_duplicate"),
        backfill.get("resolved"),
        backfill.get("total"),
        backfill.get("skipped"),
    )
    return {"packets": recorded, "backfill": backfill}
