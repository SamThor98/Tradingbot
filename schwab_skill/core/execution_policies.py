"""Phase 3 smart execution policies (policy-driven, not ad hoc).

Formalizes the order-routing decisions that ``execution.place_order`` makes today
into a single, named, attributable decision:
- **market vs limit selection** (generalizes the existing ``should_prefer_limit``)
- **reprice loop strategy** (aggressive vs patient cadence by spread)
- **auto-throttle on degraded data quality**

Pure decision function (no I/O), so it is unit-testable offline. Carries a
``policy_id`` that lands on ``ExecutionState.intent.policy_id`` for attribution.
Shipped behind ``EXEC_POLICY_MODE`` (default ``shadow`` = compute + record + attach,
never reroute).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

POLICY_ID = "exec_policy_v1"

# Data-quality labels that should throttle risk-increasing orders.
_THROTTLE_QUALITIES = {"degraded", "stale", "conflict"}


def policy_mode(skill_dir: Path | None = None) -> str:
    try:
        from config import get_exec_policy_mode

        return get_exec_policy_mode(skill_dir)
    except Exception:
        return "shadow"


def _tight_spread_bps(skill_dir: Path | None) -> float:
    try:
        from config import get_exec_policy_tight_spread_bps

        return get_exec_policy_tight_spread_bps(skill_dir)
    except Exception:
        return 10.0


def _f(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def decide(
    *,
    side: str = "BUY",
    base_order_type: str = "MARKET",
    spread_bps: float | None = None,
    expected_slippage_bps: float | None = None,
    liquid: bool = False,
    preferred_limit_price: float | None = None,
    data_quality: str | None = None,
    is_risk_increasing: bool = True,
    skill_dir: Path | None = None,
) -> dict[str, Any]:
    """Return a normalized execution-policy decision.

    The decision is *advisory*: it never performs I/O and never places an order.
    ``execution.place_order`` consults it (shadow records it; live may apply it).
    """
    reasons: list[str] = []
    tight = _tight_spread_bps(skill_dir)
    sb = _f(spread_bps)
    plp = _f(preferred_limit_price)

    # 1) market vs limit
    recommended_order_type = base_order_type.upper()
    if base_order_type.upper() == "MARKET" and liquid and plp is not None and plp > 0:
        recommended_order_type = "LIMIT"
        reasons.append("prefer_limit_liquid")

    # 2) reprice strategy (only meaningful for LIMIT)
    if recommended_order_type == "LIMIT":
        reprice_strategy = "aggressive" if (sb is not None and sb <= tight) else "patient"
    else:
        reprice_strategy = "none"

    # 3) auto-throttle on degraded data
    dq = str(data_quality or "").strip().lower()
    throttle = bool(dq in _THROTTLE_QUALITIES and is_risk_increasing)
    if throttle:
        reasons.append(f"throttle_data_quality:{dq}")

    mode = policy_mode(skill_dir)
    # In live, a throttle on a risk-increasing order is a hold recommendation.
    recommend_hold = bool(throttle and mode == "live")

    return {
        "policy_id": POLICY_ID,
        "mode": mode,
        "recommended_order_type": recommended_order_type,
        "recommended_limit_price": plp if recommended_order_type == "LIMIT" else None,
        "reprice_strategy": reprice_strategy,
        "throttle": throttle,
        "recommend_hold": recommend_hold,
        "spread_bps": round(sb, 2) if sb is not None else None,
        "expected_slippage_bps": (
            round(float(expected_slippage_bps), 2) if expected_slippage_bps is not None else None
        ),
        "reasons": reasons,
    }
