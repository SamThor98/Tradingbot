#!/usr/bin/env python3
"""Autopsy scored S0 names at T+5. Writes a report and does not change live flags.

Usage (from schwab_skill/):
  python scripts/analyze_s0_t5_autopsy.py
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SKILL_DIR = Path(__file__).resolve().parent.parent
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

from hypothesis_ledger import load_ledger_data  # noqa: E402
from research.s0_t5_autopsy import autopsy_report  # noqa: E402

OUT_JSON = SKILL_DIR / "validation_artifacts" / "s0_t5_autopsy.json"
OUT_MD = SKILL_DIR / "validation_artifacts" / "s0_t5_autopsy.md"


def _arm_line(label: str, arm: dict[str, Any]) -> str:
    hit = arm.get("hit_rate")
    mean = arm.get("mean_return_pct")
    hit_s = "n/a" if hit is None else f"{float(hit):.1%}"
    mean_s = "n/a" if mean is None else f"{float(mean):+.2f}%"
    return f"{label}: n={arm.get('n')} hit {hit_s} mean {mean_s}"


def _markdown(report: dict[str, Any]) -> str:
    registered = report["pre_registered"]
    lines = [
        "# S0 T+5 autopsy",
        "",
        f"Computed {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}.",
        "Read-only. No live flag, sort key, or exit rule was changed.",
        "",
        _arm_line("All S0", report["all"]),
        "",
        f"Pre-registered flag `{registered['name']}` ({registered['rule']}).",
        _arm_line("Flagged", registered["flagged"]),
        _arm_line("Other", registered["other"]),
        f"Other minus flagged mean: {registered['other_minus_flagged_mean']}",
        f"Decision: **{report['decision']}**.",
        "A shadow counter needs both sides n>=8 and the flagged mean at least 2 points worse.",
        "",
        "## Days",
        "",
    ]
    for day, arm in (report.get("by_day") or {}).items():
        lines.append(f"- {_arm_line(day or 'undated', arm)}")
    lines.extend(
        [
            "",
            "Exploratory cuts are descriptive. They do not authorize a counter.",
            "",
        ]
    )
    for name, split in (report.get("exploratory") or {}).items():
        lines.append(f"### {name}")
        lines.append(_arm_line("Flagged", split["flagged"]))
        lines.append(_arm_line("Other", split["other"]))
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    data = load_ledger_data(SKILL_DIR)
    records = [row for row in (data.get("records") or []) if isinstance(row, dict)]
    report = autopsy_report(records)
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        **{key: value for key, value in report.items() if key != "rows"},
        "n_rows": len(report["rows"]),
    }
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    OUT_MD.write_text(_markdown(report), encoding="utf-8")
    print(f"decision={report['decision']} n={report['n']}")
    print(f"wrote {OUT_JSON.name} and {OUT_MD.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
