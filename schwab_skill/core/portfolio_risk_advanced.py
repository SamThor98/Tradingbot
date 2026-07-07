"""Advanced portfolio risk math: covariance vol, risk contribution, VaR, stress.

Pure functions (no I/O) that extend ``core.portfolio_analytics`` with
institutional-style analytics for the portfolio risk dashboard:

- covariance-based ex-ante volatility + Euler risk contribution per name
- historical / conditional (CVaR) value-at-risk and daily win rate
- effective-N diversification and structured limit-breach scanning
- historical stress scenarios (window replay when data covers the window,
  beta-scaled otherwise; hypothetical scenarios are always beta-scaled)
- single-name gap stress and FX stress by country
- parametric Monte Carlo VaR via Cholesky decomposition

All outputs are JSON-safe primitives. Missing inputs yield ``None`` fields —
never fabricated numbers.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

from core.portfolio_analytics import normalize_weights

TRADING_DAYS = 252

# Historical scenario catalog. ``market_move_pct`` is the SPY/broad-market move
# over the window; replay uses actual ticker returns when the data covers the
# window, otherwise the portfolio impact is beta-scaled off the market move.
HISTORICAL_SCENARIOS: list[dict[str, Any]] = [
    {
        "name": "COVID Crash (Feb-Mar 2020)",
        "start": "2020-02-19",
        "end": "2020-03-23",
        "market_move_pct": -33.9,
        "scenario_type": "historical",
        "description": "Rapid 33.9% selloff in 23 trading days, V-shaped recovery.",
    },
    {
        "name": "2022 Rate Shock (Jan-Oct)",
        "start": "2022-01-03",
        "end": "2022-10-12",
        "market_move_pct": -25.2,
        "scenario_type": "historical",
        "description": "Grinding 9-month selloff driven by rate hikes.",
    },
    {
        "name": "GFC 2008-09",
        "start": "2007-10-09",
        "end": "2009-03-09",
        "market_move_pct": -56.5,
        "scenario_type": "historical",
        "description": "Credit crisis, SPY -56.5% peak to trough over 17 months.",
    },
    {
        "name": "Dot-Com Bust (2000-02)",
        "start": "2000-03-24",
        "end": "2002-10-09",
        "market_move_pct": -49.1,
        "scenario_type": "historical",
        "description": "Tech bubble burst. NASDAQ -78%. Value outperformed massively.",
    },
    {
        "name": "Black Monday (Oct 1987)",
        "start": "1987-10-19",
        "end": "1987-10-19",
        "market_move_pct": -20.4,
        "scenario_type": "historical",
        "description": "Flash crash, -20.4% in single day. Mechanical, not fundamental.",
    },
    {
        "name": "Taiwan Invasion",
        "start": None,
        "end": None,
        "market_move_pct": -30.0,
        "scenario_type": "hypothetical",
        "description": "Hypothetical: China invades Taiwan. Global chip supply crisis.",
    },
    {
        "name": "Aug 2024 Yen Carry Unwind",
        "start": "2024-07-31",
        "end": "2024-08-05",
        "market_move_pct": -8.4,
        "scenario_type": "historical",
        "description": "Yen carry trade unwind, 3-day selloff. Quick recovery.",
    },
]

DEFAULT_SINGLE_NAME_GAPS = (-0.25, -0.40, -0.50)


def _finite(value: Any, *, digits: int = 4) -> float | None:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(f):
        return None
    return round(f, digits)


def _aligned_returns(weights: dict[str, float], returns_df: pd.DataFrame) -> tuple[pd.DataFrame, pd.Series]:
    """Align return columns with normalized weights; empty results on mismatch."""
    fractions = normalize_weights(weights)
    if returns_df is None or returns_df.empty or not fractions:
        return pd.DataFrame(), pd.Series(dtype=float)
    cols = [c for c in returns_df.columns if str(c).upper() in fractions]
    if not cols:
        return pd.DataFrame(), pd.Series(dtype=float)
    aligned = returns_df[cols].apply(pd.to_numeric, errors="coerce").dropna(how="any")
    if len(aligned) < 2:
        return pd.DataFrame(), pd.Series(dtype=float)
    weight_series = pd.Series({c: fractions[str(c).upper()] for c in cols}, dtype=float)
    return aligned, weight_series


def covariance_portfolio_vol(weights: dict[str, float], returns_df: pd.DataFrame) -> float | None:
    """Annualized ex-ante portfolio volatility (%) from the sample covariance matrix."""
    aligned, w = _aligned_returns(weights, returns_df)
    if aligned.empty:
        return None
    cov = aligned.cov().to_numpy() * TRADING_DAYS
    vec = w.to_numpy()
    variance = float(vec @ cov @ vec)
    if variance < 0 or not math.isfinite(variance):
        return None
    return _finite(math.sqrt(variance) * 100.0)


def risk_contribution_decomposition(
    weights: dict[str, float],
    returns_df: pd.DataFrame,
) -> list[dict[str, Any]]:
    """Euler variance decomposition: per-ticker weight, standalone vol, and % of risk.

    ``risk_contrib_pct`` sums to ~100 across tickers (signed contributions can
    be negative for diversifying names).
    """
    aligned, w = _aligned_returns(weights, returns_df)
    if aligned.empty:
        return []
    cov = aligned.cov().to_numpy() * TRADING_DAYS
    vec = w.to_numpy()
    variance = float(vec @ cov @ vec)
    if variance <= 0 or not math.isfinite(variance):
        return []
    port_vol = math.sqrt(variance)
    marginal = cov @ vec  # d(variance)/d(w)
    rows: list[dict[str, Any]] = []
    for idx, col in enumerate(aligned.columns):
        contrib_var = float(vec[idx] * marginal[idx])
        standalone_vol = math.sqrt(max(float(cov[idx, idx]), 0.0)) * 100.0
        rows.append(
            {
                "ticker": str(col).upper(),
                "weight_pct": _finite(vec[idx] * 100.0),
                "vol_ann_pct": _finite(standalone_vol, digits=2),
                # Contribution to annualized vol, in vol points then % of total.
                "risk_contrib_ann_pct": _finite(contrib_var / port_vol * 100.0, digits=4),
                "risk_contrib_pct": _finite(contrib_var / variance * 100.0, digits=4),
            }
        )
    rows.sort(key=lambda r: -(r["risk_contrib_pct"] or 0.0))
    return rows


def historical_var(returns: pd.Series, *, confidence: float = 0.95) -> float | None:
    """Historical daily VaR (%) at the given confidence (returned as a negative pct)."""
    clean = pd.to_numeric(returns, errors="coerce").dropna()
    if len(clean) < 20:
        return None
    quantile = float(np.percentile(clean.to_numpy(), (1.0 - confidence) * 100.0))
    return _finite(quantile * 100.0)


def conditional_var(returns: pd.Series, *, confidence: float = 0.95) -> float | None:
    """Conditional VaR / expected shortfall (%): mean of returns beyond the VaR cutoff."""
    clean = pd.to_numeric(returns, errors="coerce").dropna()
    if len(clean) < 20:
        return None
    cutoff = float(np.percentile(clean.to_numpy(), (1.0 - confidence) * 100.0))
    tail = clean[clean <= cutoff]
    if tail.empty:
        return None
    return _finite(float(tail.mean()) * 100.0)


def daily_win_rate(returns: pd.Series) -> float | None:
    """Percentage of days with positive portfolio return."""
    clean = pd.to_numeric(returns, errors="coerce").dropna()
    if clean.empty:
        return None
    return _finite(float((clean > 0).mean()) * 100.0, digits=2)


# Annualizing a compounded return from fewer trading days than this produces
# absurd extrapolations (e.g. +1,100% from a hot month) — suppress instead.
MIN_ANNUALIZATION_OBS = 60


def annualized_return_pct(returns: pd.Series, *, min_observations: int = MIN_ANNUALIZATION_OBS) -> float | None:
    """Geometric annualized return (%) from daily simple returns.

    Returns ``None`` (never a fabricated extrapolation) when fewer than
    ``min_observations`` daily returns are available.
    """
    clean = pd.to_numeric(returns, errors="coerce").dropna()
    if len(clean) < max(2, int(min_observations)):
        return None
    growth = float((1.0 + clean).prod())
    if growth <= 0:
        return None
    annualized = growth ** (TRADING_DAYS / len(clean)) - 1.0
    return _finite(annualized * 100.0, digits=2)


def effective_n(weights: dict[str, float]) -> float | None:
    """Diversification-equivalent number of positions: 1 / sum(w^2)."""
    fractions = normalize_weights(weights)
    if not fractions:
        return None
    hhi = sum(v * v for v in fractions.values())
    if hhi <= 0:
        return None
    return _finite(1.0 / hhi, digits=1)


def tail_risk_summary(returns: pd.Series) -> dict[str, Any]:
    """Tail statistics of the daily portfolio return distribution."""
    clean = pd.to_numeric(returns, errors="coerce").dropna()
    if len(clean) < 20:
        return {
            "var_95_pct": None,
            "var_99_pct": None,
            "cvar_95_pct": None,
            "cvar_99_pct": None,
            "worst_day_pct": None,
            "best_day_pct": None,
            "skew": None,
            "kurtosis": None,
            "observations": int(len(clean)),
        }
    return {
        "var_95_pct": historical_var(clean, confidence=0.95),
        "var_99_pct": historical_var(clean, confidence=0.99),
        "cvar_95_pct": conditional_var(clean, confidence=0.95),
        "cvar_99_pct": conditional_var(clean, confidence=0.99),
        "worst_day_pct": _finite(float(clean.min()) * 100.0),
        "best_day_pct": _finite(float(clean.max()) * 100.0),
        "skew": _finite(float(clean.skew()), digits=3),
        "kurtosis": _finite(float(clean.kurtosis()), digits=3),
        "observations": int(len(clean)),
    }


def historical_stress_scenarios(
    weights: dict[str, float],
    ticker_returns_df: pd.DataFrame | None,
    *,
    beta: float | None,
    equity: float | None,
    scenarios: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Apply the scenario catalog to the portfolio.

    Replays actual weighted returns when the return data covers the scenario
    window (rare for long lookbacks); otherwise scales the market move by
    portfolio beta. When beta is unavailable, the impact is ``None`` — never
    fabricated.
    """
    rows: list[dict[str, Any]] = []
    for scenario in scenarios or HISTORICAL_SCENARIOS:
        market_move = float(scenario["market_move_pct"])
        impact_pct: float | None = None
        method = "beta_scaled"

        start, end = scenario.get("start"), scenario.get("end")
        if (
            start
            and end
            and ticker_returns_df is not None
            and not ticker_returns_df.empty
        ):
            window = ticker_returns_df.loc[str(start) : str(end)]
            if len(window) >= 2:
                aligned, w = _aligned_returns(weights, window)
                if not aligned.empty:
                    portfolio = aligned.mul(w, axis=1).sum(axis=1)
                    impact_pct = _finite((float((1.0 + portfolio).prod()) - 1.0) * 100.0, digits=2)
                    method = "window_replay"

        if impact_pct is None and beta is not None and math.isfinite(beta):
            impact_pct = _finite(beta * market_move, digits=2)

        stressed_nav = None
        pnl = None
        if impact_pct is not None and equity and equity > 0:
            pnl = _finite(equity * impact_pct / 100.0, digits=0)
            stressed_nav = _finite(equity + (pnl or 0.0), digits=0)

        rows.append(
            {
                "scenario": scenario["name"],
                "scenario_type": scenario["scenario_type"],
                "method": method if impact_pct is not None else "unavailable",
                "market_move_pct": _finite(market_move, digits=2),
                "portfolio_impact_pct": impact_pct,
                "stressed_nav": stressed_nav,
                "pnl": pnl,
                "description": scenario["description"],
            }
        )
    return rows


