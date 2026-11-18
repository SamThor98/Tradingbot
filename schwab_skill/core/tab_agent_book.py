"""Honest book for tab-agent digest, replay, and lab scores.

Overlapping trades are marked to market each session and held at equal weight.
They are not compounded into one chain. Cash earns the cached BIL daily return,
or zero when that cache is missing. SPY is the cached total return over the same
dates. Nothing here places an order or fetches a price.
"""

from __future__ import annotations

import json
import math
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable

from core.fixture_packet import is_fixture_packet
from core.tab_agent_objective import ObjectiveConfig, drawdown_stats, equity_path

ROUND_TRIP_COST = 0.0034
ROUND_TRIP_COST_SENSITIVITY = 0.0010
ANNUALIZATION = math.sqrt(252)
MIN_WEEKS = 8
SHRINK_PSEUDO = 10
SNA_TEST_TICKER = "SNA"
SNA_TEST_DAY = "2026-07-06"
QUARANTINE_FILE = "decision_packets.fixture_quarantine.json"
MIN_INVESTED_DAYS = 5
MIN_DOWNSIDE_OBS = 5
# A quarter of the long weight with no daily path is enough to refuse the score.
MEANINGFUL_MISSING_PATH = 0.25
MISSING_PATHS = "unscored: missing price paths"
NEAR_ZERO_DOWNSIDE = "unscored: near-zero downside"

COLUMN_GUIDE = (
    "Sortino is the annualized daily Sortino (square root of 252) of an equal-weight "
    "mark-to-market book. Max DD is that book's peak-to-trough loss. "
    "SPY total return, SPY Sortino, and SPY max DD use the same sessions as that book. "
    "The book calendar is the SPY cache when it covers the span, otherwise the NYSE session calendar. "
    "Cash earns the cached BIL daily return, or zero when BIL is not cached. "
    "Excess is the mean 20-session return minus SPY over those same sessions. "
    "Selection is that return minus the same-day pool. "
    "Each reports n, distinct days, distinct weeks, a 95% week-clustered interval, "
    "and a value shrunk toward zero by n/(n+10). Fewer than 8 distinct weeks is insufficient. "
    "A book with no daily path on a meaningful share of its longs is unscored: missing price paths, "
    "and does not report Sortino or max DD. A cash-only or near-zero-downside book does not report a Sortino. "
    "The system tab is a Brier score plus a reliability table. "
    "The book charges 0.34% round trip, and also reports the 0.10% cost sensitivity. "
    "A new label enters at the next open. A stop fills at the lower of the stop and the open."
)

ATTRIBUTION_NOTE = (
    "Unresolved live attributions stay out of the book until the horizon label exists. "
    "A 20-session label on a decision from 2026-11-04 is first available about 2026-12-03. "
    "The resolved row is then scored on this daily book, with quarantined fixtures removed."
)


def quarantine_path(skill_dir: Path | str | None) -> Path:
    root = Path(skill_dir) if skill_dir is not None else Path(".")
    return root / QUARANTINE_FILE


def load_quarantine(skill_dir: Path | str | None) -> list[dict[str, Any]]:
    """Packets moved aside by the fixture quarantine. A missing file excludes nothing."""
    path = quarantine_path(skill_dir)
    if not path.is_file():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    rows = payload.get("packets") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, dict)]


def _day(row: dict[str, Any]) -> str:
    for key in ("as_of", "date", "day", "created_at"):
        text = str(row.get(key) or "")[:10]
        if len(text) == 10 and text[4] == "-" and text[7] == "-":
            return text
    return ""


def _ticker(row: dict[str, Any]) -> str:
    return str(row.get("ticker") or row.get("symbol") or "").upper().strip()


def _as_packet(row: dict[str, Any]) -> dict[str, Any]:
    refs = row.get("refs") if isinstance(row.get("refs"), dict) else {}
    order = row.get("order_ref")
    if order is None:
        order = refs.get("order_ref")
    entry = row.get("entry_price")
    if entry is None:
        entry = refs.get("entry_price")
    return {
        "ticker": _ticker(row),
        "entry_price": entry,
        "order_ref": order,
        "refs": {"order_ref": order} if order else refs,
    }


def is_book_excluded(
    row: dict[str, Any] | None,
    *,
    skill_dir: Path | str | None = None,
    quarantine: list[dict[str, Any]] | None = None,
) -> bool:
    """True when this row must not enter a book or a baseline.

    The quarantine file is the operator's list. The approve-flow AAPL signature
    and the SNA 2026-07-06 test pair are excluded even when that file is absent.
    """
    if not isinstance(row, dict):
        return False
    if is_fixture_packet(_as_packet(row)):
        return True
    ticker = _ticker(row)
    day = _day(row)
    if ticker == SNA_TEST_TICKER and day == SNA_TEST_DAY:
        return True
    packets = quarantine if quarantine is not None else load_quarantine(skill_dir)
    packet_id = str(row.get("packet_id") or "").strip()
    for item in packets:
        item_id = str(item.get("packet_id") or "").strip()
        if packet_id and item_id and packet_id == item_id:
            return True
        if ticker and day and _ticker(item) == ticker and _day(item) == day:
            return True
        if is_fixture_packet(_as_packet(item)) and is_fixture_packet(_as_packet(row)):
            return True
    return False


