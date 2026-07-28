#!/usr/bin/env python3
"""Sweep rank-v2 percentiles on the pts_52w≤cap stack book (post-cap fidelity).

Filters the entry-timing replay cache to ``pts_52w <= cap``, replays exit grace
+ 1% breakout buffer (same path as ``analyze_signal_stack_counterfactual``),
then evaluates rank-v2 percentile trims. Writes:

  validation_artifacts/sweep_cf_rank_under_pts52w_cap<cap>_<run_id>.json

Promotion rule: PF mean ≥ 1.20 and worst-era ≥ 1.00; retention vs buffer book
should stay roughly 20–30% (guidance, not a hard gate).

Usage (from schwab_skill/):
  python scripts/sweep_rank_v2_under_pts52w_cap.py
  python scripts/sweep_rank_v2_under_pts52w_cap.py --run-id control_legacy_aug --cap 37
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

from scripts.analyze_entry_timing_shadow_counterfactual import (  # noqa: E402
    _load_replay_cache,
)
from scripts.analyze_signal_stack_counterfactual import (  # noqa: E402
    DEFAULT_EXIT_PROFILE,
    DEFAULT_MIN_BREAKOUT_BUFFER,
    DEFAULT_RUN_ID,
    PROMOTION_PF_MEAN,
    PROMOTION_WORST_ERA_PF,
    _breakout_buffer_only_filter,
    _build_stack_frame,
    _rank_v2_percentile_filter,
    _replay_exit_grace_rows,
    _summarize_stack,
)

ART = SKILL_DIR / "validation_artifacts"
DEFAULT_PERCENTILES = (60, 65, 70, 72, 73, 74, 75, 76, 78, 80)
RETENTION_GUIDANCE_MIN = 20.0
RETENTION_GUIDANCE_MAX = 30.0


def _filter_pts52w_cap(entry_df: pd.DataFrame, cap: float) -> pd.DataFrame:
    if "pts_52w" not in entry_df.columns:
        raise ValueError("entry cache missing pts_52w column")
    pts = pd.to_numeric(entry_df["pts_52w"], errors="coerce")
    return entry_df[pts.notna() & (pts <= float(cap))].copy()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", default=DEFAULT_RUN_ID)
    parser.add_argument("--cap", type=float, default=37.0)
    parser.add_argument("--exit-profile", default=DEFAULT_EXIT_PROFILE)
    parser.add_argument(
        "--data-provider",
        choices=("chunk", "schwab", "yfinance"),
        default="chunk",
    )
    parser.add_argument(
        "--min-breakout-buffer",
        type=float,
        default=DEFAULT_MIN_BREAKOUT_BUFFER,
    )
    parser.add_argument(
        "--percentiles",
        default=",".join(str(p) for p in DEFAULT_PERCENTILES),
        help="Comma-separated rank-v2 percentiles to sweep (default: plateau band).",
    )
    args = parser.parse_args()

    percentiles = sorted({int(x.strip()) for x in args.percentiles.split(",") if x.strip()})
    if not percentiles:
        print("FAIL: no percentiles")
        return 1

    entry_df = _load_replay_cache(args.run_id)
    if entry_df.empty:
        print(f"FAIL: missing entry_timing_replay_cache_{args.run_id}.json")
        return 1

    capped = _filter_pts52w_cap(entry_df, args.cap)
    if len(capped) < 100:
        print(f"FAIL: capped book too thin ({len(capped)} rows)")
        return 1

    print(
        f"Replaying exit grace on capped book: {len(capped)} / {len(entry_df)} "
        f"(pts_52w<={args.cap}) …",
        flush=True,
    )
    grace_rows = _replay_exit_grace_rows(
        args.run_id,
        profile_name=args.exit_profile,
        data_provider=args.data_provider,
    )
    if len(grace_rows) < 100:
        print(f"FAIL: exit replay too thin ({len(grace_rows)} rows)")
        return 1

    merged = _build_stack_frame(capped, grace_rows)
    if len(merged) < 100:
        print(f"FAIL: merged capped stack too thin ({len(merged)} rows)")
        return 1

    drop = _breakout_buffer_only_filter(merged, args.min_breakout_buffer)
    buffer_kept = merged[~drop]
    buffer_summary = _summarize_stack(
        buffer_kept,
        label="exit_grace_breakout_buffer_0.010",
    )
    buffer_summary["retention_pct"] = round(100 * len(buffer_kept) / max(1, len(merged)), 1)

    sweep: list[dict[str, Any]] = []
    for pct in percentiles:
        kept, threshold = _rank_v2_percentile_filter(buffer_kept, pct)
        summary = _summarize_stack(
            kept,
            label=f"exit_grace_breakout_buffer_rank_v2_p{pct}",
        )
        retention = round(100 * len(kept) / max(1, len(buffer_kept)), 1)
        summary["retention_pct"] = retention
        summary["rank_v2_min_percentile"] = pct
        summary["rank_v2_threshold"] = threshold
        summary["retention_in_guidance_band"] = (
            RETENTION_GUIDANCE_MIN <= retention <= RETENTION_GUIDANCE_MAX
        )
        sweep.append(summary)
        print(
            f"p{pct}: n={summary['n_trades']} pf_mean={summary['pf_mean']} "
            f"worst={summary['worst_era_pf']} ret={retention}% "
            f"gates={'PASS' if summary['passes_promotion_gates'] else 'FAIL'}",
            flush=True,
        )

    passing = [
        row
        for row in sweep
        if row.get("passes_promotion_gates") and row.get("retention_in_guidance_band")
    ]
    # Prefer highest pf_mean among passing; else none.
    best_pass = None
    if passing:
        best_pass = max(passing, key=lambda r: (float(r.get("pf_mean") or 0.0), -int(r["rank_v2_min_percentile"])))

    gate_pass_any = [row for row in sweep if row.get("passes_promotion_gates")]
    recommendation: dict[str, Any]
    if best_pass is not None:
        recommendation = {
            "action": "re_live_candidate",
            "rank_v2_min_percentile": best_pass["rank_v2_min_percentile"],
            "reason": (
                f"p{best_pass['rank_v2_min_percentile']} clears PF gates under pts_52w≤{args.cap} "
                f"with retention {best_pass['retention_pct']}% in guidance band."
            ),
            "candidate": best_pass,
        }
    elif gate_pass_any:
        thin = max(gate_pass_any, key=lambda r: float(r.get("pf_mean") or 0.0))
        recommendation = {
            "action": "keep_shadow_retention_out_of_band",
            "reason": (
                f"Some percentiles clear PF gates (best p{thin['rank_v2_min_percentile']}) "
                "but retention is outside 20–30% guidance; keep RANK_FILTER_V2_MODE=shadow."
            ),
            "best_gate_pass": thin,
        }
    else:
        recommendation = {
            "action": "keep_shadow",
            "reason": (
                f"No rank-v2 percentile in {percentiles} clears PF mean≥{PROMOTION_PF_MEAN} "
                f"and worst-era≥{PROMOTION_WORST_ERA_PF} on the pts_52w≤{args.cap} stack. "
                "Keep RANK_FILTER_V2_MODE=shadow."
            ),
        }

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "run_id": args.run_id,
        "analysis": f"rank_v2_sweep_under_pts52w_cap{args.cap:g}",
        "cap": float(args.cap),
        "exit_profile": args.exit_profile,
        "data_provider": args.data_provider,
        "min_breakout_buffer_pct": args.min_breakout_buffer,
        "promotion_gates": {
            "pf_mean_min": PROMOTION_PF_MEAN,
            "worst_era_pf_min": PROMOTION_WORST_ERA_PF,
            "retention_guidance_pct": [RETENTION_GUIDANCE_MIN, RETENTION_GUIDANCE_MAX],
        },
        "uncapped_entry_cache_trades": len(entry_df),
        "capped_entry_cache_trades": len(capped),
        "merged_capped_trades": len(merged),
        "buffer_stack": buffer_summary,
        "sweep": sweep,
        "recommendation": recommendation,
    }

    tag = f"{args.cap:g}".replace(".", "p")
    out_json = ART / f"sweep_cf_rank_under_pts52w_cap{tag}_{args.run_id}.json"
    out_md = ART / f"sweep_cf_rank_under_pts52w_cap{tag}_{args.run_id}.md"
    out_json.write_text(json.dumps(report, indent=2), encoding="utf-8")

    lines = [
        f"# Rank-v2 sweep under pts_52w≤{args.cap:g} — `{args.run_id}`",
        "",
        f"Generated: {report['generated_at']}",
        "",
        f"Capped trades: {len(capped)} / {len(entry_df)}",
        f"Buffer stack: n={buffer_summary.get('n_trades')} "
        f"pf_mean={buffer_summary.get('pf_mean')} worst={buffer_summary.get('worst_era_pf')} "
        f"gates={'PASS' if buffer_summary.get('passes_promotion_gates') else 'FAIL'}",
        "",
        "## Sweep",
    ]
    for row in sweep:
        lines.append(
            f"- **p{row['rank_v2_min_percentile']}**: n={row.get('n_trades')} "
            f"ret={row.get('retention_pct')}% pf_mean={row.get('pf_mean')} "
            f"worst={row.get('worst_era_pf')} "
            f"gates={'PASS' if row.get('passes_promotion_gates') else 'FAIL'}"
        )
    lines.extend(
        [
            "",
            "## Recommendation",
            f"- action: `{recommendation.get('action')}`",
            f"- {recommendation.get('reason')}",
            "",
        ]
    )
    out_md.write_text("\n".join(lines), encoding="utf-8")
    print(f"Wrote {out_json.name}")
    action = recommendation.get("action")
    reason = str(recommendation.get("reason") or "").replace("\u2264", "<=")
    print(f"Recommendation: {action} - {reason}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
