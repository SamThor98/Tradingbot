"""Orchestrate companyfacts ingest, cache-first DCF, and assumption recompute."""

from __future__ import annotations

import logging
from dataclasses import replace
from pathlib import Path
from typing import Any

from core.companyfacts.client import (
    CompanyfactsFetchError,
    fetch_companyfacts,
    load_cached_facts,
    load_cached_meta,
    lookup_cik,
)
from core.companyfacts.extract import extract_statements
from core.companyfacts.model import (
    DEFAULT_TERMINAL_GROWTH,
    DEFAULT_WACC,
    build_dcf_section,
    implied_growth,
    implied_wacc,
    price_model,
    sensitivity_table,
    statements_to_inputs,
)
from core.companyfacts.settings import load_companyfacts_config
from core.event_probability.valuation import ValuationInputs

LOG = logging.getLogger(__name__)


def _skill_dir(skill_dir: Path | None) -> Path:
    return Path(skill_dir) if skill_dir is not None else Path(__file__).resolve().parents[2]


def _overlay_price(ticker: str, skill_dir: Path | None) -> tuple[float, str]:
    """Best-effort live quote. Never required for the model itself."""
    try:
        from market_data import extract_schwab_last_price, get_current_quote

        quote = get_current_quote(ticker, skill_dir=_skill_dir(skill_dir))
        last = extract_schwab_last_price(quote) if isinstance(quote, dict) else None
        if last is not None and float(last) > 0:
            return float(last), "schwab.quote"
    except Exception as exc:
        LOG.warning("Pricing quote overlay failed for %s: %s", ticker, exc)
    return 0.0, ""


def _provenance(statements: Any, cache_meta: dict[str, Any] | None, from_cache: bool) -> dict[str, Any]:
    return {
        "source": "sec.companyfacts",
        "cik": getattr(statements, "cik", ""),
        "entity_name": getattr(statements, "entity_name", ""),
        "as_of": statements.as_of() if statements is not None else "",
        "filed": statements.filed() if statements is not None else "",
        "from_cache": bool(from_cache),
        "fetched_at": (cache_meta or {}).get("fetched_at"),
        "concepts": dict(getattr(statements, "source_concepts", {}) or {}),
    }


def _payload_from_statements(
    statements: Any,
    *,
    ticker: str,
    price: float,
    price_source: str,
    cache_meta: dict[str, Any] | None,
    from_cache: bool,
    growth: float | None = None,
    wacc: float = DEFAULT_WACC,
    terminal_growth: float = DEFAULT_TERMINAL_GROWTH,
    last_fcf: float | None = None,
    net_debt: float | None = None,
    shares: float | None = None,
) -> dict[str, Any]:
    status = "ok"
    reason = ""
    inputs: ValuationInputs | None = None
    try:
        inputs = statements_to_inputs(
            statements, price=price, growth=growth, wacc=wacc, terminal_growth=terminal_growth
        )
        if last_fcf is not None:
            inputs = replace(inputs, last_fcf=float(last_fcf))
        if net_debt is not None:
            inputs = replace(inputs, net_debt=float(net_debt))
        if shares is not None:
            inputs = replace(inputs, shares=float(shares))
        if price:
            inputs = replace(inputs, price=float(price))
    except ValueError as exc:
        status = "model_unavailable"
        reason = str(exc)

    priced = price_model(inputs) if inputs is not None else {}
    dcf = {}
    if inputs is not None:
        dcf = {
            **priced,
            "growth_rate": inputs.growth,
            "wacc": inputs.wacc,
            "terminal_growth": inputs.terminal_growth,
            "last_fcf": inputs.last_fcf,
            "net_debt": inputs.net_debt,
            "shares_outstanding": inputs.shares,
            "revenue_history": [{"year": p.end[:4], "revenue": float(p.val)} for p in statements.revenue],
            "fcf_history": [{"year": p.end[:4], "fcf": float(p.val)} for p in statements.fcf],
            "sensitivity": sensitivity_table(inputs),
            "source": "sec.companyfacts",
        }

    return {
        "status": status,
        "reason": reason,
        "ticker": ticker,
        "warnings": list(statements.errors),
        "provenance": _provenance(statements, cache_meta, from_cache),
        "price_source": price_source,
        "inputs": inputs.to_dict() if inputs is not None else None,
        "dcf": dcf,
        "implied_growth": implied_growth(inputs) if inputs is not None else None,
        "implied_wacc": implied_wacc(inputs) if inputs is not None else None,
        "assumptions": [
            "5-year FCF projection plus Gordon growth terminal value.",
            "Statements from SEC companyfacts (XBRL); WACC default 10%, terminal growth 2.5%.",
            "Recompute is local — sliders do not call EDGAR, Yahoo, or Finnhub.",
            "Does not change scan scores, ranking, sizing, or advisory.",
        ],
        "config": load_companyfacts_config().to_dict(),
    }


