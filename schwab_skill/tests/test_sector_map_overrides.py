"""Sector map overrides stay off until SECTOR_MAP_OVERRIDE_MODE is set."""

from __future__ import annotations

import json
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
    assert lookup_sector_map_override("CACI") == "XLI"
    assert lookup_sector_map_override("SONO") == "XLY"
    assert lookup_sector_map_override("MSGR") is None
    assert lookup_sector_map_override("SPY") is None


# Exact yfinance-name table on main. Consumer Defensive is intentionally absent.
_MAIN_SECTOR_TO_ETF = {
    "technology": "XLK",
    "financial services": "XLF",
    "energy": "XLE",
    "healthcare": "XLV",
    "consumer cyclical": "XLY",
    "consumer defensives": "XLP",
    "consumer staples": "XLP",
    "basic materials": "XLB",
    "materials": "XLB",
    "industrials": "XLI",
    "utilities": "XLU",
    "communication services": "XLC",
    "real estate": "XLRE",
}


def test_sector_name_map_matches_main() -> None:
    assert SECTOR_TO_ETF == _MAIN_SECTOR_TO_ETF
    assert "consumer defensive" not in SECTOR_TO_ETF


def test_off_matches_main_for_consumer_defensive(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """off returns the same ETF main returns for yfinance's Consumer Defensive label."""
    monkeypatch.setenv("SCHWAB_ONLY_DATA", "false")
    monkeypatch.setenv("SECTOR_MAP_OVERRIDE_MODE", "off")
    _install_fake_yfinance(monkeypatch, "Consumer Defensive")
    # KO is in the XLP holdings. ZZDEF is not in the sponsor map at all.
    names = ("KO", "PEP", "PG", "WMT", "COST", "PM", "ZZDEF")
    main_etf = _MAIN_SECTOR_TO_ETF.get("consumer defensive")
    for ticker in names:
        got = get_ticker_sector_etf(ticker, skill_dir=tmp_path)
        assert got == main_etf
        assert got is None
    cache = json.loads((tmp_path / ".sector_map_cache.json").read_text(encoding="utf-8"))
    for ticker in names:
        assert cache["sector_etf_by_ticker"][ticker] is None


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


def test_consumer_defensive_alias_is_shadow_and_live_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SCHWAB_ONLY_DATA", "false")
    _install_fake_yfinance(monkeypatch, "Consumer Defensive")
    monkeypatch.setenv("SECTOR_MAP_OVERRIDE_MODE", "shadow")
    assert get_ticker_sector_etf("ZZDEF", skill_dir=tmp_path) is None
    view = describe_sector_map_override("ZZDEF", None, skill_dir=tmp_path)
    assert view["disagree"] is True
    assert view["override_etf"] == "XLP"
    assert view["applied"] is False
    sink: dict[str, object] = {}
    accumulate_sector_map_override_view(sink, view)
    assert sink["sector_map_override_disagree"] == 1

    monkeypatch.setenv("SECTOR_MAP_OVERRIDE_MODE", "live")
    assert get_ticker_sector_etf("ZZDEF", skill_dir=tmp_path) == "XLP"
    assert get_ticker_sector_etf("KO", skill_dir=tmp_path) == "XLP"
    cache = json.loads((tmp_path / ".sector_map_cache.json").read_text(encoding="utf-8"))
    assert cache["sector_etf_by_ticker"]["ZZDEF"] is None
    assert cache["sector_etf_by_ticker"]["KO"] is None


def test_off_diagnostics_do_not_count(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SECTOR_MAP_OVERRIDE_MODE", "off")
    sink: dict[str, object] = {}
    accumulate_sector_map_override_view(
        sink,
        describe_sector_map_override("UBER", "XLK", skill_dir=tmp_path),
    )
    assert sink["sector_map_override_mode"] == "off"
    assert "sector_map_override_evaluated" not in sink
