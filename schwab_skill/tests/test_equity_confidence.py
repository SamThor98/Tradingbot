"""File confidence reads the factor ledger and does not add Rank points."""

from __future__ import annotations

from core.equity_confidence import equity_confidence_block


def _full_row(**extra: object) -> dict:
    row = {
        "pts_52w": 8.0,
        "price": 100.0,
        "sma_200": 90.0,
        "trend_stack_ok": True,
        "pts_volume": 12.0,
        "reliability_score": 80.0,
        "execution_score": 90.0,
        "forensic_observed": True,
        "forensic_flags": [],
        "sec_risk_tag": "low",
        "pead_surprise_pct": 0.04,
    }
    row.update(extra)
    return row


def test_complete_technical_file_leaves_fundamentals_unobserved() -> None:
    block = equity_confidence_block(_full_row(), pts_52w=8.0)
    assert block["equity_confidence_registry"] == "equity_ledger_v1"
    # 8 of 11 factors are on the row. Free cash flow, classic fundamentals, and
    # insider filings are not. Agreement holds: mild extension matches a mild
    # 200-day gap, and no cap fires.
    assert block["equity_confidence_coverage"] == round(8 / 11, 4)
    assert block["equity_confidence_agreement"] == 1.0
    assert block["equity_confidence_freshness"] == 1.0
    assert block["equity_confidence"] == round(100 * (8 / 11))
    assert "moves rank 1/1" in block["equity_confidence_summary"]
    assert "eligible 1/2" in block["equity_confidence_summary"]
    assert "no signal 1/3" in block["equity_confidence_summary"]
    assert "cap inputs 3/3" in block["equity_confidence_summary"]


def test_missing_fundamentals_do_not_become_a_midpoint() -> None:
    bare = equity_confidence_block({"pts_52w": 8.0}, pts_52w=8.0)
    with_fcf = equity_confidence_block({"pts_52w": 8.0, "fund_fcf_yield": 0.05}, pts_52w=8.0)
    assert bare["equity_confidence"] < with_fcf["equity_confidence"]
    assert bare["equity_confidence_agreement"] == with_fcf["equity_confidence_agreement"]


def test_cap_and_stretch_disagreement_lower_agreement_only() -> None:
    calm = equity_confidence_block(_full_row(), pts_52w=8.0)
    conflicted = equity_confidence_block(
        _full_row(forensic_flags=["beneish_manipulator"], price=140.0, sma_200=100.0),
        pts_52w=8.0,
    )
    assert conflicted["equity_confidence_coverage"] == calm["equity_confidence_coverage"]
    assert conflicted["equity_confidence_agreement"] < calm["equity_confidence_agreement"]
    assert conflicted["equity_confidence"] < calm["equity_confidence"]


def test_stale_filing_zeros_freshness() -> None:
    block = equity_confidence_block(_full_row(filing_recency_days=400), pts_52w=8.0)
    assert block["equity_confidence_freshness"] == 0.0
    assert block["equity_confidence"] == 0


def test_absent_filing_age_does_not_fake_staleness() -> None:
    block = equity_confidence_block(_full_row(), pts_52w=8.0)
    assert block["equity_confidence_freshness"] == 1.0
