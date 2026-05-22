"""Research routes: SEC analysis, full reports, chart data, decision card."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape as xml_escape
from zipfile import ZIP_DEFLATED, ZipFile

from fastapi import APIRouter, Query
from fastapi.responses import Response

from execution import get_account_status
from finnhub_data import get_finnhub_research_snapshot
from full_report import REPORT_SECTION_MAP, generate_full_report, quick_check, report_to_json
from schwab_auth import DualSchwabAuth
from sec_filing_compare import (
    analyze_latest_filing_for_ticker,
    compare_ticker_over_time,
    compare_ticker_vs_ticker,
)
from sector_strength import get_sector_heatmap

from .._shared import build_portfolio_risk_analytics, build_portfolio_summary
from ..dossier_style import polish_dossier_markdown
from ..management_dashboard import PROFILE_WEIGHTS, build_management_dashboard
from ..pdf_export import dossier_to_pdf
from ..report_trust import build_report_trust_payload
from ..report_v2 import build_report_v2
from ..schemas import ApiResponse

router = APIRouter(tags=["research"])

SKILL_DIR = Path(__file__).resolve().parent.parent.parent
_LOCAL_SEC_MGMT_PROFILE_OVERRIDE: str | None = None
_LOCAL_SEC_MGMT_OVERRIDE_HISTORY: list[dict[str, Any]] = []


def _ok(data: Any = None) -> ApiResponse:
    return ApiResponse(ok=True, data=data)


def _err_response(endpoint: str, exc: Exception) -> ApiResponse:
    from ..recovery_map import map_failure

    mapped = map_failure(str(exc), source=endpoint)
    headline = f"{mapped.get('title', 'Error')}: {mapped.get('summary', 'Something went wrong.')}"
    raw = str(mapped.get("raw_error") or "").strip()
    summary = str(mapped.get("summary") or "")
    err_out = headline
    if raw and raw.lower() not in summary.lower():
        err_out = f"{headline} — {raw[:220]}"
    return ApiResponse(ok=False, error=err_out, data={"recovery": mapped})


def _sec_analysis_settings() -> dict[str, Any]:
    from config import (
        get_edgar_user_agent,
        get_sec_filing_analysis_enabled,
        get_sec_filing_cache_hours,
        get_sec_filing_compare_enabled,
        get_sec_filing_llm_summary_enabled,
        get_sec_filing_max_chars,
        get_sec_filing_max_compare_items,
    )

    return {
        "analysis_enabled": bool(get_sec_filing_analysis_enabled(SKILL_DIR)),
        "compare_enabled": bool(get_sec_filing_compare_enabled(SKILL_DIR)),
        "user_agent": get_edgar_user_agent(SKILL_DIR),
        "cache_hours": float(get_sec_filing_cache_hours(SKILL_DIR)),
        "max_chars": int(get_sec_filing_max_chars(SKILL_DIR)),
        "max_compare_items": int(get_sec_filing_max_compare_items(SKILL_DIR)),
        "llm_enabled": bool(get_sec_filing_llm_summary_enabled(SKILL_DIR)),
    }


def _normalize_sec_analysis_payload(payload: dict[str, Any], *, analysis_mode: str = "full_text") -> dict[str, Any]:
    data = dict(payload or {})
    confidence = int(data.get("confidence", 0) or 0)
    why = list(data.get("why") or [])
    limits = list(data.get("limits") or [])
    evidence = list(data.get("evidence") or [])
    summary_headline = str(data.get("summary_headline") or "").strip()
    if not summary_headline:
        verdict = str(data.get("verdict") or "neutral")
        summary_headline = (
            f"{data.get('ticker', '')} {data.get('form', '')} filing reads {verdict} "
            f"with confidence {confidence}/100."
        ).strip()
    narrative_summary = str(data.get("narrative_summary") or "").strip()
    if not narrative_summary:
        narrative_summary = " ".join(why[:2]).strip() or str(data.get("high_level_takeaway") or "").strip()
    data["summary_headline"] = summary_headline
    data["narrative_summary"] = narrative_summary
    data["confidence"] = confidence
    data["limits"] = limits
    data["evidence"] = evidence
    data["analysis_mode"] = analysis_mode
    data["data_freshness"] = {
        "from_cache": bool(data.get("from_cache", False)),
        "source": str(data.get("source") or ""),
    }
    return data


def _normalize_sec_compare_payload(payload: dict[str, Any], *, analysis_mode: str = "full_text") -> dict[str, Any]:
    data = dict(payload or {})
    compare_data = dict(data.get("compare") or {})
    similarities = compare_data.get("similarities") or []
    differences = compare_data.get("differences") or []
    investor_takeaway = str(compare_data.get("investor_takeaway") or "").strip()
    compare_data.setdefault(
        "summary_headline",
        "SEC compare completed with meaningful differences." if differences else "SEC compare completed with broad alignment.",
    )
    compare_data.setdefault(
        "narrative_summary",
        (
            f"{investor_takeaway} "
            f"Shared signal: {(similarities[0] if similarities else 'limited overlap noted.')} "
            f"Key difference: {(differences[0] if differences else 'no major contrast highlighted.')}."
        ).strip(),
    )
    compare_data.setdefault("top_differences", differences[:3])
    compare_data.setdefault("top_commonalities", similarities[:3])
    if "change_summary" not in compare_data:
        compare_data["change_summary"] = {
            "new_risks": [],
            "resolved_risks": [],
            "guidance_shift": "unchanged",
            "evidence_ranked": [],
            "plain_english_rationale": [],
        }
    compare_data["analysis_mode"] = analysis_mode
    compare_data.setdefault("compare_confidence", 0)
    compare_data.setdefault("limits", [])
    compare_data.setdefault("evidence", compare_data.get("change_summary", {}).get("evidence_ranked", []))
    left = data.get("left") or data.get("latest") or {}
    right = data.get("right") or data.get("prior") or {}
    compare_data["data_freshness"] = {
        "left_from_cache": bool((left or {}).get("from_cache", False)),
        "right_from_cache": bool((right or {}).get("from_cache", False)),
        "left_source": str((left or {}).get("source") or ""),
        "right_source": str((right or {}).get("source") or ""),
    }
    data["compare"] = compare_data
    return data


def _build_report_verdicts(report: dict[str, Any]) -> dict[str, Any]:
    technical = report.get("technical") or {}
    dcf = report.get("dcf") or {}
    health = report.get("health") or {}
    miro = report.get("mirofish") or {}
    signal_score = float(technical.get("signal_score", 0) or 0)
    mos = float(dcf.get("margin_of_safety", 0) or 0)
    health_flags = health.get("flags") or []
    conviction = float(miro.get("conviction_score", 0) or 0)

    def bucket(score: float, high: float, low: float) -> str:
        if score >= high:
            return "bullish"
        if score <= low:
            return "bearish"
        return "neutral"

    return {
        "technical": {
            "verdict": bucket(signal_score, 65.0, 45.0),
            "takeaway": "Trend setup aligned." if technical.get("stage_2") and technical.get("vcp") else "Setup quality is mixed.",
        },
        "dcf": {
            "verdict": bucket(mos, 10.0, -10.0),
            "takeaway": "Valuation supports upside." if mos >= 0 else "Valuation indicates premium pricing.",
        },
        "health": {
            "verdict": "bullish" if len(health_flags) == 0 else ("bearish" if len(health_flags) >= 3 else "neutral"),
            "takeaway": "Balance sheet and margins are stable." if len(health_flags) == 0 else "Review flagged financial risks.",
        },
        "mirofish": {
            "verdict": bucket(conviction, 30.0, -30.0),
            "takeaway": (miro.get("summary") or "No sentiment synthesis available.")[:220],
        },
    }


def _source_entry(
    name: str,
    *,
    status: str,
    detail: str = "",
    as_of: str | None = None,
    fallback_used: bool = False,
) -> dict[str, Any]:
    return {
        "name": name,
        "status": status,
        "detail": detail,
        "as_of": as_of,
        "fallback_used": fallback_used,
    }


def _safe_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Wording helpers — keep institutional language consistent across surfaces.
# ---------------------------------------------------------------------------

# `report_v2.ic_snapshot.recommendation` returns the raw model verdict
# ("long", "short", "pass"). Investment committees expect BUY/SELL/HOLD style
# language, so we map it once at the dossier boundary.
_RECOMMENDATION_MAP: dict[str, str] = {
    "long": "BUY",
    "buy": "BUY",
    "strong_buy": "STRONG BUY",
    "strongbuy": "STRONG BUY",
    "short": "SELL",
    "sell": "SELL",
    "strong_sell": "STRONG SELL",
    "strongsell": "STRONG SELL",
    "pass": "HOLD",
    "hold": "HOLD",
    "watch": "WATCH",
    "neutral": "HOLD",
}


def _format_recommendation(value: Any, *, default: str = "WATCH") -> str:
    text = str(value or "").strip()
    if not text:
        return default
    key = text.lower().replace(" ", "_")
    if key in _RECOMMENDATION_MAP:
        return _RECOMMENDATION_MAP[key]
    return text.upper()


def _confidence_label_from_score(score: float) -> str:
    if score >= 75:
        return "High"
    if score >= 55:
        return "Moderately High"
    if score >= 40:
        return "Moderate"
    if score >= 25:
        return "Modest"
    return "Low"


def _pick_catalysts_and_risks(finnhub: dict[str, Any]) -> dict[str, list[str]]:
    """Surface forward-looking catalysts and risks from the Finnhub snapshot.

    Sources the catalyst/risk feed from analyst trend skew, earnings surprise
    cadence, recent ratings actions, insider activity, upcoming earnings,
    and news headline keywords. Output is deduplicated and capped per side.
    """

    if not isinstance(finnhub, dict):
        finnhub = {}
    news_rows = finnhub.get("news") if isinstance(finnhub, dict) else []
    earnings_rows = finnhub.get("earnings") if isinstance(finnhub, dict) else []
    trends = finnhub.get("recommendation_trends") if isinstance(finnhub, dict) else {}
    upgrades = finnhub.get("upgrade_downgrade") or []
    insider_tx = finnhub.get("insider_transactions") or {}
    insider_sent = finnhub.get("insider_sentiment") or {}
    upcoming = finnhub.get("earnings_calendar") or []
    news_sent = finnhub.get("news_sentiment") or {}
    catalysts: list[str] = []
    risks: list[str] = []
    seen_c: set[str] = set()
    seen_r: set[str] = set()

    def _push(bucket: list[str], seen: set[str], item: str) -> None:
        clean = item.strip()
        if not clean:
            return
        key = clean.lower()
        if key in seen:
            return
        seen.add(key)
        bucket.append(clean)

    if isinstance(trends, dict):
        buy = int(trends.get("buy", 0) or 0) + int(trends.get("strong_buy", 0) or 0)
        sell = int(trends.get("sell", 0) or 0) + int(trends.get("strong_sell", 0) or 0)
        if buy > sell and buy > 0:
            _push(catalysts, seen_c, f"Analyst panel is constructive ({buy} buy vs {sell} sell votes).")
        elif sell > buy and sell > 0:
            _push(risks, seen_r, f"Analyst panel is cautious ({sell} sell vs {buy} buy votes).")

    if isinstance(upcoming, list):
        for row in upcoming[:2]:
            date = str(row.get("date") or "").strip()
            quarter = row.get("quarter") or ""
            year = row.get("year") or ""
            if date:
                label = f"Upcoming earnings {f'{year} Q{quarter}'.strip() or ''} on {date}".strip()
                _push(catalysts, seen_c, label)

    if isinstance(earnings_rows, list):
        for row in earnings_rows[:3]:
            if not isinstance(row, dict):
                continue
            surprise_pct = _safe_float(row.get("surprise_percent"))
            period = str(row.get("period") or "").strip()
            if surprise_pct is None:
                continue
            if surprise_pct >= 5:
                _push(catalysts, seen_c, f"EPS beat {surprise_pct:+.1f}% ({period or 'recent'}).")
            elif surprise_pct <= -5:
                _push(risks, seen_r, f"EPS miss {surprise_pct:+.1f}% ({period or 'recent'}).")

    if isinstance(upgrades, list):
        for row in upgrades[:5]:
            if not isinstance(row, dict):
                continue
            company = str(row.get("company") or "Sell-side analyst").strip() or "Sell-side analyst"
            from_g = str(row.get("from_grade") or "").strip()
            to_g = str(row.get("to_grade") or "").strip()
            action = str(row.get("action") or "").strip().lower()
            if not (from_g or to_g):
                continue
            transition = " → ".join(p for p in [from_g, to_g] if p) or to_g or from_g
            label = f"{company}: {transition}".strip()
            if action.startswith("up") or "upgrade" in action:
                _push(catalysts, seen_c, label)
            elif action.startswith("down") or "downgrade" in action:
                _push(risks, seen_r, label)
            elif action == "init" or "initiat" in action:
                if "buy" in to_g.lower() or "outperform" in to_g.lower() or "overweight" in to_g.lower():
                    _push(catalysts, seen_c, f"Initiation: {label}")
                elif "sell" in to_g.lower() or "underperform" in to_g.lower() or "underweight" in to_g.lower():
                    _push(risks, seen_r, f"Initiation: {label}")

    if isinstance(insider_tx, dict):
        net_shares = insider_tx.get("net_shares_180d") or 0
        if isinstance(net_shares, (int, float)) and net_shares >= 25_000:
            _push(catalysts, seen_c, f"Insiders net-bought {int(net_shares):,} shares over the trailing 180 days.")
        elif isinstance(net_shares, (int, float)) and net_shares <= -100_000:
            _push(risks, seen_r, f"Insiders net-sold {abs(int(net_shares)):,} shares over the trailing 180 days.")

    if isinstance(insider_sent, dict):
        mspr = insider_sent.get("net_mspr_6m") or 0
        if isinstance(mspr, (int, float)) and mspr >= 30:
            _push(catalysts, seen_c, f"Insider sentiment (Finnhub MSPR sum {mspr:+.1f}) is broadly positive.")
        elif isinstance(mspr, (int, float)) and mspr <= -30:
            _push(risks, seen_r, f"Insider sentiment (Finnhub MSPR sum {mspr:+.1f}) is broadly negative.")

    if isinstance(news_sent, dict):
        bullish_pct = _safe_float(news_sent.get("bullish_percent"))
        bearish_pct = _safe_float(news_sent.get("bearish_percent"))
        if bullish_pct is not None and bullish_pct >= 0.6:
            _push(catalysts, seen_c, f"News sentiment skews bullish ({bullish_pct*100:.0f}% positive articles).")
        if bearish_pct is not None and bearish_pct >= 0.6:
            _push(risks, seen_r, f"News sentiment skews bearish ({bearish_pct*100:.0f}% negative articles).")

    if isinstance(news_rows, list):
        for row in news_rows[:6]:
            if not isinstance(row, dict):
                continue
            headline = str(row.get("headline") or "").strip()
            if not headline:
                continue
            low = headline.lower()
            if any(tok in low for tok in ("upgrade", "contract win", "beat", "launch", "partnership", "acquires", "buyback")):
                _push(catalysts, seen_c, headline)
            if any(tok in low for tok in ("downgrade", "investigation", "lawsuit", "miss", "delay", "cut", "recall", "warning")):
                _push(risks, seen_r, headline)

    return {
        "catalysts": catalysts[:6],
        "risks": risks[:6],
    }


def _compose_research_dossier(ticker: str) -> dict[str, Any]:
    symbol = ticker.upper().strip()
    generated_at = datetime.now(UTC).isoformat()
    source_metadata: list[dict[str, Any]] = []

    report = generate_full_report(
        ticker=symbol,
        skip_mirofish=False,
        skip_edgar=False,
    )
    report_data = json.loads(report_to_json(report))
    section_verdicts = _build_report_verdicts(report_data)

    source_metadata.append(
        _source_entry(
            "report_stack",
            status="ok",
            detail="technical, dcf, comps, health, edgar, mirofish",
            as_of=str(report_data.get("generated_at") or generated_at),
        )
    )

    sec_cfg = _sec_analysis_settings()
    sec_analysis: dict[str, Any] = {}
    if sec_cfg["analysis_enabled"]:
        sec_out = analyze_latest_filing_for_ticker(
            ticker=symbol,
            form_type="10-K",
            user_agent=sec_cfg["user_agent"],
            skill_dir=SKILL_DIR,
            cache_hours=sec_cfg["cache_hours"],
            max_chars=sec_cfg["max_chars"],
            enable_llm=sec_cfg["llm_enabled"],
        )
        if sec_out.get("ok"):
            sec_analysis = _normalize_sec_analysis_payload(sec_out)
            source_metadata.append(_source_entry("sec_analyze", status="ok", detail="latest filing narrative"))
        else:
            sec_analysis = {"ok": False, "error": str(sec_out.get("error") or "SEC analysis unavailable")}
            source_metadata.append(
                _source_entry("sec_analyze", status="degraded", detail=str(sec_out.get("error") or "analysis failed"), fallback_used=True)
            )
    else:
        sec_analysis = {"ok": False, "error": "SEC filing analysis disabled by config"}
        source_metadata.append(_source_entry("sec_analyze", status="disabled", detail="analysis disabled"))

    sec_compare_data: dict[str, Any] = {}
    if sec_cfg["analysis_enabled"] and sec_cfg["compare_enabled"]:
        sec_compare_out = compare_ticker_over_time(
            symbol,
            form_type="10-K",
            user_agent=sec_cfg["user_agent"],
            skill_dir=SKILL_DIR,
            cache_hours=sec_cfg["cache_hours"],
            max_chars=sec_cfg["max_chars"],
            enable_llm=sec_cfg["llm_enabled"],
            highlight_changes_only=False,
        )
        if sec_compare_out.get("ok"):
            sec_compare_data = _normalize_sec_compare_payload(sec_compare_out)
            source_metadata.append(_source_entry("sec_compare", status="ok", detail="over-time 10-K compare"))
        else:
            sec_compare_data = {"ok": False, "error": str(sec_compare_out.get("error") or "SEC compare unavailable")}
            source_metadata.append(
                _source_entry("sec_compare", status="degraded", detail=str(sec_compare_out.get("error") or "compare failed"), fallback_used=True)
            )
    else:
        sec_compare_data = {"ok": False, "error": "SEC compare disabled by config"}
        source_metadata.append(_source_entry("sec_compare", status="disabled", detail="compare disabled"))

    portfolio_summary: dict[str, Any] = {}
    portfolio_risk: dict[str, Any] = {}
    try:
        auth = DualSchwabAuth(skill_dir=SKILL_DIR)
        account_status = get_account_status(auth=auth, skill_dir=SKILL_DIR)
        if isinstance(account_status, dict):
            portfolio_summary = build_portfolio_summary(account_status)
            portfolio_risk = build_portfolio_risk_analytics(portfolio_summary, skill_dir=SKILL_DIR)
            source_metadata.append(_source_entry("portfolio", status="ok", detail="positions and risk context"))
        else:
            source_metadata.append(_source_entry("portfolio", status="degraded", detail="account status unavailable", fallback_used=True))
    except Exception as exc:  # noqa: BLE001
        portfolio_summary = {}
        portfolio_risk = {}
        source_metadata.append(_source_entry("portfolio", status="degraded", detail=str(exc)[:180], fallback_used=True))

    sector_context: dict[str, Any]
    try:
        sector_context = get_sector_heatmap(skill_dir=SKILL_DIR)
        source_metadata.append(_source_entry("sector_context", status="ok", detail="relative sector heatmap"))
    except Exception as exc:  # noqa: BLE001
        sector_context = {"ok": False, "error": str(exc)}
        source_metadata.append(_source_entry("sector_context", status="degraded", detail=str(exc)[:180], fallback_used=True))

    finnhub = get_finnhub_research_snapshot(symbol, skill_dir=SKILL_DIR)
    finnhub_errors = list(finnhub.get("errors") or []) if isinstance(finnhub, dict) else []
    finnhub_status = "ok" if finnhub.get("ok") else ("disabled" if not finnhub.get("enabled") else "degraded")
    source_metadata.append(
        _source_entry(
            "finnhub",
            status=finnhub_status,
            detail=", ".join(finnhub_errors) if finnhub_errors else "news, targets, recommendations, earnings, metrics",
            as_of=str(finnhub.get("as_of") or generated_at),
            fallback_used=finnhub_status != "ok",
        )
    )

    report_v2 = build_report_v2(report_data, portfolio_summary=portfolio_summary or None)
    signal_score = _safe_float((report_data.get("technical") or {}).get("signal_score")) or 0.0
    margin_of_safety = _safe_float((report_data.get("dcf") or {}).get("margin_of_safety")) or 0.0
    raw_confidence = _safe_float((report_v2.get("ic_snapshot") or {}).get("confidence_score"))
    if raw_confidence is None:
        raw_confidence = (signal_score * 0.7) + max(-20.0, min(20.0, margin_of_safety))
    confidence_score = max(0.0, min(100.0, raw_confidence))
    catalyst_risk = _pick_catalysts_and_risks(finnhub if isinstance(finnhub, dict) else {})

    # Backfill quote/52-week range from the integrated report stack so the
    # dossier still reads as institutional even when Finnhub is degraded.
    technical_block = report_data.get("technical") or {}
    finnhub_quote = (finnhub or {}).get("quote") or {}
    finnhub_metrics = (finnhub or {}).get("metrics") or {}
    if _safe_float(finnhub_quote.get("current")) is None:
        backfill = _safe_float(technical_block.get("current_price"))
        if backfill is not None:
            finnhub_quote = dict(finnhub_quote)
            finnhub_quote["current"] = backfill
    if _safe_float(finnhub_metrics.get("52week_high")) is None:
        backfill = _safe_float(technical_block.get("high_52w"))
        if backfill is not None:
            finnhub_metrics = dict(finnhub_metrics)
            finnhub_metrics["52week_high"] = backfill
    if _safe_float(finnhub_metrics.get("52week_low")) is None:
        backfill = _safe_float(technical_block.get("low_52w"))
        if backfill is not None:
            finnhub_metrics = dict(finnhub_metrics)
            finnhub_metrics["52week_low"] = backfill
    if isinstance(finnhub, dict):
        finnhub = dict(finnhub)
        finnhub["quote"] = finnhub_quote
        finnhub["metrics"] = finnhub_metrics

    raw_recommendation = (report_v2.get("ic_snapshot") or {}).get("recommendation")
    horizon = (
        (report_v2.get("ic_snapshot") or {}).get("time_horizon")
        or (report_v2.get("ic_snapshot") or {}).get("horizon")
        or "3-12 months"
    )
    confidence_label = (report_v2.get("ic_snapshot") or {}).get("confidence_label") or _confidence_label_from_score(confidence_score)

    report_trust = build_report_trust_payload(
        {
            **report_data,
            "report_v2": report_v2,
            "generated_at": generated_at,
        }
    )
    dossier = {
        "ticker": symbol,
        "generated_at": generated_at,
        "executive_pitch": {
            "thesis": str((report_v2.get("thesis") or {}).get("claim") or f"{symbol} setup requires review of report stack and SEC context."),
            "recommendation": _format_recommendation(raw_recommendation, default="WATCH"),
            "recommendation_raw": str(raw_recommendation or "").strip().lower(),
            "confidence_label": str(confidence_label),
            "confidence_score": round(confidence_score, 1),
            "time_horizon": str(horizon),
        },
        "sections": {
            "technical_valuation_fundamentals": {
                "report_v2": report_v2,
                "section_verdicts": section_verdicts,
                "raw_report": report_data,
            },
            "sec_narrative": {
                "analyze": sec_analysis,
                "compare": sec_compare_data,
            },
            "portfolio_and_sector_context": {
                "portfolio_summary": portfolio_summary,
                "portfolio_risk": portfolio_risk,
                "sector_heatmap": sector_context,
            },
            "finnhub_catalysts_risks": {
                "snapshot": finnhub,
                "catalysts": catalyst_risk["catalysts"],
                "risks": catalyst_risk["risks"],
            },
        },
        "source_metadata": source_metadata,
        "fallback_notes": [entry["detail"] for entry in source_metadata if entry.get("fallback_used") and entry.get("detail")],
        "report_trust": report_trust,
    }
    return dossier


def _dossier_to_markdown(dossier: dict[str, Any]) -> str:
    ticker = str(dossier.get("ticker") or "—")
    generated_at = str(dossier.get("generated_at") or "")
    pitch = dossier.get("executive_pitch") or {}
    sections = dossier.get("sections") or {}
    sec_narr = sections.get("sec_narrative") or {}
    portfolio = sections.get("portfolio_and_sector_context") or {}
    fin = sections.get("finnhub_catalysts_risks") or {}
    fundamentals = sections.get("technical_valuation_fundamentals") or {}
    report_v2 = fundamentals.get("report_v2") or {}
    raw_report = fundamentals.get("raw_report") or {}
    technical = raw_report.get("technical") or {}
    dcf = raw_report.get("dcf") or {}
    comps = raw_report.get("comps") or {}
    health = raw_report.get("health") or {}
    sec_analyze = sec_narr.get("analyze") or {}
    sec_compare = ((sec_narr.get("compare") or {}).get("compare") or {})
    snapshot = fin.get("snapshot") or {}
    quote = snapshot.get("quote") or {}
    pt = snapshot.get("price_target") or {}
    trends = snapshot.get("recommendation_trends") or {}
    rec_history = list(trends.get("history") or [])
    catalysts = list(fin.get("catalysts") or [])
    risks = list(fin.get("risks") or [])
    source_rows = list(dossier.get("source_metadata") or [])
    fallback_notes = list(dossier.get("fallback_notes") or [])
    source_index = {str(row.get("name") or ""): i + 1 for i, row in enumerate(source_rows)}

    def _num(value: Any, digits: int = 2) -> str:
        v = _safe_float(value)
        if v is None:
            return "n/a"
        return f"{v:.{digits}f}"

    def _pct(value: Any, digits: int = 1) -> str:
        v = _safe_float(value)
        if v is None:
            return "n/a"
        return f"{v:.{digits}f}%"

    profile = snapshot.get("profile") or {}
    metrics = snapshot.get("metrics") or {}
    earnings = list(snapshot.get("earnings") or [])
    news = list(snapshot.get("news") or [])
    peers = list(snapshot.get("peers") or [])
    upgrades = list(snapshot.get("upgrade_downgrade") or [])
    insider_tx = snapshot.get("insider_transactions") or {}
    insider_sent = snapshot.get("insider_sentiment") or {}
    dividends = list(snapshot.get("dividends") or [])
    splits = list(snapshot.get("splits") or [])
    upcoming = list(snapshot.get("earnings_calendar") or [])
    news_sent = snapshot.get("news_sentiment") or {}
    sec_filings = list(snapshot.get("sec_filings") or [])

    sec_summary = str(sec_analyze.get("narrative_summary") or sec_analyze.get("error") or "SEC analysis unavailable.")
    compare_summary = str(sec_compare.get("narrative_summary") or (sec_narr.get("compare") or {}).get("error") or "SEC compare unavailable.")
    hhi_label = (((portfolio.get("portfolio_risk") or {}).get("concentration") or {}).get("hhi_label") or "Unavailable")
    positions_count = (portfolio.get("portfolio_summary") or {}).get("positions_count", "n/a")
    total_mv = (portfolio.get("portfolio_summary") or {}).get("total_market_value", "n/a")
    recommendation = pitch.get("recommendation") or "WATCH"
    confidence_label = pitch.get("confidence_label") or "Moderate"
    confidence_score = pitch.get("confidence_score")
    if confidence_score is None or confidence_score == "":
        confidence_score = "n/a"
    bull_votes = int(trends.get("buy", 0) or 0) + int(trends.get("strong_buy", 0) or 0)
    bear_votes = int(trends.get("sell", 0) or 0) + int(trends.get("strong_sell", 0) or 0)
    hold_votes = int(trends.get("hold", 0) or 0)

    def _money_scaled(value: Any) -> str:
        """Format Finnhub-style market caps (already in millions)."""
        v = _safe_float(value)
        if v is None:
            return "n/a"
        abs_v = abs(v)
        if abs_v >= 1_000_000:
            return f"${v/1_000_000:.2f}T"
        if abs_v >= 1_000:
            return f"${v/1_000:.2f}B"
        return f"${v:,.2f}M"

    def _money_currency(value: Any) -> str:
        v = _safe_float(value)
        if v is None:
            return "n/a"
        if v < 0:
            return f"-${abs(v):,.2f}"
        return f"${v:,.2f}"

    def _ratio_pct(value: Any, digits: int = 1) -> str:
        """Format a fractional ratio (0.0-1.0) as a percentage.

        This intentionally always multiplies by 100; use ``_pct`` for values that
        are already expressed as percentage points (e.g., Finnhub margin fields).
        """
        v = _safe_float(value)
        if v is None:
            return "n/a"
        return f"{v * 100:.{digits}f}%"

    def _pct_finnhub(value: Any, digits: int = 1) -> str:
        """Format a Finnhub metric that is already expressed in percent."""
        v = _safe_float(value)
        if v is None:
            return "n/a"
        return f"{v:.{digits}f}%"

    def _shares_human(value: Any) -> str:
        v = _safe_float(value)
        if v is None:
            return "n/a"
        abs_v = abs(v)
        if abs_v >= 1_000_000:
            return f"{v/1_000_000:.2f}M shares"
        if abs_v >= 1_000:
            return f"{v/1_000:.1f}K shares"
        return f"{v:,.0f} shares"

    def _cite(*names: str) -> str:
        ids = [source_index.get(name) for name in names if source_index.get(name)]
        if not ids:
            return ""
        return " " + "".join(f"[{idx}]" for idx in sorted(set(ids)))

    industry_text = profile.get("finnhub_industry") or "Equity Research"
    region_text = profile.get("country") or "Global"
    issuer_name = profile.get("name") or ticker
    horizon_text = pitch.get("time_horizon") or "3-12 months"

    rec_consensus_summary = "Consensus panel data is unavailable."
    if bull_votes or bear_votes or hold_votes:
        rec_consensus_summary = (
            f"Latest consensus panel: {bull_votes} bullish · {hold_votes} neutral · "
            f"{bear_votes} bearish votes."
        )

    operating_profile = "transitioning"
    gross_margin_v = _safe_float(metrics.get("gross_margin_ttm"))
    op_margin_v = _safe_float(metrics.get("operating_margin_ttm"))
    if gross_margin_v is not None and gross_margin_v >= 60:
        operating_profile = "high-gross-margin"
    elif gross_margin_v is not None and gross_margin_v >= 40:
        operating_profile = "mid-gross-margin"
    if op_margin_v is not None and op_margin_v < 0:
        operating_profile = f"{operating_profile} with negative operating leverage"

    stage_label = "Stage 2 uptrend candidate" if technical.get("stage_2") else "non-Stage-2 / transitional tape"
    vcp_label = "VCP structure present" if technical.get("vcp") else "VCP structure not confirmed"

    base_return = (
        (report_v2.get("ic_snapshot") or {}).get("expected_return_base_pct")
        if (report_v2.get("ic_snapshot") or {}).get("expected_return_base_pct") is not None
        else (report_v2.get("ic_snapshot") or {}).get("expected_return_base_case")
    )
    bull_return = (
        (report_v2.get("ic_snapshot") or {}).get("expected_return_bull_pct")
        if (report_v2.get("ic_snapshot") or {}).get("expected_return_bull_pct") is not None
        else (report_v2.get("ic_snapshot") or {}).get("expected_return_bull_case")
    )
    bear_return = (
        (report_v2.get("ic_snapshot") or {}).get("expected_return_bear_pct")
        if (report_v2.get("ic_snapshot") or {}).get("expected_return_bear_pct") is not None
        else (report_v2.get("ic_snapshot") or {}).get("expected_return_bear_case")
    )

    lines: list[str] = [
        f"# {ticker} — Institutional Research Report",
        "",
        "## Cover Page",
        "",
        f"**Prepared:** {generated_at}",
        "**Analyst:** TradingBot Research Engine",
        f"**Coverage:** {industry_text}",
        f"**Region:** {region_text}",
        f"**Document Type:** Institutional Investment Note{_cite('report_stack', 'finnhub', 'sec_analyze')}",
        "**Analytical Stance:** Zero-bias, evidence-weighted underwriting framework (bull/base/bear).",
        "",
        "---",
        "",
        f"**Current Price:** {_money_currency(quote.get('current'))}  ",
        f"**52-Week Range:** {_money_currency(metrics.get('52week_low'))} – {_money_currency(metrics.get('52week_high'))}  ",
        (
            f"**Consensus Target (Mean):** {_money_currency(pt.get('mean'))}"
            + (f" (n={pt.get('number_of_analysts')})" if pt.get('number_of_analysts') else "")
            + "  "
        ),
        "",
        f"**Recommendation:** {recommendation} | **Confidence:** {confidence_label} ({confidence_score}/100) | **Horizon:** {horizon_text}",
        "",
        "Sections covered: Business Strategy & Operations · Fundamental Performance · Peer Relative Positioning · Valuation · Technical Stage Analysis · SEC Narrative · Portfolio Fit · Catalysts and Risks · Insider Activity · Analyst Actions · Monitoring",
        "",
        "## Executive Investment Summary",
        "",
        str(pitch.get("thesis") or "No thesis generated."),
        "",
        (
            f"Current setup: technical signal {_num(technical.get('signal_score'), 0)}/100, "
            f"DCF margin of safety {_pct(dcf.get('margin_of_safety'))}, and Street panel "
            f"{bull_votes} bullish / {hold_votes} neutral / {bear_votes} bearish."
            f"{_cite('finnhub', 'report_stack', 'portfolio')}"
        ),
        "",
        (
            f"Portfolio context is {hhi_label}; this drives risk budget and sizing."
            f"{_cite('portfolio')}"
        ),
        "",
        (
            f"Desk stance: **{recommendation}** with **{confidence_label}** confidence over a "
            f"**{horizon_text}** horizon. Execute only if invalidation triggers remain explicit and position size stays within policy."
        ),
        "",
        "### IC Quick Scorecard",
        "",
        "| Underwriting Lens | Current Read |",
        "|---|---|",
        f"| Recommendation / Confidence | {recommendation} / {confidence_label} ({confidence_score}/100) |",
        f"| Base / Bull / Bear Return | {_pct(base_return)} / {_pct(bull_return)} / {_pct(bear_return)} |",
        f"| Technical Stage / VCP | {stage_label} / {vcp_label} |",
        f"| DCF Margin of Safety | {_pct(dcf.get('margin_of_safety'))} |",
        f"| Portfolio Concentration Context | {hhi_label} |",
        "",
        "### Analyst Mandate and Research Method",
        "",
        (
            "This dossier is written from an institutional mandate: compile the fullest practical operating, "
            "fundamental, valuation, and tape-based evidence set; then pressure-test the thesis against explicit "
            "counter-arguments and invalidation lines. The objective is not to defend a side, but to rank the "
            "probability distribution and identify whether reward-to-risk is improving or deteriorating."
        ),
        "",
        "---",
        "",
        "## Part I: Company and Business Model",
        "",
        (
            f"{issuer_name} operates within the {industry_text or 'sector'} segment, "
            f"with primary exchange listing on {profile.get('exchange') or 'n/a'} and reporting in "
            f"{profile.get('currency') or 'USD'}. The issuer maps to sector ETF proxy "
            f"{technical.get('sector_etf') or 'unknown'} for relative-strength reads and is benchmarked against peers "
            f"using both fundamental multiples and tape structure."
            f"{_cite('finnhub', 'report_stack')}"
        ),
        "",
        "**Issuer Snapshot**",
        "",
        f"- Issuer: {issuer_name} | Industry: {industry_text or 'n/a'} | Exchange: {profile.get('exchange') or 'n/a'}",
        f"- Geography / Currency: {profile.get('country') or 'n/a'} / {profile.get('currency') or 'n/a'}",
        f"- Market Cap: {_money_scaled(profile.get('market_cap'))} | Shares Out: {_shares_human((_safe_float(profile.get('share_outstanding')) or 0) * 1_000_000) if _safe_float(profile.get('share_outstanding')) else 'n/a'} | IPO: {profile.get('ipo') or 'n/a'}{_cite('finnhub')}",
        f"- Beta (5Y): {_num(metrics.get('beta'))} | YTD Return: {_pct_finnhub(metrics.get('ytd_price_return_daily'))} | 52w Return: {_pct_finnhub(metrics.get('52week_price_return_daily'))}{_cite('finnhub')}",
        f"- Core thesis context: {(report_v2.get('thesis') or {}).get('claim') or 'Derived from integrated report stack.'}{_cite('report_stack')}",
        "",
        "### Business Strategy & Operations Deep Dive",
        "",
        (
            f"Current evidence frames the operating profile as **{operating_profile}**. For underwriting purposes, "
            "focus on whether growth quality is broadening across customer cohorts, whether margin durability is "
            "supported by pricing power and operating discipline, and whether management commentary and SEC deltas "
            "confirm or challenge the current narrative."
            f"{_cite('finnhub', 'sec_analyze', 'sec_compare')}"
        ),
    ]

    if peers:
        lines.extend([
            "",
            "**Peer Universe**",
            "",
            (
                f"Comparable issuers identified by Finnhub: {', '.join(peers[:8])}. "
                "Peer set frames relative valuation and trend comparison; differences in scale, capital intensity, "
                "and exposure to end-markets should be considered before treating multiples as directly comparable."
                f"{_cite('finnhub')}"
            ),
        ])

    lines.extend([
        "",
        "---",
        "",
        "## Part II: Fundamental Performance Analysis",
        "",
        (
            "Fundamental performance is read through a growth, margin, capital-efficiency, and balance-sheet lens. "
            "These four dimensions together inform whether the business is in a re-rating regime or whether multiple "
            "compression risk is elevated. The table below captures TTM trends and quarterly liquidity posture."
            f"{_cite('finnhub')}"
        ),
        "",
        "| Fundamental Metric | Value | Commentary |",
        "|---|---:|---|",
        f"| Revenue Growth (TTM YoY) | {_pct_finnhub(metrics.get('revenue_growth_ttm_yoy'))} | Top-line momentum, TTM vs prior TTM |",
        f"| Revenue Growth (5Y CAGR) | {_pct_finnhub(metrics.get('revenue_growth_5y'))} | Long-cycle compounding |",
        f"| EPS Growth (TTM YoY) | {_pct_finnhub(metrics.get('eps_growth_ttm_yoy'))} | Earnings trajectory check |",
        f"| EPS Growth (5Y CAGR) | {_pct_finnhub(metrics.get('eps_growth_5y'))} | Earnings power durability |",
        f"| Gross Margin (TTM) | {_pct_finnhub(metrics.get('gross_margin_ttm'))} | Pricing power and unit economics |",
        f"| Operating Margin (TTM) | {_pct_finnhub(metrics.get('operating_margin_ttm'))} | Operating efficiency trend |",
        f"| Net Margin (TTM) | {_pct_finnhub(metrics.get('net_margin_ttm'))} | Bottom-line profitability quality |",
        f"| Free Cash Flow Margin (TTM) | {_pct_finnhub(metrics.get('fcf_margin_ttm'))} | Cash conversion quality |",
        f"| ROE / ROA (TTM) | {_pct_finnhub(metrics.get('roe_ttm'))} / {_pct_finnhub(metrics.get('roa_ttm'))} | Capital efficiency read-through |",
        f"| ROIC (TTM) | {_pct_finnhub(metrics.get('roic_ttm'))} | Return vs cost of capital |",
        f"| Current Ratio / Quick Ratio | {_num(metrics.get('current_ratio_quarterly'))} / {_num(metrics.get('quick_ratio_quarterly'))} | Liquidity posture |",
        f"| Debt / Equity (Q) | {_num(metrics.get('debt_to_equity_quarterly'))} | Leverage profile |",
        f"| Interest Coverage (TTM) | {_num(metrics.get('interest_coverage_ttm'))} | Solvency cushion |",
        f"| Dividend Yield (TTM) | {_pct_finnhub(metrics.get('dividend_yield_ttm'))} | Total-return income contribution |",
        f"| Payout Ratio (TTM) | {_pct_finnhub(metrics.get('payout_ratio_ttm'))} | Dividend coverage |",
        "",
        "### Earnings Quality (Recent Prints)",
        "",
        (
            "Earnings dispersion and surprise cadence remain central to near-term re-rating potential. "
            "Read these prints alongside valuation multiples; profitable growth at expanding margins typically "
            "supports multiple expansion, while declining margins or earnings misses can compress multiples even "
            f"when revenue growth is intact.{_cite('finnhub')}"
        ),
        "",
        "| Period | Actual EPS | Estimate EPS | Surprise % |",
        "|---|---:|---:|---:|",
    ])

    if earnings:
        for row in earnings[:6]:
            lines.append(
                f"| {row.get('period') or 'n/a'} | {_num(row.get('actual'))} | {_num(row.get('estimate'))} | {_pct(row.get('surprise_percent'))} |"
            )
    else:
        lines.append("| n/a | n/a | n/a | n/a |")

    if upcoming:
        lines.extend([
            "",
            "### Upcoming Earnings Calendar",
            "",
            "| Date | Quarter | EPS Estimate | Revenue Estimate |",
            "|---|---|---:|---:|",
        ])
        for row in upcoming[:4]:
            quarter_str = ""
            if row.get("year") and row.get("quarter"):
                quarter_str = f"{row.get('year')} Q{row.get('quarter')}"
            rev_est = _safe_float(row.get("revenue_estimate"))
            rev_text = f"${rev_est/1_000_000_000:.2f}B" if rev_est else "n/a"
            lines.append(
                f"| {row.get('date') or 'n/a'} | {quarter_str or 'n/a'} | {_num(row.get('eps_estimate'))} | {rev_text} |"
            )

    lines.extend(
        [
            "",
            "---",
            "",
            "## Part II-B: Peer Relative Positioning",
            "",
            (
                "Peer-relative analysis is used to separate company-specific execution from broad sector beta. "
                "Multiples alone are insufficient: the key question is whether differential growth, margin path, "
                "and balance-sheet quality justify premium or discount positioning versus direct comps."
                f"{_cite('report_stack', 'finnhub')}"
            ),
            "",
            "| Relative Lens | Value |",
            "|---|---:|",
            f"| Peer Median P/E (if available) | {_num(comps.get('median_pe'))} |",
            f"| Implied Price from P/E Method | {_money_currency(comps.get('implied_price_pe'))} |",
            f"| Implied Price from P/S Method | {_money_currency(comps.get('implied_price_ps'))} |",
            f"| Consensus Panel Skew (Bull / Hold / Bear) | {bull_votes} / {hold_votes} / {bear_votes} |",
        ]
    )

    lines.extend(
        [
            "",
            "---",
            "",
            "## Part III: Valuation and Technical Positioning",
            "",
            (
                "Valuation and technical positioning are read jointly. Intrinsic-value framing (DCF) is anchored "
                "by the assumed growth, discount rate, and terminal growth combination, while the multiples table "
                "provides cross-section context against history and peers. Technical positioning is summarized through "
                "trend regime, breakout structure, and signal scoring."
                f"{_cite('report_stack', 'finnhub')}"
            ),
            "",
            "| Valuation / Technical | Value |",
            "|---|---:|",
            f"| DCF Intrinsic Value | {_money_currency(dcf.get('intrinsic_value'))} |",
            f"| DCF Margin of Safety | {_pct(dcf.get('margin_of_safety'))} |",
            f"| Consensus Target (Mean / Median) | {_money_currency(pt.get('mean'))} / {_money_currency(pt.get('median'))} |",
            f"| Consensus Target Range (Low / High) | {_money_currency(pt.get('low'))} / {_money_currency(pt.get('high'))} |",
            f"| P/E (TTM) | {_num(metrics.get('pe_ttm'))} |",
            f"| P/E (Annual) | {_num(metrics.get('pe_annual'))} |",
            f"| P/B (Annual) | {_num(metrics.get('pb_annual'))} |",
            f"| P/S (TTM) | {_num(metrics.get('ps_ttm'))} |",
            f"| EV / EBITDA | {_num(metrics.get('ev_to_ebitda'))} |",
            f"| EV / Sales | {_num(metrics.get('ev_to_sales'))} |",
            f"| EPS (TTM / Annual) | {_num(metrics.get('eps_ttm'))} / {_num(metrics.get('eps_annual'))} |",
            f"| Book Value / Share (Annual) | {_money_currency(metrics.get('book_value_per_share_annual'))} |",
            f"| Technical Signal Score | {_num(technical.get('signal_score'), 0)} / 100 |",
            f"| Stage 2 / VCP | {'YES' if technical.get('stage_2') else 'NO'} / {'YES' if technical.get('vcp') else 'NO'} |",
            "",
            (
                f"Technical structure implies {'constructive trend continuation' if technical.get('stage_2') else 'a non-trending or transitional tape'} "
                f"with sector monitor {technical.get('sector_etf') or 'n/a'}. From a valuation perspective, margin-of-safety "
                "and multiple profile should be read together with SEC and catalyst evidence, not in isolation. "
                "If the trend is constructive but multiples are stretched, prefer reduced size and explicit invalidation; "
                "if both are constructive, scale only against documented catalysts and risk budget capacity."
                f"{_cite('report_stack', 'finnhub', 'sec_analyze')}"
            ),
            "",
            "### Technical Stage Analysis",
            "",
            (
                f"Stage framework read: **{stage_label}** with **{vcp_label}**. "
                "Execution discipline should prioritize asymmetric entries near defined support/pivot structure, "
                "while avoiding thesis drift when price action invalidates the expected stage progression. "
                "Treat momentum confirmation as necessary but not sufficient; fundamental and filing corroboration "
                "still govern sizing confidence."
                f"{_cite('report_stack', 'finnhub')}"
            ),
            "",
            "---",
            "",
            "## Part IV: SEC Narrative and Comparative Filing Deltas",
            "",
            (
                "SEC narrative and comparative filing deltas surface qualitative changes that quantitative metrics often miss: "
                "shifts in risk-factor language, evolving guidance posture, and incremental management commentary. "
                "Treat this as forward-looking corroboration or contradiction of the quantitative framing above."
                f"{_cite('sec_analyze', 'sec_compare')}"
            ),
            "",
            "**Filing Analyze**",
            "",
            f"- Headline: {sec_analyze.get('summary_headline') or sec_analyze.get('error') or 'Unavailable'}{_cite('sec_analyze')}",
            f"- Narrative: {sec_summary}{_cite('sec_analyze')}",
            "",
            "**Filing Compare (Over Time)**",
            "",
            f"- Headline: {sec_compare.get('summary_headline') or (sec_narr.get('compare') or {}).get('error') or 'Unavailable'}{_cite('sec_compare')}",
            f"- Narrative: {compare_summary}{_cite('sec_compare')}",
        ]
    )

    if sec_filings:
        lines.extend([
            "",
            "**Recent SEC Filings (Finnhub)**",
            "",
            "| Form | Filed | Accepted | Report |",
            "|---|---|---|---|",
        ])
        for row in sec_filings[:5]:
            url = row.get("report_url") or row.get("filing_url") or ""
            link = f"[link]({url})" if url else "n/a"
            lines.append(
                f"| {row.get('form') or 'n/a'} | {row.get('filed_date') or 'n/a'} | {row.get('accepted_date') or 'n/a'} | {link} |"
            )

    lines.extend([
        "",
        "---",
        "",
        "## Part V: Portfolio Fit and Risk Budget Context",
        "",
        (
            "Portfolio fit converts a standalone idea into a position decision. Sector overlap, concentration "
            "contribution, and risk-budget impact dictate sizing rather than the headline thesis alone. "
            f"Concentration here reads as **{hhi_label}**, with {positions_count} open positions across "
            f"a total market value of {total_mv}."
            f"{_cite('portfolio', 'report_stack')}"
        ),
        "",
        "| Portfolio Lens | Value |",
        "|---|---|",
        f"| Open positions | {positions_count} |",
        f"| Total market value | {total_mv} |",
        f"| Concentration label | {hhi_label} |",
        f"| Risk budget impact | {(report_v2.get('portfolio_fit') or {}).get('risk_budget_impact') or 'Unavailable'} |",
        f"| Sector overlap (%) | {_pct_finnhub((report_v2.get('portfolio_fit') or {}).get('sector_overlap_pct'))} |",
        f"| Correlation proxy | {_num((report_v2.get('portfolio_fit') or {}).get('correlation_proxy'), 3)} |",
        "",
        "---",
        "",
        "## Part VI: Catalyst and Risk Matrix",
        "",
        (
            "Catalysts and risks are aggregated across filing cadence, sentiment surfaces, and quantitative "
            "checks. Treat this as a forward-event map: catalysts can re-rate price quickly in either direction, "
            "and explicit risk lines define when the thesis must be re-underwritten or unwound."
            f"{_cite('finnhub', 'sec_analyze', 'report_stack')}"
        ),
        "",
        "| Type | Item |",
        "|---|---|",
    ])

    if catalysts:
        lines.extend([f"| Catalyst | {item} |" for item in catalysts[:8]])
    else:
        lines.append("| Catalyst | No clear catalysts extracted from available feeds. |")

    if risks:
        lines.extend([f"| Risk | {item} |" for item in risks[:8]])
    else:
        lines.append("| Risk | No clear risks extracted from available feeds. |")

    invalidation = (report_v2.get("ic_snapshot") or {}).get("invalidation") or []
    if invalidation:
        lines.extend(["", "### Invalidation Criteria", ""])
        lines.extend([f"- {item}" for item in invalidation[:5]])

    # --- Insider Activity -------------------------------------------------
    has_insider_data = (insider_tx.get("rows") or insider_sent.get("rows"))
    if has_insider_data:
        lines.extend([
            "",
            "---",
            "",
            "## Part VII: Insider Activity (Trailing 180 Days)",
            "",
            (
                "Form-4 insider transactions and Finnhub's Monthly Share Purchase Ratio (MSPR) capture "
                "executive and director conviction. The scoring below counts only open-market activity "
                "(SEC Form-4 codes **P** and **S**); stock-based compensation grants (A), option exercises (M), "
                "tax-withholding sales (F), and dispositions to the issuer (D) are excluded because they are "
                "non-discretionary and do not represent a market signal. Concentrated open-market buying or "
                "persistent net-positive MSPR frequently precedes positive re-rating; sustained net-selling can "
                "be an early signal of decelerating fundamentals or governance stress — though planned 10b5-1 "
                "sales should still be discounted."
                f"{_cite('finnhub')}"
            ),
            "",
            "| Insider Lens (Open-Market Only) | Value |",
            "|---|---:|",
            f"| Net Shares (Buys − Sells, 180d) | {_shares_human(insider_tx.get('net_shares_180d'))} |",
            f"| Net Dollars (Approx., 180d) | {_money_currency(insider_tx.get('net_dollars_180d'))} |",
            f"| Open-Market Buys (P) | {insider_tx.get('buy_count_180d') or 0} |",
            f"| Open-Market Sells (S) | {insider_tx.get('sell_count_180d') or 0} |",
            f"| Insider Sentiment (MSPR Sum, 6m) | {_num(insider_sent.get('net_mspr_6m'))} |",
            f"| Insider Net Share Change (6m) | {_num(insider_sent.get('net_change_6m'), 0)} |",
        ])
        ins_rows = insider_tx.get("rows") or []
        if ins_rows:
            lines.extend([
                "",
                "**Recent Form-4 Activity**",
                "",
                "_Code legend: **P** open-market purchase · **S** open-market sale · **A** grant/award · **M** option exercise · **F** shares withheld for taxes · **D** disposition to issuer · **G** gift_",
                "",
                "| Date | Insider | Code | Type | Shares | Price |",
                "|---|---|:--:|:--:|---:|---:|",
            ])
            code_label = {
                "P": "Buy",
                "S": "Sell",
                "A": "Award",
                "M": "Exercise",
                "F": "Tax",
                "D": "Disposition",
                "G": "Gift",
            }
            for row in ins_rows[:8]:
                code = (row.get("transaction_code") or "").upper()
                kind = code_label.get(code, "Other")
                lines.append(
                    f"| {row.get('transaction_date') or 'n/a'} | {row.get('name') or 'n/a'} | {code or '-'} | {kind} | {_num(row.get('share'), 0)} | {_money_currency(row.get('transaction_price'))} |"
                )

    # --- Analyst Actions --------------------------------------------------
    if upgrades or rec_history:
        lines.extend([
            "",
            "---",
            "",
            "## Part VIII: Sell-Side Analyst Activity",
            "",
            (
                "Sell-side ratings actions and consensus drift surface how the analyst community is "
                "repricing the issuer in real time. Upgrades clustered near earnings or guidance often "
                "co-incide with revisions cycles, while persistent downgrade flow tends to lead price "
                f"weakness on a multi-week basis. {rec_consensus_summary}{_cite('finnhub')}"
            ),
        ])
        if rec_history:
            lines.extend([
                "",
                "**Consensus History**",
                "",
                "| Period | Strong Buy | Buy | Hold | Sell | Strong Sell |",
                "|---|---:|---:|---:|---:|---:|",
            ])
            for row in rec_history[:6]:
                lines.append(
                    f"| {row.get('period') or 'n/a'} | {row.get('strong_buy', 0)} | {row.get('buy', 0)} | {row.get('hold', 0)} | {row.get('sell', 0)} | {row.get('strong_sell', 0)} |"
                )
        if upgrades:
            lines.extend([
                "",
                "**Recent Ratings Actions**",
                "",
                "| Date | Firm | From | To | Action |",
                "|---|---|---|---|---|",
            ])
            for row in upgrades[:6]:
                date_str = row.get("grade_time") or "n/a"
                if isinstance(date_str, str) and "T" in date_str:
                    date_str = date_str.split("T")[0]
                lines.append(
                    f"| {date_str} | {row.get('company') or 'n/a'} | {row.get('from_grade') or '-'} | {row.get('to_grade') or '-'} | {(row.get('action') or '-').title()} |"
                )

    # --- Capital Returns --------------------------------------------------
    if dividends or splits:
        lines.extend([
            "",
            "---",
            "",
            "## Part IX: Capital Returns and Corporate Actions",
            "",
            (
                "Dividend cadence, payout cover, and share-action history (splits, special distributions) "
                "are part of the total-return picture. Read the dividend yield in Part II alongside the "
                "schedule below — declining or skipped dividends materially change the income leg of the "
                f"thesis.{_cite('finnhub')}"
            ),
        ])
        if dividends:
            lines.extend([
                "",
                "**Recent Dividends**",
                "",
                "| Ex-Date | Pay Date | Amount | Currency | Frequency |",
                "|---|---|---:|---|---|",
            ])
            freq_map = {1: "Annual", 2: "Semi-annual", 4: "Quarterly", 12: "Monthly"}
            for row in dividends[:6]:
                freq_text = freq_map.get(int(row.get("frequency") or 0), "n/a")
                lines.append(
                    f"| {row.get('ex_date') or 'n/a'} | {row.get('pay_date') or 'n/a'} | {_money_currency(row.get('amount'))} | {row.get('currency') or 'USD'} | {freq_text} |"
                )
        if splits:
            lines.extend([
                "",
                "**Stock Splits**",
                "",
                "| Date | Ratio (To : From) |",
                "|---|---|",
            ])
            for row in splits[:4]:
                ratio = "n/a"
                tf = _safe_float(row.get("to_factor"))
                ff = _safe_float(row.get("from_factor"))
                if tf and ff:
                    ratio = f"{tf:.0f} : {ff:.0f}"
                lines.append(f"| {row.get('date') or 'n/a'} | {ratio} |")

    # --- News Sentiment ---------------------------------------------------
    sent_buzz = _safe_float(news_sent.get("buzz_articles_in_last_week"))
    sent_score = _safe_float(news_sent.get("company_news_score"))
    if sent_buzz is not None or sent_score is not None or news:
        lines.extend(["", "---", "", "## Part X: News and Sentiment Pulse", ""])
        if sent_score is not None or sent_buzz is not None:
            sector_score = _safe_float(news_sent.get("sector_avg_news_score"))
            score_delta = ""
            if sent_score is not None and sector_score is not None:
                if sent_score > sector_score:
                    score_delta = f" ({(sent_score - sector_score)*100:+.0f}bp vs sector)"
                else:
                    score_delta = f" ({(sent_score - sector_score)*100:+.0f}bp vs sector)"
            lines.append(
                (
                    f"Finnhub measures a {(_num(sent_score, 2) if sent_score is not None else 'n/a')}"
                    f" composite news score{score_delta} with "
                    f"{int(sent_buzz) if sent_buzz is not None else 'n/a'} articles in the trailing week. "
                    f"Bullish vs bearish article share: {_ratio_pct(news_sent.get('bullish_percent'))} / "
                    f"{_ratio_pct(news_sent.get('bearish_percent'))}."
                    f"{_cite('finnhub')}"
                )
            )
        if news:
            lines.extend(["", "**Newsflow Digest**", ""])
            for item in news[:8]:
                headline = str(item.get("headline") or "").strip()
                summary = str(item.get("summary") or "").strip()
                source = str(item.get("source") or "").strip()
                date_str = str(item.get("datetime") or "").split("T")[0] if item.get("datetime") else ""
                if headline:
                    badge = f"{date_str} · {source}" if date_str and source else (source or "source n/a")
                    lines.append(f"- **{headline}** _({badge})_")
                    if summary:
                        lines.append(f"  - {summary[:240]}")
        else:
            lines.append("- No recent Finnhub news items were returned.")

    lines.extend(["", "## Key Metrics at a Glance", ""])
    lines.extend(
        [
            "| Metric | Value | Source |",
            "|---|---:|---|",
            f"| Current Price | {_money_currency(quote.get('current'))} | Quote feed |",
            f"| 52-Week High / Low | {_money_currency(metrics.get('52week_high'))} / {_money_currency(metrics.get('52week_low'))} | Finnhub metrics |",
            f"| Consensus Target (Mean) | {_money_currency(pt.get('mean'))} | Finnhub consensus |",
            f"| DCF Margin of Safety | {_pct(dcf.get('margin_of_safety'))} | Full report DCF |",
            f"| Health Flag Count | {len(health.get('flags') or [])} | Full report health |",
            f"| Portfolio Concentration | {hhi_label} | Portfolio risk analytics |",
            f"| Recommendation | {recommendation} ({confidence_label}, {confidence_score}/100) | TradingBot Research Engine |",
        ]
    )

    lines.extend(["", "## References", ""])
    if source_rows:
        for idx, row in enumerate(source_rows, start=1):
            lines.append(
                f"{idx}. {row.get('name')}: {row.get('status')} | {row.get('detail') or ''} | as_of={row.get('as_of') or 'n/a'}"
            )
    else:
        lines.append("1. Integrated report stack sources were unavailable.")

    lines.extend(["", "## Limitations & Fallback Notes", ""])
    if fallback_notes:
        lines.extend([f"- {item}" for item in fallback_notes])
    else:
        lines.append("- None")

    if "finnhub_api_key_missing" in {(row.get("detail") or "").lower() for row in source_rows} or any(
        "finnhub_api_key_missing" in str(note) for note in fallback_notes
    ):
        lines.extend([
            "",
            "_Finnhub data is unavailable because no API key is configured. Set `FINNHUB_API_KEY` in your "
            "environment to populate insider activity, analyst ratings, news sentiment, peers, and consensus "
            "targets. Sign up for a free key at https://finnhub.io/ — the dossier respects the free-tier "
            "60 calls/minute limit automatically._",
        ])

    lines.extend(
        [
            "",
            "## Disclaimer",
            "",
            "This report is generated for informational research workflows. It is not investment advice.",
        ]
    )
    lines.append("")
    return polish_dossier_markdown(
        "\n".join(lines),
        trust_payload=dossier.get("report_trust") if isinstance(dossier, dict) else None,
        source_rows=source_rows,
    )


def _escape_pdf_text(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _text_to_simple_pdf(text: str) -> bytes:
    lines = [line[:105] for line in text.splitlines()]
    if not lines:
        lines = ["Research dossier export."]
    line_specs: list[tuple[str, int, int]] = []
    for line in lines:
        s = line.rstrip()
        if s.startswith("# "):
            line_specs.append((s[2:].strip(), 15, 24))
        elif s.startswith("## "):
            line_specs.append((s[3:].strip(), 13, 20))
        elif s.startswith("### "):
            line_specs.append((s[4:].strip(), 12, 18))
        else:
            line_specs.append((s, 10, 14))

    chunks: list[list[tuple[str, int, int]]] = []
    current: list[tuple[str, int, int]] = []
    y = 780
    min_y = 60
    for spec in line_specs:
        _, _size, leading = spec
        if y - leading < min_y and current:
            chunks.append(current)
            current = []
            y = 780
        current.append(spec)
        y -= leading
    if current:
        chunks.append(current)

    objs: list[bytes] = []
    objs.append(b"1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n")

    page_count = max(1, len(chunks))
    first_page_obj = 3
    first_font_obj = first_page_obj + (page_count * 2)
    kids = " ".join(f"{first_page_obj + (idx * 2)} 0 R" for idx in range(page_count))
    objs.append(f"2 0 obj << /Type /Pages /Kids [{kids}] /Count {page_count} >> endobj\n".encode("ascii"))

    for idx, chunk in enumerate(chunks):
        page_obj_num = first_page_obj + (idx * 2)
        content_obj_num = page_obj_num + 1
        stream_ops: list[str] = []
        y_pos = 780
        for line, size, leading in chunk:
            stream_ops.append("BT")
            stream_ops.append(f"/F1 {size} Tf")
            stream_ops.append(f"50 {y_pos} Td")
            stream_ops.append(f"({_escape_pdf_text(line)}) Tj")
            stream_ops.append("ET")
            y_pos -= leading
        footer = f"-- {idx + 1} of {page_count} --"
        stream_ops.extend(
            [
                "BT",
                "/F1 9 Tf",
                "260 28 Td",
                f"({_escape_pdf_text(footer)}) Tj",
                "ET",
            ]
        )
        stream = "\n".join(stream_ops).encode("latin-1", "replace")
        objs.append(
            (
                f"{page_obj_num} 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                f"/Resources << /Font << /F1 {first_font_obj} 0 R >> >> /Contents {content_obj_num} 0 R >> endobj\n"
            ).encode("ascii")
        )
        objs.append(
            f"{content_obj_num} 0 obj << /Length {len(stream)} >> stream\n".encode("ascii")
            + stream
            + b"\nendstream endobj\n"
        )

    objs.append(f"{first_font_obj} 0 obj << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> endobj\n".encode("ascii"))

    out = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for obj in objs:
        offsets.append(len(out))
        out.extend(obj)
    xref_start = len(out)
    out.extend(f"xref\n0 {len(objs) + 1}\n".encode("ascii"))
    out.extend(b"0000000000 65535 f \n")
    for off in offsets[1:]:
        out.extend(f"{off:010d} 00000 n \n".encode("ascii"))
    out.extend(
        (
            f"trailer << /Size {len(objs) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref_start}\n%%EOF\n"
        ).encode("ascii")
    )
    return bytes(out)


_INVALID_SHEET_CHARS = re.compile(r"[\\/*?:\[\]]")
_INVALID_XML_CHARS = re.compile(r"[\x00-\x08\x0B\x0C\x0E-\x1F]")


def _xlsx_sheet_name(raw: str, idx: int) -> str:
    base = _INVALID_SHEET_CHARS.sub("_", str(raw or "").strip())[:31]
    if not base:
        base = f"Sheet{idx}"
    return base


def _xlsx_col_label(index: int) -> str:
    # 1-indexed column index to Excel letters (1->A, 27->AA).
    out = ""
    n = index
    while n > 0:
        n, rem = divmod(n - 1, 26)
        out = chr(65 + rem) + out
    return out


def _xlsx_cell_ref(col_idx: int, row_idx: int) -> str:
    return f"{_xlsx_col_label(col_idx)}{row_idx}"


def _xlsx_text(value: Any) -> str:
    text = str(value if value is not None else "")
    text = _INVALID_XML_CHARS.sub("", text)
    return xml_escape(text, {'"': "&quot;", "'": "&apos;"})


def _xlsx_sheet_xml(rows: list[list[Any]]) -> bytes:
    body: list[str] = []
    for row_idx, row in enumerate(rows, start=1):
        cells: list[str] = []
        for col_idx, value in enumerate(row, start=1):
            if value is None:
                continue
            ref = _xlsx_cell_ref(col_idx, row_idx)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                cells.append(f'<c r="{ref}"><v>{value}</v></c>')
            else:
                txt = _xlsx_text(value)
                cells.append(
                    f'<c r="{ref}" t="inlineStr"><is><t xml:space="preserve">{txt}</t></is></c>'
                )
        body.append(f'<row r="{row_idx}">{"".join(cells)}</row>')
    xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f'<sheetData>{"".join(body)}</sheetData>'
        "</worksheet>"
    )
    return xml.encode("utf-8")


def _build_fundamental_workbook_sheets(dossier: dict[str, Any]) -> list[tuple[str, list[list[Any]]]]:
    pitch = dossier.get("executive_pitch") or {}
    sections = dossier.get("sections") or {}
    fundamentals = sections.get("technical_valuation_fundamentals") or {}
    raw = fundamentals.get("raw_report") or {}
    tech = raw.get("technical") or {}
    dcf = raw.get("dcf") or {}
    comps = raw.get("comps") or {}
    health = raw.get("health") or {}
    snapshot = (sections.get("finnhub_catalysts_risks") or {}).get("snapshot") or {}
    metrics = snapshot.get("metrics") or {}
    catalysts = list((sections.get("finnhub_catalysts_risks") or {}).get("catalysts") or [])
    risks = list((sections.get("finnhub_catalysts_risks") or {}).get("risks") or [])
    sec_analyze = ((sections.get("sec_narrative") or {}).get("analyze") or {})
    sec_compare = (((sections.get("sec_narrative") or {}).get("compare") or {}).get("compare") or {})

    overview_rows: list[list[Any]] = [
        ["Field", "Value"],
        ["Ticker", dossier.get("ticker") or ""],
        ["Generated At (UTC)", dossier.get("generated_at") or ""],
        ["Recommendation", pitch.get("recommendation") or ""],
        ["Confidence Label", pitch.get("confidence_label") or ""],
        ["Confidence Score", pitch.get("confidence_score") or ""],
        ["Time Horizon", pitch.get("time_horizon") or ""],
    ]

    fundamental_rows: list[list[Any]] = [
        ["Metric", "Value", "Source"],
        ["Revenue Growth TTM YoY (%)", metrics.get("revenue_growth_ttm_yoy"), "Finnhub metrics"],
        ["EPS Growth TTM YoY (%)", metrics.get("eps_growth_ttm_yoy"), "Finnhub metrics"],
        ["Operating Margin TTM (%)", metrics.get("operating_margin_ttm"), "Finnhub metrics"],
        ["Net Margin TTM (%)", metrics.get("net_margin_ttm"), "Finnhub metrics"],
        ["ROE TTM (%)", metrics.get("roe_ttm"), "Finnhub metrics"],
        ["ROA TTM (%)", metrics.get("roa_ttm"), "Finnhub metrics"],
        ["Current Ratio (Q)", metrics.get("current_ratio_quarterly"), "Finnhub metrics"],
        ["Quick Ratio (Q)", metrics.get("quick_ratio_quarterly"), "Finnhub metrics"],
        ["Debt/Equity (Q)", metrics.get("debt_to_equity_quarterly"), "Finnhub metrics"],
        ["Interest Coverage TTM", metrics.get("interest_coverage_ttm"), "Finnhub metrics"],
        ["Dividend Yield TTM (%)", metrics.get("dividend_yield_ttm"), "Finnhub metrics"],
    ]

    valuation_rows: list[list[Any]] = [
        ["Lens", "Value", "Source"],
        ["DCF Intrinsic Value", dcf.get("intrinsic_value"), "report.dcf"],
        ["Current Price", dcf.get("current_price"), "report.dcf"],
        ["Margin of Safety (%)", dcf.get("margin_of_safety"), "report.dcf"],
        ["DCF Growth Rate (%)", dcf.get("growth_rate"), "report.dcf"],
        ["WACC (%)", dcf.get("wacc"), "report.dcf"],
        ["Terminal Growth (%)", dcf.get("terminal_growth"), "report.dcf"],
        ["Median Peer P/E", comps.get("median_pe"), "report.comps"],
        ["Median Peer P/S", comps.get("median_ps"), "report.comps"],
        ["Implied Price (P/E)", comps.get("implied_price_pe"), "report.comps"],
        ["Implied Price (P/S)", comps.get("implied_price_ps"), "report.comps"],
        ["P/E TTM", metrics.get("pe_ttm"), "Finnhub metrics"],
        ["P/B Annual", metrics.get("pb_annual"), "Finnhub metrics"],
        ["P/S TTM", metrics.get("ps_ttm"), "Finnhub metrics"],
        ["EV/EBITDA", metrics.get("ev_to_ebitda"), "Finnhub metrics"],
    ]

    technical_rows: list[list[Any]] = [
        ["Technical Signal", "Value", "Source"],
        ["Signal Score", tech.get("signal_score"), "report.technical"],
        ["Stage 2", bool(tech.get("stage_2")), "report.technical"],
        ["VCP", bool(tech.get("vcp")), "report.technical"],
        ["Current Price", tech.get("current_price"), "report.technical"],
        ["52w High", tech.get("high_52w"), "report.technical"],
        ["52w Low", tech.get("low_52w"), "report.technical"],
        ["SMA 50", tech.get("sma_50"), "report.technical"],
        ["SMA 150", tech.get("sma_150"), "report.technical"],
        ["SMA 200", tech.get("sma_200"), "report.technical"],
        ["Sector ETF", tech.get("sector_etf"), "report.technical"],
    ]

    health_rows: list[list[Any]] = [
        ["Health Signal", "Value", "Source"],
        ["Current Ratio", health.get("current_ratio"), "report.health"],
        ["Debt to Equity", health.get("debt_to_equity"), "report.health"],
        ["Interest Coverage", health.get("interest_coverage"), "report.health"],
        ["ROE", health.get("roe"), "report.health"],
        ["Operating Margin", health.get("operating_margin"), "report.health"],
        ["Flag Count", len(health.get("flags") or []), "report.health"],
    ]
    for idx, flag in enumerate((health.get("flags") or [])[:20], start=1):
        health_rows.append([f"Flag {idx}", flag, "report.health"])

    sec_rows: list[list[Any]] = [
        ["SEC Lens", "Value", "Source"],
        ["Analyze Headline", sec_analyze.get("summary_headline") or sec_analyze.get("error"), "sec_analyze"],
        ["Analyze Narrative", sec_analyze.get("narrative_summary"), "sec_analyze"],
        ["Compare Headline", sec_compare.get("summary_headline"), "sec_compare"],
        ["Compare Narrative", sec_compare.get("narrative_summary"), "sec_compare"],
        ["Compare Confidence", sec_compare.get("compare_confidence"), "sec_compare"],
    ]
    for idx, item in enumerate((sec_compare.get("top_differences") or [])[:10], start=1):
        sec_rows.append([f"Top Difference {idx}", item, "sec_compare"])

    catalyst_risk_rows: list[list[Any]] = [["Type", "Item"]]
    for item in catalysts[:20]:
        catalyst_risk_rows.append(["Catalyst", item])
    for item in risks[:20]:
        catalyst_risk_rows.append(["Risk", item])
    if len(catalyst_risk_rows) == 1:
        catalyst_risk_rows.append(["Info", "No catalyst/risk rows available."])

    return [
        ("Overview", overview_rows),
        ("Fundamentals", fundamental_rows),
        ("Valuation", valuation_rows),
        ("Technical", technical_rows),
        ("Health", health_rows),
        ("SEC Trace", sec_rows),
        ("Catalysts Risks", catalyst_risk_rows),
    ]


def _dossier_to_xlsx(dossier: dict[str, Any]) -> bytes:
    sheets = _build_fundamental_workbook_sheets(dossier)
    if not sheets:
        sheets = [("Overview", [["Field", "Value"], ["Ticker", dossier.get("ticker") or ""]])]

    sheet_xml_blobs: list[bytes] = []
    sheet_names: list[str] = []
    for idx, (raw_name, rows) in enumerate(sheets, start=1):
        sheet_names.append(_xlsx_sheet_name(raw_name, idx))
        sheet_xml_blobs.append(_xlsx_sheet_xml(rows))

    workbook_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        "<sheets>"
        + "".join(
            f'<sheet name="{_xlsx_text(name)}" sheetId="{idx}" r:id="rId{idx}"/>'
            for idx, name in enumerate(sheet_names, start=1)
        )
        + "</sheets></workbook>"
    ).encode("utf-8")

    workbook_rels_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        + "".join(
            f'<Relationship Id="rId{idx}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
            f'Target="worksheets/sheet{idx}.xml"/>'
            for idx in range(1, len(sheet_names) + 1)
        )
        + '<Relationship Id="rIdStyles" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" '
        'Target="styles.xml"/>'
        + "</Relationships>"
    ).encode("utf-8")

    root_rels_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
        'Target="xl/workbook.xml"/>'
        "</Relationships>"
    ).encode("utf-8")

    content_types_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        + "".join(
            f'<Override PartName="/xl/worksheets/sheet{idx}.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            for idx in range(1, len(sheet_names) + 1)
        )
        + '<Override PartName="/xl/styles.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
        "</Types>"
    ).encode("utf-8")

    # Minimal style sheet to keep workbook validators happy.
    styles_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<fonts count="1"><font><sz val="11"/><name val="Calibri"/></font></fonts>'
        '<fills count="1"><fill><patternFill patternType="none"/></fill></fills>'
        '<borders count="1"><border/></borders>'
        '<cellStyleXfs count="1"><xf/></cellStyleXfs>'
        '<cellXfs count="1"><xf xfId="0"/></cellXfs>'
        "</styleSheet>"
    ).encode("utf-8")

    buf = BytesIO()
    with ZipFile(buf, "w", compression=ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", content_types_xml)
        zf.writestr("_rels/.rels", root_rels_xml)
        zf.writestr("xl/workbook.xml", workbook_xml)
        zf.writestr("xl/_rels/workbook.xml.rels", workbook_rels_xml)
        zf.writestr("xl/styles.xml", styles_xml)
        for idx, sheet_blob in enumerate(sheet_xml_blobs, start=1):
            zf.writestr(f"xl/worksheets/sheet{idx}.xml", sheet_blob)
    return buf.getvalue()


@router.get("/api/chart/{ticker}", response_model=ApiResponse)
def chart_data(ticker: str, days: int = 120) -> ApiResponse:
    """OHLCV candle data for Lightweight Charts."""
    try:
        from market_data import get_daily_history_with_meta

        auth = DualSchwabAuth(skill_dir=SKILL_DIR)
        df, meta = get_daily_history_with_meta(
            ticker.upper().strip(),
            days=min(365, max(30, days)),
            auth=auth,
            skill_dir=SKILL_DIR,
        )
        if df is None or df.empty:
            return ApiResponse(
                ok=False,
                error=f"No price data for {ticker}",
                data={
                    "ticker": ticker.upper().strip(),
                    "provider": meta.get("provider"),
                    "used_fallback": meta.get("used_fallback"),
                    "fallback_reason": meta.get("fallback_reason"),
                },
            )

        candles: list[dict[str, Any]] = []
        for _, row in df.iterrows():
            ts = row.get("datetime") or row.get("date") or row.name
            try:
                if hasattr(ts, "timestamp"):
                    epoch = int(ts.timestamp())
                else:
                    from datetime import datetime as _dt
                    epoch = int(_dt.fromisoformat(str(ts)).timestamp())
            except Exception:
                continue
            candles.append({
                "time": epoch,
                "open": round(float(row.get("open", 0)), 2),
                "high": round(float(row.get("high", 0)), 2),
                "low": round(float(row.get("low", 0)), 2),
                "close": round(float(row.get("close", 0)), 2),
                "volume": int(row.get("volume", 0) or 0),
            })
        candles.sort(key=lambda c: c["time"])
        return _ok({"ticker": ticker.upper().strip(), "candles": candles})
    except Exception as e:
        return _err_response("chart_data", e)


@router.get("/api/check/{ticker}", response_model=ApiResponse)
def check_ticker(ticker: str) -> ApiResponse:
    try:
        data = quick_check(ticker.upper().strip())
        return _ok(data)
    except Exception as e:
        return _err_response("check", e)


@router.get("/api/report/{ticker}", response_model=ApiResponse)
def report_ticker(
    ticker: str,
    section: str | None = None,
    skip_mirofish: bool = False,
    skip_edgar: bool = False,
) -> ApiResponse:
    try:
        section_key = None
        if section:
            section_key = REPORT_SECTION_MAP.get(section.lower().strip())
            if not section_key:
                return ApiResponse(ok=False, error=f"Invalid section '{section}'. Use: tech, dcf, comps, health, edgar, mirofish.")

        report = generate_full_report(
            ticker=ticker.upper().strip(),
            skip_mirofish=skip_mirofish,
            skip_edgar=skip_edgar,
        )
        data = json.loads(report_to_json(report))
        try:
            data["finnhub_snapshot"] = get_finnhub_research_snapshot(ticker.upper().strip(), skill_dir=SKILL_DIR)
        except Exception:  # noqa: BLE001
            data["finnhub_snapshot"] = {"enabled": False, "ok": False, "errors": ["finnhub_snapshot_failed"]}
        portfolio_summary: dict[str, Any] | None = None
        try:
            auth = DualSchwabAuth(skill_dir=SKILL_DIR)
            status_data = get_account_status(auth=auth, skill_dir=SKILL_DIR)
            if isinstance(status_data, dict):
                portfolio_summary = build_portfolio_summary(status_data)
        except Exception:
            portfolio_summary = None
        data["report_v2"] = build_report_v2(data, portfolio_summary=portfolio_summary)
        data["report_trust"] = build_report_trust_payload(data)
        section_verdicts = _build_report_verdicts(data)
        if section_key:
            section_data = data.get(section_key)
            return _ok({
                "ticker": data.get("ticker"),
                "generated_at": data.get("generated_at"),
                "section": section_key,
                "data": section_data,
                "report_v2": data.get("report_v2"),
                "report_trust": data.get("report_trust"),
                "section_verdicts": section_verdicts,
                "section_quick_verdict": section_verdicts.get(section_key, {}),
            })
        data["section_verdicts"] = section_verdicts
        return _ok(data)
    except Exception as e:
        return _err_response("report", e)


@router.get("/api/sec/analyze/{ticker}", response_model=ApiResponse)
def sec_analyze_ticker(ticker: str, form_type: str = "10-K") -> ApiResponse:
    try:
        cfg = _sec_analysis_settings()
        if not cfg["analysis_enabled"]:
            return ApiResponse(ok=False, error="SEC filing analysis is disabled by configuration.")
        out = analyze_latest_filing_for_ticker(
            ticker=ticker.upper().strip(),
            form_type=form_type.upper().strip(),
            user_agent=cfg["user_agent"],
            skill_dir=SKILL_DIR,
            cache_hours=cfg["cache_hours"],
            max_chars=cfg["max_chars"],
            enable_llm=cfg["llm_enabled"],
        )
        if not out.get("ok"):
            return ApiResponse(ok=False, error=str(out.get("error", "SEC analysis failed")))
        return _ok(_normalize_sec_analysis_payload(out))
    except Exception as e:
        return _err_response("sec_analyze", e)


@router.get("/sec/analyze/{ticker}", response_model=ApiResponse)
def sec_analyze_ticker_alias(ticker: str, form_type: str = "10-K") -> ApiResponse:
    return sec_analyze_ticker(ticker=ticker, form_type=form_type)


@router.get("/api/sec/compare", response_model=ApiResponse)
def sec_compare(
    mode: str = "ticker_vs_ticker",
    ticker: str = "",
    ticker_b: str = "",
    form_type: str = "10-K",
    highlight_changes_only: bool = False,
) -> ApiResponse:
    try:
        cfg = _sec_analysis_settings()
        if not cfg["analysis_enabled"]:
            return ApiResponse(ok=False, error="SEC filing analysis is disabled by configuration.")
        if not cfg["compare_enabled"]:
            return ApiResponse(ok=False, error="SEC filing compare is disabled by configuration.")
        safe_mode = mode.strip().lower()
        safe_form = form_type.upper().strip()
        safe_ticker = ticker.upper().strip()
        safe_ticker_b = ticker_b.upper().strip()
        if cfg["max_compare_items"] < 2:
            return ApiResponse(ok=False, error="SEC compare limit is below required minimum.")

        if safe_mode == "ticker_vs_ticker":
            if not safe_ticker or not safe_ticker_b:
                return ApiResponse(ok=False, error="ticker and ticker_b are required for ticker_vs_ticker mode.")
            out = compare_ticker_vs_ticker(
                safe_ticker, safe_ticker_b,
                form_type=safe_form, user_agent=cfg["user_agent"],
                skill_dir=SKILL_DIR, cache_hours=cfg["cache_hours"],
                max_chars=cfg["max_chars"], enable_llm=cfg["llm_enabled"],
                highlight_changes_only=bool(highlight_changes_only),
            )
        elif safe_mode == "ticker_over_time":
            if not safe_ticker:
                return ApiResponse(ok=False, error="ticker is required for ticker_over_time mode.")
            out = compare_ticker_over_time(
                safe_ticker,
                form_type=safe_form, user_agent=cfg["user_agent"],
                skill_dir=SKILL_DIR, cache_hours=cfg["cache_hours"],
                max_chars=cfg["max_chars"], enable_llm=cfg["llm_enabled"],
                highlight_changes_only=bool(highlight_changes_only),
            )
        else:
            return ApiResponse(ok=False, error="Invalid mode. Use ticker_vs_ticker or ticker_over_time.")

        if not out.get("ok"):
            return ApiResponse(ok=False, error=str(out.get("error", "SEC compare failed")))
        return _ok(_normalize_sec_compare_payload(out))
    except Exception as e:
        return _err_response("sec_compare", e)


@router.get("/sec/compare", response_model=ApiResponse)
def sec_compare_alias(
    mode: str = "ticker_vs_ticker",
    ticker: str = "",
    ticker_b: str = "",
    form_type: str = "10-K",
    highlight_changes_only: bool = False,
) -> ApiResponse:
    return sec_compare(
        mode=mode, ticker=ticker, ticker_b=ticker_b,
        form_type=form_type, highlight_changes_only=highlight_changes_only,
    )


@router.get("/api/sec/management-dashboard", response_model=ApiResponse)
def sec_management_dashboard(
    mode: str = "ticker_over_time",
    ticker: str = "",
    ticker_b: str = "",
    form_type: str = "10-K",
    highlight_changes_only: bool = True,
    ruthless_mode: bool = False,
    profile_override: str = "",
) -> ApiResponse:
    try:
        cfg = _sec_analysis_settings()
        if not cfg["analysis_enabled"]:
            return ApiResponse(ok=False, error="SEC filing analysis is disabled by configuration.")
        if not cfg["compare_enabled"]:
            return ApiResponse(ok=False, error="SEC filing compare is disabled by configuration.")
        safe_mode = mode.strip().lower()
        safe_form = form_type.upper().strip()
        safe_ticker = ticker.upper().strip()
        safe_ticker_b = ticker_b.upper().strip()
        persisted_override = _LOCAL_SEC_MGMT_PROFILE_OVERRIDE or ""
        last_override = _LOCAL_SEC_MGMT_OVERRIDE_HISTORY[-1] if _LOCAL_SEC_MGMT_OVERRIDE_HISTORY else None
        safe_override = profile_override.strip().lower() or persisted_override
        if safe_override and safe_override not in PROFILE_WEIGHTS:
            supported = ", ".join(sorted(PROFILE_WEIGHTS.keys()))
            return ApiResponse(ok=False, error=f"Invalid profile_override '{profile_override}'. Use one of: {supported}.")
        if safe_mode not in {"ticker_vs_ticker", "ticker_over_time"}:
            return ApiResponse(ok=False, error="Invalid mode. Use ticker_vs_ticker or ticker_over_time.")
        if not safe_ticker:
            return ApiResponse(ok=False, error="ticker is required.")
        if safe_mode == "ticker_vs_ticker" and not safe_ticker_b:
            return ApiResponse(ok=False, error="ticker_b is required for ticker_vs_ticker mode.")

        if safe_mode == "ticker_vs_ticker":
            compare_out = compare_ticker_vs_ticker(
                safe_ticker,
                safe_ticker_b,
                form_type=safe_form,
                user_agent=cfg["user_agent"],
                skill_dir=SKILL_DIR,
                cache_hours=cfg["cache_hours"],
                max_chars=cfg["max_chars"],
                enable_llm=cfg["llm_enabled"],
                highlight_changes_only=bool(highlight_changes_only),
            )
        else:
            compare_out = compare_ticker_over_time(
                safe_ticker,
                form_type=safe_form,
                user_agent=cfg["user_agent"],
                skill_dir=SKILL_DIR,
                cache_hours=cfg["cache_hours"],
                max_chars=cfg["max_chars"],
                enable_llm=cfg["llm_enabled"],
                highlight_changes_only=bool(highlight_changes_only),
            )
        if not compare_out.get("ok"):
            return ApiResponse(ok=False, error=str(compare_out.get("error", "SEC compare failed")))
        compare_payload = _normalize_sec_compare_payload(compare_out)
        dashboard = build_management_dashboard(
            compare_payload=compare_payload,
            mode=safe_mode,
            ticker=safe_ticker,
            ticker_b=safe_ticker_b,
            form_type=safe_form,
            ruthless_mode=bool(ruthless_mode),
            profile_override=safe_override or None,
        )
        dashboard["profile"]["persisted_override"] = persisted_override or None
        dashboard["profile"]["last_override"] = last_override
        dashboard["profile"]["history_tail"] = _LOCAL_SEC_MGMT_OVERRIDE_HISTORY[-10:]
        return _ok({"compare": compare_payload.get("compare", {}), "management_dashboard": dashboard})
    except Exception as e:
        return _err_response("sec_management_dashboard", e)


@router.post("/api/sec/management-dashboard/profile", response_model=ApiResponse)
def set_sec_management_profile_override(payload: dict[str, Any]) -> ApiResponse:
    global _LOCAL_SEC_MGMT_PROFILE_OVERRIDE
    global _LOCAL_SEC_MGMT_OVERRIDE_HISTORY
    raw = str((payload or {}).get("profile_override") or "").strip().lower()
    reason = str((payload or {}).get("reason") or "").strip()
    evidence_ref = str((payload or {}).get("evidence_ref") or "").strip()
    if raw and raw not in PROFILE_WEIGHTS:
        supported = ", ".join(sorted(PROFILE_WEIGHTS.keys()))
        return ApiResponse(ok=False, error=f"Invalid profile_override '{raw}'. Use one of: {supported}.")
    before = _LOCAL_SEC_MGMT_PROFILE_OVERRIDE
    _LOCAL_SEC_MGMT_PROFILE_OVERRIDE = raw or None
    change = {
        "at": datetime.now(UTC).isoformat(),
        "actor": "local_dashboard",
        "before": before,
        "after": _LOCAL_SEC_MGMT_PROFILE_OVERRIDE,
        "reason": reason or "unspecified",
        "evidence_ref": evidence_ref or None,
    }
    _LOCAL_SEC_MGMT_OVERRIDE_HISTORY.append(change)
    if len(_LOCAL_SEC_MGMT_OVERRIDE_HISTORY) > 50:
        _LOCAL_SEC_MGMT_OVERRIDE_HISTORY = _LOCAL_SEC_MGMT_OVERRIDE_HISTORY[-50:]
    return _ok(
        {
            "profile_override": _LOCAL_SEC_MGMT_PROFILE_OVERRIDE,
            "supported_profiles": sorted(PROFILE_WEIGHTS.keys()),
            "last_override": change,
            "history_tail": _LOCAL_SEC_MGMT_OVERRIDE_HISTORY[-10:],
        }
    )


@router.get("/api/research/dossier/{ticker}", response_model=ApiResponse)
def research_dossier(ticker: str) -> ApiResponse:
    try:
        return _ok(_compose_research_dossier(ticker))
    except Exception as e:
        return _err_response("research_dossier", e)


@router.get("/api/research/dossier/{ticker}/export")
def research_dossier_export(
    ticker: str,
    format: str = Query(default="json", pattern="^(json|md|pdf|xlsx)$"),
) -> Response:
    try:
        dossier = _compose_research_dossier(ticker)
        symbol = str(dossier.get("ticker") or ticker.upper().strip())
        safe_symbol = "".join(ch for ch in symbol if ch.isalnum() or ch in ("-", "_")) or "TICKER"
        if format == "json":
            body = json.dumps(dossier, indent=2, sort_keys=True).encode("utf-8")
            filename = f"{safe_symbol.lower()}_research_dossier.json"
            media_type = "application/json"
        elif format == "md":
            body = _dossier_to_markdown(dossier).encode("utf-8")
            filename = f"{safe_symbol.lower()}_research_dossier.md"
            media_type = "text/markdown; charset=utf-8"
        elif format == "xlsx":
            body = _dossier_to_xlsx(dossier)
            filename = f"{safe_symbol.lower()}_fundamental_workbook.xlsx"
            media_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        else:
            try:
                body = dossier_to_pdf(dossier)
            except Exception:
                # Defensive: if the rich renderer fails for any reason, fall
                # back to the legacy text-based PDF so callers still get a file.
                body = _text_to_simple_pdf(_dossier_to_markdown(dossier))
            filename = f"{safe_symbol.lower()}_research_dossier.pdf"
            media_type = "application/pdf"
        headers = {"Content-Disposition": f'attachment; filename="{filename}"'}
        return Response(content=body, media_type=media_type, headers=headers)
    except Exception as exc:
        error = _err_response("research_dossier_export", exc)
        return Response(content=json.dumps(error.model_dump(), indent=2), media_type="application/json", status_code=500)


@router.get("/api/research/dossier/{ticker}/fundamental-workbook")
def research_fundamental_workbook_export(ticker: str) -> Response:
    try:
        dossier = _compose_research_dossier(ticker)
        symbol = str(dossier.get("ticker") or ticker.upper().strip())
        safe_symbol = "".join(ch for ch in symbol if ch.isalnum() or ch in ("-", "_")) or "TICKER"
        body = _dossier_to_xlsx(dossier)
        filename = f"{safe_symbol.lower()}_fundamental_model_workbook.xlsx"
        headers = {"Content-Disposition": f'attachment; filename="{filename}"'}
        return Response(
            content=body,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers=headers,
        )
    except Exception as exc:
        error = _err_response("research_fundamental_workbook_export", exc)
        return Response(content=json.dumps(error.model_dump(), indent=2), media_type="application/json", status_code=500)
