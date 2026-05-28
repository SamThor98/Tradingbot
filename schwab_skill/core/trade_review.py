"""Phase 4 weekly diagnostics over decision packets.

Pure aggregation that turns the decision-packet store into the three operator
diagnostics:
- **false positives by regime** — loss rate among resolved decisions, grouped by regime
- **edge decay by setup type** — predicted edge vs realized return, grouped by setup
- **execution drag by market condition** — avg realized slippage, grouped by volatility state

Handles unresolved (``pending``) outcomes gracefully: rates are computed only over
labeled packets and coverage is reported alongside.
"""

from __future__ import annotations

from typing import Any


def _f(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _avg(xs: list[float]) -> float | None:
    return round(sum(xs) / len(xs), 4) if xs else None


def _is_loss(outcome: dict[str, Any]) -> bool | None:
    label = str((outcome or {}).get("label") or "pending").lower()
    if label == "pending" or label == "unknown":
        return None
    return label == "loss"


def false_positives_by_regime(packets: list[dict[str, Any]]) -> dict[str, Any]:
    groups: dict[str, dict[str, int]] = {}
    for p in packets:
        regime = str(p.get("regime_state") or "unknown")
        loss = _is_loss(p.get("outcome") or {})
        if loss is None:
            continue
        g = groups.setdefault(regime, {"resolved": 0, "losses": 0})
        g["resolved"] += 1
        if loss:
            g["losses"] += 1
    return {
        regime: {
            "resolved": g["resolved"],
            "losses": g["losses"],
            "fp_rate": round(g["losses"] / g["resolved"], 4) if g["resolved"] else None,
        }
        for regime, g in groups.items()
    }


def edge_decay_by_setup(packets: list[dict[str, Any]]) -> dict[str, Any]:
    groups: dict[str, dict[str, list[float]]] = {}
    for p in packets:
        setup = str(p.get("setup_type") or "unknown")
        edge = _f(p.get("edge_score")) or _f(p.get("rank_score"))
        realized = _f((p.get("outcome") or {}).get("realized_return_pct"))
        g = groups.setdefault(setup, {"edge": [], "realized": []})
        if edge is not None:
            g["edge"].append(edge)
        if realized is not None:
            g["realized"].append(realized)
    out: dict[str, Any] = {}
    for setup, g in groups.items():
        avg_edge = _avg(g["edge"])
        avg_realized = _avg(g["realized"])
        # Decay proxy: normalized predicted edge (0..1) minus realized return fraction.
        decay = None
        if avg_edge is not None and avg_realized is not None:
            decay = round((avg_edge / 100.0) - (avg_realized / 100.0), 4)
        out[setup] = {
            "avg_edge_score": avg_edge,
            "avg_realized_return_pct": avg_realized,
            "edge_decay": decay,
            "samples": len(g["edge"]),
            "resolved": len(g["realized"]),
        }
    return out


def execution_drag_by_condition(packets: list[dict[str, Any]]) -> dict[str, Any]:
    groups: dict[str, list[float]] = {}
    for p in packets:
        cond = str(p.get("volatility_state") or "unknown")
        slip = _f((p.get("outcome") or {}).get("realized_slippage_bps")) or _f(p.get("expected_slippage_bps"))
        if slip is not None:
            groups.setdefault(cond, []).append(slip)
    return {cond: {"avg_slippage_bps": _avg(xs), "samples": len(xs)} for cond, xs in groups.items()}


def weekly_report(packets: list[dict[str, Any]] | None) -> dict[str, Any]:
    """Full diagnostics report over the provided packets."""
    rows = [p for p in (packets or []) if isinstance(p, dict)]
    resolved = sum(1 for p in rows if _is_loss(p.get("outcome") or {}) is not None)
    return {
        "total_packets": len(rows),
        "resolved_packets": resolved,
        "coverage_pct": round(resolved / len(rows) * 100, 1) if rows else 0.0,
        "false_positives_by_regime": false_positives_by_regime(rows),
        "edge_decay_by_setup": edge_decay_by_setup(rows),
        "execution_drag_by_condition": execution_drag_by_condition(rows),
    }
