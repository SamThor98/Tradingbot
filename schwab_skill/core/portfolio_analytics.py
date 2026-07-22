"""Portfolio analytics math for live holdings and resolved trade outcomes."""

from __future__ import annotations

import math
import re
from datetime import datetime
from typing import Any, Iterable

import pandas as pd


def _finite(value: float | None, *, digits: int = 6) -> float | None:
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(f):
        return None
    return round(f, digits)


def _date(value: Any) -> pd.Timestamp | None:
    try:
        ts = pd.Timestamp(value)
    except Exception:
        return None
    if pd.isna(ts):
        return None
    return ts.normalize()


_OCC_OPTION_RE = re.compile(r"^[A-Z.\/]{1,6}\s*\d{6}[CP]\d{8}$")


def is_option_symbol(symbol: str) -> bool:
    """True when ``symbol`` is an OCC-style option contract (e.g.
    ``MXCT  270115C00002500``). Option positions must not feed equity
    return analytics: their short, discontinuous price history collapses
    the aligned return matrix and their vol dynamics are not comparable."""
    return bool(_OCC_OPTION_RE.match(str(symbol or "").strip().upper()))


def daily_returns_from_prices(df: pd.DataFrame, *, price_col: str = "close") -> pd.Series:
    """Return simple daily returns from an OHLCV frame."""
    if df is None or df.empty or price_col not in df.columns:
        return pd.Series(dtype=float)
    prices = pd.to_numeric(df[price_col], errors="coerce").dropna()
    prices = prices[prices > 0]
    if prices.empty:
        return pd.Series(dtype=float)
    out = prices.sort_index().pct_change().dropna()
    out.name = "return"
    return out.astype(float)


def normalize_weights(position_weights: dict[str, float], *, renormalize: bool = True) -> dict[str, float]:
    """Normalize percent or fractional weights into a ticker -> fraction map."""
    cleaned: dict[str, float] = {}
    for ticker, raw in (position_weights or {}).items():
        symbol = str(ticker or "").upper().strip()
        if not symbol:
            continue
        try:
            value = float(raw)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(value) or value == 0:
            continue
        cleaned[symbol] = value / 100.0 if abs(value) > 1.5 else value

    if not renormalize:
        return cleaned
    total = sum(abs(v) for v in cleaned.values())
    if total <= 0:
        return {}
    return {ticker: weight / total for ticker, weight in cleaned.items()}


def weighted_portfolio_returns(
    position_weights: dict[str, float],
    ticker_returns_df: pd.DataFrame,
    *,
    renormalize: bool = True,
) -> pd.Series:
    """Combine aligned ticker return columns into a weighted portfolio return series."""
    if ticker_returns_df is None or ticker_returns_df.empty:
        return pd.Series(dtype=float)
    weights = normalize_weights(position_weights, renormalize=renormalize)
    cols = [c for c in ticker_returns_df.columns if str(c).upper() in weights]
    if not cols:
        return pd.Series(dtype=float)
    aligned = ticker_returns_df[cols].apply(pd.to_numeric, errors="coerce").dropna(how="any")
    if aligned.empty:
        return pd.Series(dtype=float)
    weight_series = pd.Series({c: weights[str(c).upper()] for c in cols}, dtype=float)
    out = aligned.mul(weight_series, axis=1).sum(axis=1)
    out.name = "portfolio_return"
    return out.astype(float)


def ownership_weighted_portfolio_returns(
    position_weights: dict[str, float],
    ticker_returns_df: pd.DataFrame,
    ownership_starts: dict[str, Any],
    *,
    cash_weight: float = 0.0,
) -> pd.Series:
    """Weight returns by day using only names owned on that calendar day.

    Pre-ownership days are excluded for each ticker. Among names held that day,
    relative equity weights are renormalized; ``cash_weight`` (fraction of total
    equity) is applied as a constant zero-return drag.
    """
    if ticker_returns_df is None or ticker_returns_df.empty:
        return pd.Series(dtype=float)
    weights = normalize_weights(position_weights, renormalize=True)
    starts: dict[str, pd.Timestamp] = {}
    for ticker, raw in (ownership_starts or {}).items():
        symbol = str(ticker or "").upper().strip()
        start = _date(raw)
        if symbol and start is not None:
            starts[symbol] = start
    if not weights or not starts:
        return pd.Series(dtype=float)

    try:
        cash_frac = max(0.0, min(1.0, float(cash_weight)))
    except (TypeError, ValueError):
        cash_frac = 0.0
    stock_frac = 1.0 - cash_frac

    cols = [c for c in ticker_returns_df.columns if str(c).upper() in weights and str(c).upper() in starts]
    if not cols:
        return pd.Series(dtype=float)
    frame = ticker_returns_df[cols].apply(pd.to_numeric, errors="coerce")
    if frame.empty:
        return pd.Series(dtype=float)

    daily: list[tuple[pd.Timestamp, float]] = []
    for ts, row in frame.iterrows():
        day = pd.Timestamp(ts).normalize()
        active: list[tuple[str, float, float]] = []  # (col, weight, ret)
        for col in cols:
            symbol = str(col).upper()
            if day < starts[symbol]:
                continue
            ret = row[col]
            try:
                ret_f = float(ret)
            except (TypeError, ValueError):
                continue
            if not math.isfinite(ret_f):
                continue
            active.append((col, weights[symbol], ret_f))
        if not active:
            continue
        total_w = sum(w for _, w, _ in active)
        if total_w <= 0:
            continue
        day_ret = sum((w / total_w) * ret for _, w, ret in active)
        daily.append((day, stock_frac * day_ret))

    if not daily:
        return pd.Series(dtype=float)
    out = pd.Series({d: r for d, r in daily}, dtype=float)
    out.name = "portfolio_return"
    return out.sort_index()


