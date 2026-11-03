"""Week-block profit-factor intervals stay wider than an i.i.d. trade resample."""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

SKILL_DIR = Path(__file__).resolve().parent.parent
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

from research.report import bootstrap_pf_ci, bootstrap_pf_ci_blocked  # noqa: E402


def test_week_block_interval_can_reach_a_pure_winner_week() -> None:
    returns = pd.Series([0.10] * 30 + [-0.05] * 30)
    blocks = pd.Series(["2024-W01"] * 30 + ["2024-W02"] * 30)
    blocked = bootstrap_pf_ci_blocked(returns, blocks, n_boot=200, seed=42)
    iid = bootstrap_pf_ci(returns, n_boot=200, seed=42)
    assert blocked["pf_lo"] is not None and blocked["pf_hi"] is not None
    assert iid["pf_hi"] is not None
    assert blocked["pf_hi"] > iid["pf_hi"]
