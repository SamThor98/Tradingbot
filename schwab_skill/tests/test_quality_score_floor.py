"""The quality score floor has to sit on the score the live formula can produce."""

from __future__ import annotations

from pathlib import Path

import signal_scanner
from config import get_quality_min_signal_score_effective, quality_signal_score_ceiling

SKILL_DIR = Path(__file__).resolve().parents[1]


def _live_scale(monkeypatch) -> None:
    monkeypatch.setenv("QUALITY_MIN_SIGNAL_SCORE", "52")
    monkeypatch.setenv("QUALITY_REQUIRE_BREAKOUT_VOLUME", "true")
    monkeypatch.setenv("QUALITY_BREAKOUT_VOLUME_MIN_RATIO", "0.9")
    monkeypatch.setenv("SCORE_PTS_SMA_MULTIPLIER", "0")
    monkeypatch.setenv("SCORE_PTS_SMA_CAP", "25")


def test_ceiling_excludes_inactive_sma_and_mirofish(monkeypatch) -> None:
    _live_scale(monkeypatch)
    assert quality_signal_score_ceiling(SKILL_DIR) == 42.0
    assert get_quality_min_signal_score_effective(SKILL_DIR) == 27.0


def test_sma_multiplier_restores_the_configured_floor(monkeypatch) -> None:
    _live_scale(monkeypatch)
    monkeypatch.setenv("SCORE_PTS_SMA_MULTIPLIER", "1")
    # Volume-compatible ceiling is 40 + 25 + 2 = 67, so the raw floor of 52 stands.
    assert get_quality_min_signal_score_effective(SKILL_DIR) == 52.0


def test_near_high_name_with_passing_volume_is_not_a_low_score(monkeypatch) -> None:
    _live_scale(monkeypatch)
    # RNR on 2026-10-08: 34.83, all of it from 52-week proximity, volume already passed.
    signal = {
        "ticker": "RNR",
        "entry_family": "stage2",
        "signal_score": 34.83,
        "latest_volume": 1_200_000.0,
        "avg_vol_50": 1_000_000.0,
    }
    reasons = signal_scanner._evaluate_quality_gates(signal, SKILL_DIR)
    assert "low_signal_score" not in reasons
    assert "weak_breakout_volume" not in reasons


def test_weak_breakout_volume_still_drops(monkeypatch) -> None:
    _live_scale(monkeypatch)
    signal = {
        "ticker": "LPG",
        "entry_family": "stage2",
        "signal_score": 36.06,
        "latest_volume": 800_000.0,
        "avg_vol_50": 1_000_000.0,
    }
    reasons = signal_scanner._evaluate_quality_gates(signal, SKILL_DIR)
    assert "low_signal_score" not in reasons
    assert "weak_breakout_volume" in reasons


def test_score_well_below_the_live_floor_still_fails(monkeypatch) -> None:
    _live_scale(monkeypatch)
    signal = {
        "ticker": "AAA",
        "entry_family": "stage2",
        "signal_score": 10.0,
        "latest_volume": 1_200_000.0,
        "avg_vol_50": 1_000_000.0,
    }
    reasons = signal_scanner._evaluate_quality_gates(signal, SKILL_DIR)
    assert "low_signal_score" in reasons
