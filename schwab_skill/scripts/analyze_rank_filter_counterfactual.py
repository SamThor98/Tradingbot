#!/usr/bin/env python3
"""Counterfactual rank filter on realized trades (P0 scoring/ranking path).

Loads augmented multi-era chunks, enriches score stack, then simulates keeping
only trades above score quantile thresholds. Reports PF, early stop-out rate,
and 21-40d winner retention vs baseline.

Usage (from schwab_skill/):
  python scripts/analyze_rank_filter_counterfactual.py
  python scripts/analyze_rank_filter_counterfactual.py --run-id control_legacy_aug
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

SKILL_DIR = Path(__file__).resolve().parent.parent
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

from scripts.phase2_common import ERA_BOUNDS, load_trades  # noqa: E402
from scripts.validate_scoring_metrics import _load_trade_frame  # noqa: E402

ART = SKILL_DIR / "validation_artifacts"
DEFAULT_RUN_ID = "control_legacy_aug"
SCORE_COLS = ("rank_score_v2", "rank_score", "composite_score", "signal_score")
MIN_PERCENTILES = (0, 30, 40, 50, 60, 70, 80)
OVERLAP_ERAS = ("crash_recovery", "bear_rates", "recent_current")


def _hold_days_map(run_id: str) -> dict[tuple[str, str, str], int]:
    """Map (era, ticker, entry_iso) -> hold_days from phase2 chunks."""
    out: dict[tuple[str, str, str], int] = {}
    for t in load_trades(run_id):
        key = (t.era, str(t.ticker or "").upper(), t.entry_date.strftime("%Y-%m-%d"))
        out[key] = t.hold_days
    return out


def _merge_hold_days(df: pd.DataFrame, hold_map: dict[tuple[str, str, str], int]) -> pd.DataFrame:
    out = df.copy()
    out["entry_iso"] = pd.to_datetime(out["entry_date"]).dt.strftime("%Y-%m-%d")
    out["ticker"] = out["ticker"].astype(str).str.upper()
    out["hold_days"] = [
        hold_map.get((str(row.era), row.ticker, row.entry_iso), 0)
        for row in out.itertuples(index=False)
    ]
    return out


def _cohort_stats(df: pd.DataFrame) -> dict[str, Any]:
    if df.empty:
        return {"n": 0}
    early = df[(df["hold_days"] <= 20) & (df["net_return"] < 0)]
    mid_win = df[(df["hold_days"] >= 21) & (df["hold_days"] <= 40) & (df["net_return"] > 0)]
    short = df[df["hold_days"] <= 20]
    long = df[(df["hold_days"] >= 21) & (df["hold_days"] <= 40)]
    n = len(df)
    return {
        "n": n,
        "pf": _safe_pf(df["net_return"]),
        "early_stopouts": int(len(early)),
        "early_stopout_pct": round(100 * len(early) / n, 2),
        "winners_21_40d": int(len(mid_win)),
        "winner_21_40d_pct": round(100 * len(mid_win) / n, 2),
        "hold_lte20d_pf": _safe_pf(short["net_return"]),
        "hold_21_40d_pf": _safe_pf(long["net_return"]),
    }


def _safe_pf(series: pd.Series) -> float | None:
    wins = float(series[series > 0].sum())
    losses = float(-series[series <= 0].sum())
    if losses <= 0:
        return None if wins <= 0 else 99.0
    return round(wins / losses, 4)


def _era_pf_from_df(df: pd.DataFrame, eras: tuple[str, ...] | None = None) -> tuple[float, float, int]:
    selected = list(eras) if eras else list(ERA_BOUNDS.keys())
    pf_vals: list[float] = []
    for era in selected:
        era_df = df[df["era"] == era]
        if era_df.empty:
            continue
        pf = _safe_pf(era_df["net_return"])
        if pf is not None:
            pf_vals.append(float(pf))
    if not pf_vals:
        return 0.0, 0.0, 0
    return round(statistics.mean(pf_vals), 4), round(min(pf_vals), 4), len(pf_vals)


def _simulate_filters(df: pd.DataFrame) -> list[dict[str, Any]]:
    baseline = _cohort_stats(df)
    baseline_pf_all, baseline_worst, _ = _era_pf_from_df(df)
    baseline_overlap_mean, baseline_overlap_worst, _ = _era_pf_from_df(df, OVERLAP_ERAS)
    rows: list[dict[str, Any]] = []

    for col in SCORE_COLS:
        if col not in df.columns or df[col].notna().sum() < 50:
            continue
        scores = pd.to_numeric(df[col], errors="coerce")
        for min_pct in MIN_PERCENTILES:
            if min_pct == 0:
                filt = df
                threshold = None
            else:
                threshold = float(scores.quantile(min_pct / 100.0))
                filt = df[scores >= threshold]
            if len(filt) < 30:
                continue
            cohort = _cohort_stats(filt)
            pf_mean, worst, n_eras = _era_pf_from_df(filt)
            overlap_mean, overlap_worst, _ = _era_pf_from_df(filt, OVERLAP_ERAS)
            rows.append(
                {
                    "score_column": col,
                    "min_percentile": min_pct,
                    "threshold": round(threshold, 2) if threshold is not None else None,
                    "retention_pct": round(100 * len(filt) / len(df), 1),
                    "pf_all": cohort["pf"],
                    "pf_mean_eras": pf_mean,
                    "worst_era_pf": worst,
                    "overlap_pf_mean": overlap_mean,
                    "overlap_worst_pf": overlap_worst,
                    "early_stopout_pct": cohort["early_stopout_pct"],
                    "winner_21_40d_pct": cohort["winner_21_40d_pct"],
                    "delta_pf_vs_baseline": round((cohort["pf"] or 0) - (baseline["pf"] or 0), 4)
                    if cohort["pf"] is not None and baseline["pf"] is not None
                    else None,
                    "delta_overlap_pf_mean": round(overlap_mean - baseline_overlap_mean, 4),
                    "delta_early_stopout_pp": round(
                        cohort["early_stopout_pct"] - baseline["early_stopout_pct"], 2
                    ),
                }
            )
    rows.sort(
        key=lambda r: (
            float(r.get("delta_overlap_pf_mean") or -999),
            -float(r.get("early_stopout_pct") or 999),
        ),
        reverse=True,
    )
    return rows


def _pick_recommendation(rows: list[dict[str, Any]], baseline: dict[str, Any]) -> dict[str, Any]:
    if not rows:
        return {"action": "insufficient_data", "reason": "No score columns with enough rows."}
    for row in rows:
        if row["min_percentile"] == 0:
            continue
        if (
            (row.get("delta_overlap_pf_mean") or 0) >= 0.05
            and row.get("retention_pct", 0) >= 50
            and (row.get("delta_early_stopout_pp") or 0) <= -3
        ):
            return {
                "action": "shadow_rank_filter",
                "score_column": row["score_column"],
                "min_percentile": row["min_percentile"],
                "threshold": row["threshold"],
                "expected_overlap_pf_delta": row["delta_overlap_pf_mean"],
                "retention_pct": row["retention_pct"],
                "reason": "Improves overlap-era PF without excessive trade loss or early-stop churn.",
            }
    best = rows[0]
    return {
        "action": "no_rank_filter_yet",
        "best_seen": {
            "score_column": best["score_column"],
            "min_percentile": best["min_percentile"],
            "delta_overlap_pf_mean": best.get("delta_overlap_pf_mean"),
            "retention_pct": best.get("retention_pct"),
        },
        "reason": (
            "No threshold met shadow criteria (overlap PF +0.05, retention >=50%, early stops -3pp). "
            "Ranking signal is too weak on realized trades to filter safely."
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Rank-filter counterfactual on trade chunks")
    parser.add_argument("--run-id", default=DEFAULT_RUN_ID)
    args = parser.parse_args()

    df = _load_trade_frame(args.run_id)
    hold_map = _hold_days_map(args.run_id)
    df = _merge_hold_days(df, hold_map)
    baseline = _cohort_stats(df)
    rows = _simulate_filters(df)
    recommendation = _pick_recommendation(rows, baseline)

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "run_id": args.run_id,
        "baseline": baseline,
        "baseline_overlap": {
            "eras": list(OVERLAP_ERAS),
            **_dict_from_pf(df, OVERLAP_ERAS),
        },
        "filters": rows[:40],
        "recommendation": recommendation,
    }
    out_json = ART / f"rank_filter_counterfactual_{args.run_id}.json"
    out_md = ART / f"rank_filter_counterfactual_{args.run_id}.md"
    out_json.write_text(json.dumps(report, indent=2), encoding="utf-8")

    lines = [
        f"# Rank filter counterfactual — `{args.run_id}`",
        "",
        f"Generated: {report['generated_at']}",
        "",
        "## Baseline",
        f"- Trades: {baseline['n']} | PF: {baseline['pf']} | early stop-outs: {baseline['early_stopout_pct']}%",
        f"- 21-40d winners: {baseline['winners_21_40d']} ({baseline['winner_21_40d_pct']}%)",
        "",
        "## Top filter scenarios (overlap-era PF delta)",
        "| score | min_pct | retain% | overlap PF d | early stop dpp | PF all |",
        "|-------|---------|---------|--------------|----------------|--------|",
    ]
    for row in rows[:12]:
        lines.append(
            f"| {row['score_column']} | {row['min_percentile']} | {row['retention_pct']} | "
            f"{row.get('delta_overlap_pf_mean'):+.4f} | {row.get('delta_early_stopout_pp'):+.1f} | "
            f"{row.get('pf_all')} |"
        )
    lines.extend(["", f"## Recommendation: **{recommendation['action']}**", ""])
    if recommendation.get("reason"):
        lines.append(recommendation["reason"])
    out_md.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(json.dumps({"recommendation": recommendation, "json": str(out_json), "md": str(out_md)}, indent=2))
    print("\n" + out_md.read_text(encoding="utf-8"))
    return 0


def _dict_from_pf(df: pd.DataFrame, eras: tuple[str, ...]) -> dict[str, Any]:
    mean, worst, n = _era_pf_from_df(df, eras)
    sub = df[df["era"].isin(eras)]
    return {"n": len(sub), "pf_mean": mean, "worst_era_pf": worst, "era_count": n}


if __name__ == "__main__":
    raise SystemExit(main())
