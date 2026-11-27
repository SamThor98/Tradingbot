"""Point-in-time setup features from a bar cache. No network and no chunk rewrite."""

from __future__ import annotations

import json
import pickle
from pathlib import Path

import numpy as np
import pandas as pd

from core.setup_rule_features import (
    BarsFormatError,
    enrich_records,
    ensure_setup_features,
    features_at,
    load_bars,
    merge_sidecar,
    prepare_bars,
    write_sidecar,
)
from core.setup_rule_search import SearchGates, load_trade_records, render_setup_rules_md, run_setup_rule_phase
from core.shadow_setup_filter import bar_features
from stage_analysis import add_indicators


def _frame(closes: list[float], highs: list[float] | None = None, start: str = "2020-01-02") -> pd.DataFrame:
    idx = pd.bdate_range(start, periods=len(closes))
    close = np.array(closes, dtype=float)
    high = np.array(highs, dtype=float) if highs is not None else np.maximum(close, close)
    low = np.minimum(close, high) * 0.99
    return pd.DataFrame(
        {"open": close, "high": high, "low": low, "close": close, "volume": 1_000_000.0},
        index=idx,
    )


def _day(stamp: pd.Timestamp) -> np.datetime64:
    return np.datetime64(pd.Timestamp(stamp).to_datetime64(), "D")


def test_geometry_matches_the_shadow_filter_and_ignores_later_bars() -> None:
    closes = [10.0] * 29 + [11.0]
    highs = [10.0] * 29 + [12.0]
    frame = _frame(closes, highs)
    entry = frame.index[-1]
    prepared = prepare_bars(frame)
    assert prepared is not None
    got = features_at(prepared, _day(entry))
    indicated = add_indicators(frame)
    reference = bar_features(indicated)
    assert reference is not None
    assert got["pct_below_52w_high"] == reference["pct_below_52w_high"]
    assert got["atr_pct"] == reference["atr_pct"]
    assert got["high_52w"] == reference["high_52w"]
    assert got["pct_below_52w_high"] == round((12.0 - 11.0) / 12.0, 6)

    future = frame.copy()
    future.loc[entry + pd.Timedelta(days=10)] = {
        "open": 11.0,
        "high": 500.0,
        "low": 10.0,
        "close": 11.0,
        "volume": 1_000_000.0,
    }
    later = prepare_bars(future)
    assert later is not None
    assert features_at(later, _day(entry)) == got
    leaked = bar_features(add_indicators(future))
    assert leaked is not None
    assert leaked["high_52w"] != got["high_52w"]


def test_missing_entry_bar_stays_null() -> None:
    frame = _frame([10.0] * 5, [11.0] * 5)
    entry = frame.index[-1]
    kept = frame.iloc[:-1]
    later = frame.iloc[[-1]].copy()
    later.index = pd.DatetimeIndex([entry + pd.offsets.BDay(3)])
    gapped = pd.concat([kept, later])
    prepared = prepare_bars(gapped)
    assert prepared is not None
    got = features_at(prepared, _day(entry))
    assert got["pct_below_52w_high"] is None
    assert got["atr_pct"] is None
    assert got["high_52w"] is None
    next_day = features_at(prepared, _day(later.index[0]))
    assert next_day["pct_below_52w_high"] is not None