def drop_excluded(
    rows: list[dict[str, Any]],
    *,
    skill_dir: Path | str | None = None,
) -> tuple[list[dict[str, Any]], int]:
    quarantine = load_quarantine(skill_dir)
    kept: list[dict[str, Any]] = []
    excluded = 0
    for row in rows:
        if is_book_excluded(row, skill_dir=skill_dir, quarantine=quarantine):
            excluded += 1
            continue
        kept.append(row)
    return kept, excluded


def _parse_day(raw: str) -> date | None:
    text = str(raw or "")[:10]
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def _iso_week(day: date) -> str:
    year, week, _weekday = day.isocalendar()
    return f"{year}-W{week:02d}"


class _Calendar:
    """Sessions inside a SPY cache span, and NYSE holidays outside it."""

    def __init__(self, spy_daily: dict[str, float] | None) -> None:
        days = [day for key in (spy_daily or {}) if (day := _parse_day(key)) is not None]
        self.spy_lo = min(days) if days else None
        self.spy_hi = max(days) if days else None
        self.spy_days = set(days)

    def is_session(self, day: date) -> bool:
        if day.weekday() >= 5:
            return False
        if self.spy_lo is not None and self.spy_hi is not None and self.spy_lo <= day <= self.spy_hi:
            return day in self.spy_days
        return day not in _nyse_holidays(day.year)

    def roll(self, day: date) -> date | None:
        cursor = day
        for _ in range(14):
            if self.is_session(cursor):
                return cursor
            cursor += timedelta(days=1)
        return None


def _observed(day: date) -> date:
    if day.weekday() == 5:
        return day - timedelta(days=1)
    if day.weekday() == 6:
        return day + timedelta(days=1)
    return day


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    first = date(year, month, 1)
    offset = (weekday - first.weekday()) % 7
    return first + timedelta(days=offset + 7 * (n - 1))


def _last_weekday(year: int, month: int, weekday: int) -> date:
    if month == 12:
        cursor = date(year + 1, 1, 1) - timedelta(days=1)
    else:
        cursor = date(year, month + 1, 1) - timedelta(days=1)
    while cursor.weekday() != weekday:
        cursor -= timedelta(days=1)
    return cursor


def _easter(year: int) -> date:
    """Anonymous Gregorian algorithm. Good Friday is two days earlier."""
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    ell = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * ell) // 451
    month = (h + ell - 7 * m + 114) // 31
    day = ((h + ell - 7 * m + 114) % 31) + 1
    return date(year, month, day)


@lru_cache(maxsize=32)
def _nyse_holidays(year: int) -> frozenset[date]:
    """Full-day NYSE closures. A Saturday holiday is observed on Friday, Sunday on Monday."""
    holidays = {
        _observed(date(year, 1, 1)),
        _observed(date(year, 6, 19)),
        _observed(date(year, 7, 4)),
        _observed(date(year, 12, 25)),
        _nth_weekday(year, 1, 0, 3),
        _nth_weekday(year, 2, 0, 3),
        _last_weekday(year, 5, 0),
        _nth_weekday(year, 9, 0, 1),
        _nth_weekday(year, 11, 3, 4),
        _easter(year) - timedelta(days=2),
    }
    new_year = date(year + 1, 1, 1)
    if new_year.weekday() == 5:
        holidays.add(date(year, 12, 31))
    return frozenset(holidays)


def holding_dates(signal: str, horizon_days: int, calendar: _Calendar | None = None) -> list[date]:
    """The next ``horizon_days`` sessions after the signal date. That is the next-open window."""
    start = _parse_day(signal)
    if start is None or horizon_days <= 0:
        return []
    cal = calendar or _Calendar(None)
    cursor = start
    out: list[date] = []
    guard = 0
    limit = int(horizon_days) * 4 + 21
    while len(out) < int(horizon_days) and guard < limit:
        cursor += timedelta(days=1)
        guard += 1
        if cal.is_session(cursor):
            out.append(cursor)
    return out


def _session_span(days: list[date], calendar: _Calendar) -> list[date]:
    if not days:
        return []
    cursor = min(days)
    end = max(days)
    out: list[date] = []
    while cursor <= end:
        if calendar.is_session(cursor):
            out.append(cursor)
        cursor += timedelta(days=1)
    return out


def apply_round_trip(gross: float, cost: float) -> float:
    """Simple return after a round-trip cost. ``gross`` and ``cost`` are fractions."""
    return (1.0 + float(gross)) * (1.0 - float(cost)) - 1.0


def _apply_path_cost(daily: list[float], cost: float) -> list[float]:
    if not daily or cost <= 0:
        return list(daily)
    if len(daily) == 1:
        return [apply_round_trip(daily[0], cost)]
    half = float(cost) / 2.0
    out = list(daily)
    out[0] = apply_round_trip(daily[0], half)
    out[-1] = apply_round_trip(daily[-1], half)
    return out


def stop_fill(stop: float, open_price: float) -> float:
    """A gap through the stop fills at the open. An intraday touch fills at the stop."""
    return min(float(stop), float(open_price))


