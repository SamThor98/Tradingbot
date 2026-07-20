#!/usr/bin/env python3
"""Build a five-era trade sample for prob-rank dual-run vs rank_v2.

Writes chunks under
``validation_artifacts/multi_era_chunks/prob_rank_dual_run_sample/``.

Uses the same liquid ~50-name universe as ``refresh_scoring_trade_sample.py``
but covers all catalog eras (including bear_rates + recent_current). This is
the pragmatic dual-run path — not a substitute for full ``control_legacy_aug``.

Example:
  python scripts/refresh_prob_rank_dual_run_sample.py
  python scripts/run_prob_rank_ops_pipeline.py \\
      --tickers-file validation_artifacts/prob_rank_dual_run_sample_tickers.txt \\
      --start 2015-01-01 --end 2026-07-01 \\
      --run-id prob_rank_dual_run_sample
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
ARTIFACT_DIR = SKILL_DIR / "validation_artifacts"
RUN_ID = "prob_rank_dual_run_sample"
CHUNKS_BASE = ARTIFACT_DIR / "multi_era_chunks" / RUN_ID
ENV_OVERRIDES = SKILL_DIR / "scripts" / "scoring_trade_sample_env.json"
MULTI_ERA = SKILL_DIR / "scripts" / "run_multi_era_backtest_schwab_only.py"
TICKERS_OUT = ARTIFACT_DIR / "prob_rank_dual_run_sample_tickers.txt"

# Shared liquid universe with scoring_trade_sample (keep in sync)
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
    {"era_name": "bear_rates", "start_date": "2022-01-01", "end_date": "2023-12-31"},
    {"era_name": "recent_current", "start_date": "2024-01-01", "end_date": "2026-07-01"},
]


def _run_chunk(*, era_name: str, start_date: str, end_date: str, tickers: list[str], out_file: Path) -> int:
    era_dir = CHUNKS_BASE / era_name
    era_dir.mkdir(parents=True, exist_ok=True)
    tickers_file = era_dir / "chunk_0000_tickers.json"
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
    print(f"[{RUN_ID}] {era_name} {start_date}..{end_date} n_tickers={len(tickers)}")
    proc = subprocess.run(cmd, cwd=str(SKILL_DIR), check=False)
    return int(proc.returncode)


def main() -> int:
    CHUNKS_BASE.mkdir(parents=True, exist_ok=True)
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    TICKERS_OUT.write_text("\n".join(SAMPLE_TICKERS) + "\n", encoding="utf-8")
    print(f"Wrote ticker list: {TICKERS_OUT}")

    total_trades = 0
    total_with_rank = 0
    for spec in CHUNK_SPECS:
        out_file = CHUNKS_BASE / spec["era_name"] / "chunk_0000.json"
        if out_file.is_file():
            payload = json.loads(out_file.read_text(encoding="utf-8"))
            n = len(payload.get("trades") or [])
            if n > 0:
                print(f"SKIP existing {out_file} ({n} trades)")
                total_trades += n
                total_with_rank += sum(
                    1 for t in (payload.get("trades") or []) if t.get("rank_score_v2") is not None
                )
                continue
        rc = _run_chunk(
            era_name=spec["era_name"],
            start_date=spec["start_date"],
            end_date=spec["end_date"],
            tickers=SAMPLE_TICKERS,
            out_file=out_file,
        )
        if rc != 0:
            print(f"FAIL: {spec['era_name']} exited {rc}")
            return rc
        if not out_file.is_file():
            print(f"FAIL: missing {out_file}")
            return 1
        payload = json.loads(out_file.read_text(encoding="utf-8"))
        trades = payload.get("trades") or []
        with_rank = sum(1 for t in trades if t.get("rank_score_v2") is not None)
        total_trades += len(trades)
        total_with_rank += with_rank
        print(f"Wrote {out_file} trades={len(trades)} with_rank_v2={with_rank}")

    summary = {
        "run_id": RUN_ID,
        "n_tickers": len(SAMPLE_TICKERS),
        "eras": [s["era_name"] for s in CHUNK_SPECS],
        "total_trades": total_trades,
        "total_with_rank_score_v2": total_with_rank,
        "chunks_base": str(CHUNKS_BASE),
        "note": "Sample dual-run universe; not full control_legacy_aug",
    }
    summary_path = ARTIFACT_DIR / f"{RUN_ID}_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    if total_trades == 0:
        return 1
    if total_trades < 100:
        print(f"WARN: only {total_trades} trades — dual-run will be thin")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
