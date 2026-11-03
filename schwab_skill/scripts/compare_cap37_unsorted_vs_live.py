#!/usr/bin/env python3
"""Shadow: book width under the live rank-v2 p76 trim.

Loads frozen ``control_legacy_aug`` trades. Compares the live daily top 5
with a top-10 cut and with every name that clears p76, plus the older
cap-only book that drops the rank trim. Does not change
``SCAN_LIVE_SORT_KEY``, ``RANK_FILTER_V2_MODE``, or ``SIGNAL_TOP_N``.
Profit-factor intervals resample entry weeks.

Usage (from schwab_skill/):
  python scripts/compare_cap37_unsorted_vs_live.py
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

from research.counterfactual import (  # noqa: E402
    _era_pf_mean_worst,
    select_by_percentile,
    select_top_n_by_day,
)
from research.report import bootstrap_pf_ci_blocked  # noqa: E402
from scripts.phase2_common import ARTIFACT_DIR, CHUNKS_DIR  # noqa: E402

PTS_52W_CAP = 37.0
PF_MEAN_FLOOR = 1.20
WORST_ERA_FLOOR = 1.00
KEEP_FIELDS = (
    "entry_date",
    "net_return",
    "pts_52w",
    "signal_score",
    "rank_score_v2",
    "ticker",
)


def load_trade_frame(run_id: str, *, chunks_root: Path | None = None) -> pd.DataFrame:
    """Read trade rows and drop OHLC paths."""
    root = (chunks_root or CHUNKS_DIR) / run_id
    if not root.exists():
        raise FileNotFoundError(f"missing trade chunks: {root}")
    rows: list[dict[str, Any]] = []
    for era_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        for chunk_path in sorted(era_dir.glob("chunk_*.json")):
            if chunk_path.name.endswith("_tickers.json"):
                continue
            payload = json.loads(chunk_path.read_text(encoding="utf-8"))
            for raw in payload.get("trades") or []:
                if not isinstance(raw, dict):
                    continue
                row = {key: raw.get(key) for key in KEEP_FIELDS}
                row["era"] = era_dir.name
                rows.append(row)
    frame = pd.DataFrame(rows)
    if frame.empty:
        return frame
    frame["entry_date"] = pd.to_datetime(frame["entry_date"], errors="coerce")
    for col in ("net_return", "pts_52w", "signal_score", "rank_score_v2"):
        frame[col] = pd.to_numeric(frame[col], errors="coerce")
    return frame.dropna(subset=["entry_date", "net_return"])


def cap37_unsorted(frame: pd.DataFrame, *, cap: float = PTS_52W_CAP) -> pd.DataFrame:
    pts = frame["pts_52w"]
    return frame[pts.isna() | (pts <= cap)].copy()


def p76_book(frame: pd.DataFrame, *, percentile: float = 76.0) -> pd.DataFrame:
    """Names that clear the live rank-v2 percentile. No daily top-N."""
    if frame.empty or "rank_score_v2" not in frame.columns:
        return frame.iloc[0:0].copy()
    return select_by_percentile(frame, score_col="rank_score_v2", min_percentile=percentile)


def live_rank_cut(
    frame: pd.DataFrame,
    *,
    percentile: float = 76.0,
    top_n: int = 5,
) -> pd.DataFrame:
    kept = p76_book(frame, percentile=percentile)
    if kept.empty or top_n <= 0:
        return kept
    return select_top_n_by_day(kept, score_col="signal_score", top_n=top_n)


def _arm(name: str, frame: pd.DataFrame, total: int, *, selection: str) -> dict[str, Any]:
    pf_mean, worst = _era_pf_mean_worst(frame)
    weeks = frame["entry_date"].dt.to_period("W").astype(str)
    ci = bootstrap_pf_ci_blocked(frame["net_return"], weeks) if not frame.empty else {}
    return {
        "arm": name,
        "selection": selection,
        "n": int(len(frame)),
        "retention": round(len(frame) / max(1, total), 4),
        "pf_mean": pf_mean,
        "worst_era_pf": worst,
        "pf_ci_method": "week_block",
        "pf_pooled_ci": ci,
    }


def shadow_verdict(cap: dict[str, Any], live: dict[str, Any]) -> dict[str, Any]:
    """Compare arms. Never sets a live-sort promotion."""
    cap_pf = cap.get("pf_mean")
    cap_worst = cap.get("worst_era_pf")
    live_pf = live.get("pf_mean")
    live_worst = live.get("worst_era_pf")
    clears = (
        isinstance(cap_pf, (int, float))
        and isinstance(cap_worst, (int, float))
        and float(cap_pf) >= PF_MEAN_FLOOR
        and float(cap_worst) >= WORST_ERA_FLOOR
    )
    beats = (
        isinstance(cap_pf, (int, float))
        and isinstance(live_pf, (int, float))
        and isinstance(cap_worst, (int, float))
        and isinstance(live_worst, (int, float))
        and float(cap_pf) > float(live_pf)
        and float(cap_worst) > float(live_worst)
    )
    cap_ci = cap.get("pf_pooled_ci") or {}
    live_ci = live.get("pf_pooled_ci") or {}
    cap_lo = cap_ci.get("pf_lo")
    live_hi = live_ci.get("pf_hi")
    separates = (
        isinstance(cap_lo, (int, float))
        and isinstance(live_hi, (int, float))
        and float(cap_lo) > float(live_hi)
    )
    if beats and clears and separates:
        verdict = "shadow_ready_sort_not_promoted"
    elif beats and clears:
        verdict = "shadow_ahead_intervals_overlap"
    else:
        verdict = "keep_live_sort"
    return {
        "verdict": verdict,
        "promote_sort": False,
        "clears_floors": clears,
        "beats_live_point": beats,
        "week_block_ci_separates": separates,
        "floors": {"pf_mean": PF_MEAN_FLOOR, "worst_era_pf": WORST_ERA_FLOOR},
    }


def capped_rank_decision(cap_p76: dict[str, Any], top5: dict[str, Any]) -> dict[str, Any]:
    """Score the daily top 5 after the cap. Never applies a live change.

    The percentile is computed on trades already inside ``pts_52w`` <= 37.
    That is the raw control book, not the July buffer-plus-exit stack.
    """
    p76_n = int(cap_p76.get("n") or 0)
    top5_n = int(top5.get("n") or 0)
    removed = max(0, p76_n - top5_n)
    compared = shadow_verdict(top5, cap_p76)
    thin = p76_n == 0 or removed <= max(1, int(0.05 * p76_n))
    if compared.get("verdict") == "shadow_ready_sort_not_promoted":
        verdict = "capped_top5_ready_not_applied"
    elif compared.get("clears_floors") and compared.get("beats_live_point"):
        verdict = "capped_top5_ahead_intervals_overlap"
    elif thin:
        verdict = "top5_not_the_lever"
    else:
        verdict = "capped_top5_changes_membership"
    return {
        "verdict": verdict,
        "promote_sort": False,
        "apply_live": False,
        "names_removed_by_top5": removed,
        "top5_vs_capped_p76": compared,
        "note": (
            "Percentile and top-N run after pts_52w <= 37 on the raw control book. "
            "This is not the July buffer-plus-exit stack (p76 there cleared the floors). "
            "SCAN_LIVE_SORT_KEY, RANK_FILTER_V2_MODE, and SIGNAL_TOP_N stay unchanged."
        ),
    }


def capacity_decision(
    top10: dict[str, Any],
    all_names: dict[str, Any],
    live: dict[str, Any],
) -> dict[str, Any]:
    """Score wider p76 books against the live top 5. Never applies the change."""
    vs_top10 = shadow_verdict(top10, live)
    vs_all = shadow_verdict(all_names, live)
    ready = "shadow_ready_sort_not_promoted"
    overlap = "shadow_ahead_intervals_overlap"
    if vs_top10.get("verdict") == ready:
        verdict = "top10_ready_not_applied"
        recommended = "p76_top10"
    elif vs_all.get("verdict") == ready:
        verdict = "p76_all_ready_not_applied"
        recommended = "p76_all"
    elif vs_top10.get("verdict") == overlap or vs_all.get("verdict") == overlap:
        verdict = "wider_ahead_intervals_overlap"
        recommended = None
    else:
        verdict = "keep_live_top5"
        recommended = "current_live_p76_top5"
    return {
        "verdict": verdict,
        "apply_live": False,
        "recommended_arm": recommended,
        "p76_top10": vs_top10,
        "p76_all": vs_all,
        "note": (
            "Same rank-v2 p76 trim as live. The daily top-5 cut is the only "
            "difference under test. apply_live stays false until a signed "
            "ledger entry changes SIGNAL_TOP_N and position size together."
        ),
    }


def build_report(frame: pd.DataFrame, *, run_id: str) -> dict[str, Any]:
    total = int(len(frame))
    capped = cap37_unsorted(frame)
    cap = _arm(
        "pts52w_cap37_unsorted",
        capped,
        total,
        selection=f"pts_52w <= {PTS_52W_CAP:g} or missing; no rank trim",
    )
    live = _arm(
        "current_live_p76_top5",
        live_rank_cut(frame, top_n=5),
        total,
        selection="rank_score_v2 >= p76, then top 5 per entry day by signal_score",
    )
    top10 = _arm(
        "p76_top10",
        live_rank_cut(frame, top_n=10),
        total,
        selection="rank_score_v2 >= p76, then top 10 per entry day by signal_score",
    )
    all_names = _arm(
        "p76_all",
        p76_book(frame),
        total,
        selection="rank_score_v2 >= p76; no daily top-N",
    )
    cap_p76 = _arm(
        "cap37_p76",
        p76_book(capped),
        total,
        selection=f"pts_52w <= {PTS_52W_CAP:g} or missing, then rank_score_v2 >= p76; no daily top-N",
    )
    cap_top10 = _arm(
        "cap37_p76_top10",
        live_rank_cut(capped, top_n=10),
        total,
        selection=(
            f"pts_52w <= {PTS_52W_CAP:g} or missing, then rank_score_v2 >= p76, "
            "then top 10 per entry day by signal_score"
        ),
    )
    cap_top5 = _arm(
        "cap37_p76_top5",
        live_rank_cut(capped, top_n=5),
        total,
        selection=(
            f"pts_52w <= {PTS_52W_CAP:g} or missing, then rank_score_v2 >= p76, "
            "then top 5 per entry day by signal_score"
        ),
    )
    cap_top3 = _arm(
        "cap37_p76_top3",
        live_rank_cut(capped, top_n=3),
        total,
        selection=(
            f"pts_52w <= {PTS_52W_CAP:g} or missing, then rank_score_v2 >= p76, "
            "then top 3 per entry day by signal_score"
        ),
    )
    decision = shadow_verdict(cap, live)
    capacity = capacity_decision(top10, all_names, live)
    capped_rank = capped_rank_decision(cap_p76, cap_top5)
    capped_rank["names_removed_by_top3"] = max(0, int(cap_p76.get("n") or 0) - int(cap_top3.get("n") or 0))
    capped_rank["top3_vs_capped_p76"] = shadow_verdict(cap_top3, cap_p76)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "run_id": run_id,
        "n_trades": total,
        "arms": {
            "pts52w_cap37_unsorted": cap,
            "current_live_p76_top5": live,
            "p76_top10": top10,
            "p76_all": all_names,
            "cap37_p76": cap_p76,
            "cap37_p76_top10": cap_top10,
            "cap37_p76_top5": cap_top5,
            "cap37_p76_top3": cap_top3,
        },
        **decision,
        "capacity": capacity,
        "capped_rank": capped_rank,
        "note": (
            "SCAN_LIVE_SORT_KEY stays signal_score. RANK_FILTER_V2 stays at p76. "
            "SIGNAL_TOP_N stays 5. A wider book that clears the gates is still a shadow. "
            "cap37_p76* arms apply the rank trim after the pts_52w cap on the raw book. "
            "They are not the July buffer-plus-exit stack."
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Shadow cap37-unsorted vs live rank cut")
    parser.add_argument("--run-id", default="control_legacy_aug")
    parser.add_argument(
        "--out",
        default="",
        help="JSON path. Default: validation_artifacts/cap37_unsorted_vs_live.json",
    )
    args = parser.parse_args()
    frame = load_trade_frame(args.run_id)
    if frame.empty or "pts_52w" not in frame.columns:
        print(f"[cap37-shadow] no usable trades for {args.run_id}", file=sys.stderr)
        return 2
    report = build_report(frame, run_id=args.run_id)
    out = Path(args.out) if args.out else (ARTIFACT_DIR / "cap37_unsorted_vs_live.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    arms = report["arms"]
    cap = arms["pts52w_cap37_unsorted"]
    live = arms["current_live_p76_top5"]
    top10 = arms["p76_top10"]
    all_names = arms["p76_all"]
    capacity = report["capacity"]
    capped_rank = report["capped_rank"]
    cap_p76 = arms["cap37_p76"]
    cap_top5 = arms["cap37_p76_top5"]
    print(
        f"[cap37-shadow] sort={report['verdict']} promote_sort={report['promote_sort']} "
        f"capacity={capacity['verdict']} apply_live={capacity['apply_live']} "
        f"capped_rank={capped_rank['verdict']}"
    )
    print(
        f"[cap37-shadow] cap n={cap['n']} pf={cap['pf_mean']} worst={cap['worst_era_pf']} "
        f"live5 n={live['n']} pf={live['pf_mean']} worst={live['worst_era_pf']} "
        f"top10 n={top10['n']} pf={top10['pf_mean']} worst={top10['worst_era_pf']} "
        f"p76_all n={all_names['n']} pf={all_names['pf_mean']} worst={all_names['worst_era_pf']}"
    )
    print(
        f"[cap37-shadow] cap_p76 n={cap_p76['n']} pf={cap_p76['pf_mean']} worst={cap_p76['worst_era_pf']} "
        f"cap_top5 n={cap_top5['n']} pf={cap_top5['pf_mean']} worst={cap_top5['worst_era_pf']} "
        f"cap_top3 n={arms['cap37_p76_top3']['n']} pf={arms['cap37_p76_top3']['pf_mean']} "
        f"worst={arms['cap37_p76_top3']['worst_era_pf']} "
        f"removed5={capped_rank['names_removed_by_top5']} removed3={capped_rank['names_removed_by_top3']}"
    )
    print(f"[cap37-shadow] wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
