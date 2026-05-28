"""Phase 4 feedback loop: advisory tuning proposals from the weekly review.

Turns a :func:`core.trade_review.weekly_report` into concrete, *advisory*
proposals for scanner weight tuning and guardrail thresholds. Proposals are
never auto-applied — they roll out OFF -> SHADOW -> LIVE through human review,
matching the project's plugin convention.
"""

from __future__ import annotations

from typing import Any

# Minimum resolved samples before a proposal is trustworthy.
_MIN_SAMPLES = 20
# False-positive rate that triggers a tightening proposal.
_FP_RATE_HIGH = 0.55
# Edge-decay (predicted minus realized) that flags an over-weighted setup.
_DECAY_HIGH = 0.10


def propose(report: dict[str, Any] | None) -> dict[str, Any]:
    """Return advisory tuning proposals. Pure; applies nothing."""
    rep = report or {}
    proposals: list[dict[str, Any]] = []

    # 1) Regime false positives -> raise the quality floor in that regime.
    for regime, stats in (rep.get("false_positives_by_regime") or {}).items():
        resolved = int(stats.get("resolved") or 0)
        fp = stats.get("fp_rate")
        if resolved >= _MIN_SAMPLES and fp is not None and fp >= _FP_RATE_HIGH:
            proposals.append(
                {
                    "kind": "guardrail_threshold",
                    "target": "QUALITY_MIN_SIGNAL_SCORE",
                    "direction": "increase",
                    "scope": f"regime={regime}",
                    "evidence": f"fp_rate={fp} over {resolved} resolved decisions",
                    "confidence": "medium",
                }
            )

    # 2) Edge decay by setup -> reduce that setup's ensemble weight.
    for setup, stats in (rep.get("edge_decay_by_setup") or {}).items():
        resolved = int(stats.get("resolved") or 0)
        decay = stats.get("edge_decay")
        if resolved >= _MIN_SAMPLES and decay is not None and decay >= _DECAY_HIGH:
            proposals.append(
                {
                    "kind": "scanner_weight",
                    "target": f"strategy_weight:{setup}",
                    "direction": "decrease",
                    "scope": f"setup={setup}",
                    "evidence": f"edge_decay={decay} over {resolved} resolved decisions",
                    "confidence": "medium",
                }
            )

    # 3) Execution drag by condition -> tighten exec policy in that condition.
    for cond, stats in (rep.get("execution_drag_by_condition") or {}).items():
        samples = int(stats.get("samples") or 0)
        slip = stats.get("avg_slippage_bps")
        if samples >= _MIN_SAMPLES and slip is not None and slip >= 30.0:
            proposals.append(
                {
                    "kind": "exec_policy",
                    "target": "EXEC_POLICY_TIGHT_SPREAD_BPS",
                    "direction": "decrease",
                    "scope": f"volatility={cond}",
                    "evidence": f"avg_slippage_bps={slip} over {samples} fills",
                    "confidence": "low",
                }
            )

    return {
        "proposals": proposals,
        "count": len(proposals),
        "note": "Advisory only — review and apply via config (OFF→SHADOW→LIVE). Nothing auto-applied.",
        "coverage_pct": rep.get("coverage_pct"),
    }
