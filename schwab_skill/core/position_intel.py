"""Position Intelligence analytics — conviction, volatility, and options tables.

Pure, offline-testable functions (fetchers injected, matching
``core/options_scoring.py`` style). Feeds ``GET /api/position-intel``:

- Conviction Scorecard: six -1/0/+1 indicator votes -> composite in [-2, +2].
- Volatility Signals: HV20 vs GARCH(1,1) forecast (EWMA fallback), expansion %.
- Options Setup: long call nearest 0.40 delta in a ~2-week window.
- Covered Calls: OTM call 0.15-0.40 delta maximizing annualized yield.

Never fabricates values: missing inputs produce ``None`` cells and a
``data_quality`` note, not synthetic numbers.
"""

from __future__ import annotations

import logging
import math
from typing import Any

import numpy as np
import pandas as pd

LOG = logging.getLogger(__name__)

TRADING_DAYS = 252
EWMA_LAMBDA = 0.94

# Conviction thresholds (accepted spec).
RSI_BULL, RSI_BEAR = 55.0, 45.0
BB_BULL, BB_BEAR = 0.8, 0.2
RANGE_BULL, RANGE_BEAR = 0.7, 0.3
VOL_BULL_RATIO, VOL_BEAR_RATIO = 1.1, 0.9
SIGNAL_BUY, SIGNAL_NEUTRAL_LO, SIGNAL_SELL = 1.0, -0.5, -1.0

# Volatility classes.
EXPANSION_SIGNAL_PCT, EXPANSION_BASE_PCT = 25.0, 10.0

# Options selection.
LONG_CALL_TARGET_DELTA = 0.40
LONG_CALL_DTE_MIN, LONG_CALL_DTE_MAX = 7, 21
LONG_CALL_DTE_MAX_FALLBACK = 35
COVERED_DTE_MIN, COVERED_DTE_MAX = 7, 30
COVERED_DELTA_MIN, COVERED_DELTA_MAX = 0.15, 0.40
MIN_OPEN_INTEREST = 50
MAX_SPREAD_PCT = 60.0


def _f(value: Any) -> float | None:
    try:
        if value is None:
            return None
        out = float(value)
        return out if math.isfinite(out) else None
    except (TypeError, ValueError):
        return None


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


# --------------------------------------------------------------------------- #
# Indicators + conviction scorecard
# --------------------------------------------------------------------------- #
def _rsi14(closes: pd.Series) -> float | None:
    if len(closes) < 15:
        return None
    delta = closes.diff()
    gain = delta.clip(lower=0.0).ewm(alpha=1 / 14, min_periods=14).mean()
    loss = (-delta.clip(upper=0.0)).ewm(alpha=1 / 14, min_periods=14).mean()
    last_gain, last_loss = float(gain.iloc[-1]), float(loss.iloc[-1])
    if last_loss <= 0:
        return 100.0
    rs = last_gain / last_loss
    return 100.0 - 100.0 / (1.0 + rs)


def _macd_state(closes: pd.Series) -> int | None:
    """+1 when MACD line above signal, -1 below, None on short history."""
    if len(closes) < 35:
        return None
    ema12 = closes.ewm(span=12, min_periods=12).mean()
    ema26 = closes.ewm(span=26, min_periods=26).mean()
    macd = ema12 - ema26
    signal = macd.ewm(span=9, min_periods=9).mean()
    diff = float(macd.iloc[-1]) - float(signal.iloc[-1])
    return 1 if diff > 0 else (-1 if diff < 0 else 0)


def _trend_vote(closes: pd.Series) -> int | None:
    if len(closes) < 200:
        return None
    price = float(closes.iloc[-1])
    sma50 = float(closes.rolling(50).mean().iloc[-1])
    sma200 = float(closes.rolling(200).mean().iloc[-1])
    if price > sma50 and sma50 > sma200:
        return 1
    if price < sma50 and sma50 < sma200:
        return -1
    return 0


def _bb_pct_b(closes: pd.Series) -> float | None:
    if len(closes) < 20:
        return None
    window = closes.tail(20)
    mid = float(window.mean())
    sd = float(window.std(ddof=0))
    if sd <= 0:
        return None
    upper, lower = mid + 2 * sd, mid - 2 * sd
    return (float(closes.iloc[-1]) - lower) / (upper - lower)


