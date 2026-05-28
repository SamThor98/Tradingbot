"""PortfolioProvider — Schwab accounts payload -> PortfolioRiskState.

``normalize_account`` is a pure transform of the ``get_account_status`` dict
(``{"accounts": [...]}``) so it is testable offline. ``build`` wraps the live
fetch and is only invoked by routes once providers are promoted past ``off``.
"""

from __future__ import annotations

from typing import Any

from core.contracts.portfolio import (
    ConcentrationStats,
    ExposureBreakdown,
    PortfolioRiskState,
    Position,
)
from core.contracts.provenance import Provenance, utc_now


def _f(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


class PortfolioProvider:
    domain = "portfolio"

    @staticmethod
    def normalize_account(
        account_status: dict[str, Any],
        *,
        sector_lookup: Any = None,
    ) -> PortfolioRiskState:
        """Transform a ``get_account_status`` payload into a risk state.

        ``sector_lookup`` (optional) maps ticker -> sector ETF; when omitted,
        sector exposure is left empty rather than guessed.
        """
        status = account_status or {}
        accounts = status.get("accounts") or []
        acct = accounts[0] if accounts else {}

        # Schwab nests balances/positions under securitiesAccount.
        sec = acct.get("securitiesAccount", acct) if isinstance(acct, dict) else {}
        balances = sec.get("currentBalances") or sec.get("initialBalances") or {}
        equity = _f(balances.get("liquidationValue")) or _f(balances.get("equity"))
        cash = _f(balances.get("cashBalance")) or _f(balances.get("totalCash"))
        buying_power = _f(balances.get("buyingPower"))

        raw_positions = sec.get("positions") or []
        positions: list[Position] = []
        for p in raw_positions:
            if not isinstance(p, dict):
                continue
            instrument = p.get("instrument") or {}
            ticker = str(instrument.get("symbol") or "").upper()
            long_qty = _f(p.get("longQuantity")) or 0.0
            short_qty = _f(p.get("shortQuantity")) or 0.0
            qty = long_qty - short_qty
            mv = _f(p.get("marketValue"))
            sector_etf = None
            if sector_lookup is not None and ticker:
                try:
                    sector_etf = sector_lookup(ticker)
                except Exception:
                    sector_etf = None
            weight = (mv / equity) if (mv is not None and equity) else None
            positions.append(
                Position(
                    ticker=ticker,
                    qty=qty,
                    avg_price=_f(p.get("averagePrice")),
                    market_value=mv,
                    unrealized_pnl=_f(p.get("currentDayProfitLoss")),
                    sector_etf=sector_etf,
                    weight_pct=round(weight * 100, 4) if weight is not None else None,
                )
            )

        exposure = PortfolioProvider._exposure(positions, equity)
        concentration = PortfolioProvider._concentration(positions, equity)

        return PortfolioRiskState(
            equity=equity,
            cash=cash,
            buying_power=buying_power,
            positions=positions,
            exposure=exposure,
            concentration=concentration,
            provenance=Provenance(source="schwab", as_of=utc_now(), confidence="high"),
        )

    @staticmethod
    def _exposure(positions: list[Position], equity: float | None) -> ExposureBreakdown:
        by_sector: dict[str, float] = {}
        gross = 0.0
        net = 0.0
        largest = 0.0
        for pos in positions:
            mv = pos.market_value or 0.0
            gross += abs(mv)
            net += mv
            largest = max(largest, abs(mv))
            if pos.sector_etf:
                by_sector[pos.sector_etf] = by_sector.get(pos.sector_etf, 0.0) + mv
        if equity:
            by_sector = {k: round(v / equity * 100, 4) for k, v in by_sector.items()}
            return ExposureBreakdown(
                by_sector=by_sector,
                gross_pct=round(gross / equity * 100, 4),
                net_pct=round(net / equity * 100, 4),
                largest_position_pct=round(largest / equity * 100, 4),
            )
        return ExposureBreakdown(by_sector={})

    @staticmethod
    def _concentration(positions: list[Position], equity: float | None) -> ConcentrationStats:
        if not equity:
            return ConcentrationStats()
        weights = sorted((abs(p.market_value or 0.0) / equity for p in positions), reverse=True)
        if not weights:
            return ConcentrationStats(top1_pct=0.0, top5_pct=0.0, herfindahl=0.0)
        return ConcentrationStats(
            top1_pct=round(weights[0] * 100, 4),
            top5_pct=round(sum(weights[:5]) * 100, 4),
            herfindahl=round(sum(w * w for w in weights), 6),
        )

    def build(self, skill_dir: Any, auth: Any = None) -> PortfolioRiskState:  # pragma: no cover
        """Live fetch + normalize. Not invoked while providers mode is off."""
        from execution import get_account_status

        status = (
            get_account_status(skill_dir=skill_dir)
            if auth is None
            else get_account_status(auth=auth, skill_dir=skill_dir)
        )
        if isinstance(status, str):
            return PortfolioRiskState(
                provenance=Provenance(source="schwab", confidence="low", is_stale=True, stale_reason=status[:200])
            )
        sector_lookup = None
        try:
            from sector_strength import get_ticker_sector_etf

            sector_lookup = lambda t: get_ticker_sector_etf(t, skill_dir=skill_dir)  # noqa: E731
        except Exception:
            sector_lookup = None
        return self.normalize_account(status, sector_lookup=sector_lookup)
