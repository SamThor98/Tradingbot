"""Rebuild cards stay point-in-time, anonymized, and free of outcome fields."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from core.tab_agent_lab_plan import anonymize_card
from core.tab_agent_rebuild import (
    build_rebuild_cases,
    card_from_bars,
    dry_run_report,
    entry_from_trade,
    forbidden_field,
    write_rebuild_cases,
)


def _frame(as_of: str = "2024-06-03") -> pd.DataFrame:
    index = pd.bdate_range(end=as_of, periods=260)
    future = pd.bdate_range(start=index[-1] + pd.offsets.BDay(1), periods=30)
    full = index.append(future)
    close = pd.Series(range(100, 100 + len(full)), index=full, dtype=float)
    return pd.DataFrame(
        {
            "open": close,
            "high": close + 1.0,
            "low": close - 1.0,
            "close": close,
            "volume": 1_000_000.0,
        },
        index=full,
    )


def _entry() -> dict:
    return {
        "ticker": "MSFT",
        "entry_date": "2024-06-03",
        "entry_price": 200.0,
        "stop_pct": 0.08,
        "sector_etf": "XLK",
        "regime_bucket": "bull",
        "era": "recent_current",
    }


def test_bars_after_as_of_do_not_change_the_card(tmp_path: Path) -> None:
    frame = _frame()
    entry = _entry()
    first = card_from_bars(entry, frame, tmp_path)
    shifted = frame.copy()
    cutoff = pd.Timestamp("2024-06-03")
    shifted.loc[shifted.index > cutoff, "close"] = shifted.loc[shifted.index > cutoff, "close"] * 10
    shifted.loc[shifted.index > cutoff, "high"] = shifted.loc[shifted.index > cutoff, "high"] * 10
    second = card_from_bars(entry, shifted, tmp_path)
    assert first == second
    anon = anonymize_card(first, case_id="MSFT:2024-06-03")
    blob = json.dumps(anon)
    assert "MSFT" not in blob
    assert "2024-06-03" not in blob
    assert "ticker" not in anon
    assert "as_of" not in anon
    assert anon["case_id"]
    assert "dist_sma_50_pct" in anon or "signal_score" in anon
    assert "qual_business" not in anon


def test_whitelist_blocks_outcome_and_model_fields() -> None:
    class Boom(dict):
        def __getitem__(self, key: str) -> object:
            if forbidden_field(str(key)):
                raise AssertionError(key)
            return dict.__getitem__(self, key)

    raw = Boom(
        {
            "ticker": "AAPL",
            "entry_date": "2020-01-06",
            "entry_price": 75.0,
            "stop_pct": 0.07,
            "sector_etf": "XLK",
            "regime_bucket": "bull",
            "return": 0.2,
            "net_return": 0.18,
            "exit_price": 90.0,
            "exit_date": "2020-02-06",
            "mfe": 0.3,
            "mae": -0.1,
            "ohlc_path": [{"close": 1}],
            "p_up_calibrated": 0.7,
            "ev_10d": 1.2,
            "rank_score_v2": 88,
            "advisory_score": 0.4,
            "reliability_score": 0.9,
            "signal_score": 99,
        }
    )
    entry = entry_from_trade(raw, era="crash_recovery")
    assert set(entry) <= {"ticker", "entry_date", "entry_price", "stop_pct", "sector_etf", "regime_bucket", "era"}
    assert "net_return" not in entry
    assert "signal_score" not in entry
    assert entry["era"] == "crash_recovery"


def test_dry_run_audits_chunks_without_fetching(tmp_path: Path) -> None:
    era = tmp_path / "recent_current"
    era.mkdir()
    (era / "chunk_000.json").write_text(
        json.dumps(
            {
                "trades": [
                    {
                        "ticker": "NVDA",
                        "entry_date": "2024-03-04",
                        "entry_price": 800.0,
                        "stop_pct": 0.08,
                        "sector_etf": "XLK",
                        "regime_bucket": "bull",
                        "net_return": 0.4,
                        "mfe": 0.5,
                        "p_up_calibrated": 0.66,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    report = dry_run_report(tmp_path)
    assert report["fetched"] is False
    assert report["orders_touched"] is False
    assert report["by_era"]["recent_current"] == 1
    assert report["leakage"]["forbidden_in_output"] == []
    assert report["leakage"]["ignored_fields"]["net_return"] == 1
    assert report["unfilled"]["bars_loaded"] is False
    assert "sma_50" in report["unfilled"]["unfilled_field_counts"]


def test_build_writes_anonymized_rebuild_rows(tmp_path: Path) -> None:
    frame = _frame("2024-06-03")
    # The label needs bars after the entry. The card must ignore them.
    cases = build_rebuild_cases(
        [_entry()], lambda _ticker: frame, tmp_path, now=datetime(2026, 11, 1, tzinfo=timezone.utc)
    )
    assert len(cases) == 1
    assert cases[0].source == "rebuild"
    assert cases[0].pre_cutoff is False
    assert cases[0].payload is not None
    assert "ticker" not in cases[0].payload
    path = write_rebuild_cases(tmp_path, cases)
    line = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    assert line["source"] == "rebuild"
    assert "MSFT" not in json.dumps(line["payload"])
