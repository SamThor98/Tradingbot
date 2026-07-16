"""Tests for backtest-aware reliability dispersion."""

from __future__ import annotations

import copy

import pytest


def test_backtest_reliability_skips_uniform_penalties(monkeypatch: pytest.MonkeyPatch) -> None:
    from core.scoring_reliability import compute_reliability_score

    row = {
        "advisory": {"confidence_bucket": "medium", "feature_coverage": 0.72},
        "used_fallback_data": True,
        "data_provider_primary": False,
        "mirofish_conviction": None,
        "mirofish_disagreement": 60.0,
        "sec_risk_tag": "unknown",
        "forensic_flags": [],
    }
    live_rel, _ = compute_reliability_score(copy.deepcopy(row), context="live")
    back_rel, reasons = compute_reliability_score(copy.deepcopy(row), context="backtest")
    assert back_rel > live_rel
    assert back_rel >= 40.0
    assert "backtest_fallback_data_ignored" in reasons


def test_backtest_reliability_disperses_by_advisory_bucket() -> None:
    from core.scoring_reliability import compute_reliability_score

    base = {
        "used_fallback_data": True,
        "data_provider_primary": False,
        "sec_risk_tag": "unknown",
        "forensic_flags": [],
    }
    high = compute_reliability_score({**base, "advisory": {"confidence_bucket": "high", "feature_coverage": 0.9}}, context="backtest")[0]
    low = compute_reliability_score({**base, "advisory": {"confidence_bucket": "low", "feature_coverage": 0.4}}, context="backtest")[0]
    assert high > low
    assert high - low >= 12.0


def test_apply_score_stack_backtest_avoids_composite_cap_pin() -> None:
    from signal_scanner import _apply_score_stack

    row = {
        "signal_score": 92.0,
        "score_components": {"pts_52w": 28.0, "pts_volume": 10.0, "pts_mirofish": 0.0},
        "advisory": {"confidence_bucket": "unknown"},
        "breakout_confirmed": True,
        "latest_volume": 1_400_000.0,
        "avg_vol_50": 1_000_000.0,
        "used_fallback_data": True,
        "data_provider_primary": False,
        "recent_8k": False,
        "sec_risk_tag": "unknown",
        "forensic_flags": [],
        "close_vs_sma200_pct": 0.12,
    }
    live = _apply_score_stack(copy.deepcopy(row), score_stack_context="live")
    row_back = copy.deepcopy(row)
    row_back["p_up_calibrated"] = 0.62
    back = _apply_score_stack(row_back, score_stack_context="backtest")
    assert float(live["reliability_score"]) < 40.0
    assert float(back["reliability_score"]) >= 40.0
    assert float(live["composite_score"]) <= 55.0
    assert float(back["composite_score"]) > float(live["composite_score"])


def test_scan_live_sort_key_defaults_to_signal_score(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SCAN_LIVE_SORT_KEY", raising=False)
    from config import clear_env_cache, get_scan_live_sort_key

    clear_env_cache()
    assert get_scan_live_sort_key() == "signal_score"


def test_scan_live_sort_key_accepts_rank_v2(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SCAN_LIVE_SORT_KEY", "rank_score_v2")
    from config import clear_env_cache, get_scan_live_sort_key

    clear_env_cache()
    assert get_scan_live_sort_key() == "rank_score_v2"
