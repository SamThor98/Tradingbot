"""Provider layer: the only place raw vendor JSON becomes cockpit DTOs.

Each provider exposes pure ``normalize_*`` functions (transform an
already-fetched raw dict into a typed contract — unit-testable offline) and,
where applicable, thin ``build()`` methods that fetch via the existing
``market_data`` / ``execution`` functions and then normalize.

Phase 0 ships these behind ``COCKPIT_PROVIDERS_MODE`` (default ``off``). In
shadow they run alongside existing endpoints for parity comparison; routes do
not consume them until Phase 1.
"""

from __future__ import annotations

from pathlib import Path

from core.providers.execution_provider import ExecutionProvider
from core.providers.market_provider import MarketContextProvider
from core.providers.options_provider import OptionsProvider
from core.providers.portfolio_provider import PortfolioProvider
from core.providers.symbol_provider import SymbolIntelProvider

__all__ = [
    "MarketContextProvider",
    "SymbolIntelProvider",
    "PortfolioProvider",
    "ExecutionProvider",
    "OptionsProvider",
    "cockpit_providers_mode",
    "cockpit_providers_enabled",
]


def cockpit_providers_mode(skill_dir: Path | None = None) -> str:
    """Rollout mode for the cockpit provider layer: off | shadow | live."""
    try:
        from config import get_cockpit_providers_mode

        return get_cockpit_providers_mode(skill_dir)
    except Exception:
        return "off"


def cockpit_providers_enabled(skill_dir: Path | None = None) -> bool:
    """True when providers should run at all (shadow or live)."""
    return cockpit_providers_mode(skill_dir) in {"shadow", "live"}
