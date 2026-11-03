"""Quick-check verdicts as staged decision packets, plus versioned prompt text.

The displayed verdict stays the built-in rule unless ``TAB_AGENT_RESEARCH_ENABLED``
is on. Every recorded packet can then be labeled by the 20-day scale-out
backfill. This module does not place orders.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

LOG = logging.getLogger(__name__)

PROMPT_RULES = "verdict_rules"
PROMPT_RATIONALE = "rationale"
_SCORE_RE = re.compile(r"STRONG_SETUP_MIN_SCORE\s*=\s*(\d+(?:\.\d+)?)")

BUILTIN_RULES = (
    "Long-only equities. Never suggest a short, an inverse ETF, or an option. "
    "HOLD OFF means stay in cash. Do not penalize market cap. "
    "Weight a clean chart, fundamentals, sector strength, and a tight stop. "
    "A catalyst is only a tiebreaker. "
    "STRONG SETUP requires stage 2, a VCP, and signal score at or above STRONG_SETUP_MIN_SCORE=60. "
    "WATCH is stage 2 without that bar. Otherwise HOLD OFF. NO DATA when price history is missing."
)
BUILTIN_RATIONALE = (
    "{ticker} is {verdict}. Chart stage2={stage_2} vcp={vcp} score={score}. "
    "Stop {stop_pct}. Use only these figures. Cash is valid. No short."
)

_CONFIDENCE = {
    "STRONG SETUP": 0.72,
    "WATCH": 0.55,
    "HOLD OFF": 0.35,
}


def prompt_catalog() -> dict[str, str]:
    return {PROMPT_RULES: BUILTIN_RULES, PROMPT_RATIONALE: BUILTIN_RATIONALE}


def _clamp_score(value: float, skill_dir: Path | None) -> float:
    from core.tab_agent_objective import load_objective

    config = load_objective(skill_dir)
    return max(config.quality_min_bound, min(config.quality_max_bound, float(value)))


def min_score_from_text(text: str, skill_dir: Path | None, *, default: float = 60.0) -> float:
    match = _SCORE_RE.search(text or "")
    if not match:
        return _clamp_score(default, skill_dir)
    try:
        return _clamp_score(float(match.group(1)), skill_dir)
    except ValueError:
        return _clamp_score(default, skill_dir)


def _live_text(skill_dir: Path | None, key: str, fallback: str) -> tuple[str, str]:
    """Return prompt text and version id. Built-in unless the research agent is on."""
    if skill_dir is None:
        return fallback, "builtin"
    try:
        from config import get_tab_agent_research_enabled

        if not get_tab_agent_research_enabled(Path(skill_dir)):
            return fallback, "builtin"
        from core.research_tab_agent import ResearchTabAgent

        row = ResearchTabAgent().registry(Path(skill_dir)).live_version("research", key)
    except Exception as exc:
        LOG.debug("Research prompt lookup skipped: %s", exc)
        return fallback, "builtin"
    if not row or not str(row.get("text") or "").strip():
        return fallback, "builtin"
    return str(row["text"]), str(row.get("id") or "builtin")


def _shadow_text(skill_dir: Path | None, key: str) -> tuple[str, str] | None:
    if skill_dir is None:
        return None
    try:
        from config import get_tab_agent_research_shadow_enabled

        if not get_tab_agent_research_shadow_enabled(Path(skill_dir)):
            return None
        from core.research_tab_agent import ResearchTabAgent

        rows = ResearchTabAgent().registry(Path(skill_dir)).versions("research", key)
    except Exception as exc:
        LOG.debug("Research shadow prompt skipped: %s", exc)
        return None
    shadow = next((row for row in rows if row.get("status") == "shadow" and str(row.get("text") or "").strip()), None)
    if shadow is None:
        return None
    return str(shadow["text"]), str(shadow.get("id") or "")


def _verdict(*, has_price: bool, stage_2: bool, vcp: bool, signal_score: float, min_score: float) -> str:
    if not has_price:
        return "NO DATA"
    if stage_2 and vcp and signal_score >= min_score:
        return "STRONG SETUP"
    if stage_2:
        return "WATCH"
    return "HOLD OFF"


def render_rationale(template: str, fields: dict[str, Any]) -> str:
    text = template or BUILTIN_RATIONALE
    for key, value in fields.items():
        text = text.replace("{" + key + "}", str(value))
    return text.strip()[:500]


def resolve_quick_check_decision(
    *,
    ticker: str,
    has_price_data: bool,
    stage_2: bool,
    vcp: bool,
    signal_score: float,
    stop_pct: float | None = None,
    skill_dir: Path | None = None,
) -> dict[str, Any]:
    """Verdict, rationale, and the prompt version that produced them."""
    rules, version_id = _live_text(skill_dir, PROMPT_RULES, BUILTIN_RULES)
    rationale_text, rationale_version = _live_text(skill_dir, PROMPT_RATIONALE, BUILTIN_RATIONALE)
    floor = min_score_from_text(rules, skill_dir)
    verdict = _verdict(
        has_price=has_price_data,
        stage_2=stage_2,
        vcp=vcp,
        signal_score=float(signal_score or 0.0),
        min_score=floor,
    )
    fields = {
        "ticker": ticker,
        "verdict": verdict,
        "stage_2": "yes" if stage_2 else "no",
        "vcp": "yes" if vcp else "no",
        "score": f"{float(signal_score or 0.0):.0f}",
        "stop_pct": "n/a" if stop_pct is None else f"{float(stop_pct):.4f}",
    }
    shadow = _shadow_text(skill_dir, PROMPT_RULES)
    shadow_payload = None
    if shadow is not None:
        shadow_floor = min_score_from_text(shadow[0], skill_dir)
        shadow_payload = {
            "verdict": _verdict(
                has_price=has_price_data,
                stage_2=stage_2,
                vcp=vcp,
                signal_score=float(signal_score or 0.0),
                min_score=shadow_floor,
            ),
            "prompt_version": shadow[1],
            "min_score": shadow_floor,
        }
    return {
        "verdict": verdict,
        "rationale": render_rationale(rationale_text, fields),
        "prompt_version": version_id,
        "rationale_version": rationale_version,
        "prompt_key": PROMPT_RULES,
        "min_score": floor,
        "stated_confidence": _CONFIDENCE.get(verdict),
        "shadow": shadow_payload,
    }


def record_quick_check_packet(
    skill_dir: Path | None,
    *,
    ticker: str,
    price: float | None,
    stop_pct: float | None,
    decision: dict[str, Any],
    created_at: datetime | None = None,
) -> dict[str, Any]:
    """Append one staged packet per ticker per UTC day. Never raises."""
    empty = {"recorded": 0, "skipped_duplicate": 0}
    if skill_dir is None or not str(ticker or "").strip():
        return empty
    try:
        from core.decision_packet import _utc_day, build_packet, load_packets, record_packet

        when = created_at or datetime.now(timezone.utc)
        day = _utc_day(when)
        symbol = str(ticker).upper()
        for packet in load_packets(skill_dir):
            refs = packet.get("refs") if isinstance(packet.get("refs"), dict) else {}
            if (
                str(packet.get("ticker") or "").upper() == symbol
                and _utc_day(packet.get("created_at")) == day
                and refs.get("source") == "quick_check"
            ):
                return {"recorded": 0, "skipped_duplicate": 1}
        signal = {
            "price": price,
            "stop_pct": stop_pct,
            "setup_type": "quick_check",
            "verdict": decision.get("verdict"),
            "rationale": decision.get("rationale"),
            "prompt_version": decision.get("prompt_version"),
            "prompt_key": decision.get("prompt_key"),
            "stated_confidence": decision.get("stated_confidence"),
            "p_up_calibrated": decision.get("stated_confidence"),
            "rank_score": decision.get("min_score"),
        }
        packet = build_packet(ticker=symbol, kind="staged", signal=signal, created_at=when)
        dumped = packet.model_dump(mode="json")
        refs = dumped.setdefault("refs", {})
        refs["source"] = "quick_check"
        refs["verdict"] = decision.get("verdict")
        refs["rationale"] = decision.get("rationale")
        refs["prompt_version"] = decision.get("prompt_version")
        refs["rationale_version"] = decision.get("rationale_version")
        refs["prompt_key"] = decision.get("prompt_key")
        refs["stated_confidence"] = decision.get("stated_confidence")
        shadow = decision.get("shadow") if isinstance(decision.get("shadow"), dict) else None
        if shadow:
            refs["shadow_verdict"] = shadow.get("verdict")
            refs["shadow_prompt_version"] = shadow.get("prompt_version")
        from core.contracts.decision_packet import DecisionPacket

        record_packet(skill_dir, DecisionPacket.model_validate(dumped))
        return {"recorded": 1, "skipped_duplicate": 0, "packet_id": dumped.get("packet_id")}
    except Exception as exc:
        LOG.warning("Quick-check packet skipped for %s: %s", ticker, exc)
        return empty
