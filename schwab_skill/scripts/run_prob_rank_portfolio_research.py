#!/usr/bin/env python3
"""Equal-weight / dynamic top-N portfolio research for prob-rank.

Example:
  python scripts/run_prob_rank_portfolio_research.py \\
      --run-id control_legacy_aug \\
      --model-dir research_store/models/<id> \\
      --features research_store/datasets/<id>.parquet \\
      --sizing equal --top-n 5
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

from experiment_registry import append_registry_event  # noqa: E402
from logger_setup import get_logger, setup_logging  # noqa: E402
from research.infer import predict_frame  # noqa: E402
from research.portfolio import run_portfolio_research  # noqa: E402
from research.train import load_model_artifact  # noqa: E402
from scripts.validate_scoring_metrics import _load_trade_frame  # noqa: E402

LOG = get_logger("run_prob_rank_portfolio_research")
ART = SKILL_DIR / "validation_artifacts"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", type=str, default="control_legacy_aug")
    parser.add_argument("--scored-features", type=str, default=None)
    parser.add_argument("--model-dir", type=str, default=None)
    parser.add_argument("--features", type=str, default=None)
    parser.add_argument("--top-n", type=int, default=5)
    parser.add_argument("--sizing", type=str, default="equal", choices=["equal", "edge_vol"])
    parser.add_argument("--max-position", type=float, default=0.25)
    parser.add_argument("--max-sector", type=float, default=0.40)
    parser.add_argument("--kelly-cap", type=float, default=0.25)
    parser.add_argument("--vol-target", type=float, default=None)
    parser.add_argument("--control-percentile", type=float, default=75.0)
    parser.add_argument("--register", action="store_true", help="Append rank_model_experiment to registry")
    parser.add_argument("--out", type=str, default=None)
    args = parser.parse_args(argv)
    setup_logging()

    try:
        trades = _load_trade_frame(args.run_id)
    except Exception as exc:
        LOG.error("Failed to load trades: %s", exc)
        return 2

    if args.scored_features:
        scored = pd.read_parquet(args.scored_features)
    elif args.model_dir and args.features:
        artifact = load_model_artifact(Path(args.model_dir))
        scored = predict_frame(artifact, pd.read_parquet(args.features))
    else:
        LOG.error("Provide --scored-features OR (--model-dir and --features)")
        return 2

    result = run_portfolio_research(
        trades,
        scored,
        top_n=args.top_n,
        sizing_mode=args.sizing,
        max_position=args.max_position,
        max_sector=args.max_sector,
        kelly_cap=args.kelly_cap,
        vol_target=args.vol_target,
        control_percentile=args.control_percentile,
    )
    result["run_id"] = args.run_id
    result["created_at_utc"] = datetime.now(timezone.utc).isoformat()

    out_path = Path(args.out) if args.out else ART / f"prob_rank_portfolio_{args.run_id}_{args.sizing}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    LOG.info("Wrote %s", out_path)

    if args.register:
        ew = result.get("equal_weight_top_n") or {}
        append_registry_event(
            event_type="rank_model_experiment",
            target="prob_rank_portfolio",
            decision="recorded",
            rationale=[
                f"sizing={args.sizing}",
                f"top_n={args.top_n}",
                f"ew_pf_mean={ew.get('pf_mean_eras')}",
                f"ew_worst={ew.get('worst_era_pf')}",
            ],
            gates={
                "pf_mean": ew.get("pf_mean_eras"),
                "worst_era_pf": ew.get("worst_era_pf"),
                "retention": result.get("retention"),
            },
            metadata={"artifact": str(out_path), "run_id": args.run_id, "sizing": args.sizing},
            skill_dir=SKILL_DIR,
        )
        LOG.info("Registry event appended (rank_model_experiment)")

    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
