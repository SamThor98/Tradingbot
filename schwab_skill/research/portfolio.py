"""Portfolio construction for prob-rank research (equal-weight then dynamic)."""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

from research.counterfactual import _era_pf_mean_worst, _safe_pf, select_top_n_by_day


def equal_weight_by_day(df: pd.DataFrame) -> pd.DataFrame:
    """Assign equal weight within each entry day (sums to 1.0 per day)."""
    out = df.copy()
    if out.empty:
        out["position_weight"] = pd.Series(dtype=float)
        return out
    out["entry_iso"] = pd.to_datetime(out["entry_date"]).dt.strftime("%Y-%m-%d")
    counts = out.groupby("entry_iso")["ticker"].transform("count").astype(float)
    out["position_weight"] = 1.0 / counts.replace(0, np.nan)
    out["sizing_mode"] = "equal"
    return out


def _clip(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _cap_and_renormalize(weights: np.ndarray, cap: float) -> np.ndarray:
    """Iteratively cap weights and redistribute residual mass to uncapped names."""
    w = np.asarray(weights, dtype=np.float64).copy()
    n = len(w)
    if n == 0:
        return w
    if cap * n < 1.0 - 1e-12:
        # Infeasible equal-feasibility: fall back to equal weight
        return np.full(n, 1.0 / n)
    for _ in range(32):
        s = float(w.sum())
        if s <= 0:
            return np.full(n, 1.0 / n)
        w = w / s
        over = w > cap + 1e-12
        if not np.any(over):
            return w
        w = np.where(over, cap, w)
        residual = 1.0 - float(w.sum())
        under = ~over
        if not np.any(under) or residual <= 0:
            return w / max(float(w.sum()), 1e-12)
        pool = w[under]
        pool_sum = float(pool.sum())
        if pool_sum <= 0:
            w[under] = residual / float(under.sum())
        else:
            w[under] = pool + residual * (pool / pool_sum)
    s = float(w.sum())
    return w / s if s > 0 else np.full(n, 1.0 / n)


def edge_vol_raw_score(row: pd.Series) -> float:
    """
    size ∝ expected_edge × confidence / volatility

    Uses expected_return_40d as edge, confidence (default 0.5), atr_pct or
    expected_downside_40d as vol proxy.
    """
    edge = float(row.get("expected_return_40d") or 0.0)
    conf = float(row.get("confidence") if row.get("confidence") is not None else row.get("prob_rank_confidence") or 0.5)
    conf = _clip(conf, 0.05, 1.0)
    vol = row.get("atr_pct")
    if vol is None or (isinstance(vol, float) and (math.isnan(vol) or vol <= 0)):
        vol = row.get("expected_downside_40d")
    try:
        vol_f = float(vol) if vol is not None else 0.02
    except (TypeError, ValueError):
        vol_f = 0.02
    vol_f = max(vol_f, 1e-4)
    # Only long positive edge; negative edge → tiny residual for diagnostics
    edge_pos = max(edge, 0.0)
    return float(edge_pos * conf / vol_f)


def dynamic_weights(
    df: pd.DataFrame,
    *,
    max_position: float = 0.25,
    max_sector: float = 0.40,
    kelly_cap: float = 0.25,
    vol_target: float | None = None,
) -> pd.DataFrame:
    """
    Cross-sectional dynamic weights per entry day with portfolio constraints.

    - Normalize raw scores within day to sum to 1.0
    - Cap per-name at ``max_position`` and Kelly-style ``kelly_cap``
    - Cap sector sum at ``max_sector`` (uses ``sector_etf`` when present)
    - Optional vol targeting scales day gross exposure (metadata only for CF trades)
    """
    out = df.copy()
    if out.empty:
        out["position_weight"] = pd.Series(dtype=float)
        return out
    out = out.reset_index(drop=True)
    out["entry_iso"] = pd.to_datetime(out["entry_date"]).dt.strftime("%Y-%m-%d")
    out["raw_size_score"] = out.apply(edge_vol_raw_score, axis=1)
    weights = np.zeros(len(out), dtype=np.float64)
    for _day, ix in out.groupby("entry_iso").groups.items():
        positions = list(ix)
        day = out.loc[positions]
        raw = day["raw_size_score"].to_numpy(dtype=np.float64)
        if not np.isfinite(raw).any() or float(np.nansum(raw)) <= 0:
            w = np.full(len(positions), 1.0 / max(1, len(positions)))
        else:
            raw = np.maximum(np.nan_to_num(raw, nan=0.0), 0.0)
            total_raw = float(raw.sum())
            w = raw / total_raw if total_raw > 0 else np.full(len(positions), 1.0 / len(positions))
        cap = min(float(max_position), float(kelly_cap))
        w = _cap_and_renormalize(w, cap)
        if "sector_etf" in day.columns:
            sectors = day["sector_etf"].fillna("UNKNOWN").astype(str).tolist()
            for _ in range(8):
                by_sec: dict[str, float] = {}
                for i, sec in enumerate(sectors):
                    by_sec[sec] = by_sec.get(sec, 0.0) + float(w[i])
                overflow = {s: v for s, v in by_sec.items() if v > float(max_sector) + 1e-9}
                if not overflow:
                    break
                for sec, total in overflow.items():
                    scale = float(max_sector) / total
                    for i, sname in enumerate(sectors):
                        if sname == sec:
                            w[i] *= scale
                s = float(w.sum())
                if s > 0:
                    w = w / s
                w = _cap_and_renormalize(w, cap)
        if vol_target is not None and vol_target > 0 and "atr_pct" in day.columns:
            day_vol = float(pd.to_numeric(day["atr_pct"], errors="coerce").fillna(0.02).mean())
            if day_vol > 0:
                # Scale gross exposure but keep relative weights; do not re-break caps
                w = w * min(1.0, float(vol_target) / day_vol)
        for i, pos in enumerate(positions):
            weights[int(pos)] = float(w[i])
    out["position_weight"] = weights
    out["sizing_mode"] = "edge_vol"
    return out


def apply_sizing(
    selected: pd.DataFrame,
    *,
    mode: str = "equal",
    max_position: float = 0.25,
    max_sector: float = 0.40,
    kelly_cap: float = 0.25,
    vol_target: float | None = None,
) -> pd.DataFrame:
    mode_l = (mode or "equal").strip().lower()
    if mode_l in ("edge_vol", "dynamic", "edge"):
        return dynamic_weights(
            selected,
            max_position=max_position,
            max_sector=max_sector,
            kelly_cap=kelly_cap,
            vol_target=vol_target,
        )
    return equal_weight_by_day(selected)


def weighted_portfolio_metrics(df: pd.DataFrame) -> dict[str, Any]:
    """PF / expectancy using position_weight × net_return (research path)."""
    if df.empty or "net_return" not in df.columns:
        return {"n": 0, "pf": None, "mean_ret": None, "weighted_mean_ret": None}
    work = df.copy()
    if "position_weight" not in work.columns:
        work = equal_weight_by_day(work)
    ret = pd.to_numeric(work["net_return"], errors="coerce")
    w = pd.to_numeric(work["position_weight"], errors="coerce").fillna(0.0)
    # Weight-scaled returns for PF (treat as contribution)
    contrib = ret * w
    # For PF on contributions, scale up by average daily book so magnitudes comparable
    pf = _safe_pf(contrib)
    # Also report unweighted PF of selected trades (ranking lift isolation)
    pf_unweighted = _safe_pf(ret)
    pf_mean, worst = _era_pf_mean_worst(work)
    return {
        "n": int(len(work)),
        "pf_unweighted": pf_unweighted,
        "pf_weighted": pf,
        "pf_mean_eras": pf_mean,
        "worst_era_pf": worst,
        "mean_ret": float(ret.mean()) if ret.notna().any() else None,
        "weighted_mean_ret": float((ret * w).sum() / w.sum()) if float(w.sum()) > 0 else None,
        "mean_weight": float(w.mean()) if len(w) else None,
        "max_weight": float(w.max()) if len(w) else None,
        "sizing_mode": str(work["sizing_mode"].iloc[0]) if "sizing_mode" in work.columns else "equal",
    }


def sector_concentration(df: pd.DataFrame) -> dict[str, Any]:
    if df.empty or "sector_etf" not in df.columns:
        return {"by_sector": {}, "max_sector_share": None}
    work = df.copy()
    if "position_weight" not in work.columns:
        work = equal_weight_by_day(work)
    g = work.groupby(work["sector_etf"].fillna("UNKNOWN").astype(str))["position_weight"].sum()
    total = float(g.sum()) or 1.0
    shares = {str(k): round(float(v) / total, 4) for k, v in g.items()}
    return {"by_sector": shares, "max_sector_share": round(max(shares.values()), 4) if shares else None}


def run_portfolio_research(
    trades: pd.DataFrame,
    scored_features: pd.DataFrame,
    *,
    top_n: int = 5,
    sizing_mode: str = "equal",
    max_position: float = 0.25,
    max_sector: float = 0.40,
    kelly_cap: float = 0.25,
    vol_target: float | None = None,
    control_percentile: float = 75.0,
) -> dict[str, Any]:
    """
    Equal-weight (or dynamic) top-N portfolio research vs rank_v2 control.
    """
    from research.counterfactual import select_by_percentile
    from research.infer import attach_scores_to_trades

    merged = attach_scores_to_trades(trades, scored_features)
    baseline_pf, baseline_worst = _era_pf_mean_worst(merged)

    selected = select_top_n_by_day(merged, score_col="expected_return_40d", top_n=top_n)
    sized = apply_sizing(
        selected,
        mode=sizing_mode,
        max_position=max_position,
        max_sector=max_sector,
        kelly_cap=kelly_cap,
        vol_target=vol_target,
    )
    metrics = weighted_portfolio_metrics(sized)
    concentration = sector_concentration(sized)

    # Equal-weight top-N isolation run (always reported)
    ew = apply_sizing(selected, mode="equal")
    ew_metrics = weighted_portfolio_metrics(ew)

    control = None
    if "rank_score_v2" in merged.columns and merged["rank_score_v2"].notna().any():
        ctrl = select_by_percentile(merged, score_col="rank_score_v2", min_percentile=control_percentile)
        ctrl_sized = equal_weight_by_day(ctrl)
        c_pf, c_worst = _era_pf_mean_worst(ctrl_sized)
        control = {
            "score_col": "rank_score_v2",
            "min_percentile": control_percentile,
            "n": int(len(ctrl_sized)),
            "retention": round(len(ctrl_sized) / max(1, len(merged)), 4),
            "pf_mean": c_pf,
            "worst_era_pf": c_worst,
        }

    retention = round(len(selected) / max(1, len(merged)), 4)
    return {
        "n_trades": int(len(merged)),
        "n_selected": int(len(selected)),
        "retention": retention,
        "top_n": int(top_n),
        "baseline": {
            "n": int(len(merged)),
            "pf_mean": baseline_pf,
            "worst_era_pf": baseline_worst,
            "retention": 1.0,
        },
        "equal_weight_top_n": ew_metrics,
        "portfolio": {**metrics, "sector_concentration": concentration},
        "rank_v2_control": control,
        "sizing_mode": sizing_mode,
    }


def size_multiplier_for_signal(
    signal: dict[str, Any],
    *,
    mode: str = "equal",
    kelly_cap: float = 0.25,
) -> float:
    """
    Live/research helper: relative size multiplier vs equal weight.

    For equal mode returns 1.0. For edge_vol returns clipped score normalized
    so typical values land near 1.0 (caller still applies book caps).
    """
    mode_l = (mode or "equal").strip().lower()
    if mode_l in ("off", "equal", ""):
        return 1.0
    row = pd.Series(signal)
    raw = edge_vol_raw_score(row)
    # Map raw to multiplier around 1.0 with Kelly-ish cap
    mult = _clip(raw / 0.5, 0.25, 1.0 + float(kelly_cap) * 3)
    return float(min(mult, 1.0 + kelly_cap))
