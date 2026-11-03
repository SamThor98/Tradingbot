from __future__ import annotations

from datetime import date, datetime

from core.weekend_backtest_campaign import (
    BASE_OVERRIDES,
    CHICAGO,
    CONFIRMATION_REFILL_WAVE,
    CORE_HORIZON_UNIVERSES,
    HORIZON_REFILL_WAVE,
    HORIZON_REFILL_WAVE_2,
    HORIZON_REFILL_WAVE_3,
    HORIZON_STRESS_WAVE,
    campaign_deadline,
    campaign_plan,
    runnable_strategy_ids,
    select_remaining_campaign_jobs,
    spec_fingerprint,
    spec_timeframe_for_strategy,
    window_start_date,
)
from webapp.backtest_spec import parse_strategy_spec


def test_deadline_is_sunday_night_chicago() -> None:
    friday = datetime(2026, 9, 11, 14, 50, tzinfo=CHICAGO)
    until = campaign_deadline(friday)
    assert until.tzinfo is not None
    assert until.astimezone(CHICAGO).weekday() == 6
    assert until.hour == 23
    assert until.minute == 59
    sunday = datetime(2026, 9, 13, 10, 0, tzinfo=CHICAGO)
    assert campaign_deadline(sunday).date() == date(2026, 9, 13)


def test_window_start_handles_leap_day() -> None:
    assert window_start_date(date(2024, 2, 29), 1) == date(2023, 2, 28)


def test_intraday_sleeves_map_to_daily_spec_timeframe() -> None:
    assert spec_timeframe_for_strategy("opening_range_breakout") == "daily"
    assert spec_timeframe_for_strategy("weekly_swing") == "weekly"


def test_campaign_plan_parses_and_skips_llm() -> None:
    plan = campaign_plan(end_date=date(2026, 9, 11))
    assert len(plan) >= 100
    assert any(row["wave"] >= 10 and row["spec"]["start_date"].startswith("2018-") for row in plan)
    assert any(row["wave"] >= 33 for row in plan)
    assert any(row["wave"] >= 37 for row in plan)
    assert any(row["wave"] >= 41 for row in plan)
    assert any(row["wave"] >= 45 for row in plan)
    assert any(row["wave"] >= 49 for row in plan)
    assert any(row["wave"] >= 53 for row in plan)
    assert any(row["wave"] >= HORIZON_STRESS_WAVE for row in plan)
    assert any(row["wave"] >= CONFIRMATION_REFILL_WAVE for row in plan)
    assert any(row["wave"] >= HORIZON_REFILL_WAVE for row in plan)
    assert any(row["wave"] >= HORIZON_REFILL_WAVE_2 for row in plan)
    fingerprints = [row["fingerprint"] for row in plan]
    assert len(fingerprints) == len(set(fingerprints))
    assert all(row["theory_name"].startswith("wknd ") for row in plan)
    universes = {row["spec"]["universe_preset"] for row in plan}
    assert "russell2000" not in universes
    assert "custom" not in universes
    assert "nasdaq100" in universes
    assert "sector_etfs" in universes
    early = [row for row in plan if row["wave"] <= 2]
    assert all(row["spec"]["universe_preset"] != "sp1500" for row in early)
    assert any(row["spec"]["universe_preset"] == "sp1500" for row in plan if row["wave"] >= 8)

    sleeves = set(runnable_strategy_ids())
    seen_ids: set[str] = set()
    for row in plan:
        spec = row["spec"]
        parsed = parse_strategy_spec(spec)
        assert parsed.theory_name == spec["theory_name"]
        ov = spec.get("overrides") or {}
        assert ov.get("skip_mirofish") is True
        assert ov.get("forensic_enabled") is False
        assert set(BASE_OVERRIDES).issubset(ov)
        seen_ids.update(parsed.strategy_ids)
        other = dict(spec)
        other["overrides"] = {**ov, "skip_mirofish": False}
        assert spec_fingerprint(other) == spec_fingerprint(spec)
    assert sleeves <= seen_ids