def _position_weights_pct(positions: list[dict[str, Any]]) -> list[tuple[str, float]]:
    """Extract (ticker, weight_pct) pairs; fractional weights are detected on
    the whole list (max <= 1.5 means 0..1 fractions) so small positions don't
    get misclassified individually."""
    raw: list[tuple[str, float]] = []
    for pos in positions or []:
        ticker = str(pos.get("ticker") or pos.get("symbol") or "").upper()
        try:
            w = float(pos.get("weight_pct"))
        except (TypeError, ValueError):
            continue
        if not ticker or not math.isfinite(w) or w <= 0:
            continue
        raw.append((ticker, w))
    if not raw:
        return []
    fractional = max(w for _, w in raw) <= 1.5
    return [(t, w * 100.0 if fractional else w) for t, w in raw]


def single_name_stress(
    positions: list[dict[str, Any]],
    *,
    equity: float | None,
    gaps: tuple[float, ...] = DEFAULT_SINGLE_NAME_GAPS,
    top_n: int = 4,
) -> list[dict[str, Any]]:
    """Idiosyncratic gap-down loss table for the largest positions."""
    weighted = _position_weights_pct(positions)
    weighted.sort(key=lambda t: -t[1])

    rows: list[dict[str, Any]] = []
    for rank, (ticker, weight_pct) in enumerate(weighted[:top_n]):
        # Deeper gap set for the largest names, single gap for the rest.
        applicable = gaps if rank < max(1, top_n - 1) else gaps[:1]
        for gap in applicable:
            impact_pct = _finite(weight_pct * gap, digits=2)
            pnl = _finite(equity * (impact_pct or 0.0) / 100.0, digits=0) if equity and impact_pct is not None else None
            rows.append(
                {
                    "scenario": f"{ticker} {int(gap * 100)}%",
                    "ticker": ticker,
                    "gap_pct": _finite(gap * 100.0, digits=0),
                    "weight_pct": _finite(weight_pct, digits=2),
                    "portfolio_impact_pct": impact_pct,
                    "pnl": pnl,
                }
            )
    return rows


