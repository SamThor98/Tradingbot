"""Orchestration for PM-grade portfolio analytics."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

from core.contracts.portfolio import (
    ClosedTradeMetrics,
    ConcentrationBlock,
    CorrelationSummary,
    FxStressSummary,
    HistoricalStressRow,
    LimitBreach,
    MonteCarloSummary,
    PortfolioAnalyticsPack,
    PortfolioRiskDashboardPack,
    PortfolioRiskState,
    RiskAdjustedMetrics,
    RiskContributionBlock,
    RiskContributionRow,
    RiskMetricsTable,
    SingleNameStressRow,
    StressBlock,
)
from core.contracts.provenance import Provenance
from core.portfolio_analytics import (
    annualized_variance,
    annualized_volatility,
    beta_vs_benchmark,
    correlation_summary,
    daily_returns_from_prices,
    drawdown_stats,
    sharpe_ratio,
    sortino_ratio,
    trade_performance_pack,
    weighted_portfolio_returns,
)


def _config(skill_dir: Path | None) -> tuple[int, str, float, float]:
    from config import (
        get_correlation_guard_max_pair_corr,
        get_portfolio_analytics_benchmark,
        get_portfolio_analytics_lookback_days,
        get_portfolio_analytics_risk_free_rate,
    )

    return (
        get_portfolio_analytics_lookback_days(skill_dir),
        get_portfolio_analytics_benchmark(skill_dir),
        get_portfolio_analytics_risk_free_rate(skill_dir),
        get_correlation_guard_max_pair_corr(skill_dir),
    )


def _weights_from_state(state: PortfolioRiskState) -> dict[str, float]:
    weights: dict[str, float] = {}
    equity = state.equity or 0.0
    for pos in state.positions:
        ticker = str(pos.ticker or "").upper().strip()
        if not ticker:
            continue
        if pos.weight_pct is not None:
            weights[ticker] = float(pos.weight_pct)
        elif equity and pos.market_value is not None:
            weights[ticker] = float(pos.market_value) / equity
    return weights


def _synthetic_equity_curve(returns: pd.Series, *, starting_equity: float | None) -> list[dict[str, Any]]:
    if returns.empty:
        return []
    equity = float(starting_equity or 100_000.0)
    curve: list[dict[str, Any]] = []
    for date, ret in returns.items():
        equity *= 1.0 + float(ret)
        curve.append({"date": pd.Timestamp(date).date().isoformat(), "equity": round(equity, 2)})
    return curve


def _load_closed_trade_metrics(skill_dir: Path | None, *, limit: int = 500) -> ClosedTradeMetrics:
    try:
        from core import decision_packet

        packets = decision_packet.load_packets(skill_dir=skill_dir, limit=limit)
        resolved = [
            packet
            for packet in packets
            if isinstance(packet, dict)
            and isinstance(packet.get("outcome"), dict)
            and packet["outcome"].get("realized_return_pct") is not None
        ]
        return ClosedTradeMetrics(**trade_performance_pack(resolved))
    except Exception as exc:
        return ClosedTradeMetrics(source="decision_packets", trades=0, per_era=[], profit_factor=None).model_copy(
            update={"source": f"decision_packets_unavailable:{type(exc).__name__}"}
        )


# A ticker whose usable history covers less than this fraction of the best
# ticker's history is dropped from return analytics: correlation and the
# portfolio return series inner-join on dates, so one gappy series would
# silently collapse the whole sample (observed: 27 obs from a 252d lookback).
MIN_HISTORY_COVERAGE_RATIO = 0.6


def _load_ticker_returns(
    weights: dict[str, float],
    *,
    benchmark: str,
    lookback_days: int,
    skill_dir: Path | None,
    auth: Any,
    data_quality: dict[str, Any],
) -> tuple[pd.DataFrame, pd.Series]:
    """Fetch daily history for weighted tickers + benchmark; returns (returns_df, benchmark_returns).

    Option contracts and low-coverage tickers are excluded from the return
    matrix (recorded in ``data_quality``) so a single short/gappy series
    cannot shrink the aligned observation window for every metric.
    """
    ticker_returns: dict[str, pd.Series] = {}
    if weights:
        from core.portfolio_analytics import is_option_symbol
        from market_data import get_daily_history_with_meta

        for ticker in sorted(weights):
            if is_option_symbol(ticker):
                data_quality.setdefault("excluded_options", []).append(ticker)
                continue
            df, meta = get_daily_history_with_meta(ticker, days=lookback_days, auth=auth, skill_dir=skill_dir)
            data_quality["provider_meta"][ticker] = meta
            returns = daily_returns_from_prices(df)
            if returns.empty:
                data_quality["missing_tickers"].append(ticker)
                continue
            if len(returns) < 20:
                data_quality["insufficient_history"].append(ticker)
                continue
            ticker_returns[ticker] = returns

        if ticker_returns:
            max_rows = max(len(s) for s in ticker_returns.values())
            floor = max(20, int(max_rows * MIN_HISTORY_COVERAGE_RATIO))
            low_coverage = sorted(t for t, s in ticker_returns.items() if len(s) < floor)
            for ticker in low_coverage:
                del ticker_returns[ticker]
                data_quality["insufficient_history"].append(ticker)
            if low_coverage:
                data_quality["low_coverage_dropped"] = low_coverage

        df, meta = get_daily_history_with_meta(benchmark, days=lookback_days, auth=auth, skill_dir=skill_dir)
        data_quality["provider_meta"][benchmark] = meta
        benchmark_returns = daily_returns_from_prices(df)
    else:
        benchmark_returns = pd.Series(dtype=float)
        data_quality["reason"] = "no_weighted_positions"

    returns_df = pd.DataFrame(ticker_returns).dropna(how="all")
    available_weight = sum(abs(weights.get(ticker, 0.0)) for ticker in ticker_returns)
    total_weight = sum(abs(v) for v in weights.values())
    if total_weight:
        data_quality["excluded_weight_pct"] = round(max(total_weight - available_weight, 0.0) / total_weight * 100.0, 4)
    if not returns_df.empty:
        data_quality["aligned_observations"] = int(len(returns_df.dropna(how="any")))
    return returns_df, benchmark_returns


def build_portfolio_analytics(
    state: PortfolioRiskState,
    *,
    skill_dir: Path | None = None,
    auth: Any = None,
    lookback_days: int | None = None,
    equity_curve: list[dict[str, Any]] | None = None,
    include_closed_trades: bool = True,
) -> PortfolioAnalyticsPack:
    """Build live risk metrics plus closed-trade performance diagnostics."""
    default_lookback, benchmark, risk_free_rate, corr_threshold = _config(skill_dir)
    resolved_lookback = max(20, int(lookback_days or default_lookback))
    data_quality: dict[str, Any] = {
        "missing_tickers": [],
        "insufficient_history": [],
        "provider_meta": {},
        "excluded_weight_pct": 0.0,
    }
    weights = _weights_from_state(state)
    returns_df, benchmark_returns = _load_ticker_returns(
        weights,
        benchmark=benchmark,
        lookback_days=resolved_lookback,
        skill_dir=skill_dir,
        auth=auth,
        data_quality=data_quality,
    )

    portfolio_returns = weighted_portfolio_returns(weights, returns_df) if not returns_df.empty else pd.Series(dtype=float)
    if portfolio_returns.empty:
        live = None
        corr = None
        drawdown = drawdown_stats(equity_curve or [])
    else:
        corr_payload = correlation_summary(returns_df, threshold=corr_threshold)
        corr = CorrelationSummary(**corr_payload)
        drawdown_source = equity_curve or _synthetic_equity_curve(portfolio_returns, starting_equity=state.equity)
        drawdown = drawdown_stats(drawdown_source)
        data_quality["drawdown_source"] = "snapshots" if equity_curve else "current_weight_backfill"
        live = RiskAdjustedMetrics(
            volatility_ann_pct=annualized_volatility(portfolio_returns),
            variance_ann=annualized_variance(portfolio_returns),
            sharpe=sharpe_ratio(portfolio_returns, rf=risk_free_rate),
            sortino=sortino_ratio(portfolio_returns, rf=risk_free_rate),
            beta_vs_benchmark=beta_vs_benchmark(portfolio_returns, benchmark_returns),
            benchmark=benchmark,
            max_drawdown_pct=drawdown.get("max_drawdown_pct"),
            current_drawdown_pct=drawdown.get("current_drawdown_pct"),
            total_return_pct=drawdown.get("total_return_pct"),
            observations=int(len(portfolio_returns)),
        )

    closed = _load_closed_trade_metrics(skill_dir) if include_closed_trades else None
    confidence = "low" if data_quality.get("missing_tickers") else "high"
    return PortfolioAnalyticsPack(
        live=live,
        correlation=corr,
        closed_trades=closed,
        lookback_days=resolved_lookback,
        data_quality=data_quality,
        equity_curve=drawdown.get("curve") or [],
        provenance=Provenance.computed(confidence=confidence, benchmark=benchmark),
    )


def build_portfolio_risk_dashboard(
    state: PortfolioRiskState,
    summary: dict[str, Any],
    *,
    static_risk: dict[str, Any] | None = None,
    skill_dir: Path | None = None,
    auth: Any = None,
    lookback_days: int | None = None,
    equity_curve: list[dict[str, Any]] | None = None,
) -> PortfolioRiskDashboardPack:
    """Compose the unified Risk-tab payload: metrics, correlation, risk
    contribution, concentration limits, and stress modules.

    ``summary`` is the ``build_portfolio_summary`` dict and ``static_risk``
    the ``webapp._shared.build_portfolio_risk_analytics`` dict (sector and
    day-PL context that ``PortfolioRiskState`` lacks); the route layer passes
    both so core stays free of webapp imports. All heavy fetches share one
    price pass via ``_load_ticker_returns``.
    """
    from config import (
        get_risk_fx_shock_by_country,
        get_risk_fx_shock_em_pct,
        get_risk_limit_country_pct,
        get_risk_limit_sector_pct,
        get_risk_limit_single_name_pct,
        get_risk_mc_simulations,
    )
    from core import post_fill_risk
    from core.portfolio_country_lookup import country_display_name, resolve_countries
    from core.portfolio_risk_advanced import (
        annualized_return_pct,
        covariance_portfolio_vol,
        daily_win_rate,
        effective_n,
        fx_stress_by_country,
        historical_stress_scenarios,
        historical_var,
        limit_breach_scan,
        monte_carlo_var,
        risk_contribution_decomposition,
        single_name_stress,
        tail_risk_summary,
    )

    default_lookback, benchmark, risk_free_rate, corr_threshold = _config(skill_dir)
    resolved_lookback = max(20, int(lookback_days or max(default_lookback, 252)))
    data_quality: dict[str, Any] = {
        "missing_tickers": [],
        "insufficient_history": [],
        "provider_meta": {},
        "excluded_weight_pct": 0.0,
    }
    weights = _weights_from_state(state)
    equity = state.equity or float(summary.get("total_market_value") or 0.0) or None

    returns_df, benchmark_returns = _load_ticker_returns(
        weights,
        benchmark=benchmark,
        lookback_days=resolved_lookback,
        skill_dir=skill_dir,
        auth=auth,
        data_quality=data_quality,
    )
    portfolio_returns = weighted_portfolio_returns(weights, returns_df) if not returns_df.empty else pd.Series(dtype=float)

    # --- Metrics table + correlation --------------------------------------
    metrics = None
    corr = None
    beta = None
    model_vol = None
    drawdown = drawdown_stats(equity_curve or [])
    data_quality["equity_curve_days"] = len(equity_curve or [])
    if not portfolio_returns.empty:
        corr = CorrelationSummary(**correlation_summary(returns_df, threshold=corr_threshold))
        drawdown_source = equity_curve or _synthetic_equity_curve(portfolio_returns, starting_equity=state.equity)
        drawdown = drawdown_stats(drawdown_source)
        data_quality["drawdown_source"] = "snapshots" if equity_curve else "current_weight_backfill"
        beta = beta_vs_benchmark(portfolio_returns, benchmark_returns)
        model_vol = annualized_volatility(portfolio_returns)
        metrics = RiskMetricsTable(
            # None below MIN_ANNUALIZATION_OBS — a hot month annualized to
            # four digits is noise, not information.
            annualized_return_pct=annualized_return_pct(portfolio_returns),
            volatility_ann_pct=model_vol,
            sharpe=sharpe_ratio(portfolio_returns, rf=risk_free_rate),
            sortino=sortino_ratio(portfolio_returns, rf=risk_free_rate),
            max_drawdown_pct=drawdown.get("max_drawdown_pct"),
            current_drawdown_pct=drawdown.get("current_drawdown_pct"),
            beta_vs_benchmark=beta,
            benchmark=benchmark,
            var_95_pct=historical_var(portfolio_returns, confidence=0.95),
            daily_win_rate_pct=daily_win_rate(portfolio_returns),
            total_return_pct=drawdown.get("total_return_pct"),
            observations=int(len(portfolio_returns)),
        )

    # --- Risk contribution -------------------------------------------------
    # Ex-ante vol (weights x cov) and vol of the weight-backfilled return
    # series are identical by construction on the same sample, so the
    # "realized" side must come from an independent basis: actual account
    # equity snapshots. With too few snapshots it is None, never a copy.
    realized_vol = None
    if equity_curve and len(equity_curve) >= 21:
        equity_series = pd.Series(
            [float(p.get("equity")) for p in equity_curve if isinstance(p, dict) and p.get("equity")],
            dtype=float,
        )
        realized_vol = annualized_volatility(equity_series.pct_change().dropna())
        data_quality["realized_vol_basis"] = "equity_snapshots"
    else:
        data_quality["realized_vol_basis"] = "unavailable_need_21_snapshots"

    risk_contribution = None
    if not returns_df.empty and weights:
        rc_rows = risk_contribution_decomposition(weights, returns_df)
        risk_contribution = RiskContributionBlock(
            ex_ante_vol_pct=covariance_portfolio_vol(weights, returns_df),
            realized_vol_pct=realized_vol,
            rows=[RiskContributionRow(**row) for row in rc_rows],
        )

    # --- Static exposure (sector/day-PL) supplied by the route layer -------
    static = static_risk or {}
    sector_allocation = static.get("sector_allocation") or []
    positions_weighted = static.get("positions_weighted") or []

    # --- Country exposure + FX stress ---------------------------------------
    # Options have no company profile — resolve equities only so option
    # contracts don't show up as "unresolved country" noise.
    from core.portfolio_analytics import is_option_symbol

    tickers = sorted(t for t in weights if not is_option_symbol(t))
    country_map = resolve_countries(tickers, skill_dir=skill_dir)
    if len(country_map) < len(tickers):
        data_quality["country_unresolved"] = sorted(set(tickers) - set(country_map))
    equity_positions = [p.model_dump() for p in state.positions if not is_option_symbol(p.ticker)]
    fx = fx_stress_by_country(
        equity_positions,
        country_map,
        equity=equity,
        shock_map=get_risk_fx_shock_by_country(skill_dir),
        em_shock_pct=get_risk_fx_shock_em_pct(skill_dir),
    )
    from core.portfolio_risk_advanced import _position_weights_pct

    country_exposure: dict[str, float] = {}
    for ticker, weight_pct in _position_weights_pct(equity_positions):
        info = country_map.get(ticker)
        if not info:
            continue
        country_exposure[info["country"]] = country_exposure.get(info["country"], 0.0) + weight_pct
    country_rows = [
        {
            "country": code,
            "country_name": country_display_name(code),
            "exposure_pct": round(pct, 2),
        }
        for code, pct in sorted(country_exposure.items(), key=lambda kv: -kv[1])
    ]

    # --- Concentration + limit breaches -------------------------------------
    conc_stats = state.concentration
    static_conc = static.get("concentration") or {}
    breaches = limit_breach_scan(
        name_weights=positions_weighted,
        sector_allocation=sector_allocation,
        country_exposure=country_rows,
        single_name_limit_pct=get_risk_limit_single_name_pct(skill_dir),
        sector_limit_pct=get_risk_limit_sector_pct(skill_dir),
        country_limit_pct=get_risk_limit_country_pct(skill_dir),
    )
    top10 = sorted(
        (float(p.get("weight_pct") or 0.0) for p in positions_weighted),
        reverse=True,
    )[:10]
    concentration = ConcentrationBlock(
        hhi=conc_stats.herfindahl,
        effective_n=effective_n(weights),
        top_position_pct=conc_stats.top1_pct or static_conc.get("top_position_pct"),
        top_5_pct=conc_stats.top5_pct or static_conc.get("top_5_pct"),
        top_10_pct=round(sum(top10), 2) if top10 else None,
        sector_count=int(static_conc.get("sector_count") or 0),
        position_count=len(state.positions),
        breaches=[LimitBreach(**b) for b in breaches],
    )

    # --- Stress modules ------------------------------------------------------
    historical_rows = historical_stress_scenarios(
        weights,
        returns_df if not returns_df.empty else None,
        beta=beta,
        equity=equity,
    )
    single_name_rows = single_name_stress(
        [p.model_dump() for p in state.positions],
        equity=equity,
    )
    mc = monte_carlo_var(
        weights,
        returns_df,
        equity=equity,
        simulations=get_risk_mc_simulations(skill_dir),
    )
    stress = StressBlock(
        historical=[HistoricalStressRow(**row) for row in historical_rows],
        single_name=[SingleNameStressRow(**row) for row in single_name_rows],
        fx=FxStressSummary(**fx),
        monte_carlo=MonteCarloSummary(**mc),
        tail_risk=tail_risk_summary(portfolio_returns),
    )

    risk_flags = post_fill_risk.assess(state.model_dump(mode="json"), skill_dir=skill_dir)
    confidence = "low" if data_quality.get("missing_tickers") else "high"
    return PortfolioRiskDashboardPack(
        equity=equity,
        position_count=len(state.positions),
        metrics=metrics,
        correlation=corr,
        risk_contribution=risk_contribution,
        concentration=concentration,
        sector_allocation=sector_allocation,
        positions_weighted=positions_weighted,
        country_exposure=country_rows,
        stress=stress,
        risk_flags=risk_flags,
        closed_trades=_load_closed_trade_metrics(skill_dir),
        equity_curve=drawdown.get("curve") or [],
        lookback_days=resolved_lookback,
        data_quality=data_quality,
        provenance=Provenance.computed(confidence=confidence, benchmark=benchmark),
    )
