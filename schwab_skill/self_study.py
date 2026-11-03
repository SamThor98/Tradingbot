"""
Self-study module: learn from trade outcomes to improve signal quality.

Records filled trades (entry/exit prices), computes round-trip returns, and analyzes
performance by MiroFish conviction band and sector. Writes learned thresholds to
.self_study.json for use in signal filtering.

Run via run_self_study() — typically scheduled after market close.
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

LOG = logging.getLogger(__name__)
SKILL_DIR = Path(__file__).resolve().parent
OUTCOMES_FILE = SKILL_DIR / ".trade_outcomes.json"
STUDY_FILE = SKILL_DIR / ".self_study.json"
_LOCK = threading.Lock()

# Minimum round trips before suggesting conviction threshold
MIN_ROUND_TRIPS_FOR_LEARNING = 30

# Minimum samples inside a conviction band before it can influence threshold.
MIN_BAND_SAMPLES_FOR_THRESHOLD = 8

# Empirical-Bayes style shrinkage strength for noisy band averages.
CONVICTION_BAND_SHRINK_PRIOR_TRIPS = 12

# Conviction bands for aggregation
CONVICTION_BANDS = [(0, 20), (20, 40), (40, 60), (60, 80), (80, 101)]

# Fingerprint of scripts/seed_calibration_demo.py — never use as a live floor.
_DEMO_SEED_ROUND_TRIPS = 12
_DEMO_SEED_WIN_RATE = 58.3
_DEMO_SEED_AVG_RETURN_PCT = 2.41
_DEMO_SEED_MIN_CONVICTION = 40
_DEMO_SEED_ADVISORY_SAMPLES = 14
_DEMO_SEED_ADVISORY_HIT_RATE = 0.62

SelfStudyMode = Literal["off", "shadow", "live"]


@dataclass(frozen=True)
class SelfStudyFloor:
    """Resolved conviction floor for the scanner (shadow or live)."""

    mode: SelfStudyMode
    min_conviction: int | None
    reason: str


@dataclass
class RoundTrip:
    """A completed BUY -> SELL cycle for one ticker."""

    ticker: str
    buy_date: str
    buy_price: float
    sell_date: str
    sell_price: float
    qty: int
    return_pct: float
    mirofish_conviction: int | None = None
    sector_etf: str | None = None


def _load_outcomes(skill_dir: Path | None = None) -> list[dict]:
    """Load trade outcomes from disk."""
    path = (skill_dir or SKILL_DIR) / ".trade_outcomes.json"
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text())
        return data if isinstance(data, list) else []
    except Exception as e:
        LOG.warning("Failed to load trade outcomes: %s", e)
        return []


def _save_outcomes(entries: list[dict], skill_dir: Path | None = None) -> None:
    """Persist trade outcomes."""
    path = (skill_dir or SKILL_DIR) / ".trade_outcomes.json"
    try:
        from _io_utils import atomic_write_json

        atomic_write_json(path, entries, indent=2)
    except Exception as e:
        LOG.warning("Failed to save trade outcomes: %s", e)


def record_trade_outcome(
    order_id: str,
    ticker: str,
    side: str,
    qty: int,
    fill_price: float | None,
    skill_dir: Path | None = None,
    mirofish_conviction: int | float | None = None,
    sector_etf: str | None = None,
) -> None:
    """
    Record a filled trade for self-study.
    Called from execution (on order placed, with order_id) and order_monitor (on FILLED, with fill_price).
    """
    skill_dir = skill_dir or SKILL_DIR
    ticker = ticker.upper()
    from datetime import date

    today = date.today().isoformat()

    with _LOCK:
        entries = _load_outcomes(skill_dir)

        # Update existing pending record (by order_id) or append new
        updated = False
        for e in entries:
            if e.get("order_id") == order_id:
                e["fill_price"] = fill_price
                e["side"] = side.upper()
                e["qty"] = int(qty)
                e["date"] = today
                if mirofish_conviction is not None:
                    e["mirofish_conviction"] = int(mirofish_conviction)
                if sector_etf:
                    e["sector_etf"] = sector_etf
                updated = True
                break

        if not updated:
            entries.append(
                {
                    "order_id": order_id,
                    "ticker": ticker,
                    "side": side.upper(),
                    "qty": int(qty),
                    "fill_price": fill_price,
                    "date": today,
                    "mirofish_conviction": int(mirofish_conviction) if mirofish_conviction is not None else None,
                    "sector_etf": sector_etf,
                }
            )

        _save_outcomes(entries, skill_dir)


def register_pending_order(
    order_id: str,
    ticker: str,
    side: str,
    qty: int,
    price_hint: float | None,
    skill_dir: Path | None = None,
    mirofish_conviction: int | float | None = None,
    sector_etf: str | None = None,
) -> None:
    """Register an order immediately after placement (before fill). Fill price updated by order_monitor."""
    record_trade_outcome(
        order_id,
        ticker,
        side,
        qty,
        fill_price=price_hint,
        skill_dir=skill_dir,
        mirofish_conviction=mirofish_conviction,
        sector_etf=sector_etf,
    )


def update_fill_price(order_id: str, fill_price: float, skill_dir: Path | None = None) -> None:
    """Update fill price when order_monitor detects FILLED."""
    skill_dir = skill_dir or SKILL_DIR
    with _LOCK:
        entries = _load_outcomes(skill_dir)
        for e in entries:
            if e.get("order_id") == order_id:
                e["fill_price"] = float(fill_price)
                break
        _save_outcomes(entries, skill_dir)


def upsert_filled_order(
    order_id: str,
    ticker: str,
    side: str,
    qty: int,
    fill_price: float,
    skill_dir: Path | None = None,
) -> None:
    """
    Ensure a FILLED order is persisted even if pending registration was missed.
    This closes gaps where monitor sees fills but no prior pending record exists.
    """
    record_trade_outcome(
        order_id=order_id,
        ticker=ticker,
        side=side,
        qty=qty,
        fill_price=fill_price,
        skill_dir=skill_dir,
    )


def _compute_round_trips(outcomes: list[dict]) -> list[RoundTrip]:
    """
    Match BUY and SELL fills by ticker (FIFO) to form round trips.
    Requires fill_price for both legs.
    """
    buys: list[dict] = []
    sells: list[dict] = []
    for o in outcomes:
        side = (o.get("side") or "").upper()
        fp = o.get("fill_price")
        if fp is None:
            continue
        try:
            fp = float(fp)
        except (TypeError, ValueError):
            continue
        entry = dict(o)
        entry["fill_price"] = fp
        if side == "BUY":
            buys.append(entry)
        elif side == "SELL":
            sells.append(entry)

    round_trips: list[RoundTrip] = []
    # FIFO: pair earliest BUY with earliest SELL for same ticker
    for sell in sells:
        ticker = (sell.get("ticker") or "").upper()
        if not ticker:
            continue
        # Find matching BUY (same ticker, before sell date)
        sell_date = sell.get("date", "")
        matching_buys = [
            b for b in buys if (b.get("ticker") or "").upper() == ticker and (b.get("date") or "") <= sell_date
        ]
        if not matching_buys:
            continue
        buy = min(matching_buys, key=lambda b: (b.get("date", ""), b.get("order_id", "")))
        buy_price = buy.get("fill_price")
        sell_price = sell.get("fill_price")
        if buy_price is None or sell_price is None or buy_price <= 0:
            continue
        ret_pct = 100 * (float(sell_price) - float(buy_price)) / float(buy_price)
        qty = min(int(buy.get("qty", 0) or 0), int(sell.get("qty", 0) or 0)) or 1
        round_trips.append(
            RoundTrip(
                ticker=ticker,
                buy_date=buy.get("date", ""),
                buy_price=float(buy_price),
                sell_date=sell_date,
                sell_price=float(sell_price),
                qty=qty,
                return_pct=ret_pct,
                mirofish_conviction=buy.get("mirofish_conviction"),
                sector_etf=buy.get("sector_etf"),
            )
        )
        buys.remove(buy)  # Consume this BUY
    return round_trips


def run_self_study(skill_dir: Path | None = None) -> dict[str, Any]:
    """
    Analyze trade outcomes and produce learned thresholds.
    Returns study result dict. Writes to .self_study.json.
    """
    skill_dir = skill_dir or SKILL_DIR
    outcomes = _load_outcomes(skill_dir)
    round_trips = _compute_round_trips(outcomes)

    result: dict[str, Any] = {
        "last_run": None,
        "round_trips_count": len(round_trips),
        "win_rate": None,
        "avg_return_pct": None,
        "by_conviction": {},
        "by_sector": {},
        "suggested_min_conviction": None,
        "min_round_trips_met": len(round_trips) >= MIN_ROUND_TRIPS_FOR_LEARNING,
    }

    if not round_trips:
        try:
            from config import get_hypothesis_self_study_merge

            if get_hypothesis_self_study_merge(skill_dir):
                from hypothesis_ledger import summarize_scored_hypotheses

                result["hypothesis_calibration"] = summarize_scored_hypotheses(skill_dir)
        except Exception as e:
            LOG.debug("Hypothesis self-study merge skipped: %s", e)
        try:
            from datetime import datetime, timezone

            from _io_utils import atomic_write_json

            result["last_run"] = datetime.now(timezone.utc).isoformat()
            atomic_write_json(skill_dir / ".self_study.json", result, indent=2)
        except Exception as e:
            LOG.warning("Self-study write failed: %s", e)
        return result

    wins = sum(1 for r in round_trips if r.return_pct > 0)
    result["win_rate"] = round(100 * wins / len(round_trips), 1)
    result["avg_return_pct"] = round(sum(r.return_pct for r in round_trips) / len(round_trips), 2)

    # By conviction band
    for lo, hi in CONVICTION_BANDS:
        band_trips = [r for r in round_trips if r.mirofish_conviction is not None and lo <= r.mirofish_conviction < hi]
        if band_trips:
            band_wins = sum(1 for r in band_trips if r.return_pct > 0)
            result["by_conviction"][f"{lo}-{hi}"] = {
                "count": len(band_trips),
                "win_rate": round(100 * band_wins / len(band_trips), 1),
                "avg_return_pct": round(sum(r.return_pct for r in band_trips) / len(band_trips), 2),
            }

    # By sector
    sectors: dict[str, list[RoundTrip]] = {}
    for r in round_trips:
        sec = r.sector_etf or "unknown"
        sectors.setdefault(sec, []).append(r)
    for sec, trips in sectors.items():
        sec_wins = sum(1 for r in trips if r.return_pct > 0)
        result["by_sector"][sec] = {
            "count": len(trips),
            "win_rate": round(100 * sec_wins / len(trips), 1),
            "avg_return_pct": round(sum(r.return_pct for r in trips) / len(trips), 2),
        }

    # Floor = first well-sampled band where shrinkage-adjusted expectancy
    # turns sustainably positive (lower well-sampled bands are non-positive,
    # later well-sampled bands stay positive). All-green books yield no floor.
    if result["min_round_trips_met"] and result["by_conviction"]:
        result["suggested_min_conviction"] = suggest_min_conviction_from_bands(
            result["by_conviction"],
            float(result.get("avg_return_pct") or 0.0),
        )
    result["threshold_rule"] = "first_sustainable_positive_band"

    try:
        from config import get_hypothesis_self_study_merge

        if get_hypothesis_self_study_merge(skill_dir):
            from hypothesis_ledger import summarize_scored_hypotheses

            result["hypothesis_calibration"] = summarize_scored_hypotheses(skill_dir)
    except Exception as e:
        LOG.debug("Hypothesis self-study merge skipped: %s", e)

    try:
        from datetime import datetime, timezone

        from _io_utils import atomic_write_json

        result["last_run"] = datetime.now(timezone.utc).isoformat()
        atomic_write_json(skill_dir / ".self_study.json", result, indent=2)
        LOG.info(
            "Self-study complete: %d round trips, win_rate=%.1f%%, suggested_min_conviction=%s",
            len(round_trips),
            result["win_rate"] or 0,
            result["suggested_min_conviction"],
        )
    except Exception as e:
        LOG.warning("Self-study write failed: %s", e)

    return result


def _band_shrunk_avg(band: dict[str, Any], global_avg: float) -> tuple[int, float]:
    band_count = int(band.get("count", 0) or 0)
    band_avg = float(band.get("avg_return_pct", 0.0) or 0.0)
    shrunk_avg = ((band_avg * band_count) + (global_avg * CONVICTION_BAND_SHRINK_PRIOR_TRIPS)) / max(
        1, band_count + CONVICTION_BAND_SHRINK_PRIOR_TRIPS
    )
    return band_count, shrunk_avg


def suggest_min_conviction_from_bands(
    by_conviction: dict[str, Any],
    global_avg_return_pct: float,
) -> int | None:
    """Return the lowest conviction floor supported by band expectancy.

    Requires a well-sampled non-positive band below a well-sampled positive
    band, with every later well-sampled band remaining positive. Does not
    ratchet to the highest profitable band, and does not invent a floor when
    every well-sampled band is already green.
    """
    well: list[tuple[int, float]] = []
    for lo, hi in CONVICTION_BANDS:
        band = by_conviction.get(f"{lo}-{hi}")
        if not isinstance(band, dict):
            continue
        band_count, shrunk_avg = _band_shrunk_avg(band, global_avg_return_pct)
        if band_count >= MIN_BAND_SAMPLES_FOR_THRESHOLD:
            well.append((lo, shrunk_avg))
    if not well:
        return None
    for i, (lo, shrunk) in enumerate(well):
        if shrunk <= 0:
            continue
        below = well[:i]
        above = well[i:]
        if not below:
            return None
        if any(avg > 0 for _, avg in below):
            continue
        if not all(avg > 0 for _, avg in above):
            continue
        if all(avg <= 0 for _, avg in below):
            return lo
    return None


def is_demo_calibration_seed(data: dict[str, Any]) -> bool:
    """True when `.self_study.json` matches scripts/seed_calibration_demo.py."""
    try:
        trips = int(data.get("round_trips_count") or 0)
        min_conv = data.get("suggested_min_conviction")
        min_conv_i = int(min_conv) if min_conv is not None else None
    except (TypeError, ValueError):
        return False
    if trips != _DEMO_SEED_ROUND_TRIPS or min_conv_i != _DEMO_SEED_MIN_CONVICTION:
        return False
    if data.get("win_rate") != _DEMO_SEED_WIN_RATE:
        return False
    if data.get("avg_return_pct") != _DEMO_SEED_AVG_RETURN_PCT:
        return False
    by_source = (data.get("hypothesis_calibration") or {}).get("by_source") or {}
    advisory = by_source.get("advisory") or {}
    return (
        advisory.get("scored_samples") == _DEMO_SEED_ADVISORY_SAMPLES
        and advisory.get("hit_rate") == _DEMO_SEED_ADVISORY_HIT_RATE
    )


def _parse_suggested_min_conviction(raw: Any) -> int | None:
    if raw is None:
        return None
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def _floor_from_study_payload(data: dict[str, Any]) -> tuple[int | None, str]:
    if is_demo_calibration_seed(data):
        return None, "demo_seed"
    try:
        trips = int(data.get("round_trips_count") or 0)
    except (TypeError, ValueError):
        trips = 0
    if trips < MIN_ROUND_TRIPS_FOR_LEARNING:
        return None, "insufficient_round_trips"
    if not data.get("min_round_trips_met"):
        return None, "min_round_trips_not_met"
    min_conv = _parse_suggested_min_conviction(data.get("suggested_min_conviction"))
    if min_conv is None:
        return None, "no_threshold"
    return min_conv, "applied"


def resolve_self_study_floor(skill_dir: Path | None = None) -> SelfStudyFloor:
    """Resolve mode + applyable min conviction for the scanner.

    A floor is applyable only when the study file is not the demo seed, has
    at least ``MIN_ROUND_TRIPS_FOR_LEARNING`` round trips, and carries a
    positive ``suggested_min_conviction``. Process env / ``SELF_STUDY_MODE``
    win over the legacy ``SELF_STUDY_ENABLED`` boolean.
    """
    skill_dir = skill_dir or SKILL_DIR
    try:
        from config import get_self_study_mode

        raw_mode = str(get_self_study_mode(skill_dir) or "off").strip().lower()
    except Exception:
        raw_mode = "off"
    mode: SelfStudyMode = raw_mode if raw_mode in ("off", "shadow", "live") else "off"
    if mode == "off":
        return SelfStudyFloor(mode="off", min_conviction=None, reason="mode_off")

    study_path = skill_dir / ".self_study.json"
    if not study_path.exists():
        return SelfStudyFloor(mode=mode, min_conviction=None, reason="no_study_file")
    try:
        data = json.loads(study_path.read_text(encoding="utf-8"))
    except Exception:
        return SelfStudyFloor(mode=mode, min_conviction=None, reason="invalid_study_file")
    if not isinstance(data, dict):
        return SelfStudyFloor(mode=mode, min_conviction=None, reason="invalid_study_file")
    min_conv, reason = _floor_from_study_payload(data)
    return SelfStudyFloor(mode=mode, min_conviction=min_conv, reason=reason)


def signal_below_self_study_floor(signal: dict[str, Any], min_conv: int) -> bool:
    """True when a live-book signal is below the learned conviction floor."""
    if str(signal.get("entry_family") or "").strip().lower() in {"horizon", "pead_primary"}:
        return False
    try:
        conviction = float(signal.get("mirofish_conviction") or 0)
    except (TypeError, ValueError):
        conviction = 0.0
    return conviction < float(min_conv)


def get_learned_min_conviction(skill_dir: Path | None = None) -> int | None:
    """
    Return suggested minimum MiroFish conviction from self-study, if available
    and the gate is in shadow or live with an applyable floor.
    """
    return resolve_self_study_floor(skill_dir).min_conviction
