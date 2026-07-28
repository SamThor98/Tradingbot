"""Offline allocator overlay on closed multi-era trades (research CF).

Applies constitution admission rules chronologically:
- drawdown staircase (soft/hard/floor) via running equity from accepted trades
- optional per-day top-N (S0 cardinality)
- optional crash window (explicit date ranges) as crash_active

This is an **admission gate** on the trade book — not a full daily desired-weight
net book. Suitable for offline Pareto checks while RTH shadow runs in parallel.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd

from core.multi_sleeve_constitution import (
    DD_FLOOR,
    DD_HARD,
    DD_SOFT,
    PF_MEAN_FLOOR,
    PF_WORST_ERA_FLOOR,
    TOP_N_S0,
)
from core.portfolio_allocator import evaluate_allocator


@dataclass(frozen=True)
class CrashWindow:
    """Inclusive calendar crash window (YYYY-MM-DD)."""

    start: str
    end: str


# Heuristic SPY shock windows when no SPY series is available (research proxy).
# Also include mid-era stress bands that overlap typical control_legacy_aug chunk
# calendars (chunks often start mid-October within each ERA_BOUNDS window).
DEFAULT_CRASH_WINDOWS: tuple[CrashWindow, ...] = (
    CrashWindow("2020-02-20", "2020-04-15"),
    CrashWindow("2020-10-15", "2020-11-15"),  # chunk-visible COVID aftermath stress
    CrashWindow("2022-01-03", "2022-03-15"),
    CrashWindow("2022-10-18", "2022-11-30"),  # chunk-visible bear_rates start
)


def _ts(value: Any) -> pd.Timestamp:
    return pd.Timestamp(value).normalize()


def in_crash_window(entry_date: Any, windows: tuple[CrashWindow, ...] | list[CrashWindow]) -> bool:
    d = _ts(entry_date)
    for w in windows:
        if _ts(w.start) <= d <= _ts(w.end):
            return True
    return False


def summarize_overlay_arm(
    df: pd.DataFrame,
    *,
    label: str,
    baseline_n: int,
) -> dict[str, Any]:
    """PF mean / worst-era / retention + constitution floor flags (Stage2 gates)."""
    from scripts.analyze_rank_filter_counterfactual import _cohort_stats, _era_pf_from_df
    from scripts.phase2_common import ERA_BOUNDS

    if df is None or df.empty:
        return {
            "label": label,
            "n_trades": 0,
            "retention_pct": 0.0,
            "pf_all": None,
            "pf_mean": None,
            "worst_era_pf": None,
            "n_eras": 0,
            "per_era_pf": {},
            "passes_pf_floors": False,
            "passes_dd_floor_note": "n/a_empty",
            "verdict": "fail_empty",
        }

    pf_mean, worst, n_eras = _era_pf_from_df(df)
    cohort = _cohort_stats(df)
    per_era_pf: dict[str, float | None] = {}
    for era in ERA_BOUNDS:
        sub = df[df["era"] == era]
        if sub.empty:
            per_era_pf[era] = None
        else:
            from scripts.analyze_rank_filter_counterfactual import _safe_pf

            per_era_pf[era] = _safe_pf(sub["net_return"])

    retention = round(100.0 * len(df) / baseline_n, 2) if baseline_n > 0 else None
    passes = bool(
        pf_mean is not None
        and worst is not None
        and float(pf_mean) >= PF_MEAN_FLOOR
        and float(worst) >= PF_WORST_ERA_FLOOR
        and n_eras >= 5
    )
    return {
        "label": label,
        "n_trades": int(len(df)),
        "retention_pct": retention,
        "pf_all": cohort.get("pf"),
        "pf_mean": pf_mean,
        "worst_era_pf": worst,
        "n_eras": n_eras,
        "early_stopout_pct": cohort.get("early_stopout_pct"),
        "per_era_pf": per_era_pf,
        "passes_pf_floors": passes,
        "verdict": "pass" if passes else "fail",
    }


def apply_top_n_per_day(
    df: pd.DataFrame,
    *,
    top_n: int = TOP_N_S0,
    score_col: str = "signal_score",
) -> pd.DataFrame:
    """Keep top-N names per entry calendar day by score (ties: ticker asc)."""
    if df.empty:
        return df.copy()
    work = df.copy()
    work["entry_day"] = pd.to_datetime(work["entry_date"]).dt.normalize()
    if score_col not in work.columns or work[score_col].notna().sum() == 0:
        # No scores: keep first top_n by ticker within day
        work["_score"] = 0.0
        work["_tie"] = work.get("ticker", pd.Series([""] * len(work))).astype(str)
        work = work.sort_values(["entry_day", "_tie"])
        kept = work.groupby("entry_day", group_keys=False).head(int(top_n))
        return kept.drop(columns=[c for c in ("_score", "_tie", "entry_day") if c in kept.columns])

    work["_score"] = pd.to_numeric(work[score_col], errors="coerce").fillna(-1e18)
    work["_tie"] = work.get("ticker", pd.Series([""] * len(work))).astype(str).str.upper()
    work = work.sort_values(["entry_day", "_score", "_tie"], ascending=[True, False, True])
    kept = work.groupby("entry_day", group_keys=False).head(int(top_n))
    return kept.drop(columns=[c for c in ("_score", "_tie", "entry_day") if c in kept.columns])


def apply_allocator_admission(
    df: pd.DataFrame,
    *,
    use_dd_staircase: bool = True,
    use_crash_windows: bool = True,
    crash_windows: tuple[CrashWindow, ...] | list[CrashWindow] | None = None,
    drop_on_soft_dd: bool = False,
    position_frac: float | None = None,
    max_concurrent: int = TOP_N_S0,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Chronologically admit trades under allocator new-risk multipliers.

    Soft DD: default keeps trade; set drop_on_soft_dd to reject instead.
    Hard/floor/crash: reject new entries.
    Equity marks use ``position_frac * net_return`` (default name cap) so DD
    reflects a capped book rather than 100% compounding every trade.
    """
    from core.multi_sleeve_constitution import CAP_NAME

    if df.empty:
        return df.copy(), {"accepted": 0, "rejected": 0, "reasons": {}}

    windows = tuple(crash_windows) if crash_windows is not None else DEFAULT_CRASH_WINDOWS
    frac = float(CAP_NAME if position_frac is None else position_frac)
    frac = max(0.01, min(0.25, frac))
    work = df.copy()
    work["entry_date"] = pd.to_datetime(work["entry_date"])
    if "exit_date" not in work.columns or work["exit_date"].isna().all():
        hold = pd.to_numeric(work.get("hold_days"), errors="coerce").fillna(20).astype(int)
        work["exit_date"] = work["entry_date"] + pd.to_timedelta(hold, unit="D")
    else:
        work["exit_date"] = pd.to_datetime(work["exit_date"])
        missing = work["exit_date"].isna()
        if missing.any():
            hold = pd.to_numeric(work.get("hold_days"), errors="coerce").fillna(20).astype(int)
            work.loc[missing, "exit_date"] = work.loc[missing, "entry_date"] + pd.to_timedelta(
                hold.loc[missing],
                unit="D",
            )
    work = work.sort_values(["entry_date", "ticker"], kind="mergesort").reset_index(drop=True)

    equity = 1.0
    peak = 1.0
    # pending exits: list of (exit_date, contribution)
    pending: list[tuple[pd.Timestamp, float]] = []
    accepted_idx: list[int] = []
    reason_counts: dict[str, int] = {}

    def _flush_exits(asof: pd.Timestamp) -> None:
        nonlocal equity, peak, pending
        still: list[tuple[pd.Timestamp, float]] = []
        for ex, contrib in pending:
            if ex <= asof:
                equity *= 1.0 + float(contrib)
                peak = max(peak, equity)
            else:
                still.append((ex, contrib))
        pending = still

    for i, row in work.iterrows():
        entry = pd.Timestamp(row["entry_date"]).normalize()
        _flush_exits(entry)
        open_n = len(pending)
        if open_n >= int(max_concurrent):
            reason_counts["max_concurrent"] = reason_counts.get("max_concurrent", 0) + 1
            continue
        dd = 0.0 if peak <= 0 else max(0.0, (peak - equity) / peak)
        crash = bool(use_crash_windows and in_crash_window(entry, windows))
        decision = evaluate_allocator(
            mode="shadow",
            crash_active=crash if use_crash_windows else False,
            drawdown_from_peak=dd if use_dd_staircase else 0.0,
        )
        mult = float(decision.s0_new_risk_mult)
        if not decision.allow_new_risk or mult <= 0.0:
            key = decision.reasons[0] if decision.reasons else "blocked"
            reason_counts[key] = reason_counts.get(key, 0) + 1
            continue
        if decision.dd_tier == "soft" and drop_on_soft_dd:
            reason_counts["dd_soft_drop"] = reason_counts.get("dd_soft_drop", 0) + 1
            continue
        accepted_idx.append(int(i))
        sized = frac * float(row["net_return"]) * (0.5 if decision.dd_tier == "soft" else 1.0)
        pending.append((pd.Timestamp(row["exit_date"]).normalize(), sized))

    if pending:
        last_exit = max(ex for ex, _ in pending)
        _flush_exits(last_exit)

    out = work.loc[accepted_idx].copy() if accepted_idx else work.iloc[0:0].copy()
    diag = {
        "accepted": len(accepted_idx),
        "rejected": int(len(work) - len(accepted_idx)),
        "reasons": reason_counts,
        "ending_equity": round(equity, 6),
        "ending_dd": round(0.0 if peak <= 0 else max(0.0, (peak - equity) / peak), 6),
        "position_frac": frac,
        "max_concurrent": int(max_concurrent),
        "dd_soft": DD_SOFT,
        "dd_hard": DD_HARD,
        "dd_floor": DD_FLOOR,
        "crash_windows": [{"start": w.start, "end": w.end} for w in windows]
        if use_crash_windows
        else [],
        "crash_window_note": (
            "Heuristic windows; control_legacy_aug era chunks often start mid-window "
            "and may miss classic crash dates — DD staircase still applies."
        ),
    }
    return out, diag