def walk_long_position(
    frame: Any,
    *,
    signal_date: str,
    horizon_days: int,
    stop_price: float | None,
) -> dict[str, Any] | None:
    """Next-open entry and a gap-aware stop. Returns gross daily simple returns.

    The first session after ``signal_date`` is the entry. A stop fills at
    ``min(stop, open)``. Missing opens do not invent a fill from the scan quote.
    """
    if frame is None or getattr(frame, "empty", True):
        return None
    columns = set(getattr(frame, "columns", []))
    if "open" not in columns or "close" not in columns:
        return None
    signal = _parse_day(signal_date)
    if signal is None:
        return None
    index = list(frame.index)
    sessions: list[tuple[date, int]] = []
    for pos, stamp in enumerate(index):
        day = _parse_day(str(stamp)[:10])
        if day is None or day <= signal:
            continue
        sessions.append((day, pos))
        if len(sessions) >= int(horizon_days):
            break
    if not sessions:
        return None

    def _cell(column: str, pos: int) -> float | None:
        if column not in columns:
            return None
        try:
            value = float(frame[column].iloc[pos])
        except (TypeError, ValueError):
            return None
        if math.isnan(value):
            return None
        return value

    entry_day, entry_pos = sessions[0]
    entry = _cell("open", entry_pos)
    if entry is None or entry <= 0:
        return None
    prev = entry
    daily: list[dict[str, Any]] = []
    stopped = False
    exit_price = entry
    exit_day = entry_day
    for day, pos in sessions:
        open_price = _cell("open", pos)
        low = _cell("low", pos)
        close = _cell("close", pos)
        if open_price is None or close is None:
            return None
        if low is None:
            low = min(open_price, close)
        if stop_price is not None and stop_price > 0 and low <= float(stop_price):
            fill = stop_fill(float(stop_price), open_price)
            ret = fill / prev - 1.0 if prev > 0 else 0.0
            daily.append({"date": day.isoformat(), "return": ret})
            exit_price = fill
            exit_day = day
            stopped = True
            break
        ret = close / prev - 1.0 if prev > 0 else 0.0
        daily.append({"date": day.isoformat(), "return": ret})
        prev = close
        exit_price = close
        exit_day = day
    gross = 1.0
    for row in daily:
        gross *= 1.0 + float(row["return"])
    return {
        "entry_date": entry_day.isoformat(),
        "entry_price": entry,
        "exit_date": exit_day.isoformat(),
        "exit_price": exit_price,
        "stop_price": None if stop_price is None else float(stop_price),
        "daily_returns": daily,
        "gross_return": gross - 1.0,
        "gross_return_pct": round((gross - 1.0) * 100.0, 4),
        "stopped": stopped,
        "stop_fill": "min_stop_open",
        "entry_convention": "next_open",
    }


def _compound(returns: list[float]) -> float:
    equity = 1.0
    for value in returns:
        equity *= max(0.0, 1.0 + float(value))
    return equity - 1.0


def _sortino_ratio(returns: list[float]) -> tuple[float | None, bool]:
    """Daily ratio and whether a positive book has no downside."""
    if not returns:
        return None, False
    mean = sum(returns) / len(returns)
    downside = math.sqrt(sum(min(value, 0.0) ** 2 for value in returns) / len(returns))
    if downside <= 1e-12:
        if mean > 0:
            return None, True
        return 0.0, False
    return mean / downside, False


def week_cluster_stats(values: list[float], days: list[date]) -> dict[str, Any]:
    """Mean, 95% week-clustered interval, and a shrink toward zero."""
    if not values or len(values) != len(days):
        return {
            "n": 0,
            "n_days": 0,
            "n_weeks": 0,
            "mean": None,
            "ci_low": None,
            "ci_high": None,
            "shrunk": None,
            "shrink_weight": None,
            "status": "insufficient",
        }
    n = len(values)
    mean = sum(values) / n
    weeks = [_iso_week(day) for day in days]
    grouped: dict[str, list[float]] = {}
    for value, week in zip(values, weeks, strict=False):
        grouped.setdefault(week, []).append(value)
    n_weeks = len(grouped)
    ci_low = None
    ci_high = None
    if n_weeks >= 2:
        score = 0.0
        for members in grouped.values():
            cluster_sum = sum(value - mean for value in members)
            score += cluster_sum * cluster_sum
        se = math.sqrt((n_weeks / (n_weeks - 1)) * score) / n
        ci_low = mean - 1.96 * se
        ci_high = mean + 1.96 * se
    weight = n / (n + SHRINK_PSEUDO)
    distinct_days = len(set(days))
    return {
        "n": n,
        "n_days": distinct_days,
        "n_weeks": n_weeks,
        "mean": round(mean, 6),
        "ci_low": None if ci_low is None else round(ci_low, 6),
        "ci_high": None if ci_high is None else round(ci_high, 6),
        "shrunk": round(weight * mean, 6),
        "shrink_weight": round(weight, 6),
        "status": "insufficient" if n_weeks < MIN_WEEKS else "ok",
    }


def _stored_path(leg: dict[str, Any], *, cost: float, apply_cost: bool) -> list[tuple[date, float]]:
    raw_daily = leg.get("daily")
    if not isinstance(raw_daily, list) or not raw_daily:
        return []
    days: list[date] = []
    values: list[float] = []
    for item in raw_daily:
        if not isinstance(item, dict):
            continue
        day = _parse_day(str(item.get("date") or ""))
        try:
            ret = float(item.get("return"))
        except (TypeError, ValueError):
            continue
        if day is None:
            continue
        days.append(day)
        values.append(ret)
    if not days:
        return []
    priced = _apply_path_cost(values, cost if apply_cost else 0.0)
    return list(zip(days, priced, strict=False))


