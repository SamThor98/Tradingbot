"""PEAD-primary capacity-aware counterfactual helpers (research only).

Selects / sizes on a frozen PEAD trade book without Stage2 transfer defaults.
PF gates remain trade-level; portfolio metrics are capacity notes only.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from research.counterfactual import select_by_percentile, select_top_n_by_day

PF_MEAN_TARGET = 1.50
WORST_ERA_FLOOR = 1.00
MIN_ERA_TRADES = 50
REQUIRED_ERAS = 5

# PEAD-native score columns (not Stage2 breakout / pts / rank-v2 defaults).
PEAD_NATIVE_SCORE_COLS = ("signal_score", "composite_score", "edge_score")

# Explicit reject-unless-clears transfer arms (Stage2 stack pieces).
TRANSFER_ARM_LABELS = (
    "transfer_breakout_buffer_ge_0.010",
    "transfer_pts_52w_cap_37",
    "transfer_rank_v2_p76",
)


def trade_key(era: Any, ticker: Any, entry_date: Any) -> tuple[str, str, str]:
    return (
        str(era or ""),
        str(ticker or "").upper(),
        pd.Timestamp(entry_date).strftime("%Y-%m-%d") if entry_date is not None else "",
    )


def summarize_arm(
    df: pd.DataFrame,
    *,
    label: str,
    baseline_n: int,
    family: str,
    is_transfer_arm: bool = False,
) -> dict[str, Any]:
    """Five-era equal-weight net PF summary + thin-era / gate flags."""
    from scripts.analyze_rank_filter_counterfactual import _cohort_stats, _era_pf_from_df

    if df.empty:
        return {
            "label": label,
            "family": family,
            "is_transfer_arm": bool(is_transfer_arm),
            "n_trades": 0,
            "retention_pct": 0.0,
            "pf_all": None,
            "pf_mean": None,
            "worst_era_pf": None,
            "n_eras": 0,
            "per_era_n": {},
            "per_era_pf": {},
            "thin_eras": list(_era_names()),
            "passes_pf_150": False,
            "verdict": "fail_empty",
        }

    pf_mean, worst, n_eras = _era_pf_from_df(df)
    cohort = _cohort_stats(df)
    per_era_n = {str(era): int(len(g)) for era, g in df.groupby("era")}
    thin_eras = [e for e, n in per_era_n.items() if n < MIN_ERA_TRADES]
    missing_eras = [e for e in _era_names() if e not in per_era_n]
    thin_eras = sorted(set(thin_eras) | set(missing_eras))
    retention = round(100.0 * len(df) / baseline_n, 2) if baseline_n > 0 else None
    passes = bool(
        pf_mean is not None
        and worst is not None
        and pf_mean >= PF_MEAN_TARGET
        and worst >= WORST_ERA_FLOOR
        and n_eras >= REQUIRED_ERAS
        and not thin_eras
    )
    if passes:
        verdict = "pass"
    elif is_transfer_arm:
        verdict = "reject_transfer_unless_clears"
    else:
        verdict = "fail"
    return {
        "label": label,
        "family": family,
        "is_transfer_arm": bool(is_transfer_arm),
        "n_trades": int(len(df)),
        "retention_pct": retention,
        "pf_all": cohort.get("pf"),
        "pf_mean": pf_mean,
        "worst_era_pf": worst,
        "n_eras": n_eras,
        "early_stopout_pct": cohort.get("early_stopout_pct"),
        "per_era_n": per_era_n,
        "per_era_pf": {
            str(era): _cohort_stats(group).get("pf") for era, group in df.groupby("era")
        },
        "thin_eras": thin_eras,
        "passes_pf_150": passes,
        "verdict": verdict,
    }


def _era_names() -> list[str]:
    from scripts.phase2_common import ERA_BOUNDS

    return list(ERA_BOUNDS.keys())


def select_top_n_arm(df: pd.DataFrame, *, score_col: str, top_n: int) -> pd.DataFrame:
    return select_top_n_by_day(df, score_col=score_col, top_n=top_n)


def select_percentile_arm(df: pd.DataFrame, *, score_col: str, min_percentile: float) -> pd.DataFrame:
    return select_by_percentile(df, score_col=score_col, min_percentile=min_percentile)


def select_transfer_breakout_buffer(df: pd.DataFrame, *, min_buf: float = 0.01) -> pd.DataFrame:
    buf = pd.to_numeric(df.get("breakout_buffer_pct"), errors="coerce")
    return df[buf.notna() & (buf >= float(min_buf))].copy()


def select_transfer_pts_cap(df: pd.DataFrame, *, cap: float = 37.0) -> pd.DataFrame:
    pts = pd.to_numeric(df.get("pts_52w"), errors="coerce")
    # Fail-open on missing pts (matches other CFs).
    return df[pts.isna() | (pts <= float(cap))].copy()


def select_transfer_rank_v2(df: pd.DataFrame, *, percentile: float = 76.0) -> pd.DataFrame:
    from core.scoring_rank_v2 import score_percentile_threshold

    if "rank_score_v2" not in df.columns:
        return df.iloc[0:0].copy()
    scored = df[df["rank_score_v2"].notna()].copy()
    if len(scored) < 3:
        return scored.iloc[0:0].copy()
    thr = score_percentile_threshold(
        scored["rank_score_v2"].astype(float).tolist(),
        int(percentile),
    )
    return scored[scored["rank_score_v2"].astype(float) >= float(thr)].copy()


def portfolio_note(
    df: pd.DataFrame,
    *,
    max_positions: int,
    risk_per_trade_pct: float,
    position_size_pct: float,
    starting_equity: float,
    score_col: str | None = None,
) -> dict[str, Any]:
    """Capacity-aware portfolio sim note (not a PF gate).

    When ``score_col`` is set, same-day entries are sorted score-desc before
    the max-positions fill so higher PEAD-native scores win scarce slots.
    """
    from backtest import _simulate_portfolio_equity

    if df.empty:
        return {
            "max_positions": int(max_positions),
            "risk_per_trade_pct": float(risk_per_trade_pct),
            "n_input": 0,
            "capacity_filtered": 0,
            "accepted_pct": 0.0,
            "total_return_net_pct": 0.0,
            "max_drawdown_net_pct": 0.0,
            "score_priority": score_col,
        }

    work = df.copy()
    work["entry_date"] = pd.to_datetime(work["entry_date"], errors="coerce")
    work["exit_date"] = pd.to_datetime(work["exit_date"], errors="coerce")
    work = work.dropna(subset=["entry_date", "exit_date"])
    if score_col and score_col in work.columns:
        work["_score_priority"] = pd.to_numeric(work[score_col], errors="coerce").fillna(-1e18)
        work = work.sort_values(
            ["entry_date", "_score_priority", "exit_date"],
            ascending=[True, False, True],
        )
    else:
        work = work.sort_values(["entry_date", "exit_date"])

    rows = work.to_dict(orient="records")
    sim = _simulate_portfolio_equity(
        rows,
        starting_equity=float(starting_equity),
        max_concurrent_positions=int(max_positions),
        position_size_pct=float(position_size_pct),
        risk_per_trade_pct=float(risk_per_trade_pct),
    )
    n_in = len(rows)
    filtered = int(sim.get("capacity_filtered") or 0)
    accepted = max(0, n_in - filtered)
    return {
        "max_positions": int(max_positions),
        "risk_per_trade_pct": float(risk_per_trade_pct),
        "position_size_pct": float(position_size_pct),
        "starting_equity": float(starting_equity),
        "n_input": n_in,
        "capacity_filtered": filtered,
        "accepted": accepted,
        "accepted_pct": round(100.0 * accepted / n_in, 2) if n_in else 0.0,
        "total_return_net_pct": sim.get("total_return_net_pct"),
        "max_drawdown_net_pct": sim.get("max_drawdown_net_pct"),
        "avg_concurrent": sim.get("avg_concurrent"),
        "peak_concurrent": sim.get("peak_concurrent"),
        "score_priority": score_col,
    }


def recommend_operating_stack(arms: list[dict[str, Any]]) -> dict[str, Any]:
    """Pick best passing PEAD-native arm for continued shadow (not live)."""
    native_pass = [
        a
        for a in arms
        if a.get("passes_pf_150")
        and not a.get("is_transfer_arm")
        and a.get("family") in {"baseline", "top_n", "percentile", "exit_grace"}
    ]
    transfer_pass = [a for a in arms if a.get("is_transfer_arm") and a.get("passes_pf_150")]
    selection_pass = [a for a in native_pass if a.get("family") in {"top_n", "percentile"}]
    pool = selection_pass or native_pass

    def _rank_key(a: dict[str, Any]) -> tuple[float, float, float, float]:
        port = a.get("portfolio") or {}
        # Prefer capacity-aware selection: higher accepted_pct, then PF quality.
        return (
            float(port.get("accepted_pct") or 0.0),
            float(a.get("worst_era_pf") or 0.0),
            float(a.get("pf_mean") or 0.0),
            float(a.get("retention_pct") or 0.0),
        )

    best = max(pool, key=_rank_key) if pool else None
    best_exit = next(
        (a for a in native_pass if a.get("family") == "exit_grace" and a.get("passes_pf_150")),
        None,
    )
    return {
        "pead_live_allowed": False,
        "strategy_pead_primary_allow_live": False,
        "executable_entry_family": "stage2_only",
        "pead_mode": "shadow_dual_admit",
        "recommended_arm": best.get("label") if best else None,
        "recommended_summary": (
            {
                "label": best.get("label"),
                "family": best.get("family"),
                "pf_mean": best.get("pf_mean"),
                "worst_era_pf": best.get("worst_era_pf"),
                "retention_pct": best.get("retention_pct"),
                "n_trades": best.get("n_trades"),
                "portfolio": best.get("portfolio"),
            }
            if best
            else None
        ),
        "entry": "pead_primary beat + liquidity (dual-admit shadow; not executable)",
        "exit": (
            f"{best_exit.get('label')} (PF-clears on PEAD book)"
            if best_exit
            else "exit_grace_t15_h40 preferred when PF-neutral vs bare; do not graft Stage2 buffer"
        ),
        "rank": (
            f"PEAD-native top-N / percentile on {', '.join(PEAD_NATIVE_SCORE_COLS)}; "
            "do not default Stage2 rank-v2 / pts_52w / breakout buffer"
        ),
        "sizing": "BACKTEST_PORTFOLIO_MAX_POSITIONS + BACKTEST_RISK_PER_TRADE_PCT with score-priority fill",
        "transfer_arms_cleared": [a.get("label") for a in transfer_pass],
        "transfer_policy": (
            "reject Stage2 buffer/pts/rank-v2 as PEAD defaults unless independently clearing "
            f"PF mean>={PF_MEAN_TARGET} / worst>={WORST_ERA_FLOOR} / no thin eras"
        ),
        "lookback_note": (
            "PEAD_LOOKBACK_DAYS / PEAD_PRIMARY_LOOKBACK_DAYS soft sweeps require new "
            "full-universe entry books; not inferred from the fixed lookback=20 cache"
        ),
    }
