from __future__ import annotations

from core.scan_catalog import (
    build_scan_catalog_payload,
    catalog_fields_for_signal,
    filter_signals_for_scan_selection,
    resolve_strategy_ids,
    scan_studio_public_config,
    selected_strategies_bypass_bull_regime,
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
    assert payload["notes"]["one_thesis"]
    by_tf = {t["id"]: t.get("default_strategy_id") for t in payload["timeframes"]}
    assert by_tf == {
        "intraday": "breakout_confirm",
        "daily": "trend_breakout",
        "weekly": "weekly_swing",
        "monthly": "monthly_position",
    }


def test_scan_studio_public_config_advertises_live_book() -> None:
    cfg = scan_studio_public_config()
    assert cfg["enabled"] is True
    assert cfg["live_book_strategy_id"] == "trend_breakout"
    assert cfg["default_universe"] == "sp1500"
    assert cfg["default_timeframe"] == "daily"


def test_counter_trend_only_bypasses_bull_regime() -> None:
    assert selected_strategies_bypass_bull_regime(["st_reversal_5d"]) is True
    assert selected_strategies_bypass_bull_regime(["st_reversal_5d", "weekly_reversal"]) is True
    assert selected_strategies_bypass_bull_regime(["trend_breakout", "st_reversal_5d"]) is False
    assert selected_strategies_bypass_bull_regime([]) is False
    assert selected_strategies_bypass_bull_regime(["weekly_swing"]) is False


def test_resolve_strategy_ids_defaults_to_timeframe() -> None:
    weekly = resolve_strategy_ids([], scan_timeframe="weekly")
    assert weekly == ["weekly_swing"]
    assert resolve_strategy_ids(["nope"], scan_timeframe=None) == []
    assert resolve_strategy_ids(["trend_breakout", "trend_breakout"]) == ["trend_breakout"]
    assert resolve_strategy_ids([], scan_timeframe="daily") == ["trend_breakout"]
    assert resolve_strategy_ids([], scan_timeframe="monthly") == ["monthly_position"]
    assert resolve_strategy_ids([], scan_timeframe="intraday") == ["breakout_confirm"]


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


def test_dashboard_html_embeds_full_scan_catalog() -> None:
    from pathlib import Path

    from core.scan_catalog import known_strategy_ids
    from webapp.static_assets import render_dashboard_html

    html_path = Path(__file__).resolve().parent.parent / "webapp" / "static" / "index.html"
    body = render_dashboard_html(html_path).body.decode("utf-8")
    assert "__SCAN_CATALOG_JSON__" not in body
    assert "scanCatalogBootstrap" in body
    assert "/static/panels/scanStudio.js?v=" in body
    for sid in known_strategy_ids():
        assert sid in body


def test_js_fallback_catalog_covers_python_strategy_ids() -> None:
    import re
    from pathlib import Path

    from core.scan_catalog import known_strategy_ids

    js = (Path(__file__).resolve().parent.parent / "webapp" / "static" / "panels" / "scanStudio.js").read_text(
        encoding="utf-8"
    )
    ids = set(re.findall(r'_fb\("([a-z0-9_]+)"', js))
    missing = sorted(known_strategy_ids() - ids)
    assert not missing, f"JS fallback catalog missing {missing}"