def run_allocator_overlay_arms(df: pd.DataFrame) -> dict[str, Any]:
    """Run control + overlay arms; return report dict."""
    baseline_n = int(len(df))
    arms: list[dict[str, Any]] = []

    control = summarize_overlay_arm(df, label="control", baseline_n=baseline_n)
    control["diagnostics"] = {"accepted": baseline_n, "rejected": 0}
    arms.append(control)

    top_n = apply_top_n_per_day(df, top_n=TOP_N_S0)
    arm_top = summarize_overlay_arm(top_n, label="top5_per_day", baseline_n=baseline_n)
    arm_top["diagnostics"] = {"accepted": int(len(top_n)), "rejected": baseline_n - int(len(top_n))}
    arms.append(arm_top)

    dd_only, dd_diag = apply_allocator_admission(
        df,
        use_dd_staircase=True,
        use_crash_windows=False,
    )
    arm_dd = summarize_overlay_arm(dd_only, label="dd_staircase_only", baseline_n=baseline_n)
    arm_dd["diagnostics"] = dd_diag
    arms.append(arm_dd)

    crash_only, crash_diag = apply_allocator_admission(
        df,
        use_dd_staircase=False,
        use_crash_windows=True,
    )
    arm_crash = summarize_overlay_arm(crash_only, label="crash_windows_only", baseline_n=baseline_n)
    arm_crash["diagnostics"] = crash_diag
    arms.append(arm_crash)

    # Combined: top-N then allocator (DD + crash)
    combined_src = top_n
    combined, comb_diag = apply_allocator_admission(
        combined_src,
        use_dd_staircase=True,
        use_crash_windows=True,
    )
    arm_comb = summarize_overlay_arm(
        combined,
        label="top5_then_dd_and_crash",
        baseline_n=baseline_n,
    )
    arm_comb["diagnostics"] = comb_diag
    arms.append(arm_comb)

    return _pack_report(baseline_n, arms)