def annualized_variance(returns: pd.Series, *, periods: int = 252) -> float | None:
    clean = pd.to_numeric(returns, errors="coerce").dropna()
    if len(clean) < 2:
        return None
    return _finite(float(clean.var(ddof=1)) * periods)


def annualized_volatility(returns: pd.Series, *, periods: int = 252) -> float | None:
    var = annualized_variance(returns, periods=periods)
    if var is None:
        return None
    return _finite(math.sqrt(var) * 100.0, digits=4)


def sharpe_ratio(returns: pd.Series, *, rf: float = 0.0, periods: int = 252) -> float | None:
    clean = pd.to_numeric(returns, errors="coerce").dropna()
    if len(clean) < 2:
        return None
    excess = clean - (rf / periods)
    std = float(excess.std(ddof=1))
    if std <= 0:
        return None
    return _finite(float(excess.mean()) / std * math.sqrt(periods), digits=4)


def sortino_ratio(returns: pd.Series, *, rf: float = 0.0, periods: int = 252) -> float | None:
    clean = pd.to_numeric(returns, errors="coerce").dropna()
    if len(clean) < 2:
        return None
    excess = clean - (rf / periods)
    downside = excess[excess < 0]
    if len(downside) < 2:
        return None
    downside_std = float(downside.std(ddof=1))
    if downside_std <= 0:
        return None
    return _finite(float(excess.mean()) / downside_std * math.sqrt(periods), digits=4)


def beta_vs_benchmark(portfolio_returns: pd.Series, benchmark_returns: pd.Series) -> float | None:
    joined = pd.concat(
        [
            pd.to_numeric(portfolio_returns, errors="coerce").rename("portfolio"),
            pd.to_numeric(benchmark_returns, errors="coerce").rename("benchmark"),
        ],
        axis=1,
    ).dropna()
    if len(joined) < 2:
        return None
    variance = float(joined["benchmark"].var(ddof=1))
    if variance <= 0:
        return None
    covariance = float(joined["portfolio"].cov(joined["benchmark"]))
    return _finite(covariance / variance, digits=4)


def correlation_summary(
    ticker_returns_df: pd.DataFrame,
    *,
    threshold: float | None = None,
) -> dict[str, Any]:
    clean = ticker_returns_df.apply(pd.to_numeric, errors="coerce").dropna(how="any")
    if clean.empty or len(clean.columns) < 2:
        return {"matrix": {}, "max_pair": None, "avg_pair_corr": None, "threshold": threshold, "breaches": []}

    corr = clean.corr()
    matrix = {
        str(row): {str(col): round(float(corr.loc[row, col]), 4) for col in corr.columns if pd.notna(corr.loc[row, col])}
        for row in corr.index
    }
    pairs: list[tuple[str, str, float]] = []
    cols = [str(c) for c in corr.columns]
    for idx, left in enumerate(cols):
        for right in cols[idx + 1 :]:
            value = corr.loc[left, right]
            if pd.notna(value):
                pairs.append((left, right, float(value)))
    if not pairs:
        return {"matrix": matrix, "max_pair": None, "avg_pair_corr": None, "threshold": threshold, "breaches": []}

    max_pair = max(pairs, key=lambda p: abs(p[2]))
    avg_pair_corr = sum(p[2] for p in pairs) / len(pairs)
    breaches = [
        {"left": left, "right": right, "corr": round(value, 4)}
        for left, right, value in pairs
        if threshold is not None and abs(value) >= threshold
    ]
    return {
        "matrix": matrix,
        "max_pair": (max_pair[0], max_pair[1], round(max_pair[2], 4)),
        "avg_pair_corr": _finite(avg_pair_corr, digits=4),
        "threshold": threshold,
        "breaches": breaches,
    }


