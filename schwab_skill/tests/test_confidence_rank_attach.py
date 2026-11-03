"""Scanner setup-confidence stays shadow: annotate only, never retarget sort."""

from __future__ import annotations

import json
from collections import defaultdict
from typing import Any

import pandas as pd

import signal_scanner
from core.scoring_confidence import ARTIFACT_SCHEMA


def _write_artifact(tmp_path) -> None:
    models = tmp_path / "research_store" / "models"
    models.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": ARTIFACT_SCHEMA,
        "model_id": "conf_fixture",
        "promote": False,
        "features": [
            {"name": "sma_200_slope", "sign": -1.0, "bounds": [0.0, 1.0]},
            {"name": "trend_strength_score", "sign": -1.0, "bounds": [0.0, 1.0]},
        ],
    }
    (models / "scan_confidence.json").write_text(json.dumps(payload), encoding="utf-8")


def _bars() -> pd.DataFrame:
    idx = pd.date_range("2024-01-01", periods=300, freq="B")
    close = pd.Series(range(1, 301), index=idx, dtype=float)
    return pd.DataFrame(
        {
            "Open": close,
            "High": close * 1.01,
            "Low": close * 0.99,
            "Close": close,
            "Volume": [1_000_000] * len(idx),
        },
        index=idx,
    )


def _signal(sort_score: float = 70.0, **extra: Any) -> dict[str, Any]:
    row = {
        "ticker": "AAA",
        "sort_score": sort_score,
        "signal_score": sort_score,
        "data_provider": "schwab",
    }
    row.update(extra)
    return row


def test_shadow_attach_scores_without_touching_sort(tmp_path, monkeypatch) -> None:
    from config import clear_env_cache

    _write_artifact(tmp_path)
    monkeypatch.setenv("CONFIDENCE_RANK_MODE", "shadow")
    clear_env_cache()

    signal = _signal(70.0)
    diag: dict[str, Any] = defaultdict(int)
    signal_scanner._attach_setup_confidence(signal, _bars(), tmp_path, diag)

    assert signal["sort_score"] == 70.0
    assert signal["setup_confidence"] is not None
    assert 0.0 <= signal["setup_confidence"] <= 100.0
    assert signal["setup_confidence_model_id"] == "conf_fixture"
    assert diag["confidence_rank_scored"] == 1


def test_live_mode_is_refused(tmp_path, monkeypatch) -> None:
    from config import clear_env_cache

    _write_artifact(tmp_path)
    monkeypatch.setenv("CONFIDENCE_RANK_MODE", "live")
    clear_env_cache()

    signal = _signal(80.0)
    diag: dict[str, Any] = defaultdict(int)
    signal_scanner._attach_setup_confidence(signal, _bars(), tmp_path, diag)

    assert diag["confidence_rank_skipped"] == "live_not_approved"
    assert signal["sort_score"] == 80.0
    assert "setup_confidence" not in signal


def test_off_mode_does_nothing(tmp_path, monkeypatch) -> None:
    from config import clear_env_cache

    _write_artifact(tmp_path)
    monkeypatch.setenv("CONFIDENCE_RANK_MODE", "off")
    clear_env_cache()

    signal = _signal(60.0)
    diag: dict[str, Any] = defaultdict(int)
    signal_scanner._attach_setup_confidence(signal, _bars(), tmp_path, diag)

    assert "setup_confidence" not in signal
    assert not diag


def test_missing_artifact_fails_closed(tmp_path, monkeypatch) -> None:
    from config import clear_env_cache

    monkeypatch.setenv("CONFIDENCE_RANK_MODE", "shadow")
    clear_env_cache()

    signal = _signal(55.0)
    diag: dict[str, Any] = defaultdict(int)
    signal_scanner._attach_setup_confidence(signal, _bars(), tmp_path, diag)

    assert diag["confidence_rank_skipped"] == "artifact_missing"
    assert signal["sort_score"] == 55.0
    assert "setup_confidence" not in signal


def test_diagnostics_rollup_reports_mode_and_counts(tmp_path, monkeypatch) -> None:
    from config import clear_env_cache

    monkeypatch.setenv("CONFIDENCE_RANK_MODE", "shadow")
    clear_env_cache()

    signals = [
        {"ticker": "AAA", "setup_confidence": 61.0, "setup_confidence_model_id": "conf_fixture"},
        {"ticker": "BBB"},
    ]
    diagnostics: dict[str, Any] = {}
    signal_scanner._stamp_confidence_rank_diagnostics(signals, diagnostics, tmp_path)

    assert diagnostics["confidence_rank_mode"] == "shadow"
    assert diagnostics["confidence_rank_scored"] == 1
    assert diagnostics["confidence_rank_unscored"] == 1
    assert diagnostics["confidence_rank_model_id"] == "conf_fixture"