def sweep_allocator_overlay_arms(df: pd.DataFrame) -> dict[str, Any]:
    """Grid-search allocator knobs for best floor-clearing / soft-rank arm."""
    baseline_n = int(len(df))
    arms: list[dict[str, Any]] = []

    control = summarize_overlay_arm(df, label="control", baseline_n=baseline_n)
    control["diagnostics"] = {"accepted": baseline_n, "rejected": 0}
    control["params"] = {}
    arms.append(control)

    for top_n in (3, 5, 7):
        trimmed = apply_top_n_per_day(df, top_n=top_n)
        for use_dd in (False, True):
            for use_crash in (False, True):
                for max_c in (top_n, max(top_n, 5)):
                    if not use_dd and not use_crash and max_c == top_n:
                        # pure top-n
                        label = f"top{top_n}_only"
                        arm = summarize_overlay_arm(trimmed, label=label, baseline_n=baseline_n)
                        arm["diagnostics"] = {
                            "accepted": int(len(trimmed)),
                            "rejected": baseline_n - int(len(trimmed)),
                        }
                        arm["params"] = {"top_n": top_n}
                        arms.append(arm)
                        continue
                    if not use_dd and not use_crash:
                        continue
                    kept, diag = apply_allocator_admission(
                        trimmed,
                        use_dd_staircase=use_dd,
                        use_crash_windows=use_crash,
                        max_concurrent=max_c,
                    )
                    label = (
                        f"top{top_n}"
                        f"{'_dd' if use_dd else ''}"
                        f"{'_crash' if use_crash else ''}"
                        f"_c{max_c}"
                    )
                    arm = summarize_overlay_arm(kept, label=label, baseline_n=baseline_n)
                    arm["diagnostics"] = diag
                    arm["params"] = {
                        "top_n": top_n,
                        "use_dd": use_dd,
                        "use_crash": use_crash,
                        "max_concurrent": max_c,
                    }
                    arms.append(arm)

    # Deduplicate by label (keep first)
    seen: set[str] = set()
    uniq: list[dict[str, Any]] = []
    for a in arms:
        lab = str(a.get("label"))
        if lab in seen:
            continue
        seen.add(lab)
        uniq.append(a)

    report = _pack_report(baseline_n, uniq)
    report["sweep"] = True
    report["n_arms"] = len(uniq)
    floor_clearers = [a for a in uniq if a.get("passes_pf_floors")]
    report["n_floor_clearers"] = len(floor_clearers)
    # Soft-rank table (floors first, then pf_mean, retention, pf_all)
    ranked = sorted(
        uniq,
        key=lambda a: (
            1 if a.get("passes_pf_floors") else 0,
            float(a.get("pf_mean") or 0.0),
            float(a.get("retention_pct") or 0.0),
            float(a.get("pf_all") or 0.0),
        ),
        reverse=True,
    )
    report["soft_rank_top5"] = ranked[:5]

    # Capacity-aware pick: floors + retention >= 50% of stack book (constitution soft #2)
    capacity_pool = [
        a
        for a in floor_clearers
        if float(a.get("retention_pct") or 0.0) >= 50.0
    ]
    if capacity_pool:
        capacity_pick = sorted(
            capacity_pool,
            key=lambda a: (
                float(a.get("pf_mean") or 0.0),
                float(a.get("worst_era_pf") or 0.0),
                float(a.get("retention_pct") or 0.0),
            ),
            reverse=True,
        )[0]
        report["capacity_aware_recommended"] = {
            "label": capacity_pick.get("label"),
            "reason": "max pf_mean among floor-clearers with retention>=50%",
            "arm": capacity_pick,
        }
    else:
        report["capacity_aware_recommended"] = {
            "label": None,
            "reason": "no floor-clearer retained >=50%",
            "arm": None,
        }
    return report


