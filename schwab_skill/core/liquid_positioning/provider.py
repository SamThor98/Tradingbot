"""Hyperliquid public-info provider for Liquid (xyz) perp market data.

No credentials: ``POST https://api.hyperliquid.xyz/info``. This is the
server-collectable leg of the Liquid integration. The Liquid MCP
(cohort positioning, news) stays a Cursor-side research tool and is
never called from dashboard code.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any

import requests

from core.liquid_positioning.normalizer import normalize_meta_and_ctxs
from core.liquid_positioning.schema import CollectDiagnostics, LiquidConfig, LiquidSnapshot

LOG = logging.getLogger(__name__)


class HyperliquidXyzProvider:
    def __init__(self, config: LiquidConfig) -> None:
        self.config = config

    def _post(self, payload: dict[str, Any]) -> Any:
        """POST with backoff on 429/5xx (Hyperliquid throttles paging bursts)."""
        last_exc: Exception | None = None
        for attempt in range(5):
            try:
                resp = requests.post(
                    self.config.api_url, json=payload, timeout=self.config.timeout_sec
                )
                if resp.status_code in (429, 500, 502, 503, 504) and attempt < 4:
                    time.sleep(min(2**attempt, 8))
                    continue
                resp.raise_for_status()
                return resp.json()
            except Exception as exc:
                last_exc = exc
                if attempt < 4:
                    time.sleep(min(2**attempt, 8))
        raise last_exc if last_exc else RuntimeError("liquid provider request failed")

    def get_snapshots(self, ts: datetime | None = None) -> tuple[list[LiquidSnapshot], CollectDiagnostics]:
        """One call returns every xyz market's funding / OI / price / volume."""
        diag = CollectDiagnostics()
        try:
            payload = self._post({"type": "metaAndAssetCtxs", "dex": self.config.dex})
        except Exception as exc:  # provider outage degrades to uncovered, never fatal
            diag.provider_errors += 1
            diag.error_codes.append(f"meta_ctxs:{type(exc).__name__}")
            LOG.warning("Liquid metaAndAssetCtxs failed: %s", exc)
            return [], diag
        snapshots, malformed = normalize_meta_and_ctxs(
            payload, min_oi_usd=self.config.min_oi_usd, ts=ts
        )
        diag.processed = len(snapshots) + malformed
        diag.malformed = malformed
        diag.thin = sum(1 for s in snapshots if s.is_thin)
        return snapshots, diag

    def get_funding_history(
        self, ticker: str, start_ms: int = 0, end_ms: int | None = None
    ) -> list[dict[str, Any]]:
        """Full hourly funding history for one ticker, paginated (500/page).

        History starts at each market's listing (xyz:AAPL: 2025-11-21).
        Returns raw rows: coin, fundingRate, premium, time (ms).
        """
        coin = f"{self.config.dex}:{ticker.upper()}"
        end = end_ms or int(time.time() * 1000)
        rows: list[dict[str, Any]] = []
        cursor = start_ms
        while cursor <= end:
            page = self._post(
                {"type": "fundingHistory", "coin": coin, "startTime": cursor, "endTime": end}
            )
            if not page:
                break
            rows.extend(page)
            last_time = int(page[-1].get("time") or 0)
            if len(page) < 500 or last_time <= cursor:
                break
            cursor = last_time + 1
            time.sleep(0.25)  # stay under the info-API rate limit while paging
        return rows

    def get_daily_candles(self, ticker: str, start_ms: int = 0, end_ms: int | None = None) -> list[dict[str, Any]]:
        coin = f"{self.config.dex}:{ticker.upper()}"
        end = end_ms or int(time.time() * 1000)
        result = self._post(
            {
                "type": "candleSnapshot",
                "req": {"coin": coin, "interval": "1d", "startTime": start_ms, "endTime": end},
            }
        )
        return list(result or [])


def utc_now() -> datetime:
    return datetime.now(timezone.utc)
