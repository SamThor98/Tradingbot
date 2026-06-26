"""Render contract: payload keys required by the filing-first SEC compare panel."""

from __future__ import annotations

REQUIRED_COMPARE_KEYS = frozenset(
    {
        "summary_headline",
        "narrative_summary",
        "compare_confidence",
        "analysis_mode",
        "data_freshness",
        "top_differences",
        "top_commonalities",
    }
)

REQUIRED_PAYLOAD_KEYS = frozenset({"ok", "mode", "compare", "left", "right"})


def _fixture_payload() -> dict[str, object]:
    return {
        "ok": True,
        "mode": "ticker_over_time",
        "form_type": "10-K",
        "left": {"ticker": "AAPL", "form": "10-K", "filing_date": "2026-03-31", "filing_url": "https://example.com/a"},
        "right": {"ticker": "AAPL", "form": "10-K", "filing_date": "2025-03-31", "filing_url": "https://example.com/b"},
        "compare": {
            "summary_headline": "Clear filing divergence.",
            "narrative_summary": "Guidance tone shifted.",
            "compare_confidence": 71,
            "analysis_mode": "full_text",
            "data_freshness": {"left_from_cache": False, "right_from_cache": True},
            "top_differences": ["Guidance tone differs."],
            "top_commonalities": ["Shared risk terms."],
            "differences": ["Guidance tone differs."],
            "similarities": ["Shared risk terms."],
            "investor_takeaway": "Use caution.",
        },
        "management_dashboard": {
            "data_fidelity": {
                "say_do_timeline": "derived_from_compare_deltas",
                "disclaimer": "Modeled from compare deltas.",
            },
            "integrity_scorecard": {"score": 68, "pillars": []},
            "say_do_timeline": [],
            "dilution_sbc_heatmap": [],
            "red_flags": [],
        },
    }


def test_render_contract_payload_shape() -> None:
    payload = _fixture_payload()
    assert REQUIRED_PAYLOAD_KEYS.issubset(payload.keys())
    compare = payload["compare"]
    assert isinstance(compare, dict)
    assert REQUIRED_COMPARE_KEYS.issubset(compare.keys())


def test_render_contract_management_dashboard_fidelity() -> None:
    payload = _fixture_payload()
    md = payload["management_dashboard"]
    assert isinstance(md, dict)
    fidelity = md["data_fidelity"]
    assert isinstance(fidelity, dict)
    assert "say_do_timeline" in fidelity
    assert "derived" in str(fidelity["say_do_timeline"])
