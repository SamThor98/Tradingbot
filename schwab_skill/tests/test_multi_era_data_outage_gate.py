"""Fail-closed data-outage gate for the multi-era chunk runner.

Regression tests for the June 2026 sweep incident: Schwab tokens expired
mid-sweep, every history fetch returned empty, all 1506 universe tickers were
excluded as insufficient_history, and five signal-gate configs recorded
PF 0.0 across all eras as if they were valid results. The gate refuses to
persist a chunk whose exclusion ratio looks like a data outage, and per-era
artifact rows now carry aggregated data_integrity counters so thin eras are
diagnosable from the artifact alone.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

SKILL_DIR = Path(__file__).resolve().parent.parent
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

from scripts.run_multi_era_backtest_schwab_only import (  # noqa: E402
    DATA_INTEGRITY_SUM_KEYS,
    RC_AUTH_PREFLIGHT_FAILED,
    RC_DATA_OUTAGE,
    _aggregate_era,
    _chunk_data_outage,
    _max_excluded_ratio,
    _schwab_only_effective,
)


def test_chunk_data_outage_boundaries() -> None:
    # 100% excluded (the incident signature) always trips the gate.
    assert _chunk_data_outage(120, 120, 0.90) is True
    # Exactly at the threshold trips (>=).
    assert _chunk_data_outage(108, 120, 0.90) is True
    # Normal delisting-level exclusion does not.
    assert _chunk_data_outage(10, 120, 0.90) is False
    assert _chunk_data_outage(0, 120, 0.90) is False
    # Degenerate chunk sizes never trip.
    assert _chunk_data_outage(5, 0, 0.90) is False


def test_max_excluded_ratio_default_and_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BACKTEST_MAX_EXCLUDED_PCT", raising=False)
    assert _max_excluded_ratio() == pytest.approx(0.90)
    monkeypatch.setenv("BACKTEST_MAX_EXCLUDED_PCT", "0.5")
    assert _max_excluded_ratio() == pytest.approx(0.5)
    # Clamped into (0.05..1.0) and resilient to junk values.
    monkeypatch.setenv("BACKTEST_MAX_EXCLUDED_PCT", "7")
    assert _max_excluded_ratio() == pytest.approx(1.0)
    monkeypatch.setenv("BACKTEST_MAX_EXCLUDED_PCT", "0.0")
    assert _max_excluded_ratio() == pytest.approx(0.05)
    monkeypatch.setenv("BACKTEST_MAX_EXCLUDED_PCT", "not_a_number")
    assert _max_excluded_ratio() == pytest.approx(0.90)


def test_schwab_only_effective_defaults_true(monkeypatch: pytest.MonkeyPatch) -> None:
    # Chunk workers setdefault SCHWAB_ONLY_DATA=true, so unset means schwab-only.
    monkeypatch.delenv("SCHWAB_ONLY_DATA", raising=False)
    assert _schwab_only_effective() is True
    monkeypatch.setenv("SCHWAB_ONLY_DATA", "false")
    assert _schwab_only_effective() is False
    monkeypatch.setenv("SCHWAB_ONLY_DATA", "true")
    assert _schwab_only_effective() is True


def test_return_codes_are_distinct() -> None:
    assert RC_DATA_OUTAGE != RC_AUTH_PREFLIGHT_FAILED
    assert RC_DATA_OUTAGE not in (0, 1, 2, 3)
    assert RC_AUTH_PREFLIGHT_FAILED not in (0, 1, 2, 3)


def test_aggregate_era_sums_data_integrity_zero_trades() -> None:
    chunks = [
        {
            "excluded_count": 120,
            "data_integrity": {
                "history_fetch_total": 120,
                "history_fetch_empty": 120,
            },
            "trades": [],
        },
        {
            "excluded_count": 30,
            "data_integrity": {
                "history_fetch_total": 120,
                "history_fetch_empty": 25,
                "history_fetch_too_short": 5,
            },
            "trades": [],
        },
        # Legacy chunk payload without the data_integrity key still aggregates.
        {"excluded_count": 3, "trades": []},
    ]
    row = _aggregate_era(
        name="bear_rates",
        start_date="2022-01-01",
        end_date="2023-12-31",
        chunk_payloads=chunks,
        universe_size=360,
    )
    assert row["total_trades"] == 0
    assert row["excluded_count"] == 153
    integrity = row["data_integrity"]
    assert set(DATA_INTEGRITY_SUM_KEYS).issubset(integrity.keys())
    assert integrity["history_fetch_total"] == 240
    assert integrity["history_fetch_empty"] == 145
    assert integrity["history_fetch_too_short"] == 5
    assert integrity["history_provider_schwab"] == 0
