#!/usr/bin/env python3
"""Replay one exit policy: 15/40 grace with VCP invalidation turned off.

Pre-entry bars stay in the window so a VCP exit means the contraction check
failed, not that the replay started at the entry bar and had fewer than 50
rows. Does not change EXIT_MANAGER_MODE, hold days, or the live stop.

Usage (from schwab_skill/):
  python scripts/analyze_vcp_invalidation_exit.py
  python scripts/analyze_vcp_invalidation_exit.py --limit 200
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

from research.open_research import arm_stats  # noqa: E402
from research.vcp_exit import interpretable_sample, vcp_exit_verdict  # noqa: E402
from scripts.replay_exit_overlay import PROFILES, _replay_exit_on_df  # noqa: E402
from scripts.run_open_research import (  # noqa: E402
    CONTROL,
    LOOKBACK,
    POST_EXIT_PAD,
    _cache_frame,
    _load_scores,
)

OUT_JSON = SKILL_DIR / "validation_artifacts" / "vcp_invalidation_exit.json"
OUT_MD = SKILL_DIR / "validation_artifacts" / "vcp_invalidation_exit.md"
MIN_HISTORY_BARS = 50
PROFILE_NAME = "exit_grace_t15_h40"


def _net_from_exit(
    window: pd.DataFrame,
    entry_idx: int,
    exit_px: float,
    original_ret: float,
    original_net: float,
) -> float | None:
    entry_px = float(window["close"].iloc[entry_idx])
    if entry_px <= 0:
        return None
    gross = (float(exit_px) - entry_px) / entry_px
    if original_ret:
        return gross - (float(original_ret) - float(original_net))
    return gross


def _replay_arm(
    window: pd.DataFrame,
    *,
    entry_idx: int,
    stop_pct: float,
    original_ret: float,
    original_net: float,
    skip_vcp: bool,
) -> tuple[float, str] | None:
    profile = PROFILES[PROFILE_NAME]
    try:
        exit_px, _exit_ts, reason = _replay_exit_on_df(
            window,
            profile=profile,
            stop_pct=stop_pct,
            skill_dir=SKILL_DIR,
            entry_idx=entry_idx,
            skip_vcp_invalidation=skip_vcp,
        )
    except Exception:
        return None
    net = _net_from_exit(window, entry_idx, float(exit_px), original_ret, original_net)
    if net is None:
        return None
    return net, str(reason or "")


def replay_book(df: pd.DataFrame, *, limit: int = 0) -> dict[str, Any]:
    """Score grace versus grace-without-VCP on trades that have 50 bars of history."""
    from stage_analysis import add_indicators

    work = df
    if limit > 0:
        work = df.head(limit)
    grace_rows: list[dict[str, Any]] = []
    skip_rows: list[dict[str, Any]] = []
    grace_reasons: dict[str, int] = {}
    skip_reasons: dict[str, int] = {}
    n = 0
    n_cache = 0
    n_after = 0
    n_history = 0
    for ticker, grp in work.groupby("ticker"):
        raw = _cache_frame(str(ticker))
        if raw is None:
            continue
        n_cache += int(len(grp))
        try:
            indicated = add_indicators(raw)
        except Exception:
            continue
        for row in grp.itertuples(index=False):
            n += 1
            entry = pd.Timestamp(row.entry_date).normalize()
            exit_dt = pd.Timestamp(row.exit_date).normalize() if pd.notna(row.exit_date) else entry
            window_src = indicated.loc[
                (indicated.index >= entry - LOOKBACK) & (indicated.index <= entry + POST_EXIT_PAD)
            ]
            if window_src.empty or window_src.index.max() <= exit_dt:
                continue
            entry_pos = int(window_src.index.searchsorted(entry))
            if entry_pos >= len(window_src):
                continue
            n_after += 1
            if entry_pos < MIN_HISTORY_BARS:
                continue
            stop = getattr(row, "stop_pct", None)
            try:
                stop_f = float(stop)
            except (TypeError, ValueError):
                continue
            if stop_f != stop_f or stop_f <= 0:
                continue
            original_ret = float(getattr(row, "return", 0.0) or 0.0)
            original_net = float(row.net_return)
            window = window_src.copy()
            grace = _replay_arm(
                window,
                entry_idx=entry_pos,
                stop_pct=stop_f,
                original_ret=original_ret,
                original_net=original_net,
                skip_vcp=False,
            )
            skip = _replay_arm(
                window,
                entry_idx=entry_pos,
                stop_pct=stop_f,
                original_ret=original_ret,
                original_net=original_net,
                skip_vcp=True,
            )
            if grace is None or skip is None:
                continue
            n_history += 1
            grace_rows.append({"era": row.era, "net_return": grace[0]})
            skip_rows.append({"era": row.era, "net_return": skip[0]})
            grace_reasons[grace[1]] = grace_reasons.get(grace[1], 0) + 1
            skip_reasons[skip[1]] = skip_reasons.get(skip[1], 0) + 1
        if n and n % 2000 == 0:
            print(f"  vcp exit replay scanned {n} trades, {n_history} with history", flush=True)
    grace_arm = arm_stats(pd.DataFrame(grace_rows))
    skip_arm = arm_stats(pd.DataFrame(skip_rows))
    interpretable = interpretable_sample(n_trades=int(len(work)), n_with_history=n_history)
    return {
        "run_id": CONTROL,
        "profile": PROFILE_NAME,
        "n_trades": int(len(work)),
        "n_with_price_cache": n_cache,
        "n_with_bar_after_exit": n_after,
        "n_with_pre_entry_history": n_history,
        "interpretable": interpretable,
        "grace_exit_reasons": grace_reasons,
        "skip_vcp_exit_reasons": skip_reasons,
        "arms": {
            "replay_grace_15_40": grace_arm,
            "replay_grace_skip_vcp": skip_arm,
        },
        "verdict": vcp_exit_verdict(grace_arm, skip_arm, interpretable=interpretable),
        "live_exit_unchanged": True,
        "note": (
            "Both arms use the 15/40 grace profile on trades with at least 50 bars "
            "before entry and a cached bar after the stored exit. Skipping VCP is "
            "one policy. A shadow-ready verdict is not an exit-mode change."
        ),
    }


def _fmt(value: Any) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, float):
        return f"{value:.4f}"
    return str(value)


def write_reports(payload: dict[str, Any]) -> None:
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    lines = [
        "# VCP invalidation exit counterfactual",
        "",
        f"Generated {payload['generated_at']}. Live exits were not changed.",
        "",
        f"Verdict: `{payload['verdict']}`.",
        "",
        f"Trades with 50 pre-entry bars: {payload['n_with_pre_entry_history']} / {payload['n_trades']}. "
        f"Interpretable: {payload['interpretable']}.",
        "",
        "| Arm | n | PF mean | Worst era | Clears floors |",
        "|---|---:|---:|---:|---|",
    ]
    for name, arm in payload["arms"].items():
        lines.append(
            f"| {name} | {arm['n']} | {_fmt(arm['pf_mean'])} | {_fmt(arm['worst_era_pf'])} | {arm['clears_floors']} |"
        )
    lines.extend(["", payload["note"], ""])
    for label, reasons in (
        ("Grace exit reasons", payload["grace_exit_reasons"]),
        ("Skip-VCP exit reasons", payload["skip_vcp_exit_reasons"]),
    ):
        rendered = ", ".join(f"{name} {count}" for name, count in sorted(reasons.items(), key=lambda item: -item[1]))
        lines.append(f"{label}: {rendered or 'none'}.")
    lines.append("")
    OUT_MD.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Replay grace with VCP invalidation skipped.")
    parser.add_argument("--limit", type=int, default=0, help="Max control trades (0 = all).")
    args = parser.parse_args()
    df = _load_scores(CONTROL)
    block = replay_book(df, limit=int(args.limit or 0))
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        **block,
    }
    write_reports(payload)
    print(f"Verdict: {payload['verdict']}")
    print(f"Wrote {OUT_JSON}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
