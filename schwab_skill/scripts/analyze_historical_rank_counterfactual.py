#!/usr/bin/env python3
"""Counterfactual: historical blend rank vs the current live selection stack.

Scores the frozen multi-era trade frame with ``core.scoring_historical`` — the
same module the scanner uses — and compares selection arms era by era:

  baseline               every trade in the frame
  current_live_p76       rank-v2 p76 filter (what RANK_FILTER_V2_MODE=live does)
  current_live_p76_top5  rank-v2 p76, then top-N/day by signal_score (live sort)
  historical_p76         historical_rank p76 filter
  historical_top5        top-N/day by historical_rank

Promotion requires the historical arm to clear PF mean >= 1.20 and worst-era
PF >= 1.00 *and* beat ``current_live_p76_top5`` on both. Anything short of that
keeps HISTORICAL_RANK_MODE=shadow.

Writes validation_artifacts/historical_rank/counterfactual_<run_id>.json

Usage (from schwab_skill/):
  python scripts/analyze_historical_rank_counterfactual.py
  python scripts/analyze_historical_rank_counterfactual.py --run-id control_legacy_aug --top-n 5
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

SKILL_DIR = Path(__file__).resolve().parent.parent
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

from core.scoring_historical import (  # noqa: E402
    compute_historical_rank,
    load_blend_artifact,
)
from research.counterfactual import (  # noqa: E402
    _era_pf_mean_worst,
    _safe_pf,
    select_by_percentile,
    select_top_n_by_day,
)
from scripts.validate_scoring_metrics import _load_trade_frame  # noqa: E402

ART = SKILL_DIR / "validation_artifacts" / "historical_rank"
PROMOTION_PF_MEAN = 1.20
PROMOTION_WORST_ERA_PF = 1.00
CURRENT_LIVE_PERCENTILE = 76.0
DEFAULT_RUN_ID = "control_legacy_aug"


def _score_trades(trades: pd.DataFrame, artifact: dict[str, Any]) -> pd.DataFrame:
    """Attach ``historical_rank`` to each frozen trade row.

    The trade frame already carries the live component columns
    (``close_vs_sma200_pct``, ``pts_volume``, ``pts_52w``), so rows are scored
    through the runtime path rather than a reimplementation.
    """
    work = trades.copy()
    records = work.to_dict("records")
    scores: list[float | None] = []
    for row in records:
        row_for_score = dict(row)
        # pts_* live under score_components on a real signal row.
        row_for_score["score_components"] = {
            "pts_volume": row.get("pts_volume"),
            "pct_from_52w_high": row.get("pct_from_52w_high"),
        }
        block = compute_historical_rank(row_for_score, artifact)
        scores.append(None if block is None else float(block["score"]))
    work["historical_rank"] = scores
    return work


def _by_era(df: pd.DataFrame) -> dict[str, Any]:
    if df.empty or "era" not in df.columns:
        return {}
    return {
        str(era): {"n": int(len(grp)), "pf": _safe_pf(grp["net_return"])}
        for era, grp in df.groupby("era")
    }


def _arm(name: str, df: pd.DataFrame, total: int, *, selection: str) -> dict[str, Any]:
    pf_mean, worst = _era_pf_mean_worst(df)
    return {
        "arm": name,
        "selection": selection,
        "n": int(len(df)),
        "retention": round(len(df) / max(1, total), 4),
        "pf_mean": pf_mean,
        "worst_era_pf": worst,
        "by_era": _by_era(df),
        "gates": {
            "pf_mean_ok": bool(pf_mean is not None and pf_mean >= PROMOTION_PF_MEAN),
            "worst_era_ok": bool(worst is not None and worst >= PROMOTION_WORST_ERA_PF),
        },
    }


def _beats(challenger: dict[str, Any], incumbent: dict[str, Any]) -> dict[str, Any]:
    c_pf, c_worst = challenger.get("pf_mean"), challenger.get("worst_era_pf")
    i_pf, i_worst = incumbent.get("pf_mean"), incumbent.get("worst_era_pf")
    pf_ok = c_pf is not None and i_pf is not None and c_pf > i_pf
    worst_ok = c_worst is not None and i_worst is not None and c_worst > i_worst
    return {
        "vs": incumbent.get("arm"),
        "pf_mean_delta": None if (c_pf is None or i_pf is None) else round(c_pf - i_pf, 4),
        "worst_era_delta": None if (c_worst is None or i_worst is None) else round(c_worst - i_worst, 4),
        "beats_pf_mean": bool(pf_ok),
        "beats_worst_era": bool(worst_ok),
        "beats_incumbent": bool(pf_ok and worst_ok),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--run-id", default=DEFAULT_RUN_ID)
    parser.add_argument("--top-n", type=int, default=5, help="Top-N per entry day for the sorted arms")
    parser.add_argument("--percentile", type=float, default=76.0, help="Percentile cut for the historical arm")
    parser.add_argument("--artifact", default=None, help="Override blend artifact path")
    parser.add_argument("--out", default=None)
    args = parser.parse_args(argv)

    artifact = load_blend_artifact(SKILL_DIR, args.artifact)
    if artifact is None:
        print("FAIL: no historical rank blend artifact. Run scripts/fit_historical_rank_blend.py first.")
        return 2

    try:
        trades = _load_trade_frame(args.run_id)
    except Exception as exc:
        print(f"FAIL: could not load trades for {args.run_id}: {exc}")
        return 2

    scored = _score_trades(trades, artifact)
    total = len(scored)
    coverage = float(scored["historical_rank"].notna().mean()) if total else 0.0
    print(f"Trades: {total} | historical_rank coverage: {coverage:.4f} | model {artifact.get('model_id')}")

    baseline = _arm("baseline", scored, total, selection="all trades")

    live_p76 = select_by_percentile(scored, score_col="rank_score_v2", min_percentile=CURRENT_LIVE_PERCENTILE)
    current_p76 = _arm(
        "current_live_p76",
        live_p76,
        total,
        selection=f"rank_score_v2 >= p{CURRENT_LIVE_PERCENTILE:g}",
    )
    live_top = select_top_n_by_day(live_p76, score_col="signal_score", top_n=args.top_n)
    current_stack = _arm(
        "current_live_p76_top5",
        live_top,
        total,
        selection=f"rank_score_v2 >= p{CURRENT_LIVE_PERCENTILE:g}, then top-{args.top_n}/day by signal_score",
    )

    hist_pct = select_by_percentile(scored, score_col="historical_rank", min_percentile=args.percentile)
    hist_p76 = _arm(
        "historical_p76",
        hist_pct,
        total,
        selection=f"historical_rank >= p{args.percentile:g}",
    )
    hist_top = select_top_n_by_day(scored, score_col="historical_rank", top_n=args.top_n)
    hist_topn = _arm(
        "historical_top5",
        hist_top,
        total,
        selection=f"top-{args.top_n}/day by historical_rank",
    )

    arms = [baseline, current_p76, current_stack, hist_p76, hist_topn]
    print("\nArm                     n      ret     PF mean   worst era  gates")
    for arm in arms:
        gates = "pass" if arm["gates"]["pf_mean_ok"] and arm["gates"]["worst_era_ok"] else "fail"
        print(
            f"  {arm['arm']:22s} {arm['n']:6d}  {arm['retention']:.3f}   "
            f"{str(arm['pf_mean']):>8s}   {str(arm['worst_era_pf']):>8s}  {gates}"
        )

    comparisons = {
        "historical_p76": _beats(hist_p76, current_stack),
        "historical_top5": _beats(hist_topn, current_stack),
    }
    promote = {
        name: bool(
            arm["gates"]["pf_mean_ok"] and arm["gates"]["worst_era_ok"] and comparisons[name]["beats_incumbent"]
        )
        for name, arm in (("historical_p76", hist_p76), ("historical_top5", hist_topn))
    }
    verdict = "promote_candidate" if any(promote.values()) else "keep_shadow"

    print("\nVs current_live_p76_top5:")
    for name, cmp in comparisons.items():
        print(
            f"  {name:18s} dPF {cmp['pf_mean_delta']} dWorst {cmp['worst_era_delta']} "
            f"beats={cmp['beats_incumbent']} promote={promote[name]}"
        )
    print(f"\nVERDICT: {verdict}")
    if verdict == "keep_shadow":
        print("  HISTORICAL_RANK_MODE stays shadow; live selection unchanged.")

    result = {
        "run_id": args.run_id,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "model_id": artifact.get("model_id"),
        "artifact": artifact.get("_path"),
        "weights": artifact.get("weights"),
        "n_trades": total,
        "historical_rank_coverage": round(coverage, 4),
        "gates": {"pf_mean_min": PROMOTION_PF_MEAN, "worst_era_pf_min": PROMOTION_WORST_ERA_PF},
        "top_n": args.top_n,
        "percentile": args.percentile,
        "arms": {arm["arm"]: arm for arm in arms},
        "comparisons": comparisons,
        "promote": promote,
        "verdict": verdict,
    }

    out_path = Path(args.out) if args.out else ART / f"counterfactual_{args.run_id}.json"
    if not out_path.is_absolute():
        out_path = SKILL_DIR / out_path
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(f"\nWrote {out_path.relative_to(SKILL_DIR)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
