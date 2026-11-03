#!/usr/bin/env python3
"""Walk-forward counterfactual: analog overlay vs live p76 + historical_top5.

For each held-out era the analog library is built from the *other* eras only,
then holdout trades are scored through ``core.scoring_analog``. That is the
leakage contract: a holdout row never sits in its own neighbor set.

Arms:
  baseline               every trade
  current_live_p76       rank-v2 p76
  current_live_p76_top5  incumbent: p76 then top-N/day by signal_score
  historical_top5        existing shadow bar (recomputed, not hardcoded)
  analog_top5            top-N/day by analog_p_win (no new hard filter)
  analog_p76             diagnostic percentile cut — not a promotion candidate

Verdict:
  analog_top5 PF mean < historical_top5  → education_only
  analog beats historical but PF mean < 1.20 or worst-era < 1.00 → keep_shadow
  LIVE sort/filter stays blocked regardless.

Usage (from schwab_skill/):
  python scripts/analyze_analog_overlay_counterfactual.py
  python scripts/analyze_analog_overlay_counterfactual.py --run-id control_legacy_aug
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

SKILL_DIR = Path(__file__).resolve().parent.parent
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

from core.scoring_analog import (  # noqa: E402
    DEFAULT_K,
    DEFAULT_MIN_N,
    AnalogLibrary,
    score_analog,
)
from core.scoring_historical import compute_historical_rank, load_blend_artifact  # noqa: E402
from core.scoring_metrics import spearman_corr  # noqa: E402
from research.counterfactual import (  # noqa: E402
    _era_pf_mean_worst,
    _safe_pf,
    select_by_percentile,
    select_top_n_by_day,
)
from research.report import calibration_deciles  # noqa: E402
from scripts.build_analog_library import (  # noqa: E402
    _rows_from_frame,
    build_library_from_rows,
    load_analog_source_frame,
)
from scripts.validate_scoring_metrics import _load_trade_frame  # noqa: E402

ART = SKILL_DIR / "validation_artifacts" / "analog_overlay"
PROMOTION_PF_MEAN = 1.20
PROMOTION_WORST_ERA_PF = 1.00
CURRENT_LIVE_PERCENTILE = 76.0
DEFAULT_RUN_ID = "control_legacy_aug"


def _signal_row_from_trade(row: dict[str, Any]) -> dict[str, Any]:
    out = dict(row)
    out["score_components"] = {
        "pts_volume": row.get("pts_volume"),
        "pts_mirofish": row.get("pts_mirofish"),
        "pts_52w": row.get("pts_52w"),
        "pct_from_52w_high": row.get("pct_from_52w_high"),
        "avg_vcp_volume_ratio": row.get("avg_vcp_volume_ratio"),
    }
    return out


def _score_historical(trades: pd.DataFrame, artifact: dict[str, Any] | None) -> pd.Series:
    if artifact is None:
        return pd.Series([None] * len(trades), index=trades.index)
    scores: list[float | None] = []
    for row in trades.to_dict("records"):
        block = compute_historical_rank(_signal_row_from_trade(row), artifact)
        scores.append(None if block is None else float(block["score"]))
    return pd.Series(scores, index=trades.index)


def _walk_forward_analog(
    frame: pd.DataFrame,
    *,
    sources: list[str],
    k: int,
    min_n: int,
) -> pd.DataFrame:
    """Score each era using a library that excludes that era."""
    work = frame.copy()
    work["analog_p_win"] = np.nan
    work["analog_neighborhood_pf"] = np.nan
    work["analog_n"] = np.nan
    work["analog_thin"] = False
    work["analog_regime"] = pd.NA
    work["analog_library_eras"] = pd.NA
    eras = [str(e) for e in work["era"].dropna().unique()]
    records = _rows_from_frame(work)
    by_era: dict[str, list[int]] = {}
    for i, row in enumerate(records):
        by_era.setdefault(str(row.get("era") or "unknown"), []).append(i)

    for holdout in sorted(eras):
        train_idx = [i for era, idxs in by_era.items() if era != holdout for i in idxs]
        test_idx = by_era.get(holdout) or []
        if not train_idx or not test_idx:
            continue
        train_rows = [records[i] for i in train_idx]
        try:
            artifact = build_library_from_rows(
                train_rows,
                sources=[*sources, f"walk_forward_holdout={holdout}"],
                k=k,
                min_n=min_n,
                fail_closed=False,
            )
        except ValueError:
            continue
        lib = AnalogLibrary(
            model_id=str(artifact.get("model_id") or "wf"),
            feature_names=tuple(artifact.get("features") or []),
            bounds=artifact.get("feature_bounds") or {},
            k=k,
            min_n=min_n,
            rows=list(artifact.get("rows") or []),
            priors=artifact.get("era_priors") or {},
        )
        train_eras = sorted({str(r.get("era") or "") for r in artifact.get("rows") or []})
        holdout_in_lib = any(str(r.get("era")) == holdout for r in (artifact.get("rows") or []))
        if holdout_in_lib:
            raise RuntimeError(f"walk-forward leak: holdout era {holdout} present in library")
        for i in test_idx:
            block = score_analog(records[i], lib, k=k, min_n=min_n)
            if block is None:
                continue
            work.iat[i, work.columns.get_loc("analog_p_win")] = block.get("analog_p_win")
            work.iat[i, work.columns.get_loc("analog_neighborhood_pf")] = block.get("analog_neighborhood_pf")
            work.iat[i, work.columns.get_loc("analog_n")] = block.get("analog_n")
            work.iat[i, work.columns.get_loc("analog_thin")] = block.get("analog_thin")
            work.iat[i, work.columns.get_loc("analog_regime")] = block.get("analog_regime")
            work.iat[i, work.columns.get_loc("analog_library_eras")] = ",".join(train_eras)
    return work


def _by_era(df: pd.DataFrame) -> dict[str, Any]:
    if df.empty or "era" not in df.columns:
        return {}
    return {
        str(era): {"n": int(len(grp)), "pf": _safe_pf(grp["net_return"])}
        for era, grp in df.groupby("era")
    }


def _arm(name: str, df: pd.DataFrame, total: int, *, selection: str) -> dict[str, Any]:
    pf_mean, worst = _era_pf_mean_worst(df)
    return {
        "arm": name,
        "selection": selection,
        "n": int(len(df)),
        "retention": round(len(df) / max(1, total), 4),
        "pf_mean": pf_mean,
        "worst_era_pf": worst,
        "by_era": _by_era(df),
        "gates": {
            "pf_mean_ok": bool(pf_mean is not None and pf_mean >= PROMOTION_PF_MEAN),
            "worst_era_ok": bool(worst is not None and worst >= PROMOTION_WORST_ERA_PF),
        },
    }


def _beats(challenger: dict[str, Any], incumbent: dict[str, Any]) -> dict[str, Any]:
    c_pf, c_worst = challenger.get("pf_mean"), challenger.get("worst_era_pf")
    i_pf, i_worst = incumbent.get("pf_mean"), incumbent.get("worst_era_pf")
    pf_ok = c_pf is not None and i_pf is not None and c_pf > i_pf
    worst_ok = c_worst is not None and i_worst is not None and c_worst > i_worst
    return {
        "vs": incumbent.get("arm"),
        "pf_mean_delta": None if (c_pf is None or i_pf is None) else round(c_pf - i_pf, 4),
        "worst_era_delta": None if (c_worst is None or i_worst is None) else round(c_worst - i_worst, 4),
        "beats_pf_mean": bool(pf_ok),
        "beats_worst_era": bool(worst_ok),
        "beats_incumbent": bool(pf_ok and worst_ok),
    }


def _cross_sectional_ic(df: pd.DataFrame, score_col: str, ret_col: str) -> float | None:
    if score_col not in df.columns or ret_col not in df.columns:
        return None
    work = df[[score_col, ret_col, "entry_date"]].copy() if "entry_date" in df.columns else df[[score_col, ret_col]].copy()
    work[score_col] = pd.to_numeric(work[score_col], errors="coerce")
    work[ret_col] = pd.to_numeric(work[ret_col], errors="coerce")
    work = work.dropna(subset=[score_col, ret_col])
    if len(work) < 8:
        return None
    if "entry_date" in work.columns:
        ics: list[float] = []
        for _, grp in work.groupby(pd.to_datetime(work["entry_date"]).dt.strftime("%Y-%m-%d")):
            if len(grp) < 3:
                continue
            ic = spearman_corr(grp[score_col], grp[ret_col])
            if ic == ic:
                ics.append(float(ic))
        if ics:
            return round(float(sum(ics) / len(ics)), 4)
    ic = spearman_corr(work[score_col], work[ret_col])
    return None if ic != ic else round(float(ic), 4)


def _md_summary(result: dict[str, Any]) -> str:
    arms = result.get("arms") or {}
    lines = [
        "# Analog overlay counterfactual",
        "",
        f"Run `{result.get('run_id')}` · model `{result.get('model_id')}` · {result.get('created_at_utc')}",
        "",
        f"Verdict: **{result.get('verdict')}**",
        "",
        "| Arm | n | Retention | PF mean | Worst-era PF |",
        "|---|---|---|---|---|",
    ]
    for name in (
        "baseline",
        "current_live_p76",
        "current_live_p76_top5",
        "historical_top5",
        "analog_top5",
        "analog_p76",
    ):
        arm = arms.get(name) or {}
        lines.append(
            f"| `{name}` | {arm.get('n', '')} | {arm.get('retention', '')} | "
            f"{arm.get('pf_mean', '')} | {arm.get('worst_era_pf', '')} |"
        )
    ic = result.get("analog_ic") or {}
    lines.extend(
        [
            "",
            f"Analog IC vs net_return: {ic.get('vs_net_return')}",
            f"Analog IC vs ret_40d_fwd: {ic.get('vs_ret_40d_fwd')}",
            "",
            "ANALOG_OVERLAY_MODE stays shadow; live selection unchanged.",
            "",
        ]
    )
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--run-id", default=DEFAULT_RUN_ID)
    parser.add_argument("--top-n", type=int, default=5)
    parser.add_argument("--percentile", type=float, default=76.0)
    parser.add_argument("--k", type=int, default=DEFAULT_K)
    parser.add_argument("--min-n", type=int, default=DEFAULT_MIN_N)
    parser.add_argument("--out", default=None)
    args = parser.parse_args(argv)

    try:
        analog_frame, sources = load_analog_source_frame(args.run_id)
    except Exception as exc:
        print(f"FAIL: could not load analog source for {args.run_id}: {exc}")
        return 2

    try:
        live_frame = _load_trade_frame(args.run_id)
    except Exception as exc:
        print(f"FAIL: could not load trades for {args.run_id}: {exc}")
        return 2

    # Align analog extras onto the scoring trade frame (rank_score_v2 / signal_score).
    analog_frame["entry_date"] = pd.to_datetime(analog_frame["entry_date"], errors="coerce")
    live_frame["entry_date"] = pd.to_datetime(live_frame["entry_date"], errors="coerce")
    extra_cols = [
        c
        for c in analog_frame.columns
        if c not in live_frame.columns or c in {"ticker", "entry_date"}
    ]
    keep = ["ticker", "entry_date"] + [c for c in extra_cols if c not in {"ticker", "entry_date"}]
    scored = live_frame.merge(
        analog_frame[keep].drop_duplicates(subset=["ticker", "entry_date"]),
        on=["ticker", "entry_date"],
        how="left",
    )

    hist_artifact = load_blend_artifact(SKILL_DIR)
    scored["historical_rank"] = _score_historical(scored, hist_artifact)
    scored = _walk_forward_analog(scored, sources=sources, k=args.k, min_n=args.min_n)
    scored["analog_p_win"] = pd.to_numeric(scored["analog_p_win"], errors="coerce")
    scored["analog_neighborhood_pf"] = pd.to_numeric(scored["analog_neighborhood_pf"], errors="coerce")
    scored["historical_rank"] = pd.to_numeric(scored["historical_rank"], errors="coerce")

    total = len(scored)
    coverage = float(pd.to_numeric(scored["analog_p_win"], errors="coerce").notna().mean()) if total else 0.0
    print(
        f"Trades: {total} | analog coverage: {coverage:.4f} | "
        f"k={args.k} min_n={args.min_n} | sources={sources}"
    )

    baseline = _arm("baseline", scored, total, selection="all trades")

    live_p76 = select_by_percentile(scored, score_col="rank_score_v2", min_percentile=CURRENT_LIVE_PERCENTILE)
    current_p76 = _arm(
        "current_live_p76",
        live_p76,
        total,
        selection=f"rank_score_v2 >= p{CURRENT_LIVE_PERCENTILE:g}",
    )
    live_top = select_top_n_by_day(live_p76, score_col="signal_score", top_n=args.top_n)
    current_stack = _arm(
        "current_live_p76_top5",
        live_top,
        total,
        selection=f"rank_score_v2 >= p{CURRENT_LIVE_PERCENTILE:g}, then top-{args.top_n}/day by signal_score",
    )

    hist_top = select_top_n_by_day(scored, score_col="historical_rank", top_n=args.top_n)
    hist_topn = _arm(
        "historical_top5",
        hist_top,
        total,
        selection=f"top-{args.top_n}/day by historical_rank",
    )

    analog_top = select_top_n_by_day(scored, score_col="analog_p_win", top_n=args.top_n)
    analog_topn = _arm(
        "analog_top5",
        analog_top,
        total,
        selection=f"top-{args.top_n}/day by analog_p_win (walk-forward library)",
    )
    analog_pct = select_by_percentile(scored, score_col="analog_p_win", min_percentile=args.percentile)
    analog_p76 = _arm(
        "analog_p76",
        analog_pct,
        total,
        selection=f"analog_p_win >= p{args.percentile:g} (diagnostic only)",
    )

    arms = [baseline, current_p76, current_stack, hist_topn, analog_topn, analog_p76]
    print("\nArm                     n      ret     PF mean   worst era  gates")
    for arm in arms:
        gates = "pass" if arm["gates"]["pf_mean_ok"] and arm["gates"]["worst_era_ok"] else "fail"
        print(
            f"  {arm['arm']:22s} {arm['n']:6d}  {arm['retention']:.3f}   "
            f"{str(arm['pf_mean']):>8s}   {str(arm['worst_era_pf']):>8s}  {gates}"
        )

    analog_ic = {
        "vs_net_return": _cross_sectional_ic(scored, "analog_p_win", "net_return"),
        "vs_ret_40d_fwd": _cross_sectional_ic(scored, "analog_p_win", "ret_40d_fwd")
        if "ret_40d_fwd" in scored.columns
        else None,
    }
    print(f"\nAnalog IC (cross-sectional Spearman): vs net_return {analog_ic['vs_net_return']} "
          f"vs ret_40d_fwd {analog_ic['vs_ret_40d_fwd']}")

    cal_df = scored.dropna(subset=["analog_p_win", "net_return"]).copy()
    deciles = calibration_deciles(cal_df, score_col="analog_p_win", label_col="net_return") if len(cal_df) >= 20 else []
    if deciles and "analog_p_win" in cal_df.columns:
        try:
            cal_df = cal_df.copy()
            cal_df["bin"] = pd.qcut(cal_df["analog_p_win"], 10, labels=False, duplicates="drop")
            pf_by_bin = {int(b): _safe_pf(grp["net_return"]) for b, grp in cal_df.groupby("bin")}
            for item in deciles:
                item["pf"] = pf_by_bin.get(int(item.get("bin", -1)))
        except Exception:
            pass

    vs_hist = _beats(analog_topn, hist_topn)
    vs_live = _beats(analog_topn, current_stack)
    hist_pf = hist_topn.get("pf_mean")
    analog_pf = analog_topn.get("pf_mean")
    gates_ok = bool(analog_topn["gates"]["pf_mean_ok"] and analog_topn["gates"]["worst_era_ok"])
    if analog_pf is None or hist_pf is None or analog_pf < hist_pf:
        verdict = "education_only"
    elif not gates_ok or not vs_live["beats_incumbent"]:
        verdict = "keep_shadow"
    else:
        # Floors cleared and beats live — still do not promote this wave.
        verdict = "keep_shadow"

    print("\nVs historical_top5 / current_live_p76_top5:")
    print(
        f"  analog_top5 vs historical_top5  dPF {vs_hist['pf_mean_delta']} "
        f"dWorst {vs_hist['worst_era_delta']} beats={vs_hist['beats_incumbent']}"
    )
    print(
        f"  analog_top5 vs live_p76_top5    dPF {vs_live['pf_mean_delta']} "
        f"dWorst {vs_live['worst_era_delta']} beats={vs_live['beats_incumbent']}"
    )
    print(f"\nVERDICT: {verdict}")
    print("  ANALOG_OVERLAY_MODE stays shadow; live selection unchanged.")
    print("  HISTORICAL_RANK_MODE stays shadow; live selection unchanged.")

    result = {
        "run_id": args.run_id,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "model_id": "walk_forward_leave_one_era_out",
        "sources": sources,
        "n_trades": total,
        "analog_coverage": round(coverage, 4),
        "k": args.k,
        "min_n": args.min_n,
        "gates": {"pf_mean_min": PROMOTION_PF_MEAN, "worst_era_pf_min": PROMOTION_WORST_ERA_PF},
        "top_n": args.top_n,
        "percentile": args.percentile,
        "arms": {arm["arm"]: arm for arm in arms},
        "comparisons": {
            "analog_top5_vs_historical_top5": vs_hist,
            "analog_top5_vs_current_live_p76_top5": vs_live,
        },
        "analog_ic": analog_ic,
        "calibration_deciles": deciles,
        "verdict": verdict,
        "promote": False,
    }

    out_path = Path(args.out) if args.out else ART / f"counterfactual_{args.run_id}.json"
    if not out_path.is_absolute():
        out_path = SKILL_DIR / out_path
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    md_path = out_path.with_suffix(".md")
    md_path.write_text(_md_summary(result), encoding="utf-8")
    print(f"\nWrote {out_path.relative_to(SKILL_DIR)}")
    print(f"Wrote {md_path.relative_to(SKILL_DIR)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