def _smeared_path(
    leg: dict[str, Any],
    *,
    cost: float,
    apply_cost: bool,
    calendar: _Calendar,
) -> list[tuple[date, float]]:
    """Spread a total return across sessions. Used only when no price cache is configured."""
    try:
        gross = float(leg.get("return_pct")) / 100.0
    except (TypeError, ValueError):
        return []
    horizon = int(leg.get("horizon_days") or 0)
    days = holding_dates(str(leg.get("date") or ""), horizon, calendar)
    if not days:
        return []
    net = apply_round_trip(gross, cost) if apply_cost else gross
    if net <= -1.0:
        rate = -1.0
    else:
        rate = (1.0 + net) ** (1.0 / len(days)) - 1.0
    return [(day, rate) for day in days]


def _cache_path(
    leg: dict[str, Any],
    lookup: Callable[[str], Any] | None,
    *,
    cost: float,
    apply_cost: bool,
) -> list[tuple[date, float]]:
    """Next-open path from the local price cache. No network call."""
    if lookup is None:
        return []
    ticker = str(leg.get("ticker") or "").upper().strip()
    if not ticker:
        return []
    try:
        frame = lookup(ticker)
    except Exception:
        return []
    if frame is None:
        return []
    stop_price: float | None
    try:
        raw_stop = leg.get("stop_price")
        stop_price = None if raw_stop in (None, "") else float(raw_stop)
    except (TypeError, ValueError):
        stop_price = None
    try:
        horizon = int(leg.get("horizon_days") or 0)
    except (TypeError, ValueError):
        horizon = 0
    walked = walk_long_position(
        frame,
        signal_date=str(leg.get("date") or ""),
        horizon_days=horizon,
        stop_price=stop_price,
    )
    if not walked or not walked.get("daily_returns"):
        return []
    return _stored_path({"daily": walked["daily_returns"]}, cost=cost, apply_cost=apply_cost)


def _resolve_leg(
    leg: dict[str, Any],
    *,
    cost: float,
    apply_cost: bool,
    calendar: _Calendar,
    lookup: Callable[[str], Any] | None,
) -> tuple[list[tuple[date, float]], str]:
    """A stored path, a cache path, a smear, or missing.

    The smear is the pre-cache fallback. Once a price cache is configured, a
    long that the cache cannot price is missing and must not be smeared.
    """
    if str(leg.get("decision") or "long") != "long":
        return [], "cash"
    stored = _stored_path(leg, cost=cost, apply_cost=apply_cost)
    if stored:
        return stored, "stored"
    cached = _cache_path(leg, lookup, cost=cost, apply_cost=apply_cost)
    if cached:
        return cached, "cache"
    if lookup is None:
        smeared = _smeared_path(leg, cost=cost, apply_cost=apply_cost, calendar=calendar)
        if smeared:
            return smeared, "smear"
    return [], "missing"


def _book_from_paths(
    paths: list[list[tuple[date, float]]],
    weights: list[float],
    cash_days: list[date],
    cash_daily: dict[str, float] | None,
    calendar: _Calendar,
) -> tuple[list[str], list[float], list[bool]]:
    by_day: dict[date, list[tuple[float, float]]] = {}
    invested_days: set[date] = set()
    for path, weight in zip(paths, weights, strict=False):
        for day, ret in path:
            if not calendar.is_session(day):
                continue
            by_day.setdefault(day, []).append((max(0.0, float(weight)), ret))
            invested_days.add(day)
    span_days = _session_span([day for path in paths for day, _ret in path] + cash_days, calendar)
    dates: list[str] = []
    returns: list[float] = []
    invested: list[bool] = []
    for day in span_days:
        key = day.isoformat()
        open_legs = by_day.get(day) or []
        if open_legs:
            weight_sum = sum(weight for weight, _ret in open_legs)
            if weight_sum <= 0:
                port = 0.0
            else:
                port = sum(weight * ret for weight, ret in open_legs) / weight_sum
        else:
            port = 0.0 if not cash_daily else float(cash_daily.get(key) or 0.0)
        dates.append(key)
        returns.append(port)
        invested.append(day in invested_days)
    return dates, returns, invested


def _metrics_from_daily(dates: list[str], returns: list[float]) -> dict[str, Any]:
    ratio, unbounded = _sortino_ratio(returns)
    annualized = None if ratio is None else round(ratio * ANNUALIZATION, 6)
    path = equity_path(returns)
    drawdown = drawdown_stats(path, dates)
    return {
        "sortino_daily": None if ratio is None else round(ratio, 6),
        "sortino": annualized,
        "primary_score": annualized,
        "primary_unbounded": unbounded,
        "max_drawdown": drawdown["max_drawdown"],
        "max_drawdown_duration_days": drawdown["max_drawdown_duration_days"],
        "max_drawdown_duration_trades": drawdown["max_drawdown_duration_trades"],
        "book_method": "daily_mtm",
        "sortino_unit": "annualized_daily",
        "daily_dates": dates,
        "daily_returns": [round(value, 8) for value in returns],
    }


