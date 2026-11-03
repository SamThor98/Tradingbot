"""Each selected scan sleeve ranks and fills Stage B independently."""

from __future__ import annotations

from signal_scanner import (
    _apply_rank_v2_percentile_filter,
    _assemble_stage_a_shortlist,
    _horizon_stage_a_sort_key,
    _is_research_entry_family,
)


def _horizon(ticker: str, plugin_score: float, stage_a_score: float) -> dict:
    return {
        "ticker": ticker,
        "entry_family": "horizon",
        "executable": False,
        "stage_a_score": stage_a_score,
        "horizon_plugin_hits": [{"name": "weekly_swing", "raw_score": plugin_score, "triggered": True}],
    }


def _stage2(ticker: str, stage_a_score: float) -> dict:
    return {
        "ticker": ticker,
        "entry_family": "stage2",
        "executable": True,
        "stage_a_score": stage_a_score,
    }


def _pead(ticker: str, edge_score: float) -> dict:
    return {
        "ticker": ticker,
        "entry_family": "pead_primary",
        "executable": False,
        "edge_score": edge_score,
        "stage_a_score": 1.0,
    }


def test_horizon_sort_uses_plugin_score_not_stage2() -> None:
    weak_stage2_strong_weekly = _horizon("AAA", plugin_score=90.0, stage_a_score=10.0)
    strong_stage2_weak_weekly = _horizon("BBB", plugin_score=20.0, stage_a_score=99.0)
    ranked = sorted(
        [strong_stage2_weak_weekly, weak_stage2_strong_weekly],
        key=_horizon_stage_a_sort_key,
    )
    assert [c["ticker"] for c in ranked] == ["AAA", "BBB"]


def test_dedicated_horizon_scan_gets_full_stage_b_budget() -> None:
    horizon = [_horizon(f"H{i:02d}", plugin_score=float(100 - i), stage_a_score=1.0) for i in range(80)]
    out = _assemble_stage_a_shortlist(
        executable=[],
        horizon_only=horizon,
        pead_only=[],
        want_stage2=False,
        want_pead=False,
        want_horizon=True,
        top_n=0,
        multiplier=3.0,
        cap=40,
        nocap_limit=250,
        horizon_sidecar_cap=40,
    )
    assert out["dedicated_research"] is True
    assert len(out["shortlist"]) == 80
    assert [c["ticker"] for c in out["shortlist"][:3]] == ["H00", "H01", "H02"]
    assert out["pead_stage_b"] == []


def test_dedicated_pead_scan_joins_stage_b() -> None:
    pead = [_pead(f"P{i:02d}", edge_score=float(50 - i)) for i in range(12)]
    out = _assemble_stage_a_shortlist(
        executable=[],
        horizon_only=[],
        pead_only=pead,
        want_stage2=False,
        want_pead=True,
        want_horizon=False,
        top_n=5,
        multiplier=3.0,
        cap=40,
        nocap_limit=250,
        horizon_sidecar_cap=40,
    )
    assert out["dedicated_research"] is True
    assert len(out["shortlist"]) == 12
    assert [c["ticker"] for c in out["pead_stage_b"][:3]] == ["P00", "P01", "P02"]
    assert all(c["entry_family"] == "pead_primary" for c in out["shortlist"])


def test_dedicated_horizon_and_pead_each_get_own_budget() -> None:
    horizon = [_horizon(f"H{i:02d}", plugin_score=float(90 - i), stage_a_score=1.0) for i in range(20)]
    pead = [_pead(f"P{i:02d}", edge_score=float(40 - i)) for i in range(20)]
    out = _assemble_stage_a_shortlist(
        executable=[],
        horizon_only=horizon,
        pead_only=pead,
        want_stage2=False,
        want_pead=True,
        want_horizon=True,
        top_n=5,
        multiplier=3.0,
        cap=40,
        nocap_limit=250,
        horizon_sidecar_cap=40,
    )
    # top_n=5 * multiplier 3 = 15 per family
    assert len(out["horizon_extra"]) == 15
    assert len(out["pead_stage_b"]) == 15
    assert len(out["shortlist"]) == 30


