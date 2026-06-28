#!/usr/bin/env python3
"""One-shot P0 entry-timing experiment status (env, last scan, offline compare)."""

from __future__ import annotations

import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL_DIR))


def main() -> int:
    from config import get_entry_timing_experiment_readiness
    from core.entry_timing_live_compare import (
        assess_stage2b_readiness,
        build_live_entry_shadow_compare_report,
        load_entry_timing_evidence_log,
        load_last_scan_diagnostics,
        load_validation_artifact,
    )
    from core.env_local import entry_timing_experiment_file_readiness

    run_id = sys.argv[1] if len(sys.argv) > 1 else "control_legacy_aug"
    process = get_entry_timing_experiment_readiness(SKILL_DIR)
    file_env = entry_timing_experiment_file_readiness(SKILL_DIR / ".env")
    from config import get_entry_timing_breakout_buffer_readiness, get_entry_timing_shadow_mode

    buffer_ready = get_entry_timing_breakout_buffer_readiness(SKILL_DIR)
    mode = get_entry_timing_shadow_mode(SKILL_DIR)

    print("=== Entry-timing experiment status ===")
    print(f"Process mode: {mode} profile={buffer_ready.get('profile')}")
    print(f"Process env ready (shadow experiment): {process.get('ready')} profile={process.get('profile')}")
    print(f".env file ready (shadow experiment):   {file_env.get('ready')} profile={file_env.get('profile')}")
    if file_env.get("ready") and not process.get("ready") and mode != "live":
        print("ACTION: restart dashboard to load .env experiment vars")
    if mode == "live":
        print("NOTE: entry timing LIVE enforcement active in this process")

    entry_art = load_validation_artifact(SKILL_DIR, f"entry_timing_shadow_counterfactual_{run_id}.json")
    if entry_art:
        rec = entry_art.get("recommendation") or {}
        print(f"Offline recommendation: {rec.get('action')}")
    else:
        print("Offline recommendation: missing artifact")

    diagnostics, meta = load_last_scan_diagnostics(sqlite_path=SKILL_DIR / "webapp" / "webapp.db")
    if diagnostics is None:
        print(f"Last scan: unavailable ({meta.get('error')})")
        print("ACTION: python scripts/run_entry_timing_experiment_scan.py --smoke")
        return 1 if not process.get("ready") else 0

    report = build_live_entry_shadow_compare_report(
        diagnostics,
        skill_dir=SKILL_DIR,
        run_id=run_id,
        live_meta=meta,
        experiment_env_ready=bool(buffer_ready.get("ready")),
    )
    if report is None:
        print("Compare: could not build report")
        return 1

    live = report.get("live") or {}
    comparison = report.get("comparison") or {}
    print(f"Last scan source: {meta.get('source')} at={meta.get('scan_at') or live.get('scan_at')}")
    if mode == "live":
        blocked = int(live.get("entry_timing_blocked") or diagnostics.get("entry_timing_blocked") or 0)
        kept = int(live.get("stage_a_candidates") or 0)
        print(f"Live enforcement: blocked={blocked} kept={kept} rate={live.get('would_filter_pct')}%")
    else:
        print(
            f"Stage A shadow: {live.get('entry_shadow_would_filter_any')}/{live.get('stage_a_candidates')} "
            f"({live.get('would_filter_pct_stage_a')})"
        )
    print(
        f"Stage 2: {live.get('entry_shadow_stage2_would_filter_any')}/"
        f"{live.get('entry_shadow_stage2_evaluated')} ({live.get('would_filter_pct_stage2')})"
    )
    print(
        f"Compare rate: {live.get('would_filter_pct')} source={live.get('rate_source')} "
        f"verdict={comparison.get('verdict')}"
    )
    for warn in comparison.get("warnings") or []:
        print(f"WARN: {warn}")
    for err in comparison.get("errors") or []:
        print(f"FAIL: {err}")

    evidence_log = load_entry_timing_evidence_log(SKILL_DIR, run_id)
    stage2b = evidence_log.get("stage2b")
    if not isinstance(stage2b, dict):
        stage2b = assess_stage2b_readiness(evidence_log.get("records") or [])
    print(
        f"Stage 2b readiness: {stage2b.get('pass_scans')}/{stage2b.get('required_pass_scans')} pass scans "
        f"-> {'READY' if stage2b.get('ready') else 'NOT READY'}"
    )
    for msg in stage2b.get("messages") or []:
        print(f"NOTE: {msg}")

    stack_path = SKILL_DIR / "validation_artifacts" / f"signal_stack_counterfactual_{run_id}.json"
    stack_art: dict | None = None
    if stack_path.is_file():
        try:
            import json as _json

            payload = _json.loads(stack_path.read_text(encoding="utf-8"))
            stack_art = payload if isinstance(payload, dict) else None
        except Exception:
            stack_art = None
    if stack_art:
        scenarios = stack_art.get("scenarios") if isinstance(stack_art.get("scenarios"), dict) else {}
        stack_row = scenarios.get("exit_grace_breakout_buffer_0.010") or {}
        stack_rec = stack_art.get("recommendation") if isinstance(stack_art.get("recommendation"), dict) else {}
        print(
            f"Stack offline: pf_mean={stack_row.get('pf_mean')} worst={stack_row.get('worst_era_pf')} "
            f"retention={stack_row.get('retention_pct')}% "
            f"gates={'PASS' if stack_row.get('passes_promotion_gates') else 'FAIL'}"
        )
        print(f"Stack recommendation: {stack_rec.get('action')}")

    from core.entry_timing_live_compare import assess_entry_timing_live_promotion_readiness

    live_ready = assess_entry_timing_live_promotion_readiness(SKILL_DIR, run_id=run_id)
    print(
        f"Live promotion readiness: {'READY' if live_ready.get('ready') else 'NOT READY'} "
        f"(mode={live_ready.get('mode')})"
    )
    for msg in live_ready.get("messages") or []:
        print(f"NOTE: {msg}")
    for err in live_ready.get("errors") or []:
        print(f"BLOCK: {err}")

    verdict = comparison.get("verdict")
    if mode == "live" and verdict == "pass":
        return 0
    if verdict == "pass":
        return 0
    if verdict in {"skip", "stale_scan"} and process.get("ready"):
        print("ACTION: run a fresh scan with experiment env loaded")
        return 0
    if verdict == "fail" and int(live.get("entry_shadow_stage2_evaluated") or 0) < 50:
        print("ACTION: run larger scan: python scripts/run_entry_timing_experiment_scan.py --max-tickers 500")
        return 0
    return 1 if verdict == "fail" else 0


if __name__ == "__main__":
    raise SystemExit(main())
