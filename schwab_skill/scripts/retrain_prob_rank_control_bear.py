#!/usr/bin/env python3
"""Retrain prob-rank on control confirm panel with higher bear weight; re-CF p75.

Uses the already-materialized scored feature panel (drops prior model scores),
retrains LightGBM with era weights, rescores, and evaluates on control_legacy_aug.

Example:
  python scripts/retrain_prob_rank_control_bear.py \\
      --panel validation_artifacts/prob_rank_control_confirm/scored_features_control_legacy_aug_labeled.parquet \\
      --bear-weight 3.0 --chop-weight 2.5 --purge-trade-keys
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

SKILL_DIR = Path(__file__).resolve().parent.parent
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

from logger_setup import get_logger, setup_logging  # noqa: E402
from research.calibrate import apply_chop_aware_scores, apply_regime_aware_scores  # noqa: E402
from research.counterfactual import run_prob_rank_counterfactual  # noqa: E402
from research.dataset import resolve_feature_columns  # noqa: E402
from research.infer import attach_scores_to_trades, predict_frame  # noqa: E402
from research.promotion import evaluate_prob_rank_promotion  # noqa: E402
from research.regime_context import REGIME_FEATURE_NAMES  # noqa: E402
from research.train import train_prob_rank_model  # noqa: E402
from scripts.validate_scoring_metrics import _load_trade_frame  # noqa: E402

LOG = get_logger("retrain_prob_rank_control_bear")

_DROP_SCORE_COLS = (
    "expected_return_40d",
    "expected_downside_40d",
    "confidence",
    "expected_pf_proxy",
)


def _purge_cf_trade_keys(train_df: pd.DataFrame, trades: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Drop panel rows that match control dual-run trade (ticker, entry_date) keys."""
    keys = trades[["ticker", "entry_date"]].copy()
    keys["ticker"] = keys["ticker"].astype(str).str.upper()
    keys["asof_date"] = pd.to_datetime(keys["entry_date"]).dt.strftime("%Y-%m-%d")
    keys = keys[["ticker", "asof_date"]].drop_duplicates()
    keys["_cf_trade"] = 1

    work = train_df.copy()
    work["ticker"] = work["ticker"].astype(str).str.upper()
    work["asof_date"] = pd.to_datetime(work["asof_date"]).dt.strftime("%Y-%m-%d")
    before = len(work)
    merged = work.merge(keys, on=["ticker", "asof_date"], how="left")
    kept = merged[merged["_cf_trade"].isna()].drop(columns=["_cf_trade"], errors="ignore")
    stats = {
        "n_before": int(before),
        "n_after": int(len(kept)),
        "n_purged": int(before - len(kept)),
        "n_cf_keys": int(len(keys)),
        "purge_frac": round(float((before - len(kept)) / max(1, before)), 4),
    }
    return kept, stats


