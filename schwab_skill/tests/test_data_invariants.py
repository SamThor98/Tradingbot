"""Invariant checks for data lineage / scoring consistency."""

from __future__ import annotations

import copy
from datetime import date
from pathlib import Path

import pytest


def test_apply_score_stack_counts_conviction_via_signal_score_only() -> None:
    """Changing conviction alone must not move ``edge_score`` when signal_score is fixed."""
    from signal_scanner import _apply_score_stack

    base = {
        "signal_score": 72.0,
        "advisory": {"confidence_bucket": "high", "p_up_10d": 0.56},
        "breakout_confirmed": True,
        "latest_volume": 1_000_000.0,
        "avg_vol_50": 1_000_000.0,
        "data_provider_primary": True,
        "used_fallback_data": False,
        "recent_8k": False,
        "sec_risk_tag": "unknown",
        "forensic_flags": [],
        "mirofish_disagreement": 0.0,
    }
    hi = {**base, "mirofish_conviction": 90.0}
    lo = {**base, "mirofish_conviction": -90.0}
    a = _apply_score_stack(copy.deepcopy(hi))
    b = _apply_score_stack(copy.deepcopy(lo))
    assert a["edge_score"] == b["edge_score"]
    assert a["p_up_calibrated"] == b["p_up_calibrated"]


def test_apply_score_stack_emits_rank_score_metadata() -> None:
    from signal_scanner import _apply_score_stack

    row = {
        "signal_score": 76.0,
        "advisory": {"confidence_bucket": "high", "p_up_10d": 0.61},
        "breakout_confirmed": True,
        "latest_volume": 1_300_000.0,
        "avg_vol_50": 1_000_000.0,
        "data_provider_primary": True,
        "used_fallback_data": False,
        "recent_8k": False,
        "sec_risk_tag": "unknown",
        "forensic_flags": [],
        "mirofish_disagreement": 10.0,
        "mirofish_conviction": 35.0,
    }
    out = _apply_score_stack(copy.deepcopy(row))
    assert isinstance(out.get("rank_score"), (int, float))
    assert 0.0 <= float(out["rank_score"]) <= 100.0
    assert out.get("rank_basis") == "high_level_v1"


def test_apply_score_stack_rank_score_caps_on_high_risk() -> None:
    from signal_scanner import _apply_score_stack

    row = {
        "signal_score": 92.0,
        "advisory": {"confidence_bucket": "high", "p_up_10d": 0.72},
        "breakout_confirmed": True,
        "latest_volume": 1_600_000.0,
        "avg_vol_50": 1_000_000.0,
        "data_provider_primary": True,
        "used_fallback_data": False,
        "recent_8k": False,
        "sec_risk_tag": "high",
        "forensic_flags": ["beneish_manipulator"],
        "mirofish_disagreement": 0.0,
        "mirofish_conviction": 45.0,
    }
    out = _apply_score_stack(copy.deepcopy(row))
    assert float(out.get("rank_score", 100.0)) <= 45.0


def test_forensic_snapshot_skipped_under_schwab_only(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("SCHWAB_ONLY_DATA", "true")
    from config import clear_env_cache

    clear_env_cache()
    from forensic_accounting import compute_forensic_snapshot

    out = compute_forensic_snapshot("IBM", skill_dir=tmp_path)
    assert out.get("data_lineage") == "schwab_only:yfinance_fundamentals_blocked"
    assert out.get("forensic_flags") == []


def test_sp1500_membership_csv(tmp_path: Path) -> None:
    from sp1500_membership import tickers_as_of

    assert tickers_as_of(date(2020, 6, 1), tmp_path / "missing.csv") is None
    p = tmp_path / "m.csv"
    p.write_text("ticker,start_date,end_date\nAAA,2000-01-01,\nBBB,2000-01-01,2019-12-31\n", encoding="utf-8")
    s2020 = tickers_as_of(date(2020, 6, 1), p)
    assert s2020 is not None and "AAA" in s2020 and "BBB" not in s2020


def test_extract_schwab_live_price_never_prior_close() -> None:
    from market_data import extract_schwab_last_price, extract_schwab_live_price

    q = {"quote": {"closePrice": 100.0}}
    assert extract_schwab_live_price(q) is None
    assert extract_schwab_last_price(q) == 100.0


def test_fundamentals_snapshot_refuses_schwab_only(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("SCHWAB_ONLY_DATA", "true")
    from config import clear_env_cache

    clear_env_cache()
    from fundamentals_snapshot import capture_daily_snapshot

    out = capture_daily_snapshot("IBM", skill_dir=tmp_path)
    assert out.get("ok") is False
    assert out.get("reason") == "schwab_only_data"


def test_refit_weights_missing_outcomes(tmp_path: Path) -> None:
    from refit_weights import suggest_composite_tilt, summarize_outcomes_by_score_band

    summary = summarize_outcomes_by_score_band(tmp_path)
    assert summary.get("ok") is False
    assert suggest_composite_tilt(summary)["edge_tilt"] == 1.0
