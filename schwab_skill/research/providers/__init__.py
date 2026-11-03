"""PIT provider adapters (stub for tests, Sharadar fail-closed without a key)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from research.pit_protocols import PITFundamentalProvider, PITUniverseProvider


def resolve_pit_providers(
    *,
    skill_dir: Path | None = None,
    fixture: dict[str, Any] | None = None,
) -> tuple[PITUniverseProvider, PITFundamentalProvider]:
    """Pick stub vs Sharadar from config. Sharadar without a key fails closed."""
    from config import get_pit_universe_provider, get_sharadar_api_key
    from research.providers.sharadar_pit import SharadarPITAdapter
    from research.providers.stub_pit import StubPITProvider

    name = get_pit_universe_provider(skill_dir)
    if fixture is not None:
        stub = StubPITProvider(fixture)
        return stub, stub
    if name == "sharadar":
        adapter = SharadarPITAdapter(skill_dir=skill_dir)
        if not get_sharadar_api_key(skill_dir):
            return adapter, adapter
        return adapter, adapter
    stub = StubPITProvider()
    return stub, stub