def ingest_ticker(
    ticker: str,
    *,
    skill_dir: Path | None = None,
    force: bool = False,
    fetcher: Any = None,
    price: float | None = None,
) -> dict[str, Any]:
    """Fetch (or reuse cache) and return a pricing payload."""
    cfg = load_companyfacts_config(skill_dir)
    symbol = str(ticker or "").strip().upper()
    if not cfg.enabled:
        return {
            "status": "disabled",
            "ticker": symbol,
            "reason": "COMPANYFACTS_ENABLED is false",
            "warnings": [],
            "provenance": {"source": "sec.companyfacts"},
            "dcf": {},
            "inputs": None,
            "config": cfg.to_dict(),
        }
    try:
        facts = fetch_companyfacts(
            symbol,
            skill_dir=skill_dir,
            cache_hours=cfg.cache_hours,
            timeout_sec=cfg.timeout_sec,
            fetcher=fetcher,
            force=force,
        )
    except CompanyfactsFetchError as exc:
        LOG.warning("companyfacts ingest failed for %s: %s", symbol, exc.detail)
        return {
            "status": "error",
            "ticker": symbol,
            "reason": f"{exc.code}:{exc.detail}",
            "warnings": [exc.code],
            "provenance": {"source": "sec.companyfacts"},
            "dcf": {},
            "inputs": None,
            "config": cfg.to_dict(),
        }
    cache_info = facts.pop("_cache", {}) if isinstance(facts.get("_cache"), dict) else {}
    statements = extract_statements(facts, ticker=symbol)
    live_price, price_source = (float(price), "override") if price is not None else _overlay_price(symbol, skill_dir)
    if price is None and live_price <= 0:
        price_source = ""
    meta = {
        "fetched_at": cache_info.get("fetched_at"),
        "cik": cache_info.get("cik") or statements.cik,
    }
    payload = _payload_from_statements(
        statements,
        ticker=symbol,
        price=live_price,
        price_source=price_source,
        cache_meta=meta,
        from_cache=bool(cache_info.get("from_cache")),
    )
    if payload["status"] == "ok" and statements.errors:
        payload["status"] = "degraded"
    return payload


def load_inputs_for_ticker(
    ticker: str,
    *,
    skill_dir: Path | None = None,
    price: float | None = None,
) -> ValuationInputs | None:
    """Cache-only load. Returns None when facts are not on disk (no network)."""
    symbol = str(ticker or "").strip().upper()
    cfg = load_companyfacts_config(skill_dir)
    if not cfg.enabled:
        return None
    try:
        cik = lookup_cik(symbol, skill_dir, cached_only=True)
    except Exception:
        return None
    facts = load_cached_facts(cik, skill_dir)
    meta = load_cached_meta(cik, skill_dir)
    if not facts or not meta or not meta.get("ok"):
        return None
    statements = extract_statements(facts, ticker=symbol)
    live_price = float(price) if price is not None else _overlay_price(symbol, skill_dir)[0]
    try:
        return statements_to_inputs(statements, price=live_price)
    except ValueError:
        return None


