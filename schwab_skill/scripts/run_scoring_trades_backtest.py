#!/usr/bin/env python3
"""Run augmented multi-era trade chunks for scoring validation on realized PnL.

Writes ``validation_artifacts/multi_era_chunks/scoring_trades_v1/`` with full
score-stack fields (composite_score, pts_*, close_vs_sma200_pct) when the
backtest produces trades.

Example:
    python scripts/run_scoring_trades_backtest.py
    python scripts/validate_scoring_metrics.py --source trades --run-id scoring_trades_v1
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
ARTIFACT_DIR = SKILL_DIR / "validation_artifacts"
DEFAULT_RUN_ID = "scoring_trades_v2"
MULTI_ERA = SKILL_DIR / "scripts" / "run_multi_era_backtest_schwab_only.py"
ENV_OVERRIDES = SKILL_DIR / "scripts" / "scoring_trade_sample_env.json"

# Eras/windows with historically nonzero trade pickup in augmented chunks.
ERA_WINDOWS: list[tuple[str, str, str]] = [
    ("late_bull", "2016-01-01", "2017-12-31"),
    ("bear_rates", "2022-01-01", "2023-06-30"),
    ("recent_current", "2024-01-01", "2025-06-01"),
]

SAMPLE_TICKERS = [
    "AAPL", "MSFT", "GOOGL", "AMZN", "META", "NVDA", "TSLA", "JPM", "V", "JNJ",
    "WMT", "PG", "MA", "HD", "DIS", "BAC", "XOM", "CVX", "PFE", "KO",
    "PEP", "INTC", "CSCO", "VZ", "T", "ABT", "MRK", "MCD", "NKE", "ORCL",
    "ACGL", "ANET", "AVGO", "CDNS", "CEG", "DECK", "FSLR", "GE", "LLY", "UBER",
]


def _run_era(run_id: str, era_name: str, start: str, end: str) -> tuple[int, int]:
    chunks_root = ARTIFACT_DIR / "multi_era_chunks" / run_id
    era_dir = chunks_root / era_name
    era_dir.mkdir(parents=True, exist_ok=True)
    tickers_file = era_dir / "chunk_0000_tickers.json"
    out_file = era_dir / "chunk_0000.json"
    tickers_file.write_text(json.dumps(SAMPLE_TICKERS), encoding="utf-8")

    cmd = [
        sys.executable,
        str(MULTI_ERA),
        "--single-chunk",
        "--start-date",
        start,
        "--end-date",
        end,
        "--era-name",
        era_name,
        "--tickers-file",
        str(tickers_file),
        "--out-file",
        str(out_file),
        "--env-overrides",
        str(ENV_OVERRIDES),
    ]
    print(f"\n=== {era_name} ({start} -> {end}) ===")
    proc = subprocess.run(cmd, cwd=str(SKILL_DIR), check=False)
    if proc.returncode != 0 or not out_file.exists():
        print(f"WARN: {era_name} chunk failed (rc={proc.returncode})")
        return 0, 0

    payload = json.loads(out_file.read_text(encoding="utf-8"))
    trades = payload.get("trades") or []
    score_keys = ("composite_score", "pts_volume", "close_vs_sma200_pct", "rank_score")
    with_scores = sum(1 for t in trades if any(t.get(k) is not None for k in score_keys))
    print(f"  trades={len(trades)} with_score_stack={with_scores}")
    return len(trades), with_scores


def main() -> int:
    parser = argparse.ArgumentParser(description="Run augmented trade chunks for scoring validation.")
    parser.add_argument("--run-id", default=DEFAULT_RUN_ID, help="Chunk directory name under multi_era_chunks/")
    args = parser.parse_args()
    run_id = str(args.run_id)
    chunks_root = ARTIFACT_DIR / "multi_era_chunks" / run_id
    chunks_root.mkdir(parents=True, exist_ok=True)
    total_trades = 0
    total_scored = 0
    for era_name, start, end in ERA_WINDOWS:
        n, scored = _run_era(run_id, era_name, start, end)
        total_trades += n
        total_scored += scored

    meta = {
        "run_id": run_id,
        "eras": [e[0] for e in ERA_WINDOWS],
        "total_trades": total_trades,
        "trades_with_score_stack": total_scored,
    }
    (chunks_root / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print(f"\nWrote {chunks_root} — {total_trades} trades ({total_scored} with score stack)")
    if total_trades < 30:
        print("WARN: fewer than 30 trades — validation may skip; check market data / auth")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
