from __future__ import annotations

import json
from unittest.mock import MagicMock

from webapp.routes import research


def _stub_report_payload() -> dict:
    return {
        "ticker": "AAPL",
        "generated_at": "2026-05-07T10:00:00+00:00",
        "technical": {
            "signal_score": 71.0,
            "stage_2": True,
            "vcp": True,
            "sector_etf": "XLK",
        },
        "dcf": {"margin_of_safety": 12.0},
        "health": {"flags": []},
        "mirofish": {"conviction_score": 35.0, "summary": "Strong institutional accumulation."},
    }


def _patch_shared_dependencies(monkeypatch, *, finnhub_ok: bool) -> None:
    payload = _stub_report_payload()
    monkeypatch.setattr(research, "generate_full_report", lambda *args, **kwargs: {"stub": True})
    monkeypatch.setattr(research, "report_to_json", lambda report: json.dumps(payload))
    monkeypatch.setattr(
        research,
        "build_report_v2",
        lambda report_data, portfolio_summary=None: {
            "thesis": {"claim": "Upside thesis from blended technical + valuation alignment."},
            "ic_snapshot": {
                "recommendation": "long",
                "confidence_score": 78.0,
                "time_horizon": "3-6 months",
                "invalidation": ["Stage 2 break"],
            },
            "portfolio_fit": {"risk_budget_impact": "low", "sector_overlap_pct": 0.05, "correlation_proxy": 0.12},
            "monitoring_plan": {
                "weekly_checks": ["Recompute trend"],
                "monthly_checks": ["Re-underwrite valuation"],
                "kill_switches": ["Thesis invalidation"],
            },
        },
    )
    monkeypatch.setattr(
        research,
        "analyze_latest_filing_for_ticker",
        lambda **kwargs: {
            "ok": True,
            "ticker": "AAPL",
            "form": "10-K",
            "verdict": "bullish",
            "confidence": 78,
            "why": ["Margin expansion trends remain intact."],
            "summary_headline": "Latest filing supports the growth thesis.",
            "narrative_summary": "Management commentary and disclosures align with continued demand growth.",
        },
    )
    monkeypatch.setattr(
        research,
        "compare_ticker_over_time",
        lambda *args, **kwargs: {
            "ok": True,
            "compare": {
                "summary_headline": "Disclosure posture is improving over time.",
                "narrative_summary": "Risk-factor language and guidance quality improved versus prior filing.",
                "similarities": ["Core demand narrative is consistent."],
                "differences": ["Capex guidance is more disciplined."],
            },
            "left": {"from_cache": False, "source": "sec"},
            "right": {"from_cache": True, "source": "sec"},
        },
    )
    monkeypatch.setattr(research, "get_account_status", lambda **kwargs: {"accounts": []})
    monkeypatch.setattr(
        research,
        "build_portfolio_summary",
        lambda status: {"positions_count": 2, "total_market_value": 100000, "positions": []},
    )
    monkeypatch.setattr(
        research,
        "build_portfolio_risk_analytics",
        lambda summary, skill_dir: {"concentration": {"hhi_label": "Moderate"}, "position_count": 2},
    )
    monkeypatch.setattr(research, "get_sector_heatmap", lambda **kwargs: {"rows": [{"sector": "Technology", "rel_strength": 1.2}]})
    monkeypatch.setattr(
        research,
        "get_finnhub_research_snapshot",
        lambda *args, **kwargs: {
            "enabled": True,
            "ok": finnhub_ok,
            "as_of": "2026-05-07T10:00:00+00:00",
            "errors": [] if finnhub_ok else ["company_news:rate_limited"],
            "recommendation_trends": {
                "buy": 12, "strong_buy": 3, "sell": 1, "strong_sell": 0, "hold": 4,
                "history": [
                    {"period": "2026-04-01", "strong_buy": 3, "buy": 12, "hold": 4, "sell": 1, "strong_sell": 0},
                    {"period": "2026-03-01", "strong_buy": 2, "buy": 11, "hold": 5, "sell": 2, "strong_sell": 0},
                ],
            },
            "news": [
                {"headline": "Analyst upgrade after product launch", "source": "Reuters",
                 "datetime": "2026-05-01T14:00:00+00:00"},
                {"headline": "Lawsuit filed by former vendor", "source": "Bloomberg"},
            ],
            "earnings": [{"period": "2026-Q1", "actual": 1.5, "estimate": 1.4, "surprise_percent": 8.2}],
            "earnings_calendar": [
                {"symbol": "AAPL", "date": "2026-07-31", "year": 2026, "quarter": 3, "eps_estimate": 1.55,
                 "revenue_estimate": 95_000_000_000},
            ],
            "price_target": {"mean": 220.0, "high": 250, "low": 180, "median": 218, "number_of_analysts": 32},
            "quote": {"current": 192.4, "previous_close": 190.0, "change": 2.4, "change_percent": 1.26},
            "profile": {
                "name": "Apple Inc.", "finnhub_industry": "Hardware", "exchange": "NASDAQ",
                "country": "US", "currency": "USD", "market_cap": 3_200_000.0, "share_outstanding": 16_000.0,
                "ipo": "1980-12-12",
            },
            "metrics": {
                "pe_ttm": 28.0, "pb_annual": 35.0, "ps_ttm": 7.5, "ev_to_ebitda": 22.0,
                "revenue_growth_ttm_yoy": 8.2, "operating_margin_ttm": 31.0, "net_margin_ttm": 24.0,
                "roe_ttm": 145.0, "roa_ttm": 28.0, "current_ratio_quarterly": 1.2, "quick_ratio_quarterly": 1.0,
                "debt_to_equity_quarterly": 1.55, "interest_coverage_ttm": 30.0, "dividend_yield_ttm": 0.5,
                "52week_high": 220.0, "52week_low": 165.0, "beta": 1.2,
            },
            "peers": ["MSFT", "GOOG", "META"],
            "insider_transactions": {
                "rows": [
                    {"name": "Tim Cook", "share": -10000, "transaction_price": 195.0,
                     "transaction_date": "2026-04-12", "transaction_code": "S"},
                    {"name": "Insider B", "share": 5000, "transaction_price": 188.0,
                     "transaction_date": "2026-03-30", "transaction_code": "P"},
                ],
                "net_shares_180d": -5000,
                "net_dollars_180d": -950_000.0,
                "buy_count_180d": 1,
                "sell_count_180d": 1,
            },
            "insider_sentiment": {
                "rows": [{"year": 2026, "month": 4, "mspr": 12.5, "change": 3000}],
                "net_mspr_6m": 15.0,
                "net_change_6m": 4500,
            },
            "upgrade_downgrade": [
                {"symbol": "AAPL", "company": "Morgan Stanley", "from_grade": "Equal-Weight",
                 "to_grade": "Overweight", "action": "up", "grade_time": "2026-04-15T13:00:00+00:00"},
            ],
            "sec_filings": [
                {"form": "10-Q", "filed_date": "2026-04-30", "accepted_date": "2026-04-30",
                 "report_url": "https://example.com/10q", "filing_url": "https://example.com/idx"},
            ],
            "dividends": [
                {"amount": 0.24, "ex_date": "2026-05-10", "pay_date": "2026-05-15",
                 "currency": "USD", "frequency": 4},
            ],
            "splits": [],
            "news_sentiment": {
                "buzz_articles_in_last_week": 18,
                "buzz_weekly_avg": 12.5,
                "company_news_score": 0.82,
                "sector_avg_news_score": 0.66,
                "bullish_percent": 0.55,
                "bearish_percent": 0.18,
            },
        },
    )


