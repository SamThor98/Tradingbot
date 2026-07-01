#!/usr/bin/env python3
"""Run a focused live scan for the P0 entry-timing experiment and compare to offline.

Use when ``last_scan`` is stale (compare verdict ``stale_scan``) but experiment
env is ready. Persists results to ``webapp/webapp.db`` like the dashboard.

Examples (from schwab_skill/):
  python scripts/run_entry_timing_experiment_scan.py --smoke
  python scripts/run_entry_timing_experiment_scan.py --max-tickers 120
  python scripts/run_entry_timing_experiment_scan.py --smoke --no-persist
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SKILL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL_DIR))

from core.entry_timing_live_compare import extract_live_entry_shadow_metrics  # noqa: E402

DEFAULT_RUN_ID = "control_legacy_aug"
LOCAL_USER_ID = "local"
LAST_SCAN_SIGNALS_CAP = 200

EXPERIMENT_SMOKE_TICKERS = [
    "AAPL",
    "MSFT",
    "NVDA",
    "AMZN",
    "META",
    "GOOGL",
    "TSLA",
    "AMD",
    "AVGO",
    "NFLX",
    "CRM",
    "ORCL",
    "ADBE",
    "INTC",
    "QCOM",
    "COST",
    "PEP",
    "KO",
    "JPM",
    "BAC",
    "WMT",
    "UNH",
    "LLY",
    "ABBV",
    "MRK",
    "PFE",
    "TMO",
    "ABT",
    "DHR",
    "CAT",
    "DE",
    "GE",
    "RTX",
    "LMT",
    "BA",
    "UBER",
    "SHOP",
    "SNOW",
    "PANW",
    "CRWD",
]


def _load_sp1500_tickers(skill_dir: Path, limit: int | None) -> list[str]:
    from signal_scanner import _load_watchlist

    tickers = list(_load_watchlist(skill_dir))
    if limit is not None and limit > 0:
        tickers = tickers[:limit]
    return tickers


def _persist_last_scan(
    *,
    sqlite_path: Path,
    last_scan: dict[str, Any],
) -> None:
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from webapp.models import AppState

    engine = create_engine(
        f"sqlite:///{sqlite_path.resolve().as_posix()}",
        connect_args={"check_same_thread": False},
    )
    Session = sessionmaker(bind=engine)
    db = Session()
    try:
        row = db.query(AppState).filter(AppState.user_id == LOCAL_USER_ID, AppState.key == "last_scan").first()
        if not row:
            row = AppState(user_id=LOCAL_USER_ID, key="last_scan", value_json=last_scan)
            db.add(row)
        else:
            row.value_json = last_scan
        db.commit()
    finally:
        db.close()


def _print_scan_summary(diagnostics: dict[str, Any]) -> None:
    live = extract_live_entry_shadow_metrics(diagnostics)
    stage_a = int(live.get("stage_a_candidates") or 0)
    stage2_eval = int(live.get("entry_shadow_stage2_evaluated") or 0)
    print(f"Stage A candidates: {stage_a}")
    print(f"Stage 2 shadow evaluated: {stage2_eval}")
    print(
        f"Entry shadow would-filter (stage A): {live.get('entry_shadow_would_filter_any')} "
        f"({live.get('would_filter_pct_stage_a')})"
    )
    print(
        f"Entry shadow would-filter (stage 2): {live.get('entry_shadow_stage2_would_filter_any')} "
        f"({live.get('would_filter_pct_stage2')})"
    )
    if live.get("would_filter_pct") is not None:
        print(
            f"Compare rate: {live.get('would_filter_pct'):.1f}% "
            f"(source={live.get('rate_source')})"
        )
    print(f"Profile: {live.get('entry_timing_shadow_profile')} mode={live.get('entry_timing_shadow_mode')}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", default=DEFAULT_RUN_ID)
    parser.add_argument("--smoke", action="store_true", help=f"Scan {len(EXPERIMENT_SMOKE_TICKERS)} liquid tickers only")
    parser.add_argument(
        "--max-tickers",
        type=int,
        default=0,
        help="When not --smoke, cap SP1500 watchlist to N tickers (0 = full watchlist)",
    )
    parser.add_argument(
        "--sqlite",
        default=str(SKILL_DIR / "webapp" / "webapp.db"),
        help="Persist last_scan to this SQLite path (default: webapp/webapp.db)",
    )
    parser.add_argument("--no-persist", action="store_true", help="Do not write last_scan to SQLite")
    parser.add_argument("--no-compare", action="store_true", help="Skip live/offline compare artifact")
    parser.add_argument(
        "--min-stage-a",
        type=int,
        default=0,
        help="Min Stage A for compare band check (default: 3 for --smoke else 10)",
    )
    args = parser.parse_args()
    min_stage_a = args.min_stage_a if args.min_stage_a > 0 else (3 if args.smoke else 10)

    from config import get_entry_timing_breakout_buffer_readiness
    from core.entry_timing_live_compare import (
        build_live_entry_shadow_compare_report,
        write_live_entry_shadow_compare_report,
    )
    from core.env_local import ENTRY_TIMING_LIVE_ENV, reload_env_file_into_process, restore_process_env
    from core.scan_service import run_scan

    env_path = SKILL_DIR / ".env"
    saved_env = reload_env_file_into_process(env_path, keys=list(ENTRY_TIMING_LIVE_ENV.keys()))
    try:
        readiness = get_entry_timing_breakout_buffer_readiness(SKILL_DIR)
        if not readiness.get("ready"):
            print("FAIL: breakout-buffer profile not ready in this process")
            for item in readiness.get("missing_env") or []:
                print(f"- {item}")
            print("Run: python scripts/apply_entry_timing_experiment_env.py or apply_entry_timing_live_env.py")
            return 1

        mode = str(readiness.get("mode") or "")

        if args.smoke:
            watchlist = list(EXPERIMENT_SMOKE_TICKERS)
        else:
            limit = args.max_tickers if args.max_tickers > 0 else None
            watchlist = _load_sp1500_tickers(SKILL_DIR, limit)

        print(f"Running entry-timing scan on {len(watchlist)} tickers…")
        print(f"Process profile: {readiness.get('profile')} mode={mode}")

        scan_out = run_scan(skill_dir=SKILL_DIR, watchlist_override=watchlist)
        diagnostics = scan_out.diagnostics
        signals = scan_out.signals
        finished_at = datetime.now(timezone.utc).isoformat()

        _print_scan_summary(diagnostics)
        print(f"Signals kept: {len(signals)}")

        profile_ok = str(diagnostics.get("entry_timing_shadow_profile") or "") == "breakout_buffer_only_0.010"
        mode_ok = str(diagnostics.get("entry_timing_shadow_mode") or "") in {"shadow", "live"}
        if profile_ok and mode_ok:
            print(f"PASS: profile active on scan diagnostics (mode={diagnostics.get('entry_timing_shadow_mode')})")
        else:
            print("FAIL: experiment profile not reflected in scan diagnostics")
            return 1

        stage_a = int(diagnostics.get("stage_a_candidates") or 0)
        if args.smoke and stage_a < 10:
            print(
                f"NOTE: smoke sample has {stage_a} Stage A candidates — run full SP1500 scan "
                "for live/offline would-filter rate validation (~50% target)."
            )

        if not args.no_persist:
            sqlite_path = Path(args.sqlite)
            last_scan = {
                "at": finished_at,
                "signals_found": len(signals),
                "signals": signals[:LAST_SCAN_SIGNALS_CAP],
                "shortlist_signals": scan_out.shortlist_signals[:LAST_SCAN_SIGNALS_CAP],
                "diagnostics": diagnostics,
                "diagnostics_summary": None,
                "strategy_summary": None,
            }
            _persist_last_scan(sqlite_path=sqlite_path, last_scan=last_scan)
            print(f"Persisted last_scan to {sqlite_path}")

        if args.no_compare:
            return 0

        live_meta = {
            "source": "run_entry_timing_experiment_scan",
            "scan_at": finished_at,
            "signals_found": len(signals),
            "watchlist_size": len(watchlist),
        }
        write_live_entry_shadow_compare_report(
            diagnostics,
            skill_dir=SKILL_DIR,
            run_id=args.run_id,
            live_meta=live_meta,
            experiment_env_ready=True,
            min_stage_a=min_stage_a,
        )
        report = build_live_entry_shadow_compare_report(
            diagnostics,
            skill_dir=SKILL_DIR,
            run_id=args.run_id,
            live_meta=live_meta,
            experiment_env_ready=True,
            min_stage_a=min_stage_a,
        )
        if report is None:
            print("FAIL: could not build compare report")
            return 1

        comparison = report.get("comparison") or {}
        verdict = comparison.get("verdict", "unknown")
        print(f"Live/offline verdict: {verdict}")
        for warn in comparison.get("warnings") or []:
            print(f"WARN: {warn}")
        for err in comparison.get("errors") or []:
            print(f"FAIL: {err}")

        out = SKILL_DIR / "validation_artifacts" / f"live_entry_shadow_compare_{args.run_id}.json"
        print(f"Wrote {out}")

        if args.smoke and stage_a < 10 and verdict == "fail":
            print("Smoke scan: profile verified; rate band check deferred until full scan.")
            return 0
        return 1 if verdict == "fail" else 0
    finally:
        restore_process_env(saved_env)


if __name__ == "__main__":
    raise SystemExit(main())
