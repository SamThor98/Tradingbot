from __future__ import annotations

import json
import sys
import types
from collections import defaultdict
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

import signal_scanner
from config import clear_env_cache, get_self_study_mode
from scripts.seed_calibration_demo import _demo_self_study
from self_study import (
    MIN_ROUND_TRIPS_FOR_LEARNING,
    get_learned_min_conviction,
    is_demo_calibration_seed,
    resolve_self_study_floor,
    signal_below_self_study_floor,
    suggest_min_conviction_from_bands,
)


def _band(count: int, avg: float) -> dict[str, float | int]:
    return {"count": count, "win_rate": 50.0, "avg_return_pct": avg}


def _valid_study(*, min_conv: int = 40) -> dict[str, Any]:
    return {
        "suggested_min_conviction": min_conv,
        "round_trips_count": MIN_ROUND_TRIPS_FOR_LEARNING,
        "min_round_trips_met": True,
        "win_rate": 50.0,
        "avg_return_pct": 1.0,
    }


@pytest.fixture(autouse=True)
def _clean_self_study_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    for key in ("SELF_STUDY_MODE", "SELF_STUDY_ENABLED"):
        monkeypatch.delenv(key, raising=False)
    clear_env_cache()
    yield
    clear_env_cache()


def test_suggest_min_conviction_all_green_yields_no_floor() -> None:
    by_conviction = {
        "0-20": _band(8, 1.5),
        "20-40": _band(8, 2.0),
        "40-60": _band(8, 2.2),
    }
    assert suggest_min_conviction_from_bands(by_conviction, 1.8) is None


def test_suggest_min_conviction_first_sustainable_positive() -> None:
    by_conviction = {
        "0-20": _band(8, -8.0),
        "20-40": _band(8, 2.0),
        "40-60": _band(8, 2.5),
        "60-80": _band(8, 3.0),
    }
    assert suggest_min_conviction_from_bands(by_conviction, 0.5) == 20


def test_suggest_min_conviction_does_not_ratchet_to_highest_green() -> None:
    by_conviction = {
        "0-20": _band(8, -8.0),
        "20-40": _band(8, 1.5),
        "60-80": _band(8, 4.0),
    }
    assert suggest_min_conviction_from_bands(by_conviction, 0.5) == 20


def test_suggest_min_conviction_non_monotonic_yields_none() -> None:
    by_conviction = {
        "20-40": _band(8, 2.0),
        "40-60": _band(8, -8.0),
    }
    assert suggest_min_conviction_from_bands(by_conviction, 0.5) is None


def test_suggest_min_conviction_insufficient_band_samples() -> None:
    by_conviction = {
        "0-20": _band(3, -2.0),
        "40-60": _band(4, 3.0),
    }
    assert suggest_min_conviction_from_bands(by_conviction, 0.5) is None


def test_demo_seed_fingerprint_matches_seed_script() -> None:
    assert is_demo_calibration_seed(_demo_self_study()) is True
    valid = _valid_study()
    assert is_demo_calibration_seed(valid) is False


def test_self_study_mode_defaults_off(tmp_path: Path) -> None:
    assert get_self_study_mode(tmp_path) == "off"


