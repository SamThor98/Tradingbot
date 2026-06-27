"""Shared MiroFish entry simulation for backtest and audit dataset builds."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

import pandas as pd

LOG = logging.getLogger(__name__)
SKILL_DIR = Path(__file__).resolve().parent.parent


def run_mirofish_for_entry(
    ticker: str,
    seeded_df: pd.DataFrame,
    skill_dir: Path | str | None = None,
) -> dict[str, Any] | None:
    if os.environ.get("BACKTEST_SKIP_MIROFISH", "").strip().lower() in ("1", "true", "yes"):
        return None
    sd = Path(skill_dir or SKILL_DIR)
    try:
        from engine_analysis import MarketSimulation

        sim = MarketSimulation(ticker, seed_df=seeded_df, skill_dir=sd)
        result = sim.run()
        return {
            "conviction_score": result.get("conviction_score"),
            "summary": result.get("summary"),
            "continuation_probability": result.get("continuation_probability"),
            "bull_trap_probability": result.get("bull_trap_probability"),
        }
    except Exception as e:
        LOG.warning("MiroFish sim failed for %s: %s", ticker, e)
        return None
