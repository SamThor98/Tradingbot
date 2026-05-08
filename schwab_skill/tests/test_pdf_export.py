"""Tests for the institutional PDF export layout engine."""

from __future__ import annotations

from webapp.pdf_export import PDFBuilder, dossier_to_pdf


def _minimal_dossier() -> dict:
    return {
        "ticker": "AAPL",
        "generated_at": "2026-05-07T10:00:00+00:00",
        "executive_pitch": {
            "recommendation": "BUY",
            "confidence_label": "High",
            "confidence_score": 84,
            "time_horizon": "3-6 months",
            "thesis": "Technical regime and valuation framing align with expanding institutional flow.",
        },
        "sections": {
            "technical_valuation_fundamentals": {
                "report_v2": {
                    "ic_snapshot": {
                        "expected_return_base_pct": 12.0,
                        "thesis_top3": ["Trend constructive", "Margin expansion intact"],
                        "risks_top3": ["Customer concentration"],
                        "invalidation": ["Stage 2 break"],
                    },
                    "monitoring_plan": {
                        "weekly_checks": ["Recompute trend"],
                        "monthly_checks": ["Re-underwrite valuation"],
                        "kill_switches": ["Thesis invalidation"],
                    },
                    "portfolio_fit": {"risk_budget_impact": "Medium", "sector_overlap_pct": 0.18},
                },
                "raw_report": {
                    "technical": {
                        "signal_score": 72,
                        "stage_2": True,
                        "vcp": True,
                        "sector_etf": "XLK",
                        "current_price": 192.4,
                        "high_52w": 210,
                        "low_52w": 150,
                        "sma_50": 190,
                        "sma_150": 185,
                        "sma_200": 180,
                    },
                    "dcf": {"intrinsic_value": 215, "margin_of_safety": 12.0},
                    "health": {"flags": []},
                    "comps": {"median_pe": 28, "implied_price_pe": 215, "implied_price_ps": 210},
                    "edgar": {
                        "risk_tag": "low",
                        "recent_8k": False,
                        "recent_filings": [
                            {"form": "10-K", "date": "2026-04-01", "description": "Annual report"},
                        ],
                    },
                },
            },
            "sec_narrative": {
                "analyze": {
                    "summary_headline": "Filing supports growth thesis.",
                    "narrative_summary": "Management commentary aligns with continued demand.",
                },
                "compare": {"compare": {
                    "summary_headline": "Disclosure posture improving.",
                    "narrative_summary": "Risk language softer than prior filing.",
                }},
            },
            "portfolio_and_sector_context": {
                "portfolio_summary": {"positions_count": 5, "total_market_value": 125000},
                "portfolio_risk": {"concentration": {"hhi_label": "Moderate"}},
            },
            "finnhub_catalysts_risks": {
                "snapshot": {
                    "profile": {
                        "name": "Apple Inc.",
                        "finnhub_industry": "Hardware",
                        "exchange": "NASDAQ",
                        "country": "US",
                        "currency": "USD",
                        "market_cap": 3.2e12,
                    },
                    "quote": {"current": 192.4},
                    "metrics": {
                        "52week_low": 150,
                        "52week_high": 210,
                        "pe_ttm": 28,
                        "revenue_growth_ttm_yoy": 0.08,
                        "operating_margin_ttm": 0.31,
                        "net_margin_ttm": 0.24,
                    },
                    "recommendation_trends": {"buy": 12, "strong_buy": 3, "sell": 1, "strong_sell": 0},
                    "price_target": {"mean": 220},
                    "earnings": [
                        {"period": "2026-Q1", "actual": 1.5, "estimate": 1.4, "surprise_percent": 7.1},
                        {"period": "2025-Q4", "actual": 1.2, "estimate": 1.1, "surprise_percent": 9.0},
                    ],
                },
                "catalysts": ["Earnings in 3 weeks", "Product launch"],
                "risks": ["Supply chain volatility"],
            },
        },
        "source_metadata": [
            {"name": "finnhub", "status": "ok", "detail": "data ok"},
            {"name": "report_stack", "status": "ok", "detail": "fundamentals available"},
        ],
        "fallback_notes": [],
    }


def test_pdf_builder_outputs_valid_pdf_header() -> None:
    builder = PDFBuilder(header_text="Test Doc", footer_text="footer")
    builder.heading("Hello", level=1)
    builder.paragraph("Body paragraph for the smoke test.")
    pdf = builder.to_bytes()

    assert pdf.startswith(b"%PDF-1.4")
    assert b"%%EOF" in pdf
    assert b"/Type /Catalog" in pdf
    assert b"/Type /Pages" in pdf
    assert b"xref" in pdf
    # Helvetica regular and bold fonts are required for headings + body.
    assert b"/BaseFont /Helvetica" in pdf
    assert b"/BaseFont /Helvetica-Bold" in pdf


def test_pdf_builder_paginates_long_content() -> None:
    builder = PDFBuilder()
    # Push enough content to force several page breaks.
    for i in range(220):
        builder.paragraph(
            "Lorem ipsum dolor sit amet, consectetur adipiscing elit. "
            f"Paragraph index {i} keeps the layout engine busy with continuous body text."
        )
    pdf = builder.to_bytes()
    # Each page produces a /Type /Page object; long content should yield multiple pages.
    assert pdf.count(b"/Type /Page ") >= 5, "Long content should paginate into multiple pages"


def test_pdf_builder_renders_table_with_borders_and_zebra() -> None:
    builder = PDFBuilder()
    builder.table(
        headers=["Metric", "Value", "Commentary"],
        rows=[
            ["Revenue Growth", "8.0%", "Healthy top-line"],
            ["Margin", "31.0%", "Stable operating leverage"],
            ["EPS Growth", "10.5%", "Earnings re-rating intact"],
            ["Leverage", "0.42", "Comfortable D/E"],
        ],
        col_widths=[2.5, 1.5, 3.0],
        alignments=["left", "right", "left"],
    )
    pdf = builder.to_bytes()
    # Table should render as filled rectangles + stroked borders, not raw text dump.
    body = pdf.decode("latin-1", errors="ignore")
    assert " re f" in body, "Table fill rectangles should be present"
    assert " re S" in body, "Table stroked borders should be present"


def test_dossier_to_pdf_includes_institutional_section_titles() -> None:
    pdf = dossier_to_pdf(_minimal_dossier())
    body = pdf.decode("latin-1", errors="ignore")

    expected_titles = [
        "Executive Investment Summary",
        "Company and Business Model",
        "Fundamental Performance",
        "Valuation and Technical Positioning",
        "SEC Narrative and Filing Deltas",
        "Portfolio Fit and Risk Budget",
        "Catalyst and Risk Matrix",
        "Monitoring Plan",
        "References and Source Metadata",
        "Disclaimer",
    ]
    for title in expected_titles:
        assert title in body, f"Missing institutional section in PDF: {title}"

    # Cover details should appear in the rendered text.
    assert "AAPL" in body
    assert "Apple Inc." in body
    # Eyebrow labels are rendered in uppercase by the layout engine.
    assert "RECOMMENDATION" in body
    assert "BUY" in body
    assert "EXPECTED RETURN" in body
