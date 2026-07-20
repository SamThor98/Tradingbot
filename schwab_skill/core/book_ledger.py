"""Realize P/L and ST/LT buckets from Schwab TRADE transactions (FIFO)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any


def _parse_day(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    text = str(value).strip()
    if not text:
        return None
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).date()
    except Exception:
        try:
            return date.fromisoformat(text[:10])
        except Exception:
            return None


def _f(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _holding_bucket(open_day: date, close_day: date) -> str:
    """US equities: long-term if held more than one year."""
    try:
        one_year = date(open_day.year + 1, open_day.month, open_day.day)
    except ValueError:
        # Feb 29 → Feb 28 next year
        one_year = date(open_day.year + 1, open_day.month, 28)
    return "lt" if close_day > one_year else "st"


@dataclass
class _Lot:
    symbol: str
    qty: float
    cost_total: float
    open_day: date


@dataclass
class OpenLot:
    """Unmatched FIFO opening lot still open after processing the fetch window."""

    symbol: str
    qty: float
    cost_total: float
    open_day: date
    asset_class: str = "equity"  # equity | option


@dataclass
class RealizedFill:
    activity_id: str | None
    symbol: str
    trade_date: date
    qty: float
    proceeds: float
    cost_basis: float
    realized_pl: float
    holding: str  # st | lt
    side: str  # SELL close
    description: str
    fees: float
    open_day: date | None
    source: str = "schwab"
    asset_class: str = "equity"  # equity | option
    underlying: str | None = None


@dataclass
class LedgerResult:
    fills: list[RealizedFill] = field(default_factory=list)
    open_lots: list[OpenLot] = field(default_factory=list)
    fees_by_day: dict[str, float] = field(default_factory=dict)
    opens_skipped: int = 0
    closes_unmatched: int = 0
    raw_trade_count: int = 0


def _security_legs(tx: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for item in tx.get("transferItems") or []:
        if not isinstance(item, dict):
            continue
        if item.get("feeType"):
            continue
        inst = item.get("instrument") or {}
        if not isinstance(inst, dict):
            continue
        sym = str(inst.get("symbol") or "").upper().strip()
        asset = str(inst.get("assetType") or "").upper()
        if not sym or asset not in ("", "EQUITY", "ETF", "COLLECTIVE_INVESTMENT"):
            # Allow missing assetType; skip obvious non-equity
            if asset in ("OPTION", "FUTURE", "FOREX", "FIXED_INCOME", "MUTUAL_FUND", "INDEX"):
                continue
            if not sym:
                continue
        out.append(item)
    return out


def _fee_total(tx: dict[str, Any]) -> float:
    total = 0.0
    for item in tx.get("transferItems") or []:
        if not isinstance(item, dict) or not item.get("feeType"):
            continue
        amt = _f(item.get("amount"))
        if amt is not None:
            total += abs(amt)
    return total


def build_realized_ledger(raw_trades: list[dict[str, Any]]) -> LedgerResult:
    """FIFO match OPENING → CLOSING equity legs into realized fills.

    Opens before the fetch window leave unmatched closes counted in
    ``closes_unmatched`` (P/L omitted rather than invented).
    """
    result = LedgerResult(raw_trade_count=len(raw_trades))
    lots: dict[str, list[_Lot]] = {}

    # Sort oldest → newest for FIFO
    def _sort_key(tx: dict[str, Any]) -> tuple[str, str]:
        day = _parse_day(tx.get("tradeDate") or tx.get("time") or tx.get("settlementDate"))
        return (day.isoformat() if day else "9999-99-99", str(tx.get("activityId") or ""))

    ordered = sorted((t for t in raw_trades if isinstance(t, dict)), key=_sort_key)

    for tx in ordered:
        day = _parse_day(tx.get("tradeDate") or tx.get("time") or tx.get("settlementDate"))
        if day is None:
            continue
        fees = _fee_total(tx)
        if fees:
            result.fees_by_day[day.isoformat()] = result.fees_by_day.get(day.isoformat(), 0.0) + fees
        desc = str(tx.get("description") or "")
        activity_id = str(tx.get("activityId")) if tx.get("activityId") is not None else None

        for leg in _security_legs(tx):
            inst = leg.get("instrument") or {}
            symbol = str(inst.get("symbol") or "").upper().strip()
            qty = abs(_f(leg.get("amount")) or 0.0)
            price = _f(leg.get("price"))
            cost = _f(leg.get("cost"))
            effect = str(leg.get("positionEffect") or "").upper()
            if qty <= 0 or not symbol:
                continue

            # Infer effect when Schwab omits it
            if effect not in ("OPENING", "CLOSING"):
                # Buys typically have negative cost / negative netAmount
                net = _f(tx.get("netAmount"))
                if net is not None and net < 0:
                    effect = "OPENING"
                elif net is not None and net > 0:
                    effect = "CLOSING"
                elif "BOUGHT" in desc.upper() or "BUY" in desc.upper():
                    effect = "OPENING"
                elif "SOLD" in desc.upper() or "SELL" in desc.upper():
                    effect = "CLOSING"
                else:
                    continue

            if effect == "OPENING":
                cost_total = abs(cost) if cost is not None else (abs(price or 0.0) * qty)
                if cost_total <= 0:
                    result.opens_skipped += 1
                    continue
                lots.setdefault(symbol, []).append(
                    _Lot(symbol=symbol, qty=qty, cost_total=cost_total, open_day=day)
                )
                continue

            # CLOSING — treat as long sale (proceeds from cost on leg or price*qty)
            proceeds = abs(cost) if cost is not None else (abs(price or 0.0) * qty)
            remaining = qty
            queue = lots.setdefault(symbol, [])
            while remaining > 1e-9 and queue:
                lot = queue[0]
                take = min(lot.qty, remaining)
                frac = take / lot.qty if lot.qty > 0 else 0.0
                lot_cost = lot.cost_total * frac
                lot_proceeds = proceeds * (take / qty) if qty > 0 else 0.0
                realized = lot_proceeds - lot_cost
                holding = _holding_bucket(lot.open_day, day)
                result.fills.append(
                    RealizedFill(
                        activity_id=activity_id,
                        symbol=symbol,
                        trade_date=day,
                        qty=take,
                        proceeds=lot_proceeds,
                        cost_basis=lot_cost,
                        realized_pl=realized,
                        holding=holding,
                        side="SELL",
                        description=desc,
                        fees=fees * (take / qty) if qty > 0 else 0.0,
                        open_day=lot.open_day,
                    )
                )
                lot.qty -= take
                lot.cost_total -= lot_cost
                remaining -= take
                if lot.qty <= 1e-9:
                    queue.pop(0)

            if remaining > 1e-6:
                result.closes_unmatched += 1

    for sym, queue in lots.items():
        for lot in queue:
            if lot.qty > 1e-9:
                result.open_lots.append(
                    OpenLot(
                        symbol=sym,
                        qty=lot.qty,
                        cost_total=lot.cost_total,
                        open_day=lot.open_day,
                        asset_class="equity",
                    )
                )
    result.open_lots.sort(key=lambda lot: (lot.symbol, lot.open_day.isoformat()))
    return result


def _option_legs(tx: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract OPTION transfer legs (single-leg matching only)."""
    out: list[dict[str, Any]] = []
    for item in tx.get("transferItems") or []:
        if not isinstance(item, dict):
            continue
        if item.get("feeType"):
            continue
        inst = item.get("instrument") or {}
        if not isinstance(inst, dict):
            continue
        asset = str(inst.get("assetType") or "").upper()
        if asset != "OPTION":
            continue
        sym = str(inst.get("symbol") or "").upper().strip()
        if not sym:
            continue
        out.append(item)
    return out


