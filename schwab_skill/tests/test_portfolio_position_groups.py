"""Covered-call nesting for Portfolio → Positions."""

from __future__ import annotations

from core.portfolio_position_groups import (
    group_portfolio_positions,
    normalize_schwab_position,
    parse_occ_symbol,
    summarize_position_groups,
)
from webapp._shared import build_portfolio_summary


def test_parse_occ_symbol_call() -> None:
    parsed = parse_occ_symbol("AAPL  260821C00200000")
    assert parsed is not None
    assert parsed["underlying"] == "AAPL"
    assert parsed["put_call"] == "CALL"
    assert parsed["strike"] == 200.0
    assert parsed["expiry"] == "2026-08-21"


def test_parse_occ_symbol_put() -> None:
    parsed = parse_occ_symbol("SPXW  261218P04500000")
    assert parsed is not None
    assert parsed["underlying"] == "SPXW"
    assert parsed["put_call"] == "PUT"
    assert parsed["strike"] == 4500.0


def test_normalize_short_call_signed_qty_and_last() -> None:
    row = normalize_schwab_position(
        {
            "instrument": {
                "assetType": "OPTION",
                "symbol": "AAPL  260821C00200000",
                "underlyingSymbol": "AAPL",
                "putCall": "CALL",
                "strikePrice": 200.0,
            },
            "longQuantity": 0,
            "shortQuantity": 2,
            "marketValue": -620.0,
            "averagePrice": 3.5,
            "currentDayProfitLoss": 40.0,
            "shortOpenProfitLoss": 80.0,
        }
    )
    assert row is not None
    assert row["qty"] == -2
    assert row["side"] == "short"
    assert row["asset_type"] == "OPTION"
    assert row["underlying"] == "AAPL"
    assert row["put_call"] == "CALL"
    assert row["last"] == 3.1  # 620 / (2 * 100)
    # Short: open_pl / (avg * contracts * 100) = 80 / 700 ≈ 11.43%
    assert row["pl_pct"] == 11.43


def test_short_option_pl_pct_inverts_when_mark_rises() -> None:
    """Short call marked above entry is a loss (was wrongly positive before)."""
    from core.portfolio_position_groups import compute_pl_pct

    # last 3.45 > avg 2.29 → short is underwater
    pct = compute_pl_pct(
        signed_qty=-7,
        avg_cost=2.2933,
        last=3.45,
        is_option=True,
        open_pl=-809.69,
    )
    assert pct is not None
    assert pct < 0
    assert abs(pct - (-50.44)) < 0.05


def test_group_nests_short_call_under_equity() -> None:
    positions = [
        {
            "symbol": "AAPL",
            "qty": 200,
            "market_value": 39680.0,
            "day_pl": 240.0,
            "pl_pct": 12.4,
            "last": 198.4,
            "avg_cost": 180.0,
            "asset_type": "EQUITY",
            "side": "long",
        },
        {
            "symbol": "AAPL  260821C00200000",
            "qty": -2,
            "market_value": -620.0,
            "day_pl": 40.0,
            "pl_pct": 18.2,
            "last": 3.1,
            "avg_cost": 3.5,
            "asset_type": "OPTION",
            "underlying": "AAPL",
            "side": "short",
            "put_call": "CALL",
            "strike": 200.0,
            "expiry": "2026-08-21",
        },
        {
            "symbol": "MSFT",
            "qty": 50,
            "market_value": 21000.0,
            "day_pl": -110.0,
            "pl_pct": 5.1,
            "last": 420.0,
            "avg_cost": 400.0,
            "asset_type": "EQUITY",
            "side": "long",
        },
    ]
    groups = group_portfolio_positions(positions)
    assert [g["kind"] for g in groups] == ["equity_package", "equity_package"]
    aapl = groups[0]
    assert aapl["symbol"] == "AAPL"
    assert aapl["coverage"] == "covered"
    assert len(aapl["legs"]) == 1
    assert aapl["legs"][0]["qty"] == -2
    assert aapl["legs"][0]["coverage"] == "covered"
    assert aapl["legs"][0]["shares_covered"] == 200
    assert groups[1]["symbol"] == "MSFT"
    assert groups[1]["legs"] == []


