#!/usr/bin/env python3
"""Full IC improvement pipeline: audit dataset → tune → validate candidates + trades.

Example:
    python scripts/run_scoring_ic_pipeline.py --full-history --max-tickers 80
    python scripts/run_scoring_ic_pipeline.py --skip-build --csv validation_artifacts/scoring_audit_dataset.csv
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
ARTIFACT_DIR = SKILL_DIR / "validation_artifacts"
PY = sys.executable


def _run(cmd: list[str], *, step: str) -> int:
    print(f"\n=== {step} ===")
    print(" ".join(cmd))
    proc = subprocess.run(cmd, cwd=str(SKILL_DIR), check=False)
    return int(proc.returncode)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build, tune, and validate composite score IC.")
    parser.add_argument("--skip-build", action="store_true")
    parser.add_argument("--full-history", action="store_true")
    parser.add_argument("--with-mirofish", action="store_true")
    parser.add_argument("--max-tickers", type=int, default=80)
    parser.add_argument("--csv", default="", help="Existing audit CSV (skip build)")
    parser.add_argument("--skip-trades", action="store_true")
    args = parser.parse_args()

    csv_path = Path(args.csv) if args.csv else (
        ARTIFACT_DIR / ("scoring_audit_dataset_full.csv" if args.full_history else "scoring_audit_dataset.csv")
    )

    build_rc = 0
    if not args.skip_build and not args.csv:
        build_cmd = [
            PY,
            str(SKILL_DIR / "scripts" / "build_scoring_audit_dataset.py"),
            "--max-tickers",
            str(int(args.max_tickers)),
        ]
        if args.full_history:
            build_cmd.append("--full-history")
        if args.with_mirofish:
            build_cmd.append("--with-mirofish")
        build_rc = _run(build_cmd, step="Build scoring audit dataset")
        if build_rc != 0:
            return build_rc

    if not csv_path.exists():
        print(f"FAIL: dataset missing at {csv_path}")
        return 1

    tune_rc = _run(
        [PY, str(SKILL_DIR / "scripts" / "tune_composite_weights.py"), "--csv", str(csv_path)],
        step="Tune composite weights",
    )

    validate_rc = _run(
        [
            PY,
            str(SKILL_DIR / "scripts" / "validate_scoring_metrics.py"),
            "--strict",
            "--dataset",
            str(csv_path),
        ],
        step="Validate candidates (strict)",
    )

    trades_rc = 0
    if not args.skip_trades:
        trades_rc = _run(
            [PY, str(SKILL_DIR / "scripts" / "validate_scoring_metrics.py"), "--source", "trades", "--strict"],
            step="Validate realized trades (strict, skip if no chunks)",
        )

    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "dataset_csv": str(csv_path),
        "build_rc": build_rc,
        "tune_rc": tune_rc,
        "validate_rc": validate_rc,
        "trades_rc": trades_rc,
    }
    rec_path = ARTIFACT_DIR / "composite_weight_recommendation.json"
    if rec_path.exists():
        try:
            summary["tuner"] = json.loads(rec_path.read_text(encoding="utf-8"))
        except Exception:
            pass
    report_path = ARTIFACT_DIR / "scoring_metrics_report.json"
    if report_path.exists():
        try:
            summary["validation"] = json.loads(report_path.read_text(encoding="utf-8"))
        except Exception:
            pass

    out_path = ARTIFACT_DIR / "scoring_ic_pipeline_summary.json"
    out_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"\nWrote {out_path}")

    if validate_rc != 0:
        return validate_rc
    if tune_rc != 0:
        return tune_rc
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
