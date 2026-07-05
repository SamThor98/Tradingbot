from __future__ import annotations

from typing import Any


def _safe_float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _has_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, set, dict)):
        return len(value) > 0
    return True


def _set_if_missing(
    target: dict[str, Any],
    sources: dict[str, str],
    key: str,
    value: Any,
    source: str,
) -> None:
    if _has_value(target.get(key)) or not _has_value(value):
        return
    target[key] = value
    sources[key] = source


def _mark_existing_sources(values: dict[str, Any], prefix: str) -> dict[str, str]:
    return {key: prefix for key, value in values.items() if _has_value(value)}


def merge_fundamental_metrics(
    finnhub_snapshot: dict[str, Any],
    raw_report: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, str]]:
    """Merge dossier metrics with report-stack fallbacks and source labels."""

    metrics = dict(finnhub_snapshot.get("metrics") or {})
    sources = _mark_existing_sources(metrics, "finnhub")
    health = raw_report.get("health") or {}
    dcf = raw_report.get("dcf") or {}
    technical = raw_report.get("technical") or {}
    comps = raw_report.get("comps") or {}

    fallback_map = {
        "current_ratio_quarterly": ("current_ratio", health, "report.health"),
        "debt_to_equity_quarterly": ("debt_to_equity", health, "report.health"),
        "interest_coverage_ttm": ("interest_coverage", health, "report.health"),
        "roe_ttm": ("roe", health, "report.health"),
        "operating_margin_ttm": ("operating_margin", health, "report.health"),
        "52week_high": ("high_52w", technical, "report.technical"),
        "52week_low": ("low_52w", technical, "report.technical"),
        "pe_ttm": ("median_pe", comps, "report.comps"),
        "ps_ttm": ("median_ps", comps, "report.comps"),
    }
    for metric_key, (fallback_key, block, source) in fallback_map.items():
        _set_if_missing(metrics, sources, metric_key, block.get(fallback_key), source)

    growth_rate = _safe_float(dcf.get("growth_rate"))
    if growth_rate is not None:
        # DCF growth_rate is stored as a decimal in the report stack.
        _set_if_missing(metrics, sources, "revenue_growth_5y", growth_rate * 100.0, "report.dcf")

    return metrics, sources


def merge_company_profile(
    finnhub_snapshot: dict[str, Any],
    raw_report: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, str]]:
    profile = dict(finnhub_snapshot.get("profile") or {})
    sources = _mark_existing_sources(profile, "finnhub")
    technical = raw_report.get("technical") or {}

    _set_if_missing(profile, sources, "exchange", "n/a", "report_stack")
    _set_if_missing(profile, sources, "finnhub_industry", technical.get("sector_etf"), "report.technical")
    _set_if_missing(profile, sources, "currency", "USD", "report_stack")
    return profile, sources


def merge_peers(finnhub_peers: Any, raw_report: dict[str, Any]) -> tuple[list[str], str]:
    peers: list[str] = []
    if isinstance(finnhub_peers, list):
        peers = [str(peer).strip().upper() for peer in finnhub_peers if str(peer or "").strip()]
    if peers:
        return peers, "finnhub"

    comps = raw_report.get("comps") or {}
    comp_rows = comps.get("peers") or []
    if isinstance(comp_rows, list):
        for row in comp_rows:
            if isinstance(row, dict):
                ticker = str(row.get("ticker") or row.get("symbol") or "").strip().upper()
            else:
                ticker = str(row or "").strip().upper()
            if ticker:
                peers.append(ticker)
    return peers, "report.comps" if peers else "unavailable"


def merge_research_snapshot(
    finnhub_snapshot: dict[str, Any],
    raw_report: dict[str, Any],
) -> dict[str, Any]:
    """Return a Finnhub-shaped snapshot enriched with report-stack fallbacks."""

    snapshot = dict(finnhub_snapshot or {})
    metrics, metric_sources = merge_fundamental_metrics(snapshot, raw_report)
    profile, profile_sources = merge_company_profile(snapshot, raw_report)
    peers, peer_source = merge_peers(snapshot.get("peers"), raw_report)

    quote = dict(snapshot.get("quote") or {})
    quote_sources = _mark_existing_sources(quote, "finnhub")
    technical = raw_report.get("technical") or {}
    _set_if_missing(quote, quote_sources, "current", technical.get("current_price"), "report.technical")

    snapshot["metrics"] = metrics
    snapshot["profile"] = profile
    snapshot["peers"] = peers
    snapshot["quote"] = quote
    snapshot["merged_sources"] = {
        "metrics": metric_sources,
        "profile": profile_sources,
        "quote": quote_sources,
        "peers": peer_source,
    }
    return snapshot
