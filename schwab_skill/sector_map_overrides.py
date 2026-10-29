"""Ticker → sector ETF overrides from sponsor holdings.

The bundled map is a point-in-time classification: Select Sector SPDR
membership plus iShares IJH (S&P 400) and IJR (S&P 600) holdings, each as of
2026-10-27. It is today's sector assignment, not a historical one, and is
not applied unless ``SECTOR_MAP_OVERRIDE_MODE`` is ``shadow`` or ``live``.

``off`` (the default) leaves the yfinance sector-name map in charge, including
yfinance's ``Consumer Defensive`` label, which stays unresolved. ``shadow``
keeps that ETF and records disagreements (holdings map, or that staples
alias). ``live`` returns the sponsor ETF, and applies ``Consumer Defensive``
→ XLP when the name is not in the holdings map.
"""

from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path
from typing import Any

LOG = logging.getLogger(__name__)

_MAP_PATH = Path(__file__).resolve().parent / "data" / "sector_map_overrides.json"
_SAMPLE_CAP = 20

# yfinance says "Consumer Defensive". The base SECTOR_TO_ETF map does not
# (it has the plural "consumer defensives" / "consumer staples" only). This
# alias is gated: off ignores it, shadow logs it, live applies it.
GATED_YAHOO_SECTOR_TO_ETF: dict[str, str] = {
    "consumer defensive": "XLP",
}

_YAHOO_SECTOR_SEEN: dict[str, str] = {}


def note_yahoo_sector(ticker: str, yahoo_sector: str | None) -> None:
    """Remember a yfinance sector label for a later shadow comparison."""
    sector = str(yahoo_sector or "").strip().lower()
    symbol = str(ticker or "").upper().strip()
    if symbol and sector:
        _YAHOO_SECTOR_SEEN[symbol] = sector


def _seen_yahoo_sector(ticker: str) -> str | None:
    return _YAHOO_SECTOR_SEEN.get(str(ticker or "").upper().strip())


def _gated_alias_etf(yahoo_sector: str | None, mode: str) -> str | None:
    if mode == "off":
        return None
    sector = str(yahoo_sector or "").strip().lower()
    if not sector:
        return None
    return GATED_YAHOO_SECTOR_TO_ETF.get(sector)


def _override_etf(ticker: str, mode: str, yahoo_sector: str | None) -> str | None:
    mapped = lookup_sector_map_override(ticker)
    if mapped:
        return mapped
    return _gated_alias_etf(yahoo_sector, mode)


@lru_cache(maxsize=1)
def _load_etf_by_ticker() -> dict[str, str]:
    try:
        payload = json.loads(_MAP_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        LOG.warning("Sector map overrides unavailable: %s", exc)
        return {}
    raw = payload.get("etf_by_ticker") if isinstance(payload, dict) else None
    if not isinstance(raw, dict):
        LOG.warning("Sector map overrides missing etf_by_ticker")
        return {}
    out: dict[str, str] = {}
    for ticker, etf in raw.items():
        if not isinstance(ticker, str) or not isinstance(etf, str):
            continue
        symbol = ticker.upper().strip()
        fund = etf.upper().strip()
        if symbol and fund:
            out[symbol] = fund
    return out


def sector_map_override_as_of() -> str | None:
    try:
        payload = json.loads(_MAP_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if isinstance(payload, dict) and isinstance(payload.get("as_of"), str):
        return payload["as_of"]
    return None


def lookup_sector_map_override(ticker: str) -> str | None:
    """Return the sponsor-file sector ETF for ``ticker``, or None when unlisted.

    The file is a point-in-time membership list, not a historical sector.
    """
    key = str(ticker or "").upper().strip()
    if not key:
        return None
    mapping = _load_etf_by_ticker()
    hit = mapping.get(key)
    if hit:
        return hit
    dashed = key.replace(".", "-")
    if dashed != key and mapping.get(dashed):
        return mapping[dashed]
    dotted = key.replace("-", ".")
    if dotted != key and mapping.get(dotted):
        return mapping[dotted]
    return None


def apply_sector_map_override(
    ticker: str,
    base_etf: str | None,
    skill_dir: Path | None = None,
    *,
    yahoo_sector: str | None = None,
) -> str | None:
    """Return the ETF the rest of the pipeline should use.

    ``off`` and ``shadow`` return ``base_etf``. ``live`` returns the sponsor
    ETF when the name is listed, otherwise the gated yfinance alias
    (``Consumer Defensive`` → XLP).
    """
    from config import get_sector_map_override_mode

    mode = get_sector_map_override_mode(skill_dir)
    if mode != "live":
        return base_etf
    sector = yahoo_sector if yahoo_sector is not None else _seen_yahoo_sector(ticker)
    override = _override_etf(ticker, mode, sector)
    if override:
        return override
    return base_etf


def describe_sector_map_override(
    ticker: str,
    effective_etf: str | None,
    skill_dir: Path | None = None,
    *,
    yahoo_sector: str | None = None,
) -> dict[str, Any]:
    """Compare the ETF just returned with the sponsor override, if any.

    In ``shadow``, ``effective_etf`` is still the yfinance result, so a
    disagreement means live mode would reassign the name (holdings map, or
    the gated ``Consumer Defensive`` alias). In ``live``, ``applied`` means
    the returned ETF came from that override. The comparison is point-in-time
    against today's sponsor files, not a historical sector.
    """
    from config import get_sector_map_override_mode

    mode = get_sector_map_override_mode(skill_dir)
    sector = yahoo_sector if yahoo_sector is not None else _seen_yahoo_sector(ticker)
    override = _override_etf(ticker, mode, sector)
    disagree = bool(mode == "shadow" and override and override != effective_etf)
    applied = bool(mode == "live" and override)
    base_etf: str | None
    if mode == "shadow" or mode == "off":
        base_etf = effective_etf
    else:
        base_etf = None if applied else effective_etf
    return {
        "mode": mode,
        "ticker": str(ticker or "").upper().strip(),
        "base_etf": base_etf,
        "override_etf": override,
        "effective_etf": effective_etf,
        "disagree": disagree,
        "applied": applied,
        "as_of": sector_map_override_as_of(),
    }


def accumulate_sector_map_override_view(
    sink: dict[str, Any],
    view: dict[str, Any],
    *,
    sample_cap: int = _SAMPLE_CAP,
) -> None:
    """Fold one per-ticker view into scan or backtest diagnostics."""
    mode = str(view.get("mode") or "off")
    sink["sector_map_override_mode"] = mode
    if mode == "off":
        return
    sink["sector_map_override_evaluated"] = int(sink.get("sector_map_override_evaluated") or 0) + 1
    if view.get("disagree") or view.get("applied"):
        key = "sector_map_override_disagree" if view.get("disagree") else "sector_map_override_applied"
        sink[key] = int(sink.get(key) or 0) + 1
        samples = sink.get("sector_map_override_samples")
        if not isinstance(samples, list):
            samples = []
            sink["sector_map_override_samples"] = samples
        if len(samples) < sample_cap:
            samples.append(
                {
                    "ticker": view.get("ticker"),
                    "base_etf": view.get("base_etf"),
                    "override_etf": view.get("override_etf"),
                    "effective_etf": view.get("effective_etf"),
                }
            )
