#!/usr/bin/env python3
"""Track A2: pre-entry early-stop keep/drop counterfactual on frozen chunks.

Uses only features available at entry (pts_52w, signal_score, rank_score_v2,
breakout_buffer_pct from entry-timing replay). Sweeps rule thresholds and
reports five-era PF mean / worst-era vs incremental (+0.03) and strict (1.50)
gates.

Usage (from schwab_skill/):
  python scripts/analyze_early_stop_preentry_counterfactual.py
  python scripts/analyze_early_stop_preentry_counterfactual.py --run-id control_legacy_aug
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

SKILL_DIR = Path(__file__).resolve().parent.parent
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

from scripts.analyze_entry_timing_shadow_counterfactual import (  # noqa: E402
    _load_replay_cache,
)
from scripts.analyze_rank_filter_counterfactual import (  # noqa: E402
    DEFAULT_RUN_ID,
    _cohort_stats,
    _era_pf_from_df,
    _hold_days_map,
    _merge_hold_days,
)
from scripts.validate_scoring_metrics import _load_trade_frame  # noqa: E402

ART = SKILL_DIR / "validation_artifacts"
PF_MEAN_FLOOR = 1.20
PF_MEAN_TARGET = 1.50
WORST_ERA_FLOOR = 1.00
MIN_INCREMENTAL_LIFT = 0.03
MIN_ERA_TRADES = 50
RETENTION_FLOOR_PCT = 40.0


def _attach_breakout_buffer(df: pd.DataFrame, run_id: str) -> pd.DataFrame:
    out = df.copy()
    cache = _load_replay_cache(run_id)
    buf_map: dict[tuple[str, str, str], float | None] = {}
    if cache is not None and not cache.empty:
        for row in cache.to_dict(orient="records"):
            key = (
                str(row.get("era") or ""),
                str(row.get("ticker") or "").upper(),
                pd.Timestamp(row.get("entry_date")).strftime("%Y-%m-%d")
                if row.get("entry_date")
                else "",
            )
            raw = row.get("breakout_buffer_pct")
            try:
                buf_map[key] = float(raw) if raw is not None and not (isinstance(raw, float) and math.isnan(raw)) else None
            except (TypeError, ValueError):
                buf_map[key] = None
    out["entry_iso"] = pd.to_datetime(out["entry_date"]).dt.strftime("%Y-%m-%d")
    out["ticker"] = out["ticker"].astype(str).str.upper()
    out["breakout_buffer_pct"] = [
        buf_map.get((str(r.era), r.ticker, r.entry_iso)) for r in out.itertuples(index=False)
    ]
    return out


def _summarize_kept(df: pd.DataFrame, baseline_mean: float, label: str) -> dict[str, Any]:
    pf_mean, worst, n_eras = _era_pf_from_df(df)
    cohort = _cohort_stats(df)
    per_era_n = {str(era): int(len(g)) for era, g in df.groupby("era")}
    thin_eras = [e for e, n in per_era_n.items() if n < MIN_ERA_TRADES]
    lift = (pf_mean - baseline_mean) if pf_mean is not None else None
    return {
        "label": label,
        "n_trades": int(len(df)),
        "pf_all": cohort.get("pf"),
        "pf_mean": pf_mean,
        "worst_era_pf": worst,
        "n_eras": n_eras,
        "early_stopout_pct": cohort.get("early_stopout_pct"),
        "pf_mean_lift": round(lift, 4) if lift is not None else None,
        "per_era_n": per_era_n,
        "thin_eras": thin_eras,
        "passes_incremental": bool(
            pf_mean is not None
            and lift is not None
            and lift >= MIN_INCREMENTAL_LIFT
            and worst >= WORST_ERA_FLOOR
            and not thin_eras
        ),
        "passes_pf_120": bool(
            pf_mean is not None and pf_mean >= PF_MEAN_FLOOR and worst >= WORST_ERA_FLOOR and not thin_eras
        ),
        "passes_pf_150": bool(
            pf_mean is not None and pf_mean >= PF_MEAN_TARGET and worst >= WORST_ERA_FLOOR and not thin_eras
        ),
    }


def _rule_masks(df: pd.DataFrame) -> list[tuple[str, pd.Series]]:
    masks: list[tuple[str, pd.Series]] = []
    pts = pd.to_numeric(df.get("pts_52w"), errors="coerce")
    score = pd.to_numeric(df.get("signal_score"), errors="coerce")
    rank_v2 = pd.to_numeric(df.get("rank_score_v2"), errors="coerce")
    buf = pd.to_numeric(df.get("breakout_buffer_pct"), errors="coerce")

    for cap in (35.0, 33.0, 30.0, 28.0):
        # Keep when pts missing or <= cap (fail-open on missing feature).
        masks.append((f"pts_52w_cap_{cap:.0f}", pts.isna() | (pts <= cap)))

    for min_buf in (0.01, 0.015, 0.02):
        masks.append(
            (
                f"breakout_buffer_gte_{min_buf:.3f}",
                buf.isna() | (buf >= min_buf),
            )
        )

    if score.notna().sum() >= 50:
        for q in (0.25, 0.40, 0.50):
            thr = float(score.quantile(q))
            masks.append((f"signal_score_gte_q{int(q * 100)}", score.isna() | (score >= thr)))

    if rank_v2.notna().sum() >= 50:
        for q in (0.50, 0.60, 0.75):
            thr = float(rank_v2.quantile(q))
            masks.append((f"rank_v2_gte_q{int(q * 100)}", rank_v2.isna() | (rank_v2 >= thr)))

    # Composite: tighter chase + minimum breakout buffer (promoted stack-aligned).
    masks.append(
        (
            "pts33_and_buffer_0.010",
            (pts.isna() | (pts <= 33.0)) & (buf.isna() | (buf >= 0.01)),
        )
    )
    masks.append(
        (
            "pts30_and_buffer_0.010",
            (pts.isna() | (pts <= 30.0)) & (buf.isna() | (buf >= 0.01)),
        )
    )
    return masks


def _decide(best: dict[str, Any] | None, oracle_mean: float | None) -> dict[str, Any]:
    if best is None:
        return {
            "action": "kill_or_revise",
            "reasons": ["no rule produced a kept book"],
            "recommended_rule": None,
        }
    reasons: list[str] = []
    if best.get("passes_pf_150"):
        action = "pass_strict_pf_150_offline"
    elif best.get("passes_incremental"):
        action = "pass_incremental_shadow_candidate"
        reasons.append(
            f"incremental lift {best.get('pf_mean_lift')} >= +{MIN_INCREMENTAL_LIFT} "
            "but below strict 1.50"
        )
    elif best.get("passes_pf_120"):
        action = "keep_shadow_only"
        reasons.append("clears 1.20 floors without +0.03 incremental lift bar")
    else:
        action = "kill_or_revise"
        reasons.append("failed incremental and promotion floors")
    if oracle_mean is not None and oracle_mean < PF_MEAN_TARGET:
        reasons.append(
            f"oracle ceiling PF mean {oracle_mean:.4f} < {PF_MEAN_TARGET} — "
            "Track A alone cannot hit strict 1A"
        )
    return {
        "action": action,
        "reasons": reasons,
        "recommended_rule": best.get("label"),
        "recommended_summary": {
            "pf_mean": best.get("pf_mean"),
            "worst_era_pf": best.get("worst_era_pf"),
            "retention_pct": best.get("retention_pct"),
            "pf_mean_lift": best.get("pf_mean_lift"),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Pre-entry early-stop keep/drop CF")
    parser.add_argument("--run-id", default=DEFAULT_RUN_ID)
    args = parser.parse_args()

    df = _load_trade_frame(args.run_id)
    df = _merge_hold_days(df, _hold_days_map(args.run_id))
    df = _attach_breakout_buffer(df, args.run_id)

    baseline_mean, baseline_worst, baseline_eras = _era_pf_from_df(df)
    baseline = _summarize_kept(df, baseline_mean, "baseline_all")
    baseline["retention_pct"] = 100.0

    early_mask = (df["hold_days"] <= 20) & (df["net_return"] < 0)
    oracle_df = df[~early_mask]
    oracle = _summarize_kept(oracle_df, baseline_mean, "oracle_drop_early_stops")
    oracle["retention_pct"] = round(100.0 * len(oracle_df) / max(len(df), 1), 2)

    scenarios: list[dict[str, Any]] = []
    for label, keep_mask in _rule_masks(df):
        kept = df[keep_mask.fillna(True)]
        row = _summarize_kept(kept, baseline_mean, label)
        row["retention_pct"] = round(100.0 * len(kept) / max(len(df), 1), 2)
        if row["retention_pct"] < RETENTION_FLOOR_PCT:
            row["passes_incremental"] = False
            row["passes_pf_120"] = False
            row["passes_pf_150"] = False
            row["retention_fail"] = True
        scenarios.append(row)

    # Prefer incremental passers with highest PF mean; else best PF mean above worst-era floor.
    viable = [s for s in scenarios if s.get("passes_incremental")]
    if not viable:
        viable = [
            s
            for s in scenarios
            if s.get("worst_era_pf") is not None
            and float(s["worst_era_pf"]) >= WORST_ERA_FLOOR
            and not s.get("thin_eras")
        ]
    best = max(viable, key=lambda s: float(s.get("pf_mean") or 0.0), default=None)
    decision = _decide(best, oracle.get("pf_mean"))

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "run_id": args.run_id,
        "gates": {
            "pf_mean_floor": PF_MEAN_FLOOR,
            "pf_mean_target": PF_MEAN_TARGET,
            "worst_era_floor": WORST_ERA_FLOOR,
            "min_incremental_lift": MIN_INCREMENTAL_LIFT,
            "min_era_trades": MIN_ERA_TRADES,
            "retention_floor_pct": RETENTION_FLOOR_PCT,
        },
        "baseline": baseline,
        "oracle_ceiling": oracle,
        "scenarios": scenarios,
        "decision": decision,
        "baseline_pf_mean": baseline_mean,
        "baseline_worst_era_pf": baseline_worst,
        "baseline_n_eras": baseline_eras,
    }

    out_json = ART / f"early_stop_preentry_cf_{args.run_id}.json"
    out_md = ART / f"early_stop_preentry_cf_{args.run_id}.md"
    out_json.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")

    lines = [
        f"# Early-stop pre-entry CF — `{args.run_id}`",
        "",
        f"Generated: {report['generated_at']}",
        "",
        f"- Baseline PF mean: **{baseline_mean}** (worst {baseline_worst})",
        f"- Oracle PF mean: **{oracle.get('pf_mean')}** (worst {oracle.get('worst_era_pf')})",
        f"- Decision: **{decision.get('action')}** rule=`{decision.get('recommended_rule')}`",
        f"- Reasons: {'; '.join(decision.get('reasons') or []) or 'n/a'}",
        "",
        "## Top scenarios",
        "| rule | n | retention | PF mean | worst | lift | incr | 1.50 |",
        "|---|---:|---:|---:|---:|---:|:---:|:---:|",
    ]
    ranked = sorted(scenarios, key=lambda s: float(s.get("pf_mean") or 0.0), reverse=True)[:12]
    for s in ranked:
        lines.append(
            f"| {s['label']} | {s['n_trades']} | {s.get('retention_pct')} | "
            f"{s.get('pf_mean')} | {s.get('worst_era_pf')} | {s.get('pf_mean_lift')} | "
            f"{s.get('passes_incremental')} | {s.get('passes_pf_150')} |"
        )
    out_md.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"Wrote {out_json}")
    print(f"Wrote {out_md}")
    print(
        f"Decision: {decision.get('action')} rule={decision.get('recommended_rule')} "
        f"pf_mean={(decision.get('recommended_summary') or {}).get('pf_mean')}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
