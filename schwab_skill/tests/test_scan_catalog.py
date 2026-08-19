from __future__ import annotations

from core.scan_catalog import (
    build_scan_catalog_payload,
    catalog_fields_for_signal,
    filter_signals_for_scan_selection,
    resolve_strategy_ids,
    signal_matches_strategy_ids,
)


def test_catalog_payload_has_four_timeframes_and_live_breakout() -> None:
    payload = build_scan_catalog_payload()
    tf_ids = [t["id"] for t in payload["timeframes"]]
    assert tf_ids == ["intraday", "daily", "weekly", "monthly"]
    ids = {s["id"] for s in payload["strategies"]}
    assert "trend_breakout" in ids
    assert "pullback" in ids
    assert "weekly_swing" in ids
    assert "momentum_12_1" in ids
    assert "opening_range_breakout" in ids
    assert "donchian_20" in ids
    assert "gap_and_go" in ids
    assert "monthly_52w_high" in ids
    universes = {u["id"]: u for u in payload["universes"]}
    assert universes["sp1500"]["available"] is True
    assert universes["nasdaq100"]["available"] is True
    assert universes["russell2000"]["available"] is False


def test_resolve_strategy_ids_defaults_to_timeframe() -> None:
    weekly = resolve_strategy_ids([], scan_timeframe="weekly")
    assert weekly[0] == "weekly_swing"
    assert set(weekly) >= {"weekly_swing", "weekly_vcp", "weekly_breakout"}
    assert resolve_strategy_ids(["nope"], scan_timeframe=None) == []
    assert resolve_strategy_ids(["trend_breakout", "trend_breakout"]) == ["trend_breakout"]


def test_filter_weekly_sleeve_matches_triggered_plugin_not_live_breakout() -> None:
    rows = [
        {"ticker": "AAPL", "entry_family": "stage2", "strategy_attribution": {"top_live": "trend_breakout"}},
        {"ticker": "MSFT", "entry_family": "pead_primary", "strategy_attribution": {"top_live": "trend_breakout"}},
        {
            "ticker": "NVDA",
            "entry_family": "horizon",
            "strategy_plugins": [{"name": "weekly_swing", "triggered": True, "mode": "shadow"}],
        },
    ]
    kept = filter_signals_for_scan_selection(rows, ["weekly_swing"])
    assert [r["ticker"] for r in kept] == ["NVDA"]
    pull = filter_signals_for_scan_selection(rows, ["pullback"])
    assert pull == []
    pead = filter_signals_for_scan_selection(rows, ["pead_primary"])
    assert [r["ticker"] for r in pead] == ["MSFT"]


def test_breakout_confirm_matcher() -> None:
    sig = {"ticker": "NVDA", "breakout_confirmed": True, "strategy_attribution": {"top_live": "trend_breakout"}}
    assert signal_matches_strategy_ids(sig, ["breakout_confirm"]) is True
    assert signal_matches_strategy_ids({"ticker": "X", "breakout_confirmed": False}, ["breakout_confirm"]) is False


def test_catalog_fields_stamp_description() -> None:
    fields = catalog_fields_for_signal(
        {"entry_family": "stage2", "strategy_attribution": {"top_live": "trend_breakout"}}
    )
    assert fields["catalog_id"] == "trend_breakout"
    assert fields["timeframe"] == "daily"
    assert "Stage 2" in fields["description"] or "VCP" in fields["description"]


def test_run_scan_applies_strategy_id_filter(tmp_path, monkeypatch) -> None:
    from core import scan_service

    signals = [
        {"ticker": "AAPL", "entry_family": "stage2", "strategy_attribution": {"top_live": "trend_breakout"}},
        {
            "ticker": "MSFT",
            "entry_family": "stage2",
            "strategy_attribution": {"top_live": "trend_breakout"},
            "strategy_plugins": [{"name": "pullback", "triggered": True, "mode": "shadow"}],
        },
    ]

    def _fake_scan(**kwargs):
        return list(signals), {"watchlist_source": "universe_nasdaq100"}

    monkeypatch.setattr(scan_service, "scan_for_signals_detailed", _fake_scan)
    out = scan_service.run_scan(
        skill_dir=tmp_path,
        universe_preset="nasdaq100",
        strategy_ids=["pullback"],
        scan_timeframe="daily",
    )
    assert [s["ticker"] for s in out.signals] == ["MSFT"]
    assert out.diagnostics["strategy_id_filter_dropped"] == 1
    assert out.diagnostics["scan_timeframe"] == "daily"
    assert out.diagnostics["universe_preset"] == "nasdaq100"
