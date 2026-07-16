"""Unit tests for Book FIFO ledger + tax estimate (no live Schwab)."""

from __future__ import annotations

from datetime import date

from core.book_ledger import (
    aggregate_calendar,
    build_realized_ledger,
    lookback_start_for_year,
    tax_estimate_from_ledger,
)


def _trade(
    *,
    activity_id: int,
    trade_date: str,
    symbol: str,
    qty: float,
    price: float,
    effect: str,
    net: float | None = None,
) -> dict:
    cost = -(abs(qty) * price) if effect == "OPENING" else abs(qty) * price
    if net is None:
        net = cost
    return {
        "activityId": activity_id,
        "tradeDate": f"{trade_date}T15:30:00.000Z",
        "description": f"{effect} {qty} {symbol}",
        "type": "TRADE",
        "netAmount": net,
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


def test_fifo_realized_short_term() -> None:
    raw = [
        _trade(activity_id=1, trade_date="2026-01-10", symbol="AAA", qty=10, price=100.0, effect="OPENING"),
        _trade(activity_id=2, trade_date="2026-03-10", symbol="AAA", qty=10, price=110.0, effect="CLOSING"),
    ]
    ledger = build_realized_ledger(raw)
    assert len(ledger.fills) == 1
    fill = ledger.fills[0]
    assert fill.symbol == "AAA"
    assert fill.realized_pl == 100.0  # (110-100)*10
    assert fill.holding == "st"


def test_fifo_partial_close_and_long_term() -> None:
    raw = [
        _trade(activity_id=1, trade_date="2024-06-01", symbol="BBB", qty=20, price=50.0, effect="OPENING"),
        _trade(activity_id=2, trade_date="2026-01-15", symbol="BBB", qty=5, price=60.0, effect="CLOSING"),
    ]
    ledger = build_realized_ledger(raw)
    assert len(ledger.fills) == 1
    assert ledger.fills[0].qty == 5
    assert ledger.fills[0].realized_pl == 50.0
    assert ledger.fills[0].holding == "lt"


def test_unmatched_close_omits_invented_pl() -> None:
    raw = [
        _trade(activity_id=9, trade_date="2026-02-01", symbol="CCC", qty=3, price=20.0, effect="CLOSING"),
    ]
    ledger = build_realized_ledger(raw)
    assert ledger.fills == []
    assert ledger.closes_unmatched == 1


def test_calendar_and_tax_estimate() -> None:
    raw = [
        _trade(activity_id=1, trade_date="2026-01-05", symbol="DDD", qty=10, price=10.0, effect="OPENING"),
        _trade(activity_id=2, trade_date="2026-01-20", symbol="DDD", qty=10, price=12.0, effect="CLOSING"),
        _trade(activity_id=3, trade_date="2026-02-01", symbol="EEE", qty=5, price=100.0, effect="OPENING"),
        _trade(activity_id=4, trade_date="2026-02-10", symbol="EEE", qty=5, price=90.0, effect="CLOSING"),
    ]
    ledger = build_realized_ledger(raw)
    cal = aggregate_calendar(ledger, mtm_by_day={"2026-01-20": 25.0}, year=2026, month=1)
    days = {d["date"]: d for d in cal["days"]}
    assert days["2026-01-20"]["realized_pl"] == 20.0
    assert days["2026-01-20"]["mtm_pl"] == 25.0

    tax = tax_estimate_from_ledger(
        ledger,
        tax_year=2026,
        federal_st_rate=0.24,
        federal_lt_rate=0.15,
        state_rate=0.05,
        rates_configured=True,
    )
    assert tax["short_term"]["net"] == -30.0  # +20 then -50
    assert tax["estimate"] is not None
    # After netting ST loss vs nothing LT: no positive ST/LT → federal 0; state on max(total,0)=0
    assert tax["estimate"]["federal"] == 0.0

    tax_blank = tax_estimate_from_ledger(
        ledger,
        tax_year=2026,
        federal_st_rate=None,
        federal_lt_rate=None,
        state_rate=None,
        rates_configured=False,
    )
    assert tax_blank["estimate"] is None
    assert tax_blank["rates_configured"] is False


def test_holding_bucket_boundary() -> None:
    # Exactly one year later is still short-term; day after is long-term
    raw = [
        _trade(activity_id=1, trade_date="2025-03-01", symbol="FFF", qty=1, price=10.0, effect="OPENING"),
        _trade(activity_id=2, trade_date="2026-03-01", symbol="FFF", qty=1, price=11.0, effect="CLOSING"),
        _trade(activity_id=3, trade_date="2025-03-01", symbol="GGG", qty=1, price=10.0, effect="OPENING"),
        _trade(activity_id=4, trade_date="2026-03-02", symbol="GGG", qty=1, price=11.0, effect="CLOSING"),
    ]
    ledger = build_realized_ledger(raw)
    by_sym = {f.symbol: f.holding for f in ledger.fills}
    assert by_sym["FFF"] == "st"
    assert by_sym["GGG"] == "lt"
    assert date.fromisoformat("2026-03-02") > date(2026, 3, 1)


def test_lookback_start_stays_within_schwab_year_window() -> None:
    today = date(2026, 7, 16)
    start = lookback_start_for_year(2026, today)
    # Schwab rejects inclusive 365-day spans; lookback must be ≤ 364 days.
    assert (today - start).days <= 364
    assert start == date(2025, 7, 17)
