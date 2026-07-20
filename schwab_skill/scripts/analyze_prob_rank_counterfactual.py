#!/usr/bin/env python3
"""Counterfactual: attach prob-rank scores to frozen multi-era trades.

Compares baseline vs top-N (or percentile) selection vs rank_v2 p75 control.

Requires a scored feature parquet (from train_prob_rank_model / predict) with
ticker, asof_date, expected_return_40d — or a model dir + feature dataset.

Examples:
  python scripts/analyze_prob_rank_counterfactual.py \\
      --run-id control_legacy_aug \\
      --scored-features path/to/scored.parquet \\
      --top-n 5
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

SKILL_DIR = Path(__file__).resolve().parent.parent
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

from logger_setup import get_logger, setup_logging  # noqa: E402
from research.counterfactual import run_prob_rank_counterfactual  # noqa: E402
from research.infer import predict_frame  # noqa: E402
from research.train import load_model_artifact  # noqa: E402
from scripts.validate_scoring_metrics import _load_trade_frame  # noqa: E402

LOG = get_logger("analyze_prob_rank_counterfactual")
ART = SKILL_DIR / "validation_artifacts"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", type=str, default="control_legacy_aug")
    parser.add_argument("--scored-features", type=str, default=None, help="Parquet with scores")
    parser.add_argument("--model-dir", type=str, default=None, help="research_store/models/<id>")
    parser.add_argument("--features", type=str, default=None, help="Feature/dataset parquet to score")
    parser.add_argument("--top-n", type=int, default=5)
    parser.add_argument("--min-percentile", type=float, default=None)
    parser.add_argument("--control-percentile", type=float, default=75.0)
    parser.add_argument("--out", type=str, default=None)
    args = parser.parse_args(argv)
    setup_logging()

    try:
        trades = _load_trade_frame(args.run_id)
    except Exception as exc:
        LOG.error("Failed to load trades for %s: %s", args.run_id, exc)
        return 2

    if args.scored_features:
        scored = pd.read_parquet(args.scored_features)
    elif args.model_dir and args.features:
        artifact = load_model_artifact(Path(args.model_dir))
        feats = pd.read_parquet(args.features)
        scored = predict_frame(artifact, feats)
    else:
        LOG.error("Provide --scored-features OR (--model-dir and --features)")
        return 2

    result = run_prob_rank_counterfactual(
        trades,
        scored,
        top_n=args.top_n,
        min_percentile=args.min_percentile,
        control_percentile=args.control_percentile,
    )
    result["run_id"] = args.run_id
    result["created_at_utc"] = datetime.now(timezone.utc).isoformat()

    out_path = Path(args.out) if args.out else ART / f"prob_rank_counterfactual_{args.run_id}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    LOG.info("Wrote %s", out_path)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
