"""Fetch and cache SEC companyfacts JSON (one document per CIK)."""

from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path
from typing import Any, Callable

LOG = logging.getLogger(__name__)

SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SEC_COMPANYFACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
TICKER_INDEX_TTL_SEC = 24 * 3600
FAILED_TTL_SEC = 15 * 60
DEFAULT_TIMEOUT_SEC = 20.0

JsonFetcher = Callable[[str, str, float], dict[str, Any]]


class CompanyfactsFetchError(RuntimeError):
    """EDGAR refused or failed a companyfacts request."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(detail or code)
        self.code = code
        self.detail = detail or code


def cache_root(skill_dir: Path | None = None) -> Path:
    from pathlib import Path as _Path

    if skill_dir is None:
        skill_dir = _Path(__file__).resolve().parents[2]
    return Path(skill_dir) / ".companyfacts"


def _lock_for(root: Path) -> threading.Lock:
    return _ROOT_LOCKS.setdefault(str(root), threading.Lock())


_ROOT_LOCKS: dict[str, threading.Lock] = {}


def _headers(user_agent: str) -> dict[str, str]:
    return {
        "User-Agent": user_agent,
        "Accept": "application/json",
        "Accept-Encoding": "gzip, deflate",
    }


def default_fetcher(url: str, user_agent: str, timeout_sec: float) -> dict[str, Any]:
    import requests

    resp = requests.get(url, headers=_headers(user_agent), timeout=timeout_sec)
    if resp.status_code == 429:
        retry_after = str(resp.headers.get("Retry-After") or "").strip()
        raise CompanyfactsFetchError("rate_limited", retry_after or "429")
    if resp.status_code in {401, 403}:
        raise CompanyfactsFetchError("forbidden", f"HTTP {resp.status_code}")
    if resp.status_code == 404:
        raise CompanyfactsFetchError("not_found", f"HTTP 404 for {url}")
    resp.raise_for_status()
    data = resp.json()
    if not isinstance(data, dict):
        raise CompanyfactsFetchError("malformed", "SEC response was not a JSON object")
    return data


def _atomic_write(path: Path, payload: dict[str, Any]) -> None:
    from _io_utils import atomic_write_json

    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(path, payload, indent=2)


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def resolve_user_agent(skill_dir: Path | None = None) -> str:
    from config import get_edgar_user_agent, is_real_edgar_user_agent

    ua = get_edgar_user_agent(skill_dir)
    if not is_real_edgar_user_agent(ua):
        raise CompanyfactsFetchError(
            "edgar_user_agent",
            "EDGAR_USER_AGENT missing or contains placeholder/example contact; "
            "SEC requires a real operator email. Set EDGAR_USER_AGENT in .env.",
        )
    return ua


def ticker_index_path(skill_dir: Path | None = None) -> Path:
    return cache_root(skill_dir) / "tickers.json"


def facts_path(cik: str, skill_dir: Path | None = None) -> Path:
    return cache_root(skill_dir) / "facts" / f"CIK{str(cik).zfill(10)}.json"


def meta_path(cik: str, skill_dir: Path | None = None) -> Path:
    return cache_root(skill_dir) / "facts" / f"CIK{str(cik).zfill(10)}.meta.json"


def load_ticker_index(
    skill_dir: Path | None = None,
    *,
    user_agent: str | None = None,
    timeout_sec: float = DEFAULT_TIMEOUT_SEC,
    fetcher: JsonFetcher | None = None,
    now: float | None = None,
    cached_only: bool = False,
) -> dict[str, str]:
    """Return {TICKER: CIK10} from a 24h cache, fetching company_tickers.json on miss."""
    path = ticker_index_path(skill_dir)
    cached = _read_json(path)
    ts = float((cached or {}).get("fetched_at", 0) or 0)
    mapping = (cached or {}).get("tickers")
    age_ok = (now if now is not None else time.time()) - ts <= TICKER_INDEX_TTL_SEC
    if isinstance(mapping, dict) and mapping and (age_ok or cached_only):
        return {str(k).upper(): str(v).zfill(10) for k, v in mapping.items() if str(k).strip()}
    if cached_only:
        raise CompanyfactsFetchError("cik_not_found", "ticker index is not cached")

    ua = user_agent or resolve_user_agent(skill_dir)
    fetch = fetcher or default_fetcher
    raw = fetch(SEC_TICKERS_URL, ua, timeout_sec)
    tickers: dict[str, str] = {}
    for row in raw.values():
        if not isinstance(row, dict):
            continue
        ticker = str(row.get("ticker") or "").strip().upper()
        cik = str(row.get("cik_str") or "").strip()
        if ticker and cik:
            tickers[ticker] = cik.zfill(10)
    if not tickers:
        raise CompanyfactsFetchError("malformed", "company_tickers.json had no ticker rows")
    _atomic_write(path, {"fetched_at": now if now is not None else time.time(), "tickers": tickers})
    return tickers


def lookup_cik(
    ticker: str,
    skill_dir: Path | None = None,
    *,
    user_agent: str | None = None,
    timeout_sec: float = DEFAULT_TIMEOUT_SEC,
    fetcher: JsonFetcher | None = None,
    cached_only: bool = False,
) -> str:
    symbol = str(ticker or "").strip().upper()
    index = load_ticker_index(
        skill_dir,
        user_agent=user_agent,
        timeout_sec=timeout_sec,
        fetcher=fetcher,
        cached_only=cached_only,
    )
    cik = index.get(symbol)
    if not cik:
        raise CompanyfactsFetchError("cik_not_found", f"CIK not found for {symbol}")
    return cik


def _meta_fresh(meta: dict[str, Any] | None, *, cache_hours: float, now: float) -> bool:
    if not meta:
        return False
    ok = bool(meta.get("ok"))
    ts = float(meta.get("fetched_at", 0) or 0)
    if ts <= 0:
        return False
    ttl = float(cache_hours) * 3600.0 if ok else min(float(cache_hours) * 3600.0, FAILED_TTL_SEC)
    return (now - ts) <= ttl


def load_cached_facts(cik: str, skill_dir: Path | None = None) -> dict[str, Any] | None:
    return _read_json(facts_path(cik, skill_dir))


def load_cached_meta(cik: str, skill_dir: Path | None = None) -> dict[str, Any] | None:
    return _read_json(meta_path(cik, skill_dir))


def fetch_companyfacts(
    ticker: str,
    *,
    skill_dir: Path | None = None,
    cache_hours: float = 72.0,
    timeout_sec: float = DEFAULT_TIMEOUT_SEC,
    fetcher: JsonFetcher | None = None,
    force: bool = False,
    now: float | None = None,
    user_agent: str | None = None,
) -> dict[str, Any]:
    """Return companyfacts JSON for ``ticker``. Cache-first unless ``force``.

    Successful payloads honor ``cache_hours``. Failures are cached for 15 minutes
    so a 429 cannot stampede EDGAR. Recompute paths must not call this.
    """
    symbol = str(ticker or "").strip().upper()
    ts = now if now is not None else time.time()
    fetch = fetcher or default_fetcher
    ua = user_agent

    def _ua() -> str:
        nonlocal ua
        if ua is None:
            ua = resolve_user_agent(skill_dir)
        return ua

    try:
        cik = lookup_cik(
            symbol,
            skill_dir,
            user_agent=ua,
            timeout_sec=timeout_sec,
            fetcher=fetcher,
            cached_only=True,
        )
    except CompanyfactsFetchError:
        cik = lookup_cik(
            symbol,
            skill_dir,
            user_agent=_ua(),
            timeout_sec=timeout_sec,
            fetcher=fetch,
        )

    meta = load_cached_meta(cik, skill_dir)
    if not force and _meta_fresh(meta, cache_hours=cache_hours, now=ts):
        if meta and meta.get("ok"):
            facts = load_cached_facts(cik, skill_dir)
            if facts:
                facts = dict(facts)
                facts["_cache"] = {
                    "from_cache": True,
                    "cik": cik,
                    "fetched_at": meta.get("fetched_at"),
                    "ticker": symbol,
                }
                return facts
        raise CompanyfactsFetchError(
            str((meta or {}).get("code") or "cached_error"),
            str((meta or {}).get("detail") or "cached companyfacts error"),
        )

    url = SEC_COMPANYFACTS_URL.format(cik=cik)
    try:
        facts = fetch(url, _ua(), timeout_sec)
    except CompanyfactsFetchError as exc:
        _atomic_write(
            meta_path(cik, skill_dir),
            {
                "ok": False,
                "code": exc.code,
                "detail": exc.detail,
                "fetched_at": ts,
                "ticker": symbol,
                "cik": cik,
            },
        )
        raise
    except Exception as exc:
        _atomic_write(
            meta_path(cik, skill_dir),
            {
                "ok": False,
                "code": "fetch_failed",
                "detail": str(exc)[:220],
                "fetched_at": ts,
                "ticker": symbol,
                "cik": cik,
            },
        )
        raise CompanyfactsFetchError("fetch_failed", str(exc)[:220]) from exc

    root = cache_root(skill_dir)
    with _lock_for(root):
        _atomic_write(facts_path(cik, skill_dir), facts)
        _atomic_write(
            meta_path(cik, skill_dir),
            {
                "ok": True,
                "code": "ok",
                "fetched_at": ts,
                "ticker": symbol,
                "cik": cik,
                "entity_name": facts.get("entityName"),
            },
        )
    out = dict(facts)
    out["_cache"] = {
        "from_cache": False,
        "cik": cik,
        "fetched_at": ts,
        "ticker": symbol,
    }
    LOG.info("Cached companyfacts for %s (CIK %s)", symbol, cik)
    return out
