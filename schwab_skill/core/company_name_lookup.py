"""Batch ticker -> company display name via Finnhub profile2.

Fail-soft: omit unresolved tickers; never invent names. Cached on disk
(profiles are near-static). Intended for scan shortlist enrichment only —
not a full research snapshot.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

LOG = logging.getLogger(__name__)

CACHE_FILE = ".company_name_cache.json"
CACHE_TTL_HOURS = 24.0 * 14


def _cache_path(skill_dir: Path) -> Path:
    return Path(skill_dir) / CACHE_FILE


def _load_cache(skill_dir: Path) -> dict[str, Any]:
    path = _cache_path(skill_dir)
    if not path.exists():
        return {}
    try:
        with path.open(encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except Exception as exc:  # noqa: BLE001
        LOG.debug("Company name cache read failed: %s", exc)
        return {}


def _save_cache(skill_dir: Path, cache: dict[str, Any]) -> None:
    try:
        with _cache_path(skill_dir).open("w", encoding="utf-8") as fh:
            json.dump(cache, fh, indent=2, sort_keys=True)
    except Exception as exc:  # noqa: BLE001
        LOG.debug("Company name cache write failed: %s", exc)


def _fetch_profile_name(ticker: str, api_key: str, *, timeout_sec: float) -> str | None:
    import requests

    try:
        resp = requests.get(
            "https://finnhub.io/api/v1/stock/profile2",
            params={"symbol": ticker, "token": api_key},
            timeout=timeout_sec,
        )
        if resp.status_code != 200:
            LOG.debug("profile2 name %s -> http %s", ticker, resp.status_code)
            return None
        payload = resp.json()
        if not isinstance(payload, dict):
            return None
        name = str(payload.get("name") or "").strip()
        return name or None
    except Exception as exc:  # noqa: BLE001
        LOG.debug("profile2 name %s failed: %s", ticker, exc)
        return None


def resolve_company_names(
    tickers: list[str],
    *,
    skill_dir: Path | None = None,
) -> dict[str, str]:
    """Return ``{TICKER: company_name}`` for resolvable symbols only."""
    from config import get_finnhub_api_key, get_finnhub_timeout_sec

    sd = Path(skill_dir) if skill_dir else Path(__file__).resolve().parent.parent
    cache = _load_cache(sd)
    now = time.time()
    out: dict[str, str] = {}
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
            name = entry.get("name")
            if (
                isinstance(name, str)
                and name.strip()
                and isinstance(stored_at, (int, float))
                and (now - stored_at) / 3600.0 <= CACHE_TTL_HOURS
            ):
                out[ticker] = name.strip()
                continue
            # Negative cache hit (looked up, no name) — skip refetch within TTL.
            if (
                name in ("", None)
                and isinstance(stored_at, (int, float))
                and (now - stored_at) / 3600.0 <= CACHE_TTL_HOURS
            ):
                continue
        if not api_key:
            continue
        name = _fetch_profile_name(ticker, api_key, timeout_sec=timeout_sec)
        cache[ticker] = {"stored_at": now, "name": name or ""}
        dirty = True
        if name:
            out[ticker] = name

    if dirty:
        _save_cache(sd, cache)
    return out


def attach_company_names(
    rows: list[dict[str, Any]],
    *,
    skill_dir: Path | None = None,
) -> list[dict[str, Any]]:
    """Attach ``company_name`` onto signal dicts when resolvable. Mutates in place."""
    tickers = [
        str(r.get("ticker") or r.get("symbol") or "").upper().strip()
        for r in rows
        if isinstance(r, dict)
    ]
    names = resolve_company_names(tickers, skill_dir=skill_dir)
    for row in rows:
        if not isinstance(row, dict):
            continue
        ticker = str(row.get("ticker") or row.get("symbol") or "").upper().strip()
        name = names.get(ticker)
        if name:
            row["company_name"] = name
        else:
            row.pop("company_name", None)
    return rows
