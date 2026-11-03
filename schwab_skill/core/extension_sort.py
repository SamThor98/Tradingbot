"""Shadow sort that prefers less-extended names.

``pts_52w`` is the extension measure already on the live book. Higher points
mean a more extended name. The shadow score is ``100 - pts_52w`` so a
descending sort puts the least-extended names first.

This never writes ``sort_score`` and never changes ``SCAN_LIVE_SORT_KEY``.
``live`` is refused: the inverted single variable clears the research floors,
and promoting it is a separate sort-key decision.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


def extension_points(signal_row: dict[str, Any]) -> float | None:
    """Return ``pts_52w`` when the row actually carries it. Missing is not zero."""
    comps = signal_row.get("score_components")
    raw: Any = None
    if isinstance(comps, dict) and comps.get("pts_52w") is not None:
        raw = comps.get("pts_52w")
    elif signal_row.get("pts_52w") is not None:
        raw = signal_row.get("pts_52w")
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def extension_sort_block(signal_row: dict[str, Any]) -> dict[str, Any] | None:
    """Fields to stamp on a shadow row. None when extension is unknown."""
    from core.equity_confidence import equity_confidence_block
    from core.trust_score import trust_score_block

    pts = extension_points(signal_row)
    if pts is None:
        return None
    block = {
        "pts_52w": round(pts, 2),
        "extension_sort_score": round(100.0 - pts, 2),
        "extension_sort_basis": "inv_pts_52w",
        "extension_sort_mode": "shadow",
    }
    block.update(trust_score_block(signal_row, pts_52w=pts))
    block.update(equity_confidence_block(signal_row, pts_52w=pts))
    return block


def apply_extension_sort_shadow(
    signals: list[dict[str, Any]],
    diagnostics: dict[str, Any],
    skill_dir: Path | None = None,
) -> list[dict[str, Any]]:
    """Stamp the shadow score. Leave live ordering untouched."""
    from config import get_extension_sort_mode, get_scan_live_sort_key

    mode = get_extension_sort_mode(skill_dir)
    diagnostics["extension_sort_mode"] = mode
    diagnostics["extension_sort_basis"] = "inv_pts_52w"
    diagnostics["extension_sort_live_sort_key"] = get_scan_live_sort_key(skill_dir)
    diagnostics["equity_confidence_registry"] = "equity_ledger_v1"
    if mode == "live":
        diagnostics["extension_sort_skipped"] = "live_not_approved"
        diagnostics["extension_sort_scored"] = 0
        diagnostics["extension_sort_unscored"] = len(signals)
        return signals
    if mode != "shadow":
        diagnostics["extension_sort_scored"] = 0
        diagnostics["extension_sort_unscored"] = len(signals)
        return signals

    scored = 0
    for signal in signals:
        sort_before = signal.get("sort_score")
        block = extension_sort_block(signal)
        if block is None:
            continue
        signal.update(block)
        signal["sort_score"] = sort_before
        scored += 1
    diagnostics["extension_sort_scored"] = scored
    diagnostics["extension_sort_unscored"] = len(signals) - scored
    return signals
