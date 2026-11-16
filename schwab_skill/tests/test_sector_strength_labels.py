from __future__ import annotations

import sys
import types
from pathlib import Path

import sector_strength


def test_consumer_defensive_singular_maps_to_xlp() -> None:
    # yfinance currently reports staples as "Consumer Defensive" (singular).
    assert sector_strength.SECTOR_TO_ETF["consumer defensive"] == "XLP"


def test_sector_etf_resolves_to_itself_without_lookup(tmp_path: Path) -> None:
    assert sector_strength.get_ticker_sector_etf("XLK", skill_dir=tmp_path) == "XLK"
    assert sector_strength.get_ticker_sector_etf("xlv", skill_dir=tmp_path) == "XLV"


def test_staples_ticker_resolves_via_yfinance_sector_label(monkeypatch, tmp_path: Path) -> None:
    fake_yf = types.ModuleType("yfinance")

    class _Ticker:
        def __init__(self, _symbol: str) -> None:
            pass

        @property
        def info(self) -> dict:
            return {"sector": "Consumer Defensive"}

    fake_yf.Ticker = _Ticker  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "yfinance", fake_yf)
    monkeypatch.setattr(sector_strength, "get_schwab_only_data", lambda: False, raising=False)

    assert sector_strength.get_ticker_sector_etf("ADM", skill_dir=tmp_path) == "XLP"
