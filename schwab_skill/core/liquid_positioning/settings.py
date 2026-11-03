"""Bind ``LIQUID_*`` env settings into a LiquidConfig."""

from __future__ import annotations

from pathlib import Path

from core.liquid_positioning.schema import LiquidConfig


def build_liquid_config(skill_dir: Path | None = None) -> LiquidConfig:
    from config import (
        get_liquid_api_url,
        get_liquid_collect_enabled,
        get_liquid_dex,
        get_liquid_min_oi_usd,
        get_liquid_overlay_mode,
    )

    return LiquidConfig(
        mode=str(get_liquid_overlay_mode(skill_dir)),
        collect_enabled=bool(get_liquid_collect_enabled(skill_dir)),
        dex=str(get_liquid_dex(skill_dir)),
        api_url=str(get_liquid_api_url(skill_dir)),
        min_oi_usd=float(get_liquid_min_oi_usd(skill_dir)),
    )
