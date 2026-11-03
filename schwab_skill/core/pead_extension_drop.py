"""PEAD-only shadow mark for names extended above the 200-day average.

The cutoff is the 80th percentile of ``close_vs_sma200_pct`` on
``pead_primary_aug_fixed_full_c40`` (0.2028). Keeping names under that frozen
cut matched the walk-forward drop (PF 1.595 / worst 1.208 versus 1.599 / 1.205).

Only ``entry_family=pead_primary`` is stamped. Stage 2 and ``both`` are left
alone. Nothing is removed from the scan, and ``live`` is refused.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

PEAD_ONLY_FAMILY = "pead_primary"
DROP_BASIS = "close_vs_sma200_pct_p80"


def sma200_distance(signal_row: dict[str, Any]) -> float | None:
    """Fraction above the 200-day average. Missing is None, not zero."""
    raw = signal_row.get("close_vs_sma200_pct")
    if raw is None:
        comps = signal_row.get("score_components")
        if isinstance(comps, dict):
            raw = comps.get("close_vs_sma200_pct", comps.get("dist_sma200_pct"))
    if raw is None:
        raw = signal_row.get("dist_sma200_pct")
    if raw is not None:
        try:
            return float(raw)
        except (TypeError, ValueError):
            return None
    price = signal_row.get("price", signal_row.get("current_price"))
    sma = signal_row.get("sma_200")
    try:
        price_f = float(price)
        sma_f = float(sma)
    except (TypeError, ValueError):
        return None
    if price_f <= 0 or sma_f <= 0:
        return None
    return (price_f / sma_f) - 1.0


def _is_pead_only(signal_row: dict[str, Any]) -> bool:
    return str(signal_row.get("entry_family") or "").strip().lower() == PEAD_ONLY_FAMILY


def apply_pead_extension_drop_shadow(
    signals: list[dict[str, Any]],
    diagnostics: dict[str, Any],
    skill_dir: Path | None = None,
) -> list[dict[str, Any]]:
    """Stamp a would-drop flag on PEAD-only rows. Never removes a signal."""
    from config import get_pead_extension_drop_min, get_pead_extension_drop_mode

    mode = get_pead_extension_drop_mode(skill_dir)
    cutoff = get_pead_extension_drop_min(skill_dir)
    diagnostics["pead_extension_drop_mode"] = mode
    diagnostics["pead_extension_drop_min"] = cutoff
    diagnostics["pead_extension_drop_basis"] = DROP_BASIS
    if mode == "live":
        diagnostics["pead_extension_drop_skipped"] = "live_not_approved"
        diagnostics["pead_extension_drop_evaluated"] = 0
        diagnostics["pead_extension_drop_would_drop"] = 0
        diagnostics["pead_extension_drop_unmeasured"] = 0
        return signals
    if mode != "shadow":
        diagnostics["pead_extension_drop_evaluated"] = 0
        diagnostics["pead_extension_drop_would_drop"] = 0
        diagnostics["pead_extension_drop_unmeasured"] = 0
        return signals

    evaluated = 0
    would_drop = 0
    unmeasured = 0
    for signal in signals:
        if not _is_pead_only(signal):
            continue
        evaluated += 1
        distance = sma200_distance(signal)
        signal["pead_extension_drop_mode"] = "shadow"
        signal["pead_extension_drop_min"] = cutoff
        signal["pead_extension_drop_basis"] = DROP_BASIS
        if distance is None:
            unmeasured += 1
            signal["pead_extension_drop_would_drop"] = False
            signal["pead_extension_drop_reason"] = "distance_missing"
            continue
        flag = distance >= cutoff
        signal["pead_extension_drop_distance"] = round(distance, 4)
        signal["pead_extension_drop_would_drop"] = flag
        if flag:
            would_drop += 1
    diagnostics["pead_extension_drop_evaluated"] = evaluated
    diagnostics["pead_extension_drop_would_drop"] = would_drop
    diagnostics["pead_extension_drop_unmeasured"] = unmeasured
    return signals
