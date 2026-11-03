from __future__ import annotations

from pathlib import Path

import signal_scanner

SKILL_DIR = Path(__file__).resolve().parents[1]


def test_scanner_quality_shadow_does_not_hard_drop_volume(monkeypatch) -> None:
    monkeypatch.setenv("QUALITY_GATES_MODE", "shadow")
    assert signal_scanner._quality_mode_should_filter(["weak_breakout_volume"], SKILL_DIR) is False
    assert signal_scanner._quality_mode_should_filter(["low_signal_score"], SKILL_DIR) is False


def test_scanner_quality_soft_counts_volume_until_a_second_reason(monkeypatch) -> None:
    monkeypatch.setenv("QUALITY_GATES_MODE", "soft")
    monkeypatch.setenv("QUALITY_SOFT_MIN_REASONS", "1")
    assert signal_scanner._quality_mode_should_filter(["weak_breakout_volume"], SKILL_DIR) is False
    assert signal_scanner._quality_mode_should_filter(["low_signal_score"], SKILL_DIR) is True
    assert signal_scanner._quality_mode_should_filter(
        ["weak_breakout_volume", "low_signal_score"],
        SKILL_DIR,
    ) is True
    monkeypatch.setenv("QUALITY_SOFT_MIN_REASONS", "2")
    assert signal_scanner._quality_mode_should_filter(["low_signal_score"], SKILL_DIR) is False
    assert signal_scanner._quality_mode_should_filter(
        ["weak_breakout_volume", "low_signal_score"],
        SKILL_DIR,
    ) is True


def test_scanner_quality_hard_still_drops_volume_alone(monkeypatch) -> None:
    monkeypatch.setenv("QUALITY_GATES_MODE", "hard")
    assert signal_scanner._quality_mode_should_filter(["weak_breakout_volume"], SKILL_DIR) is True


def test_scanner_quality_off_does_not_filter(monkeypatch) -> None:
    monkeypatch.setenv("QUALITY_GATES_MODE", "off")
    assert signal_scanner._quality_mode_should_filter(["weak_breakout_volume", "low_signal_score"], SKILL_DIR) is False