def test_compose_dossier_merges_required_sections(monkeypatch) -> None:
    _patch_shared_dependencies(monkeypatch, finnhub_ok=True)
    dossier = research._compose_research_dossier("AAPL")
    assert dossier["ticker"] == "AAPL"
    assert "executive_pitch" in dossier
    assert "technical_valuation_fundamentals" in dossier["sections"]
    assert "sec_narrative" in dossier["sections"]
    assert "portfolio_and_sector_context" in dossier["sections"]
    assert "finnhub_catalysts_risks" in dossier["sections"]
    assert any(row["name"] == "report_stack" and row["status"] == "ok" for row in dossier["source_metadata"])
    assert any(row["name"] == "finnhub" and row["status"] == "ok" for row in dossier["source_metadata"])


def test_compose_dossier_records_finnhub_fallback_notes(monkeypatch) -> None:
    _patch_shared_dependencies(monkeypatch, finnhub_ok=False)
    dossier = research._compose_research_dossier("AAPL")
    assert any(row["name"] == "finnhub" and row["status"] == "degraded" for row in dossier["source_metadata"])
    assert any("company_news:rate_limited" in note for note in dossier["fallback_notes"])


def test_export_endpoint_sets_content_disposition(monkeypatch) -> None:
    monkeypatch.setattr(
        research,
        "_compose_research_dossier",
        lambda ticker: {
            "ticker": "AAPL",
            "generated_at": "2026-05-07T10:00:00+00:00",
            "executive_pitch": {"recommendation": "BUY", "confidence_label": "High", "confidence_score": 84, "time_horizon": "3-6 months", "thesis": "Stub thesis"},
            "sections": {"sec_narrative": {}, "portfolio_and_sector_context": {}, "finnhub_catalysts_risks": {}},
            "source_metadata": [],
            "fallback_notes": [],
        },
    )

    json_resp = research.research_dossier_export("AAPL", format="json")
    assert json_resp.status_code == 200
    assert json_resp.media_type == "application/json"
    assert "attachment; filename=\"aapl_research_dossier.json\"" == json_resp.headers.get("content-disposition")

    md_resp = research.research_dossier_export("AAPL", format="md")
    assert md_resp.status_code == 200
    assert md_resp.media_type == "text/markdown; charset=utf-8"
    assert "attachment; filename=\"aapl_research_dossier.md\"" == md_resp.headers.get("content-disposition")

    pdf_resp = research.research_dossier_export("AAPL", format="pdf")
    assert pdf_resp.status_code == 200
    assert pdf_resp.media_type == "application/pdf"
    assert "attachment; filename=\"aapl_research_dossier.pdf\"" == pdf_resp.headers.get("content-disposition")
    assert bytes(pdf_resp.body).startswith(b"%PDF-1.4")

    xlsx_resp = research.research_dossier_export("AAPL", format="xlsx")
    assert xlsx_resp.status_code == 200
    assert xlsx_resp.media_type == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    assert "attachment; filename=\"aapl_fundamental_workbook.xlsx\"" == xlsx_resp.headers.get("content-disposition")
    # XLSX container should start with ZIP magic bytes.
    assert bytes(xlsx_resp.body).startswith(b"PK\x03\x04")

    model_wb_resp = research.research_fundamental_workbook_export("AAPL")
    assert model_wb_resp.status_code == 200
    assert model_wb_resp.media_type == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    assert "attachment; filename=\"aapl_fundamental_model_workbook.xlsx\"" == model_wb_resp.headers.get("content-disposition")
    assert bytes(model_wb_resp.body).startswith(b"PK\x03\x04")


