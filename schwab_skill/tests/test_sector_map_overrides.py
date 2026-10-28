"""Sector map overrides stay off until SECTOR_MAP_OVERRIDE_MODE is set."""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

from config import get_sector_map_override_mode
from core.plugin_mode_workbench import resolve_modes
from sector_map_overrides import (
    accumulate_sector_map_override_view,
    describe_sector_map_override,
    lookup_sector_map_override,
)
from sector_strength import SECTOR_TO_ETF, get_ticker_sector_etf


def test_spdr_map_covers_known_disagreements() -> None:
    assert lookup_sector_map_override("AAPL") == "XLK"
    assert lookup_sector_map_override("UBER") == "XLI"
    assert lookup_sector_map_override("PAYX") == "XLI"
    assert lookup_sector_map_override("PKG") == "XLB"
    assert lookup_sector_map_override("KO") == "XLP"
    assert lookup_sector_map_override("brk-b") == "XLF"
    assert lookup_sector_map_override("BRK.B") == "XLF"
    assert lookup_sector_map_override("CACI") is None
    assert lookup_sector_map_override("SPY") is None


def test_consumer_defensive_alias_matches_yfinance_label() -> None:
    assert SECTOR_TO_ETF["consumer defensive"] == "XLP"
    assert SECTOR_TO_ETF["consumer defensives"] == "XLP"


def test_override_mode_defaults_off(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SECTOR_MAP_OVERRIDE_MODE", raising=False)
    assert get_sector_map_override_mode(tmp_path) == "off"
    assert resolve_modes(tmp_path)["sector_map_override"] == "off"


def _install_fake_yfinance(monkeypatch: pytest.MonkeyPatch, sector: str) -> None:
    fake = types.ModuleType("yfinance")

    class _Ticker:
        def __init__(self, _symbol: str) -> None:
            self.info = {"sector": sector}

    fake.Ticker = _Ticker
    monkeypatch.setitem(sys.modules, "yfinance", fake)


def test_off_and_shadow_keep_yfinance_etf(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SCHWAB_ONLY_DATA", "false")
    _install_fake_yfinance(monkeypatch, "Technology")

    monkeypatch.setenv("SECTOR_MAP_OVERRIDE_MODE", "off")
    assert get_ticker_sector_etf("UBER", skill_dir=tmp_path) == "XLK"

    monkeypatch.setenv("SECTOR_MAP_OVERRIDE_MODE", "shadow")
    assert get_ticker_sector_etf("UBER", skill_dir=tmp_path) == "XLK"
    view = describe_sector_map_override("UBER", "XLK", skill_dir=tmp_path)
    assert view["disagree"] is True
    assert view["override_etf"] == "XLI"
    assert view["applied"] is False


def test_live_uses_override_without_poisoning_yfinance_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SCHWAB_ONLY_DATA", "false")
    _install_fake_yfinance(monkeypatch, "Technology")
    monkeypatch.setenv("SECTOR_MAP_OVERRIDE_MODE", "live")
    assert get_ticker_sector_etf("UBER", skill_dir=tmp_path) == "XLI"

    monkeypatch.setenv("SECTOR_MAP_OVERRIDE_MODE", "off")
    assert get_ticker_sector_etf("UBER", skill_dir=tmp_path) == "XLK"


def test_live_override_survives_yfinance_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SCHWAB_ONLY_DATA", "false")
    fake = types.ModuleType("yfinance")

    class _Boom:
        def __init__(self, _symbol: str) -> None:
            raise RuntimeError("yahoo down")

    fake.Ticker = _Boom
    monkeypatch.setitem(sys.modules, "yfinance", fake)
    monkeypatch.setenv("SECTOR_MAP_OVERRIDE_MODE", "live")
    assert get_ticker_sector_etf("PAYX", skill_dir=tmp_path) == "XLI"

    monkeypatch.setenv("SECTOR_MAP_OVERRIDE_MODE", "off")
    assert get_ticker_sector_etf("PAYX", skill_dir=tmp_path) is None


def test_shadow_diagnostics_count_disagreements(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SECTOR_MAP_OVERRIDE_MODE", "shadow")
    sink: dict[str, object] = {
        "sector_map_override_evaluated": 0,
        "sector_map_override_disagree": 0,
        "sector_map_override_applied": 0,
        "sector_map_override_samples": [],
    }
    accumulate_sector_map_override_view(
        sink,
        describe_sector_map_override("UBER", "XLK", skill_dir=tmp_path),
    )
    accumulate_sector_map_override_view(
        sink,
        describe_sector_map_override("AAPL", "XLK", skill_dir=tmp_path),
    )
    assert sink["sector_map_override_evaluated"] == 2
    assert sink["sector_map_override_disagree"] == 1
    assert sink["sector_map_override_applied"] == 0
    samples = sink["sector_map_override_samples"]
    assert isinstance(samples, list)
    assert samples[0]["ticker"] == "UBER"
    assert samples[0]["override_etf"] == "XLI"


def test_off_diagnostics_do_not_count(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SECTOR_MAP_OVERRIDE_MODE", "off")
    sink: dict[str, object] = {}
    accumulate_sector_map_override_view(
        sink,
        describe_sector_map_override("UBER", "XLK", skill_dir=tmp_path),
    )
    assert sink["sector_map_override_mode"] == "off"
    assert "sector_map_override_evaluated" not in sink
