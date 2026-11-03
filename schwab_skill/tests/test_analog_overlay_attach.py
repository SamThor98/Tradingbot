"""Scanner analog overlay stays shadow: annotate only, never retarget sort."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

import signal_scanner
from core.scoring_analog import FEATURE_NAMES
from scripts.build_analog_library import build_library_from_rows, write_library


def _record_nonfatal_stub(*_args: Any, **_kwargs: Any) -> None:
    return None


def _signal(ticker: str, sort_score: float, **extra: Any) -> dict[str, Any]:
    row = {
        "ticker": ticker,
        "sort_score": sort_score,
        "signal_score": sort_score,
        "composite_score": sort_score,
        "rank_score": sort_score,
        "close_vs_sma200_pct": 0.10,
        "pct_from_52w_high": 0.86,
        "volume_ratio": 1.4,
        "pts_mirofish": 6.0,
        "p_up_calibrated": 0.52,
        "era": "recent_current",
        "entry_date": "2024-06-01",
    }
    row.update(extra)
    return row


def _write_fixture_library(tmp_path) -> None:
    rows = []
    for i in range(24):
        rows.append(
            {
                "ticker": f"L{i}",
                "entry_date": f"2022-03-{(i % 27) + 1:02d}",
                "era": "bear_rates",
                "close_vs_sma200_pct": 0.08 + (i % 5) * 0.01,
                "pct_from_52w_high": 0.84,
                "volume_ratio": 1.2,
                "pts_mirofish": 5.0,
                "p_up_calibrated": 0.50,
                "net_return": 0.15 if i % 2 == 0 else -0.09,
                "hold_days": 25,
                "exit_reason": "time_exit",
            }
        )
    artifact = build_library_from_rows(rows, sources=["fixture"], k=8, min_n=6, fail_closed=False)
    write_library(artifact, out_dir=tmp_path / "research_store" / "models")


def test_shadow_attach_does_not_mutate_sort_score(tmp_path, monkeypatch) -> None:
    from config import clear_env_cache

    _write_fixture_library(tmp_path)
    monkeypatch.setenv("ANALOG_OVERLAY_MODE", "shadow")
    monkeypatch.setenv("ANALOG_OVERLAY_K", "8")
    monkeypatch.setenv("ANALOG_OVERLAY_MIN_N", "6")
    clear_env_cache()

    signals = [_signal("AAA", 71.0), _signal("BBB", 64.0)]
    diagnostics: dict[str, Any] = defaultdict(int)
    out = signal_scanner._apply_analog_overlay(signals, diagnostics, tmp_path)
    assert [s["sort_score"] for s in out] == [71.0, 64.0]
    assert diagnostics["analog_overlay_mode"] == "shadow"
    assert diagnostics["analog_overlay_scored"] >= 1
    assert any(s.get("analog_neighborhood_pf") is not None for s in out)
    assert "analog_overlay_model_id" in diagnostics


def test_live_mode_is_refused_and_does_not_sort(tmp_path, monkeypatch) -> None:
    from config import clear_env_cache

    _write_fixture_library(tmp_path)
    monkeypatch.setenv("ANALOG_OVERLAY_MODE", "live")
    clear_env_cache()
    signals = [_signal("AAA", 80.0)]
    diagnostics: dict[str, Any] = defaultdict(int)
    out = signal_scanner._apply_analog_overlay(signals, diagnostics, tmp_path)
    assert diagnostics["analog_overlay_skipped"] == "live_not_approved"
    assert out[0]["sort_score"] == 80.0
    assert "analog_p_win" not in out[0]


def test_missing_artifact_skips_without_crash(tmp_path, monkeypatch) -> None:
    from config import clear_env_cache

    monkeypatch.setenv("ANALOG_OVERLAY_MODE", "shadow")
    clear_env_cache()
    signals = [_signal("AAA", 50.0)]
    diagnostics: dict[str, Any] = defaultdict(int)
    out = signal_scanner._apply_analog_overlay(signals, diagnostics, tmp_path)
    assert diagnostics["analog_overlay_skipped"] == "artifact_missing"
    assert out[0]["sort_score"] == 50.0


def test_chain_shadow_keeps_live_sort_key(tmp_path, monkeypatch) -> None:
    import sys
    import types

    from config import clear_env_cache

    _write_fixture_library(tmp_path)
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
            meta_policy_counterfactual_reason=lambda **_k: None,
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
    for var in (
        "QUALITY_GATES_MODE",
        "EVENT_RISK_MODE",
        "SIGNAL_EDGE_SHADOW_MODE",
        "RANK_FILTER_V2_MODE",
        "HISTORICAL_RANK_MODE",
        "ANALOG_OVERLAY_MODE",
        "CONFLUENCE_GATE_MODE",
    ):
        monkeypatch.setenv(var, "off")
    monkeypatch.setenv("ANALOG_OVERLAY_MODE", "shadow")
    clear_env_cache()
    signals = [_signal("LOW", 10.0), _signal("HIGH", 90.0)]
    diagnostics: dict[str, Any] = defaultdict(int)
    out = signal_scanner._apply_post_stage_b_chain(
        signals,
        diagnostics,
        skill_dir=tmp_path,
        scan_id="t",
        top_n=2,
        regime_v2_snapshot=None,
        regime_v2_mode="off",
        capture_shortlist=None,
        record_nonfatal=_record_nonfatal_stub,
    )
    assert [s["ticker"] for s in out] == ["HIGH", "LOW"]
    assert out[0]["sort_score"] == 90.0
    assert FEATURE_NAMES
