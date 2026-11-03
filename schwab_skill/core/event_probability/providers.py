"""Prediction-market provider adapters.

The interface is *event-first*: callers ask for a known ``source_market_id``
taken from the exposure catalog, rather than searching by ticker. That keeps
provider-specific matching heuristics out of the valuation path -- an event is
mapped to companies deliberately by an operator, not by string similarity.

Three adapters ship:

``stub``
    Fixture-backed, no network. Default provider and the one used by tests.
``polymarket``
    Live Polymarket Gamma API. No credentials are required today; if a source
    later needs a key, add a ``config.py`` getter and read it here -- never
    hard-code secrets.
``historical_file``
    Point-in-time replay from a frozen JSON store, for backtests.
"""

from __future__ import annotations

import json
import logging
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from core.event_probability._util import http_get_json, parse_timestamp, safe_float
from core.event_probability.schema import (
    RESOLUTION_OPEN,
    EventProbabilityConfig,
    ProviderError,
    RawMarket,
)

LOG = logging.getLogger(__name__)

POLYMARKET_BASE_URL = "https://gamma-api.polymarket.com/markets"
# INTEGRATION POINT: live credentials. Polymarket's public Gamma endpoint needs
# none. A keyed source should resolve its secret through config.py getters
# (see get_event_prob_* in config.py) and pass it in here.
DEFAULT_FIXTURE_PATH = "research/event_market_fixtures.json"


class EventMarketProvider(Protocol):
    """Provider contract. Implementations must not mutate shared state."""

    provider_name: str

    def list_markets(self, *, as_of: datetime) -> list[RawMarket]:
        """Every market this provider can currently serve."""
        ...

    def fetch_market(self, source_market_id: str, *, as_of: datetime) -> RawMarket | None:
        """One market by provider-native id, or ``None`` when absent."""
        ...


class StubEventProvider:
    """Fixture-backed provider. Deterministic and offline."""

    provider_name = "stub"

    def __init__(self, markets: list[RawMarket] | None = None) -> None:
        self._markets = list(markets or [])

    def list_markets(self, *, as_of: datetime) -> list[RawMarket]:
        return list(self._markets)

    def fetch_market(self, source_market_id: str, *, as_of: datetime) -> RawMarket | None:
        wanted = str(source_market_id or "").strip()
        for market in self._markets:
            if market.source_market_id == wanted:
                return market
        return None


class PolymarketEventProvider:
    """Live Polymarket adapter, fetching only explicitly configured markets."""

    provider_name = "polymarket"

    def __init__(self, *, timeout_sec: float, market_ids: list[str] | None = None) -> None:
        self._timeout_sec = max(0.1, float(timeout_sec))
        self._market_ids = [str(m).strip() for m in (market_ids or []) if str(m).strip()]

    def list_markets(self, *, as_of: datetime) -> list[RawMarket]:
        out: list[RawMarket] = []
        for market_id in self._market_ids:
            market = self.fetch_market(market_id, as_of=as_of)
            if market is not None:
                out.append(market)
        return out

    def fetch_market(self, source_market_id: str, *, as_of: datetime) -> RawMarket | None:
        market_id = str(source_market_id or "").strip()
        if not market_id:
            return None
        url = f"{POLYMARKET_BASE_URL}?id={urllib.parse.quote_plus(market_id)}"
        try:
            payload = http_get_json(url, timeout_sec=self._timeout_sec)
        except RuntimeError as exc:
            raise ProviderError(str(exc), detail=market_id) from exc
        rows = payload if isinstance(payload, list) else [payload]
        for row in rows:
            market = _market_from_polymarket_row(row)
            if market is not None:
                return market
        return None


class HistoricalFileEventProvider:
    """Replays a frozen JSON store without leaking post-``as_of`` observations."""

    provider_name = "historical_file"

    def __init__(self, *, markets: list[RawMarket]) -> None:
        self._by_id: dict[str, list[RawMarket]] = {}
        for market in markets:
            self._by_id.setdefault(market.source_market_id, []).append(market)
        for rows in self._by_id.values():
            rows.sort(key=lambda m: m.updated_ts or datetime.min.replace(tzinfo=timezone.utc))

    def list_markets(self, *, as_of: datetime) -> list[RawMarket]:
        out: list[RawMarket] = []
        for market_id in self._by_id:
            market = self.fetch_market(market_id, as_of=as_of)
            if market is not None:
                out.append(market)
        return out

    def fetch_market(self, source_market_id: str, *, as_of: datetime) -> RawMarket | None:
        best: RawMarket | None = None
        for market in self._by_id.get(str(source_market_id or "").strip(), []):
            updated = market.updated_ts
            if updated is not None and updated > as_of:
                continue
            best = market
        return best


