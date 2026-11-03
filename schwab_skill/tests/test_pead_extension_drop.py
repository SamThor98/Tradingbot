"""PEAD extension shadow marks only pead_primary rows and never removes them."""

from __future__ import annotations

import pytest

from config import get_pead_extension_drop_min
from core.pead_extension_drop import apply_pead_extension_drop_shadow, sma200_distance


def test_distance_uses_price_when_field_missing() -> None:
    assert sma200_distance({"price": 120.0, "sma_200": 100.0}) == pytest.approx(0.2)
    assert sma200_distance({"ticker": "X"}) is None


def test_shadow_marks_pead_only_and_keeps_the_row(monkeypatch) -> None:
    monkeypatch.setenv("PEAD_EXTENSION_DROP_MODE", "shadow")
    monkeypatch.setenv("PEAD_EXTENSION_DROP_MIN_SMA200_PCT", "0.20")
    signals = [
        {"ticker": "PEAD", "entry_family": "pead_primary", "close_vs_sma200_pct": 0.30, "sort_score": 40.0},
        {"ticker": "NEAR", "entry_family": "pead_primary", "close_vs_sma200_pct": 0.10, "sort_score": 50.0},
        {"ticker": "S2", "entry_family": "stage2", "close_vs_sma200_pct": 0.40, "sort_score": 70.0},
        {"ticker": "BOTH", "entry_family": "both", "close_vs_sma200_pct": 0.40, "sort_score": 80.0},
        {"ticker": "MISS", "entry_family": "pead_primary", "sort_score": 10.0},
    ]
    diag: dict = {}
    out = apply_pead_extension_drop_shadow(signals, diag, skill_dir=None)
    assert [row["ticker"] for row in out] == ["PEAD", "NEAR", "S2", "BOTH", "MISS"]
    assert signals[0]["pead_extension_drop_would_drop"] is True
    assert signals[0]["sort_score"] == 40.0
    assert signals[1]["pead_extension_drop_would_drop"] is False
    assert "pead_extension_drop_would_drop" not in signals[2]
    assert "pead_extension_drop_would_drop" not in signals[3]
    assert signals[4]["pead_extension_drop_reason"] == "distance_missing"
    assert diag["pead_extension_drop_evaluated"] == 3
    assert diag["pead_extension_drop_would_drop"] == 1
    assert diag["pead_extension_drop_unmeasured"] == 1


def test_live_is_refused(monkeypatch) -> None:
    monkeypatch.setenv("PEAD_EXTENSION_DROP_MODE", "live")
    signals = [{"ticker": "PEAD", "entry_family": "pead_primary", "close_vs_sma200_pct": 0.50}]
    diag: dict = {}
    out = apply_pead_extension_drop_shadow(signals, diag, skill_dir=None)
    assert len(out) == 1
    assert "pead_extension_drop_would_drop" not in signals[0]
    assert diag["pead_extension_drop_skipped"] == "live_not_approved"
    assert diag["pead_extension_drop_would_drop"] == 0


def test_off_is_a_noop(monkeypatch) -> None:
    monkeypatch.setenv("PEAD_EXTENSION_DROP_MODE", "off")
    signals = [{"ticker": "PEAD", "entry_family": "pead_primary", "close_vs_sma200_pct": 0.50}]
    diag: dict = {}
    apply_pead_extension_drop_shadow(signals, diag, skill_dir=None)
    assert "pead_extension_drop_would_drop" not in signals[0]
    assert diag["pead_extension_drop_mode"] == "off"
    assert diag["pead_extension_drop_evaluated"] == 0


def test_default_cutoff_is_the_frozen_book_percentile(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("PEAD_EXTENSION_DROP_MIN_SMA200_PCT", raising=False)
    assert get_pead_extension_drop_min(tmp_path) == 0.2028
