#!/usr/bin/env python3
"""Cohort attribution for management integrity on decision packets.

Loads historical/live packets from ``decision_packets.json`` (or a custom path),
runs era-split lift analysis (≤20d vs 21–40d), and prints a pilot recommendation.

Usage:
    python scripts/analyze_packet_cohorts.py
    python scripts/analyze_packet_cohorts.py --packets-path decision_packets.json --json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

from core import decision_packet, packet_feature_analysis  # noqa: E402

# Report text contains non-cp1252 characters (e.g. "≤"); Windows consoles
# default to cp1252 and would crash on print without this.
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout.reconfigure(errors="replace")
    sys.stderr.reconfigure(errors="replace")


def _print_cohorts(title: str, block: dict) -> None:
    print(f"\n=== {title} ===")
    for feature in ("management_integrity",):
        feat = block.get(feature) or {}
        cohorts = feat.get("cohorts") or {}
        if not cohorts:
            print(f"  {feature}: no resolved cohorts")
            continue
        print(f"  {feature}:")
        for bucket, stats in cohorts.items():
            print(
                f"    {bucket}: n={stats.get('resolved')} "
                f"win_rate={stats.get('win_rate')} avg_return_pct={stats.get('avg_return_pct')}"
            )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Packet cohort attribution for shadow features.")
    parser.add_argument(
        "--packets-path",
        type=Path,
        default=None,
        help="Override path to decision_packets.json (default: skill_dir/decision_packets.json).",
    )
    parser.add_argument("--short-max-days", type=int, default=20)
    parser.add_argument("--long-min-days", type=int, default=21)
    parser.add_argument("--long-max-days", type=int, default=40)
    parser.add_argument("--json", action="store_true", help="Emit full JSON report on stdout.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    skill_dir = SKILL_DIR
    if args.packets_path:
        # Load directly when a custom store path is supplied.
        path = args.packets_path
        if not path.exists():
            print(f"Packets file not found: {path}", file=sys.stderr)
            return 1
        data = json.loads(path.read_text(encoding="utf-8"))
        packets = [p for p in data.get("packets", []) if isinstance(p, dict)]
    else:
        packets = decision_packet.load_packets(skill_dir)

    report = packet_feature_analysis.feature_lift_report(
        packets,
        short_max_days=args.short_max_days,
        long_min_days=args.long_min_days,
        long_max_days=args.long_max_days,
    )

    if args.json:
        print(json.dumps(report, indent=2))
        return 0

    print(
        f"Packets: {report.get('total_packets')} total, "
        f"{report.get('resolved_packets')} resolved "
        f"({report.get('coverage_pct')}%)"
    )
    print(
        f"Feature coverage: management_integrity={report.get('management_integrity_packets')}"
    )

    for era, block in (report.get("era_splits") or {}).items():
        _print_cohorts(f"Era {era}", block)

    _print_cohorts("All resolved", report.get("all_resolved") or {})

    pilot = report.get("pilot_recommendation") or {}
    print("\n=== Pilot recommendation ===")
    print(f"  ready: {pilot.get('ready_for_single_era_pilot')}")
    print(f"  feature: {pilot.get('recommended_feature')}")
    print(f"  note: {pilot.get('note')}")
    for cand in pilot.get("candidates") or []:
        print(
            f"  - {cand.get('feature')}: "
            f"short_lift={cand.get('short_era_lift_pct')} "
            f"long_lift={cand.get('long_era_lift_pct')}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
