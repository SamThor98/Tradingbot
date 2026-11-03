"""Triple-barrier labels + combinatorial purged/embargoed CV."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

SKILL_DIR = Path(__file__).resolve().parent.parent
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

from research.purged_cv import (  # noqa: E402
    assert_train_labels_respect_embargo,
    combinatorial_purged_splits,
)
from research.triple_barrier import (  # noqa: E402
    BARRIER_PROFIT,
    BARRIER_STOP,
    BARRIER_TIMEOUT,
    label_ohlc_path,
    triple_barrier_at_index,
)

# Hand-checked 10-row fixture (trade-chunk OHLC path shape).
# stop=7%, profit=14% (2R), grace=15, max_hold=40.
_STOP = 0.07
_PROFIT = 0.14
_GRACE = 15
_HOLD = 40


def _path_crash_day2() -> list[dict]:
    rows = []
    px = 100.0
    for i in range(5):
        px = 100.0 if i == 0 else (92.0 if i >= 1 else 100.0)
        if i >= 1:
            px = 92.0
        rows.append({"date": f"2016-01-{i+2:02d}", "open": px, "high": px + 0.2, "low": px - 0.2, "close": px})
    return rows


def _path_profit_day5() -> list[dict]:
    rows = []
    for i in range(8):
        px = 100.0 + i * 3.0  # day 5 (i=4) close=112, day 5 high reaches 114+
        hi = px + 3.0
        rows.append({"date": f"2016-01-{i+2:02d}", "open": px, "high": hi, "low": px - 0.3, "close": px})
    return rows


def _path_timeout_flat() -> list[dict]:
    return [
        {"date": f"2016-01-{(i % 28) + 1:02d}", "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.2}
        for i in range(40)
    ]


def _path_slow_bleed() -> list[dict]:
    rows = []
    px = 100.0
    for i in range(20):
        px = 100.0 - i * 0.2  # stays above 93 until late
        rows.append({"date": f"2016-02-{i+1:02d}", "open": px, "high": px + 0.1, "low": px - 0.1, "close": px})
    return rows


def _path_trail_after_grace() -> list[dict]:
    """Rise during grace, then drop from high after day 15 — trailing stop."""
    rows = []
    for i in range(25):
        if i < 15:
            px = 100.0 + i * 0.5  # 107.0 at day 15
        else:
            px = 107.0 - (i - 14) * 2.0  # drops through trailing 7% of ~107
        rows.append({"date": f"2016-03-{i+1:02d}", "open": px, "high": px + 0.2, "low": px - 0.4, "close": px})
    return rows


def _path_gap_stop() -> list[dict]:
    return [
        {"date": "2016-04-02", "open": 100.0, "high": 100.5, "low": 99.5, "close": 100.0},
        {"date": "2016-04-03", "open": 90.0, "high": 91.0, "low": 88.0, "close": 89.0},
    ]


def _path_gap_profit() -> list[dict]:
    return [
        {"date": "2016-04-02", "open": 100.0, "high": 101.0, "low": 99.5, "close": 100.5},
        {"date": "2016-04-03", "open": 115.0, "high": 116.0, "low": 114.0, "close": 115.0},
    ]


def _path_touch_stop_intraday() -> list[dict]:
    return [
        {"date": "2016-05-02", "open": 100.0, "high": 100.4, "low": 99.8, "close": 100.2},
        {"date": "2016-05-03", "open": 100.0, "high": 100.5, "low": 92.5, "close": 99.0},
    ]


def _path_near_miss_stop() -> list[dict]:
    rows: list[dict[str, Any]] = []
    for i in range(10):
        px = 100.0 - i * 0.3  # 97.3 at day 10, still above 93
        rows.append({"date": f"2016-06-{i+1:02d}", "open": px, "high": px + 0.2, "low": px - 0.2, "close": px})
    # then timeout remainder
    last = rows[-1]["close"]
    for i in range(10, 40):
        rows.append({"date": f"2016-07-{(i-9):02d}", "open": last, "high": last + 0.3, "low": last - 0.3, "close": last})
    return rows


def _path_late_profit() -> list[dict]:
    rows = []
    for i in range(12):
        px = 100.0 + i * 0.4
        rows.append({"date": f"2016-08-{i+1:02d}", "open": px, "high": px + 0.2, "low": px - 0.2, "close": px})
    px = 114.5
    rows.append({"date": "2016-08-13", "open": 105.0, "high": 115.0, "low": 104.0, "close": px})
    return rows


HAND_CHECKED = [
    {"id": "T01", "ohlc": _path_crash_day2(), "expected_hit": BARRIER_STOP},
    {"id": "T02", "ohlc": _path_profit_day5(), "expected_hit": BARRIER_PROFIT},
    {"id": "T03", "ohlc": _path_timeout_flat(), "expected_hit": BARRIER_TIMEOUT},
    {"id": "T04", "ohlc": _path_slow_bleed(), "expected_hit": BARRIER_TIMEOUT},
    {"id": "T05", "ohlc": _path_trail_after_grace(), "expected_hit": BARRIER_STOP},
    {"id": "T06", "ohlc": _path_gap_stop(), "expected_hit": BARRIER_STOP},
    {"id": "T07", "ohlc": _path_gap_profit(), "expected_hit": BARRIER_PROFIT},
    {"id": "T08", "ohlc": _path_touch_stop_intraday(), "expected_hit": BARRIER_STOP},
    {"id": "T09", "ohlc": _path_near_miss_stop(), "expected_hit": BARRIER_TIMEOUT},
    {"id": "T10", "ohlc": _path_late_profit(), "expected_hit": BARRIER_PROFIT},
]


def test_ten_row_hand_checked_barrier_hits() -> None:
    assert len(HAND_CHECKED) == 10
    for row in HAND_CHECKED:
        got = label_ohlc_path(
            100.0,
            row["ohlc"],
            stop_pct=_STOP,
            profit_pct=_PROFIT,
            max_hold_days=_HOLD,
            min_hold_before_trail=_GRACE,
        )
        assert got["barrier_hit"] == row["expected_hit"], (
            f"{row['id']} expected {row['expected_hit']} got {got}"
        )
        assert set(got) >= {"label", "barrier_hit", "holding_days", "ret_net"}
        if got["barrier_hit"] == BARRIER_PROFIT:
            assert got["label"] == 1
        elif got["barrier_hit"] == BARRIER_STOP:
            assert got["label"] == -1
        else:
            assert got["label"] == 0


def test_triple_barrier_on_ohlcv_frame_matches_path() -> None:
    idx = pd.bdate_range("2016-01-04", periods=50)
    close = np.full(50, 100.0)
    close[2] = 90.0  # stop on day 2
    df = pd.DataFrame(
        {"open": close, "high": close + 0.1, "low": close - 0.1, "close": close, "volume": 1e6},
        index=idx,
    )
    lab = triple_barrier_at_index(df, 0, stop_pct=_STOP)
    assert lab is not None
    assert lab["barrier_hit"] == BARRIER_STOP
    assert lab["holding_days"] == 2


def test_cpcv_no_train_label_uses_bars_after_embargo() -> None:
    rng = np.random.default_rng(0)
    dates = pd.bdate_range("2016-01-04", periods=200)
    rows = []
    for i, d in enumerate(dates):
        rows.append(
            {
                "asof_date": d,
                "ticker": "AAA" if i % 2 == 0 else "BBB",
                "barrier_holding_days": 40,
                "barrier_ret_net": float(rng.normal(0, 0.02)),
                "dist_sma50_pct": float(rng.normal(0, 0.05)),
            }
        )
    df = pd.DataFrame(rows)
    folds = combinatorial_purged_splits(df, n_groups=5, n_test_groups=1, embargo_days=40, min_train_rows=10)
    assert folds
    for fold in folds:
        errs = assert_train_labels_respect_embargo(df, fold, default_holding_days=40)
        assert errs == [], errs
