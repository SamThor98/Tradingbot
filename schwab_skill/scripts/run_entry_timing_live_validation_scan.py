#!/usr/bin/env python3
"""Run SP1500 scan with entry-timing live enforcement and validate drop rate.

Applies live env from .env into this process, runs scan, checks that
entry_timing_blocked aligns with the prior shadow would-filter baseline.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
ENV_PATH = SKILL_DIR / ".env"
sys.path.insert(0, str(SKILL_DIR))

DEFAULT_RUN_ID = "control_legacy_aug"
SHADOW_BASELINE = {
    "stage_a_candidates": 88,
    "entry_shadow_would_filter_any": 41,
    "watchlist_size": 1503,
}


def _load_shadow_baseline(path: Path) -> dict:
    if not path.exists():
        return dict(SHADOW_BASELINE)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return dict(SHADOW_BASELINE)
    return payload if isinstance(payload, dict) else dict(SHADOW_BASELINE)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", default=DEFAULT_RUN_ID)
    parser.add_argument("--max-tickers", type=int, default=0, help="0 = full watchlist")
    parser.add_argument(
        "--shadow-baseline",
        default=str(SKILL_DIR / "validation_artifacts" / "entry_timing_shadow_baseline_control_legacy_aug.json"),
    )
    args = parser.parse_args()

    from config import get_entry_timing_breakout_buffer_readiness, get_entry_timing_shadow_mode
    from core.env_local import ENTRY_TIMING_LIVE_ENV, reload_env_file_into_process, restore_process_env
    from core.scan_service import run_scan
    from signal_scanner import _load_watchlist

    saved = reload_env_file_into_process(ENV_PATH, keys=list(ENTRY_TIMING_LIVE_ENV.keys()))
    try:
        readiness = get_entry_timing_breakout_buffer_readiness(SKILL_DIR)
        if not readiness.get("ready") or get_entry_timing_shadow_mode(SKILL_DIR) != "live":
            print("FAIL: process env not in live breakout-buffer profile")
            for item in readiness.get("missing_env") or []:
                print(f"- {item}")
            print(f"- mode={get_entry_timing_shadow_mode(SKILL_DIR)} (expected live)")
            print("Run: python scripts/apply_entry_timing_live_env.py")
            return 1

        baseline = _load_shadow_baseline(Path(args.shadow_baseline))
        limit = args.max_tickers if args.max_tickers > 0 else None
        watchlist = list(_load_watchlist(SKILL_DIR))
        if limit:
            watchlist = watchlist[:limit]

        print(f"Running live-enforced scan on {len(watchlist)} tickers (mode=live)…")
        scan_out = run_scan(skill_dir=SKILL_DIR, watchlist_override=watchlist)
        diagnostics = scan_out.diagnostics
        finished_at = datetime.now(timezone.utc).isoformat()

        stage_a = int(diagnostics.get("stage_a_candidates") or 0)
        blocked = int(diagnostics.get("entry_timing_blocked") or 0)
        watchlist_size = int(diagnostics.get("watchlist_size") or len(watchlist))
        expected_kept = int(baseline.get("stage_a_candidates") or 0) - int(
            baseline.get("entry_shadow_would_filter_any") or 0
        )
        blocked_rate = (100.0 * blocked / watchlist_size) if watchlist_size else None
        kept_delta = stage_a - expected_kept if expected_kept else None

        print(f"Stage A kept: {stage_a}")
        print(f"entry_timing_blocked: {blocked}")
        print(f"Shadow baseline kept: {expected_kept} (from {baseline.get('stage_a_candidates')} - {baseline.get('entry_shadow_would_filter_any')})")
        if kept_delta is not None:
            print(f"Delta vs shadow expected kept: {kept_delta:+d}")
        if blocked_rate is not None:
            print(f"Blocked / watchlist: {blocked_rate:.1f}%")

        from scripts.run_entry_timing_experiment_scan import _persist_last_scan

        _persist_last_scan(
            sqlite_path=SKILL_DIR / "webapp" / "webapp.db",
            last_scan={
                "at": finished_at,
                "signals_found": len(scan_out.signals),
                "signals": scan_out.signals[:200],
                "shortlist_signals": scan_out.shortlist_signals[:200],
                "diagnostics": diagnostics,
                "diagnostics_summary": None,
                "strategy_summary": None,
            },
        )

        errors: list[str] = []
        if int(diagnostics.get("entry_timing_live_enforced") or 0) != 1:
            errors.append("entry_timing_live_enforced != 1")
        if blocked <= 0:
            errors.append("entry_timing_blocked is zero")
        if stage_a <= 0:
            errors.append("stage_a_candidates is zero")
        if expected_kept > 0 and abs(stage_a - expected_kept) > max(15, expected_kept * 0.35):
            errors.append(
                f"stage_a {stage_a} too far from shadow expected kept {expected_kept} (±35%)"
            )

        if errors:
            print("Live validation FAIL:")
            for err in errors:
                print(f"- {err}")
            return 1

        print("Live validation PASS: enforcement active and Stage A retention aligns with shadow baseline")
        return 0
    finally:
        restore_process_env(saved)


if __name__ == "__main__":
    raise SystemExit(main())
