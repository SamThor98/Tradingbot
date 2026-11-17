"""Local price-cache reads and adjusted-price quality exclusions."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

import backtest
import sector_strength
from scripts.run_multi_era_backtest_schwab_only import _aggregate_era


def _bars(n: int = 280, close: float = 100.0, start: str = "2019-01-02") -> pd.DataFrame:
    idx = pd.bdate_range(start, periods=n)
    frame = pd.DataFrame(
        {
            "open": [close] * n,
            "high": [close + 1.0] * n,
            "low": [close - 1.0] * n,
            "close": [close] * n,
            "volume": [1_000_000.0] * n,
        },
        index=idx,
    )
    frame.index.name = "date"
    return frame


def _write_csv(cache: Path, ticker: str, frame: pd.DataFrame) -> None:
    cache.mkdir(parents=True, exist_ok=True)
    out = frame.copy()
    out.index.name = "date"
    out.reset_index().to_csv(cache / f"{ticker.upper()}.csv", index=False)


def _write_parquet(cache: Path, ticker: str, frame: pd.DataFrame) -> None:
    cache.mkdir(parents=True, exist_ok=True)
    out = frame.copy()
    out.index.name = "date"
    out.reset_index().to_parquet(cache / f"{ticker.upper()}.parquet", index=False)


def test_cache_hit_does_not_call_schwab(monkeypatch, tmp_path: Path) -> None:
    calls: list[tuple[str, str, str]] = []

    def _remote(symbol: str, start: str, end: str, schwab_auth=None):
        calls.append((symbol, start, end))
        return _bars(3), {"provider": "schwab", "used_fallback": False, "reason": "schwab_ok", "rows": 3}

    monkeypatch.setattr(backtest, "_fetch_remote_history", _remote)
    covered = pd.bdate_range("2018-01-02", "2020-12-31")
    _write_parquet(tmp_path, "AAPL", _bars(len(covered), start="2018-01-02"))
    frame, meta = backtest._fetch_history_with_meta("AAPL", "2019-06-03", "2019-12-31", cache_dir=tmp_path)
    assert calls == []
    assert meta["provider"] == "cache"
    assert meta["cache_status"] == "hit"
    assert not frame.empty
    assert frame.index.min() >= pd.Timestamp("2019-06-03")
    assert frame.index.max() <= pd.Timestamp("2019-12-31")


def test_cache_miss_and_partial_gap_use_schwab(monkeypatch, tmp_path: Path) -> None:
    calls: list[tuple[str, str, str]] = []

    def _remote(symbol: str, start: str, end: str, schwab_auth=None):
        calls.append((symbol, start, end))
        idx = pd.bdate_range(start, end)
        if len(idx) == 0:
            return _bars(0), {"provider": "schwab", "used_fallback": False, "reason": "schwab_empty", "rows": 0}
        frame = _bars(len(idx), start=start)
        return frame, {"provider": "schwab", "used_fallback": False, "reason": "schwab_ok", "rows": len(frame)}

    monkeypatch.setattr(backtest, "_fetch_remote_history", _remote)
    _miss, miss_meta = backtest._fetch_history_with_meta("MSFT", "2020-01-02", "2020-06-30", cache_dir=tmp_path)
    assert calls == [("MSFT", "2020-01-02", "2020-06-30")]
    assert miss_meta["cache_status"] == "miss"
    assert miss_meta["provider"] == "schwab"

    calls.clear()
    _write_csv(tmp_path, "PART", _bars(40, start="2020-03-02"))
    frame, meta = backtest._fetch_history_with_meta("PART", "2020-01-02", "2020-06-30", cache_dir=tmp_path)
    assert meta["cache_status"] == "partial"
    assert meta["provider"] == "cache+schwab"
    assert any(call[0] == "PART" and call[1] == "2020-01-02" for call in calls)
    assert any(call[0] == "PART" and call[2] == "2020-06-30" for call in calls)
    assert frame.index.min() <= pd.Timestamp("2020-01-06")
    assert frame.index.max() >= pd.Timestamp("2020-06-01")
    cached_day = pd.Timestamp("2020-03-02")
    assert float(frame.loc[cached_day, "close"]) == 100.0


def test_single_large_move_is_kept_and_repeated_reversals_are_not() -> None:
    held = _bars(30)
    held.iloc[10:, held.columns.get_loc("close")] = 180.0
    assert backtest.screen_price_quality(held, move_pct=0.50, min_reversals=2) is None

    one = _bars(30)
    one.iloc[10, one.columns.get_loc("close")] = 170.0
    one.iloc[11:, one.columns.get_loc("close")] = 80.0
    assert backtest.screen_price_quality(one, move_pct=0.50, min_reversals=2) is None

    repeated = _bars(40)
    repeated.iloc[10, repeated.columns.get_loc("close")] = 170.0
    repeated.iloc[11:20, repeated.columns.get_loc("close")] = 80.0
    repeated.iloc[20, repeated.columns.get_loc("close")] = 140.0
    repeated.iloc[21:, repeated.columns.get_loc("close")] = 60.0
    found = backtest.screen_price_quality(repeated, move_pct=0.50, min_reversals=2)
    assert found is not None
    assert found["reason"] == "reversal_spikes"
    assert found["reversal_count"] >= 2


def test_non_positive_price_excludes_on_one_bar() -> None:
    bad = _bars(12)
    bad.iloc[4, bad.columns.get_loc("close")] = 0.0
    found = backtest.screen_price_quality(bad)
    assert found is not None
    assert found["reason"] == "non_positive_price"
    assert found["bars"] == 1

    negative = _bars(8)
    negative.iloc[3, negative.columns.get_loc("open")] = -2.0
    assert backtest.screen_price_quality(negative)["reason"] == "non_positive_price"


def test_prepare_context_records_cache_quality_and_missing_names(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("BACKTEST_PRICE_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(sector_strength, "SECTOR_ETFS", [])
    monkeypatch.setattr(sector_strength, "get_ticker_sector_etf", lambda *_a, **_k: None)

    good = _bars()
    spike = _bars()
    spike.iloc[20:, spike.columns.get_loc("close")] = 180.0
    zero = _bars()
    zero.iloc[15, zero.columns.get_loc("close")] = 0.0
    repeated = _bars()
    repeated.iloc[10, repeated.columns.get_loc("close")] = 170.0
    repeated.iloc[11:20, repeated.columns.get_loc("close")] = 80.0
    repeated.iloc[20, repeated.columns.get_loc("close")] = 140.0
    repeated.iloc[21:, repeated.columns.get_loc("close")] = 60.0
    empty = good.iloc[0:0].copy()
    frames = {"GOOD": good, "SPIKE": spike, "ZERO": zero, "REV": repeated, "GONE": empty, "SPY": good}

    def _fake(symbol: str, start: str, end: str, schwab_auth=None, cache_dir=None):
        frame = frames.get(symbol, empty)
        if frame.empty:
            meta = {
                "provider": "schwab",
                "used_fallback": False,
                "reason": "schwab_empty",
                "rows": 0,
                "cache_status": "miss",
            }
        else:
            meta = {
                "provider": "cache",
                "used_fallback": False,
                "reason": "cache_hit",
                "rows": len(frame),
                "cache_status": "hit",
            }
        return frame.copy(), meta

    monkeypatch.setattr(backtest, "_fetch_history_with_meta", _fake)
    ctx = backtest._prepare_context(
        "2020-01-02",
        "2020-06-30",
        watchlist=["GOOD", "SPIKE", "ZERO", "REV", "GONE"],
        skill_dir=tmp_path,
        history_pad_days=0,
    )
    assert ctx.watchlist == ["GOOD", "SPIKE"]
    quality = {row["ticker"]: row["reason"] for row in ctx.data_integrity["price_quality_excluded"]}
    assert quality["ZERO"] == "non_positive_price"
    assert quality["REV"] == "reversal_spikes"
    assert "SPIKE" not in quality
    missing = {row["ticker"] for row in ctx.data_integrity["no_data_tickers"]}
    assert "GONE" in missing
    assert ctx.data_integrity["survivorship_note"]
    assert ctx.data_integrity["price_cache_enabled"] is True
    assert ctx.data_integrity["price_cache_dir"] == str(tmp_path)
    assert ctx.data_integrity["history_cache_hit"] >= 2


def test_aggregate_keeps_quality_and_missing_names() -> None:
    row = _aggregate_era(
        name="crash_recovery",
        start_date="2020-01-01",
        end_date="2021-12-31",
        universe_size=4,
        chunk_payloads=[
            {
                "excluded_count": 2,
                "price_cache_enabled": True,
                "price_cache_dir": "/tmp/price_cache",
                "price_quality_excluded": [{"ticker": "FIZZ", "reason": "reversal_spikes"}],
                "no_data_tickers": [{"ticker": "EA", "reason": "no_data"}],
                "data_integrity": {"history_cache_hit": 3, "no_data_count": 1, "price_quality_excluded_count": 1},
                "trades": [],
            },
            {
                "excluded_count": 1,
                "price_quality_excluded": [{"ticker": "TDG", "reason": "non_positive_price"}],
                "no_data_tickers": [{"ticker": "WBD", "reason": "no_data"}],
                "data_integrity": {},
                "trades": [],
            },
        ],
    )
    assert row["price_cache_enabled"] is True
    assert row["price_cache_dir"] == "/tmp/price_cache"
    assert {item["ticker"] for item in row["price_quality_excluded"]} == {"FIZZ", "TDG"}
    assert {item["ticker"] for item in row["no_data_tickers"]} == {"EA", "WBD"}
    assert row["survivorship_note"]
    assert row["data_integrity"]["history_cache_hit"] == 3
