#!/usr/bin/env python3
"""Build augmented trade chunks for scoring validation on realized trades.

Writes chunks under ``validation_artifacts/multi_era_chunks/scoring_trade_sample/``
so ``validate_scoring_metrics.py --source trades --run-id scoring_trade_sample`` can
exercise rank_score / pts_* fields without a full multi-era sweep.

Example:
    python scripts/refresh_scoring_trade_sample.py
    python scripts/validate_scoring_metrics.py --source trades --run-id scoring_trade_sample --strict
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
ARTIFACT_DIR = SKILL_DIR / "validation_artifacts"
CHUNKS_BASE = ARTIFACT_DIR / "multi_era_chunks" / "scoring_trade_sample"
ENV_OVERRIDES = SKILL_DIR / "scripts" / "scoring_trade_sample_env.json"
MULTI_ERA = SKILL_DIR / "scripts" / "run_multi_era_backtest_schwab_only.py"

SAMPLE_TICKERS = [
    "AAPL", "MSFT", "GOOGL", "GOOG", "AMZN", "META", "NVDA", "TSLA", "JPM", "V",
    "JNJ", "WMT", "PG", "MA", "HD", "DIS", "BAC", "XOM", "CVX", "PFE",
    "KO", "PEP", "INTC", "CSCO", "VZ", "T", "ABT", "MRK", "MCD", "NKE",
    "ORCL", "CRM", "ADBE", "NFLX", "AMD", "QCOM", "TXN", "UNH", "LLY", "AVGO",
    "COST", "TMO", "ACN", "DHR", "NEE", "LIN", "PM", "RTX", "HON", "LOW",
]

CHUNK_SPECS: list[dict[str, str]] = [
    {"era_name": "late_bull", "start_date": "2015-01-01", "end_date": "2017-12-31"},
    {"era_name": "volatility_chop", "start_date": "2018-01-01", "end_date": "2019-12-31"},
    {"era_name": "crash_recovery", "start_date": "2020-01-01", "end_date": "2021-12-31"},
]


def _run_chunk(*, era_name: str, start_date: str, end_date: str, tickers: list[str], out_file: Path) -> int:
    era_dir = CHUNKS_BASE / era_name
    era_dir.mkdir(parents=True, exist_ok=True)
    tickers_file = era_dir / f"chunk_{out_file.stem.split('_')[-1]}_tickers.json"
    tickers_file.write_text(json.dumps(tickers), encoding="utf-8")
    cmd = [
        sys.executable,
        str(MULTI_ERA),
        "--single-chunk",
        "--start-date",
        start_date,
        "--end-date",
        end_date,
        "--era-name",
        era_name,
        "--tickers-file",
        str(tickers_file),
        "--out-file",
        str(out_file),
        "--env-overrides",
        str(ENV_OVERRIDES),
    ]
    print(f"Running augmented trade sample ({era_name}, {start_date}..{end_date}, {len(tickers)} tickers)...")
    proc = subprocess.run(cmd, cwd=str(SKILL_DIR), check=False)
    return int(proc.returncode)


def main() -> int:
    CHUNKS_BASE.mkdir(parents=True, exist_ok=True)
    total_trades = 0
    total_with_scores = 0

    for spec in CHUNK_SPECS:
        out_file = CHUNKS_BASE / spec["era_name"] / "chunk_0000.json"
        rc = _run_chunk(
            era_name=spec["era_name"],
            start_date=spec["start_date"],
            end_date=spec["end_date"],
            tickers=SAMPLE_TICKERS,
            out_file=out_file,
        )
        if rc != 0:
            print(f"FAIL: sample chunk {spec['era_name']} exited {rc}")
            return rc
        if not out_file.exists():
            print(f"FAIL: expected output missing: {out_file}")
            return 1
        payload = json.loads(out_file.read_text(encoding="utf-8"))
        trades = payload.get("trades") or []
        score_keys = ("rank_score", "pts_volume", "composite_score", "reliability_score")
        with_scores = sum(1 for t in trades if any(t.get(k) is not None for k in score_keys))
        total_trades += len(trades)
        total_with_scores += with_scores
        print(f"Wrote {out_file} ({len(trades)} trades, {with_scores} with score stack fields)")

    print(f"Total trades across sample chunks: {total_trades}")
    if total_trades < 100:
        print(
            f"WARN: only {total_trades} trades — market data may be limited; "
            "rerun with Schwab auth or yfinance connectivity for a larger sample",
        )
    if total_trades == 0:
        return 1
    if total_with_scores == 0:
        print("WARN: trades lack score stack — check BACKTEST_AUGMENTED_LOGGING and advisory stack")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
