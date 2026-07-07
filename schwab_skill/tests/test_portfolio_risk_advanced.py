from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from core.portfolio_risk_advanced import (
    conditional_var,
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


def _returns_frame(rows: int = 120, seed: int = 3) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2026-01-01", periods=rows, freq="B")
    base = rng.normal(0.0005, 0.012, rows)
    return pd.DataFrame(
        {
            "AAA": base + rng.normal(0, 0.004, rows),
            "BBB": base * 0.8 + rng.normal(0, 0.006, rows),
            "CCC": rng.normal(0.0002, 0.02, rows),
        },
        index=dates,
    )


WEIGHTS = {"AAA": 40.0, "BBB": 35.0, "CCC": 25.0}


def test_covariance_portfolio_vol_below_weighted_average_of_standalone_vols() -> None:
    df = _returns_frame()
    port_vol = covariance_portfolio_vol(WEIGHTS, df)
    assert port_vol is not None and port_vol > 0
    standalone = {c: float(df[c].std(ddof=1)) * math.sqrt(252) * 100 for c in df.columns}
    weighted_avg = sum(standalone[t] * w for t, w in WEIGHTS.items()) / 100.0
    # Diversification: covariance vol must not exceed the weighted standalone average.
    assert port_vol <= weighted_avg + 1e-6


def test_risk_contribution_sums_to_100_pct() -> None:
    rows = risk_contribution_decomposition(WEIGHTS, _returns_frame())
    assert len(rows) == 3
    total = sum(r["risk_contrib_pct"] for r in rows)
    assert math.isclose(total, 100.0, abs_tol=0.05)
    assert rows == sorted(rows, key=lambda r: -r["risk_contrib_pct"])


def test_var_and_cvar_ordering() -> None:
    df = _returns_frame()
    portfolio = df.mul(pd.Series({k: v / 100 for k, v in WEIGHTS.items()}), axis=1).sum(axis=1)
    var95 = historical_var(portfolio, confidence=0.95)
    cvar95 = conditional_var(portfolio, confidence=0.95)
    assert var95 is not None and var95 < 0
    assert cvar95 is not None and cvar95 <= var95  # tail mean is at least as bad

    win = daily_win_rate(portfolio)
    assert win is not None and 0 <= win <= 100


def test_var_requires_min_history() -> None:
    short = pd.Series([0.01, -0.02, 0.005])
    assert historical_var(short) is None
    assert conditional_var(short) is None


def test_effective_n_equal_weights() -> None:
    assert effective_n({"A": 25.0, "B": 25.0, "C": 25.0, "D": 25.0}) == 4.0
    assert effective_n({}) is None


def test_tail_risk_summary_shape() -> None:
    df = _returns_frame()
    portfolio = df.mean(axis=1)
    out = tail_risk_summary(portfolio)
    assert out["var_99_pct"] <= out["var_95_pct"]
    assert out["worst_day_pct"] < 0 < out["best_day_pct"]
    assert out["observations"] == len(portfolio)


def test_historical_stress_beta_scaled_and_unavailable() -> None:
    rows = historical_stress_scenarios(WEIGHTS, None, beta=1.5, equity=100_000.0)
    covid = next(r for r in rows if "COVID" in r["scenario"])
    assert covid["method"] == "beta_scaled"
    assert math.isclose(covid["portfolio_impact_pct"], 1.5 * -33.9, abs_tol=0.05)
    assert covid["pnl"] == pytest.approx(100_000 * 1.5 * -0.339, abs=5)
    assert covid["stressed_nav"] == pytest.approx(100_000 + covid["pnl"], abs=1)

    hypothetical = next(r for r in rows if r["scenario_type"] == "hypothetical")
    assert hypothetical["method"] == "beta_scaled"

    # No beta -> fail closed, never fabricate.
    rows_no_beta = historical_stress_scenarios(WEIGHTS, None, beta=None, equity=100_000.0)
    assert all(r["method"] == "unavailable" and r["portfolio_impact_pct"] is None for r in rows_no_beta)


def test_historical_stress_window_replay() -> None:
    dates = pd.date_range("2024-07-30", periods=6, freq="B")
    df = pd.DataFrame({"AAA": [-0.01, -0.03, -0.02, -0.04, 0.01, 0.02]}, index=dates)
    rows = historical_stress_scenarios({"AAA": 100.0}, df, beta=1.0, equity=50_000.0)
    yen = next(r for r in rows if "Yen" in r["scenario"])
    assert yen["method"] == "window_replay"
    assert yen["portfolio_impact_pct"] < 0


def test_single_name_stress_math() -> None:
    positions = [
        {"ticker": "GRPN", "weight_pct": 19.7},
        {"ticker": "KSPI", "weight_pct": 14.5},
        {"ticker": "TENB", "weight_pct": 8.3},
        {"ticker": "XNET", "weight_pct": 7.8},
        {"ticker": "TINY", "weight_pct": 0.5},
    ]
    rows = single_name_stress(positions, equity=14_209_824.0)
    grpn_25 = next(r for r in rows if r["scenario"] == "GRPN -25%")
    assert grpn_25["portfolio_impact_pct"] == pytest.approx(-4.93, abs=0.01)
    assert grpn_25["pnl"] == pytest.approx(-700_000, abs=5_000)
    # Only the top 4 names appear; the last gets a single gap.
    assert not any(r["ticker"] == "TINY" for r in rows)
    assert len([r for r in rows if r["ticker"] == "XNET"]) == 1


def test_fx_stress_by_country() -> None:
    positions = [
        {"ticker": "KSPI", "weight_pct": 14.55},
        {"ticker": "BABA", "weight_pct": 11.11},
        {"ticker": "AAPL", "weight_pct": 50.0},
        {"ticker": "MYST", "weight_pct": 5.0},
    ]
    country_map = {
        "KSPI": {"country": "KZ", "currency": "USD"},
        "BABA": {"country": "CN", "currency": "USD"},
        "AAPL": {"country": "US", "currency": "USD"},
    }
    out = fx_stress_by_country(
        positions,
        country_map,
        equity=1_000_000.0,
        shock_map={"KZ": -20.0, "CN": -10.0},
        em_shock_pct=-15.0,
    )
    assert out["non_usd_weight_pct"] == pytest.approx(25.66, abs=0.01)
    expected_impact = 14.55 * -0.20 + 11.11 * -0.10
    assert out["scenario_impact_pct"] == pytest.approx(expected_impact, abs=0.01)
    assert out["unresolved_tickers"] == ["MYST"]
    assert {row["country"] for row in out["by_country"]} == {"KZ", "CN"}


def test_monte_carlo_var_reproducible_and_ordered() -> None:
    df = _returns_frame()
    out = monte_carlo_var(WEIGHTS, df, equity=100_000.0, simulations=2000, seed=11)
    assert out["simulations"] == 2000
    assert out["var_99_pct"] <= out["var_95_pct"] < 0
    assert out["cvar_95_pct"] <= out["var_95_pct"]
    again = monte_carlo_var(WEIGHTS, df, equity=100_000.0, simulations=2000, seed=11)
    assert again["var_95_pct"] == out["var_95_pct"]


def test_is_option_symbol_detects_occ_contracts() -> None:
    from core.portfolio_analytics import is_option_symbol

    assert is_option_symbol("MXCT  270115C00002500")
    assert is_option_symbol("DFTX  260918C00024000")
    assert is_option_symbol("SPXW  261218P04500000")
    assert not is_option_symbol("META")
    assert not is_option_symbol("BRK.B")
    assert not is_option_symbol("")


def test_annualized_return_suppressed_on_short_windows() -> None:
    from core.portfolio_risk_advanced import annualized_return_pct

    hot_month = pd.Series([0.01] * 27)  # would annualize to 4 digits
    assert annualized_return_pct(hot_month) is None
    long_series = pd.Series([0.001] * 120)
    assert annualized_return_pct(long_series) is not None


def test_load_ticker_returns_drops_low_coverage_and_options(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """A short/gappy series must not collapse the aligned sample; options are excluded outright."""
    import core.portfolio_analytics_service as svc

    dates = pd.date_range("2025-07-01", periods=170, freq="B")
    rng = np.random.default_rng(9)

    def fake_history(ticker, *, days, auth=None, skill_dir=None):
        n = 25 if ticker == "GAPPY" else len(dates)
        prices = 100.0 * np.cumprod(1.0 + rng.normal(0.0004, 0.01, n))
        return pd.DataFrame({"close": prices}, index=dates[-n:]), {"provider": "test", "rows": n}

    import market_data

    monkeypatch.setattr(market_data, "get_daily_history_with_meta", fake_history)
    dq: dict = {"missing_tickers": [], "insufficient_history": [], "provider_meta": {}, "excluded_weight_pct": 0.0}
    weights = {"AAA": 0.4, "BBB": 0.3, "GAPPY": 0.2, "MXCT  270115C00002500": 0.1}
    returns_df, _bench = svc._load_ticker_returns(
        weights, benchmark="SPY", lookback_days=252, skill_dir=tmp_path, auth=None, data_quality=dq
    )

    assert set(returns_df.columns) == {"AAA", "BBB"}
    assert dq["excluded_options"] == ["MXCT  270115C00002500"]
    assert "GAPPY" in dq["insufficient_history"]
    assert dq["low_coverage_dropped"] == ["GAPPY"]
    # Aligned sample stays at full length instead of collapsing to 24 rows.
    assert dq["aligned_observations"] >= 160
    # Excluded weight accounts for both the option and the dropped ticker.
    assert dq["excluded_weight_pct"] == pytest.approx(30.0, abs=0.1)


def test_build_portfolio_risk_dashboard_smoke(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """End-to-end pack assembly with mocked prices and country lookups (no network)."""
    import core.portfolio_analytics_service as svc
    import core.portfolio_country_lookup as lookup
    from core.contracts.portfolio import PortfolioRiskState, Position

    rng = np.random.default_rng(5)
    dates = pd.date_range("2025-07-01", periods=260, freq="B")

    def fake_history(ticker, *, days, auth=None, skill_dir=None):
        base = rng.normal(0.0004, 0.015, len(dates))
        prices = 100.0 * np.cumprod(1.0 + base)
        return pd.DataFrame({"close": prices}, index=dates), {"provider": "test", "rows": len(dates)}

    import market_data

    monkeypatch.setattr(market_data, "get_daily_history_with_meta", fake_history)
    monkeypatch.setattr(
        lookup,
        "resolve_countries",
        lambda tickers, skill_dir=None: {
            "KSPI": {"country": "KZ", "currency": "USD", "country_name": "Kazakhstan"},
            "AAPL": {"country": "US", "currency": "USD", "country_name": "United States"},
        },
    )

    state = PortfolioRiskState(
        equity=100_000.0,
        positions=[
            Position(ticker="AAPL", qty=100, market_value=60_000.0, weight_pct=0.6),
            Position(ticker="KSPI", qty=200, market_value=40_000.0, weight_pct=0.4),
        ],
    )
    summary = {"total_market_value": 100_000.0, "positions_count": 2}
    static_risk = {
        "sector_allocation": [{"sector": "Technology", "weight_pct": 60.0, "value": 60_000.0}],
        "positions_weighted": [
            {"symbol": "AAPL", "weight_pct": 60.0},
            {"symbol": "KSPI", "weight_pct": 40.0},
        ],
        "concentration": {"top_position_pct": 60.0, "top_5_pct": 100.0, "sector_count": 1},
    }

    pack = svc.build_portfolio_risk_dashboard(
        state, summary, static_risk=static_risk, skill_dir=tmp_path, lookback_days=252
    )

    assert pack.position_count == 2
    assert pack.metrics is not None and pack.metrics.volatility_ann_pct is not None
    assert pack.metrics.var_95_pct is not None
    assert pack.correlation is not None and "AAPL" in pack.correlation.matrix
    assert pack.risk_contribution is not None and len(pack.risk_contribution.rows) == 2
    assert pack.concentration is not None and pack.concentration.effective_n is not None
    # 60% single name and 60% Technology and 40% KZ all breach default limits.
    breach_kinds = {b.kind for b in pack.concentration.breaches}
    assert breach_kinds == {"single_name", "sector", "country"}
    assert len(pack.stress.historical) == 7
    assert pack.stress.single_name and pack.stress.single_name[0].ticker == "AAPL"
    assert pack.stress.fx is not None and pack.stress.fx.non_usd_weight_pct == pytest.approx(40.0, abs=0.1)
    assert pack.stress.monte_carlo is not None and pack.stress.monte_carlo.simulations > 0
    assert pack.stress.tail_risk["var_95_pct"] is not None
    assert pack.equity_curve  # synthetic backfill curve
    payload = pack.model_dump(mode="json")
    assert payload["stress"]["fx"]["by_country"]


def test_limit_breach_scan() -> None:
    breaches = limit_breach_scan(
        name_weights=[
            {"symbol": "GRPN", "weight_pct": 19.7},
            {"symbol": "SMALL", "weight_pct": 2.0},
        ],
        sector_allocation=[
            {"sector": "Technology", "weight_pct": 43.9},
            {"sector": "Unknown", "weight_pct": 50.0},
        ],
        country_exposure=[
            {"country": "KZ", "exposure_pct": 14.6},
            {"country": "US", "exposure_pct": 60.0},
        ],
        single_name_limit_pct=10.0,
        sector_limit_pct=35.0,
        country_limit_pct=10.0,
    )
    kinds = sorted(b["kind"] for b in breaches)
    assert kinds == ["country", "sector", "single_name"]
    grpn = next(b for b in breaches if b["kind"] == "single_name")
    assert "GRPN" in grpn["message"] and "10%" in grpn["message"]
