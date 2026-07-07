"""Ticker -> country/currency resolution for portfolio FX and country risk.

Resolves company domicile via the Finnhub ``stock/profile2`` endpoint with a
long-lived local JSON cache (profiles rarely change). Fails soft: when the
Finnhub API key is missing or a request fails, the ticker simply resolves to
an empty mapping and the caller reports it as unresolved in ``data_quality``.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

LOG = logging.getLogger(__name__)

CACHE_FILE = ".portfolio_country_cache.json"
CACHE_TTL_HOURS = 24.0 * 14  # profiles are near-static; two weeks

# ISO alpha-2 -> display name for countries commonly seen in equity portfolios.
COUNTRY_DISPLAY_NAMES: dict[str, str] = {
    "US": "United States",
    "CA": "Canada",
    "CN": "China",
    "KZ": "Kazakhstan",
    "KR": "South Korea",
    "JP": "Japan",
    "GB": "United Kingdom",
    "DE": "Germany",
    "FR": "France",
    "NL": "Netherlands",
    "CH": "Switzerland",
    "IE": "Ireland",
    "IL": "Israel",
    "IN": "India",
    "BR": "Brazil",
    "MX": "Mexico",
    "AR": "Argentina",
    "TW": "Taiwan",
    "HK": "Hong Kong",
    "SG": "Singapore",
    "AU": "Australia",
    "SE": "Sweden",
    "DK": "Denmark",
    "NO": "Norway",
    "FI": "Finland",
    "ES": "Spain",
    "IT": "Italy",
    "BE": "Belgium",
    "LU": "Luxembourg",
    "GR": "Greece",
    "ZA": "South Africa",
    "ID": "Indonesia",
    "VN": "Vietnam",
    "TR": "Turkey",
    "AE": "United Arab Emirates",
    "SA": "Saudi Arabia",
    "CL": "Chile",
    "CO": "Colombia",
    "PE": "Peru",
    "PH": "Philippines",
    "TH": "Thailand",
    "MY": "Malaysia",
    "NZ": "New Zealand",
    "AT": "Austria",
    "PT": "Portugal",
    "PL": "Poland",
    "CZ": "Czech Republic",
    "HU": "Hungary",
    "BM": "Bermuda",
    "KY": "Cayman Islands",
    "UY": "Uruguay",
}

# Developed-market ISO codes get a milder default FX shock than the broad EM
# uniform shock. Overridable via RISK_FX_SHOCK_BY_COUNTRY.
DEVELOPED_MARKETS = frozenset(
    {"US", "CA", "GB", "DE", "FR", "NL", "CH", "IE", "JP", "AU", "NZ",
     "SE", "DK", "NO", "FI", "ES", "IT", "BE", "LU", "AT", "PT", "SG", "HK"}
)


def country_display_name(code: str) -> str:
    code = str(code or "").upper().strip()
    return COUNTRY_DISPLAY_NAMES.get(code, code or "Unknown")


def _cache_path(skill_dir: Path) -> Path:
    return Path(skill_dir) / CACHE_FILE


def _load_cache(skill_dir: Path) -> dict[str, Any]:
    try:
        path = _cache_path(skill_dir)
        if not path.exists():
            return {}
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except Exception as exc:  # noqa: BLE001
        LOG.debug("Country cache read failed: %s", exc)
        return {}


def _save_cache(skill_dir: Path, cache: dict[str, Any]) -> None:
    try:
        with _cache_path(skill_dir).open("w", encoding="utf-8") as fh:
            json.dump(cache, fh, indent=2, sort_keys=True)
    except Exception as exc:  # noqa: BLE001
        LOG.debug("Country cache write failed: %s", exc)


def _fetch_profile(ticker: str, api_key: str, *, timeout_sec: float) -> dict[str, Any] | None:
    import requests

    try:
        resp = requests.get(
            "https://finnhub.io/api/v1/stock/profile2",
            params={"symbol": ticker, "token": api_key},
            timeout=timeout_sec,
        )
        if resp.status_code != 200:
            LOG.debug("profile2 %s -> http %s", ticker, resp.status_code)
            return None
        payload = resp.json()
        return payload if isinstance(payload, dict) else None
    except Exception as exc:  # noqa: BLE001
        LOG.debug("profile2 %s failed: %s", ticker, exc)
        return None


def resolve_countries(
    tickers: list[str],
    *,
    skill_dir: Path | None = None,
) -> dict[str, dict[str, Any]]:
    """Resolve tickers to ``{country, currency, country_name}`` maps.

    Cached lookups never hit the network. Unresolvable tickers are omitted so
    callers can surface them via ``data_quality``.
    """
    from config import get_finnhub_api_key, get_finnhub_timeout_sec

    sd = Path(skill_dir) if skill_dir else Path(__file__).resolve().parent.parent
    cache = _load_cache(sd)
    now = time.time()
    out: dict[str, dict[str, Any]] = {}
    dirty = False
    api_key = get_finnhub_api_key(sd)
    timeout_sec = get_finnhub_timeout_sec(sd)

    for raw in tickers or []:
        ticker = str(raw or "").upper().strip()
        if not ticker:
            continue
        entry = cache.get(ticker)
        if isinstance(entry, dict):
            stored_at = entry.get("stored_at")
            info = entry.get("info")
            if (
                isinstance(info, dict)
                and isinstance(stored_at, (int, float))
                and (now - stored_at) / 3600.0 <= CACHE_TTL_HOURS
            ):
                if info.get("country"):
                    out[ticker] = info
                continue
        if not api_key:
            continue
        profile = _fetch_profile(ticker, api_key, timeout_sec=timeout_sec)
        info = {}
        if profile:
            country = str(profile.get("country") or "").upper().strip()
            currency = str(profile.get("currency") or "").upper().strip()
            if country:
                info = {
                    "country": country,
                    "currency": currency,
                    "country_name": country_display_name(country),
                }
        cache[ticker] = {"stored_at": now, "info": info}
        dirty = True
        if info:
            out[ticker] = info

    if dirty:
        _save_cache(sd, cache)
    return out
