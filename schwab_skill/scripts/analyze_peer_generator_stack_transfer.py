#!/usr/bin/env python3
"""Track B3: transfer exit-grace (+ optional buffer) onto a peer-generator run.

When ``entry_timing_replay_cache_<run_id>.json`` is missing (typical for peer
generators), evaluates exit-grace-only on chunk OHLC paths. If the entry cache
exists, delegates to the full stack CF (grace + 1% buffer ± rank-v2).

Usage (from schwab_skill/):
  python scripts/analyze_peer_generator_stack_transfer.py --run-id pullback_only_aug
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
    _cohort_stats,
    _era_pf_from_df,
)
from scripts.analyze_signal_stack_counterfactual import (  # noqa: E402
    DEFAULT_EXIT_PROFILE,
    DEFAULT_MIN_BREAKOUT_BUFFER,
    DEFAULT_RANK_V2_PERCENTILE,
    PROMOTION_PF_MEAN,
    PROMOTION_WORST_ERA_PF,
    _replay_exit_grace_rows,
)
from scripts.analyze_signal_stack_counterfactual import (
    main as _stack_main,
)
from scripts.phase2_common import CHUNKS_DIR, load_trades  # noqa: E402

ART = SKILL_DIR / "validation_artifacts"
PF_MEAN_TARGET = 1.50


def _baseline_from_chunks(run_id: str) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for t in load_trades(run_id):
        rows.append(
            {
                "era": t.era,
                "ticker": str(t.ticker or "").upper(),
                "entry_date": t.entry_date.strftime("%Y-%m-%d"),
                "net_return": float(t.net_ret),
                "hold_days": int(t.hold_days),
            }
        )
    return pd.DataFrame(rows)


def _summarize(df: pd.DataFrame, label: str) -> dict[str, Any]:
    pf_mean, worst, n_eras = _era_pf_from_df(df)
    cohort = _cohort_stats(df)
    return {
        "label": label,
        "n_trades": int(len(df)),
        "pf_all": cohort.get("pf"),
        "pf_mean": pf_mean,
        "worst_era_pf": worst,
        "n_eras": n_eras,
        "early_stopout_pct": cohort.get("early_stopout_pct"),
        "passes_pf_120": bool(pf_mean >= PROMOTION_PF_MEAN and worst >= PROMOTION_WORST_ERA_PF),
        "passes_pf_150": bool(pf_mean >= PF_MEAN_TARGET and worst >= PROMOTION_WORST_ERA_PF),
        "per_era_pf": {
            str(era): _cohort_stats(group).get("pf") for era, group in df.groupby("era")
        },
    }


def _exit_grace_only(run_id: str, exit_profile: str) -> dict[str, Any]:
    baseline_df = _baseline_from_chunks(run_id)
    if baseline_df.empty:
        return {
            "action": "blocked_no_trades",
            "reason": f"No trades loaded for run_id={run_id}",
        }
    grace_rows = _replay_exit_grace_rows(
        run_id,
        profile_name=exit_profile,
        data_provider="chunk",
    )
    grace_df = pd.DataFrame(grace_rows)
    if grace_df.empty:
        return {
            "action": "blocked_exit_replay_empty",
            "reason": "Exit grace replay produced 0 rows (need ohlc_path on chunks)",
            "baseline": _summarize(baseline_df, "bare_baseline"),
        }
    # Align columns for cohort helpers.
    if "net_return" not in grace_df.columns and "net_ret" in grace_df.columns:
        grace_df["net_return"] = grace_df["net_ret"]
    if "hold_days" not in grace_df.columns:
        grace_df["hold_days"] = 0
    baseline = _summarize(baseline_df, "bare_baseline")
    grace = _summarize(grace_df, f"exit_grace_{exit_profile}")
    if grace.get("passes_pf_150"):
        action = "pass_strict_pf_150_exit_grace"
    elif grace.get("passes_pf_120"):
        action = "pass_pf_120_exit_grace_ready_for_full_universe"
    else:
        action = "kill_or_revise_exit_grace"
    return {
        "action": action,
        "mode": "exit_grace_only",
        "reason": (
            "No entry_timing_replay_cache for peer generator; evaluated exit-grace-only "
            "transfer. Build entry-timing cache to add 1% breakout buffer arm."
        ),
        "baseline": baseline,
        "exit_grace": grace,
        "gates": {
            "pf_mean_floor": PROMOTION_PF_MEAN,
            "pf_mean_target": PF_MEAN_TARGET,
            "worst_era_floor": PROMOTION_WORST_ERA_PF,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Peer-generator stack transfer CF")
    parser.add_argument("--run-id", required=True, help="Peer generator multi-era run_id")
    parser.add_argument("--exit-profile", default=DEFAULT_EXIT_PROFILE)
    parser.add_argument("--min-breakout-buffer", type=float, default=DEFAULT_MIN_BREAKOUT_BUFFER)
    parser.add_argument("--rank-v2-percentile", type=int, default=DEFAULT_RANK_V2_PERCENTILE)
    args = parser.parse_args()

    chunk_root = CHUNKS_DIR / args.run_id
    out = ART / f"peer_generator_stack_transfer_{args.run_id}.json"
    if not chunk_root.exists():
        report = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "run_id": args.run_id,
            "action": "blocked_missing_chunks",
            "reason": (
                f"No chunks at {chunk_root}. Run multi-era with "
                f"--env-overrides research/env_overrides/{args.run_id}.json first."
            ),
        }
        out.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"Wrote {out}")
        print(report["reason"])
        return 2

    entry_cache = ART / f"entry_timing_replay_cache_{args.run_id}.json"
    if entry_cache.exists():
        sys.argv = [
            "analyze_signal_stack_counterfactual.py",
            "--run-id",
            args.run_id,
            "--exit-profile",
            args.exit_profile,
            "--min-breakout-buffer",
            str(args.min_breakout_buffer),
            "--rank-v2-percentile",
            str(args.rank_v2_percentile),
        ]
        rc = int(_stack_main())
        report = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "run_id": args.run_id,
            "mode": "full_stack_cf",
            "stack_rc": rc,
            "action": "stack_cf_complete" if rc == 0 else "stack_cf_failed",
        }
        out.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"Wrote {out}")
        return rc

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "run_id": args.run_id,
        "exit_profile": args.exit_profile,
        **_exit_grace_only(args.run_id, args.exit_profile),
    }
    out.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(f"Wrote {out}")
    print(
        f"action={report.get('action')} "
        f"bare_pf={((report.get('baseline') or {}).get('pf_mean'))} "
        f"grace_pf={((report.get('exit_grace') or {}).get('pf_mean'))}"
    )
    return 0 if str(report.get("action", "")).startswith("pass_") else 1


if __name__ == "__main__":
    raise SystemExit(main())
