"""Quick-check must fail closed when no price history exists.

Regression for the P0 audit finding where an unknown/invalid symbol (e.g.
ZZZZZ) was returned as a normal ok:true payload with no data-quality marker,
so the dashboard rendered it as a fresh, high-confidence result.
"""

from __future__ import annotations

import pandas as pd
import pytest

import full_report
from full_report import DCFSection, HealthSection, TechnicalSection, quick_check


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    monkeypatch.setattr(full_report, "_build_dcf", lambda ticker: DCFSection())
    monkeypatch.setattr(full_report, "_build_health", lambda ticker: HealthSection())


def _patch_history(monkeypatch, df: pd.DataFrame) -> None:
    import market_data

    monkeypatch.setattr(market_data, "get_daily_history", lambda *a, **k: df)


def test_quick_check_no_price_data_sets_data_quality(monkeypatch) -> None:
    _patch_history(monkeypatch, pd.DataFrame())

    embed = quick_check("ZZZZZ")

    assert embed["data_quality"] == "NO_PRICE_DATA"
    assert "NO DATA" in embed["description"]
    assert "no price history found" in embed["description"]


def test_quick_check_with_price_data_has_no_data_quality_marker(monkeypatch) -> None:
    df = pd.DataFrame(
        {
            "open": [100.0] * 10,
            "high": [101.0] * 10,
            "low": [99.0] * 10,
            "close": [100.5] * 10,
            "volume": [1_000_000] * 10,
        }
    )
    _patch_history(monkeypatch, df)
    monkeypatch.setattr(
        full_report,
        "_build_technical",
        lambda ticker, df, auth, skill_dir=None: TechnicalSection(
            ticker=ticker, current_price=100.5
        ),
    )

    embed = quick_check("STT")

    assert "data_quality" not in embed
    assert "NO DATA" not in embed["description"]