def test_dossier_markdown_includes_institutional_sections(monkeypatch) -> None:
    """The exported Markdown must follow institutional section flow."""

    _patch_shared_dependencies(monkeypatch, finnhub_ok=True)
    dossier = research._compose_research_dossier("AAPL")
    md = research._dossier_to_markdown(dossier)

    expected_sections_in_order = [
        "## Cover Page",
        "## Executive Investment Summary",
        "## Part I: Company and Business Model",
        "## Part II: Fundamental Performance Analysis",
        "## Part III: Valuation and Technical Positioning",
        "## Part IV: SEC Narrative and Comparative Filing Deltas",
        "## Part V: Portfolio Fit and Risk Budget Context",
        "## Part VI: Catalyst and Risk Matrix",
        "## References",
        "## Disclaimer",
    ]
    last_idx = -1
    for header in expected_sections_in_order:
        idx = md.find(header)
        assert idx != -1, f"Missing institutional section: {header}"
        assert idx > last_idx, f"Section out of order: {header}"
        last_idx = idx

    # The Markdown must use real pipe-tables, not pre-formatted code blocks.
    assert "| Fundamental Metric | Value | Commentary |" in md
    assert "| Period | Actual EPS | Estimate EPS | Surprise % |" in md
    assert "| Valuation / Technical | Value |" in md


def test_dossier_pdf_exports_multipage_layout(monkeypatch) -> None:
    """The PDF export must produce a multi-page, layout-grade document."""

    _patch_shared_dependencies(monkeypatch, finnhub_ok=True)

    pdf_resp = research.research_dossier_export("AAPL", format="pdf")
    body = bytes(pdf_resp.body)
    assert pdf_resp.status_code == 200
    assert pdf_resp.media_type == "application/pdf"
    assert body.startswith(b"%PDF-1.4")
    assert b"%%EOF" in body
    assert b"/Type /Catalog" in body
    assert b"/Type /Pages" in body
    # Each new page produces at least one /Type /Page object; institutional
    # output should always span more than a single page.
    assert body.count(b"/Type /Page ") >= 2, "PDF should span multiple pages"
    # PDF should be substantially larger than the legacy text-dump format.
    assert len(body) >= 8000, f"PDF unexpectedly small: {len(body)} bytes"
    # Both regular and bold Helvetica fonts should be embedded references.
    assert b"/BaseFont /Helvetica" in body
    assert b"/BaseFont /Helvetica-Bold" in body


