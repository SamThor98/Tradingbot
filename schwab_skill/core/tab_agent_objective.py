"""Shared objective for every tab agent: risk-adjusted return and a smooth equity curve.

Win rate and average return are diagnostics. A candidate is preferred only when
its configured risk-adjusted score (Sortino by default, or Sharpe) is higher
and max drawdown depth and duration do not worsen beyond the configured
tolerances. ``auto_promote`` is never set here.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

OBJECTIVE_ID = "risk_adjusted_equity"
PRIMARY_METRICS = ("sortino", "sharpe")


@dataclass(frozen=True)
class ObjectiveConfig:
    metric: str = "sortino"
    min_samples: int = 30
    drawdown_tolerance: float = 0.02
    drawdown_duration_tolerance_days: int = 10
    horizon_days: int = 10

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": OBJECTIVE_ID,
            "metric": self.metric,
            "min_samples": self.min_samples,
            "drawdown_tolerance": self.drawdown_tolerance,
            "drawdown_duration_tolerance_days": self.drawdown_duration_tolerance_days,
            "horizon_days": self.horizon_days,
            "statement": (
                "Prefer the prompt book with the higher risk-adjusted score of per-trade "
                f"{self.horizon_days}-day returns ({self.metric}) whose max drawdown and "
                "drawdown duration are not materially worse."
            ),
        }


def load_objective(skill_dir: Path | None = None) -> ObjectiveConfig:
    from config import (
        get_decision_packet_horizon_days,
        get_tab_agent_drawdown_duration_tolerance_days,
        get_tab_agent_drawdown_tolerance,
        get_tab_agent_min_samples,
        get_tab_agent_objective_metric,
    )

    return ObjectiveConfig(
        metric=get_tab_agent_objective_metric(skill_dir),
        min_samples=get_tab_agent_min_samples(skill_dir),
        drawdown_tolerance=get_tab_agent_drawdown_tolerance(skill_dir),
        drawdown_duration_tolerance_days=get_tab_agent_drawdown_duration_tolerance_days(skill_dir),
        horizon_days=get_decision_packet_horizon_days(skill_dir),
    )


def _stdev(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    var = sum((value - mean) ** 2 for value in values) / (len(values) - 1)
    return math.sqrt(var)


def _downside_deviation(values: list[float]) -> float:
    if not values:
        return 0.0
    squares = [min(value, 0.0) ** 2 for value in values]
    return math.sqrt(sum(squares) / len(squares))


def _ratio(mean: float, scale: float) -> float | None:
    if scale > 1e-12:
        return mean / scale
    if mean > 0:
        return None
    return 0.0


def _parse_day(raw: str | None) -> datetime | None:
    text = str(raw or "").strip()[:10]
    if len(text) < 10:
        return None
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def equity_path(returns: list[float]) -> list[float]:
    """Compound per-trade simple returns. ``returns`` are fractions, not percents."""
    equity = 1.0
    path = [equity]
    for ret in returns:
        equity *= max(0.0, 1.0 + float(ret))
        path.append(equity)
    return path


def drawdown_stats(path: list[float], dates: list[str] | None = None) -> dict[str, Any]:
    """Max peak-to-trough drawdown and the longest underwater stretch."""
    if len(path) < 2:
        return {"max_drawdown": 0.0, "max_drawdown_duration_days": 0, "max_drawdown_duration_trades": 0}
    peak = path[0]
    peak_i = 0
    max_dd = 0.0
    max_trade_len = 0
    max_day_len = 0
    underwater_from: int | None = None
    stamps = [_parse_day(day) for day in (dates or [])]

    def _close_span(start: int, end: int) -> None:
        nonlocal max_trade_len, max_day_len
        max_trade_len = max(max_trade_len, end - start)
        start_dt = stamps[start] if start < len(stamps) else None
        end_dt = stamps[min(end, len(stamps) - 1)] if stamps else None
        if start_dt is not None and end_dt is not None:
            max_day_len = max(max_day_len, (end_dt - start_dt).days)

    for i in range(1, len(path)):
        if path[i] >= peak:
            if underwater_from is not None:
                _close_span(underwater_from, i)
                underwater_from = None
            peak = path[i]
            peak_i = i
            continue
        dd = path[i] / peak - 1.0 if peak > 0 else -1.0
        max_dd = min(max_dd, dd)
        if underwater_from is None:
            underwater_from = peak_i
    if underwater_from is not None:
        _close_span(underwater_from, len(path) - 1)
    return {
        "max_drawdown": round(max_dd, 6),
        "max_drawdown_duration_days": int(max_day_len),
        "max_drawdown_duration_trades": int(max_trade_len),
    }


def evaluate_book(
    returns_pct: list[float],
    dates: list[str] | None = None,
    *,
    config: ObjectiveConfig,
) -> dict[str, Any]:
    """Score one ordered book of per-trade percent returns."""
    pairs = list(zip(dates or [""] * len(returns_pct), returns_pct, strict=False))
    pairs.sort(key=lambda item: (item[0] or "",))
    ordered_dates = [item[0] for item in pairs]
    fractions = [float(item[1]) / 100.0 for item in pairs]
    n = len(fractions)
    wins = sum(1 for value in fractions if value > 0.005)
    mean = sum(fractions) / n if n else 0.0
    sharpe = _ratio(mean, _stdev(fractions)) if n else None
    sortino = _ratio(mean, _downside_deviation(fractions)) if n else None
    primary = sortino if config.metric == "sortino" else sharpe
    path = equity_path(fractions)
    dd = drawdown_stats(path, ordered_dates)
    return {
        "n": n,
        "sufficient": n >= config.min_samples,
        "primary_metric": config.metric,
        "primary_score": None if primary is None else round(primary, 6),
        "primary_unbounded": primary is None and mean > 0 and n > 0,
        "sharpe": None if sharpe is None else round(sharpe, 6),
        "sortino": None if sortino is None else round(sortino, 6),
        "max_drawdown": dd["max_drawdown"],
        "max_drawdown_duration_days": dd["max_drawdown_duration_days"],
        "max_drawdown_duration_trades": dd["max_drawdown_duration_trades"],
        "win_rate": round(wins / n, 4) if n else None,
        "avg_realized_return_pct": round(mean * 100.0, 4) if n else None,
        "horizon_days": config.horizon_days,
    }


def _primary_rank(stats: dict[str, Any]) -> tuple[int, float]:
    """Higher is better. Unbounded positive ratios outrank every finite score."""
    if stats.get("primary_unbounded"):
        return (1, float(stats.get("avg_realized_return_pct") or 0.0))
    score = stats.get("primary_score")
    if score is None:
        return (0, float("-inf"))
    return (0, float(score))


def compare_books(
    candidate: dict[str, Any],
    baseline: dict[str, Any] | None,
    *,
    config: ObjectiveConfig,
) -> str:
    """Return the preference label. Never an instruction to promote."""
    if baseline is None or candidate.get("n", 0) < config.min_samples or baseline.get("n", 0) < config.min_samples:
        return "insufficient_sample"
    if _primary_rank(candidate) <= _primary_rank(baseline):
        if _primary_rank(candidate) < _primary_rank(baseline):
            return "worse_than_live"
        return "no_separation"
    cand_dd = float(candidate.get("max_drawdown") or 0.0)
    base_dd = float(baseline.get("max_drawdown") or 0.0)
    # More negative is worse. Tolerance is an absolute equity fraction.
    if cand_dd < base_dd - float(config.drawdown_tolerance):
        return "drawdown_worse"
    cand_days = int(candidate.get("max_drawdown_duration_days") or 0)
    base_days = int(baseline.get("max_drawdown_duration_days") or 0)
    if cand_days > base_days + int(config.drawdown_duration_tolerance_days):
        return "drawdown_duration_worse"
    return "better_than_live"
