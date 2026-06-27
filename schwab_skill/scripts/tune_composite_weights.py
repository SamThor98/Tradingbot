#!/usr/bin/env python3
"""Grid-search composite stack weights against candidates or realized trades."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

SKILL_DIR = Path(__file__).resolve().parent.parent
ARTIFACT_DIR = SKILL_DIR / "validation_artifacts"
DEFAULT_CSV = ARTIFACT_DIR / "scoring_audit_dataset.csv"
FALLBACK_CSV = ARTIFACT_DIR / "advisory_dataset_latest.csv"
OUT_CANDIDATES = ARTIFACT_DIR / "composite_weight_recommendation.json"
OUT_TRADES = ARTIFACT_DIR / "composite_weight_recommendation_trades.json"

if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

from core.scoring_audit_builder import enrich_with_live_score_stack, has_live_stack  # noqa: E402
from core.scoring_metrics import (  # noqa: E402
    assign_era,
    component_ic_table,
    enrich_candidate_scores,
    load_trade_chunks_frame,
    optimize_composite_weights,
    pick_primary_horizon,
    prepare_trade_frame_for_tuning,
)


def _resolve_csv(path: str) -> Path | None:
    if path:
        candidate = Path(path)
        return candidate if candidate.exists() else None
    for p in (DEFAULT_CSV, ARTIFACT_DIR / "scoring_audit_dataset_full.csv", FALLBACK_CSV):
        if p.exists():
            return p
    return None


def _load_candidate_frame(csv_path: Path) -> pd.DataFrame:
    df = pd.read_csv(csv_path)
    df["entry_date"] = pd.to_datetime(df["entry_date"], errors="coerce")
    df = df.dropna(subset=["entry_date", "signal_score"]).copy()
    if not has_live_stack(df):
        from config import get_stage2_52w_pct

        df = enrich_candidate_scores(df, stage2_floor=float(get_stage2_52w_pct(SKILL_DIR)))
        df["score_stack_source"] = "proxy"
    elif "score_stack_source" not in df.columns:
        df["score_stack_source"] = "live"
        df = enrich_with_live_score_stack(df, skill_dir=SKILL_DIR)
    df["era"] = assign_era(df["entry_date"])
    from core.scoring_metrics import reapply_composite_scores

    df = reapply_composite_scores(df, skill_dir=SKILL_DIR)
    from core.scoring_rank_v2 import enrich_dataframe_rank_v2

    df = enrich_dataframe_rank_v2(df, skill_dir=SKILL_DIR)
    return df


def _apply_env_to_file(env_path: Path, env_map: dict[str, Any]) -> None:
    if not env_path.exists():
        return
    try:
        current = json.loads(env_path.read_text(encoding="utf-8"))
    except Exception:
        current = {}
    if not isinstance(current, dict):
        current = {}
    for key, val in env_map.items():
        current[str(key)] = str(val)
    env_path.write_text(json.dumps(current, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Tune composite score weights via offline IC search.")
    parser.add_argument("--source", choices=("candidates", "trades"), default="candidates")
    parser.add_argument("--csv", default="", help="Audit CSV path (candidates source)")
    parser.add_argument("--run-id", default="scoring_trades_v2", help="Chunk run id (trades source)")
    parser.add_argument("--out", default="", help="Recommendation JSON path")
    parser.add_argument(
        "--apply-env",
        default="",
        help="Merge recommended env into this JSON file (e.g. scripts/scoring_trade_sample_env.json)",
    )
    args = parser.parse_args()

    if args.source == "candidates":
        csv_path = _resolve_csv(args.csv)
        if csv_path is None:
            print("FAIL: no scoring audit CSV — run build_scoring_audit_dataset.py first")
            return 1
        df = _load_candidate_frame(csv_path)
        dataset_ref = str(csv_path)
        profile = "candidates"
        min_era_wins = 3
        min_era_rows = 40
        min_ic_lift = 0.01
        min_rows = 50
        out_path = Path(args.out or OUT_CANDIDATES)
    else:
        try:
            raw = load_trade_chunks_frame(args.run_id, skill_dir=SKILL_DIR)
            df = prepare_trade_frame_for_tuning(raw, skill_dir=SKILL_DIR)
        except (FileNotFoundError, ValueError) as exc:
            print(f"FAIL: {exc}")
            return 1
        dataset_ref = f"chunks:{args.run_id}"
        profile = "trades"
        min_era_wins = 1
        min_era_rows = 8
        min_ic_lift = 0.0
        min_rows = 30
        out_path = Path(args.out or OUT_TRADES)

    horizon_key, y_col, ret_col = pick_primary_horizon(df, args.source)
    work = df.dropna(subset=[y_col, ret_col]).copy()
    if len(work) < min_rows:
        print(f"FAIL: only {len(work)} labeled rows for {horizon_key} (need {min_rows})")
        return 1

    ic_table = component_ic_table(work, y_col=y_col, ret_col=ret_col)
    result = optimize_composite_weights(
        work,
        y_col=y_col,
        ret_col=ret_col,
        min_era_wins=min_era_wins,
        min_era_rows=min_era_rows,
        min_ic_lift=min_ic_lift,
        profile=profile,
    )
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": args.source,
        "dataset": dataset_ref,
        "score_stack_source": str(df.get("score_stack_source", pd.Series(["unknown"])).iloc[0]),
        "primary_horizon": horizon_key,
        "y_col": y_col,
        "ret_col": ret_col,
        "row_count": int(len(work)),
        "component_ic": ic_table,
        **result,
    }

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    rec = result.get("recommended") or {}
    env = result.get("recommended_env") or {}
    print(f"Source: {args.source}")
    print(f"Horizon: {horizon_key} ({len(work)} rows)")
    print(f"Baseline signal IC: {result.get('baseline_signal_ic')}")
    print(f"Best composite IC: {rec.get('spearman_ic')} (lift {rec.get('ic_lift_vs_signal')})")
    print(f"Beats rank v2: {rec.get('beats_v2')} (v2 IC {rec.get('rank_score_v2_ic')})")
    print(f"Era wins: {rec.get('era_wins')}/{rec.get('era_total')}")
    print(f"Promote defaults: {result.get('promote_recommended_defaults')}")
    print("Recommended env:")
    for key, val in env.items():
        print(f"  {key}={val}")
    print(f"Wrote {out_path}")

    if args.apply_env and env:
        env_path = Path(args.apply_env)
        _apply_env_to_file(env_path, env)
        print(f"Merged recommended env into {env_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
