"""Unit tests for the analog overlay (core/scoring_analog.py)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.scoring_analog import (
    FEATURE_NAMES,
    FORBIDDEN_INPUTS,
    AnalogLibrary,
    compact_library_row,
    features_from_signal_row,
    fit_feature_bounds,
    label_outcome_rows,
    load_analog_artifact,
    raw_features_from_signal_row,
    reconstruct_score_components,
    score_analog,
)
from research.leakage import assert_no_label_columns_in_features
from scripts.build_analog_library import build_library_from_rows

BOUNDS = {
    "close_vs_sma200_pct": {"lo": 0.0, "hi": 0.20},
    "pct_from_52w_high": {"lo": 0.70, "hi": 1.00},
    "volume_points": {"lo": 0.0, "hi": 20.0},
    "pts_mirofish": {"lo": 0.0, "hi": 15.0},
    "p_up_calibrated": {"lo": 0.40, "hi": 0.60},
}


def _row(**overrides: object) -> dict:
    base = {
        "ticker": "AAA",
        "entry_date": "2020-03-01",
        "era": "crash_recovery",
        "close_vs_sma200_pct": 0.10,
        "pct_from_52w_high": 0.85,
        "volume_ratio": 1.5,
        "pts_mirofish": 7.5,
        "p_up_calibrated": 0.50,
        "net_return": 0.04,
        "hold_days": 28,
        "exit_reason": "time_exit",
    }
    base.update(overrides)
    return base


def _library(rows: list[dict], *, k: int = 5, min_n: int = 4) -> AnalogLibrary:
    labeled = label_outcome_rows(rows)
    packed = [compact_library_row(r, bounds=BOUNDS) for r in labeled]
    compact = [p for p in packed if p is not None]
    return AnalogLibrary(
        model_id="analog_test",
        feature_names=FEATURE_NAMES,
        bounds=BOUNDS,
        k=k,
        min_n=min_n,
        rows=compact,
        priors={"crash_recovery": {"p_win": 0.45, "pf": 1.05, "n": len(compact)}},
    )


def test_feature_list_has_no_label_leakage() -> None:
    assert assert_no_label_columns_in_features(list(FEATURE_NAMES)) == []
    assert not (FORBIDDEN_INPUTS & set(FEATURE_NAMES))
    assert "ticker" not in FEATURE_NAMES
    assert "expected_return_40d" not in FEATURE_NAMES


def test_raw_features_parity_flat_trade_vs_nested_components() -> None:
    nested = {
        "close_vs_sma200_pct": 0.12,
        "score_components": {
            "pct_from_52w_high": 0.90,
            "pts_volume": 12.0,
            "pts_mirofish": 8.0,
        },
        "advisory": {"p_up_10d": 0.55},
        "net_return": 0.99,
        "hold_days": 3,
        "exit_reason": "stop",
        "ticker": "NVDA",
    }
    flat = {
        "close_vs_sma200_pct": 0.12,
        "pct_from_52w_high": 0.90,
        "pts_volume": 12.0,
        "pts_mirofish": 8.0,
        "p_up_calibrated": 0.55,
        "net_return": -0.40,
        "ticker": "AAPL",
    }
    assert raw_features_from_signal_row(nested) == raw_features_from_signal_row(flat)


def test_reconstruct_score_components_from_flat_trade_columns() -> None:
    comps = reconstruct_score_components({"pts_volume": 11.0, "pts_52w": 30.0})
    assert comps["pts_volume"] == 11.0
    assert comps["pts_52w"] == 30.0


def test_raw_features_ignore_forward_returns_and_ticker() -> None:
    raw = raw_features_from_signal_row(
        {
            "close_vs_sma200_pct": 0.10,
            "ret_40d_fwd": 0.80,
            "net_return": 0.80,
            "expected_return_40d": 0.40,
            "ticker": "NVDA",
        }
    )
    assert set(raw) == set(FEATURE_NAMES)
    assert "ret_40d_fwd" not in raw
    assert "ticker" not in raw
    assert raw["close_vs_sma200_pct"] == pytest.approx(0.10)


def test_score_analog_returns_none_not_zero_when_unscored() -> None:
    lib = _library([_row(ticker=f"T{i}", close_vs_sma200_pct=0.05 + i * 0.01, net_return=0.1) for i in range(12)])
    assert score_analog({}, lib) is None
    assert score_analog({"ticker": "ZZZ"}, lib) is None


def test_shrinkage_when_neighborhood_thinner_than_min_n() -> None:
    winners = [
        _row(ticker=f"W{i}", entry_date=f"2020-01-{i + 1:02d}", net_return=0.20, close_vs_sma200_pct=0.10)
        for i in range(6)
    ]
    losers = [
        _row(ticker=f"L{i}", entry_date=f"2020-02-{i + 1:02d}", net_return=-0.15, close_vs_sma200_pct=0.11)
        for i in range(6)
    ]
    lib = _library(winners + losers, k=3, min_n=20)
    out = score_analog(_row(ticker="QRY", entry_date="2021-06-01", close_vs_sma200_pct=0.10), lib, k=3, min_n=20)
    assert out is not None
    assert out["analog_thin"] is True
    assert out["analog_n"] == 3
    # Pulled toward the 0.45 era prior, not a raw 1.0 / 0.0 extreme.
    assert 0.35 <= float(out["analog_p_win"]) <= 0.70


def test_ticker_identity_is_not_a_distance_feature() -> None:
    rows = [_row(ticker=f"T{i}", entry_date=f"2019-03-{i + 1:02d}", close_vs_sma200_pct=0.08 + (i % 3) * 0.01, net_return=0.05 if i % 2 else -0.04) for i in range(15)]
    lib = _library(rows, k=5, min_n=4)
    a = score_analog(_row(ticker="NVDA", entry_date="2021-01-01", close_vs_sma200_pct=0.09), lib)
    b = score_analog(_row(ticker="AAPL", entry_date="2021-01-01", close_vs_sma200_pct=0.09), lib)
    assert a is not None and b is not None
    assert a["analog_p_win"] == b["analog_p_win"]
    assert a["analog_n"] == b["analog_n"]


def test_walk_forward_library_excludes_holdout_era_rows() -> None:
    train = [
        _row(
            ticker=f"TR{i}",
            era="bear_rates",
            entry_date=f"2022-03-{i + 1:02d}",
            close_vs_sma200_pct=0.08,
            net_return=0.12 if i < 8 else -0.08,
        )
        for i in range(16)
    ]
    holdout = _row(ticker="HOLD", era="recent_current", entry_date="2024-06-01", close_vs_sma200_pct=0.08)
    artifact = build_library_from_rows(train, sources=["unit"], k=5, min_n=4, fail_closed=False)
    assert all(r.get("era") != "recent_current" for r in artifact["rows"])
    lib = AnalogLibrary(
        model_id="wf",
        feature_names=FEATURE_NAMES,
        bounds=artifact["feature_bounds"],
        k=5,
        min_n=4,
        rows=artifact["rows"],
        priors=artifact["era_priors"],
    )
    out = score_analog(holdout, lib, k=5, min_n=4)
    assert out is not None
    assert all((nb.get("ticker") or "") != "HOLD" for nb in out["analog_neighbors"])
    assert "recent_current" not in str(out["analog_regime"]) or out["analog_regime"] == "all_eras"


def test_self_row_is_excluded_from_neighbors() -> None:
    rows = [_row(ticker="SELF", entry_date="2020-03-01", close_vs_sma200_pct=0.10, net_return=0.50)]
    rows.extend(
        _row(ticker=f"O{i}", entry_date=f"2020-04-{i + 1:02d}", close_vs_sma200_pct=0.10, net_return=-0.10)
        for i in range(10)
    )
    lib = _library(rows, k=5, min_n=4)
    out = score_analog(_row(ticker="SELF", entry_date="2020-03-01", close_vs_sma200_pct=0.10), lib, k=5, min_n=4)
    assert out is not None
    assert all(not (nb.get("ticker") == "SELF" and nb.get("entry_date") == "2020-03-01") for nb in out["analog_neighbors"])


def test_normalize_uses_fitted_bounds_not_trend_cap() -> None:
    wide = {"close_vs_sma200_pct": {"lo": 0.0, "hi": 0.80}}
    feats = features_from_signal_row({"close_vs_sma200_pct": 0.40}, bounds=wide)
    assert feats["close_vs_sma200_pct"] == pytest.approx(50.0)


def test_load_analog_artifact_missing_returns_none(tmp_path: Path) -> None:
    assert load_analog_artifact(tmp_path, "missing.json") is None


def test_load_analog_artifact_rejects_empty_rows(tmp_path: Path) -> None:
    path = tmp_path / "analog_library.json"
    path.write_text(json.dumps({"model_id": "x", "rows": []}), encoding="utf-8")
    assert load_analog_artifact(tmp_path, "analog_library.json") is None


def test_fit_bounds_are_p1_p99() -> None:
    raws = [{"close_vs_sma200_pct": float(i) / 100.0} for i in range(100)]
    for row in raws:
        row.update({"pct_from_52w_high": None, "volume_points": None, "pts_mirofish": None, "p_up_calibrated": None})
    bounds = fit_feature_bounds(raws)
    assert bounds["close_vs_sma200_pct"]["lo"] == pytest.approx(0.01, abs=0.02)
    assert bounds["close_vs_sma200_pct"]["hi"] == pytest.approx(0.98, abs=0.02)
