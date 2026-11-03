"""Point-in-time universe and fundamental provider protocols.

Adapters must fail-closed on missing credentials or stale as-of snapshots.
Never invent zeros for missing fundamentals.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

PIT_FUNDAMENTAL_FIELDS: tuple[str, ...] = (
    "market_cap",
    "float_shares",
    "institutional_ownership",
    "insider_ownership",
    "estimate_revisions",
    "roe",
)


@runtime_checkable
class PITUniverseProvider(Protocol):
    """As-of index membership. Empty list means fail-closed, not 'all names'."""

    provider_id: str

    def universe_asof(self, asof: str) -> list[str]:
        """Return tickers that were members on calendar date ``asof`` (YYYY-MM-DD)."""

    def wikipedia_now(self) -> list[str]:
        """Today's membership (survivorship-biased). Used only for replay deltas."""

    def data_quality(self) -> dict[str, Any]:
        """Guardrail payload; surface verbatim. Must include status and reason."""


@runtime_checkable
class PITFundamentalProvider(Protocol):
    """As-of fundamentals. Missing fields stay absent (None), never 0.0 fillers."""

    provider_id: str

    def fundamentals_asof(self, ticker: str, asof: str) -> dict[str, float | None]:
        """Return PIT fields dated on/before ``asof``. Omit or None if unknown."""

    def data_quality(self) -> dict[str, Any]:
        """Guardrail payload; surface verbatim."""
