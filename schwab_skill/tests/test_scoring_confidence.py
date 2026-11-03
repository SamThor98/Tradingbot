"""Unit tests for the setup-confidence shadow readout (core/scoring_confidence.py)."""

from __future__ import annotations

import json
from pathlib import Path

from core.scoring_confidence import (
    ARTIFACT_SCHEMA,
    FORBIDDEN_INPUTS,
    compute_setup_confidence,
    load_confidence_artifact,
    score_setup_confidence,
)


def _artifact(**overrides: object) -> dict:
    base: dict = {
        "schema": ARTIFACT_SCHEMA,
        "model_id": "conf_test",
        "features": [
            # Negative sign: steeper slope scores lower, matching the fitted book.
            {"name": "sma_200_slope", "sign": -1.0, "bounds": [0.0, 1.0]},
            {"name": "trend_strength_score", "sign": -1.0, "bounds": [0.0, 1.0]},
        ],
    }
    base.update(overrides)
    return base


def test_negative_sign_inverts_the_score() -> None:
    art = _artifact()
    calm = compute_setup_confidence(art, {"sma_200_slope": 0.0, "trend_strength_score": 0.0})
    steep = compute_setup_confidence(art, {"sma_200_slope": 1.0, "trend_strength_score": 1.0})
    assert calm is not None and steep is not None
    assert calm["setup_confidence"] == 100.0
    assert steep["setup_confidence"] == 0.0


def test_unscored_row_returns_none_not_zero() -> None:
    # A missing feature must read as "unknown", never as "no confidence".
    assert compute_setup_confidence(_artifact(), {"unrelated": 1.0}) is None
    assert compute_setup_confidence(_artifact(), {}) is None


def test_partial_coverage_scores_and_flags() -> None:
    block = compute_setup_confidence(_artifact(), {"sma_200_slope": 0.25})
    assert block is not None
    assert block["setup_confidence"] == 75.0
    assert block["setup_confidence_coverage"] == 0.5
    assert any("missing_features" in r for r in block["setup_confidence_reasons"])


def test_values_clamp_outside_fitted_bounds() -> None:
    art = _artifact()
    low = compute_setup_confidence(art, {"sma_200_slope": -5.0, "trend_strength_score": -5.0})
    high = compute_setup_confidence(art, {"sma_200_slope": 9.0, "trend_strength_score": 9.0})
    assert low is not None and high is not None
    assert low["setup_confidence"] == 100.0
    assert high["setup_confidence"] == 0.0


def test_live_signal_alias_resolves_sma200_distance() -> None:
    art = _artifact(
        features=[{"name": "close_vs_sma200_pct", "sign": -1.0, "bounds": [0.0, 1.0]}]
    )
    # Live rows may carry the registry spelling instead.
    block = compute_setup_confidence(art, None, {"dist_sma200_pct": 0.25})
    assert block is not None
    assert block["setup_confidence"] == 75.0


def test_feature_row_wins_over_signal_row() -> None:
    art = _artifact(features=[{"name": "sma_200_slope", "sign": 1.0, "bounds": [0.0, 1.0]}])
    block = compute_setup_confidence(art, {"sma_200_slope": 0.9}, {"sma_200_slope": 0.1})
    assert block is not None
    assert block["setup_confidence"] == 90.0


def test_risk_caps_only_lower_the_score() -> None:
    art = _artifact()
    features = {"sma_200_slope": 0.0, "trend_strength_score": 0.0}
    clean = score_setup_confidence({}, art, feature_row=features)
    risky = score_setup_confidence(
        {"reliability_score": 10.0, "execution_score": 10.0, "sec_risk_tag": "high"},
        art,
        feature_row=features,
    )
    assert clean is not None and risky is not None
    assert clean["setup_confidence"] == 100.0
    assert risky["setup_confidence"] < clean["setup_confidence"]
    assert "risk_capped" in risky["setup_confidence_reasons"]


def test_artifact_rejects_label_features(tmp_path: Path) -> None:
    bad = _artifact(features=[{"name": "net_return", "sign": 1.0, "bounds": [0.0, 1.0]}])
    path = tmp_path / "scan_confidence.json"
    path.write_text(json.dumps(bad), encoding="utf-8")
    assert load_confidence_artifact(tmp_path, str(path)) is None


def test_artifact_rejects_wrong_schema(tmp_path: Path) -> None:
    path = tmp_path / "scan_confidence.json"
    path.write_text(json.dumps(_artifact(schema="something_else")), encoding="utf-8")
    assert load_confidence_artifact(tmp_path, str(path)) is None


def test_missing_artifact_fails_closed(tmp_path: Path) -> None:
    assert load_confidence_artifact(tmp_path, str(tmp_path / "absent.json")) is None


def test_forbidden_inputs_cover_outcome_columns() -> None:
    for column in ("net_return", "hold_days", "exit_reason", "ret_40d_fwd"):
        assert column in FORBIDDEN_INPUTS


def test_shipped_artifact_is_loadable_and_clean() -> None:
    skill_dir = Path(__file__).resolve().parent.parent
    artifact = load_confidence_artifact(skill_dir)
    if artifact is None:
        return  # research artifact not built in this checkout
    assert artifact["features"]
    for feat in artifact["features"]:
        assert feat["name"] not in FORBIDDEN_INPUTS
        assert len(feat["bounds"]) == 2
    # Shadow only: the artifact must never advertise itself as promotable.
    assert artifact.get("promote") is False