def build_options_realized_ledger(raw_trades: list[dict[str, Any]]) -> LedgerResult:
    """FIFO match single-leg OPTION OPENING → CLOSING fills.

    Multi-leg spreads/rolls/assignments are not modeled; unmatched closes are
    counted rather than inventing P/L.
    """
    result = LedgerResult(raw_trade_count=len(raw_trades))
    lots: dict[str, list[_Lot]] = {}

    def _sort_key(tx: dict[str, Any]) -> tuple[str, str]:
        day = _parse_day(tx.get("tradeDate") or tx.get("time") or tx.get("settlementDate"))
        return (day.isoformat() if day else "9999-99-99", str(tx.get("activityId") or ""))

    ordered = sorted((t for t in raw_trades if isinstance(t, dict)), key=_sort_key)

    for tx in ordered:
        day = _parse_day(tx.get("tradeDate") or tx.get("time") or tx.get("settlementDate"))
        if day is None:
            continue
        fees = _fee_total(tx)
        if fees:
            result.fees_by_day[day.isoformat()] = result.fees_by_day.get(day.isoformat(), 0.0) + fees
        desc = str(tx.get("description") or "")
        activity_id = str(tx.get("activityId")) if tx.get("activityId") is not None else None
        option_legs = _option_legs(tx)
        # Skip multi-leg option tickets (spreads) — leave for Exceptions later
        if len(option_legs) > 1:
            continue

        for leg in option_legs:
            inst = leg.get("instrument") or {}
            symbol = str(inst.get("symbol") or "").upper().strip()
            underlying = str(inst.get("underlyingSymbol") or "").upper().strip() or None
            qty = abs(_f(leg.get("amount")) or 0.0)
            price = _f(leg.get("price"))
            cost = _f(leg.get("cost"))
            effect = str(leg.get("positionEffect") or "").upper()
            if qty <= 0 or not symbol:
                continue

            if effect not in ("OPENING", "CLOSING"):
                net = _f(tx.get("netAmount"))
                if net is not None and net < 0:
                    effect = "OPENING"
                elif net is not None and net > 0:
                    effect = "CLOSING"
                elif "BOUGHT" in desc.upper() or "BUY" in desc.upper():
                    effect = "OPENING"
                elif "SOLD" in desc.upper() or "SELL" in desc.upper():
                    effect = "CLOSING"
                else:
                    continue

            if effect == "OPENING":
                cost_total = abs(cost) if cost is not None else (abs(price or 0.0) * qty * 100.0)
                if cost_total <= 0:
                    result.opens_skipped += 1
                    continue
                lots.setdefault(symbol, []).append(
                    _Lot(symbol=symbol, qty=qty, cost_total=cost_total, open_day=day)
                )
                continue

            proceeds = abs(cost) if cost is not None else (abs(price or 0.0) * qty * 100.0)
            remaining = qty
            queue = lots.setdefault(symbol, [])
            while remaining > 1e-9 and queue:
                lot = queue[0]
                take = min(lot.qty, remaining)
                frac = take / lot.qty if lot.qty > 0 else 0.0
                lot_cost = lot.cost_total * frac
                lot_proceeds = proceeds * (take / qty) if qty > 0 else 0.0
                realized = lot_proceeds - lot_cost
                holding = _holding_bucket(lot.open_day, day)
                result.fills.append(
                    RealizedFill(
                        activity_id=activity_id,
                        symbol=symbol,
                        trade_date=day,
                        qty=take,
                        proceeds=lot_proceeds,
                        cost_basis=lot_cost,
                        realized_pl=realized,
                        holding=holding,
                        side="SELL",
                        description=desc,
                        fees=fees * (take / qty) if qty > 0 else 0.0,
                        open_day=lot.open_day,
                        asset_class="option",
                        underlying=underlying,
                    )
                )
                lot.qty -= take
                lot.cost_total -= lot_cost
                remaining -= take
                if lot.qty <= 1e-9:
                    queue.pop(0)

            if remaining > 1e-6:
                result.closes_unmatched += 1

    for sym, queue in lots.items():
        for lot in queue:
            if lot.qty > 1e-9:
                result.open_lots.append(
                    OpenLot(
                        symbol=sym,
                        qty=lot.qty,
                        cost_total=lot.cost_total,
                        open_day=lot.open_day,
                        asset_class="option",
                    )
                )
    result.open_lots.sort(key=lambda lot: (lot.symbol, lot.open_day.isoformat()))
    return result