def fx_stress_by_country(
    positions: list[dict[str, Any]],
    country_map: dict[str, dict[str, Any]],
    *,
    equity: float | None,
    shock_map: dict[str, float],
    em_shock_pct: float = -15.0,
) -> dict[str, Any]:
    """Aggregate non-USD currency exposure and apply per-country FX shocks.

    ``country_map`` is ticker -> {"country": ISO2, "currency": str}. Tickers
    without a resolved profile are excluded and reported in ``unresolved``.
    """
    exposures: dict[str, float] = {}
    unresolved: list[str] = []
    for ticker, weight_pct in _position_weights_pct(positions):
        info = country_map.get(ticker) or {}
        country = str(info.get("country") or "").upper()
        if not country:
            unresolved.append(ticker)
            continue
        # Country of domicile drives FX/country risk even for USD-quoted ADRs.
        if country == "US":
            continue
        exposures[country] = exposures.get(country, 0.0) + weight_pct

    non_usd_weight = sum(exposures.values())
    rows: list[dict[str, Any]] = []
    scenario_impact_pct = 0.0
    for country, exposure_pct in sorted(exposures.items(), key=lambda kv: -kv[1]):
        shock = float(shock_map.get(country, em_shock_pct))
        scenario_impact_pct += exposure_pct * shock / 100.0
        rows.append(
            {
                "country": country,
                "exposure_pct": _finite(exposure_pct, digits=2),
                "fx_shock_pct": _finite(shock, digits=1),
            }
        )

    broad_em_impact_pct = non_usd_weight * em_shock_pct / 100.0
    return {
        "non_usd_weight_pct": _finite(non_usd_weight, digits=2),
        "scenario_impact_pct": _finite(scenario_impact_pct, digits=2),
        "scenario_pnl": _finite(equity * scenario_impact_pct / 100.0, digits=0) if equity else None,
        "broad_em_shock_pct": _finite(em_shock_pct, digits=1),
        "broad_em_impact_pnl": _finite(equity * broad_em_impact_pct / 100.0, digits=0) if equity else None,
        "by_country": rows,
        "unresolved_tickers": sorted(set(unresolved)),
    }


