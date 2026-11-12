"""Date split, embargo, and the zero-spend trust gate for the prompt lab.

The holdout is live cases only, from the last configured weeks (or trading
days). Rebuilt cases never enter it. Train is rebuilt cases plus live cases
whose 20-trading-day label ends before the holdout starts. A night that fails
the gate does not call the model.

Nothing here places an order.
"""

from __future__ import annotations

import hashlib
import random
from collections import defaultdict
from dataclasses import dataclass, replace
from datetime import date, timedelta
from typing import Any, Callable

from core.tab_agent_lab_score import PRE_CUTOFF, LabCase

# Fields both a live packet and a rebuilt bar card can carry. Fundamentals,
# news, catalysts, and model-derived scores stay off so the two sources match.
_DROP_EXACT = {
    "qual_business",
    "rank_score",
    "rank_score_v2",
    "composite_score",
    "score",
    "edge_score",
    "reliability_score",
    "p_up_calibrated",
    "ev_10d",
}
_DROP_PARTS = ("news", "catalyst", "earning", "pead", "forensic", "fundamental", "filing", "headline", "advisory")
_KEEP_MARKET = ("regime_bucket", "regime_state", "sector_winning")
_ANON_COPY = (
    "stage_2",
    "vcp",
    "signal_score",
    "stop_pct",
    "spread_pct",
    "avg_dollar_volume",
    "dollar_volume",
    "relative_volume",
    "relative_strength",
    "sector_relative_strength",
    "sector_winning",
    "sector_etf",
    "regime_bucket",
    "qual_trend",
    "qual_setup",
    "qual_asymmetry",
    "qual_crowding",
    "qual_raw",
)
_INSTRUCTION = "Decide from these fields only. Long or cash. Never a short or an option."


def shift_trading_days(day: date, offset: int) -> date:
    """Move ``offset`` weekdays. Holidays are not skipped."""
    if offset == 0:
        return day
    step = 1 if offset > 0 else -1
    left = abs(int(offset))
    cursor = day
    while left:
        cursor += timedelta(days=step)
        if cursor.weekday() < 5:
            left -= 1
    return cursor


def trading_days_between(start: date, end: date) -> int:
    """Weekday steps strictly after ``start`` through ``end``."""
    if end <= start:
        return 0
    count = 0
    cursor = start
    while cursor < end:
        cursor += timedelta(days=1)
        if cursor.weekday() < 5:
            count += 1
    return count


def resolve_holdout_days(*, holdout_days: int | None, holdout_weeks: int) -> int | None:
    """Trading-day holdout length. None means the week fallback."""
    if holdout_days is not None and int(holdout_days) > 0:
        return max(1, int(holdout_days))
    return None


def _parse_day(value: str) -> date | None:
    text = str(value or "")[:10]
    if len(text) < 10:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def _drop_card_key(key: str) -> bool:
    name = str(key or "")
    if name in _DROP_EXACT:
        return True
    lowered = name.lower()
    return any(part in lowered for part in _DROP_PARTS)


def lab_decision_card(signal: dict[str, Any] | None, as_of: str) -> dict[str, Any]:
    """Point-in-time card shared by live and rebuilt cases.

    Fundamentals and news or catalysts are removed. Model-derived scores are
    removed so a live packet cannot show the model a field the rebuild lacks.
    """
    from core.tab_agent_replay import pit_payload

    payload = pit_payload(signal if isinstance(signal, dict) else {}, as_of)
    card = {key: value for key, value in payload.items() if not _drop_card_key(str(key))}
    source = signal if isinstance(signal, dict) else {}
    for key in _KEEP_MARKET:
        if key not in card and source.get(key) is not None and not _drop_card_key(key):
            card[key] = source.get(key)
    if "regime_bucket" not in card and source.get("regime_state") not in (None, ""):
        card["regime_bucket"] = source.get("regime_state")
    card.setdefault("instruction", _INSTRUCTION)
    return card


_RAW_MARKERS = ("ticker", "symbol", "as_of", "price", "close", "entry_price", "sma_50", "sma_200", "stop_price")


def _is_raw_card(card: dict[str, Any]) -> bool:
    """A pit card still carries identity or a dollar price. An anonymized card does not."""
    return any(key in card for key in _RAW_MARKERS)