def _promo(cf: dict, *, label: str) -> dict:
    metrics = {
        "pf_mean": cf["prob_rank"]["pf_mean"],
        "worst_era_pf": cf["prob_rank"]["worst_era_pf"],
        "n_trades": cf["prob_rank"]["n"],
        "retention": cf["prob_rank"]["retention"],
        "dual_run_ok": True,
        "walk_forward_ic_mean": None,
    }
    v = evaluate_prob_rank_promotion(metrics, requested="shadow")
    return {
        "label": label,
        "decision": v.decision,
        "floors_cleared": v.floors_cleared,
        "composite_score": v.composite_score,
        "rationale": v.rationale,
        "metrics": metrics,
        "by_era": cf["prob_rank"].get("by_era"),
        "score_col": cf["prob_rank"].get("score_col"),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", type=str, default="control_legacy_aug")
    parser.add_argument(
        "--panel",
        type=str,
        default=str(
            SKILL_DIR
            / "validation_artifacts"
            / "prob_rank_control_confirm"
            / "scored_features_control_legacy_aug_labeled.parquet"
        ),
    )
    parser.add_argument(
        "--out-dir",
        type=str,
        default=str(SKILL_DIR / "validation_artifacts" / "prob_rank_control_retrain"),
    )
    parser.add_argument("--bear-weight", type=float, default=3.0)
    parser.add_argument("--chop-weight", type=float, default=2.5)
    parser.add_argument("--num-boost-round", type=int, default=150)
    parser.add_argument("--min-percentile", type=float, default=75.0)
    parser.add_argument("--chop-blend", type=float, default=0.55)
    parser.add_argument("--risk-off-blend", type=float, default=0.65)
    parser.add_argument(
        "--holdout-era",
        type=str,
        default="recent_current",
        help="Era excluded from final fit (default recent_current). Use 'none' to include all eras.",
    )
    parser.add_argument(
        "--purge-trade-keys",
        action="store_true",
        help="Exclude control dual-run (ticker, entry_date) rows from training",
    )
    args = parser.parse_args(argv)
    setup_logging()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    panel_path = Path(args.panel)
    if not panel_path.is_file():
        LOG.error("Missing panel %s", panel_path)
        return 2

    panel = pd.read_parquet(panel_path)
    trades = _load_trade_frame(args.run_id)
    train_df = panel.drop(columns=[c for c in _DROP_SCORE_COLS if c in panel.columns], errors="ignore")
    if "ret_40d_fwd" not in train_df.columns:
        LOG.error("Panel missing ret_40d_fwd labels")
        return 2
    before = len(train_df)
    train_df = train_df[train_df["ret_40d_fwd"].notna()].copy()
    LOG.info("Train rows %s -> %s (labeled)", before, len(train_df))

    purge_stats: dict | None = None
    if args.purge_trade_keys:
        train_df, purge_stats = _purge_cf_trade_keys(train_df, trades)
        LOG.info("Purged CF trade keys: %s", purge_stats)
        if len(train_df) < 5000:
            LOG.error("Too few train rows after purge: %s", len(train_df))
            return 2

    feature_cols = resolve_feature_columns(train_df)
    for name in REGIME_FEATURE_NAMES:
        if name in train_df.columns and name not in feature_cols:
            feature_cols.append(name)
    for name in ("trend_efficiency_20d", "breakout_hot_raw"):
        if name in train_df.columns and name not in feature_cols:
            feature_cols.append(name)

    era_weights = {
        "bear_rates": float(args.bear_weight),
        "volatility_chop": float(args.chop_weight),
    }
    holdout_raw = str(args.holdout_era or "").strip().lower()
    holdout_era = None if holdout_raw in {"", "none", "off", "null"} else str(args.holdout_era).strip()
    panel_tag = Path(args.panel).stem.replace("scored_features_", "")[:40]
    ds_tag = "purged" if args.purge_trade_keys else "labeled"
    hold_tag = "norecenthold" if holdout_era is None else f"hold_{holdout_era[:12]}"
    artifact = train_prob_rank_model(
        train_df,
        feature_cols,
        skill_dir=SKILL_DIR,
        num_boost_round=args.num_boost_round,
        early_stopping_rounds=max(10, args.num_boost_round // 5),
        dataset_id=(
            f"ctrl_{panel_tag}_{ds_tag}_{hold_tag}_b{args.bear_weight:g}_c{args.chop_weight:g}"
        ),
        write=True,
        era_weights=era_weights,
        holdout_era=holdout_era,
    )
    model_id = artifact["model_id"]
    LOG.info("Trained %s features=%s", model_id, len(feature_cols))

    # Rescore full panel (including unlabeled) for trade join coverage
    feat_all = panel.drop(columns=[c for c in _DROP_SCORE_COLS if c in panel.columns], errors="ignore")
    scored = predict_frame(artifact, feat_all)
    scored_path = out_dir / f"scored_features_{model_id}.parquet"
    scored.to_parquet(scored_path, index=False)

    merged = attach_scores_to_trades(trades, scored)

    cf_raw = run_prob_rank_counterfactual(
        trades,
        scored,
        min_percentile=args.min_percentile,
        control_percentile=75.0,
        score_col="expected_return_40d",
        pre_merged=merged,
    )
    chop = apply_chop_aware_scores(merged, chop_blend=args.chop_blend)
    cf_chop = run_prob_rank_counterfactual(
        trades,
        scored,
        min_percentile=args.min_percentile,
        control_percentile=75.0,
        score_col="expected_return_40d_chop_cal",
        pre_merged=chop,
    )
    regime = apply_regime_aware_scores(merged, risk_off_blend=args.risk_off_blend)
    cf_regime = run_prob_rank_counterfactual(
        trades,
        scored,
        min_percentile=args.min_percentile,
        control_percentile=75.0,
        score_col="expected_return_40d_calibrated",
        pre_merged=regime,
    )
    both = apply_regime_aware_scores(
        chop,
        model_col="expected_return_40d_chop_cal",
        out_col="expected_return_40d_chop_regime",
        risk_off_blend=args.risk_off_blend,
    )
    cf_both = run_prob_rank_counterfactual(
        trades,
        scored,
        min_percentile=args.min_percentile,
        control_percentile=75.0,
        score_col="expected_return_40d_chop_regime",
        pre_merged=both,
    )

    variants = [
        _promo(cf_raw, label="raw"),
        _promo(cf_chop, label="chop_cal"),
        _promo(cf_regime, label=f"regime_w{args.risk_off_blend:g}"),
        _promo(cf_both, label=f"chop_then_regime_w{args.risk_off_blend:g}"),
    ]
    best = max(
        variants,
        key=lambda r: (
            1 if r["floors_cleared"] else 0,
            float(r["metrics"]["worst_era_pf"] or 0.0),
            float(r["metrics"]["pf_mean"] or 0.0),
        ),
    )
    summary = {
        "model_id": model_id,
        "model_dir": str(SKILL_DIR / "research_store" / "models" / model_id),
        "run_id": args.run_id,
        "era_weights": era_weights,
        "holdout_era": holdout_era,
        "purge_trade_keys": bool(args.purge_trade_keys),
        "purge_stats": purge_stats,
        "n_train": int(len(train_df)),
        "n_features": len(feature_cols),
        "scored_path": str(scored_path),
        "variants": variants,
        "best": best,
        "rank_v2_control": {
            "pf_mean": cf_raw["rank_v2_control"]["pf_mean"],
            "worst_era_pf": cf_raw["rank_v2_control"]["worst_era_pf"],
        },
        "note": (
            "Purged CF trade keys from train" if args.purge_trade_keys else "In-sample trade keys allowed"
        )
        + (
            "; final fit includes recent_current (no era holdout)"
            if holdout_era is None
            else f"; final-fit holdout_era={holdout_era}"
        )
        + "; keep PROB_RANK_MODE=shadow until reviewed; do not enable live",
    }
    out_path = out_dir / f"retrain_summary_{model_id}.json"
    out_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    (out_dir / f"prob_rank_counterfactual_p75_{model_id}.json").write_text(
        json.dumps(cf_raw, indent=2), encoding="utf-8"
    )
    promo = {
        "decision": best["decision"],
        "floors_cleared": best["floors_cleared"],
        "composite_score": best["composite_score"],
        "rationale": best["rationale"],
        "metrics": best["metrics"],
        "calibration_label": best["label"],
        "model_id": model_id,
        "run_id": args.run_id,
        "purge_trade_keys": bool(args.purge_trade_keys),
        "purge_stats": purge_stats,
        "note": summary["note"],
    }
    (out_dir / f"prob_rank_promotion_decision_{model_id}.json").write_text(
        json.dumps(promo, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary, indent=2))
    LOG.info("Wrote %s best=%s floors=%s", out_path, best["label"], best["floors_cleared"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
