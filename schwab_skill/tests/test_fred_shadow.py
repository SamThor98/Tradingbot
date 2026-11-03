"""FRED SHADOW overlay fail-closed without a key; never a live gate."""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

SKILL_DIR = Path(__file__).resolve().parent.parent
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

from research.fred_regime import attach_fred_shadow  # noqa: E402


def test_fred_shadow_fail_closed_without_key(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("FRED_REGIME_MODE", "shadow")
    monkeypatch.delenv("FRED_API_KEY", raising=False)
    df = pd.DataFrame({"asof_date": ["2016-06-01"], "ticker": ["AAPL"]})
    out, quality = attach_fred_shadow(df, skill_dir=tmp_path)
    assert quality["live_gate"] is False
    assert quality["status"] == "error"
    assert "missing_fred_api_key" in str(quality.get("reason"))
    assert "yield_curve_spread" not in out.columns


def test_fred_off_skips(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("FRED_REGIME_MODE", "off")
    df = pd.DataFrame({"asof_date": ["2016-06-01"], "ticker": ["AAPL"]})
    _out, quality = attach_fred_shadow(df, skill_dir=tmp_path)
    assert quality["status"] == "skipped"
    assert quality["live_gate"] is False
