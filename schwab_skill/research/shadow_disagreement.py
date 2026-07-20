"""Attribute edge on days where prob-rank top-N and rank-v2 top-N disagree."""

from __future__ import annotations

from typing import Any

import pandas as pd

from research.counterfactual import _era_pf_mean_worst, _safe_pf


def _bucket_metrics(df: pd.DataFrame) -> dict[str, Any]:
    if df is None or df.empty or "net_return" not in df.columns:
        return {"n": 0, "pf": None, "mean_ret": None, "win_rate": None}
    ret = pd.to_numeric(df["net_return"], errors="coerce").dropna()
    if ret.empty:
        return {"n": int(len(df)), "pf": None, "mean_ret": None, "win_rate": None}
    wins = ret[ret > 0]
    return {
        "n": int(len(ret)),
        "pf": _safe_pf(ret),
        "mean_ret": round(float(ret.mean()), 6),
        "win_rate": round(float((ret > 0).mean()), 4),
        "sum_win": round(float(wins.sum()), 6) if len(wins) else 0.0,
        "sum_loss": round(float(ret[ret <= 0].sum()), 6),
    }


def analyze_topn_disagreement(
    merged: pd.DataFrame,
    *,
    top_n: int = 5,
    min_cohort: int = 8,
    score_col: str = "expected_return_40d",
    rank_v2_col: str = "rank_score_v2",
    max_days: int | None = None,
) -> dict[str, Any]:
    """
    On each entry-date with ≥ ``min_cohort`` scored trades, take top-N by
    ``score_col`` and top-N by ``rank_v2_col``, then attribute realized
    ``net_return`` to overlap / only_prob / only_v2 buckets.
    """
    if merged is None or getattr(merged, "empty", True):
        return {"ok": False, "error": "empty_merged"}
    need = {"ticker", "net_return", score_col, rank_v2_col}
    missing = need - set(merged.columns)
    if missing:
        return {"ok": False, "error": f"missing_cols:{sorted(missing)}"}

    df = merged.copy()
    df["ticker"] = df["ticker"].astype(str).str.upper()
    if "entry_iso" not in df.columns:
        df["entry_iso"] = pd.to_datetime(df["entry_date"]).dt.strftime("%Y-%m-%d")
    df = df[df[score_col].notna() & df[rank_v2_col].notna() & df["net_return"].notna()]
    if df.empty:
        return {"ok": False, "error": "no_scored_labeled_rows"}

    top_n = max(1, int(top_n))
    sizes = df.groupby("entry_iso").size().sort_values(ascending=False)
    dates = [d for d, n in sizes.items() if int(n) >= int(min_cohort)]
    if max_days is not None:
        dates = dates[: int(max_days)]

    parts: list[pd.DataFrame] = []
    day_stats: list[dict[str, Any]] = []
    for day in dates:
        grp = df[df["entry_iso"] == day]
        prob_keep = set(grp.sort_values(score_col, ascending=False).head(top_n)["ticker"])
        v2_keep = set(grp.sort_values(rank_v2_col, ascending=False).head(top_n)["ticker"])
        inter = prob_keep & v2_keep
        only_p = prob_keep - v2_keep
        only_v = v2_keep - prob_keep
        union = prob_keep | v2_keep
        jaccard = (len(inter) / len(union)) if union else None

        tagged = grp[grp["ticker"].isin(union)].copy()
        if tagged.empty:
            continue

        def _arm(t: str) -> str:
            if t in inter:
                return "overlap"
            if t in only_p:
                return "only_prob"
            return "only_v2"

        tagged["disagreement_arm"] = tagged["ticker"].map(_arm)
        parts.append(tagged)
        day_stats.append(
            {
                "entry_iso": str(day),
                "n_cohort": int(len(grp)),
                "jaccard": round(float(jaccard), 4) if jaccard is not None else None,
                "overlap_n": len(inter),
                "only_prob_n": len(only_p),
                "only_v2_n": len(only_v),
                "era": str(grp["era"].iloc[0]) if "era" in grp.columns else None,
            }
        )

    if not parts:
        return {"ok": False, "error": "no_day_cohorts", "min_cohort": min_cohort}

    all_sel = pd.concat(parts, ignore_index=True)
    buckets = {
        name: _bucket_metrics(all_sel[all_sel["disagreement_arm"] == name])
        for name in ("overlap", "only_prob", "only_v2")
    }
    # Full top-N arms (not just disagreement slices)
    full_prob_rows: list[pd.DataFrame] = []
    full_v2_rows: list[pd.DataFrame] = []
    for day in dates:
        grp = df[df["entry_iso"] == day]
        full_prob_rows.append(grp.sort_values(score_col, ascending=False).head(top_n))
        full_v2_rows.append(grp.sort_values(rank_v2_col, ascending=False).head(top_n))
    full_prob = pd.concat(full_prob_rows, ignore_index=True) if full_prob_rows else pd.DataFrame()
    full_v2 = pd.concat(full_v2_rows, ignore_index=True) if full_v2_rows else pd.DataFrame()

    pr_pf, pr_worst = _era_pf_mean_worst(full_prob) if not full_prob.empty else (None, None)
    v2_pf, v2_worst = _era_pf_mean_worst(full_v2) if not full_v2.empty else (None, None)

    only_p_pf = buckets["only_prob"].get("pf")
    only_v_pf = buckets["only_v2"].get("pf")
    verdict = "inconclusive"
    rationale: list[str] = []
    if only_p_pf is not None and only_v_pf is not None:
        gap = abs(float(only_p_pf) - float(only_v_pf))
        if gap < 0.03 and min(float(only_p_pf), float(only_v_pf)) >= 1.15:
            verdict = "near_tie"
            rationale.append(
                f"only_prob PF {only_p_pf:.3f} ≈ only_v2 PF {only_v_pf:.3f} "
                f"(gap {gap:.3f} < 0.03) — disagreement is not decisive"
            )
        elif only_p_pf >= 1.2 and only_p_pf > only_v_pf:
            verdict = "disagreement_favors_prob"
            rationale.append(
                f"only_prob PF {only_p_pf:.3f} > only_v2 PF {only_v_pf:.3f} and clears 1.20"
            )
        elif only_v_pf >= 1.2 and only_v_pf > only_p_pf:
            verdict = "disagreement_favors_rank_v2"
            rationale.append(
                f"only_v2 PF {only_v_pf:.3f} > only_prob PF {only_p_pf:.3f} and clears 1.20"
            )
        elif only_p_pf > only_v_pf:
            verdict = "slight_edge_prob"
            rationale.append(f"only_prob PF {only_p_pf:.3f} > only_v2 {only_v_pf:.3f} but below strong bar")
        elif only_v_pf > only_p_pf:
            verdict = "slight_edge_rank_v2"
            rationale.append(f"only_v2 PF {only_v_pf:.3f} > only_prob {only_p_pf:.3f}")
        else:
            rationale.append("only_prob and only_v2 PF tied")
    else:
        rationale.append("Insufficient PF in disagreement buckets")

    if pr_pf is not None and v2_pf is not None and pr_pf > v2_pf:
        rationale.append(f"Full top-{top_n}/day: prob PF {pr_pf:.3f} > rank_v2 {v2_pf:.3f}")
    elif pr_pf is not None and v2_pf is not None:
        rationale.append(f"Full top-{top_n}/day: rank_v2 PF {v2_pf:.3f} >= prob {pr_pf:.3f}")

    jaccards = [d["jaccard"] for d in day_stats if d.get("jaccard") is not None]
    return {
        "ok": True,
        "top_n": top_n,
        "min_cohort": min_cohort,
        "n_days": len(day_stats),
        "mean_jaccard": round(sum(jaccards) / len(jaccards), 4) if jaccards else None,
        "buckets": buckets,
        "full_top_n_arms": {
            "prob_rank": {
                "n": int(len(full_prob)),
                "pf_mean": pr_pf,
                "worst_era_pf": pr_worst,
                "metrics": _bucket_metrics(full_prob),
            },
            "rank_v2": {
                "n": int(len(full_v2)),
                "pf_mean": v2_pf,
                "worst_era_pf": v2_worst,
                "metrics": _bucket_metrics(full_v2),
            },
        },
        "by_era_only_prob": _by_era_bucket(all_sel, "only_prob"),
        "by_era_only_v2": _by_era_bucket(all_sel, "only_v2"),
        "verdict": verdict,
        "rationale": rationale,
        "day_stats_sample": day_stats[:10],
        "note": (
            "Low Jaccard is informative only if only_prob outperforms only_v2. "
            "Keep PROB_RANK_MODE=shadow; do not enable live from this alone."
        ),
    }


def _by_era_bucket(df: pd.DataFrame, arm: str) -> dict[str, Any]:
    if df.empty or "era" not in df.columns:
        return {}
    sub = df[df["disagreement_arm"] == arm]
    out: dict[str, Any] = {}
    for era, grp in sub.groupby("era"):
        out[str(era)] = _bucket_metrics(grp)
    return out