def test_recommendation_mapping_translates_long_to_buy(monkeypatch) -> None:
    """`report_v2` recommendation values are translated into IC-friendly labels."""

    _patch_shared_dependencies(monkeypatch, finnhub_ok=True)
    dossier = research._compose_research_dossier("AAPL")
    pitch = dossier["executive_pitch"]

    # raw "long" → "BUY" for the rendered recommendation; raw kept for traceability.
    assert pitch["recommendation"] == "BUY"
    assert pitch["recommendation_raw"] == "long"
    assert pitch["confidence_score"] == 78.0
    # confidence_label should never fall back to the literal "Moderate" when the
    # underlying score implies a higher tier.
    assert pitch["confidence_label"] in {"High", "Moderately High"}
    # Horizon must be honored from the report_v2 ic_snapshot rather than the
    # legacy "3-6 months" hardcoded default.
    assert pitch["time_horizon"] == "3-6 months"


def test_format_recommendation_handles_unknown_values() -> None:
    """The mapping should be defensive about unknown verdicts."""

    assert research._format_recommendation("long") == "BUY"
    assert research._format_recommendation("PASS") == "HOLD"
    assert research._format_recommendation("strong_buy") == "STRONG BUY"
    assert research._format_recommendation("avoid") == "AVOID"
    assert research._format_recommendation("") == "WATCH"
    assert research._format_recommendation(None, default="HOLD") == "HOLD"


def test_dossier_markdown_renders_extended_sections(monkeypatch) -> None:
    """The expanded Finnhub coverage flows into the rendered Markdown."""

    _patch_shared_dependencies(monkeypatch, finnhub_ok=True)
    dossier = research._compose_research_dossier("AAPL")
    md = research._dossier_to_markdown(dossier)

    # Wording fix: BUY / SELL / HOLD style rather than long/short/pass.
    assert "**Recommendation:** BUY" in md
    # Core new sections are present.
    assert "Part VII: Insider Activity" in md
    assert "Part VIII: Sell-Side Analyst Activity" in md
    assert "Part IX: Capital Returns and Corporate Actions" in md
    assert "Part X: News and Sentiment Pulse" in md
    # Specific data points should make it through.
    assert "Morgan Stanley" in md
    assert "Tim Cook" in md
    assert "MSFT" in md  # peer universe
    # Consensus history table headers.
    assert "| Period | Strong Buy | Buy | Hold | Sell | Strong Sell |" in md


def test_dossier_markdown_renders_finnhub_percent_fields_correctly(monkeypatch) -> None:
    """Regression: Finnhub-percent fields below 1 should not be auto-multiplied.

    A 0.85% dividend yield previously rendered as 85% because the legacy
    `_ratio_pct` heuristic auto-multiplied any |v| <= 1 by 100. The dedicated
    `_pct_finnhub` helper preserves the percent value Finnhub returns.
    """

    _patch_shared_dependencies(monkeypatch, finnhub_ok=True)
    dossier = research._compose_research_dossier("AAPL")
    md = research._dossier_to_markdown(dossier)

    # Dividend yield in fixture is 0.5 (already percent → 0.5%).
    assert "| Dividend Yield (TTM) | 0.5%" in md
    # Revenue growth fixture is 8.2 → 8.2%; ROE is 145.0 → 145.0%.
    assert "| Revenue Growth (TTM YoY) | 8.2%" in md
    assert "| ROE / ROA (TTM) | 145.0% / 28.0%" in md


def test_dossier_markdown_emits_finnhub_setup_hint_when_disabled(monkeypatch) -> None:
    """When the API key is missing the dossier surfaces a setup hint."""

    _patch_shared_dependencies(monkeypatch, finnhub_ok=True)

    def _disabled_snapshot(*args, **kwargs):
        return {
            "enabled": False,
            "ok": False,
            "errors": ["finnhub_api_key_missing"],
            "as_of": "2026-05-07T10:00:00+00:00",
            "profile": {}, "quote": {}, "price_target": {},
            "recommendation_trends": {"history": []},
            "news": [], "earnings": [], "metrics": {}, "peers": [],
            "insider_transactions": {"rows": []}, "insider_sentiment": {"rows": []},
            "upgrade_downgrade": [], "sec_filings": [], "dividends": [], "splits": [],
            "earnings_calendar": [], "news_sentiment": {},
        }

    monkeypatch.setattr(research, "get_finnhub_research_snapshot", _disabled_snapshot)
    dossier = research._compose_research_dossier("AAPL")
    md = research._dossier_to_markdown(dossier)
    assert "FINNHUB_API_KEY" in md, "Markdown should call out missing Finnhub key"


