"""Phase B research feature platform: registry, engine, materializer."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

SKILL_DIR = Path(__file__).resolve().parent.parent
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

from research.feature_engine import (  # noqa: E402
    compute_feature_row,
    compute_ohlcv_features,
    window_asof,
)
from research.materialize import materialize_ticker, write_feature_panel  # noqa: E402
from research.paths import ensure_research_store_layout, panels_features_dir  # noqa: E402
from research.registry import (  # noqa: E402
    FEATURE_SCHEMA_VERSION,
    align_ops_features,
    enabled_feature_names,
    extract_registry_aligned_from_signal,
    feature_coverage,
    load_feature_registry,
)


def _synthetic_uptrend(n: int = 320, seed: int = 7) -> pd.DataFrame:
    """Build OHLCV that typically satisfies Stage 2 near the end."""
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2019-01-02", periods=n)
    # Smooth uptrend with mild noise
    close = 50 + np.linspace(0, 80, n) + rng.normal(0, 0.4, n).cumsum() * 0.05
    close = np.maximum(close, 5.0)
    high = close * (1.0 + rng.uniform(0.001, 0.01, n))
    low = close * (1.0 - rng.uniform(0.001, 0.01, n))
    open_ = close * (1.0 + rng.normal(0, 0.002, n))
    # Volume dry-up into the end (VCP-friendly)
    volume = rng.integers(800_000, 1_200_000, n).astype(float)
    volume[-15:] = volume[-15:] * 0.55
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
        index=idx,
    )


def test_load_feature_registry_schema_v1() -> None:
    reg = load_feature_registry(reload=True)
    assert int(reg["schema_version"]) == FEATURE_SCHEMA_VERSION
    names = enabled_feature_names(reg, ohlcv_only=True)
    assert "dist_sma50_pct" in names
    assert "volume_score" in names
    assert "vcp_score" in names
    # Deferred / disabled must not appear in ohlcv enabled list
    assert "float_shares" not in names
    assert "rs_percentile_252d" not in names


def test_align_ops_features_maps_aliases() -> None:
    aligned = align_ops_features(
        {
            "close_vs_sma50_pct": 0.05,
            "close_vs_sma200_pct": 0.12,
            "advisory_prob": 0.61,
            "signal_score": 72.0,
        }
    )
    assert aligned["dist_sma50_pct"] == pytest.approx(0.05)
    assert aligned["dist_sma200_pct"] == pytest.approx(0.12)
    assert aligned["advisory_p_up_10d"] == pytest.approx(0.61)
    assert aligned["signal_score"] == pytest.approx(72.0)


def test_extract_registry_aligned_from_signal() -> None:
    signal = {
        "signal_score": 70.0,
        "close_vs_sma50_pct": 0.03,
        "rank_score_v2": 55.0,
        "p_up_calibrated": 0.58,
        "advisory": {"p_up_10d": 0.58},
        "score_components": {"pct_from_52w_high": 0.92, "atr_14": 1.5},
        "pead_surprise_pct": 4.2,
    }
    aligned = extract_registry_aligned_from_signal(signal)
    assert aligned["dist_sma50_pct"] == pytest.approx(0.03)
    assert aligned["advisory_p_up_10d"] == pytest.approx(0.58)
    assert aligned["pct_from_52w_high"] == pytest.approx(0.92)
    assert aligned["rank_score_v2"] == pytest.approx(55.0)


def test_window_asof_is_point_in_time() -> None:
    df = _synthetic_uptrend(100)
    asof = str(df.index[50].date())
    pit = window_asof(df, asof)
    assert pit.index.max().date() == pd.Timestamp(asof).date()
    assert len(pit) == 51


def test_compute_ohlcv_features_produces_continuous_scores() -> None:
    df = _synthetic_uptrend()
    feats = compute_ohlcv_features(df)
    assert feats
    for key in (
        "sma_50",
        "sma_200",
        "dist_sma50_pct",
        "dist_sma200_pct",
        "atr_pct",
        "volume_ratio",
        "stage_score",
        "vcp_score",
        "breakout_quality_score",
        "ret_20d_prev",
        "volume_score",
    ):
        assert key in feats
        assert feats[key] is not None
    # Scores are continuous in [0, 1]
    assert 0.0 <= float(feats["stage_score"]) <= 1.0
    assert 0.0 <= float(feats["vcp_score"]) <= 1.0


def test_compute_feature_row_stage2_gate() -> None:
    df = _synthetic_uptrend()
    asof = str(df.index[-1].date())
    row = compute_feature_row(ticker="TEST", df=df, asof_date=asof, require_stage2=True)
    # Synthetic uptrend should usually pass Stage 2 at the end
    assert row is not None
    assert row["ticker"] == "TEST"
    assert row["feature_schema_version"] == FEATURE_SCHEMA_VERSION
    assert 0.0 <= float(row["feature_coverage"]) <= 1.0
    assert float(row["feature_coverage"]) > 0.5


def test_feature_coverage_handles_nan() -> None:
    cov = feature_coverage({"a": 1.0, "b": None, "c": float("nan")}, ["a", "b", "c"])
    assert cov == pytest.approx(1.0 / 3.0)


def test_materialize_writes_parquet(tmp_path: Path) -> None:
    df = _synthetic_uptrend()
    asof = str(df.index[-1].date())
    frame = materialize_ticker(
        ticker="SYN",
        bars=df,
        asof_dates=[asof],
        skill_dir=tmp_path,
        require_stage2=False,
        write=True,
        bar_provider="test",
    )
    assert len(frame) == 1
    ensure_research_store_layout(tmp_path, schema_version=FEATURE_SCHEMA_VERSION)
    year = pd.Timestamp(asof).year
    out = panels_features_dir(schema_version=FEATURE_SCHEMA_VERSION, skill_dir=tmp_path) / f"year={year}" / "SYN.parquet"
    assert out.is_file()
    loaded = pd.read_parquet(out)
    assert len(loaded) == 1
    assert loaded.iloc[0]["ticker"] == "SYN"
    assert "volume_score" in loaded.columns


def test_write_feature_panel_dedupes(tmp_path: Path) -> None:
    df = _synthetic_uptrend()
    asof = str(df.index[-1].date())
    row = compute_feature_row(
        ticker="DEDUP",
        df=df,
        asof_date=asof,
        require_stage2=False,
    )
    assert row is not None
    frame = pd.DataFrame([row])
    write_feature_panel(frame, skill_dir=tmp_path, ticker="DEDUP")
    row2 = dict(row)
    row2["volume_score"] = 0.99
    write_feature_panel(pd.DataFrame([row2]), skill_dir=tmp_path, ticker="DEDUP")
    year = pd.Timestamp(asof).year
    path = panels_features_dir(schema_version=FEATURE_SCHEMA_VERSION, skill_dir=tmp_path) / f"year={year}" / "DEDUP.parquet"
    loaded = pd.read_parquet(path)
    assert len(loaded) == 1
    assert float(loaded.iloc[0]["volume_score"]) == pytest.approx(0.99)


def test_registry_json_is_valid_on_disk() -> None:
    path = SKILL_DIR / "research" / "feature_registry.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 1
    assert all("name" in f and "enabled" in f for f in payload["features"])