def _volume_vote(df: pd.DataFrame) -> int | None:
    """Up-day volume vs 20d average volume."""
    if len(df) < 21 or "volume" not in df.columns:
        return None
    tail = df.tail(21)
    closes = tail["close"].astype(float)
    vols = tail["volume"].astype(float)
    up_mask = closes.diff() > 0
    up_vols = vols[up_mask]
    avg_all = float(vols.tail(20).mean())
    if avg_all <= 0 or up_vols.empty:
        return None
    ratio = float(up_vols.mean()) / avg_all
    if ratio >= VOL_BULL_RATIO:
        return 1
    if ratio <= VOL_BEAR_RATIO:
        return -1
    return 0


def _range_position(df: pd.DataFrame) -> float | None:
    if len(df) < 20:
        return None
    tail = df.tail(20)
    hi = float(tail["high"].max()) if "high" in tail.columns else float(tail["close"].max())
    lo = float(tail["low"].min()) if "low" in tail.columns else float(tail["close"].min())
    if hi <= lo:
        return None
    return (float(df["close"].iloc[-1]) - lo) / (hi - lo)


def _vote(value: float | None, bull: float, bear: float) -> int | None:
    if value is None:
        return None
    if value > bull:
        return 1
    if value < bear:
        return -1
    return 0


def _signal_label(composite: float) -> str:
    if composite >= SIGNAL_BUY:
        return "BUY"
    if composite <= SIGNAL_SELL:
        return "SELL"
    if composite <= SIGNAL_NEUTRAL_LO:
        return "LEAN BEAR"
    return "NEUTRAL"


def conviction_row(ticker: str, df: pd.DataFrame | None) -> dict[str, Any]:
    """Score one ticker's daily OHLCV frame into a conviction scorecard row."""
    row: dict[str, Any] = {
        "ticker": ticker,
        "rsi": None,
        "votes": {"rsi": None, "macd": None, "trend": None, "bb": None, "vol": None, "range": None},
        "composite": None,
        "signal": None,
    }
    if df is None or df.empty or "close" not in df.columns:
        return row
    closes = df["close"].astype(float)

    rsi = _rsi14(closes)
    row["rsi"] = round(rsi, 1) if rsi is not None else None
    votes = {
        "rsi": _vote(rsi, RSI_BULL, RSI_BEAR),
        "macd": _macd_state(closes),
        "trend": _trend_vote(closes),
        "bb": _vote(_bb_pct_b(closes), BB_BULL, BB_BEAR),
        "vol": _volume_vote(df),
        "range": _vote(_range_position(df), RANGE_BULL, RANGE_BEAR),
    }
    row["votes"] = votes
    known = [v for v in votes.values() if v is not None]
    if known:
        # Weighted sum (equal weights) scaled from [-6, +6] to [-2, +2];
        # missing votes count as 0 so partial data stays conservative.
        composite = sum(known) / 3.0
        row["composite"] = round(composite, 2)
        row["signal"] = _signal_label(composite)
    return row


# --------------------------------------------------------------------------- #
# Volatility: HV20 + GARCH(1,1) with EWMA fallback
# --------------------------------------------------------------------------- #
def _log_returns(closes: pd.Series) -> np.ndarray:
    vals = pd.to_numeric(closes, errors="coerce").dropna().to_numpy(dtype=float)
    vals = vals[vals > 0]
    if len(vals) < 2:
        return np.empty(0)
    return np.diff(np.log(vals))


def hv_annualized(returns: np.ndarray, window: int = 20) -> float | None:
    """Annualized close-to-close historical volatility over the trailing window."""
    if len(returns) < window:
        return None
    tail = returns[-window:]
    sd = float(np.std(tail, ddof=1))
    return sd * math.sqrt(TRADING_DAYS)


def _ewma_vol(returns: np.ndarray, lam: float = EWMA_LAMBDA) -> float | None:
    if len(returns) < 20:
        return None
    var = float(np.var(returns[:20], ddof=0))
    for r in returns[20:]:
        var = lam * var + (1.0 - lam) * r * r
    return math.sqrt(var) * math.sqrt(TRADING_DAYS)


