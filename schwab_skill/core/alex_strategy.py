"""Alex strategy — labeled Swing Edge v10 mean-reversion sleeve.

Research / Track B only. Does not change the Stage 2 live scanner. Replay
rules match the Swing Edge v10 summary: RSI(14) oversold + SMA20 gap, IT
exclusion, SPY/SHY guards, ATR stop, 85% of 2R target, RSI-50 exit, 7-day
hold, 7-day cooldown, 12-slot book, 1.1% risk with loss-streak and drawdown
size tiers.
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

LOG = logging.getLogger(__name__)

SKILL_DIR = Path(__file__).resolve().parent.parent
DEFAULT_UNIVERSE_PATH = SKILL_DIR / "universes" / "alex_strategy.json"
EXCLUDED_SECTOR = "Information Technology"
RATE_HIKE_SECTORS = frozenset({"Financials", "Real Estate"})
MIN_BARS = 80


@dataclass(frozen=True)
class AlexParams:
    rsi_entry_max: float = 30.0
    sma_drop_min: float = 0.05
    atr_period: int = 14
    atr_mult: float = 1.5
    reward_ratio: float = 2.0
    target_scale: float = 0.85
    max_hold_days: int = 7
    rsi_exit: float = 50.0
    cooldown_days: int = 7
    max_positions: int = 12
    risk_per_trade_pct: float = 0.011
    starting_equity: float = 5000.0
    spy_roc_days: int = 10
    spy_roc_min: float = -0.01
    shy_roc_days: int = 60
    shy_roc_min: float = -0.015
    entry_slippage_pct: float = 0.0005
    commission_per_trade: float = 1.0
    entry_mode: str = "next_open"
    excluded_sector: str = EXCLUDED_SECTOR


def load_alex_params(skill_dir: Path | None = None) -> AlexParams:
    from config import (
        get_alex_atr_mult,
        get_alex_commission_per_trade,
        get_alex_cooldown_days,
        get_alex_entry_mode,
        get_alex_entry_slippage_pct,
        get_alex_max_hold_days,
        get_alex_max_positions,
        get_alex_reward_ratio,
        get_alex_risk_per_trade_pct,
        get_alex_rsi_entry_max,
        get_alex_rsi_exit,
        get_alex_shy_roc_min,
        get_alex_sma_drop_min,
        get_alex_spy_roc_min,
        get_alex_starting_equity,
        get_alex_target_scale,
    )

    sd = skill_dir or SKILL_DIR
    mode = get_alex_entry_mode(sd)
    return AlexParams(
        rsi_entry_max=get_alex_rsi_entry_max(sd),
        sma_drop_min=get_alex_sma_drop_min(sd),
        atr_mult=get_alex_atr_mult(sd),
        reward_ratio=get_alex_reward_ratio(sd),
        target_scale=get_alex_target_scale(sd),
        max_hold_days=get_alex_max_hold_days(sd),
        rsi_exit=get_alex_rsi_exit(sd),
        cooldown_days=get_alex_cooldown_days(sd),
        max_positions=get_alex_max_positions(sd),
        risk_per_trade_pct=get_alex_risk_per_trade_pct(sd),
        starting_equity=get_alex_starting_equity(sd),
        spy_roc_min=get_alex_spy_roc_min(sd),
        shy_roc_min=get_alex_shy_roc_min(sd),
        entry_slippage_pct=get_alex_entry_slippage_pct(sd),
        commission_per_trade=get_alex_commission_per_trade(sd),
        entry_mode=mode,
    )


def load_alex_universe(path: Path | None = None) -> dict[str, str]:
    """Return ticker -> sector. Information Technology is omitted at source."""
    src = path or DEFAULT_UNIVERSE_PATH
    payload = json.loads(src.read_text(encoding="utf-8"))
    sectors = payload.get("sectors") or {}
    mapping: dict[str, str] = {}
    for sector, tickers in sectors.items():
        if str(sector) == EXCLUDED_SECTOR:
            continue
        for raw in tickers or []:
            ticker = str(raw).strip().upper()
            if ticker:
                mapping[ticker] = str(sector)
    return mapping


def size_multiplier(*, consecutive_losses: int, drawdown_pct: float) -> float:
    """Lower of loss-streak and peak-to-trough equity multipliers."""
    losses = max(0, int(consecutive_losses))
    if losses <= 2:
        loss_mult = 1.0
    elif losses <= 4:
        loss_mult = 0.75
    elif losses <= 8:
        loss_mult = 0.50
    else:
        loss_mult = 0.25
    dd = max(0.0, float(drawdown_pct))
    if dd < 0.10:
        dd_mult = 1.0
    elif dd < 0.20:
        dd_mult = 0.75
    elif dd < 0.25:
        dd_mult = 0.50
    else:
        dd_mult = 0.25
    return min(loss_mult, dd_mult)


def add_alex_indicators(
    df: pd.DataFrame, *, rsi_period: int = 14, sma_period: int = 20, atr_period: int = 14
) -> pd.DataFrame:
    """Add rsi_14, sma_20, atr_14 using Wilder smoothing (TA-Lib compatible)."""
    if df.empty:
        out = df.copy()
        out["rsi_14"] = pd.Series(dtype=float)
        out["sma_20"] = pd.Series(dtype=float)
        out["atr_14"] = pd.Series(dtype=float)
        return out
    out = df.copy()
    close = out["close"].astype(float)
    high = out["high"].astype(float)
    low = out["low"].astype(float)
    out["sma_20"] = close.rolling(sma_period, min_periods=sma_period).mean()
    out["rsi_14"] = _wilder_rsi(close, rsi_period)
    out["atr_14"] = _wilder_atr(high, low, close, atr_period)
    return out


def roc_at(df: pd.DataFrame, ts: pd.Timestamp, days: int) -> float | None:
    """Close-to-close rate of change over ``days`` trading bars as-of ``ts``."""
    if df is None or df.empty or days <= 0:
        return None
    idx = df.index
    loc = int(idx.searchsorted(pd.Timestamp(ts), side="right")) - 1
    if loc < days:
        return None
    end_px = float(df["close"].iloc[loc])
    start_px = float(df["close"].iloc[loc - days])
    if start_px <= 0 or end_px <= 0:
        return None
    return (end_px / start_px) - 1.0


def evaluate_alex_entry(
    row: pd.Series,
    *,
    sector: str,
    params: AlexParams,
    spy_roc: float | None,
    shy_roc: float | None,
    in_cooldown: bool,
) -> tuple[bool, str]:
    """Return (ok, reason). reason is empty on pass."""
    if str(sector) == params.excluded_sector:
        return False, "it_excluded"
    if in_cooldown:
        return False, "cooldown"
    if spy_roc is None or spy_roc <= params.spy_roc_min:
        return False, "spy_roc_guard"
    if str(sector) in RATE_HIKE_SECTORS and (shy_roc is None or shy_roc <= params.shy_roc_min):
        return False, "shy_roc_guard"
    rsi = _safe_float(row.get("rsi_14"))
    sma = _safe_float(row.get("sma_20"))
    close = _safe_float(row.get("close"))
    atr = _safe_float(row.get("atr_14"))
    if rsi is None or sma is None or close is None or atr is None:
        return False, "indicators_missing"
    if close <= 0 or sma <= 0 or atr <= 0:
        return False, "invalid_price"
    if rsi > params.rsi_entry_max:
        return False, "rsi_not_oversold"
    drop = (sma - close) / sma
    if drop < params.sma_drop_min:
        return False, "sma_drop_short"
    return True, ""


def stop_and_target(entry_price: float, atr: float, params: AlexParams) -> tuple[float, float]:
    stop = entry_price - (params.atr_mult * atr)
    risk = entry_price - stop
    target = entry_price + (risk * params.reward_ratio * params.target_scale)
    return stop, target


def evaluate_alex_bar_exit(
    row: pd.Series,
    *,
    entry_idx: int,
    idx: int,
    stop: float,
    target: float,
    params: AlexParams,
    include_time_exit: bool,
) -> tuple[float, str] | None:
    """Same-bar stop+target resolves to stop (fail-closed)."""
    low = float(row["low"])
    high = float(row["high"])
    close = float(row["close"])
    if low <= stop:
        return (stop if stop > 0 else low, "stop")
    if high >= target:
        return (target, "target")
    rsi = _safe_float(row.get("rsi_14"))
    if rsi is not None and rsi >= params.rsi_exit:
        return (close, "rsi_exit")
    if include_time_exit and (idx - entry_idx) >= params.max_hold_days:
        return (close, "time_exit")
    return None


def simulate_alex_exit(
    df: pd.DataFrame,
    entry_idx: int,
    *,
    entry_price: float,
    atr: float,
    params: AlexParams,
    include_entry_bar: bool = False,
) -> dict[str, Any]:
    """Walk bars after fill. Same-bar stop+target resolves to stop (fail-closed)."""
    stop, target = stop_and_target(entry_price, atr, params)
    last = min(len(df) - 1, entry_idx + params.max_hold_days)
    risk = entry_price - stop
    if last < entry_idx or entry_price <= 0 or risk <= 0:
        px = float(df["close"].iloc[min(entry_idx, len(df) - 1)])
        return _exit_payload(df, entry_idx, entry_idx, entry_price, px, "invalid_entry", stop, target)
    start = entry_idx if include_entry_bar else entry_idx + 1
    if start > last:
        px = float(df["close"].iloc[min(entry_idx, len(df) - 1)])
        return _exit_payload(df, entry_idx, entry_idx, entry_price, px, "invalid_entry", stop, target)
    for idx in range(start, last + 1):
        hit = evaluate_alex_bar_exit(
            df.iloc[idx],
            entry_idx=entry_idx,
            idx=idx,
            stop=stop,
            target=target,
            params=params,
            include_time_exit=idx > entry_idx,
        )
        if hit is not None:
            fill, reason = hit
            return _exit_payload(df, entry_idx, idx, entry_price, fill, reason, stop, target)
    row = df.iloc[last]
    return _exit_payload(df, entry_idx, last, entry_price, float(row["close"]), "eod", stop, target)


def run_alex_backtest(
    price_data: dict[str, pd.DataFrame],
    *,
    universe: dict[str, str],
    spy: pd.DataFrame,
    shy: pd.DataFrame,
    params: AlexParams | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
) -> dict[str, Any]:
    """Replay Alex rules on preloaded daily bars (indicators added here)."""
    cfg = params or AlexParams()
    diagnostics: dict[str, Any] = {
        "signals_raw": 0,
        "filled": 0,
        "blocked_it": 0,
        "blocked_cooldown": 0,
        "blocked_spy": 0,
        "blocked_shy": 0,
        "blocked_rsi": 0,
        "blocked_sma": 0,
        "blocked_indicators": 0,
        "blocked_capacity": 0,
        "blocked_open": 0,
        "spy_blocked_days": 0,
        "shy_blocked_days": 0,
        "history_too_short": 0,
        "entry_mode": cfg.entry_mode,
        "universe_size": len(universe),
        "exits": {"stop": 0, "target": 0, "rsi_exit": 0, "time_exit": 0, "eod": 0, "invalid_entry": 0},
    }
    frames: dict[str, pd.DataFrame] = {}
    for ticker, raw in price_data.items():
        if ticker not in universe:
            continue
        if raw is None or len(raw) < MIN_BARS:
            diagnostics["history_too_short"] += 1
            continue
        frames[ticker] = add_alex_indicators(raw)
    spy_i = add_alex_indicators(spy) if spy is not None and not spy.empty else pd.DataFrame()
    shy_i = add_alex_indicators(shy) if shy is not None and not shy.empty else pd.DataFrame()

    calendar = _master_calendar(frames, spy_i)
    if start_date:
        start_ts = pd.Timestamp(start_date)
        calendar = [t for t in calendar if t >= start_ts]
    if end_date:
        end_ts = pd.Timestamp(end_date)
        calendar = [t for t in calendar if t <= end_ts]
    open_positions: dict[str, dict[str, Any]] = {}
    pending: list[dict[str, Any]] = []
    cooldown_until: dict[str, pd.Timestamp] = {}
    trades: list[dict[str, Any]] = []
    equity = float(cfg.starting_equity)
    peak = equity
    consecutive_losses = 0
    curve: list[tuple[str, float]] = []

    for ts in calendar:
        if cfg.entry_mode == "next_open":
            _fill_pending(
                pending,
                ts=ts,
                frames=frames,
                open_positions=open_positions,
                equity=equity,
                peak=peak,
                consecutive_losses=consecutive_losses,
                params=cfg,
                diagnostics=diagnostics,
            )
            pending.clear()

        _process_exits(
            open_positions,
            ts=ts,
            frames=frames,
            trades=trades,
            cooldown_until=cooldown_until,
            params=cfg,
            diagnostics=diagnostics,
        )
        equity, peak, consecutive_losses = _mark_equity(trades, cfg.starting_equity)
        curve.append((pd.Timestamp(ts).strftime("%Y-%m-%d"), equity))

        spy_roc = roc_at(spy_i, ts, cfg.spy_roc_days)
        shy_roc = roc_at(shy_i, ts, cfg.shy_roc_days)
        if spy_roc is None or spy_roc <= cfg.spy_roc_min:
            diagnostics["spy_blocked_days"] += 1
        if shy_roc is None or shy_roc <= cfg.shy_roc_min:
            diagnostics["shy_blocked_days"] += 1

        day_signals: list[dict[str, Any]] = []
        for ticker, df in frames.items():
            if ts not in df.index:
                continue
            row = df.loc[ts]
            if isinstance(row, pd.DataFrame):
                row = row.iloc[-1]
            loc = int(df.index.get_loc(ts))
            if isinstance(loc, slice):
                loc = loc.stop - 1
            sector = universe.get(ticker, "")
            in_cd = ticker in cooldown_until and ts <= cooldown_until[ticker]
            ok, reason = evaluate_alex_entry(
                row,
                sector=sector,
                params=cfg,
                spy_roc=spy_roc,
                shy_roc=shy_roc,
                in_cooldown=bool(in_cd),
            )
            if ticker in open_positions:
                if ok:
                    diagnostics["blocked_open"] += 1
                continue
            if not ok:
                _count_block(diagnostics, reason)
                continue
            diagnostics["signals_raw"] += 1
            rsi = float(row["rsi_14"])
            sma = float(row["sma_20"])
            close = float(row["close"])
            atr = float(row["atr_14"])
            drop = (sma - close) / sma
            day_signals.append(
                {
                    "ticker": ticker,
                    "sector": sector,
                    "signal_date": ts,
                    "signal_idx": loc,
                    "rsi": rsi,
                    "sma_drop": drop,
                    "atr": atr,
                    "close": close,
                    "filled": False,
                }
            )
        day_signals.sort(key=lambda s: (s["rsi"], -s["sma_drop"], s["ticker"]))

        if cfg.entry_mode == "close":
            _fill_signals_same_close(
                day_signals,
                ts=ts,
                frames=frames,
                open_positions=open_positions,
                equity=equity,
                peak=peak,
                consecutive_losses=consecutive_losses,
                params=cfg,
                diagnostics=diagnostics,
            )
        else:
            pending.extend(day_signals)

    if open_positions:
        last_ts = calendar[-1] if calendar else None
        if last_ts is not None:
            _force_close_open(
                open_positions,
                ts=last_ts,
                frames=frames,
                trades=trades,
                cooldown_until=cooldown_until,
                params=cfg,
                diagnostics=diagnostics,
            )

    equity, peak, consecutive_losses = _mark_equity(trades, cfg.starting_equity)
    return _summarize(trades, diagnostics, cfg, equity, peak, curve)


def _wilder_rsi(close: pd.Series, period: int) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0.0, float("nan"))
    rsi = 100.0 - (100.0 / (1.0 + rs))
    return rsi.mask(avg_loss <= 0, 100.0).astype(float)


def _wilder_atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int) -> pd.Series:
    prev_close = close.shift(1)
    tr = pd.concat(
        [(high - low).abs(), (high - prev_close).abs(), (low - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    return tr.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()


def _safe_float(value: Any) -> float | None:
    try:
        if value is None or (isinstance(value, float) and math.isnan(value)):
            return None
        out = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(out) or math.isinf(out):
        return None
    return out


def _exit_payload(
    df: pd.DataFrame,
    entry_idx: int,
    exit_idx: int,
    entry_price: float,
    exit_price: float,
    reason: str,
    stop: float,
    target: float,
) -> dict[str, Any]:
    entry_date = df.index[entry_idx]
    exit_date = df.index[exit_idx]
    stop_pct = (entry_price - stop) / entry_price if entry_price > 0 else 0.0
    gross = (exit_price - entry_price) / entry_price if entry_price > 0 else 0.0
    return {
        "entry_idx": int(entry_idx),
        "exit_idx": int(exit_idx),
        "entry_date": pd.Timestamp(entry_date),
        "exit_date": pd.Timestamp(exit_date),
        "entry_price": float(entry_price),
        "exit_price": float(exit_price),
        "exit_reason": reason,
        "stop": float(stop),
        "target": float(target),
        "stop_pct": float(stop_pct),
        "gross_return": float(gross),
        "bars_held": int(exit_idx - entry_idx),
    }


def _master_calendar(frames: dict[str, pd.DataFrame], spy: pd.DataFrame) -> list[pd.Timestamp]:
    if spy is not None and not spy.empty:
        return [pd.Timestamp(t) for t in spy.index]
    dates: set[pd.Timestamp] = set()
    for df in frames.values():
        dates.update(pd.Timestamp(t) for t in df.index)
    return sorted(dates)


def _count_block(diagnostics: dict[str, Any], reason: str) -> None:
    mapping = {
        "it_excluded": "blocked_it",
        "cooldown": "blocked_cooldown",
        "spy_roc_guard": "blocked_spy",
        "shy_roc_guard": "blocked_shy",
        "rsi_not_oversold": "blocked_rsi",
        "sma_drop_short": "blocked_sma",
        "indicators_missing": "blocked_indicators",
        "invalid_price": "blocked_indicators",
    }
    key = mapping.get(reason)
    if key:
        diagnostics[key] = int(diagnostics[key]) + 1


def _risk_shares(
    *, equity: float, peak: float, consecutive_losses: int, stop_distance: float, params: AlexParams
) -> int:
    if equity <= 0 or stop_distance <= 0:
        return 0
    dd = 0.0 if peak <= 0 else max(0.0, 1.0 - (equity / peak))
    mult = size_multiplier(consecutive_losses=consecutive_losses, drawdown_pct=dd)
    risk_dollars = equity * params.risk_per_trade_pct * mult
    shares = math.floor(risk_dollars / stop_distance)
    return max(0, int(shares))


def _fill_pending(
    pending: list[dict[str, Any]],
    *,
    ts: pd.Timestamp,
    frames: dict[str, pd.DataFrame],
    open_positions: dict[str, dict[str, Any]],
    equity: float,
    peak: float,
    consecutive_losses: int,
    params: AlexParams,
    diagnostics: dict[str, Any],
) -> None:
    slots = params.max_positions - len(open_positions)
    if slots <= 0:
        diagnostics["blocked_capacity"] += len(pending)
        return
    filled = 0
    available = float(equity)
    for sig in pending:
        if filled >= slots:
            diagnostics["blocked_capacity"] += 1
            continue
        ticker = sig["ticker"]
        if ticker in open_positions:
            diagnostics["blocked_open"] += 1
            continue
        df = frames.get(ticker)
        if df is None or ts not in df.index:
            continue
        loc = int(df.index.get_loc(ts))
        if isinstance(loc, slice):
            loc = loc.start
        open_px = float(df["open"].iloc[loc])
        if open_px <= 0:
            continue
        fill_px = open_px * (1.0 + params.entry_slippage_pct)
        atr = float(sig["atr"])
        stop_distance = params.atr_mult * atr
        qty = _risk_shares(
            equity=equity,
            peak=peak,
            consecutive_losses=consecutive_losses,
            stop_distance=stop_distance,
            params=params,
        )
        if qty <= 0 or fill_px * qty > available:
            max_qty = math.floor(available / fill_px) if fill_px > 0 else 0
            qty = min(qty, max_qty)
        if qty <= 0:
            continue
        open_positions[ticker] = {
            **sig,
            "entry_idx": loc,
            "entry_date": ts,
            "entry_price": fill_px,
            "qty": qty,
            "atr": atr,
        }
        sig["filled"] = True
        filled += 1
        available -= fill_px * qty
        diagnostics["filled"] += 1


def _fill_signals_same_close(
    signals: list[dict[str, Any]],
    *,
    ts: pd.Timestamp,
    frames: dict[str, pd.DataFrame],
    open_positions: dict[str, dict[str, Any]],
    equity: float,
    peak: float,
    consecutive_losses: int,
    params: AlexParams,
    diagnostics: dict[str, Any],
) -> None:
    slots = params.max_positions - len(open_positions)
    if slots <= 0:
        diagnostics["blocked_capacity"] += len(signals)
        return
    filled = 0
    for sig in signals:
        if filled >= slots:
            diagnostics["blocked_capacity"] += 1
            continue
        ticker = sig["ticker"]
        loc = int(sig["signal_idx"])
        fill_px = float(sig["close"]) * (1.0 + params.entry_slippage_pct)
        atr = float(sig["atr"])
        qty = _risk_shares(
            equity=equity,
            peak=peak,
            consecutive_losses=consecutive_losses,
            stop_distance=params.atr_mult * atr,
            params=params,
        )
        if qty <= 0:
            continue
        open_positions[ticker] = {
            **sig,
            "entry_idx": loc,
            "entry_date": ts,
            "entry_price": fill_px,
            "qty": qty,
            "atr": atr,
        }
        filled += 1
        diagnostics["filled"] += 1


def _process_exits(
    open_positions: dict[str, dict[str, Any]],
    *,
    ts: pd.Timestamp,
    frames: dict[str, pd.DataFrame],
    trades: list[dict[str, Any]],
    cooldown_until: dict[str, pd.Timestamp],
    params: AlexParams,
    diagnostics: dict[str, Any],
) -> None:
    closed: list[str] = []
    for ticker, pos in open_positions.items():
        df = frames.get(ticker)
        if df is None or ts not in df.index:
            continue
        loc = int(df.index.get_loc(ts))
        if isinstance(loc, slice):
            loc = loc.stop - 1
        entry_idx = int(pos["entry_idx"])
        is_entry_bar = loc == entry_idx
        if loc < entry_idx:
            continue
        if is_entry_bar and params.entry_mode != "next_open":
            continue
        stop, target = stop_and_target(float(pos["entry_price"]), float(pos["atr"]), params)
        hit = evaluate_alex_bar_exit(
            df.iloc[loc],
            entry_idx=entry_idx,
            idx=loc,
            stop=stop,
            target=target,
            params=params,
            include_time_exit=not is_entry_bar,
        )
        if hit is None:
            continue
        fill, reason = hit
        result = _exit_payload(
            df,
            entry_idx,
            loc,
            float(pos["entry_price"]),
            fill,
            reason,
            stop,
            target,
        )
        trade = _finalize_trade(ticker, pos, result, params)
        trades.append(trade)
        diagnostics["exits"][reason] = int(diagnostics["exits"].get(reason, 0)) + 1
        cooldown_until[ticker] = pd.Timestamp(ts) + pd.offsets.BDay(params.cooldown_days)
        closed.append(ticker)
    for ticker in closed:
        open_positions.pop(ticker, None)


def _force_close_open(
    open_positions: dict[str, dict[str, Any]],
    *,
    ts: pd.Timestamp,
    frames: dict[str, pd.DataFrame],
    trades: list[dict[str, Any]],
    cooldown_until: dict[str, pd.Timestamp],
    params: AlexParams,
    diagnostics: dict[str, Any],
) -> None:
    for ticker, pos in list(open_positions.items()):
        df = frames.get(ticker)
        if df is None:
            continue
        loc = len(df) - 1
        result = simulate_alex_exit(
            df,
            int(pos["entry_idx"]),
            entry_price=float(pos["entry_price"]),
            atr=float(pos["atr"]),
            params=params,
        )
        if result["exit_idx"] <= int(pos["entry_idx"]):
            px = float(df["close"].iloc[loc])
            result = _exit_payload(
                df,
                int(pos["entry_idx"]),
                loc,
                float(pos["entry_price"]),
                px,
                "eod",
                float(pos["entry_price"]) - params.atr_mult * float(pos["atr"]),
                float(pos["entry_price"])
                + params.atr_mult * float(pos["atr"]) * params.reward_ratio * params.target_scale,
            )
        trade = _finalize_trade(ticker, pos, result, params)
        trades.append(trade)
        diagnostics["exits"][str(result["exit_reason"])] = (
            int(diagnostics["exits"].get(str(result["exit_reason"]), 0)) + 1
        )
        cooldown_until[ticker] = pd.Timestamp(ts) + pd.offsets.BDay(params.cooldown_days)
        open_positions.pop(ticker, None)


def _finalize_trade(ticker: str, pos: dict[str, Any], result: dict[str, Any], params: AlexParams) -> dict[str, Any]:
    qty = int(pos["qty"])
    entry_price = float(result["entry_price"])
    exit_price = float(result["exit_price"])
    notional = entry_price * qty
    fees = 2.0 * float(params.commission_per_trade)
    pnl = (exit_price - entry_price) * qty - fees
    net_return = pnl / notional if notional > 0 else 0.0
    return {
        "ticker": ticker,
        "sector": pos.get("sector"),
        "entry_date": pd.Timestamp(result["entry_date"]).strftime("%Y-%m-%d"),
        "exit_date": pd.Timestamp(result["exit_date"]).strftime("%Y-%m-%d"),
        "entry_price": round(entry_price, 4),
        "exit_price": round(exit_price, 4),
        "qty": qty,
        "rsi": round(float(pos.get("rsi") or 0.0), 3),
        "sma_drop": round(float(pos.get("sma_drop") or 0.0), 4),
        "atr": round(float(pos.get("atr") or 0.0), 4),
        "stop_pct": round(float(result["stop_pct"]), 6),
        "exit_reason": result["exit_reason"],
        "bars_held": result["bars_held"],
        "gross_return": round(float(result["gross_return"]), 6),
        "net_return": round(float(net_return), 6),
        "pnl": round(float(pnl), 2),
        "fees": round(fees, 2),
    }


def _mark_equity(trades: list[dict[str, Any]], starting_equity: float) -> tuple[float, float, int]:
    """Replay closed-trade PnL in exit-date order for sizing state."""
    equity = float(starting_equity)
    peak = equity
    consecutive_losses = 0
    for t in sorted(trades, key=lambda x: (x["exit_date"], x["ticker"])):
        equity += float(t["pnl"])
        if equity > peak:
            peak = equity
        if float(t["pnl"]) < 0:
            consecutive_losses += 1
        else:
            consecutive_losses = 0
    return equity, peak, consecutive_losses


def _summarize(
    trades: list[dict[str, Any]],
    diagnostics: dict[str, Any],
    params: AlexParams,
    equity: float,
    peak: float,
    curve: list[tuple[str, float]],
) -> dict[str, Any]:
    pnls = [float(t["pnl"]) for t in trades]
    nets = [float(t.get("net_return", 0.0) or 0.0) for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    win_sum = sum(wins)
    loss_sum = abs(sum(losses))
    if loss_sum > 0:
        pf = win_sum / loss_sum
    elif win_sum > 0:
        pf = float("inf")
    else:
        pf = 0.0
    gp = sum(r for r in nets if r > 0)
    gl = abs(sum(r for r in nets if r <= 0))
    if gl > 0:
        pf_net = gp / gl
    elif gp > 0:
        pf_net = float("inf")
    else:
        pf_net = 0.0
    wins_net = sum(1 for r in nets if r > 0)
    eq = pd.Series([v for _d, v in curve] or [params.starting_equity])
    dd = (eq / eq.cummax()) - 1.0
    max_dd = float(dd.min()) if not dd.empty else 0.0
    start = params.starting_equity
    total_return = (equity / start) - 1.0 if start > 0 else 0.0
    years = None
    if len(curve) >= 2:
        d0 = pd.Timestamp(curve[0][0])
        d1 = pd.Timestamp(curve[-1][0])
        years = max((d1 - d0).days / 365.25, 1.0 / 365.25)
    cagr = ((equity / start) ** (1.0 / years) - 1.0) if years and start > 0 and equity > 0 else 0.0
    return {
        "strategy": "alex",
        "source": "swing_edge_v10",
        "params": {
            "entry_mode": params.entry_mode,
            "starting_equity": params.starting_equity,
            "max_positions": params.max_positions,
            "risk_per_trade_pct": params.risk_per_trade_pct,
            "rsi_entry_max": params.rsi_entry_max,
            "sma_drop_min": params.sma_drop_min,
            "max_hold_days": params.max_hold_days,
        },
        "total_trades": len(trades),
        "win_rate": round(len(wins) / len(trades), 4) if trades else 0.0,
        "win_rate_net": round(100.0 * wins_net / len(trades), 2) if trades else 0.0,
        "profit_factor": round(float(pf), 4) if math.isfinite(pf) else "inf",
        "profit_factor_net": round(float(pf_net), 4) if math.isfinite(pf_net) else "inf",
        "total_pnl": round(sum(pnls), 2),
        "ending_equity": round(float(equity), 2),
        "total_return_pct": round(100.0 * total_return, 4),
        "cagr_pct": round(100.0 * cagr, 4),
        "max_drawdown_pct": round(100.0 * max_dd, 4),
        "peak_equity": round(float(peak), 2),
        "diagnostics": diagnostics,
        "trades": trades,
        "equity_curve": [{"date": d, "equity": round(v, 2)} for d, v in curve[:: max(1, len(curve) // 400)]],
    }