def decision_payload(case: LabCase, *, reveal_ticker: bool = False) -> dict[str, Any]:
    """The card ``decide`` sends. Live and rebuild use the same anonymized shape.

    ``reveal_ticker`` puts the ticker back on a live card only. It defaults off
    so train and the live holdout stay on one distribution.
    """
    stored = case.payload if isinstance(case.payload, dict) and case.payload else None
    if stored is not None and not _is_raw_card(stored):
        card = {key: value for key, value in stored.items() if not _drop_card_key(str(key))}
        for banned in _RAW_MARKERS:
            card.pop(banned, None)
    else:
        raw = stored or lab_decision_card(
            {
                "ticker": case.ticker,
                "signal_score": case.signal_score,
                "avg_dollar_volume": case.avg_dollar_volume,
                "spread_pct": case.spread_pct,
            },
            case.as_of,
        )
        card = anonymize_card(raw, case_id=f"{case.ticker}:{case.as_of}")
    if reveal_ticker and str(case.source or "live") != "rebuild" and case.ticker:
        card["ticker"] = case.ticker
    card.setdefault("instruction", _INSTRUCTION)
    return card


def _num(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number:
        return None
    return number


def anonymize_card(card: dict[str, Any], *, case_id: str) -> dict[str, Any]:
    """Hashed id, no ticker or date. Price levels become percent distances."""
    price = _num(card.get("price"))
    if price is None:
        price = _num(card.get("close"))
    if price is None:
        price = _num(card.get("entry_price"))
    out: dict[str, Any] = {
        "case_id": hashlib.sha256(str(case_id).encode("utf-8")).hexdigest()[:16],
        "instruction": _INSTRUCTION,
    }
    for key in _ANON_COPY:
        if card.get(key) is not None and not _drop_card_key(key):
            out[key] = card[key]

    def _dist(level_key: str) -> float | None:
        level = _num(card.get(level_key))
        if price is None or level is None or level == 0:
            return None
        return round((price - level) / level * 100.0, 4)

    sma50 = _dist("sma_50")
    sma200 = _dist("sma_200")
    if sma50 is not None:
        out["dist_sma_50_pct"] = sma50
    if sma200 is not None:
        out["dist_sma_200_pct"] = sma200
    high_ratio = _num(card.get("pct_from_52w_high"))
    if high_ratio is not None and high_ratio > 0:
        out["pct_off_52w_high"] = round((1.0 - high_ratio) * 100.0, 4)
    elif price is not None:
        high = _num(card.get("high_52w"))
        if high is not None and high > 0:
            out["pct_off_52w_high"] = round((high - price) / high * 100.0, 4)
    stop = _num(card.get("stop_price"))
    if price is not None and stop is not None and price > 0:
        out["dist_stop_pct"] = round((price - stop) / price * 100.0, 4)
    for banned in (
        "ticker",
        "symbol",
        "as_of",
        "date",
        "entry_date",
        "price",
        "close",
        "entry_price",
        "sma_50",
        "sma_200",
    ):
        out.pop(banned, None)
    return out


def era_for(as_of: str, explicit: str = "") -> str:
    if explicit:
        return explicit
    day = _parse_day(as_of)
    if day is None:
        return "unknown"
    from core.scoring_metrics import ERA_BOUNDS

    for name, (start, end) in ERA_BOUNDS.items():
        start_day = date.fromisoformat(start)
        end_day = date.fromisoformat(end) if end else date.max
        if start_day <= day <= end_day:
            return name
    return "unknown"


def _is_rebuild(case: LabCase) -> bool:
    return str(case.source or "") == "rebuild"


def _dedupe(cases: list[LabCase]) -> tuple[list[LabCase], int]:
    """One row per ticker and date. A live row wins over a rebuild of the same name."""
    chosen: dict[tuple[str, str], LabCase] = {}
    removed = 0
    ordered = sorted(cases, key=lambda case: (_is_rebuild(case), case.as_of, case.ticker, case.case_id))
    for case in ordered:
        key = (case.ticker, case.as_of)
        if key in chosen:
            removed += 1
            continue
        chosen[key] = case
    rows = sorted(chosen.values(), key=lambda case: (case.as_of, case.ticker, case.case_id))
    return rows, removed


def _holdout_membership(day: date, last: date, *, holdout_days: int | None, holdout_weeks: int) -> bool:
    if holdout_days is not None and holdout_days > 0:
        start = shift_trading_days(last, -(int(holdout_days) - 1))
        return day >= start
    cutoff = last - timedelta(days=7 * max(1, int(holdout_weeks)))
    return day > cutoff


def stamp_cohort_baseline(cases: list[LabCase]) -> list[LabCase]:
    """Mean rebuild return on each date. That is the buy-every-candidate baseline."""
    grouped: dict[str, list[float]] = defaultdict(list)
    for case in cases:
        if _is_rebuild(case):
            grouped[case.as_of].append(float(case.long_return_pct))
    means = {day: sum(values) / len(values) for day, values in grouped.items() if values}
    stamped: list[LabCase] = []
    for case in cases:
        if _is_rebuild(case) and case.as_of in means and case.cohort_return_pct is None:
            stamped.append(replace(case, cohort_return_pct=round(means[case.as_of], 6)))
        else:
            stamped.append(case)
    return stamped


def sample_train_per_era(cases: list[LabCase], per_era: int, *, seed: str = "lab") -> list[LabCase]:
    """Keep at most ``per_era`` train rows in each era. A non-empty era stays non-empty.

    ``per_era`` <= 0 keeps every row. Holdout rows are not passed here.
    """
    if per_era <= 0 or not cases:
        return list(cases)
    groups: dict[str, list[LabCase]] = defaultdict(list)
    for case in cases:
        groups[era_for(case.as_of, case.era)].append(case)
    kept: list[LabCase] = []
    for era in sorted(groups):
        rows = sorted(groups[era], key=lambda case: (case.as_of, case.ticker, case.case_id))
        if len(rows) <= int(per_era):
            kept.extend(rows)
            continue
        rng = random.Random(f"{seed}:{era}")
        picked = rng.sample(rows, int(per_era))
        picked.sort(key=lambda case: (case.as_of, case.ticker, case.case_id))
        kept.extend(picked)
    kept.sort(key=lambda case: (case.as_of, case.ticker, case.case_id))
    return kept


def _window(cases: list[LabCase]) -> dict[str, Any]:
    dates = sorted({case.as_of for case in cases})
    return {
        "n": len(cases),
        "dates": len(dates),
        "start": dates[0] if dates else "",
        "end": dates[-1] if dates else "",
        "live": sum(1 for case in cases if not _is_rebuild(case)),
        "rebuild": sum(1 for case in cases if _is_rebuild(case)),
        "pre_cutoff": sum(1 for case in cases if case.pre_cutoff),
    }


@dataclass
class LabPlan:
    train: list[LabCase]
    holdout: list[LabCase]
    embargo: list[LabCase]
    ok: bool
    failures: list[str]
    reason: str
    holdout_days: int | None
    holdout_weeks: int
    embargo_days: int
    fixtures_excluded: int
    duplicates_removed: int
    train_before_sample: int
    min_train_cases: int
    min_train_dates: int
    min_holdout_cases: int
    min_holdout_dates: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "failures": list(self.failures),
            "reason": self.reason,
            "train": _window(self.train),
            "holdout": _window(self.holdout),
            "embargo": _window(self.embargo) | {"trading_days": int(self.embargo_days)},
            "holdout_days": self.holdout_days,
            "holdout_weeks": self.holdout_weeks,
            "embargo_days": self.embargo_days,
            "fixtures_excluded": self.fixtures_excluded,
            "duplicates_removed": self.duplicates_removed,
            "train_before_sample": self.train_before_sample,
            "needed": {
                "min_train_cases": self.min_train_cases,
                "min_train_dates": self.min_train_dates,
                "min_holdout_cases": self.min_holdout_cases,
                "min_holdout_dates": self.min_holdout_dates,
            },
            "orders_touched": False,
        }