def _pack_report(baseline_n: int, arms: list[dict[str, Any]]) -> dict[str, Any]:
    floor_clearers = [a for a in arms if a.get("passes_pf_floors")]
    if floor_clearers:
        recommended = sorted(
            floor_clearers,
            key=lambda a: (
                float(a.get("pf_mean") or 0.0),
                float(a.get("retention_pct") or 0.0),
            ),
            reverse=True,
        )[0]
        reason = "max pf_mean among floor-clearers (then retention)"
    else:
        recommended = max(
            arms,
            key=lambda a: (
                float(a.get("pf_mean") or 0.0),
                float(a.get("worst_era_pf") or 0.0),
                float(a.get("retention_pct") or 0.0),
            ),
        )
        reason = "no arm cleared floors; best pf_mean/worst/retention shown"

    return {
        "baseline_n": baseline_n,
        "arms": arms,
        "recommended": {
            "label": recommended.get("label"),
            "reason": reason,
            "arm": recommended,
        },
        "constitution_floors": {
            "pf_mean": PF_MEAN_FLOOR,
            "worst_era_pf": PF_WORST_ERA_FLOOR,
            "max_dd": DD_FLOOR,
        },
    }


def make_demo_trade_frame(*, n_per_era: int = 80) -> pd.DataFrame:
    """Synthetic multi-era book for smoke tests when chunks are absent."""
    from scripts.phase2_common import ERA_BOUNDS

    rows: list[dict[str, Any]] = []
    for era, (start, end) in ERA_BOUNDS.items():
        start_ts = pd.Timestamp(start)
        for i in range(n_per_era):
            entry = start_ts + pd.Timedelta(days=i * 3)
            # Mild positive expectancy with some losers
            net = 0.04 if i % 5 else -0.03
            if era == "bear_rates" and i % 3 == 0:
                net = -0.05
            rows.append(
                {
                    "era": era,
                    "ticker": f"T{i % 20}",
                    "entry_date": entry,
                    "exit_date": entry + pd.Timedelta(days=20),
                    "net_return": net,
                    "signal_score": float(100 - (i % 20)),
                    "hold_days": 20,
                }
            )
    return pd.DataFrame(rows)
