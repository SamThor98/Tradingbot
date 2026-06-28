#!/usr/bin/env python3
"""Validate offline signal-gate stack clears PF promotion thresholds (P0)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
DEFAULT_RUN_ID = "control_legacy_aug"
PF_MEAN_MIN = 1.20
WORST_ERA_MIN = 1.00


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
    rec = report.get("recommendation") if isinstance(report.get("recommendation"), dict) else {}

    pf_mean = float(stack.get("pf_mean") or 0.0)
    worst = float(stack.get("worst_era_pf") or 0.0)
    errors: list[str] = []
    if pf_mean < PF_MEAN_MIN:
        errors.append(f"stack pf_mean {pf_mean:.4f} < {PF_MEAN_MIN}")
    if worst < WORST_ERA_MIN:
        errors.append(f"stack worst_era_pf {worst:.4f} < {WORST_ERA_MIN}")
    if not stack.get("passes_promotion_gates"):
        errors.append("stack passes_promotion_gates is false")

    if errors:
        print("signal gate stack validation failed:")
        for err in errors:
            print(f"- {err}")
        print(f"recommendation: {rec.get('action')}")
        return 1

    print("signal gate stack validation passed")
    print(f"- stack pf_mean={pf_mean:.4f} worst_era_pf={worst:.4f} retention={stack.get('retention_pct')}%")
    print(f"- exit grace only pf_mean={grace.get('pf_mean')} worst={grace.get('worst_era_pf')}")
    print(f"- data_provider: {report.get('data_provider')}")
    print(f"- recommendation: {rec.get('action')}")
    print(
        "NOTE: offline stack clears promotion gates; re-run phase2_edge_audit after live week "
        "before plugin promotion."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