def test_sector_breadth_uses_asof_labels_and_the_trades_own_sector() -> None:
    closes_up = [100.0] * 54 + [110.0]
    closes_down = [100.0] * 54 + [90.0]
    up = _frame(closes_up)
    down = _frame(closes_down)
    entry = up.index[-1]
    earlier = up.index[50]
    after = entry + pd.offsets.BDay(5)
    trades = [
        {"ticker": "BBB", "entry_date": earlier.date().isoformat(), "sector_etf": "XLF", "net_return": 0.01},
        {"ticker": "BBB", "entry_date": entry.date().isoformat(), "sector_etf": "XLK", "net_return": 0.01},
        {"ticker": "AAA", "entry_date": after.date().isoformat(), "sector_etf": "XLK", "net_return": 0.01},
        {"ticker": "CCC", "entry_date": entry.date().isoformat(), "sector_etf": "", "net_return": 0.01},
    ]
    rows, meta = enrich_records(
        trades,
        {"AAA": up, "BBB": down, "CCC": up},
    )
    by_key = {(row["ticker"], row["entry_date"]): row for row in rows}
    bbb = by_key[("BBB", entry.date().isoformat())]
    # AAA's only label is after entry, so its modal XLK counts in the universe.
    # BBB's own sector on entry is XLK. AAA is above SMA50, BBB is below: 1 of 2.
    assert bbb["sector_breadth50"] == 0.5
    assert bbb["sector_breadth_n"] == 2
    assert bbb["sector_breadth_above"] == 1
    # Before BBB's XLK label, the as-of sector is XLF and AAA is not in it yet.
    early = by_key[("BBB", earlier.date().isoformat())]
    assert early["sector_breadth50"] == 0.0
    assert early["sector_breadth_n"] == 1
    blank = by_key[("CCC", entry.date().isoformat())]
    assert blank["sector_breadth50"] is None
    assert blank["pct_below_52w_high"] is not None
    assert meta["universe_n"] == 3
    assert "scan" in meta["breadth_note"]


def test_sidecar_merges_without_rewriting_chunks(tmp_path: Path) -> None:
    era = tmp_path / "multi_era_chunks" / "sample" / "late_bull"
    era.mkdir(parents=True)
    original = {
        "era": "late_bull",
        "trades": [
            {
                "ticker": "AAA",
                "entry_date": "2020-06-01",
                "exit_date": "2020-06-08",
                "net_return": 0.01,
                "signal_score": 30,
                "sector_etf": "XLK",
            }
        ],
    }
    chunk = era / "chunk_0000.json"
    chunk.write_text(json.dumps(original), encoding="utf-8")
    sidecar = tmp_path / "setup_rule_features" / "sample.jsonl"
    write_sidecar(
        sidecar,
        [
            {
                "ticker": "aaa",
                "entry_date": "2020-06-01T00:00:00",
                "pct_below_52w_high": 0.04,
                "atr_pct": 2.5,
                "sector_breadth50": 0.25,
                "high_52w": 50.0,
                "atr_14": 1.25,
                "sector_breadth_n": 4,
                "sector_breadth_above": 1,
            }
        ],
        {"universe_n": 1},
    )
    loaded = load_trade_records(tmp_path / "multi_era_chunks" / "sample")
    assert loaded[0]["pct_below_52w_high"] == 0.04
    assert loaded[0]["atr_pct"] == 2.5
    assert loaded[0]["sector_breadth50"] == 0.25
    assert loaded[0]["signal_score"] == 30
    assert json.loads(chunk.read_text(encoding="utf-8")) == original


def test_existing_feature_is_not_overwritten() -> None:
    records = [{"ticker": "AAA", "entry_date": "2020-06-01", "atr_pct": 1.0, "pct_below_52w_high": None}]
    filled = merge_sidecar(
        records,
        [{"ticker": "AAA", "entry_date": "2020-06-01", "atr_pct": 9.0, "pct_below_52w_high": 0.2}],
    )
    assert filled == 1
    assert records[0]["atr_pct"] == 1.0
    assert records[0]["pct_below_52w_high"] == 0.2


