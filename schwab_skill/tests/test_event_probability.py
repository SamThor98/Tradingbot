"""Event-probability layer: normalization, calibration, mapping, valuation, PIT.

Every case is deterministic — fixture markets, frozen valuation inputs, an
in-memory SQLite database, and no RNG. Nothing here touches the network.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from core.event_probability.calibrator import DEFAULT_PRIOR, QualityInputs, calibrate
from core.event_probability.impact import (
    ASSUME_INDEPENDENT_EVENTS,
    MAX_ENUMERATED_EVENTS,
    EventExposure,
    compute_impact,
    scenario_probability,
)
from core.event_probability.mapper import load_catalog
from core.event_probability.normalizer import (
    build_event_id,
    compute_probability_changes,
    normalize_market,
    observation_from_event,
)
from core.event_probability.providers import (
    HistoricalFileEventProvider,
    StubEventProvider,
    load_fixture_markets,
)
from core.event_probability.repository import EventRepository
from core.event_probability.schema import (
    EventObservation,
    EventProbabilityConfig,
    ExposureMapping,
    MalformedEventError,
    ProviderError,
    RawMarket,
)
from core.event_probability.sensitivity import compute_sensitivity, largest_exposures
from core.event_probability.service import EventProbabilityService
from core.event_probability.signal import compute_event_mispricing_score
from core.event_probability.taxonomy import OTHER, infer_category, normalize_category
from core.event_probability.valuation import (
    FUNDAMENTAL_VARIABLES,
    ValuationInputs,
    apply_shocks,
    value_equity,
    value_with_shocks,
)

SKILL_DIR = Path(__file__).resolve().parent.parent

T0 = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)


def _config(**overrides: Any) -> EventProbabilityConfig:
    return replace(
        EventProbabilityConfig(
            enabled=True,
            mode="live",
            provider="stub",
            min_liquidity=1000.0,
            max_spread=0.08,
            max_event_age_hours=72.0,
            max_market_weight=0.85,
            lookback_days=(1, 7, 30),
            ems_lookback_days=7,
        ),
        **overrides,
    )


def _market(**overrides: Any) -> RawMarket:
    return replace(
        RawMarket(
            source="stub",
            source_market_id="evt-1",
            question="Will the US impose tighter China semiconductor export controls?",
            raw_probability=0.40,
            liquidity=50_000.0,
            spread=0.01,
            volume=250_000.0,
            updated_ts=T0,
        ),
        **overrides,
    )


def _inputs(**overrides: Any) -> ValuationInputs:
    return replace(
        ValuationInputs(
            ticker="NVDA",
            last_fcf=6.0e10,
            growth=0.18,
            wacc=0.10,
            terminal_growth=0.025,
            net_debt=-3.0e10,
            shares=2.45e10,
            price=180.0,
        ),
        **overrides,
    )


def _mapping(**overrides: Any) -> ExposureMapping:
    return replace(
        ExposureMapping(
            event_id="stub:evt-1",
            ticker="NVDA",
            fundamental_variable="revenue_growth",
            impact_if_yes=-0.06,
            impact_if_no=0.0,
            materiality=1.0,
            direction="negative",
        ),
        **overrides,
    )


@pytest.fixture()
def db() -> Iterator[Session]:
    """In-memory database with only the event-probability tables created."""
    from webapp.db import Base
    from webapp.models import EventProbabilityEvent, EventProbabilityHistory  # noqa: F401

    engine = create_engine("sqlite://")
    Base.metadata.create_all(
        engine,
        tables=[
            Base.metadata.tables["event_probability_events"],
            Base.metadata.tables["event_probability_history"],
        ],
    )
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()


# --------------------------------------------------------------------- taxonomy


def test_infer_category_prefers_narrow_match() -> None:
    assert infer_category("New export control rules on AI chips") == "export_control"
    assert infer_category("Will the FOMC cut the interest rate?") == "monetary_policy"
    assert infer_category("Will CPI come in above 3%?") == "inflation"


def test_infer_category_falls_back_to_other() -> None:
    assert infer_category("Who wins the local pie contest?") == OTHER
    assert infer_category("") == OTHER


def test_normalize_category_accepts_known_and_rejects_unknown() -> None:
    assert normalize_category("monetary_policy") == "monetary_policy"
    assert normalize_category("Monetary Policy") == "monetary_policy"
    assert normalize_category("tea_leaves") == OTHER


# ------------------------------------------------------------------ calibration


def test_calibration_blends_toward_prior_and_caps_market_weight() -> None:
    result = calibrate(
        QualityInputs(raw_probability=0.80, liquidity=1e9, spread=0.0, age_hours=0.0, prior_probability=0.40),
        config=_config(),
    )
    assert result.market_weight == pytest.approx(0.85)
    assert result.adjusted_probability == pytest.approx((0.85 * 0.80) + (0.15 * 0.40))


def test_zero_liquidity_falls_back_entirely_to_prior() -> None:
    result = calibrate(
        QualityInputs(raw_probability=0.90, liquidity=0.0, spread=0.01, age_hours=0.0, prior_probability=0.30),
        config=_config(),
    )
    assert result.market_weight == 0.0
    assert result.adjusted_probability == pytest.approx(0.30)
    assert "zero_liquidity" in result.warnings


def test_missing_prior_uses_uninformative_default() -> None:
    result = calibrate(
        QualityInputs(raw_probability=0.90, liquidity=None, spread=0.01, age_hours=0.0),
        config=_config(),
    )
    assert result.prior_probability == DEFAULT_PRIOR
    assert result.adjusted_probability == pytest.approx(DEFAULT_PRIOR)


def test_stale_price_zeroes_freshness_component() -> None:
    fresh = calibrate(
        QualityInputs(raw_probability=0.7, liquidity=50_000.0, spread=0.01, age_hours=1.0, prior_probability=0.5),
        config=_config(),
    )
    stale = calibrate(
        QualityInputs(raw_probability=0.7, liquidity=50_000.0, spread=0.01, age_hours=500.0, prior_probability=0.5),
        config=_config(),
    )
    assert "stale_price" in stale.warnings
    assert stale.components["freshness_score"] == 0.0
    assert stale.market_weight < fresh.market_weight


def test_invalid_probability_is_clamped_and_flagged() -> None:
    high = calibrate(
        QualityInputs(raw_probability=1.4, liquidity=50_000.0, spread=0.01, age_hours=0.0, prior_probability=0.5),
        config=_config(),
    )
    assert high.raw_probability == 1.0
    assert "probability_out_of_range" in high.warnings

    junk = calibrate(
        QualityInputs(raw_probability="not-a-number", liquidity=50_000.0, spread=0.01, age_hours=0.0),
        config=_config(),
    )
    assert "invalid_probability" in junk.warnings
    assert 0.0 <= junk.adjusted_probability <= 1.0


# ----------------------------------------------------------------- normalization


def test_event_id_is_stable_and_source_scoped() -> None:
    assert build_event_id("Polymarket", " abc-123 ") == "polymarket:abc-123"
    with pytest.raises(MalformedEventError):
        build_event_id("stub", "")


def test_normalize_market_populates_schema() -> None:
    event, calibration = normalize_market(_market(), as_of=T0, history=[], config=_config())
    assert event.event_id == "stub:evt-1"
    assert event.event_category == "export_control"
    assert event.current_probability == pytest.approx(0.40)
    assert event.adjusted_probability == pytest.approx(calibration.adjusted_probability)
    assert event.resolution_status == "open"
    assert event.previous_probability is None


def test_normalize_market_rejects_missing_name() -> None:
    with pytest.raises(MalformedEventError):
        normalize_market(_market(question="   "), as_of=T0, history=[], config=_config())


def test_event_past_resolution_date_without_outcome_is_not_open() -> None:
    event, _ = normalize_market(
        _market(resolution_date=T0 - timedelta(days=1)),
        as_of=T0,
        history=[],
        config=_config(),
    )
    assert event.resolution_status == "unknown"
    assert not event.is_open()


# -------------------------------------------------------------- change lookbacks


def _obs(days_before: int, adjusted: float) -> EventObservation:
    return EventObservation(
        event_id="stub:evt-1",
        timestamp=T0 - timedelta(days=days_before),
        raw_probability=adjusted,
        adjusted_probability=adjusted,
    )


def test_probability_changes_use_newest_observation_at_or_before_cutoff() -> None:
    history = [_obs(40, 0.10), _obs(30, 0.20), _obs(7, 0.30), _obs(1, 0.45)]
    changes = compute_probability_changes(
        current_adjusted=0.50,
        history=history,
        as_of=T0,
        lookback_days=(1, 7, 30),
    )
    assert changes[1] == pytest.approx(0.05)
    assert changes[7] == pytest.approx(0.20)
    assert changes[30] == pytest.approx(0.30)


def test_missing_baseline_reports_none_not_zero() -> None:
    changes = compute_probability_changes(
        current_adjusted=0.50,
        history=[_obs(2, 0.40)],
        as_of=T0,
        lookback_days=(1, 7, 30),
    )
    assert changes[1] == pytest.approx(0.10)
    assert changes[7] is None
    assert changes[30] is None


def test_future_observations_are_ignored() -> None:
    future = EventObservation(
        event_id="stub:evt-1",
        timestamp=T0 + timedelta(days=5),
        raw_probability=0.99,
        adjusted_probability=0.99,
    )
    changes = compute_probability_changes(
        current_adjusted=0.50,
        history=[_obs(10, 0.20), future],
        as_of=T0,
        lookback_days=(7,),
    )
    assert changes[7] == pytest.approx(0.30)


# ------------------------------------------------------------------- valuation


def test_shock_variables_map_to_the_documented_inputs() -> None:
    base = _inputs()
    grown, mult = apply_shocks(base, [("revenue_growth", 0.02)])
    assert grown.growth == pytest.approx(0.20)
    assert mult == 1.0

    discounted, _ = apply_shocks(base, [("wacc", -0.005)])
    assert discounted.wacc == pytest.approx(0.095)

    _, multiple = apply_shocks(base, [("price_multiple", 0.10)])
    assert multiple == pytest.approx(1.10)


def test_wacc_is_kept_above_terminal_growth() -> None:
    shocked, _ = apply_shocks(_inputs(), [("wacc", -0.50)])
    assert shocked.wacc > shocked.terminal_growth


def test_unsupported_variable_is_ignored_not_applied_silently_wrong() -> None:
    shocked, mult = apply_shocks(_inputs(), [("moon_phase", 0.5)])
    assert shocked == _inputs()
    assert mult == 1.0
    assert "moon_phase" not in FUNDAMENTAL_VARIABLES


def test_negative_growth_shock_lowers_value() -> None:
    base = _inputs()
    assert value_with_shocks(base, [("revenue_growth", -0.06)]) < value_equity(base)


# ---------------------------------------------------------- impact + sensitivity


def _exposure(mapping: ExposureMapping, probability: float, *, event_id: str = "stub:evt-1") -> EventExposure:
    event, _ = normalize_market(
        _market(source_market_id=event_id.split(":", 1)[1], raw_probability=probability),
        as_of=T0,
        history=[],
        config=_config(max_market_weight=1.0),
    )
    return EventExposure(event=event, mapping=mapping)


def test_binary_expected_value_matches_manual_weighting() -> None:
    inputs = _inputs()
    exposure = _exposure(_mapping(), 0.40)
    p = exposure.probability
    value_yes = value_with_shocks(inputs, [("revenue_growth", -0.06)])
    value_no = value_equity(inputs)

    result = compute_impact(inputs, [exposure])
    assert result.expected_value == pytest.approx((p * value_yes) + ((1 - p) * value_no))
    assert result.valuation_adjustment == pytest.approx(result.expected_value - result.base_value)
    assert len(result.scenarios) == 2


def test_materiality_scales_the_modeled_impact_once() -> None:
    inputs = _inputs()
    full = compute_impact(inputs, [_exposure(_mapping(materiality=1.0), 0.40)])
    half = compute_impact(inputs, [_exposure(_mapping(materiality=0.5), 0.40)])
    assert abs(half.valuation_adjustment) < abs(full.valuation_adjustment)


def test_two_events_enumerate_four_independent_scenarios() -> None:
    exposures = [
        _exposure(_mapping(), 0.40, event_id="stub:evt-1"),
        _exposure(
            _mapping(event_id="stub:evt-2", fundamental_variable="wacc", impact_if_yes=-0.005, direction="positive"),
            0.60,
            event_id="stub:evt-2",
        ),
    ]
    result = compute_impact(_inputs(), exposures)
    assert ASSUME_INDEPENDENT_EVENTS is True
    assert len(result.scenarios) == 4
    assert sum(row.probability for row in result.scenarios) == pytest.approx(1.0)


def test_scenario_probability_is_the_independence_seam() -> None:
    exposures = [
        _exposure(_mapping(), 0.40, event_id="stub:evt-1"),
        _exposure(_mapping(event_id="stub:evt-2"), 0.50, event_id="stub:evt-2"),
    ]
    p0, p1 = exposures[0].probability, exposures[1].probability
    assert scenario_probability(exposures, (True, True)) == pytest.approx(p0 * p1)
    assert scenario_probability(exposures, (True, False)) == pytest.approx(p0 * (1 - p1))


def test_resolved_events_are_excluded_with_a_reason() -> None:
    event, _ = normalize_market(
        _market(resolution_status="resolved_yes"),
        as_of=T0,
        history=[],
        config=_config(),
    )
    result = compute_impact(_inputs(), [EventExposure(event=event, mapping=_mapping())])
    assert result.valuation_adjustment == pytest.approx(0.0)
    assert result.excluded[0]["reason"] == "resolution_status:resolved_yes"


def test_large_event_sets_truncate_enumeration_and_say_so() -> None:
    exposures = [
        _exposure(_mapping(event_id=f"stub:evt-{i}", impact_if_yes=-0.01 * (i + 1)), 0.30, event_id=f"stub:evt-{i}")
        for i in range(MAX_ENUMERATED_EVENTS + 2)
    ]
    result = compute_impact(_inputs(), exposures)
    assert len(result.truncated_event_ids) == 2
    assert len(result.scenarios) == 2**MAX_ENUMERATED_EVENTS


def test_sensitivity_is_the_yes_no_spread_per_percentage_point() -> None:
    inputs = _inputs()
    exposure = _exposure(_mapping(), 0.40)
    sens = compute_sensitivity(inputs, exposure)
    expected = (sens.value_if_yes - sens.value_if_no) / 100.0
    assert sens.dollar_per_pp == pytest.approx(expected)
    assert sens.dollar_per_pp < 0
    assert sens.pct_of_price_per_pp == pytest.approx((expected / inputs.price) * 100.0)


def test_largest_exposures_splits_positive_and_negative() -> None:
    inputs = _inputs()
    negative = compute_sensitivity(inputs, _exposure(_mapping(), 0.40))
    positive = compute_sensitivity(
        inputs,
        _exposure(
            _mapping(event_id="stub:evt-2", fundamental_variable="wacc", impact_if_yes=-0.005),
            0.60,
            event_id="stub:evt-2",
        ),
    )
    best, worst = largest_exposures([negative, positive])
    assert best is positive
    assert worst is negative


def test_distribution_metrics_are_probability_weighted() -> None:
    result = compute_impact(_inputs(), [_exposure(_mapping(), 0.40)])
    dist = result.distribution
    assert dist.p10 <= dist.median_value <= dist.p90
    assert 0.0 <= dist.prob_loss <= 1.0
    assert dist.expected_value == pytest.approx(result.expected_value)


# -------------------------------------------------------------------- mispricing


def test_mispricing_gap_is_implied_minus_realized() -> None:
    inputs = _inputs()
    exposure = _exposure(_mapping(), 0.40)
    event = exposure.event
    event.probability_change_7d = -0.10
    sens = compute_sensitivity(inputs, exposure)

    score = compute_event_mispricing_score(
        ticker="NVDA",
        events_by_id={event.event_id: event},
        sensitivities=[sens],
        lookback_days=7,
        realized_return_pct=2.0,
    )
    assert sens.pct_of_price_per_pp is not None
    expected_implied = (-0.10 * 100.0) * sens.pct_of_price_per_pp
    assert score.implied_move_pct == pytest.approx(expected_implied)
    assert score.ems_gap_pct == pytest.approx(expected_implied - 2.0)
    assert score.contributing_events == 1


def test_events_without_a_lookback_baseline_do_not_contribute() -> None:
    inputs = _inputs()
    exposure = _exposure(_mapping(), 0.40)
    exposure.event.probability_change_7d = None
    score = compute_event_mispricing_score(
        ticker="NVDA",
        events_by_id={exposure.event.event_id: exposure.event},
        sensitivities=[compute_sensitivity(inputs, exposure)],
        lookback_days=7,
        realized_return_pct=1.0,
    )
    assert score.implied_move_pct == 0.0
    assert score.contributing_events == 0
    assert score.missing_change_events == 1


def test_missing_realized_return_leaves_gap_undefined() -> None:
    inputs = _inputs()
    exposure = _exposure(_mapping(), 0.40)
    exposure.event.probability_change_7d = 0.05
    score = compute_event_mispricing_score(
        ticker="NVDA",
        events_by_id={exposure.event.event_id: exposure.event},
        sensitivities=[compute_sensitivity(inputs, exposure)],
        lookback_days=7,
        realized_return_pct=None,
    )
    assert score.ems_gap_pct is None


# ----------------------------------------------------------------------- mapping


def _write_catalog(tmp_path: Path, mappings: list[dict]) -> Path:
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps({"version": 1, "mappings": mappings}), encoding="utf-8")
    return path


def test_effective_window_excludes_maps_outside_the_as_of_range(tmp_path: Path) -> None:
    path = _write_catalog(
        tmp_path,
        [
            {
                "event_id": "stub:evt-1",
                "ticker": "NVDA",
                "fundamental_variable": "revenue_growth",
                "impact_if_yes": -0.06,
                "effective_from": "2026-01-01T00:00:00Z",
                "effective_to": "2026-06-01T00:00:00Z",
            },
            {
                "event_id": "stub:evt-1",
                "ticker": "NVDA",
                "fundamental_variable": "revenue_growth",
                "impact_if_yes": -0.03,
                "effective_from": "2026-06-01T00:00:00Z",
            },
        ],
    )
    catalog = load_catalog(path)
    assert len(catalog) == 2

    old = catalog.for_ticker("NVDA", as_of=datetime(2026, 3, 1, tzinfo=timezone.utc))
    assert [m.impact_if_yes for m in old] == [-0.06]

    new = catalog.for_ticker("NVDA", as_of=datetime(2026, 8, 1, tzinfo=timezone.utc))
    assert [m.impact_if_yes for m in new] == [-0.03]


def test_invalid_rows_are_rejected_and_counted(tmp_path: Path) -> None:
    path = _write_catalog(
        tmp_path,
        [
            {"ticker": "NVDA", "fundamental_variable": "revenue_growth", "impact_if_yes": -0.01},
            {"event_id": "stub:evt-1", "fundamental_variable": "revenue_growth", "impact_if_yes": -0.01},
            {"event_id": "stub:evt-1", "ticker": "NVDA", "fundamental_variable": "vibes", "impact_if_yes": -0.01},
            {"event_id": "stub:evt-1", "ticker": "NVDA", "fundamental_variable": "revenue_growth"},
            {"event_id": "stub:evt-1", "ticker": "NVDA", "fundamental_variable": "wacc", "impact_if_yes": 0.01},
        ],
    )
    catalog = load_catalog(path)
    assert len(catalog) == 1
    assert len(catalog.errors) == 4


def test_duplicate_mapping_rows_are_dropped(tmp_path: Path) -> None:
    row = {
        "event_id": "stub:evt-1",
        "ticker": "NVDA",
        "fundamental_variable": "revenue_growth",
        "impact_if_yes": -0.06,
    }
    catalog = load_catalog(_write_catalog(tmp_path, [row, dict(row)]))
    assert len(catalog) == 1
    assert any("duplicate_mapping" in err for err in catalog.errors)


def test_missing_catalog_is_empty_not_fatal(tmp_path: Path) -> None:
    catalog = load_catalog(tmp_path / "absent.json")
    assert len(catalog) == 0
    assert catalog.errors == []


def test_shipped_catalog_and_fixtures_are_valid_and_consistent() -> None:
    catalog = load_catalog(SKILL_DIR / "research" / "event_exposure_catalog.json")
    assert catalog.errors == []
    assert len(catalog) > 0

    fixture_ids = {
        m.source_market_id for m in load_fixture_markets(SKILL_DIR / "research" / "event_market_fixtures.json")
    }
    assert set(catalog.source_market_ids()) <= fixture_ids


# --------------------------------------------------------------------- providers


def test_stub_provider_serves_only_known_ids() -> None:
    provider = StubEventProvider(markets=[_market()])
    assert provider.fetch_market("evt-1", as_of=T0) is not None
    assert provider.fetch_market("nope", as_of=T0) is None


def test_historical_provider_never_returns_future_observations() -> None:
    provider = HistoricalFileEventProvider(
        markets=[
            _market(raw_probability=0.20, updated_ts=T0 - timedelta(days=10)),
            _market(raw_probability=0.60, updated_ts=T0 + timedelta(days=10)),
        ]
    )
    market = provider.fetch_market("evt-1", as_of=T0)
    assert market is not None
    assert market.raw_probability == pytest.approx(0.20)


def test_provider_failure_is_counted_not_raised(db: Session) -> None:
    class BrokenProvider:
        provider_name = "broken"

        def list_markets(self, *, as_of):
            raise ProviderError("network_error")

        def fetch_market(self, source_market_id, *, as_of):
            raise ProviderError("rate_limited", detail=source_market_id)

    catalog = load_catalog(SKILL_DIR / "research" / "event_exposure_catalog.json")
    service = EventProbabilityService(config=_config(), provider=BrokenProvider(), catalog=catalog)
    diagnostics = service.ingest(db, as_of=T0)
    assert diagnostics.stored == 0
    assert diagnostics.provider_errors > 0
    assert "rate_limited" in diagnostics.error_codes


def test_malformed_provider_payload_is_skipped(db: Session) -> None:
    catalog = load_catalog(SKILL_DIR / "research" / "event_exposure_catalog.json")
    market_id = catalog.source_market_ids()[0]
    provider = StubEventProvider(markets=[_market(source_market_id=market_id, question="")])
    service = EventProbabilityService(config=_config(), provider=provider, catalog=catalog)
    diagnostics = service.ingest(db, as_of=T0)
    assert diagnostics.malformed == 1
    assert diagnostics.stored == 0


# -------------------------------------------------------- repository / point-in-time


def _store(db: Session, *, adjusted: float, at: datetime) -> None:
    repo = EventRepository(db)
    event, _ = normalize_market(
        _market(raw_probability=adjusted, updated_ts=at), as_of=at, history=[], config=_config()
    )
    event.adjusted_probability = adjusted
    event.current_probability = adjusted
    repo.upsert_event(event)
    repo.append_observation(observation_from_event(event, timestamp=at))


def test_history_is_append_only_and_deduped(db: Session) -> None:
    repo = EventRepository(db)
    event, _ = normalize_market(_market(), as_of=T0, history=[], config=_config())
    repo.upsert_event(event)
    assert repo.append_observation(observation_from_event(event, timestamp=T0)) is True
    assert repo.append_observation(observation_from_event(event, timestamp=T0)) is False
    assert len(repo.history(event.event_id)) == 1


def test_event_as_of_ignores_later_observations(db: Session) -> None:
    _store(db, adjusted=0.20, at=T0 - timedelta(days=14))
    _store(db, adjusted=0.30, at=T0 - timedelta(days=7))
    _store(db, adjusted=0.55, at=T0)
    repo = EventRepository(db)

    past = repo.event_as_of("stub:evt-1", as_of=T0 - timedelta(days=7), lookback_days=(1, 7, 30))
    assert past is not None
    assert past.adjusted_probability == pytest.approx(0.30)
    assert past.probability_change_7d == pytest.approx(0.10)

    now = repo.event_as_of("stub:evt-1", as_of=T0, lookback_days=(1, 7, 30))
    assert now is not None
    assert now.adjusted_probability == pytest.approx(0.55)
    assert now.probability_change_7d == pytest.approx(0.25)


def test_event_as_of_before_first_observation_is_none(db: Session) -> None:
    _store(db, adjusted=0.20, at=T0)
    repo = EventRepository(db)
    assert repo.event_as_of("stub:evt-1", as_of=T0 - timedelta(days=1), lookback_days=(7,)) is None


def test_history_query_respects_as_of_bound(db: Session) -> None:
    _store(db, adjusted=0.20, at=T0 - timedelta(days=3))
    _store(db, adjusted=0.40, at=T0)
    repo = EventRepository(db)
    assert len(repo.history("stub:evt-1", as_of=T0 - timedelta(days=1))) == 1
    assert len(repo.history("stub:evt-1")) == 2


# ------------------------------------------------------------------ service payload


def _service(db_catalog_path: Path | None = None) -> EventProbabilityService:
    catalog = load_catalog(db_catalog_path or (SKILL_DIR / "research" / "event_exposure_catalog.json"))
    provider = StubEventProvider(markets=load_fixture_markets(SKILL_DIR / "research" / "event_market_fixtures.json"))
    service = EventProbabilityService(
        config=_config(),
        provider=provider,
        catalog=catalog,
        valuation_loader=lambda ticker: _inputs(ticker=ticker),
        realized_return_loader=lambda ticker, **kwargs: 1.0,
    )
    return service


def test_payload_reports_disabled_when_feature_is_off(db: Session) -> None:
    service = EventProbabilityService(
        config=_config(enabled=False, mode="off"),
        provider=StubEventProvider(),
        catalog=load_catalog(SKILL_DIR / "research" / "event_exposure_catalog.json"),
    )
    payload = service.ticker_payload(db, "NVDA", as_of=T0)
    assert payload["status"] == "disabled"
    assert payload["reason"] == "feature_off"


def test_payload_reports_no_exposure_for_unmapped_ticker(db: Session) -> None:
    payload = _service().ticker_payload(db, "MSFT", as_of=T0)
    assert payload["status"] == "no_exposure"


def test_payload_reports_no_data_before_any_ingest(db: Session) -> None:
    payload = _service().ticker_payload(db, "NVDA", as_of=T0)
    assert payload["status"] == "no_data"


def test_end_to_end_payload_after_ingest(db: Session) -> None:
    service = _service()
    diagnostics = service.ingest(db, as_of=T0)
    assert diagnostics.stored == diagnostics.processed > 0
    assert diagnostics.mapping_errors == 0

    payload = service.ticker_payload(db, "NVDA", as_of=T0)
    assert payload["status"] == "ok"
    assert payload["events"]
    assert payload["summary"]["event_count"] == len(payload["events"])
    assert payload["summary"]["base_value"] is not None
    assert payload["independent_events"] is True
    assert all(entry["sensitivity"] is not None for entry in payload["events"])


def test_repeated_ingest_does_not_duplicate_history(db: Session) -> None:
    service = _service()
    first = service.ingest(db, as_of=T0)
    second = service.ingest(db, as_of=T0)
    assert second.duplicates == first.stored
    assert second.stored == 0


def test_payload_degrades_when_valuation_is_unavailable(db: Session) -> None:
    from core.event_probability.schema import ValuationUnavailableError

    service = _service()
    service.ingest(db, as_of=T0)

    def boom(ticker: str) -> ValuationInputs:
        raise ValuationUnavailableError(f"fcf_unavailable:{ticker}")

    service._valuation_loader = boom
    payload = service.ticker_payload(db, "NVDA", as_of=T0)
    assert payload["status"] == "valuation_unavailable"
    # Probabilities still render so the operator sees the events, just no
    # sensitivities.
    assert payload["events"]
    assert all(entry["sensitivity"] is None for entry in payload["events"])


def test_payload_warns_about_stale_events(db: Session) -> None:
    service = _service()
    service.ingest(db, as_of=T0)
    payload = service.ticker_payload(db, "NVDA", as_of=T0 + timedelta(days=30))
    assert any(w["warning"] == "stale_event" for w in payload["warnings"])


_EVENT_PROB_ENV_KEYS = (
    "EVENT_PROB_ENABLED",
    "EVENT_PROB_MODE",
    "EVENT_PROB_PROVIDER",
    "EVENT_PROB_TIMEOUT_MS",
    "EVENT_PROB_CACHE_TTL_SEC",
    "EVENT_PROB_MAX_EVENT_AGE_HOURS",
    "EVENT_PROB_MIN_LIQUIDITY",
    "EVENT_PROB_MAX_SPREAD",
    "EVENT_PROB_MAX_MARKET_WEIGHT",
    "EVENT_PROB_EMS_LOOKBACK_DAYS",
    "EVENT_PROB_HISTORICAL_SNAPSHOT_PATH",
)


def _clear_event_prob_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in _EVENT_PROB_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


def test_event_prob_config_binds_documented_defaults(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from core.event_probability.settings import build_event_probability_config

    _clear_event_prob_env(monkeypatch)
    cfg = build_event_probability_config(tmp_path)
    assert cfg.enabled is False
    assert cfg.mode == "off"
    assert cfg.provider == "stub"
    assert cfg.timeout_ms == 1500
    assert cfg.cache_ttl_sec == 300
    assert cfg.max_event_age_hours == 72.0
    assert cfg.min_liquidity == 1000.0
    assert cfg.max_spread == 0.08
    assert cfg.max_market_weight == 0.85
    assert cfg.lookback_days == (1, 7, 30)
    assert cfg.ems_lookback_days == 7
    assert cfg.historical_snapshot_path == ""


def test_event_prob_config_reads_env_overrides(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from core.event_probability.settings import build_event_probability_config

    _clear_event_prob_env(monkeypatch)
    monkeypatch.setenv("EVENT_PROB_ENABLED", "true")
    monkeypatch.setenv("EVENT_PROB_MODE", "LIVE")
    monkeypatch.setenv("EVENT_PROB_PROVIDER", "historical_file")
    monkeypatch.setenv("EVENT_PROB_EMS_LOOKBACK_DAYS", "14")
    monkeypatch.setenv("EVENT_PROB_HISTORICAL_SNAPSHOT_PATH", " research/snap.json ")
    monkeypatch.setenv("EVENT_PROB_MAX_MARKET_WEIGHT", "2")
    cfg = build_event_probability_config(tmp_path)
    assert cfg.enabled is True
    assert cfg.mode == "live"
    assert cfg.provider == "historical_file"
    assert cfg.ems_lookback_days == 14
    assert cfg.historical_snapshot_path == "research/snap.json"
    assert cfg.max_market_weight == 1.0


def test_event_prob_config_rejects_unknown_mode_and_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from core.event_probability.settings import build_event_probability_config

    _clear_event_prob_env(monkeypatch)
    monkeypatch.setenv("EVENT_PROB_MODE", "paper")
    monkeypatch.setenv("EVENT_PROB_PROVIDER", "kalshi")
    cfg = build_event_probability_config(tmp_path)
    assert cfg.mode == "off"
    assert cfg.provider == "stub"
