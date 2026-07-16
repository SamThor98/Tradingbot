#!/usr/bin/env python3
"""Combined P0 stack counterfactual: exit grace + entry-timing filter.

Replays ``exit_grace_t15_h40`` on augmented trades, joins entry-timing replay
cache, and reports PF mean / worst-era PF for:

* exit grace only (all trades)
* exit grace + breakout-buffer-only at 1.0% (experiment profile)

Writes ``validation_artifacts/signal_stack_counterfactual_<run_id>.json``.
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
from scripts.analyze_rank_filter_counterfactual import (  # noqa: E402
    _cohort_stats,
    _era_pf_from_df,
    _hold_days_map,
    _merge_hold_days,
)
from scripts.phase2_common import load_trades  # noqa: E402
from scripts.replay_exit_overlay import (  # noqa: E402
    PROFILES,
    BarLoader,
    _replay_trade,
)

ART = SKILL_DIR / "validation_artifacts"
DEFAULT_RUN_ID = "control_legacy_aug"
DEFAULT_EXIT_PROFILE = "exit_grace_t15_h40"
DEFAULT_MIN_BREAKOUT_BUFFER = 0.01
DEFAULT_RANK_V2_PERCENTILE = 75
PROMOTION_PF_MEAN = 1.20
PROMOTION_WORST_ERA_PF = 1.00


def _trade_key(era: Any, ticker: Any, entry_date: Any) -> tuple[str, str, str]:
    return (
        str(era),
        str(ticker or "").upper(),
        pd.Timestamp(entry_date).strftime("%Y-%m-%d"),
    )


def _breakout_buffer_only_filter(df: pd.DataFrame, min_buf: float) -> pd.Series:
    return df["breakout_buffer_pct"].apply(lambda v: pd.notna(v) and float(v) < min_buf).fillna(False)


def _summarize_stack(df: pd.DataFrame, *, label: str) -> dict[str, Any]:
    pf_mean, worst_era_pf, n_eras = _era_pf_from_df(df)
    cohort = _cohort_stats(df)
    return {
        "label": label,
        "n_trades": len(df),
        "retention_pct": None,
        "pf_all": cohort.get("pf"),
        "pf_mean": pf_mean,
        "worst_era_pf": worst_era_pf,
        "n_eras": n_eras,
        "early_stopout_pct": cohort.get("early_stopout_pct"),
        "hold_21_40d_pf": cohort.get("hold_21_40d_pf"),
        "passes_pf_mean_gate": pf_mean >= PROMOTION_PF_MEAN,
        "passes_worst_era_gate": worst_era_pf >= PROMOTION_WORST_ERA_PF,
        "passes_promotion_gates": pf_mean >= PROMOTION_PF_MEAN and worst_era_pf >= PROMOTION_WORST_ERA_PF,
        "per_era_pf": {
            str(era): _cohort_stats(group).get("pf")
            for era, group in df.groupby("era")
        },
    }


def _rank_v2_percentile_filter(
    df: pd.DataFrame,
    min_percentile: int,
) -> tuple[pd.DataFrame, float | None]:
    from core.scoring_rank_v2 import score_percentile_threshold

    if "rank_score_v2" not in df.columns:
        return df.iloc[0:0].copy(), None
    scored = df[df["rank_score_v2"].notna()].copy()
    if len(scored) < 3:
        return scored.iloc[0:0].copy(), None
    threshold = score_percentile_threshold(
        scored["rank_score_v2"].astype(float).tolist(),
        min_percentile,
    )
    return scored[scored["rank_score_v2"].astype(float) >= threshold].copy(), threshold


def _replay_exit_grace_rows(
    run_id: str,
    *,
    profile_name: str,
    data_provider: str,
) -> list[dict[str, Any]]:
    profile = PROFILES.get(profile_name)
    if profile is None:
        raise ValueError(f"unknown exit profile: {profile_name}")
    trades = [t for t in load_trades(run_id) if t.has_path()]
    loader = BarLoader(data_provider, SKILL_DIR)
    max_hold = profile.hold_days
    rows: list[dict[str, Any]] = []
    for trade in trades:
        row = _replay_trade(
            trade,
            profile,
            loader=loader,
            max_hold_days=max_hold,
            skill_dir=SKILL_DIR,
        )
        if row is not None:
            rows.append(row)
    return rows


def _build_stack_frame(
    entry_df: pd.DataFrame,
    grace_rows: list[dict[str, Any]],
) -> pd.DataFrame:
    grace_by_key = {
        _trade_key(row["era"], row["ticker"], row["entry_date"]): row for row in grace_rows
    }
    out = entry_df.copy()
    out["entry_iso"] = pd.to_datetime(out["entry_date"]).dt.strftime("%Y-%m-%d")
    out["ticker"] = out["ticker"].astype(str).str.upper()
    grace_net: list[float | None] = []
    grace_hold: list[int | None] = []
    for row in out.itertuples(index=False):
        key = (str(row.era), row.ticker, row.entry_iso)
        grace = grace_by_key.get(key)
        grace_net.append(float(grace["net_return"]) if grace else None)
        grace_hold.append(int(grace["hold_days"]) if grace else None)
    out["grace_net_return"] = grace_net
    out["grace_hold_days"] = grace_hold
    merged = out[out["grace_net_return"].notna()].copy()
    merged["net_return"] = merged["grace_net_return"].astype(float)
    merged["hold_days"] = merged["grace_hold_days"].fillna(0).astype(int)
    return merged


def _pick_recommendation(scenarios: dict[str, dict[str, Any]]) -> dict[str, Any]:
    grace = scenarios.get("exit_grace_all") or {}
    stack = scenarios.get("exit_grace_breakout_buffer_0.010") or {}
    if stack.get("passes_promotion_gates"):
        return {
            "action": "promote_stack_shadow_first",
            "reason": (
                "Combined exit grace + 1.0% breakout buffer clears PF promotion gates offline. "
                "Keep entry timing in shadow until one more live scan week, then evaluate live enforcement."
            ),
            "stack": stack,
        }
    if grace.get("passes_promotion_gates"):
        return {
            "action": "promote_exit_grace_only",
            "reason": (
                "Exit grace alone clears promotion gates; entry filter does not add enough PF to require live enforcement yet."
            ),
            "grace": grace,
            "stack": stack,
        }
    gap_mean = round(PROMOTION_PF_MEAN - float(grace.get("pf_mean") or 0.0), 4)
    gap_worst = round(PROMOTION_WORST_ERA_PF - float(grace.get("worst_era_pf") or 0.0), 4)
    return {
        "action": "halt_fix_signal_first",
        "reason": (
            f"Stack still below promotion gates (PF mean gap {gap_mean:+.4f}, worst-era gap {gap_worst:+.4f}). "
            "Continue shadow entry-timing evidence; do not enforce live."
        ),
        "grace": grace,
        "stack": stack,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", default=DEFAULT_RUN_ID)
    parser.add_argument("--exit-profile", default=DEFAULT_EXIT_PROFILE)
    parser.add_argument(
        "--data-provider",
        choices=("chunk", "schwab", "yfinance"),
        default="chunk",
        help="Bar source for exit replay (default: chunk ohlc_path).",
    )
    parser.add_argument(
        "--min-breakout-buffer",
        type=float,
        default=DEFAULT_MIN_BREAKOUT_BUFFER,
        help="Breakout buffer threshold for entry filter (default: 0.01).",
    )
    parser.add_argument(
        "--rank-v2-percentile",
        type=int,
        default=DEFAULT_RANK_V2_PERCENTILE,
        help="Rank-v2 percentile trim evaluated after the promoted stack (default: 75).",
    )
    args = parser.parse_args()

    entry_df = _load_replay_cache(args.run_id)
    if entry_df.empty:
        print(f"FAIL: missing entry_timing_replay_cache_{args.run_id}.json")
        return 1

    grace_rows = _replay_exit_grace_rows(
        args.run_id,
        profile_name=args.exit_profile,
        data_provider=args.data_provider,
    )
    if len(grace_rows) < 100:
        print(f"FAIL: exit replay too thin ({len(grace_rows)} rows)")
        return 1

    merged = _build_stack_frame(entry_df, grace_rows)
    hold_map = _hold_days_map(args.run_id)
    legacy_df = _merge_hold_days(entry_df.copy(), hold_map)

    grace_all = _summarize_stack(merged, label="exit_grace_all")
    grace_all["retention_pct"] = round(100 * len(merged) / max(1, len(entry_df)), 1)

    drop = _breakout_buffer_only_filter(merged, args.min_breakout_buffer)
    kept = merged[~drop]
    stack = _summarize_stack(kept, label="exit_grace_breakout_buffer_0.010")
    stack["retention_pct"] = round(100 * len(kept) / max(1, len(merged)), 1)
    stack["min_breakout_buffer_pct"] = args.min_breakout_buffer

    rank_v2_kept, rank_v2_threshold = _rank_v2_percentile_filter(
        kept,
        args.rank_v2_percentile,
    )
    rank_v2_label = f"exit_grace_breakout_buffer_rank_v2_p{args.rank_v2_percentile}"
    rank_v2_stack = _summarize_stack(
        rank_v2_kept,
        label=rank_v2_label,
    )
    rank_v2_stack["retention_pct"] = round(100 * len(rank_v2_kept) / max(1, len(kept)), 1)
    rank_v2_stack["rank_v2_min_percentile"] = args.rank_v2_percentile
    rank_v2_stack["rank_v2_threshold"] = rank_v2_threshold

    legacy = _summarize_stack(legacy_df, label="legacy_baseline")
    legacy["retention_pct"] = 100.0

    scenarios = {
        "legacy_baseline": legacy,
        "exit_grace_all": grace_all,
        "exit_grace_breakout_buffer_0.010": stack,
        rank_v2_label: rank_v2_stack,
    }
    recommendation = _pick_recommendation(scenarios)

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "run_id": args.run_id,
        "exit_profile": args.exit_profile,
        "data_provider": args.data_provider,
        "promotion_gates": {
            "pf_mean_min": PROMOTION_PF_MEAN,
            "worst_era_pf_min": PROMOTION_WORST_ERA_PF,
        },
        "entry_cache_trades": len(entry_df),
        "exit_replayed_trades": len(grace_rows),
        "merged_trades": len(merged),
        "scenarios": scenarios,
        "recommendation": recommendation,
    }

    out_json = ART / f"signal_stack_counterfactual_{args.run_id}.json"
    out_md = ART / f"signal_stack_counterfactual_{args.run_id}.md"
    out_json.write_text(json.dumps(report, indent=2), encoding="utf-8")

    lines = [
        f"# Signal stack counterfactual — `{args.run_id}`",
        "",
        f"Generated: {report['generated_at']}",
        "",
        "## Scenarios",
    ]
    for key, row in scenarios.items():
        lines.append(
            f"- **{key}**: n={row.get('n_trades')} pf_mean={row.get('pf_mean')} "
            f"worst={row.get('worst_era_pf')} early_stop={row.get('early_stopout_pct')}% "
            f"gates={'PASS' if row.get('passes_promotion_gates') else 'FAIL'}"
        )
    lines.extend(
        [
            "",
            "## Recommendation",
            f"- **{recommendation.get('action')}** — {recommendation.get('reason')}",
        ]
    )
    out_md.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"Wrote {out_json}")
    print(f"Wrote {out_md}")
    for key in (
        "legacy_baseline",
        "exit_grace_all",
        "exit_grace_breakout_buffer_0.010",
        rank_v2_label,
    ):
        row = scenarios[key]
        print(
            f"{key}: pf_mean={row.get('pf_mean')} worst={row.get('worst_era_pf')} "
            f"n={row.get('n_trades')} gates={'PASS' if row.get('passes_promotion_gates') else 'FAIL'}"
        )
    print(f"Recommendation: {recommendation.get('action')}")
    return 0 if recommendation.get("action") != "halt_fix_signal_first" else 1


if __name__ == "__main__":
    raise SystemExit(main())
