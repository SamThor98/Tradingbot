"""Per-signal shadow tags and the JSONL log used for later outcome joins.

Tags name the shadow filters a row trips. They do not change which names the
live scan returns. The JSONL file sits next to ``.signal_quality_metrics.json``
so a later pass can join tickers to forward returns without the feature-store
schema.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

SHADOW_TAG_STAGE2 = "stage2"
SHADOW_TAG_ENTRY_TIMING = "entry_timing"
SHADOW_TAG_RANK = "rank"
SHADOW_TAG_WEAK_BREAKOUT_VOLUME = "weak_breakout_volume"
SHADOW_TAG_WEAK_MIROFISH = "weak_mirofish"
SHADOW_TICKER_WEEKLY_SWING_BUFFER = "weekly_swing_entry_buffer"

SHADOW_TAGS_JSONL = ".shadow_signal_tags.jsonl"

_PERSISTED_SIGNAL_KEYS = (
    "shadow_tags",
    "neutral_rank",
    "weekly_swing_entry_buffer",
    "horizon_hits",
    "stage_a_penalties",
    "entry_timing_shadow",
    "rank_filter_shadow",
    "rank_filter_v2",
)


def note_shadow_ticker(diagnostics: dict[str, Any], tag: str, ticker: str) -> None:
    """Record one ticker under a shadow tag. Repeats in the same scan collapse."""
    name = str(ticker or "").strip().upper()
    label = str(tag or "").strip()
    if not name or not label:
        return
    bucket = diagnostics.get("shadow_tickers")
    if not isinstance(bucket, dict):
        bucket = {}
        diagnostics["shadow_tickers"] = bucket
    seen = bucket.get(label)
    if not isinstance(seen, list):
        seen = []
        bucket[label] = seen
    if name not in seen:
        seen.append(name)


def shadow_persistence_fields(signal: dict[str, Any]) -> dict[str, Any]:
    """Fields copied into feature_store ``raw_features_json``."""
    out: dict[str, Any] = {}
    for key in _PERSISTED_SIGNAL_KEYS:
        value = signal.get(key)
        if value is not None:
            out[key] = value
    return out


def _weak_breakout_volume(signal: dict[str, Any]) -> bool:
    if signal.get("weak_breakout_volume_shadow") is True:
        return True
    latest = signal.get("latest_volume")
    average = signal.get("avg_vol_50")
    try:
        if latest is not None and average is not None and float(average) > 0 and float(latest) < float(average):
            return True
    except (TypeError, ValueError):
        return False
    return False


def _weak_mirofish(signal: dict[str, Any]) -> bool:
    if signal.get("weak_mirofish_shadow") is True:
        return True
    result = signal.get("mirofish_result") if isinstance(signal.get("mirofish_result"), dict) else {}
    bull = result.get("bull_trap_probability")
    cont = result.get("continuation_probability")
    if bull is None or cont is None:
        comps = signal.get("score_components") if isinstance(signal.get("score_components"), dict) else {}
        bull = bull if bull is not None else comps.get("bull_trap_probability")
        cont = cont if cont is not None else comps.get("continuation_probability")
    try:
        if bull is not None and cont is not None and float(bull) >= float(cont):
            return True
    except (TypeError, ValueError):
        return False
    return False


def attach_shadow_tags(signal: dict[str, Any]) -> list[str]:
    """Stamp ``shadow_tags`` from the filters already annotated on the row.

    The list is stable and deduped. ``weekly_swing_entry_buffer`` is recorded
    on its own block; a cut also counts as ``entry_timing`` because it is the
    same 1% breakout-buffer rule.
    """
    penalties = [str(item) for item in (signal.get("stage_a_penalties") or [])]
    tags: list[str] = []
    if SHADOW_TAG_STAGE2 in penalties or "stage2_shadow_would_filter" in penalties:
        tags.append(SHADOW_TAG_STAGE2)

    entry = signal.get("entry_timing_shadow") if isinstance(signal.get("entry_timing_shadow"), dict) else {}
    weekly = (
        signal.get("weekly_swing_entry_buffer") if isinstance(signal.get("weekly_swing_entry_buffer"), dict) else {}
    )
    entry_hit = bool(entry.get("would_filter")) or bool(weekly.get("would_cut"))
    if not entry_hit:
        entry_hit = any(item.startswith("entry_shadow_") for item in penalties)
    if entry_hit:
        tags.append(SHADOW_TAG_ENTRY_TIMING)

    rank_shadow = signal.get("rank_filter_shadow") if isinstance(signal.get("rank_filter_shadow"), dict) else {}
    rank_v2 = signal.get("rank_filter_v2") if isinstance(signal.get("rank_filter_v2"), dict) else {}
    neutral = signal.get("neutral_rank") if isinstance(signal.get("neutral_rank"), dict) else {}
    rank_hit = bool(rank_shadow.get("would_drop_any")) or bool(rank_v2.get("would_drop"))
    if neutral.get("survives_shadow_rank_v2") is False:
        rank_hit = True
    if rank_hit:
        tags.append(SHADOW_TAG_RANK)

    if _weak_breakout_volume(signal):
        tags.append(SHADOW_TAG_WEAK_BREAKOUT_VOLUME)
    if _weak_mirofish(signal):
        tags.append(SHADOW_TAG_WEAK_MIROFISH)

    signal["shadow_tags"] = tags
    return tags


def merge_signal_shadow_tickers(diagnostics: dict[str, Any], signals: list[dict[str, Any]]) -> None:
    """Fold final-signal tags into the scan's unique-ticker sets."""
    for signal in signals:
        ticker = str(signal.get("ticker") or "")
        for tag in list(signal.get("shadow_tags") or []):
            note_shadow_ticker(diagnostics, str(tag), ticker)
        weekly = signal.get("weekly_swing_entry_buffer")
        if isinstance(weekly, dict) and weekly.get("would_cut"):
            note_shadow_ticker(diagnostics, SHADOW_TICKER_WEEKLY_SWING_BUFFER, ticker)


def append_shadow_tag_rows(skill_dir: Path | None, rows: list[dict[str, Any]]) -> int:
    """Append JSON objects, one signal per line. Returns rows written."""
    if skill_dir is None or not rows:
        return 0
    path = Path(skill_dir) / SHADOW_TAGS_JSONL
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, default=str, sort_keys=True) + "\n")
    return len(rows)
