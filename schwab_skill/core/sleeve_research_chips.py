"""Research chip helpers for wave 1 (R3/R2/R4/R6/R1/R5) — shadow-safe scaffolding."""

from __future__ import annotations

import math
from typing import Any

from core.multi_sleeve_constitution import RESEARCH_WAVE


def research_wave_order() -> tuple[str, ...]:
    return RESEARCH_WAVE


def r3_capacity_penalty(
    row: dict[str, Any],
    *,
    adv_field: str = "adv_usd",
    min_adv_usd: float = 5_000_000.0,
    max_penalty: float = 0.35,
) -> float:
    """Return score multiplier in (1-max_penalty, 1] based on ADV capacity.

    Illiquid names get penalized; does not hard-drop (retention soft-rank).
    """
    try:
        adv = float(row.get(adv_field)) if row.get(adv_field) is not None else min_adv_usd
    except (TypeError, ValueError):
        adv = min_adv_usd
    if not math.isfinite(adv) or adv <= 0:
        return 1.0 - max_penalty
    # log taper: at min_adv → ~1-max_penalty/2; well above → ~1
    ratio = adv / min_adv_usd
    if ratio >= 4.0:
        return 1.0
    if ratio <= 0.25:
        return 1.0 - max_penalty
    # interpolate
    t = (ratio - 0.25) / (4.0 - 0.25)
    return (1.0 - max_penalty) + t * max_penalty


def apply_r3_capacity_to_rows(
    rows: list[dict[str, Any]],
    *,
    score_field: str = "signal_score",
    out_field: str = "signal_score_r3",
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        row = dict(r)
        try:
            base = float(row.get(score_field) or 0.0)
        except (TypeError, ValueError):
            base = 0.0
        mult = r3_capacity_penalty(row)
        row[out_field] = base * mult
        row["r3_capacity_mult"] = mult
        out.append(row)
    return out


def r2_anti_feature_flags(row: dict[str, Any]) -> list[str]:
    """Shadow veto candidates from loser-autopsy style features (non-enforcing)."""
    flags: list[str] = []
    try:
        ext = row.get("extension_pct_sma50")
        if ext is not None and float(ext) > 0.12:
            flags.append("extended_gt_12pct_sma50")
    except (TypeError, ValueError):
        pass
    try:
        days = row.get("days_to_earnings")
        if days is not None and 0 <= float(days) <= 5:
            flags.append("earnings_within_5d")
    except (TypeError, ValueError):
        pass
    try:
        adv = row.get("adv_usd")
        if adv is not None and float(adv) < 1_000_000:
            flags.append("thin_adv")
    except (TypeError, ValueError):
        pass
    return flags


def r4_pead_sleeve_score(row: dict[str, Any]) -> float:
    """PEAD magnitude × surprise × liquidity composite for S1 ranking."""
    try:
        surprise = abs(float(row.get("pead_surprise_pct") or row.get("surprise_pct") or 0.0))
    except (TypeError, ValueError):
        surprise = 0.0
    try:
        edge = float(row.get("edge_score") or 0.0)
    except (TypeError, ValueError):
        edge = 0.0
    try:
        adv = float(row.get("adv_usd") or 5_000_000.0)
    except (TypeError, ValueError):
        adv = 5_000_000.0
    liq = min(1.0, max(0.1, adv / 20_000_000.0))
    return (0.5 * edge) + (0.3 * surprise) + (20.0 * liq)


def r6_vol_damper(
    sleeve_cap: float,
    *,
    book_vol: float | None,
    baseline_vol: float = 0.01,
    max_shrink: float = 0.5,
) -> float:
    """Shrink sleeve cap when book realized vol spikes (pre-crash damper)."""
    if book_vol is None or baseline_vol <= 0:
        return sleeve_cap
    try:
        bv = float(book_vol)
    except (TypeError, ValueError):
        return sleeve_cap
    if not math.isfinite(bv) or bv <= 0:
        return sleeve_cap
    ratio = bv / baseline_vol
    if ratio <= 1.25:
        return sleeve_cap
    shrink = min(max_shrink, (ratio - 1.25) / 2.0)
    return sleeve_cap * (1.0 - shrink)


def r1_entry_state(
    *,
    pierced: bool,
    held_above_n: bool,
    setup_ok: bool,
) -> str:
    """Path-dependent entry state machine labels (shadow research)."""
    if not setup_ok:
        return "idle"
    if pierced and held_above_n:
        return "armed"
    if pierced:
        return "pierce"
    return "setup"


def r5_sector_rs_eligible(
    row: dict[str, Any],
    *,
    top_quintile_sectors: set[str] | frozenset[str],
    sector_field: str = "sector_etf",
) -> bool:
    sec = str(row.get(sector_field) or "").upper()
    return bool(sec) and sec in {s.upper() for s in top_quintile_sectors}
