"""Composite promotion gates for PROB_RANK_MODE (floors + soft score)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# Hard floors (same as phase2_edge_audit / stack validators)
PF_MEAN_FLOOR = 1.20
WORST_ERA_PF_FLOOR = 1.00

# Soft composite weights (design §9.2)
COMPOSITE_WEIGHTS: dict[str, float] = {
    "mean_pf": 0.20,
    "worst_era_pf": 0.20,
    "pf_stability": 0.10,
    "trade_count": 0.10,
    "retention": 0.05,
    "calibration": 0.10,
    "rank_ic": 0.10,
    "drift": 0.10,
    "cv_consistency": 0.05,
}


@dataclass(frozen=True)
class PromotionVerdict:
    decision: str  # promote_shadow | promote_live | hold | reject
    floors_cleared: bool
    composite_score: float | None
    rationale: list[str]
    gates: dict[str, Any]
    dimension_scores: dict[str, float]


def _clip01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _norm_pf_mean(pf: float | None) -> float:
    if pf is None:
        return 0.0
    # 1.0 → 0, 1.20 → 0.5, 1.40 → 1.0
    return _clip01((float(pf) - 1.0) / 0.40)


def _norm_worst(pf: float | None) -> float:
    if pf is None:
        return 0.0
    # 1.0 → 0.5, 1.20 → 1.0, <1.0 → lower
    return _clip01((float(pf) - 0.85) / 0.35)


def _norm_stability(bootstrap: dict[str, Any] | None) -> float:
    if not bootstrap:
        return 0.5
    lo = bootstrap.get("pf_lo")
    hi = bootstrap.get("pf_hi")
    mean = bootstrap.get("pf_mean")
    if lo is None or hi is None or mean is None or float(mean) <= 0:
        return 0.5
    width = float(hi) - float(lo)
    # Narrower CI relative to mean → higher score
    rel = width / max(float(mean), 1e-6)
    return _clip01(1.0 - rel)


def _norm_trade_count(n: int | None, *, min_n: int = 200, target_n: int = 2000) -> float:
    if n is None or n <= 0:
        return 0.0
    if n < min_n:
        return _clip01(n / min_n) * 0.5
    return _clip01(0.5 + 0.5 * (n - min_n) / max(1, target_n - min_n))


def _norm_retention(ret: float | None, *, target: float = 0.30, band: float = 0.15) -> float:
    """Prefer intentional selectivity near target (e.g. ~25–35% like rank p75)."""
    if ret is None:
        return 0.5
    r = float(ret)
    dist = abs(r - target)
    return _clip01(1.0 - dist / max(band, 1e-6))


def _norm_calibration(cal_error: float | None) -> float:
    if cal_error is None:
        return 0.5
    # Lower absolute calibration error better; 0 → 1, 0.1 → 0
    return _clip01(1.0 - float(cal_error) / 0.10)


def _norm_ic(ic: float | None) -> float:
    if ic is None:
        return 0.5
    # -0.05 → 0, 0 → 0.5, +0.05 → 1
    return _clip01((float(ic) + 0.05) / 0.10)


def _norm_drift(drift: float | None) -> float:
    """Live vs backtest absolute PF drift; lower is better."""
    if drift is None:
        return 0.5
    return _clip01(1.0 - abs(float(drift)) / 0.20)


def _norm_cv(cv_std: float | None) -> float:
    if cv_std is None:
        return 0.5
    return _clip01(1.0 - float(cv_std) / 0.05)


def compute_composite_promotion_score(metrics: dict[str, Any]) -> tuple[float, dict[str, float]]:
    """Return (weighted composite in [0,1], per-dimension scores)."""
    dims = {
        "mean_pf": _norm_pf_mean(metrics.get("pf_mean")),
        "worst_era_pf": _norm_worst(metrics.get("worst_era_pf")),
        "pf_stability": _norm_stability(metrics.get("bootstrap")),
        "trade_count": _norm_trade_count(metrics.get("n_trades") or metrics.get("n")),
        "retention": _norm_retention(metrics.get("retention")),
        "calibration": _norm_calibration(metrics.get("calibration_error")),
        "rank_ic": _norm_ic(metrics.get("walk_forward_ic_mean") or metrics.get("ic")),
        "drift": _norm_drift(metrics.get("live_backtest_pf_drift")),
        "cv_consistency": _norm_cv(metrics.get("cv_ic_std")),
    }
    score = 0.0
    for key, weight in COMPOSITE_WEIGHTS.items():
        score += weight * float(dims.get(key, 0.5))
    return round(score, 4), dims


def evaluate_prob_rank_promotion(
    metrics: dict[str, Any],
    *,
    requested: str = "shadow",
    min_composite_shadow: float = 0.45,
    min_composite_live: float = 0.60,
    require_dual_run_for_live: bool = True,
) -> PromotionVerdict:
    """
    Floors gate ship/no-ship; composite ranks quality among floor-clearing runs.

    ``requested``: shadow | live — aspiration; verdict may be hold/reject instead.
    """
    pf_mean = metrics.get("pf_mean")
    worst = metrics.get("worst_era_pf")
    rationale: list[str] = []
    floors_ok = (
        pf_mean is not None
        and worst is not None
        and float(pf_mean) >= PF_MEAN_FLOOR
        and float(worst) >= WORST_ERA_PF_FLOOR
    )
    composite, dims = compute_composite_promotion_score(metrics)

    gates = {
        "pf_mean": pf_mean,
        "pf_mean_floor": PF_MEAN_FLOOR,
        "worst_era_pf": worst,
        "worst_era_pf_floor": WORST_ERA_PF_FLOOR,
        "floors_cleared": floors_ok,
        "composite_score": composite,
        "min_composite_shadow": min_composite_shadow,
        "min_composite_live": min_composite_live,
        "requested": requested,
        "dual_run_ok": bool(metrics.get("dual_run_ok")),
        "n_trades": metrics.get("n_trades") or metrics.get("n"),
        "retention": metrics.get("retention"),
    }

    if not floors_ok:
        rationale.append(
            f"Hard floors failed: pf_mean={pf_mean} (need>={PF_MEAN_FLOOR}), "
            f"worst_era_pf={worst} (need>={WORST_ERA_PF_FLOOR})"
        )
        # Still allow shadow-only hold for observation if not catastrophic
        if pf_mean is not None and float(pf_mean) >= 1.05 and worst is not None and float(worst) >= 0.85:
            rationale.append("Metrics in iterate band — hold (do not promote)")
            return PromotionVerdict("hold", False, composite, rationale, gates, dims)
        rationale.append("Reject — below iterate band")
        return PromotionVerdict("reject", False, composite, rationale, gates, dims)

    rationale.append(
        f"Hard floors cleared: pf_mean={pf_mean}, worst_era_pf={worst}"
    )
    req = (requested or "shadow").strip().lower()
    if req == "live":
        if require_dual_run_for_live and not metrics.get("dual_run_ok"):
            rationale.append("Live requires dual_run_ok=true (shadow dual-run evidence)")
            if composite >= min_composite_shadow:
                rationale.append(f"Composite {composite} ≥ shadow bar — promote_shadow instead")
                return PromotionVerdict("promote_shadow", True, composite, rationale, gates, dims)
            rationale.append(f"Composite {composite} below shadow bar {min_composite_shadow} — hold")
            return PromotionVerdict("hold", True, composite, rationale, gates, dims)
        if composite < min_composite_live:
            rationale.append(f"Composite {composite} < live bar {min_composite_live}")
            if composite >= min_composite_shadow:
                return PromotionVerdict("promote_shadow", True, composite, rationale, gates, dims)
            return PromotionVerdict("hold", True, composite, rationale, gates, dims)
        rationale.append(f"Composite {composite} clears live bar {min_composite_live}")
        return PromotionVerdict("promote_live", True, composite, rationale, gates, dims)

    # shadow request
    if composite < min_composite_shadow:
        rationale.append(f"Composite {composite} < shadow bar {min_composite_shadow} — hold")
        return PromotionVerdict("hold", True, composite, rationale, gates, dims)
    rationale.append(f"Composite {composite} clears shadow bar {min_composite_shadow}")
    return PromotionVerdict("promote_shadow", True, composite, rationale, gates, dims)


def metrics_from_portfolio_result(result: dict[str, Any], *, extras: dict[str, Any] | None = None) -> dict[str, Any]:
    """Map ``run_portfolio_research`` / CF output into promotion metrics."""
    port = result.get("portfolio") or result.get("equal_weight_top_n") or result.get("prob_rank") or {}
    metrics: dict[str, Any] = {
        "pf_mean": port.get("pf_mean_eras") or port.get("pf_mean") or port.get("pf_unweighted"),
        "worst_era_pf": port.get("worst_era_pf"),
        "n_trades": port.get("n") or result.get("n_selected") or result.get("n_trades"),
        "retention": result.get("retention") or port.get("retention"),
    }
    # Prefer equal-weight isolation metrics for promotion (design: isolate ranking lift)
    ew = result.get("equal_weight_top_n") or {}
    if ew.get("pf_mean_eras") is not None:
        metrics["pf_mean"] = ew.get("pf_mean_eras")
        metrics["worst_era_pf"] = ew.get("worst_era_pf")
        metrics["n_trades"] = ew.get("n")
    if extras:
        metrics.update(extras)
    return metrics