def monte_carlo_var(
    weights: dict[str, float],
    returns_df: pd.DataFrame,
    *,
    equity: float | None,
    simulations: int = 5000,
    seed: int | None = 7,
) -> dict[str, Any]:
    """Parametric Monte Carlo daily VaR via Cholesky-correlated normal draws."""
    empty = {
        "simulations": 0,
        "var_95_pct": None,
        "var_99_pct": None,
        "cvar_95_pct": None,
        "var_95_pnl": None,
        "var_99_pnl": None,
        "mean_daily_pct": None,
    }
    aligned, w = _aligned_returns(weights, returns_df)
    if aligned.empty or len(aligned) < 20:
        return empty
    mu = aligned.mean().to_numpy()
    cov = aligned.cov().to_numpy()
    # Regularize for Cholesky stability on near-singular covariances.
    jitter = 1e-12
    for _ in range(6):
        try:
            chol = np.linalg.cholesky(cov + np.eye(cov.shape[0]) * jitter)
            break
        except np.linalg.LinAlgError:
            jitter *= 100.0
    else:
        return empty

    rng = np.random.default_rng(seed)
    draws = rng.standard_normal((int(simulations), cov.shape[0]))
    sim_returns = (mu + draws @ chol.T) @ w.to_numpy()
    var95 = float(np.percentile(sim_returns, 5.0))
    var99 = float(np.percentile(sim_returns, 1.0))
    tail = sim_returns[sim_returns <= var95]
    return {
        "simulations": int(simulations),
        "var_95_pct": _finite(var95 * 100.0),
        "var_99_pct": _finite(var99 * 100.0),
        "cvar_95_pct": _finite(float(tail.mean()) * 100.0) if tail.size else None,
        "var_95_pnl": _finite(equity * var95, digits=0) if equity else None,
        "var_99_pnl": _finite(equity * var99, digits=0) if equity else None,
        "mean_daily_pct": _finite(float(sim_returns.mean()) * 100.0, digits=4),
    }


