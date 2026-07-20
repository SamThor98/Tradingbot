#!/usr/bin/env python3
"""Attribute PF on days where prob-rank top-N and rank-v2 top-N disagree.

Answers: is low Jaccard *good* (only_prob beats only_v2) or noise?

Example:
  python scripts/analyze_prob_rank_shadow_disagreement.py
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
from research.infer import attach_scores_to_trades  # noqa: E402
from research.shadow_disagreement import analyze_topn_disagreement  # noqa: E402
from scripts.validate_scoring_metrics import _load_trade_frame  # noqa: E402

LOG = get_logger("analyze_prob_rank_shadow_disagreement")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", type=str, default="control_legacy_aug")
    parser.add_argument(
        "--scored",
        type=str,
        default=str(
            SKILL_DIR
            / "validation_artifacts"
            / "prob_rank_control_confirm"
            / "scored_features_control_legacy_aug.parquet"
        ),
    )
    parser.add_argument("--model-id", type=str, default="lgbm_ret_40d_fwd_2c9efe271d")
    parser.add_argument("--top-n", type=int, default=5)
    parser.add_argument("--min-cohort", type=int, default=8)
    parser.add_argument("--max-days", type=int, default=0, help="0 = all qualifying days")
    parser.add_argument(
        "--out",
        type=str,
        default=str(
            SKILL_DIR
            / "validation_artifacts"
            / "prob_rank_shadow_evidence"
            / "disagreement_attribution.json"
        ),
    )
    args = parser.parse_args(argv)
    setup_logging()

    scored_path = Path(args.scored)
    if not scored_path.is_file():
        LOG.error("Missing scored panel %s", scored_path)
        return 2

    trades = _load_trade_frame(args.run_id)
    scored = pd.read_parquet(scored_path)
    retrain = (
        SKILL_DIR
        / "validation_artifacts"
        / "prob_rank_control_retrain"
        / f"scored_features_{args.model_id}.parquet"
    )
    if retrain.is_file():
        scored = pd.concat([scored, pd.read_parquet(retrain)], ignore_index=True)
        scored = scored.drop_duplicates(subset=["ticker", "asof_date"], keep="last")

    merged = attach_scores_to_trades(trades, scored)
    report = analyze_topn_disagreement(
        merged,
        top_n=args.top_n,
        min_cohort=args.min_cohort,
        max_days=args.max_days or None,
    )
    report["model_id"] = args.model_id
    report["run_id"] = args.run_id
    report["scored_path"] = str(scored_path)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    LOG.info(
        "Disagreement verdict=%s only_prob_pf=%s only_v2_pf=%s -> %s",
        report.get("verdict"),
        (report.get("buckets") or {}).get("only_prob", {}).get("pf"),
        (report.get("buckets") or {}).get("only_v2", {}).get("pf"),
        out,
    )
    return 0 if report.get("ok") else 3


if __name__ == "__main__":
    raise SystemExit(main())