def garch_forecast(returns: np.ndarray) -> dict[str, Any]:
    """Next-day annualized vol forecast via GARCH(1,1); EWMA(0.94) fallback.

    Returns {vol, method, alpha, beta, error}. ``vol`` is None only when both
    estimators lack data.
    """
    out: dict[str, Any] = {"vol": None, "method": None, "alpha": None, "beta": None, "error": None}
    if len(returns) >= 60:
        try:
            from arch import arch_model

            fit = arch_model(returns * 100.0, vol="GARCH", p=1, q=1, mean="Zero", rescale=False).fit(
                disp="off", show_warning=False
            )
            variance = float(fit.forecast(horizon=1, reindex=False).variance.iloc[-1, 0])
            if variance > 0:
                out["vol"] = math.sqrt(variance) / 100.0 * math.sqrt(TRADING_DAYS)
                out["method"] = "garch"
                out["alpha"] = round(float(fit.params.get("alpha[1]", float("nan"))), 4)
                out["beta"] = round(float(fit.params.get("beta[1]", float("nan"))), 4)
                return out
            out["error"] = "non_positive_variance"
        except ImportError:
            out["error"] = "arch_not_installed"
        except Exception as exc:
            out["error"] = f"{type(exc).__name__}: {exc}"[:120]

    ewma = _ewma_vol(returns)
    if ewma is not None:
        out["vol"] = ewma
        out["method"] = "ewma"
    return out


def _hv20_series(returns: np.ndarray) -> np.ndarray:
    """Rolling HV20 history used for the regime percentile."""
    if len(returns) < 20:
        return np.empty(0)
    series = pd.Series(returns).rolling(20).std(ddof=1).dropna().to_numpy()
    return series * math.sqrt(TRADING_DAYS)


def _regime_label(hv20: float, hv_history: np.ndarray) -> str | None:
    if len(hv_history) < 60:
        return None
    pct = float(np.mean(hv_history <= hv20))
    if pct < 0.25:
        return "LOW"
    if pct < 0.75:
        return "NORMAL"
    if pct < 0.90:
        return "HIGH"
    return "EXTREME"


def volatility_row(ticker: str, df: pd.DataFrame | None) -> dict[str, Any]:
    """HV20 vs GARCH forecast row for one ticker."""
    row: dict[str, Any] = {
        "ticker": ticker,
        "hv20": None,
        "garch": None,
        "garch_method": None,
        "garch_alpha": None,
        "garch_beta": None,
        "expansion_pct": None,
        "regime": None,
        "signal": None,
    }
    if df is None or df.empty or "close" not in df.columns:
        return row
    returns = _log_returns(df["close"])
    hv = hv_annualized(returns)
    if hv is not None:
        row["hv20"] = round(hv, 4)
        row["regime"] = _regime_label(hv, _hv20_series(returns))

    fc = garch_forecast(returns)
    if fc["vol"] is not None:
        row["garch"] = round(fc["vol"], 4)
        row["garch_method"] = fc["method"]
        row["garch_alpha"] = fc["alpha"]
        row["garch_beta"] = fc["beta"]
    if fc.get("error"):
        row["garch_error"] = fc["error"]

    if hv is not None and hv > 0 and row["garch"] is not None:
        expansion = (row["garch"] - hv) / hv * 100.0
        row["expansion_pct"] = round(expansion, 1)
        if expansion >= EXPANSION_SIGNAL_PCT:
            row["signal"] = "SIGNAL"
        elif expansion >= EXPANSION_BASE_PCT:
            row["signal"] = "BASE"
        else:
            row["signal"] = "WEAK"
    return row


# --------------------------------------------------------------------------- #
# Options: contract extraction + selection
# --------------------------------------------------------------------------- #
def _iter_calls(chain: dict[str, Any]) -> list[dict[str, Any]]:
    """Flatten Schwab callExpDateMap into contract dicts with parsed fields."""
    out: list[dict[str, Any]] = []
    call_map = chain.get("callExpDateMap") if isinstance(chain, dict) else None
    if not isinstance(call_map, dict):
        return out
    for exp_key, strikes in call_map.items():
        if not isinstance(strikes, dict):
            continue
        expiry = str(exp_key).split(":")[0]
        for strike_key, contracts in strikes.items():
            strike = _f(strike_key)
            if strike is None or not isinstance(contracts, list):
                continue
            for c in contracts:
                if not isinstance(c, dict):
                    continue
                bid, ask = _f(c.get("bid")), _f(c.get("ask"))
                dte = c.get("daysToExpiration")
                out.append(
                    {
                        "expiry": expiry,
                        "strike": strike,
                        "bid": bid,
                        "ask": ask,
                        "mid": round((bid + ask) / 2.0, 4) if bid is not None and ask is not None else None,
                        "delta": _f(c.get("delta")),
                        "iv": (_f(c.get("volatility")) or 0) / 100.0 if _f(c.get("volatility")) else None,
                        "dte": int(dte) if isinstance(dte, (int, float)) else None,
                        "open_interest": int(_f(c.get("openInterest")) or 0),
                    }
                )
    return out