def _reason(plan_bits: dict[str, Any]) -> str:
    train = plan_bits["train"]
    holdout = plan_bits["holdout"]
    embargo = plan_bits["embargo"]
    needed = plan_bits["needed"]
    failures = plan_bits["failures"]
    head = "Trust gate passed." if not failures else "Trust gate blocked the night with zero spend."
    text = (
        f"{head} "
        f"train {train['n']} cases over {train['dates']} dates ({train['start'] or 'none'}..{train['end'] or 'none'}, "
        f"live {train['live']}, rebuild {train['rebuild']}); "
        f"holdout {holdout['n']} cases over {holdout['dates']} dates "
        f"({holdout['start'] or 'none'}..{holdout['end'] or 'none'}, live only); "
        f"embargo {plan_bits['embargo_days']} trading days dropped {embargo['n']} cases "
        f"({embargo['start'] or 'none'}..{embargo['end'] or 'none'}). "
        f"Need train>={needed['min_train_cases']} cases over >={needed['min_train_dates']} dates "
        f"and holdout>={needed['min_holdout_cases']} cases over >={needed['min_holdout_dates']} dates. "
        f"Fixtures excluded: {plan_bits['fixtures_excluded']}. "
        f"Duplicate (ticker, as_of) removed: {plan_bits['duplicates_removed']}."
    )
    if failures:
        text += " Failed: " + ", ".join(failures) + "."
    return text


