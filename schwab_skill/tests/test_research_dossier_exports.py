from __future__ import annotations

import json

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
                "recommendation": "BUY",
                "confidence_label": "High",
                "time_horizon": "3-6 months",
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
            "errors": [] if finnhub_ok else ["company_news:Timeout"],
            "recommendation_trends": {"buy": 12, "strong_buy": 3, "sell": 1, "strong_sell": 0},
            "news": [{"headline": "Analyst upgrade after product launch"}],
            "earnings": [{"period": "2026-Q1", "surprise_percent": 8.2}],
            "price_target": {"mean": 220.0},
            "quote": {"current": 192.4},
            "profile": {"name": "Apple Inc."},
            "metrics": {"pe_ttm": 28.0},
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
    assert any("company_news:Timeout" in note for note in dossier["fallback_notes"])


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