def _spy_metrics(
    dates: list[str],
    spy_daily: dict[str, float] | None,
    calendar: _Calendar,
) -> dict[str, Any]:
    """SPY total return, annualized Sortino, and max DD on the book's sessions."""
    blank = {"spy_total_return": None, "spy_sortino": None, "spy_max_drawdown": None}
    if not dates or not spy_daily:
        return blank
    kept: list[str] = []
    returns: list[float] = []
    for day_text in dates:
        day = _parse_day(day_text)
        if day is not None and not calendar.is_session(day):
            continue
        if day_text not in spy_daily:
            return blank
        kept.append(day_text)
        returns.append(float(spy_daily[day_text]))
    if not returns:
        return blank
    ratio, _unbounded = _sortino_ratio(returns)
    drawdown = drawdown_stats(equity_path(returns), kept)
    equity = 1.0
    for value in returns:
        equity *= max(0.0, 1.0 + value)
    return {
        "spy_total_return": round(equity - 1.0, 6),
        "spy_sortino": None if ratio is None else round(ratio * ANNUALIZATION, 6),
        "spy_max_drawdown": drawdown["max_drawdown"],
    }


def _price_lookup(skill_dir: Path | str | None) -> Callable[[str], Any] | None:
    """Per-ticker frames from the backtest price cache. None when the cache is off."""
    if skill_dir is None:
        return None
    try:
        from backtest import read_price_cache_frame, resolve_price_cache_dir
        from config import get_backtest_price_cache_dir
    except Exception:
        return None
    cache_dir = resolve_price_cache_dir(get_backtest_price_cache_dir(Path(skill_dir)), Path(skill_dir))
    if cache_dir is None or not cache_dir.is_dir():
        return None
    frames: dict[str, Any] = {}

    def lookup(ticker: str) -> Any:
        key = str(ticker or "").upper().strip()
        if not key:
            return None
        if key not in frames:
            frames[key] = read_price_cache_frame(cache_dir, key)
        return frames[key]

    return lookup


def _clear_ratio(metrics: dict[str, Any], reason: str, *, clear_drawdown: bool) -> None:
    metrics["sortino"] = None
    metrics["sortino_daily"] = None
    metrics["primary_score"] = None
    metrics["primary_unbounded"] = False
    metrics["sortino_cost_10bps"] = None
    if clear_drawdown:
        metrics["max_drawdown"] = None
        metrics["max_drawdown_duration_days"] = None
        metrics["max_drawdown_duration_trades"] = None
        metrics["max_drawdown_cost_10bps"] = None
    metrics["not_scored_reason"] = reason
    metrics["sample_status"] = reason
    metrics["path_status"] = reason


def _near_zero_downside(returns: list[float], invested: list[bool], metrics: dict[str, Any]) -> bool:
    """Cash-only and near-zero-downside books must not print an inflated Sortino."""
    if not returns:
        return False
    invested_days = sum(1 for flag in invested if flag)
    if invested_days == 0:
        return True
    if metrics.get("primary_unbounded"):
        return False
    downside_obs = sum(1 for value in returns if value < -1e-4)
    sortino = metrics.get("sortino")
    if (
        len(returns) >= 20
        and invested_days < MIN_INVESTED_DAYS
        and downside_obs < MIN_DOWNSIDE_OBS
        and sortino is not None
        and float(sortino) > 0
    ):
        return True
    return False


def _prompt_stats(
    legs: list[dict[str, Any]],
    paths: list[list[tuple[date, float]]],
    *,
    spy_daily: dict[str, float] | None,
    pool: dict[str, list[float]] | None,
    calendar: _Calendar,
) -> tuple[dict[str, Any], dict[str, Any], int]:
    excess_values: list[float] = []
    excess_days: list[date] = []
    selection_values: list[float] = []
    selection_days: list[date] = []
    signal_weeks: set[str] = set()
    for leg, path in zip(legs, paths, strict=False):
        if str(leg.get("decision") or "long") != "long" or not path:
            continue
        signal = _parse_day(str(leg.get("date") or ""))
        if signal is None:
            signal = path[0][0]
        signal_weeks.add(_iso_week(signal))
        net = _compound([ret for day, ret in path if calendar.is_session(day)])
        if spy_daily is not None:
            spy_ret = 1.0
            covered = True
            saw = False
            for day, _ret in path:
                if not calendar.is_session(day):
                    continue
                key = day.isoformat()
                if key not in spy_daily:
                    covered = False
                    break
                saw = True
                spy_ret *= 1.0 + float(spy_daily[key])
            if covered and saw:
                excess_values.append(net - (spy_ret - 1.0))
                excess_days.append(signal)
        if pool is not None:
            peers = pool.get(signal.isoformat()) or []
            if peers:
                peer_mean = sum(float(value) for value in peers) / len(peers)
                selection_values.append(net - peer_mean / 100.0)
                selection_days.append(signal)
    return (
        week_cluster_stats(excess_values, excess_days),
        week_cluster_stats(selection_values, selection_days),
        len(signal_weeks),
    )


