"""Side-by-side counterfactual: attach prob-rank scores to frozen trades."""

from __future__ import annotations

import statistics
from typing import Any

import pandas as pd

from research.infer import attach_scores_to_trades


def _safe_pf(series: pd.Series) -> float | None:
    s = pd.to_numeric(series, errors="coerce").dropna()
    if s.empty:
        return None
    wins = float(s[s > 0].sum())
    losses = float(-s[s <= 0].sum())
    if losses <= 0:
        return None if wins <= 0 else 99.0
    return round(wins / losses, 4)


def _era_pf_mean_worst(df: pd.DataFrame) -> tuple[float | None, float | None]:
    if "era" not in df.columns or df.empty:
        pf = _safe_pf(df["net_return"]) if "net_return" in df.columns else None
        return pf, pf
    pfs: list[float] = []
    for _, grp in df.groupby("era"):
        pf = _safe_pf(grp["net_return"])
        if pf is not None:
            pfs.append(float(pf))
    if not pfs:
        return None, None
    return round(statistics.mean(pfs), 4), round(min(pfs), 4)


def select_top_n_by_day(df: pd.DataFrame, *, score_col: str, top_n: int) -> pd.DataFrame:
    """Keep top-N trades per entry calendar day by score."""
    if df.empty or top_n <= 0:
        return df.iloc[0:0].copy()
    work = df.dropna(subset=[score_col]).copy()
    work["entry_iso"] = pd.to_datetime(work["entry_date"]).dt.strftime("%Y-%m-%d")
    parts: list[pd.DataFrame] = []
    for _, grp in work.groupby("entry_iso"):
        parts.append(grp.nlargest(int(top_n), score_col))
    if not parts:
        return work.iloc[0:0].copy()
    return pd.concat(parts, ignore_index=True)


def select_by_percentile(df: pd.DataFrame, *, score_col: str, min_percentile: float) -> pd.DataFrame:
    """Keep rows with score >= cohort percentile (global, matching rank-v2 CF style)."""
    work = df.dropna(subset=[score_col]).copy()
    if work.empty:
        return work
    thr = float(work[score_col].quantile(min_percentile / 100.0))
    return work[work[score_col] >= thr].copy()


def run_prob_rank_counterfactual(
    trades: pd.DataFrame,
    scored_features: pd.DataFrame,
    *,
    top_n: int | None = 5,
    min_percentile: float | None = None,
    control_score_col: str = "rank_score_v2",
    control_percentile: float = 75.0,
    score_col: str = "expected_return_40d",
    pre_merged: pd.DataFrame | None = None,
) -> dict[str, Any]:
    """
    Compare baseline vs prob-rank selection vs rank_v2 percentile control.

    ``trades`` needs: ticker, entry_date, net_return, era (optional), rank_score_v2 (for control).
    ``scored_features`` needs: ticker, asof_date, and ``score_col`` (default expected_return_40d).
    Pass ``pre_merged`` to reuse a calibrated trade frame.
    """
    merged = pre_merged if pre_merged is not None else attach_scores_to_trades(trades, scored_features)
    if score_col not in merged.columns and score_col != "expected_return_40d":
        raise ValueError(f"score_col {score_col} missing from merged frame")
    baseline_pf, baseline_worst = _era_pf_mean_worst(merged)

    # Prob-rank selection
    if min_percentile is not None:
        selected = select_by_percentile(merged, score_col=score_col, min_percentile=min_percentile)
        selection = {"mode": "percentile", "min_percentile": min_percentile, "score_col": score_col}
    else:
        selected = select_top_n_by_day(merged, score_col=score_col, top_n=int(top_n or 5))
        selection = {"mode": "top_n_per_day", "top_n": int(top_n or 5), "score_col": score_col}
    pr_pf, pr_worst = _era_pf_mean_worst(selected)

    control = None
    ctrl_sel = None
    if control_score_col in merged.columns and merged[control_score_col].notna().any():
        ctrl_sel = select_by_percentile(merged, score_col=control_score_col, min_percentile=control_percentile)
        c_pf, c_worst = _era_pf_mean_worst(ctrl_sel)
        control = {
            "score_col": control_score_col,
            "min_percentile": control_percentile,
            "n": int(len(ctrl_sel)),
            "retention": round(len(ctrl_sel) / max(1, len(merged)), 4),
            "pf_mean": c_pf,
            "worst_era_pf": c_worst,
        }

    scored_n = int(merged["expected_return_40d"].notna().sum()) if "expected_return_40d" in merged.columns else 0

    def _by_era(df: pd.DataFrame) -> dict[str, Any]:
        if df.empty or "era" not in df.columns:
            return {}
        out: dict[str, Any] = {}
        for era, grp in df.groupby("era"):
            pf = _safe_pf(grp["net_return"])
            out[str(era)] = {"n": int(len(grp)), "pf": pf}
        return out

    if control is not None and ctrl_sel is not None:
        control = {**control, "by_era": _by_era(ctrl_sel)}

    return {
        "n_trades": int(len(merged)),
        "n_scored": scored_n,
        "coverage": round(scored_n / max(1, len(merged)), 4),
        "baseline": {
            "n": int(len(merged)),
            "pf_mean": baseline_pf,
            "worst_era_pf": baseline_worst,
            "retention": 1.0,
            "by_era": _by_era(merged),
        },
        "prob_rank": {
            **selection,
            "n": int(len(selected)),
            "retention": round(len(selected) / max(1, len(merged)), 4),
            "pf_mean": pr_pf,
            "worst_era_pf": pr_worst,
            "by_era": _by_era(selected),
        },
        "rank_v2_control": control,
    }
