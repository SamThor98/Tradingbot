#!/usr/bin/env python3
"""Biweekly promotion decision with fresh-window confirmation.

Locked-cadence wrapper around ``decide_strategy_promotion.py`` that:

1. Picks the most recent ranking artifact produced by the weekly cycle.
2. Re-runs ``validate_pf_robustness.py`` against the latest data window
   (the "fresh-window confirmation" step) so the promotion decision is
   never made off a stale tune.
3. Calls ``decide_strategy_promotion.py`` with
   ``--require-fresh-window-confirm`` pointing at the just-written
   confirmation artifact.

Designed to be invoked from the existing scheduler. Use ``--apply`` only
on the biweekly cadence — the weekly cycle stays in dry-run.

Cadence (locked goal profile):
- Weekly: ``run_strategy_tune_cycle.py`` (no --apply) — refreshes ranking
  + diagnostics.
- Biweekly: this script with ``--apply`` — confirms + promotes.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = SKILL_DIR / "scripts"
ARTIFACT_DIR = SKILL_DIR / "validation_artifacts"
sys.path.insert(0, str(SCRIPTS_DIR))

from _strategy_gates import (  # noqa: E402
    DEFAULT_MAX_DD_DEGRADE_CAP_PCT,
    DEFAULT_MIN_EXPECTANCY_DELTA,
    DEFAULT_MIN_OOS_PF,
    DEFAULT_MIN_OOS_PF_DELTA,
    DEFAULT_MIN_PF_DELTA,
    DEFAULT_MIN_TRADES_PER_ERA,
    DEFAULT_MIN_TRADES_THRESHOLD,
)


def _run(cmd: list[str]) -> int:
    print(" ".join(cmd))
    proc = subprocess.run(cmd, cwd=str(SKILL_DIR))
    return int(proc.returncode)


def _latest(pattern: str) -> Path | None:
    files = sorted(ARTIFACT_DIR.glob(pattern))
    return files[-1] if files else None


def main() -> int:
    parser = argparse.ArgumentParser(description="Biweekly strategy promotion with fresh-window confirmation.")
    parser.add_argument(
        "--ranking-artifact",
        default="",
        help=(
            "Optional explicit ranking artifact. Defaults to the most recent "
            "optimization_candidate_ranking_*.json in validation_artifacts/."
        ),
    )
    parser.add_argument(
        "--champion-artifact",
        default="",
        help="Optional champion params artifact. Defaults to artifacts/strategy_champion_params.json.",
    )
    parser.add_argument("--min-pf-delta", type=float, default=DEFAULT_MIN_PF_DELTA)
    parser.add_argument("--min-expectancy-delta", type=float, default=DEFAULT_MIN_EXPECTANCY_DELTA)
    parser.add_argument("--min-oos-pf", type=float, default=DEFAULT_MIN_OOS_PF)
    parser.add_argument("--min-oos-pf-delta", type=float, default=DEFAULT_MIN_OOS_PF_DELTA)
    parser.add_argument("--max-drawdown-degrade-cap", type=float, default=DEFAULT_MAX_DD_DEGRADE_CAP_PCT)
    parser.add_argument("--min-trades-threshold", type=int, default=DEFAULT_MIN_TRADES_THRESHOLD)
    parser.add_argument("--min-trades-per-era", type=int, default=DEFAULT_MIN_TRADES_PER_ERA)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply the promotion if both gates and fresh-window confirmation pass.",
    )
    args = parser.parse_args()

    ranking = (
        Path(args.ranking_artifact).resolve()
        if args.ranking_artifact
        else _latest("optimization_candidate_ranking_*.json")
    )
    if not ranking or not ranking.exists():
        print("FAIL: no ranking artifact found. Run the weekly tune cycle first.")
        return 1

    # Step 1: derive challenger artifact from the ranking, then run a
    # fresh PF robustness validation against the latest window. This is
    # the "fresh-window confirmation" — it must pass before --apply is
    # honoured downstream.
    selected_artifact: str
    try:
        ranking_data = json.loads(ranking.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"FAIL: cannot read ranking artifact ({exc})")
        return 1
    selected = (ranking_data or {}).get("selected", {}) if isinstance(ranking_data, dict) else {}
    selected_artifact = str(selected.get("artifact") or "").strip()
    if not selected_artifact:
        print("FAIL: ranking artifact has no selected.artifact entry")
        return 1

    champion_artifact = args.champion_artifact or str(SKILL_DIR / "artifacts" / "strategy_champion_params.json")

    confirm_cmd = [
        sys.executable,
        str(SCRIPTS_DIR / "validate_pf_robustness.py"),
        "--champion-artifact",
        champion_artifact,
        "--challenger-artifact",
        selected_artifact,
        "--min-pf-delta",
        str(args.min_pf_delta),
        "--min-expectancy-delta",
        str(args.min_expectancy_delta),
        "--min-oos-pf",
        str(args.min_oos_pf),
        "--min-oos-pf-delta",
        str(args.min_oos_pf_delta),
        "--max-drawdown-degrade-cap",
        str(args.max_drawdown_degrade_cap),
        "--min-trades-threshold",
        str(args.min_trades_threshold),
        "--min-trades-per-era",
        str(args.min_trades_per_era),
    ]
    rc = _run(confirm_cmd)
    confirm_artifact = _latest("strategy_promotion_report_*.json")
    if rc != 0 or confirm_artifact is None:
        print("FAIL: fresh-window confirmation step did not pass.")
        return 1

    # Step 2: invoke the existing decision script with the confirmation.
    decision_cmd = [
        sys.executable,
        str(SCRIPTS_DIR / "decide_strategy_promotion.py"),
        "--ranking-artifact",
        str(ranking),
        "--champion-artifact",
        champion_artifact,
        "--min-pf-delta",
        str(args.min_pf_delta),
        "--min-expectancy-delta",
        str(args.min_expectancy_delta),
        "--min-oos-pf",
        str(args.min_oos_pf),
        "--min-oos-pf-delta",
        str(args.min_oos_pf_delta),
        "--max-drawdown-degrade-cap",
        str(args.max_drawdown_degrade_cap),
        "--min-trades-threshold",
        str(args.min_trades_threshold),
        "--min-trades-per-era",
        str(args.min_trades_per_era),
        "--require-fresh-window-confirm",
        "--fresh-window-confirm-artifact",
        str(confirm_artifact),
    ]
    if args.apply:
        decision_cmd.append("--apply")
    rc = _run(decision_cmd)

    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    summary = {
        "run_id": run_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "passed": rc == 0,
        "applied": bool(args.apply) and rc == 0,
        "ranking_artifact": str(ranking),
        "fresh_window_confirm_artifact": str(confirm_artifact),
        "champion_artifact": champion_artifact,
        "challenger_artifact": selected_artifact,
        "gates": {
            "min_pf_delta": float(args.min_pf_delta),
            "min_expectancy_delta": float(args.min_expectancy_delta),
            "min_oos_pf": float(args.min_oos_pf),
            "min_oos_pf_delta": float(args.min_oos_pf_delta),
            "max_drawdown_degrade_cap": float(args.max_drawdown_degrade_cap),
            "min_trades_threshold": int(args.min_trades_threshold),
            "min_trades_per_era": int(args.min_trades_per_era),
        },
    }
    out = ARTIFACT_DIR / f"strategy_promotion_biweekly_{run_id}.json"
    out.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"Biweekly summary artifact: {out}")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
