"""Management integrity shadow enrichment for Stage B.

Builds a compact integrity scorecard from SEC filing compare output. SHADOW
mode attaches evidence only; LIVE score nudges are intentionally deferred until
packet cohort analysis shows lift.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

LOG = logging.getLogger(__name__)

SKILL_DIR = Path(__file__).resolve().parent


def _score_bucket(score: int | float | None) -> str:
    try:
        s = float(score)
    except (TypeError, ValueError):
        return "unknown"
    if s >= 70:
        return "high"
    if s >= 50:
        return "medium"
    return "low"


def fetch_management_integrity_snapshot(
    ticker: str,
    *,
    skill_dir: Path | None = None,
    user_agent: str | None = None,
    cache_hours: float = 24.0,
    max_chars: int = 120_000,
    form_type: str = "10-Q",
) -> dict[str, Any] | None:
    """Return a compact management-integrity snapshot for a ticker, or None."""
    sym = str(ticker or "").upper().strip()
    if not sym:
        return None
    try:
        from config import get_sec_filing_compare_enabled

        if not get_sec_filing_compare_enabled(skill_dir):
            return None

        from sec_filing_compare import compare_ticker_over_time
        from webapp.management_dashboard import build_management_dashboard

        compare_out = compare_ticker_over_time(
            sym,
            form_type=form_type,
            user_agent=user_agent,
            skill_dir=skill_dir or SKILL_DIR,
            cache_hours=cache_hours,
            max_chars=max_chars,
            enable_llm=False,
            highlight_changes_only=True,
        )
        if not compare_out.get("ok"):
            return None

        compare_payload = {"compare": compare_out.get("compare") or compare_out}
        dashboard = build_management_dashboard(
            compare_payload=compare_payload,
            mode="ticker_over_time",
            ticker=sym,
            form_type=form_type,
        )
        scorecard = dashboard.get("integrity_scorecard") or {}
        score = scorecard.get("score")
        red_flags = dashboard.get("red_flags") or []
        profile = dashboard.get("profile") or {}
        return {
            "score": int(score) if score is not None else None,
            "score_bucket": _score_bucket(score),
            "profile": profile.get("selected"),
            "red_flag_count": len(red_flags) if isinstance(red_flags, list) else 0,
            "source": "management_dashboard_v1",
        }
    except Exception as exc:
        LOG.debug("Management integrity snapshot skipped for %s: %s", ticker, exc)
        return None
