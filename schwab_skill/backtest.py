"""
Backtest runner for Stage 2 + VCP strategy with live-parity leaning rules.

This module favors rule parity (shared Stage 2/VCP checks, breakout confirmation,
quality gates, and sector climate filter) and reports return/risk diagnostics.
"""

from __future__ import annotations

import logging
import math
import os
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterator

import pandas as pd
import requests

from backtest_guardrails import AdaptiveGuardrailPolicy, load_adaptive_guardrail_policy
from backtest_intelligence import (
    BacktestIntelligenceConfig,
    apply_event_risk_overlay,
    apply_exec_quality_overlay,
    apply_meta_policy_overlay,
    evaluate_event_risk_for_backtest,
)
from config import (
    get_adaptive_stop_base_pct,
    get_adaptive_stop_enabled,
    get_backtest_adaptive_guardrail_policy_path,
    get_backtest_adaptive_guardrails_enabled,
    get_backtest_entry_family,
    get_backtest_entry_fill,
    get_backtest_hold_days,
    get_backtest_min_hold_days_before_trail,
    get_backtest_min_hold_defer_soft_exits,
    get_backtest_portfolio_max_positions,
    get_backtest_portfolio_starting_equity,
    get_backtest_position_size_pct,
    get_backtest_price_cache_dir,
    get_backtest_price_quality_enabled,
    get_backtest_price_quality_min_reversals,
    get_backtest_price_quality_move_pct,
    get_backtest_risk_per_trade_pct,
    get_breakout_confirm_bars,
    get_breakout_confirm_enabled,
    get_confluence_gate_mode,
    get_confluence_require_count,
    get_forensic_altman_min,
    get_forensic_beneish_max,
    get_forensic_cache_hours,
    get_forensic_enabled,
    get_forensic_filter_mode,
    get_forensic_sloan_max,
    get_pead_enabled,
    get_pead_lookback_days,
    get_pead_score_boost,
    get_pead_score_boost_large,
    get_pead_score_penalty,
    get_pead_score_penalty_large,
    get_quality_gates_mode,
    get_scan_allow_bear_regime,
    get_scan_sector_gate_mode,
    get_scan_vcp_gate_mode,
)
from core.backtest_trade_tape import PROGRESS_MIN_INTERVAL_SEC, rank_closed_trades
from env_overrides import temporary_env
from schwab_auth import DualSchwabAuth
from sector_regime_analysis import build_spy_regime_series, lookup_regime_at, summarize_sector_regime
from signal_scanner import _apply_score_stack, _evaluate_confluence, _evaluate_quality_gates, _load_watchlist
from stage_analysis import (
    add_indicators,
    check_vcp_volume,
    compute_signal_components,
    early_stop_gate_blocks_stage_a,
    entry_timing_blocks_stage_a,
    evaluate_early_stop_gate,
    evaluate_entry_timing_shadow,
    evaluate_pts_52w_cap,
    expansion_volume_ratio,
    is_pullback_entry,
    is_stage_2,
    pts_52w_cap_blocks_stage_a,
    tag_entry_family,
)

SKILL_DIR = Path(__file__).resolve().parent
LOG = logging.getLogger(__name__)


def _runtime_skill_dir() -> Path:
    """Optional runtime override for hosted workers.

    Celery SaaS tasks materialize per-user token files into a temporary skill
    directory. Backtest internals that instantiate DualSchwabAuth directly
    need this override to read tenant-scoped token files instead of the
    repository root.
    """
    raw = (os.getenv("TB_RUNTIME_SKILL_DIR") or "").strip()
    if raw:
        return Path(raw)
    return SKILL_DIR


def _temporary_env(overrides: dict[str, str] | None) -> Iterator[None]:
    # Compatibility wrapper retained for existing imports/call sites.
    return temporary_env(overrides)


@contextmanager
def _override_env(key: str, value: str) -> Iterator[None]:
    previous = os.environ.get(key)
    os.environ[key] = value
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = previous


def replay_honors_account_focus(
    universe_preset: str | None,
    env_overrides: dict[str, str] | None = None,
) -> bool:
    """True only when this replay asked for the short account book.

    ``SIGNAL_UNIVERSE_MODE=focused`` in ``.env`` is the live-scan setting.
    A named index stays that index unless the preset is ``focused`` or this
    run's overrides set the mode to focused.
    """
    preset = str(universe_preset or "sp1500").strip().lower() or "sp1500"
    explicit = str((env_overrides or {}).get("SIGNAL_UNIVERSE_MODE") or "").strip().lower()
    return preset == "focused" or explicit == "focused"


def _entry_time_score(trade: dict[str, Any]) -> float | None:
    """Score known when the entry was chosen. Outcomes are not a score."""
    for key in ("_score_priority", "signal_score"):
        if key not in trade or trade.get(key) is None:
            continue
        try:
            val = float(trade.get(key))
        except (TypeError, ValueError):
            continue
        if val != val:
            continue
        return val
    return None


def portfolio_admission_key(trade: dict[str, Any]) -> tuple[Any, ...]:
    """Order same-day entries without looking at the exit.

    Higher entry-time score first, then ticker. Exit date and return are
    not in the key: when the book is full, sorting on them picks the trade
    by how it later turned out.
    """
    raw_entry = trade.get("entry_date")
    try:
        entry = pd.Timestamp(raw_entry)
        entry = pd.Timestamp.max if pd.isna(entry) else entry
    except Exception:
        entry = pd.Timestamp.max
    score = _entry_time_score(trade)
    score_ord = float("inf") if score is None else -float(score)
    ticker = str(trade.get("ticker") or "").upper()
    return (entry, score_ord, ticker)


def sessions_before_start(df: pd.DataFrame, start: str) -> int:
    """How many bars in ``df`` fall strictly before ``start``."""
    if df is None or df.empty:
        return 0
    try:
        start_ts = pd.Timestamp(start)
    except Exception:
        return 0
    if pd.isna(start_ts):
        return 0
    count = 0
    for ts in df.index:
        try:
            if pd.Timestamp(ts) < start_ts:
                count += 1
        except Exception:
            continue
    return count


def first_replay_session(df: pd.DataFrame, start: str, end: str) -> pd.Timestamp | None:
    """First bar that can be traded: index >= REPLAY_MIN_BAR_INDEX and inside the era.

    The index counts bars in the full frame, including the warmup fetched
    before ``start``. Without those bars the first eligible session is the
    200th session of the era (mid-October when the era starts in January).
    """
    if df is None or df.empty:
        return None
    try:
        start_ts = pd.Timestamp(start)
        end_ts = pd.Timestamp(end)
    except Exception:
        return None
    for idx, ts in enumerate(df.index):
        if idx < REPLAY_MIN_BAR_INDEX:
            continue
        try:
            stamp = pd.Timestamp(ts)
        except Exception:
            continue
        if start_ts <= stamp <= end_ts:
            return stamp
    return None


def resolve_entry_fill(
    df: pd.DataFrame,
    signal_idx: int,
    mode: str,
    *,
    era_end: pd.Timestamp | str | None = None,
) -> dict[str, Any] | None:
    """Price and bar for an entry after the signal at ``signal_idx``.

    ``next_open`` fills at the next session's open and returns None when
    that bar is missing or falls after ``era_end``. ``same_close`` fills at
    the signal bar's close. Costs are applied later to whatever price this
    returns.
    """
    if df is None or df.empty or signal_idx < 0 or signal_idx >= len(df):
        return None
    fill_mode = str(mode or "next_open").strip().lower()
    if fill_mode not in {"next_open", "same_close"}:
        fill_mode = "next_open"
    end_ts = None
    if era_end is not None:
        try:
            end_ts = pd.Timestamp(era_end)
        except Exception:
            end_ts = None

    if fill_mode == "same_close":
        try:
            px = float(df["close"].iloc[signal_idx])
        except (TypeError, ValueError, KeyError):
            return None
        if not math.isfinite(px) or px <= 0:
            return None
        ts = pd.Timestamp(df.index[signal_idx])
        if end_ts is not None and ts > end_ts:
            return None
        return {"fill_idx": int(signal_idx), "entry_price": px, "entry_ts": ts, "entry_fill": "same_close"}

    fill_idx = int(signal_idx) + 1
    if fill_idx >= len(df) or "open" not in df.columns:
        return None
    try:
        px = float(df["open"].iloc[fill_idx])
    except (TypeError, ValueError):
        return None
    if not math.isfinite(px) or px <= 0:
        return None
    ts = pd.Timestamp(df.index[fill_idx])
    if end_ts is not None and ts.normalize() > end_ts.normalize():
        return None
    return {"fill_idx": fill_idx, "entry_price": px, "entry_ts": ts, "entry_fill": "next_open"}


def _history_pad_days(strategy_ids: list[str] | None) -> int:
    """Calendar days of price history fetched before the requested start."""
    pad = HISTORY_WARMUP_DAYS
    selected = [str(s).strip().lower() for s in (strategy_ids or []) if str(s).strip()]
    if not selected:
        return pad
    try:
        from core.horizon_strategies import executable_stage_a_history_days, stage_a_history_days

        pad = max(pad, int(executable_stage_a_history_days()), int(stage_a_history_days(selected)))
    except Exception:
        return pad
    return pad


def _attach_liquid_shadow(trades: list[dict[str, Any]], diagnostics: dict[str, Any], skill_dir: Path) -> None:
    """Attach Liquid funding features to closed trades (shadow only).

    Point-in-time: funding features use only funding days strictly before
    each trade's entry date (see core.liquid_positioning.features). This
    never changes returns, ranking, exits, or sizing — it only labels
    trades so a later study can measure whether funding separates them.
    Uncovered tickers keep None features, never zero-filled.
    """
    diagnostics["liquid_evaluated"] = 0
    diagnostics["liquid_covered"] = 0
    diagnostics["liquid_uncovered"] = 0
    diagnostics["liquid_errors"] = 0
    try:
        from config import get_liquid_overlay_mode

        if get_liquid_overlay_mode(skill_dir) == "off":
            return
    except Exception:
        return
    try:
        from core.liquid_positioning.features import funding_features_asof
        from core.liquid_positioning.store import get_funding_daily

        by_ticker: dict[str, list[dict[str, Any]]] = {}
        for trade in trades:
            ticker = str(trade.get("ticker") or "").upper()
            if not ticker:
                continue
            diagnostics["liquid_evaluated"] += 1
            if ticker not in by_ticker:
                by_ticker[ticker] = get_funding_daily(ticker, days=3650, skill_dir=skill_dir)
            daily = by_ticker[ticker]
            try:
                entry = pd.Timestamp(trade.get("entry_date")).date()
            except Exception:
                # Unparseable entry date: leave the trade uncovered rather
                # than labelling it with funding history from after entry.
                entry = None
            feats = (
                funding_features_asof(daily, entry)
                if entry is not None
                else {
                    "liquid_funding_mean_30d": None,
                    "liquid_funding_last": None,
                    "liquid_funding_z90": None,
                    "liquid_funding_pct_rank": None,
                    "liquid_funding_days": 0,
                }
            )
            trade.update(feats)
            if feats.get("liquid_funding_days"):
                diagnostics["liquid_covered"] += 1
            else:
                diagnostics["liquid_uncovered"] += 1
    except Exception as exc:
        diagnostics["liquid_errors"] += 1
        LOG.warning("Liquid shadow attach failed: %s", exc)


def _load_adaptive_guardrail_policy(skill_dir: Path) -> AdaptiveGuardrailPolicy | None:
    if not get_backtest_adaptive_guardrails_enabled(skill_dir):
        return None
    policy_path = get_backtest_adaptive_guardrail_policy_path(skill_dir)
    return load_adaptive_guardrail_policy(skill_dir, policy_path)


HOLD_DAYS = 20
MIN_BARS = 260
# Sessions that must already exist before an era's first day. SMA200 is valid
# at 200; a 52-week high uses 252. 420 calendar days is about 290 sessions
# (420 * 252 / 365), which covers that with holiday slack.
INDICATOR_WARMUP_SESSIONS = 252
# First bar index the replay will trade. Index 200 means 200 earlier sessions
# exist, which is when the 200-day average is defined.
REPLAY_MIN_BAR_INDEX = 200
# Calendar days fetched before the requested start so the first era session
# already has INDICATOR_WARMUP_SESSIONS of history.
HISTORY_WARMUP_DAYS = 420
# Why stored eras began mid-October: Schwab periodType=month returns at most
# six months. A series that actually starts on the era's January 1 has no
# valid 200-session average until about the 200th session, mid-October. The
# 2020 crash and the 2022 bear sit before that date, so those books never
# tested them. Replay requests use periodType=year, period=20.
# A cache that ends on the last completed session still covers a request
# whose end date is today or the next holiday. Larger holes are fetched.
CACHE_EDGE_TOLERANCE_DAYS = 4
SURVIVORSHIP_NOTE = (
    "Tickers with no daily bars are listed in no_data_tickers. A missing name "
    "is usually delisted or merged, so a universe built from today's members "
    "does not include them and the replay is survivorship-biased."
)
HISTORY_FETCH_NOTE = (
    "Fetch daily bars from 420 calendar days before each era start "
    "(about 290 sessions; 252 are required before the first day for a full "
    "52-week window, 200 for the 200-day average). Schwab must be asked with "
    "periodType=year, period=20, frequencyType=daily, frequency=1, plus the "
    "start and end. periodType=month stops at six months and drops this warmup."
)
SECTOR_LOOKBACK_DAYS = 21
DEFAULT_SLIPPAGE_BPS_PER_SIDE = 15.0
DEFAULT_FEE_PER_SHARE = 0.005
DEFAULT_MIN_FEE_PER_ORDER = 1.0
DEFAULT_MAX_ADV_PARTICIPATION = 0.02


@dataclass
class BacktestContext:
    watchlist: list[str]
    price_data: dict[str, pd.DataFrame]
    sector_etf_by_ticker: dict[str, str | None]
    sector_perf: dict[str, pd.DataFrame]
    history_meta_by_ticker: dict[str, dict[str, Any]]
    excluded_tickers: list[dict[str, Any]]
    data_integrity: dict[str, Any]


@dataclass
class CandidateSignal:
    ticker: str
    idx: int
    signal: dict[str, Any]
    reasons: list[str]
    comps: dict[str, Any]
    sector_reason: str
    entry_price: float
    day_volume: float
    qty_hint: int
    stop_pct: float
    pm_mult: float
    meta_size_mult: float
    event_size_mult: float
    event_policy: dict[str, Any] | None
    meta_payload: dict[str, Any] | None
    adaptive_size_mult: float
    effective_slippage_bps: float
    exec_info: dict[str, Any]
    telemetry: dict[str, Any]


@dataclass
class ActivePosition:
    ticker: str
    entry_idx: int
    entry_date: pd.Timestamp
    entry_price: float
    qty: int
    day_volume: float
    signal: dict[str, Any]
    reasons: list[str]
    comps: dict[str, Any]
    sector_reason: str
    stop_pct: float
    pm_mult: float
    meta_size_mult: float
    event_size_mult: float
    event_policy: dict[str, Any] | None
    meta_payload: dict[str, Any] | None
    effective_slippage_bps: float
    exec_info: dict[str, Any]
    telemetry: dict[str, Any]
    highest_close: float
    exit_manager_exit_idx: int | None = None
    exit_manager_reason: str | None = None


def _fetch_history(symbol: str, start_date: str, end_date: str) -> pd.DataFrame:
    df, _meta = _fetch_history_with_meta(symbol, start_date, end_date)
    return df


def _is_ohlc_path_logging_enabled() -> bool:
    """Opt-in: include the daily OHLCV slice between entry and exit on each
    trade record. Roughly +2-3 KB per trade and only useful for the Phase 2
    replay engine (alternate exits, tighter stops, partial TPs, trailing
    stops). Default OFF so production runs and the existing chunk schema
    are unaffected."""
    return os.environ.get("BACKTEST_OHLC_PATH", "").lower() in ("1", "true", "yes", "on")


def _compute_mfe_mae(
    df: pd.DataFrame,
    entry_idx: int,
    exit_idx: int,
    entry_price: float,
    *,
    include_entry_bar: bool = False,
) -> tuple[float | None, float | None]:
    """Maximum Favorable / Adverse Excursion as fractional returns.

    Walks the *intraday* high and low between entry and exit (exclusive of
    entry day, inclusive of exit day) so the result reflects the worst /
    best print the trade ever saw, not just close-to-close moves. A next-open
    fill includes the entry bar: that session's range is after the open.
    Returns (None, None) when the window is empty or the entry price is invalid.
    """
    if entry_price <= 0 or exit_idx < entry_idx:
        return None, None
    last = min(exit_idx, len(df) - 1)
    start = entry_idx if include_entry_bar else entry_idx + 1
    if last < start:
        return 0.0, 0.0
    window = df.iloc[start : last + 1]
    if window.empty:
        return 0.0, 0.0
    try:
        high_series = window["high"] if "high" in window.columns else window["close"]
        low_series = window["low"] if "low" in window.columns else window["close"]
    except KeyError:
        return None, None
    try:
        max_high = float(high_series.max())
        min_low = float(low_series.min())
    except (TypeError, ValueError):
        return None, None
    return (max_high - entry_price) / entry_price, (min_low - entry_price) / entry_price


