"""Shadow-only setup filter.

Records which candidate rules would keep each final scan signal. It does not
reorder, drop, size, or trade. Rows are append-only JSONL under
``data/shadow_setup_filter/`` (gitignored). The outcome resolver runs only when
called and reuses the price cache, then the existing daily-history path.

Rules, evaluated at scan time:

- R1: ``signal_score`` <= max (default 36)
- R1b: close at least ``min_pct_below_52w`` under the 52-week high (default 3%)
- R2: ``sector_breadth50`` <= max (default 0.75)
- R3: R1 and R1b and R2
- R4: ATR(14) as a percent of close >= min (default 2.14)
- R3+R4: R3 and R4

``pct_below_52w_high`` and ``distance_above_sma50`` are fractions of price.
``atr_pct`` is percent of close, so 2.14 means 2.14%. ``sector_breadth50`` is
the share of that sector's scanned names whose loaded close is above the
50-day average. Sector labels come from the lookup the scan already did, or
from the on-disk sector cache. Names still missing a sector are left out of
the ratio. No new sector lookup runs from this module.
"""

from __future__ import annotations

import json
import logging
import statistics
import threading
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

LOG = logging.getLogger(__name__)

STORE_DIR = Path("data") / "shadow_setup_filter"
STORE_NAME = "observations.jsonl"
_ET = ZoneInfo("America/New_York")
_LOCK = threading.Lock()
_TRADING_DAYS_52W = 252

RULES: tuple[tuple[str, str], ...] = (
    ("R1", "r1"),
    ("R1b", "r1b"),
    ("R2", "r2"),
    ("R3", "r3"),
    ("R4", "r4"),
    ("R3+R4", "r3_r4"),
)


@dataclass(frozen=True)
class ShadowSetupThresholds:
    max_signal_score: float = 36.0
    min_pct_below_52w: float = 0.03
    max_sector_breadth50: float = 0.75
    min_atr_pct: float = 2.14
    horizons: tuple[int, ...] = (5, 10, 20)
    stop_pcts: tuple[float, ...] = (0.05, 0.08, 0.12)


def thresholds_from_config(skill_dir: Path | None = None) -> ShadowSetupThresholds:
    from config import (
        get_shadow_setup_filter_forward_horizons,
        get_shadow_setup_filter_max_sector_breadth50,
        get_shadow_setup_filter_max_signal_score,
        get_shadow_setup_filter_min_atr_pct,
        get_shadow_setup_filter_min_pct_below_52w,
        get_shadow_setup_filter_stop_pcts,
    )

    return ShadowSetupThresholds(
        max_signal_score=float(get_shadow_setup_filter_max_signal_score(skill_dir)),
        min_pct_below_52w=float(get_shadow_setup_filter_min_pct_below_52w(skill_dir)),
        max_sector_breadth50=float(get_shadow_setup_filter_max_sector_breadth50(skill_dir)),
        min_atr_pct=float(get_shadow_setup_filter_min_atr_pct(skill_dir)),
        horizons=tuple(get_shadow_setup_filter_forward_horizons(skill_dir)),
        stop_pcts=tuple(get_shadow_setup_filter_stop_pcts(skill_dir)),
    )


def store_path(skill_dir: Path | None) -> Path | None:
    if skill_dir is None:
        return None
    return Path(skill_dir) / STORE_DIR / STORE_NAME


def scan_date_et(now: datetime | None = None) -> str:
    moment = now or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(_ET).date().isoformat()


def _float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number in (float("inf"), float("-inf")):
        return None
    return number


def _round(value: float | None, places: int) -> float | None:
    if value is None:
        return None
    return round(float(value), places)


def sma50_probe(ticker: str, frame: Any) -> dict[str, Any]:
    """One universe name: is the loaded close above its 50-day average?"""
    above: bool | None = None
    try:
        close = float(frame["close"].iloc[-1])
        sma = float(frame["sma_50"].iloc[-1]) if "sma_50" in getattr(frame, "columns", []) else float("nan")
        if sma == sma and sma > 0 and close == close:
            above = bool(close > sma)
    except Exception:
        above = None
    return {"ticker": str(ticker or "").upper().strip(), "above_sma50": above}