def test_phase_enriches_from_bars_and_reports_coverage(tmp_path: Path, monkeypatch) -> None:
    idx = pd.bdate_range("2020-01-02", periods=60)
    frame = pd.DataFrame(
        {
            "open": 100.0,
            "high": 101.0,
            "low": 99.0,
            "close": 100.0,
            "volume": 1_000_000.0,
        },
        index=idx,
    )
    frame.iloc[-1, frame.columns.get_loc("close")] = 110.0
    frame.iloc[-1, frame.columns.get_loc("high")] = 112.0
    bars = tmp_path / "bars.pkl"
    with bars.open("wb") as handle:
        pickle.dump({"AAA": frame, "BBB": frame}, handle, protocol=4)
    pool = tmp_path / "multi_era_chunks" / "sample" / "late_bull"
    pool.mkdir(parents=True)
    entry = idx[-1].date().isoformat()
    (pool / "chunk_0000.json").write_text(
        json.dumps(
            {
                "trades": [
                    {
                        "ticker": "AAA",
                        "entry_date": entry,
                        "exit_date": "2020-04-01",
                        "net_return": 0.02,
                        "signal_score": 30,
                        "sector_etf": "XLK",
                    },
                    {
                        "ticker": "BBB",
                        "entry_date": entry,
                        "exit_date": "2020-04-01",
                        "net_return": -0.01,
                        "signal_score": 48,
                        "sector_etf": "XLK",
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("SETUP_RULE_FEATURES_BARS", str(bars))
    report = run_setup_rule_phase(
        tmp_path,
        trades_path=tmp_path / "multi_era_chunks" / "sample",
        write_shadow=False,
        gates=SearchGates(min_oos_n=1, min_discovery_n=1, min_era_n=1, min_voting_eras=1),
    )
    coverage = report["data_issues"]["feature_coverage"]
    assert coverage["pct_below_52w_high"]["pct"] == 100.0
    assert coverage["atr_pct"]["pct"] == 100.0
    assert coverage["sector_breadth50"]["pct"] == 100.0
    assert report["data_issues"]["feature_status"] == "written"
    assert "bar cache" in report["data_issues"]["breadth_note"]
    sidecar = tmp_path / "setup_rule_features" / "sample.jsonl"
    assert sidecar.is_file()
    chunk = (pool / "chunk_0000.json").read_text(encoding="utf-8")
    assert "pct_below_52w_high" not in chunk
    text = render_setup_rules_md(report)
    assert "pct_below_52w_high=100.0%" in text
    assert report["orders_touched"] is False


def test_bad_bar_cache_is_explicit(tmp_path: Path) -> None:
    path = tmp_path / "bars.pkl"
    path.write_bytes(pickle.dumps({"AAA": [1, 2, 3]}))
    try:
        load_bars(path)
    except BarsFormatError as exc:
        assert "DataFrame" in str(exc)
    else:
        raise AssertionError("expected BarsFormatError")


def test_long_frame_and_missing_bars_do_not_fail_the_phase(tmp_path: Path) -> None:
    idx = pd.bdate_range("2020-01-02", periods=20)
    long_frame = pd.DataFrame(
        {
            "ticker": ["AAA"] * len(idx),
            "date": idx,
            "high": 11.0,
            "low": 9.0,
            "close": 10.0,
            "volume": 1_000_000.0,
        }
    )
    bars = tmp_path / "bars.pkl"
    with bars.open("wb") as handle:
        pickle.dump(long_frame, handle, protocol=4)
    loaded = load_bars(bars)
    assert "AAA" in loaded
    pool = tmp_path / "multi_era_chunks" / "sample" / "late_bull"
    pool.mkdir(parents=True)
    (pool / "chunk_0000.json").write_text(
        json.dumps(
            {
                "trades": [
                    {
                        "ticker": "AAA",
                        "entry_date": "2020-01-02",
                        "exit_date": "2020-01-09",
                        "net_return": 0.01,
                        "signal_score": 30,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    missing = ensure_setup_features(
        tmp_path,
        tmp_path / "multi_era_chunks" / "sample",
        bars_file=tmp_path / "missing.pkl",
    )
    assert missing["status"] == "features_missing"
    assert "features_missing" in missing["note"]
    report = run_setup_rule_phase(
        tmp_path,
        trades_path=tmp_path / "multi_era_chunks" / "sample",
        write_shadow=False,
        gates=SearchGates(min_oos_n=1, min_discovery_n=1, min_era_n=1, min_voting_eras=1),
    )
    assert report["orders_touched"] is False
    assert report["data_issues"]["feature_status"] == "features_missing"
    assert "Feature coverage" in render_setup_rules_md(report)
