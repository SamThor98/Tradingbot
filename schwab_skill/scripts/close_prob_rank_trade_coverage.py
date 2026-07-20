#!/usr/bin/env python3
"""Materialize features on missing trade entry dates and re-score dual-run.

Example:
  python scripts/close_prob_rank_trade_coverage.py \\
      --run-id prob_rank_dual_run_sample \\
      --model-dir research_store/models/lgbm_ret_40d_fwd_ee51da619c \\
      --features research_store/datasets/rank_stage2_pass_v1_s1_690f53a237f2.parquet
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
from research.counterfactual import run_prob_rank_counterfactual  # noqa: E402
from research.coverage import (  # noqa: E402
    coverage_report,
    materialize_trade_entry_features,
    missing_trade_keys,
)
from research.infer import predict_frame  # noqa: E402
from research.promotion import evaluate_prob_rank_promotion  # noqa: E402
from research.train import load_model_artifact  # noqa: E402
from scripts.validate_scoring_metrics import _load_trade_frame  # noqa: E402

LOG = get_logger("close_prob_rank_trade_coverage")


def _fetch_bars(ticker: str, start: str | None, end: str | None) -> pd.DataFrame | None:
    from datetime import datetime, timezone

    from market_data import get_daily_history

    # get_daily_history returns trailing N days ending near today — size N from now→start.
    days = 1200
    if start:
        try:
            start_ts = datetime.strptime(start[:10], "%Y-%m-%d")
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            days = max(400, min(5000, (now - start_ts).days + 400))
        except ValueError:
            days = 1200
    df = get_daily_history(ticker, days=days, skill_dir=SKILL_DIR)
    if df is None or getattr(df, "empty", True):
        return None
    if end:
        df = df.loc[df.index <= pd.Timestamp(end)]
    return df


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", type=str, default="prob_rank_dual_run_sample")
    parser.add_argument("--model-dir", type=str, required=True)
    parser.add_argument("--features", type=str, required=True, help="Existing feature/dataset parquet")
    parser.add_argument("--min-percentile", type=float, default=75.0)
    parser.add_argument(
        "--out-dir",
        type=str,
        default=str(SKILL_DIR / "validation_artifacts" / "prob_rank_ops_sample_train"),
    )
    args = parser.parse_args(argv)
    setup_logging()
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    trades = _load_trade_frame(args.run_id)
    existing = pd.read_parquet(args.features)
    artifact = load_model_artifact(Path(args.model_dir))
    scored_existing = predict_frame(artifact, existing)
    before = coverage_report(trades, scored_existing)
    LOG.info("Coverage before: %s", before)

    miss = missing_trade_keys(trades, scored_existing)
    LOG.info("Missing scored trades: %s", len(miss))
    if miss.empty:
        scored = scored_existing
    else:
        # Fetch bars spanning missing + warm-up
        tickers = sorted(miss["ticker"].unique())
        starts = pd.to_datetime(miss["entry_iso"]).min()
        ends = pd.to_datetime(miss["entry_iso"]).max()
        ticker_bars: dict[str, pd.DataFrame] = {}
        for t in tickers:
            bars = _fetch_bars(t, str(starts.date()), str(ends.date()))
            if bars is not None:
                ticker_bars[t] = bars
            else:
                LOG.warning("No bars for missing ticker %s", t)
        entry_feats = materialize_trade_entry_features(
            miss,
            ticker_bars,
            skill_dir=SKILL_DIR,
            write=True,
        )
        LOG.info("Materialized trade-entry rows: %s", len(entry_feats))
        if entry_feats.empty:
            scored = scored_existing
        else:
            scored_new = predict_frame(artifact, entry_feats)
            scored = pd.concat([scored_existing, scored_new], ignore_index=True)
            scored = scored.drop_duplicates(subset=["ticker", "asof_date"], keep="last")

    after = coverage_report(trades, scored)
    LOG.info("Coverage after: %s", after)

    scored_path = out_dir / "scored_features_coverage_closed.parquet"
    scored.to_parquet(scored_path, index=False)

    cf = run_prob_rank_counterfactual(
        trades,
        scored,
        min_percentile=args.min_percentile,
        control_percentile=75.0,
    )
    cf["coverage_before"] = before
    cf["coverage_after"] = after
    cf_path = out_dir / "prob_rank_counterfactual_p75_coverage_closed.json"
    cf_path.write_text(json.dumps(cf, indent=2), encoding="utf-8")

    metrics = {
        "pf_mean": cf["prob_rank"]["pf_mean"],
        "worst_era_pf": cf["prob_rank"]["worst_era_pf"],
        "n_trades": cf["prob_rank"]["n"],
        "retention": cf["prob_rank"]["retention"],
        "dual_run_ok": True,
    }
    verdict = evaluate_prob_rank_promotion(metrics, requested="shadow")
    promo = {
        "decision": verdict.decision,
        "floors_cleared": verdict.floors_cleared,
        "composite_score": verdict.composite_score,
        "rationale": verdict.rationale,
        "gates": verdict.gates,
        "metrics": metrics,
        "counterfactual": str(cf_path),
    }
    promo_path = out_dir / "prob_rank_promotion_decision_coverage_closed.json"
    promo_path.write_text(json.dumps(promo, indent=2), encoding="utf-8")

    summary = {
        "coverage_before": before,
        "coverage_after": after,
        "prob_rank": cf["prob_rank"],
        "rank_v2_control": cf["rank_v2_control"],
        "promotion": {"decision": verdict.decision, "floors_cleared": verdict.floors_cleared},
        "scored_path": str(scored_path),
        "cf_path": str(cf_path),
    }
    (out_dir / "coverage_close_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
