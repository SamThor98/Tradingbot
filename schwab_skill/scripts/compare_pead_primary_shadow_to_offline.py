#!/usr/bin/env python3
"""Compare saved-scan PEAD-primary shadow admits to offline pead_primary rules.

Checks:
  1) Non-execution invariant — no ``entry_family=pead_primary`` on returned signals
  2) Saved shadow rows still satisfy beat + surprise > 0
  3) Optional ``--revalidate``: refetch history/earnings and re-run admit rules

Exit codes:
  0 — pass, warn, or skip
  1 — fail
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL_DIR))

from core.pead_primary_shadow_compare import (  # noqa: E402
    build_pead_primary_shadow_compare_report,
    load_scan_bundle,
    write_pead_primary_shadow_compare_report,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--sqlite",
        default=str(SKILL_DIR / "webapp" / "webapp.db"),
        help="Local dashboard SQLite path (default: webapp/webapp.db)",
    )
    parser.add_argument(
        "--diagnostics-json",
        help="Optional JSON file with diagnostics or last_scan blob",
    )
    parser.add_argument(
        "--require-scan",
        action="store_true",
        help="Fail when no last_scan is available (default: skip exit 0)",
    )
    parser.add_argument(
        "--revalidate",
        action="store_true",
        help="Refetch history/earnings and re-run pead_primary admit for shadow names",
    )
    parser.add_argument(
        "--write-artifact",
        action="store_true",
        help="Write validation_artifacts/pead_primary_shadow_compare_<stamp>.json",
    )
    args = parser.parse_args()

    diag_json = Path(args.diagnostics_json) if args.diagnostics_json else None
    sqlite_path = Path(args.sqlite) if args.sqlite else None
    diagnostics, signals, live_meta = load_scan_bundle(
        sqlite_path=sqlite_path,
        diagnostics_json=diag_json,
    )

    if diagnostics is None:
        msg = live_meta.get("error") or "no live scan diagnostics"
        if args.require_scan:
            print(f"FAIL: {msg}")
            return 1
        print(f"SKIP: {msg}")
        if args.write_artifact:
            report = {
                "verdict": "skip",
                "errors": [],
                "warnings": [msg],
                "live_meta": live_meta,
            }
            path = write_pead_primary_shadow_compare_report(report, SKILL_DIR)
            print(f"Wrote {path}")
        return 0

    auth = None
    if args.revalidate:
        try:
            from schwab_auth import DualSchwabAuth

            auth = DualSchwabAuth(skill_dir=SKILL_DIR)
        except Exception:
            auth = None

    report = build_pead_primary_shadow_compare_report(
        diagnostics,
        signals=signals,
        live_meta=live_meta,
        revalidate=bool(args.revalidate),
        skill_dir=SKILL_DIR if args.revalidate else None,
        auth=auth,
    )

    if args.write_artifact:
        path = write_pead_primary_shadow_compare_report(report, SKILL_DIR)
        print(f"Wrote {path}")

    verdict = str(report.get("verdict") or "fail")
    counters = report.get("counters") or {}
    mode = report.get("mode") or {}
    print(
        f"verdict={verdict} effective_mode={mode.get('effective')} "
        f"evaluated={counters.get('pead_primary_evaluated')} "
        f"admitted={counters.get('pead_primary_admitted')} "
        f"overlap={counters.get('overlap_with_stage2')} "
        f"would_rank_top_n={counters.get('pead_primary_would_rank_top_n')} "
        f"capacity_top_n={counters.get('pead_primary_would_rank_capacity_top_n')} "
        f"capacity_arm={counters.get('pead_primary_capacity_rank_arm')} "
        f"shadow_truncated={counters.get('pead_primary_shadow_truncated')} "
        f"executable_stage2_only={report.get('invariant', {}).get('executable_still_stage2_only')} "
        f"dq={report.get('data_quality')}"
    )
    canary = report.get("canary_sleeve") or {}
    if canary:
        print(
            f"canary_sleeve n={canary.get('n_names')} "
            f"arm={canary.get('rank_arm')} "
            f"tickers={canary.get('tickers')} "
            f"rth_dq_ok={canary.get('rth_dq_ok')} "
            f"enablement_ready={canary.get('enablement_ready')}"
        )
    for err in report.get("errors") or []:
        print(f"ERROR: {err}")
    for warn in report.get("warnings") or []:
        print(f"WARN: {warn}")

    if verdict == "fail":
        print("FAIL: PEAD-primary shadow parity checks failed")
        return 1
    if verdict == "skip":
        print("SKIP: PEAD-primary shadow mode off or no comparable scan")
        return 0
    if verdict == "warn":
        print("PASS with warnings: PEAD-primary shadow parity")
        return 0
    print("PASS: PEAD-primary shadow parity")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
