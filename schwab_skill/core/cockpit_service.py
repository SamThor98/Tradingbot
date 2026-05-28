"""Cockpit service: build the four lane payloads from normalized DTOs.

Shared by local (`webapp.main`) and SaaS (`webapp.main_saas` / tenant) surfaces
so the cockpit stays at parity by construction. Functions accept already-fetched
inputs (scan diagnostics/signals, account status, pending-trade rows) and return
plain dicts (DTO ``model_dump``) ready for ``ApiResponse`` envelopes.

The provider/contract layer does the normalization; this module orchestrates and
applies the Phase 1 pre-trade gates. It performs no Schwab I/O itself — callers
fetch (mirroring the existing /api/portfolio, /api/sectors routes) and pass data
in, which keeps these functions unit-testable offline.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from core import post_fill_risk, pretrade_gates, scan_delta
from core.contracts.execution import ExecutionQuality, ExecutionState, OrderIntent
from core.contracts.provenance import Provenance, utc_now
from core.providers import (
    ExecutionProvider,
    MarketContextProvider,
    OptionsProvider,
    PortfolioProvider,
    SymbolIntelProvider,
)


def _f(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------- #
# Lane 1: Market Regime
# --------------------------------------------------------------------------- #
def build_market(diagnostics: dict[str, Any] | None) -> dict[str, Any]:
    snap = MarketContextProvider.from_diagnostics(diagnostics)
    return snap.model_dump(mode="json")


# --------------------------------------------------------------------------- #
# Lane 2: Ranked Opportunities
# --------------------------------------------------------------------------- #
def build_opportunities(
    signals: list[dict[str, Any]] | None,
    *,
    shortlist: list[dict[str, Any]] | None = None,
    skill_dir: Path | None = None,
    include_filtered: bool = True,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """Normalize scan signals into decision cards with pre-trade gates applied.

    ``signals`` are the tradeable, ranked rows. ``shortlist`` (optional) adds
    near-miss / filtered candidates so operators can see "why not" cards.
    """
    rows: list[dict[str, Any]] = list(signals or [])
    if include_filtered and shortlist:
        seen = {str((s or {}).get("ticker", "")).upper() for s in rows}
        for s in shortlist:
            if str((s or {}).get("ticker", "")).upper() not in seen:
                rows.append(s)

    mode = pretrade_gates.gates_mode(skill_dir)
    cards: list[dict[str, Any]] = []
    for sig in rows:
        if not isinstance(sig, dict):
            continue
        card = SymbolIntelProvider.normalize_signal(sig)
        if mode != "off":
            card.pre_trade = pretrade_gates.from_signal(sig, skill_dir=skill_dir)
        cards.append(card.model_dump(mode="json"))

    cards.sort(key=lambda c: (c.get("rank") or {}).get("rank_score") or -1e9, reverse=True)
    if limit is not None:
        cards = cards[: max(0, int(limit))]
    return cards


# --------------------------------------------------------------------------- #
# Lane 3: Portfolio Risk + Exposure
# --------------------------------------------------------------------------- #
def build_portfolio(
    account_status: dict[str, Any] | None,
    *,
    sector_lookup: Callable[[str], str | None] | None = None,
    stop_lookup: Callable[[str], bool] | None = None,
    skill_dir: Path | None = None,
) -> dict[str, Any]:
    state = PortfolioProvider.normalize_account(account_status or {}, sector_lookup=sector_lookup)
    out = state.model_dump(mode="json")
    # Phase 3 post-fill risk controls (stop integrity, exposure/concentration drift).
    out["risk_flags"] = post_fill_risk.assess(out, stop_lookup=stop_lookup, skill_dir=skill_dir)
    return out


# --------------------------------------------------------------------------- #
# Lane 4: Execution Blotter
# --------------------------------------------------------------------------- #
def build_blotter(
    pending_rows: list[dict[str, Any]] | None,
    *,
    order_results: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in pending_rows or []:
        if isinstance(row, dict):
            out.append(ExecutionProvider.from_pending_trade(row).model_dump(mode="json"))
    for res in order_results or []:
        if isinstance(res, dict):
            out.append(ExecutionProvider.from_order_result(res).model_dump(mode="json"))
    return out


# --------------------------------------------------------------------------- #
# One-click order-intent preview (read-only; no broker POST)
# --------------------------------------------------------------------------- #
def build_order_intent_preview(
    *,
    ticker: str,
    qty: int | None,
    price: float | None,
    side: str = "BUY",
    order_type: str = "MARKET",
    signal: dict[str, Any] | None = None,
    bid: float | None = None,
    ask: float | None = None,
    quote_age_sec: float | None = None,
    skill_dir: Path | None = None,
) -> dict[str, Any]:
    """Build a staged :class:`ExecutionState` previewing an order intent.

    Read-only: computes spread / expected price / pre-trade verdict without
    contacting the broker. This mirrors what ``place_order`` would evaluate at
    its guardrail boundary, so the operator sees the verdict before approving.
    """
    sig = signal or {}
    px = _f(price) or _f(sig.get("price"))
    checks = pretrade_gates.compute_checks(
        price=px,
        bid=bid,
        ask=ask,
        quote_age_sec=quote_age_sec,
        avg_vol_50=_f(sig.get("avg_vol_50")),
        event_risk=sig.get("event_risk") if isinstance(sig.get("event_risk"), dict) else None,
        skill_dir=skill_dir,
    )

    # Expected price: mid when we have a 2-sided quote, else last/price.
    b, a = _f(bid), _f(ask)
    expected = (a + b) / 2.0 if (a and b) else px

    state = ExecutionState(
        ticker=str(ticker).upper(),
        side=side.upper(),
        qty=_f(qty),
        state="staged",
        intent=OrderIntent(side=side.upper(), order_type=order_type.upper(), limit_price=px),
        quality=ExecutionQuality(
            expected_price=round(expected, 4) if expected is not None else None,
            spread_bps_at_submit=checks.spread_bps,
        ),
        reason=None if checks.tradeable else "; ".join(checks.blockers) or "pre_trade_block",
        shadow=True,
        provenance=Provenance(source="computed", as_of=utc_now(), confidence="high"),
    )
    out = state.model_dump(mode="json")
    out["pre_trade"] = checks.model_dump(mode="json")
    out["gates_mode"] = pretrade_gates.gates_mode(skill_dir)
    return out


# --------------------------------------------------------------------------- #
# Phase 2: stateful deltas, adaptive watchlists, movers, options intel
# --------------------------------------------------------------------------- #
def _signals_of(scan: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(scan, dict):
        return []
    rows = scan.get("signals")
    return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []


def build_deltas(
    prev_scan: dict[str, Any] | None,
    curr_scan: dict[str, Any] | None,
) -> dict[str, Any]:
    """What changed since last cycle. Accepts last_scan-shaped payloads."""
    return scan_delta.compute_delta(_signals_of(prev_scan), _signals_of(curr_scan))


def build_watchlists(
    prev_scan: dict[str, Any] | None,
    curr_scan: dict[str, Any] | None,
    *,
    skill_dir: Path | None = None,
) -> dict[str, Any]:
    """Adaptive watchlists: breaking out now / setup improving / risk rising."""
    return scan_delta.adaptive_watchlists(_signals_of(prev_scan), _signals_of(curr_scan), skill_dir=skill_dir)


def build_movers(movers_json: dict[str, Any] | None) -> dict[str, Any]:
    """Normalize a Schwab /movers payload into gainers/losers/most_active."""
    return MarketContextProvider.normalize_movers(movers_json)


def build_symbol_options(chain_json: dict[str, Any] | None) -> dict[str, Any]:
    """Normalize a Schwab /chains payload into OptionsIntel."""
    return OptionsProvider.normalize_chain(chain_json).model_dump(mode="json")


def build_execution_quality(
    exec_metrics_summary: dict[str, Any] | None,
    blotter: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Execution-quality attribution: lifecycle counts + slippage stats.

    ``exec_metrics_summary`` is the dict from
    ``execution_persistence.get_execution_safety_summary`` (event counters).
    ``blotter`` is the normalized ExecutionState list.
    """
    events = (exec_metrics_summary or {}).get("events", {}) or {}
    rows = blotter or []

    # Lifecycle state counts.
    state_counts: dict[str, int] = {}
    slippages: list[float] = []
    spreads: list[float] = []
    reprices: list[int] = []
    for r in rows:
        st = str(r.get("state") or "unknown")
        state_counts[st] = state_counts.get(st, 0) + 1
        q = r.get("quality") or {}
        if q.get("realized_slippage_bps") is not None:
            slippages.append(float(q["realized_slippage_bps"]))
        if q.get("spread_bps_at_submit") is not None:
            spreads.append(float(q["spread_bps_at_submit"]))
        if q.get("reprice_count") is not None:
            reprices.append(int(q["reprice_count"]))

    def _avg(xs: list[float]) -> float | None:
        return round(sum(xs) / len(xs), 2) if xs else None

    evaluated = int(events.get("exec_quality_evaluated", 0) or 0)
    blocked = int(events.get("exec_quality_live_blocked", 0) or 0)
    would_block = int(events.get("exec_quality_shadow_would_block", 0) or 0)
    prefer_limit = int(events.get("exec_quality_shadow_would_prefer_limit", 0) or 0)

    return {
        "lifecycle_counts": state_counts,
        "slippage": {
            "avg_realized_bps": _avg(slippages),
            "max_realized_bps": round(max(slippages), 2) if slippages else None,
            "samples": len(slippages),
        },
        "spread": {"avg_bps": _avg(spreads), "samples": len(spreads)},
        "reprice": {
            "avg_count": _avg([float(x) for x in reprices]),
            "samples": len(reprices),
        },
        "policy_events": {
            "evaluated": evaluated,
            "live_blocked": blocked,
            "shadow_would_block": would_block,
            "shadow_would_prefer_limit": prefer_limit,
        },
    }
