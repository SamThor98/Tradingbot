#!/usr/bin/env python3
"""Seed shadow evidence ledger from control_legacy scored trade-day cohorts.

Builds multi-name Jaccard rows (prob-rank top-N vs rank-v2 top-N) from the
coverage-closed confirm panel so the ledger is not stuck on single-ticker
live smokes. Does not change PROB_RANK_MODE or selection.

Example:
  python scripts/seed_prob_rank_shadow_evidence_from_cf.py
  python scripts/seed_prob_rank_shadow_evidence_from_cf.py --max-days 60 --top-n 5
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
from research.shadow_evidence import (  # noqa: E402
    append_shadow_evidence,
    ledger_path,
    load_shadow_evidence_records,
    seed_records_from_scored_trades,
    summarize_shadow_evidence,
)
from scripts.validate_scoring_metrics import _load_trade_frame  # noqa: E402

LOG = get_logger("seed_prob_rank_shadow_evidence_from_cf")


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
    parser.add_argument(
        "--model-id",
        type=str,
        default="lgbm_ret_40d_fwd_2c9efe271d",
    )
    parser.add_argument("--top-n", type=int, default=5)
    parser.add_argument("--min-cohort", type=int, default=8)
    parser.add_argument("--max-days", type=int, default=40)
    parser.add_argument(
        "--replace-cf-rows",
        action="store_true",
        help="Drop prior source=cf_day_cohort rows before appending",
    )
    args = parser.parse_args(argv)
    setup_logging()

    scored_path = Path(args.scored)
    if not scored_path.is_file():
        LOG.error("Scored panel missing: %s", scored_path)
        return 2

    trades = _load_trade_frame(args.run_id)
    scored = pd.read_parquet(scored_path)
    # Prefer latest retrain scores when available
    retrain = (
        SKILL_DIR
        / "validation_artifacts"
        / "prob_rank_control_retrain"
        / f"scored_features_{args.model_id}.parquet"
    )
    if retrain.is_file():
        scored = pd.concat([scored, pd.read_parquet(retrain)], ignore_index=True)
        scored = scored.drop_duplicates(subset=["ticker", "asof_date"], keep="last")
        LOG.info("Merged retrain scores from %s", retrain.name)

    merged = attach_scores_to_trades(trades, scored)
    records = seed_records_from_scored_trades(
        merged,
        top_n=args.top_n,
        model_id=args.model_id,
        min_cohort=args.min_cohort,
        max_days=args.max_days,
    )
    if not records:
        LOG.error("No day cohorts met min_cohort=%s", args.min_cohort)
        return 3

    path = ledger_path(SKILL_DIR)
    if args.replace_cf_rows and path.is_file():
        kept = [
            r
            for r in load_shadow_evidence_records(SKILL_DIR)
            if r.get("source") != "cf_day_cohort"
        ]
        path.write_text(
            "".join(json.dumps(r, default=str) + "\n" for r in kept),
            encoding="utf-8",
        )
        LOG.info("Cleared prior cf_day_cohort rows; kept %s live/other rows", len(kept))

    for rec in records:
        append_shadow_evidence(SKILL_DIR, rec)

    summary = summarize_shadow_evidence(load_shadow_evidence_records(SKILL_DIR))
    # Split live vs CF for clarity
    all_rows = load_shadow_evidence_records(SKILL_DIR)
    cf_rows = [r for r in all_rows if r.get("source") == "cf_day_cohort"]
    live_rows = [r for r in all_rows if r.get("source") != "cf_day_cohort"]
    out = {
        "seeded_n": len(records),
        "cf_summary": summarize_shadow_evidence(cf_rows),
        "live_summary": summarize_shadow_evidence(live_rows),
        "all_summary": summary,
        "ledger": str(path),
        "model_id": args.model_id,
        "note": "CF day cohorts bootstrap multi-name overlap; live scans still required before live mode",
    }
    out_path = path.parent / "cf_seed_summary.json"
    out_path.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(json.dumps(out, indent=2))
    LOG.info("Seeded %s CF day rows -> %s", len(records), path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
