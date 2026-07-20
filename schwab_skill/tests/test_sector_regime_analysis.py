"""Unit tests for sector × regime attribution helpers."""

from __future__ import annotations

import pandas as pd

from sector_regime_analysis import (
    build_spy_regime_series,
    classify_entry_regime_bucket,
    cohort_metrics,
    lookup_regime_at,
    sector_gate_counterfactual,
    summarize_sector_regime,
)


def test_classify_entry_regime_bucket() -> None:
    assert classify_entry_regime_bucket(above_sma_200=True, ret_63=0.05) == "bull"
    assert classify_entry_regime_bucket(above_sma_200=False, ret_63=-0.05) == "bear"
    assert classify_entry_regime_bucket(above_sma_200=True, ret_63=-0.02) == "chop"
    assert classify_entry_regime_bucket(above_sma_200=False, ret_63=0.02) == "chop"
    assert classify_entry_regime_bucket(above_sma_200=None, ret_63=0.01) == "unknown"


def test_build_spy_regime_series_and_lookup() -> None:
    idx = pd.bdate_range("2019-01-01", periods=260)
    # Uptrend then drop below a synthetic SMA path via flat then decline
    closes = [100.0 + i * 0.2 for i in range(200)] + [140.0 - i * 0.8 for i in range(60)]
    spy = pd.DataFrame({"close": closes, "high": closes, "low": closes, "open": closes, "volume": 1e6}, index=idx)
    above, buckets = build_spy_regime_series(spy)
    assert above is not None and buckets is not None
    assert len(above) == len(spy)
    tags = lookup_regime_at(idx[-1], above, buckets)
    assert tags["regime_bucket"] in {"bull", "chop", "bear", "unknown"}
    assert tags["regime_above_200"] in {True, False}


def test_summarize_and_counterfactual_gate() -> None:
    trades = []
    for i in range(40):
        trades.append(
            {
                "ticker": "AAA",
                "net_return": 0.02 if i % 2 == 0 else -0.01,
                "sector_etf": "XLK",
                "regime_bucket": "bull",
                "sector_filter": "sector_winning",
            }
        )
    for i in range(20):
        trades.append(
            {
                "ticker": "BBB",
                "net_return": -0.03,
                "sector_etf": "XLE",
                "regime_bucket": "bear",
                "sector_filter": "sector_not_winning",
            }
        )

    summary = summarize_sector_regime(trades, min_trades=10)
    assert summary["trade_count"] == 60
    assert "XLK" in summary["by_sector"]
    assert "bull" in summary["by_regime"]
    assert "XLK|bull" in summary["by_sector_regime"]

    cf = sector_gate_counterfactual(trades, min_trades=10)
    assert cf["hard_gate"]["trade_count"] == 40
    assert cf["baseline_shadow"]["trade_count"] == 60
    # Dropping all-losing XLE/bear trades should improve PF
    assert cf["lift"]["profit_factor_delta"] is not None
    assert float(cf["lift"]["profit_factor_delta"]) > 0
    assert cf["recommendation"] in {
        "promote_hard_candidate",
        "keep_shadow_unstable_across_regimes",
        "keep_shadow_no_lift",
        "insufficient_sample_keep_shadow",
        "inconclusive_keep_shadow",
    }


def test_cohort_metrics_empty_and_sparse() -> None:
    empty = cohort_metrics(pd.Series(dtype=float), min_trades=5)
    assert empty["n"] == 0 and empty["sparse"] is True
    sparse = cohort_metrics(pd.Series([0.01, -0.01]), min_trades=5)
    assert sparse["n"] == 2 and sparse["sparse"] is True
