"""Convert Hyperliquid ``metaAndAssetCtxs`` payloads to LiquidSnapshots."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from core.liquid_positioning.schema import LiquidSnapshot

LOG = logging.getLogger(__name__)


def _f(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def normalize_meta_and_ctxs(
    payload: Any,
    *,
    min_oi_usd: float,
    ts: datetime | None = None,
) -> tuple[list[LiquidSnapshot], int]:
    """Return (snapshots, malformed_count) from a metaAndAssetCtxs response.

    Payload shape: ``[meta, contexts]`` where meta.universe[i] pairs with
    contexts[i]. Open interest arrives in coin units; USD OI is derived as
    ``openInterest * markPx`` (validated against Liquid's own AAPL figure:
    178,089 coin x $343.63 = $61.2M vs Liquid's reported $61.3M, 2026-10-21).
    Markets below ``min_oi_usd`` are kept but flagged ``is_thin`` so
    downstream code treats them as no-signal instead of inventing a skew.
    """
    stamp = ts or datetime.now(timezone.utc)
    if not (isinstance(payload, list) and len(payload) == 2):
        return [], 1
    meta, ctxs = payload
    universe = (meta or {}).get("universe") or []
    if not isinstance(ctxs, list):
        return [], 1

    snapshots: list[LiquidSnapshot] = []
    malformed = 0
    for asset, ctx in zip(universe, ctxs):
        name = (asset or {}).get("name") or ""
        if not name or not isinstance(ctx, dict):
            malformed += 1
            continue
        mark_px = _f(ctx.get("markPx"))
        oi_coin = _f(ctx.get("openInterest"))
        oi_usd = oi_coin * mark_px if (oi_coin is not None and mark_px is not None) else None
        ticker = name.split(":", 1)[-1].upper()
        try:
            max_lev = int(asset.get("maxLeverage")) if asset.get("maxLeverage") is not None else None
        except (TypeError, ValueError):
            max_lev = None
        snapshots.append(
            LiquidSnapshot(
                ticker=ticker,
                source_coin=name,
                ts=stamp,
                mark_px=mark_px,
                funding_rate=_f(ctx.get("funding")),
                open_interest_coin=oi_coin,
                open_interest_usd=oi_usd,
                day_volume_usd=_f(ctx.get("dayNtlVlm")),
                premium=_f(ctx.get("premium")),
                max_leverage=max_lev,
                is_thin=bool(oi_usd is not None and oi_usd < min_oi_usd),
            )
        )
    return snapshots, malformed
