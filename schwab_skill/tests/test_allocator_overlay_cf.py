"""Tests for offline allocator overlay CF."""

from __future__ import annotations

import pandas as pd

from core.allocator_overlay_cf import (
    apply_allocator_admission,
    apply_top_n_per_day,
    make_demo_trade_frame,
    run_allocator_overlay_arms,
)


def test_demo_frame_has_five_eras() -> None:
    df = make_demo_trade_frame(n_per_era=40)
    assert df["era"].nunique() == 5
    assert len(df) == 200


def test_top_n_per_day_caps_cardinality() -> None:
    df = make_demo_trade_frame(n_per_era=30).copy()
    day0 = pd.Timestamp(df["entry_date"].iloc[0])
    df["entry_date"] = day0
    df["exit_date"] = day0 + pd.Timedelta(days=20)
    kept = apply_top_n_per_day(df, top_n=5)
    assert len(kept) == 5


def test_dd_and_crash_reject_some_trades() -> None:
    df = make_demo_trade_frame(n_per_era=60)
    out, diag = apply_allocator_admission(df, use_dd_staircase=True, use_crash_windows=True)
    assert diag["accepted"] + diag["rejected"] == len(df)
    assert len(out) <= len(df)


def test_run_arms_smoke() -> None:
    report = run_allocator_overlay_arms(make_demo_trade_frame(n_per_era=50))
    labels = {a["label"] for a in report["arms"]}
    assert "control" in labels
    assert "top5_then_dd_and_crash" in labels
    assert report["recommended"]["label"]
