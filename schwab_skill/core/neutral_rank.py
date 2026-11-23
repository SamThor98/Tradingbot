"""Shadow rank that removes the inverted price points from the live score.

``signal_score`` is ``pts_52w + pts_sma + pts_volume + pts_mirofish``. A
price-only replay found ``pts_52w + pts_volume`` ranks names backwards on both
daily Stage 2 breakouts and weekly_swing admits. ``rank_score_v2`` already
drops ``pts_52w`` when ``RANK_V2_EXCLUDE_52W`` is on, then puts half its weight
back on volume, and the signal term still contains ``pts_volume``.

The neutral score subtracts those two components and keeps everything else
already inside ``signal_score`` (SMA points, MiroFish points). No new weights.
The shadow rank cut uses the same percentile as the live rank-v2 trim
(``RANK_FILTER_SHADOW_MIN_PERCENTILE_RANK_V2``, default 76) on that score.

``shadow`` logs both orders and both cut survivals. ``live`` retargets the
book sort at the neutral score. The cut itself is never a drop.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

NEUTRAL_RANK_BASIS = "signal_score_minus_pts_52w_minus_pts_volume"
_RESEARCH_FAMILIES = frozenset({"horizon", "pead_primary"})


def _safe_float(value: Any, default: float | None = 0.0) -> float | None:
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def component_points(signal: dict[str, Any], key: str) -> float:
    """Points that were added into ``signal_score``. Missing is zero, not a guess."""
    comps = signal.get("score_components")
    raw: Any = None
    if isinstance(comps, dict) and comps.get(key) is not None:
        raw = comps.get(key)
    elif signal.get(key) is not None:
        raw = signal.get(key)
    value = _safe_float(raw, 0.0)
    return float(value or 0.0)


def neutral_score_from_signal(signal: dict[str, Any]) -> float:
    """``signal_score`` with 52-week-high points and volume points removed."""
    score = _safe_float(signal.get("signal_score"), 0.0)
    neutral = float(score or 0.0) - component_points(signal, "pts_52w") - component_points(signal, "pts_volume")
    return max(0.0, min(100.0, neutral))


def _is_research_family(signal: dict[str, Any]) -> bool:
    return str(signal.get("entry_family") or "").strip().lower() in _RESEARCH_FAMILIES


def _ordinal_ranks(signals: list[dict[str, Any]], key_fn: Any) -> list[int]:
    order = sorted(range(len(signals)), key=lambda index: key_fn(signals[index]))
    ranks = [0] * len(signals)
    for rank, index in enumerate(order, start=1):
        ranks[index] = rank
    return ranks


def _live_sort_value(signal: dict[str, Any]) -> float:
    for key in ("sort_score", "signal_score"):
        value = _safe_float(signal.get(key), None)
        if value is not None:
            return float(value)
    return 0.0


def stamp_neutral_rank(
    signals: list[dict[str, Any]],
    diagnostics: dict[str, Any],
    skill_dir: Path | None = None,
    *,
    scan_id: str | None = None,
    persist: bool = False,
    exempt_research: bool = True,
) -> list[dict[str, Any]]:
    """Annotate every row. Does not reorder and does not drop.

    Ranks are 1-based on this cohort (best = 1). Ties break on ticker, then
    on list order. ``survives_live_rank_v2`` follows the live rank-v2 rule on
    this path: research families are exempt when ``exempt_research`` is true,
    matching the scanner, and included when it is false, matching the backtest.
    ``survives_shadow_rank_v2`` is the same percentile on the neutral score
    and is informational in every mode.
    """
    from config import get_neutral_rank_mode, get_rank_filter_shadow_min_percentile_rank_v2
    from core.scoring_rank_v2 import score_percentile_threshold
    from core.shadow_tags import append_shadow_tag_rows, attach_shadow_tags

    mode = get_neutral_rank_mode(skill_dir)
    diagnostics["neutral_rank_mode"] = mode
    diagnostics["neutral_rank_basis"] = NEUTRAL_RANK_BASIS
    diagnostics["neutral_rank_cut_enforced"] = 0
    if mode == "off" or not signals:
        diagnostics["neutral_rank_scored"] = 0
        diagnostics["neutral_rank_would_drop"] = 0
        return signals

    percentile = int(get_rank_filter_shadow_min_percentile_rank_v2(skill_dir))
    scored_at = datetime.now(timezone.utc).isoformat()
    for signal in signals:
        signal["neutral_score"] = round(neutral_score_from_signal(signal), 4)

    live_ranks = _ordinal_ranks(
        signals,
        lambda row: (
            -_live_sort_value(row),
            -float(_safe_float(row.get("signal_score"), 0.0) or 0.0),
            str(row.get("ticker") or ""),
        ),
    )
    shadow_ranks = _ordinal_ranks(
        signals,
        lambda row: (
            -float(row.get("neutral_score") or 0.0),
            str(row.get("ticker") or ""),
        ),
    )

    cohort_indexes = [
        index for index, signal in enumerate(signals) if not (exempt_research and _is_research_family(signal))
    ]
    rank_cut_evaluated = len(cohort_indexes) >= 3
    live_threshold: float | None = None
    shadow_threshold: float | None = None
    if rank_cut_evaluated:
        live_values: list[float] = []
        shadow_values: list[float] = []
        for index in cohort_indexes:
            signal = signals[index]
            live_value = _safe_float(signal.get("rank_score_v2"), None)
            if live_value is not None:
                live_values.append(float(live_value))
            shadow_values.append(float(signal.get("neutral_score") or 0.0))
        if len(live_values) >= 3:
            live_threshold = score_percentile_threshold(live_values, percentile)
        if len(shadow_values) >= 3:
            shadow_threshold = score_percentile_threshold(shadow_values, percentile)
        if live_threshold is None and shadow_threshold is None:
            rank_cut_evaluated = False

    would_drop = 0
    rows: list[dict[str, Any]] = []
    for index, signal in enumerate(signals):
        exempt = bool(exempt_research and _is_research_family(signal))
        live_value = _safe_float(signal.get("rank_score_v2"), None)
        neutral = float(signal.get("neutral_score") or 0.0)
        # Missing rank_score_v2 matches the live filter, which only drops rows
        # that already carry a score. A skipped cut also drops nobody.
        if exempt or live_threshold is None or live_value is None:
            survives_live = True
        else:
            survives_live = float(live_value) >= float(live_threshold)
        if shadow_threshold is None:
            survives_shadow = True
        else:
            survives_shadow = neutral >= float(shadow_threshold)
        if survives_shadow is False:
            would_drop += 1
        block = {
            "mode": mode,
            "basis": NEUTRAL_RANK_BASIS,
            "neutral_score": round(neutral, 4),
            "pts_52w": round(component_points(signal, "pts_52w"), 4),
            "pts_volume": round(component_points(signal, "pts_volume"), 4),
            "signal_score": _safe_float(signal.get("signal_score"), None),
            "live_rank": live_ranks[index],
            "shadow_rank": shadow_ranks[index],
            "cohort_n": len(signals),
            "rank_cut_evaluated": rank_cut_evaluated,
            "rank_cut_percentile": percentile,
            "live_rank_v2_threshold": None if live_threshold is None else round(float(live_threshold), 4),
            "shadow_rank_threshold": None if shadow_threshold is None else round(float(shadow_threshold), 4),
            "survives_live_rank_v2": survives_live,
            "survives_shadow_rank_v2": survives_shadow,
            "live_rank_v2_exempt": exempt,
            "applied_to_sort": False,
        }
        signal["neutral_rank"] = block
        tags = attach_shadow_tags(signal)
        if persist:
            rows.append(
                {
                    "record_type": "ranked_signal",
                    "scan_id": scan_id,
                    "scored_at": scored_at,
                    "ticker": str(signal.get("ticker") or "").upper(),
                    "entry_family": signal.get("entry_family"),
                    "horizon_hits": list(signal.get("horizon_hits") or []),
                    "signal_score": block["signal_score"],
                    "neutral_score": block["neutral_score"],
                    "pts_52w": block["pts_52w"],
                    "pts_volume": block["pts_volume"],
                    "live_rank": block["live_rank"],
                    "shadow_rank": block["shadow_rank"],
                    "survives_live_rank_v2": survives_live,
                    "survives_shadow_rank_v2": survives_shadow,
                    "live_rank_v2_exempt": exempt,
                    "rank_cut_evaluated": rank_cut_evaluated,
                    "rank_cut_percentile": percentile,
                    "neutral_rank_mode": mode,
                    "shadow_tags": tags,
                    "weekly_swing_entry_buffer_would_cut": bool(
                        (signal.get("weekly_swing_entry_buffer") or {}).get("would_cut")
                    )
                    if isinstance(signal.get("weekly_swing_entry_buffer"), dict)
                    else False,
                }
            )

    diagnostics["neutral_rank_scored"] = len(signals)
    diagnostics["neutral_rank_would_drop"] = would_drop
    diagnostics["neutral_rank_cut_evaluated"] = int(rank_cut_evaluated)
    if live_threshold is not None:
        diagnostics["neutral_rank_live_threshold"] = round(float(live_threshold), 4)
    if shadow_threshold is not None:
        diagnostics["neutral_rank_shadow_threshold"] = round(float(shadow_threshold), 4)
    if persist:
        try:
            diagnostics["neutral_rank_persisted"] = append_shadow_tag_rows(skill_dir, rows)
        except Exception:
            diagnostics["neutral_rank_persisted"] = 0
    return signals


def apply_neutral_sort(
    signals: list[dict[str, Any]], diagnostics: dict[str, Any], skill_dir: Path | None = None
) -> bool:
    """Point non-research ``sort_score`` at the neutral score when mode is live.

    Research admits keep their research-score primary. Callers that sort a
    research book should use :func:`neutral_secondary_score` as the tie break
    only when this returns true.
    """
    from config import get_neutral_rank_mode

    if get_neutral_rank_mode(skill_dir) != "live":
        return False
    applied = 0
    for signal in signals:
        if _is_research_family(signal):
            continue
        block = signal.get("neutral_rank") if isinstance(signal.get("neutral_rank"), dict) else {}
        score = _safe_float(block.get("neutral_score"), None)
        if score is None:
            continue
        signal["sort_score"] = float(score)
        block["applied_to_sort"] = True
        signal["neutral_rank"] = block
        signal["neutral_rank_applied"] = True
        applied += 1
    diagnostics["neutral_rank_applied"] = applied
    return True


def neutral_secondary_score(signal: dict[str, Any], *, enabled: bool) -> float:
    """Secondary book key. Live uses the neutral score; shadow keeps signal_score."""
    if enabled:
        block = signal.get("neutral_rank") if isinstance(signal.get("neutral_rank"), dict) else {}
        score = _safe_float(block.get("neutral_score"), None)
        if score is not None:
            return float(score)
    value = _safe_float(signal.get("signal_score"), 0.0)
    return float(value or 0.0)
