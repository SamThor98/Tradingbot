"""Liquid (xyz perp) positioning layer — shadow-first, read-only.

Collects funding / open-interest / price snapshots for Liquid's
Hyperliquid xyz markets via the public info API. No execution, no
Liquid MCP calls from server code. Promotion to any rank influence
requires the capped-replay gates in signal_edge_ledger.json.
"""

from core.liquid_positioning.schema import CollectDiagnostics, LiquidConfig, LiquidSnapshot

__all__ = ["CollectDiagnostics", "LiquidConfig", "LiquidSnapshot"]