def limit_breach_scan(
    *,
    name_weights: list[dict[str, Any]],
    sector_allocation: list[dict[str, Any]],
    country_exposure: list[dict[str, Any]],
    single_name_limit_pct: float,
    sector_limit_pct: float,
    country_limit_pct: float,
) -> list[dict[str, Any]]:
    """Structured limit breaches for names, sectors, and countries."""
    breaches: list[dict[str, Any]] = []
    for row in name_weights or []:
        label = str(row.get("symbol") or row.get("ticker") or "").upper()
        pct = _finite(row.get("weight_pct"), digits=2)
        if label and pct is not None and pct > single_name_limit_pct:
            breaches.append(
                {
                    "kind": "single_name",
                    "label": label,
                    "value_pct": pct,
                    "limit_pct": single_name_limit_pct,
                    "message": f"{label} {pct}% > {single_name_limit_pct:g}% single-name limit",
                }
            )
    for row in sector_allocation or []:
        label = str(row.get("sector") or "")
        pct = _finite(row.get("weight_pct"), digits=2)
        if label and label != "Unknown" and pct is not None and pct > sector_limit_pct:
            breaches.append(
                {
                    "kind": "sector",
                    "label": label,
                    "value_pct": pct,
                    "limit_pct": sector_limit_pct,
                    "message": f"{label} {pct}% > {sector_limit_pct:g}% sector limit",
                }
            )
    for row in country_exposure or []:
        label = str(row.get("country") or "").upper()
        pct = _finite(row.get("exposure_pct"), digits=2)
        if label and label != "US" and pct is not None and pct > country_limit_pct:
            breaches.append(
                {
                    "kind": "country",
                    "label": label,
                    "value_pct": pct,
                    "limit_pct": country_limit_pct,
                    "message": f"{label} {pct}% > {country_limit_pct:g}% single-country limit",
                }
            )
    return breaches
