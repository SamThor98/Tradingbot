#!/usr/bin/env python3
"""Improve volatility_chop ranking via chop features, era weight, compression blend.

Example:
  python scripts/improve_prob_rank_volatility_chop.py \\
      --dataset research_store/datasets/rank_stage2_pass_v1_s1_690f53a237f2.parquet \\
      --run-id prob_rank_dual_run_sample
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
from research.calibrate import add_chop_helper_features, apply_chop_aware_scores  # noqa: E402
from research.counterfactual import run_prob_rank_counterfactual  # noqa: E402
from research.dataset import resolve_feature_columns  # noqa: E402
from research.infer import attach_scores_to_trades, predict_frame  # noqa: E402
from research.promotion import evaluate_prob_rank_promotion  # noqa: E402
from research.regime_context import (  # noqa: E402
    REGIME_FEATURE_NAMES,
    attach_regime_features,
    compute_spy_regime_table,
    fetch_spy_bars,
)
from research.registry import load_feature_registry  # noqa: E402
from research.report import write_experiment_report  # noqa: E402
from research.train import train_prob_rank_model  # noqa: E402
from scripts.validate_scoring_metrics import _load_trade_frame  # noqa: E402

LOG = get_logger("improve_prob_rank_volatility_chop")

HELPER_FEATURES = ["trend_efficiency_20d", "breakout_hot_raw"]


def _spearman(a: pd.Series, b: pd.Series) -> float | None:
    s = a.corr(b, method="spearman")
    return None if s != s else float(s)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=str, required=True)
    parser.add_argument("--run-id", type=str, default="prob_rank_dual_run_sample")
    parser.add_argument("--num-boost-round", type=int, default=120)
    parser.add_argument("--bear-weight", type=float, default=2.0)
    parser.add_argument("--chop-weight", type=float, default=2.5)
    parser.add_argument("--chop-blend", type=float, default=0.55)
    parser.add_argument("--breakout-penalty", type=float, default=0.35)
    parser.add_argument(
        "--out-dir",
        type=str,
        default=str(SKILL_DIR / "validation_artifacts" / "prob_rank_ops_sample_train"),
    )
    args = parser.parse_args(argv)
    setup_logging()
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    load_feature_registry(reload=True)
    ds = pd.read_parquet(args.dataset)
    spy = fetch_spy_bars(skill_dir=SKILL_DIR, days=4000)
    if spy is None or getattr(spy, "empty", True):
        LOG.error("SPY bars unavailable")
        return 2

    enriched = attach_regime_features(ds, spy, assign_eras=True)
    enriched = add_chop_helper_features(enriched)
    enriched_path = out_dir / "dataset_with_chop_regime_features.parquet"
    enriched.to_parquet(enriched_path, index=False)

    feature_cols = resolve_feature_columns(enriched)
    for name in list(REGIME_FEATURE_NAMES) + HELPER_FEATURES:
        if name in enriched.columns and name not in feature_cols:
            feature_cols.append(name)
    LOG.info("Training with %s features", len(feature_cols))

    era_weights = {
        "bear_rates": float(args.bear_weight),
        "volatility_chop": float(args.chop_weight),
    }
    artifact = train_prob_rank_model(
        enriched,
        feature_cols,
        skill_dir=SKILL_DIR,
        num_boost_round=args.num_boost_round,
        early_stopping_rounds=max(10, args.num_boost_round // 5),
        dataset_id=f"chop_w{args.chop_weight:g}_bear_w{args.bear_weight:g}",
        write=True,
        era_weights=era_weights,
    )
    scored_ds = predict_frame(artifact, enriched)
    report_dir = write_experiment_report(
        run_id=f"chop_fix_{artifact['model_id']}",
        artifact=artifact,
        scored_df=scored_ds,
        skill_dir=SKILL_DIR,
    )

    # Score trade keys using enriched dataset + prior coverage-closed feature rows
    cov_path = out_dir / "scored_features_coverage_closed.parquet"
    feat_base = enriched.copy()
    feat_base["ticker"] = feat_base["ticker"].astype(str).str.upper()
    feat_base["asof_date"] = pd.to_datetime(feat_base["asof_date"]).dt.strftime("%Y-%m-%d")
    if cov_path.is_file():
        cov_scored = pd.read_parquet(cov_path)
        trade_keys = cov_scored[["ticker", "asof_date"]].drop_duplicates()
        trade_keys["ticker"] = trade_keys["ticker"].astype(str).str.upper()
        trade_keys["asof_date"] = pd.to_datetime(trade_keys["asof_date"]).dt.strftime("%Y-%m-%d")
        old_feats = cov_scored.drop(
            columns=[
                c
                for c in (
                    "expected_return_40d",
                    "expected_downside_40d",
                    "confidence",
                    "expected_pf_proxy",
                )
                if c in cov_scored.columns
            ],
            errors="ignore",
        )
        old_feats["ticker"] = old_feats["ticker"].astype(str).str.upper()
        old_feats["asof_date"] = pd.to_datetime(old_feats["asof_date"]).dt.strftime("%Y-%m-%d")
        old_feats = attach_regime_features(old_feats, spy, assign_eras=True)
        old_feats = add_chop_helper_features(old_feats)
        key_cols = ["ticker", "asof_date"]
        marked = old_feats.merge(
            feat_base[key_cols].drop_duplicates().assign(_in_ds=1),
            on=key_cols,
            how="left",
        )
        miss = marked[marked["_in_ds"].isna()].drop(columns=["_in_ds"])
        feat_all = pd.concat([feat_base, miss], ignore_index=True, sort=False)
        feat_all = feat_all.drop_duplicates(subset=key_cols, keep="last")
        feat_all = feat_all.merge(trade_keys, on=key_cols, how="inner")
        scored = predict_frame(artifact, feat_all)
        feat_for_join = feat_all
    else:
        scored = scored_ds
        feat_for_join = feat_base

    scored_path = out_dir / f"scored_features_chop_{artifact['model_id']}.parquet"
    scored.to_parquet(scored_path, index=False)

    trades = _load_trade_frame(args.run_id)
    merged = attach_scores_to_trades(trades, scored)
    merged["entry_iso"] = pd.to_datetime(merged["entry_date"]).dt.strftime("%Y-%m-%d")
    spy_tbl = compute_spy_regime_table(spy)
    regime_cols = [c for c in REGIME_FEATURE_NAMES if c != "rel_spy_20d" and c in spy_tbl.columns]
    merged = merged.drop(columns=[c for c in regime_cols if c in merged.columns], errors="ignore")
    merged = merged.merge(
        spy_tbl[["asof_date", *regime_cols]].drop_duplicates("asof_date"),
        left_on="entry_iso",
        right_on="asof_date",
        how="left",
    )
    # Join compression / breakout for chop calibration
    join_cols = [
        c
        for c in (
            "compression_score",
            "breakout_velocity",
            "ret_20d_prev",
            "atr_pct",
            *HELPER_FEATURES,
        )
        if c in feat_for_join.columns
    ]
    fj = feat_for_join[["ticker", "asof_date", *join_cols]].drop_duplicates(["ticker", "asof_date"])
    merged = merged.drop(columns=[c for c in join_cols if c in merged.columns], errors="ignore")
    merged = merged.merge(
        fj, left_on=["ticker", "entry_iso"], right_on=["ticker", "asof_date"], how="left", suffixes=("", "_f")
    )

    cf_raw = run_prob_rank_counterfactual(
        trades, scored, min_percentile=75.0, control_percentile=75.0, score_col="expected_return_40d"
    )
    chop_cal = apply_chop_aware_scores(
        merged,
        chop_blend=args.chop_blend,
        breakout_penalty=args.breakout_penalty,
    )
    cf_cal = run_prob_rank_counterfactual(
        trades,
        scored,
        min_percentile=75.0,
        control_percentile=75.0,
        score_col="expected_return_40d_chop_cal",
        pre_merged=chop_cal,
    )

    chop = chop_cal[chop_cal["era"] == "volatility_chop"]
    promo_raw = evaluate_prob_rank_promotion(
        {
            "pf_mean": cf_raw["prob_rank"]["pf_mean"],
            "worst_era_pf": cf_raw["prob_rank"]["worst_era_pf"],
            "n_trades": cf_raw["prob_rank"]["n"],
            "retention": cf_raw["prob_rank"]["retention"],
            "dual_run_ok": True,
        },
        requested="shadow",
    )
    promo_cal = evaluate_prob_rank_promotion(
        {
            "pf_mean": cf_cal["prob_rank"]["pf_mean"],
            "worst_era_pf": cf_cal["prob_rank"]["worst_era_pf"],
            "n_trades": cf_cal["prob_rank"]["n"],
            "retention": cf_cal["prob_rank"]["retention"],
            "dual_run_ok": True,
        },
        requested="shadow",
    )

    summary = {
        "model_id": artifact["model_id"],
        "report_dir": str(report_dir),
        "era_weights": era_weights,
        "chop_blend": args.chop_blend,
        "breakout_penalty": args.breakout_penalty,
        "volatility_chop_ic": {
            "model_raw": _spearman(chop["expected_return_40d"], chop["net_return"]),
            "model_chop_cal": _spearman(chop["expected_return_40d_chop_cal"], chop["net_return"]),
            "compression": _spearman(chop["compression_score"], chop["net_return"])
            if "compression_score" in chop.columns
            else None,
            "n": int(len(chop)),
        },
        "counterfactual_raw": {"prob_rank": cf_raw["prob_rank"], "rank_v2": cf_raw["rank_v2_control"]},
        "counterfactual_chop_cal": {
            "prob_rank": cf_cal["prob_rank"],
            "rank_v2": cf_cal["rank_v2_control"],
        },
        "promotion_raw": {
            "decision": promo_raw.decision,
            "floors_cleared": promo_raw.floors_cleared,
            "composite_score": promo_raw.composite_score,
        },
        "promotion_chop_cal": {
            "decision": promo_cal.decision,
            "floors_cleared": promo_cal.floors_cleared,
            "composite_score": promo_cal.composite_score,
            "rationale": promo_cal.rationale,
        },
        "chop_feature_importance": {
            k: v
            for k, v in (artifact.get("feature_importance_gain") or {}).items()
            if k in REGIME_FEATURE_NAMES or k in HELPER_FEATURES or k in {"compression_score", "breakout_velocity"}
        },
        "scored_path": str(scored_path),
        "enriched_dataset": str(enriched_path),
    }
    out_path = out_dir / "volatility_chop_improvement_summary.json"
    out_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    (out_dir / "prob_rank_counterfactual_p75_chop_calibrated.json").write_text(
        json.dumps(cf_cal, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