def trade_key_for_fill(fill: RealizedFill) -> str:
    """Stable key for Notes sheet merge across regenerates."""
    aid = fill.activity_id or "na"
    open_s = fill.open_day.isoformat() if fill.open_day else "na"
    close_s = fill.trade_date.isoformat()
    qty_s = f"{fill.qty:.4f}".rstrip("0").rstrip(".")
    return f"{aid}|{fill.symbol}|{open_s}|{close_s}|{qty_s}"


def closed_row_analysis(fill: RealizedFill, *, tax_year: int) -> dict[str, Any]:
    """Analysis-pack fields for a realized close (equity or option)."""
    hold_days = 0
    if fill.open_day is not None:
        hold_days = max(0, (fill.trade_date - fill.open_day).days)
    ret_pct = None
    if fill.cost_basis and abs(fill.cost_basis) > 1e-9:
        ret_pct = round(100.0 * fill.realized_pl / abs(fill.cost_basis), 4)
    win = "win" if fill.realized_pl > 0 else ("loss" if fill.realized_pl < 0 else "flat")
    in_year = fill.trade_date.year == tax_year
    return {
        "trade_key": trade_key_for_fill(fill),
        "activity_id": fill.activity_id,
        "symbol": fill.symbol,
        "underlying": fill.underlying,
        "asset_class": fill.asset_class,
        "open_date": fill.open_day.isoformat() if fill.open_day else None,
        "close_date": fill.trade_date.isoformat(),
        "qty": round(fill.qty, 4),
        "cost_basis": round(fill.cost_basis, 2),
        "proceeds": round(fill.proceeds, 2),
        "fees": round(fill.fees, 2),
        "realized_pl": round(fill.realized_pl, 2),
        "return_pct": ret_pct,
        "hold_days": hold_days,
        "holding": fill.holding,
        "win_loss": win,
        "close_month": fill.trade_date.month if in_year else None,
        "close_weekday": fill.trade_date.strftime("%a") if in_year else None,
        "in_tax_year": in_year,
        "description": fill.description,
        "source": fill.source,
    }


