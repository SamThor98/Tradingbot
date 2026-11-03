"""Liquid positioning layer: normalizer, thin-flag, store round-trip, config."""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.liquid_positioning.normalizer import normalize_meta_and_ctxs
from core.liquid_positioning.schema import LiquidSnapshot
from core.liquid_positioning.store import store_snapshots


def _payload():
    return [
        {"universe": [{"name": "xyz:AAPL", "maxLeverage": 20}, {"name": "xyz:CVX", "maxLeverage": 10}, {"name": "xyz:THIN", "maxLeverage": 5}]},
        [
            {"funding": "0.0000034", "openInterest": "178089.218", "markPx": "343.63", "dayNtlVlm": "25289486.28", "premium": "0.00012"},
            {"funding": "-0.0000247", "openInterest": "1024.28", "markPx": "226.65", "dayNtlVlm": "65544.61", "premium": "-0.0003"},
            {"funding": "0.0", "openInterest": "10", "markPx": "5.0", "dayNtlVlm": "100", "premium": "0"},
        ],
    ]


def test_normalize_derives_oi_usd_and_thin_flag():
    snaps, malformed = normalize_meta_and_ctxs(_payload(), min_oi_usd=1_000_000.0)
    assert malformed == 0 and len(snaps) == 3
    aapl = snaps[0]
    assert aapl.ticker == "AAPL" and aapl.source_coin == "xyz:AAPL"
    assert abs(aapl.open_interest_usd - 178089.218 * 343.63) < 1.0
    assert aapl.is_thin is False
    assert snaps[1].is_thin is True  # CVX-scale OI (~$232K) is no-signal
    assert snaps[2].is_thin is True


def test_normalize_rejects_bad_payload():
    snaps, malformed = normalize_meta_and_ctxs({"nope": 1}, min_oi_usd=1.0)
    assert snaps == [] and malformed == 1


def test_store_round_trip(tmp_path, monkeypatch):
    db_path = tmp_path / "liquid_test.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    # Force the standalone-engine path in store._get_session
    import core.liquid_positioning.store as store

    original = store._get_session

    def standalone(skill_dir=None):
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker

        eng = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
        store.Base.metadata.create_all(bind=eng)
        return sessionmaker(bind=eng)()

    monkeypatch.setattr(store, "_get_session", standalone)
    try:
        snap = LiquidSnapshot(
            ticker="AAPL", source_coin="xyz:AAPL", ts=datetime.now(timezone.utc),
            mark_px=343.63, funding_rate=0.0000034, open_interest_coin=178089.218,
            open_interest_usd=61_200_000.0, is_thin=False,
        )
        assert store_snapshots([snap]) == 1
        cov = store.snapshot_coverage()
        assert cov["rows"] == 1 and cov["tickers"] == 1
        latest = store.get_latest_snapshots()
        assert latest[0]["ticker"] == "AAPL"
    finally:
        monkeypatch.setattr(store, "_get_session", original)


def test_funding_aggregation_and_asof_no_lookahead():
    from datetime import date

    from core.liquid_positioning.features import aggregate_funding_daily, funding_features_asof

    base_ms = 1764547200000  # 2025-12-01T00:00Z
    rows = []
    for d in range(30):
        for h in (0, 12):
            rows.append({"time": base_ms + (d * 24 + h) * 3600_000, "fundingRate": "0.0001", "coin": "xyz:AAPL"})
    # Same-day (entry day) spike must never leak into features for that day.
    entry_day_start = base_ms + 30 * 24 * 3600_000
    rows.append({"time": entry_day_start + 3600_000, "fundingRate": "0.01", "coin": "xyz:AAPL"})
    daily = aggregate_funding_daily(rows)
    assert len(daily) == 31 and daily[0]["n_hours"] == 2

    feats = funding_features_asof(daily, date(2025, 12, 31))
    assert feats["liquid_funding_days"] == 30
    assert feats["liquid_funding_last"] == 0.0001  # not the 0.01 same-day spike
    assert feats["liquid_funding_z90"] == 0.0

    empty = funding_features_asof([], date(2025, 12, 31))
    assert empty["liquid_funding_last"] is None and empty["liquid_funding_days"] == 0


def test_backfill_with_fake_provider(monkeypatch):
    import core.liquid_positioning.backfill as backfill
    import core.liquid_positioning.store as store

    captured = {}
    monkeypatch.setattr(store, "store_funding_daily", lambda t, rows, skill_dir=None: captured.update({t: rows}) or len(rows))
    monkeypatch.setattr(backfill, "store_funding_daily", lambda t, rows, skill_dir=None: captured.update({t: rows}) or len(rows))

    class FakeProvider:
        def get_funding_history(self, ticker):
            base_ms = 1764547200000
            return [{"time": base_ms + h * 3600_000, "fundingRate": "0.0002"} for h in range(48)]

    diag = backfill.backfill_funding_for_tickers(FakeProvider(), ["AAPL"])
    assert diag["stored_days"] == 2 and diag["errors"] == []
    assert captured["AAPL"][0]["funding_mean"] == 0.0002


def test_study_buckets_label_thin_samples_insufficient():
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "liquid_study", Path(__file__).resolve().parent.parent / "scripts" / "liquid_funding_shadow_study.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    trades = [
        {"ticker": "AAPL", "net_return": 0.05, "liquid_funding_days": 100, "liquid_funding_z90": 2.0},
        {"ticker": "MSFT", "net_return": -0.03, "liquid_funding_days": 100, "liquid_funding_z90": 0.1},
        {"ticker": "BIIB", "net_return": 0.02, "liquid_funding_days": 0, "liquid_funding_z90": None},
    ]
    out = mod.analyze_trades(trades)
    assert out["total_trades"] == 3 and out["covered_trades"] == 2
    assert out["buckets"]["crowded_long"]["n"] == 1
    assert out["buckets"]["crowded_long"]["status"] == "insufficient_sample"
    assert out["verdict"] == "insufficient_sample"
    assert out["buckets"]["covered_all"]["pf_net"] is not None


def test_config_defaults_are_shadow_and_collect_off():
    from config import get_liquid_collect_enabled, get_liquid_min_oi_usd, get_liquid_overlay_mode

    assert get_liquid_overlay_mode() in ("off", "shadow", "live")
    assert isinstance(get_liquid_collect_enabled(), bool)
    assert get_liquid_min_oi_usd() >= 0
