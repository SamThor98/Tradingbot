from __future__ import annotations

from pathlib import Path

import signal_scanner

SKILL_DIR = Path(__file__).resolve().parents[1]


def test_scanner_quality_shadow_does_not_hard_drop_volume(monkeypatch) -> None:
    monkeypatch.setenv("QUALITY_GATES_MODE", "shadow")
    assert signal_scanner._quality_mode_should_filter(["weak_breakout_volume"], SKILL_DIR) is False
    assert signal_scanner._quality_mode_should_filter(["low_signal_score"], SKILL_DIR) is False


def test_scanner_quality_soft_hard_drops_volume(monkeypatch) -> None:
    monkeypatch.setenv("QUALITY_GATES_MODE", "soft")
    assert signal_scanner._quality_mode_should_filter(["weak_breakout_volume"], SKILL_DIR) is True


def test_scanner_quality_off_does_not_filter(monkeypatch) -> None:
    monkeypatch.setenv("QUALITY_GATES_MODE", "off")
    assert signal_scanner._quality_mode_should_filter(["weak_breakout_volume", "low_signal_score"], SKILL_DIR) is False
