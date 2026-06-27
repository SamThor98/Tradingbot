#!/usr/bin/env python3
"""Exit-grace validation sequence (P0 base-signal / exit edge).

Steps (default order):
  1. replay_exit_overlay on control_legacy_aug (fast exit A/B on fixed entries)
  2. compare_exit_grace_smoke --from-replay
  3. phase1_trade_diagnostics
  4. validate_hold_duration_guardrail
  5. optional: phase1_overlay_sweep full universe (control_legacy + control_legacy_exits)
  6. optional: phase2_edge_audit when control chunks have trades

Usage (from schwab_skill/):
  python scripts/run_exit_grace_validation_sequence.py
  python scripts/run_exit_grace_validation_sequence.py --run-full-era --max-workers 4
  python scripts/run_exit_grace_validation_sequence.py --skip-replay --skip-full-era
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SKILL_DIR = Path(__file__).resolve().parent.parent
ARTIFACT_DIR = SKILL_DIR / "validation_artifacts"
SCRIPTS = SKILL_DIR / "scripts"

DEFAULT_REPLAY_RUN_ID = "control_legacy_aug"
REPLAY_PROFILES = [
    "baseline_legacy",
    "control_legacy_defaults",
    "exit_grace_t15_h40",
    "exit_grace_t10_h40",
    "exit_grace_t15_h30",
]
FULL_ERA_CONFIGS = ["control_legacy", "control_legacy_exits"]


def _run_step(name: str, cmd: list[str]) -> dict[str, Any]:
    print(f"[sequence] {name}: {' '.join(cmd)}", flush=True)
    proc = subprocess.run(cmd, cwd=str(SKILL_DIR), capture_output=True, text=True, check=False)
    tail = (proc.stdout or proc.stderr or "").strip()[-800:]
    ok = proc.returncode == 0
    print(f"[sequence] {name}: {'PASS' if ok else 'FAIL'} (rc={proc.returncode})", flush=True)
    if tail:
        print(tail, flush=True)
    return {"step": name, "ok": ok, "rc": proc.returncode, "tail": tail}


def _chunk_trade_count(run_id: str) -> int:
    if str(SKILL_DIR) not in sys.path:
        sys.path.insert(0, str(SKILL_DIR))
    from scripts.phase2_common import load_trades

    try:
        return len(load_trades(run_id))
    except Exception:
        return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Run exit-grace validation sequence")
    parser.add_argument("--replay-run-id", default=DEFAULT_REPLAY_RUN_ID)
    parser.add_argument(
        "--data-provider",
        choices=("chunk", "schwab", "yfinance"),
        default="chunk",
        help="Bar source for replay_exit_overlay (chunk is fastest when augmented).",
    )
    parser.add_argument("--skip-replay", action="store_true")
    parser.add_argument("--skip-diagnostics", action="store_true")
    parser.add_argument("--skip-hold-guardrail", action="store_true")
    parser.add_argument("--skip-compare", action="store_true")
    parser.add_argument("--skip-edge-audit", action="store_true")
    parser.add_argument(
        "--run-full-era",
        action="store_true",
        help="Run full-universe multi-era sweep for control_legacy + control_legacy_exits.",
    )
    parser.add_argument("--max-workers", type=int, default=4)
    parser.add_argument("--chunk-size", type=int, default=120)
    parser.add_argument(
        "--full-era-no-resume",
        action="store_true",
        help="Pass --no-resume to multi-era (wipes only those config chunk dirs).",
    )
    args = parser.parse_args()
    py = sys.executable
    steps: list[dict[str, Any]] = []

    if not args.skip_replay:
        steps.append(
            _run_step(
                "replay_exit_overlay",
                [
                    py,
                    str(SCRIPTS / "replay_exit_overlay.py"),
                    "--run-id",
                    args.replay_run_id,
                    "--data-provider",
                    args.data_provider,
                    "--profiles",
                    *REPLAY_PROFILES,
                ],
            )
        )

    if not args.skip_compare:
        steps.append(
            _run_step(
                "compare_exit_grace_smoke",
                [
                    py,
                    str(SCRIPTS / "compare_exit_grace_smoke.py"),
                    "--from-replay",
                    args.replay_run_id,
                ],
            )
        )

    if not args.skip_diagnostics:
        steps.append(
            _run_step(
                "phase1_trade_diagnostics",
                [
                    py,
                    str(SCRIPTS / "phase1_trade_diagnostics.py"),
                    "--run-id",
                    args.replay_run_id,
                ],
            )
        )

    if not args.skip_hold_guardrail:
        steps.append(
            _run_step(
                "validate_hold_duration_guardrail",
                [
                    py,
                    str(SCRIPTS / "validate_hold_duration_guardrail.py"),
                    "--run-id",
                    args.replay_run_id,
                ],
            )
        )

    if args.run_full_era:
        cmd = [
            py,
            str(SCRIPTS / "phase1_overlay_sweep.py"),
            "--only",
            *FULL_ERA_CONFIGS,
            "--max-workers",
            str(args.max_workers),
            "--chunk-size",
            str(args.chunk_size),
            "--progress-path",
            "phase1_progress_exit_grace_full.json",
            "--no-skip-completed",
        ]
        if args.full_era_no_resume:
            cmd.append("--no-resume")
        steps.append(_run_step("phase1_overlay_sweep_full_era", cmd))

    if not args.skip_edge_audit:
        control_n = _chunk_trade_count("control_legacy")
        bare_n = _chunk_trade_count("stage2_only")
        if control_n >= 50 and bare_n >= 50:
            steps.append(
                _run_step(
                    "phase2_edge_audit",
                    [
                        py,
                        str(SCRIPTS / "phase2_edge_audit.py"),
                        "--control-run-id",
                        "control_legacy",
                        "--bare-run-id",
                        "stage2_only",
                    ],
                )
            )
        else:
            msg = (
                f"skip phase2_edge_audit: control_legacy trades={control_n}, "
                f"stage2_only trades={bare_n} (need >=50 each; run --run-full-era first)"
            )
            print(f"[sequence] {msg}", flush=True)
            steps.append({"step": "phase2_edge_audit", "ok": None, "skipped": True, "reason": msg})

    passed = sum(1 for s in steps if s.get("ok") is True)
    failed = sum(1 for s in steps if s.get("ok") is False)
    skipped = sum(1 for s in steps if s.get("skipped"))
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "replay_run_id": args.replay_run_id,
        "passed": passed,
        "failed": failed,
        "skipped": skipped,
        "steps": steps,
    }
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    out = ARTIFACT_DIR / "exit_grace_validation_sequence.json"
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({"passed": passed, "failed": failed, "skipped": skipped, "report": str(out)}, indent=2))
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
