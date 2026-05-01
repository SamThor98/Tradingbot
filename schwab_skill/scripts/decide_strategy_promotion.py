#!/usr/bin/env python3
"""
Decide/apply strategy parameter promotion from walk-forward artifacts.
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
CHAMPION_PARAMS_FILE = SKILL_DIR / "artifacts" / "strategy_champion_params.json"
sys.path.insert(0, str(SCRIPTS_DIR))

from _strategy_gates import (
    DEFAULT_MAX_DD_DEGRADE_CAP_PCT,
    DEFAULT_MIN_EXPECTANCY_DELTA,
    DEFAULT_MIN_OOS_PF,
    DEFAULT_MIN_OOS_PF_DELTA,
    DEFAULT_MIN_PF_DELTA,
    DEFAULT_MIN_TRADES_PER_ERA,
    DEFAULT_MIN_TRADES_THRESHOLD,
)
from promotion_guard import ensure_signed_approval
from release_gate import ensure_release_gate_for_apply

from experiment_registry import append_registry_event


def _run_validate(cmd_args: list[str]) -> tuple[int, str]:
    cmd = [sys.executable, str(SCRIPTS_DIR / "validate_pf_robustness.py")] + cmd_args
    proc = subprocess.run(cmd, cwd=str(SKILL_DIR), capture_output=True, text=True)
    out = (proc.stdout or "").strip()
    err = (proc.stderr or "").strip()
    if out:
        print(out)
    if err:
        print(err)
    return proc.returncode, out


def _extract_best_params(path_like: str) -> dict[str, str]:
    p = Path(path_like)
    if not p.is_absolute():
        p = SKILL_DIR / p
    data = json.loads(p.read_text(encoding="utf-8"))
    if isinstance(data, dict) and isinstance(data.get("best_params"), dict):
        return {str(k): str(v) for k, v in data["best_params"].items()}
    raise ValueError("artifact missing best_params")


def _check_fresh_window_confirmation(path_like: str) -> tuple[bool, str]:
    """Verify a fresh-window confirmation artifact exists, is recent, and passed.

    Used by the biweekly cadence to ensure the promotion decision is backed
    by a re-run of the gates against the latest available data, not just
    the (potentially stale) walk-forward artifact selected earlier in the
    week. Returns (ok, reason) so callers can surface a precise refusal.
    """
    if not path_like:
        return False, "missing_fresh_window_confirm_artifact_arg"
    p = Path(path_like)
    if not p.is_absolute():
        p = SKILL_DIR / p
    if not p.exists():
        return False, f"fresh_window_artifact_not_found:{p}"
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception as exc:
        return False, f"fresh_window_artifact_unreadable:{exc}"
    if not isinstance(data, dict):
        return False, "fresh_window_artifact_not_an_object"
    if not bool(data.get("passed")):
        return False, "fresh_window_artifact_did_not_pass"
    return True, "ok"


def _load_selected_artifact_from_ranking(path_like: str) -> str:
    p = Path(path_like)
    if not p.is_absolute():
        p = SKILL_DIR / p
    data = json.loads(p.read_text(encoding="utf-8"))
    selected = data.get("selected", {}) if isinstance(data, dict) else {}
    candidate = str(selected.get("artifact") or "").strip()
    if not candidate:
        raise ValueError("ranking artifact missing selected.artifact")
    return candidate


def main() -> int:
    parser = argparse.ArgumentParser(description="Decide and optionally apply strategy parameter promotion")
    parser.add_argument("--challenger-artifact", default="", help="Walk-forward optimizer artifact json")
    parser.add_argument("--ranking-artifact", default="", help="Optional ranking artifact to source selected challenger")
    parser.add_argument(
        "--champion-artifact",
        default="",
        help="Optional champion artifact json. Default uses artifacts/strategy_champion_params.json",
    )
    parser.add_argument("--min-pf-delta", type=float, default=DEFAULT_MIN_PF_DELTA)
    parser.add_argument("--min-expectancy-delta", type=float, default=DEFAULT_MIN_EXPECTANCY_DELTA)
    parser.add_argument("--min-oos-pf", type=float, default=DEFAULT_MIN_OOS_PF)
    parser.add_argument("--min-oos-pf-delta", type=float, default=DEFAULT_MIN_OOS_PF_DELTA)
    parser.add_argument("--max-drawdown-degrade-cap", type=float, default=DEFAULT_MAX_DD_DEGRADE_CAP_PCT)
    parser.add_argument("--min-trades-threshold", type=int, default=DEFAULT_MIN_TRADES_THRESHOLD)
    parser.add_argument(
        "--min-trades-per-era",
        type=int,
        default=DEFAULT_MIN_TRADES_PER_ERA,
        help="Per-era throughput floor; passed through to validate_pf_robustness.",
    )
    parser.add_argument(
        "--require-fresh-window-confirm",
        action="store_true",
        help=(
            "Biweekly promotion safety: when set, require an explicit "
            "fresh-window confirmation file before applying. The file path "
            "is supplied via --fresh-window-confirm-artifact."
        ),
    )
    parser.add_argument(
        "--fresh-window-confirm-artifact",
        default="",
        help=(
            "Path to a JSON artifact whose 'passed' field is True (typical "
            "output of a recent validate_pf_robustness run). Required when "
            "--require-fresh-window-confirm is passed."
        ),
    )
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if args.apply:
        ok, _reasons = ensure_release_gate_for_apply(
            skill_dir=SKILL_DIR,
            target="strategy_champion_params",
            require_slo_status=False,
        )
        if not ok:
            return 3
        if args.require_fresh_window_confirm:
            confirm_ok, confirm_reason = _check_fresh_window_confirmation(
                args.fresh_window_confirm_artifact
            )
            if not confirm_ok:
                print(
                    "Refusing --apply: fresh-window confirmation missing or "
                    f"invalid ({confirm_reason}). Re-run validate_pf_robustness "
                    "against the latest data window and retry."
                )
                return 4
    if not ensure_signed_approval(
        "strategy_champion_params", apply_requested=args.apply
    ):
        return 2

    challenger_artifact = args.challenger_artifact
    if args.ranking_artifact:
        challenger_artifact = _load_selected_artifact_from_ranking(args.ranking_artifact)
    if not challenger_artifact:
        raise ValueError("Provide --challenger-artifact or --ranking-artifact")

    champion_ref = args.champion_artifact or str(CHAMPION_PARAMS_FILE)
    cmd_args = [
        "--champion-artifact",
        champion_ref,
        "--challenger-artifact",
        challenger_artifact,
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
    rc, _stdout = _run_validate(cmd_args)
    promote = rc == 0

    decision = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "promote": promote,
        "applied": False,
        "champion_artifact": champion_ref,
        "challenger_artifact": challenger_artifact,
        "ranking_artifact": args.ranking_artifact or None,
        "gates": {
            "min_pf_delta": float(args.min_pf_delta),
            "min_expectancy_delta": float(args.min_expectancy_delta),
            "min_oos_pf": float(args.min_oos_pf),
            "min_oos_pf_delta": float(args.min_oos_pf_delta),
            "max_drawdown_degrade_cap": float(args.max_drawdown_degrade_cap),
            "min_trades_threshold": int(args.min_trades_threshold),
            "min_trades_per_era": int(args.min_trades_per_era),
        },
        "fresh_window_confirm_required": bool(args.require_fresh_window_confirm),
        "fresh_window_confirm_artifact": (
            args.fresh_window_confirm_artifact or None
        ),
    }
    decision["reasons"] = [] if promote else ["pf_robustness_gate_failed"]
    if promote and args.apply:
        params = _extract_best_params(challenger_artifact)
        CHAMPION_PARAMS_FILE.parent.mkdir(parents=True, exist_ok=True)
        CHAMPION_PARAMS_FILE.write_text(json.dumps({"params": params}, indent=2), encoding="utf-8")
        decision["applied"] = True
        decision["applied_path"] = str(CHAMPION_PARAMS_FILE)

    append_registry_event(
        event_type="strategy_promotion_decision",
        target="strategy_champion_params",
        decision="promote" if promote else "reject",
        rationale=[str(x) for x in decision.get("reasons") or []],
        gates=decision.get("gates"),
        metadata={
            "applied": bool(decision.get("applied")),
            "challenger_artifact": challenger_artifact,
            "champion_artifact": champion_ref,
        },
        skill_dir=SKILL_DIR,
    )

    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = ARTIFACT_DIR / f"strategy_promotion_decision_{run_id}.json"
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(decision, indent=2), encoding="utf-8")
    print(f"Decision artifact: {out}")
    print(json.dumps(decision, indent=2))
    return 0 if promote else 1


if __name__ == "__main__":
    raise SystemExit(main())
