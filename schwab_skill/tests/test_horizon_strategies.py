from __future__ import annotations

import pandas as pd

from core.horizon_strategies import (
    DUAL_ADMIT_STRATEGY_IDS,
    evaluate_donchian_20,
    evaluate_gap_and_go,
    evaluate_horizon_plugins,
    evaluate_monthly_position,
    evaluate_nr7_breakout,
    evaluate_range_expansion,
    evaluate_weekly_swing,
    resample_monthly,
    resample_weekly,
    selected_dual_admit_ids,
    triggered_dual_admit_ids,
)
from core.scan_catalog import (
    build_scan_catalog_payload,
    catalog_fields_for_signal,
    filter_signals_for_scan_selection,
    resolve_strategy_ids,
    signal_matches_strategy_ids,
)
from strategy_plugins import build_default_strategy_plugins


def _ohlcv(
    *,
    n: int = 80,
    close: list[float] | None = None,
    open_: list[float] | None = None,
    high: list[float] | None = None,
    low: list[float] | None = None,
    volume: list[float] | None = None,
    start: str = "2022-01-03",
) -> pd.DataFrame:
    idx = pd.bdate_range(start, periods=n)
    if close is None:
        close = [50.0 + i * 0.2 for i in range(n)]
    if open_ is None:
        open_ = [c * 0.995 for c in close]
    if high is None:
        high = [max(o, c) * 1.01 for o, c in zip(open_, close, strict=True)]
    if low is None:
        low = [min(o, c) * 0.99 for o, c in zip(open_, close, strict=True)]
    if volume is None:
        volume = [1_000_000.0] * n
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
        index=idx,
    )


def test_gap_and_go_triggers_on_held_opening_gap() -> None:
    n = 60
    close = [100.0] * (n - 1) + [103.5]
    open_ = [100.0] * (n - 1) + [102.5]
    high = [101.0] * (n - 1) + [104.0]
    low = [99.0] * (n - 1) + [102.4]
    volume = [1_000_000.0] * (n - 1) + [2_000_000.0]
    df = _ohlcv(n=n, close=close, open_=open_, high=high, low=low, volume=volume)
    out = evaluate_gap_and_go(df)
    assert out["triggered"] is True
    assert out["name"] == "gap_and_go"
    assert out["mode"] == "shadow"


def test_range_expansion_needs_wide_bar_and_prior_high() -> None:
    n = 30
    close = [100.0] * (n - 1) + [106.0]
    open_ = [100.0] * (n - 1) + [100.2]
    high = [101.0] * (n - 1) + [106.2]
    low = [99.5] * (n - 1) + [100.0]
    df = _ohlcv(n=n, close=close, open_=open_, high=high, low=low)
    out = evaluate_range_expansion(df)
    assert out["triggered"] is True


def test_donchian_20_breaks_prior_channel() -> None:
    n = 40
    close = [100.0] * (n - 1) + [108.0]
    high = [101.0] * (n - 1) + [108.5]
    low = [99.0] * (n - 1) + [107.0]
    open_ = [100.0] * (n - 1) + [101.0]
    df = _ohlcv(n=n, close=close, open_=open_, high=high, low=low)
    out = evaluate_donchian_20(df)
    assert out["triggered"] is True
    assert out["meta"]["donchian_high"] == 101.0


def test_nr7_breakout_requires_narrow_bar_then_close_through() -> None:
    n = 20
    # Wide ranges, then a 0.2 NR7 bar, then a breakout close.
    high = [110.0] * (n - 2) + [100.2, 103.0]
    low = [90.0] * (n - 2) + [100.0, 100.1]
    close = [100.0] * (n - 2) + [100.1, 102.5]
    open_ = [100.0] * (n - 2) + [100.05, 100.3]
    df = _ohlcv(n=n, close=close, open_=open_, high=high, low=low)
    out = evaluate_nr7_breakout(df)
    assert out["meta"]["yesterday_was_nr7"] is True
    assert out["triggered"] is True


def test_weekly_swing_on_rising_weekly_structure() -> None:
    df = _ohlcv(n=280)
    weekly = resample_weekly(df)
    assert len(weekly) >= 36
    out = evaluate_weekly_swing(df)
    assert out["triggered"] is True
    assert out["meta"]["sma_30w_rising"] is True


def test_monthly_faber_on_rising_ten_month_sma() -> None:
    df = _ohlcv(n=280, start="2019-01-02")
    monthly = resample_monthly(df)
    assert len(monthly) >= 12
    out = evaluate_monthly_position(df)
    assert out["triggered"] is True
    assert out["meta"]["sma_10m_rising"] is True


def test_evaluate_horizon_plugins_covers_catalog_sleeves() -> None:
    names = {p["name"] for p in evaluate_horizon_plugins(_ohlcv(n=280))}
    assert {
        "gap_and_go",
        "range_expansion",
        "donchian_20",
        "nr7_breakout",
        "weekly_swing",
        "weekly_vcp",
        "weekly_breakout",
        "monthly_position",
        "monthly_52w_high",
        "monthly_pullback",
    } <= names


