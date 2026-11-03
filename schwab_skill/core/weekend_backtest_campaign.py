"""Deterministic weekend backtest campaign for the local 8182 queue.

Builds Scan-catalog specs (indexes × sleeves × scanner knobs). Does not place
orders. Full S&P 1500 jobs are last so a single-worker queue can finish many
faster named-universe replays first.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from core.scan_catalog import (
    STRATEGIES,
    get_strategy,
    known_timeframe_ids,
)

CHICAGO = ZoneInfo("America/Chicago")
THEORY_PREFIX = "wknd "
DEFAULT_DEADLINE_WEEKDAY = 6  # Sunday
DEFAULT_DEADLINE_HOUR = 23
DEFAULT_DEADLINE_MINUTE = 59

# Faster / smaller first. Full S&P 1500 is last on purpose.
PRIORITY_STRATEGY_IDS: tuple[str, ...] = (
    "trend_breakout",
    "pullback",
    "weekly_swing",
    "weekly_reversal",
    "donchian_20",
    "pead_primary",
    "weekly_breakout",
    "weekly_vcp",
    "st_reversal_5d",
    "monthly_position",
    "monthly_52w_high",
    "nr7_breakout",
    "breakout_confirm",
    "gap_and_go",
    "range_expansion",
    "opening_range_breakout",
    "overnight_gap_fade",
    "monthly_pullback",
    "momentum_12_1",
    "tsmom_12m",
)

SLOWER_UNIVERSE_SLEEVES: tuple[str, ...] = (
    "trend_breakout",
    "pullback",
    "weekly_swing",
    "weekly_reversal",
    "donchian_20",
    "pead_primary",
    "st_reversal_5d",
    "monthly_position",
)

# Unsupervised runs skip LLM/forensic so a hung vendor call cannot stall Sunday.
BASE_OVERRIDES: dict[str, Any] = {
    "skip_mirofish": True,
    "forensic_enabled": False,
}

_FINGERPRINT_SKIP_KEYS = frozenset({"skip_mirofish", "forensic_enabled"})

# Operator 2026-09-14: confirmation queue, not 2y ETF knob reprints.
# weekly_vcp held PF ≥ 1.7 on Nasdaq-100 / S&P 500 / S&P 1500 at 2y.
# monthly_pullback needs SP1500 + 5y/8y. weekly_swing on SP1500 is survivorship diagnostic.
HORIZON_STRESS_WAVE = 57
CONFIRMATION_REFILL_WAVE = 58
HORIZON_REFILL_WAVE = 59
HORIZON_REFILL_WAVE_2 = 60
HORIZON_REFILL_WAVE_3 = 61
MIN_HORIZON_YEARS = 5
CORE_HORIZON_UNIVERSES: tuple[str, ...] = ("nasdaq100", "sp500", "sp1500")
HORIZON_CONFIRMATION: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("weekly_vcp", ("nasdaq100", "sp500", "sp1500")),
    ("monthly_pullback", ("sp500", "sp1500")),
    ("weekly_swing", ("sp1500",)),  # survivorship diagnostic, not a candidate
)
HORIZON_STRESS_SLEEVES: tuple[str, ...] = tuple(sid for sid, _unis in HORIZON_CONFIRMATION)
HORIZON_STRESS_PAIRS: tuple[tuple[str, tuple[str, ...]], ...] = ()


@dataclass(frozen=True)
class CampaignJob:
    wave: int
    theory_name: str
    universe_preset: str
    scan_timeframe: str
    strategy_ids: tuple[str, ...]
    window_years: int
    extra_overrides: tuple[tuple[str, Any], ...] = ()


def campaign_deadline(now: datetime | None = None) -> datetime:
    """Sunday 23:59 America/Chicago of the current weekend (or today if Sunday)."""
    current = now.astimezone(CHICAGO) if now is not None else datetime.now(CHICAGO)
    days_ahead = (DEFAULT_DEADLINE_WEEKDAY - current.weekday()) % 7
    target = (current + timedelta(days=days_ahead)).replace(
        hour=DEFAULT_DEADLINE_HOUR,
        minute=DEFAULT_DEADLINE_MINUTE,
        second=59,
        microsecond=0,
    )
    return target


def campaign_end_date(now: datetime | None = None) -> date:
    current = now.astimezone(CHICAGO) if now is not None else datetime.now(CHICAGO)
    return current.date()


def window_start_date(end: date, years: int) -> date:
    years = max(1, int(years))
    try:
        return end.replace(year=end.year - years)
    except ValueError:
        return end.replace(year=end.year - years, day=28)


def spec_timeframe_for_strategy(strategy_id: str) -> str:
    row = get_strategy(strategy_id) or {}
    tf = str(row.get("timeframe") or "daily").strip().lower()
    if tf in known_timeframe_ids():
        return tf
    return "daily"


def runnable_strategy_ids() -> list[str]:
    known = {str(s["id"]) for s in STRATEGIES if s.get("runnable")}
    ordered = [sid for sid in PRIORITY_STRATEGY_IDS if sid in known]
    extra = sorted(sid for sid in known if sid not in set(ordered))
    return ordered + extra


def _theory_name(
    wave: int,
    universe: str,
    strategy_ids: tuple[str, ...],
    years: int,
    extra: dict[str, Any] | None = None,
) -> str:
    sleeves = "+".join(strategy_ids)
    bits = [f"wknd w{wave}", universe, sleeves, f"{years}y"]
    if extra:
        for key, val in extra.items():
            bits.append(f"{key}={val}")
    name = " ".join(bits)
    return name[:200]


def _job(
    *,
    wave: int,
    universe: str,
    strategy_ids: tuple[str, ...] | list[str],
    years: int,
    extra: dict[str, Any] | None = None,
    timeframe: str | None = None,
) -> CampaignJob:
    ids = tuple(str(s).strip().lower() for s in strategy_ids if str(s).strip())
    tf = timeframe or spec_timeframe_for_strategy(ids[0])
    extra = dict(extra or {})
    return CampaignJob(
        wave=wave,
        theory_name=_theory_name(wave, universe, ids, years, extra),
        universe_preset=universe,
        scan_timeframe=tf,
        strategy_ids=ids,
        window_years=years,
        extra_overrides=tuple(sorted(extra.items(), key=lambda kv: kv[0])),
    )


def _horizon_stress_jobs() -> list[CampaignJob]:
    """5y then 8y confirmation queue: weekly_vcp, monthly_pullback, weekly_swing/SP1500."""
    runnable = set(runnable_strategy_ids())
    jobs: list[CampaignJob] = []
    for years in (5, 8):
        for sid, universes in HORIZON_CONFIRMATION:
            if sid not in runnable:
                continue
            for universe in universes:
                jobs.append(_job(wave=HORIZON_STRESS_WAVE, universe=universe, strategy_ids=(sid,), years=years))
    jobs.extend(_confirmation_refill_jobs())
    jobs.extend(
        _horizon_refill_jobs(
            wave=HORIZON_REFILL_WAVE,
            extras=(
                {"quality_min_signal_score": 47},
                {"vcp_days": 23},
                {"stage2_52w_pct": 0.655},
            ),
        )
    )
    jobs.extend(
        _horizon_refill_jobs(
            wave=HORIZON_REFILL_WAVE_2,
            extras=(
                {"quality_min_signal_score": 61},
                {"vcp_days": 24},
                {"stage2_sma_upward_days": 44},
            ),
        )
    )
    # Sixth tranche — horizon-only remaining hit 0 on 2026-09-17.
    # New knob values so fingerprints are not already in campaign state.
    jobs.extend(
        _horizon_refill_jobs(
            wave=HORIZON_REFILL_WAVE_3,
            extras=(
                {"quality_min_signal_score": 53},
                {"vcp_days": 21},
                {"stage2_sma_upward_days": 36},
            ),
        )
    )
    return jobs


def _horizon_refill_jobs(
    *,
    wave: int,
    extras: tuple[dict[str, Any], ...],
) -> list[CampaignJob]:
    """Unique 5y/8y confirmation fingerprints on core indexes."""
    runnable = set(runnable_strategy_ids())
    jobs: list[CampaignJob] = []
    for extra in extras:
        for years in (5, 8):
            for sid, universes in HORIZON_CONFIRMATION:
                if sid not in runnable:
                    continue
                for universe in universes:
                    jobs.append(
                        _job(
                            wave=wave,
                            universe=universe,
                            strategy_ids=(sid,),
                            years=years,
                            extra=extra,
                        )
                    )
        for universe in ("sp500", "sp1500"):
            jobs.append(
                _job(
                    wave=wave,
                    universe=universe,
                    strategy_ids=("weekly_vcp", "weekly_breakout"),
                    years=8,
                    timeframe="weekly",
                    extra=extra,
                )
            )
    return jobs


def _confirmation_refill_jobs() -> list[CampaignJob]:
    """Unique 5y/8y catalog dual-admit follow-ups on S&P 500 / S&P 1500."""
    runnable = set(runnable_strategy_ids())
    pairs: tuple[tuple[str, tuple[str, ...]], ...] = (
        ("weekly", ("weekly_vcp", "weekly_breakout")),
        ("monthly", ("monthly_pullback", "monthly_position")),
    )
    jobs: list[CampaignJob] = []
    for years in (5, 8):
        for universe in ("sp500", "sp1500"):
            for timeframe, ids in pairs:
                if any(sid not in runnable for sid in ids):
                    continue
                jobs.append(
                    _job(
                        wave=CONFIRMATION_REFILL_WAVE,
                        universe=universe,
                        strategy_ids=ids,
                        years=years,
                        timeframe=timeframe,
                    )
                )
    return jobs


def is_core_horizon_row(row: dict[str, Any]) -> bool:
    spec = row.get("spec") if isinstance(row.get("spec"), dict) else {}
    years = int(row.get("window_years") or 0)
    uni = str(spec.get("universe_preset") or "")
    return years >= MIN_HORIZON_YEARS and uni in CORE_HORIZON_UNIVERSES


def fingerprints_from_runs(
    runs: list[dict[str, Any]],
    *,
    plan_fingerprints: set[str] | None = None,
) -> set[str]:
    """Fingerprints already on the dashboard for this plan, including hypothesis cards."""
    found: set[str] = set()
    wanted = plan_fingerprints or set()
    for row in runs:
        spec = row.get("spec") if isinstance(row.get("spec"), dict) else None
        if not isinstance(spec, dict) or not (spec.get("theory_name") or spec.get("start_date")):
            continue
        fingerprint = spec_fingerprint(spec)
        theory = str(spec.get("theory_name") or "")
        if theory.startswith(THEORY_PREFIX) or fingerprint in wanted:
            found.add(fingerprint)
    return found


def select_remaining_campaign_jobs(
    plan: list[dict[str, Any]],
    known: set[str],
    *,
    horizon_only: bool = False,
) -> list[dict[str, Any]]:
    """Skip submitted fingerprints. Ready hypothesis cards stay ahead of the catalog."""
    remaining = [row for row in plan if row.get("fingerprint") not in known]
    cards = [row for row in remaining if row.get("source") == "hypothesis_card"]
    catalog = [row for row in remaining if row.get("source") != "hypothesis_card"]
    preferred = [row for row in catalog if is_core_horizon_row(row)]
    if horizon_only:
        return cards + preferred
    rest = [row for row in catalog if not is_core_horizon_row(row)]
    return cards + preferred + rest


def build_campaign_jobs() -> list[CampaignJob]:
    """Catalog sweep with 5y/8y core-index stress first, then 2y refill."""
    jobs: list[CampaignJob] = []
    jobs.extend(_horizon_stress_jobs())
    sleeves = runnable_strategy_ids()

    for sid in sleeves:
        jobs.append(_job(wave=1, universe="sector_etfs", strategy_ids=(sid,), years=2))

    for sid in sleeves:
        jobs.append(_job(wave=2, universe="nasdaq100", strategy_ids=(sid,), years=2))

    jobs.append(
        _job(
            wave=2,
            universe="nasdaq100",
            strategy_ids=("weekly_swing", "weekly_reversal"),
            years=2,
            timeframe="weekly",
            extra={"sma_days": 18},
        )
    )

    tunable_rows: list[dict[str, Any]] = [
        {"stage2_52w_pct": 0.80},
        {"stage2_52w_pct": 0.90},
        {"stage2_sma_upward_days": 15},
        {"stage2_sma_upward_days": 18},
        {"stage2_sma_upward_days": 25},
        {"vcp_days": 3},
        {"vcp_days": 6},
        {"scan_vcp_gate_mode": "hard"},
        {"quality_min_signal_score": 40},
        {"quality_min_signal_score": 60},
    ]
    for extra in tunable_rows:
        jobs.append(
            _job(
                wave=3,
                universe="nasdaq100",
                strategy_ids=("trend_breakout",),
                years=2,
                extra=extra,
            )
        )
    jobs.append(
        _job(
            wave=3,
            universe="nasdaq100",
            strategy_ids=("weekly_swing",),
            years=2,
            extra={"stage2_52w_pct": 0.90},
        )
    )
    jobs.append(
        _job(
            wave=3,
            universe="nasdaq100",
            strategy_ids=("weekly_swing",),
            years=2,
            extra={"stage2_sma_upward_days": 18},
        )
    )

    for sid in SLOWER_UNIVERSE_SLEEVES:
        jobs.append(_job(wave=4, universe="focused", strategy_ids=(sid,), years=2))

    for sid in ("trend_breakout", "weekly_swing", "weekly_reversal", "pullback"):
        jobs.append(_job(wave=5, universe="nasdaq100", strategy_ids=(sid,), years=5))

    for sid in ("trend_breakout", "weekly_swing"):
        jobs.append(_job(wave=6, universe="sp500", strategy_ids=(sid,), years=2))

    jobs.append(_job(wave=7, universe="focused", strategy_ids=("trend_breakout",), years=5))

    jobs.append(
        _job(
            wave=8,
            universe="sp1500",
            strategy_ids=("weekly_swing", "weekly_reversal"),
            years=2,
            timeframe="weekly",
            extra={"sma_days": 18},
        )
    )
    jobs.append(
        _job(
            wave=9,
            universe="sp1500",
            strategy_ids=("weekly_swing", "weekly_reversal"),
            years=5,
            timeframe="weekly",
            extra={"sma_days": 18},
        )
    )
    jobs.append(_job(wave=9, universe="sp1500", strategy_ids=("trend_breakout",), years=2))

    # Second tranche — first plan drained Friday evening; keep the queue
    # busy through Sunday with new windows, knobs, and dual-admit pairs.
    for years in (5, 8):
        for sid in sleeves:
            jobs.append(_job(wave=10, universe="sector_etfs", strategy_ids=(sid,), years=years))

    for sid in (
        "trend_breakout",
        "pullback",
        "weekly_swing",
        "weekly_reversal",
        "donchian_20",
        "pead_primary",
        "weekly_breakout",
        "st_reversal_5d",
        "monthly_position",
    ):
        jobs.append(_job(wave=11, universe="nasdaq100", strategy_ids=(sid,), years=8))

    for sid in SLOWER_UNIVERSE_SLEEVES:
        if sid in {"trend_breakout", "weekly_swing", "weekly_reversal", "pullback"}:
            continue
        jobs.append(_job(wave=11, universe="nasdaq100", strategy_ids=(sid,), years=5))

    more_knobs: list[tuple[tuple[str, ...], dict[str, Any]]] = [
        (("trend_breakout",), {"stage2_52w_pct": 0.75}),
        (("trend_breakout",), {"stage2_52w_pct": 0.88}),
        (("trend_breakout",), {"stage2_sma_upward_days": 10}),
        (("trend_breakout",), {"stage2_sma_upward_days": 30}),
        (("trend_breakout",), {"vcp_days": 5}),
        (("trend_breakout",), {"scan_vcp_gate_mode": "shadow"}),
        (("trend_breakout",), {"quality_min_signal_score": 70}),
        (("trend_breakout",), {"quality_breakout_volume_min_ratio": 0.75}),
        (("trend_breakout",), {"quality_breakout_volume_min_ratio": 1.10}),
        (("trend_breakout",), {"quality_require_breakout_volume": True}),
        (("trend_breakout",), {"stage2_52w_pct": 0.90, "stage2_sma_upward_days": 18}),
        (("trend_breakout",), {"stage2_52w_pct": 0.80, "vcp_days": 3}),
        (("weekly_swing",), {"stage2_52w_pct": 0.80}),
        (("weekly_swing",), {"vcp_days": 3}),
        (("weekly_swing",), {"quality_min_signal_score": 40}),
        (("pullback",), {"stage2_52w_pct": 0.90}),
        (("donchian_20",), {"quality_min_signal_score": 40}),
        (("weekly_reversal",), {"stage2_sma_upward_days": 18}),
    ]
    for ids, extra in more_knobs:
        jobs.append(_job(wave=12, universe="nasdaq100", strategy_ids=ids, years=2, extra=extra))

    dual_pairs: list[tuple[str, tuple[str, ...]]] = [
        ("daily", ("trend_breakout", "donchian_20")),
        ("daily", ("trend_breakout", "pullback")),
        ("daily", ("trend_breakout", "nr7_breakout")),
        ("weekly", ("weekly_swing", "weekly_breakout")),
        ("weekly", ("weekly_swing", "weekly_vcp")),
        ("monthly", ("monthly_position", "monthly_52w_high")),
    ]
    for tf, ids in dual_pairs:
        jobs.append(_job(wave=13, universe="nasdaq100", strategy_ids=ids, years=2, timeframe=tf))

    for sid in ("weekly_swing", "pullback", "weekly_reversal", "donchian_20"):
        jobs.append(_job(wave=14, universe="focused", strategy_ids=(sid,), years=5))
    for sid in ("trend_breakout", "weekly_swing"):
        jobs.append(_job(wave=14, universe="sp500", strategy_ids=(sid,), years=5))
    for sid in SLOWER_UNIVERSE_SLEEVES:
        if sid in {"trend_breakout", "weekly_swing"}:
            continue
        jobs.append(_job(wave=14, universe="sp500", strategy_ids=(sid,), years=2))

    jobs.append(_job(wave=15, universe="sp1500", strategy_ids=("trend_breakout",), years=5))
    jobs.append(
        _job(
            wave=15,
            universe="sp1500",
            strategy_ids=("trend_breakout", "weekly_swing"),
            years=2,
            timeframe="daily",
        )
    )

    # Third tranche — keep Sunday covered after the 162-job plan tails out.
    for sid in sleeves:
        if sid in {
            "trend_breakout",
            "pullback",
            "weekly_swing",
            "weekly_reversal",
            "donchian_20",
            "pead_primary",
            "weekly_breakout",
            "st_reversal_5d",
            "monthly_position",
        }:
            continue
        jobs.append(_job(wave=16, universe="nasdaq100", strategy_ids=(sid,), years=8))

    for sid in ("trend_breakout", "weekly_swing", "pullback", "weekly_reversal"):
        jobs.append(_job(wave=16, universe="focused", strategy_ids=(sid,), years=8))
    for sid in ("trend_breakout", "weekly_swing"):
        jobs.append(_job(wave=16, universe="sp500", strategy_ids=(sid,), years=8))

    third_knobs: list[tuple[tuple[str, ...], dict[str, Any]]] = [
        (("trend_breakout",), {"quality_gates_mode": "soft"}),
        (("trend_breakout",), {"quality_gates_mode": "hard"}),
        (("trend_breakout",), {"pead_enabled": True}),
        (("trend_breakout",), {"breakout_confirm_enabled": True}),
        (("trend_breakout",), {"stage2_52w_pct": 0.82, "stage2_sma_upward_days": 15}),
        (("trend_breakout",), {"vcp_days": 4, "quality_min_signal_score": 55}),
        (("weekly_swing",), {"stage2_52w_pct": 0.85, "stage2_sma_upward_days": 15}),
        (("weekly_swing",), {"scan_vcp_gate_mode": "hard"}),
        (("pullback",), {"quality_min_signal_score": 40}),
        (("donchian_20",), {"stage2_52w_pct": 0.90}),
        (("pead_primary",), {"quality_min_signal_score": 40}),
        (("weekly_breakout",), {"stage2_sma_upward_days": 18}),
    ]
    for ids, extra in third_knobs:
        jobs.append(_job(wave=17, universe="nasdaq100", strategy_ids=ids, years=2, extra=extra))

    for tf, ids in (
        ("daily", ("trend_breakout", "pead_primary")),
        ("daily", ("pullback", "donchian_20")),
        ("weekly", ("weekly_breakout", "weekly_vcp")),
        ("weekly", ("weekly_swing", "weekly_reversal")),
        ("monthly", ("monthly_52w_high", "monthly_pullback")),
    ):
        jobs.append(_job(wave=17, universe="nasdaq100", strategy_ids=ids, years=5, timeframe=tf))

    for sid in ("pullback", "weekly_swing", "donchian_20", "pead_primary"):
        jobs.append(_job(wave=18, universe="sp1500", strategy_ids=(sid,), years=2))
    jobs.append(
        _job(
            wave=18,
            universe="sp1500",
            strategy_ids=("weekly_swing",),
            years=5,
            extra={"stage2_52w_pct": 0.90},
        )
    )

    for extra in (
        {"stage2_52w_pct": 0.80},
        {"quality_min_signal_score": 40},
        {"stage2_sma_upward_days": 18},
    ):
        for sid in (
            "trend_breakout",
            "weekly_swing",
            "pullback",
            "donchian_20",
            "weekly_reversal",
            "pead_primary",
        ):
            jobs.append(_job(wave=19, universe="sector_etfs", strategy_ids=(sid,), years=2, extra=extra))
    for sid in ("nr7_breakout", "weekly_vcp", "monthly_52w_high", "gap_and_go"):
        jobs.append(_job(wave=19, universe="focused", strategy_ids=(sid,), years=5))
        jobs.append(_job(wave=19, universe="sp500", strategy_ids=(sid,), years=5))

    # Fourth tranche — wave 19 is fast sector-ETF knobs; keep Sunday busy.
    for extra in (
        {"vcp_days": 3},
        {"stage2_52w_pct": 0.90},
        {"quality_min_signal_score": 60},
        {"scan_vcp_gate_mode": "hard"},
    ):
        for sid in (
            "trend_breakout",
            "weekly_swing",
            "pullback",
            "donchian_20",
            "weekly_reversal",
            "pead_primary",
        ):
            jobs.append(_job(wave=20, universe="sector_etfs", strategy_ids=(sid,), years=2, extra=extra))

    for sid in (
        "nr7_breakout",
        "weekly_vcp",
        "gap_and_go",
        "range_expansion",
        "monthly_52w_high",
        "monthly_pullback",
        "tsmom_12m",
        "overnight_gap_fade",
    ):
        jobs.append(_job(wave=21, universe="nasdaq100", strategy_ids=(sid,), years=5))

    for tf, ids in (
        ("daily", ("trend_breakout", "st_reversal_5d")),
        ("weekly", ("weekly_vcp", "weekly_reversal")),
        ("monthly", ("monthly_position", "tsmom_12m")),
        ("daily", ("donchian_20", "nr7_breakout")),
    ):
        jobs.append(_job(wave=21, universe="nasdaq100", strategy_ids=ids, years=5, timeframe=tf))

    for sid in ("nr7_breakout", "weekly_vcp", "monthly_52w_high", "st_reversal_5d"):
        jobs.append(_job(wave=22, universe="sp1500", strategy_ids=(sid,), years=2))
    for sid in ("trend_breakout", "weekly_swing", "pullback"):
        jobs.append(_job(wave=22, universe="focused", strategy_ids=(sid,), years=2, extra={"vcp_days": 3}))

    for extra in (
        {"vcp_days": 6},
        {"stage2_sma_upward_days": 25},
        {"quality_min_signal_score": 70},
    ):
        for sid in ("trend_breakout", "weekly_swing", "pullback", "donchian_20"):
            jobs.append(_job(wave=23, universe="sector_etfs", strategy_ids=(sid,), years=5, extra=extra))
    for sid in ("trend_breakout", "weekly_swing", "pullback", "donchian_20", "pead_primary"):
        jobs.append(
            _job(
                wave=23,
                universe="nasdaq100",
                strategy_ids=(sid,),
                years=5,
                extra={"stage2_52w_pct": 0.88},
            )
        )
    for sid in ("monthly_position", "monthly_52w_high", "momentum_12_1"):
        jobs.append(_job(wave=24, universe="sp500", strategy_ids=(sid,), years=5))
        jobs.append(_job(wave=24, universe="focused", strategy_ids=(sid,), years=8))
    for sid in runnable_strategy_ids():
        jobs.append(
            _job(
                wave=24,
                universe="sector_etfs",
                strategy_ids=(sid,),
                years=5,
                extra={"quality_min_signal_score": 55},
            )
        )

    for extra in (
        {"stage2_52w_pct": 0.78},
        {"vcp_days": 8},
        {"quality_breakout_volume_min_ratio": 0.85},
        {"quality_gates_mode": "off"},
    ):
        for sid in ("trend_breakout", "weekly_swing", "pullback", "donchian_20", "weekly_reversal"):
            jobs.append(_job(wave=25, universe="nasdaq100", strategy_ids=(sid,), years=2, extra=extra))
    for sid in runnable_strategy_ids():
        if sid in SLOWER_UNIVERSE_SLEEVES:
            continue
        jobs.append(_job(wave=25, universe="focused", strategy_ids=(sid,), years=5))
    for sid in ("trend_breakout", "weekly_swing", "pullback"):
        jobs.append(_job(wave=26, universe="sp500", strategy_ids=(sid,), years=2, extra={"stage2_52w_pct": 0.90}))
        jobs.append(
            _job(
                wave=26,
                universe="nasdaq100",
                strategy_ids=(sid,),
                years=8,
                extra={"quality_min_signal_score": 40},
            )
        )
    for extra in ({"stage2_52w_pct": 0.85}, {"vcp_days": 4}):
        for sid in ("trend_breakout", "weekly_swing", "pullback", "pead_primary"):
            jobs.append(_job(wave=26, universe="sector_etfs", strategy_ids=(sid,), years=8, extra=extra))

    for extra in (
        {"stage2_sma_upward_days": 12},
        {"quality_min_signal_score": 45},
        {"scan_vcp_gate_mode": "hard", "vcp_days": 3},
    ):
        for sid in ("trend_breakout", "weekly_swing", "pullback", "pead_primary", "donchian_20"):
            jobs.append(_job(wave=27, universe="nasdaq100", strategy_ids=(sid,), years=2, extra=extra))
    for sid in ("weekly_breakout", "weekly_vcp", "nr7_breakout", "gap_and_go", "range_expansion"):
        jobs.append(_job(wave=27, universe="sp1500", strategy_ids=(sid,), years=2))
        jobs.append(_job(wave=27, universe="focused", strategy_ids=(sid,), years=8))
    for sid in runnable_strategy_ids():
        jobs.append(
            _job(
                wave=28,
                universe="sector_etfs",
                strategy_ids=(sid,),
                years=8,
                extra={"stage2_sma_upward_days": 18},
            )
        )

    for extra in (
        {"stage2_52w_pct": 0.83, "vcp_days": 5},
        {"stage2_sma_upward_days": 22},
        {"quality_min_signal_score": 35},
    ):
        for sid in ("trend_breakout", "weekly_swing", "pullback", "donchian_20"):
            jobs.append(_job(wave=29, universe="nasdaq100", strategy_ids=(sid,), years=2, extra=extra))
    for sid in SLOWER_UNIVERSE_SLEEVES:
        if sid in {"trend_breakout", "weekly_swing"}:
            continue
        jobs.append(_job(wave=29, universe="sp500", strategy_ids=(sid,), years=8))
    for extra in ({"quality_gates_mode": "soft"}, {"pead_enabled": True}):
        for sid in ("trend_breakout", "weekly_swing"):
            jobs.append(_job(wave=30, universe="focused", strategy_ids=(sid,), years=5, extra=extra))
            jobs.append(_job(wave=30, universe="sp1500", strategy_ids=(sid,), years=2, extra=extra))
    for sid in (
        "trend_breakout",
        "weekly_swing",
        "pullback",
        "donchian_20",
        "pead_primary",
        "weekly_reversal",
    ):
        jobs.append(
            _job(
                wave=30,
                universe="sector_etfs",
                strategy_ids=(sid,),
                years=8,
                extra={"quality_min_signal_score": 40},
            )
        )

    for extra in (
        {"stage2_52w_pct": 0.87, "stage2_sma_upward_days": 16},
        {"vcp_days": 7, "quality_min_signal_score": 50},
        {"quality_require_breakout_volume": False},
    ):
        for sid in ("trend_breakout", "weekly_swing", "pullback"):
            jobs.append(_job(wave=31, universe="nasdaq100", strategy_ids=(sid,), years=2, extra=extra))
            jobs.append(_job(wave=31, universe="focused", strategy_ids=(sid,), years=2, extra=extra))
    for sid in (
        "monthly_position",
        "monthly_52w_high",
        "monthly_pullback",
        "momentum_12_1",
        "tsmom_12m",
    ):
        jobs.append(
            _job(
                wave=32,
                universe="nasdaq100",
                strategy_ids=(sid,),
                years=5,
                extra={"quality_min_signal_score": 40},
            )
        )
        jobs.append(_job(wave=32, universe="sp500", strategy_ids=(sid,), years=2, extra={"stage2_52w_pct": 0.90}))
    for sid in runnable_strategy_ids():
        jobs.append(_job(wave=32, universe="sector_etfs", strategy_ids=(sid,), years=8, extra={"vcp_days": 6}))

    # Fifth tranche — Sunday midday remaining ~20; unused knobs only so
    # fingerprints stay new and the one-worker queue lasts until 23:59 CT.
    for extra in (
        {"stage2_52w_pct": 0.72},
        {"stage2_52w_pct": 0.81},
        {"stage2_sma_upward_days": 14},
        {"quality_min_signal_score": 48},
    ):
        for sid in (
            "trend_breakout",
            "weekly_swing",
            "pullback",
            "donchian_20",
            "weekly_reversal",
            "pead_primary",
        ):
            jobs.append(_job(wave=33, universe="nasdaq100", strategy_ids=(sid,), years=2, extra=extra))

    for extra in (
        {"stage2_52w_pct": 0.76},
        {"vcp_days": 9},
        {"quality_min_signal_score": 32},
        {"quality_gates_mode": "shadow"},
    ):
        for sid in sleeves:
            jobs.append(_job(wave=34, universe="sector_etfs", strategy_ids=(sid,), years=2, extra=extra))

    for extra in (
        {"stage2_52w_pct": 0.84, "stage2_sma_upward_days": 20},
        {"vcp_days": 10, "quality_min_signal_score": 52},
        {"quality_breakout_volume_min_ratio": 1.00},
    ):
        for sid in ("trend_breakout", "weekly_swing", "pullback", "donchian_20"):
            jobs.append(_job(wave=35, universe="focused", strategy_ids=(sid,), years=2, extra=extra))
            jobs.append(_job(wave=35, universe="nasdaq100", strategy_ids=(sid,), years=5, extra=extra))

    for tf, ids in (
        ("daily", ("trend_breakout", "gap_and_go")),
        ("daily", ("pullback", "pead_primary")),
        ("daily", ("donchian_20", "st_reversal_5d")),
        ("daily", ("nr7_breakout", "range_expansion")),
        ("weekly", ("weekly_breakout", "weekly_reversal")),
        ("monthly", ("monthly_position", "monthly_pullback")),
    ):
        jobs.append(_job(wave=35, universe="nasdaq100", strategy_ids=ids, years=2, timeframe=tf))

    for sid in ("nr7_breakout", "weekly_vcp", "gap_and_go", "range_expansion", "st_reversal_5d"):
        jobs.append(_job(wave=36, universe="sp500", strategy_ids=(sid,), years=2, extra={"stage2_52w_pct": 0.86}))
        jobs.append(
            _job(
                wave=36,
                universe="focused",
                strategy_ids=(sid,),
                years=5,
                extra={"quality_min_signal_score": 48},
            )
        )
    for extra in (
        {"stage2_sma_upward_days": 28},
        {"quality_min_signal_score": 65},
        {"vcp_days": 2},
    ):
        for sid in ("trend_breakout", "weekly_swing", "pullback"):
            jobs.append(_job(wave=36, universe="nasdaq100", strategy_ids=(sid,), years=2, extra=extra))
            jobs.append(_job(wave=36, universe="sector_etfs", strategy_ids=(sid,), years=5, extra=extra))

    # Sixth tranche — Sunday evening remaining ~13; unused knobs only.
    for extra in (
        {"stage2_52w_pct": 0.73},
        {"stage2_52w_pct": 0.89},
        {"stage2_sma_upward_days": 17},
        {"quality_min_signal_score": 38},
    ):
        for sid in (
            "trend_breakout",
            "weekly_swing",
            "pullback",
            "donchian_20",
            "weekly_reversal",
            "pead_primary",
        ):
            jobs.append(_job(wave=37, universe="nasdaq100", strategy_ids=(sid,), years=2, extra=extra))

    for extra in (
        {"stage2_52w_pct": 0.77},
        {"vcp_days": 11},
        {"quality_min_signal_score": 58},
        {"quality_breakout_volume_min_ratio": 0.90},
    ):
        for sid in sleeves:
            jobs.append(_job(wave=38, universe="sector_etfs", strategy_ids=(sid,), years=2, extra=extra))

    for extra in (
        {"stage2_52w_pct": 0.79, "stage2_sma_upward_days": 21},
        {"vcp_days": 12, "quality_min_signal_score": 42},
        {"quality_require_breakout_volume": True, "quality_breakout_volume_min_ratio": 1.25},
    ):
        for sid in ("trend_breakout", "weekly_swing", "pullback", "donchian_20"):
            jobs.append(_job(wave=39, universe="focused", strategy_ids=(sid,), years=2, extra=extra))
            jobs.append(_job(wave=39, universe="nasdaq100", strategy_ids=(sid,), years=2, extra=extra))

    for tf, ids in (
        ("daily", ("trend_breakout", "range_expansion")),
        ("daily", ("pullback", "nr7_breakout")),
        ("daily", ("pead_primary", "st_reversal_5d")),
        ("weekly", ("weekly_vcp", "weekly_breakout")),
        ("monthly", ("monthly_52w_high", "momentum_12_1")),
        ("daily", ("gap_and_go", "donchian_20")),
    ):
        jobs.append(_job(wave=39, universe="nasdaq100", strategy_ids=ids, years=2, timeframe=tf))

    for extra in (
        {"stage2_sma_upward_days": 11},
        {"quality_min_signal_score": 75},
        {"vcp_days": 14},
    ):
        for sid in ("trend_breakout", "weekly_swing", "pullback", "pead_primary"):
            jobs.append(_job(wave=40, universe="sector_etfs", strategy_ids=(sid,), years=5, extra=extra))
            jobs.append(_job(wave=40, universe="nasdaq100", strategy_ids=(sid,), years=5, extra=extra))

    # Continuous refill — operator asked for no Sunday cutoff (2026-09-13).
    for extra in (
        {"stage2_52w_pct": 0.71},
        {"stage2_sma_upward_days": 19},
        {"quality_min_signal_score": 44},
        {"vcp_days": 13},
    ):
        for sid in (
            "trend_breakout",
            "weekly_swing",
            "pullback",
            "donchian_20",
            "weekly_reversal",
            "pead_primary",
        ):
            jobs.append(_job(wave=41, universe="nasdaq100", strategy_ids=(sid,), years=2, extra=extra))

    for extra in (
        {"stage2_52w_pct": 0.74},
        {"quality_min_signal_score": 36},
        {"quality_breakout_volume_min_ratio": 0.95},
        {"stage2_sma_upward_days": 23},
    ):
        for sid in sleeves:
            jobs.append(_job(wave=42, universe="sector_etfs", strategy_ids=(sid,), years=2, extra=extra))

    for extra in (
        {"stage2_52w_pct": 0.91, "stage2_sma_upward_days": 13},
        {"vcp_days": 15, "quality_min_signal_score": 62},
        {"quality_gates_mode": "soft", "quality_min_signal_score": 48},
    ):
        for sid in ("trend_breakout", "weekly_swing", "pullback", "donchian_20"):
            jobs.append(_job(wave=43, universe="focused", strategy_ids=(sid,), years=2, extra=extra))
            jobs.append(_job(wave=43, universe="nasdaq100", strategy_ids=(sid,), years=2, extra=extra))

    for sid in ("nr7_breakout", "weekly_vcp", "gap_and_go", "st_reversal_5d", "range_expansion"):
        jobs.append(_job(wave=44, universe="nasdaq100", strategy_ids=(sid,), years=2, extra={"stage2_52w_pct": 0.68}))
        jobs.append(
            _job(wave=44, universe="focused", strategy_ids=(sid,), years=5, extra={"quality_min_signal_score": 68})
        )
    for extra in ({"stage2_sma_upward_days": 32}, {"vcp_days": 16}, {"quality_min_signal_score": 80}):
        for sid in ("trend_breakout", "weekly_swing", "pullback"):
            jobs.append(_job(wave=44, universe="sector_etfs", strategy_ids=(sid,), years=5, extra=extra))
            jobs.append(_job(wave=44, universe="nasdaq100", strategy_ids=(sid,), years=5, extra=extra))

    for extra in (
        {"stage2_52w_pct": 0.69},
        {"stage2_sma_upward_days": 9},
        {"quality_min_signal_score": 41},
        {"vcp_days": 17},
    ):
        for sid in (
            "trend_breakout",
            "weekly_swing",
            "pullback",
            "donchian_20",
            "weekly_reversal",
            "pead_primary",
        ):
            jobs.append(_job(wave=45, universe="nasdaq100", strategy_ids=(sid,), years=2, extra=extra))

    for extra in (
        {"stage2_52w_pct": 0.92},
        {"quality_min_signal_score": 33},
        {"quality_breakout_volume_min_ratio": 1.15},
        {"stage2_sma_upward_days": 27},
    ):
        for sid in sleeves:
            jobs.append(_job(wave=46, universe="sector_etfs", strategy_ids=(sid,), years=2, extra=extra))

    for extra in (
        {"stage2_52w_pct": 0.94, "vcp_days": 18},
        {"quality_min_signal_score": 72, "stage2_sma_upward_days": 36},
        {"quality_require_breakout_volume": False, "quality_breakout_volume_min_ratio": 1.35},
    ):
        for sid in ("trend_breakout", "weekly_swing", "pullback", "donchian_20"):
            jobs.append(_job(wave=47, universe="focused", strategy_ids=(sid,), years=2, extra=extra))
            jobs.append(_job(wave=47, universe="nasdaq100", strategy_ids=(sid,), years=2, extra=extra))

    for tf, ids in (
        ("daily", ("trend_breakout", "overnight_gap_fade")),
        ("daily", ("pullback", "gap_and_go")),
        ("weekly", ("weekly_swing", "weekly_vcp")),
        ("monthly", ("monthly_position", "momentum_12_1")),
    ):
        jobs.append(_job(wave=47, universe="nasdaq100", strategy_ids=ids, years=2, timeframe=tf))

    for sid in ("monthly_position", "monthly_52w_high", "tsmom_12m", "momentum_12_1"):
        jobs.append(
            _job(wave=48, universe="sp500", strategy_ids=(sid,), years=2, extra={"quality_min_signal_score": 41})
        )
    for extra in ({"vcp_days": 1}, {"stage2_sma_upward_days": 33}):
        for sid in ("trend_breakout", "weekly_swing", "pullback"):
            jobs.append(_job(wave=48, universe="sector_etfs", strategy_ids=(sid,), years=8, extra=extra))
            jobs.append(_job(wave=48, universe="nasdaq100", strategy_ids=(sid,), years=5, extra=extra))

    for extra in (
        {"stage2_52w_pct": 0.67},
        {"stage2_sma_upward_days": 7},
        {"quality_min_signal_score": 39},
        {"vcp_days": 19},
    ):
        for sid in (
            "trend_breakout",
            "weekly_swing",
            "pullback",
            "donchian_20",
            "weekly_reversal",
            "pead_primary",
        ):
            jobs.append(_job(wave=49, universe="nasdaq100", strategy_ids=(sid,), years=2, extra=extra))

    for extra in (
        {"stage2_52w_pct": 0.96},
        {"quality_min_signal_score": 27},
        {"quality_breakout_volume_min_ratio": 1.20},
        {"stage2_sma_upward_days": 29},
    ):
        for sid in sleeves:
            jobs.append(_job(wave=50, universe="sector_etfs", strategy_ids=(sid,), years=2, extra=extra))

    for extra in (
        {"stage2_52w_pct": 0.705, "vcp_days": 20},
        {"quality_min_signal_score": 53, "stage2_sma_upward_days": 38},
        {"quality_gates_mode": "hard", "quality_min_signal_score": 78},
    ):
        for sid in ("trend_breakout", "weekly_swing", "pullback", "donchian_20"):
            jobs.append(_job(wave=51, universe="focused", strategy_ids=(sid,), years=2, extra=extra))
            jobs.append(_job(wave=51, universe="nasdaq100", strategy_ids=(sid,), years=2, extra=extra))

    for tf, ids in (
        ("daily", ("donchian_20", "gap_and_go")),
        ("daily", ("nr7_breakout", "st_reversal_5d")),
        ("weekly", ("weekly_reversal", "weekly_vcp")),
        ("monthly", ("monthly_52w_high", "tsmom_12m")),
    ):
        jobs.append(_job(wave=51, universe="nasdaq100", strategy_ids=ids, years=2, timeframe=tf))

    for sid in ("weekly_breakout", "weekly_vcp", "nr7_breakout", "pead_primary"):
        jobs.append(_job(wave=52, universe="sp500", strategy_ids=(sid,), years=2, extra={"stage2_sma_upward_days": 40}))
    for extra in ({"quality_min_signal_score": 53}, {"vcp_days": 20}):
        for sid in ("trend_breakout", "weekly_swing", "pullback"):
            jobs.append(_job(wave=52, universe="sector_etfs", strategy_ids=(sid,), years=5, extra=extra))
            jobs.append(_job(wave=52, universe="nasdaq100", strategy_ids=(sid,), years=5, extra=extra))

    # Continuous refill — remaining dropped to ~24 (2026-09-14 13:26 CT).
    for extra in (
        {"stage2_52w_pct": 0.64},
        {"stage2_sma_upward_days": 8},
        {"quality_min_signal_score": 29},
        {"vcp_days": 21},
    ):
        for sid in (
            "trend_breakout",
            "weekly_swing",
            "pullback",
            "donchian_20",
            "weekly_reversal",
            "pead_primary",
            "weekly_vcp",
            "monthly_pullback",
        ):
            jobs.append(_job(wave=53, universe="nasdaq100", strategy_ids=(sid,), years=2, extra=extra))

    for extra in (
        {"stage2_52w_pct": 0.97},
        {"quality_min_signal_score": 46},
        {"quality_breakout_volume_min_ratio": 1.08},
        {"stage2_sma_upward_days": 31},
    ):
        for sid in sleeves:
            jobs.append(_job(wave=54, universe="sector_etfs", strategy_ids=(sid,), years=2, extra=extra))

    for extra in (
        {"stage2_52w_pct": 0.66, "vcp_days": 22},
        {"quality_min_signal_score": 57, "stage2_sma_upward_days": 41},
        {"quality_gates_mode": "shadow", "quality_min_signal_score": 82},
    ):
        for sid in ("weekly_vcp", "monthly_pullback", "weekly_swing", "donchian_20"):
            jobs.append(_job(wave=55, universe="focused", strategy_ids=(sid,), years=2, extra=extra))
            jobs.append(_job(wave=55, universe="nasdaq100", strategy_ids=(sid,), years=2, extra=extra))

    for tf, ids in (
        ("weekly", ("weekly_vcp", "weekly_swing")),
        ("weekly", ("weekly_breakout", "weekly_reversal")),
        ("daily", ("donchian_20", "opening_range_breakout")),
        ("monthly", ("monthly_pullback", "monthly_position")),
    ):
        jobs.append(_job(wave=55, universe="nasdaq100", strategy_ids=ids, years=2, timeframe=tf))

    for sid in ("weekly_vcp", "monthly_pullback", "weekly_swing", "donchian_20"):
        jobs.append(
            _job(wave=56, universe="sp500", strategy_ids=(sid,), years=2, extra={"quality_min_signal_score": 46})
        )
        jobs.append(
            _job(wave=56, universe="sp1500", strategy_ids=(sid,), years=2, extra={"quality_min_signal_score": 46})
        )
    for extra in ({"stage2_sma_upward_days": 43}, {"vcp_days": 22}):
        for sid in ("weekly_vcp", "weekly_swing", "monthly_pullback"):
            jobs.append(_job(wave=56, universe="nasdaq100", strategy_ids=(sid,), years=5, extra=extra))
            jobs.append(_job(wave=56, universe="sp500", strategy_ids=(sid,), years=5, extra=extra))

    seen: set[str] = set()
    unique: list[CampaignJob] = []
    for job in jobs:
        key = job.theory_name
        if key in seen:
            continue
        seen.add(key)
        unique.append(job)
    return unique


def job_overrides(job: CampaignJob) -> dict[str, Any]:
    out = dict(BASE_OVERRIDES)
    for key, val in job.extra_overrides:
        if key == "sma_days":
            out["stage2_sma_upward_days"] = int(val)
            continue
        out[str(key)] = val
    return out


def spec_for_job(job: CampaignJob, *, end_date: date | None = None) -> dict[str, Any]:
    end = end_date or campaign_end_date()
    start = window_start_date(end, job.window_years)
    preset = job.universe_preset
    custom = preset == "custom"
    return {
        "schema_version": 1,
        "theory_name": job.theory_name,
        "universe_mode": "tickers" if custom else "watchlist",
        "universe_preset": preset,
        "tickers": [],
        "scan_timeframe": job.scan_timeframe,
        "strategy_ids": list(job.strategy_ids),
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "overrides": job_overrides(job),
    }


def spec_fingerprint(spec: dict[str, Any]) -> str:
    ov = {str(k): v for k, v in dict(spec.get("overrides") or {}).items() if str(k) not in _FINGERPRINT_SKIP_KEYS}
    ids = [str(s).lower() for s in (spec.get("strategy_ids") or [])]
    return "|".join(
        [
            str(spec.get("universe_preset") or spec.get("universe_mode") or ""),
            str(spec.get("scan_timeframe") or ""),
            ",".join(ids),
            str(spec.get("start_date") or ""),
            str(spec.get("end_date") or ""),
            repr(sorted(ov.items())),
        ]
    )


def campaign_plan(*, end_date: date | None = None) -> list[dict[str, Any]]:
    end = end_date or campaign_end_date()
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for job in build_campaign_jobs():
        spec = spec_for_job(job, end_date=end)
        fingerprint = spec_fingerprint(spec)
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        rows.append(
            {
                "wave": job.wave,
                "theory_name": job.theory_name,
                "fingerprint": fingerprint,
                "window_years": job.window_years,
                "spec": spec,
            }
        )
    return rows
