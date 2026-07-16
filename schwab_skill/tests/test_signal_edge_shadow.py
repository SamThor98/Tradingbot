from __future__ import annotations

from collections import defaultdict
from typing import Any

import pytest

import signal_scanner


def _record_nonfatal_stub(*_args: Any, **_kwargs: Any) -> None:
    return None


def _run_chain(
    signals: list[dict[str, Any]],
    *,
    skill_dir,
    monkeypatch,
    rank_filter_mode: str = "shadow",
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    monkeypatch.setenv("SIGNAL_EDGE_SHADOW_MODE", "shadow")
    monkeypatch.setenv("RANK_FILTER_V2_MODE", rank_filter_mode)
    diagnostics: dict[str, Any] = defaultdict(int)
    out = signal_scanner._apply_post_stage_b_chain(
        signals,
        diagnostics,
        skill_dir=skill_dir,
        scan_id="testscan",
        top_n=0,
        regime_v2_snapshot=None,
        regime_v2_mode="off",
        capture_shortlist=None,
        record_nonfatal=_record_nonfatal_stub,
    )
    return out, diagnostics


@pytest.fixture
def hermetic_chain(monkeypatch):
    import sys
    import types

    monkeypatch.setitem(
        sys.modules,
        "self_study",
        types.SimpleNamespace(get_learned_min_conviction=lambda *_a, **_k: None),
    )
    monkeypatch.setitem(
        sys.modules,
        "strategy_plugins",
        types.SimpleNamespace(
            apply_strategy_ensemble=lambda *, signals, diagnostics, regime_v2_snapshot, skill_dir: signals
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "agent_intelligence",
        types.SimpleNamespace(
            apply_meta_policy_to_signal=lambda *, signal, diagnostics, skill_dir: (signal, True),
            log_counterfactual_event=lambda *_a, **_k: False,
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "feature_store",
        types.SimpleNamespace(log_stage_b_signal=lambda *_a, **_k: None),
    )
    monkeypatch.setattr(signal_scanner, "_evaluate_quality_gates", lambda *_a, **_k: [])
    monkeypatch.setattr(
        signal_scanner,
        "_apply_event_risk_policy_to_signals",
        lambda signals, diagnostics, skill_dir: signals,
    )
    monkeypatch.setattr(signal_scanner, "_record_quality_snapshot", lambda *_a, **_k: None)
    for var in ("QUALITY_GATES_MODE", "EVENT_RISK_MODE", "CORRELATION_GUARD_MODE"):
        monkeypatch.setenv(var, "off")


def test_rank_filter_shadow_counts_would_drop(tmp_path, hermetic_chain, monkeypatch) -> None:
    signals = [
        {
            "ticker": "LOW",
            "signal_score": 30.0,
            "composite_score": 35.0,
            "rank_score_v2": 40.0,
            "mirofish_conviction": 100.0,
        },
        {
            "ticker": "MID",
            "signal_score": 60.0,
            "composite_score": 62.0,
            "rank_score_v2": 65.0,
            "mirofish_conviction": 100.0,
        },
        {
            "ticker": "HIGH",
            "signal_score": 90.0,
            "composite_score": 92.0,
            "rank_score_v2": 95.0,
            "mirofish_conviction": 100.0,
        },
    ]
    out, diagnostics = _run_chain(signals, skill_dir=tmp_path, monkeypatch=monkeypatch)

    assert len(out) == 3
    assert diagnostics["signal_edge_shadow_mode"] == "shadow"
    assert diagnostics["rank_filter_would_drop_composite"] >= 1
    assert diagnostics["rank_filter_would_drop_any"] >= 1
    assert diagnostics["rank_filter_v2_would_drop"] >= 1
    assert diagnostics["rank_filter_v2_dropped"] == 0
    low = next(s for s in out if s["ticker"] == "LOW")
    assert low.get("rank_filter_shadow", {}).get("composite_score_would_drop") is True
    high = next(s for s in out if s["ticker"] == "HIGH")
    assert high.get("rank_filter_shadow", {}).get("composite_score_would_drop") is False


def test_rank_filter_v2_live_keeps_top_thirty_percent(tmp_path, hermetic_chain, monkeypatch) -> None:
    signals = [
        {
            "ticker": f"T{score}",
            "signal_score": float(score),
            "composite_score": float(score),
            "rank_score_v2": float(score),
            "mirofish_conviction": 100.0,
        }
        for score in range(1, 11)
    ]

    out, diagnostics = _run_chain(
        signals,
        skill_dir=tmp_path,
        monkeypatch=monkeypatch,
        rank_filter_mode="live",
    )

    assert [signal["ticker"] for signal in out] == ["T10", "T9", "T8"]
    assert diagnostics["rank_filter_v2_evaluated"] == 10
    assert diagnostics["rank_filter_v2_would_drop"] == 7
    assert diagnostics["rank_filter_v2_dropped"] == 7


def test_signal_edge_shadow_off_skips_counters(tmp_path, hermetic_chain, monkeypatch) -> None:
    monkeypatch.setenv("SIGNAL_EDGE_SHADOW_MODE", "off")
    signals = [
        {"ticker": "AAA", "signal_score": 50.0, "composite_score": 50.0, "mirofish_conviction": 100.0},
        {"ticker": "BBB", "signal_score": 80.0, "composite_score": 80.0, "mirofish_conviction": 100.0},
        {"ticker": "CCC", "signal_score": 90.0, "composite_score": 90.0, "mirofish_conviction": 100.0},
    ]
    diagnostics: dict[str, Any] = defaultdict(int)
    signal_scanner._apply_post_stage_b_chain(
        signals,
        diagnostics,
        skill_dir=tmp_path,
        scan_id="testscan",
        top_n=0,
        regime_v2_snapshot=None,
        regime_v2_mode="off",
        capture_shortlist=None,
        record_nonfatal=_record_nonfatal_stub,
    )
    assert diagnostics["signal_edge_shadow_mode"] == "off"
    assert diagnostics.get("rank_filter_would_drop_composite", 0) == 0


def test_score_quantile_threshold_interpolates() -> None:
    threshold = signal_scanner._score_quantile_threshold([10.0, 20.0, 30.0, 40.0], 50)
    assert threshold == pytest.approx(25.0)


def test_accumulate_entry_shadow_stage2_diagnostics() -> None:
    from signal_scanner import _accumulate_entry_shadow_stage2_diagnostics

    diagnostics: dict[str, int] = defaultdict(int)
    _accumulate_entry_shadow_stage2_diagnostics(
        diagnostics,
        {
            "entry_timing_at_stage2": {
                "would_filter": True,
                "would_filter_reasons": ["breakout_buffer_low"],
            }
        },
    )
    assert diagnostics["entry_shadow_stage2_evaluated"] == 1
    assert diagnostics["entry_shadow_stage2_would_filter_any"] == 1
    assert diagnostics["entry_shadow_stage2_would_filter_breakout_buffer"] == 1