def _spread_pct(c: dict[str, Any]) -> float | None:
    bid, ask, mid = c.get("bid"), c.get("ask"), c.get("mid")
    if bid is None or ask is None or not mid or mid <= 0:
        return None
    return (ask - bid) / mid * 100.0


def _liq_label(c: dict[str, Any]) -> str:
    spread = _spread_pct(c)
    oi = c.get("open_interest") or 0
    if spread is None:
        return "ILLIQUID"
    if oi < 100:
        return "LOW OI" if spread <= 25.0 else "ILLIQUID"
    if spread <= 5.0 and oi >= 500:
        return "TIGHT"
    if spread <= 10.0:
        return "OK"
    if spread <= 25.0:
        return "WIDE"
    return "ILLIQUID"


def _tradeable(c: dict[str, Any]) -> bool:
    spread = _spread_pct(c)
    return (
        c.get("mid") is not None
        and (c.get("bid") or 0) > 0
        and (c.get("open_interest") or 0) >= MIN_OPEN_INTEREST
        and spread is not None
        and spread <= MAX_SPREAD_PCT
    )


def _prob_above(spot: float, level: float, vol: float, dte: int) -> float | None:
    """Lognormal (zero-drift) probability that S_T ends above ``level``."""
    if spot <= 0 or level <= 0 or vol <= 0 or dte <= 0:
        return None
    t = dte / 365.0
    sigma_t = vol * math.sqrt(t)
    d = (math.log(spot / level) - 0.5 * sigma_t * sigma_t) / sigma_t
    return _norm_cdf(d)


def select_long_call(chain: dict[str, Any] | None, spot: float | None, garch_vol: float | None) -> dict[str, Any] | None:
    """Best-buy call: ~7-21 DTE (fallback to 35), strike closest to 0.40 delta."""
    if not chain or not spot or spot <= 0:
        return None
    calls = [c for c in _iter_calls(chain) if c.get("dte") is not None and c.get("delta")]
    pool = [c for c in calls if LONG_CALL_DTE_MIN <= c["dte"] <= LONG_CALL_DTE_MAX and _tradeable(c)]
    if not pool:
        pool = [c for c in calls if LONG_CALL_DTE_MIN <= c["dte"] <= LONG_CALL_DTE_MAX_FALLBACK and _tradeable(c)]
    if not pool:
        return None
    best = min(pool, key=lambda c: abs(abs(c["delta"]) - LONG_CALL_TARGET_DELTA))

    mid = best["mid"]
    breakeven = best["strike"] + mid
    be_pct = (breakeven - spot) / spot * 100.0
    p_win = _prob_above(spot, breakeven, garch_vol, best["dte"]) if garch_vol else None
    edge_pct = None
    if garch_vol and best.get("iv"):
        edge_pct = (garch_vol - best["iv"]) / best["iv"] * 100.0
    return {
        "expiry": best["expiry"],
        "strike": best["strike"],
        "mid": round(mid, 2),
        "dte": best["dte"],
        "delta": round(abs(best["delta"]), 2),
        "iv": round(best["iv"], 4) if best.get("iv") else None,
        "breakeven_pct": round(be_pct, 1),
        "p_win": round(p_win, 3) if p_win is not None else None,
        "edge_pct": round(edge_pct, 1) if edge_pct is not None else None,
        "liq": _liq_label(best),
    }


def select_covered_call(
    chain: dict[str, Any] | None, spot: float | None, garch_vol: float | None
) -> dict[str, Any] | None:
    """Income call: OTM, 7-30 DTE, 0.15-0.40 delta, max annualized yield."""
    if not chain or not spot or spot <= 0:
        return None
    pool = [
        c
        for c in _iter_calls(chain)
        if c.get("dte") is not None
        and COVERED_DTE_MIN <= c["dte"] <= COVERED_DTE_MAX
        and c.get("strike") is not None
        and c["strike"] > spot
        and c.get("delta")
        and COVERED_DELTA_MIN <= abs(c["delta"]) <= COVERED_DELTA_MAX
        and _tradeable(c)
    ]
    if not pool:
        return None

    def _ann_yield(c: dict[str, Any]) -> float:
        return (c["mid"] / spot) * (365.0 / c["dte"])

    best = max(pool, key=_ann_yield)
    yield_pct = best["mid"] / spot * 100.0
    p_above = _prob_above(spot, best["strike"], garch_vol, best["dte"]) if garch_vol else None
    return {
        "expiry": best["expiry"],
        "strike": best["strike"],
        "mid": round(best["mid"], 2),
        "dte": best["dte"],
        "delta": round(abs(best["delta"]), 2),
        "yield_pct": round(yield_pct, 2),
        "p_keep": round(1.0 - p_above, 3) if p_above is not None else None,
        "annualized_pct": round(yield_pct * 365.0 / best["dte"], 1),
        "liq": _liq_label(best),
    }