def score_legs(
    legs: list[dict[str, Any]],
    *,
    spy_daily: dict[str, float] | None = None,
    cash_daily: dict[str, float] | None = None,
    pool: dict[str, list[float]] | None = None,
    apply_cost: bool = True,
    cost: float = ROUND_TRIP_COST,
    sensitivity: float = ROUND_TRIP_COST_SENSITIVITY,
    skill_dir: Path | str | None = None,
    price_lookup: Callable[[str], Any] | None = None,
) -> dict[str, Any]:
    """Daily book for one set of long and cash legs. Returns are percents on each leg."""
    calendar = _Calendar(spy_daily)
    lookup = price_lookup if price_lookup is not None else _price_lookup(skill_dir)
    resolved = [_resolve_leg(leg, cost=cost, apply_cost=apply_cost, calendar=calendar, lookup=lookup) for leg in legs]
    paths = [path for path, _source in resolved]
    sources = [source for _path, source in resolved]
    weights = [float(leg.get("weight") or 1.0) for leg in legs]
    cash_days: list[date] = []
    for leg in legs:
        if str(leg.get("decision") or "") != "cash":
            continue
        day = _parse_day(str(leg.get("date") or ""))
        if day is None:
            continue
        session = calendar.roll(day)
        if session is not None:
            cash_days.append(session)
    dates, returns, invested = _book_from_paths(paths, weights, cash_days, cash_daily if cash_daily else None, calendar)
    metrics = _metrics_from_daily(dates, returns)
    if apply_cost and sensitivity != cost:
        sens_resolved = [
            _resolve_leg(leg, cost=sensitivity, apply_cost=True, calendar=calendar, lookup=lookup) for leg in legs
        ]
        sens_paths = [path for path, _source in sens_resolved]
        sens_dates, sens_returns, _sens_invested = _book_from_paths(
            sens_paths, weights, cash_days, cash_daily if cash_daily else None, calendar
        )
        sens = _metrics_from_daily(sens_dates, sens_returns)
        metrics["sortino_cost_10bps"] = sens.get("sortino")
        metrics["max_drawdown_cost_10bps"] = sens.get("max_drawdown")
    else:
        metrics["sortino_cost_10bps"] = metrics.get("sortino")
        metrics["max_drawdown_cost_10bps"] = metrics.get("max_drawdown")
    excess, selection, n_weeks = _prompt_stats(legs, paths, spy_daily=spy_daily, pool=pool, calendar=calendar)
    long_weight = 0.0
    missing_weight = 0.0
    for leg, source, weight in zip(legs, sources, weights, strict=False):
        if str(leg.get("decision") or "long") != "long":
            continue
        use = max(0.0, float(weight))
        if use <= 0:
            continue
        long_weight += use
        if source == "missing":
            missing_weight += use
    missing_share = (missing_weight / long_weight) if long_weight else 0.0
    metrics.update(
        {
            "round_trip_cost": cost if apply_cost else 0.0,
            "round_trip_cost_pct": round((cost if apply_cost else 0.0) * 100.0, 4),
            "cost_sensitivity": sensitivity,
            "excess_vs_spy": excess,
            "selection_vs_pool": selection,
            "n_weeks": n_weeks,
            "week_gate": "insufficient" if n_weeks < MIN_WEEKS else "ok",
            "sample_status": "insufficient" if n_weeks < MIN_WEEKS else "readable",
            "entry_convention": "next_open",
            "stop_fill": "min_stop_open",
            "path_coverage": None if not long_weight else round(1.0 - missing_share, 4),
            "invested_days": sum(1 for flag in invested if flag),
            **_spy_metrics(dates, spy_daily, calendar),
        }
    )
    if long_weight and missing_share >= MEANINGFUL_MISSING_PATH:
        _clear_ratio(metrics, MISSING_PATHS, clear_drawdown=True)
    elif _near_zero_downside(returns, invested, metrics):
        _clear_ratio(metrics, NEAR_ZERO_DOWNSIDE, clear_drawdown=False)
    return metrics


def overlay_daily_book(
    book: dict[str, Any],
    legs: list[dict[str, Any]],
    *,
    spy_daily: dict[str, float] | None = None,
    cash_daily: dict[str, float] | None = None,
    pool: dict[str, list[float]] | None = None,
    apply_cost: bool = True,
    skill_dir: Path | str | None = None,
    price_lookup: Callable[[str], Any] | None = None,
) -> dict[str, Any]:
    """Replace serial Sortino and max drawdown with the daily book. Keep win rate and n."""
    book["per_trade_sortino"] = book.get("sortino")
    book["per_trade_max_drawdown"] = book.get("max_drawdown")
    metrics = score_legs(
        legs,
        spy_daily=spy_daily,
        cash_daily=cash_daily,
        pool=pool,
        apply_cost=apply_cost,
        skill_dir=skill_dir,
        price_lookup=price_lookup,
    )
    smooth_dates = metrics.get("daily_dates") or []
    smooth_returns = metrics.get("daily_returns") or []
    book.update(metrics)
    if book.get("week_gate") == "insufficient":
        book["sufficient"] = False
    book["smoothness_daily"] = _smoothness(smooth_returns, smooth_dates)
    if book.get("smoothness") is None:
        book["smoothness"] = book.get("smoothness_daily")
    return book


