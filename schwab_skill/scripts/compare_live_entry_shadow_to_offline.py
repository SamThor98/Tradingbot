#!/usr/bin/env python3
"""Compare live scan entry-timing shadow counters to offline replay evidence (P0).

Reads the latest ``last_scan`` diagnostics (SQLite or JSON) and checks that
``entry_shadow_would_filter_any / stage_a_candidates`` aligns with the offline
breakout-buffer-only experiment (~50% would-filter on control_legacy_aug).

Exit codes:
  0 — pass, warn, skip (no live scan unless --require-live-scan)
  1 — fail or missing offline artifacts
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL_DIR))

from core.entry_timing_live_compare import (  # noqa: E402
    DEFAULT_RUN_ID,
    _safe_int,
    build_live_entry_shadow_compare_report,
    load_last_scan_diagnostics,
    load_validation_artifact,
    offline_entry_timing_targets,
    write_live_entry_shadow_compare_report,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", default=DEFAULT_RUN_ID)
    parser.add_argument(
        "--sqlite",
        default=str(SKILL_DIR / "webapp" / "webapp.db"),
        help="Local dashboard SQLite path (default: webapp/webapp.db)",
    )
    parser.add_argument("--diagnostics-json", help="Optional JSON file with diagnostics or last_scan blob")
    parser.add_argument(
        "--require-live-scan",
        action="store_true",
        help="Fail when no live scan is available (default: skip with exit 0)",
    )
    parser.add_argument(
        "--no-expect-experiment",
        action="store_true",
        help="Do not require breakout_buffer_only_0.010 profile on live scan",
    )
    parser.add_argument("--min-stage-a", type=int, default=10)
    parser.add_argument(
        "--write-artifact",
        action="store_true",
        help="Write validation_artifacts/live_entry_shadow_compare_<run_id>.json",
    )
    args = parser.parse_args()

    if load_validation_artifact(SKILL_DIR, f"entry_timing_shadow_counterfactual_{args.run_id}.json") is None:
        print(f"FAIL: missing entry_timing_shadow_counterfactual_{args.run_id}.json")
        return 1

    diag_json = Path(args.diagnostics_json) if args.diagnostics_json else None
    sqlite_path = Path(args.sqlite) if args.sqlite else None
    diagnostics, live_meta = load_last_scan_diagnostics(
        sqlite_path=sqlite_path,
        diagnostics_json=diag_json,
    )

    if diagnostics is None:
        msg = live_meta.get("error") or "no live scan diagnostics"
        if args.require_live_scan:
            print(f"FAIL: {msg}")
            return 1
        print(f"SKIP: {msg}")
        if args.write_artifact:
            offline = offline_entry_timing_targets(
                load_validation_artifact(SKILL_DIR, f"entry_timing_shadow_counterfactual_{args.run_id}.json") or {}
            )
            out = SKILL_DIR / "validation_artifacts" / f"live_entry_shadow_compare_{args.run_id}.json"
            out.write_text(
                json.dumps(
                    {
                        "generated_at": datetime.now(timezone.utc).isoformat(),
                        "run_id": args.run_id,
                        "skipped": True,
                        "live_meta": live_meta,
                        "offline": offline,
                        "live": None,
                        "comparison": None,
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
            print(f"Wrote {out}")
        return 0

    expect_experiment = not args.no_expect_experiment
    min_stage_a = max(1, int(args.min_stage_a))
    from config import get_entry_timing_experiment_readiness

    env_ready = bool(get_entry_timing_experiment_readiness(SKILL_DIR).get("ready"))
    if args.write_artifact:
        live_meta_with_watchlist = dict(live_meta)
        if not live_meta_with_watchlist.get("watchlist_size"):
            live_meta_with_watchlist["watchlist_size"] = _safe_int(diagnostics.get("watchlist_size"))
        write_live_entry_shadow_compare_report(
            diagnostics,
            skill_dir=SKILL_DIR,
            run_id=args.run_id,
            live_meta=live_meta_with_watchlist,
            expect_experiment=expect_experiment,
            min_stage_a=min_stage_a,
            experiment_env_ready=env_ready,
        )
        print(f"Wrote {SKILL_DIR / 'validation_artifacts' / f'live_entry_shadow_compare_{args.run_id}.json'}")

    report = build_live_entry_shadow_compare_report(
        diagnostics,
        skill_dir=SKILL_DIR,
        run_id=args.run_id,
        live_meta=live_meta,
        expect_experiment=expect_experiment,
        min_stage_a=min_stage_a,
        experiment_env_ready=env_ready,
    )
    if report is None:
        print("FAIL: could not build compare report")
        return 1

    comparison = report.get("comparison") or {}
    live = report.get("live") or {}
    offline = report.get("offline") or {}
    verdict = comparison.get("verdict", "unknown")

    live_pct = live.get("would_filter_pct")
    offline_pct = offline.get("would_filter_pct_offline")
    if live_pct is not None:
        print(
            f"Live entry shadow: {live.get('compare_would_filter', live.get('entry_shadow_would_filter_any'))}/"
            f"{live.get('compare_denominator', live.get('stage_a_candidates'))} "
            f"({live_pct:.1f}% would-filter, source={live.get('rate_source', 'stage_a_candidates')})"
        )
    else:
        print("Live entry shadow: rate unavailable")
    if offline_pct is not None:
        print(f"Offline target: ~{offline_pct:.1f}% would-filter (retain {offline.get('retention_pct'):.1f}%)")
    print(f"Profile: {live.get('entry_timing_shadow_profile')} mode={live.get('entry_timing_shadow_mode')}")
    print(f"Verdict: {verdict}")

    for warn in comparison.get("warnings") or []:
        print(f"WARN: {warn}")
    for err in comparison.get("errors") or []:
        print(f"FAIL: {err}")

    if verdict == "fail":
        return 1
    if verdict in {"skip", "stale_scan"}:
        print(f"{verdict.upper()}: see warnings above")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
