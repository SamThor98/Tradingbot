#!/usr/bin/env python3
"""Compare live-scan MiroFish scoring to BACKTEST_SKIP_MIROFISH books (P0).

Reads the latest ``last_scan`` (SQLite or JSON) and reports whether live
``pts_mirofish`` / LLM conviction can be compared to offline PF. Rank-v2 still
blends mirofish at weight 0.15 even when backtests skip the LLM.

Exit codes:
  0 — pass, warn, skip (no live scan unless --require-live-scan)
  1 — fail or missing live scan when required
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL_DIR))

from core.mirofish_live_compare import (  # noqa: E402
    ARTIFACT_NAME,
    build_mirofish_live_compare_report,
    default_backtest_skip_mirofish,
    load_last_scan,
    write_mirofish_live_compare_report,
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
        "--require-live-scan",
        action="store_true",
        help="Fail when no live scan is available (default: skip with exit 0)",
    )
    parser.add_argument(
        "--write-artifact",
        action="store_true",
        help=f"Write validation_artifacts/{ARTIFACT_NAME}",
    )
    args = parser.parse_args()

    diag_json = Path(args.diagnostics_json) if args.diagnostics_json else None
    sqlite_path = Path(args.sqlite) if args.sqlite else None
    last_scan, live_meta = load_last_scan(
        sqlite_path=sqlite_path,
        diagnostics_json=diag_json,
    )
    skip_mirofish = default_backtest_skip_mirofish()

    if last_scan is None:
        msg = live_meta.get("error") or "no live scan"
        if args.require_live_scan:
            print(f"FAIL: {msg}")
            return 1
        print(f"SKIP: {msg}")
        if args.write_artifact:
            report = build_mirofish_live_compare_report(
                None,
                backtest_skip_mirofish=skip_mirofish,
            )
            report["live_meta"] = live_meta
            path = write_mirofish_live_compare_report(report, SKILL_DIR)
            print(f"Wrote {path}")
        return 0

    report = build_mirofish_live_compare_report(
        last_scan,
        backtest_skip_mirofish=skip_mirofish,
    )
    report["live_meta"] = live_meta

    if args.write_artifact:
        path = write_mirofish_live_compare_report(report, SKILL_DIR)
        print(f"Wrote {path}")

    verdict = str(report.get("verdict") or "fail")
    print(
        f"verdict={verdict} scored={report.get('live_mirofish_scored')} "
        f"missing={report.get('live_mirofish_missing')} "
        f"skip_mirofish={report.get('backtest_skip_mirofish')} "
        f"gap={report.get('fidelity_gap')}"
    )
    llm_model = report.get("llm_model")
    llm_provider = report.get("llm_provider")
    if llm_model or llm_provider:
        print(f"llm={llm_provider or '?'}/{llm_model or '?'}")
    action = report.get("recommended_action")
    if action:
        print(action)

    if verdict == "fail":
        print("FAIL: live vs backtest MiroFish compare failed")
        return 1
    if verdict == "skip":
        print("SKIP: no live scan signals to compare")
        return 0
    if verdict == "warn":
        print("WARN: live vs backtest MiroFish fidelity gap")
        return 0
    print("PASS: live vs backtest MiroFish compare")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
