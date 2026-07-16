"""Shared provenance envelope embedded in every cockpit DTO.

Every normalized object must declare where its data came from, how fresh it is,
and how much to trust it. The provider layer translates the lineage ``dict``
emitted by ``market_data`` / ``execution`` into this typed envelope.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field

# "manual" = user-entered data (manual portfolio book), never a vendor feed.
DataSource = Literal["schwab", "yfinance", "polygon", "cache", "computed", "manual", "unknown"]
ConfidenceLevel = Literal["high", "medium", "low"]


def utc_now() -> datetime:
    """Timezone-aware current UTC timestamp (single source of truth for DTOs)."""
    return datetime.now(timezone.utc)


class Provenance(BaseModel):
    """Trust + freshness envelope. Rendered on every cockpit panel."""

    source: DataSource = "unknown"
    # as_of is the timestamp of the *data itself* (e.g. last trade / last bar),
    # not when we fetched it. fetched_at is wall-clock at retrieval time.
    as_of: datetime | None = None
    fetched_at: datetime = Field(default_factory=utc_now)
    confidence: ConfidenceLevel = "medium"
    is_stale: bool = False
    stale_reason: str | None = None
    # Free-form lineage breadcrumbs (provider meta keys) kept for debugging.
    notes: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def computed(cls, confidence: ConfidenceLevel = "high", **notes: Any) -> Provenance:
        """Provenance for values derived locally from already-trusted inputs."""
        return cls(source="computed", as_of=utc_now(), confidence=confidence, notes=dict(notes))

    @classmethod
    def from_lineage(
        cls,
        meta: dict[str, Any] | None,
        *,
        as_of: datetime | None = None,
        is_stale: bool | None = None,
        stale_reason: str | None = None,
    ) -> Provenance:
        """Build a Provenance from a ``market_data`` / ``execution`` lineage dict.

        Understands the lineage keys those modules already emit:
        ``provider``, ``used_fallback`` / ``used_fallback_data``,
        ``fallback_reason``, ``fallback_provider``, ``http_status``,
        and a data-quality label (``data_quality`` or ``_data_quality``).
        """
        meta = meta or {}

        # Resolve effective source: an explicit fallback provider wins.
        source = str(meta.get("fallback_provider") or meta.get("provider") or "unknown").strip().lower()
        if source not in {"schwab", "yfinance", "polygon", "cache", "computed"}:
            source = "unknown"

        used_fallback = bool(meta.get("used_fallback") or meta.get("used_fallback_data"))
        data_quality = str(meta.get("data_quality") or meta.get("_data_quality") or "").strip().lower()

        # Confidence mapping: Schwab-primary + ok quality => high; fallbacks or
        # degraded quality => medium; stale/conflict => low.
        if data_quality in {"stale", "conflict"}:
            confidence: ConfidenceLevel = "low"
        elif used_fallback or source != "schwab" or data_quality == "degraded":
            confidence = "medium"
        else:
            confidence = "high"

        resolved_stale = bool(is_stale) if is_stale is not None else data_quality in {"stale", "conflict"}
        resolved_stale_reason = (
            stale_reason
            or meta.get("fallback_reason")
            or (data_quality if data_quality in {"stale", "conflict", "degraded"} else None)
        )

        notes: dict[str, Any] = {}
        for key in ("fallback_reason", "http_status", "rows", "data_quality", "_data_quality"):
            if meta.get(key) is not None:
                notes[key] = meta.get(key)

        return cls(
            source=source,  # type: ignore[arg-type]
            as_of=as_of,
            confidence=confidence,
            is_stale=resolved_stale,
            stale_reason=(str(resolved_stale_reason)[:200] if resolved_stale_reason else None),
            notes=notes,
        )