def _smoothness(returns: list[float], dates: list[str]) -> float | None:
    del dates
    if len(returns) < 3:
        return None
    path = equity_path(returns)
    xs = list(range(len(path)))
    mean_x = sum(xs) / len(xs)
    mean_y = sum(path) / len(path)
    var_x = sum((x - mean_x) ** 2 for x in xs)
    if var_x <= 1e-18:
        return None
    cov = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, path, strict=False))
    slope = cov / var_x
    intercept = mean_y - slope * mean_x
    ss_tot = sum((y - mean_y) ** 2 for y in path)
    if ss_tot <= 1e-18:
        return 1.0
    ss_res = sum((y - (intercept + slope * x)) ** 2 for x, y in zip(xs, path, strict=False))
    return round(max(0.0, 1.0 - ss_res / ss_tot), 6)


def legs_from_series(
    returns_pct: list[float],
    dates: list[str],
    decisions: list[str] | None,
    *,
    weights: list[float] | None = None,
    horizon_days: int,
    dailies: list[Any] | None = None,
    tickers: list[str] | None = None,
    stops: list[Any] | None = None,
    apply_cost: bool = True,
) -> list[dict[str, Any]]:
    labels = list(decisions) if decisions is not None else ["long"] * len(returns_pct)
    while len(labels) < len(returns_pct):
        labels.append("long")
    legs: list[dict[str, Any]] = []
    for index, (ret, day) in enumerate(zip(returns_pct, dates, strict=False)):
        daily = None
        if dailies is not None and index < len(dailies):
            daily = dailies[index]
        ticker = "" if tickers is None or index >= len(tickers) else str(tickers[index] or "")
        stop = None if stops is None or index >= len(stops) else stops[index]
        legs.append(
            {
                "date": str(day or "")[:10],
                "ticker": ticker.upper().strip(),
                "return_pct": float(ret),
                "decision": "cash" if str(labels[index] or "") == "cash" else "long",
                "weight": 1.0 if weights is None or index >= len(weights) else float(weights[index]),
                "horizon_days": int(horizon_days),
                "daily": daily,
                "stop_price": stop,
                "cost_applied": not apply_cost,
            }
        )
    return legs


def cached_simple_returns(skill_dir: Path | str | None, symbol: str) -> dict[str, float] | None:
    """Close-to-close returns from the backtest price cache. No network call."""
    if skill_dir is None:
        return None
    try:
        from backtest import read_price_cache_frame, resolve_price_cache_dir
        from config import get_backtest_price_cache_dir
    except Exception:
        return None
    cache_dir = resolve_price_cache_dir(get_backtest_price_cache_dir(Path(skill_dir)), Path(skill_dir))
    if cache_dir is None:
        return None
    frame = read_price_cache_frame(cache_dir, symbol)
    if frame is None or getattr(frame, "empty", True) or "close" not in getattr(frame, "columns", []):
        return None
    closes: list[tuple[str, float]] = []
    for stamp, value in zip(frame.index, frame["close"].tolist(), strict=False):
        day = str(stamp)[:10]
        try:
            price = float(value)
        except (TypeError, ValueError):
            continue
        if len(day) < 10 or price <= 0 or math.isnan(price):
            continue
        closes.append((day, price))
    returns: dict[str, float] = {}
    previous: float | None = None
    for day, price in closes:
        if previous is not None and previous > 0:
            returns[day] = price / previous - 1.0
        previous = price
    return returns or None


def benchmark_series(skill_dir: Path | str | None) -> tuple[dict[str, float] | None, dict[str, float] | None]:
    """SPY and BIL daily simple returns from the cache. Either may be missing."""
    return cached_simple_returns(skill_dir, "SPY"), cached_simple_returns(skill_dir, "BIL")


def score_book_rows(
    rows: list[dict[str, Any]],
    *,
    config: ObjectiveConfig,
    skill_dir: Path | str | None = None,
    spy_daily: dict[str, float] | None = None,
    cash_daily: dict[str, float] | None = None,
    pool: dict[str, list[float]] | None = None,
    apply_cost: bool = True,
) -> dict[str, Any]:
    """Score rows after quarantine. A quarantined packet is not a leg."""
    from core.tab_agent_objective import evaluate_book

    kept, excluded = drop_excluded(rows, skill_dir=skill_dir)
    returns: list[float] = []
    dates: list[str] = []
    decisions: list[str] = []
    dailies: list[Any] = []
    tickers: list[str] = []
    stops: list[Any] = []
    for row in kept:
        try:
            ret = float(row.get("return_pct"))
        except (TypeError, ValueError):
            continue
        day = _day(row)
        if len(day) < 10:
            continue
        side = str(row.get("decision") or "long").lower()
        if side not in {"long", "cash"}:
            continue
        returns.append(0.0 if side == "cash" else ret)
        dates.append(day)
        decisions.append(side)
        dailies.append(row.get("daily"))
        tickers.append(_ticker(row))
        stops.append(row.get("stop_price"))
    book = evaluate_book(returns, dates, config=config, decisions=decisions)
    legs = legs_from_series(
        returns,
        dates,
        decisions,
        horizon_days=config.horizon_days,
        dailies=dailies,
        tickers=tickers,
        stops=stops,
        apply_cost=apply_cost,
    )
    overlay_daily_book(
        book,
        legs,
        spy_daily=spy_daily,
        cash_daily=cash_daily,
        pool=pool,
        apply_cost=apply_cost,
        skill_dir=skill_dir,
    )
    book["excluded"] = excluded
    book["tickers"] = tickers
    return book


