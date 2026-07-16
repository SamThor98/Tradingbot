"""ManualPortfolioProvider — user-entered ticker/qty rows -> PortfolioRiskState.

Ingestion adapter for the manual (non-Schwab) risk dashboard. Prices each
row from daily history (Schwab primary, yfinance fallback via
``market_data.get_daily_history_with_meta``), fails closed when any ticker
cannot be priced, and emits both the typed risk state and the
``build_portfolio_summary``-shaped dict that the static risk analytics
(`webapp._shared.build_portfolio_risk_analytics`) expect. Long-only:
negative or zero share counts are rejected.
"""

from __future__ import annotations

import re
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


class ManualPortfolioError(ValueError):
    """Raised when a manual book cannot be built (fail-closed on bad input)."""

    def __init__(self, message: str, *, unpriced: list[str] | None = None) -> None:
        super().__init__(message)
        self.unpriced = list(unpriced or [])


def _clean_rows(rows: list[dict[str, Any]]) -> list[tuple[str, float]]:
    """Validate and dedupe (ticker, qty) rows; duplicate tickers merge qty."""
    merged: dict[str, float] = {}
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
        if ticker not in merged:
            order.append(ticker)
        merged[ticker] = merged.get(ticker, 0.0) + qty
    if not merged:
        raise ManualPortfolioError("At least one position is required.")
    if len(merged) > MAX_MANUAL_POSITIONS:
        raise ManualPortfolioError(f"Manual portfolios are capped at {MAX_MANUAL_POSITIONS} distinct tickers.")
    return [(ticker, merged[ticker]) for ticker in order]


class ManualPortfolioProvider:
    domain = "portfolio_manual"

    @staticmethod
    def price_rows(
        rows: list[dict[str, Any]],
        *,
        skill_dir: Path | str | None = None,
        auth: Any = None,
    ) -> list[dict[str, Any]]:
        """Fetch a recent close for each row and derive market value.

        Fail-closed: if any ticker cannot be priced the whole book is
        rejected (``ManualPortfolioError.unpriced`` lists the offenders) —
        a partially priced book would produce silently wrong weights.
        """
        from market_data import get_daily_history_with_meta

        cleaned = _clean_rows(rows)
        priced: list[dict[str, Any]] = []
        unpriced: list[str] = []
        for ticker, qty in cleaned:
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
            priced.append(
                {
                    "symbol": ticker,
                    "qty": qty,
                    "last": round(last, 4),
                    "market_value": round(last * qty, 2),
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
        ``build_portfolio_risk_dashboard`` consume it unchanged. Manual books
        have no broker day-P/L or cost basis, so those fields are zeroed.
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
                    avg_price=p["last"],
                    market_value=p["market_value"],
                    sector_etf=sector_etf,
                    weight_pct=round(weight * 100, 4) if weight is not None else None,
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
                    "avg_cost": p["last"],
                    "last": p["last"],
                    "pl_pct": 0.0,
                }
                for p in sorted(priced, key=lambda r: r["market_value"], reverse=True)
            ],
        }
        return state, summary