def plan_split(
    cases: list[LabCase],
    *,
    holdout_weeks: int = 6,
    holdout_days: int | None = None,
    embargo_days: int = 20,
    train_per_era: int = 300,
    min_train_cases: int = 150,
    min_train_dates: int = 15,
    min_holdout_cases: int = 200,
    min_holdout_dates: int = 10,
    fixtures_excluded: int = 0,
    duplicates_removed: int = 0,
    sample_seed: str = "lab",
) -> LabPlan:
    """Split by as-of date, purge the label horizon, then sample train per era."""
    rows, removed = _dedupe(list(cases))
    duplicates_removed += removed
    stamped = stamp_cohort_baseline(rows)
    live_days = [_parse_day(case.as_of) for case in stamped if not _is_rebuild(case)]
    live_days = [day for day in live_days if day is not None]
    resolved_days = resolve_holdout_days(holdout_days=holdout_days, holdout_weeks=holdout_weeks)
    embargo_n = max(0, int(embargo_days))

    holdout: list[LabCase] = []
    train_pool: list[LabCase] = []
    embargo: list[LabCase] = []
    if not live_days:
        for case in stamped:
            if _is_rebuild(case):
                train_pool.append(case)
            else:
                embargo.append(case)
        holdout_start = None
    else:
        last = max(live_days)
        in_window: list[LabCase] = []
        earlier: list[LabCase] = []
        for case in stamped:
            day = _parse_day(case.as_of)
            if day is None:
                continue
            if not _is_rebuild(case) and _holdout_membership(
                day, last, holdout_days=resolved_days, holdout_weeks=holdout_weeks
            ):
                in_window.append(case)
            else:
                earlier.append(case)
        holdout = [case for case in in_window if not _is_rebuild(case)]
        holdout_dates = [_parse_day(case.as_of) for case in holdout]
        holdout_dates = [day for day in holdout_dates if day is not None]
        holdout_start = min(holdout_dates) if holdout_dates else None
        for case in earlier:
            day = _parse_day(case.as_of)
            if day is None or holdout_start is None:
                train_pool.append(case)
                continue
            if trading_days_between(day, holdout_start) > embargo_n:
                train_pool.append(case)
            else:
                embargo.append(case)
        for case in in_window:
            if _is_rebuild(case):
                embargo.append(case)

    before = len(train_pool)
    train = sample_train_per_era(train_pool, int(train_per_era), seed=sample_seed)
    if train_pool and not train:
        train = list(train_pool)
    if any(not _is_rebuild(case) for case in holdout) or holdout:
        holdout = [case for case in holdout if not _is_rebuild(case)]

    train_stats = _window(train)
    holdout_stats = _window(holdout)
    embargo_stats = _window(embargo)
    failures: list[str] = []
    seen: set[tuple[str, str]] = set()
    for case in train + holdout + embargo:
        key = (case.ticker, case.as_of)
        if key in seen:
            failures.append("duplicate_ticker_as_of")
            break
        seen.add(key)
    if train_stats["n"] < int(min_train_cases):
        failures.append("insufficient_train_cases")
    if train_stats["dates"] < int(min_train_dates):
        failures.append("insufficient_train_dates")
    if holdout_stats["n"] < int(min_holdout_cases):
        failures.append("insufficient_holdout_cases")
    if holdout_stats["dates"] < int(min_holdout_dates):
        failures.append("insufficient_holdout_dates")
    bits = {
        "train": train_stats,
        "holdout": holdout_stats,
        "embargo": embargo_stats,
        "embargo_days": embargo_n,
        "needed": {
            "min_train_cases": int(min_train_cases),
            "min_train_dates": int(min_train_dates),
            "min_holdout_cases": int(min_holdout_cases),
            "min_holdout_dates": int(min_holdout_dates),
        },
        "fixtures_excluded": int(fixtures_excluded),
        "duplicates_removed": int(duplicates_removed),
        "failures": failures,
    }
    return LabPlan(
        train=train,
        holdout=holdout,
        embargo=embargo,
        ok=not failures,
        failures=failures,
        reason=_reason(bits),
        holdout_days=resolved_days,
        holdout_weeks=int(holdout_weeks),
        embargo_days=embargo_n,
        fixtures_excluded=int(fixtures_excluded),
        duplicates_removed=int(duplicates_removed),
        train_before_sample=before,
        min_train_cases=int(min_train_cases),
        min_train_dates=int(min_train_dates),
        min_holdout_cases=int(min_holdout_cases),
        min_holdout_dates=int(min_holdout_dates),
    )


