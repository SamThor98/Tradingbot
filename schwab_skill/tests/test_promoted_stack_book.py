"""Tests for promoted stack book builder (uses cache when present)."""

from __future__ import annotations

from core.allocator_overlay_cf import sweep_allocator_overlay_arms
from core.promoted_stack_book import build_promoted_stack_frame


def test_promoted_stack_frame_nonempty_when_cache_present() -> None:
    df, meta = build_promoted_stack_frame("control_legacy_aug")
    # If caches missing, skip soft — CI may not have artifacts
    if meta.get("entry_cache_n", 0) == 0:
        return
    assert meta.get("final_n", 0) > 100
    assert not df.empty
    assert {"era", "ticker", "entry_date", "exit_date", "net_return"} <= set(df.columns)


def test_sweep_capacity_aware_field() -> None:
    from core.allocator_overlay_cf import make_demo_trade_frame

    report = sweep_allocator_overlay_arms(make_demo_trade_frame(n_per_era=40))
    assert "capacity_aware_recommended" in report
    assert report["n_arms"] >= 5