def test_group_partial_and_orphan() -> None:
    positions = [
        {
            "symbol": "META",
            "qty": 50,
            "market_value": 25000.0,
            "day_pl": 0.0,
            "pl_pct": 0.0,
            "last": 500.0,
            "avg_cost": 480.0,
            "asset_type": "EQUITY",
            "side": "long",
        },
        {
            "symbol": "META  260918C00500000",
            "qty": -1,
            "market_value": -400.0,
            "day_pl": 10.0,
            "pl_pct": 0.0,
            "last": 4.0,
            "avg_cost": 5.0,
            "asset_type": "OPTION",
            "underlying": "META",
            "side": "short",
            "put_call": "CALL",
            "strike": 500.0,
            "expiry": "2026-09-18",
        },
        {
            "symbol": "NVDA  260918C00150000",
            "qty": -1,
            "market_value": -820.0,
            "day_pl": -30.0,
            "pl_pct": -6.0,
            "last": 8.2,
            "avg_cost": 9.0,
            "asset_type": "OPTION",
            "underlying": "NVDA",
            "side": "short",
            "put_call": "CALL",
            "strike": 150.0,
            "expiry": "2026-09-18",
        },
    ]
    groups = group_portfolio_positions(positions)
    meta = next(g for g in groups if g.get("symbol") == "META")
    assert meta["coverage"] == "partial"
    assert meta["legs"][0]["coverage"] == "partial"
    orphan = next(g for g in groups if g.get("kind") == "orphan_option")
    assert orphan["symbol"].startswith("NVDA")
    assert orphan["coverage"] == "orphan"


def test_long_call_nests_under_equity() -> None:
    positions = [
        {
            "symbol": "TSLA",
            "qty": 100,
            "market_value": 25000.0,
            "day_pl": 0.0,
            "pl_pct": 0.0,
            "last": 250.0,
            "avg_cost": 240.0,
            "asset_type": "EQUITY",
            "side": "long",
        },
        {
            "symbol": "TSLA  260821C00300000",
            "qty": 1,
            "market_value": 500.0,
            "day_pl": 20.0,
            "pl_pct": 10.0,
            "last": 5.0,
            "avg_cost": 4.5,
            "asset_type": "OPTION",
            "underlying": "TSLA",
            "side": "long",
            "put_call": "CALL",
            "strike": 300.0,
            "expiry": "2026-08-21",
        },
    ]
    groups = group_portfolio_positions(positions)
    assert len(groups) == 1
    tsla = groups[0]
    assert tsla["symbol"] == "TSLA"
    assert len(tsla["legs"]) == 1
    assert tsla["legs"][0]["coverage"] is None
    assert tsla["coverage"] is None


def test_build_portfolio_summary_includes_groups() -> None:
    account = {
        "accounts": [
            {
                "securitiesAccount": {
                    "positions": [
                        {
                            "instrument": {"symbol": "AAPL", "assetType": "EQUITY"},
                            "longQuantity": 200,
                            "shortQuantity": 0,
                            "marketValue": 40000.0,
                            "averagePrice": 180.0,
                            "currentDayProfitLoss": 100.0,
                        },
                        {
                            "instrument": {
                                "symbol": "AAPL  260821C00200000",
                                "assetType": "OPTION",
                                "underlyingSymbol": "AAPL",
                                "putCall": "CALL",
                                "strikePrice": 200.0,
                            },
                            "longQuantity": 0,
                            "shortQuantity": 2,
                            "marketValue": -600.0,
                            "averagePrice": 3.0,
                            "currentDayProfitLoss": 20.0,
                        },
                    ]
                }
            }
        ]
    }
    summary = build_portfolio_summary(account)
    assert summary["positions_count"] == 2
    assert summary["equity_count"] == 1
    assert summary["option_count"] == 1
    assert summary["covered_package_count"] == 1
    groups = summary["position_groups"]
    assert len(groups) == 1
    assert groups[0]["coverage"] == "covered"
    assert len(groups[0]["legs"]) == 1
    counts = summarize_position_groups(groups)
    assert counts["option_leg_count"] == 1


def test_occ_fallback_without_underlying_symbol() -> None:
    row = normalize_schwab_position(
        {
            "instrument": {"symbol": "DFTX  260918C00024000"},
            "longQuantity": 0,
            "shortQuantity": 1,
            "marketValue": -100.0,
            "averagePrice": 1.0,
            "currentDayProfitLoss": 0.0,
        }
    )
    assert row is not None
    assert row["asset_type"] == "OPTION"
    assert row["underlying"] == "DFTX"
    assert row["put_call"] == "CALL"
    assert row["strike"] == 24.0