def test_dual_admit_selection_and_trigger_filter() -> None:
    assert selected_dual_admit_ids(["trend_breakout", "donchian_20"]) == ["donchian_20"]
    n = 40
    close = [100.0] * (n - 1) + [108.0]
    high = [101.0] * (n - 1) + [108.5]
    df = _ohlcv(n=n, close=close, high=high, low=[99.0] * (n - 1) + [107.0], open_=[100.0] * (n - 1) + [101.0])
    hits = triggered_dual_admit_ids(df, ["donchian_20", "weekly_swing"])
    assert "donchian_20" in hits
    assert "weekly_swing" not in hits  # too few bars for weekly stage 2


def test_stage2_plugins_keep_live_breakout_and_add_shadow_sleeves() -> None:
    df = _ohlcv(n=80)
    plugins = build_default_strategy_plugins(
        signal={"signal_score": 70.0, "entry_family": "stage2", "price": 65.0, "sma_50": 60.0, "sma_200": 50.0},
        candidate={"df": df, "entry_family": "stage2"},
        pullback_mode="off",
    )
    by_name = {p["name"]: p for p in plugins}
    assert by_name["trend_breakout"]["triggered"] is True
    assert by_name["trend_breakout"]["mode"] == "live"
    assert by_name["donchian_20"]["mode"] == "shadow"


def test_horizon_only_does_not_trigger_live_breakout() -> None:
    df = _ohlcv(n=80)
    plugins = build_default_strategy_plugins(
        signal={"signal_score": 70.0, "entry_family": "horizon"},
        candidate={"df": df, "entry_family": "horizon"},
        pullback_mode="off",
    )
    by_name = {p["name"]: p for p in plugins}
    assert by_name["trend_breakout"]["triggered"] is False


def test_catalog_lists_multiple_strategies_per_timeframe() -> None:
    payload = build_scan_catalog_payload()
    by_tf: dict[str, list[str]] = {}
    for row in payload["strategies"]:
        by_tf.setdefault(str(row["timeframe"]), []).append(str(row["id"]))
    assert len(by_tf["intraday"]) >= 3
    assert len(by_tf["daily"]) >= 5
    assert len(by_tf["weekly"]) >= 3
    assert len(by_tf["monthly"]) >= 3
    assert "donchian_20" in by_tf["daily"]
    assert "weekly_vcp" in by_tf["weekly"]
    assert "monthly_52w_high" in by_tf["monthly"]
    weekly = next(s for s in payload["strategies"] if s["id"] == "weekly_swing")
    assert weekly.get("proxy_ids") in ((), [], None)


def test_weekly_default_ids_are_weekly_sleeves() -> None:
    ids = resolve_strategy_ids([], scan_timeframe="weekly")
    assert ids[0] == "weekly_swing"
    assert "weekly_vcp" in ids
    assert "trend_breakout" not in ids


def test_weekly_filter_requires_triggered_plugin_not_stage2_proxy() -> None:
    rows = [
        {"ticker": "AAPL", "entry_family": "stage2", "strategy_attribution": {"top_live": "trend_breakout"}},
        {
            "ticker": "MSFT",
            "entry_family": "horizon",
            "strategy_plugins": [{"name": "weekly_swing", "triggered": True, "mode": "shadow"}],
        },
    ]
    kept = filter_signals_for_scan_selection(rows, ["weekly_swing"])
    assert [r["ticker"] for r in kept] == ["MSFT"]


def test_untriggered_plugin_does_not_match_filter() -> None:
    sig = {
        "ticker": "X",
        "entry_family": "stage2",
        "strategy_plugins": [{"name": "donchian_20", "triggered": False, "mode": "shadow"}],
        "strategy_attribution": {"top_live": "trend_breakout"},
    }
    assert signal_matches_strategy_ids(sig, ["donchian_20"]) is False
    assert signal_matches_strategy_ids(sig, ["trend_breakout"]) is True


def test_horizon_catalog_fields_prefer_triggered_sleeve() -> None:
    fields = catalog_fields_for_signal(
        {
            "entry_family": "horizon",
            "strategy_plugins": [{"name": "weekly_vcp", "triggered": True, "mode": "shadow"}],
            "strategy_attribution": {"top_live": "trend_breakout", "top_shadow": "weekly_vcp"},
        }
    )
    assert fields["catalog_id"] == "weekly_vcp"
    assert fields["timeframe"] == "weekly"


def test_dual_admit_set_excludes_live_breakout() -> None:
    assert "trend_breakout" not in DUAL_ADMIT_STRATEGY_IDS
    assert "pead_primary" not in DUAL_ADMIT_STRATEGY_IDS
    assert "breakout_confirm" not in DUAL_ADMIT_STRATEGY_IDS
    assert "donchian_20" in DUAL_ADMIT_STRATEGY_IDS
