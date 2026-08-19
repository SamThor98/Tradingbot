"""
Dynamic watchlist loader: named universes (S&P 1500 default) from Wikipedia.
S&P 1500 = S&P 500 + S&P 400 + S&P 600. Cached 24h per universe preset.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from pathlib import Path

LOG = logging.getLogger(__name__)
SKILL_DIR = Path(__file__).resolve().parent
CACHE_FILE = SKILL_DIR / ".watchlist_cache.json"
CACHE_HOURS = 24
_WIKI_HEADERS = {"User-Agent": "TradingBot/1.0 (https://github.com/)"}

# Named-universe cache files besides the legacy SP1500 cache.
_UNIVERSE_CACHE_PREFIX = ".universe_cache_"


def _utc_calendar_date() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")
LIQUID_ETF_HINTS = {"SPY", "QQQ", "IWM", "DIA", "XLK", "XLF", "XLE", "XLV", "XLY", "XLP", "XLI", "XLB", "XLU", "XLC", "XLRE"}

_NASDAQ100_FALLBACK = [
    "AAPL", "MSFT", "NVDA", "AMZN", "META", "GOOGL", "GOOG", "AVGO", "TSLA", "NFLX",
    "COST", "AMD", "PEP", "ADBE", "CSCO", "INTC", "QCOM", "TXN", "AMGN", "INTU",
]


def _clean_symbols(values: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in values:
        sym = str(raw).strip().upper()
        if not sym or len(sym) > 8:
            continue
        if not all(ch.isalnum() or ch in "-." for ch in sym):
            continue
        if sym in seen:
            continue
        seen.add(sym)
        out.append(sym)
    return out


def _fetch_wiki_symbols(url: str, *, columns: tuple[str, ...] = ("Symbol", "Ticker")) -> list[str]:
    """Fetch ticker symbols from the first Wikipedia HTML table that has a known column."""
    from io import StringIO

    import pandas as pd
    import requests

    resp = requests.get(url, headers=_WIKI_HEADERS, timeout=15)
    resp.raise_for_status()
    tables = pd.read_html(StringIO(resp.text))
    wanted = {c.lower() for c in columns}
    for df in tables:
        colmap = {str(c).strip().lower(): c for c in df.columns}
        hit = next((colmap[k] for k in colmap if k in wanted), None)
        if hit is None:
            continue
        symbols = _clean_symbols([str(s) for s in df[hit].dropna().tolist()])
        if len(symbols) >= 10:
            return symbols
    return []


def _fetch_sp500() -> list[str]:
    """Fetch S&P 500 tickers from Wikipedia."""
    return _fetch_wiki_symbols("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies")


def _fetch_sp400() -> list[str]:
    """Fetch S&P 400 mid-cap tickers from Wikipedia."""
    return _fetch_wiki_symbols("https://en.wikipedia.org/wiki/List_of_S%26P_400_companies")


def _fetch_sp600() -> list[str]:
    """Fetch S&P 600 small-cap tickers from Wikipedia (~$400M-$2B market cap)."""
    return _fetch_wiki_symbols("https://en.wikipedia.org/wiki/List_of_S%26P_600_companies")


def _fetch_nasdaq100() -> list[str]:
    """Fetch Nasdaq-100 tickers from Wikipedia."""
    return _fetch_wiki_symbols("https://en.wikipedia.org/wiki/Nasdaq-100")


def _cache_path(name: str) -> Path:
    key = str(name or "sp1500").strip().lower()
    if key in {"sp1500", "watchlist"}:
        return CACHE_FILE
    return SKILL_DIR / f"{_UNIVERSE_CACHE_PREFIX}{key}.json"


def _load_cached(name: str = "sp1500") -> tuple[list[str], float, str | None] | None:
    """Load cached watchlist. Returns (tickers, timestamp, as_of_utc_date or None) or None."""
    path = _cache_path(name)
    if not path.exists():
        return None
    try:
        import json

        data = json.loads(path.read_text())
        tickers = data.get("tickers", [])
        ts = data.get("timestamp", 0)
        if not tickers or not ts:
            return None
        as_of = data.get("as_of_utc_date")
        as_of_s = as_of.strip() if isinstance(as_of, str) else None
        return tickers, float(ts), as_of_s or None
    except Exception as e:
        LOG.warning("Watchlist cache read failed (%s): %s", name, e)
    return None


def _save_cache(tickers: list[str], name: str = "sp1500") -> None:
    """Save watchlist to cache (MiroFish uses separate .mirofish_cache.json)."""
    try:
        from _io_utils import atomic_write_json

        data = {
            "tickers": tickers,
            "timestamp": time.time(),
            "as_of_utc_date": _utc_calendar_date(),
            "universe": str(name or "sp1500"),
        }
        atomic_write_json(_cache_path(name), data, indent=0)
    except Exception as e:
        LOG.warning("Watchlist cache write failed (%s): %s", name, e)


def _cache_fresh(cached: tuple[list[str], float, str | None] | None) -> list[str] | None:
    if not cached:
        return None
    tickers, ts, as_of = cached
    today = _utc_calendar_date()
    cache_fresh_for_day = as_of == today
    legacy_fresh = as_of is None and (time.time() - ts) < CACHE_HOURS * 3600
    if cache_fresh_for_day or legacy_fresh:
        return list(tickers)
    return None


def _load_named_fetch(
    name: str,
    fetcher,
    *,
    force_refresh: bool = False,
    fallback: list[str] | None = None,
) -> list[str]:
    if not force_refresh:
        hit = _cache_fresh(_load_cached(name))
        if hit:
            LOG.debug("Using cached %s universe (%d tickers)", name, len(hit))
            return hit
    try:
        tickers = fetcher()
    except Exception as e:
        LOG.warning("Fetch failed for universe %s: %s", name, e)
        tickers = []
    if tickers:
        _save_cache(tickers, name)
        LOG.info("Loaded %d tickers for universe %s", len(tickers), name)
        return tickers
    if fallback:
        LOG.warning("Universe %s fetch empty; using fallback (%d names)", name, len(fallback))
        return list(fallback)
    return []


def load_full_watchlist(force_refresh: bool = False) -> list[str]:
    """
    Load S&P 1500 watchlist (S&P 500 + S&P 400 + S&P 600).
    Uses cache for the same UTC calendar day (daily refresh), or if the cache file
    predates as_of_utc_date, falls back to the prior <24h timestamp rule.
    Returns deduplicated list of tickers, all sectors.
    """
    if not force_refresh:
        cached = _load_cached()
        if cached:
            tickers, ts, as_of = cached
            today = _utc_calendar_date()
            cache_fresh_for_day = as_of == today
            legacy_fresh = as_of is None and (time.time() - ts) < CACHE_HOURS * 3600
            if cache_fresh_for_day or legacy_fresh:
                LOG.debug("Using cached watchlist (%d tickers)", len(tickers))
                return tickers

    LOG.info("Fetching S&P 500 + S&P 400 + S&P 600 (S&P 1500)...")
    # Fetch each index independently. A single failure (e.g. lxml missing,
    # transient Wikipedia outage) used to wipe the entire universe down to the
    # 18-ticker fallback because all fetches
    # fetches lived in one try block. Now any combination of successful
    # fetches contributes; only when every fetch fails do we fall back.
    fetched: list[list[str]] = []
    for label, fn in (
        ("S&P 500", _fetch_sp500),
        ("S&P 400", _fetch_sp400),
        ("S&P 600", _fetch_sp600),
    ):
        try:
            tickers = fn()
            if tickers:
                fetched.append(tickers)
                LOG.info("Fetched %d tickers from %s", len(tickers), label)
            else:
                LOG.warning("Fetch returned 0 tickers for %s", label)
        except Exception as e:
            LOG.warning("Fetch failed for %s: %s", label, e)

    if not fetched:
        LOG.warning("All watchlist fetches failed. Using fallback.")
        return _fallback_watchlist()

    merged: list[str] = []
    for lst in fetched:
        merged.extend(lst)
    combined = list(dict.fromkeys(merged))
    if combined:
        _save_cache(combined)
        LOG.info(
            "Loaded %d tickers (%d index fetches succeeded)",
            len(combined),
            len(fetched),
        )
    return combined or _fallback_watchlist()


def _fallback_watchlist() -> list[str]:
    """Minimal fallback if fetch fails."""
    return [
        "AAPL", "MSFT", "GOOGL", "AMZN", "META", "NVDA", "JPM", "V", "UNH",
        "XOM", "JNJ", "WMT", "PG", "HD", "DIS", "BAC", "KO", "PEP",
    ]


def prefilter_watchlist(
    tickers: list[str],
    max_tickers: int = 800,
    include_etf_hints: bool = True,
) -> list[str]:
    """
    Deterministic quality prefilter to reduce noisy universe size.
    Keeps plain symbols first, optionally preserving a small set of liquid ETF hints.
    """
    cleaned = [t.strip().upper() for t in tickers if t and t.strip()]
    deduped = list(dict.fromkeys(cleaned))

    plain = [t for t in deduped if t.isalpha() and 1 <= len(t) <= 5]
    extras = []
    if include_etf_hints:
        extras = [t for t in deduped if t in LIQUID_ETF_HINTS and t not in plain]

    merged = plain + extras
    if max_tickers > 0:
        merged = merged[:max_tickers]
    return merged


def load_universe(preset: str, *, force_refresh: bool = False) -> list[str]:
    """Load a named scan universe. Unknown presets raise ValueError."""
    key = str(preset or "sp1500").strip().lower()
    if key in {"sp1500", "watchlist"}:
        return load_full_watchlist(force_refresh=force_refresh)
    if key == "sp500":
        return _load_named_fetch("sp500", _fetch_sp500, force_refresh=force_refresh, fallback=_fallback_watchlist())
    if key == "sp400":
        return _load_named_fetch("sp400", _fetch_sp400, force_refresh=force_refresh, fallback=_fallback_watchlist())
    if key == "sp600":
        return _load_named_fetch("sp600", _fetch_sp600, force_refresh=force_refresh, fallback=_fallback_watchlist())
    if key == "nasdaq100":
        return _load_named_fetch(
            "nasdaq100",
            _fetch_nasdaq100,
            force_refresh=force_refresh,
            fallback=_NASDAQ100_FALLBACK,
        )
    if key == "sector_etfs":
        return sorted(LIQUID_ETF_HINTS)
    if key == "focused":
        return load_full_watchlist(force_refresh=force_refresh)
    raise ValueError(f"unknown or unavailable universe preset: {preset!r}")

