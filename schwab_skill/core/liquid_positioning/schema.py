"""Normalized shapes for the Liquid (xyz perp) positioning layer.

Liquid's tradeable equity/commodity perps sit on the Hyperliquid ``xyz``
HIP-3 dex. The public Hyperliquid info API exposes live funding, open
interest, mark price and 24h volume for every xyz market without auth,
plus hourly funding history and daily candles back to each market's
listing (xyz:AAPL: 2025-11-21, verified 2026-10-21).

Liquid's proprietary cohort positioning (smart-money / crowd skew) is
only available through the Liquid MCP and is deliberately NOT part of
this schema: it has no history and cannot be collected server-side.
Everything here is a point-in-time market snapshot, safe to store and
later join point-in-time into backtests.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(slots=True)
class LiquidSnapshot:
    """One point-in-time observation of one xyz perp market."""

    ticker: str  # bare symbol, e.g. AAPL (source coin is xyz:AAPL)
    source_coin: str  # xyz:AAPL
    ts: datetime
    mark_px: float | None = None
    funding_rate: float | None = None  # hourly funding rate, decimal
    open_interest_coin: float | None = None
    open_interest_usd: float | None = None
    day_volume_usd: float | None = None
    premium: float | None = None
    max_leverage: int | None = None
    is_thin: bool = False  # OI below the configured no-signal floor

    def to_dict(self) -> dict[str, Any]:
        return {
            "ticker": self.ticker,
            "source_coin": self.source_coin,
            "ts": self.ts.isoformat(),
            "mark_px": self.mark_px,
            "funding_rate": self.funding_rate,
            "open_interest_coin": self.open_interest_coin,
            "open_interest_usd": self.open_interest_usd,
            "day_volume_usd": self.day_volume_usd,
            "premium": self.premium,
            "max_leverage": self.max_leverage,
            "is_thin": self.is_thin,
        }


@dataclass(slots=True)
class LiquidConfig:
    """Resolved ``LIQUID_*`` settings (see config.py getters)."""

    mode: str = "shadow"  # off | shadow | live (plugin rollout; live never executes)
    collect_enabled: bool = False
    dex: str = "xyz"
    api_url: str = "https://api.hyperliquid.xyz/info"
    min_oi_usd: float = 1_000_000.0
    timeout_sec: float = 15.0

    def is_active(self) -> bool:
        return self.mode != "off"


@dataclass
class CollectDiagnostics:
    """Counters surfaced by the collector CLI and (later) diagnostics tab."""

    processed: int = 0
    stored: int = 0
    thin: int = 0
    malformed: int = 0
    provider_errors: int = 0
    error_codes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "processed": self.processed,
            "stored": self.stored,
            "thin": self.thin,
            "malformed": self.malformed,
            "provider_errors": self.provider_errors,
            "error_codes": list(self.error_codes),
        }
