"""Shadow research-wave fields and the deterministic skeptic vote.

Annotations ride on the signal. ``signal_score`` changes only when
``MIROFISH_ABSTAIN_MODE=live`` and disagreement clears the threshold.
"""

from __future__ import annotations

from typing import Any

from agent_intelligence import compute_vote_disagreement

_SKEPTIC_NAME = "skeptic"
_SKEPTIC_PENALTY = {
    "extended_gt_12pct_sma50": -40,
    "earnings_within_5d": -30,
    "thin_adv": -25,
    "data_quality_stale": -80,
    "data_quality_conflict": -80,
    "data_quality_degraded": -40,
    "beneish_manipulator": -70,
    "altman_distress": -70,
    "sec_risk_high": -50,
}


def _safe_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number:
        return None
    return number


def _adv_proxy(signal: dict[str, Any]) -> float | None:
    existing = _safe_float(signal.get("adv_usd"))
    if existing is not None and existing > 0:
        return existing
    price = _safe_float(signal.get("price"))
    avg_vol = _safe_float(signal.get("avg_vol_50"))
    last_vol = _safe_float(signal.get("latest_volume"))
    volume = avg_vol if avg_vol is not None and avg_vol > 0 else last_vol
    if price is None or volume is None or price <= 0 or volume <= 0:
        return None
    return price * volume


def _extension_pct(signal: dict[str, Any]) -> float | None:
    existing = _safe_float(signal.get("extension_pct_sma50"))
    if existing is not None:
        return existing
    price = _safe_float(signal.get("price"))
    sma50 = _safe_float(signal.get("sma_50"))
    if price is None or sma50 is None or sma50 <= 0:
        return None
    return (price - sma50) / sma50


def _held_above_prior_high(df: Any) -> bool:
    try:
        if df is None or len(df) < 3:
            return False
        if "close" not in df.columns or "high" not in df.columns:
            return False
        return float(df["close"].iloc[-2]) >= float(df["high"].iloc[-3])
    except (TypeError, ValueError, KeyError, IndexError):
        return False


def _setup_ok(signal: dict[str, Any]) -> bool:
    if signal.get("trend_stack_ok") is True:
        return True
    price = _safe_float(signal.get("price"))
    sma50 = _safe_float(signal.get("sma_50"))
    sma200 = _safe_float(signal.get("sma_200"))
    if price is None or sma50 is None or sma50 <= 0 or price <= sma50:
        return False
    if sma200 is not None and sma200 > 0 and price <= sma200:
        return False
    return True


def _data_quality(signal: dict[str, Any]) -> str:
    raw = signal.get("_data_quality")
    if raw is None:
        raw = signal.get("data_quality")
    return str(raw or "").strip().lower()


def _skeptic_reasons(signal: dict[str, Any], r2_flags: list[str]) -> list[str]:
    reasons = list(r2_flags)
    quality = _data_quality(signal)
    if quality in {"stale", "conflict", "degraded"}:
        reasons.append(f"data_quality_{quality}")
    flags = signal.get("forensic_flags")
    if isinstance(flags, list):
        for flag in ("beneish_manipulator", "altman_distress"):
            if flag in flags:
                reasons.append(flag)
    if str(signal.get("sec_risk_tag") or "").strip().lower() == "high":
        reasons.append("sec_risk_high")
    # Preserve order, drop duplicates.
    seen: set[str] = set()
    ordered: list[str] = []
    for reason in reasons:
        if reason in seen:
            continue
        seen.add(reason)
        ordered.append(reason)
    return ordered


def _skeptic_vote(reasons: list[str]) -> dict[str, Any] | None:
    if not reasons:
        return None
    score = 0
    for reason in reasons:
        score += int(_SKEPTIC_PENALTY.get(reason, -20))
    score = max(-100, min(100, score))
    continuation = max(0.0, min(1.0, 0.5 + (score / 200.0)))
    return {
        "name": _SKEPTIC_NAME,
        "evidence": "rules",
        "score": score,
        "reason": " | ".join(reasons)[:200],
        "conviction_score": score,
        "bull_trap_probability": round(1.0 - continuation, 6),
        "continuation_probability": round(continuation, 6),
        "key_drivers": reasons[:5],
        "vcp_alignment": 0.0,
        "sma_alignment": 0.0,
        "horizon": "rules",
    }


def _strip_skeptic(votes: list[Any]) -> list[dict[str, Any]]:
    kept: list[dict[str, Any]] = []
    for vote in votes:
        if isinstance(vote, dict) and str(vote.get("name") or "") != _SKEPTIC_NAME:
            kept.append(vote)
    return kept


