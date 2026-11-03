"""Unit tests for the historical blend ranker (core/scoring_historical.py)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.scoring_historical import (
    RAW_KEYS,
    cohort_percentile,
    compute_historical_rank,
    features_from_signal_row,
    load_blend_artifact,
    normalize_feature,
    raw_features_from_signal_row,
    volume_points,
)

# Round numbers so the expected normalizations are obvious.
BOUNDS = {
    "close_vs_sma200_pct": {"lo": 0.0, "hi": 0.20},
    "volume_points": {"lo": 0.0, "hi": 20.0},
    "pct_from_52w_high": {"lo": 0.70, "hi": 1.00},
    "expected_return_40d": {"lo": -0.05, "hi": 0.11},
}


def _artifact(weights: dict[str, float] | None = None) -> dict:
    return {
        "model_id": "hist_blend_test",
        "weights": weights or {"trend_norm": 1.0},
        "feature_bounds": BOUNDS,
    }


# --------------------------------------------------------------------------- #
# Normalization primitives
# --------------------------------------------------------------------------- #
def test_normalize_feature_maps_fitted_band_to_0_100() -> None:
    assert normalize_feature("prox_52w_norm", 0.70, BOUNDS) == 0.0
    assert normalize_feature("prox_52w_norm", 1.00, BOUNDS) == 100.0
    assert normalize_feature("prox_52w_norm", 0.85, BOUNDS) == pytest.approx(50.0)
    # Outside the band clamps rather than going negative / above 100.
    assert normalize_feature("prox_52w_norm", 0.40, BOUNDS) == 0.0
    assert normalize_feature("prox_52w_norm", 1.40, BOUNDS) == 100.0
    assert normalize_feature("prox_52w_norm", None, BOUNDS) is None
    assert normalize_feature("prox_52w_norm", "n/a", BOUNDS) is None


def test_normalize_feature_falls_back_when_bounds_absent_or_degenerate() -> None:
    """Hand-written artifacts without fitted bounds still score."""
    assert normalize_feature("trend_norm", 0.10, None) == pytest.approx(50.0)
    assert normalize_feature("trend_norm", 0.10, {"close_vs_sma200_pct": {"lo": 5, "hi": 5}}) == pytest.approx(50.0)


def test_normalize_feature_uses_the_fitted_band_not_the_composite_cap() -> None:
    """Wider fitted bounds must spread scores the composite cap would pin at 100."""
    wide = {"close_vs_sma200_pct": {"lo": 0.0, "hi": 0.80}}
    assert normalize_feature("trend_norm", 0.40, wide) == pytest.approx(50.0)
    # Same input saturates under the legacy 0.20 cap.
    assert normalize_feature("trend_norm", 0.40, BOUNDS) == 100.0


def test_volume_points_takes_the_stronger_of_dryup_and_breakout() -> None:
    # Dry-up ratio 0.5 -> 10 of the 20 available points.
    assert volume_points(avg_vcp_volume_ratio=0.5) == 10.0
    # Breakout ratio 2.0 saturates the 0-20 band.
    assert volume_points(volume_ratio=2.0) == 20.0
    # Breakout wins over a weak dry-up reading.
    assert volume_points(avg_vcp_volume_ratio=0.9, volume_ratio=2.0) == 20.0
    # Precomputed component points are accepted directly.
    assert volume_points(pts_volume=20.0) == 20.0
    assert volume_points() is None


def test_volume_points_accepts_raw_volume_pair() -> None:
    assert volume_points(latest_volume=2_000_000, avg_vol_50=1_000_000) == 20.0


def test_cohort_percentile_is_mid_rank() -> None:
    assert cohort_percentile([10, 20, 30, 40], 10) == 12.5
    assert cohort_percentile([10, 20, 30, 40], 40) == 87.5
    assert cohort_percentile([10, 10, 30, 30], 10) == 25.0
    assert cohort_percentile([], 5) == 0.0


# --------------------------------------------------------------------------- #
# Feature extraction from live signal rows
# --------------------------------------------------------------------------- #
def test_features_from_signal_row_uses_live_field_names() -> None:
    row = {
        "close_vs_sma200_pct": 0.10,  # midpoint of the trend band -> 50
        "volume_ratio": 1.5,  # halfway to the 2.0 breakout cap -> 50
        "score_components": {"pct_from_52w_high": 0.85},  # -> 50
        "prob_rank": {"expected_return_40d": 0.03},  # -> 50
    }
    feats = features_from_signal_row(row, bounds=BOUNDS)
    assert feats["trend_norm"] == pytest.approx(50.0)
    assert feats["volume_norm"] == pytest.approx(50.0)
    assert feats["prox_52w_norm"] == pytest.approx(50.0)
    assert feats["ml_expected_return_norm"] == pytest.approx(50.0)


def test_features_derive_trend_from_price_and_sma_when_pct_absent() -> None:
    feats = features_from_signal_row({"price": 110.0, "sma_200": 100.0}, bounds=BOUNDS)
    assert feats["trend_norm"] == pytest.approx(50.0, abs=0.01)


def test_raw_features_prefer_score_components_over_top_level() -> None:
    raw = raw_features_from_signal_row(
        {"score_components": {"pts_volume": 12.0}, "pts_volume": 3.0, "pct_from_52w_high": 0.9}
    )
    assert raw["volume_points"] == 12.0
    assert raw["pct_from_52w_high"] == 0.9


def test_raw_features_report_none_when_absent() -> None:
    raw = raw_features_from_signal_row({})
    assert raw == {
        "close_vs_sma200_pct": None,
        "volume_points": None,
        "pct_from_52w_high": None,
        "expected_return_40d": None,
    }


# --------------------------------------------------------------------------- #
# Blend
# --------------------------------------------------------------------------- #
def test_compute_historical_rank_blends_weighted_features() -> None:
    row = {"close_vs_sma200_pct": 0.10, "volume_ratio": 2.0}
    out = compute_historical_rank(row, _artifact({"trend_norm": 0.5, "volume_norm": 0.5}))
    assert out is not None
    # (50 * 0.5 + 100 * 0.5) / 1.0
    assert out["score"] == pytest.approx(75.0)
    assert out["coverage"] == 1.0
    assert out["model_id"] == "hist_blend_test"


def test_compute_historical_rank_renormalizes_over_available_features() -> None:
    """A missing feature must shift toward remaining evidence, not toward zero."""
    row = {"close_vs_sma200_pct": 0.10}  # no volume evidence at all
    out = compute_historical_rank(row, _artifact({"trend_norm": 0.5, "volume_norm": 0.5}))
    assert out is not None
    assert out["score"] == pytest.approx(50.0)
    assert out["missing_features"] == ["volume_norm"]
    assert out["coverage"] == 0.5


def test_compute_historical_rank_returns_none_when_nothing_scorable() -> None:
    assert compute_historical_rank({}, _artifact({"trend_norm": 1.0})) is None


def test_compute_historical_rank_returns_none_without_weights() -> None:
    assert compute_historical_rank({"close_vs_sma200_pct": 0.1}, {"weights": {}}) is None


def test_compute_historical_rank_applies_risk_caps_only_downward() -> None:
    row = {"close_vs_sma200_pct": 0.20}  # top of the trend band -> 100
    clean = compute_historical_rank(row, _artifact())
    risky = compute_historical_rank(
        {**row, "sec_risk_tag": "high", "forensic_flags": ["beneish_manipulator"]},
        _artifact(),
    )
    assert clean is not None and risky is not None
    assert clean["score"] == 100.0
    assert risky["score"] < clean["score"]
    assert any("risk cap" in r for r in risky["reasons"])


def test_compute_historical_rank_reasons_name_features_and_weights() -> None:
    out = compute_historical_rank({"close_vs_sma200_pct": 0.10}, _artifact())
    assert out is not None
    assert any("trend_norm" in r and "w1.00" in r for r in out["reasons"])


# --------------------------------------------------------------------------- #
# Artifact loading
# --------------------------------------------------------------------------- #
def test_load_blend_artifact_missing_returns_none(tmp_path) -> None:
    assert load_blend_artifact(tmp_path, "nope/missing.json") is None


def test_load_blend_artifact_rejects_weightless_payload(tmp_path) -> None:
    path = tmp_path / "blend.json"
    path.write_text(json.dumps({"model_id": "x", "weights": {}}), encoding="utf-8")
    assert load_blend_artifact(tmp_path, "blend.json") is None


def test_load_blend_artifact_reads_relative_path(tmp_path) -> None:
    path = tmp_path / "blend.json"
    path.write_text(json.dumps(_artifact()), encoding="utf-8")
    loaded = load_blend_artifact(tmp_path, "blend.json")
    assert loaded is not None
    assert loaded["weights"] == {"trend_norm": 1.0}


def test_shipped_artifact_is_loadable_and_leakage_free() -> None:
    """The committed blend fixture must load and must not carry the leaked ML feature.

    The fitted file under research_store/models is gitignored, so CI loads the
    shape fixture instead of a local operator artifact.
    """
    skill = Path(__file__).resolve().parents[1]
    artifact = load_blend_artifact(skill, "tests/fixtures/historical_rank_blend.json")
    assert artifact is not None, "tests/fixtures/historical_rank_blend.json missing"
    assert artifact["weights"], "shipped blend has no weights"
    assert "ml_expected_return_norm" not in artifact["weights"]
    worst = artifact.get("fit", {}).get("oos_ic_worst_era")
    assert worst is not None and worst > 0, "every era must have positive out-of-sample IC"
    # Fitted bounds must be present for each weighted feature, otherwise the
    # runtime silently falls back to composite_score's saturating caps.
    bounds = artifact.get("feature_bounds") or {}
    for name in artifact["weights"]:
        assert RAW_KEYS[name] in bounds, f"no fitted bounds for {name}"


def test_shipped_artifact_resolves_the_book_instead_of_saturating() -> None:
    """A ranker that pins much of the book at 100 cannot discriminate."""
    skill = Path(__file__).resolve().parents[1]
    artifact = load_blend_artifact(skill, "tests/fixtures/historical_rank_blend.json")
    assert artifact is not None
    resolution = artifact.get("fit", {}).get("score_resolution") or {}
    assert resolution.get("share_at_max") is not None
    assert resolution["share_at_max"] < 0.10, "too much of the panel ties at the maximum score"
