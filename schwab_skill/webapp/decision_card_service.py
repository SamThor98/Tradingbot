"""Shared decision-card payload for local and SaaS dashboards.

Routes still resolve the signal (in-memory scan job vs tenant ScanResult)
and build the pre-trade checklist. This module owns the card shape the
frontend renders (`brief`, rank v2, reliability, SEC notes).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _safe_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        n = float(value)
    except (TypeError, ValueError):
        return None
    if n != n:  # NaN
        return None
    return n


def _as_percent_points(value: Any) -> float | None:
    """Convert a fractional return or surprise to percent points.

    Callers pass scanner ratios: earnings surprise is clamped at ±3 (±300%),
    and breakout buffer / distance above the 50-day are also fractions.
    A value above 1 is still a fraction, not a percent already.
    """
    n = _safe_float(value)
    if n is None:
        return None
    return n * 100.0


def _trim_lines(items: list[str], limit: int = 8) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in items:
        txt = str(raw or "").strip()
        if not txt or txt in seen:
            continue
        seen.add(txt)
        out.append(txt)
        if len(out) >= limit:
            break
    return out


def coerce_scan_signal(raw: Any) -> dict[str, Any] | None:
    """Normalize a scan row / ScanResult payload into one signal dict."""
    payload: Any = raw
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except Exception:
            return None
    if not isinstance(payload, dict) or not payload:
        return None
    nested = payload.get("signal")
    if isinstance(nested, dict) and (nested.get("ticker") or nested.get("symbol") or nested.get("price") is not None):
        merged = {**payload, **nested}
        return merged
    return payload


def find_signal_in_scan_collections(symbol: str, *collections: Any) -> dict[str, Any] | None:
    """Return the first matching scan-row dict for ``symbol`` across collections."""
    needle = str(symbol or "").upper().strip()
    if not needle:
        return None
    for coll in collections:
        if not isinstance(coll, list):
            continue
        for row in coll:
            signal = coerce_scan_signal(row)
            if not signal:
                continue
            ticker = str(signal.get("ticker") or signal.get("symbol") or "").upper().strip()
            if ticker == needle:
                return signal
    return None


def _volume_ratio(signal: dict[str, Any]) -> float | None:
    direct = _safe_float(
        signal.get("volume_ratio", signal.get("rel_volume", signal.get("rvol", signal.get("vol_ratio"))))
    )
    if direct is not None:
        return direct
    latest = _safe_float(signal.get("latest_volume", signal.get("volume")))
    avg = _safe_float(signal.get("avg_vol_50", signal.get("avg_volume_50", signal.get("average_volume"))))
    if latest is None or avg is None or avg <= 0:
        return None
    return latest / avg


def _strategy_label(signal: dict[str, Any]) -> str:
    raw = (
        (signal.get("strategy_attribution") or {}).get("top_live")
        if isinstance(signal.get("strategy_attribution"), dict)
        else None
    )
    label = str(raw or "unknown").strip() or "unknown"
    return label.replace("_", " ").replace("-", " ")


def _humanize_code(code: Any) -> str:
    txt = str(code or "").strip()
    if not txt:
        return ""
    return txt.replace("_", " ").replace("-", " ")


def _build_key_risks(
    signal: dict[str, Any], checklist: dict[str, Any], *, sec_risk_tag: str, confidence_bucket: str
) -> list[str]:
    key_risks: list[str] = []
    for r in list(checklist.get("block_reasons_plain") or []):
        txt = str(r).strip()
        if txt:
            key_risks.append(txt)
    filter_status = str(signal.get("_filter_status") or "kept").strip() or "kept"
    if filter_status != "kept":
        key_risks.append(f"Scan disposition: {_humanize_code(filter_status)}.")
    for reason in list(signal.get("_filter_reasons") or []):
        txt = _humanize_code(reason)
        if txt:
            key_risks.append(f"Filter: {txt}.")
    if sec_risk_tag in {"high", "medium"}:
        key_risks.append(f"SEC risk tag: {sec_risk_tag}.")
    if confidence_bucket in {"low", "unknown"}:
        key_risks.append("Advisory confidence is low/unknown.")
    event_risk = signal.get("event_risk") if isinstance(signal.get("event_risk"), dict) else {}
    if event_risk.get("flagged"):
        event_reasons = ", ".join([str(x) for x in list(event_risk.get("reasons") or []) if str(x).strip()])
        key_risks.append(f"Event risk flagged{': ' + event_reasons if event_reasons else ''}.")
    if signal.get("used_fallback_data"):
        fallback = str(signal.get("fallback_reason") or "").strip()
        key_risks.append(f"Using fallback market data{f' ({fallback})' if fallback else ''}.")
    dq = str(signal.get("_data_quality") or signal.get("data_quality") or "").strip().lower()
    if dq in {"degraded", "fail", "failed", "error", "stale"}:
        key_risks.append(f"Data quality is {dq}.")
    if signal.get("recent_8k"):
        key_risks.append("Recent 8-K filing in the lookback window.")
    flags = {str(v) for v in list(signal.get("forensic_flags") or []) if str(v).strip()}
    if "beneish_manipulator" in flags:
        key_risks.append("Forensic: Beneish manipulator flag.")
    if "altman_distress" in flags:
        key_risks.append("Forensic: Altman distress zone.")
    if "sloan_high" in flags:
        key_risks.append("Forensic: high Sloan accrual ratio.")
    if not key_risks:
        key_risks.append("No hard risk blockers on this scan row.")
    return _trim_lines(key_risks)


def _build_catalyst_notes(signal: dict[str, Any]) -> list[str]:
    notes: list[str] = []
    pead_pts = _as_percent_points(signal.get("pead_surprise_pct"))
    pead_beat = signal.get("pead_beat")
    if pead_pts is not None:
        if pead_beat is True:
            notes.append(f"Positive earnings surprise: {round(pead_pts, 1)}%.")
        elif pead_beat is False:
            notes.append(f"Negative earnings surprise: {round(pead_pts, 1)}%.")
        else:
            notes.append(f"Recent earnings surprise: {round(pead_pts, 1)}%.")
    elif pead_beat is True:
        notes.append("Recent earnings beat (surprise size not attached).")
    elif pead_beat is False:
        notes.append("Recent earnings miss (surprise size not attached).")
    guidance = str(signal.get("guidance_signal") or "neutral").strip().lower()
    if guidance and guidance not in {"neutral", "unknown", "none"}:
        notes.append(f"Filing guidance signal: {guidance}.")
    if signal.get("breakout_confirmed") is True:
        notes.append("Breakout confirmed on the latest bar.")
    elif signal.get("breakout_confirmed") is False:
        notes.append("Breakout is not confirmed on the latest bar.")
    vol = _volume_ratio(signal)
    if vol is not None:
        notes.append(f"Relative volume vs 50-day average: {vol:.2f}x.")
    sector = str(signal.get("sector_etf") or "").strip()
    if sector:
        notes.append(f"Sector ETF: {sector}.")
    rs = signal.get("sector_rs_rank", signal.get("sector_relative_strength_rank"))
    if rs is not None:
        notes.append(f"Sector relative-strength rank: {rs}.")
    attr = signal.get("strategy_attribution") if isinstance(signal.get("strategy_attribution"), dict) else {}
    tf = str(attr.get("timeframe") or signal.get("scan_timeframe") or signal.get("timeframe") or "").strip()
    if tf:
        notes.append(f"Scan horizon: {tf}.")
    notes.append(f"Primary strategy signal: {_strategy_label(signal)}.")
    return _trim_lines(notes)


def _build_forensic_notes(signal: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    flags = [str(v).strip() for v in list(signal.get("forensic_flags") or []) if str(v).strip()]
    if flags:
        lines.extend(flags)
    else:
        lines.append("No forensic flags on this scan row.")
    sloan = _safe_float(signal.get("forensic_sloan"))
    beneish = _safe_float(signal.get("forensic_beneish"))
    altman = _safe_float(signal.get("forensic_altman"))
    if sloan is not None:
        lines.append(f"Sloan accrual ratio: {sloan:.3f}.")
    if beneish is not None:
        lines.append(f"Beneish M-score: {beneish:.3f}.")
    if altman is not None:
        lines.append(f"Altman Z-score: {altman:.2f}.")
    if sloan is None and beneish is None and altman is None:
        lines.append("Sloan / Beneish / Altman ratios were not attached to this scan row.")
    return _trim_lines(lines)


def _build_forensic_notes_from_research(snapshot: dict[str, Any], signal: dict[str, Any]) -> list[str]:
    """Research-surface notes preferring ART scores. Does not mutate scan flags."""
    lines: list[str] = []
    flags = [str(v).strip() for v in list(snapshot.get("forensic_flags") or []) if str(v).strip()]
    if not flags:
        flags = [str(v).strip() for v in list(signal.get("forensic_flags") or []) if str(v).strip()]
    if flags:
        lines.extend(flags)
    else:
        lines.append("No forensic flags on this scan row.")
    sloan = snapshot.get("sloan") if isinstance(snapshot.get("sloan"), dict) else {}
    beneish = snapshot.get("beneish") if isinstance(snapshot.get("beneish"), dict) else {}
    altman = snapshot.get("altman") if isinstance(snapshot.get("altman"), dict) else {}
    sloan_n = _safe_float(sloan.get("sloan_ratio"))
    beneish_n = _safe_float(beneish.get("m_score"))
    altman_n = _safe_float(altman.get("z_score"))
    if sloan_n is not None:
        lines.append(f"Sloan accrual ratio: {sloan_n:.3f}.")
    if beneish_n is not None:
        lines.append(f"Beneish M-score: {beneish_n:.3f}.")
    if altman_n is not None:
        lines.append(f"Altman Z-score: {altman_n:.2f}.")
    lineage = str(snapshot.get("data_lineage") or "").strip()
    if lineage:
        lines.append(f"Research forensic lineage: {lineage}.")
    if sloan_n is None and beneish_n is None and altman_n is None:
        lines.append("Sloan / Beneish / Altman ratios were not attached to this scan row.")
    return _trim_lines(lines)


def _build_sec_notes(signal: dict[str, Any], *, sec_risk_tag: str) -> list[str]:
    notes: list[str] = [f"SEC risk tag: {sec_risk_tag}."]
    recency = signal.get("filing_recency_days")
    recency_n = None
    try:
        if recency is not None and recency != "":
            recency_n = int(recency)
    except (TypeError, ValueError):
        recency_n = _safe_float(recency)
        recency_n = int(recency_n) if recency_n is not None else None
    if recency_n is not None:
        notes.append(f"Most recent filing context age: {recency_n} day(s).")
    if signal.get("recent_8k"):
        notes.append("Recent 8-K is present in the lookback window.")
    for reason in list(signal.get("sec_risk_reasons") or []):
        txt = str(reason).strip()
        if txt:
            notes.append(txt if txt.endswith(".") else f"{txt}.")
    guidance = str(signal.get("guidance_signal") or "").strip().lower()
    if guidance and guidance not in {"neutral", "unknown", "", "none"}:
        notes.append(f"Guidance from latest 10-Q compare: {guidance}.")
    mgmt = signal.get("management_integrity") if isinstance(signal.get("management_integrity"), dict) else {}
    bucket = str(mgmt.get("score_bucket") or "").strip().lower()
    if bucket:
        notes.append(f"Management integrity bucket: {bucket}.")
    if (
        sec_risk_tag in {"unknown", ""}
        and recency_n is None
        and not signal.get("recent_8k")
        and not list(signal.get("sec_risk_reasons") or [])
    ):
        notes.append("SEC enrichment was not attached beyond the default unknown tag.")
    return _trim_lines(notes)


def _build_expected_move_window(*, p_up_10d: Any, conviction: Any) -> str:
    bits = ["10 trading days"]
    p_up = _safe_float(p_up_10d)
    if p_up is not None:
        pts = p_up * 100.0 if abs(p_up) <= 1.0 else p_up
        bits.append(f"P(up) {round(pts, 1)}%")
    conv = _safe_float(conviction)
    if conv is not None:
        bits.append(f"conviction {round(conv, 1)}")
    return " · ".join(bits)


def _build_entry_stop_ideas(
    signal: dict[str, Any],
    *,
    price: float,
    stop_level: float | None,
    qty: int,
    size_usd: float,
) -> list[str]:
    ideas: list[str] = []
    sma200 = _safe_float(signal.get("sma_200"))
    sma50 = _safe_float(signal.get("sma_50"))
    sma_stop = round(sma200 * 0.97, 2) if sma200 and sma200 > 0 else None
    shown_stop = sma_stop if sma_stop is not None else stop_level
    if sma_stop is not None:
        ideas.append(f"Stop / invalidation: {shown_stop} — 3% under the 200-day.")
    elif shown_stop is not None:
        ideas.append(f"Stop / invalidation: {shown_stop} — 7% under price, 200-day missing.")
    else:
        ideas.append("Stop / invalidation not computed (no price or SMA-200).")
    if price > 0:
        ideas.append(f"Last scan price: {price}.")
    else:
        ideas.append("Last scan price unavailable — scan row has no price.")
    if sma50 and sma50 > 0:
        ideas.append(f"50-day SMA: {round(sma50, 2)}.")
    if sma200 and sma200 > 0:
        ideas.append(f"200-day SMA: {round(sma200, 2)}.")
    shadow = signal.get("entry_timing_shadow") if isinstance(signal.get("entry_timing_shadow"), dict) else {}
    if not shadow:
        shadow = signal.get("entry_timing_at_stage2") if isinstance(signal.get("entry_timing_at_stage2"), dict) else {}
    buf = _as_percent_points(shadow.get("breakout_buffer_pct"))
    if buf is not None:
        ideas.append(f"Breakout buffer vs prior high: {round(buf, 1)}%.")
    pct_sma50 = _as_percent_points(shadow.get("pct_above_sma50"))
    if pct_sma50 is not None:
        ideas.append(f"Price vs 50-day SMA: {round(pct_sma50, 1)}%.")
    ideas.append(f"Sizing preview: {qty} shares (~${round(float(size_usd), 2)}).")
    return _trim_lines(ideas, limit=8)


def build_decision_card_payload(
    signal: dict[str, Any],
    *,
    ticker: str,
    skill_dir: Path,
    checklist: dict[str, Any],
    size_usd: float | None = None,
) -> dict[str, Any]:
    """Return the dashboard decision-card dict for one scan-row signal."""
    from execution import get_position_size_usd

    symbol = str(ticker or "").upper().strip()
    signal = coerce_scan_signal(signal) or {}
    price = _safe_float(signal.get("price") or signal.get("current_price")) or 0.0
    if size_usd is None:
        size_usd = get_position_size_usd(ticker=symbol, price=price if price > 0 else None, skill_dir=skill_dir)
    qty = max(1, int(size_usd / price)) if price > 0 else 1
    stop_pct = max(0.03, min(0.15, 0.07))
    sma200 = _safe_float(signal.get("sma_200"))
    stop_level = (
        round(sma200 * 0.97, 2)
        if sma200 and sma200 > 0
        else (round(price * (1.0 - stop_pct), 2) if price > 0 else None)
    )
    entry_zone = (
        {"low": round(price * 0.995, 2), "high": round(price * 1.005, 2)} if price > 0 else {"low": None, "high": None}
    )
    advisory = signal.get("advisory") if isinstance(signal.get("advisory"), dict) else {}
    confidence_bucket = str((advisory.get("confidence_bucket") or signal.get("confidence_bucket") or "unknown")).lower()
    score = _safe_float(signal.get("composite_score", signal.get("signal_score", 0))) or 0.0
    reliability = signal.get("reliability_score")
    edge = signal.get("edge_score")
    execution = signal.get("execution_score")
    rank_score = signal.get("rank_score")
    rank_score_v2 = signal.get("rank_score_v2")
    rank_score_v1 = signal.get("rank_score_v1") or rank_score
    rank_basis = signal.get("rank_basis")
    historical_rank = signal.get("historical_rank")
    rank_percentile = signal.get("rank_percentile")
    conviction = signal.get("mirofish_conviction")
    try:
        reliability_text = f"{float(reliability):.1f}" if reliability is not None else "unknown"
    except (TypeError, ValueError):
        reliability_text = "unknown"
    reasons = [
        f"Signal score {score:.1f}",
        f"Reliability {reliability_text}",
        f"Confidence: {confidence_bucket}",
        f"Strategy: {_strategy_label(signal)}",
    ]
    if historical_rank is not None:
        reasons.append(f"Rank score {float(historical_rank):.1f}")
    elif rank_score_v2 is not None:
        reasons.append(f"Rank score {float(rank_score_v2):.1f}")
    elif rank_score is not None:
        reasons.append(f"Rank score {float(rank_score):.1f}")
    if rank_basis:
        reasons.append(f"Rank basis: {str(rank_basis).replace('_', ' ')}")
    if conviction is not None:
        reasons.append(f"Conviction {conviction}")
    event_risk = signal.get("event_risk") if isinstance(signal.get("event_risk"), dict) else {}
    if event_risk.get("flagged"):
        event_bits = ",".join(event_risk.get("reasons") or [])
        reasons.append(f"Event risk flagged{f' ({event_bits})' if event_bits else ''}")

    p_up_10d = advisory.get("p_up_10d") if advisory.get("p_up_10d") is not None else signal.get("p_up_10d")
    sec_risk_tag = str(signal.get("sec_risk_tag") or "unknown").lower() or "unknown"
    vol = _volume_ratio(signal)
    vol_bit = f", relative volume {vol:.2f}x" if vol is not None else ""
    setup_summary = (
        f"{symbol} remains a breakout candidate with score {score:.1f}, "
        f"{confidence_bucket} confidence, and strategy {_strategy_label(signal)}{vol_bit}."
    )
    key_risks = _build_key_risks(
        signal,
        checklist if isinstance(checklist, dict) else {},
        sec_risk_tag=sec_risk_tag,
        confidence_bucket=confidence_bucket,
    )
    catalyst_notes = _build_catalyst_notes(signal)
    forensic_note_lines = _build_forensic_notes(signal)
    sharadar_overlay: dict[str, Any] = {
        "available": False,
        "status": "disabled",
        "chips": [],
        "holders": [],
        "insiders": [],
        "actions": [],
        "forensic": None,
    }
    try:
        from sharadar_research import decision_card_overlay, overlay_research_forensic, snapshot_for_research

        snap = snapshot_for_research(symbol, skill_dir=skill_dir)
        sharadar_overlay = decision_card_overlay(snap)
        art_forensic = overlay_research_forensic(
            {
                "ok": False,
                "sloan": {"sloan_ratio": _safe_float(signal.get("forensic_sloan"))}
                if _safe_float(signal.get("forensic_sloan")) is not None
                else None,
                "beneish": {"m_score": _safe_float(signal.get("forensic_beneish"))}
                if _safe_float(signal.get("forensic_beneish")) is not None
                else None,
                "altman": {"z_score": _safe_float(signal.get("forensic_altman"))}
                if _safe_float(signal.get("forensic_altman")) is not None
                else None,
                "forensic_flags": list(signal.get("forensic_flags") or []),
            },
            snap,
            skill_dir=skill_dir,
        )
        if "sharadar_art" in str(art_forensic.get("data_lineage") or ""):
            forensic_note_lines = _build_forensic_notes_from_research(art_forensic, signal)
            sharadar_overlay["forensic"] = {
                "ok": bool(art_forensic.get("ok")),
                "sloan": art_forensic.get("sloan"),
                "beneish": art_forensic.get("beneish"),
                "altman": art_forensic.get("altman"),
                "forensic_flags": list(art_forensic.get("forensic_flags") or []),
                "data_lineage": art_forensic.get("data_lineage"),
            }
    except Exception:
        sharadar_overlay = {**sharadar_overlay, "available": False}
    sec_notes = _build_sec_notes(signal, sec_risk_tag=sec_risk_tag)
    expected_move_window = _build_expected_move_window(
        p_up_10d=p_up_10d,
        conviction=conviction,
    )
    entry_stop_ideas = _build_entry_stop_ideas(
        signal,
        price=price,
        stop_level=stop_level,
        qty=qty,
        size_usd=float(size_usd or 0),
    )

    dq_raw = signal.get("_data_quality")
    if dq_raw is None or not str(dq_raw).strip():
        dq_raw = signal.get("data_quality")
    data_quality = str(dq_raw).strip() if dq_raw is not None and str(dq_raw).strip() else None

    return {
        "ticker": symbol,
        "side": "BUY",
        "entry_price": price if price > 0 else None,
        "data_quality": data_quality,
        "entry_zone": entry_zone,
        "stop_invalidation": stop_level,
        "size": {"qty": qty, "usd": size_usd},
        "confidence": {
            "bucket": confidence_bucket,
            # ``signal_score`` has carried the composite value since v1 of this
            # payload; ``composite_score`` names it honestly for the UI.
            "signal_score": score,
            "composite_score": score,
            "mirofish_conviction": conviction,
            "edge_score": edge,
            "reliability_score": reliability,
            "execution_score": execution,
            "rank_score": rank_score_v2 if rank_score_v2 is not None else rank_score,
            "rank_score_v2": rank_score_v2,
            "rank_score_v1": rank_score_v1,
            "rank_basis": rank_basis,
            "historical_rank": historical_rank,
            "rank_percentile": rank_percentile,
        },
        "key_reasons": reasons[:6],
        "block_reason": (checklist.get("block_reasons") or [None])[0] if isinstance(checklist, dict) else None,
        "checklist": checklist,
        "brief": {
            "setup_summary": setup_summary,
            "key_risks": key_risks,
            "catalyst_notes": catalyst_notes,
            "forensic_flags": forensic_note_lines,
            "sec_notes": sec_notes,
            "expected_move_window": expected_move_window,
            "entry_stop_ideas": entry_stop_ideas,
        },
        "sharadar": sharadar_overlay,
    }
