"""Ticket blotter copy stays literal. No softened review verdict."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "webapp" / "static"


def test_ticket_status_is_clear_or_one_block() -> None:
    drawer = (STATIC / "panels" / "tradeDrawer.js").read_text(encoding="utf-8")
    facts = (STATIC / "modules" / "ticketFacts.js").read_text(encoding="utf-8")
    assert "clear to stage" in facts
    assert "Looks OK to review" not in drawer
    assert "Not ready — safety checks need attention." not in drawer
    assert "buildTicketModel" in drawer
    for label in ("Side", "Qty", "Limit", "Stop", "Heat", "Data quality", "Guardrail", "Status"):
        assert label in drawer


def test_pending_blotter_keeps_scan_density_fields() -> None:
    board = (STATIC / "panels" / "pendingBoard.js").read_text(encoding="utf-8")
    assert "pending-blotter" in board
    assert "Notional vs preset cap" not in board
    assert "meter conviction" not in board
    assert "Requires extra review" not in board
    for header in ("Ticker", "Side", "Qty", "Limit", "Stop", "Heat", "Sector", "Book", "Status", "Actions"):
        assert header in board
    assert "pending-blotter-note" in board
    assert 'data-stage="${clear ? "clear" : "blocked"}"' in board
    assert "renderSignalTrustRow" in board
    assert "disabled" in board
