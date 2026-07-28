"""Sleeve attribution model D: counterfactual books + filled-share + cost by |Δw|."""

from __future__ import annotations

from typing import Any


def counterfactual_sleeve_pnl(
    sleeve_weights: dict[str, float],
    returns: dict[str, float],
) -> dict[str, Any]:
    """Mark sleeve P&L as Σ w_i * r_i (as-if solo under those weights)."""
    pnl = 0.0
    contrib: dict[str, float] = {}
    for tkr, w in (sleeve_weights or {}).items():
        key = str(tkr).upper()
        r = float(returns.get(key) or 0.0)
        c = float(w) * r
        contrib[key] = c
        pnl += c
    return {"pnl": pnl, "contributions": contrib, "method": "counterfactual_weight_path"}


def filled_share_attribution(
    sleeve_desired_pre_net: dict[str, dict[str, float]],
    fill_notional_by_ticker: dict[str, float],
) -> dict[str, dict[str, float]]:
    """Allocate fills to sleeves proportional to pre-net desired weight per ticker."""
    out: dict[str, dict[str, float]] = {}
    for tkr, fill in (fill_notional_by_ticker or {}).items():
        key = str(tkr).upper()
        desires = {
            sleeve: float(wmap.get(key) or 0.0)
            for sleeve, wmap in (sleeve_desired_pre_net or {}).items()
        }
        total = sum(max(0.0, v) for v in desires.values())
        if total <= 0 or fill == 0:
            continue
        for sleeve, d in desires.items():
            if d <= 0:
                continue
            out.setdefault(sleeve, {})[key] = float(fill) * (d / total)
    return out


def split_costs_by_abs_delta_w(
    sleeve_delta_w: dict[str, float],
    total_cost: float,
) -> dict[str, float]:
    """Split a shared cost pool proportional to sleeve |Δw|."""
    abs_map = {s: abs(float(v)) for s, v in (sleeve_delta_w or {}).items()}
    denom = sum(abs_map.values())
    if denom <= 0 or total_cost == 0:
        return {s: 0.0 for s in abs_map}
    return {s: float(total_cost) * (a / denom) for s, a in abs_map.items()}


def sleeve_delta_gross(
    prev_weights: dict[str, float],
    next_weights: dict[str, float],
) -> float:
    """Sum of absolute weight changes across tickers."""
    keys = set(prev_weights or {}) | set(next_weights or {})
    return sum(
        abs(float((next_weights or {}).get(k, 0.0)) - float((prev_weights or {}).get(k, 0.0)))
        for k in keys
    )


def build_attribution_snapshot(
    *,
    sleeve_weights: dict[str, dict[str, float]],
    returns: dict[str, float],
    fill_notional_by_ticker: dict[str, float] | None = None,
    prev_sleeve_weights: dict[str, dict[str, float]] | None = None,
    total_cost: float = 0.0,
) -> dict[str, Any]:
    """One-session attribution pack for promotion monitoring."""
    counterfactual: dict[str, Any] = {}
    delta_by_sleeve: dict[str, float] = {}
    for sleeve, wmap in (sleeve_weights or {}).items():
        counterfactual[sleeve] = counterfactual_sleeve_pnl(wmap, returns)
        prev = (prev_sleeve_weights or {}).get(sleeve) or {}
        delta_by_sleeve[sleeve] = sleeve_delta_gross(prev, wmap)

    filled = filled_share_attribution(
        sleeve_weights,
        fill_notional_by_ticker or {},
    )
    costs = split_costs_by_abs_delta_w(delta_by_sleeve, total_cost)
    return {
        "method": "D",
        "counterfactual": counterfactual,
        "filled_share": filled,
        "costs_by_sleeve": costs,
        "delta_w_by_sleeve": delta_by_sleeve,
    }
