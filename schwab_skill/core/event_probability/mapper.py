"""Event -> company exposure catalog.

Mappings are operator-authored and version-controlled in
``research/event_exposure_catalog.json`` rather than stored mutably in SQL: a
backtest must be able to reconstruct the mapping that was live at a historical
timestamp, and git history plus ``effective_from`` / ``effective_to`` gives that
for free.

Changing a mapping means closing the existing row (set ``effective_to``) and
appending a new one. Editing an open row in place silently rewrites history and
is the one thing that breaks point-in-time correctness here.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from core.event_probability._util import parse_timestamp, safe_float
from core.event_probability.schema import ExposureMapping
from core.event_probability.valuation import FUNDAMENTAL_VARIABLES

LOG = logging.getLogger(__name__)

DEFAULT_CATALOG_PATH = "research/event_exposure_catalog.json"

VALID_DIRECTIONS = frozenset({"positive", "negative", "mixed", "unknown"})


class ExposureCatalog:
    """Immutable, in-memory view of the exposure catalog."""

    def __init__(self, mappings: list[ExposureMapping], *, errors: list[str] | None = None) -> None:
        self._mappings = list(mappings)
        self._errors = list(errors or [])

    @property
    def errors(self) -> list[str]:
        """Rows rejected at load time. Surfaced as ``mapping_errors``."""
        return list(self._errors)

    def __len__(self) -> int:
        return len(self._mappings)

    def all_mappings(self) -> list[ExposureMapping]:
        return list(self._mappings)

    def for_ticker(self, ticker: str, *, as_of: datetime) -> list[ExposureMapping]:
        wanted = str(ticker or "").strip().upper()
        return [m for m in self._mappings if m.ticker == wanted and m.is_effective(as_of)]

    def for_event(self, event_id: str, *, as_of: datetime) -> list[ExposureMapping]:
        wanted = str(event_id or "").strip()
        return [m for m in self._mappings if m.event_id == wanted and m.is_effective(as_of)]

    def event_ids(self, *, as_of: datetime | None = None) -> list[str]:
        seen: dict[str, None] = {}
        for mapping in self._mappings:
            if as_of is not None and not mapping.is_effective(as_of):
                continue
            seen.setdefault(mapping.event_id, None)
        return list(seen)

    def tickers(self, *, as_of: datetime | None = None) -> list[str]:
        seen: dict[str, None] = {}
        for mapping in self._mappings:
            if as_of is not None and not mapping.is_effective(as_of):
                continue
            seen.setdefault(mapping.ticker, None)
        return list(seen)

    def source_market_ids(self) -> list[str]:
        """Provider-native ids implied by the catalog (``source:market_id``)."""
        seen: dict[str, None] = {}
        for mapping in self._mappings:
            _, _, market_id = mapping.event_id.partition(":")
            if market_id:
                seen.setdefault(market_id, None)
        return list(seen)


def load_catalog(path: Path | str) -> ExposureCatalog:
    """Load and validate the catalog. A missing file yields an empty catalog."""
    catalog_path = Path(path)
    try:
        payload = json.loads(catalog_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        LOG.info("Exposure catalog not found at %s; no event exposures configured", catalog_path)
        return ExposureCatalog([])
    except (OSError, json.JSONDecodeError) as exc:
        LOG.warning("Exposure catalog unreadable at %s: %s", catalog_path, exc)
        return ExposureCatalog([], errors=[f"catalog_unreadable:{type(exc).__name__}"])

    rows = payload.get("mappings") if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        LOG.warning("Exposure catalog %s has no 'mappings' list", catalog_path)
        return ExposureCatalog([], errors=["catalog_shape"])

    mappings: list[ExposureMapping] = []
    errors: list[str] = []
    seen_keys: set[tuple[str, str, str, str]] = set()
    for index, row in enumerate(rows):
        try:
            mapping = _mapping_from_row(row)
        except ValueError as exc:
            LOG.warning("Exposure catalog row %d rejected: %s", index, exc)
            errors.append(f"row_{index}:{exc}")
            continue
        key = (
            mapping.event_id,
            mapping.ticker,
            mapping.fundamental_variable,
            mapping.effective_from.isoformat() if mapping.effective_from else "",
        )
        if key in seen_keys:
            LOG.warning("Duplicate exposure mapping ignored: %s", key)
            errors.append(f"row_{index}:duplicate_mapping")
            continue
        seen_keys.add(key)
        mappings.append(mapping)
    return ExposureCatalog(mappings, errors=errors)


def _mapping_from_row(row: Any) -> ExposureMapping:
    if not isinstance(row, dict):
        raise ValueError("not_an_object")
    event_id = str(row.get("event_id") or "").strip()
    ticker = str(row.get("ticker") or "").strip().upper()
    variable = str(row.get("fundamental_variable") or "").strip()
    if not event_id:
        raise ValueError("event_id_missing")
    if not ticker:
        raise ValueError("ticker_missing")
    if variable not in FUNDAMENTAL_VARIABLES:
        raise ValueError(f"unsupported_fundamental_variable:{variable or 'missing'}")

    impact_if_yes = safe_float(row.get("impact_if_yes"))
    if impact_if_yes is None:
        raise ValueError("impact_if_yes_missing")
    impact_if_no = safe_float(row.get("impact_if_no"))

    materiality = safe_float(row.get("materiality"))
    materiality = 1.0 if materiality is None else max(0.0, min(1.0, materiality))
    confidence = safe_float(row.get("confidence"))
    confidence = 0.5 if confidence is None else max(0.0, min(1.0, confidence))

    direction = str(row.get("direction") or "").strip().lower()
    if direction not in VALID_DIRECTIONS:
        direction = "negative" if impact_if_yes < 0 else "positive" if impact_if_yes > 0 else "unknown"

    effective_from = parse_timestamp(row.get("effective_from"))
    effective_to = parse_timestamp(row.get("effective_to"))
    if effective_from is not None and effective_to is not None and effective_to <= effective_from:
        raise ValueError("effective_window_inverted")

    return ExposureMapping(
        event_id=event_id,
        ticker=ticker,
        fundamental_variable=variable,
        impact_if_yes=impact_if_yes,
        impact_if_no=0.0 if impact_if_no is None else impact_if_no,
        industry=(str(row["industry"]).strip() or None) if row.get("industry") else None,
        exposure_type=str(row.get("exposure_type") or "fundamental").strip() or "fundamental",
        direction=direction,
        materiality=materiality,
        confidence=confidence,
        effective_from=effective_from,
        effective_to=effective_to,
        notes=str(row.get("notes") or "").strip(),
    )
