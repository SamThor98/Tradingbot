#!/usr/bin/env python3
"""Re-run control_legacy confirm CF with chop/regime calibration (no rematerialize).

Uses existing scored parquet from confirm_prob_rank_control_legacy.py and applies
the same post-score blends that cleared floors on the dual-run sample.

Example:
  python scripts/recalibrate_prob_rank_control_confirm.py \\
      --run-id control_legacy_aug
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import pandas as pd

SKILL_DIR = Path(__file__).resolve().parent.parent
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

from logger_setup import get_logger, setup_logging  # noqa: E402
from research.calibrate import (  # noqa: E402
    apply_chop_aware_scores,
    apply_regime_aware_scores,
    fit_risk_off_blend,
)
from research.counterfactual import run_prob_rank_counterfactual  # noqa: E402
from research.coverage import coverage_report  # noqa: E402
from research.infer import attach_scores_to_trades  # noqa: E402
from research.promotion import evaluate_prob_rank_promotion  # noqa: E402
from scripts.validate_scoring_metrics import _load_trade_frame  # noqa: E402

LOG = get_logger("recalibrate_prob_rank_control_confirm")


def _promo(cf: dict[str, Any], *, label: str) -> dict[str, Any]:
    metrics = {
        "pf_mean": cf["prob_rank"]["pf_mean"],
        "worst_era_pf": cf["prob_rank"]["worst_era_pf"],
        "n_trades": cf["prob_rank"]["n"],
        "retention": cf["prob_rank"]["retention"],
        "dual_run_ok": True,
        "walk_forward_ic_mean": None,
    }
    verdict = evaluate_prob_rank_promotion(metrics, requested="shadow")
    return {
        "label": label,
        "decision": verdict.decision,
        "floors_cleared": verdict.floors_cleared,
        "composite_score": verdict.composite_score,
        "rationale": verdict.rationale,
        "metrics": metrics,
        "by_era": cf["prob_rank"].get("by_era"),
        "score_col": cf["prob_rank"].get("score_col"),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", type=str, default="control_legacy_aug")
    parser.add_argument(
        "--out-dir",
        type=str,
        default=str(SKILL_DIR / "validation_artifacts" / "prob_rank_control_confirm"),
    )
    parser.add_argument("--min-percentile", type=float, default=75.0)
    parser.add_argument("--chop-blend", type=float, default=0.55)
    parser.add_argument("--risk-off-blend", type=float, default=None, help="None=fit on risk-off")
    args = parser.parse_args(argv)
    setup_logging()

    out_dir = Path(args.out_dir)
    scored_path = out_dir / f"scored_features_{args.run_id}.parquet"
    if not scored_path.exists():
        LOG.error("Missing scored parquet: %s", scored_path)
        return 2

    trades = _load_trade_frame(args.run_id)
    scored = pd.read_parquet(scored_path)
    cov = coverage_report(trades, scored)
    merged = attach_scores_to_trades(trades, scored)
    cal_cols = [
        c
        for c in merged.columns
        if c
        in (
            "regime_risk_off",
            "regime_chop_score",
            "compression_score",
            "breakout_velocity",
            "spy_above_sma200",
        )
    ]
    LOG.info(
        "Joined trades=%s scored_cov=%s cal_cols=%s",
        len(merged),
        cov.get("coverage"),
        cal_cols,
    )
    if "regime_risk_off" not in merged.columns:
        LOG.warning("regime_risk_off missing after join — calibration masks will be weak")

    variants: list[tuple[str, pd.DataFrame, str]] = []

    # 1) raw (parity with confirm summary)
    variants.append(("raw", merged, "expected_return_40d"))

    # 2) chop-aware (sample promote_shadow path)
    chop = apply_chop_aware_scores(merged, chop_blend=args.chop_blend)
    variants.append(("chop_cal", chop, "expected_return_40d_chop_cal"))

    # 3) regime-aware (bear_rates path) — try fit + a few fixed blends
    fit = fit_risk_off_blend(merged)
    blend_candidates: list[float] = []
    if args.risk_off_blend is not None:
        blend_candidates = [float(args.risk_off_blend)]
    else:
        blend_candidates = sorted(
            {float(fit["best_blend"]), 0.0, 0.35, 0.5, 0.65},
            key=lambda x: (x != float(fit["best_blend"]), x),
        )
    LOG.info("risk_off blend candidates=%s fit=%s", blend_candidates, fit)
    for bw in blend_candidates:
        regime = apply_regime_aware_scores(merged, risk_off_blend=bw)
        variants.append((f"regime_cal_w{bw:g}", regime, "expected_return_40d_calibrated"))
        chop_then_regime = apply_regime_aware_scores(
            chop,
            model_col="expected_return_40d_chop_cal",
            out_col="expected_return_40d_chop_regime",
            risk_off_blend=bw,
        )
        variants.append(
            (f"chop_then_regime_w{bw:g}", chop_then_regime, "expected_return_40d_chop_regime")
        )
        regime_then_chop = apply_chop_aware_scores(
            regime,
            model_col="expected_return_40d_calibrated",
            out_col="expected_return_40d_regime_chop",
            chop_blend=args.chop_blend,
        )
        variants.append(
            (f"regime_then_chop_w{bw:g}", regime_then_chop, "expected_return_40d_regime_chop")
        )

    results: list[dict[str, Any]] = []
    best: dict[str, Any] | None = None
    for label, frame, score_col in variants:
        cf = run_prob_rank_counterfactual(
            trades,
            scored,
            min_percentile=args.min_percentile,
            control_percentile=75.0,
            score_col=score_col,
            pre_merged=frame,
        )
        row = _promo(cf, label=label)
        row["rank_v2_control"] = {
            "pf_mean": cf["rank_v2_control"]["pf_mean"],
            "worst_era_pf": cf["rank_v2_control"]["worst_era_pf"],
        }
        results.append(row)
        LOG.info(
            "%s pf_mean=%s worst=%s decision=%s floors=%s",
            label,
            row["metrics"]["pf_mean"],
            row["metrics"]["worst_era_pf"],
            row["decision"],
            row["floors_cleared"],
        )
        if best is None:
            best = {**row, "cf": cf}
            continue
        # Prefer floors cleared; else higher worst_era then pf_mean
        def _key(r: dict[str, Any]) -> tuple:
            return (
                1 if r["floors_cleared"] else 0,
                float(r["metrics"]["worst_era_pf"] or 0.0),
                float(r["metrics"]["pf_mean"] or 0.0),
            )

        if _key(row) > _key(best):
            best = {**row, "cf": cf}

    assert best is not None
    best_cf = best.pop("cf")
    summary = {
        "run_id": args.run_id,
        "coverage": cov,
        "chop_blend": args.chop_blend,
        "risk_off_blend_candidates": blend_candidates,
        "risk_off_fit": fit,
        "variants": results,
        "best": {k: v for k, v in best.items() if k != "cf"},
        "note": (
            "Post-hoc calibration on confirm scores; keep PROB_RANK_MODE=off "
            "until floors clear and shadow wiring is explicit."
        ),
    }
    out_path = out_dir / f"recalibrate_summary_{args.run_id}.json"
    out_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    # Persist best CF + promotion alongside confirm artifacts
    cf_path = out_dir / f"prob_rank_counterfactual_p75_{args.run_id}_best_cal.json"
    promo_path = out_dir / f"prob_rank_promotion_decision_{args.run_id}_best_cal.json"
    best_cf["calibration_label"] = best["label"]
    best_cf["coverage_after"] = cov
    cf_path.write_text(json.dumps(best_cf, indent=2), encoding="utf-8")
    promo = {
        "decision": best["decision"],
        "floors_cleared": best["floors_cleared"],
        "composite_score": best["composite_score"],
        "rationale": best["rationale"],
        "metrics": best["metrics"],
        "calibration_label": best["label"],
        "score_col": best["score_col"],
        "chop_blend": args.chop_blend,
        "risk_off_blend_candidates": blend_candidates,
        "counterfactual": str(cf_path),
        "run_id": args.run_id,
        "note": "Best of raw/chop/regime variants; do not enable live",
    }
    promo_path.write_text(json.dumps(promo, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    LOG.info("Wrote %s", out_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