def bar_features(frame: Any, *, lookback: int = _TRADING_DAYS_52W) -> dict[str, Any] | None:
    """Geometry from a frame the scan already loaded. ``atr_pct`` is percent of close."""
    try:
        if frame is None or len(frame) < 1:
            return None
        close = float(frame["close"].iloc[-1])
        if close != close or close <= 0:
            return None
        window = frame.iloc[-min(int(lookback), len(frame)) :]
        high_52w = float(window["high"].astype(float).max())
        pct_below = ((high_52w - close) / high_52w) if high_52w > 0 else None
        sma = float(frame["sma_50"].iloc[-1]) if "sma_50" in frame.columns else float("nan")
        distance = ((close - sma) / sma) if sma == sma and sma > 0 else None
        atr = float(frame["atr_14"].iloc[-1]) if "atr_14" in frame.columns else float("nan")
        atr_pct = (atr / close) * 100.0 if atr == atr and atr >= 0 else None
    except Exception:
        return None
    return {
        "close": _round(close, 4),
        "high_52w": _round(high_52w, 4),
        "pct_below_52w_high": _round(pct_below, 6),
        "sma_50": None if sma != sma else _round(sma, 4),
        "distance_above_sma50": _round(distance, 6),
        "atr_14": None if atr != atr else _round(atr, 6),
        "atr_pct": _round(atr_pct, 4),
    }


def take_breadth_probe(out: dict[str, Any], samples: dict[str, dict[str, Any]], ticker: str = "") -> None:
    """Pop the stage-A probe so it never rides along in checkpoints or logs."""
    probe = out.pop("_shadow_breadth_probe", None)
    if not isinstance(probe, dict):
        return
    key = str(probe.get("ticker") or ticker or "").upper().strip()
    if not key:
        return
    probe["ticker"] = key
    samples.setdefault(key, probe)


def take_bar_probe(out: dict[str, Any], features: dict[str, dict[str, Any]], ticker: str) -> None:
    probe = out.pop("shadow_setup_probe", None)
    if not isinstance(probe, dict):
        return
    key = str(ticker or "").upper().strip()
    if key:
        features[key] = probe


def _cached_sector_map(skill_dir: Path | None) -> dict[str, str]:
    """On-disk sector cache only. Does not call the network."""
    if skill_dir is None:
        return {}
    try:
        from sector_strength import _load_sector_cache

        mapping = _load_sector_cache(skill_dir).get("sector_etf_by_ticker") or {}
    except Exception:
        LOG.debug("Shadow setup sector cache unreadable", exc_info=True)
        return {}
    out: dict[str, str] = {}
    if not isinstance(mapping, dict):
        return out
    for ticker, etf in mapping.items():
        if isinstance(etf, str) and etf.strip():
            out[str(ticker).upper()] = etf.strip().upper()
    return out


