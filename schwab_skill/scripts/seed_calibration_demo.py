#!/usr/bin/env python3
"""Seed local demo calibration files for dashboard QA (System → Calibration panel)."""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent


def _demo_self_study() -> dict:
    now = datetime.now(timezone.utc).isoformat()
    return {
        "last_run": now,
        "updated_at": now,
        "suggested_min_conviction": 40,
        "round_trips_count": 12,
        "win_rate": 58.3,
        "avg_return_pct": 2.41,
        "min_round_trips_met": True,
        "hypothesis_calibration": {
            "by_source": {
                "advisory": {
                    "scored_samples": 14,
                    "hit_rate": 0.62,
                    "mean_return_pct": 1.85,
                },
                "signal_scanner": {
                    "scored_samples": 22,
                    "hit_rate": 0.48,
                    "mean_return_pct": -0.32,
                },
            },
            "ledger_records": 36,
        },
    }


def _demo_ledger() -> list[dict]:
    rows: list[dict] = []
    for i in range(14):
        rows.append({"source": "advisory", "ticker": f"ADV{i:02d}"})
    for i in range(22):
        rows.append({"source": "signal_scanner", "ticker": f"SCN{i:02d}"})
    for i in range(3):
        rows.append({"source": "unknown", "ticker": f"UNK{i}"})
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--skill-dir",
        type=Path,
        default=SKILL_DIR,
        help="Skill directory (default: schwab_skill/)",
    )
    args = parser.parse_args()
    skill_dir = args.skill_dir.resolve()
    (skill_dir / ".self_study.json").write_text(
        json.dumps(_demo_self_study(), indent=2),
        encoding="utf-8",
    )
    (skill_dir / ".hypothesis_ledger.json").write_text(
        json.dumps(_demo_ledger(), indent=2),
        encoding="utf-8",
    )
    port = os.environ.get("LOCAL_WEB_PORT", "8182")
    print(f"Seeded calibration demo data in {skill_dir}")
    print(f"Open: https://127.0.0.1:{port}/?screen=diagnostics&section=calibration")


if __name__ == "__main__":
    main()
