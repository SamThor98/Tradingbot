#!/usr/bin/env python3
"""Decide PROB_RANK_MODE promotion (shadow/live) from research artifacts.

Hard floors: PF mean ≥ 1.20, worst-era PF ≥ 1.00.
Composite score ranks alternatives that clear floors.

Example:
  python scripts/decide_prob_rank_promotion.py \\
      --artifact validation_artifacts/prob_rank_portfolio_control_legacy_aug_equal.json \\
      --requested shadow
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

from experiment_registry import append_registry_event  # noqa: E402
from logger_setup import get_logger, setup_logging  # noqa: E402
from research.promotion import (  # noqa: E402
    evaluate_prob_rank_promotion,
    metrics_from_portfolio_result,
)

LOG = get_logger("decide_prob_rank_promotion")


def _load_metrics(path: Path, extras: dict) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if "pf_mean" in payload and "worst_era_pf" in payload and "portfolio" not in payload:
        metrics = dict(payload)
    else:
        metrics = metrics_from_portfolio_result(payload)
    # Optional report metrics.json merge
    metrics.update({k: v for k, v in extras.items() if v is not None})
    return metrics


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact", type=str, required=True, help="Portfolio CF or metrics JSON")
    parser.add_argument("--report-dir", type=str, default=None, help="Optional prob_rank/<run>/ report")
    parser.add_argument("--requested", type=str, default="shadow", choices=["shadow", "live"])
    parser.add_argument("--dual-run-ok", action="store_true", help="Mark dual-run evidence present")
    parser.add_argument("--ic", type=float, default=None, help="Override walk-forward IC mean")
    parser.add_argument("--calibration-error", type=float, default=None)
    parser.add_argument("--drift", type=float, default=None, help="Live vs backtest PF drift")
    parser.add_argument("--apply", action="store_true", help="Append registry decision (always records)")
    parser.add_argument("--out", type=str, default=None)
    args = parser.parse_args(argv)
    setup_logging()

    extras: dict = {"dual_run_ok": bool(args.dual_run_ok)}
    if args.ic is not None:
        extras["walk_forward_ic_mean"] = args.ic
    if args.calibration_error is not None:
        extras["calibration_error"] = args.calibration_error
    if args.drift is not None:
        extras["live_backtest_pf_drift"] = args.drift

    if args.report_dir:
        report = Path(args.report_dir)
        metrics_path = report / "metrics.json"
        if metrics_path.is_file():
            m = json.loads(metrics_path.read_text(encoding="utf-8"))
            if m.get("walk_forward_ic_mean") is not None and extras.get("walk_forward_ic_mean") is None:
                extras["walk_forward_ic_mean"] = m.get("walk_forward_ic_mean")
            boot = report / "bootstrap.json"
            if boot.is_file():
                extras["bootstrap"] = json.loads(boot.read_text(encoding="utf-8"))

    metrics = _load_metrics(Path(args.artifact), extras)
    verdict = evaluate_prob_rank_promotion(metrics, requested=args.requested)

    payload = {
        "decision": verdict.decision,
        "floors_cleared": verdict.floors_cleared,
        "composite_score": verdict.composite_score,
        "rationale": verdict.rationale,
        "gates": verdict.gates,
        "dimension_scores": verdict.dimension_scores,
        "metrics": metrics,
        "artifact": str(args.artifact),
    }

    out_path = Path(args.out) if args.out else SKILL_DIR / "validation_artifacts" / "prob_rank_promotion_decision.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    LOG.info("Wrote %s decision=%s", out_path, verdict.decision)

    # Always append registry when --apply; otherwise still allow dry-run print
    if args.apply:
        append_registry_event(
            event_type="prob_rank_promotion_decision",
            target="PROB_RANK_MODE",
            decision=verdict.decision,
            rationale=verdict.rationale,
            gates=verdict.gates,
            metadata={
                "artifact": str(args.artifact),
                "decision_path": str(out_path),
                "requested": args.requested,
                "composite_score": verdict.composite_score,
                "dimension_scores": verdict.dimension_scores,
            },
            skill_dir=SKILL_DIR,
        )
        LOG.info("Registry event appended (prob_rank_promotion_decision)")

    print(json.dumps(payload, indent=2))
    # Exit 0 for promote_*, 2 for hold, 1 for reject (CI-friendly)
    if verdict.decision.startswith("promote"):
        return 0
    if verdict.decision == "hold":
        return 2
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
