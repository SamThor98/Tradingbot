"""Offline measurements for the open P0 questions.

Walk-forward selection fits its cutoff on the other eras, then scores the
holdout. Nothing here writes a live sort key or an exit change.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from research.counterfactual import _era_pf_mean_worst

PF_MEAN_FLOOR = 1.20
WORST_FLOOR = 1.00
MIN_LIFT = 0.03


def arm_stats(df: pd.DataFrame) -> dict[str, Any]:
    mean, worst = _era_pf_mean_worst(df)
    return {
        "n": int(len(df)),
        "pf_mean": mean,
        "worst_era_pf": worst,
        "clears_floors": bool(
            mean is not None and worst is not None and mean >= PF_MEAN_FLOOR and worst >= WORST_FLOOR
        ),
    }


def walk_forward_percentile(
    df: pd.DataFrame,
    *,
    score_col: str,
    min_percentile: float = 76.0,
) -> pd.DataFrame:
    """Keep holdout rows at or above the cutoff learned on the other eras."""
    if df.empty or "era" not in df.columns or score_col not in df.columns:
        return df.iloc[0:0].copy()
    parts: list[pd.DataFrame] = []
    for era in df["era"].astype(str).unique():
        train = pd.to_numeric(df.loc[df["era"].astype(str) != era, score_col], errors="coerce").dropna()
        if train.empty:
            continue
        threshold = float(train.quantile(min_percentile / 100.0))
        hold = df.loc[df["era"].astype(str) == era]
        score = pd.to_numeric(hold[score_col], errors="coerce")
        parts.append(hold.loc[score >= threshold])
    if not parts:
        return df.iloc[0:0].copy()
    return pd.concat(parts, ignore_index=True)


def surprise_vs_return(df: pd.DataFrame, surprises: list[Any]) -> dict[str, Any]:
    """Spearman IC of an as-of surprise against net return. No survival label required."""
    from core.scoring_metrics import spearman_corr

    frame = pd.DataFrame(
        {
            "era": df["era"].astype(str).to_numpy(),
            "net": pd.to_numeric(df["net_return"], errors="coerce").to_numpy(),
            "x": pd.to_numeric(pd.Series(list(surprises)), errors="coerce").to_numpy(),
        }
    )

    def _one(part: pd.DataFrame) -> float | None:
        pair = part.dropna()
        if len(pair) < 50 or int(pair["x"].nunique()) < 3:
            return None
        val = spearman_corr(pair["x"], pair["net"])
        if val != val:
            return None
        return round(float(val), 4)

    ics = [ic for _era, grp in frame.groupby("era") if (ic := _one(grp)) is not None]
    signs = [1 if v > 0 else -1 for v in ics if v != 0]
    consistent = max(signs.count(1), signs.count(-1)) if signs else 0
    return {
        "ic_net_return": _one(frame),
        "sign_consistent_eras": consistent,
        "n_eras_with_ic": len(ics),
    }


def sort_key_verdict(arms: dict[str, dict[str, Any]]) -> str:
    """Promotion stays closed. This only says whether the shadow sort cleared."""
    challenger = arms.get("wf_p76_inv_pts52w") or {}
    baseline = arms.get("take_every_trade") or {}
    live = arms.get("top5_signal_score") or {}
    if not challenger.get("clears_floors"):
        return "do_not_promote_sort_key"
    base_pf = baseline.get("pf_mean")
    live_pf = live.get("pf_mean")
    chal_pf = challenger.get("pf_mean")
    if chal_pf is None:
        return "do_not_promote_sort_key"
    beats_base = base_pf is None or (chal_pf - float(base_pf)) >= MIN_LIFT
    beats_live = live_pf is None or float(chal_pf) > float(live_pf)
    if beats_base and beats_live:
        return "shadow_ready_sort_key_not_promoted"
    return "clears_floors_but_not_enough_lift"