def prepare_scoring_cases(cases: list[LabCase]) -> list[LabCase]:
    """Rebuild rows are scored as excess over the same-date buy-all baseline."""
    if not any(_is_rebuild(case) for case in cases):
        return list(cases)
    stamped = stamp_cohort_baseline(cases)
    prepared: list[LabCase] = []
    for case in stamped:
        if not _is_rebuild(case) or case.cohort_return_pct is None:
            prepared.append(case)
            continue
        excess = round(float(case.long_return_pct) - float(case.cohort_return_pct), 6)
        prepared.append(replace(case, long_return_pct=excess))
    return prepared


def _subset_book(
    cases: list[LabCase],
    decisions: list[str],
    *,
    config: Any,
    text: str,
    live_text: str,
    drift_weight: float,
) -> dict[str, Any] | None:
    from core.tab_agent_lab_score import score_long_cash

    if not cases:
        return None
    return score_long_cash(
        cases,
        decisions,
        config=config,
        text=text,
        live_text=live_text,
        drift_weight=drift_weight,
    )


def source_views(
    cases: list[LabCase],
    decisions: list[str],
    *,
    config: Any,
    text: str = "",
    live_text: str = "",
    drift_weight: float = 0.0,
) -> dict[str, Any]:
    """Sortino slices. Rebuild slices use excess returns. Keys do not say holdout."""
    prepared = prepare_scoring_cases(cases)
    pairs = list(zip(cases, prepared, decisions, strict=False))

    def _take(pred: Callable[[LabCase], bool]) -> dict[str, Any] | None:
        chosen = [(prepared_case, decision) for _raw, prepared_case, decision in pairs if pred(_raw)]
        if not chosen:
            return None
        subset, sides = zip(*chosen, strict=False)
        return _subset_book(
            list(subset),
            list(sides),
            config=config,
            text=text,
            live_text=live_text,
            drift_weight=drift_weight,
        )

    used = any(_is_rebuild(case) and case.pre_cutoff for case in cases)
    return {
        "slices": {
            "rebuild_pre_cutoff": _take(lambda case: _is_rebuild(case) and case.pre_cutoff),
            "rebuild_post_cutoff": _take(lambda case: _is_rebuild(case) and not case.pre_cutoff),
            "live": _take(lambda case: not _is_rebuild(case)),
        },
        "ex_pre_cutoff": _take(lambda case: not (_is_rebuild(case) and case.pre_cutoff)),
        "used_pre_cutoff": used,
    }


def project_holdout_readiness(
    cohorts: list[tuple[str, str, int]],
    *,
    holdout_weeks: int = 6,
    min_cases: int = 200,
    min_dates: int = 10,
) -> dict[str, Any]:
    """First maturity date when the live holdout clears the case and date floors.

    ``cohorts`` are ``(as_of, matures_on, n_cases)``. A cohort counts on its
    maturity date. The holdout is the same last-``holdout_weeks`` rule as the lab.
    """
    ordered = sorted(cohorts, key=lambda item: (item[1], item[0]))
    ready_on = ""
    snapshot: dict[str, Any] = {}
    seen: list[tuple[date, int]] = []
    for as_of, matures_on, count in ordered:
        day = _parse_day(as_of)
        mature = _parse_day(matures_on)
        if day is None or mature is None or int(count) <= 0:
            continue
        seen.append((day, int(count)))
        last = max(item[0] for item in seen)
        cutoff = last - timedelta(days=7 * max(1, int(holdout_weeks)))
        hold_dates = sorted({item[0] for item in seen if item[0] > cutoff})
        hold_n = sum(item[1] for item in seen if item[0] > cutoff)
        if not ready_on and hold_n >= int(min_cases) and len(hold_dates) >= int(min_dates):
            ready_on = mature.isoformat()
            snapshot = {
                "as_of_start": hold_dates[0].isoformat(),
                "as_of_end": hold_dates[-1].isoformat(),
                "cases": hold_n,
                "dates": len(hold_dates),
            }
    return {
        "holdout_ready_on": ready_on,
        "holdout": snapshot,
        "min_cases": int(min_cases),
        "min_dates": int(min_dates),
        "holdout_weeks": int(holdout_weeks),
    }


def is_pre_cutoff(as_of: str) -> bool:
    day = _parse_day(as_of)
    return bool(day is not None and day < PRE_CUTOFF)
