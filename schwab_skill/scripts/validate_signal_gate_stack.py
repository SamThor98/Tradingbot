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
RANK_V2_PF_MEAN_MIN = 1.23
RANK_V2_WORST_ERA_MIN = 1.10
RANK_V2_RECENT_CURRENT_MIN = 1.15
RANK_V2_RETENTION_MIN = 25.0
RANK_V2_RETENTION_MAX = 35.0


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
    rank_v2_key = next(
        (key for key in scenarios if key.startswith("exit_grace_breakout_buffer_rank_v2_p")),
        None,
    )
    rank_v2_stack = (scenarios.get(rank_v2_key) if rank_v2_key else None) or {}
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
    if rank_v2_stack:
        rank_pct = rank_v2_stack.get("rank_v2_min_percentile") or "?"
        rank_pf = float(rank_v2_stack.get("pf_mean") or 0.0)
        rank_worst = float(rank_v2_stack.get("worst_era_pf") or 0.0)
        rank_retention = float(rank_v2_stack.get("retention_pct") or 0.0)
        per_era = rank_v2_stack.get("per_era_pf") if isinstance(rank_v2_stack.get("per_era_pf"), dict) else {}
        rank_recent = float(per_era.get("recent_current") or 0.0)
        if rank_pf < RANK_V2_PF_MEAN_MIN:
            errors.append(f"rank-v2 p{rank_pct} pf_mean {rank_pf:.4f} < {RANK_V2_PF_MEAN_MIN}")
        if rank_worst < RANK_V2_WORST_ERA_MIN:
            errors.append(f"rank-v2 p{rank_pct} worst_era_pf {rank_worst:.4f} < {RANK_V2_WORST_ERA_MIN}")
        if rank_recent < RANK_V2_RECENT_CURRENT_MIN:
            errors.append(
                f"rank-v2 p{rank_pct} recent_current PF {rank_recent:.4f} < {RANK_V2_RECENT_CURRENT_MIN}"
            )
        if not RANK_V2_RETENTION_MIN <= rank_retention <= RANK_V2_RETENTION_MAX:
            errors.append(
                f"rank-v2 p{rank_pct} retention {rank_retention:.1f}% outside "
                f"{RANK_V2_RETENTION_MIN:.0f}-{RANK_V2_RETENTION_MAX:.0f}%"
            )

    if errors:
        print("signal gate stack validation failed:")
        for err in errors:
            print(f"- {err}")
        print(f"recommendation: {rec.get('action')}")
        return 1

    print("signal gate stack validation passed")
    print(f"- stack pf_mean={pf_mean:.4f} worst_era_pf={worst:.4f} retention={stack.get('retention_pct')}%")
    print(f"- exit grace only pf_mean={grace.get('pf_mean')} worst={grace.get('worst_era_pf')}")
    if rank_v2_stack:
        print(
            f"- rank-v2 p{rank_v2_stack.get('rank_v2_min_percentile')} "
            f"pf_mean={rank_v2_stack.get('pf_mean')} "
            f"worst={rank_v2_stack.get('worst_era_pf')} "
            f"retention={rank_v2_stack.get('retention_pct')}% mode=shadow"
        )
    print(f"- data_provider: {report.get('data_provider')}")
    print(f"- recommendation: {rec.get('action')}")
    print(
        "NOTE: offline stack clears promotion gates; re-run phase2_edge_audit after live week "
        "before plugin promotion."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
