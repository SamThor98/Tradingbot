"""Unit tests for Book YTD Excel export (no live Schwab)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from core.book_export import (
    build_ytd_workbook_bytes,
    export_ytd_workbook,
    read_export_status,
)
from core.book_ledger import (
    build_options_realized_ledger,
    build_realized_ledger,
    closed_row_analysis,
    trade_key_for_fill,
)
from core.xlsx_workbook import read_sheet_rows, sheets_to_xlsx


def _eq_trade(
    *,
    activity_id: int,
    trade_date: str,
    symbol: str,
    qty: float,
    price: float,
    effect: str,
) -> dict:
    cost = -(abs(qty) * price) if effect == "OPENING" else abs(qty) * price
    return {
        "activityId": activity_id,
        "tradeDate": f"{trade_date}T15:30:00.000Z",
        "description": f"{effect} {qty} {symbol}",
        "type": "TRADE",
        "netAmount": cost,
        "transferItems": [
            {
                "instrument": {"assetType": "EQUITY", "symbol": symbol},
                "amount": qty,
                "cost": cost,
                "price": price,
                "positionEffect": effect,
            }
        ],
    }


def _opt_trade(
    *,
    activity_id: int,
    trade_date: str,
    symbol: str,
    qty: float,
    price: float,
    effect: str,
    underlying: str = "AAA",
) -> dict:
    # Options: cost ~ price * qty * 100
    mult = abs(qty) * price * 100.0
    cost = -mult if effect == "OPENING" else mult
    return {
        "activityId": activity_id,
        "tradeDate": f"{trade_date}T15:30:00.000Z",
        "description": f"{effect} {qty} {symbol}",
        "type": "TRADE",
        "netAmount": cost,
        "transferItems": [
            {
                "instrument": {
                    "assetType": "OPTION",
                    "symbol": symbol,
                    "underlyingSymbol": underlying,
                },
                "amount": qty,
                "cost": cost,
                "price": price,
                "positionEffect": effect,
            }
        ],
    }


def test_open_lots_exposed_after_partial_close() -> None:
    raw = [
        _eq_trade(activity_id=1, trade_date="2026-01-10", symbol="AAA", qty=10, price=100.0, effect="OPENING"),
        _eq_trade(activity_id=2, trade_date="2026-02-10", symbol="AAA", qty=4, price=110.0, effect="CLOSING"),
    ]
    ledger = build_realized_ledger(raw)
    assert len(ledger.fills) == 1
    assert len(ledger.open_lots) == 1
    assert ledger.open_lots[0].qty == 6
    assert ledger.open_lots[0].symbol == "AAA"


def test_options_single_leg_fifo() -> None:
    raw = [
        _opt_trade(
            activity_id=1,
            trade_date="2026-01-05",
            symbol="AAA  260320C00100000",
            qty=1,
            price=2.5,
            effect="OPENING",
        ),
        _opt_trade(
            activity_id=2,
            trade_date="2026-02-05",
            symbol="AAA  260320C00100000",
            qty=1,
            price=4.0,
            effect="CLOSING",
        ),
    ]
    ledger = build_options_realized_ledger(raw)
    assert len(ledger.fills) == 1
    assert ledger.fills[0].realized_pl == 150.0  # (4-2.5)*100
    assert ledger.fills[0].asset_class == "option"
    assert ledger.fills[0].underlying == "AAA"


def test_closed_row_analysis_pack() -> None:
    raw = [
        _eq_trade(activity_id=1, trade_date="2026-01-10", symbol="ZZZ", qty=10, price=10.0, effect="OPENING"),
        _eq_trade(activity_id=2, trade_date="2026-01-20", symbol="ZZZ", qty=10, price=12.0, effect="CLOSING"),
    ]
    fill = build_realized_ledger(raw).fills[0]
    row = closed_row_analysis(fill, tax_year=2026)
    assert row["win_loss"] == "win"
    assert row["hold_days"] == 10
    assert row["return_pct"] == 20.0
    assert row["close_weekday"] == "Tue"
    assert trade_key_for_fill(fill) == row["trade_key"]


def test_xlsx_notes_roundtrip(tmp_path: Path) -> None:
    sheets = [
        ("Fills", [["activity_id"], [1]]),
        (
            "Notes",
            [
                ["trade_key", "symbol", "close_date", "note", "tags", "updated_at"],
                ["k1", "AAA", "2026-01-20", "my note", "tagA", "2026-01-21T00:00:00+00:00"],
            ],
        ),
    ]
    path = tmp_path / "book_ytd_2026.xlsx"
    path.write_bytes(sheets_to_xlsx(sheets))
    rows = read_sheet_rows(path, "Notes")
    assert rows is not None
    assert rows[1][0] == "k1"
    assert rows[1][3] == "my note"


def test_export_preserves_notes(tmp_path: Path) -> None:
    skill_dir = tmp_path
    exports = skill_dir / "exports"
    exports.mkdir()
    path = exports / "book_ytd_2026.xlsx"
    # Seed notes via first export
    raw = [
        _eq_trade(activity_id=1, trade_date="2026-01-10", symbol="AAA", qty=10, price=100.0, effect="OPENING"),
        _eq_trade(activity_id=2, trade_date="2026-03-10", symbol="AAA", qty=10, price=110.0, effect="CLOSING"),
    ]
    meta = {"error": None, "count": 2, "source": "schwab", "start": "2025-01-01", "end": "2026-07-20"}

    with patch("core.book_export.fetch_trades_for_skill", return_value=(raw, meta)):
        with patch("core.book_export.resolve_export_path", return_value=path):
            first = export_ytd_workbook(skill_dir=skill_dir, tax_year=2026, source="button")
    assert first["ok"] is True
    assert path.is_file()

    notes_rows = read_sheet_rows(path, "Notes")
    assert notes_rows and len(notes_rows) >= 2
    key = notes_rows[1][0]
    # Inject a note into the Notes sheet
    from core.xlsx_workbook import sheets_to_xlsx as _build

    closed = read_sheet_rows(path, "Closed")
    fills = read_sheet_rows(path, "Fills")
    open_lots = read_sheet_rows(path, "OpenLots")
    closed_opt = read_sheet_rows(path, "ClosedOptions")
    summary = read_sheet_rows(path, "Summary")
    notes_rows[1][3] = "hold thesis"
    notes_rows[1][4] = "swing"
    path.write_bytes(
        _build(
            [
                ("Fills", fills or [["x"]]),
                ("Closed", closed or [["x"]]),
                ("OpenLots", open_lots or [["x"]]),
                ("ClosedOptions", closed_opt or [["x"]]),
                ("Summary", summary or [["x"]]),
                ("Notes", notes_rows),
            ]
        )
    )

    with patch("core.book_export.fetch_trades_for_skill", return_value=(raw, meta)):
        with patch("core.book_export.resolve_export_path", return_value=path):
            second = export_ytd_workbook(skill_dir=skill_dir, tax_year=2026, source="eod")
    assert second["ok"] is True
    notes2 = read_sheet_rows(path, "Notes")
    assert notes2 is not None
    by_key = {r[0]: r for r in notes2[1:] if r}
    assert by_key[key][3] == "hold thesis"
    assert by_key[key][4] == "swing"

    status = read_export_status(skill_dir)
    assert status.get("ok") is True
    assert status.get("source") == "eod"


def test_build_workbook_has_expected_sheets(tmp_path: Path) -> None:
    raw = [
        _eq_trade(activity_id=1, trade_date="2026-01-10", symbol="AAA", qty=10, price=100.0, effect="OPENING"),
        _eq_trade(activity_id=2, trade_date="2026-03-10", symbol="AAA", qty=10, price=110.0, effect="CLOSING"),
        _opt_trade(
            activity_id=3,
            trade_date="2026-01-15",
            symbol="AAA  260320C00100000",
            qty=1,
            price=1.0,
            effect="OPENING",
        ),
        _opt_trade(
            activity_id=4,
            trade_date="2026-02-15",
            symbol="AAA  260320C00100000",
            qty=1,
            price=1.5,
            effect="CLOSING",
        ),
    ]
    meta = {"error": None, "count": 4, "source": "schwab"}
    path = tmp_path / "book_ytd_2026.xlsx"
    with patch("core.book_export.fetch_trades_for_skill", return_value=(raw, meta)):
        payload, meta_out = build_ytd_workbook_bytes(
            skill_dir=tmp_path, tax_year=2026, xlsx_path=path
        )
    assert payload.startswith(b"PK\x03\x04")
    path.write_bytes(payload)
    assert read_sheet_rows(path, "Closed") is not None
    assert read_sheet_rows(path, "ClosedOptions") is not None
    assert read_sheet_rows(path, "OpenLots") is not None
    assert read_sheet_rows(path, "Summary") is not None
    assert meta_out["closed_count"] == 1
    assert meta_out["closed_options_count"] == 1


def test_config_hhmm_and_path(tmp_path: Path, monkeypatch) -> None:
    from config import clear_env_cache, get_book_ytd_export_hhmm, get_book_ytd_export_path

    clear_env_cache()
    monkeypatch.setenv("BOOK_YTD_EXPORT_HHMM", "17:05")
    assert get_book_ytd_export_hhmm(tmp_path) == (17, 5)
    monkeypatch.setenv("BOOK_YTD_EXPORT_PATH", "exports/book_ytd_{year}.xlsx")
    clear_env_cache()
    p = get_book_ytd_export_path(tmp_path, tax_year=2026)
    assert p.name == "book_ytd_2026.xlsx"
    assert "exports" in p.parts