def test_mixed_stage2_keeps_horizon_as_sidecar_ranked_by_plugin() -> None:
    executable = [_stage2(f"S{i:02d}", stage_a_score=float(80 - i)) for i in range(10)]
    horizon = [
        _horizon("WEAK_S2", plugin_score=95.0, stage_a_score=2.0),
        _horizon("STRONG_S2", plugin_score=11.0, stage_a_score=88.0),
    ]
    out = _assemble_stage_a_shortlist(
        executable=executable,
        horizon_only=horizon,
        pead_only=[_pead("PEAD", edge_score=99.0)],
        want_stage2=True,
        want_pead=True,
        want_horizon=True,
        top_n=5,
        multiplier=3.0,
        cap=40,
        nocap_limit=250,
        horizon_sidecar_cap=40,
    )
    assert out["dedicated_research"] is False
    assert out["pead_stage_b"] == []
    assert [c["ticker"] for c in out["horizon_extra"]] == ["WEAK_S2", "STRONG_S2"]
    assert all(c["ticker"] != "PEAD" for c in out["shortlist"])
    assert [c["ticker"] for c in out["shortlist"] if c["entry_family"] == "stage2"] == [
        f"S{i:02d}" for i in range(10)
    ]


def test_rank_v2_live_does_not_drop_research(monkeypatch, tmp_path) -> None:
    import config

    monkeypatch.setenv("RANK_FILTER_V2_MODE", "live")
    monkeypatch.setenv("RANK_FILTER_SHADOW_MIN_PERCENTILE_RANK_V2", "80")
    config.clear_env_cache()
    signals = [
        {"ticker": "A", "entry_family": "stage2", "rank_score_v2": 10.0},
        {"ticker": "B", "entry_family": "stage2", "rank_score_v2": 20.0},
        {"ticker": "C", "entry_family": "stage2", "rank_score_v2": 90.0},
        {"ticker": "W", "entry_family": "horizon", "rank_score_v2": 1.0},
        {"ticker": "P", "entry_family": "pead_primary", "rank_score_v2": 1.0},
    ]
    diagnostics: dict = {}
    kept = _apply_rank_v2_percentile_filter(signals, diagnostics, tmp_path)
    tickers = {s["ticker"] for s in kept}
    assert "W" in tickers
    assert "P" in tickers
    assert "C" in tickers
    assert diagnostics["rank_filter_v2_dropped"] >= 1


def test_research_family_helper() -> None:
    assert _is_research_entry_family("horizon") is True
    assert _is_research_entry_family("pead_primary") is True
    assert _is_research_entry_family("stage2") is False
    assert _is_research_entry_family("both") is False


def test_quality_gates_skip_stage2_floors_for_research(tmp_path, monkeypatch) -> None:
    import config
    import signal_scanner

    monkeypatch.setenv("QUALITY_MIN_SIGNAL_SCORE", "80")
    monkeypatch.setenv("QUALITY_REQUIRE_BREAKOUT_VOLUME", "true")
    config.clear_env_cache()
    weak = {
        "ticker": "AAA",
        "signal_score": 10.0,
        "latest_volume": 1.0,
        "avg_vol_50": 100.0,
        "entry_family": "horizon",
    }
    assert signal_scanner._evaluate_quality_gates(weak, tmp_path) == []
    weak["entry_family"] = "pead_primary"
    assert "low_signal_score" not in signal_scanner._evaluate_quality_gates(weak, tmp_path)
    weak["entry_family"] = "stage2"
    reasons = signal_scanner._evaluate_quality_gates(weak, tmp_path)
    assert "low_signal_score" in reasons
    assert "weak_breakout_volume" in reasons
