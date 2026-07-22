"""ManualPortfolioProvider — user-entered ticker/qty rows -> PortfolioRiskState.

Ingestion adapter for the manual (non-Schwab) risk dashboard. Prices each
row from daily history (Schwab primary, yfinance fallback via
``market_data.get_daily_history_with_meta``), fails closed when any ticker
cannot be priced, and emits both the typed risk state and the
``build_portfolio_summary``-shaped dict that the static risk analytics
(`webapp._shared.build_portfolio_risk_analytics`) expect. Long-only:
negative or zero share counts are rejected. Each row requires an ownership
start date and avg cost so P/L % and ownership-period risk metrics are real.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from core.contracts.portfolio import PortfolioRiskState, Position
from core.contracts.provenance import Provenance, utc_now
from core.providers.portfolio_provider import PortfolioProvider

# Anonymous-facing cap: enough for a typical retail book while bounding the
# per-request price-history fan-out.
MAX_MANUAL_POSITIONS = 15

# Recent-close window: wide enough to cover holidays/weekends, cheap to fetch.
_PRICE_LOOKBACK_DAYS = 10

# Equity-style symbols only (covers class shares like BRK.B and BF-B).
_TICKER_RE = re.compile(r"^[A-Z0-9.\-]{1,16}$")

_ACQUIRED_FLOOR = date(1990, 1, 1)


class ManualPortfolioError(ValueError):
    """Raised when a manual book cannot be built (fail-closed on bad input)."""

    def __init__(self, message: str, *, unpriced: list[str] | None = None) -> None:
        super().__init__(message)
        self.unpriced = list(unpriced or [])


def _parse_acquired_at(raw: Any, *, ticker: str) -> date:
    if raw is None or raw == "":
        raise ManualPortfolioError(f"Ownership start date is required for {ticker}.")
    if isinstance(raw, datetime):
        value = raw.date()
    elif isinstance(raw, date):
        value = raw
    else:
        try:
            value = date.fromisoformat(str(raw)[:10])
        except ValueError as exc:
            raise ManualPortfolioError(
                f"Ownership start date for {ticker} must be YYYY-MM-DD."
            ) from exc
    today = datetime.now(timezone.utc).date()
    if value < _ACQUIRED_FLOOR:
        raise ManualPortfolioError(f"Ownership start date for {ticker} must be on or after 1990-01-01.")
    if value > today:
        raise ManualPortfolioError(f"Ownership start date for {ticker} cannot be in the future.")
    return value


def _parse_avg_cost(raw: Any, *, ticker: str) -> float:
    try:
        cost = float(raw)
    except (TypeError, ValueError):
        raise ManualPortfolioError(f"Avg cost for {ticker} is not a number.") from None
    if cost <= 0 or cost != cost:  # NaN check
        raise ManualPortfolioError(f"Avg cost for {ticker} must be positive.")
    return cost


def _clean_rows(rows: list[dict[str, Any]]) -> list[tuple[str, float, date, float]]:
    """Validate and dedupe rows; duplicate tickers merge qty (weighted cost, earliest date)."""
    merged: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for row in rows or []:
        ticker = str(row.get("ticker") or "").upper().strip()
        if not ticker:
            raise ManualPortfolioError("Every row needs a ticker symbol.")
        if not _TICKER_RE.match(ticker):
            raise ManualPortfolioError(f"'{ticker[:16]}' is not a valid ticker symbol.")
        try:
            qty = float(row.get("qty"))
        except (TypeError, ValueError):
            raise ManualPortfolioError(f"Share count for {ticker} is not a number.") from None
        if qty <= 0:
            raise ManualPortfolioError(f"Share count for {ticker} must be positive (long-only).")
        acquired_at = _parse_acquired_at(row.get("acquired_at"), ticker=ticker)
        avg_cost = _parse_avg_cost(row.get("avg_cost"), ticker=ticker)
        if ticker not in merged:
            order.append(ticker)
            merged[ticker] = {
                "qty": qty,
                "acquired_at": acquired_at,
                "avg_cost": avg_cost,
                "cost_basis": avg_cost * qty,
            }
        else:
            prev = merged[ticker]
            new_qty = float(prev["qty"]) + qty
            prev["cost_basis"] = float(prev["cost_basis"]) + avg_cost * qty
            prev["qty"] = new_qty
            prev["avg_cost"] = float(prev["cost_basis"]) / new_qty if new_qty else avg_cost
            prev["acquired_at"] = min(prev["acquired_at"], acquired_at)
    if not merged:
        raise ManualPortfolioError("At least one position is required.")
    if len(merged) > MAX_MANUAL_POSITIONS:
        raise ManualPortfolioError(f"Manual portfolios are capped at {MAX_MANUAL_POSITIONS} distinct tickers.")
    return [
        (ticker, float(merged[ticker]["qty"]), merged[ticker]["acquired_at"], float(merged[ticker]["avg_cost"]))
        for ticker in order
    ]


class ManualPortfolioProvider:
    domain = "portfolio_manual"

    @staticmethod
    def price_rows(
        rows: list[dict[str, Any]],
        *,
        skill_dir: Path | str | None = None,
        auth: Any = None,
    ) -> list[dict[str, Any]]:
        """Fetch a recent close for each row and derive market value / P/L.

        Fail-closed: if any ticker cannot be priced the whole book is
        rejected (``ManualPortfolioError.unpriced`` lists the offenders) —
        a partially priced book would produce silently wrong weights.
        """
        from market_data import get_daily_history_with_meta

        cleaned = _clean_rows(rows)
        priced: list[dict[str, Any]] = []
        unpriced: list[str] = []
        for ticker, qty, acquired_at, avg_cost in cleaned:
            last: float | None = None
            meta: dict[str, Any] = {}
            try:
                df, meta = get_daily_history_with_meta(
                    ticker, days=_PRICE_LOOKBACK_DAYS, auth=auth, skill_dir=skill_dir
                )
                if df is not None and not df.empty and "close" in df.columns:
                    closes = df["close"].dropna()
                    if not closes.empty:
                        value = float(closes.iloc[-1])
                        if value > 0:
                            last = value
            except Exception:
                last = None
            if last is None:
                unpriced.append(ticker)
                continue
            pl_pct = ((last - avg_cost) / avg_cost) * 100.0
            unrealized_pnl = (last - avg_cost) * qty
            priced.append(
                {
                    "symbol": ticker,
                    "qty": qty,
                    "last": round(last, 4),
                    "market_value": round(last * qty, 2),
                    "avg_cost": round(avg_cost, 4),
                    "pl_pct": round(pl_pct, 4),
                    "unrealized_pnl": round(unrealized_pnl, 2),
                    "acquired_at": acquired_at,
                    "price_provider": meta.get("provider"),
                }
            )
        if unpriced:
            raise ManualPortfolioError(
                "Could not price: " + ", ".join(unpriced) + ". Fix or remove these tickers and retry.",
                unpriced=unpriced,
            )
        return priced

    @staticmethod
    def build(
        rows: list[dict[str, Any]],
        *,
        cash: float | None = None,
        skill_dir: Path | str | None = None,
        auth: Any = None,
        sector_lookup: Any = None,
    ) -> tuple[PortfolioRiskState, dict[str, Any]]:
        """Price a manual book and return (risk state, portfolio-summary dict).

        The summary dict matches ``webapp._shared.build_portfolio_summary``
        output so ``build_portfolio_risk_analytics`` and
        ``build_portfolio_risk_dashboard`` consume it unchanged.
        """
        priced = ManualPortfolioProvider.price_rows(rows, skill_dir=skill_dir, auth=auth)
        cash_value = max(0.0, float(cash or 0.0))
        total_mv = round(sum(p["market_value"] for p in priced), 2)
        equity = round(total_mv + cash_value, 2)

        positions: list[Position] = []
        for p in priced:
            sector_etf = None
            if sector_lookup is not None:
                try:
                    sector_etf = sector_lookup(p["symbol"])
                except Exception:
                    sector_etf = None
            weight = (p["market_value"] / equity) if equity else None
            positions.append(
                Position(
                    ticker=p["symbol"],
                    qty=p["qty"],
                    avg_price=p["avg_cost"],
                    market_value=p["market_value"],
                    unrealized_pnl=p["unrealized_pnl"],
                    sector_etf=sector_etf,
                    weight_pct=round(weight * 100, 4) if weight is not None else None,
                    acquired_at=p["acquired_at"],
                )
            )

        state = PortfolioRiskState(
            equity=equity,
            cash=cash_value,
            positions=positions,
            exposure=PortfolioProvider._exposure(positions, equity),
            concentration=PortfolioProvider._concentration(positions, equity),
            provenance=Provenance(source="manual", as_of=utc_now(), confidence="medium"),
        )

        summary = {
            "account_count": 0,
            "positions_count": len(priced),
            "total_market_value": total_mv,
            "cash": cash_value,
            "equity": equity,
            "source": "manual",
            "positions": [
                {
                    "symbol": p["symbol"],
                    "qty": int(p["qty"]) if float(p["qty"]).is_integer() else p["qty"],
                    "market_value": p["market_value"],
                    "day_pl": 0.0,
                    "avg_cost": p["avg_cost"],
                    "last": p["last"],
                    "pl_pct": p["pl_pct"],
                    "unrealized_pnl": p["unrealized_pnl"],
                    "acquired_at": p["acquired_at"].isoformat(),
                }
                for p in sorted(priced, key=lambda r: r["market_value"], reverse=True)
            ],
        }
        return state, summary
