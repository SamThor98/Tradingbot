#!/usr/bin/env python3
"""Seed Stage 2b evidence log with known aligned pass scans (idempotent)."""

from __future__ import annotations

import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL_DIR))

DEFAULT_RUN_ID = "control_legacy_aug"

KNOWN_PASS_SCANS = [
    {
        "scan_at": "2026-06-28T15:20:22.954184+00:00",
        "recorded_at": "2026-06-28T15:20:22.954184+00:00",
        "source": "run_entry_timing_experiment_scan",
        "watchlist_size": 300,
        "signals_found": 1,
        "stage_a_candidates": 35,
        "entry_shadow_would_filter_any": 17,
        "would_filter_pct": 48.57142857142857,
        "rate_source": "stage_a_candidates",
        "entry_shadow_stage2_evaluated": 97,
        "entry_shadow_stage2_would_filter_any": 79,
        "would_filter_pct_stage2": 81.44329896907216,
        "entry_timing_shadow_profile": "breakout_buffer_only_0.010",
        "entry_timing_shadow_mode": "shadow",
        "verdict": "pass",
        "delta_would_filter_pp": -1.328571428571429,
    },
    {
        "scan_at": "2026-06-28T15:37:11.808151+00:00",
        "recorded_at": "2026-06-28T15:37:11.808151+00:00",
        "source": "run_entry_timing_experiment_scan",
        "watchlist_size": 1503,
        "signals_found": 1,
        "stage_a_candidates": 88,
        "entry_shadow_would_filter_any": 41,
        "would_filter_pct": 46.590909090909086,
        "rate_source": "stage_a_candidates",
        "entry_shadow_stage2_evaluated": 269,
        "entry_shadow_stage2_would_filter_any": 221,
        "would_filter_pct_stage2": 82.15613382899627,
        "entry_timing_shadow_profile": "breakout_buffer_only_0.010",
        "entry_timing_shadow_mode": "shadow",
        "verdict": "pass",
        "delta_would_filter_pp": -3.309090909090914,
    },
]


def main() -> int:
    from datetime import datetime, timezone

    from core.entry_timing_live_compare import (
        assess_stage2b_readiness,
        entry_timing_evidence_log_path,
        load_entry_timing_evidence_log,
    )

    run_id = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_RUN_ID
    log = load_entry_timing_evidence_log(SKILL_DIR, run_id)
    by_scan = {
        str(row.get("scan_at")): row for row in log.get("records") or [] if isinstance(row, dict) and row.get("scan_at")
    }
    added = 0
    updated = 0
    for row in KNOWN_PASS_SCANS:
        key = row["scan_at"]
        if key not in by_scan:
            by_scan[key] = dict(row)
            added += 1
            continue
        existing = by_scan[key]
        for field, value in row.items():
            if field in {"watchlist_size", "source"} and not existing.get(field):
                existing[field] = value
                updated += 1
            elif field == "watchlist_size" and existing.get(field) in {0, None} and value:
                existing[field] = value
                updated += 1
    records = sorted(by_scan.values(), key=lambda row: str(row.get("scan_at") or ""))
    log["run_id"] = run_id
    log["updated_at"] = datetime.now(timezone.utc).isoformat()
    log["records"] = records
    log["stage2b"] = assess_stage2b_readiness(records)

    path = entry_timing_evidence_log_path(SKILL_DIR, run_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(__import__("json").dumps(log, indent=2), encoding="utf-8")

    print(f"Seeded {added} record(s), updated {updated} field(s); total={len(records)}")
    print(f"Stage 2b ready: {log['stage2b'].get('ready')}")
    print(f"Wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
