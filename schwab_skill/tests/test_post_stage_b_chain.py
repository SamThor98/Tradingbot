from __future__ import annotations

import sys
import types
from collections import defaultdict
from typing import Any

import pytest

import signal_scanner


def _record_nonfatal_stub(*_args: Any, **_kwargs: Any) -> None:
    return None


def _make_signal(ticker: str, rank: float) -> dict[str, Any]:
    return {
        "ticker": ticker,
        "rank_score": rank,
        "signal_score": rank,
        "composite_score": rank,
        "ensemble_score": rank,
        "mirofish_conviction": 100.0,
    }


@pytest.fixture
def hermetic_chain(monkeypatch):
    """Stub the optional filter-layer modules so the chain is pass-through
    except for the ranking/top-N logic under test, regardless of suite-wide
    state (e.g. a learned self-study min-conviction)."""
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
    # Neutralize the module-level quality/event layers so the chain is a pure
    # ranking/trim test, independent of the project .env config that other
    # test modules may have loaded into the config cache.
    monkeypatch.setattr(signal_scanner, "_evaluate_quality_gates", lambda *_a, **_k: [])
    monkeypatch.setattr(
        signal_scanner,
        "_apply_event_risk_policy_to_signals",
        lambda signals, diagnostics, skill_dir: signals,
    )
    monkeypatch.setattr(signal_scanner, "_record_quality_snapshot", lambda *_a, **_k: None)
    for var in ("QUALITY_GATES_MODE", "EVENT_RISK_MODE"):
        monkeypatch.setenv(var, "off")


def _run_chain(signals, *, top_n, skill_dir, capture=None):
    diagnostics: dict[str, Any] = defaultdict(int)
    out = signal_scanner._apply_post_stage_b_chain(
        signals,
        diagnostics,
        skill_dir=skill_dir,
        scan_id="testscan",
        top_n=top_n,
        regime_v2_snapshot=None,
        regime_v2_mode="off",
        capture_shortlist=capture,
        record_nonfatal=_record_nonfatal_stub,
    )
    return out, diagnostics


def test_chain_ranks_and_trims_to_top_n(tmp_path, hermetic_chain) -> None:
    signals = [
        _make_signal("LOW", 10.0),
        _make_signal("HIGH", 90.0),
        _make_signal("MID", 50.0),
    ]
    out, diagnostics = _run_chain(signals, top_n=2, skill_dir=tmp_path)

    assert [s["ticker"] for s in out] == ["HIGH", "MID"]
    assert diagnostics["rank_basis"] == "rank_score"
    assert diagnostics["top_n_applied"] == 1


def test_chain_capture_shortlist_tags_dispositions(tmp_path, hermetic_chain) -> None:
    signals = [
        _make_signal("AAA", 30.0),
        _make_signal("BBB", 80.0),
        _make_signal("CCC", 60.0),
    ]
    capture: list[dict[str, Any]] = []
    out, _diagnostics = _run_chain(signals, top_n=2, skill_dir=tmp_path, capture=capture)

    assert [s["ticker"] for s in out] == ["BBB", "CCC"]
    status_by_ticker = {s["ticker"]: s.get("_filter_status") for s in capture}
    assert status_by_ticker["BBB"] == "kept"
    assert status_by_ticker["CCC"] == "kept"
    assert status_by_ticker["AAA"] == "trimmed_top_n"


def test_chain_top_n_zero_returns_all(tmp_path, hermetic_chain) -> None:
    signals = [_make_signal("AAA", 30.0), _make_signal("BBB", 80.0)]
    out, diagnostics = _run_chain(signals, top_n=0, skill_dir=tmp_path)

    assert len(out) == 2
    assert diagnostics["top_n_applied"] == 0