def sector_breadth_map(samples: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Share of names in each sector whose loaded close is above the 50-day average."""
    counts: dict[str, list[int]] = {}
    for probe in samples.values():
        if not isinstance(probe, dict):
            continue
        sector = str(probe.get("sector_etf") or "").upper().strip()
        above = probe.get("above_sma50")
        if not sector or above is None:
            continue
        bucket = counts.setdefault(sector, [0, 0])
        bucket[1] += 1
        if bool(above):
            bucket[0] += 1
    out: dict[str, dict[str, Any]] = {}
    for sector, (n_above, total) in counts.items():
        if total <= 0:
            continue
        out[sector] = {
            "sector_breadth50": n_above / total,
            "sector_breadth_above": n_above,
            "sector_breadth_n": total,
        }
    return out


def evaluate_rules(
    *,
    signal_score: float | None,
    pct_below_52w_high: float | None,
    sector_breadth50: float | None,
    atr_pct: float | None,
    thresholds: ShadowSetupThresholds | None = None,
) -> dict[str, Any]:
    """Keep/drop flags. Missing inputs are unevaluable, not a silent keep."""
    limits = thresholds or ShadowSetupThresholds()
    score = _float(signal_score)
    below = _float(pct_below_52w_high)
    breadth = _float(sector_breadth50)
    atr = _float(atr_pct)
    r1_evaluable = score is not None
    r1_keep = bool(r1_evaluable and score <= limits.max_signal_score)
    r1b_evaluable = below is not None
    r1b_keep = bool(r1b_evaluable and below >= limits.min_pct_below_52w)
    r2_evaluable = breadth is not None
    r2_keep = bool(r2_evaluable and breadth <= limits.max_sector_breadth50)
    r3_evaluable = r1_evaluable and r1b_evaluable and r2_evaluable
    r3_keep = bool(r3_evaluable and r1_keep and r1b_keep and r2_keep)
    r4_evaluable = atr is not None
    r4_keep = bool(r4_evaluable and atr >= limits.min_atr_pct)
    r3_r4_evaluable = r3_evaluable and r4_evaluable
    r3_r4_keep = bool(r3_r4_evaluable and r3_keep and r4_keep)
    return {
        "r1_evaluable": r1_evaluable,
        "r1_keep": r1_keep,
        "r1b_evaluable": r1b_evaluable,
        "r1b_keep": r1b_keep,
        "r2_evaluable": r2_evaluable,
        "r2_keep": r2_keep,
        "r3_evaluable": r3_evaluable,
        "r3_keep": r3_keep,
        "r4_evaluable": r4_evaluable,
        "r4_keep": r4_keep,
        "r3_r4_evaluable": r3_r4_evaluable,
        "r3_r4_keep": r3_r4_keep,
    }


def _pct_below_from_signal(signal: dict[str, Any]) -> float | None:
    comps = signal.get("score_components") if isinstance(signal.get("score_components"), dict) else {}
    ratio = _float(comps.get("pct_from_52w_high"))
    if ratio is None:
        ratio = _float(signal.get("pct_from_52w_high"))
    if ratio is None:
        return None
    return 1.0 - ratio


def _distance_from_signal(signal: dict[str, Any]) -> float | None:
    price = _float(signal.get("price"))
    sma = _float(signal.get("sma_50"))
    if price is None or sma is None or sma <= 0:
        return None
    return (price - sma) / sma


def _low_score_ranks(signals: list[dict[str, Any]]) -> list[int]:
    order = sorted(
        range(len(signals)),
        key=lambda index: (
            _float(signals[index].get("signal_score")) is None,
            _float(signals[index].get("signal_score")) or 0.0,
            str(signals[index].get("ticker") or ""),
            index,
        ),
    )
    ranks = [0] * len(signals)
    for rank, index in enumerate(order, start=1):
        ranks[index] = rank
    return ranks


def build_signal_rows(
    signals: list[dict[str, Any]],
    *,
    scan_id: str | None,
    scan_date: str,
    logged_at: str,
    thresholds: ShadowSetupThresholds,
    breadth: dict[str, dict[str, Any]] | None = None,
    bar_features_by_ticker: dict[str, dict[str, Any]] | None = None,
    cohort: str = "final",
) -> list[dict[str, Any]]:
    """Feature rows. Does not write and does not mutate ``signals``."""
    breadth = breadth or {}
    bars = bar_features_by_ticker or {}
    low_ranks = _low_score_ranks(signals)
    limit_block = {
        "max_signal_score": thresholds.max_signal_score,
        "min_pct_below_52w": thresholds.min_pct_below_52w,
        "max_sector_breadth50": thresholds.max_sector_breadth50,
        "min_atr_pct": thresholds.min_atr_pct,
    }
    rows: list[dict[str, Any]] = []
    for index, signal in enumerate(signals):
        if not isinstance(signal, dict):
            continue
        ticker = str(signal.get("ticker") or "").upper().strip()
        if not ticker:
            continue
        bar = bars.get(ticker) if isinstance(bars.get(ticker), dict) else {}
        sector = str(signal.get("sector_etf") or bar.get("sector_etf") or "").upper().strip() or None
        breadth_row = breadth.get(sector) if sector else None
        if not isinstance(breadth_row, dict):
            breadth_row = {}
        pct_below = _float(bar.get("pct_below_52w_high"))
        if pct_below is None:
            pct_below = _pct_below_from_signal(signal)
        distance = _float(bar.get("distance_above_sma50"))
        if distance is None:
            distance = _distance_from_signal(signal)
        atr_pct = _float(bar.get("atr_pct"))
        entry = _float(signal.get("price"))
        if entry is None:
            entry = _float(bar.get("close"))
        score = _float(signal.get("signal_score"))
        breadth_value = _float(breadth_row.get("sector_breadth50"))
        flags = evaluate_rules(
            signal_score=score,
            pct_below_52w_high=pct_below,
            sector_breadth50=breadth_value,
            atr_pct=atr_pct,
            thresholds=thresholds,
        )
        if "sector_winning" in signal:
            sector_winning: bool | None = bool(signal.get("sector_winning"))
        else:
            sector_winning = None
        rows.append(
            {
                "record_type": "signal",
                "cohort": cohort,
                "scan_id": scan_id,
                "scan_date": scan_date,
                "logged_at": logged_at,
                "ticker": ticker,
                "entry_price": _round(entry, 4),
                "close": _round(_float(bar.get("close")), 4),
                "signal_score": _round(score, 4),
                "existing_rank": index + 1,
                "low_score_first_rank": low_ranks[index],
                "pct_below_52w_high": _round(pct_below, 6),
                "distance_above_sma50": _round(distance, 6),
                "sector_etf": sector,
                "sector_winning": sector_winning,
                "sector_breadth50": _round(breadth_value, 6),
                "sector_breadth_above": breadth_row.get("sector_breadth_above"),
                "sector_breadth_n": breadth_row.get("sector_breadth_n"),
                "stop_pct": _round(_float(signal.get("stop_pct")), 6),
                "atr_pct": _round(atr_pct, 4),
                "thresholds": limit_block,
                **flags,
            }
        )
    return rows


def _prepare_breadth(
    samples: dict[str, Any],
    skill_dir: Path | None,
) -> dict[str, dict[str, Any]]:
    cleaned: dict[str, dict[str, Any]] = {}
    for ticker, probe in samples.items():
        if isinstance(probe, dict):
            cleaned[str(ticker).upper()] = dict(probe)
    if not cleaned:
        return {}
    cache = _cached_sector_map(skill_dir)
    for ticker, probe in cleaned.items():
        if probe.get("sector_etf"):
            probe["sector_etf"] = str(probe.get("sector_etf")).upper()
            continue
        cached = cache.get(ticker)
        if cached:
            probe["sector_etf"] = cached
    return sector_breadth_map(cleaned)


def _rule_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for _label, key in RULES:
        kept = sum(1 for row in rows if row.get(f"{key}_keep"))
        dropped = sum(1 for row in rows if row.get(f"{key}_evaluable") and not row.get(f"{key}_keep"))
        counts[f"shadow_setup_filter_{key}_would_keep"] = kept
        counts[f"shadow_setup_filter_{key}_would_drop"] = dropped
    return counts


def load_rows(skill_dir: Path | None) -> list[dict[str, Any]]:
    path = store_path(skill_dir)
    if path is None or not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if not text:
            continue
        try:
            row = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def append_rows(skill_dir: Path | None, rows: list[dict[str, Any]]) -> int:
    path = store_path(skill_dir)
    if path is None or not rows:
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = "".join(json.dumps(row, sort_keys=True, default=str) + "\n" for row in rows)
    with _LOCK:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(payload)
    return len(rows)


def record_final_candidates(
    signals: list[dict[str, Any]],
    diagnostics: dict[str, Any],
    *,
    skill_dir: Path | None,
    now: datetime | None = None,
) -> int:
    """Append one row per final signal. Leaves ``signals`` unchanged."""
    from config import get_shadow_setup_filter_enabled

    if not get_shadow_setup_filter_enabled(skill_dir):
        return 0
    started = datetime.now(timezone.utc)
    moment = now or started
    thresholds = thresholds_from_config(skill_dir)
    samples = diagnostics.get("_shadow_breadth_samples")
    bars = diagnostics.get("_shadow_bar_features")
    breadth = _prepare_breadth(samples if isinstance(samples, dict) else {}, skill_dir)
    rows = build_signal_rows(
        list(signals or []),
        scan_id=str(diagnostics.get("scan_id") or "") or None,
        scan_date=scan_date_et(moment),
        logged_at=moment.astimezone(timezone.utc).isoformat(),
        thresholds=thresholds,
        breadth=breadth,
        bar_features_by_ticker=bars if isinstance(bars, dict) else {},
    )
    written = 0
    try:
        written = append_rows(skill_dir, rows)
    except Exception:
        LOG.warning("Shadow setup filter log failed", exc_info=True)
        written = 0
    elapsed_ms = (datetime.now(timezone.utc) - started).total_seconds() * 1000.0
    diagnostics["shadow_setup_filter_enabled"] = 1
    diagnostics["shadow_setup_filter_logged"] = written
    diagnostics["shadow_setup_filter_ms"] = round(elapsed_ms, 3)
    diagnostics["shadow_setup_filter_breadth_sectors"] = len(breadth)
    diagnostics.update(_rule_counts(rows))
    LOG.info("Shadow setup filter logged %s final candidates in %.1f ms", written, elapsed_ms)
    return written


def log_regime_block(
    skill_dir: Path | None,
    diagnostics: dict[str, Any],
    *,
    reason: str,
    now: datetime | None = None,
) -> int:
    """Mark a regime-blocked scan. Per-signal rows are not computed.

    The live gate returns before bars are loaded, so capturing those names
    would mean running the scan. This records that the capture was skipped.
    """
    from config import get_shadow_setup_filter_enabled

    if not get_shadow_setup_filter_enabled(skill_dir):
        return 0
    moment = now or datetime.now(timezone.utc)
    row = {
        "record_type": "scan_marker",
        "cohort": "regime_blocked",
        "capture": "not_run",
        "reason": str(reason or ""),
        "scan_id": diagnostics.get("scan_id"),
        "scan_date": scan_date_et(moment),
        "logged_at": moment.astimezone(timezone.utc).isoformat(),
        "note": "Regime gate returned before bars were loaded, so per-signal features were not computed.",
    }
    try:
        written = append_rows(skill_dir, [row])
    except Exception:
        LOG.warning("Shadow setup regime marker failed", exc_info=True)
        written = 0
    diagnostics["shadow_setup_filter_enabled"] = 1
    diagnostics["shadow_setup_filter_regime_capture"] = "not_run"
    return written


def stop_label(stop_pct: float) -> str:
    points = float(stop_pct) * 100.0
    if abs(points - round(points)) < 1e-6:
        return str(int(round(points)))
    text = f"{points:.2f}".rstrip("0").rstrip(".")
    return text or "0"


def _normalize_frame(frame: Any) -> Any | None:
    import pandas as pd

    if frame is None:
        return None
    try:
        df = frame.copy()
    except Exception:
        return None
    if getattr(df, "empty", True):
        return None
    df.columns = [str(column).strip().lower() for column in df.columns]
    if "date" in df.columns:
        df = df.set_index("date")
    elif "datetime" in df.columns:
        df = df.set_index("datetime")
    if "close" not in df.columns:
        return None
    idx = pd.to_datetime(df.index, errors="coerce")
    if getattr(idx, "tz", None) is not None:
        idx = idx.tz_convert("UTC").tz_localize(None)
    df.index = pd.DatetimeIndex(idx).normalize()
    df = df[~df.index.isna()]
    df = df.sort_index()
    df = df[~df.index.duplicated(keep="last")]
    df = df[pd.to_numeric(df["close"], errors="coerce").notna()]
    if df.empty:
        return None
    return df


def forward_stats(
    frame: Any,
    *,
    scan_date: str,
    entry_price: float,
    horizons: tuple[int, ...] = (5, 10, 20),
    stop_pcts: tuple[float, ...] = (0.05, 0.08, 0.12),
) -> dict[str, Any]:
    """Forward closes, MFE/MAE, and stop hits after ``scan_date``.

    Returns are close-to-entry: close of the Nth later session divided by the
    stored entry price, minus one. The path starts the session after the scan
    date. Stops use the low. A stop is hit when a low is at or below
    ``entry * (1 - stop)``. MFE/MAE and stops use the bars inside the longest
    horizon (fewer when that much data is not in the frame yet).
    """
    import pandas as pd

    horizons = tuple(int(h) for h in horizons if int(h) > 0)
    if not horizons:
        horizons = (5, 10, 20)
    longest = max(horizons)
    out: dict[str, Any] = {"forward_bars": 0, "window_bars": 0, "mfe": None, "mae": None}
    for horizon in horizons:
        out[f"ret_{horizon}d"] = None
    for stop in stop_pcts:
        label = stop_label(stop)
        out[f"stop_hit_{label}"] = None
        out[f"stop_hit_day_{label}"] = None
    entry = _float(entry_price)
    if entry is None or entry <= 0:
        return out
    try:
        day = pd.Timestamp(scan_date).normalize()
    except Exception:
        return out
    df = _normalize_frame(frame)
    if df is None:
        return out
    forward = df[df.index > day]
    out["forward_bars"] = int(len(forward))
    for horizon in horizons:
        if len(forward) >= horizon:
            close_n = float(forward["close"].iloc[horizon - 1])
            out[f"ret_{horizon}d"] = _round((close_n / entry) - 1.0, 8)
    window = forward.iloc[:longest]
    out["window_bars"] = int(len(window))
    if window.empty:
        return out
    highs = window["high"] if "high" in window.columns else window["close"]
    lows = window["low"] if "low" in window.columns else window["close"]
    high_values = [float(value) for value in highs.tolist() if _float(value) is not None]
    low_values = [float(value) for value in lows.tolist() if _float(value) is not None]
    if high_values:
        out["mfe"] = _round((max(high_values) / entry) - 1.0, 8)
    if low_values:
        out["mae"] = _round((min(low_values) / entry) - 1.0, 8)
    for stop in stop_pcts:
        label = stop_label(float(stop))
        if not low_values:
            continue
        level = entry * (1.0 - float(stop))
        hit_day: int | None = None
        for offset, raw in enumerate(lows.tolist(), start=1):
            low = _float(raw)
            if low is None:
                continue
            if low <= level:
                hit_day = offset
                break
        out[f"stop_hit_{label}"] = hit_day is not None
        out[f"stop_hit_day_{label}"] = hit_day
    return out


def _outcome_key(row: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(row.get("scan_date") or ""),
        str(row.get("scan_id") or ""),
        str(row.get("ticker") or "").upper(),
    )


def _latest_outcomes(rows: list[dict[str, Any]]) -> dict[tuple[str, str, str], dict[str, Any]]:
    best: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in rows:
        if row.get("record_type") != "outcome":
            continue
        key = _outcome_key(row)
        prev = best.get(key)
        if prev is None or int(row.get("forward_bars") or 0) >= int(prev.get("forward_bars") or 0):
            best[key] = row
    return best


def _same_outcome(previous: dict[str, Any], new_stats: dict[str, Any]) -> bool:
    if int(previous.get("forward_bars") or 0) != int(new_stats.get("forward_bars") or 0):
        return False
    for key, value in new_stats.items():
        if previous.get(key) != value:
            return False
    return True


def _forward_count(frame: Any, scan_date: str) -> int:
    import pandas as pd

    df = _normalize_frame(frame)
    if df is None or not scan_date:
        return 0
    try:
        day = pd.Timestamp(scan_date).normalize()
    except Exception:
        return 0
    return int((df.index > day).sum())


def _cache_frame(skill_dir: Path, ticker: str) -> Any | None:
    try:
        from backtest import read_price_cache_frame, resolve_price_cache_dir
        from config import get_backtest_price_cache_dir
    except Exception:
        LOG.debug("Shadow setup price cache import failed", exc_info=True)
        return None
    cache_dir = resolve_price_cache_dir(get_backtest_price_cache_dir(skill_dir), skill_dir)
    if cache_dir is None:
        return None
    try:
        return read_price_cache_frame(cache_dir, ticker)
    except Exception:
        LOG.debug("Shadow setup price cache read failed for %s", ticker, exc_info=True)
        return None


def _lookback_days(rows: list[dict[str, Any]], now: datetime) -> int:
    oldest: date | None = None
    for row in rows:
        if row.get("record_type") != "signal":
            continue
        try:
            day = date.fromisoformat(str(row.get("scan_date") or ""))
        except ValueError:
            continue
        if oldest is None or day < oldest:
            oldest = day
    if oldest is None:
        return 180
    span = (now.date() - oldest).days + 40
    return max(60, min(span, 1200))


def default_history_provider(
    skill_dir: Path,
    *,
    days: int,
    allow_network: bool,
    requirements: dict[str, tuple[str, int]] | None = None,
) -> Callable[[str], Any]:
    """Price cache first. ``market_data.get_daily_history`` only if the cache falls short.

    ``requirements`` maps a ticker to ``(earliest scan date, bars needed after it)``.
    A cache that already has that many later sessions is used as-is.
    """
    needed = requirements or {}

    def provide(ticker: str) -> Any:
        cached = _cache_frame(skill_dir, ticker)
        scan_date, minimum = needed.get(str(ticker).upper(), ("", 0))
        cached_bars = _forward_count(cached, scan_date) if scan_date else 0
        cache_hit = cached is not None and getattr(cached, "empty", True) is False
        if cache_hit and (not scan_date or cached_bars >= minimum):
            return cached
        if not allow_network:
            return cached
        from market_data import get_daily_history

        live = get_daily_history(ticker, days=days, skill_dir=skill_dir)
        if _forward_count(live, scan_date) >= cached_bars:
            return live
        return cached if cache_hit else live

    return provide


def resolve_outcomes(
    skill_dir: Path,
    *,
    history_provider: Callable[[str], Any] | None = None,
    allow_network: bool = True,
    now: datetime | None = None,
    thresholds: ShadowSetupThresholds | None = None,
) -> dict[str, Any]:
    """Fill forward returns for logged signals. Does not schedule any job."""
    moment = now or datetime.now(timezone.utc)
    limits = thresholds or thresholds_from_config(skill_dir)
    rows = load_rows(skill_dir)
    outcomes = _latest_outcomes(rows)
    pending = [row for row in rows if row.get("record_type") == "signal" and _float(row.get("entry_price"))]
    provider = history_provider
    if provider is None:
        longest = max(limits.horizons) if limits.horizons else 20
        requirements: dict[str, tuple[str, int]] = {}
        for row in pending:
            ticker = str(row.get("ticker") or "").upper()
            scan_day = str(row.get("scan_date") or "")
            if not ticker:
                continue
            previous = requirements.get(ticker)
            if previous is None or (scan_day and scan_day < previous[0]):
                requirements[ticker] = (scan_day, longest)
        provider = default_history_provider(
            skill_dir,
            days=_lookback_days(rows, moment),
            allow_network=allow_network,
            requirements=requirements,
        )
    frames: dict[str, Any] = {}
    appended: list[dict[str, Any]] = []
    skipped = 0
    for row in pending:
        ticker = str(row.get("ticker") or "").upper()
        entry = _float(row.get("entry_price"))
        if not ticker or entry is None:
            skipped += 1
            continue
        if ticker not in frames:
            try:
                frames[ticker] = provider(ticker)
            except Exception:
                LOG.warning("Shadow setup history lookup failed for %s", ticker, exc_info=True)
                frames[ticker] = None
        stats = forward_stats(
            frames.get(ticker),
            scan_date=str(row.get("scan_date") or ""),
            entry_price=entry,
            horizons=limits.horizons,
            stop_pcts=limits.stop_pcts,
        )
        if int(stats.get("forward_bars") or 0) <= 0:
            skipped += 1
            continue
        previous = outcomes.get(_outcome_key(row))
        if previous is not None and _same_outcome(previous, stats):
            skipped += 1
            continue
        outcome = {
            "record_type": "outcome",
            "cohort": row.get("cohort") or "final",
            "scan_id": row.get("scan_id"),
            "scan_date": row.get("scan_date"),
            "ticker": ticker,
            "entry_price": entry,
            "resolved_at": moment.astimezone(timezone.utc).isoformat(),
            **stats,
        }
        appended.append(outcome)
        outcomes[_outcome_key(outcome)] = outcome
    written = 0
    try:
        written = append_rows(skill_dir, appended)
    except Exception:
        LOG.warning("Shadow setup outcome append failed", exc_info=True)
        written = 0
    return {
        "pending": len(pending),
        "written": written,
        "skipped": skipped,
        "tickers": len(frames),
    }


def _return_stats(values: list[float]) -> dict[str, Any]:
    if not values:
        return {"n": 0, "mean": None, "median": None, "win_pct": None}
    wins = sum(1 for value in values if value > 0)
    return {
        "n": len(values),
        "mean": round(statistics.fmean(values), 8),
        "median": round(float(statistics.median(values)), 8),
        "win_pct": round(100.0 * wins / len(values), 4),
    }


def summarize_rows(
    rows: list[dict[str, Any]],
    *,
    horizons: tuple[int, ...] = (5, 10, 20),
) -> dict[str, Any]:
    """Kept vs dropped vs all, per rule, for each forward horizon."""
    signals = [row for row in rows if row.get("record_type") == "signal"]
    outcomes = _latest_outcomes(rows)
    joined: list[tuple[dict[str, Any], dict[str, Any] | None]] = []
    for row in signals:
        joined.append((row, outcomes.get(_outcome_key(row))))
    rules: dict[str, Any] = {}
    for label, key in RULES:
        kept_n = sum(1 for row in signals if row.get(f"{key}_keep"))
        dropped_n = sum(1 for row in signals if row.get(f"{key}_evaluable") and not row.get(f"{key}_keep"))
        unevaluable_n = sum(1 for row in signals if not row.get(f"{key}_evaluable"))
        horizon_block: dict[str, Any] = {}
        for horizon in horizons:
            field = f"ret_{int(horizon)}d"
            grouped: dict[str, list[float]] = {"kept": [], "dropped": [], "all": []}
            for row, outcome in joined:
                if outcome is None:
                    continue
                value = _float(outcome.get(field))
                if value is None:
                    continue
                grouped["all"].append(value)
                if row.get(f"{key}_keep"):
                    grouped["kept"].append(value)
                elif row.get(f"{key}_evaluable"):
                    grouped["dropped"].append(value)
            horizon_block[str(int(horizon))] = {name: _return_stats(values) for name, values in grouped.items()}
        rules[label] = {
            "n_kept": kept_n,
            "n_dropped": dropped_n,
            "n_unevaluable": unevaluable_n,
            "horizons": horizon_block,
        }
    return {
        "n_signal_rows": len(signals),
        "n_outcome_rows": sum(1 for row in rows if row.get("record_type") == "outcome"),
        "n_regime_markers": sum(1 for row in rows if row.get("record_type") == "scan_marker"),
        "rules": rules,
    }


def _fmt_return(value: Any) -> str:
    number = _float(value)
    if number is None:
        return "n/a"
    return f"{number * 100:.2f}%"


def _fmt_win(value: Any) -> str:
    number = _float(value)
    if number is None:
        return "n/a"
    return f"{number:.1f}%"


def render_report(summary: dict[str, Any]) -> str:
    lines = [
        "Shadow setup filter (log only)",
        f"signal rows: {summary.get('n_signal_rows', 0)}",
        f"outcome rows: {summary.get('n_outcome_rows', 0)}",
        f"regime markers: {summary.get('n_regime_markers', 0)}",
        "Returns are close vs the stored entry price. Win means return > 0.",
        "atr_pct is ATR(14) as a percent of close. R4 keeps names at or above the configured floor.",
    ]
    rules = summary.get("rules") if isinstance(summary.get("rules"), dict) else {}
    for label, _key in RULES:
        block = rules.get(label) if isinstance(rules.get(label), dict) else {}
        lines.append(
            f"{label}: kept={block.get('n_kept', 0)} dropped={block.get('n_dropped', 0)} "
            f"unevaluable={block.get('n_unevaluable', 0)}"
        )
        horizons = block.get("horizons") if isinstance(block.get("horizons"), dict) else {}
        for horizon, groups in horizons.items():
            if not isinstance(groups, dict):
                continue
            for group in ("kept", "dropped", "all"):
                stats = groups.get(group) if isinstance(groups.get(group), dict) else {}
                lines.append(
                    f"  {horizon}d {group}: n={stats.get('n', 0)} "
                    f"mean={_fmt_return(stats.get('mean'))} "
                    f"median={_fmt_return(stats.get('median'))} "
                    f"win={_fmt_win(stats.get('win_pct'))}"
                )
    return "\n".join(lines)


def measure_local_overhead(*, names: int = 1500, signals_n: int = 40) -> dict[str, float]:
    """CPU time for the work a scan adds. Uses in-memory frames only."""
    import pandas as pd

    index = pd.bdate_range("2020-01-01", periods=260)
    frame = pd.DataFrame(
        {
            "open": 100.0,
            "high": 101.0,
            "low": 99.0,
            "close": 100.0,
            "volume": 1_000_000.0,
            "sma_50": 98.0,
            "atr_14": 2.5,
        },
        index=index,
    )
    started = datetime.now(timezone.utc)
    for offset in range(int(names)):
        sma50_probe(f"T{offset}", frame)
    probe_ms = (datetime.now(timezone.utc) - started).total_seconds() * 1000.0
    samples = {
        f"T{offset}": {
            "ticker": f"T{offset}",
            "sector_etf": f"XL{offset % 11}",
            "above_sma50": offset % 3 != 0,
        }
        for offset in range(int(names))
    }
    signals = [
        {
            "ticker": f"S{offset}",
            "price": 50.0 + offset,
            "signal_score": float(offset),
            "sector_etf": f"XL{offset % 11}",
            "sector_winning": True,
            "sma_50": 48.0,
            "stop_pct": 0.08,
            "score_components": {"pct_from_52w_high": 0.9},
        }
        for offset in range(int(signals_n))
    ]
    bars = {f"S{offset}": {"atr_pct": 2.5, "pct_below_52w_high": 0.1, "close": 50.0} for offset in range(signals_n)}
    started = datetime.now(timezone.utc)
    breadth = sector_breadth_map(samples)
    build_signal_rows(
        signals,
        scan_id="bench",
        scan_date="2024-06-03",
        logged_at="2024-06-03T20:00:00+00:00",
        thresholds=ShadowSetupThresholds(),
        breadth=breadth,
        bar_features_by_ticker=bars,
    )
    record_ms = (datetime.now(timezone.utc) - started).total_seconds() * 1000.0
    return {
        "names": float(names),
        "signals": float(signals_n),
        "sma50_probe_ms": round(probe_ms, 3),
        "record_ms": round(record_ms, 3),
    }