def _apply_live_abstain(signal: dict[str, Any]) -> None:
    components = signal.get("score_components")
    points = 0.0
    if isinstance(components, dict):
        parsed = _safe_float(components.get("pts_mirofish"))
        points = parsed if parsed is not None else 0.0
        components["pts_mirofish"] = 0.0
        component_score = _safe_float(components.get("score"))
        if component_score is not None:
            components["score"] = round(max(0.0, component_score - points), 2)
    current = _safe_float(signal.get("signal_score"))
    if current is not None:
        signal["signal_score"] = round(max(0.0, current - points), 2)
    signal["mirofish_conviction"] = None
    result = signal.get("mirofish_result")
    if isinstance(result, dict):
        result["conviction_score"] = None
        result["summary"] = "Abstain (disagreement)"


def annotate_research_wave(
    signal: dict[str, Any],
    *,
    skill_dir: Any = None,
    df: Any = None,
) -> dict[str, int]:
    """Attach R1–R5 shadow fields and the skeptic. Idempotent on the skeptic vote."""
    from config import (
        get_mirofish_abstain_disagreement,
        get_mirofish_abstain_mode,
        get_r3_capacity_shadow,
    )
    from core.sleeve_research_chips import (
        r1_entry_state,
        r2_anti_feature_flags,
        r3_capacity_penalty,
        r4_pead_sleeve_score,
        r5_sector_rs_eligible,
    )

    counters = {
        "skeptic_votes": 0,
        "abstain_would": 0,
        "r2_flagged": 0,
        "r3_annotated": 0,
        "r4_scored": 0,
        "r5_eligible": 0,
    }
    extension = _extension_pct(signal)
    if extension is not None:
        signal["extension_pct_sma50"] = round(extension, 6)
    proxy = _adv_proxy(signal)
    if proxy is not None and _safe_float(signal.get("adv_usd")) is None:
        signal["adv_usd_proxy"] = round(proxy, 2)

    probe = dict(signal)
    if probe.get("adv_usd") is None and proxy is not None:
        probe["adv_usd"] = proxy
    if extension is not None:
        probe["extension_pct_sma50"] = extension

    flags = r2_anti_feature_flags(probe)
    signal["r2_flags"] = flags
    if flags:
        counters["r2_flagged"] = 1

    if get_r3_capacity_shadow(skill_dir):
        multiplier = r3_capacity_penalty(probe)
        base = _safe_float(signal.get("signal_score"))
        signal["r3_capacity_mult"] = round(float(multiplier), 6)
        if base is not None:
            signal["signal_score_r3"] = round(base * float(multiplier), 2)
        counters["r3_annotated"] = 1

    family = str(signal.get("entry_family") or "")
    if family == "pead_primary" or signal.get("pead_surprise_pct") is not None:
        signal["r4_score"] = round(float(r4_pead_sleeve_score(probe)), 4)
        counters["r4_scored"] = 1

    signal["r1_entry_state"] = r1_entry_state(
        pierced=bool(signal.get("breakout_confirmed")),
        held_above_n=_held_above_prior_high(df),
        setup_ok=_setup_ok(signal),
    )

    sector = str(signal.get("sector_etf") or "").upper()
    winning = {sector} if signal.get("sector_winning") and sector else set()
    eligible = r5_sector_rs_eligible(signal, top_quintile_sectors=winning)
    signal["r5_sector_rs_eligible"] = bool(eligible)
    signal["r5_universe"] = "winning_vs_spy"
    if eligible:
        counters["r5_eligible"] = 1

    reasons = _skeptic_reasons(signal, flags)
    vote = _skeptic_vote(reasons)
    signal["skeptic"] = {
        "voted": vote is not None,
        "score": None if vote is None else vote["score"],
        "reasons": reasons,
    }
    result = signal.get("mirofish_result")
    votes: list[dict[str, Any]] = []
    if isinstance(result, dict) and isinstance(result.get("agent_votes"), list):
        votes = _strip_skeptic(result.get("agent_votes") or [])
    if vote is not None:
        votes.append(vote)
        counters["skeptic_votes"] = 1
    disagreement = _safe_float(signal.get("mirofish_disagreement"))
    if isinstance(result, dict):
        result["agent_votes"] = votes
    if votes:
        disagreement = compute_vote_disagreement(votes)
        if vote is not None:
            signal["mirofish_disagreement_with_skeptic"] = disagreement
            if isinstance(result, dict):
                result["mirofish_disagreement_with_skeptic"] = disagreement
    mode = get_mirofish_abstain_mode(skill_dir)
    threshold = get_mirofish_abstain_disagreement(skill_dir)
    would = mode != "off" and disagreement is not None and disagreement >= threshold
    signal["mirofish_abstain"] = {
        "mode": mode,
        "would": would,
        "disagreement": disagreement,
        "threshold": threshold,
    }
    signal["mirofish_abstain_would"] = would
    if would:
        counters["abstain_would"] = 1
    if mode == "live" and would:
        _apply_live_abstain(signal)
    return counters
