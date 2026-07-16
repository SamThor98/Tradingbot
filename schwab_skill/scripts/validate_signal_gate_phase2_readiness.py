#!/usr/bin/env python3
"""Synthesize bare-signal phase2 audit vs offline stack gate readiness."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
ART = SKILL_DIR / "validation_artifacts"
DEFAULT_RUN_ID = "control_legacy_aug"
PF_MEAN_MIN = 1.20
WORST_ERA_MIN = 1.00
BARE_OK_VERDICTS = {"proceed", "iterate", "iterate_with_caution"}
BARE_HALT_VERDICTS = {"halt", "halt_fix_signal_first", "halt_insufficient_data"}


def _load(name: str) -> dict | None:
    path = ART / name
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def _normalize_verdict(value: object) -> str:
    return str(value or "").strip().lower()


def main() -> int:
    run_id = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_RUN_ID
    phase2 = _load("phase2_edge_audit.json")
    stack = _load(f"signal_stack_counterfactual_{run_id}.json")

    errors: list[str] = []
    notes: list[str] = []

    bare_verdict = (phase2 or {}).get("verdict")
    bare_verdict_key = _normalize_verdict(bare_verdict)
    bare_pf_mean = None
    bare_worst = None
    if phase2 and isinstance(phase2.get("bare"), dict):
        bare_pf_mean = phase2["bare"].get("pf_mean")
        bare_worst = phase2["bare"].get("worst_era_pf")
    if phase2 is None:
        notes.append("phase2_edge_audit.json missing — run scripts/phase2_edge_audit.py")
    elif bare_verdict_key not in BARE_OK_VERDICTS:
        notes.append(f"bare signal phase2 verdict={bare_verdict} (stack may still clear offline gates)")

    stack_row: dict = {}
    stack_rec: dict = {}
    if stack is None:
        errors.append(f"missing signal_stack_counterfactual_{run_id}.json")
    else:
        scenarios = stack.get("scenarios") if isinstance(stack.get("scenarios"), dict) else {}
        stack_row = scenarios.get("exit_grace_breakout_buffer_0.010") or {}
        stack_rec = stack.get("recommendation") if isinstance(stack.get("recommendation"), dict) else {}
        pf_mean = float(stack_row.get("pf_mean") or 0.0)
        worst = float(stack_row.get("worst_era_pf") or 0.0)
        if pf_mean < PF_MEAN_MIN:
            errors.append(f"stack pf_mean {pf_mean:.4f} < {PF_MEAN_MIN}")
        if worst < WORST_ERA_MIN:
            errors.append(f"stack worst_era_pf {worst:.4f} < {WORST_ERA_MIN}")

    stack_passes = not errors and bool(stack_row.get("passes_promotion_gates"))
    bare_ok = bare_verdict_key in BARE_OK_VERDICTS
    bare_halt = bare_verdict_key in BARE_HALT_VERDICTS

    if stack_passes and bare_halt:
        recommendation = "stack_offline_clears_gates_bare_signal_halt"
        action = (
            "Offline exit-grace + 1% breakout buffer clears PF gates; bare signal audit still HALT. "
            "Keep entry-timing live, monitor one market week, then re-run phase2 on live-enforced trades."
        )
    elif stack_passes and bare_ok:
        recommendation = "stack_and_bare_aligned"
        action = "Stack and bare signal audits align — eligible for plugin shadow promotion after live week."
    elif stack_passes:
        recommendation = "stack_offline_clears_gates_bare_signal_unknown"
        action = (
            "Stack clears PF gates; bare phase2 verdict unclear. "
            "Monitor live week before plugin promotion."
        )
    else:
        recommendation = "halt_fix_signal_first"
        action = "Stack does not clear promotion gates — do not promote plugins."

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "run_id": run_id,
        "bare_phase2_verdict": bare_verdict,
        "bare_pf_mean": bare_pf_mean,
        "bare_worst_era_pf": bare_worst,
        "stack_pf_mean": stack_row.get("pf_mean"),
        "stack_worst_era_pf": stack_row.get("worst_era_pf"),
        "stack_passes_promotion_gates": stack_passes,
        "stack_recommendation": stack_rec.get("action"),
        "recommendation": recommendation,
        "action": action,
        "notes": notes,
        "errors": errors,
    }
    out = ART / f"signal_gate_phase2_readiness_{run_id}.json"
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")

    if errors:
        print("signal gate phase2 readiness failed:")
        for err in errors:
            print(f"- {err}")
        print(f"recommendation: {recommendation}")
        return 1

    print("signal gate phase2 readiness passed")
    print(f"- bare phase2 verdict: {bare_verdict}")
    print(f"- stack pf_mean={stack_row.get('pf_mean')} worst={stack_row.get('worst_era_pf')}")
    print(f"- recommendation: {recommendation}")
    print(f"- {action}")
    for note in notes:
        print(f"NOTE: {note}")
    print(f"Wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