def test_horizon_stress_wave_covers_8y_sp1500() -> None:
    plan = campaign_plan(end_date=date(2026, 9, 11))
    assert plan[0]["wave"] == HORIZON_STRESS_WAVE
    assert plan[0]["window_years"] == 5
    assert plan[0]["spec"]["universe_preset"] == "nasdaq100"
    assert plan[0]["spec"]["start_date"] == "2021-09-11"

    stress = [row for row in plan if row["wave"] == HORIZON_STRESS_WAVE]
    assert stress
    assert all(row["window_years"] in {5, 8} for row in stress)
    assert all(row["spec"]["universe_preset"] in CORE_HORIZON_UNIVERSES for row in stress)
    eight_sp1500 = [
        row
        for row in stress
        if row["spec"]["universe_preset"] == "sp1500" and row["window_years"] == 8
    ]
    assert any(row["spec"]["strategy_ids"] == ["weekly_swing"] for row in eight_sp1500)
    assert any(row["spec"]["start_date"] == "2018-09-11" for row in eight_sp1500)

    vcp_stress = [row for row in stress if row["spec"]["strategy_ids"] == ["weekly_vcp"]]
    assert {row["spec"]["universe_preset"] for row in vcp_stress} == {"nasdaq100", "sp500", "sp1500"}
    pull_stress = [row for row in stress if row["spec"]["strategy_ids"] == ["monthly_pullback"]]
    assert {row["spec"]["universe_preset"] for row in pull_stress} == {"sp500", "sp1500"}
    swing_stress = [row for row in stress if row["spec"]["strategy_ids"] == ["weekly_swing"]]
    assert {row["spec"]["universe_preset"] for row in swing_stress} == {"sp1500"}

    assert plan[0]["spec"]["strategy_ids"] == ["weekly_vcp"]
    assert plan[0]["spec"]["universe_preset"] == "nasdaq100"

    refill = [row for row in plan if row["wave"] == CONFIRMATION_REFILL_WAVE]
    assert refill
    assert all(int(row["window_years"] or 0) in {5, 8} for row in refill)
    assert all(row["spec"]["universe_preset"] in {"sp500", "sp1500"} for row in refill)
    assert any("weekly_vcp" in row["spec"]["strategy_ids"] for row in refill)

    later = [row for row in plan if row["wave"] == HORIZON_REFILL_WAVE]
    assert later
    assert all(int(row["window_years"] or 0) in {5, 8} for row in later)
    assert all(row["spec"]["universe_preset"] in CORE_HORIZON_UNIVERSES for row in later)
    assert any(row["spec"]["strategy_ids"] == ["weekly_vcp"] for row in later)

    later2 = [row for row in plan if row["wave"] == HORIZON_REFILL_WAVE_2]
    assert later2
    assert all(int(row["window_years"] or 0) in {5, 8} for row in later2)
    assert all(row["spec"]["universe_preset"] in CORE_HORIZON_UNIVERSES for row in later2)
    assert any(row["spec"]["strategy_ids"] == ["weekly_vcp"] for row in later2)

    later3 = [row for row in plan if row["wave"] == HORIZON_REFILL_WAVE_3]
    assert later3
    assert all(int(row["window_years"] or 0) in {5, 8} for row in later3)
    assert all(row["spec"]["universe_preset"] in CORE_HORIZON_UNIVERSES for row in later3)
    assert any(row["spec"]["strategy_ids"] == ["weekly_vcp"] for row in later3)

    remaining_empty_known = select_remaining_campaign_jobs(plan, set(), horizon_only=True)
    assert remaining_empty_known[0]["spec"]["strategy_ids"] == ["weekly_vcp"]
    assert all(int(row["window_years"] or 0) >= 5 for row in remaining_empty_known)
    assert all(row["spec"]["universe_preset"] in CORE_HORIZON_UNIVERSES for row in remaining_empty_known)
    assert any(row["spec"]["strategy_ids"] == ["weekly_vcp"] for row in remaining_empty_known)

    known = {
        row["fingerprint"]
        for row in plan
        if not (
            row["spec"]["universe_preset"] == "sp1500" and int(row["window_years"] or 0) == 8
        )
    }
    remaining = select_remaining_campaign_jobs(plan, known, horizon_only=True)
    assert remaining
    assert all(row["spec"]["universe_preset"] == "sp1500" for row in remaining)
    assert all(int(row["window_years"] or 0) == 8 for row in remaining)

    two_year = next(
        row
        for row in plan
        if int(row["window_years"] or 0) == 2 and row["spec"]["universe_preset"] == "nasdaq100"
    )
    remaining_only = select_remaining_campaign_jobs(
        plan, {two_year["fingerprint"]}, horizon_only=True
    )
    assert remaining_only
    assert all(int(row["window_years"] or 0) >= 5 for row in remaining_only)
    assert two_year["fingerprint"] not in {row["fingerprint"] for row in remaining_only}
