"""Group Schwab portfolio positions so options nest under covering equity.

Display-only helper for Research → Portfolio → Positions. Risk/Intel keep using
the flat ``positions`` list and ``is_option_symbol`` exclusion.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any

from core.portfolio_analytics import is_option_symbol

# OCC: root (1–6, may include . /) + YYMMDD + C|P + strike*1000 (8 digits)
_OCC_PARSE_RE = re.compile(
    r"^(?P<root>[A-Z.\/]{1,6})\s*(?P<ymd>\d{6})(?P<cp>[CP])(?P<strike>\d{8})$"
)


def _f(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_occ_symbol(symbol: str) -> dict[str, Any] | None:
    """Parse an OCC option symbol into underlying, expiry, put/call, strike."""
    raw = str(symbol or "").strip().upper()
    match = _OCC_PARSE_RE.match(raw)
    if not match:
        return None
    ymd = match.group("ymd")
    try:
        expiry = date(2000 + int(ymd[0:2]), int(ymd[2:4]), int(ymd[4:6])).isoformat()
    except ValueError:
        expiry = None
    strike_raw = int(match.group("strike"))
    return {
        "underlying": match.group("root").strip().rstrip("."),
        "expiry": expiry,
        "put_call": "CALL" if match.group("cp") == "C" else "PUT",
        "strike": round(strike_raw / 1000.0, 3),
    }


def _coverage_label(*, shares: float, contracts: float) -> str:
    """Return covered | partial | orphan for short-call share coverage."""
    abs_contracts = abs(contracts)
    if abs_contracts <= 0:
        return "orphan"
    shares_needed = abs_contracts * 100.0
    if shares <= 0:
        return "orphan"
    if shares + 1e-9 >= shares_needed:
        return "covered"
    return "partial"


def _option_last(market_value: float, signed_qty: float) -> float:
    abs_qty = abs(signed_qty)
    if abs_qty <= 0:
        return 0.0
    # Schwab option MV ≈ contracts * 100 * premium
    return abs(market_value) / (abs_qty * 100.0)


def _equity_last(market_value: float, signed_qty: float) -> float:
    abs_qty = abs(signed_qty)
    if abs_qty <= 0:
        return 0.0
    return abs(market_value) / abs_qty


def compute_pl_pct(
    *,
    signed_qty: float,
    avg_cost: float,
    last: float,
    is_option: bool,
    open_pl: float | None,
) -> float | None:
    """Unrealized P/L % vs cost basis.

    Prefers Schwab ``longOpenProfitLoss`` / ``shortOpenProfitLoss``. Shorts use
    credit as basis so a rising mark shows as a loss.
    """
    mult = 100.0 if is_option else 1.0
    cost_basis = abs(float(avg_cost) * float(signed_qty) * mult) if avg_cost and signed_qty else 0.0
    if open_pl is not None and cost_basis > 0:
        return round(float(open_pl) / cost_basis * 100.0, 2)
    if not avg_cost:
        return None
    if signed_qty < 0:
        return round((avg_cost - last) / avg_cost * 100.0, 2)
    return round((last - avg_cost) / avg_cost * 100.0, 2)


def _resolve_underlying(row_or_symbol: Any, explicit: str | None = None) -> str:
    if explicit:
        return str(explicit).upper().strip()
    symbol = str(row_or_symbol or "")
    if isinstance(row_or_symbol, dict):
        explicit = str(row_or_symbol.get("underlying") or "").upper().strip() or None
        if explicit:
            return explicit
        symbol = str(row_or_symbol.get("symbol") or "")
    occ = parse_occ_symbol(symbol)
    return str((occ or {}).get("underlying") or "").upper().strip()


def normalize_schwab_position(pos: dict[str, Any]) -> dict[str, Any] | None:
    """Flatten one Schwab position dict into a UI row (signed qty)."""
    if not isinstance(pos, dict):
        return None
    inst = pos.get("instrument") if isinstance(pos.get("instrument"), dict) else {}
    symbol = str(inst.get("symbol") or "").strip().upper() or "?"
    long_qty = _f(pos.get("longQuantity")) or 0.0
    short_qty = _f(pos.get("shortQuantity")) or 0.0
    signed_qty = long_qty - short_qty
    if abs(signed_qty) < 1e-12:
        return None

    asset_type = str(inst.get("assetType") or "").upper().strip()
    underlying = str(inst.get("underlyingSymbol") or "").upper().strip() or None
    put_call = str(inst.get("putCall") or "").upper().strip() or None
    strike = _f(inst.get("strikePrice"))
    expiry_raw = inst.get("expirationDate") or inst.get("expiry") or inst.get("maturityDate")
    expiry: str | None = None
    if expiry_raw:
        expiry = str(expiry_raw)[:10]

    occ = parse_occ_symbol(symbol) if (asset_type == "OPTION" or is_option_symbol(symbol)) else None
    if occ:
        asset_type = asset_type or "OPTION"
        underlying = underlying or occ.get("underlying")
        put_call = put_call or occ.get("put_call")
        if strike is None:
            strike = occ.get("strike")
        if expiry is None:
            expiry = occ.get("expiry")
    elif is_option_symbol(symbol):
        asset_type = asset_type or "OPTION"

    is_option = asset_type == "OPTION" or is_option_symbol(symbol)
    mkt_val = _f(pos.get("marketValue")) or 0.0
    day_pl = _f(pos.get("currentDayProfitLoss")) or 0.0
    day_pl_pct = _f(pos.get("currentDayProfitLossPercentage"))
    avg_cost = _f(pos.get("averagePrice")) or 0.0
    last = _option_last(mkt_val, signed_qty) if is_option else _equity_last(mkt_val, signed_qty)
    open_pl = _f(pos.get("shortOpenProfitLoss")) if signed_qty < 0 else _f(pos.get("longOpenProfitLoss"))
    pl_pct = compute_pl_pct(
        signed_qty=signed_qty,
        avg_cost=avg_cost,
        last=last,
        is_option=is_option,
        open_pl=open_pl,
    )
    side = "long" if signed_qty > 0 else "short"

    # Preserve integer quantities when whole contracts/shares; else keep float.
    qty_out: int | float
    if abs(signed_qty - round(signed_qty)) < 1e-9:
        qty_out = int(round(signed_qty))
    else:
        qty_out = round(signed_qty, 4)

    return {
        "symbol": symbol,
        "qty": qty_out,
        "market_value": round(mkt_val, 2),
        "day_pl": round(day_pl, 2),
        "day_pl_pct": round(day_pl_pct, 2) if day_pl_pct is not None else None,
        "avg_cost": round(avg_cost, 4),
        "last": round(last, 4),
        "pl_pct": pl_pct,
        "open_pl": round(open_pl, 2) if open_pl is not None else None,
        "asset_type": "OPTION" if is_option else (asset_type or "EQUITY"),
        "underlying": underlying,
        "side": side,
        "put_call": put_call,
        "strike": round(strike, 4) if strike is not None else None,
        "expiry": expiry,
    }


def _leg_coverage(row: dict[str, Any], *, shares: float) -> str | None:
    """Coverage badge only for short calls; other legs nest without a badge."""
    qty = float(row.get("qty") or 0)
    put_call = str(row.get("put_call") or "").upper()
    if not put_call:
        occ = parse_occ_symbol(str(row.get("symbol") or ""))
        put_call = str((occ or {}).get("put_call") or "")
    if qty < 0 and put_call == "CALL":
        return _coverage_label(shares=shares, contracts=qty)
    return None


def _leg_from_row(row: dict[str, Any], *, coverage: str | None) -> dict[str, Any]:
    contracts = float(row.get("qty") or 0)
    return {
        "kind": "option",
        "symbol": row.get("symbol"),
        "underlying": row.get("underlying"),
        "side": row.get("side"),
        "put_call": row.get("put_call"),
        "strike": row.get("strike"),
        "expiry": row.get("expiry"),
        "qty": row.get("qty"),
        "market_value": row.get("market_value"),
        "day_pl": row.get("day_pl"),
        "day_pl_pct": row.get("day_pl_pct"),
        "last": row.get("last"),
        "pl_pct": row.get("pl_pct"),
        "avg_cost": row.get("avg_cost"),
        "open_pl": row.get("open_pl"),
        "coverage": coverage,
        "shares_covered": int(round(abs(contracts) * 100)),
    }


def _package_coverage(legs: list[dict[str, Any]]) -> str | None:
    labels = {str(leg.get("coverage") or "") for leg in legs if leg.get("coverage")}
    if not labels:
        return None
    if "partial" in labels:
        return "partial"
    if "covered" in labels:
        return "covered"
    if "orphan" in labels:
        return "orphan"
    return next(iter(labels), None)


def group_portfolio_positions(positions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Build parent/child groups for the Positions table.

    Any option with a matching long equity underlying nests under that equity.
    Short CALL legs get Covered/Partial badges; naked short calls warn as orphan.
    """
    equities: dict[str, dict[str, Any]] = {}
    equity_order: list[str] = []
    options: list[dict[str, Any]] = []

    for row in positions:
        if not isinstance(row, dict):
            continue
        symbol = str(row.get("symbol") or "").upper()
        asset = str(row.get("asset_type") or "").upper()
        is_opt = asset == "OPTION" or is_option_symbol(symbol)
        if is_opt:
            # Ensure put_call/underlying populated for grouping.
            if not row.get("put_call") or not row.get("underlying"):
                occ = parse_occ_symbol(symbol)
                if occ:
                    row = {
                        **row,
                        "put_call": row.get("put_call") or occ.get("put_call"),
                        "underlying": row.get("underlying") or occ.get("underlying"),
                        "strike": row.get("strike") if row.get("strike") is not None else occ.get("strike"),
                        "expiry": row.get("expiry") or occ.get("expiry"),
                    }
            options.append(row)
            continue
        if symbol and symbol not in equities:
            equities[symbol] = row
            equity_order.append(symbol)

    legs_by_equity: dict[str, list[dict[str, Any]]] = {s: [] for s in equity_order}
    leftover: list[dict[str, Any]] = []

    for opt in options:
        under = _resolve_underlying(opt)
        parent = equities.get(under) if under else None
        if parent is None:
            leftover.append(opt)
            continue
        shares = float(parent.get("qty") or 0)
        coverage = _leg_coverage(opt, shares=shares)
        legs_by_equity.setdefault(under, []).append(_leg_from_row(opt, coverage=coverage))

    groups: list[dict[str, Any]] = []

    for sym in equity_order:
        row = equities[sym]
        legs = legs_by_equity.get(sym) or []
        groups.append(
            {
                "kind": "equity_package",
                "symbol": row.get("symbol"),
                "qty": row.get("qty"),
                "side": row.get("side") or "long",
                "market_value": row.get("market_value"),
                "day_pl": row.get("day_pl"),
                "pl_pct": row.get("pl_pct"),
                "last": row.get("last"),
                "avg_cost": row.get("avg_cost"),
                "coverage": _package_coverage(legs),
                "legs": legs,
            }
        )

    leftover_sorted = sorted(
        leftover,
        key=lambda r: abs(float(r.get("market_value") or 0)),
        reverse=True,
    )
    for row in leftover_sorted:
        qty = float(row.get("qty") or 0)
        put_call = str(row.get("put_call") or "").upper()
        under = _resolve_underlying(row) or None
        is_naked_short_call = qty < 0 and put_call == "CALL"
        groups.append(
            {
                "kind": "orphan_option" if is_naked_short_call else "option_standalone",
                "symbol": row.get("symbol"),
                "underlying": under,
                "side": row.get("side"),
                "put_call": put_call or None,
                "strike": row.get("strike"),
                "expiry": row.get("expiry"),
                "qty": row.get("qty"),
                "market_value": row.get("market_value"),
                "day_pl": row.get("day_pl"),
                "day_pl_pct": row.get("day_pl_pct"),
                "last": row.get("last"),
                "pl_pct": row.get("pl_pct"),
                "avg_cost": row.get("avg_cost"),
                "open_pl": row.get("open_pl"),
                "coverage": "orphan" if is_naked_short_call else None,
                "legs": [],
            }
        )

    return groups


def summarize_position_groups(groups: list[dict[str, Any]]) -> dict[str, int]:
    """Counts for portfolio meta / diagnostics."""
    equity_count = sum(1 for g in groups if g.get("kind") == "equity_package")
    option_legs = sum(len(g.get("legs") or []) for g in groups if g.get("kind") == "equity_package")
    orphan_count = sum(1 for g in groups if g.get("kind") == "orphan_option")
    standalone_opts = sum(1 for g in groups if g.get("kind") == "option_standalone")
    covered_packages = sum(
        1 for g in groups if g.get("kind") == "equity_package" and g.get("coverage") in ("covered", "partial")
    )
    return {
        "equity_count": equity_count,
        "option_leg_count": option_legs,
        "orphan_option_count": orphan_count,
        "standalone_option_count": standalone_opts,
        "covered_package_count": covered_packages,
    }
