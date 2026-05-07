"""Research routes: SEC analysis, full reports, chart data, decision card."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

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
from ..report_v2 import build_report_v2
from ..schemas import ApiResponse

router = APIRouter(tags=["research"])

SKILL_DIR = Path(__file__).resolve().parent.parent.parent


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


def _pick_catalysts_and_risks(finnhub: dict[str, Any]) -> dict[str, list[str]]:
    news_rows = finnhub.get("news") if isinstance(finnhub, dict) else []
    earnings_rows = finnhub.get("earnings") if isinstance(finnhub, dict) else []
    trends = finnhub.get("recommendation_trends") if isinstance(finnhub, dict) else {}
    catalysts: list[str] = []
    risks: list[str] = []

    if isinstance(trends, dict):
        buy = int(trends.get("buy", 0) or 0) + int(trends.get("strong_buy", 0) or 0)
        sell = int(trends.get("sell", 0) or 0) + int(trends.get("strong_sell", 0) or 0)
        if buy > sell:
            catalysts.append(f"Analyst trend skew is constructive ({buy} buy vs {sell} sell votes).")
        elif sell > buy:
            risks.append(f"Analyst trend skew is cautious ({sell} sell vs {buy} buy votes).")

    if isinstance(earnings_rows, list):
        for row in earnings_rows[:3]:
            if not isinstance(row, dict):
                continue
            surprise_pct = _safe_float(row.get("surprise_percent"))
            period = str(row.get("period") or "").strip()
            if surprise_pct is None:
                continue
            if surprise_pct >= 5:
                catalysts.append(f"Earnings surprise +{surprise_pct:.1f}% ({period or 'recent'}).")
            elif surprise_pct <= -5:
                risks.append(f"Earnings miss {surprise_pct:.1f}% ({period or 'recent'}).")

    if isinstance(news_rows, list):
        for row in news_rows[:5]:
            if not isinstance(row, dict):
                continue
            headline = str(row.get("headline") or "").strip()
            if not headline:
                continue
            low = headline.lower()
            if any(tok in low for tok in ("upgrade", "contract", "beat", "launch", "partnership")):
                catalysts.append(headline)
            if any(tok in low for tok in ("downgrade", "investigation", "lawsuit", "miss", "delay", "cut")):
                risks.append(headline)

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
    confidence_score = max(0.0, min(100.0, (signal_score * 0.7) + max(-20.0, min(20.0, margin_of_safety))))
    catalyst_risk = _pick_catalysts_and_risks(finnhub if isinstance(finnhub, dict) else {})

    dossier = {
        "ticker": symbol,
        "generated_at": generated_at,
        "executive_pitch": {
            "thesis": str((report_v2.get("thesis") or {}).get("claim") or f"{symbol} setup requires review of report stack and SEC context."),
            "recommendation": str((report_v2.get("ic_snapshot") or {}).get("recommendation") or "WATCH"),
            "confidence_label": str((report_v2.get("ic_snapshot") or {}).get("confidence_label") or "Moderate"),
            "confidence_score": round(confidence_score, 1),
            "time_horizon": str((report_v2.get("ic_snapshot") or {}).get("time_horizon") or "3-6 months"),
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
    health = raw_report.get("health") or {}
    sec_analyze = sec_narr.get("analyze") or {}
    sec_compare = ((sec_narr.get("compare") or {}).get("compare") or {})
    quote = (fin.get("snapshot") or {}).get("quote") or {}
    pt = (fin.get("snapshot") or {}).get("price_target") or {}
    trends = (fin.get("snapshot") or {}).get("recommendation_trends") or {}
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

    snapshot = fin.get("snapshot") or {}
    profile = snapshot.get("profile") or {}
    metrics = snapshot.get("metrics") or {}
    earnings = list(snapshot.get("earnings") or [])
    news = list(snapshot.get("news") or [])
    sec_summary = str(sec_analyze.get("narrative_summary") or sec_analyze.get("error") or "SEC analysis unavailable.")
    compare_summary = str(sec_compare.get("narrative_summary") or (sec_narr.get("compare") or {}).get("error") or "SEC compare unavailable.")
    hhi_label = (((portfolio.get("portfolio_risk") or {}).get("concentration") or {}).get("hhi_label") or "Unavailable")
    positions_count = (portfolio.get("portfolio_summary") or {}).get("positions_count", "n/a")
    total_mv = (portfolio.get("portfolio_summary") or {}).get("total_market_value", "n/a")
    recommendation = pitch.get("recommendation", "WATCH")
    confidence_label = pitch.get("confidence_label", "Moderate")
    confidence_score = pitch.get("confidence_score", "n/a")
    bull_votes = int(trends.get("buy", 0) or 0) + int(trends.get("strong_buy", 0) or 0)
    bear_votes = int(trends.get("sell", 0) or 0) + int(trends.get("strong_sell", 0) or 0)

    def _money(value: Any) -> str:
        v = _safe_float(value)
        if v is None:
            return "n/a"
        if abs(v) >= 1000:
            return f"${v/1000:.2f}B"
        return f"${v:.2f}M"

    def _ratio_pct(value: Any, digits: int = 1) -> str:
        v = _safe_float(value)
        if v is None:
            return "n/a"
        if abs(v) <= 1:
            v *= 100
        return f"{v:.{digits}f}%"

    def _cite(*names: str) -> str:
        ids = [source_index.get(name) for name in names if source_index.get(name)]
        if not ids:
            return ""
        return " " + "".join(f"[{idx}]" for idx in sorted(set(ids)))

    lines: list[str] = [
        f"# {ticker} — Institutional Research Report",
        "",
        "## Cover Page",
        "",
        f"Prepared: {generated_at} | Analyst: TradingBot Research Engine",
        f"Coverage: {profile.get('finnhub_industry') or 'Equity Research'} | Region: {profile.get('country') or 'Global'}",
        f"Document Type: Institutional Investment Note{_cite('report_stack', 'finnhub', 'sec_analyze')}",
        "",
        "---",
        "",
        f"Current Price: ${_num(quote.get('current'))} | 52-Week Range: ${_num(metrics.get('52week_low'))}–${_num(metrics.get('52week_high'))} | Consensus Target (Finnhub Mean): ${_num(pt.get('mean'))}",
        "",
        f"Recommendation: **{recommendation}** | Confidence: **{confidence_label} ({confidence_score}/100)** | Horizon: **{pitch.get('time_horizon', '3-6 months')}**",
        "",
        "Business Strategy & Operations · Fundamental Performance · Valuation · Risk & Catalyst Analysis",
        "",
        "## Executive Investment Summary",
        "",
        str(pitch.get("thesis") or "No thesis generated."),
        "",
        f"{ticker} currently screens with technical score {_num(technical.get('signal_score'), 0)}/100 and DCF margin of safety {_pct(dcf.get('margin_of_safety'))}. "
        f"Street positioning from Finnhub reads {bull_votes} bullish vs {bear_votes} bearish recommendation votes, while portfolio concentration context is **{hhi_label}**.{_cite('finnhub', 'report_stack', 'portfolio')}",
        "",
        "## Part I: Company and Business Model",
        "",
        f"- Issuer: {profile.get('name') or ticker} | Industry: {profile.get('finnhub_industry') or 'n/a'} | Exchange: {profile.get('exchange') or 'n/a'}",
        f"- Geography/Currency: {profile.get('country') or 'n/a'} / {profile.get('currency') or 'n/a'}",
        f"- Market Cap (Finnhub): {_money(profile.get('market_cap'))} | IPO: {profile.get('ipo') or 'n/a'}{_cite('finnhub')}",
        f"- Core thesis context: {(report_v2.get('thesis') or {}).get('claim') or 'Derived from integrated report stack.'}{_cite('report_stack')}",
        "",
        "## Part II: Fundamental Performance Analysis",
        "",
        "| Fundamental Metric | Value | Commentary |",
        "|---|---:|---|",
        f"| Revenue Growth (TTM YoY) | {_ratio_pct(metrics.get('revenue_growth_ttm_yoy'))} | Growth momentum from Finnhub metrics feed |",
        f"| EPS Growth (TTM YoY) | {_ratio_pct(metrics.get('eps_growth_ttm_yoy'))} | Earnings trajectory check |",
        f"| Operating Margin (TTM) | {_ratio_pct(metrics.get('operating_margin_ttm'))} | Operating efficiency trend |",
        f"| Net Margin (TTM) | {_ratio_pct(metrics.get('net_margin_ttm'))} | Bottom-line profitability quality |",
        f"| ROE / ROA (TTM) | {_ratio_pct(metrics.get('roe_ttm'))} / {_ratio_pct(metrics.get('roa_ttm'))} | Capital efficiency read-through |",
        f"| Current Ratio / Debt-Equity | {_num(metrics.get('current_ratio_quarterly'))} / {_num(metrics.get('debt_to_equity_quarterly'))} | Liquidity and leverage posture |",
        "",
        "### Earnings Quality (Recent Prints)",
        "",
            f"Earnings dispersion and surprise cadence remain central to near-term rerating potential and should be read with valuation compression/expansion risk in mind.{_cite('finnhub')}",
            "",
        "| Period | Actual EPS | Estimate EPS | Surprise % |",
        "|---|---:|---:|---:|",
    ]

    if earnings:
        for row in earnings[:6]:
            lines.append(
                f"| {row.get('period') or 'n/a'} | {_num(row.get('actual'))} | {_num(row.get('estimate'))} | {_pct(row.get('surprise_percent'))} |"
            )
    else:
        lines.append("| n/a | n/a | n/a | n/a |")

    lines.extend(
        [
            "",
            "## Part III: Valuation and Technical Positioning",
            "",
            "| Valuation / Technical | Value |",
            "|---|---:|",
            f"| DCF Margin of Safety | {_pct(dcf.get('margin_of_safety'))} |",
            f"| P/E (TTM) | {_num(metrics.get('pe_ttm'))} |",
            f"| P/B (Annual) | {_num(metrics.get('pb_annual'))} |",
            f"| P/S (TTM) | {_num(metrics.get('ps_ttm'))} |",
            f"| EV / EBITDA | {_num(metrics.get('ev_to_ebitda'))} |",
            f"| EV / Sales | {_num(metrics.get('ev_to_sales'))} |",
            f"| Technical Signal Score | {_num(technical.get('signal_score'), 0)} |",
            f"| Stage 2 / VCP | {bool(technical.get('stage_2'))} / {bool(technical.get('vcp'))} |",
            "",
            f"Technical structure implies {'constructive trend continuation' if technical.get('stage_2') else 'non-trending or transitional tape'} with sector monitor {technical.get('sector_etf') or 'n/a'}. "
            f"From a valuation perspective, margin-of-safety and multiple profile should be read together with SEC and catalyst evidence, not in isolation.{_cite('report_stack', 'finnhub', 'sec_analyze')}",
            "",
            "## Part IV: SEC Narrative and Comparative Filing Deltas",
            "",
            f"- Analyze Headline: {sec_analyze.get('summary_headline') or sec_analyze.get('error') or 'Unavailable'}{_cite('sec_analyze')}",
            f"- Analyze Narrative: {sec_summary}{_cite('sec_analyze')}",
            f"- Compare Headline: {sec_compare.get('summary_headline') or (sec_narr.get('compare') or {}).get('error') or 'Unavailable'}{_cite('sec_compare')}",
            f"- Compare Narrative: {compare_summary}{_cite('sec_compare')}",
            "",
            "## Part V: Portfolio Fit and Risk Budget Context",
            "",
            f"- Open positions: {positions_count}",
            f"- Total market value: {total_mv}",
            f"- Concentration label: {hhi_label}",
            f"- Risk budget impact: {(report_v2.get('portfolio_fit') or {}).get('risk_budget_impact') or 'Unavailable'}{_cite('portfolio', 'report_stack')}",
            "",
            "## Part VI: Catalyst and Risk Matrix",
            "",
            "| Type | Item |",
            "|---|---|",
        ]
    )

    if catalysts:
        lines.extend([f"| Catalyst | {item} |" for item in catalysts[:8]])
    else:
        lines.append("| Catalyst | No clear catalysts extracted from available feeds. |")

    if risks:
        lines.extend([f"| Risk | {item} |" for item in risks[:8]])
    else:
        lines.append("| Risk | No clear risks extracted from available feeds. |")

    lines.extend(["", "### Newsflow Digest (Finnhub)", ""])
    if news:
        for item in news[:8]:
            headline = str(item.get("headline") or "").strip()
            summary = str(item.get("summary") or "").strip()
            source = str(item.get("source") or "").strip()
            if headline:
                lines.append(f"- {headline} ({source or 'source n/a'})")
                if summary:
                    lines.append(f"  - {summary[:220]}")
    else:
        lines.append("- No recent Finnhub news items were returned.")

    lines.extend(["", "## Key Metrics at a Glance", ""])
    lines.extend(
        [
            "| Metric | Value | Source |",
            "|---|---:|---|",
            f"| Current Price | ${_num(quote.get('current'))} | Finnhub quote |",
            f"| 52-Week High | ${_num(metrics.get('52week_high'))} | Finnhub metrics |",
            f"| 52-Week Low | ${_num(metrics.get('52week_low'))} | Finnhub metrics |",
            f"| DCF Margin of Safety | {_pct(dcf.get('margin_of_safety'))} | Full report DCF |",
            f"| Health Flag Count | {len(health.get('flags') or [])} | Full report health |",
            f"| Portfolio Concentration | {hhi_label} | Portfolio risk analytics |",
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
    lines.extend(
        [
            "",
            "## Disclaimer",
            "",
            "This report is generated for informational research workflows. It is not investment advice.",
        ]
    )
    lines.append("")
    return "\n".join(lines)


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
        section_verdicts = _build_report_verdicts(data)
        if section_key:
            section_data = data.get(section_key)
            return _ok({
                "ticker": data.get("ticker"),
                "generated_at": data.get("generated_at"),
                "section": section_key,
                "data": section_data,
                "report_v2": data.get("report_v2"),
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


@router.get("/api/research/dossier/{ticker}", response_model=ApiResponse)
def research_dossier(ticker: str) -> ApiResponse:
    try:
        return _ok(_compose_research_dossier(ticker))
    except Exception as e:
        return _err_response("research_dossier", e)


@router.get("/api/research/dossier/{ticker}/export")
def research_dossier_export(
    ticker: str,
    format: str = Query(default="json", pattern="^(json|md|pdf)$"),
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
        else:
            markdown = _dossier_to_markdown(dossier)
            body = _text_to_simple_pdf(markdown)
            filename = f"{safe_symbol.lower()}_research_dossier.pdf"
            media_type = "application/pdf"
        headers = {"Content-Disposition": f'attachment; filename="{filename}"'}
        return Response(content=body, media_type=media_type, headers=headers)
    except Exception as exc:
        error = _err_response("research_dossier_export", exc)
        return Response(content=json.dumps(error.model_dump(), indent=2), media_type="application/json", status_code=500)