# --------------------------------------------------------------------------- #
# Orchestrator
# --------------------------------------------------------------------------- #
def build_position_intel(
    positions: list[dict[str, Any]],
    *,
    history_fetcher: Any,
    chain_fetcher: Any,
) -> dict[str, Any]:
    """Compute all four tables for the given open equity positions.

    ``positions`` rows follow ``webapp/_shared.build_portfolio_summary``
    (``symbol``, ``qty``, ``last``). ``history_fetcher(ticker) -> DataFrame``
    (daily OHLCV); ``chain_fetcher(ticker) -> dict | None`` (Schwab chain JSON).
    """
    from core.portfolio_analytics import is_option_symbol

    conviction: list[dict[str, Any]] = []
    volatility: list[dict[str, Any]] = []
    options_setup: list[dict[str, Any]] = []
    covered_calls: list[dict[str, Any]] = []
    data_quality: list[str] = []
    garch_params: dict[str, Any] = {}

    equity = [p for p in positions if p.get("symbol") and not is_option_symbol(str(p["symbol"]))]
    for pos in equity:
        ticker = str(pos["symbol"]).upper()
        spot = _f(pos.get("last"))

        df = None
        try:
            df = history_fetcher(ticker)
        except Exception as exc:
            LOG.warning("position intel history fetch failed for %s: %s", ticker, exc)
        if df is None or getattr(df, "empty", True):
            data_quality.append(f"{ticker}: price history unavailable — indicators skipped")

        c_row = conviction_row(ticker, df)
        conviction.append(c_row)

        v_row = volatility_row(ticker, df)
        volatility.append(v_row)
        if v_row.get("garch_method") == "ewma":
            data_quality.append(f"{ticker}: GARCH fit unavailable ({v_row.get('garch_error') or 'short history'}) — EWMA fallback")
        if v_row.get("garch_method") == "garch" and not garch_params:
            garch_params = {"alpha": v_row.get("garch_alpha"), "beta": v_row.get("garch_beta")}

        chain = None
        try:
            chain = chain_fetcher(ticker)
        except Exception as exc:
            LOG.warning("position intel chain fetch failed for %s: %s", ticker, exc)
        if not chain:
            data_quality.append(f"{ticker}: option chain unavailable — options rows skipped")

        garch_vol = v_row.get("garch")
        long_call = select_long_call(chain, spot, garch_vol)
        if long_call is not None:
            options_setup.append({"ticker": ticker, **long_call})
        elif chain:
            data_quality.append(f"{ticker}: no tradeable call near 0.40Δ in window")

        cc = select_covered_call(chain, spot, garch_vol)
        if cc is not None:
            covered_calls.append({"ticker": ticker, "shares": int(pos.get("qty") or 0), **cc})

    scored = [r["composite"] for r in conviction if r.get("composite") is not None]
    summary = {
        "positions": len(equity),
        "bullish": sum(1 for r in conviction if r.get("signal") == "BUY"),
        "bearish": sum(1 for r in conviction if r.get("signal") in ("SELL", "LEAN BEAR")),
        "neutral": sum(1 for r in conviction if r.get("signal") == "NEUTRAL"),
        "avg_composite": round(sum(scored) / len(scored), 2) if scored else None,
        "vol_signals": sum(1 for r in volatility if r.get("signal") == "SIGNAL"),
    }
    conviction.sort(key=lambda r: r.get("composite") if r.get("composite") is not None else -99, reverse=True)
    volatility.sort(key=lambda r: r.get("expansion_pct") if r.get("expansion_pct") is not None else -1e9, reverse=True)
    options_setup.sort(key=lambda r: r.get("edge_pct") if r.get("edge_pct") is not None else -1e9, reverse=True)
    covered_calls.sort(key=lambda r: r.get("annualized_pct") or 0, reverse=True)

    return {
        "conviction": conviction,
        "volatility": volatility,
        "options_setup": options_setup,
        "covered_calls": covered_calls,
        "summary": summary,
        "garch_params": garch_params,
        "data_quality": data_quality,
    }
