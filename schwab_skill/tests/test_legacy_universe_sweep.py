from __future__ import annotations

import sys
from pathlib import Path

import pytest

SKILL_DIR = Path(__file__).resolve().parent.parent
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

from scripts.run_legacy_universe_sweep import runnable_scan_universes  # noqa: E402
from scripts.run_multi_era_backtest_schwab_only import (  # noqa: E402
    ERAS,
    _load_universe_tickers,
    _normalize_universe_preset,
    _select_eras,
)


def test_runnable_scan_universes_match_catalog() -> None:
    ids = {row["id"] for row in runnable_scan_universes()}
    assert {"sp1500", "sp500", "nasdaq100", "sector_etfs", "focused"} <= ids
    assert "custom" not in ids
    assert "russell2000" not in ids


def test_normalize_universe_preset_rejects_unwired() -> None:
    assert _normalize_universe_preset(None) == "sp1500"
    with pytest.raises(ValueError, match="russell2000"):
        _normalize_universe_preset("russell2000")
    with pytest.raises(ValueError, match="custom"):
        _normalize_universe_preset("custom")


def test_select_eras_filters_and_rejects_unknown() -> None:
    assert _select_eras(None) == list(ERAS)
    only = _select_eras("recent_current,bear_rates")
    assert [name for _, _, name in only] == ["recent_current", "bear_rates"]
    with pytest.raises(ValueError, match="unknown era"):
        _select_eras("not_an_era")


def test_load_universe_tickers_sector_etfs() -> None:
    tickers = _load_universe_tickers("sector_etfs")
    assert "SPY" in tickers
    assert "QQQ" in tickers
    assert 10 <= len(tickers) <= 20
