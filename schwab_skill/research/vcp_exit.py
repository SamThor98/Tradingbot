"""Single-policy counterfactual: skip VCP invalidation under exit grace.

The live exit path is unchanged. This module only decides whether a replay
that already ran both arms is allowed to be discussed as a shadow candidate.
Promotion still requires a separate operator step; nothing here writes env.
"""

from __future__ import annotations

from typing import Any

PF_MEAN_FLOOR = 1.20
WORST_FLOOR = 1.00
MIN_LIFT = 0.03
MIN_INTERPRETABLE = 500
MIN_SHARE = 0.50


def vcp_exit_verdict(
    grace: dict[str, Any],
    skip: dict[str, Any],
    *,
    interpretable: bool,
) -> str:
    """Require floors and +0.03 PF mean versus the 15/40 grace replay.

    ``shadow_ready_exit_not_promoted`` is not a live exit change.
    """
    if not interpretable:
        return "not_interpretable"
    if not skip.get("clears_floors"):
        return "do_not_change_vcp_exit"
    grace_pf = grace.get("pf_mean")
    skip_pf = skip.get("pf_mean")
    if grace_pf is None or skip_pf is None:
        return "do_not_change_vcp_exit"
    if float(skip_pf) - float(grace_pf) < MIN_LIFT:
        return "do_not_change_vcp_exit"
    return "shadow_ready_exit_not_promoted"


def interpretable_sample(*, n_trades: int, n_with_history: int) -> bool:
    """A VCP read needs pre-entry bars. A thin slice is not an exit change."""
    if n_trades <= 0 or n_with_history < MIN_INTERPRETABLE:
        return False
    return (n_with_history / n_trades) >= MIN_SHARE