def _build_ohlc_path(df: pd.DataFrame, entry_idx: int, exit_idx: int) -> list[dict[str, Any]]:
    """Serialize the inclusive [entry, exit] OHLCV slice as a list of small
    dicts so the path round-trips through chunk JSON. ~80 bytes/day, capped
    by the trade's hold window."""
    if exit_idx < entry_idx:
        return []
    last = min(exit_idx, len(df) - 1)
    window = df.iloc[entry_idx : last + 1]
    if window.empty:
        return []
    out: list[dict[str, Any]] = []
    for ts, row in window.iterrows():
        try:
            out.append(
                {
                    "date": pd.Timestamp(ts).isoformat()[:10],
                    "open": float(row.get("open", 0.0) or 0.0),
                    "high": float(row.get("high", 0.0) or 0.0),
                    "low": float(row.get("low", 0.0) or 0.0),
                    "close": float(row.get("close", 0.0) or 0.0),
                    "volume": float(row.get("volume", 0.0) or 0.0),
                }
            )
        except (TypeError, ValueError):
            continue
    return out


def resolve_price_cache_dir(raw: str | None, skill_dir: Path | None = None) -> Path | None:
    """Absolute cache directory, or None when replay should not use one."""
    text = str(raw or "").strip()
    if not text:
        return None
    path = Path(text).expanduser()
    if path.is_absolute():
        return path
    root = (skill_dir or SKILL_DIR).parent
    return (root / path).resolve()


def price_cache_candidate_paths(cache_dir: Path, ticker: str) -> list[Path]:
    """Per-ticker files this repo already writes, plus a CSV twin.

    ``{TICKER}.parquet`` has columns ``date, open, high, low, close, volume``
    (the date may instead be the index). ``{TICKER}.csv`` is the same table.
    """
    symbol = str(ticker or "").strip().upper()
    safe = "".join(ch if ch.isalnum() else "_" for ch in symbol)
    names = [f"{safe}.parquet", f"{safe}.csv", f"{symbol}.parquet", f"{symbol}.csv"]
    seen: set[str] = set()
    out: list[Path] = []
    for name in names:
        if name in seen:
            continue
        seen.add(name)
        out.append(cache_dir / name)
    return out


def _normalize_ohlcv_frame(frame: pd.DataFrame) -> pd.DataFrame | None:
    if frame is None or frame.empty:
        return None
    work = frame.copy()
    work.columns = [str(col).strip().lower() for col in work.columns]
    if "date" in work.columns:
        work = work.set_index("date")
    elif "datetime" in work.columns:
        work = work.set_index("datetime")
    needed = ["open", "high", "low", "close", "volume"]
    if any(col not in work.columns for col in needed):
        return None
    out = work[needed].apply(pd.to_numeric, errors="coerce")
    try:
        idx = pd.to_datetime(out.index)
    except Exception:
        return None
    if getattr(idx, "tz", None) is not None:
        idx = idx.tz_localize(None)
    out.index = pd.DatetimeIndex(idx).normalize()
    out.index.name = "date"
    out = out.sort_index()
    out = out[~out.index.duplicated(keep="last")]
    return out


def read_price_cache_frame(cache_dir: Path, ticker: str) -> pd.DataFrame | None:
    """Load one ticker from the cache. Unreadable files are a miss."""
    for path in price_cache_candidate_paths(cache_dir, ticker):
        if not path.is_file():
            continue
        try:
            if path.suffix.lower() == ".parquet":
                raw = pd.read_parquet(path)
            elif path.suffix.lower() == ".csv":
                raw = pd.read_csv(path)
            else:
                continue
        except Exception as exc:
            LOG.warning("Price cache unreadable for %s (%s): %s", ticker, path, exc)
            continue
        normalized = _normalize_ohlcv_frame(raw)
        if normalized is not None and not normalized.empty:
            return normalized
    return None


