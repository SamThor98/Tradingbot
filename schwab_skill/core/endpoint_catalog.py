"""Canonical Schwab endpoint catalog (Phase 0 capability map).

Single machine-readable source of truth for which Schwab surfaces exist, how
they are classified, their session, latency budgets, fallback rules, and
degraded-mode behavior. Mirrors the ``schwab-endpoint-catalog`` skill doc.

Classification engines:
- ``market_context``  — regime, breadth, movers, volatility, price history
- ``symbol_intel``    — quotes, options chains, fundamentals/search
- ``portfolio_risk``  — accounts, positions, transactions
- ``execution``       — order lifecycle (place/get/replace/cancel/preview)
- ``reliability``     — OAuth / token health
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

EngineClass = Literal["market_context", "symbol_intel", "portfolio_risk", "execution", "reliability"]
Session = Literal["market", "account", "both", "none"]
Status = Literal["live", "gap"]


@dataclass(frozen=True)
class EndpointSpec:
    key: str  # stable id, e.g. "marketdata.quotes"
    engine: EngineClass
    method: str  # GET | POST | PUT | DELETE | URL
    path: str  # Schwab API path (templated)
    session: Session
    status: Status
    phase: str  # phase introduced / planned: "live" | "P1" | "P2" | "P3"
    p95_latency_budget_ms: int | None = None
    fallback: str | None = None  # human-readable fallback rule
    degraded_mode: str | None = None  # behavior when stale/circuit-open
    callers: tuple[str, ...] = field(default_factory=tuple)  # known call sites
    notes: str | None = None


# Base host: https://api.schwabapi.com
_CATALOG: tuple[EndpointSpec, ...] = (
    # --- reliability -------------------------------------------------------
    EndpointSpec(
        key="oauth.token",
        engine="reliability",
        method="POST",
        path="/v1/oauth/token",
        session="both",
        status="live",
        phase="live",
        p95_latency_budget_ms=2000,
        fallback="re-auth via run_dual_auth (local) / dashboard OAuth (SaaS)",
        degraded_mode="token health -> warn/critical/expired; block account ops on expiry",
        callers=("schwab_auth.exchange_code", "schwab_auth.refresh_tokens"),
    ),
    # --- market_context ----------------------------------------------------
    EndpointSpec(
        key="marketdata.pricehistory.daily",
        engine="market_context",
        method="GET",
        path="/marketdata/v1/pricehistory",
        session="market",
        status="live",
        phase="live",
        p95_latency_budget_ms=1500,
        fallback="yfinance daily OHLCV unless SCHWAB_ONLY_DATA",
        degraded_mode="circuit open -> fast-fail then yfinance; stale bars rejected in Stage A",
        callers=("market_data.get_daily_history_with_meta", "backtest._fetch_history_schwab"),
    ),
    EndpointSpec(
        key="marketdata.pricehistory.minute",
        engine="market_context",
        method="GET",
        path="/marketdata/v1/pricehistory",
        session="market",
        status="gap",
        phase="P2",
        p95_latency_budget_ms=1500,
        fallback="none planned (intraday only used for freshness confirm)",
        degraded_mode="skip intraday confirm; fall back to daily-bar logic",
        notes="frequencyType=minute; intraday freshness + breakout timing",
    ),
    EndpointSpec(
        key="marketdata.movers",
        engine="market_context",
        method="GET",
        path="/marketdata/v1/movers/{index}",
        session="market",
        status="gap",
        phase="P2",
        p95_latency_budget_ms=1500,
        fallback="empty movers block in MarketSnapshot",
        degraded_mode="omit movers lane content; keep regime",
        callers=("market_data.get_market_movers_with_status",),
        notes="implemented, flag-gated by MARKET_MOVERS_MODE (default off)",
    ),
    EndpointSpec(
        key="marketdata.markets.hours",
        engine="market_context",
        method="GET",
        path="/marketdata/v1/markets",
        session="market",
        status="gap",
        phase="P2",
        p95_latency_budget_ms=1500,
        fallback="local market-hours table",
        degraded_mode="assume RTH from local calendar",
    ),
    # --- symbol_intel ------------------------------------------------------
    EndpointSpec(
        key="marketdata.quotes",
        engine="symbol_intel",
        method="GET",
        path="/marketdata/v1/quotes",
        session="market",
        status="live",
        phase="live",
        p95_latency_budget_ms=1200,
        fallback="Polygon last-trade/prev-close; yfinance for guardrail pricing",
        degraded_mode="mark quote stale when timestamp older than DATA_QUOTE_MAX_AGE_SEC",
        callers=("market_data.get_current_quote_with_status",),
    ),
    EndpointSpec(
        key="marketdata.options.chains",
        engine="symbol_intel",
        method="GET",
        path="/marketdata/v1/chains",
        session="market",
        status="gap",
        phase="P2",
        p95_latency_budget_ms=2500,
        fallback="omit options_intel block on SymbolDecisionCard",
        degraded_mode="card renders without IV/skew; confidence unaffected",
        callers=("market_data.get_options_chain_with_status",),
        notes="implemented, flag-gated by OPTIONS_INTEL_MODE (default off)",
    ),
    EndpointSpec(
        key="marketdata.instruments",
        engine="symbol_intel",
        method="GET",
        path="/marketdata/v1/instruments",
        session="market",
        status="gap",
        phase="P2",
        p95_latency_budget_ms=1500,
        fallback="yfinance / Finnhub fundamentals (current behavior)",
        degraded_mode="keep existing fundamental fallbacks",
        callers=("market_data.get_instrument_with_status",),
        notes="implemented, flag-gated by INSTRUMENTS_MODE (default off)",
    ),
    # --- portfolio_risk ----------------------------------------------------
    EndpointSpec(
        key="trader.accounts",
        engine="portfolio_risk",
        method="GET",
        path="/trader/v1/accounts",
        session="account",
        status="live",
        phase="live",
        p95_latency_budget_ms=1500,
        fallback="none (account data has no third-party fallback)",
        degraded_mode="RISK_FAIL_CLOSED_ON_DATA_OUTAGE blocks new entries on outage",
        callers=("execution.get_account_status", "execution.GuardrailWrapper._get_accounts"),
    ),
    EndpointSpec(
        key="trader.accounts.transactions",
        engine="portfolio_risk",
        method="GET",
        path="/trader/v1/accounts/{hash}/transactions",
        session="account",
        status="live",
        phase="live",
        p95_latency_budget_ms=2000,
        fallback="Book calendar/tax omit realized rows when fetch fails",
        degraded_mode="fail closed for realized P/L; MTM still uses local snapshots",
        callers=("core.schwab_transactions.fetch_trades_for_skill", "webapp.routes.book"),
        notes="Book feature: realized P/L calendar + tax estimate (FIFO)",
    ),
    # --- execution ---------------------------------------------------------
    EndpointSpec(
        key="trader.orders.place",
        engine="execution",
        method="POST",
        path="/trader/v1/accounts/{hash}/orders",
        session="account",
        status="live",
        phase="live",
        p95_latency_budget_ms=2500,
        fallback="none (orders never fall back to third parties)",
        degraded_mode="EXECUTION_SHADOW_MODE simulates; auto-throttle on degraded data (P3)",
        callers=("execution.place_order", "execution._post_order_with_refresh"),
    ),
    EndpointSpec(
        key="trader.orders.get",
        engine="execution",
        method="GET",
        path="/trader/v1/accounts/{hash}/orders/{orderId}",
        session="account",
        status="live",
        phase="live",
        p95_latency_budget_ms=1500,
        fallback="none",
        degraded_mode="poll backoff; treat unknown as working until terminal",
        callers=("execution._get_order_with_refresh", "order_monitor._poll_order_status"),
    ),
    EndpointSpec(
        key="trader.orders.replace",
        engine="execution",
        method="PUT",
        path="/trader/v1/accounts/{hash}/orders/{orderId}",
        session="account",
        status="live",
        phase="live",
        p95_latency_budget_ms=2500,
        fallback="none",
        degraded_mode="reprice loop halts on degraded data (P3 policy)",
        callers=("execution._replace_order_with_refresh",),
    ),
    EndpointSpec(
        key="trader.orders.cancel",
        engine="execution",
        method="DELETE",
        path="/trader/v1/accounts/{hash}/orders/{orderId}",
        session="account",
        status="live",
        phase="live",
        p95_latency_budget_ms=2000,
        fallback="none",
        degraded_mode="always allowed (risk-reducing)",
        callers=("execution._cancel_order_with_refresh",),
    ),
    EndpointSpec(
        key="trader.orders.preview",
        engine="execution",
        method="POST",
        path="/trader/v1/accounts/{hash}/previewOrder",
        session="account",
        status="gap",
        phase="P1",
        p95_latency_budget_ms=2000,
        fallback="local order-intent simulation via guardrail read-only checks",
        degraded_mode="fall back to local simulated preview",
        notes="powers one-click order-intent preview before approval",
    ),
)

_BY_KEY: dict[str, EndpointSpec] = {e.key: e for e in _CATALOG}


def all_endpoints() -> tuple[EndpointSpec, ...]:
    return _CATALOG


def get_endpoint(key: str) -> EndpointSpec | None:
    return _BY_KEY.get(key)


def by_engine(engine: EngineClass) -> list[EndpointSpec]:
    return [e for e in _CATALOG if e.engine == engine]


def by_status(status: Status) -> list[EndpointSpec]:
    return [e for e in _CATALOG if e.status == status]


def live_keys() -> list[str]:
    return [e.key for e in _CATALOG if e.status == "live"]


def gap_keys() -> list[str]:
    return [e.key for e in _CATALOG if e.status == "gap"]


def coverage_summary() -> dict[str, dict[str, int]]:
    """Per-engine live vs gap counts, for the capability-map dashboard."""
    out: dict[str, dict[str, int]] = {}
    for e in _CATALOG:
        bucket = out.setdefault(e.engine, {"live": 0, "gap": 0})
        bucket[e.status] += 1
    return out
