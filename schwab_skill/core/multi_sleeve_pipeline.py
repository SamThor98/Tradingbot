"""Attach multi-sleeve diagnostics pack to a scan session (shadow-safe)."""

from __future__ import annotations

from typing import Any

from core.portfolio_allocator import evaluate_allocator
from core.sleeve_research_chips import apply_r3_capacity_to_rows
from core.sleeve_weights import build_multi_sleeve_targets


def build_multi_sleeve_diagnostics(
    *,
    signals: list[dict[str, Any]],
    pead_canary: dict[str, Any] | None = None,
    mode: str = "off",
    data_quality: str = "ok",
    regime_bear: bool = False,
    crash_active: bool = False,
    drawdown_from_peak: float | None = None,
    s1_live: bool = False,
    s1_promoted_cap: bool = False,
    r3_capacity_shadow: bool = False,
    current_weights: dict[str, float] | None = None,
) -> dict[str, Any]:
    """Compute allocator decision + desired weights for diagnostics / paper trading."""
    if str(mode or "off").lower() == "off":
        return {"mode": "off", "enabled": False}

    s0_rows = [dict(s) for s in (signals or []) if isinstance(s, dict)]
    if r3_capacity_shadow:
        s0_rows = apply_r3_capacity_to_rows(s0_rows)
        score_field = "signal_score_r3"
    else:
        score_field = "signal_score"

    s1_rows: list[dict[str, Any]] = []
    if isinstance(pead_canary, dict):
        for n in pead_canary.get("names") or []:
            if isinstance(n, dict) and n.get("ticker"):
                s1_rows.append(dict(n))

    decision = evaluate_allocator(
        mode=mode,
        data_quality=data_quality,
        regime_bear=regime_bear,
        crash_active=crash_active,
        drawdown_from_peak=drawdown_from_peak,
        s1_promoted_cap=s1_promoted_cap,
    )
    targets = build_multi_sleeve_targets(
        s0_rows=s0_rows,
        s1_rows=s1_rows or None,
        decision=decision,
        current_weights=current_weights,
        s0_score_field=score_field,
        s1_score_field="edge_score",
        s1_paper_only=not s1_live,
    )
    return {
        "mode": mode,
        "enabled": True,
        "s1_live": bool(s1_live),
        "r3_capacity_shadow": bool(r3_capacity_shadow),
        **targets,
    }