def _history_gaps(index: pd.DatetimeIndex, start_date: str, end_date: str) -> list[tuple[str, str]]:
    """Requested dates the cache does not already cover, outside a short edge tolerance."""
    if index is None or len(index) == 0:
        return [(start_date, end_date)]
    start_ts = pd.Timestamp(start_date)
    end_ts = pd.Timestamp(end_date)
    slack = pd.Timedelta(days=CACHE_EDGE_TOLERANCE_DAYS)
    gaps: list[tuple[str, str]] = []
    first = pd.Timestamp(index.min())
    last = pd.Timestamp(index.max())
    if first > start_ts + slack:
        gap_end = (first - pd.Timedelta(days=1)).strftime("%Y-%m-%d")
        if gap_end >= start_date:
            gaps.append((start_date, gap_end))
    if last < end_ts - slack:
        gap_start = (last + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
        if gap_start <= end_date:
            gaps.append((gap_start, end_date))
    return gaps


def _slice_history(frame: pd.DataFrame, start_date: str, end_date: str) -> pd.DataFrame:
    start_ts = pd.Timestamp(start_date)
    end_ts = pd.Timestamp(end_date)
    return frame.loc[(frame.index >= start_ts) & (frame.index <= end_ts)].copy()


def _count_reversal_spikes(close: pd.Series, move_pct: float) -> int:
    """Count >move_pct closes that the next session undoes with another >move_pct move.

    The reversing bar is consumed so one spike-and-undo is one event. A move
    that sticks, and a single spike, are not repeated reversals.
    """
    prices = pd.to_numeric(close, errors="coerce")
    returns = prices.pct_change()
    count = 0
    i = 1
    last = len(returns) - 1
    while i < last:
        current = returns.iloc[i]
        nxt = returns.iloc[i + 1]
        try:
            current_f = float(current)
            next_f = float(nxt)
        except (TypeError, ValueError):
            i += 1
            continue
        if current_f != current_f or next_f != next_f:
            i += 1
            continue
        if abs(current_f) >= move_pct and abs(next_f) >= move_pct and current_f * next_f < 0:
            count += 1
            i += 2
            continue
        i += 1
    return count


def screen_price_quality(
    frame: pd.DataFrame,
    *,
    move_pct: float = 0.50,
    min_reversals: int = 2,
) -> dict[str, Any] | None:
    """Exclusion record for an unusable adjusted series, or None to keep it.

    One non-positive open/high/low/close is enough: Schwab special-dividend
    adjustments write those. A single >move_pct day is kept. The series is
    dropped only when that kind of day reverses the next session at least
    ``min_reversals`` times.
    """
    if frame is None or frame.empty:
        return None
    for col in ("open", "high", "low", "close"):
        if col not in frame.columns:
            continue
        values = pd.to_numeric(frame[col], errors="coerce")
        bad = values[values <= 0]
        if not bad.empty:
            first = pd.Timestamp(bad.index[0])
            return {
                "reason": "non_positive_price",
                "column": col,
                "bars": int(len(bad)),
                "first_date": first.date().isoformat(),
            }
    if "close" not in frame.columns:
        return None
    reversals = _count_reversal_spikes(frame["close"], float(move_pct))
    if reversals >= int(min_reversals):
        return {
            "reason": "reversal_spikes",
            "reversal_count": int(reversals),
            "move_pct": float(move_pct),
            "min_reversals": int(min_reversals),
        }
    return None


def _remote_history_meta(frame: pd.DataFrame, source: dict[str, Any]) -> dict[str, Any]:
    provider = str(source.get("provider") or "unknown")
    used_fallback = bool(source.get("used_fallback"))
    reason = str(source.get("fallback_reason") or "").strip()
    if not reason:
        if provider == "schwab" and not frame.empty:
            reason = "schwab_ok"
        elif provider == "schwab":
            reason = "schwab_empty"
        else:
            reason = "unknown"
    return {
        "provider": provider,
        "used_fallback": used_fallback,
        "reason": reason,
        "rows": int(source.get("rows") or len(frame)),
    }


def _fetch_remote_history(
    symbol: str,
    start_date: str,
    end_date: str,
    schwab_auth: DualSchwabAuth | None = None,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Schwab daily history first, Yahoo only when Schwab fails."""
    from market_data import get_daily_history_with_meta

    frame, source = get_daily_history_with_meta(
        symbol,
        auth=schwab_auth,
        skill_dir=_runtime_skill_dir(),
        start_date=start_date,
        end_date=end_date,
        extended_history=True,
    )
    return frame, _remote_history_meta(frame, source)


def _fetch_history_with_meta(
    symbol: str,
    start_date: str,
    end_date: str,
    schwab_auth: DualSchwabAuth | None = None,
    cache_dir: Path | None = None,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Cache first. Schwab (then Yahoo) only for a missing ticker or missing dates.

    Replay asks for the extended year/20 daily window so the pre-era warmup
    is actually returned. A Yahoo fill is a fallback, so the study will not
    treat that window as clean enough to search.
    """
    cached = read_price_cache_frame(cache_dir, symbol) if cache_dir is not None else None
    if cached is None:
        frame, meta = _fetch_remote_history(symbol, start_date, end_date, schwab_auth=schwab_auth)
        meta["cache_status"] = "miss" if cache_dir is not None else "off"
        return frame, meta

    gaps = _history_gaps(cached.index, start_date, end_date)
    if not gaps:
        sliced = _slice_history(cached, start_date, end_date)
        return sliced, {
            "provider": "cache",
            "used_fallback": False,
            "reason": "cache_hit",
            "rows": int(len(sliced)),
            "cache_status": "hit",
        }

    parts = [cached]
    gap_provider = "schwab"
    gap_filled = False
    for gap_start, gap_end in gaps:
        gap_frame, gap_meta = _fetch_remote_history(symbol, gap_start, gap_end, schwab_auth=schwab_auth)
        gap_provider = str(gap_meta.get("provider") or gap_provider)
        if gap_frame is not None and not gap_frame.empty:
            parts.append(gap_frame)
            gap_filled = True
    merged = pd.concat(parts)
    merged = merged.sort_index()
    merged = merged[~merged.index.duplicated(keep="first")]
    sliced = _slice_history(merged, start_date, end_date)
    provider = f"cache+{gap_provider}" if gap_filled else "cache"
    return sliced, {
        "provider": provider,
        "used_fallback": False,
        "reason": "cache_partial" if gap_filled else "cache_gap_unfilled",
        "rows": int(len(sliced)),
        "cache_status": "partial",
        "gap_provider": gap_provider,
        "gaps": [{"start": start, "end": end} for start, end in gaps],
    }


def _fetch_history_schwab(
    symbol: str,
    start_date: str,
    end_date: str,
    auth: DualSchwabAuth | None = None,
) -> pd.DataFrame:
    from market_data import schwab_daily_pricehistory_params

    url = "https://api.schwabapi.com/marketdata/v1/pricehistory"
    start_ms = int(datetime.fromisoformat(start_date).replace(tzinfo=timezone.utc).timestamp() * 1000)
    end_ms = int(datetime.fromisoformat(end_date).replace(tzinfo=timezone.utc).timestamp() * 1000)
    params = schwab_daily_pricehistory_params(symbol, start_ms, end_ms, extended=True)
    try:
        session_auth = auth or DualSchwabAuth(skill_dir=_runtime_skill_dir(), auto_refresh=False)
        token = session_auth.get_market_token()
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        resp = requests.get(url, params=params, headers=headers, timeout=30)
        if resp.status_code == 401 and session_auth.market_session.force_refresh():
            token = session_auth.get_market_token()
            headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
            resp = requests.get(url, params=params, headers=headers, timeout=30)
        resp.raise_for_status()
        payload = resp.json()
        candles = payload.get("candles") or []
        if not candles:
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume"]).rename_axis("date")
        df = pd.DataFrame(candles)
        if "datetime" not in df.columns:
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume"]).rename_axis("date")
        dt_series = pd.to_datetime(df["datetime"], unit="ms", utc=True)
        for c in ("open", "high", "low", "close", "volume"):
            if c not in df.columns:
                return pd.DataFrame(columns=["open", "high", "low", "close", "volume"]).rename_axis("date")
        out = df[["open", "high", "low", "close", "volume"]].copy().astype(float)
        out.index = pd.DatetimeIndex(dt_series).tz_localize(None).normalize()
        out.index.name = "date"
        return out.sort_index().drop_duplicates()
    except Exception as e:
        LOG.warning("Schwab-only history fetch failed for %s: %s", symbol, e)
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"]).rename_axis("date")


def _session_close_now(day_ts: Any) -> pd.Timestamp:
    """Treat the replay bar as a completed regular session (16:00 Eastern)."""
    ts = pd.Timestamp(day_ts)
    if ts.tzinfo is None:
        ts = ts.tz_localize("America/New_York")
    else:
        ts = ts.tz_convert("America/New_York")
    return ts.normalize() + pd.Timedelta(hours=16)


@dataclass
class EntryAdmit:
    admitted: bool
    family: str
    shadow_only: bool
    stage2_ok: bool
    pead_ok: bool
    horizon_ok: bool
    horizon_hits: list[dict[str, Any]]


def _evaluate_backtest_entry_admit(
    window: pd.DataFrame,
    *,
    skill_dir: Path,
    entry_family: str,
    strategy_ids: list[str] | None,
    day_ts: pd.Timestamp,
    diagnostics: dict[str, Any],
) -> EntryAdmit:
    """Match Scan studio Stage A arms: Stage 2, PEAD, and dual-admit sleeves."""
    selected = [str(s).strip().lower() for s in (strategy_ids or []) if str(s).strip()]
    if not selected:
        if entry_family == "pullback":
            ok = is_pullback_entry(window, skill_dir)
            if not ok:
                diagnostics["entry_family_fail"] = int(diagnostics["entry_family_fail"]) + 1
            return EntryAdmit(
                admitted=ok,
                family="pullback",
                shadow_only=True,
                stage2_ok=False,
                pead_ok=False,
                horizon_ok=ok,
                horizon_hits=[],
            )
        if entry_family == "pead_primary":
            try:
                px = float(window["close"].iloc[-1])
                avg_vol = float(window["avg_vol_50"].iloc[-1]) if "avg_vol_50" in window.columns else 0.0
            except Exception:
                px, avg_vol = 0.0, 0.0
            ok = px >= 5.0 and avg_vol >= 200_000.0
            if not ok:
                diagnostics["entry_family_fail"] = int(diagnostics["entry_family_fail"]) + 1
            return EntryAdmit(
                admitted=ok,
                family="pead_primary",
                shadow_only=True,
                stage2_ok=False,
                pead_ok=ok,
                horizon_ok=False,
                horizon_hits=[],
            )
        stage2_ok = is_stage_2(window, skill_dir)
        if not stage2_ok:
            diagnostics["stage2_fail"] = int(diagnostics["stage2_fail"]) + 1
        return EntryAdmit(
            admitted=stage2_ok,
            family="stage2",
            shadow_only=False,
            stage2_ok=stage2_ok,
            pead_ok=False,
            horizon_ok=False,
            horizon_hits=[],
        )

    from core.horizon_strategies import selected_horizon_plugin_hits
    from core.scan_catalog import scan_arm_flags

    arms = scan_arm_flags(selected)
    want_stage2 = bool(arms.get("want_stage2"))
    want_pead = bool(arms.get("want_pead"))
    want_horizon = [str(s) for s in (arms.get("want_horizon") or []) if str(s)]

    stage2_ok = False
    if want_stage2:
        stage2_ok = bool(is_stage_2(window, skill_dir))
        if not stage2_ok:
            diagnostics["stage2_fail"] = int(diagnostics["stage2_fail"]) + 1

    pead_ok = False
    if want_pead:
        try:
            px = float(window["close"].iloc[-1])
            avg_vol = float(window["avg_vol_50"].iloc[-1]) if "avg_vol_50" in window.columns else 0.0
        except Exception:
            px, avg_vol = 0.0, 0.0
        pead_ok = px >= 5.0 and avg_vol >= 200_000.0
        if not pead_ok:
            diagnostics["entry_family_fail"] = int(diagnostics["entry_family_fail"]) + 1

    horizon_hits: list[dict[str, Any]] = []
    horizon_ok = False
    if want_horizon:
        pullback_hit = "pullback" in want_horizon and is_pullback_entry(window, skill_dir)
        try:
            horizon_hits = selected_horizon_plugin_hits(
                window,
                want_horizon,
                pullback_triggered=pullback_hit,
                now=_session_close_now(day_ts),
            )
        except Exception:
            LOG.debug("Backtest horizon eval failed", exc_info=True)
            horizon_hits = []
        horizon_ok = bool(horizon_hits)
        if not horizon_ok:
            diagnostics["entry_family_fail"] = int(diagnostics["entry_family_fail"]) + 1
        else:
            diagnostics["horizon_admit"] = int(diagnostics.get("horizon_admit", 0) or 0) + 1

    family = tag_entry_family(stage2_ok=stage2_ok, pead_ok=pead_ok, horizon_ok=horizon_ok) or ""
    admitted = bool(family)
    if not admitted:
        return EntryAdmit(
            admitted=False,
            family=entry_family,
            shadow_only=True,
            stage2_ok=stage2_ok,
            pead_ok=pead_ok,
            horizon_ok=horizon_ok,
            horizon_hits=[],
        )
    shadow_only = family in {"pead_primary", "horizon"}
    return EntryAdmit(
        admitted=True,
        family=family,
        shadow_only=shadow_only,
        stage2_ok=stage2_ok,
        pead_ok=pead_ok,
        horizon_ok=horizon_ok,
        horizon_hits=list(horizon_hits),
    )


def _prepare_context(
    start_date: str,
    end_date: str,
    watchlist: list[str] | None = None,
    skill_dir: Path | None = None,
    universe_preset: str | None = None,
    history_pad_days: int = 0,
    honor_account_focus: bool = False,
) -> BacktestContext:
    from sector_strength import SECTOR_ETFS, get_ticker_sector_etf

    sd = skill_dir or SKILL_DIR
    # One auth object for the whole replay. Per-ticker sessions refresh the
    # token against each other and drop the run onto Yahoo.
    schwab_auth = DualSchwabAuth(skill_dir=sd, auto_refresh=False)
    preset_key = str(universe_preset or "sp1500").strip().lower() or "sp1500"
    if watchlist is not None:
        universe = watchlist
    elif honor_account_focus:
        universe = _load_watchlist(sd, universe_preset=preset_key)
    else:
        with _override_env("SIGNAL_UNIVERSE_MODE", "broad"):
            universe = _load_watchlist(sd, universe_preset=preset_key)
    cleaned = [str(t).strip().upper() for t in universe if str(t).strip()]
    universe = list(dict.fromkeys(cleaned))
    price_data: dict[str, pd.DataFrame] = {}
    history_meta_by_ticker: dict[str, dict[str, Any]] = {}
    sector_etf_by_ticker: dict[str, str | None] = {}
    excluded: list[dict[str, Any]] = []
    apply_sp1500_membership = watchlist is None and preset_key in {"sp1500", "watchlist"}
    if watchlist is not None:
        membership_mode = "explicit_watchlist_override"
    elif apply_sp1500_membership:
        membership_mode = "live_watchlist"
    else:
        membership_mode = "named_universe_live_constituents"
    data_integrity: dict[str, Any] = {
        "history_fetch_total": 0,
        "history_fetch_empty": 0,
        "history_fetch_too_short": 0,
        "history_provider_schwab": 0,
        "history_provider_yfinance": 0,
        "history_provider_unknown": 0,
        "history_fallback_used": 0,
        "history_reason_counts": {},
        "membership_filter_mode": membership_mode,
        "membership_as_of_date": None,
        "membership_file_present": False,
        "membership_input_universe": len(universe),
        "membership_filtered_out": 0,
        "universe_preset": preset_key if watchlist is None else "custom",
        "account_focus_honored": bool(honor_account_focus and watchlist is None),
        "history_warmup_short": 0,
        "history_warmup_calendar_days": int(HISTORY_WARMUP_DAYS),
        "history_warmup_sessions_required": int(INDICATOR_WARMUP_SESSIONS),
        "history_fetch_note": HISTORY_FETCH_NOTE,
        "history_provider_cache": 0,
        "history_cache_hit": 0,
        "history_cache_partial": 0,
        "history_cache_miss": 0,
        "price_quality_excluded_count": 0,
        "no_data_count": 0,
        "price_quality_excluded": [],
        "no_data_tickers": [],
        "survivorship_note": SURVIVORSHIP_NOTE,
    }
    cache_dir = resolve_price_cache_dir(get_backtest_price_cache_dir(sd), sd)
    data_integrity["price_cache_enabled"] = cache_dir is not None
    data_integrity["price_cache_dir"] = str(cache_dir) if cache_dir is not None else None
    data_integrity["price_cache_present"] = bool(cache_dir is not None and cache_dir.is_dir())
    quality_on = bool(get_backtest_price_quality_enabled(sd))
    quality_move = float(get_backtest_price_quality_move_pct(sd))
    quality_min_reversals = int(get_backtest_price_quality_min_reversals(sd))
    data_integrity["price_quality_enabled"] = quality_on
    data_integrity["price_quality_move_pct"] = quality_move
    data_integrity["price_quality_min_reversals"] = quality_min_reversals
    from market_data import SCHWAB_DAILY_EXTENDED_PERIOD_YEARS

    data_integrity["history_period_type"] = "year"
    data_integrity["history_period_years"] = int(SCHWAB_DAILY_EXTENDED_PERIOD_YEARS)
    pad = max(0, int(history_pad_days or 0))
    fetch_start = start_date
    if pad:
        try:
            fetch_start = (datetime.strptime(start_date, "%Y-%m-%d") - timedelta(days=pad)).strftime("%Y-%m-%d")
        except ValueError:
            fetch_start = start_date
        data_integrity["history_pad_days"] = pad
        data_integrity["history_fetch_start"] = fetch_start

    if apply_sp1500_membership:
        # Deterministic membership policy: freeze eligibility at backtest start
        # date so repeated runs over the same window produce a stable universe.
        membership_as_of: date | None = None
        try:
            membership_as_of = datetime.fromisoformat(start_date).date()
        except ValueError:
            LOG.warning("Backtest start_date is not ISO format (%s); skipping membership filter.", start_date)
        if membership_as_of is not None:
            try:
                from sp1500_membership import tickers_as_of

                members = tickers_as_of(membership_as_of)
            except Exception as e:
                LOG.warning("SP1500 membership filter unavailable; using live watchlist universe: %s", e)
                members = None

            data_integrity["membership_as_of_date"] = membership_as_of.isoformat()
            if members is None:
                data_integrity["membership_filter_mode"] = "membership_missing_live_fallback"
            else:
                before = len(universe)
                universe = [t for t in universe if t in members]
                filtered = max(0, before - len(universe))
                data_integrity["membership_file_present"] = True
                data_integrity["membership_filter_mode"] = "historical_membership_start_date"
                data_integrity["membership_filtered_out"] = filtered
                LOG.info(
                    "Backtest universe membership filter applied as_of=%s: %d -> %d symbols",
                    membership_as_of.isoformat(),
                    before,
                    len(universe),
                )

    def _record_fetch(ticker: str, df: pd.DataFrame, history_meta: dict[str, Any]) -> bool:
        """Count the fetch and return False when the ticker must not enter the book."""
        history_meta_by_ticker[ticker] = dict(history_meta or {})
        data_integrity["history_fetch_total"] = int(data_integrity.get("history_fetch_total", 0) or 0) + 1
        provider = str(history_meta.get("provider") or "unknown")
        cache_status = str(history_meta.get("cache_status") or "off")
        reason = str(history_meta.get("reason") or "unknown")
        reason_counts = dict(data_integrity.get("history_reason_counts") or {})
        reason_counts[reason] = int(reason_counts.get(reason, 0) or 0) + 1
        data_integrity["history_reason_counts"] = reason_counts
        if cache_status == "hit":
            data_integrity["history_cache_hit"] = int(data_integrity.get("history_cache_hit", 0) or 0) + 1
        elif cache_status == "partial":
            data_integrity["history_cache_partial"] = int(data_integrity.get("history_cache_partial", 0) or 0) + 1
        elif cache_status == "miss":
            data_integrity["history_cache_miss"] = int(data_integrity.get("history_cache_miss", 0) or 0) + 1
        if provider == "cache":
            data_integrity["history_provider_cache"] = int(data_integrity.get("history_provider_cache", 0) or 0) + 1
        elif provider == "schwab":
            data_integrity["history_provider_schwab"] = int(data_integrity.get("history_provider_schwab", 0) or 0) + 1
        elif provider.startswith("cache+"):
            data_integrity["history_provider_cache"] = int(data_integrity.get("history_provider_cache", 0) or 0) + 1
            gap_provider = provider.split("+", 1)[1]
            if gap_provider == "schwab":
                data_integrity["history_provider_schwab"] = (
                    int(data_integrity.get("history_provider_schwab", 0) or 0) + 1
                )
            elif gap_provider == "yfinance":
                data_integrity["history_provider_yfinance"] = (
                    int(data_integrity.get("history_provider_yfinance", 0) or 0) + 1
                )
        elif provider == "yfinance":
            data_integrity["history_provider_yfinance"] = (
                int(data_integrity.get("history_provider_yfinance", 0) or 0) + 1
            )
        else:
            data_integrity["history_provider_unknown"] = int(data_integrity.get("history_provider_unknown", 0) or 0) + 1
        if bool(history_meta.get("used_fallback")):
            data_integrity["history_fallback_used"] = int(data_integrity.get("history_fallback_used", 0) or 0) + 1
        if df.empty:
            data_integrity["history_fetch_empty"] = int(data_integrity.get("history_fetch_empty", 0) or 0) + 1
            data_integrity["no_data_count"] = int(data_integrity.get("no_data_count", 0) or 0) + 1
            note = {"ticker": ticker, "reason": "no_data"}
            data_integrity["no_data_tickers"].append(note)
            excluded.append({"ticker": ticker, "reason": "no_data", "bars": 0})
            return False
        if quality_on:
            quality = screen_price_quality(df, move_pct=quality_move, min_reversals=quality_min_reversals)
            if quality is not None:
                record = {"ticker": ticker, **quality}
                data_integrity["price_quality_excluded"].append(record)
                data_integrity["price_quality_excluded_count"] = (
                    int(data_integrity.get("price_quality_excluded_count", 0) or 0) + 1
                )
                excluded.append(record)
                return False
        if len(df) < MIN_BARS:
            data_integrity["history_fetch_too_short"] = int(data_integrity.get("history_fetch_too_short", 0) or 0) + 1
            excluded.append({"ticker": ticker, "reason": "insufficient_history", "bars": len(df)})
            return False
        return True

    for ticker in universe:
        df, history_meta = _fetch_history_with_meta(
            ticker, fetch_start, end_date, schwab_auth=schwab_auth, cache_dir=cache_dir
        )
        if not _record_fetch(ticker, df, history_meta):
            continue
        price_data[ticker] = add_indicators(df)
        if sessions_before_start(df, start_date) < INDICATOR_WARMUP_SESSIONS:
            data_integrity["history_warmup_short"] = int(data_integrity.get("history_warmup_short", 0) or 0) + 1
        try:
            sector_etf_by_ticker[ticker] = get_ticker_sector_etf(ticker, skill_dir=sd)
        except Exception:
            sector_etf_by_ticker[ticker] = None

    sector_perf: dict[str, pd.DataFrame] = {}
    for sym in sorted(set(SECTOR_ETFS + ["SPY"])):
        sdf, _sector_meta = _fetch_history_with_meta(
            sym, fetch_start, end_date, schwab_auth=schwab_auth, cache_dir=cache_dir
        )
        if sdf.empty or len(sdf) < MIN_BARS:
            continue
        if quality_on:
            sector_quality = screen_price_quality(sdf, move_pct=quality_move, min_reversals=quality_min_reversals)
            if sector_quality is not None:
                record = {"ticker": sym, "role": "sector", **sector_quality}
                data_integrity["price_quality_excluded"].append(record)
                data_integrity["price_quality_excluded_count"] = (
                    int(data_integrity.get("price_quality_excluded_count", 0) or 0) + 1
                )
                continue
        sector_perf[sym] = sdf

    return BacktestContext(
        watchlist=sorted(price_data.keys()),
        price_data=price_data,
        sector_etf_by_ticker=sector_etf_by_ticker,
        sector_perf=sector_perf,
        history_meta_by_ticker=history_meta_by_ticker,
        excluded_tickers=excluded,
        data_integrity=data_integrity,
    )


def _window_return(df: pd.DataFrame, idx: int, lookback: int) -> float | None:
    if idx < lookback or idx >= len(df):
        return None
    start_px = float(df["close"].iloc[idx - lookback])
    end_px = float(df["close"].iloc[idx])
    if start_px <= 0:
        return None
    return (end_px - start_px) / start_px


def _sector_filter_pass(ticker: str, entry_idx: int, context: BacktestContext) -> tuple[bool, str]:
    etf = context.sector_etf_by_ticker.get(ticker)
    if not etf:
        return False, "no_sector_etf"

    etf_df = context.sector_perf.get(etf)
    spy_df = context.sector_perf.get("SPY")
    if etf_df is None or spy_df is None:
        return True, "sector_data_unavailable_allow"

    ticker_date = context.price_data[ticker].index[entry_idx]
    try:
        etf_i = etf_df.index.get_indexer([ticker_date], method="pad")[0]
        spy_i = spy_df.index.get_indexer([ticker_date], method="pad")[0]
    except Exception:
        return True, "sector_index_fallback_allow"
    if etf_i < 0 or spy_i < 0:
        return True, "sector_date_missing_allow"

    etf_ret = _window_return(etf_df, etf_i, SECTOR_LOOKBACK_DAYS)
    spy_ret = _window_return(spy_df, spy_i, SECTOR_LOOKBACK_DAYS)
    if etf_ret is None or spy_ret is None:
        return True, "sector_short_window_allow"
    if etf_ret <= spy_ret:
        return False, "sector_not_winning"
    return True, "sector_winning"


def _run_mirofish_for_entry(
    ticker: str,
    seeded_df: pd.DataFrame,
    skill_dir: Path | None = None,
) -> dict[str, Any] | None:
    from core.mirofish_entry import run_mirofish_for_entry

    return run_mirofish_for_entry(ticker, seeded_df, skill_dir=skill_dir or SKILL_DIR)


def _safe_telemetry_float(value: Any, default: float = 0.0) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return float(default)
    return float(out)


def _safe_telemetry_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def _roc_auc_rank(y_true: pd.Series, y_score: pd.Series) -> float | None:
    y = pd.to_numeric(y_true, errors="coerce")
    s = pd.to_numeric(y_score, errors="coerce")
    mask = ~(y.isna() | s.isna())
    if int(mask.sum()) <= 1:
        return None
    yb = y[mask].astype(int)
    pos = yb == 1
    neg = yb == 0
    n_pos = int(pos.sum())
    n_neg = int(neg.sum())
    if n_pos == 0 or n_neg == 0:
        return None
    ranks = s[mask].rank(method="average")
    sum_pos = float(ranks[pos].sum())
    return float((sum_pos - (n_pos * (n_pos + 1) / 2.0)) / (n_pos * n_neg))


def _score_stack_metrics_from_trades(trades_df: pd.DataFrame) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if trades_df.empty:
        return out

    score_cols = ["signal_score", "composite_score", "rank_score", "edge_score", "execution_score", "reliability_score"]
    for col in score_cols:
        if col in trades_df.columns:
            auc = _roc_auc_rank(trades_df["net_return"] > 0, trades_df[col])
            ic = trades_df[[col, "net_return"]].corr(method="spearman").iloc[0, 1]
            out[col] = {
                "auc_upside": float(auc) if auc is not None else None,
                "spearman_ic_net_return": float(ic) if pd.notna(ic) else None,
            }

    if "p_up_calibrated" in trades_df.columns:
        p = pd.to_numeric(trades_df["p_up_calibrated"], errors="coerce").clip(lower=1e-6, upper=1 - 1e-6)
        y = (pd.to_numeric(trades_df["net_return"], errors="coerce") > 0).astype(float)
        mask = ~(p.isna() | y.isna())
        if int(mask.sum()) > 0:
            p2 = p[mask]
            y2 = y[mask]
            brier = float(((p2 - y2) ** 2).mean())
            logloss = float(-(y2 * p2.map(math.log) + (1.0 - y2) * (1.0 - p2).map(math.log)).mean())
            out["p_up_calibrated"] = {
                "brier": brier,
                "logloss": logloss,
                "auc_upside": _roc_auc_rank(y2, p2),
            }
    return out


def _provider_segment_metrics_from_trades(trades_df: pd.DataFrame) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if trades_df.empty:
        return out
    if "data_provider" in trades_df.columns:
        for provider, group in trades_df.groupby("data_provider"):
            net = pd.to_numeric(group["net_return"], errors="coerce")
            out[str(provider)] = {
                "n": int(len(group)),
                "win_rate_net": float((net > 0).mean() * 100.0) if len(group) else 0.0,
                "avg_net_return_pct": float(net.mean() * 100.0) if len(group) else 0.0,
            }
    if "used_fallback_data" in trades_df.columns:
        for label, group in trades_df.groupby("used_fallback_data"):
            net = pd.to_numeric(group["net_return"], errors="coerce")
            out[f"fallback_{bool(label)}"] = {
                "n": int(len(group)),
                "win_rate_net": float((net > 0).mean() * 100.0) if len(group) else 0.0,
                "avg_net_return_pct": float(net.mean() * 100.0) if len(group) else 0.0,
            }
    return out


def _build_telemetry_payload(signal: dict[str, Any], comps: dict[str, Any]) -> dict[str, Any]:
    advisory = signal.get("advisory") if isinstance(signal.get("advisory"), dict) else {}
    meta_policy = signal.get("meta_policy") if isinstance(signal.get("meta_policy"), dict) else {}
    prob_rank = signal.get("prob_rank") if isinstance(signal.get("prob_rank"), dict) else {}
    return {
        "mirofish_conviction": _safe_telemetry_float(signal.get("mirofish_conviction")),
        "advisory_prob": _safe_telemetry_float(advisory.get("p_up_10d")),
        "agent_uncertainty": _safe_telemetry_float(meta_policy.get("uncertainty_score")),
        "vcp_volume_ratio": _safe_telemetry_float(comps.get("avg_vcp_volume_ratio")),
        "sector_rs_rank": _safe_telemetry_int(
            signal.get("sector_rs_rank", signal.get("sector_relative_strength_rank"))
        ),
        "prob_rank_expected_return_40d": _safe_telemetry_float(prob_rank.get("expected_return_40d")),
        "prob_rank_confidence": _safe_telemetry_float(prob_rank.get("confidence")),
        "prob_rank_model_id": str(prob_rank.get("model_id") or signal.get("prob_rank_model_id") or ""),
    }


def _score_fields_for_trade(signal: dict[str, Any], skill_dir: Path | None = None) -> dict[str, Any]:
    comps = signal.get("score_components") if isinstance(signal.get("score_components"), dict) else {}
    price_raw = signal.get("price")
    sma_200_raw = signal.get("sma_200")
    try:
        price = float(price_raw) if price_raw is not None else None
    except (TypeError, ValueError):
        price = None
    try:
        sma_200 = float(sma_200_raw) if sma_200_raw is not None else None
    except (TypeError, ValueError):
        sma_200 = None
    trend_pct: float | None
    trend_raw = signal.get("close_vs_sma200_pct")
    if trend_raw is not None:
        try:
            trend_pct = float(trend_raw)
        except (TypeError, ValueError):
            trend_pct = None
    else:
        trend_pct = None
    if trend_pct is None and price is not None and sma_200 is not None and sma_200 > 0:
        trend_pct = (float(price) / float(sma_200)) - 1.0
    advisory = signal.get("advisory") if isinstance(signal.get("advisory"), dict) else {}
    out: dict[str, Any] = {
        "signal_score": signal.get("signal_score"),
        "edge_score": signal.get("edge_score"),
        "reliability_score": signal.get("reliability_score"),
        "execution_score": signal.get("execution_score"),
        "composite_score": signal.get("composite_score"),
        "rank_score": signal.get("rank_score"),
        "rank_score_v2": signal.get("rank_score_v2"),
        "expected_return_40d": signal.get("expected_return_40d"),
        "prob_rank_confidence": signal.get("prob_rank_confidence"),
        "prob_rank_model_id": signal.get("prob_rank_model_id"),
        "p_up_calibrated": signal.get("p_up_calibrated"),
        "advisory_confidence_bucket": advisory.get("confidence_bucket"),
        "advisory_feature_coverage": advisory.get("feature_coverage"),
        "data_provider": signal.get("data_provider"),
        "data_provider_primary": signal.get("data_provider_primary"),
        "used_fallback_data": signal.get("used_fallback_data"),
        "pts_52w": comps.get("pts_52w"),
        "pts_sma": comps.get("pts_sma"),
        "pts_volume": comps.get("pts_volume"),
        "pts_mirofish": comps.get("pts_mirofish"),
        "expansion_volume_ratio": signal.get("expansion_volume_ratio"),
        "close_vs_sma200_pct": trend_pct,
        "entry_sma_200": sma_200,
    }
    if price is not None:
        out["entry_price_ref"] = price
    grade = signal.get("debate_grade")
    if isinstance(grade, dict):
        from core.debate_committee import join_committee_sample
        from core.debate_grade import flatten_debate_grade

        join_committee_sample(signal, skill_dir)
        out.update(flatten_debate_grade(grade))
    return out


def _resolve_backtest_exit_settings(skill_dir: Path | None = None) -> tuple[int, int, bool]:
    sd = skill_dir or SKILL_DIR
    hold_days = int(get_backtest_hold_days(sd))
    min_hold_before_trail = int(get_backtest_min_hold_days_before_trail(sd))
    defer_soft_exits = bool(get_backtest_min_hold_defer_soft_exits(sd))
    return hold_days, min_hold_before_trail, defer_soft_exits


def _stop_triggered(
    *,
    px: float,
    entry_price: float,
    stop_pct: float,
    highest_close: float,
    bars_held: int,
    min_hold_before_trail: int,
) -> bool:
    if bars_held < min_hold_before_trail:
        return px <= entry_price * (1.0 - stop_pct)
    return px <= highest_close * (1.0 - stop_pct)


def _evaluate_position_exit(
    *,
    px: float,
    entry_price: float,
    entry_idx: int,
    idx: int,
    stop_pct: float,
    highest_close: float,
    hold_days: int,
    min_hold_before_trail: int,
    defer_soft_exits: bool,
    window: pd.DataFrame,
    skill_dir: Path,
    skip_vcp_invalidation: bool = False,
) -> str | None:
    bars_held = idx - entry_idx
    if _stop_triggered(
        px=px,
        entry_price=entry_price,
        stop_pct=stop_pct,
        highest_close=highest_close,
        bars_held=bars_held,
        min_hold_before_trail=min_hold_before_trail,
    ):
        return "trailing_stop"
    if bars_held >= hold_days:
        return "time_exit"

    soft_exits_allowed = not defer_soft_exits or bars_held >= min_hold_before_trail
    if not soft_exits_allowed:
        return None

    sma50 = float(window["sma_50"].iloc[-1]) if "sma_50" in window.columns else 0.0
    if sma50 > 0 and px < sma50:
        return "sma50_break"
    if skip_vcp_invalidation:
        return None
    try:
        if not check_vcp_volume(window, skill_dir, exclude_last_bars=0):
            return "vcp_invalidation"
    except Exception:
        pass
    return None


def _simulate_exit(
    df: pd.DataFrame,
    entry_idx: int,
    hold_days: int,
    stop_pct: float,
    *,
    min_hold_before_trail: int | None = None,
    skill_dir: Path | None = None,
    entry_price: float | None = None,
    evaluate_entry_bar: bool = False,
) -> tuple[float, pd.Timestamp, str]:
    sd = skill_dir or SKILL_DIR
    if min_hold_before_trail is None:
        min_hold_before_trail = int(get_backtest_min_hold_days_before_trail(sd))
    if entry_price is None:
        entry_price = float(df["close"].iloc[entry_idx])
    else:
        entry_price = float(entry_price)
    highest_close = entry_price
    last_idx = min(entry_idx + hold_days, len(df) - 1)
    first_j = entry_idx if evaluate_entry_bar else entry_idx + 1
    for j in range(first_j, last_idx + 1):
        px = float(df["close"].iloc[j])
        highest_close = max(highest_close, px)
        if _stop_triggered(
            px=px,
            entry_price=entry_price,
            stop_pct=stop_pct,
            highest_close=highest_close,
            bars_held=j - entry_idx,
            min_hold_before_trail=min_hold_before_trail,
        ):
            return px, df.index[j], "trailing_stop"
    return float(df["close"].iloc[last_idx]), df.index[last_idx], "time_exit"


def _plan_exit_manager_for_entry(
    df: pd.DataFrame,
    entry_idx: int,
    hold_days: int,
    stop_pct: float,
    skill_dir: Path,
    diagnostics: dict[str, Any],
    *,
    entry_price: float | None = None,
    evaluate_entry_bar: bool = False,
    mode: str | None = None,
) -> tuple[int | None, str | None]:
    """Run the exit-manager simulator once per entry.

    ``mode`` is the run's intelligence-overlay exit-manager setting. When the
    caller passes it, that value replaces the env lookup. When ``mode`` is
    omitted, ``EXIT_MANAGER_MODE`` still decides, which is what env-driven
    backtests and direct callers use.

    ``off`` does not call the simulator, so the counter stays 0. ``shadow``
    records a comparison and leaves the bar-loop exit unchanged. ``live``
    returns the managed exit index so the bar loop can take it when no
    earlier stop fired. Hold-day settings stay on the existing getters.
    """
    if mode is None:
        from config import get_exit_manager_mode

        mode = str(get_exit_manager_mode(skill_dir) or "off").strip().lower()
    else:
        mode = str(mode or "off").strip().lower()
        if mode not in {"off", "shadow", "live"}:
            mode = "off"
    if mode == "off":
        return None, None
    from backtest_intelligence import simulate_exit_with_manager

    try:
        _px, exit_ts, reason, info = simulate_exit_with_manager(
            df,
            int(entry_idx),
            int(hold_days),
            float(stop_pct),
            skill_dir=skill_dir,
            mode=mode,
            entry_price=entry_price,
            evaluate_entry_bar=evaluate_entry_bar,
        )
    except Exception:
        diagnostics["exit_manager_simulated_errors"] = int(diagnostics.get("exit_manager_simulated_errors", 0) or 0) + 1
        return None, None
    diagnostics["exit_manager_simulated"] = int(diagnostics.get("exit_manager_simulated", 0) or 0) + 1
    shadow = info.get("shadow") if isinstance(info, dict) else None
    if isinstance(shadow, dict) and shadow.get("legacy_reason") != shadow.get("managed_reason"):
        diagnostics["exit_manager_shadow_would_differ"] = (
            int(diagnostics.get("exit_manager_shadow_would_differ", 0) or 0) + 1
        )
    if mode != "live":
        return None, None
    exit_idx = None
    if exit_ts is not None:
        for i, ts in enumerate(df.index):
            if ts == exit_ts:
                exit_idx = i
                break
    if exit_idx is None:
        return None, None
    return int(exit_idx), str(reason or "exit_manager")


def _resolve_stop_pct_for_entry(df: pd.DataFrame, entry_idx: int, skill_dir: Path | None = None) -> float:
    from config import (
        get_adaptive_stop_atr_mult,
        get_adaptive_stop_base_pct,
        get_adaptive_stop_enabled,
        get_adaptive_stop_max_pct,
        get_adaptive_stop_min_pct,
        get_adaptive_stop_trend_lookback,
    )

    sd = skill_dir or SKILL_DIR
    base_pct = float(get_adaptive_stop_base_pct(sd))
    if not get_adaptive_stop_enabled(sd):
        return max(0.01, base_pct)
    min_pct = float(get_adaptive_stop_min_pct(sd))
    max_pct = float(get_adaptive_stop_max_pct(sd))
    atr_mult = float(get_adaptive_stop_atr_mult(sd))
    lookback = max(10, int(get_adaptive_stop_trend_lookback(sd)))

    try:
        price = float(df["close"].iloc[entry_idx])
        atr = float(df["atr_14"].iloc[entry_idx]) if "atr_14" in df.columns else 0.0
        if price <= 0 or atr <= 0:
            return max(min_pct, min(max_pct, base_pct))
        atr_pct = atr / price
        prev_i = max(0, entry_idx - lookback)
        prev_price = float(df["close"].iloc[prev_i])
        trend_lookback = ((price / prev_price) - 1.0) if prev_price > 0 else 0.0
        stop_pct = atr_pct * atr_mult
        if trend_lookback < -0.03:
            stop_pct *= 1.2
        elif trend_lookback > 0.06:
            stop_pct *= 0.9
        return max(min_pct, min(max_pct, stop_pct))
    except Exception:
        return max(min_pct, min(max_pct, base_pct))


def _estimate_order_qty(entry_price: float, day_volume: float, max_adv_participation: float) -> int:
    if entry_price <= 0:
        return 1
    max_by_liq = int(max(1.0, float(day_volume) * float(max_adv_participation)))
    # Keep notional bounded so fees/slippage percent remain realistic and comparable.
    target_notional = 10000.0
    qty_target = int(max(1.0, target_notional / entry_price))
    return max(1, min(max_by_liq, qty_target))


def _net_return_after_costs(
    entry_price: float,
    exit_price: float,
    qty: int,
    slippage_bps_per_side: float,
    fee_per_share: float,
    min_fee_per_order: float,
) -> tuple[float, dict[str, float]]:
    if entry_price <= 0 or qty <= 0:
        return 0.0, {"slippage_pct": 0.0, "fees_pct": 0.0, "gross_return": 0.0}
    gross = (exit_price - entry_price) / entry_price
    slippage_pct = 2.0 * (float(slippage_bps_per_side) / 10000.0)
    entry_fee = max(float(min_fee_per_order), float(fee_per_share) * qty)
    exit_fee = max(float(min_fee_per_order), float(fee_per_share) * qty)
    fees_pct = (entry_fee + exit_fee) / (entry_price * qty)
    net = gross - slippage_pct - fees_pct
    return float(net), {"slippage_pct": float(slippage_pct), "fees_pct": float(fees_pct), "gross_return": float(gross)}


def _serialize_equity_curves(
    equity_curve: list[tuple[str, float]],
    starting_equity: float,
    *,
    max_points: int = 800,
) -> dict[str, Any]:
    """Downsample and JSON-serialize portfolio equity + drawdown series."""
    pairs: list[tuple[str, float]] = list(equity_curve) if equity_curve else [("", float(starting_equity))]
    if len(pairs) > max_points:
        step = max(1, len(pairs) // max_points)
        sampled = [pairs[0], *pairs[step::step]]
        if sampled[-1] != pairs[-1]:
            sampled.append(pairs[-1])
        pairs = sampled
    values = [float(v) for _d, v in pairs]
    eq_series = pd.Series(values)
    peak = eq_series.cummax()
    dd = (eq_series / peak) - 1.0
    return {
        "equity_curve": [{"date": d or None, "equity": round(v, 2)} for d, v in pairs],
        "drawdown_curve": [
            {"date": d or None, "drawdown_pct": round(100.0 * float(dd_i), 3)} for (d, _), dd_i in zip(pairs, dd)
        ],
    }


def _simulate_portfolio_equity(
    trades: list[dict[str, Any]],
    *,
    starting_equity: float,
    max_concurrent_positions: int,
    position_size_pct: float,
    risk_per_trade_pct: float,
) -> dict[str, Any]:
    """Replay per-trade returns through a shared equity book.

    Each trade carries ``entry_date``, ``exit_date``, ``net_return`` (per-share
    %), and optionally ``stop_pct`` for risk-based sizing. Trades arriving while
    the book already holds ``max_concurrent_positions`` open positions are
    dropped and counted under ``capacity_filtered`` — they remain in the PF
    numerator/denominator (PF is signal quality, sizing-invariant) but do not
    contribute to the equity curve.

    Sizing per accepted trade:
      * If ``risk_per_trade_pct > 0`` AND ``stop_pct`` is finite & positive,
        allocate ``equity * risk_per_trade_pct / stop_pct`` (capped at 100%
        of equity for that one name).
      * Otherwise fall back to ``equity * position_size_pct``.

    Open notional is reserved. A new trade that would push gross exposure
    above current equity is left out (``capacity_filtered``). Stacking ten
    full-equity positions was implicit leverage: equal-weight percent profit
    factor could stay above 1 while the compounded book fell 67% to 97%.

    ``profit_factor_dollar`` is the dollar P&L of trades that actually
    entered. It is not the equal-weight percent profit factor.

    Returns a dict with ``equity_curve`` (list of ``(timestamp, equity)``),
    ``total_return_net_pct``, ``max_drawdown_net_pct``, ``capacity_filtered``,
    ``avg_concurrent``, ``peak_concurrent``, ``risk_sized_count``,
    ``fixed_sized_count``, ``profit_factor_dollar``.
    """
    if not trades:
        return {
            "equity_curve": [],
            "total_return_net_pct": 0.0,
            "max_drawdown_net_pct": 0.0,
            "capacity_filtered": 0,
            "avg_concurrent": 0.0,
            "peak_concurrent": 0,
            "risk_sized_count": 0,
            "fixed_sized_count": 0,
            "profit_factor_dollar": None,
            "gross_capped_at_equity": True,
            "starting_equity": float(starting_equity),
            "ending_equity": float(starting_equity),
            "admitted": [],
        }

    parsed: list[dict[str, Any]] = []
    for t in trades:
        try:
            ed = pd.Timestamp(t.get("entry_date") or t.get("exit_date"))
            xd = pd.Timestamp(t.get("exit_date") or t.get("entry_date"))
        except Exception:
            continue
        if pd.isna(ed) or pd.isna(xd):
            continue
        if xd < ed:
            ed, xd = xd, ed
        score = _entry_time_score(t)
        parsed.append(
            {
                "entry_date": ed,
                "exit_date": xd,
                "net_return": float(t.get("net_return", 0.0) or 0.0),
                "stop_pct": float(t.get("stop_pct", 0.0) or 0.0),
                "ticker": str(t.get("ticker") or "").upper(),
                "signal_score": score,
                "_score_priority": t.get("_score_priority"),
            }
        )
    if not parsed:
        return {
            "equity_curve": [],
            "total_return_net_pct": 0.0,
            "max_drawdown_net_pct": 0.0,
            "capacity_filtered": 0,
            "avg_concurrent": 0.0,
            "peak_concurrent": 0,
            "risk_sized_count": 0,
            "fixed_sized_count": 0,
            "profit_factor_dollar": None,
            "gross_capped_at_equity": True,
            "starting_equity": float(starting_equity),
            "ending_equity": float(starting_equity),
            "admitted": [],
        }

    # Exit date is not a tie-break. Same-day order is score, then ticker.
    parsed.sort(key=portfolio_admission_key)
    equity = float(starting_equity)
    peak = equity
    worst_dd = 0.0
    open_positions: list[dict[str, Any]] = []
    capacity_filtered = 0
    risk_sized = 0
    fixed_sized = 0
    concurrent_samples: list[int] = []
    peak_concurrent = 0
    open_notional = 0.0
    dollar_win = 0.0
    dollar_loss = 0.0
    admitted: list[dict[str, Any]] = []
    curve: list[tuple[str, float]] = [(parsed[0]["entry_date"].isoformat(), equity)]

    def _close_due(now: pd.Timestamp) -> None:
        nonlocal equity, peak, worst_dd, open_notional, dollar_win, dollar_loss
        still_open: list[dict[str, Any]] = []
        ready = [p for p in open_positions if p["exit_date"] <= now]
        ready.sort(key=lambda p: p["exit_date"])
        for p in ready:
            pnl = float(p["allocated"]) * float(p["net_return"])
            equity += pnl
            open_notional = max(0.0, open_notional - float(p["allocated"]))
            if pnl > 0:
                dollar_win += pnl
            elif pnl < 0:
                dollar_loss += abs(pnl)
            if equity > peak:
                peak = equity
            if peak > 0:
                dd = (equity / peak) - 1.0
                if dd < worst_dd:
                    worst_dd = dd
            curve.append((p["exit_date"].isoformat(), equity))
        for p in open_positions:
            if p["exit_date"] > now:
                still_open.append(p)
        open_positions[:] = still_open

    for t in parsed:
        _close_due(t["entry_date"])
        if equity <= 0 or len(open_positions) >= max_concurrent_positions:
            capacity_filtered += 1
            continue
        room = max(0.0, equity - open_notional)
        if room <= 0:
            capacity_filtered += 1
            continue
        if risk_per_trade_pct > 0 and t["stop_pct"] > 0:
            allocated = min(equity, equity * risk_per_trade_pct / t["stop_pct"])
            sized_by_risk = True
        else:
            allocated = equity * position_size_pct
            sized_by_risk = False
        allocated = min(float(allocated), room)
        if allocated <= 0:
            capacity_filtered += 1
            continue
        if sized_by_risk:
            risk_sized += 1
        else:
            fixed_sized += 1
        open_notional += allocated
        open_positions.append(
            {
                "exit_date": t["exit_date"],
                "net_return": t["net_return"],
                "allocated": allocated,
            }
        )
        score = t.get("signal_score")
        admitted.append(
            {
                "ticker": t.get("ticker") or "",
                "entry_date": pd.Timestamp(t["entry_date"]).date().isoformat(),
                "signal_score": None if score is None else float(score),
            }
        )
        concurrent_samples.append(len(open_positions))
        if len(open_positions) > peak_concurrent:
            peak_concurrent = len(open_positions)

    if open_positions:
        last = max(p["exit_date"] for p in open_positions)
        _close_due(last + pd.Timedelta(days=1))

    total_return = (equity / starting_equity) - 1.0 if starting_equity > 0 else 0.0
    avg_concurrent = sum(concurrent_samples) / len(concurrent_samples) if concurrent_samples else 0.0
    profit_factor_dollar = (dollar_win / dollar_loss) if dollar_loss > 0 else None
    return {
        "equity_curve": curve,
        "total_return_net_pct": round(100.0 * total_return, 4),
        "max_drawdown_net_pct": round(100.0 * worst_dd, 4),
        "capacity_filtered": int(capacity_filtered),
        "avg_concurrent": round(float(avg_concurrent), 3),
        "peak_concurrent": int(peak_concurrent),
        "risk_sized_count": int(risk_sized),
        "fixed_sized_count": int(fixed_sized),
        "profit_factor_dollar": (None if profit_factor_dollar is None else round(float(profit_factor_dollar), 4)),
        "gross_capped_at_equity": True,
        "starting_equity": float(starting_equity),
        "ending_equity": float(equity),
        "admitted": admitted,
    }


def _max_drawdown(returns: pd.Series) -> float:
    """Legacy single-asset drawdown helper retained ONLY for callers that
    explicitly opt out of the portfolio simulator. Treats the trade list as a
    sequential 100%-of-equity roll, which is **not** a real portfolio metric;
    do not surface its output as a deployable risk number."""
    if returns.empty:
        return 0.0
    equity = (1.0 + returns).cumprod()
    peak = equity.cummax()
    dd = (equity / peak) - 1.0
    return float(dd.min())


def _quality_mode_should_filter(reasons: list[str], skill_dir: Path | None = None) -> bool:
    """Same rule as the scanner, including the soft-mode volume count."""
    from signal_scanner import _quality_mode_should_filter as scanner_filter

    return scanner_filter(reasons, skill_dir or SKILL_DIR)


def run_backtest(
    tickers: list[str] | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    slippage_bps_per_side: float = DEFAULT_SLIPPAGE_BPS_PER_SIDE,
    fee_per_share: float = DEFAULT_FEE_PER_SHARE,
    min_fee_per_order: float = DEFAULT_MIN_FEE_PER_ORDER,
    max_adv_participation: float = DEFAULT_MAX_ADV_PARTICIPATION,
    skill_dir: Path | None = None,
    env_overrides: dict[str, str] | None = None,
    include_all_trades: bool = False,
    prediction_market_snapshot_path: str | None = None,
    intelligence_overlay: dict[str, str] | BacktestIntelligenceConfig | None = None,
    universe_preset: str | None = None,
    strategy_ids: list[str] | None = None,
    on_progress: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    honor_account_focus = replay_honors_account_focus(universe_preset, env_overrides)
    with _temporary_env(env_overrides):
        return _run_backtest_core(
            tickers=tickers,
            start_date=start_date,
            end_date=end_date,
            slippage_bps_per_side=slippage_bps_per_side,
            fee_per_share=fee_per_share,
            min_fee_per_order=min_fee_per_order,
            max_adv_participation=max_adv_participation,
            skill_dir=skill_dir,
            include_all_trades=include_all_trades,
            prediction_market_snapshot_path=prediction_market_snapshot_path,
            intelligence_overlay=intelligence_overlay,
            universe_preset=universe_preset,
            strategy_ids=strategy_ids,
            on_progress=on_progress,
            honor_account_focus=honor_account_focus,
        )


def _run_backtest_core(
    tickers: list[str] | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    slippage_bps_per_side: float = DEFAULT_SLIPPAGE_BPS_PER_SIDE,
    fee_per_share: float = DEFAULT_FEE_PER_SHARE,
    min_fee_per_order: float = DEFAULT_MIN_FEE_PER_ORDER,
    max_adv_participation: float = DEFAULT_MAX_ADV_PARTICIPATION,
    skill_dir: Path | None = None,
    include_all_trades: bool = False,
    prediction_market_snapshot_path: str | None = None,
    intelligence_overlay: dict[str, str] | BacktestIntelligenceConfig | None = None,
    universe_preset: str | None = None,
    strategy_ids: list[str] | None = None,
    on_progress: Callable[[dict[str, Any]], None] | None = None,
    honor_account_focus: bool = False,
) -> dict[str, Any]:
    sd = skill_dir or SKILL_DIR
    end = end_date or datetime.now().strftime("%Y-%m-%d")
    start = start_date or (datetime.now() - timedelta(days=3652)).strftime("%Y-%m-%d")

    if isinstance(intelligence_overlay, BacktestIntelligenceConfig):
        overlay_cfg = intelligence_overlay
    elif intelligence_overlay is not None:
        overlay_cfg = BacktestIntelligenceConfig.from_mapping(intelligence_overlay)
    else:
        overlay_cfg = BacktestIntelligenceConfig.from_env(sd)

    selected_ids = [str(s).strip().lower() for s in (strategy_ids or []) if str(s).strip()]
    history_pad = _history_pad_days(selected_ids)

    requested = [str(t).strip().upper() for t in (tickers or []) if str(t).strip()]
    try:
        from sec_enrichment import cached_filings_by_ticker

        filings_by_ticker = cached_filings_by_ticker(sd)
    except Exception:
        filings_by_ticker = {}
    context = _prepare_context(
        start,
        end,
        watchlist=requested if requested else None,
        skill_dir=sd,
        universe_preset=universe_preset,
        history_pad_days=history_pad,
        honor_account_focus=honor_account_focus,
    )

    all_trades: list[dict[str, Any]] = []
    _last_progress_at = 0.0
    _progress_failed = False

    def _publish_trade_progress(*, force: bool = False) -> None:
        nonlocal _last_progress_at, _progress_failed
        if on_progress is None or _progress_failed or not all_trades:
            return
        now = time.monotonic()
        if not force and (now - _last_progress_at) < PROGRESS_MIN_INTERVAL_SEC:
            return
        _last_progress_at = now
        try:
            on_progress({"progress": rank_closed_trades(all_trades)})
        except Exception:
            _progress_failed = True
            LOG.warning("Backtest progress callback failed; live tape stopped for this run")

    diagnostics: dict[str, int | float] = {
        "stage2_fail": 0,
        "vcp_fail": 0,
        "breakout_not_confirmed": 0,
        "sector_not_winning": 0,
        "no_sector_etf": 0,
        "sector_would_block": 0,
        "vcp_would_block": 0,
        "quality_gates_filtered": 0,
        "quality_gates_would_filter": 0,
        "forensic_filtered": 0,
        "regime_blocked": 0,
        "entries": 0,
        "debate_grade_evaluated": 0,
        "debate_grade_abstain": 0,
        "debate_grade_would_skip": 0,
        "debate_grade_live_coerced": 0,
        "capital_filtered": 0,
        "position_limit_filtered": 0,
        "liquidity_filtered": 0,
        "adaptive_guardrail_filtered": 0,
        "adaptive_guardrail_downsized": 0,
        "adaptive_guardrail_extra_slot_entries": 0,
        "risk_sized_entries": 0,
        "fixed_sized_entries": 0,
        "prediction_market_processed": 0,
        "prediction_market_applied": 0,
        "prediction_market_skipped": 0,
        "prediction_market_errors": 0,
        "meta_policy_processed": 0,
        "meta_policy_suppressed": 0,
        "meta_policy_downsized": 0,
        "event_risk_live_blocked": 0,
        "event_risk_live_downsized": 0,
        "event_risk_live_flagged": 0,
        "event_risk_shadow_flagged": 0,
        "event_risk_shadow_would_block": 0,
        "event_risk_shadow_would_downsize": 0,
        "exit_manager_partial_done": 0,
        "exit_manager_simulated": 0,
        "exit_manager_shadow_would_differ": 0,
        "exit_manager_simulated_errors": 0,
        "exits_exit_manager": 0,
        "confluence_confirmed": 0,
        "confluence_blocked": 0,
        "confluence_would_block": 0,
        "entry_timing_shadow_evaluated": 0,
        "entry_timing_shadow_would_filter": 0,
        "entry_timing_blocked": 0,
        "weekly_swing_entry_buffer_evaluated": 0,
        "weekly_swing_entry_buffer_would_cut": 0,
        "weekly_swing_entry_buffer_blocked": 0,
        "neutral_rank_scored": 0,
        "neutral_rank_would_drop": 0,
        "neutral_rank_cut_enforced": 0,
        "pts_52w_cap_evaluated": 0,
        "pts_52w_cap_would_filter": 0,
        "pts_52w_cap_blocked": 0,
        "early_stop_gate_evaluated": 0,
        "early_stop_gate_would_filter": 0,
        "early_stop_gate_blocked": 0,
        "entry_family_fail": 0,
        "pead_primary_fail": 0,
        "liquid_evaluated": 0,
        "liquid_covered": 0,
        "liquid_uncovered": 0,
        "liquid_errors": 0,
        "rank_filter_v2_evaluated": 0,
        "rank_filter_v2_would_drop": 0,
        "rank_filter_v2_dropped": 0,
        "exits_trailing_stop": 0,
        "exits_time_exit": 0,
        "exits_sma50_break": 0,
        "exits_vcp_invalidation": 0,
        "exits_final_liquidation": 0,
        "horizon_admit": 0,
        "entry_fill_skipped_no_next_bar": 0,
    }

    entry_family = get_backtest_entry_family(sd)
    entry_fill = get_backtest_entry_fill(sd)
    diagnostics["entry_family"] = entry_family
    diagnostics["entry_fill"] = entry_fill
    if selected_ids:
        diagnostics["strategy_ids"] = list(selected_ids)
        try:
            from core.scan_catalog import scan_arm_flags

            diagnostics["scan_arms"] = scan_arm_flags(selected_ids)
        except Exception:
            diagnostics["scan_arms"] = {}
    breakout_confirm_on = bool(get_breakout_confirm_enabled(sd))
    breakout_confirm_bars = int(get_breakout_confirm_bars(sd))
    breakout_enabled = breakout_confirm_on and entry_family == "stage2" and not selected_ids
    vcp_gate_mode = get_scan_vcp_gate_mode(sd)
    sector_gate_mode = get_scan_sector_gate_mode(sd)
    confluence_mode = get_confluence_gate_mode(sd)
    confluence_require_count = int(get_confluence_require_count(sd))
    if confluence_mode in ("shadow", "live"):
        from earnings_signal import _resolve_pead_provider

        if _resolve_pead_provider(sd) == "off":
            # Without a PEAD provider, pead_positive can never confirm; only
            # advisory_high remains. Surface that so thin/0-trade sweeps are
            # interpretable instead of silently testing "block everything".
            diagnostics["confluence_pead_unavailable"] = True
            LOG.warning(
                "Confluence gate active but PEAD provider is off: PEAD "
                "confirmation unavailable; only advisory_high can confirm."
            )
    quality_mode = get_quality_gates_mode(sd)
    adaptive_stop_enabled = get_adaptive_stop_enabled(sd)
    stop_pct_base = float(get_adaptive_stop_base_pct(sd))
    forensic_enabled = get_forensic_enabled(sd)
    forensic_mode = get_forensic_filter_mode(sd)
    forensic_cache_hours = float(get_forensic_cache_hours(sd))
    forensic_sloan_max = float(get_forensic_sloan_max(sd))
    forensic_beneish_max = float(get_forensic_beneish_max(sd))
    forensic_altman_min = float(get_forensic_altman_min(sd))
    pead_enabled = get_pead_enabled(sd)
    pead_lookback_days = int(get_pead_lookback_days(sd))
    pead_score_boost = float(get_pead_score_boost(sd))
    pead_score_boost_large = float(get_pead_score_boost_large(sd))
    pead_score_penalty = float(get_pead_score_penalty(sd))
    pead_score_penalty_large = float(get_pead_score_penalty_large(sd))
    adaptive_guardrail_policy = _load_adaptive_guardrail_policy(sd)
    backtest_hold_days, min_hold_before_trail, defer_soft_exits = _resolve_backtest_exit_settings(sd)

    starting_equity = float(get_backtest_portfolio_starting_equity(sd))
    max_concurrent_positions = max(1, int(get_backtest_portfolio_max_positions(sd)))
    position_size_pct = max(0.001, float(get_backtest_position_size_pct(sd)))
    risk_per_trade_pct = max(0.0, float(get_backtest_risk_per_trade_pct(sd)))
    max_position_size_pct = 0.10
    current_cash = float(starting_equity)
    active_positions: dict[str, ActivePosition] = {}
    equity_curve: list[tuple[str, float]] = []
    concurrent_samples: list[int] = []
    peak_concurrent = 0

    prediction_market_engine = None
    prediction_market_mode = "off"
    prediction_market_provider = "off"
    try:
        from prediction_market import (
            PredictionMarketOverlayEngine,
            apply_overlay_to_signal,
            build_prediction_market_config,
            build_provider,
            load_historical_provider,
        )

        pm_cfg = build_prediction_market_config(skill_dir=sd)
        prediction_market_mode = str(pm_cfg.mode)
        prediction_market_provider = str(pm_cfg.provider)
        if pm_cfg.enabled and pm_cfg.mode == "live":
            if prediction_market_snapshot_path:
                prediction_market_provider = "historical_file"
                pm_provider = load_historical_provider(prediction_market_snapshot_path)
                prediction_market_engine = PredictionMarketOverlayEngine(config=pm_cfg, provider=pm_provider)
            elif os.getenv("BACKTEST_ALLOW_NON_HISTORICAL_PM", "").strip().lower() in {"1", "true", "yes", "on"}:
                pm_provider = build_provider(pm_cfg)
                prediction_market_engine = PredictionMarketOverlayEngine(config=pm_cfg, provider=pm_provider)
            else:
                prediction_market_mode = "off"
                LOG.warning(
                    "Prediction-market backtest disabled: require historical snapshot file for strict PIT evaluation."
                )
    except Exception as e:
        diagnostics["prediction_market_errors"] = int(diagnostics["prediction_market_errors"]) + 1
        LOG.warning("Prediction-market backtest setup skipped: %s", e)

    spy_df = context.sector_perf.get("SPY")
    allow_bear_regime = bool(get_scan_allow_bear_regime(sd))
    if selected_ids:
        try:
            from core.scan_catalog import selected_strategies_bypass_bull_regime

            if selected_strategies_bypass_bull_regime(selected_ids):
                allow_bear_regime = True
                diagnostics["regime_bypass_counter_trend"] = True
        except Exception:
            pass
    # Always build regime series for trade tagging; only use above-200 as a hard
    # entry block when SCAN_ALLOW_BEAR_REGIME is false.
    spy_above_200, spy_regime_bucket = build_spy_regime_series(spy_df)
    spy_regime = spy_above_200 if (spy_above_200 is not None and not allow_bear_regime) else None

    forensic_cache: dict[str, dict[str, Any] | None] = {}
    ticker_date_index: dict[str, dict[pd.Timestamp, int]] = {}
    timeline_dates: set[pd.Timestamp] = set()
    for ticker in context.watchlist:
        df = context.price_data.get(ticker)
        if df is None or df.empty:
            continue
        idx_map: dict[pd.Timestamp, int] = {}
        for idx, ts in enumerate(df.index):
            t = pd.Timestamp(ts)
            idx_map[t] = idx
            if idx >= REPLAY_MIN_BAR_INDEX:
                timeline_dates.add(t)
        ticker_date_index[ticker] = idx_map

    timeline = sorted(timeline_dates)
    if history_pad:
        try:
            start_ts = pd.Timestamp(start)
            end_ts = pd.Timestamp(end)
            timeline = [t for t in timeline if start_ts <= pd.Timestamp(t) <= end_ts]
        except Exception:
            pass
    if not timeline:
        return {
            "start_date": start,
            "end_date": end,
            "total_trades": 0,
            "win_rate": 0.0,
            "total_return_pct": 0.0,
            "total_return_net_pct": 0.0,
            "cagr_pct": 0.0,
            "cagr_net_pct": 0.0,
            "avg_return_pct": 0.0,
            "avg_return_net_pct": 0.0,
            "avg_gain_pct": 0.0,
            "avg_loss_pct": 0.0,
            "profit_factor": 0.0,
            "profit_factor_net": 0.0,
            "max_drawdown_pct": 0.0,
            "max_drawdown_net_pct": 0.0,
            "portfolio_enabled": True,
            "portfolio_summary": {
                "capacity_filtered": 0,
                "avg_concurrent": 0.0,
                "peak_concurrent": 0,
                "risk_sized_count": 0,
                "fixed_sized_count": 0,
                "starting_equity": float(starting_equity),
                "ending_equity": float(starting_equity),
            },
            "diagnostics": diagnostics,
            "quality_gates_mode": quality_mode,
            "prediction_market_mode": prediction_market_mode,
            "prediction_market_provider": prediction_market_provider,
            "intelligence_overlay": overlay_cfg.as_dict(),
            "excluded_tickers": context.excluded_tickers[:50],
            "excluded_count": len(context.excluded_tickers),
            "universe_size": len(context.watchlist),
            "data_integrity": context.data_integrity,
            "entry_fill": entry_fill,
            "history_warmup_calendar_days": HISTORY_WARMUP_DAYS,
            "history_warmup_sessions_required": INDICATOR_WARMUP_SESSIONS,
            "history_fetch_note": HISTORY_FETCH_NOTE,
            "findings": "No valid timeline generated over the requested window.",
            "trades_sample": [],
            "trade_ranks": rank_closed_trades(all_trades),
        }

    def _price_at_or_before(ticker: str, ts: pd.Timestamp) -> float | None:
        df = context.price_data.get(ticker)
        if df is None or df.empty:
            return None
        try:
            i = int(df.index.get_indexer([ts], method="pad")[0])
        except Exception:
            return None
        if i < 0:
            return None
        return float(df["close"].iloc[i])

    def _equity_at(ts: pd.Timestamp) -> float:
        mark = float(current_cash)
        for pos in active_positions.values():
            px = _price_at_or_before(pos.ticker, ts)
            if px is not None and px > 0:
                mark += float(pos.qty) * float(px)
            else:
                mark += float(pos.qty) * float(pos.entry_price)
        return mark

    def _candidate_rank_key(candidate: CandidateSignal) -> tuple[float, float, float, float]:
        from config import get_neutral_rank_mode, get_scan_live_sort_key

        sort_key = get_scan_live_sort_key(sd)
        primary = _safe_telemetry_float(candidate.signal.get(sort_key))
        if get_neutral_rank_mode(sd) == "live":
            block = candidate.signal.get("neutral_rank")
            if isinstance(block, dict) and block.get("neutral_score") is not None:
                primary = _safe_telemetry_float(block.get("neutral_score"))
                block["applied_to_sort"] = True
        rank_v2 = _safe_telemetry_float(candidate.signal.get("rank_score_v2"))
        composite_score = _safe_telemetry_float(candidate.signal.get("composite_score"))
        rank_score = _safe_telemetry_float(candidate.signal.get("rank_score"))
        return (
            primary,
            rank_v2 if sort_key != "rank_score_v2" else composite_score,
            composite_score if sort_key != "composite_score" else rank_score,
            _safe_telemetry_float(candidate.signal.get("signal_score")),
        )

    def _apply_candidate_rank_v2_filter(candidates: list[CandidateSignal]) -> list[CandidateSignal]:
        from config import get_rank_filter_shadow_min_percentile_rank_v2, get_rank_filter_v2_mode
        from core.scoring_rank_v2 import score_percentile_threshold

        mode = get_rank_filter_v2_mode(sd)
        diagnostics["rank_filter_v2_mode"] = mode
        if mode == "off" or not candidates:
            return candidates
        scored = [
            (candidate, _safe_telemetry_float(candidate.signal.get("rank_score_v2")))
            for candidate in candidates
            if candidate.signal.get("rank_score_v2") is not None
        ]
        diagnostics["rank_filter_v2_evaluated"] = int(diagnostics["rank_filter_v2_evaluated"]) + len(scored)
        if len(scored) < 3:
            return candidates
        min_percentile = get_rank_filter_shadow_min_percentile_rank_v2(sd)
        threshold = score_percentile_threshold([score for _, score in scored], min_percentile)
        diagnostics["rank_filter_v2_threshold"] = round(threshold, 4)
        would_drop_ids = {id(candidate) for candidate, score in scored if score < threshold}
        diagnostics["rank_filter_v2_would_drop"] = int(diagnostics["rank_filter_v2_would_drop"]) + len(would_drop_ids)
        if mode != "live":
            return candidates
        kept = [candidate for candidate in candidates if id(candidate) not in would_drop_ids]
        diagnostics["rank_filter_v2_dropped"] = int(diagnostics["rank_filter_v2_dropped"]) + (
            len(candidates) - len(kept)
        )
        return kept

    def _apply_candidate_prob_rank(candidates: list[CandidateSignal]) -> list[CandidateSignal]:
        from config import get_prob_rank_mode, get_prob_rank_top_n

        mode = get_prob_rank_mode(sd)
        diagnostics["prob_rank_mode"] = mode
        if mode == "off" or not candidates:
            return candidates
        scored = [c for c in candidates if c.signal.get("expected_return_40d") is not None]
        diagnostics["prob_rank_scored"] = int(diagnostics.get("prob_rank_scored", 0) or 0) + len(scored)
        if not scored:
            return candidates
        ordered = sorted(
            scored,
            key=lambda c: _safe_telemetry_float(c.signal.get("expected_return_40d")),
            reverse=True,
        )
        top_n = max(1, int(get_prob_rank_top_n(sd)))
        diagnostics["prob_rank_top_n"] = top_n
        diagnostics["prob_rank_would_keep"] = int(diagnostics.get("prob_rank_would_keep", 0) or 0) + min(
            top_n, len(ordered)
        )
        diagnostics["prob_rank_would_drop"] = int(diagnostics.get("prob_rank_would_drop", 0) or 0) + max(
            0, len(ordered) - top_n
        )
        for i, cand in enumerate(ordered):
            block = cand.signal.get("prob_rank") if isinstance(cand.signal.get("prob_rank"), dict) else {}
            block = dict(block)
            block["cross_section_rank"] = i + 1
            block["cross_section_n"] = len(ordered)
            block["mode"] = mode
            cand.signal["prob_rank"] = block
        if mode != "live":
            return candidates
        kept_ids = {id(c) for c in ordered[:top_n]}
        kept = [c for c in candidates if id(c) in kept_ids]
        diagnostics["prob_rank_dropped"] = int(diagnostics.get("prob_rank_dropped", 0) or 0) + (
            len(candidates) - len(kept)
        )
        return kept

    for day_ts in timeline:
        # Exit pass first: capital from exits is available for same-day entries.
        for ticker, pos in list(active_positions.items()):
            df = context.price_data.get(ticker)
            if df is None or df.empty:
                continue
            idx = ticker_date_index.get(ticker, {}).get(day_ts)
            # same_close enters on the bar's close, so that bar is not an exit.
            # next_open enters on the bar's open, so that bar's close can exit.
            if idx is None or idx < pos.entry_idx:
                continue
            if idx == pos.entry_idx and entry_fill != "next_open":
                continue
            px = float(df["close"].iloc[idx])
            pos.highest_close = max(float(pos.highest_close), px)

            exit_reason = _evaluate_position_exit(
                px=px,
                entry_price=float(pos.entry_price),
                entry_idx=int(pos.entry_idx),
                idx=int(idx),
                stop_pct=float(pos.stop_pct),
                highest_close=float(pos.highest_close),
                hold_days=backtest_hold_days,
                min_hold_before_trail=min_hold_before_trail,
                defer_soft_exits=defer_soft_exits,
                window=df.iloc[: idx + 1],
                skill_dir=sd,
            )
            if exit_reason == "trailing_stop":
                diagnostics["exits_trailing_stop"] = int(diagnostics["exits_trailing_stop"]) + 1
            elif exit_reason == "time_exit":
                diagnostics["exits_time_exit"] = int(diagnostics["exits_time_exit"]) + 1
            elif exit_reason == "sma50_break":
                diagnostics["exits_sma50_break"] = int(diagnostics["exits_sma50_break"]) + 1
            elif exit_reason == "vcp_invalidation":
                diagnostics["exits_vcp_invalidation"] = int(diagnostics["exits_vcp_invalidation"]) + 1
            if not exit_reason and pos.exit_manager_exit_idx is not None and int(idx) >= int(pos.exit_manager_exit_idx):
                exit_reason = pos.exit_manager_reason or "exit_manager"
                diagnostics["exits_exit_manager"] = int(diagnostics.get("exits_exit_manager", 0) or 0) + 1
            if not exit_reason:
                continue

            net_ret, cost_ctx = _net_return_after_costs(
                entry_price=float(pos.entry_price),
                exit_price=float(px),
                qty=int(pos.qty),
                slippage_bps_per_side=float(pos.effective_slippage_bps),
                fee_per_share=fee_per_share,
                min_fee_per_order=min_fee_per_order,
            )
            gross_ret = (float(px) - float(pos.entry_price)) / float(pos.entry_price) if pos.entry_price > 0 else 0.0
            current_cash += float(pos.entry_price) * float(pos.qty) * (1.0 + float(net_ret))
            mfe, mae = _compute_mfe_mae(
                df,
                pos.entry_idx,
                idx,
                float(pos.entry_price),
                include_entry_bar=entry_fill == "next_open",
            )
            ohlc_path = _build_ohlc_path(df, pos.entry_idx, idx) if _is_ohlc_path_logging_enabled() else []

            regime_tags = lookup_regime_at(pos.entry_date, spy_above_200, spy_regime_bucket)
            all_trades.append(
                {
                    "ticker": pos.ticker,
                    "entry_date": pd.Timestamp(pos.entry_date).isoformat(),
                    "exit_date": pd.Timestamp(day_ts).isoformat(),
                    "entry_fill": entry_fill,
                    "signal_date": pos.signal.get("entry_date"),
                    "entry_price": round(float(pos.entry_price), 4),
                    "exit_price": round(float(px), 4),
                    "return": float(gross_ret),
                    "net_return": float(net_ret),
                    "mfe": mfe,
                    "mae": mae,
                    "ohlc_path": ohlc_path,
                    "exit_reason": exit_reason,
                    **_score_fields_for_trade(pos.signal, sd),
                    "mirofish_conviction": pos.signal.get("mirofish_conviction"),
                    "data_provider": pos.signal.get("data_provider"),
                    "data_provider_primary": pos.signal.get("data_provider_primary"),
                    "used_fallback_data": pos.signal.get("used_fallback_data"),
                    "fallback_reason": pos.signal.get("fallback_reason"),
                    "sector_etf": context.sector_etf_by_ticker.get(pos.ticker),
                    "sector_filter": pos.sector_reason,
                    "regime_above_200": regime_tags.get("regime_above_200"),
                    "regime_bucket": regime_tags.get("regime_bucket"),
                    "quality_reasons": pos.reasons,
                    "forensic_sloan": pos.signal.get("forensic_sloan"),
                    "forensic_beneish": pos.signal.get("forensic_beneish"),
                    "forensic_altman": pos.signal.get("forensic_altman"),
                    "forensic_flags": pos.signal.get("forensic_flags"),
                    "pead_beat": pos.signal.get("pead_beat"),
                    "pead_surprise_pct": pos.signal.get("pead_surprise_pct"),
                    "qty_estimate": int(pos.qty),
                    "day_volume": float(pos.day_volume),
                    "slippage_pct": float(cost_ctx["slippage_pct"]),
                    "fees_pct": float(cost_ctx["fees_pct"]),
                    "stop_pct": float(pos.stop_pct),
                    "prediction_market_status": ((pos.signal.get("prediction_market") or {}).get("status")),
                    "prediction_market_reason": ((pos.signal.get("prediction_market") or {}).get("reason")),
                    "prediction_market_size_multiplier": float(pos.pm_mult),
                    "meta_policy_decision": (pos.meta_payload or {}).get("decision") if pos.meta_payload else None,
                    "meta_policy_size_multiplier": float(pos.meta_size_mult),
                    "event_risk_action": (pos.event_policy or {}).get("action") if pos.event_policy else None,
                    "event_risk_size_multiplier": float(pos.event_size_mult),
                    "exit_manager_partial_done": False,
                    "exec_quality_regime": pos.exec_info.get("regime"),
                    "exec_quality_effective_slippage_bps": pos.exec_info.get("effective_slippage_bps"),
                    "telemetry": pos.telemetry,
                }
            )
            _publish_trade_progress()
            active_positions.pop(ticker, None)

        # Entry pass: evaluate all tickers on the same date with point-in-time windows.
        candidates: list[CandidateSignal] = []
        for ticker in context.watchlist:
            if ticker in active_positions:
                continue
            df = context.price_data.get(ticker)
            if df is None or df.empty:
                continue
            idx = ticker_date_index.get(ticker, {}).get(day_ts)
            if idx is None or idx < REPLAY_MIN_BAR_INDEX or idx >= len(df) - 1:
                continue
            window = df.iloc[: idx + 1]

            if spy_regime is not None and not allow_bear_regime:
                try:
                    spy_i = int(spy_regime.index.get_indexer([day_ts], method="pad")[0])
                    if spy_i >= 0 and not bool(spy_regime.iloc[spy_i]):
                        diagnostics["regime_blocked"] = int(diagnostics["regime_blocked"]) + 1
                        continue
                except Exception:
                    pass

            admit = _evaluate_backtest_entry_admit(
                window,
                skill_dir=sd,
                entry_family=entry_family,
                strategy_ids=selected_ids,
                day_ts=day_ts,
                diagnostics=diagnostics,
            )
            if not admit.admitted:
                continue
            candidate_family = admit.family or entry_family
            vcp_ok = check_vcp_volume(window, sd)
            if not vcp_ok:
                diagnostics["vcp_fail"] = int(diagnostics["vcp_fail"]) + 1
                apply_vcp_hard = vcp_gate_mode == "hard" and not admit.shadow_only
                if apply_vcp_hard:
                    continue
                diagnostics["vcp_would_block"] = int(diagnostics["vcp_would_block"]) + 1
            apply_breakout = breakout_confirm_on and admit.stage2_ok and (breakout_enabled or bool(selected_ids))
            if apply_breakout and idx >= breakout_confirm_bars:
                # Each of the last BREAKOUT_CONFIRM_BARS closes must hold above
                # the prior bar's high (bars=1 preserves legacy behavior).
                breakout_ok = True
                for j in range(breakout_confirm_bars):
                    if float(df["close"].iloc[idx - j]) < float(df["high"].iloc[idx - j - 1]):
                        breakout_ok = False
                        break
                if not breakout_ok:
                    diagnostics["breakout_not_confirmed"] = int(diagnostics["breakout_not_confirmed"]) + 1
                    continue

            entry_shadow = evaluate_entry_timing_shadow(window, sd)
            from stage_analysis import evaluate_weekly_swing_entry_buffer

            weekly_buffer = evaluate_weekly_swing_entry_buffer(
                horizon_hits=admit.horizon_hits,
                entry_shadow=entry_shadow,
                df=window,
                skill_dir=sd,
            )
            if weekly_buffer.get("applies"):
                diagnostics["weekly_swing_entry_buffer_evaluated"] = (
                    int(diagnostics.get("weekly_swing_entry_buffer_evaluated", 0) or 0) + 1
                )
                diagnostics["weekly_swing_entry_buffer_mode"] = weekly_buffer.get("mode")
                if weekly_buffer.get("would_cut"):
                    diagnostics["weekly_swing_entry_buffer_would_cut"] = (
                        int(diagnostics.get("weekly_swing_entry_buffer_would_cut", 0) or 0) + 1
                    )
                    if str(weekly_buffer.get("mode") or "") == "live":
                        diagnostics["weekly_swing_entry_buffer_blocked"] = (
                            int(diagnostics.get("weekly_swing_entry_buffer_blocked", 0) or 0) + 1
                        )
                        continue
            if str(entry_shadow.get("mode") or "off") != "off":
                diagnostics["entry_timing_shadow_evaluated"] = int(diagnostics["entry_timing_shadow_evaluated"]) + 1
                if entry_shadow.get("would_filter"):
                    diagnostics["entry_timing_shadow_would_filter"] = (
                        int(diagnostics["entry_timing_shadow_would_filter"]) + 1
                    )
                    if entry_timing_blocks_stage_a(entry_shadow, sd):
                        diagnostics["entry_timing_blocked"] = int(diagnostics["entry_timing_blocked"]) + 1
                        continue

            sector_ok, sector_reason = _sector_filter_pass(ticker, idx, context)
            if not sector_ok:
                if sector_reason == "sector_not_winning":
                    diagnostics["sector_not_winning"] = int(diagnostics["sector_not_winning"]) + 1
                elif sector_reason == "no_sector_etf":
                    diagnostics["no_sector_etf"] = int(diagnostics["no_sector_etf"]) + 1
                if sector_gate_mode == "hard" and not admit.shadow_only:
                    continue
                diagnostics["sector_would_block"] = int(diagnostics["sector_would_block"]) + 1

            forensic_snapshot = forensic_cache.get(ticker)
            if forensic_enabled and forensic_mode != "off":
                try:
                    if forensic_snapshot is None:
                        from forensic_accounting import compute_forensic_snapshot

                        forensic_snapshot = compute_forensic_snapshot(
                            ticker,
                            skill_dir=sd,
                            cache_hours=forensic_cache_hours,
                            sloan_max=forensic_sloan_max,
                            beneish_max=forensic_beneish_max,
                            altman_min=forensic_altman_min,
                        )
                        forensic_cache[ticker] = forensic_snapshot
                    forensic_flags = list((forensic_snapshot or {}).get("forensic_flags", []) or [])
                    if forensic_mode == "hard" and forensic_flags:
                        diagnostics["forensic_filtered"] = int(diagnostics["forensic_filtered"]) + 1
                        continue
                except Exception as e:
                    LOG.debug("Backtest forensic check skipped for %s: %s", ticker, e)

            miro = _run_mirofish_for_entry(ticker, window, skill_dir=sd)
            comps = compute_signal_components(
                window,
                mirofish_conviction=miro.get("conviction_score") if miro else None,
                mirofish_result=miro,
            )
            pts_cap = evaluate_pts_52w_cap(
                float(comps["pts_52w"]) if comps.get("pts_52w") is not None else None,
                sd,
            )
            if str(pts_cap.get("mode") or "off") != "off":
                diagnostics["pts_52w_cap_evaluated"] = int(diagnostics["pts_52w_cap_evaluated"]) + 1
                if pts_cap.get("would_filter"):
                    diagnostics["pts_52w_cap_would_filter"] = int(diagnostics["pts_52w_cap_would_filter"]) + 1
                    if pts_52w_cap_blocks_stage_a(pts_cap, sd):
                        diagnostics["pts_52w_cap_blocked"] = int(diagnostics["pts_52w_cap_blocked"]) + 1
                        continue
            early_stop_gate = evaluate_early_stop_gate(
                pts_52w=float(comps["pts_52w"]) if comps.get("pts_52w") is not None else None,
                breakout_buffer_pct=(
                    float(entry_shadow.get("breakout_buffer_pct"))
                    if isinstance(entry_shadow, dict) and entry_shadow.get("breakout_buffer_pct") is not None
                    else None
                ),
                skill_dir=sd,
            )
            if str(early_stop_gate.get("mode") or "off") != "off":
                diagnostics["early_stop_gate_evaluated"] = int(diagnostics["early_stop_gate_evaluated"]) + 1
                if early_stop_gate.get("would_filter"):
                    diagnostics["early_stop_gate_would_filter"] = int(diagnostics["early_stop_gate_would_filter"]) + 1
                    if early_stop_gate_blocks_stage_a(early_stop_gate, sd):
                        diagnostics["early_stop_gate_blocked"] = int(diagnostics["early_stop_gate_blocked"]) + 1
                        continue
            signal: dict[str, Any] = {
                "ticker": ticker,
                "signal_score": float(comps.get("score", 0) or 0),
                # price/sma_50/sma_200/score_components mirror the live
                # scanner's signal_row: the advisory model extracts its
                # features from these keys, and without them every advisory
                # score degenerated to zeroed features (confluence gate could
                # never see advisory_high in backtests).
                "price": float(window["close"].iloc[-1]),
                "sma_50": float(window["sma_50"].iloc[-1]) if "sma_50" in window.columns else None,
                "sma_200": float(window["sma_200"].iloc[-1]) if "sma_200" in window.columns else None,
                "score_components": comps,
                "latest_volume": float(window["volume"].iloc[-1]),
                "avg_vol_50": float(window["avg_vol_50"].iloc[-1]),
                "expansion_volume_ratio": expansion_volume_ratio(window),
                "mirofish_result": miro,
                "mirofish_conviction": miro.get("conviction_score") if miro else None,
                "forensic_sloan": ((forensic_snapshot or {}).get("sloan") or {}).get("sloan_ratio"),
                "forensic_beneish": ((forensic_snapshot or {}).get("beneish") or {}).get("m_score"),
                "forensic_altman": ((forensic_snapshot or {}).get("altman") or {}).get("z_score"),
                "forensic_flags": list((forensic_snapshot or {}).get("forensic_flags", []) or []),
                "breakout_confirmed": True,
                "entry_family": candidate_family,
                "weekly_swing_entry_buffer": weekly_buffer,
                "horizon_hits": [
                    str(hit.get("name") or hit) if isinstance(hit, dict) else str(hit)
                    for hit in (admit.horizon_hits or [])
                ],
            }
            try:
                from core.debate_grade import stamp_checklist_fields

                stamped = stamp_checklist_fields(window, sd, include_points=False)
                for key in ("trend_stack_ok", "sma200_rising", "vcp_present"):
                    if key in stamped:
                        signal[key] = stamped[key]
            except Exception as stamp_exc:
                LOG.debug("Debate-grade checklist skipped for %s: %s", ticker, stamp_exc)
            if admit.horizon_hits:
                signal["strategy_plugins"] = list(admit.horizon_hits)
            history_meta = context.history_meta_by_ticker.get(ticker, {})
            signal["data_provider"] = str(history_meta.get("provider") or "unknown")
            signal["data_provider_primary"] = str(signal["data_provider"]).lower() == "schwab"
            signal["used_fallback_data"] = bool(history_meta.get("used_fallback", False))
            signal["fallback_reason"] = history_meta.get("reason")

            pead_info = None
            pead_required = pead_enabled or candidate_family in {"pead_primary", "both"}
            if pead_required:
                try:
                    from earnings_signal import check_earnings_at_date

                    pead_info = check_earnings_at_date(
                        ticker,
                        day_ts,
                        df=window,
                        lookback_days=pead_lookback_days,
                        skill_dir=sd,
                    )
                except Exception as e:
                    LOG.debug("Backtest PEAD check skipped for %s: %s", ticker, e)
            if candidate_family == "pead_primary":
                beat = bool((pead_info or {}).get("beat"))
                surprise = (pead_info or {}).get("surprise_pct")
                try:
                    surprise_f = float(surprise) if surprise is not None else None
                except (TypeError, ValueError):
                    surprise_f = None
                if not beat or surprise_f is None or surprise_f <= 0.0:
                    diagnostics["pead_primary_fail"] = int(diagnostics["pead_primary_fail"]) + 1
                    continue
            signal["pead_surprise_pct"] = (pead_info or {}).get("surprise_pct")
            signal["pead_beat"] = (pead_info or {}).get("beat")
            pead_score_delta = 0.0
            if pead_required and signal["pead_surprise_pct"] is not None:
                try:
                    s = float(signal["pead_surprise_pct"])
                    if s > 0.15:
                        pead_score_delta = pead_score_boost_large
                    elif s > 0.05:
                        pead_score_delta = pead_score_boost
                    elif s < -0.15:
                        pead_score_delta = -abs(pead_score_penalty_large)
                    elif s < 0:
                        pead_score_delta = -abs(pead_score_penalty)
                except (TypeError, ValueError):
                    pead_score_delta = 0.0
            if pead_score_delta != 0.0:
                signal["signal_score"] = max(0.0, min(100.0, float(signal["signal_score"]) + pead_score_delta))
            signal["pead_score_delta"] = float(pead_score_delta)

            try:
                from advisory_model import score_signal_advisory

                advisory = score_signal_advisory(signal, skill_dir=sd, window=window)
                if advisory is not None:
                    signal["advisory"] = advisory.to_dict()
            except Exception as e:
                LOG.debug("Backtest advisory scoring skipped for %s: %s", ticker, e)

            try:
                signal = _apply_score_stack(signal, sd, score_stack_context="backtest")
            except Exception as e:
                LOG.debug("Backtest score stack skipped for %s: %s", ticker, e)

            if prediction_market_engine is not None and prediction_market_mode == "live":
                diagnostics["prediction_market_processed"] = int(diagnostics["prediction_market_processed"]) + 1
                try:
                    entry_dt = pd.Timestamp(day_ts).to_pydatetime()
                    entry_dt_utc = (
                        entry_dt.replace(tzinfo=timezone.utc)
                        if entry_dt.tzinfo is None
                        else entry_dt.astimezone(timezone.utc)
                    )
                    regime_for_entry = True
                    if spy_regime is not None:
                        try:
                            spy_i = int(spy_regime.index.get_indexer([day_ts], method="pad")[0])
                            if spy_i >= 0:
                                regime_for_entry = bool(spy_regime.iloc[spy_i])
                        except Exception:
                            pass
                    evaluation = prediction_market_engine.evaluate(
                        ticker=ticker,
                        as_of=entry_dt_utc,
                        regime_is_bullish=regime_for_entry,
                    )
                    signal = apply_overlay_to_signal(signal=signal, evaluation=evaluation, advisory=None)
                    if evaluation.status == "ok" and bool(evaluation.overlay.get("applied")):
                        diagnostics["prediction_market_applied"] = int(diagnostics["prediction_market_applied"]) + 1
                    elif evaluation.status == "error":
                        diagnostics["prediction_market_errors"] = int(diagnostics["prediction_market_errors"]) + 1
                    else:
                        diagnostics["prediction_market_skipped"] = int(diagnostics["prediction_market_skipped"]) + 1
                except Exception as e:
                    diagnostics["prediction_market_errors"] = int(diagnostics["prediction_market_errors"]) + 1
                    LOG.warning("Backtest prediction-market overlay failed for %s: %s", ticker, e)

            reasons = _evaluate_quality_gates(signal, sd)
            if _quality_mode_should_filter(reasons, sd):
                diagnostics["quality_gates_filtered"] = int(diagnostics["quality_gates_filtered"]) + 1
                continue
            if reasons:
                diagnostics["quality_gates_would_filter"] = int(diagnostics["quality_gates_would_filter"]) + 1

            # Confluence gate: mirror of the live scanner's post-Stage-B gate.
            # Requires an independent confirmation (PEAD-positive or
            # advisory-high) beyond the Stage 2 + VCP base setup.
            if confluence_mode in ("shadow", "live"):
                confirmations = _evaluate_confluence(signal, sd)
                signal["confluence_confirmations"] = confirmations
                if len(confirmations) >= confluence_require_count:
                    diagnostics["confluence_confirmed"] = int(diagnostics["confluence_confirmed"]) + 1
                elif confluence_mode == "live":
                    diagnostics["confluence_blocked"] = int(diagnostics["confluence_blocked"]) + 1
                    continue
                else:
                    signal["confluence_would_block"] = True
                    diagnostics["confluence_would_block"] = int(diagnostics["confluence_would_block"]) + 1

            meta_size_mult = 1.0
            event_size_mult = 1.0
            adaptive_size_mult = 1.0
            event_policy = None
            meta_payload: dict[str, Any] | None = None
            if overlay_cfg.meta_policy != "off":
                signal, meta_allow, meta_size_mult = apply_meta_policy_overlay(
                    signal=signal,
                    diagnostics=diagnostics,
                    skill_dir=sd,
                    mode=overlay_cfg.meta_policy,
                )
                meta_payload = signal.get("meta_policy")
                if not meta_allow:
                    continue
            if overlay_cfg.event_risk != "off":
                event_policy = evaluate_event_risk_for_backtest(
                    ticker=ticker,
                    entry_date=day_ts,
                    pead_info=pead_info,
                    skill_dir=sd,
                    mode=overlay_cfg.event_risk,
                )
                event_allow, event_size_mult = apply_event_risk_overlay(
                    policy=event_policy,
                    diagnostics=diagnostics,
                    mode=overlay_cfg.event_risk,
                )
                signal["event_risk"] = event_policy
                if not event_allow:
                    continue

            signal_score = _safe_telemetry_float(signal.get("signal_score"), 0.0)
            vcp_volume_ratio = _safe_telemetry_float(comps.get("avg_vcp_volume_ratio"), 0.0)
            if adaptive_guardrail_policy is not None:
                if not adaptive_guardrail_policy.allows_entry(signal_score):
                    diagnostics["adaptive_guardrail_filtered"] = int(diagnostics["adaptive_guardrail_filtered"]) + 1
                    continue
                adaptive_size_mult = adaptive_guardrail_policy.size_multiplier(signal_score, vcp_volume_ratio)
                if adaptive_size_mult < 0.999:
                    diagnostics["adaptive_guardrail_downsized"] = int(diagnostics["adaptive_guardrail_downsized"]) + 1

            fill = resolve_entry_fill(df, idx, entry_fill, era_end=end)
            if fill is None:
                diagnostics["entry_fill_skipped_no_next_bar"] = (
                    int(diagnostics.get("entry_fill_skipped_no_next_bar", 0) or 0) + 1
                )
                continue
            entry_price = float(fill["entry_price"])
            fill_idx = int(fill["fill_idx"])
            day_volume = float(df["volume"].iloc[idx]) if "volume" in df.columns else 0.0
            qty_hint = _estimate_order_qty(entry_price, day_volume, max_adv_participation=max_adv_participation)
            pm_mult = _safe_telemetry_float(signal.get("prediction_market_size_multiplier"), 1.0)
            pm_mult = max(0.85, min(1.15, pm_mult))
            combined_size_mult = pm_mult * float(meta_size_mult) * float(event_size_mult) * float(adaptive_size_mult)
            qty_hint = max(1, int(round(float(qty_hint) * combined_size_mult)))
            liq_cap = int(day_volume * max_adv_participation) if day_volume > 0 else 1
            if qty_hint > liq_cap:
                diagnostics["liquidity_filtered"] = int(diagnostics["liquidity_filtered"]) + 1
                qty_hint = liq_cap
            if qty_hint <= 0:
                diagnostics["liquidity_filtered"] = int(diagnostics["liquidity_filtered"]) + 1
                continue

            stop_pct_entry = _resolve_stop_pct_for_entry(df, idx, skill_dir=sd)
            signal["stop_pct"] = float(stop_pct_entry)
            # Entry date keeps an undated forensic snapshot from filling the business pillar.
            signal["entry_date"] = pd.Timestamp(day_ts).date().isoformat()
            try:
                from core.debate_grade import attach_debate_grade, stamp_pit_business

                stamp_pit_business(signal, filings_by_ticker.get(str(ticker).upper()) or [])
                attach_debate_grade(signal, diagnostics=diagnostics, skill_dir=sd)
            except Exception as grade_exc:
                LOG.debug("Debate grade skipped for %s: %s", ticker, grade_exc)
            effective_slippage_bps, exec_info = apply_exec_quality_overlay(
                slippage_bps_per_side=slippage_bps_per_side,
                day_volume=day_volume,
                qty=qty_hint,
                skill_dir=sd,
                mode=overlay_cfg.exec_quality,
            )
            try:
                from research.runtime import score_candidates_for_backtest_day

                score_candidates_for_backtest_day(signal, df.iloc[: idx + 1], day_ts, skill_dir=sd)
            except Exception:
                pass
            telemetry = _build_telemetry_payload(signal, comps)
            candidates.append(
                CandidateSignal(
                    ticker=ticker,
                    idx=fill_idx,
                    signal=signal,
                    reasons=reasons,
                    comps=comps,
                    sector_reason=sector_reason,
                    entry_price=entry_price,
                    day_volume=day_volume,
                    qty_hint=qty_hint,
                    stop_pct=stop_pct_entry,
                    pm_mult=pm_mult,
                    meta_size_mult=float(meta_size_mult),
                    event_size_mult=float(event_size_mult),
                    event_policy=event_policy,
                    meta_payload=meta_payload,
                    adaptive_size_mult=float(adaptive_size_mult),
                    effective_slippage_bps=float(effective_slippage_bps),
                    exec_info=exec_info,
                    telemetry=telemetry,
                )
            )

        try:
            from core.neutral_rank import stamp_neutral_rank

            stamp_neutral_rank(
                [candidate.signal for candidate in candidates],
                diagnostics,
                sd,
                scan_id="backtest",
                persist=False,
                exempt_research=False,
            )
        except Exception:
            LOG.debug("Backtest neutral rank shadow skipped", exc_info=True)
        candidates = _apply_candidate_rank_v2_filter(candidates)
        candidates = _apply_candidate_prob_rank(candidates)
        day_equity = _equity_at(day_ts)
        max_position_notional = max(0.0, float(day_equity) * max_position_size_pct)
        for candidate in sorted(candidates, key=_candidate_rank_key, reverse=True):
            max_positions_for_candidate = max_concurrent_positions
            if adaptive_guardrail_policy is not None:
                cand_score = _safe_telemetry_float(candidate.signal.get("signal_score"), 0.0)
                max_positions_for_candidate = adaptive_guardrail_policy.max_positions_for_candidate(
                    max_concurrent_positions,
                    cand_score,
                )
            if len(active_positions) >= max_positions_for_candidate:
                diagnostics["capital_filtered"] = int(diagnostics["capital_filtered"]) + 1
                continue
            if candidate.entry_price <= 0:
                continue
            size_by_risk = (
                float(day_equity) * float(risk_per_trade_pct) / float(candidate.stop_pct)
                if risk_per_trade_pct > 0.0 and candidate.stop_pct > 0.0
                else None
            )
            sized_notional = (
                float(size_by_risk) if size_by_risk is not None else float(day_equity) * float(position_size_pct)
            )
            sized_notional = max(0.0, min(float(sized_notional), float(max_position_notional)))
            qty_by_size = int(sized_notional / candidate.entry_price)
            qty_by_cash = int(max(0.0, min(max_position_notional, current_cash)) / candidate.entry_price)
            qty_by_liq = int(candidate.day_volume * max_adv_participation) if candidate.day_volume > 0 else 1
            qty = min(int(candidate.qty_hint), int(qty_by_size), int(qty_by_cash), int(qty_by_liq))
            if qty <= 0:
                if qty_by_cash <= 0:
                    diagnostics["capital_filtered"] = int(diagnostics["capital_filtered"]) + 1
                else:
                    diagnostics["position_limit_filtered"] = int(diagnostics["position_limit_filtered"]) + 1
                continue
            notional = float(qty) * float(candidate.entry_price)
            if notional > current_cash:
                diagnostics["capital_filtered"] = int(diagnostics["capital_filtered"]) + 1
                continue

            current_cash -= notional
            if (
                max_positions_for_candidate > max_concurrent_positions
                and len(active_positions) >= max_concurrent_positions
            ):
                diagnostics["adaptive_guardrail_extra_slot_entries"] = (
                    int(diagnostics["adaptive_guardrail_extra_slot_entries"]) + 1
                )
            entry_df = context.price_data.get(candidate.ticker)
            fill_ts = day_ts
            if entry_df is not None and 0 <= int(candidate.idx) < len(entry_df):
                fill_ts = pd.Timestamp(entry_df.index[int(candidate.idx)])
            pos = ActivePosition(
                ticker=candidate.ticker,
                entry_idx=candidate.idx,
                entry_date=fill_ts,
                entry_price=float(candidate.entry_price),
                qty=int(qty),
                day_volume=float(candidate.day_volume),
                signal=candidate.signal,
                reasons=candidate.reasons,
                comps=candidate.comps,
                sector_reason=candidate.sector_reason,
                stop_pct=float(candidate.stop_pct),
                pm_mult=float(candidate.pm_mult),
                meta_size_mult=float(candidate.meta_size_mult),
                event_size_mult=float(candidate.event_size_mult),
                event_policy=candidate.event_policy,
                meta_payload=candidate.meta_payload,
                effective_slippage_bps=float(candidate.effective_slippage_bps),
                exec_info=candidate.exec_info,
                telemetry=candidate.telemetry,
                highest_close=float(candidate.entry_price),
            )
            if entry_df is not None and not entry_df.empty:
                exit_idx, mgr_reason = _plan_exit_manager_for_entry(
                    entry_df,
                    int(candidate.idx),
                    int(backtest_hold_days),
                    float(candidate.stop_pct),
                    sd,
                    diagnostics,
                    entry_price=float(candidate.entry_price),
                    evaluate_entry_bar=entry_fill == "next_open",
                    mode=overlay_cfg.exit_manager,
                )
                pos.exit_manager_exit_idx = exit_idx
                pos.exit_manager_reason = mgr_reason
            active_positions[candidate.ticker] = pos
            if size_by_risk is not None:
                diagnostics["risk_sized_entries"] = int(diagnostics["risk_sized_entries"]) + 1
            else:
                diagnostics["fixed_sized_entries"] = int(diagnostics["fixed_sized_entries"]) + 1
            diagnostics["entries"] = int(diagnostics["entries"]) + 1

        concurrent_samples.append(len(active_positions))
        peak_concurrent = max(peak_concurrent, len(active_positions))
        equity_curve.append((pd.Timestamp(day_ts).isoformat(), float(_equity_at(day_ts))))

    # Final liquidation of remaining positions at end date.
    final_ts = pd.Timestamp(timeline[-1])
    for ticker, pos in list(active_positions.items()):
        df = context.price_data.get(ticker)
        if df is None or df.empty:
            continue
        try:
            exit_idx = int(df.index.get_indexer([final_ts], method="pad")[0])
        except Exception:
            exit_idx = len(df) - 1
        if exit_idx <= pos.entry_idx:
            exit_idx = min(len(df) - 1, pos.entry_idx + 1)
        exit_price = float(df["close"].iloc[exit_idx])
        exit_date = pd.Timestamp(df.index[exit_idx])
        diagnostics["exits_final_liquidation"] = int(diagnostics["exits_final_liquidation"]) + 1
        net_ret, cost_ctx = _net_return_after_costs(
            entry_price=float(pos.entry_price),
            exit_price=float(exit_price),
            qty=int(pos.qty),
            slippage_bps_per_side=float(pos.effective_slippage_bps),
            fee_per_share=fee_per_share,
            min_fee_per_order=min_fee_per_order,
        )
        gross_ret = (
            (float(exit_price) - float(pos.entry_price)) / float(pos.entry_price) if pos.entry_price > 0 else 0.0
        )
        current_cash += float(pos.entry_price) * float(pos.qty) * (1.0 + float(net_ret))
        mfe, mae = _compute_mfe_mae(
            df,
            pos.entry_idx,
            exit_idx,
            float(pos.entry_price),
            include_entry_bar=entry_fill == "next_open",
        )
        ohlc_path = _build_ohlc_path(df, pos.entry_idx, exit_idx) if _is_ohlc_path_logging_enabled() else []
        regime_tags = lookup_regime_at(pos.entry_date, spy_above_200, spy_regime_bucket)
        all_trades.append(
            {
                "ticker": pos.ticker,
                "entry_date": pd.Timestamp(pos.entry_date).isoformat(),
                "exit_date": pd.Timestamp(exit_date).isoformat(),
                "entry_fill": entry_fill,
                "signal_date": pos.signal.get("entry_date"),
                "entry_price": round(float(pos.entry_price), 4),
                "exit_price": round(float(exit_price), 4),
                "return": float(gross_ret),
                "net_return": float(net_ret),
                "mfe": mfe,
                "mae": mae,
                "ohlc_path": ohlc_path,
                "exit_reason": "final_liquidation",
                **_score_fields_for_trade(pos.signal, sd),
                "mirofish_conviction": pos.signal.get("mirofish_conviction"),
                "data_provider": pos.signal.get("data_provider"),
                "data_provider_primary": pos.signal.get("data_provider_primary"),
                "used_fallback_data": pos.signal.get("used_fallback_data"),
                "fallback_reason": pos.signal.get("fallback_reason"),
                "sector_etf": context.sector_etf_by_ticker.get(pos.ticker),
                "sector_filter": pos.sector_reason,
                "regime_above_200": regime_tags.get("regime_above_200"),
                "regime_bucket": regime_tags.get("regime_bucket"),
                "quality_reasons": pos.reasons,
                "forensic_sloan": pos.signal.get("forensic_sloan"),
                "forensic_beneish": pos.signal.get("forensic_beneish"),
                "forensic_altman": pos.signal.get("forensic_altman"),
                "forensic_flags": pos.signal.get("forensic_flags"),
                "pead_beat": pos.signal.get("pead_beat"),
                "pead_surprise_pct": pos.signal.get("pead_surprise_pct"),
                "qty_estimate": int(pos.qty),
                "day_volume": float(pos.day_volume),
                "slippage_pct": float(cost_ctx["slippage_pct"]),
                "fees_pct": float(cost_ctx["fees_pct"]),
                "stop_pct": float(pos.stop_pct),
                "prediction_market_status": ((pos.signal.get("prediction_market") or {}).get("status")),
                "prediction_market_reason": ((pos.signal.get("prediction_market") or {}).get("reason")),
                "prediction_market_size_multiplier": float(pos.pm_mult),
                "meta_policy_decision": (pos.meta_payload or {}).get("decision") if pos.meta_payload else None,
                "meta_policy_size_multiplier": float(pos.meta_size_mult),
                "event_risk_action": (pos.event_policy or {}).get("action") if pos.event_policy else None,
                "event_risk_size_multiplier": float(pos.event_size_mult),
                "exit_manager_partial_done": False,
                "exec_quality_regime": pos.exec_info.get("regime"),
                "exec_quality_effective_slippage_bps": pos.exec_info.get("effective_slippage_bps"),
                "telemetry": pos.telemetry,
            }
        )
        _publish_trade_progress()
        active_positions.pop(ticker, None)

    ending_equity = float(current_cash)
    equity_curve.append((pd.Timestamp(final_ts).isoformat(), ending_equity))
    _publish_trade_progress(force=True)

    if not all_trades:
        return {
            "start_date": start,
            "end_date": end,
            "total_trades": 0,
            "win_rate": 0.0,
            "total_return_pct": round(100.0 * ((ending_equity / starting_equity) - 1.0), 2),
            "total_return_net_pct": round(100.0 * ((ending_equity / starting_equity) - 1.0), 2),
            "cagr_pct": 0.0,
            "cagr_net_pct": 0.0,
            "avg_return_pct": 0.0,
            "avg_return_net_pct": 0.0,
            "avg_gain_pct": 0.0,
            "avg_loss_pct": 0.0,
            "profit_factor": 0.0,
            "profit_factor_net": 0.0,
            "max_drawdown_pct": 0.0,
            "max_drawdown_net_pct": 0.0,
            "portfolio_enabled": True,
            "portfolio_summary": {
                "capacity_filtered": int(diagnostics.get("capital_filtered", 0)),
                "avg_concurrent": round(
                    (sum(concurrent_samples) / len(concurrent_samples)) if concurrent_samples else 0.0,
                    3,
                ),
                "peak_concurrent": int(peak_concurrent),
                "risk_sized_count": 0,
                "fixed_sized_count": int(diagnostics.get("entries", 0)),
                "starting_equity": float(starting_equity),
                "ending_equity": float(ending_equity),
            },
            "avg_holding_days": backtest_hold_days,
            "backtest_min_hold_days_before_trail": min_hold_before_trail,
            "backtest_min_hold_defer_soft_exits": defer_soft_exits,
            "trailing_stop_pct": round(100.0 * stop_pct_base, 2),
            "adaptive_stop_enabled": bool(adaptive_stop_enabled),
            "adaptive_guardrails_enabled": bool(adaptive_guardrail_policy is not None),
            "slippage_bps_per_side": float(slippage_bps_per_side),
            "fee_per_share": float(fee_per_share),
            "min_fee_per_order": float(min_fee_per_order),
            "max_adv_participation": float(max_adv_participation),
            "diagnostics": diagnostics,
            "quality_gates_mode": quality_mode,
            "prediction_market_mode": prediction_market_mode,
            "prediction_market_provider": prediction_market_provider,
            "intelligence_overlay": overlay_cfg.as_dict(),
            "excluded_tickers": context.excluded_tickers[:50],
            "excluded_count": len(context.excluded_tickers),
            "universe_size": len(context.watchlist),
            "data_integrity": context.data_integrity,
            "score_stack_metrics": {},
            "provider_segment_metrics": {},
            "sector_regime_summary": summarize_sector_regime([]),
            "entry_fill": entry_fill,
            "history_warmup_calendar_days": HISTORY_WARMUP_DAYS,
            "history_warmup_sessions_required": INDICATOR_WARMUP_SESSIONS,
            "history_fetch_note": HISTORY_FETCH_NOTE,
            "findings": "No trades generated over the requested window.",
            "trades_sample": [],
            "trade_ranks": rank_closed_trades(all_trades),
        }

    trades_df = pd.DataFrame(all_trades)
    ret = trades_df["return"].astype(float)
    ret_net = trades_df["net_return"].astype(float)
    wins = int((ret > 0).sum())
    wins_net = int((ret_net > 0).sum())
    total = int(len(trades_df))
    avg_ret = float(ret.mean())
    avg_ret_net = float(ret_net.mean())
    avg_gain = float(ret[ret > 0].mean()) if (ret > 0).any() else 0.0
    avg_loss = float(ret[ret <= 0].mean()) if (ret <= 0).any() else 0.0
    avg_gain_net = float(ret_net[ret_net > 0].mean()) if (ret_net > 0).any() else 0.0
    avg_loss_net = float(ret_net[ret_net <= 0].mean()) if (ret_net <= 0).any() else 0.0
    gross_profit = float(ret[ret > 0].sum()) if (ret > 0).any() else 0.0
    gross_loss = abs(float(ret[ret <= 0].sum())) if (ret <= 0).any() else 0.0
    gross_profit_net = float(ret_net[ret_net > 0].sum()) if (ret_net > 0).any() else 0.0
    gross_loss_net = abs(float(ret_net[ret_net <= 0].sum())) if (ret_net <= 0).any() else 0.0
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else float("inf")
    profit_factor_net = (gross_profit_net / gross_loss_net) if gross_loss_net > 0 else float("inf")

    total_ret = (ending_equity / starting_equity) - 1.0 if starting_equity > 0 else 0.0
    total_ret_net = total_ret
    years = max(1e-9, (pd.Timestamp(end) - pd.Timestamp(start)).days / 365.25)
    cagr = float((1.0 + total_ret) ** (1.0 / years) - 1.0) if total_ret > -1.0 else -1.0
    cagr_net = cagr

    eq_series = pd.Series([float(v) for _d, v in equity_curve]) if equity_curve else pd.Series([starting_equity])
    peak = eq_series.cummax()
    dd = (eq_series / peak) - 1.0
    max_dd = float(dd.min()) if not dd.empty else 0.0

    findings = (
        f"Event-driven portfolio backtest generated {total} trades across {len(context.watchlist)} symbols. "
        f"Gross win rate {100.0 * wins / total:.1f}%, net win rate {100.0 * wins_net / total:.1f}%, "
        f"portfolio net return {100.0 * total_ret_net:.2f}% with max DD {100.0 * max_dd:.2f}%, "
        f"net PF {profit_factor_net if profit_factor_net == float('inf') else round(float(profit_factor_net), 3)}."
    )
    _attach_liquid_shadow(all_trades, diagnostics, sd)
    score_stack_metrics = _score_stack_metrics_from_trades(trades_df)
    provider_segment_metrics = _provider_segment_metrics_from_trades(trades_df)
    sector_regime_summary = summarize_sector_regime(all_trades)
    curve_payload = _serialize_equity_curves(equity_curve, starting_equity)

    out: dict[str, Any] = {
        "start_date": start,
        "end_date": end,
        "total_trades": total,
        "win_rate": round(100.0 * wins / total, 2),
        "win_rate_net": round(100.0 * wins_net / total, 2),
        "total_return_pct": round(100.0 * total_ret, 2),
        "total_return_net_pct": round(100.0 * total_ret_net, 2),
        "cagr_pct": round(100.0 * cagr, 2),
        "cagr_net_pct": round(100.0 * cagr_net, 2),
        "avg_return_pct": round(100.0 * avg_ret, 3),
        "avg_return_net_pct": round(100.0 * avg_ret_net, 3),
        "avg_gain_pct": round(100.0 * avg_gain, 3),
        "avg_loss_pct": round(100.0 * avg_loss, 3),
        "avg_gain_net_pct": round(100.0 * avg_gain_net, 3),
        "avg_loss_net_pct": round(100.0 * avg_loss_net, 3),
        "profit_factor": round(float(profit_factor), 3) if profit_factor != float("inf") else "inf",
        "profit_factor_net": round(float(profit_factor_net), 3) if profit_factor_net != float("inf") else "inf",
        "max_drawdown_pct": round(100.0 * max_dd, 2),
        "max_drawdown_net_pct": round(100.0 * max_dd, 2),
        "equity_curve": curve_payload["equity_curve"],
        "drawdown_curve": curve_payload["drawdown_curve"],
        "portfolio_enabled": True,
        "portfolio_summary": {
            "capacity_filtered": int(diagnostics.get("capital_filtered", 0)),
            "avg_concurrent": round(
                (sum(concurrent_samples) / len(concurrent_samples)) if concurrent_samples else 0.0,
                3,
            ),
            "peak_concurrent": int(peak_concurrent),
            "risk_sized_count": int(diagnostics.get("risk_sized_entries", 0)),
            "fixed_sized_count": int(diagnostics.get("fixed_sized_entries", 0)),
            "starting_equity": float(starting_equity),
            "ending_equity": float(ending_equity),
        },
        "avg_holding_days": backtest_hold_days,
        "backtest_min_hold_days_before_trail": min_hold_before_trail,
        "backtest_min_hold_defer_soft_exits": defer_soft_exits,
        "trailing_stop_pct": round(100.0 * stop_pct_base, 2),
        "adaptive_stop_enabled": bool(adaptive_stop_enabled),
        "adaptive_guardrails_enabled": bool(adaptive_guardrail_policy is not None),
        "backtest_position_size_pct": float(position_size_pct),
        "backtest_risk_per_trade_pct": float(risk_per_trade_pct),
        "slippage_bps_per_side": float(slippage_bps_per_side),
        "fee_per_share": float(fee_per_share),
        "min_fee_per_order": float(min_fee_per_order),
        "max_adv_participation": float(max_adv_participation),
        "diagnostics": diagnostics,
        "quality_gates_mode": quality_mode,
        "prediction_market_mode": prediction_market_mode,
        "prediction_market_provider": prediction_market_provider,
        "intelligence_overlay": overlay_cfg.as_dict(),
        "excluded_tickers": context.excluded_tickers[:50],
        "excluded_count": len(context.excluded_tickers),
        "universe_size": len(context.watchlist),
        "data_integrity": context.data_integrity,
        "score_stack_metrics": score_stack_metrics,
        "provider_segment_metrics": provider_segment_metrics,
        "sector_regime_summary": sector_regime_summary,
        "trades_sample": all_trades[:5],
        "trade_ranks": rank_closed_trades(all_trades),
        "entry_fill": entry_fill,
        "history_warmup_calendar_days": HISTORY_WARMUP_DAYS,
        "history_warmup_sessions_required": INDICATOR_WARMUP_SESSIONS,
        "history_fetch_note": HISTORY_FETCH_NOTE,
        "findings": findings,
    }
    if include_all_trades:
        out["trades"] = all_trades
    return out


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    print("Running 10-year live-parity backtest using env/config watchlist...")
    result = run_backtest(start_date=(datetime.now() - timedelta(days=3652)).strftime("%Y-%m-%d"))
    print("\n--- BACKTEST RESULTS ---")
    for k, v in result.items():
        if k not in {"trades_sample", "excluded_tickers", "findings"}:
            print(f"  {k}: {v}")
    print("\n--- FINDINGS ---")
    print(result.get("findings", ""))
    if result.get("trades_sample"):
        print("\nSample trades:", result["trades_sample"][:3])


if __name__ == "__main__":
    main()
