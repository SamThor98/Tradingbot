"""Close-of-day research loop.

Score open hypothesis horizons, refresh self-study, then copy hypothesis-card
metrics from artifacts already on disk. Does not change plugin modes, write
``.env``, run backtests, or place orders.
"""

from __future__ import annotations

import importlib.util
import logging
from pathlib import Path
from typing import Any, Callable

LOG = logging.getLogger(__name__)


def _score_ledger(skill_dir: Path) -> int:
    path = Path(__file__).resolve().parent.parent / "scripts" / "score_hypothesis_outcomes.py"
    spec = importlib.util.spec_from_file_location("score_hypothesis_outcomes", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load hypothesis scorer from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    run_score: Callable[..., int] = module.run_score
    return int(run_score(skill_dir, False))


def run_research_close(skill_dir: Path | str) -> dict[str, Any]:
    """Score the ledger first. Self-study still runs when scoring fails."""
    root = Path(skill_dir)
    score_ok = True
    score_error: str | None = None
    try:
        _score_ledger(root)
    except Exception as exc:
        score_ok = False
        score_error = type(exc).__name__
        LOG.exception("Hypothesis scoring failed")

    study: dict[str, Any] = {}
    study_error: str | None = None
    try:
        from self_study import run_self_study

        raw = run_self_study(skill_dir=root)
        if isinstance(raw, dict):
            study = raw
    except Exception as exc:
        study_error = type(exc).__name__
        LOG.exception("Self-study failed")

    calibration = study.get("hypothesis_calibration")
    card_summary: dict[str, Any] = {}
    card_error: str | None = None
    try:
        from core.hypothesis_card import score_all_cards

        scored = score_all_cards(root)
        if isinstance(scored, dict):
            card_summary = scored
    except Exception as exc:
        card_error = type(exc).__name__
        LOG.exception("Hypothesis card scoring failed")
    return {
        "score_ok": score_ok,
        "score_error": score_error,
        "study_error": study_error,
        "round_trips_count": study.get("round_trips_count"),
        "hypothesis_calibration": calibration if isinstance(calibration, dict) else None,
        "hypothesis_cards": card_summary,
        "hypothesis_cards_error": card_error,
    }
