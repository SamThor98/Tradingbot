"""Pure helpers for strategy promotion gate evaluation.

Shared between:

- ``scripts/validate_pf_robustness.py`` (the runnable gate validator)
- ``tests/test_strategy_promotion_gates.py`` (boundary/regression tests)

Keeping the logic here as a small pure-function module makes it possible to
unit-test gate boundaries without spinning up a full backtest. The functions
operate on plain dictionaries that match the artifact shape emitted by
``optimize_strategy_loop.py`` and ``validate_pf_robustness._run_profile``.

Goal profile (locked):
- Risk-adjusted quality > return > drawdown > cross-era consistency.
- Adaptive regime behaviour (size down + stricter quality, not full off).
- Balanced throughput (no zero-trade eras).
- Moderate promotion strictness (defaults below).

These constants are used as defaults but are configurable via CLI/env so
operators can tighten/loosen without code changes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

# Locked goal-profile defaults. Mirror these in CLI defaults so that
# `--help` shows the canonical values, and override sites only need to
# pass values when they want to deviate.
DEFAULT_MIN_PF_DELTA: float = 0.02
DEFAULT_MIN_EXPECTANCY_DELTA: float = 0.00
DEFAULT_MIN_OOS_PF: float = 1.15
DEFAULT_MIN_OOS_PF_DELTA: float = 0.01
DEFAULT_MAX_DD_DEGRADE_CAP_PCT: float = 2.0
DEFAULT_MIN_TRADES_THRESHOLD: int = 35
# Per-era throughput floor (sparse-activity rejection). Defaults to half
# the aggregate floor so an individual era can dip but never collapse.
DEFAULT_MIN_TRADES_PER_ERA: int = 20
# Sample-size discipline: bucket-level stats below this N are flagged as
# low-confidence and must not drive hard policy rejections by themselves.
DEFAULT_LOW_CONFIDENCE_N: int = 30


@dataclass(frozen=True)
class PromotionGates:
    """Configurable thresholds for strategy promotion decisions.

    Defaults match the locked goal profile (moderate strictness, balanced
    throughput, drawdown tolerance ~16%). All fields are exposed via CLI on
    ``decide_strategy_promotion.py`` and ``validate_pf_robustness.py``.
    """

    min_pf_delta: float = DEFAULT_MIN_PF_DELTA
    min_expectancy_delta: float = DEFAULT_MIN_EXPECTANCY_DELTA
    min_oos_pf: float = DEFAULT_MIN_OOS_PF
    min_oos_pf_delta: float = DEFAULT_MIN_OOS_PF_DELTA
    max_drawdown_degrade_cap: float = DEFAULT_MAX_DD_DEGRADE_CAP_PCT
    min_trades_threshold: int = DEFAULT_MIN_TRADES_THRESHOLD
    min_trades_per_era: int = DEFAULT_MIN_TRADES_PER_ERA

    def as_dict(self) -> dict[str, float | int]:
        return {
            "min_pf_delta": float(self.min_pf_delta),
            "min_expectancy_delta": float(self.min_expectancy_delta),
            "min_oos_pf": float(self.min_oos_pf),
            "min_oos_pf_delta": float(self.min_oos_pf_delta),
            "max_drawdown_degrade_cap": float(self.max_drawdown_degrade_cap),
            "min_trades_threshold": int(self.min_trades_threshold),
            "min_trades_per_era": int(self.min_trades_per_era),
        }


@dataclass
class GateResult:
    """Outcome of evaluating promotion gates against champion vs challenger."""

    passed: bool
    reasons: list[str] = field(default_factory=list)
    comparison: dict[str, Any] = field(default_factory=dict)
    per_era: list[dict[str, Any]] = field(default_factory=list)


def _f(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _i(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def _aggregates_or(profile: Mapping[str, Any]) -> Mapping[str, Any]:
    agg = profile.get("aggregates") if isinstance(profile, Mapping) else None
    return agg if isinstance(agg, Mapping) else {}


def _windows(profile: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    rows = profile.get("windows") if isinstance(profile, Mapping) else None
    if isinstance(rows, list):
        return [r for r in rows if isinstance(r, Mapping)]
    # Optimizer artifacts use "splits" instead of "windows".
    rows = profile.get("splits") if isinstance(profile, Mapping) else None
    if isinstance(rows, list):
        return [r for r in rows if isinstance(r, Mapping)]
    return []


def detect_zero_trade_eras(windows: Iterable[Mapping[str, Any]]) -> list[str]:
    """Return the names of any windows/eras with zero trades.

    A zero-trade era is a hard rejection regardless of other thresholds —
    a config that goes silent in any backtest era is dead-on-arrival,
    not "selective". This is the anti-dead-run gate.
    """
    out: list[str] = []
    for row in windows:
        trades = _i(row.get("total_trades"), 0)
        if trades <= 0:
            name = str(row.get("name") or row.get("start_date") or "?")
            out.append(name)
    return out


def detect_sparse_eras(windows: Iterable[Mapping[str, Any]], *, min_trades_per_era: int) -> list[dict[str, Any]]:
    """Return rows for windows whose trade count falls below the floor.

    Used to enforce the balanced-throughput policy: even if no era is
    fully dead, a config that consistently produces too-thin samples
    cannot be trusted as a promotion candidate.
    """
    out: list[dict[str, Any]] = []
    for row in windows:
        trades = _i(row.get("total_trades"), 0)
        if 0 < trades < int(min_trades_per_era):
            out.append(
                {
                    "name": str(row.get("name") or row.get("start_date") or "?"),
                    "total_trades": trades,
                    "min_required": int(min_trades_per_era),
                }
            )
    return out


def compare_aggregates(champion: Mapping[str, Any], challenger: Mapping[str, Any]) -> dict[str, Any]:
    """Champion vs challenger summary aggregates, in the shape used by
    promotion-decision artifacts. Accepts both ``pf_net_mean`` (validate
    artifacts) and ``pf_mean`` (optimizer artifacts) for resilience.
    """
    ca = _aggregates_or(champion)
    na = _aggregates_or(challenger)
    pf_champ = _f(ca.get("pf_net_mean", ca.get("pf_mean")))
    pf_chall = _f(na.get("pf_net_mean", na.get("pf_mean")))
    exp_champ = _f(ca.get("expectancy_net_mean", ca.get("expectancy_mean")))
    exp_chall = _f(na.get("expectancy_net_mean", na.get("expectancy_mean")))
    oos_pf_champ = _f(ca.get("oos_pf_net", ca.get("oos_pf")))
    oos_pf_chall = _f(na.get("oos_pf_net", na.get("oos_pf")))
    dd_champ = _f(ca.get("max_drawdown_net_worst", ca.get("drawdown_worst")))
    dd_chall = _f(na.get("max_drawdown_net_worst", na.get("drawdown_worst")))
    trades_champ = _i(ca.get("trades_min"))
    trades_chall = _i(na.get("trades_min"))
    return {
        "pf_delta": round(pf_chall - pf_champ, 6),
        "expectancy_delta": round(exp_chall - exp_champ, 6),
        "oos_pf": round(oos_pf_chall, 6),
        "oos_pf_delta": round(oos_pf_chall - oos_pf_champ, 6),
        "drawdown_delta": round(dd_chall - dd_champ, 6),
        "trades_min": int(trades_chall),
        "trades_min_delta": int(trades_chall) - int(trades_champ),
        "champion_drawdown": round(dd_champ, 6),
        "challenger_drawdown": round(dd_chall, 6),
    }


def evaluate_promotion_gates(
    champion: Mapping[str, Any],
    challenger: Mapping[str, Any],
    *,
    gates: PromotionGates,
) -> GateResult:
    """Evaluate the locked promotion gates against champion/challenger.

    Returns a :class:`GateResult` whose ``reasons`` list is human-readable
    and machine-parseable (each reason starts with a stable token used by
    decision artifacts and dashboards). When ``passed`` is True the
    challenger has cleared every gate; otherwise the reasons enumerate
    every failing condition (we intentionally do NOT short-circuit so
    the operator gets the full picture, not just the first failure).

    Order of evaluation:
    1. Zero-trade era hard rejection (anti-dead-run gate).
    2. Per-era throughput floor (balanced-throughput policy).
    3. Aggregate trade-count floor.
    4. PF improvement.
    5. Expectancy improvement.
    6. OOS PF floor + delta.
    7. Drawdown degradation cap.
    """
    challenger_windows = _windows(challenger)
    comparison = compare_aggregates(champion, challenger)
    reasons: list[str] = []
    passed = True

    dead_eras = detect_zero_trade_eras(challenger_windows)
    if dead_eras:
        passed = False
        reasons.append("zero_trade_era_detected:" + ",".join(dead_eras))

    sparse_eras = detect_sparse_eras(challenger_windows, min_trades_per_era=int(gates.min_trades_per_era))
    if sparse_eras:
        passed = False
        for row in sparse_eras:
            reasons.append(
                f"era_trade_count_too_low:{row['name']}:{int(row['total_trades'])}<{int(row['min_required'])}"
            )

    trades_min = int(comparison["trades_min"])
    if trades_min < int(gates.min_trades_threshold):
        passed = False
        reasons.append(f"trades_min_too_low:{trades_min}<{int(gates.min_trades_threshold)}")

    pf_delta = float(comparison["pf_delta"])
    if pf_delta < float(gates.min_pf_delta):
        passed = False
        reasons.append(f"pf_delta_too_small:{pf_delta:.6f}<{float(gates.min_pf_delta):.6f}")

    exp_delta = float(comparison["expectancy_delta"])
    if exp_delta < float(gates.min_expectancy_delta):
        passed = False
        reasons.append(f"expectancy_delta_too_small:{exp_delta:.6f}<{float(gates.min_expectancy_delta):.6f}")

    oos_pf = float(comparison["oos_pf"])
    if oos_pf < float(gates.min_oos_pf):
        passed = False
        reasons.append(f"oos_pf_below_floor:{oos_pf:.6f}<{float(gates.min_oos_pf):.6f}")

    oos_pf_delta = float(comparison["oos_pf_delta"])
    if oos_pf_delta < float(gates.min_oos_pf_delta):
        passed = False
        reasons.append(f"oos_pf_delta_too_small:{oos_pf_delta:.6f}<{float(gates.min_oos_pf_delta):.6f}")

    # Drawdowns are stored as negative percentages; magnitude rises as
    # things get worse. The cap is an absolute %-point increase vs champion.
    champ_dd = abs(min(0.0, float(comparison["champion_drawdown"])))
    chall_dd = abs(min(0.0, float(comparison["challenger_drawdown"])))
    cap = champ_dd + float(gates.max_drawdown_degrade_cap)
    if chall_dd > cap:
        passed = False
        reasons.append(f"drawdown_degraded_too_much:{chall_dd:.4f}>{cap:.4f}")

    if not reasons:
        reasons.append("challenger_meets_walkforward_promotion_gates")

    per_era = [
        {
            "name": str(row.get("name") or row.get("start_date") or "?"),
            "total_trades": _i(row.get("total_trades"), 0),
            "profit_factor_net": _f(row.get("profit_factor_net", row.get("profit_factor"))),
            "max_drawdown_net_pct": _f(row.get("max_drawdown_net_pct", row.get("max_drawdown_pct"))),
            "is_zero_trade": _i(row.get("total_trades"), 0) <= 0,
            "is_sparse": (0 < _i(row.get("total_trades"), 0) < int(gates.min_trades_per_era)),
        }
        for row in challenger_windows
    ]

    return GateResult(passed=passed, reasons=reasons, comparison=comparison, per_era=per_era)


def annotate_bucket_confidence(
    rows: list[dict[str, Any]],
    *,
    n_field: str = "count",
    min_n: int = DEFAULT_LOW_CONFIDENCE_N,
) -> list[dict[str, Any]]:
    """Tag bucket-level diagnostic rows with low-confidence metadata.

    Used by ``analyze_guardrails.py`` and other diagnostics that emit
    bucketed stats (e.g. signal-score buckets, VCP-ratio buckets). The
    returned rows are mutated in place and also returned for chaining.

    Adds two fields per row:

    - ``low_confidence``: True when ``count < min_n``.
    - ``confidence_metadata``: ``{"min_n": int, "n": int, "policy": str}``
      so downstream consumers know why the flag was set.

    Low-confidence buckets MUST NOT be the sole driver of a promotion
    rejection or auto-tuning decision (project convention: small samples
    are observed but not gated on).
    """
    for row in rows:
        n = _i(row.get(n_field), 0)
        is_low = n < int(min_n)
        row["low_confidence"] = bool(is_low)
        row["confidence_metadata"] = {
            "min_n": int(min_n),
            "n": int(n),
            "policy": "low_n_no_hard_decisions",
        }
    return rows