def aggregate_calendar(
    ledger: LedgerResult,
    *,
    mtm_by_day: dict[str, float],
    year: int | None = None,
    month: int | None = None,
) -> dict[str, Any]:
    """Build day cells: realized primary + MTM secondary."""
    realized_by_day: dict[str, dict[str, Any]] = {}
    for fill in ledger.fills:
        key = fill.trade_date.isoformat()
        cell = realized_by_day.setdefault(
            key,
            {"date": key, "realized_pl": 0.0, "trade_count": 0, "symbols": set()},
        )
        cell["realized_pl"] += fill.realized_pl
        cell["trade_count"] += 1
        cell["symbols"].add(fill.symbol)

    days_out: list[dict[str, Any]] = []
    all_keys = set(realized_by_day) | set(mtm_by_day) | set(ledger.fees_by_day)
    for key in sorted(all_keys):
        d = date.fromisoformat(key)
        if year is not None and d.year != year:
            continue
        if month is not None and d.month != month:
            continue
        cell = realized_by_day.get(key, {"date": key, "realized_pl": 0.0, "trade_count": 0, "symbols": set()})
        days_out.append(
            {
                "date": key,
                "realized_pl": round(float(cell["realized_pl"]), 2),
                "mtm_pl": round(float(mtm_by_day.get(key, 0.0)), 2) if key in mtm_by_day else None,
                "trade_count": int(cell["trade_count"]),
                "fees": round(float(ledger.fees_by_day.get(key, 0.0)), 2),
                "symbols": sorted(cell["symbols"]),
            }
        )
    return {
        "days": days_out,
        "fills": [
            {
                "activity_id": f.activity_id,
                "symbol": f.symbol,
                "trade_date": f.trade_date.isoformat(),
                "qty": round(f.qty, 4),
                "proceeds": round(f.proceeds, 2),
                "cost_basis": round(f.cost_basis, 2),
                "realized_pl": round(f.realized_pl, 2),
                "holding": f.holding,
                "description": f.description,
                "fees": round(f.fees, 2),
                "open_day": f.open_day.isoformat() if f.open_day else None,
                "source": f.source,
            }
            for f in ledger.fills
            if (year is None or f.trade_date.year == year)
            and (month is None or f.trade_date.month == month)
        ],
        "opens_skipped": ledger.opens_skipped,
        "closes_unmatched": ledger.closes_unmatched,
        "raw_trade_count": ledger.raw_trade_count,
    }