def test_self_study_mode_legacy_enabled_maps_to_live(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("SELF_STUDY_ENABLED=true\n", encoding="utf-8")
    assert get_self_study_mode(tmp_path) == "live"


def test_self_study_mode_overrides_legacy_enabled(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text(
        "SELF_STUDY_ENABLED=true\nSELF_STUDY_MODE=shadow\n",
        encoding="utf-8",
    )
    assert get_self_study_mode(tmp_path) == "shadow"


def test_resolve_refuses_demo_seed_even_when_live(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("SELF_STUDY_MODE=live\n", encoding="utf-8")
    (tmp_path / ".self_study.json").write_text(
        json.dumps(_demo_self_study()),
        encoding="utf-8",
    )
    floor = resolve_self_study_floor(tmp_path)
    assert floor.mode == "live"
    assert floor.min_conviction is None
    assert floor.reason == "demo_seed"
    assert get_learned_min_conviction(tmp_path) is None


def test_resolve_refuses_under_sampled_book(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("SELF_STUDY_MODE=live\n", encoding="utf-8")
    payload = _valid_study()
    payload["round_trips_count"] = 12
    payload["min_round_trips_met"] = True
    (tmp_path / ".self_study.json").write_text(json.dumps(payload), encoding="utf-8")
    floor = resolve_self_study_floor(tmp_path)
    assert floor.min_conviction is None
    assert floor.reason == "insufficient_round_trips"


def test_resolve_applies_valid_floor(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("SELF_STUDY_MODE=shadow\n", encoding="utf-8")
    (tmp_path / ".self_study.json").write_text(json.dumps(_valid_study(min_conv=40)), encoding="utf-8")
    floor = resolve_self_study_floor(tmp_path)
    assert floor.mode == "shadow"
    assert floor.min_conviction == 40
    assert floor.reason == "applied"
    assert get_learned_min_conviction(tmp_path) == 40


def test_signal_below_floor_skips_horizon() -> None:
    assert (
        signal_below_self_study_floor(
            {"ticker": "AAA", "mirofish_conviction": 10, "entry_family": "horizon"},
            40,
        )
        is False
    )
    assert (
        signal_below_self_study_floor(
            {"ticker": "PEP", "mirofish_conviction": 10, "entry_family": "pead_primary"},
            40,
        )
        is False
    )
    assert (
        signal_below_self_study_floor(
            {"ticker": "BBB", "mirofish_conviction": 10, "entry_family": "stage2"},
            40,
        )
        is True
    )


def _record_nonfatal_stub(*_args: Any, **_kwargs: Any) -> None:
    return None


@pytest.fixture
def chain_without_self_study_stub(monkeypatch: pytest.MonkeyPatch):
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
        "CONFLUENCE_GATE_MODE",
        "HISTORICAL_RANK_MODE",
        "ANALOG_OVERLAY_MODE",
    ):
        monkeypatch.setenv(var, "off")
    clear_env_cache()


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


def _sig(ticker: str, conviction: float, rank: float = 80.0) -> dict[str, Any]:
    return {
        "ticker": ticker,
        "rank_score": rank,
        "signal_score": rank,
        "composite_score": rank,
        "ensemble_score": rank,
        "mirofish_conviction": conviction,
        "entry_family": "stage2",
    }


def test_chain_live_drops_below_floor(
    tmp_path: Path, chain_without_self_study_stub, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SELF_STUDY_MODE", "live")
    clear_env_cache()
    (tmp_path / ".self_study.json").write_text(json.dumps(_valid_study(min_conv=40)), encoding="utf-8")
    capture: list[dict[str, Any]] = []
    out, diagnostics = _run_chain(
        [_sig("LOW", 21.0, 90.0), _sig("HIGH", 70.0, 80.0)],
        top_n=0,
        skill_dir=tmp_path,
        capture=capture,
    )
    assert [s["ticker"] for s in out] == ["HIGH"]
    assert diagnostics["self_study_filtered"] == 1
    assert diagnostics["self_study_would_filter"] == 0
    assert diagnostics["self_study_floor_reason"] == "applied"
    by_status = {s["ticker"]: s.get("_filter_status") for s in capture}
    assert by_status["LOW"] == "filtered_self_study"
    assert by_status["HIGH"] == "kept"


def test_chain_shadow_counts_would_filter(
    tmp_path: Path, chain_without_self_study_stub, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SELF_STUDY_MODE", "shadow")
    clear_env_cache()
    (tmp_path / ".self_study.json").write_text(json.dumps(_valid_study(min_conv=40)), encoding="utf-8")
    out, diagnostics = _run_chain(
        [_sig("LOW", 21.0, 90.0), _sig("HIGH", 70.0, 80.0)],
        top_n=0,
        skill_dir=tmp_path,
    )
    assert {s["ticker"] for s in out} == {"LOW", "HIGH"}
    assert diagnostics["self_study_filtered"] == 0
    assert diagnostics["self_study_would_filter"] == 1
    assert diagnostics["self_study_mode"] == "shadow"


def test_chain_demo_seed_does_not_filter(
    tmp_path: Path, chain_without_self_study_stub, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SELF_STUDY_MODE", "live")
    clear_env_cache()
    (tmp_path / ".self_study.json").write_text(json.dumps(_demo_self_study()), encoding="utf-8")
    out, diagnostics = _run_chain(
        [_sig("LOW", 12.0), _sig("HIGH", 70.0)],
        top_n=0,
        skill_dir=tmp_path,
    )
    assert {s["ticker"] for s in out} == {"LOW", "HIGH"}
    assert diagnostics["self_study_filtered"] == 0
    assert diagnostics["self_study_floor_reason"] == "demo_seed"
