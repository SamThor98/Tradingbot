#!/usr/bin/env python3
"""Early stop-out cohort analysis on realized trades (P0 entry quality).

Identifies what distinguishes <=20d losing exits from 21-40d winners on
augmented multi-era chunks. Primary binding constraint for signal-edge work.

Usage (from schwab_skill/):
  python scripts/analyze_early_stopout_cohorts.py
  python scripts/analyze_early_stopout_cohorts.py --run-id control_legacy_aug
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

from scripts.analyze_rank_filter_counterfactual import (  # noqa: E402
    DEFAULT_RUN_ID,
    _cohort_stats,
    _hold_days_map,
    _merge_hold_days,
    _safe_pf,
)
from scripts.phase2_common import load_trades  # noqa: E402
from scripts.validate_scoring_metrics import _load_trade_frame  # noqa: E402

ART = SKILL_DIR / "validation_artifacts"


def _merge_trade_metadata(df: pd.DataFrame, run_id: str) -> pd.DataFrame:
    meta: dict[tuple[str, str, str], dict[str, Any]] = {}
    for trade in load_trades(run_id):
        key = (trade.era, str(trade.ticker or "").upper(), trade.entry_date.strftime("%Y-%m-%d"))
        meta[key] = {
            "exit_reason": trade.exit_reason,
            "mfe": trade.mfe,
            "mae": trade.mae,
            "stop_pct": trade.stop_pct,
        }
    out = df.copy()
    out["entry_iso"] = pd.to_datetime(out["entry_date"]).dt.strftime("%Y-%m-%d")
    out["ticker"] = out["ticker"].astype(str).str.upper()
    for col in ("exit_reason", "mfe", "mae", "stop_pct"):
        out[col] = [
            meta.get((str(row.era), row.ticker, row.entry_iso), {}).get(col)
            for row in out.itertuples(index=False)
        ]
    return out


def _label_cohort(row: pd.Series) -> str:
    hold = int(row.get("hold_days") or 0)
    net = float(row.get("net_return") or 0.0)
    if hold <= 20 and net < 0:
        return "early_stop"
    if 21 <= hold <= 40 and net > 0:
        return "winner_21_40"
    return "other"


def _summarize_group(df: pd.DataFrame) -> dict[str, Any]:
    if df.empty:
        return {"n": 0}
    out: dict[str, Any] = {
        "n": int(len(df)),
        "pf": _safe_pf(df["net_return"]),
        "early_stopout_pct": round(
            100 * len(df[(df["hold_days"] <= 20) & (df["net_return"] < 0)]) / len(df),
            2,
        ),
    }
    if "signal_score" in df.columns:
        scores = pd.to_numeric(df["signal_score"], errors="coerce").dropna()
        if len(scores) >= 5:
            out["signal_score_mean"] = round(float(scores.mean()), 2)
            out["signal_score_median"] = round(float(scores.median()), 2)
    for col in ("mfe", "mae", "stop_pct"):
        if col not in df.columns:
            continue
        vals = pd.to_numeric(df[col], errors="coerce").dropna()
        if len(vals) >= 5:
            out[f"{col}_mean"] = round(float(vals.mean()), 4)
    if "exit_reason" in df.columns:
        reasons = df["exit_reason"].fillna("unknown").astype(str)
        out["exit_reason_counts"] = reasons.value_counts().to_dict()
    return out


def _era_breakdown(df: pd.DataFrame) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for era, era_df in df.groupby("era"):
        n = len(era_df)
        early = era_df[(era_df["hold_days"] <= 20) & (era_df["net_return"] < 0)]
        winners = era_df[(era_df["hold_days"] >= 21) & (era_df["hold_days"] <= 40) & (era_df["net_return"] > 0)]
        rows.append(
            {
                "era": str(era),
                "n": int(n),
                "early_stopouts": int(len(early)),
                "early_stopout_pct": round(100 * len(early) / n, 2) if n else 0.0,
                "winners_21_40d": int(len(winners)),
                "hold_lte20d_pf": _safe_pf(era_df[era_df["hold_days"] <= 20]["net_return"]),
                "hold_21_40d_pf": _safe_pf(
                    era_df[(era_df["hold_days"] >= 21) & (era_df["hold_days"] <= 40)]["net_return"]
                ),
            }
        )
    rows.sort(key=lambda r: str(r.get("era") or ""))
    return rows


def _signal_score_quartiles(df: pd.DataFrame) -> list[dict[str, Any]]:
    if "signal_score" not in df.columns:
        return []
    scores = pd.to_numeric(df["signal_score"], errors="coerce")
    valid = df[scores.notna()].copy()
    if len(valid) < 20:
        return []
    valid["signal_score"] = scores[scores.notna()]
    try:
        valid["quartile"] = pd.qcut(valid["signal_score"], 4, labels=["q1", "q2", "q3", "q4"], duplicates="drop")
    except ValueError:
        return []
    rows: list[dict[str, Any]] = []
    for quartile, sub in valid.groupby("quartile", observed=True):
        n = len(sub)
        early = sub[(sub["hold_days"] <= 20) & (sub["net_return"] < 0)]
        rows.append(
            {
                "quartile": str(quartile),
                "signal_score_range": [
                    round(float(sub["signal_score"].min()), 2),
                    round(float(sub["signal_score"].max()), 2),
                ],
                "n": int(n),
                "early_stopout_pct": round(100 * len(early) / n, 2) if n else 0.0,
                "pf": _safe_pf(sub["net_return"]),
            }
        )
    return rows


def _pick_recommendation(
    baseline: dict[str, Any],
    cohorts: dict[str, dict[str, Any]],
    quartiles: list[dict[str, Any]],
) -> dict[str, Any]:
    early = cohorts.get("early_stop") or {}
    winner = cohorts.get("winner_21_40") or {}
    early_reasons = early.get("exit_reason_counts") or {}
    winner_reasons = winner.get("exit_reason_counts") or {}
    early_trailing_share = 0.0
    if early.get("n"):
        early_trailing_share = round(
            100 * float(early_reasons.get("trailing_stop", 0)) / float(early["n"]),
            1,
        )
    winner_time_share = 0.0
    if winner.get("n"):
        winner_time_share = round(
            100 * float(winner_reasons.get("time_exit", 0)) / float(winner["n"]),
            1,
        )

    score_delta = None
    if early.get("signal_score_mean") is not None and winner.get("signal_score_mean") is not None:
        score_delta = round(float(winner["signal_score_mean"]) - float(early["signal_score_mean"]), 2)

    q_spread = None
    if len(quartiles) >= 2:
        q_spread = round(float(quartiles[-1]["early_stopout_pct"]) - float(quartiles[0]["early_stopout_pct"]), 2)

    action = "fix_entry_timing_not_rank_filter"
    reasons = [
        f"Early stop-outs {baseline.get('early_stopout_pct')}% of trades; "
        f"{early_trailing_share}% of early losses exit via trailing_stop.",
        f"21-40d winners {winner_time_share}% exit via time_exit (hold to plan).",
    ]
    if score_delta is not None and abs(score_delta) < 2.0:
        reasons.append(
            f"signal_score barely separates cohorts (delta {score_delta:+.2f}); rank filters won't fix early stops."
        )
    if q_spread is not None and abs(q_spread) < 5.0:
        reasons.append(f"Early-stop rate flat across signal_score quartiles (spread {q_spread:+.1f}pp).")
    mae_early = early.get("mae_mean")
    mae_winner = winner.get("mae_mean")
    if mae_early is not None and mae_winner is not None:
        reasons.append(
            f"Early stops show deeper initial drawdown (MAE {mae_early} vs {mae_winner})."
        )

    return {
        "action": action,
        "early_trailing_stop_share_pct": early_trailing_share,
        "winner_time_exit_share_pct": winner_time_share,
        "signal_score_delta_winner_minus_early": score_delta,
        "quartile_early_stop_spread_pp": q_spread,
        "reason": " ".join(reasons),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Early stop-out cohort analysis")
    parser.add_argument("--run-id", default=DEFAULT_RUN_ID)
    args = parser.parse_args()

    df = _load_trade_frame(args.run_id)
    hold_map = _hold_days_map(args.run_id)
    df = _merge_hold_days(df, hold_map)
    df = _merge_trade_metadata(df, args.run_id)

    df["cohort"] = df.apply(_label_cohort, axis=1)
    baseline = _cohort_stats(df)
    cohorts = {name: _summarize_group(sub) for name, sub in df.groupby("cohort")}

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "run_id": args.run_id,
        "baseline": baseline,
        "cohorts": cohorts,
        "by_era": _era_breakdown(df),
        "signal_score_quartiles": _signal_score_quartiles(df),
        "recommendation": _pick_recommendation(
            baseline,
            cohorts,
            _signal_score_quartiles(df),
        ),
    }

    out_json = ART / f"early_stopout_cohorts_{args.run_id}.json"
    out_md = ART / f"early_stopout_cohorts_{args.run_id}.md"
    out_json.write_text(json.dumps(report, indent=2), encoding="utf-8")

    early = cohorts.get("early_stop") or {}
    winner = cohorts.get("winner_21_40") or {}
    rec = report["recommendation"]
    lines = [
        f"# Early stop-out cohorts — `{args.run_id}`",
        "",
        f"Generated: {report['generated_at']}",
        "",
        "## Baseline",
        f"- Trades: {baseline.get('n')} | PF: {baseline.get('pf')} | early stop-outs: {baseline.get('early_stopout_pct')}%",
        f"- 21-40d PF: {baseline.get('hold_21_40d_pf')} | <=20d PF: {baseline.get('hold_lte20d_pf')}",
        "",
        "## Cohort contrast",
        f"- early_stop: n={early.get('n')} trailing_stop={((early.get('exit_reason_counts') or {}).get('trailing_stop', 0))}",
        f"- winner_21_40: n={winner.get('n')} time_exit={((winner.get('exit_reason_counts') or {}).get('time_exit', 0))}",
        f"- signal_score mean early={early.get('signal_score_mean')} winner={winner.get('signal_score_mean')}",
        f"- MAE mean early={early.get('mae_mean')} winner={winner.get('mae_mean')}",
        "",
        "## Recommendation",
        f"- action: **{rec.get('action')}**",
        f"- {rec.get('reason')}",
        "",
        "## By era",
        "| era | n | early_stop % | hold 21-40d PF |",
        "|-----|---:|---:|---:|",
    ]
    for row in report["by_era"]:
        lines.append(
            f"| {row['era']} | {row['n']} | {row['early_stopout_pct']} | {row.get('hold_21_40d_pf')} |"
        )
    out_md.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"Wrote {out_json}")
    print(f"Wrote {out_md}")
    print(f"Recommendation: {rec.get('action')} — {rec.get('reason')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
