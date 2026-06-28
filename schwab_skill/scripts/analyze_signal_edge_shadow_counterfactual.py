#!/usr/bin/env python3
"""Counterfactual for live signal-edge shadow rules on realized trades (P0).

Simulates the rank-filter shadow thresholds configured in ``config.py``
(composite p50, rank_v2 p70, signal p70) on enriched ``control_legacy_aug``
trades. Stage 2 shadow tighten cannot be replayed from trade chunks (no
entry-time 52w/SMA fields) — report notes that gap.

Usage (from schwab_skill/):
  python scripts/analyze_signal_edge_shadow_counterfactual.py
  python scripts/analyze_signal_edge_shadow_counterfactual.py --run-id control_legacy_aug
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

from config import (  # noqa: E402
    get_rank_filter_shadow_min_percentile_composite,
    get_rank_filter_shadow_min_percentile_rank_v2,
    get_rank_filter_shadow_min_percentile_signal,
)
from scripts.analyze_rank_filter_counterfactual import (  # noqa: E402
    DEFAULT_RUN_ID,
    OVERLAP_ERAS,
    _cohort_stats,
    _era_pf_from_df,
    _hold_days_map,
    _merge_hold_days,
    _pick_recommendation,
)
from scripts.validate_scoring_metrics import _load_trade_frame  # noqa: E402

ART = SKILL_DIR / "validation_artifacts"


def _quantile_threshold(series: pd.Series, min_pct: int) -> float | None:
    scores = pd.to_numeric(series, errors="coerce").dropna()
    if len(scores) < 3:
        return None
    return float(scores.quantile(min_pct / 100.0))


def _shadow_specs(skill_dir: Path) -> list[tuple[str, int, str]]:
    return [
        (
            "composite_score",
            get_rank_filter_shadow_min_percentile_composite(skill_dir),
            "composite_only",
        ),
        (
            "rank_score_v2",
            get_rank_filter_shadow_min_percentile_rank_v2(skill_dir),
            "rank_v2_only",
        ),
        (
            "signal_score",
            get_rank_filter_shadow_min_percentile_signal(skill_dir),
            "signal_only",
        ),
    ]


def _simulate_shadow_rules(df: pd.DataFrame, skill_dir: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    specs = _shadow_specs(skill_dir)
    thresholds: dict[str, Any] = {}
    drop_masks: dict[str, pd.Series] = {}

    for col, min_pct, label in specs:
        if col not in df.columns:
            thresholds[col] = {"skipped": True, "reason": "column_missing"}
            continue
        threshold = _quantile_threshold(df[col], min_pct)
        if threshold is None:
            thresholds[col] = {"skipped": True, "reason": "insufficient_scores", "min_percentile": min_pct}
            continue
        scores = pd.to_numeric(df[col], errors="coerce")
        drop_masks[label] = scores < threshold
        thresholds[col] = {
            "min_percentile": min_pct,
            "threshold": round(threshold, 4),
            "would_drop": int(drop_masks[label].sum()),
        }

    any_drop = pd.Series(False, index=df.index)
    for mask in drop_masks.values():
        any_drop = any_drop | mask.fillna(False)

    baseline = _cohort_stats(df)
    baseline_overlap_mean, _, _ = _era_pf_from_df(df, OVERLAP_ERAS)
    rows: list[dict[str, Any]] = []

    scenarios: list[tuple[str, pd.Series]] = [(label, mask) for label, mask in drop_masks.items()]
    scenarios.append(("shadow_any_rule", any_drop))

    for scenario, drop_mask in scenarios:
        kept = df[~drop_mask.fillna(False)]
        if len(kept) < 30:
            continue
        cohort = _cohort_stats(kept)
        overlap_mean, overlap_worst, _ = _era_pf_from_df(kept, OVERLAP_ERAS)
        rows.append(
            {
                "scenario": scenario,
                "retention_pct": round(100 * len(kept) / len(df), 1),
                "would_drop": int(drop_mask.sum()),
                "pf_all": cohort["pf"],
                "overlap_pf_mean": overlap_mean,
                "overlap_worst_pf": overlap_worst,
                "early_stopout_pct": cohort["early_stopout_pct"],
                "winner_21_40d_pct": cohort["winner_21_40d_pct"],
                "delta_overlap_pf_mean": round(overlap_mean - baseline_overlap_mean, 4),
                "delta_early_stopout_pp": round(
                    cohort["early_stopout_pct"] - baseline["early_stopout_pct"], 2
                ),
            }
        )

    meta = {
        "thresholds": thresholds,
        "stage2_shadow_note": (
            "Stage 2 shadow (tighter 52w pct / SMA uptrend) applies at Stage A and "
            "is not replayable from trade chunks — monitor via live scan diagnostics "
            "(stage2_shadow_would_filter)."
        ),
    }
    return rows, meta


def _load_offline_rank_rows(run_id: str) -> list[dict[str, Any]]:
    path = ART / f"rank_filter_counterfactual_{run_id}.json"
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    return list(payload.get("filters") or [])


def _cross_check(rows: list[dict[str, Any]], offline_rows: list[dict[str, Any]]) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    for scenario, col, pct in (
        ("composite_only", "composite_score", 50),
        ("rank_v2_only", "rank_score_v2", 70),
        ("signal_only", "signal_score", 70),
    ):
        shadow = next((r for r in rows if r.get("scenario") == scenario), None)
        offline = next(
            (
                r
                for r in offline_rows
                if r.get("score_column") == col and r.get("min_percentile") == pct
            ),
            None,
        )
        if not shadow or not offline:
            checks.append({"scenario": scenario, "status": "missing_data"})
            continue
        delta_pf = abs(
            float(shadow.get("overlap_pf_mean") or 0) - float(offline.get("overlap_pf_mean") or 0)
        )
        checks.append(
            {
                "scenario": scenario,
                "status": "aligned" if delta_pf < 0.02 else "drift",
                "shadow_overlap_pf": shadow.get("overlap_pf_mean"),
                "offline_overlap_pf": offline.get("overlap_pf_mean"),
                "delta_pf": round(delta_pf, 4),
            }
        )
    return {"checks": checks}


def main() -> int:
    parser = argparse.ArgumentParser(description="Signal-edge shadow counterfactual on trade chunks")
    parser.add_argument("--run-id", default=DEFAULT_RUN_ID)
    args = parser.parse_args()

    df = _load_trade_frame(args.run_id)
    hold_map = _hold_days_map(args.run_id)
    df = _merge_hold_days(df, hold_map)
    baseline = _cohort_stats(df)
    rows, meta = _simulate_shadow_rules(df, SKILL_DIR)
    offline_rows = _load_offline_rank_rows(args.run_id)
    cross_check = _cross_check(rows, offline_rows)

    any_row = next((r for r in rows if r.get("scenario") == "shadow_any_rule"), None)
    recommendation = _pick_recommendation(
        [
            {
                "min_percentile": 0,
                "score_column": "shadow_any_rule",
                "delta_overlap_pf_mean": (any_row or {}).get("delta_overlap_pf_mean"),
                "retention_pct": (any_row or {}).get("retention_pct"),
                "delta_early_stopout_pp": (any_row or {}).get("delta_early_stopout_pp"),
            },
            *[
                {
                    "min_percentile": 50,
                    "score_column": r.get("scenario"),
                    "delta_overlap_pf_mean": r.get("delta_overlap_pf_mean"),
                    "retention_pct": r.get("retention_pct"),
                    "delta_early_stopout_pp": r.get("delta_early_stopout_pp"),
                }
                for r in rows
                if r.get("scenario") != "shadow_any_rule"
            ],
        ],
        baseline,
    )

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "run_id": args.run_id,
        "baseline": baseline,
        "baseline_overlap": {
            "eras": list(OVERLAP_ERAS),
            "pf_mean": _era_pf_from_df(df, OVERLAP_ERAS)[0],
            "worst_pf": _era_pf_from_df(df, OVERLAP_ERAS)[1],
        },
        "shadow_config": meta,
        "scenarios": rows,
        "offline_cross_check": cross_check,
        "recommendation": recommendation,
    }

    out_json = ART / f"signal_edge_shadow_counterfactual_{args.run_id}.json"
    out_md = ART / f"signal_edge_shadow_counterfactual_{args.run_id}.md"
    out_json.write_text(json.dumps(report, indent=2), encoding="utf-8")

    lines = [
        f"# Signal-edge shadow counterfactual ({args.run_id})",
        "",
        f"Generated: {report['generated_at']}",
        "",
        "## Baseline",
        f"- Trades: {baseline.get('n')}",
        f"- PF: {baseline.get('pf')}",
        f"- Early stop-outs: {baseline.get('early_stopout_pct')}%",
        f"- 21-40d winners: {baseline.get('winner_21_40d_pct')}%",
        "",
        "## Shadow scenarios",
        "| scenario | retention | would_drop | overlap PF | d overlap PF | d early stops pp |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for row in rows:
        lines.append(
            f"| {row['scenario']} | {row['retention_pct']}% | {row['would_drop']} | "
            f"{row.get('overlap_pf_mean')} | {row.get('delta_overlap_pf_mean')} | "
            f"{row.get('delta_early_stopout_pp')} |"
        )
    lines.extend(
        [
            "",
            "## Recommendation",
            f"- action: {recommendation.get('action')}",
            f"- reason: {recommendation.get('reason')}",
            "",
            meta["stage2_shadow_note"],
        ]
    )
    out_md.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"Wrote {out_json}")
    print(f"Wrote {out_md}")
    print(f"Recommendation: {recommendation.get('action')} — {recommendation.get('reason')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
