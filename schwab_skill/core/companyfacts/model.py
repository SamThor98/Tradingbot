"""Price a 5-year FCF DCF from extracted companyfacts statements."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from core.companyfacts.extract import ExtractedStatements
from core.event_probability.valuation import (
    MIN_GROWTH,
    MIN_WACC_SPREAD,
    ValuationInputs,
    dcf_per_share,
    value_equity,
)

DEFAULT_WACC = 0.10
DEFAULT_TERMINAL_GROWTH = 0.025
MAX_GROWTH = 0.30
MIN_GROWTH_CLAMP = -0.10
SENSITIVITY_WACC = (0.08, 0.09, 0.10, 0.11, 0.12)
SENSITIVITY_TG = (0.02, 0.025, 0.03)


def cagr(values: list[float]) -> float:
    positive = [v for v in values if v > 0]
    if len(positive) < 2:
        return 0.05
    start, end = positive[0], positive[-1]
    periods = len(positive) - 1
    if start <= 0 or periods <= 0:
        return 0.05
    raw = (end / start) ** (1.0 / periods) - 1.0
    return max(MIN_GROWTH_CLAMP, min(MAX_GROWTH, float(raw)))


def statements_to_inputs(
    statements: ExtractedStatements,
    *,
    price: float = 0.0,
    growth: float | None = None,
    wacc: float = DEFAULT_WACC,
    terminal_growth: float = DEFAULT_TERMINAL_GROWTH,
) -> ValuationInputs:
    last_fcf = statements.last_fcf()
    shares = float(statements.shares.val) if statements.shares is not None else 0.0
    if last_fcf is None or last_fcf == 0.0:
        raise ValueError("fcf_unavailable")
    if shares <= 0:
        raise ValueError("shares_outstanding_unavailable")
    hist = [float(p.val) for p in statements.fcf]
    growth_rate = 0.05 if growth is None else float(growth)
    if growth is None:
        growth_rate = cagr(hist)
        if last_fcf <= 0:
            last_fcf = abs(last_fcf)
            growth_rate = max(growth_rate, 0.05)
    return ValuationInputs(
        ticker=statements.ticker,
        last_fcf=float(last_fcf),
        growth=float(growth_rate),
        wacc=float(wacc),
        terminal_growth=float(terminal_growth),
        net_debt=float(statements.net_debt()),
        shares=float(shares),
        price=float(price or 0.0),
        source="sec.companyfacts",
    )


def price_model(inputs: ValuationInputs) -> dict[str, Any]:
    """Project FCF, terminal value, MOS. Pure; no I/O."""
    discount = max(float(inputs.wacc), float(inputs.terminal_growth) + MIN_WACC_SPREAD)
    growth = max(MIN_GROWTH, min(1.0, float(inputs.growth)))
    tg = float(inputs.terminal_growth)
    last_fcf = float(inputs.last_fcf)
    net_debt = float(inputs.net_debt)
    shares = float(inputs.shares)
    price = float(inputs.price)

    projected: list[dict[str, float]] = []
    pv_sum = 0.0
    cf = last_fcf
    for year in range(1, 6):
        cf *= 1.0 + growth
        pv = cf / ((1.0 + discount) ** year) if (1.0 + discount) != 0 else 0.0
        pv_sum += pv
        projected.append({"year": float(year), "fcf": round(cf, 2), "pv": round(pv, 2)})

    terminal_value = 0.0
    pv_terminal = 0.0
    if discount > tg:
        terminal_value = cf * (1.0 + tg) / (discount - tg)
        pv_terminal = terminal_value / ((1.0 + discount) ** 5)
    enterprise = pv_sum + pv_terminal
    equity = enterprise - net_debt
    intrinsic = equity / shares if shares > 0 else 0.0
    mos = ((intrinsic - price) / price * 100.0) if price > 0 else 0.0
    return {
        "projected_fcf": projected,
        "terminal_value": terminal_value,
        "enterprise_value": enterprise,
        "equity_value": equity,
        "intrinsic_value": intrinsic,
        "current_price": price,
        "margin_of_safety": mos,
        "wacc_used": discount,
    }


def sensitivity_table(inputs: ValuationInputs) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for wacc in SENSITIVITY_WACC:
        row: dict[str, Any] = {"wacc": wacc}
        for tg in SENSITIVITY_TG:
            shocked = ValuationInputs(
                ticker=inputs.ticker,
                last_fcf=inputs.last_fcf,
                growth=inputs.growth,
                wacc=float(wacc),
                terminal_growth=float(tg),
                net_debt=inputs.net_debt,
                shares=inputs.shares,
                price=inputs.price,
                source=inputs.source,
            )
            try:
                row[f"tg_{tg}"] = round(value_equity(shocked), 2)
            except Exception:
                row[f"tg_{tg}"] = None
        rows.append(row)
    return rows


def implied_growth(inputs: ValuationInputs, *, target_price: float | None = None) -> float | None:
    """Binary-search FCF growth so intrinsic value matches price. Deterministic."""
    price = float(target_price if target_price is not None else inputs.price)
    if price <= 0 or inputs.shares <= 0 or inputs.last_fcf == 0:
        return None
    lo, hi = MIN_GROWTH_CLAMP, MAX_GROWTH
    for _ in range(48):
        mid = (lo + hi) / 2.0
        iv = dcf_per_share(
            last_fcf=inputs.last_fcf,
            growth=mid,
            wacc=inputs.wacc,
            terminal_growth=inputs.terminal_growth,
            net_debt=inputs.net_debt,
            shares=inputs.shares,
        )
        if iv > price:
            hi = mid
        else:
            lo = mid
    return round((lo + hi) / 2.0, 6)


def implied_wacc(inputs: ValuationInputs, *, target_price: float | None = None) -> float | None:
    price = float(target_price if target_price is not None else inputs.price)
    if price <= 0 or inputs.shares <= 0:
        return None
    lo = float(inputs.terminal_growth) + MIN_WACC_SPREAD
    hi = 0.40
    for _ in range(48):
        mid = (lo + hi) / 2.0
        iv = dcf_per_share(
            last_fcf=inputs.last_fcf,
            growth=inputs.growth,
            wacc=mid,
            terminal_growth=inputs.terminal_growth,
            net_debt=inputs.net_debt,
            shares=inputs.shares,
        )
        # Higher WACC -> lower IV. If IV is still above price, discount more.
        if iv > price:
            lo = mid
        else:
            hi = mid
    return round((lo + hi) / 2.0, 6)


def build_dcf_section(
    statements: ExtractedStatements,
    *,
    price: float = 0.0,
    growth: float | None = None,
    wacc: float = DEFAULT_WACC,
    terminal_growth: float = DEFAULT_TERMINAL_GROWTH,
) -> Any:
    """Build the report-stack ``DCFSection`` from extracted statements."""
    from full_report import DCFSection

    sec = DCFSection()
    sec.source = "sec.companyfacts"
    sec.cik = statements.cik
    sec.as_of = statements.as_of()
    sec.filed = statements.filed()
    if statements.errors and (
        "fcf_unavailable" in statements.errors or "insufficient_fcf_history" in statements.errors
    ):
        sec.error = ";".join(statements.errors)
        return sec

    try:
        inputs = statements_to_inputs(
            statements, price=price, growth=growth, wacc=wacc, terminal_growth=terminal_growth
        )
    except ValueError as exc:
        sec.error = str(exc)
        return sec

    priced = price_model(inputs)
    sec.growth_rate = inputs.growth
    sec.wacc = inputs.wacc
    sec.terminal_growth = inputs.terminal_growth
    sec.last_fcf = inputs.last_fcf
    sec.net_debt = inputs.net_debt
    sec.shares_outstanding = inputs.shares
    sec.projected_fcf = priced["projected_fcf"]
    sec.terminal_value = priced["terminal_value"]
    sec.enterprise_value = priced["enterprise_value"]
    sec.intrinsic_value = priced["intrinsic_value"]
    sec.current_price = priced["current_price"]
    sec.margin_of_safety = priced["margin_of_safety"]
    sec.sensitivity = sensitivity_table(inputs)
    sec.revenue_history = [{"year": p.end[:4], "revenue": float(p.val)} for p in statements.revenue]
    sec.fcf_history = [{"year": p.end[:4], "fcf": float(p.val)} for p in statements.fcf]
    if "shares_outstanding_unavailable" in statements.errors:
        sec.error = "Shares outstanding unavailable."
    return sec


def inputs_to_dict(inputs: ValuationInputs) -> dict[str, Any]:
    return asdict(inputs)