def drawdown_stats(equity_curve: Iterable[dict[str, Any]]) -> dict[str, Any]:
    rows = []
    for point in equity_curve or []:
        if not isinstance(point, dict):
            continue
        try:
            equity = float(point.get("equity"))
        except (TypeError, ValueError):
            continue
        if not math.isfinite(equity) or equity <= 0:
            continue
        rows.append({"date": point.get("date") or point.get("exit_date"), "equity": equity})
    if not rows:
        return {"max_drawdown_pct": None, "current_drawdown_pct": None, "total_return_pct": None, "curve": []}

    peak = rows[0]["equity"]
    start = rows[0]["equity"]
    curve: list[dict[str, Any]] = []
    max_dd = 0.0
    current_dd = 0.0
    for row in rows:
        equity = row["equity"]
        peak = max(peak, equity)
        current_dd = (equity / peak - 1.0) * 100.0 if peak > 0 else 0.0
        max_dd = min(max_dd, current_dd)
        curve.append({"date": row.get("date"), "equity": round(equity, 2), "drawdown_pct": round(current_dd, 4)})

    total_return = (rows[-1]["equity"] / start - 1.0) * 100.0 if start > 0 else None
    return {
        "max_drawdown_pct": _finite(max_dd, digits=4),
        "current_drawdown_pct": _finite(current_dd, digits=4),
        "total_return_pct": _finite(total_return, digits=4),
        "curve": curve,
    }


def _packet_outcome(packet: dict[str, Any]) -> dict[str, Any]:
    out = packet.get("outcome") if isinstance(packet, dict) else None
    return out if isinstance(out, dict) else {}


def _trade_return(raw: dict[str, Any]) -> float | None:
    outcome = _packet_outcome(raw)
    value = (
        outcome.get("realized_return_pct")
        if outcome
        else raw.get("realized_return_pct", raw.get("net_return", raw.get("return")))
    )
    try:
        ret = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(ret):
        return None
    return ret / 100.0 if abs(ret) > 1.5 else ret


def _era_for_date(ts: pd.Timestamp | None) -> str:
    try:
        from scripts.phase2_common import ERA_BOUNDS

        if ts is not None:
            for era, (start, end) in ERA_BOUNDS.items():
                start_ts = pd.Timestamp(start)
                end_ts = pd.Timestamp(end) if end else None
                if ts >= start_ts and (end_ts is None or ts <= end_ts):
                    return era
    except Exception:
        pass
    return "unknown"


def _trades_from_dicts(rows: Iterable[dict[str, Any]]) -> list[Any]:
    from scripts.phase2_common import Trade

    trades: list[Any] = []
    for raw in rows or []:
        if not isinstance(raw, dict):
            continue
        net_ret = _trade_return(raw)
        if net_ret is None:
            continue
        created = _date(raw.get("created_at") or raw.get("entry_date"))
        horizon = _packet_outcome(raw).get("horizon_days") or raw.get("horizon_days") or 0
        try:
            horizon_days = max(int(horizon or 0), 0)
        except (TypeError, ValueError):
            horizon_days = 0
        entry = created or pd.Timestamp(datetime.utcnow()).normalize()
        exit_ = entry + pd.Timedelta(days=horizon_days)
        trades.append(
            Trade(
                era=str(raw.get("era") or _era_for_date(entry)),
                entry_date=entry,
                exit_date=exit_,
                ret=net_ret,
                net_ret=net_ret,
                stop_pct=float(raw.get("stop_pct") or 0.0),
                ticker=raw.get("ticker"),
            )
        )
    return trades


def _jsonify_pf(value: float | None) -> float | str | None:
    if value is None:
        return None
    if math.isinf(value):
        return "inf"
    return round(float(value), 6)


def trade_performance_pack(rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Compute closed-trade stats from packet/trade dictionaries."""
    from scripts.phase2_common import (
        expectancy,
        max_drawdown_pct,
        per_era_stats,
        profit_factor,
        total_return_pct,
        win_rate,
    )

    trades = _trades_from_dicts(rows)
    returns = pd.Series([t.net_ret for t in trades], dtype=float)
    exp = expectancy(trades)
    return {
        "source": "decision_packets",
        "trades": len(trades),
        "profit_factor": _jsonify_pf(profit_factor(trades)),
        "win_rate": _finite(win_rate(trades), digits=4),
        "expectancy_pct": _finite(exp * 100.0 if exp is not None else None, digits=4),
        "sharpe": sharpe_ratio(returns),
        "max_drawdown_pct": _finite(max_drawdown_pct(trades), digits=4) if trades else None,
        "total_return_pct": _finite(total_return_pct(trades), digits=4) if trades else None,
        "per_era": [e.to_dict() for e in per_era_stats(trades)] if trades else [],
    }
