#!/usr/bin/env python3
"""Validate combined signal stack counterfactual artifact (P0 promotion gates)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
DEFAULT_RUN_ID = "control_legacy_aug"
PROMOTION_PF_MEAN = 1.20
PROMOTION_WORST_ERA_PF = 1.00


def _gate_failures(row: dict, label: str) -> list[str]:
    errors: list[str] = []
    pf_mean = float(row.get("pf_mean") or 0.0)
    worst = float(row.get("worst_era_pf") or 0.0)
    if pf_mean < PROMOTION_PF_MEAN:
        errors.append(f"{label} pf_mean {pf_mean:.4f} < {PROMOTION_PF_MEAN}")
    if worst < PROMOTION_WORST_ERA_PF:
        errors.append(f"{label} worst_era_pf {worst:.4f} < {PROMOTION_WORST_ERA_PF}")
    return errors


def main() -> int:
    run_id = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_RUN_ID
    path = SKILL_DIR / "validation_artifacts" / f"signal_stack_counterfactual_{run_id}.json"
    if not path.exists():
        print(f"FAIL: missing {path.name} — run analyze_signal_stack_counterfactual.py")
        return 1
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        print(f"FAIL: invalid JSON in {path.name}")
        return 1

    scenarios = report.get("scenarios") if isinstance(report.get("scenarios"), dict) else {}
    stack = scenarios.get("exit_grace_breakout_buffer_0.010") or {}
    grace = scenarios.get("exit_grace_all") or {}
    rec = report.get("recommendation") or {}
    errors: list[str] = []
    notes: list[str] = []

    if int(report.get("merged_trades") or 0) < 500:
        errors.append(f"merged_trades too small: {report.get('merged_trades')} (<500)")

    if not stack:
        errors.append("missing scenario exit_grace_breakout_buffer_0.010")
    else:
        errors.extend(_gate_failures(stack, "stack"))

    grace_failures = _gate_failures(grace, "exit_grace_all") if grace else ["missing scenario exit_grace_all"]
    if grace_failures:
        notes.extend(grace_failures)

    if errors:
        print("signal stack promotion validation failed:")
        for err in errors:
            print(f"- {err}")
        print(f"recommendation: {rec.get('action')}")
        return 1

    print("signal stack promotion validation passed")
    print(f"- stack pf_mean={stack.get('pf_mean')} worst={stack.get('worst_era_pf')} retention={stack.get('retention_pct')}%")
    if grace:
        print(f"- exit grace only pf_mean={grace.get('pf_mean')} worst={grace.get('worst_era_pf')}")
    for note in notes:
        print(f"NOTE: {note}")
    print(f"- recommendation: {rec.get('action')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
