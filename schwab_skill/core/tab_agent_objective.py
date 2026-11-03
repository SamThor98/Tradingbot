"""Shared objective for every tab agent: risk-adjusted return and a smooth equity curve.

Win rate and average return are diagnostics. A candidate is preferred when its
Sortino (default) or Sharpe of primary scale-out returns is higher and its max
drawdown is not deeper than the absolute guardrail (default 20%). Drawdown
duration is reported and does not veto. Cash is a scored 0% outcome. Shorts
and options are out of the book. Market cap is not a penalty.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

OBJECTIVE_ID = "risk_adjusted_equity"
PRIMARY_METRICS = ("sortino", "sharpe")
POLICY_FILE = ".tab_agent_policy.json"
CASH_DECISIONS = {"cash", "no_trade", "pass", "abstain", "stay_in_cash"}
SHORT_DECISIONS = {"short", "option", "put", "call", "inverse"}

SIGNAL_PRIORITIES = (
    "Clean chart setup and price action, strong fundamentals, sector and market strength, "
    "and a tight stop with good reward-to-risk are high weight. A catalyst (earnings or news) "
    "is the lowest priority and only a tiebreaker."
)
LONG_ONLY_RULE = (
    "Long-only equities. Never suggest a short, an inverse ETF, or an option. "
    "No trade / stay in cash is a valid scored outcome in a weak regime: cash is a 0% return "
    "and it avoids drawdown."
)
LIQUIDITY_RULE = (
    "Do not penalize market cap. A name qualifies at any capitalization when it clears the "
    "liquidity gate (average dollar volume and spread). Tune those thresholds only inside "
    "the configured bounds. Do not impose a top-N; surface every name that clears the quality threshold."
)


@dataclass(frozen=True)
class ObjectiveConfig:
    metric: str = "sortino"
    min_samples: int = 30
    drawdown_tolerance: float = 0.20
    drawdown_duration_tolerance_days: int = 10
    horizon_days: int = 20
    min_avg_dollar_volume: float = 20_000_000.0
    max_spread_pct: float = 0.01
    adv_min_bound: float = 5_000_000.0
    adv_max_bound: float = 100_000_000.0
    spread_min_bound: float = 0.002
    spread_max_bound: float = 0.03
    quality_threshold: float = 60.0
    quality_min_bound: float = 40.0
    quality_max_bound: float = 90.0
    max_names: int = 0
    review_wait_hours: int = 48
    replay_weight: float = 0.5
    min_live_samples: int = 10

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": OBJECTIVE_ID,
            "metric": self.metric,
            "min_samples": self.min_samples,
            "drawdown_tolerance": self.drawdown_tolerance,
            "drawdown_guardrail": f"Block only when max drawdown is deeper than {self.drawdown_tolerance:.0%}.",
            "drawdown_duration_tolerance_days": self.drawdown_duration_tolerance_days,
            "horizon_days": self.horizon_days,
            "exit_model": "scale_out",
            "long_only": True,
            "cash_is_scored": True,
            "market_cap_penalty": False,
            "min_avg_dollar_volume": self.min_avg_dollar_volume,
            "max_spread_pct": self.max_spread_pct,
            "adv_bounds": [self.adv_min_bound, self.adv_max_bound],
            "spread_bounds": [self.spread_min_bound, self.spread_max_bound],
            "quality_threshold": self.quality_threshold,
            "quality_bounds": [self.quality_min_bound, self.quality_max_bound],
            "max_names": self.max_names,
            "max_names_unlimited": self.max_names <= 0,
            "review_wait_hours": self.review_wait_hours,
            "replay_weight": self.replay_weight,
            "min_live_samples": self.min_live_samples,
            "signal_priorities": SIGNAL_PRIORITIES,
            "statement": (
                f"Prefer the long-only book with the higher {self.metric} of {self.horizon_days}-day "
                "scale-out returns. Cash is a 0% outcome. Block a book only when max drawdown is "
                f"deeper than {self.drawdown_tolerance:.0%}. {SIGNAL_PRIORITIES} {LIQUIDITY_RULE}"
            ),
        }


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _read_policy(skill_dir: Path | None) -> dict[str, Any]:
    if skill_dir is None:
        return {}
    path = Path(skill_dir) / POLICY_FILE
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def load_objective(skill_dir: Path | None = None) -> ObjectiveConfig:
    from config import (
        get_decision_packet_horizon_days,
        get_tab_agent_adv_bounds,
        get_tab_agent_debate_max_names,
        get_tab_agent_drawdown_duration_tolerance_days,
        get_tab_agent_drawdown_tolerance,
        get_tab_agent_max_spread_pct,
        get_tab_agent_min_avg_dollar_volume,
        get_tab_agent_min_live_samples,
        get_tab_agent_min_samples,
        get_tab_agent_objective_metric,
        get_tab_agent_quality_bounds,
        get_tab_agent_quality_threshold,
        get_tab_agent_replay_weight,
        get_tab_agent_review_wait_hours,
        get_tab_agent_spread_bounds,
    )

    adv_low, adv_high = get_tab_agent_adv_bounds(skill_dir)
    spread_low, spread_high = get_tab_agent_spread_bounds(skill_dir)
    quality_low, quality_high = get_tab_agent_quality_bounds(skill_dir)
    policy = _read_policy(skill_dir)

    def _num(value: Any, default: float) -> float:
        try:
            if value is None or value == "":
                return default
            return float(value)
        except (TypeError, ValueError):
            return default

    adv = _num(policy.get("min_avg_dollar_volume"), get_tab_agent_min_avg_dollar_volume(skill_dir))
    spread = _num(policy.get("max_spread_pct"), get_tab_agent_max_spread_pct(skill_dir))
    quality = _num(policy.get("quality_threshold"), get_tab_agent_quality_threshold(skill_dir))
    return ObjectiveConfig(
        metric=get_tab_agent_objective_metric(skill_dir),
        min_samples=get_tab_agent_min_samples(skill_dir),
        drawdown_tolerance=get_tab_agent_drawdown_tolerance(skill_dir),
        drawdown_duration_tolerance_days=get_tab_agent_drawdown_duration_tolerance_days(skill_dir),
        horizon_days=get_decision_packet_horizon_days(skill_dir),
        min_avg_dollar_volume=_clamp(adv, adv_low, adv_high),
        max_spread_pct=_clamp(spread, spread_low, spread_high),
        adv_min_bound=adv_low,
        adv_max_bound=adv_high,
        spread_min_bound=spread_low,
        spread_max_bound=spread_high,
        quality_threshold=_clamp(quality, quality_low, quality_high),
        quality_min_bound=quality_low,
        quality_max_bound=quality_high,
        max_names=get_tab_agent_debate_max_names(skill_dir),
        review_wait_hours=get_tab_agent_review_wait_hours(skill_dir),
        replay_weight=get_tab_agent_replay_weight(skill_dir),
        min_live_samples=get_tab_agent_min_live_samples(skill_dir),
    )


def apply_policy_update(skill_dir: Path | str, proposal: dict[str, Any]) -> dict[str, Any]:
    """Clamp an agent proposal onto the liquidity and quality bounds and store it."""
    root = Path(skill_dir)
    current = load_objective(root)
    updated = {
        "min_avg_dollar_volume": current.min_avg_dollar_volume,
        "max_spread_pct": current.max_spread_pct,
        "quality_threshold": current.quality_threshold,
    }
    if proposal.get("min_avg_dollar_volume") is not None:
        updated["min_avg_dollar_volume"] = _clamp(
            float(proposal["min_avg_dollar_volume"]), current.adv_min_bound, current.adv_max_bound
        )
    if proposal.get("max_spread_pct") is not None:
        updated["max_spread_pct"] = _clamp(
            float(proposal["max_spread_pct"]), current.spread_min_bound, current.spread_max_bound
        )
    if proposal.get("quality_threshold") is not None:
        updated["quality_threshold"] = _clamp(
            float(proposal["quality_threshold"]), current.quality_min_bound, current.quality_max_bound
        )
    updated["market_cap_penalty"] = False
    updated["max_names"] = current.max_names
    path = root / POLICY_FILE
    path.write_text(json.dumps(updated, indent=2) + "\n", encoding="utf-8")
    return updated


def liquidity_decision(
    avg_dollar_volume: float | None,
    spread_pct: float | None,
    *,
    config: ObjectiveConfig,
) -> str:
    """Return pass, fail, or unknown. Market cap is not an input."""
    if avg_dollar_volume is None and spread_pct is None:
        return "unknown"
    if avg_dollar_volume is not None and float(avg_dollar_volume) < config.min_avg_dollar_volume:
        return "fail"
    if spread_pct is not None and float(spread_pct) > config.max_spread_pct:
        return "fail"
    if avg_dollar_volume is None or spread_pct is None:
        return "unknown"
    return "pass"


def clears_quality_bar(score: float | None, *, config: ObjectiveConfig) -> bool:
    if score is None:
        return False
    return float(score) >= float(config.quality_threshold)


def name_cap(config: ObjectiveConfig) -> int | None:
    """None means every name that clears the quality bar is kept."""
    if int(config.max_names) <= 0:
        return None
    return int(config.max_names)


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


def _weighted_stdev(values: list[float], weights: list[float]) -> float:
    weight_sum = sum(weights)
    if weight_sum <= 0 or len(values) < 2:
        return 0.0
    mean = sum(weight * value for weight, value in zip(weights, values, strict=False)) / weight_sum
    var = sum(weight * (value - mean) ** 2 for weight, value in zip(weights, values, strict=False)) / weight_sum
    return math.sqrt(var)


def _weighted_downside(values: list[float], weights: list[float]) -> float:
    weight_sum = sum(weights)
    if weight_sum <= 0:
        return 0.0
    squares = sum(weight * min(value, 0.0) ** 2 for weight, value in zip(weights, values, strict=False))
    return math.sqrt(squares / weight_sum)


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
    weights: list[float] | None = None,
    sources: list[str] | None = None,
) -> dict[str, Any]:
    """Score one ordered book of per-trade percent returns.

    ``sources`` marks ``replay`` rows. They count toward ``n`` and ``min_samples``.
    Their equity and ratio contribution is ``weight`` (default 1, live). A weight
    below 1 pulls the mean toward the live rows.
    """
    raw_weights = list(weights) if weights is not None else [1.0] * len(returns_pct)
    raw_sources = list(sources) if sources is not None else ["live"] * len(returns_pct)
    while len(raw_weights) < len(returns_pct):
        raw_weights.append(1.0)
    while len(raw_sources) < len(returns_pct):
        raw_sources.append("live")
    triples = list(zip(dates or [""] * len(returns_pct), returns_pct, raw_weights, raw_sources, strict=False))
    triples.sort(key=lambda item: (item[0] or "",))
    ordered_dates = [item[0] for item in triples]
    fractions = [float(item[1]) / 100.0 for item in triples]
    weights_sorted = [max(0.0, float(item[2])) for item in triples]
    sources_sorted = ["replay" if str(item[3]) == "replay" else "live" for item in triples]
    n = len(fractions)
    n_replay = sum(1 for source in sources_sorted if source == "replay")
    n_live = n - n_replay
    wins = sum(1 for value in fractions if value > 0.005)
    uniform = weights is None or all(abs(weight - 1.0) <= 1e-9 for weight in weights_sorted)
    if uniform or n == 0:
        mean = sum(fractions) / n if n else 0.0
        sharpe = _ratio(mean, _stdev(fractions)) if n else None
        sortino = _ratio(mean, _downside_deviation(fractions)) if n else None
        path = equity_path(fractions)
    else:
        weight_sum = sum(weights_sorted)
        mean = (
            sum(weight * value for weight, value in zip(weights_sorted, fractions, strict=False)) / weight_sum
            if weight_sum
            else 0.0
        )
        sharpe = _ratio(mean, _weighted_stdev(fractions, weights_sorted)) if n else None
        sortino = _ratio(mean, _weighted_downside(fractions, weights_sorted)) if n else None
        path = equity_path([value * weight for value, weight in zip(fractions, weights_sorted, strict=False)])
    primary = sortino if config.metric == "sortino" else sharpe
    dd = drawdown_stats(path, ordered_dates)
    return {
        "n": n,
        "n_live": n_live,
        "n_replay": n_replay,
        "replay_weight": config.replay_weight,
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


def _replay_without_live_floor(stats: dict[str, Any], config: ObjectiveConfig) -> bool:
    """Replay can fill min_samples. Promotion still needs the live floor."""
    if int(stats.get("n_replay") or 0) <= 0:
        return False
    return int(stats.get("n_live") or 0) < int(config.min_live_samples)


def compare_books(
    candidate: dict[str, Any],
    baseline: dict[str, Any] | None,
    *,
    config: ObjectiveConfig,
) -> str:
    """Return the preference label. Never an instruction to promote."""
    if baseline is None or candidate.get("n", 0) < config.min_samples or baseline.get("n", 0) < config.min_samples:
        return "insufficient_sample"
    if _replay_without_live_floor(candidate, config) or _replay_without_live_floor(baseline, config):
        return "insufficient_live_sample"
    if _primary_rank(candidate) <= _primary_rank(baseline):
        if _primary_rank(candidate) < _primary_rank(baseline):
            return "worse_than_live"
        return "no_separation"
    cand_dd = float(candidate.get("max_drawdown") or 0.0)
    # Absolute guardrail. A 8% dip does not lose to a 2% dip when Sortino is higher.
    if cand_dd < -float(config.drawdown_tolerance):
        return "drawdown_worse"
    return "better_than_live"
