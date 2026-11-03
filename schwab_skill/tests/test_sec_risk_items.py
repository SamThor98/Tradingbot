"""Routine 8-K items must not be tagged high.

A high tag caps the composite score and setup confidence at 45. Officer
departures, bylaw edits, and the 8.01 catch-all are common and were pinning
that cap on almost every row.
"""

from __future__ import annotations

from sec_enrichment import _risk_tag_from_filings


def _filing(items: list[str], description: str = "FORM 8-K") -> dict:
    return {"form": "8-K", "items": items, "description": description}


def test_routine_8k_items_are_medium() -> None:
    for code in ("5.02", "5.03", "8.01"):
        tag, reasons = _risk_tag_from_filings([_filing([code])])
        assert tag == "medium", code
        assert any(code in reason for reason in reasons)


def test_distress_8k_items_stay_high() -> None:
    for code in ("1.03", "3.01", "4.02"):
        tag, _reasons = _risk_tag_from_filings([_filing([code])])
        assert tag == "high", code


def test_distress_keyword_still_marks_a_catchall_filing_high() -> None:
    tag, reasons = _risk_tag_from_filings([_filing(["8.01"], description="notice of delisting")])
    assert tag == "high"
    assert any("delisting" in reason for reason in reasons)
