"""
Finnhub data aggregation helpers for the institutional research dossier.

This module wraps a curated set of Finnhub REST endpoints behind a single
``FinnhubClient``. The client is hardened for the free-tier API (60 requests
per minute) with:

* Token-bucket pacing between calls so we stay below the documented limit.
* Exponential-backoff retries with respect for Finnhub's ``Retry-After`` header
  on HTTP 429 responses.
* Per-endpoint timeouts and structured error reporting that flows into the
  dossier source-metadata so the UI can show degraded states instead of
  silently rendering ``n/a``.

The public ``get_finnhub_research_snapshot`` returns a stable shape regardless
of whether the API key is configured, whether individual calls fail, or whether
upstream data is missing — every consumer can treat the payload as fully
populated and use the included ``errors`` list to render degraded badges.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import requests

from config import (
    get_finnhub_api_key,
    get_finnhub_cache_enabled,
    get_finnhub_cache_hours,
    get_finnhub_max_news_items,
    get_finnhub_max_retries,
    get_finnhub_news_days,
    get_finnhub_rate_limit_per_min,
    get_finnhub_retry_backoff_cap_sec,
    get_finnhub_timeout_sec,
)

LOG = logging.getLogger(__name__)
FINNHUB_BASE = "https://finnhub.io/api/v1"
SKILL_DIR = Path(__file__).resolve().parent
FINNHUB_CACHE_FILE = ".finnhub_cache.json"

# Finnhub free tier publishes 60 requests/minute. We pace at 55 to leave headroom
# for parallel snapshot calls from concurrent dossiers.
DEFAULT_RATE_LIMIT_PER_MIN = 55
DEFAULT_RATE_WINDOW_SEC = 60.0
FAILED_PAYLOAD_TTL_HOURS = 0.25

# Endpoints that frequently 403 on the free plan. We still attempt them but
# downgrade to ``info`` logging so the dossier doesn't spam ERRORs.
PREMIUM_ENDPOINTS = frozenset(
    {
        "stock/insider-sentiment",
        "stock/social-sentiment",
        "news-sentiment",
        "stock/transcripts",
        "stock/transcripts-list",
        "stock/ownership",
        "stock/fund-ownership",
        "stock/lobbying",
        "stock/usa-spending",
        "stock/uspto-patent",
        "stock/visa-application",
        "stock/financials-reported",
        "stock/upgrade-downgrade",
    }
)


def _safe_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_int(value: Any) -> int | None:
    try:
        if value is None:
            return None
        return int(value)
    except (TypeError, ValueError):
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return None


def _safe_str(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _has_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, set, dict)):
        return len(value) > 0
    return True


def _to_iso_utc(ts: int | float | None) -> str | None:
    if ts is None:
        return None
    try:
        return datetime.fromtimestamp(float(ts), tz=UTC).isoformat()
    except (TypeError, ValueError, OSError):
        return None


def _fmt_date(dt: datetime) -> str:
    return dt.date().isoformat()


def _cache_path(skill_dir: Path) -> Path:
    return skill_dir / FINNHUB_CACHE_FILE


def _load_cache(skill_dir: Path) -> dict[str, Any]:
    path = _cache_path(skill_dir)
    try:
        if not path.exists():
            return {}
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except Exception as exc:  # noqa: BLE001
        LOG.debug("Finnhub cache read failed: %s", exc)
        return {}


def _save_cache(skill_dir: Path, cache: dict[str, Any]) -> None:
    path = _cache_path(skill_dir)
    try:
        with path.open("w", encoding="utf-8") as fh:
            json.dump(cache, fh, indent=2, sort_keys=True)
    except Exception as exc:  # noqa: BLE001
        LOG.debug("Finnhub cache write failed: %s", exc)


def _cache_key(ticker: str) -> str:
    return str(ticker or "").strip().upper()


def _cached_payload(skill_dir: Path, ticker: str, *, success_ttl_hours: float) -> dict[str, Any] | None:
    cache = _load_cache(skill_dir)
    entry = cache.get(_cache_key(ticker))
    if not isinstance(entry, dict):
        return None
    payload = entry.get("payload")
    if not isinstance(payload, dict):
        return None
    stored_at = _safe_float(entry.get("stored_at"))
    if stored_at is None:
        return None
    ttl_hours = success_ttl_hours if payload.get("ok") else FAILED_PAYLOAD_TTL_HOURS
    age_hours = (time.time() - stored_at) / 3600.0
    if age_hours > ttl_hours:
        return None
    cached = dict(payload)
    cached["from_cache"] = True
    return cached


def _remember_payload(skill_dir: Path, ticker: str, payload: dict[str, Any]) -> None:
    cache = _load_cache(skill_dir)
    cache[_cache_key(ticker)] = {"stored_at": time.time(), "payload": payload}
    _save_cache(skill_dir, cache)


@dataclass(frozen=True)
class _CallResult:
    payload: Any
    status: str  # "ok" | "missing" | "rate_limited" | "forbidden" | "error"
    detail: str = ""


class _RateLimiter:
    """Simple sliding-window rate limiter (thread-safe)."""

    def __init__(self, max_calls: int, window_sec: float) -> None:
        self.max_calls = max(1, int(max_calls))
        self.window_sec = float(window_sec)
        self._calls: deque[float] = deque()
        self._lock = threading.Lock()

    def acquire(self) -> None:
        while True:
            with self._lock:
                now = time.monotonic()
                while self._calls and (now - self._calls[0]) > self.window_sec:
                    self._calls.popleft()
                if len(self._calls) < self.max_calls:
                    self._calls.append(now)
                    return
                wait_for = self.window_sec - (now - self._calls[0])
            if wait_for > 0:
                # Cap the per-call wait so a misbehaving deque cannot block forever.
                time.sleep(min(5.0, max(0.05, wait_for)))


class FinnhubClient:
    """Resilient wrapper around the Finnhub REST API.

    The client handles rate limiting, retries with backoff for transient
    failures, and standard normalization of empty payloads.
    """

    def __init__(
        self,
        *,
        api_key: str,
        timeout_sec: float = 8.0,
        session: requests.Session | None = None,
        max_retries: int = 3,
        retry_backoff_cap_sec: float = 30.0,
        rate_limit_per_min: int = DEFAULT_RATE_LIMIT_PER_MIN,
    ) -> None:
        self.api_key = api_key.strip()
        self.timeout_sec = float(timeout_sec)
        self.session = session or requests.Session()
        self.max_retries = max(0, int(max_retries))
        self.retry_backoff_cap_sec = max(1.0, float(retry_backoff_cap_sec))
        self._limiter = _RateLimiter(rate_limit_per_min, DEFAULT_RATE_WINDOW_SEC)

    def _retry_sleep(self, attempt: int, *, multiplier: float = 0.6) -> float:
        wait_for = max(0.1, multiplier * (2**attempt))
        return min(self.retry_backoff_cap_sec, wait_for)

    # ------------------------------------------------------------------
    # Low-level transport
    # ------------------------------------------------------------------

    def _request(self, endpoint: str, params: dict[str, Any] | None) -> _CallResult:
        merged: dict[str, Any] = {"token": self.api_key}
        if params:
            merged.update({k: v for k, v in params.items() if v is not None})
        url = f"{FINNHUB_BASE}/{endpoint.lstrip('/')}"

        last_detail = ""
        for attempt in range(self.max_retries + 1):
            self._limiter.acquire()
            try:
                resp = self.session.get(url, params=merged, timeout=self.timeout_sec)
            except requests.RequestException as exc:
                last_detail = f"{type(exc).__name__}"
                LOG.info("Finnhub %s transport error (attempt %s): %s", endpoint, attempt + 1, exc)
                if attempt < self.max_retries:
                    time.sleep(self._retry_sleep(attempt, multiplier=0.6))
                    continue
                return _CallResult(None, "error", last_detail)

            if resp.status_code == 429:
                retry_after = resp.headers.get("Retry-After")
                wait_for = self._retry_sleep(attempt, multiplier=1.5)
                if retry_after:
                    try:
                        wait_for = max(wait_for, float(retry_after))
                    except (TypeError, ValueError):
                        pass
                wait_for = min(self.retry_backoff_cap_sec, wait_for)
                LOG.info("Finnhub %s rate limited; waiting %.1fs", endpoint, wait_for)
                time.sleep(wait_for)
                last_detail = "rate_limited"
                if attempt < self.max_retries:
                    continue
                return _CallResult(None, "rate_limited", last_detail)

            if resp.status_code == 403:
                return _CallResult(None, "forbidden", "premium endpoint")

            if resp.status_code == 401:
                return _CallResult(None, "error", "unauthorized")

            if resp.status_code == 404:
                return _CallResult(None, "missing", "not found")

            if 500 <= resp.status_code < 600:
                last_detail = f"http_{resp.status_code}"
                LOG.info(
                    "Finnhub %s server error %s (attempt %s)",
                    endpoint, resp.status_code, attempt + 1,
                )
                if attempt < self.max_retries:
                    time.sleep(self._retry_sleep(attempt, multiplier=0.6))
                    continue
                return _CallResult(None, "error", last_detail)

            try:
                resp.raise_for_status()
            except requests.HTTPError as exc:
                return _CallResult(None, "error", f"http_{resp.status_code}: {exc}")

            try:
                payload = resp.json()
            except ValueError as exc:
                return _CallResult(None, "error", f"invalid_json: {exc}")

            return _CallResult(payload, "ok", "")
        return _CallResult(None, "error", last_detail or "unknown")

    def _get_json(
        self,
        endpoint: str,
        params: dict[str, Any] | None = None,
        *,
        label: str,
        errors: list[str],
    ) -> Any:
        result = self._request(endpoint, params)
        if result.status == "ok":
            payload = result.payload
            if isinstance(payload, dict) and not payload:
                # Empty dict is treated as "missing" but not error-tagged.
                return {}
            if isinstance(payload, list) and not payload:
                return []
            return payload if payload is not None else {}
        if result.status == "missing":
            return None
        # Premium endpoints fail "softly" — flagged but not noisy.
        if endpoint in PREMIUM_ENDPOINTS and result.status in {"forbidden", "error"}:
            errors.append(f"{label}:{result.status}")
            return None
        errors.append(f"{label}:{result.status}")
        return None

    # ------------------------------------------------------------------
    # Snapshot composition
    # ------------------------------------------------------------------

    def get_snapshot(
        self,
        ticker: str,
        *,
        news_days: int = 30,
        max_news_items: int = 12,
    ) -> dict[str, Any]:
        sym = ticker.upper().strip()
        now = datetime.now(UTC)
        news_start = now - timedelta(days=max(1, news_days))
        long_window_start = now - timedelta(days=180)
        upcoming_end = now + timedelta(days=90)
        errors: list[str] = []

        profile = self._get_json("stock/profile2", {"symbol": sym}, label="profile2", errors=errors) or {}
        quote = self._get_json("quote", {"symbol": sym}, label="quote", errors=errors) or {}
        recommendations = self._get_json("stock/recommendation", {"symbol": sym}, label="recommendation", errors=errors)
        price_target = self._get_json("stock/price-target", {"symbol": sym}, label="price_target", errors=errors) or {}
        news = self._get_json(
            "company-news",
            {"symbol": sym, "from": _fmt_date(news_start), "to": _fmt_date(now)},
            label="company_news", errors=errors,
        )
        earnings = self._get_json("stock/earnings", {"symbol": sym, "limit": 12}, label="earnings", errors=errors)
        metrics = self._get_json("stock/metric", {"symbol": sym, "metric": "all"}, label="metrics", errors=errors) or {}

        # Newer / richer fields (free tier where possible, else premium-soft).
        peers = self._get_json("stock/peers", {"symbol": sym}, label="peers", errors=errors)
        insider_tx = self._get_json(
            "stock/insider-transactions",
            {"symbol": sym, "from": _fmt_date(long_window_start), "to": _fmt_date(now)},
            label="insider_tx", errors=errors,
        )
        insider_sent = self._get_json(
            "stock/insider-sentiment",
            {"symbol": sym, "from": _fmt_date(long_window_start - timedelta(days=180)), "to": _fmt_date(now)},
            label="insider_sentiment", errors=errors,
        )
        upgrades = self._get_json(
            "stock/upgrade-downgrade",
            {"symbol": sym, "from": _fmt_date(long_window_start), "to": _fmt_date(now)},
            label="upgrade_downgrade", errors=errors,
        )
        sec_filings = self._get_json(
            "stock/filings",
            {"symbol": sym, "from": _fmt_date(now - timedelta(days=365)), "to": _fmt_date(now)},
            label="sec_filings", errors=errors,
        )
        dividends = self._get_json(
            "stock/dividend",
            {"symbol": sym, "from": _fmt_date(now - timedelta(days=730)), "to": _fmt_date(now)},
            label="dividends", errors=errors,
        )
        splits = self._get_json(
            "stock/split",
            {"symbol": sym, "from": _fmt_date(now - timedelta(days=1825)), "to": _fmt_date(now)},
            label="splits", errors=errors,
        )
        earnings_calendar = self._get_json(
            "calendar/earnings",
            {"symbol": sym, "from": _fmt_date(now), "to": _fmt_date(upcoming_end)},
            label="earnings_calendar", errors=errors,
        ) or {}
        news_sentiment = self._get_json(
            "news-sentiment", {"symbol": sym}, label="news_sentiment", errors=errors,
        )

        # ---------------- Normalization ----------------

        rec_rows = recommendations if isinstance(recommendations, list) else []
        latest_rec = rec_rows[0] if rec_rows else {}
        recommendation_history: list[dict[str, Any]] = []
        for row in rec_rows[:6]:
            if not isinstance(row, dict):
                continue
            recommendation_history.append(
                {
                    "period": _safe_str(row.get("period")),
                    "buy": _safe_int(row.get("buy")) or 0,
                    "hold": _safe_int(row.get("hold")) or 0,
                    "sell": _safe_int(row.get("sell")) or 0,
                    "strong_buy": _safe_int(row.get("strongBuy")) or 0,
                    "strong_sell": _safe_int(row.get("strongSell")) or 0,
                }
            )

        news_rows = news if isinstance(news, list) else []
        normalized_news: list[dict[str, Any]] = []
        for row in news_rows[: max(1, max_news_items)]:
            if not isinstance(row, dict):
                continue
            normalized_news.append(
                {
                    "headline": _safe_str(row.get("headline")),
                    "summary": _safe_str(row.get("summary")),
                    "source": _safe_str(row.get("source")),
                    "url": _safe_str(row.get("url")),
                    "category": _safe_str(row.get("category")),
                    "datetime": _to_iso_utc(row.get("datetime")),
                }
            )

        earnings_rows = earnings if isinstance(earnings, list) else []
        normalized_earnings: list[dict[str, Any]] = []
        for row in earnings_rows[:8]:
            if not isinstance(row, dict):
                continue
            normalized_earnings.append(
                {
                    "period": _safe_str(row.get("period")),
                    "actual": _safe_float(row.get("actual")),
                    "estimate": _safe_float(row.get("estimate")),
                    "surprise": _safe_float(row.get("surprise")),
                    "surprise_percent": _safe_float(row.get("surprisePercent")),
                }
            )

        metric_block = metrics.get("metric") if isinstance(metrics, dict) else {}
        metric_block = metric_block if isinstance(metric_block, dict) else {}
        series = metrics.get("series") if isinstance(metrics, dict) else {}
        series_quarterly = (series or {}).get("quarterly") if isinstance(series, dict) else {}

        normalized_peers: list[str] = []
        if isinstance(peers, list):
            for peer in peers[:10]:
                peer_str = _safe_str(peer)
                if peer_str and peer_str.upper() != sym:
                    normalized_peers.append(peer_str.upper())

        # Insider transactions — only score open-market activity.
        #
        # SEC Form-4 transaction codes:
        #   P = open-market purchase  (real buy conviction)
        #   S = open-market sale      (real sell conviction)
        #   A = grant/award (stock-based comp; NOT a market signal)
        #   M = option exercise (reclassifies shares; NOT a market signal)
        #   F = shares withheld for taxes (forced; NOT a market signal)
        #   D = disposition to issuer (NOT a market signal)
        #   G = gift; X = warrant exercise; J/K/V = other (NOT signal)
        #
        # We split the aggregate into "open-market" (P/S only) and "all"
        # so the dossier can show conviction without being polluted by comp.
        insider_data_rows: list[dict[str, Any]] = []
        open_market_shares = 0
        open_market_value = 0.0
        open_market_buys = 0
        open_market_sells = 0
        if isinstance(insider_tx, dict):
            tx_rows = insider_tx.get("data") or []
            for row in tx_rows:
                if not isinstance(row, dict):
                    continue
                share = _safe_float(row.get("share")) or 0.0
                price = _safe_float(row.get("transactionPrice")) or _safe_float(row.get("price")) or 0.0
                code = _safe_str(row.get("transactionCode")).upper()
                if code == "P":
                    open_market_buys += 1
                    open_market_shares += int(abs(share))
                    open_market_value += abs(share) * price
                elif code == "S":
                    open_market_sells += 1
                    open_market_shares -= int(abs(share))
                    open_market_value -= abs(share) * price
                # All other codes are non-discretionary (awards, tax, exercise,
                # gifts) and intentionally excluded from conviction scoring.
                insider_data_rows.append(
                    {
                        "name": _safe_str(row.get("name")),
                        "share": _safe_float(row.get("share")),
                        "transaction_price": _safe_float(
                            row.get("transactionPrice") or row.get("price")
                        ),
                        "transaction_date": _safe_str(row.get("transactionDate") or row.get("filingDate")),
                        "transaction_code": code,
                        "is_open_market": code in {"P", "S"},
                    }
                )

        insider_sentiment_rows: list[dict[str, Any]] = []
        net_mspr = 0.0
        net_change = 0
        if isinstance(insider_sent, dict):
            sentiment_rows = insider_sent.get("data") or []
            for row in sentiment_rows[-6:]:
                if not isinstance(row, dict):
                    continue
                mspr = _safe_float(row.get("mspr")) or 0.0
                chg = _safe_int(row.get("change")) or 0
                net_mspr += mspr
                net_change += chg
                insider_sentiment_rows.append(
                    {
                        "year": _safe_int(row.get("year")),
                        "month": _safe_int(row.get("month")),
                        "mspr": mspr,
                        "change": chg,
                    }
                )

        upgrade_rows: list[dict[str, Any]] = []
        if isinstance(upgrades, list):
            for row in upgrades[:8]:
                if not isinstance(row, dict):
                    continue
                upgrade_rows.append(
                    {
                        "symbol": _safe_str(row.get("symbol")),
                        "company": _safe_str(row.get("company")),
                        "from_grade": _safe_str(row.get("fromGrade")),
                        "to_grade": _safe_str(row.get("toGrade")),
                        "action": _safe_str(row.get("action")).lower(),
                        "grade_time": _to_iso_utc(row.get("gradeTime"))
                        or _safe_str(row.get("gradeTime")),
                    }
                )

        filing_rows: list[dict[str, Any]] = []
        if isinstance(sec_filings, list):
            for row in sec_filings[:6]:
                if not isinstance(row, dict):
                    continue
                filing_rows.append(
                    {
                        "form": _safe_str(row.get("form")),
                        "filed_date": _safe_str(row.get("filedDate")),
                        "accepted_date": _safe_str(row.get("acceptedDate")),
                        "report_url": _safe_str(row.get("reportUrl")),
                        "filing_url": _safe_str(row.get("filingUrl")),
                    }
                )

        dividend_rows: list[dict[str, Any]] = []
        if isinstance(dividends, list):
            for row in dividends[:8]:
                if not isinstance(row, dict):
                    continue
                dividend_rows.append(
                    {
                        "amount": _safe_float(row.get("amount")),
                        "ex_date": _safe_str(row.get("exDate")),
                        "pay_date": _safe_str(row.get("payDate")),
                        "currency": _safe_str(row.get("currency")),
                        "frequency": _safe_int(row.get("freq")),
                    }
                )

        split_rows: list[dict[str, Any]] = []
        if isinstance(splits, list):
            for row in splits[:6]:
                if not isinstance(row, dict):
                    continue
                split_rows.append(
                    {
                        "date": _safe_str(row.get("date")),
                        "from_factor": _safe_float(row.get("fromFactor")),
                        "to_factor": _safe_float(row.get("toFactor")),
                    }
                )

        upcoming_earnings: list[dict[str, Any]] = []
        if isinstance(earnings_calendar, dict):
            cal_rows = earnings_calendar.get("earningsCalendar") or []
            for row in cal_rows[:4]:
                if not isinstance(row, dict):
                    continue
                upcoming_earnings.append(
                    {
                        "symbol": _safe_str(row.get("symbol")),
                        "date": _safe_str(row.get("date")),
                        "hour": _safe_str(row.get("hour")),
                        "year": _safe_int(row.get("year")),
                        "quarter": _safe_int(row.get("quarter")),
                        "eps_estimate": _safe_float(row.get("epsEstimate")),
                        "revenue_estimate": _safe_float(row.get("revenueEstimate")),
                    }
                )

        news_sentiment_block: dict[str, Any] = {}
        if isinstance(news_sentiment, dict):
            sentiment = news_sentiment.get("sentiment") or {}
            news_sentiment_block = {
                "buzz_articles_in_last_week": _safe_int(
                    (news_sentiment.get("buzz") or {}).get("articlesInLastWeek")
                ),
                "buzz_weekly_avg": _safe_float((news_sentiment.get("buzz") or {}).get("weeklyAverage")),
                "company_news_score": _safe_float(news_sentiment.get("companyNewsScore")),
                "sector_avg_news_score": _safe_float(news_sentiment.get("sectorAverageNewsScore")),
                "bullish_percent": _safe_float(sentiment.get("bullishPercent")),
                "bearish_percent": _safe_float(sentiment.get("bearishPercent")),
            }

        recent_quarter_revenue = None
        recent_quarter_eps = None
        if isinstance(series_quarterly, dict):
            ttm_revenue = series_quarterly.get("revenuePerShareTTM") or []
            if isinstance(ttm_revenue, list) and ttm_revenue:
                latest = ttm_revenue[0]
                if isinstance(latest, dict):
                    recent_quarter_revenue = _safe_float(latest.get("v"))

        core_quality_checks = {
            "profile": _has_value(profile.get("name")) if isinstance(profile, dict) else False,
            "quote": _has_value(_safe_float(quote.get("c"))) if isinstance(quote, dict) else False,
            "metrics": _has_value(metric_block.get("peTTM")) or _has_value(metric_block.get("epsTTM")),
            "news": len(normalized_news) >= 3,
            "recommendations": len(recommendation_history) >= 1,
            "earnings": len(normalized_earnings) >= 1,
            "price_target": _has_value(price_target.get("targetMean")) if isinstance(price_target, dict) else False,
        }
        core_quality_pass = sum(1 for ok in core_quality_checks.values() if ok)
        core_quality_total = len(core_quality_checks)
        quality_ok = core_quality_pass >= 5
        quality_notes: list[str] = []
        if not quality_ok:
            quality_notes.append(
                f"insufficient_core_coverage:{core_quality_pass}/{core_quality_total}"
            )
            errors.append("quality:insufficient_core_coverage")

        success = not errors and quality_ok
        return {
            "enabled": True,
            "ticker": sym,
            "as_of": now.isoformat(),
            "ok": success,
            "errors": errors,
            "quality": {
                "ok": quality_ok,
                "core_checks_passed": core_quality_pass,
                "core_checks_total": core_quality_total,
                "core_checks": core_quality_checks,
                "notes": quality_notes,
            },
            "profile": {
                "name": _safe_str(profile.get("name")),
                "exchange": _safe_str(profile.get("exchange")),
                "finnhub_industry": _safe_str(profile.get("finnhubIndustry")),
                "market_cap": _safe_float(profile.get("marketCapitalization")),
                "country": _safe_str(profile.get("country")),
                "currency": _safe_str(profile.get("currency")),
                "share_outstanding": _safe_float(profile.get("shareOutstanding")),
                "ipo": _safe_str(profile.get("ipo")),
                "weburl": _safe_str(profile.get("weburl")),
                "phone": _safe_str(profile.get("phone")),
                "logo": _safe_str(profile.get("logo")),
                "ggroup": _safe_str(profile.get("ggroup")),
                "gsubind": _safe_str(profile.get("gsubind")),
            },
            "quote": {
                "current": _safe_float(quote.get("c")),
                "change": _safe_float(quote.get("d")),
                "change_percent": _safe_float(quote.get("dp")),
                "high": _safe_float(quote.get("h")),
                "low": _safe_float(quote.get("l")),
                "open": _safe_float(quote.get("o")),
                "previous_close": _safe_float(quote.get("pc")),
                "timestamp": _to_iso_utc(quote.get("t")),
            },
            "price_target": {
                "mean": _safe_float(price_target.get("targetMean")),
                "high": _safe_float(price_target.get("targetHigh")),
                "low": _safe_float(price_target.get("targetLow")),
                "median": _safe_float(price_target.get("targetMedian")),
                "last_updated": _safe_str(price_target.get("lastUpdated")),
                "number_of_analysts": _safe_int(price_target.get("numberOfAnalysts")),
            },
            "recommendation_trends": {
                "latest_period": _safe_str(latest_rec.get("period")),
                "buy": _safe_int(latest_rec.get("buy")) or 0 if latest_rec else 0,
                "hold": _safe_int(latest_rec.get("hold")) or 0 if latest_rec else 0,
                "sell": _safe_int(latest_rec.get("sell")) or 0 if latest_rec else 0,
                "strong_buy": _safe_int(latest_rec.get("strongBuy")) or 0 if latest_rec else 0,
                "strong_sell": _safe_int(latest_rec.get("strongSell")) or 0 if latest_rec else 0,
                "history": recommendation_history,
            },
            "news": normalized_news,
            "earnings": normalized_earnings,
            "metrics": {
                "pe_ttm": _safe_float(metric_block.get("peTTM")),
                "pe_annual": _safe_float(metric_block.get("peAnnual")),
                "forward_pe": _safe_float(
                    metric_block.get("forwardPE") or metric_block.get("peExclExtraTTM")
                ),
                "pb_annual": _safe_float(metric_block.get("pbAnnual")),
                "ps_ttm": _safe_float(metric_block.get("psTTM")),
                "ev_to_ebitda": _safe_float(metric_block.get("evToEbitdaTTM")),
                "ev_to_sales": _safe_float(metric_block.get("evToSalesTTM")),
                "eps_ttm": _safe_float(metric_block.get("epsTTM")),
                "eps_annual": _safe_float(metric_block.get("epsAnnual")),
                "revenue_ttm": _safe_float(
                    metric_block.get("revenueTTM") or metric_block.get("revenuePerShareTTM")
                ),
                "revenue_growth_ttm_yoy": _safe_float(metric_block.get("revenueGrowthTTMYoy")),
                "revenue_growth_5y": _safe_float(metric_block.get("revenueGrowth5Y")),
                "eps_growth_ttm_yoy": _safe_float(metric_block.get("epsGrowthTTMYoy")),
                "eps_growth_5y": _safe_float(metric_block.get("epsGrowth5Y")),
                "net_margin_ttm": _safe_float(metric_block.get("netMarginTTM")),
                "operating_margin_ttm": _safe_float(metric_block.get("operatingMarginTTM")),
                "gross_margin_ttm": _safe_float(metric_block.get("grossMarginTTM")),
                "fcf_margin_ttm": _safe_float(metric_block.get("freeCashFlowMarginTTM")),
                "roe_ttm": _safe_float(metric_block.get("roeTTM")),
                "roa_ttm": _safe_float(metric_block.get("roaTTM")),
                "roic_ttm": _safe_float(metric_block.get("roicTTM")),
                "debt_to_equity_quarterly": _safe_float(
                    metric_block.get("totalDebt/totalEquityQuarterly")
                ),
                "debt_to_equity_annual": _safe_float(
                    metric_block.get("totalDebt/totalEquityAnnual")
                ),
                "current_ratio_quarterly": _safe_float(metric_block.get("currentRatioQuarterly")),
                "quick_ratio_quarterly": _safe_float(metric_block.get("quickRatioQuarterly")),
                "interest_coverage_ttm": _safe_float(metric_block.get("netInterestCoverageTTM")),
                "dividend_yield_ttm": _safe_float(
                    metric_block.get("dividendYieldIndicatedAnnual")
                    or metric_block.get("currentDividendYieldTTM")
                ),
                "payout_ratio_ttm": _safe_float(metric_block.get("payoutRatioTTM")),
                "52week_high": _safe_float(metric_block.get("52WeekHigh")),
                "52week_low": _safe_float(metric_block.get("52WeekLow")),
                "52week_high_date": _safe_str(metric_block.get("52WeekHighDate")),
                "52week_low_date": _safe_str(metric_block.get("52WeekLowDate")),
                "52week_price_return_daily": _safe_float(
                    metric_block.get("52WeekPriceReturnDaily")
                ),
                "ytd_price_return_daily": _safe_float(metric_block.get("ytdPriceReturnDaily")),
                "beta": _safe_float(metric_block.get("beta")),
                "book_value_per_share_annual": _safe_float(
                    metric_block.get("bookValuePerShareAnnual")
                ),
                "tangible_book_value_per_share_quarterly": _safe_float(
                    metric_block.get("tangibleBookValuePerShareQuarterly")
                ),
                "shares_outstanding": _safe_float(metric_block.get("sharesOutstanding")),
                "recent_quarter_revenue_per_share": recent_quarter_revenue,
                "recent_quarter_eps": recent_quarter_eps,
            },
            "peers": normalized_peers,
            "insider_transactions": {
                # Open-market scoring only (P/S codes). Awards/exercises/tax
                # withholding are excluded because they are non-discretionary.
                "rows": insider_data_rows[:12],
                "net_shares_180d": open_market_shares,
                "net_dollars_180d": round(open_market_value, 2),
                "buy_count_180d": open_market_buys,
                "sell_count_180d": open_market_sells,
                "scoring_note": "Open-market transactions only (Form-4 P/S codes). Awards, option exercises, and tax-withholding sales are excluded.",
            },
            "insider_sentiment": {
                "rows": insider_sentiment_rows,
                "net_mspr_6m": round(net_mspr, 2),
                "net_change_6m": net_change,
            },
            "upgrade_downgrade": upgrade_rows,
            "sec_filings": filing_rows,
            "dividends": dividend_rows,
            "splits": split_rows,
            "earnings_calendar": upcoming_earnings,
            "news_sentiment": news_sentiment_block,
        }


def _empty_payload(ticker: str, *, errors: list[str], enabled: bool) -> dict[str, Any]:
    return {
        "enabled": enabled,
        "ticker": ticker.upper().strip(),
        "ok": False,
        "errors": errors,
        "as_of": datetime.now(UTC).isoformat(),
        "quality": {
            "ok": False,
            "core_checks_passed": 0,
            "core_checks_total": 0,
            "core_checks": {},
            "notes": ["snapshot_unavailable"],
        },
        "profile": {},
        "quote": {},
        "price_target": {},
        "recommendation_trends": {"history": []},
        "news": [],
        "earnings": [],
        "metrics": {},
        "peers": [],
        "insider_transactions": {"rows": [], "net_shares_180d": 0, "net_dollars_180d": 0.0,
                                 "buy_count_180d": 0, "sell_count_180d": 0},
        "insider_sentiment": {"rows": [], "net_mspr_6m": 0.0, "net_change_6m": 0},
        "upgrade_downgrade": [],
        "sec_filings": [],
        "dividends": [],
        "splits": [],
        "earnings_calendar": [],
        "news_sentiment": {},
    }


def get_finnhub_research_snapshot(
    ticker: str,
    *,
    skill_dir: Path | None = None,
) -> dict[str, Any]:
    """Load a normalized Finnhub snapshot for research workflows.

    Returns a stable payload shape even when Finnhub is not configured or all
    requests fail. Callers should inspect ``ok``/``errors`` to decide whether
    to render degraded badges or show a fully populated dossier section.
    """
    sd = skill_dir or SKILL_DIR
    api_key = get_finnhub_api_key(sd)
    if not api_key:
        return _empty_payload(ticker, errors=["finnhub_api_key_missing"], enabled=False)

    cache_enabled = get_finnhub_cache_enabled(sd)
    cache_hours = get_finnhub_cache_hours(sd)
    if cache_enabled:
        cached = _cached_payload(sd, ticker, success_ttl_hours=cache_hours)
        if cached is not None:
            return cached

    client = FinnhubClient(
        api_key=api_key,
        timeout_sec=get_finnhub_timeout_sec(sd),
        max_retries=get_finnhub_max_retries(sd),
        retry_backoff_cap_sec=get_finnhub_retry_backoff_cap_sec(sd),
        rate_limit_per_min=get_finnhub_rate_limit_per_min(sd),
    )
    try:
        payload = client.get_snapshot(
            ticker=ticker,
            news_days=get_finnhub_news_days(sd),
            max_news_items=get_finnhub_max_news_items(sd),
        )
        if cache_enabled:
            _remember_payload(sd, ticker, payload)
        return payload
    except Exception as exc:  # noqa: BLE001 — never raise from snapshot.
        LOG.warning("Finnhub snapshot raised unexpectedly: %s", exc)
        payload = _empty_payload(ticker, errors=[f"snapshot:{type(exc).__name__}"], enabled=True)
        if cache_enabled:
            _remember_payload(sd, ticker, payload)
        return payload


def _normalize_finnhub_earnings_calendar(payload: Any, *, symbol: str) -> list[dict[str, Any]]:
    """Normalize Finnhub calendar/earnings rows to announcement-date keyed records."""
    sym = _cache_key(symbol)
    rows_out: list[dict[str, Any]] = []
    cal_rows: list[Any] = []
    if isinstance(payload, dict):
        cal_rows = payload.get("earningsCalendar") or []
    elif isinstance(payload, list):
        cal_rows = payload
    if not isinstance(cal_rows, list):
        return rows_out
    for row in cal_rows:
        if not isinstance(row, dict):
            continue
        row_sym = _safe_str(row.get("symbol")).upper()
        if row_sym and row_sym != sym:
            continue
        date_str = _safe_str(row.get("date"))
        if not date_str:
            continue
        actual_eps = _safe_float(row.get("epsActual"))
        estimate_eps = _safe_float(row.get("epsEstimate"))
        if actual_eps is None and estimate_eps is None:
            continue
        rows_out.append(
            {
                "date": date_str,
                "actual_eps": actual_eps,
                "estimate_eps": estimate_eps,
                "quarter": _safe_int(row.get("quarter")),
                "year": _safe_int(row.get("year")),
                "source": "calendar/earnings",
            }
        )
    rows_out.sort(key=lambda r: r.get("date") or "", reverse=True)
    return rows_out


def _normalize_finnhub_stock_earnings(payload: Any) -> list[dict[str, Any]]:
    """Fallback: Finnhub stock/earnings uses fiscal period end as the date key."""
    rows = payload if isinstance(payload, list) else []
    rows_out: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        period = _safe_str(row.get("period"))
        if not period:
            continue
        actual_eps = _safe_float(row.get("actual"))
        estimate_eps = _safe_float(row.get("estimate"))
        if actual_eps is None and estimate_eps is None:
            continue
        rows_out.append(
            {
                "date": period,
                "actual_eps": actual_eps,
                "estimate_eps": estimate_eps,
                "quarter": _safe_int(row.get("quarter")),
                "year": _safe_int(row.get("year")),
                "source": "stock/earnings",
            }
        )
    rows_out.sort(key=lambda r: r.get("date") or "", reverse=True)
    return rows_out


def get_finnhub_earnings_history(
    ticker: str,
    *,
    skill_dir: Path | None = None,
    history_years: int = 12,
) -> dict[str, Any]:
    """Fetch historical earnings rows for PEAD enrichment.

    Primary source is ``calendar/earnings`` (announcement dates). When that
    returns no rows, falls back to ``stock/earnings`` keyed by fiscal period
    end (less precise for lookback windows).
    """
    sd = skill_dir or SKILL_DIR
    sym = _cache_key(ticker)
    api_key = get_finnhub_api_key(sd)
    if not api_key:
        return {
            "ok": False,
            "ticker": sym,
            "rows": [],
            "errors": ["finnhub_api_key_missing"],
            "as_of": datetime.now(UTC).isoformat(),
        }

    now = datetime.now(UTC)
    start = now - timedelta(days=max(365, int(history_years) * 365))
    errors: list[str] = []
    client = FinnhubClient(
        api_key=api_key,
        timeout_sec=get_finnhub_timeout_sec(sd),
        max_retries=get_finnhub_max_retries(sd),
        retry_backoff_cap_sec=get_finnhub_retry_backoff_cap_sec(sd),
        rate_limit_per_min=get_finnhub_rate_limit_per_min(sd),
    )
    calendar_payload = client._get_json(
        "calendar/earnings",
        {"symbol": sym, "from": _fmt_date(start), "to": _fmt_date(now)},
        label="earnings_history_calendar",
        errors=errors,
    )
    rows = _normalize_finnhub_earnings_calendar(calendar_payload, symbol=sym)
    if not rows:
        stock_payload = client._get_json(
            "stock/earnings",
            {"symbol": sym, "limit": 40},
            label="earnings_history_stock",
            errors=errors,
        )
        rows = _normalize_finnhub_stock_earnings(stock_payload)

    ok = bool(rows)
    return {
        "ok": ok,
        "ticker": sym,
        "rows": rows,
        "errors": errors if not ok else [],
        "as_of": now.isoformat(),
    }