def tax_estimate_from_ledger(
    ledger: LedgerResult,
    *,
    tax_year: int,
    federal_st_rate: float | None,
    federal_lt_rate: float | None,
    state_rate: float | None,
    rates_configured: bool,
) -> dict[str, Any]:
    """ST/LT netting + optional dollar estimate (no wash sales)."""
    st_gains = 0.0
    st_losses = 0.0
    lt_gains = 0.0
    lt_losses = 0.0
    for f in ledger.fills:
        if f.trade_date.year != tax_year:
            continue
        if f.holding == "lt":
            if f.realized_pl >= 0:
                lt_gains += f.realized_pl
            else:
                lt_losses += f.realized_pl
        else:
            if f.realized_pl >= 0:
                st_gains += f.realized_pl
            else:
                st_losses += f.realized_pl

    st_net = st_gains + st_losses
    lt_net = lt_gains + lt_losses
    # Simple netting: losses in one bucket offset the other
    total_net = st_net + lt_net
    remaining_st = st_net
    remaining_lt = lt_net
    if remaining_st < 0 and remaining_lt > 0:
        offset = min(remaining_lt, -remaining_st)
        remaining_lt -= offset
        remaining_st += offset
    elif remaining_lt < 0 and remaining_st > 0:
        offset = min(remaining_st, -remaining_lt)
        remaining_st -= offset
        remaining_lt += offset

    estimate: dict[str, Any] | None = None
    if rates_configured and federal_st_rate is not None and federal_lt_rate is not None:
        st_r = max(0.0, float(federal_st_rate))
        lt_r = max(0.0, float(federal_lt_rate))
        state_r = max(0.0, float(state_rate or 0.0))
        federal = max(0.0, remaining_st) * st_r + max(0.0, remaining_lt) * lt_r
        # State on total positive net (simplified)
        state = max(0.0, total_net) * state_r
        estimate = {
            "federal": round(federal, 2),
            "state": round(state, 2),
            "total": round(federal + state, 2),
            "federal_st_rate": st_r,
            "federal_lt_rate": lt_r,
            "state_rate": state_r,
        }

    return {
        "tax_year": tax_year,
        "disclaimer": (
            "Estimate only — not tax advice. Ignores wash sales, NIIT, deductions, "
            "and broker-adjusted basis. Compare to your 1099-B."
        ),
        "short_term": {
            "gains": round(st_gains, 2),
            "losses": round(st_losses, 2),
            "net": round(st_net, 2),
            "net_after_netting": round(remaining_st, 2),
        },
        "long_term": {
            "gains": round(lt_gains, 2),
            "losses": round(lt_losses, 2),
            "net": round(lt_net, 2),
            "net_after_netting": round(remaining_lt, 2),
        },
        "total_realized_net": round(total_net, 2),
        "rates_configured": rates_configured,
        "estimate": estimate,
        "closes_unmatched": ledger.closes_unmatched,
    }


def lookback_start_for_year(tax_year: int, today: date | None = None) -> date:
    """Fetch from prior year so open lots can match closes in tax_year."""
    today = today or datetime.now(timezone.utc).date()
    start = date(tax_year - 1, 1, 1)
    # Cap to Schwab max window (364 calendar days — 365 inclusive is rejected)
    earliest = today - timedelta(days=364)
    return max(start, earliest)
