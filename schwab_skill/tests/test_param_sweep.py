"""Isolated param sweep: tiny grid, no scanner/execution import."""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pandas as pd

SKILL_DIR = Path(__file__).resolve().parent.parent
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

from research.param_sweep import NOT_PROMOTION_ELIGIBLE, sweep_frozen_trades, sweep_ohlcv_panel  # noqa: E402


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module.split(".")[0])
    return names


def test_param_sweep_does_not_import_scanner_or_execution() -> None:
    mods = _imported_modules(SKILL_DIR / "research" / "param_sweep.py")
    assert "signal_scanner" not in mods
    assert "execution" not in mods
    script_mods = _imported_modules(SKILL_DIR / "scripts" / "run_param_sweep.py")
    assert "signal_scanner" not in script_mods
    assert "execution" not in script_mods


def test_tiny_frozen_grid_finishes() -> None:
    trades = pd.DataFrame(
        {
            "net_return": [0.05, -0.02, 0.01, -0.04, 0.03, 0.02] * 5,
            "breakout_distance_pct": [0.02, 0.0, 0.015, 0.005, 0.03, 0.012] * 5,
        }
    )
    table = sweep_frozen_trades(
        trades,
        atr_multiples=(2.0, 2.5),
        breakout_buffers=(0.0, 0.01),
        hold_days=(30, 40),
    )
    assert len(table) == 8
    assert (table["promotion_eligible"] == False).all()  # noqa: E712
    assert NOT_PROMOTION_ELIGIBLE
    assert set(table.columns) >= {"atr_multiple", "breakout_buffer", "hold_days", "pf", "n", "retention"}


def test_tiny_ohlcv_grid() -> None:
    idx = pd.bdate_range("2019-01-02", periods=80)
    close = 50 + pd.Series(range(80)).astype(float)
    df = pd.DataFrame(
        {"open": close, "high": close + 0.5, "low": close - 0.5, "close": close, "volume": 1e6},
        index=idx,
    )
    table = sweep_ohlcv_panel(
        {"AAA": df},
        atr_multiples=(2.5,),
        breakout_buffers=(0.0,),
        hold_days=(20,),
        entry_every=15,
    )
    assert len(table) == 1
    assert bool(table.iloc[0]["promotion_eligible"]) is False
