"""
Finnhub data aggregation helpers for research dossier generation.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import requests

from config import (
    get_finnhub_api_key,
    get_finnhub_max_news_items,
    get_finnhub_news_days,
    get_finnhub_timeout_sec,
)

LOG = logging.getLogger(__name__)
FINNHUB_BASE = "https://finnhub.io/api/v1"
SKILL_DIR = Path(__file__).resolve().parent


def _safe_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_iso_utc(ts: int | float | None) -> str | None:
    if ts is None:
        return None
    try:
        return datetime.fromtimestamp(float(ts), tz=UTC).isoformat()
    except (TypeError, ValueError, OSError):
        return None


def _fmt_date(dt: datetime) -> str:
    return dt.date().isoformat()


class FinnhubClient:
    """Small resilient wrapper around Finnhub REST APIs."""

    def __init__(
        self,
        *,
        api_key: str,
        timeout_sec: float = 8.0,
        session: requests.Session | None = None,
    ) -> None:
        self.api_key = api_key.strip()
        self.timeout_sec = timeout_sec
        self.session = session or requests.Session()

    def _get_json(self, endpoint: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        merged = {"token": self.api_key}
        if params:
            merged.update(params)
        url = f"{FINNHUB_BASE}/{endpoint.lstrip('/')}"
        resp = self.session.get(url, params=merged, timeout=self.timeout_sec)
        resp.raise_for_status()
        payload = resp.json()
        if isinstance(payload, dict):
            return payload
        return {"raw": payload}

    def _try_get_json(
        self,
        endpoint: str,
        params: dict[str, Any] | None,
        *,
        label: str,
        errors: list[str],
    ) -> dict[str, Any]:
        try:
            return self._get_json(endpoint, params=params)
        except Exception as exc:  # noqa: BLE001
            LOG.warning("Finnhub %s fetch failed: %s", label, exc)
            errors.append(f"{label}:{type(exc).__name__}")
            return {}

    def get_snapshot(
        self,
        ticker: str,
        *,
        news_days: int = 30,
        max_news_items: int = 12,
    ) -> dict[str, Any]:
        sym = ticker.upper().strip()
        now = datetime.now(UTC)
        start = now - timedelta(days=max(1, news_days))
        errors: list[str] = []

        profile = self._try_get_json("stock/profile2", {"symbol": sym}, label="profile2", errors=errors)
        quote = self._try_get_json("quote", {"symbol": sym}, label="quote", errors=errors)
        recommendations = self._try_get_json("stock/recommendation", {"symbol": sym}, label="recommendation", errors=errors)
        price_target = self._try_get_json("stock/price-target", {"symbol": sym}, label="price_target", errors=errors)
        news = self._try_get_json(
            "company-news",
            {"symbol": sym, "from": _fmt_date(start), "to": _fmt_date(now)},
            label="company_news",
            errors=errors,
        )
        earnings = self._try_get_json("stock/earnings", {"symbol": sym, "limit": 8}, label="earnings", errors=errors)
        metrics = self._try_get_json("stock/metric", {"symbol": sym, "metric": "all"}, label="metrics", errors=errors)

        rec_rows = recommendations if isinstance(recommendations, list) else []
        latest_rec = rec_rows[0] if rec_rows else {}
        news_rows = news if isinstance(news, list) else []
        earnings_rows = earnings if isinstance(earnings, list) else []
        metric_block = metrics.get("metric") if isinstance(metrics, dict) else {}

        normalized_news: list[dict[str, Any]] = []
        for row in news_rows[: max(1, max_news_items)]:
            if not isinstance(row, dict):
                continue
            normalized_news.append(
                {
                    "headline": str(row.get("headline") or "").strip(),
                    "summary": str(row.get("summary") or "").strip(),
                    "source": str(row.get("source") or "").strip(),
                    "url": str(row.get("url") or "").strip(),
                    "datetime": _to_iso_utc(row.get("datetime")),
                }
            )

        normalized_earnings: list[dict[str, Any]] = []
        for row in earnings_rows[:8]:
            if not isinstance(row, dict):
                continue
            normalized_earnings.append(
                {
                    "period": str(row.get("period") or "").strip(),
                    "actual": _safe_float(row.get("actual")),
                    "estimate": _safe_float(row.get("estimate")),
                    "surprise": _safe_float(row.get("surprise")),
                    "surprise_percent": _safe_float(row.get("surprisePercent")),
                }
            )

        return {
            "enabled": True,
            "ticker": sym,
            "as_of": now.isoformat(),
            "ok": len(errors) == 0,
            "errors": errors,
            "profile": {
                "name": str(profile.get("name") or "").strip(),
                "exchange": str(profile.get("exchange") or "").strip(),
                "finnhub_industry": str(profile.get("finnhubIndustry") or "").strip(),
                "market_cap": _safe_float(profile.get("marketCapitalization")),
                "country": str(profile.get("country") or "").strip(),
                "currency": str(profile.get("currency") or "").strip(),
                "share_outstanding": _safe_float(profile.get("shareOutstanding")),
                "ipo": str(profile.get("ipo") or "").strip(),
                "weburl": str(profile.get("weburl") or "").strip(),
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
                "last_updated": str(price_target.get("lastUpdated") or "").strip(),
            },
            "recommendation_trends": {
                "latest_period": str(latest_rec.get("period") or "").strip(),
                "buy": int(latest_rec.get("buy") or 0) if latest_rec else 0,
                "hold": int(latest_rec.get("hold") or 0) if latest_rec else 0,
                "sell": int(latest_rec.get("sell") or 0) if latest_rec else 0,
                "strong_buy": int(latest_rec.get("strongBuy") or 0) if latest_rec else 0,
                "strong_sell": int(latest_rec.get("strongSell") or 0) if latest_rec else 0,
            },
            "news": normalized_news,
            "earnings": normalized_earnings,
            "metrics": {
                "pe_ttm": _safe_float(metric_block.get("peTTM")),
                "pb_annual": _safe_float(metric_block.get("pbAnnual")),
                "ps_ttm": _safe_float(metric_block.get("psTTM")),
                "ev_to_ebitda": _safe_float(metric_block.get("evToEbitdaTTM")),
                "ev_to_sales": _safe_float(metric_block.get("evToSalesTTM")),
                "eps_ttm": _safe_float(metric_block.get("epsTTM")),
                "revenue_growth_ttm_yoy": _safe_float(metric_block.get("revenueGrowthTTMYoy")),
                "eps_growth_ttm_yoy": _safe_float(metric_block.get("epsGrowthTTMYoy")),
                "net_margin_ttm": _safe_float(metric_block.get("netMarginTTM")),
                "operating_margin_ttm": _safe_float(metric_block.get("operatingMarginTTM")),
                "roe_ttm": _safe_float(metric_block.get("roeTTM")),
                "roa_ttm": _safe_float(metric_block.get("roaTTM")),
                "debt_to_equity_quarterly": _safe_float(metric_block.get("totalDebt/totalEquityQuarterly")),
                "current_ratio_quarterly": _safe_float(metric_block.get("currentRatioQuarterly")),
                "52week_high": _safe_float(metric_block.get("52WeekHigh")),
                "52week_low": _safe_float(metric_block.get("52WeekLow")),
                "beta": _safe_float(metric_block.get("beta")),
            },
        }


def get_finnhub_research_snapshot(
    ticker: str,
    *,
    skill_dir: Path | None = None,
) -> dict[str, Any]:
    """
    Load a normalized Finnhub snapshot for research workflows.

    Returns a stable payload shape even when Finnhub is not configured.
    """
    sd = skill_dir or SKILL_DIR
    api_key = get_finnhub_api_key(sd)
    if not api_key:
        return {
            "enabled": False,
            "ticker": ticker.upper().strip(),
            "ok": False,
            "errors": ["finnhub_api_key_missing"],
            "as_of": datetime.now(UTC).isoformat(),
            "profile": {},
            "quote": {},
            "price_target": {},
            "recommendation_trends": {},
            "news": [],
            "earnings": [],
            "metrics": {},
        }
    client = FinnhubClient(
        api_key=api_key,
        timeout_sec=get_finnhub_timeout_sec(sd),
    )
    return client.get_snapshot(
        ticker=ticker,
        news_days=get_finnhub_news_days(sd),
        max_news_items=get_finnhub_max_news_items(sd),
    )