def calibration_from_pairs(pairs: list[tuple[float, float, str]]) -> dict[str, Any]:
    """Brier score and a reliability table. ``pairs`` are (confidence, outcome, day)."""
    if not pairs:
        return {
            "n": 0,
            "scored": False,
            "not_scored_reason": "no_matured_packets",
            "primary_metric": "brier",
            "primary_score": None,
            "brier": None,
            "sortino": None,
            "sharpe": None,
            "max_drawdown": None,
            "scoring": "calibration",
            "lower_is_better": True,
            "reliability": [],
            "sample_status": "insufficient",
            "week_gate": "insufficient",
            "n_weeks": 0,
        }
    brier = sum((p - y) ** 2 for p, y, _day in pairs) / len(pairs)
    bins: dict[str, list[tuple[float, float]]] = {}
    for confidence, outcome, _day in pairs:
        lo = min(int(confidence / 0.1) * 0.1, 0.9)
        key = f"{lo:.1f}-{lo + 0.1:.1f}"
        bins.setdefault(key, []).append((confidence, outcome))
    reliability = []
    for key, rows in sorted(bins.items()):
        if len(rows) < 5:
            continue
        mean_p = sum(p for p, _y in rows) / len(rows)
        freq = sum(y for _p, y in rows) / len(rows)
        reliability.append(
            {
                "bin": key,
                "n": len(rows),
                "mean_confidence": round(mean_p, 4),
                "frequency": round(freq, 4),
                "gap": round(mean_p - freq, 4),
            }
        )
    days = [_parse_day(day) for _p, _y, day in pairs]
    valid_days = [day for day in days if day is not None]
    n_weeks = len({_iso_week(day) for day in valid_days})
    gate = "insufficient" if n_weeks < MIN_WEEKS else "ok"
    return {
        "n": len(pairs),
        "scored": True,
        "not_scored_reason": None,
        "primary_metric": "brier",
        "primary_score": round(brier, 6),
        "brier": round(brier, 6),
        "sortino": None,
        "sharpe": None,
        "max_drawdown": None,
        "scoring": "calibration",
        "lower_is_better": True,
        "reliability": reliability,
        "sample_status": "insufficient" if gate == "insufficient" else "readable",
        "week_gate": gate,
        "n_weeks": n_weeks,
        "sufficient": len(pairs) >= 1 and gate == "ok",
    }


def _next_open(frame: Any, signal_date: str) -> float | None:
    if frame is None or getattr(frame, "empty", True) or "open" not in getattr(frame, "columns", []):
        return None
    signal = _parse_day(signal_date)
    if signal is None:
        return None
    for stamp, value in zip(frame.index, frame["open"].tolist(), strict=False):
        day = _parse_day(str(stamp)[:10])
        if day is None or day <= signal:
            continue
        try:
            price = float(value)
        except (TypeError, ValueError):
            return None
        if price > 0 and not math.isnan(price):
            return price
        return None
    return None


def stop_for_book(packet: dict[str, Any], entry: float | None) -> float | None:
    """Absolute stop when one is stored. A percent stop is applied to the next open."""
    refs = packet.get("refs") if isinstance(packet.get("refs"), dict) else {}
    for raw in (refs.get("stop_price"), packet.get("stop_price")):
        try:
            if raw is None or raw == "":
                continue
            stop = float(raw)
        except (TypeError, ValueError):
            continue
        if stop > 0:
            return stop
    for raw in (refs.get("stop_pct"), packet.get("stop_pct")):
        try:
            if raw is None or raw == "":
                continue
            pct = float(raw)
        except (TypeError, ValueError):
            continue
        if pct <= 0 or entry is None or entry <= 0:
            continue
        frac = pct / 100.0 if pct > 1.0 else pct
        return entry * (1.0 - frac)
    return None


def attach_scale_out_label(
    marked: dict[str, Any],
    frame: Any,
    *,
    signal_date: str,
    horizon_days: int,
    packet: dict[str, Any] | None = None,
    stop_price: float | None = None,
) -> dict[str, Any]:
    """Record the entry convention and the cost the book will charge. Does not rewrite the stored return."""
    marked["entry_convention"] = "next_open"
    marked["realized_cost_pct"] = round(ROUND_TRIP_COST * 100.0, 4)
    marked["cost_sensitivity_pct"] = round(ROUND_TRIP_COST_SENSITIVITY * 100.0, 4)
    marked["stop_fill"] = "min_stop_open"
    resolved_stop = stop_price
    if resolved_stop is None and isinstance(packet, dict):
        resolved_stop = stop_for_book(packet, _next_open(frame, signal_date))
    walked = walk_long_position(
        frame,
        signal_date=signal_date,
        horizon_days=horizon_days,
        stop_price=resolved_stop,
    )
    if walked is None:
        marked["book_path"] = None
        return marked
    marked["daily_returns"] = walked["daily_returns"]
    marked["book_return_pct"] = walked["gross_return_pct"]
    marked["entry_price_used"] = round(float(walked["entry_price"]), 4)
    marked["book_stop_price"] = walked["stop_price"]
    marked["book_path"] = "next_open"
    return marked
