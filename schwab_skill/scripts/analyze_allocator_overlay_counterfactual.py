#!/usr/bin/env python3
"""Offline multi-sleeve allocator overlay counterfactual (closed-trade admission).

Does NOT re-run backtests. Loads multi-era chunks (or --demo synthetic book),
applies top-N / DD staircase / crash-window overlays, reports PF mean / worst-era
/ retention vs constitution floors.

Usage (from schwab_skill/):
  python scripts/analyze_allocator_overlay_counterfactual.py --demo
  python scripts/analyze_allocator_overlay_counterfactual.py --run-id control_legacy_aug
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

from core.allocator_overlay_cf import (  # noqa: E402
    make_demo_trade_frame,
    run_allocator_overlay_arms,
    sweep_allocator_overlay_arms,
)
from scripts.phase2_common import CHUNKS_DIR, ERA_BOUNDS  # noqa: E402

ART = SKILL_DIR / "validation_artifacts"
ALL_ERAS = list(ERA_BOUNDS.keys())


def _load_trades_frame(run_id: str) -> pd.DataFrame:
    base = CHUNKS_DIR / run_id
    rows: list[dict[str, Any]] = []
    if not base.exists():
        return pd.DataFrame()
    for era in ALL_ERAS:
        era_dir = base / era
        if not era_dir.exists():
            continue
        for chunk_path in sorted(era_dir.glob("chunk_*.json")):
            if chunk_path.name.endswith("_tickers.json"):
                continue
            try:
                payload = json.loads(chunk_path.read_text(encoding="utf-8"))
            except Exception:
                continue
            for raw in payload.get("trades") or []:
                net = raw.get("net_return")
                if net is None:
                    net = raw.get("return", 0.0)
                entry = raw.get("entry_date")
                exit_d = raw.get("exit_date")
                if not entry or not exit_d:
                    continue
                try:
                    entry_ts = pd.Timestamp(entry)
                    exit_ts = pd.Timestamp(exit_d)
                    hold = max(int((exit_ts - entry_ts).days), 0)
                except Exception:
                    continue
                score = raw.get("signal_score")
                rank_v2 = raw.get("rank_score_v2")
                pts = raw.get("pts_52w")
                try:
                    score_f = float(score) if score is not None else None
                except (TypeError, ValueError):
                    score_f = None
                try:
                    rank_f = float(rank_v2) if rank_v2 is not None else None
                except (TypeError, ValueError):
                    rank_f = None
                try:
                    pts_f = float(pts) if pts is not None else None
                except (TypeError, ValueError):
                    pts_f = None
                rows.append(
                    {
                        "era": era,
                        "ticker": str(raw.get("ticker") or "").upper(),
                        "entry_date": entry_ts,
                        "exit_date": exit_ts,
                        "net_return": float(net or 0.0),
                        "signal_score": score_f,
                        "rank_score_v2": rank_f,
                        "pts_52w": pts_f,
                        "hold_days": hold,
                    }
                )
    return pd.DataFrame(rows)


def _apply_live_stack_proxy(df: pd.DataFrame) -> pd.DataFrame:
    """Approximate live S0 stack: pts_52w<=37 + rank_v2 >= era p76 (no breakout buffer)."""
    if df.empty:
        return df
    work = df.copy()
    if "pts_52w" in work.columns:
        pts = pd.to_numeric(work["pts_52w"], errors="coerce")
        work = work[pts.isna() | (pts <= 37.0)]
    if "rank_score_v2" in work.columns and work["rank_score_v2"].notna().sum() >= 50:
        scores = pd.to_numeric(work["rank_score_v2"], errors="coerce")
        thr = float(scores.quantile(0.76))
        work = work[scores >= thr]
    return work.reset_index(drop=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", default="control_legacy_aug", help="multi_era_chunks/<run_id>")
    parser.add_argument(
        "--demo",
        action="store_true",
        help="Use synthetic multi-era trades (smoke when chunks missing)",
    )
    parser.add_argument(
        "--apply-live-stack-proxy",
        action="store_true",
        help="Prefilter pts_52w<=37 + rank_v2 p76 before allocator overlay",
    )
    parser.add_argument(
        "--promoted-stack",
        action="store_true",
        help="Build exit_grace+1%% buffer+pts37+rank p76 book (true live stack proxy)",
    )
    parser.add_argument(
        "--sweep",
        action="store_true",
        help="Grid-search top-N / DD / crash / concurrency arms",
    )
    parser.add_argument(
        "--write-artifact",
        action="store_true",
        default=True,
        help="Write JSON under validation_artifacts/ (default on)",
    )
    parser.add_argument("--no-write-artifact", action="store_true", help="Skip artifact write")
    args = parser.parse_args()

    stack_meta: dict[str, Any] = {}
    if args.demo:
        df = make_demo_trade_frame()
        source = "demo_synthetic"
        run_id = "demo"
    elif args.promoted_stack:
        from core.promoted_stack_book import build_promoted_stack_frame

        print("Building promoted stack book (exit grace replay + entry cache)…")
        df, stack_meta = build_promoted_stack_frame(args.run_id)
        source = (
            f"promoted_stack:{args.run_id}"
            f"/buffer={stack_meta.get('min_breakout_buffer')}"
            f"/pts={stack_meta.get('pts_52w_max')}"
            f"/rank_p{stack_meta.get('rank_v2_percentile')}"
        )
        run_id = f"{args.run_id}_promoted_stack"
        print(
            "stack meta: entry={entry} grace={grace} joined={joined} "
            "buffer={buf} pts={pts} rank={rank} final={final}".format(
                entry=stack_meta.get("entry_cache_n"),
                grace=stack_meta.get("grace_replay_n"),
                joined=stack_meta.get("grace_joined_n"),
                buf=stack_meta.get("after_breakout_buffer_n"),
                pts=stack_meta.get("after_pts_52w_n"),
                rank=stack_meta.get("after_rank_v2_n"),
                final=stack_meta.get("final_n"),
            )
        )
        if df.empty:
            print("FAIL: promoted stack book empty:", stack_meta.get("error"))
            return 1
    else:
        df = _load_trades_frame(args.run_id)
        source = f"multi_era_chunks/{args.run_id}"
        run_id = args.run_id
        if df.empty:
            print(
                f"FAIL: no trades under {CHUNKS_DIR / args.run_id}. "
                "Re-run with --demo, or generate chunks via "
                "scripts/run_multi_era_backtest_schwab_only.py"
            )
            return 1
        if args.apply_live_stack_proxy:
            before = len(df)
            df = _apply_live_stack_proxy(df)
            source = f"{source}+pts37_rank_p76"
            run_id = f"{args.run_id}_stack_proxy"
            print(f"live-stack-proxy: {before} -> {len(df)} trades")
            if df.empty:
                print("FAIL: stack proxy filtered all trades")
                return 1

    if args.sweep:
        run_id = f"{run_id}_sweep"
        report = sweep_allocator_overlay_arms(df)
    else:
        report = run_allocator_overlay_arms(df)

    payload = {
        "schema": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "run_id": run_id,
        "source": source,
        "method": "closed_trade_admission_overlay",
        "stack_meta": stack_meta or None,
        "note": (
            "Allocator as chronological admission gate on closed trades; "
            "not full daily desired-weight net book. Crash windows are "
            "heuristic calendar proxies unless replaced later with SPY series."
        ),
        **report,
    }

    # Console summary
    print(f"source={source} baseline_n={report['baseline_n']} arms={report.get('n_arms', len(report['arms']))}")
    show = report.get("soft_rank_top5") or report["arms"]
    for arm in show[:12]:
        print(
            "  {label}: n={n} ret={ret}% pf_mean={pfm} worst={w} floors={ok}".format(
                label=arm.get("label"),
                n=arm.get("n_trades"),
                ret=arm.get("retention_pct"),
                pfm=arm.get("pf_mean"),
                w=arm.get("worst_era_pf"),
                ok=arm.get("passes_pf_floors"),
            )
        )
    if report.get("n_floor_clearers") is not None:
        print(f"floor_clearers={report.get('n_floor_clearers')}")
    rec = report.get("recommended") or {}
    print(f"recommended={rec.get('label')} ({rec.get('reason')})")
    cap = report.get("capacity_aware_recommended") or {}
    if cap.get("label"):
        print(f"capacity_aware={cap.get('label')} ({cap.get('reason')})")

    if args.write_artifact and not args.no_write_artifact:
        ART.mkdir(parents=True, exist_ok=True)
        out = ART / f"allocator_overlay_cf_{run_id}.json"
        out.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
        latest = ART / "allocator_overlay_cf_latest.json"
        latest.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
        print(f"Wrote {out}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
