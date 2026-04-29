from __future__ import annotations

import config


def test_signal_scan_full_universe_defaults_false(monkeypatch):
    monkeypatch.delenv("SIGNAL_SCAN_FULL_UNIVERSE", raising=False)
    config.clear_env_cache()
    assert config.get_signal_scan_full_universe() is False


def test_signal_scan_full_universe_can_be_enabled(monkeypatch):
    monkeypatch.setenv("SIGNAL_SCAN_FULL_UNIVERSE", "1")
    config.clear_env_cache()
    assert config.get_signal_scan_full_universe() is True
