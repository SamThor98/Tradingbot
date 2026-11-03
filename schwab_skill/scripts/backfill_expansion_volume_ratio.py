#!/usr/bin/env python3
"""Backfill expansion_volume_ratio onto a stored entry-timing replay tape.

The tape has ticker and entry date. It does not have the bars. Daily volume
comes from yfinance (auto-adjusted), one series per name, cached as CSV under
validation_artifacts/expansion_volume/bars. The ratio is entry-bar volume
divided by the 50-session average of the bars before that session. It is
written onto each trade. It is not a gate.

Usage (from schwab_skill/):
  python scripts/backfill_expansion_volume_ratio.py --run-id stage2_only_aug
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd

SKILL_DIR = Path(__file__).resolve().parent.parent
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

from stage_analysis import expansion_volume_ratio_by_date  # noqa: E402

ART = SKILL_DIR / "validation_artifacts"
BAR_DIR = ART / "expansion_volume" / "bars"
FETCH_START = "2014-06-01"
MAX_GAP_DAYS = 7
BATCH_SIZE = 25


def _cache_path(ticker: str) -> Path:
    safe = "".join(ch if ch.isalnum() else "_" for ch in ticker.upper())
    return BAR_DIR / f"{safe}.csv"


def _read_bars(ticker: str) -> pd.DataFrame | None:
    path = _cache_path(ticker)
    if not path.is_file():
        return None
    frame = pd.read_csv(path, parse_dates=["date"])
    if frame.empty or "volume" not in frame.columns:
        return None
    frame = frame.set_index("date").sort_index()
    return frame


def _write_bars(ticker: str, frame: pd.DataFrame) -> None:
    BAR_DIR.mkdir(parents=True, exist_ok=True)
    out = frame.copy()
    out.index.name = "date"
    out.reset_index().to_csv(_cache_path(ticker), index=False)


def _normalize_downloaded(raw: pd.DataFrame) -> pd.DataFrame | None:
    if raw is None or raw.empty:
        return None
    renamed = raw.rename(
        columns={
            "Open": "open",
            "High": "high",
            "Low": "low",
            "Close": "close",
            "Volume": "volume",
        }
    )
    needed = ["open", "high", "low", "close", "volume"]
    if any(col not in renamed.columns for col in needed):
        return None
    out = renamed[needed].apply(pd.to_numeric, errors="coerce")
    out = out.dropna(subset=["close", "volume"])
    out = out[(out["close"] > 0) & (out["volume"] >= 0)]
    if out.empty:
        return None
    out.index = pd.to_datetime(out.index)
    if getattr(out.index, "tz", None) is not None:
        out.index = out.index.tz_convert("UTC").tz_localize(None)
    out.index = out.index.normalize()
    out.index.name = "date"
    return out.sort_index()


def _split_download(raw: pd.DataFrame, tickers: list[str]) -> dict[str, pd.DataFrame]:
    if raw is None or raw.empty:
        return {}
    frames: dict[str, pd.DataFrame] = {}
    if not isinstance(raw.columns, pd.MultiIndex):
        if len(tickers) == 1:
            normalized = _normalize_downloaded(raw)
            if normalized is not None:
                frames[tickers[0]] = normalized
        return frames
    levels = [set(map(str, raw.columns.get_level_values(i))) for i in range(raw.columns.nlevels)]
    ticker_level = 0 if any(ticker in levels[0] for ticker in tickers) else 1
    for ticker in tickers:
        if ticker not in raw.columns.get_level_values(ticker_level):
            continue
        normalized = _normalize_downloaded(raw.xs(ticker, axis=1, level=ticker_level))
        if normalized is not None:
            frames[ticker] = normalized
    return frames


def _fetch_missing(tickers: list[str]) -> dict[str, Any]:
    import yfinance as yf

    missing = [ticker for ticker in tickers if _read_bars(ticker) is None]
    end = (datetime.now(timezone.utc).date() + timedelta(days=1)).isoformat()
    fetched = 0
    failed: list[str] = []
    print(f"Bars on disk: {len(tickers) - len(missing)}. To fetch: {len(missing)}.", flush=True)
    for offset in range(0, len(missing), BATCH_SIZE):
        batch = missing[offset : offset + BATCH_SIZE]
        try:
            raw = yf.download(
                batch,
                start=FETCH_START,
                end=end,
                auto_adjust=True,
                group_by="ticker",
                threads=True,
                progress=False,
            )
        except Exception as exc:
            print(f"Batch failed at {batch[0]}: {exc}", flush=True)
            failed.extend(batch)
            time.sleep(2)
            continue
        got = _split_download(raw, batch)
        for ticker in batch:
            frame = got.get(ticker)
            if frame is None or len(frame) < 51:
                failed.append(ticker)
                continue
            _write_bars(ticker, frame)
            fetched += 1
        print(f"Fetched {min(offset + BATCH_SIZE, len(missing))}/{len(missing)}", flush=True)
        time.sleep(0.5)
    return {"fetched": fetched, "failed": failed}


def _ratio_for_entry(series: pd.Series, entry: Any) -> float | None:
    if series is None or series.empty:
        return None
    when = pd.Timestamp(entry)
    if when.tzinfo is not None:
        when = when.tz_convert("UTC").tz_localize(None)
    when = when.normalize()
    prior = series.loc[:when]
    if prior.empty:
        return None
    bar = pd.Timestamp(prior.index[-1]).normalize()
    if (when - bar).days > MAX_GAP_DAYS:
        return None
    value = prior.iloc[-1]
    if pd.isna(value):
        return None
    return round(float(value), 4)


def backfill(run_id: str) -> dict[str, Any]:
    path = ART / f"entry_timing_replay_cache_{run_id}.json"
    rows = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(rows, list):
        raise SystemExit(f"{path.name} is not a trade list")
    tickers = sorted({str(row.get("ticker") or "").upper() for row in rows if row.get("ticker")})
    fetch = _fetch_missing(tickers)
    series_by_ticker: dict[str, pd.Series] = {}
    for ticker in tickers:
        bars = _read_bars(ticker)
        if bars is None:
            continue
        series_by_ticker[ticker] = expansion_volume_ratio_by_date(bars)

    filled = 0
    for row in rows:
        ticker = str(row.get("ticker") or "").upper()
        ratio = _ratio_for_entry(series_by_ticker.get(ticker, pd.Series(dtype=float)), row.get("entry_date"))
        row["expansion_volume_ratio"] = ratio
        if ratio is not None:
            filled += 1

    backup = path.with_name(path.stem + ".before_expansion_volume.json")
    if not backup.is_file():
        backup.write_bytes(path.read_bytes())
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(rows), encoding="utf-8")
    tmp.replace(path)

    values = [row["expansion_volume_ratio"] for row in rows if row.get("expansion_volume_ratio") is not None]
    series = pd.Series(values, dtype="float64")
    missing_tickers = [ticker for ticker in tickers if ticker not in series_by_ticker]
    summary = {
        "run_id": run_id,
        "tape": path.name,
        "bar_source": "yfinance auto_adjust daily volume, cached as CSV",
        "bar_start": FETCH_START,
        "max_gap_days": MAX_GAP_DAYS,
        "trades": len(rows),
        "filled": filled,
        "missing": len(rows) - filled,
        "tickers": len(tickers),
        "tickers_with_bars": len(series_by_ticker),
        "tickers_missing": missing_tickers,
        "fetch_failed": fetch["failed"],
        "median": None if series.empty else round(float(series.median()), 4),
        "p10": None if series.empty else round(float(series.quantile(0.10)), 4),
        "p90": None if series.empty else round(float(series.quantile(0.90)), 4),
        "share_below_1": None if series.empty else round(float((series < 1).mean()), 4),
        "share_at_least_1": None if series.empty else round(float((series >= 1).mean()), 4),
    }
    summary_path = ART / "expansion_volume" / f"{run_id}_summary.json"
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps({k: summary[k] for k in summary if k not in {"tickers_missing", "fetch_failed"}}, indent=2))
    print(f"Wrote {path}")
    print(f"Wrote {summary_path}")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill expansion volume ratio onto a replay tape.")
    parser.add_argument("--run-id", default="stage2_only_aug")
    args = parser.parse_args()
    backfill(str(args.run_id))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
