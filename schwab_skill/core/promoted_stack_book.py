"""Build promoted S0 stack trade books for offline allocator overlays.

Combines entry-timing replay cache (breakout buffer) + exit-grace replay
(chunk OHLC) + optional pts_52w / rank_v2 filters — matching the live
operating stack as closely as closed-trade CF allows.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd

from scripts.analyze_entry_timing_shadow_counterfactual import _load_replay_cache
from scripts.analyze_signal_stack_counterfactual import (
    _breakout_buffer_only_filter,
    _build_stack_frame,
    _rank_v2_percentile_filter,
    _replay_exit_grace_rows,
    _trade_key,
)

SKILL_DIR = Path(__file__).resolve().parents[1]
ART = SKILL_DIR / "validation_artifacts"
DEFAULT_EXIT_PROFILE = "exit_grace_t15_h40"


def exit_grace_cache_path(run_id: str) -> Path:
    return ART / f"exit_grace_replay_{run_id}_{DEFAULT_EXIT_PROFILE}.json"


def load_or_build_exit_grace_rows(
    run_id: str,
    *,
    data_provider: str = "chunk",
    force_rebuild: bool = False,
) -> list[dict[str, Any]]:
    path = exit_grace_cache_path(run_id)
    if path.exists() and not force_rebuild:
        try:
            rows = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(rows, list) and rows:
                return rows
        except Exception:
            pass
    rows = _replay_exit_grace_rows(
        run_id,
        profile_name=DEFAULT_EXIT_PROFILE,
        data_provider=data_provider,
    )
    ART.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rows, default=str), encoding="utf-8")
    return rows


def build_promoted_stack_frame(
    run_id: str = "control_legacy_aug",
    *,
    min_breakout_buffer: float = 0.01,
    rank_v2_percentile: int = 76,
    pts_52w_max: float | None = 37.0,
    data_provider: str = "chunk",
    force_rebuild_grace: bool = False,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Return trade frame with grace net_return under live stack filters."""
    entry_df = _load_replay_cache(run_id)
    meta: dict[str, Any] = {
        "run_id": run_id,
        "entry_cache_n": int(len(entry_df)),
        "min_breakout_buffer": min_breakout_buffer,
        "rank_v2_percentile": rank_v2_percentile,
        "pts_52w_max": pts_52w_max,
    }
    if entry_df.empty:
        meta["error"] = "empty_entry_timing_cache"
        return pd.DataFrame(), meta

    grace_rows = load_or_build_exit_grace_rows(
        run_id,
        data_provider=data_provider,
        force_rebuild=force_rebuild_grace,
    )
    meta["grace_replay_n"] = len(grace_rows)
    merged = _build_stack_frame(entry_df, grace_rows)
    meta["grace_joined_n"] = int(len(merged))

    # Breakout buffer keep (drop when buffer < min)
    drop = _breakout_buffer_only_filter(merged, min_breakout_buffer)
    kept = merged.loc[~drop].copy()
    meta["after_breakout_buffer_n"] = int(len(kept))

    if pts_52w_max is not None and "pts_52w" in kept.columns:
        pts = pd.to_numeric(kept["pts_52w"], errors="coerce")
        kept = kept[pts.isna() | (pts <= float(pts_52w_max))].copy()
        meta["after_pts_52w_n"] = int(len(kept))

    if rank_v2_percentile and rank_v2_percentile > 0:
        kept, thr = _rank_v2_percentile_filter(kept, int(rank_v2_percentile))
        meta["rank_v2_threshold"] = thr
        meta["after_rank_v2_n"] = int(len(kept))

    # Normalize columns for allocator overlay
    out = kept.copy()
    out["entry_date"] = pd.to_datetime(out["entry_date"])
    if "exit_date" not in out.columns:
        # reconstruct exit from entry + hold when missing
        out["exit_date"] = out["entry_date"] + pd.to_timedelta(
            pd.to_numeric(out.get("hold_days"), errors="coerce").fillna(20).astype(int),
            unit="D",
        )
    else:
        out["exit_date"] = pd.to_datetime(out["exit_date"])
    out["net_return"] = pd.to_numeric(out["net_return"], errors="coerce").fillna(0.0)
    out["hold_days"] = pd.to_numeric(out.get("hold_days"), errors="coerce").fillna(0).astype(int)
    out["ticker"] = out["ticker"].astype(str).str.upper()
    meta["final_n"] = int(len(out))
    meta["trade_keys_sample"] = [
        _trade_key(r.era, r.ticker, r.entry_date)
        for r in out.head(3).itertuples(index=False)
    ]
    return out.reset_index(drop=True), meta