def test_dossier_markdown_confidence_blocks_are_trust_weighted(monkeypatch) -> None:
    """House-style confidence blocks should be dynamic, not static placeholder text."""

    _patch_shared_dependencies(monkeypatch, finnhub_ok=True)
    dossier = research._compose_research_dossier("AAPL")
    md = research._dossier_to_markdown(dossier)

    assert "### Confidence" in md
    assert "Medium by default; elevate only when data quality and citations are complete." not in md
    assert "data confidence" in md and "citation completeness" in md


def test_finnhub_client_paces_requests_under_rate_limit(monkeypatch) -> None:
    """The Finnhub client should respect the configured rate limit (best-effort)."""

    from finnhub_data import _RateLimiter

    limiter = _RateLimiter(max_calls=3, window_sec=60.0)
    # Three immediate acquires should succeed without blocking.
    for _ in range(3):
        limiter.acquire()
    # Fourth would normally block; verify the deque is full.
    assert len(limiter._calls) == 3


def test_finnhub_client_retries_on_429(monkeypatch) -> None:
    """A transient 429 response should be retried with backoff before failing."""

    import finnhub_data

    rate_limited_resp = MagicMock()
    rate_limited_resp.status_code = 429
    rate_limited_resp.headers = {"Retry-After": "0.05"}

    success_resp = MagicMock()
    success_resp.status_code = 200
    success_resp.headers = {}
    success_resp.json.return_value = {"name": "Apple Inc."}
    success_resp.raise_for_status.return_value = None

    session = MagicMock()
    session.get.side_effect = [rate_limited_resp, success_resp]

    client = finnhub_data.FinnhubClient(api_key="x", timeout_sec=1.0, session=session, max_retries=2,
                                        rate_limit_per_min=999)
    errors: list[str] = []
    payload = client._get_json("stock/profile2", {"symbol": "AAPL"}, label="profile2", errors=errors)
    assert payload == {"name": "Apple Inc."}
    assert errors == [], "Retry should have absorbed the transient 429"
    assert session.get.call_count == 2


def test_finnhub_client_softly_marks_premium_endpoint_failures() -> None:
    """403 on a premium-only endpoint should not crash and should surface as forbidden."""

    import finnhub_data

    forbidden_resp = MagicMock()
    forbidden_resp.status_code = 403
    forbidden_resp.headers = {}

    session = MagicMock()
    session.get.return_value = forbidden_resp

    client = finnhub_data.FinnhubClient(api_key="x", timeout_sec=1.0, session=session, max_retries=0,
                                        rate_limit_per_min=999)
    errors: list[str] = []
    payload = client._get_json(
        "stock/insider-sentiment", {"symbol": "AAPL"}, label="insider_sentiment", errors=errors,
    )
    assert payload is None
    assert errors == ["insider_sentiment:forbidden"]


def test_report_v2_exposes_institutional_section_blueprint() -> None:
    """report_v2 must expose a stable institutional section blueprint."""

    from webapp.report_v2 import (
        INSTITUTIONAL_SECTION_ORDER,
        build_report_v2,
        institutional_section_blueprint,
    )

    blueprint = institutional_section_blueprint()
    ids = [section["id"] for section in blueprint]
    assert ids == list(INSTITUTIONAL_SECTION_ORDER)
    for entry in blueprint:
        assert entry["title"], f"Blueprint entry missing title: {entry}"

    payload = build_report_v2({
        "ticker": "AAPL",
        "technical": {"signal_score": 70, "stage_2": True, "vcp": True, "sector_etf": "XLK"},
        "dcf": {"margin_of_safety": 12.0},
        "health": {"flags": []},
        "edgar": {},
        "mirofish": {"conviction_score": 30, "summary": ""},
    })
    assert payload["institutional_section_order"] == list(INSTITUTIONAL_SECTION_ORDER)
    assert payload["institutional_sections"][0]["id"] == "cover"
    assert payload["institutional_sections"][-1]["id"] == "disclaimer"

