"""Phase D: PROB_RANK_MODE shadow/live runtime adapter."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

SKILL_DIR = Path(__file__).resolve().parent.parent
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

pytest.importorskip("lightgbm")

from config import get_prob_rank_mode, get_prob_rank_top_n  # noqa: E402
from research.runtime import (  # noqa: E402
    apply_prob_rank_cohort,
    clear_model_cache,
    resolve_model_dir,
    score_signal_with_bars,
)
from research.train import train_prob_rank_model  # noqa: E402


def _train_tiny_model(tmp_path: Path) -> Path:
    rng = np.random.default_rng(1)
    rows = []
    for era, start in (
        ("late_bull", "2016-01-04"),
        ("volatility_chop", "2018-01-02"),
        ("crash_recovery", "2020-01-02"),
        ("bear_rates", "2022-01-03"),
        ("recent_current", "2024-01-02"),
    ):
        dates = pd.bdate_range(start, periods=40)
        for i, dt in enumerate(dates):
            f1 = float(rng.normal())
            ret = 0.01 * f1 + float(rng.normal(0, 0.01))
            rows.append(
                {
                    "asof_date": str(dt.date()),
                    "ticker": f"T{i % 3}",
                    "era": era,
                    "dist_sma50_pct": f1 * 0.01,
                    "volume_score": 0.5,
                    "atr_pct": 0.02,
                    "ret_20d_prev": 0.01,
                    "stage_score": 0.6,
                    "breakout_quality_score": 0.55,
                    "feature_coverage": 1.0,
                    "ret_40d_fwd": ret,
                    "dataset_id": "tiny",
                }
            )
    df = pd.DataFrame(rows)
    cols = [
        "dist_sma50_pct",
        "volume_score",
        "atr_pct",
        "ret_20d_prev",
        "stage_score",
        "breakout_quality_score",
    ]
    art = train_prob_rank_model(
        df,
        cols,
        skill_dir=tmp_path,
        num_boost_round=20,
        early_stopping_rounds=5,
        write=True,
    )
    return tmp_path / "research_store" / "models" / art["model_id"]


def _bars(n: int = 260) -> pd.DataFrame:
    idx = pd.bdate_range("2023-01-03", periods=n)
    close = np.linspace(50, 120, n)
    return pd.DataFrame(
        {
            "open": close,
            "high": close * 1.01,
            "low": close * 0.99,
            "close": close,
            "volume": np.full(n, 1_000_000.0),
        },
        index=idx,
    )


def test_config_defaults_off(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Isolate from the repo `.env` (which may enable shadow locally).
    monkeypatch.delenv("PROB_RANK_MODE", raising=False)
    monkeypatch.delenv("PROB_RANK_TOP_N", raising=False)
    assert get_prob_rank_mode(tmp_path) == "off"
    assert get_prob_rank_top_n(tmp_path) == 5


def test_resolve_model_dir_picks_latest(tmp_path: Path) -> None:
    model_dir = _train_tiny_model(tmp_path)
    clear_model_cache()
    found = resolve_model_dir(tmp_path)
    assert found is not None
    assert found.resolve() == model_dir.resolve()


def test_score_signal_with_bars_attaches_block(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    model_dir = _train_tiny_model(tmp_path)
    clear_model_cache()
    monkeypatch.setenv("PROB_RANK_MODE", "shadow")
    monkeypatch.setenv("PROB_RANK_MODEL_DIR", str(model_dir))
    signal = {"ticker": "ABC", "signal_score": 70.0}
    block = score_signal_with_bars(signal, _bars(), skill_dir=tmp_path, include_shap=False)
    assert block is not None
    assert "expected_return_40d" in block
    assert signal.get("prob_rank") is not None
    assert signal.get("expected_return_40d") is not None


def test_shadow_cohort_does_not_drop(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PROB_RANK_MODE", "shadow")
    monkeypatch.setenv("PROB_RANK_TOP_N", "1")
    signals = [
        {"ticker": "A", "expected_return_40d": 0.05, "prob_rank": {}},
        {"ticker": "B", "expected_return_40d": 0.01, "prob_rank": {}},
        {"ticker": "C", "expected_return_40d": -0.02, "prob_rank": {}},
    ]
    diagnostics: dict = {}
    out = apply_prob_rank_cohort(signals, diagnostics, SKILL_DIR)
    assert len(out) == 3
    assert diagnostics["prob_rank_would_keep"] == 1
    assert diagnostics["prob_rank_would_drop"] == 2
    assert out[0]["prob_rank"]["cross_section_rank"] in (1, 2, 3)


def test_live_cohort_keeps_top_n(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PROB_RANK_MODE", "live")
    monkeypatch.setenv("PROB_RANK_TOP_N", "2")
    signals = [
        {"ticker": "A", "expected_return_40d": 0.05, "prob_rank": {}},
        {"ticker": "B", "expected_return_40d": 0.01, "prob_rank": {}},
        {"ticker": "C", "expected_return_40d": -0.02, "prob_rank": {}},
    ]
    diagnostics: dict = {}
    out = apply_prob_rank_cohort(signals, diagnostics, SKILL_DIR)
    assert len(out) == 2
    tickers = {s["ticker"] for s in out}
    assert tickers == {"A", "B"}
    assert diagnostics["prob_rank_dropped"] == 1


def test_off_mode_passthrough(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PROB_RANK_MODE", "off")
    signals = [{"ticker": "A", "expected_return_40d": 0.1}]
    diagnostics: dict = {}
    out = apply_prob_rank_cohort(signals, diagnostics, SKILL_DIR)
    assert out is signals or len(out) == 1
    assert diagnostics.get("prob_rank_mode") == "off"
