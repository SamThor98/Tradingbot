"""
Unit tests for the Phase 2 trade-logging augmentation.

These tests guarantee three properties that the rollout depends on:

1. ``BACKTEST_AUGMENTED_LOGGING`` OFF (default) produces a chunk schema
   that is byte-identical to the pre-augmentation legacy schema. Existing
   artifacts and downstream consumers (phase1_trade_diagnostics,
   phase2_common.load_trades fallback) MUST keep working unchanged.
2. ``BACKTEST_AUGMENTED_LOGGING`` ON adds ticker, entry/exit prices, costs,
   MFE/MAE, and overlay decision fields, but only includes ``ohlc_path``
   when a non-empty path is present (saves bytes when path logging is off).
3. The MFE/MAE math is correct: MFE comes from the highest *high* between
   entry+1 and exit, MAE comes from the lowest *low*, both expressed as
   fractional returns relative to entry_price.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

SKILL_DIR = Path(__file__).resolve().parent.parent
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

from backtest import _build_ohlc_path, _compute_mfe_mae  # noqa: E402
from scripts.run_multi_era_backtest_schwab_only import _project_trades  # noqa: E402


def _ohlc_df(rows: list[tuple[str, float, float, float, float, float]]) -> pd.DataFrame:
    """Build a minimal OHLCV dataframe indexed by date."""
    idx = pd.DatetimeIndex([pd.Timestamp(r[0]) for r in rows])
    return pd.DataFrame(
        {
            "open": [r[1] for r in rows],
            "high": [r[2] for r in rows],
            "low": [r[3] for r in rows],
            "close": [r[4] for r in rows],
            "volume": [r[5] for r in rows],
        },
        index=idx,
    )


# ---------------------------------------------------------------------------
# _compute_mfe_mae
# ---------------------------------------------------------------------------


def test_mfe_mae_uses_intraday_high_low_not_close() -> None:
    """The whole point of MFE/MAE vs close-to-close is to capture intraday
    excursions. Bug-bait: code that uses close instead of high/low would
    pass naive tests where high == close. This df makes them differ."""
    df = _ohlc_df(
        [
            ("2020-01-02", 100, 100, 100, 100, 1e6),  # entry
            ("2020-01-03", 100, 110, 95, 102, 1e6),  # high 110, low 95
            ("2020-01-04", 102, 108, 99, 101, 1e6),
            ("2020-01-05", 101, 104, 100, 103, 1e6),  # exit at close 103
        ]
    )
    mfe, mae = _compute_mfe_mae(df, entry_idx=0, exit_idx=3, entry_price=100.0)
    # MFE = (110 - 100) / 100 = 0.10 (from day 2's high)
    assert mfe == pytest.approx(0.10)
    # MAE = (95 - 100) / 100 = -0.05 (from day 2's low)
    assert mae == pytest.approx(-0.05)


def test_mfe_mae_excludes_entry_day() -> None:
    """Entry day's high/low should not count: at entry we're holding the
    close, not seeking a worst-case excursion against ourselves."""
    df = _ohlc_df(
        [
            ("2020-01-02", 100, 999, 1, 100, 1e6),  # extreme entry-day excursion
            ("2020-01-03", 100, 102, 99, 101, 1e6),
        ]
    )
    mfe, mae = _compute_mfe_mae(df, entry_idx=0, exit_idx=1, entry_price=100.0)
    assert mfe == pytest.approx(0.02)  # from day 2 high 102
    assert mae == pytest.approx(-0.01)  # from day 2 low 99


def test_mfe_mae_falls_back_to_close_when_high_low_missing() -> None:
    """The fetcher always returns OHLCV, but defensive code paths exist for
    truncated frames. Falling back to close should not crash."""
    idx = pd.DatetimeIndex([pd.Timestamp("2020-01-02"), pd.Timestamp("2020-01-03")])
    df = pd.DataFrame({"close": [100.0, 105.0]}, index=idx)
    mfe, mae = _compute_mfe_mae(df, entry_idx=0, exit_idx=1, entry_price=100.0)
    assert mfe == pytest.approx(0.05)
    assert mae == pytest.approx(0.05)


def test_mfe_mae_returns_zero_for_same_day_exit() -> None:
    df = _ohlc_df([("2020-01-02", 100, 100, 100, 100, 1e6)])
    mfe, mae = _compute_mfe_mae(df, entry_idx=0, exit_idx=0, entry_price=100.0)
    assert mfe == 0.0
    assert mae == 0.0


def test_mfe_mae_returns_none_for_invalid_entry_price() -> None:
    df = _ohlc_df([("2020-01-02", 0, 0, 0, 0, 0)])
    mfe, mae = _compute_mfe_mae(df, entry_idx=0, exit_idx=0, entry_price=0.0)
    assert mfe is None
    assert mae is None


# ---------------------------------------------------------------------------
# _build_ohlc_path
# ---------------------------------------------------------------------------


def test_ohlc_path_is_inclusive_of_entry_and_exit() -> None:
    df = _ohlc_df(
        [
            ("2020-01-02", 100, 101, 99, 100, 1e6),
            ("2020-01-03", 100, 102, 99, 101, 1e6),
            ("2020-01-04", 101, 103, 100, 102, 1e6),
        ]
    )
    path = _build_ohlc_path(df, entry_idx=0, exit_idx=2)
    assert len(path) == 3
    assert path[0]["date"] == "2020-01-02"
    assert path[-1]["date"] == "2020-01-04"
    assert path[1]["high"] == pytest.approx(102.0)


def test_ohlc_path_serializes_to_jsonable_primitives() -> None:
    """Each record must be JSON-friendly so the chunk JSON write doesn't
    blow up on numpy scalars or pandas Timestamps."""
    import json

    df = _ohlc_df([("2020-01-02", 100, 101, 99, 100, 1e6)])
    path = _build_ohlc_path(df, entry_idx=0, exit_idx=0)
    json.dumps(path)  # must not raise


def test_ohlc_path_handles_empty_window() -> None:
    df = _ohlc_df([("2020-01-02", 100, 100, 100, 100, 1e6)])
    assert _build_ohlc_path(df, entry_idx=0, exit_idx=-5) == []


# ---------------------------------------------------------------------------
# _project_trades — legacy path (default)
# ---------------------------------------------------------------------------

LEGACY_KEYS = {
    "return",
    "net_return",
    "entry_date",
    "exit_date",
    "stop_pct",
    "signal_score",
    "mirofish_conviction",
    "exit_reason",
}


def _fat_trade(**overrides) -> dict:
    """Build the kind of in-memory trade dict that backtest.run_backtest
    actually produces: 30+ keys covering ticker, prices, costs, MFE/MAE,
    overlay decisions."""
    base = {
        "ticker": "AAPL",
        "entry_date": "2017-01-06T00:00:00",
        "exit_date": "2017-02-06T00:00:00",
        "entry_price": 100.0,
        "exit_price": 110.0,
        "return": 0.10,
        "net_return": 0.095,
        "exit_reason": "trailing_stop",
        "signal_score": 7.5,
        "mirofish_conviction": 0.8,
        "qty_estimate": 200,
        "day_volume": 5_000_000.0,
        "slippage_pct": 0.001,
        "fees_pct": 0.0005,
        "stop_pct": 0.05,
        "mfe": 0.12,
        "mae": -0.02,
        "meta_policy_decision": "allow",
        "meta_policy_size_multiplier": 1.0,
        "event_risk_action": "block",
        "event_risk_size_multiplier": 0.0,
        "exec_quality_regime": "normal",
        "exec_quality_effective_slippage_bps": 5.0,
        "exit_manager_partial_done": True,
        "ohlc_path": [{"date": "2017-01-06", "open": 100, "high": 101, "low": 99, "close": 100, "volume": 1e6}],
    }
    base.update(overrides)
    return base


def test_legacy_projection_drops_all_augmented_fields() -> None:
    """Critical guarantee: with augmentation OFF, the chunk schema is exactly
    what it was before this PR. No extra keys leak through, even though the
    in-memory trade dict has them."""
    rows = _project_trades([_fat_trade()], augmented=False)
    assert len(rows) == 1
    assert set(rows[0].keys()) == LEGACY_KEYS


def test_legacy_projection_preserves_field_values() -> None:
    """Values that DO survive into the legacy schema must match the inputs
    exactly (no rounding, no type conversion errors)."""
    rows = _project_trades([_fat_trade()], augmented=False)
    r = rows[0]
    assert r["return"] == pytest.approx(0.10)
    assert r["net_return"] == pytest.approx(0.095)
    assert r["stop_pct"] == pytest.approx(0.05)
    assert r["signal_score"] == pytest.approx(7.5)
    assert r["mirofish_conviction"] == pytest.approx(0.8)
    assert r["exit_reason"] == "trailing_stop"
    assert r["entry_date"] == "2017-01-06T00:00:00"
    assert r["exit_date"] == "2017-02-06T00:00:00"


def test_legacy_projection_handles_missing_optional_fields() -> None:
    """A minimal pre-augmentation trade dict must still project cleanly.
    This proves we did not accidentally make any new field 'required'."""
    minimal = {
        "return": 0.05,
        "net_return": 0.04,
        "entry_date": "2017-01-06T00:00:00",
        "exit_date": "2017-02-06T00:00:00",
        "stop_pct": 0.05,
    }
    rows = _project_trades([minimal], augmented=False)
    r = rows[0]
    assert r["signal_score"] is None
    assert r["mirofish_conviction"] is None
    assert r["exit_reason"] == ""


# ---------------------------------------------------------------------------
# _project_trades — augmented path
# ---------------------------------------------------------------------------

AUGMENTED_REQUIRED_KEYS = LEGACY_KEYS | {
    "ticker",
    "entry_price",
    "exit_price",
    "qty_estimate",
    "day_volume",
    "slippage_pct",
    "fees_pct",
    "mfe",
    "mae",
    "meta_policy_decision",
    "meta_policy_size_multiplier",
    "event_risk_action",
    "event_risk_size_multiplier",
    "exec_quality_regime",
    "exec_quality_effective_slippage_bps",
    "exit_manager_partial_done",
}


def test_augmented_projection_includes_all_replay_engine_fields() -> None:
    rows = _project_trades([_fat_trade()], augmented=True)
    assert len(rows) == 1
    keys = set(rows[0].keys())
    missing = AUGMENTED_REQUIRED_KEYS - keys
    assert missing == set(), f"missing required augmented fields: {missing}"


def test_augmented_projection_includes_ohlc_path_only_when_present() -> None:
    """Save bytes when path logging is off: omit ohlc_path entirely rather
    than emit an empty list."""
    with_path = _project_trades([_fat_trade()], augmented=True)[0]
    assert "ohlc_path" in with_path
    assert with_path["ohlc_path"][0]["date"] == "2017-01-06"

    without_path = _project_trades([_fat_trade(ohlc_path=[])], augmented=True)[0]
    assert "ohlc_path" not in without_path


def test_augmented_projection_normalizes_numerics() -> None:
    """str inputs (which can come from JSON round-trips upstream) must be
    coerced to float so downstream consumers don't have to defend against
    type heterogeneity."""
    t = _fat_trade(entry_price="100.0", exit_price="110.0", mfe="0.12", mae="-0.02")
    r = _project_trades([t], augmented=True)[0]
    assert r["entry_price"] == pytest.approx(100.0)
    assert r["exit_price"] == pytest.approx(110.0)
    assert r["mfe"] == pytest.approx(0.12)
    assert r["mae"] == pytest.approx(-0.02)


def test_augmented_projection_tolerates_none_values() -> None:
    """Some fields (mfe, signal_score) can legitimately be None for trades
    where the underlying compute failed. The projection must pass None
    through, not crash on float(None)."""
    t = _fat_trade(mfe=None, mae=None, signal_score=None, exit_price=None)
    r = _project_trades([t], augmented=True)[0]
    assert r["mfe"] is None
    assert r["mae"] is None
    assert r["signal_score"] is None
    assert r["exit_price"] is None


def test_augmented_projection_flags_partial_done_as_bool() -> None:
    r_true = _project_trades([_fat_trade(exit_manager_partial_done=True)], augmented=True)[0]
    r_false = _project_trades([_fat_trade(exit_manager_partial_done=False)], augmented=True)[0]
    r_missing = _project_trades([_fat_trade(exit_manager_partial_done=None)], augmented=True)[0]
    assert r_true["exit_manager_partial_done"] is True
    assert r_false["exit_manager_partial_done"] is False
    assert r_missing["exit_manager_partial_done"] is False


def test_augmented_projection_round_trips_through_json() -> None:
    """The entire augmented row must be JSON-serializable so chunk writes
    don't fail on numpy scalars, Timestamps, or other non-primitive types."""
    import json

    rows = _project_trades([_fat_trade()], augmented=True)
    blob = json.dumps(rows)
    assert "AAPL" in blob
    parsed = json.loads(blob)
    assert parsed[0]["ticker"] == "AAPL"
