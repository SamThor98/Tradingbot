#!/usr/bin/env python3
"""Validate a prob-rank promotion decision artifact against hard floors + schema."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

from research.promotion import PF_MEAN_FLOOR, WORST_ERA_PF_FLOOR, evaluate_prob_rank_promotion  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--decision",
        type=str,
        default=str(SKILL_DIR / "validation_artifacts" / "prob_rank_promotion_decision.json"),
    )
    parser.add_argument("--strict", action="store_true", help="Fail unless decision promotes")
    args = parser.parse_args(argv)

    path = Path(args.decision)
    if not path.is_file():
        print(f"FAIL: missing decision artifact {path}")
        return 1
    payload = json.loads(path.read_text(encoding="utf-8"))
    required = {"decision", "floors_cleared", "gates", "rationale"}
    if not required.issubset(payload):
        print(f"FAIL: decision missing keys {required - set(payload)}")
        return 1

    metrics = payload.get("metrics") or {}
    # Re-evaluate for consistency
    verdict = evaluate_prob_rank_promotion(
        metrics,
        requested=str((payload.get("gates") or {}).get("requested") or "shadow"),
    )
    if verdict.decision != payload.get("decision"):
        print(
            f"FAIL: stored decision {payload.get('decision')} != recomputed {verdict.decision}"
        )
        return 1

    gates = payload.get("gates") or {}
    if gates.get("pf_mean_floor") != PF_MEAN_FLOOR or gates.get("worst_era_pf_floor") != WORST_ERA_PF_FLOOR:
        print("FAIL: floor constants drift in gates block")
        return 1

    print(
        f"PASS: prob-rank promotion decision valid "
        f"(decision={payload.get('decision')} floors={payload.get('floors_cleared')} "
        f"composite={payload.get('composite_score')})"
    )
    if args.strict and not str(payload.get("decision", "")).startswith("promote"):
        print("FAIL: --strict requires promote_* decision")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
