"""SEC companyfacts warehouse for research pricing models.

Fetches ``data.sec.gov`` XBRL companyfacts once per ticker, caches them on
disk, and prices the same 5-year FCF DCF the report stack uses. Click-time
recompute never hits EDGAR or Yahoo.

This is a research layer. It does not change scan ranking, sizing, or
advisory. See ``docs/COMPANYFACTS_PRICING.md``.
"""

from __future__ import annotations

from core.companyfacts.extract import ExtractedStatements, extract_statements
from core.companyfacts.model import (
    build_dcf_section,
    implied_growth,
    price_model,
    sensitivity_table,
)
from core.companyfacts.service import (
    ingest_ticker,
    load_inputs_for_ticker,
    pricing_payload,
    recompute_payload,
)

__all__ = [
    "ExtractedStatements",
    "build_dcf_section",
    "extract_statements",
    "implied_growth",
    "ingest_ticker",
    "load_inputs_for_ticker",
    "price_model",
    "pricing_payload",
    "recompute_payload",
    "sensitivity_table",
]