def pricing_payload(
    ticker: str,
    *,
    skill_dir: Path | None = None,
    ingest_on_miss: bool = True,
    fetcher: Any = None,
    price: float | None = None,
) -> dict[str, Any]:
    """Panel payload. Uses disk cache; optionally ingests once on miss."""
    symbol = str(ticker or "").strip().upper()
    cfg = load_companyfacts_config(skill_dir)
    if not cfg.enabled:
        return ingest_ticker(symbol, skill_dir=skill_dir, fetcher=fetcher, price=price)

    cached = None
    try:
        cik = lookup_cik(symbol, skill_dir, fetcher=fetcher, cached_only=True)
        facts = load_cached_facts(cik, skill_dir)
        meta = load_cached_meta(cik, skill_dir)
        if facts and meta and meta.get("ok"):
            statements = extract_statements(facts, ticker=symbol)
            live_price, price_source = (
                (float(price), "override") if price is not None else _overlay_price(symbol, skill_dir)
            )
            cached = _payload_from_statements(
                statements,
                ticker=symbol,
                price=live_price,
                price_source=price_source,
                cache_meta=meta,
                from_cache=True,
            )
    except CompanyfactsFetchError:
        cached = None
    except Exception:
        cached = None

    if cached is not None:
        if cached.get("status") == "ok" and cached.get("warnings"):
            cached["status"] = "degraded"
        return cached
    if ingest_on_miss:
        return ingest_ticker(symbol, skill_dir=skill_dir, fetcher=fetcher, price=price)
    return {
        "status": "not_ingested",
        "ticker": symbol,
        "reason": "No cached companyfacts. Click Refresh fundamentals to fetch the SEC XBRL document once.",
        "warnings": [],
        "provenance": {"source": "sec.companyfacts", "from_cache": False},
        "dcf": {},
        "inputs": None,
        "config": cfg.to_dict(),
    }


def recompute_payload(
    ticker: str,
    *,
    skill_dir: Path | None = None,
    growth: float | None = None,
    wacc: float | None = None,
    terminal_growth: float | None = None,
    last_fcf: float | None = None,
    net_debt: float | None = None,
    shares: float | None = None,
    price: float | None = None,
) -> dict[str, Any]:
    """CPU-only reprice from cached statements. Never calls EDGAR."""
    symbol = str(ticker or "").strip().upper()
    cfg = load_companyfacts_config(skill_dir)
    try:
        cik = lookup_cik(symbol, skill_dir, cached_only=True)
    except CompanyfactsFetchError as exc:
        return {
            "status": "not_ingested",
            "ticker": symbol,
            "reason": f"{exc.code}:{exc.detail}",
            "dcf": {},
            "inputs": None,
            "config": cfg.to_dict(),
        }
    facts = load_cached_facts(cik, skill_dir)
    meta = load_cached_meta(cik, skill_dir)
    if not facts or not meta or not meta.get("ok"):
        return {
            "status": "not_ingested",
            "ticker": symbol,
            "reason": "No cached companyfacts. Refresh fundamentals before recomputing.",
            "dcf": {},
            "inputs": None,
            "config": cfg.to_dict(),
        }
    statements = extract_statements(facts, ticker=symbol)
    live_price, price_source = (float(price), "override") if price is not None else _overlay_price(symbol, skill_dir)
    payload = _payload_from_statements(
        statements,
        ticker=symbol,
        price=live_price,
        price_source=price_source,
        cache_meta=meta,
        from_cache=True,
        growth=growth,
        wacc=DEFAULT_WACC if wacc is None else float(wacc),
        terminal_growth=DEFAULT_TERMINAL_GROWTH if terminal_growth is None else float(terminal_growth),
        last_fcf=last_fcf,
        net_debt=net_debt,
        shares=shares,
    )
    payload["recomputed"] = True
    return payload


def try_build_dcf_section(ticker: str, *, skill_dir: Path | None = None) -> Any | None:
    """Report-stack helper: DCFSection from cache, or None to fall through."""
    symbol = str(ticker or "").strip().upper()
    cfg = load_companyfacts_config(skill_dir)
    if not cfg.enabled:
        return None
    try:
        cik = lookup_cik(symbol, skill_dir, cached_only=True)
    except Exception:
        return None
    facts = load_cached_facts(cik, skill_dir)
    meta = load_cached_meta(cik, skill_dir)
    if not facts or not meta or not meta.get("ok"):
        return None
    statements = extract_statements(facts, ticker=symbol)
    price, _src = _overlay_price(symbol, skill_dir)
    section = build_dcf_section(statements, price=price)
    if section.error:
        return None
    return section
