#!/usr/bin/env python3
"""Validate offline entry-timing shadow evidence artifacts (P0)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
ART = SKILL_DIR / "validation_artifacts"
DEFAULT_RUN_ID = "control_legacy_aug"


def _load(name: str) -> dict | None:
    path = ART / name
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def main() -> int:
    run_id = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_RUN_ID
    errors: list[str] = []

    entry = _load(f"entry_timing_shadow_counterfactual_{run_id}.json")
    early = _load(f"early_stopout_cohorts_{run_id}.json")
    stack = _load(f"signal_stack_counterfactual_{run_id}.json")
    cache_path = ART / f"entry_timing_replay_cache_{run_id}.json"

    if entry is None:
        errors.append(f"missing entry_timing_shadow_counterfactual_{run_id}.json")
    if early is None:
        errors.append(f"missing early_stopout_cohorts_{run_id}.json")
    if not cache_path.exists():
        errors.append(f"missing entry_timing_replay_cache_{run_id}.json")

    if entry:
        rec = entry.get("recommendation") or {}
        action = rec.get("action")
        if action not in {
            "experiment_breakout_buffer_only",
            "keep_entry_timing_shadow_only",
            "revise_shadow_thresholds",
            "fix_entry_timing_not_rank_filter",
            "tune_shadow_thresholds",
        }:
            errors.append(f"unexpected recommendation action: {action}")
        replay = entry.get("live_shadow_replay") or {}
        replayed = int(replay.get("replayed_trades") or 0)
        if replayed < 500:
            errors.append(f"replay sample too small: {replayed} (<500)")
        sweep = entry.get("breakout_buffer_only_sweep") or []
        exp = next((r for r in sweep if r.get("min_breakout_buffer_pct") == 0.01), None)
        stack_scenarios = stack.get("scenarios") if stack and isinstance(stack.get("scenarios"), dict) else {}
        combined = stack_scenarios.get("exit_grace_breakout_buffer_0.010") or {}
        combined_retention = float(combined.get("retention_pct") or 0.0)
        combined_stack_ready = (
            bool(combined.get("passes_promotion_gates"))
            and int(combined.get("n_eras") or 0) == 5
            and 40.0 <= combined_retention <= 60.0
        )
        if exp is None:
            errors.append("breakout_buffer_only sweep missing 0.01 row")
        elif not (
            (exp.get("retention_pct") or 0) >= 50
            and (exp.get("delta_early_stopout_pp") or 0) <= -3
            and (exp.get("delta_overlap_pf_mean") or 0) >= 0.05
        ) and not combined_stack_ready:
            errors.append("breakout_buffer 0.01 row and combined exit-grace stack do not meet promotion shape")

    if early:
        baseline = early.get("baseline") or {}
        # control_legacy_aug measured ~21.91% early stops; floor is a sanity
        # check that the cohort artifact still shows a material early-stop drag.
        if (baseline.get("early_stopout_pct") or 0) < 18:
            errors.append("early_stopout baseline unexpectedly low")

    if errors:
        print("entry timing shadow evidence validation failed:")
        for err in errors:
            print(f"- {err}")
        return 1

    print("entry timing shadow evidence validation passed")
    if entry:
        rec = entry.get("recommendation") or {}
        print(f"- recommendation: {rec.get('action')}")
        print(f"- replay trades: {(entry.get('live_shadow_replay') or {}).get('replayed_trades')}")
    if stack:
        combined = ((stack.get("scenarios") or {}).get("exit_grace_breakout_buffer_0.010") or {})
        print(
            f"- combined stack: pf_mean={combined.get('pf_mean')} "
            f"worst={combined.get('worst_era_pf')} retention={combined.get('retention_pct')}%"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
