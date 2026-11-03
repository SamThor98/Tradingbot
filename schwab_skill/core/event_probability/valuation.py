"""Valuation integration.

Prices scenarios with the same 5-year FCF + perpetuity model the report stack
uses (``full_report._build_dcf`` / ``core.fundamental_workbook``), so an
event-adjusted value is directly comparable to the DCF an operator already sees.

:func:`dcf_per_share` mirrors ``core.fundamental_workbook._recompute_dcf_cache``
rather than importing it: that module fails at import time on this tree because
it expects a newer ``core.xlsx_workbook`` API (``Cell``, ``SheetSpec``) that
does not exist yet. Importing it would make this layer inherit an unrelated
break. Consolidate once that module is repaired.

Valuation inputs are captured once into a frozen :class:`ValuationInputs` so
every scenario in a run is priced off identical fundamentals. That freezing is
also what makes a historical replay reproducible -- see the point-in-time
caveat in ``docs/EVENT_PROBABILITY_LAYER.md``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from typing import Any

from core.event_probability._util import safe_float
from core.event_probability.schema import ValuationUnavailableError

LOG = logging.getLogger(__name__)

# How each supported fundamental/valuation variable enters the model.
#   growth           -> additive delta on the DCF FCF growth rate
#   wacc             -> additive delta on the discount rate
#   terminal_growth  -> additive delta on the perpetuity growth rate
#   value_multiplier -> multiplicative scalar on per-share equity value
FUNDAMENTAL_VARIABLES: dict[str, str] = {
    "revenue_growth": "growth",
    "fcf_growth": "growth",
    "operating_margin": "growth",
    "wacc": "wacc",
    "discount_rate": "wacc",
    "terminal_growth": "terminal_growth",
    "terminal_multiple": "value_multiplier",
    "price_multiple": "value_multiplier",
}

# Keep the perpetuity formula well-defined: WACC must stay above terminal growth.
MIN_WACC_SPREAD = 0.005
MIN_GROWTH = -0.90
MAX_GROWTH = 1.00


@dataclass(frozen=True, slots=True)
class ValuationInputs:
    """Frozen DCF inputs for one ticker at one point in time."""

    ticker: str
    last_fcf: float
    growth: float
    wacc: float
    terminal_growth: float
    net_debt: float
    shares: float
    price: float
    source: str = "report.dcf"

    def to_dict(self) -> dict[str, Any]:
        return {
            "ticker": self.ticker,
            "last_fcf": self.last_fcf,
            "growth": self.growth,
            "wacc": self.wacc,
            "terminal_growth": self.terminal_growth,
            "net_debt": self.net_debt,
            "shares": self.shares,
            "price": self.price,
            "source": self.source,
        }


PROJECTION_YEARS = 5


def dcf_per_share(
    *,
    last_fcf: float,
    growth: float,
    wacc: float,
    terminal_growth: float,
    net_debt: float,
    shares: float,
) -> float:
    """Per-share equity value from a 5-year FCF projection plus perpetuity."""
    if shares <= 0:
        return 0.0
    discount = max(wacc, terminal_growth + MIN_WACC_SPREAD)
    cash_flow = float(last_fcf)
    pv_sum = 0.0
    for year in range(1, PROJECTION_YEARS + 1):
        cash_flow *= 1.0 + growth
        pv_sum += cash_flow / ((1.0 + discount) ** year)
    terminal_value = cash_flow * (1.0 + terminal_growth) / (discount - terminal_growth)
    pv_terminal = terminal_value / ((1.0 + discount) ** PROJECTION_YEARS)
    return ((pv_sum + pv_terminal) - float(net_debt)) / float(shares)


def value_equity(inputs: ValuationInputs) -> float:
    """Per-share intrinsic value for the given inputs."""
    if inputs.shares <= 0:
        raise ValuationUnavailableError(f"shares_outstanding_unavailable:{inputs.ticker}")
    return dcf_per_share(
        last_fcf=float(inputs.last_fcf),
        growth=float(inputs.growth),
        wacc=float(inputs.wacc),
        terminal_growth=float(inputs.terminal_growth),
        net_debt=float(inputs.net_debt),
        shares=float(inputs.shares),
    )


def apply_shocks(inputs: ValuationInputs, shocks: list[tuple[str, float]]) -> tuple[ValuationInputs, float]:
    """Fold assumption deltas into the inputs.

    Returns the shocked inputs plus a multiplicative scalar for variables that
    act on the resulting value (multiples) rather than on a cash-flow input.
    """
    growth = float(inputs.growth)
    wacc = float(inputs.wacc)
    terminal_growth = float(inputs.terminal_growth)
    multiplier = 1.0

    for variable, delta in shocks:
        kind = FUNDAMENTAL_VARIABLES.get(str(variable))
        if kind is None:
            LOG.warning("Ignoring unsupported fundamental variable %r", variable)
            continue
        value = safe_float(delta)
        if value is None:
            LOG.warning("Ignoring non-numeric impact for %r: %r", variable, delta)
            continue
        if kind == "growth":
            growth += value
        elif kind == "wacc":
            wacc += value
        elif kind == "terminal_growth":
            terminal_growth += value
        elif kind == "value_multiplier":
            multiplier *= max(0.0, 1.0 + value)

    growth = max(MIN_GROWTH, min(MAX_GROWTH, growth))
    terminal_growth = max(-0.05, min(0.10, terminal_growth))
    wacc = max(terminal_growth + MIN_WACC_SPREAD, min(0.60, wacc))

    shocked = replace(inputs, growth=growth, wacc=wacc, terminal_growth=terminal_growth)
    return shocked, multiplier


def value_with_shocks(inputs: ValuationInputs, shocks: list[tuple[str, float]]) -> float:
    """Per-share value after applying a set of assumption deltas."""
    shocked, multiplier = apply_shocks(inputs, shocks)
    return value_equity(shocked) * multiplier


def last_fcf_from_dcf(dcf: dict[str, Any]) -> float | None:
    """Most recent actual FCF from a report DCF payload.

    Falls back to de-growing the first projected year when history is absent,
    matching how the workbook derives the same figure.
    """
    history = dcf.get("fcf_history")
    if isinstance(history, list) and history and isinstance(history[-1], dict):
        value = safe_float(history[-1].get("fcf"))
        if value is not None:
            return value
    projected = dcf.get("projected_fcf")
    growth = safe_float(dcf.get("growth_rate")) or 0.0
    if isinstance(projected, list) and projected and isinstance(projected[0], dict):
        year_one = safe_float(projected[0].get("fcf"))
        if year_one is not None and (1.0 + growth) != 0:
            return year_one / (1.0 + growth)
    return None


def inputs_from_dcf(ticker: str, dcf: dict[str, Any]) -> ValuationInputs:
    """Build frozen inputs from a report DCF section payload."""
    if not isinstance(dcf, dict) or dcf.get("error"):
        raise ValuationUnavailableError(f"dcf_unavailable:{ticker}:{(dcf or {}).get('error', 'missing')}")

    last_fcf = last_fcf_from_dcf(dcf)
    shares = safe_float(dcf.get("shares_outstanding"))
    price = safe_float(dcf.get("current_price"))
    if last_fcf is None or last_fcf == 0.0:
        raise ValuationUnavailableError(f"fcf_unavailable:{ticker}")
    if shares is None or shares <= 0:
        raise ValuationUnavailableError(f"shares_outstanding_unavailable:{ticker}")
    if price is None or price <= 0:
        raise ValuationUnavailableError(f"price_unavailable:{ticker}")

    growth = safe_float(dcf.get("growth_rate"))
    wacc = safe_float(dcf.get("wacc"))
    terminal_growth = safe_float(dcf.get("terminal_growth"))
    net_debt = safe_float(dcf.get("net_debt"))

    return ValuationInputs(
        ticker=str(ticker).strip().upper(),
        last_fcf=float(last_fcf),
        growth=0.05 if growth is None else float(growth),
        wacc=0.10 if wacc is None else float(wacc),
        terminal_growth=0.025 if terminal_growth is None else float(terminal_growth),
        net_debt=0.0 if net_debt is None else float(net_debt),
        shares=float(shares),
        price=float(price),
    )


def load_valuation_inputs(ticker: str) -> ValuationInputs:
    """Default loader: SEC companyfacts cache, then live report DCF.

    Companyfacts is not point-in-time across restatements; historical replays
    must still inject frozen ``ValuationInputs``.
    """
    from dataclasses import asdict

    try:
        from core.companyfacts.service import ingest_ticker, load_inputs_for_ticker

        loaded = load_inputs_for_ticker(ticker)
        if loaded is not None:
            return loaded
        ingest_ticker(ticker)
        loaded = load_inputs_for_ticker(ticker)
        if loaded is not None:
            return loaded
    except Exception as exc:
        LOG.warning("companyfacts valuation loader failed for %s: %s", ticker, exc)

    from full_report import _build_dcf

    section = _build_dcf(str(ticker).strip().upper())
    return inputs_from_dcf(ticker, asdict(section))