def build_provider(
    config: EventProbabilityConfig,
    *,
    skill_dir: Path,
    market_ids: list[str] | None = None,
) -> EventMarketProvider:
    """Instantiate the configured provider, failing back to the stub."""
    provider = str(config.provider or "stub").strip().lower()
    if provider == "polymarket":
        return PolymarketEventProvider(
            timeout_sec=float(config.timeout_ms) / 1000.0,
            market_ids=market_ids,
        )
    if provider == "historical_file":
        raw_path = str(config.historical_snapshot_path or "").strip()
        if not raw_path:
            raise ProviderError("historical_path_missing")
        return load_historical_provider(Path(raw_path))
    return StubEventProvider(markets=load_fixture_markets(skill_dir / DEFAULT_FIXTURE_PATH))


def load_fixture_markets(path: Path) -> list[RawMarket]:
    """Load stub fixtures. A missing file is not an error -- it means no events."""
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        LOG.info("Event-probability fixtures not found at %s; stub provider is empty", path)
        return []
    except (OSError, json.JSONDecodeError) as exc:
        raise ProviderError("fixture_unreadable", detail=str(exc)) from exc
    return _markets_from_rows(payload, default_source="stub")


def load_historical_provider(path: Path) -> HistoricalFileEventProvider:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProviderError("historical_unreadable", detail=str(exc)) from exc
    return HistoricalFileEventProvider(markets=_markets_from_rows(payload, default_source="historical_file"))


def _markets_from_rows(payload: Any, *, default_source: str) -> list[RawMarket]:
    rows = payload.get("markets") if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        raise ProviderError("fixture_shape", detail="expected a list of markets")
    out: list[RawMarket] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        market_id = str(row.get("source_market_id") or row.get("id") or "").strip()
        question = str(row.get("question") or row.get("event_name") or "").strip()
        probability = safe_float(row.get("raw_probability", row.get("probability")))
        if not market_id or not question or probability is None:
            LOG.warning("Skipping malformed fixture market: id=%r question=%r", market_id, question)
            continue
        out.append(
            RawMarket(
                source=str(row.get("source") or default_source).strip() or default_source,
                source_market_id=market_id,
                question=question,
                raw_probability=probability,
                description=str(row.get("description") or ""),
                category_hint=(str(row["event_category"]) if row.get("event_category") else None),
                volume=safe_float(row.get("volume")),
                liquidity=safe_float(row.get("liquidity")),
                spread=safe_float(row.get("spread")),
                resolution_date=parse_timestamp(row.get("resolution_date")),
                updated_ts=parse_timestamp(row.get("updated_ts") or row.get("timestamp")),
                resolution_status=str(row.get("resolution_status") or RESOLUTION_OPEN),
            )
        )
    return out


def _market_from_polymarket_row(row: Any) -> RawMarket | None:
    if not isinstance(row, dict):
        return None
    market_id = str(row.get("id") or row.get("market_id") or "").strip()
    question = str(row.get("question") or row.get("title") or "").strip()
    if not market_id or not question:
        return None
    probability = _polymarket_probability(row)
    if probability is None:
        return None
    closed = bool(row.get("closed")) or str(row.get("closed") or "").lower() == "true"
    return RawMarket(
        source="polymarket",
        source_market_id=market_id,
        question=question,
        raw_probability=probability,
        description=str(row.get("description") or ""),
        volume=safe_float(row.get("volume24hr") or row.get("volume") or row.get("volumeNum")),
        liquidity=safe_float(row.get("liquidity") or row.get("liquidityNum")),
        spread=safe_float(row.get("spread")),
        resolution_date=parse_timestamp(row.get("endDate") or row.get("closeTime")),
        updated_ts=parse_timestamp(row.get("updatedAt") or row.get("lastTradeTime")),
        resolution_status="unknown" if closed else RESOLUTION_OPEN,
    )


def _polymarket_probability(row: dict[str, Any]) -> float | None:
    prices = row.get("outcomePrices")
    if isinstance(prices, str):
        try:
            prices = json.loads(prices)
        except json.JSONDecodeError:
            prices = None
    if isinstance(prices, list) and prices:
        return safe_float(prices[0])
    value = safe_float(row.get("probability"))
    if value is None:
        return None
    return value / 100.0 if value > 1.0 else value
