"""Offline search for entry filters on a stored trade pool.

The search reads trades that are already on disk. It does not call a model,
the network, or a market-data provider, and it does not change stops, orders,
or the live scan. A rule that passes is reported. The shadow filter can log
it later. Nothing here drops a scan row.

Named rules use the same comparisons as ``core.shadow_setup_filter``:

- R1: ``signal_score`` <= configured max (default 36)
- R1b: fraction below the 52-week high >= configured min (default 0.03)
- R2: ``sector_breadth50`` <= configured max (default 0.75)
- R4: ATR as a percent of close >= configured min (default 2.14)
- R3: R1 and R1b and R2
- R3+R4: R3 and R4

``atr_pct`` is percent of close after normalization, matching the shadow
filter. A stored fraction (0.0214) is scaled to 2.14. ``pct_from_52w_high``
(close / 52-week high) is converted to the fraction below that high.

Holdout: entry dates before 2022-01-01 are discovery. Entry dates on or after
that day are the mandatory out-of-sample check and are not used to choose
thresholds or to test neighbor stability. A rule passes only when that
holdout still beats the baseline after a multiple-testing haircut, a majority
of voting eras agree, the sample is large enough, and the neighboring
thresholds also beat the discovery baseline.
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd

LOG = logging.getLogger(__name__)

OOS_START = "2022-01-01"
VOTING_ERAS = ("volatility_chop", "crash_recovery", "bear_rates", "recent_current")
DISCOVERY_ERAS = ("late_bull", "volatility_chop", "crash_recovery")
ERA_BOUNDS: tuple[tuple[str, str, str | None], ...] = (
    ("late_bull", "2015-01-01", "2018-01-01"),
    ("volatility_chop", "2018-01-01", "2020-01-01"),
    ("crash_recovery", "2020-01-01", "2022-01-01"),
    ("bear_rates", "2022-01-01", "2024-01-01"),
    ("recent_current", "2024-01-01", None),
)
KNOWN_ERAS = {name for name, _start, _end in ERA_BOUNDS}

PRIMARY_GRIDS: dict[str, tuple[str, tuple[float, ...]]] = {
    "signal_score": ("<=", (30.0, 32.0, 34.0, 36.0, 38.0, 40.0, 42.0)),
    "pct_below_52w_high": (">=", (0.01, 0.02, 0.03, 0.04, 0.05, 0.08)),
    "sector_breadth50": ("<=", (0.55, 0.65, 0.70, 0.75, 0.80, 0.85, 0.95)),
    "atr_pct": (">=", (1.5, 1.8, 2.0, 2.14, 2.5, 3.0, 3.5)),
}
ONE_SIDED_EXTRAS = {"pts_52w": "<="}
BOTH_SIDED_EXTRAS = (
    "pts_sma",
    "pts_volume",
    "close_vs_sma200_pct",
    "expansion_volume_ratio",
    "distance_above_sma50",
    "breakout_buffer_pct",
)
QUANTILES = (0.2, 0.4, 0.6, 0.8)
LEAKAGE_COLUMNS = {
    "return",
    "net_return",
    "r_multiple",
    "mfe",
    "mae",
    "exit_price",
    "exit_date",
    "exit_reason",
    "hold_days",
    "pnl",
    "ohlc_path",
    "qty",
    "qty_estimate",
    "stop_pct",
    "entry_price",
    "entry_price_ref",
    "exit_day",
    "entry_day",
}
NESTED_KEYS = ("score_components", "telemetry", "features", "shadow_setup")
RECOMMENDED_NAME = "recommended_rules.json"


@dataclass(frozen=True)
class Clause:
    feature: str
    op: str
    threshold: float

    def label(self) -> str:
        return f"{self.feature}{self.op}{_num(self.threshold)}"


@dataclass(frozen=True)
class Rule:
    clauses: tuple[Clause, ...]
    name: str = ""
    family: str = "grid"

    def __post_init__(self) -> None:
        ordered = tuple(sorted(self.clauses, key=lambda clause: (clause.feature, clause.op, clause.threshold)))
        object.__setattr__(self, "clauses", ordered)

    @property
    def rule_id(self) -> str:
        return " & ".join(clause.label() for clause in self.clauses)


@dataclass(frozen=True)
class SearchGates:
    min_oos_n: int = 200
    min_discovery_n: int = 150
    min_era_n: int = 80
    era_majority: float = 0.6
    min_voting_eras: int = 3
    oos_start: str = OOS_START


def _num(value: float) -> str:
    text = f"{float(value):.6f}".rstrip("0").rstrip(".")
    return text if text not in {"", "-0"} else "0"


def _finite(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number


def _norm_ppf(p: float) -> float:
    """Inverse standard normal CDF (Acklam's approximation)."""
    if p <= 0.0:
        return -math.inf
    if p >= 1.0:
        return math.inf
    a = (
        -3.969683028665376e01,
        2.209460984245205e02,
        -2.759285104469687e02,
        1.383577518672690e02,
        -3.066479806614716e01,
        2.506628277459239e00,
    )
    b = (
        -5.447609879822406e01,
        1.615858368580409e02,
        -1.556989798598866e02,
        6.680131188771972e01,
        -1.328068155288572e01,
    )
    c = (
        -7.784894002430293e-03,
        -3.223964580411365e-01,
        -2.400758277161838e00,
        -2.549732539343734e00,
        4.374664141464968e00,
        2.938163982698783e00,
    )
    d = (
        7.784695709041462e-03,
        3.224671290700398e-01,
        2.445134137142996e00,
        3.754408661907416e00,
    )
    plow = 0.02425
    phigh = 1.0 - plow
    if p < plow:
        q = math.sqrt(-2.0 * math.log(p))
        return (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / (
            (((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1.0
        )
    if p > phigh:
        q = math.sqrt(-2.0 * math.log(1.0 - p))
        return -(((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / (
            (((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1.0
        )
    q = p - 0.5
    r = q * q
    return (
        (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5])
        * q
        / (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1.0)
    )


def expected_max_z(n_trials: int) -> float:
    """Expected maximum of ``n_trials`` i.i.d. standard normals.

    One trial has expectation 0, so a single pre-registered rule is not
    haircut. The value is the average of the inverse CDF of the maximum.
    """
    n = max(int(n_trials), 1)
    if n == 1:
        return 0.0
    steps = 64
    total = 0.0
    for k in range(1, steps + 1):
        u = (k - 0.5) / steps
        total += _norm_ppf(u ** (1.0 / n))
    return total / steps


def _flatten_trade(raw: dict[str, Any], era: str) -> dict[str, Any]:
    out = dict(raw)
    for key in NESTED_KEYS:
        nested = raw.get(key)
        if isinstance(nested, dict):
            for child, value in nested.items():
                out.setdefault(str(child), value)
    if era and not out.get("era"):
        out["era"] = era
    return out


def load_trade_records(path: Path) -> list[dict[str, Any]]:
    """Read a chunk directory, a JSON list, or JSONL. Missing path yields []."""
    target = Path(path)
    if not target.exists():
        return []
    rows: list[dict[str, Any]] = []
    if target.is_file():
        return _read_trade_file(target, era="")
    files = sorted(target.rglob("chunk_*.json"))
    if not files and target.suffix.lower() in {".json", ".jsonl"}:
        return _read_trade_file(target, era="")
    for file in files:
        if file.name.endswith("_tickers.json"):
            continue
        era = file.parent.name if file.parent.name in KNOWN_ERAS else ""
        rows.extend(_read_trade_file(file, era=era))
    if rows:
        return rows
    for file in sorted(target.rglob("*.jsonl")):
        rows.extend(_read_trade_file(file, era=""))
    return rows


def _read_trade_file(path: Path, *, era: str) -> list[dict[str, Any]]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        LOG.warning("Setup-rule search could not read %s", path)
        return []
    if path.suffix.lower() == ".jsonl" or _looks_like_jsonl(text):
        rows: list[dict[str, Any]] = []
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                rows.append(_flatten_trade(payload, era))
        return rows
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        LOG.warning("Setup-rule search skipped unreadable JSON %s", path)
        return []
    if isinstance(payload, list):
        return [_flatten_trade(row, era) for row in payload if isinstance(row, dict)]
    if isinstance(payload, dict):
        file_era = str(payload.get("era") or era or "")
        trades = payload.get("trades")
        if isinstance(trades, list):
            return [_flatten_trade(row, file_era) for row in trades if isinstance(row, dict)]
        if "net_return" in payload or "return" in payload:
            return [_flatten_trade(payload, file_era)]
    return []


def _looks_like_jsonl(text: str) -> bool:
    lines = [line for line in text.splitlines() if line.strip()]
    if len(lines) < 2:
        return False
    return lines[0].lstrip().startswith("{") and lines[1].lstrip().startswith("{")


def _naive_days(column: Any, n: int) -> pd.Series:
    if column is None:
        parsed = pd.to_datetime(pd.Series([pd.NaT] * n), errors="coerce", utc=True)
    else:
        parsed = pd.to_datetime(column, errors="coerce", utc=True)
    return parsed.dt.tz_convert(None)


def _era_for_day(day: Any) -> str:
    try:
        stamp = np.datetime64(day, "D")
    except (TypeError, ValueError):
        return "undated"
    if np.isnat(stamp):
        return "undated"
    for name, start, end in ERA_BOUNDS:
        if stamp < np.datetime64(start):
            continue
        if end is None or stamp < np.datetime64(end):
            return name
    if stamp < np.datetime64("2015-01-01"):
        return "pre_2015"
    return "undated"


def _median(values: np.ndarray) -> float | None:
    finite = values[np.isfinite(values)]
    if finite.size == 0:
        return None
    return float(np.median(finite))


def normalize_trades(records: Iterable[dict[str, Any]]) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Build the search frame. Outcome columns stay off the feature list."""
    raw_rows = [row for row in records if isinstance(row, dict)]
    notes: dict[str, Any] = {
        "atr_pct_scale": None,
        "sector_breadth_scale": None,
        "pct_below_source": None,
        "era_label_disagreements": 0,
        "dropped_missing_date_or_return": 0,
        "ignored_columns": [],
        "features_present": [],
        "features_missing": [],
    }
    if not raw_rows:
        return pd.DataFrame(), notes
    frame = pd.DataFrame(raw_rows)
    notes["dropped_missing_date_or_return"] = int(len(frame))
    entry = _naive_days(frame["entry_date"] if "entry_date" in frame.columns else None, len(frame))
    exit_ = _naive_days(frame["exit_date"] if "exit_date" in frame.columns else None, len(frame))
    net = (
        pd.to_numeric(frame["net_return"], errors="coerce")
        if "net_return" in frame.columns
        else pd.Series(np.nan, index=frame.index)
    )
    if net.isna().all() and "return" in frame.columns:
        net = pd.to_numeric(frame.get("return"), errors="coerce")
    keep = entry.notna() & np.isfinite(pd.to_numeric(net, errors="coerce").to_numpy(dtype=float))
    frame = frame.loc[keep].copy()
    entry = entry.loc[keep]
    exit_ = exit_.loc[keep]
    net = pd.to_numeric(net.loc[keep], errors="coerce")
    notes["dropped_missing_date_or_return"] = int(notes["dropped_missing_date_or_return"] - len(frame))
    if frame.empty:
        return pd.DataFrame(), notes

    entry_day = pd.DatetimeIndex(entry).values.astype("datetime64[D]")
    exit_day = pd.DatetimeIndex(exit_).values.astype("datetime64[D]")
    out = pd.DataFrame(
        {
            "entry_day": entry_day,
            "exit_day": exit_day,
            "net_return": net.to_numpy(dtype=float),
        }
    )
    out["year"] = pd.DatetimeIndex(entry).year.astype(int)
    out["era"] = [_era_for_day(day) for day in out["entry_day"].tolist()]
    source_era = frame.get("era")
    if source_era is not None:
        labels = source_era.astype(str)
        disagree = 0
        for label, assigned in zip(labels.tolist(), out["era"].tolist(), strict=False):
            if label in KNOWN_ERAS and label != assigned:
                disagree += 1
        notes["era_label_disagreements"] = disagree
    out["exit_reason"] = frame["exit_reason"].astype(str) if "exit_reason" in frame.columns else ""
    if "mae" in frame.columns:
        out["mae"] = pd.to_numeric(frame["mae"], errors="coerce")
    if "stop_pct" in frame.columns:
        out["stop_pct"] = pd.to_numeric(frame["stop_pct"], errors="coerce")
    r_multiple = pd.to_numeric(frame["r_multiple"], errors="coerce") if "r_multiple" in frame.columns else None
    if r_multiple is None or not np.isfinite(r_multiple.to_numpy(dtype=float)).any():
        stop = pd.to_numeric(frame["stop_pct"], errors="coerce") if "stop_pct" in frame.columns else None
        if stop is not None:
            with np.errstate(divide="ignore", invalid="ignore"):
                computed = net.to_numpy(dtype=float) / stop.to_numpy(dtype=float)
            computed = np.where(stop.to_numpy(dtype=float) > 0, computed, np.nan)
            out["r_multiple"] = computed
        else:
            out["r_multiple"] = np.nan
    else:
        out["r_multiple"] = r_multiple.to_numpy(dtype=float)

    _attach_features(frame, out, notes)
    present = [
        name for name in _feature_names() if name in out.columns and np.isfinite(out[name].to_numpy(dtype=float)).any()
    ]
    notes["features_present"] = present
    notes["features_missing"] = [name for name in PRIMARY_GRIDS if name not in present]
    ignored = []
    for column in frame.columns:
        if (
            column in LEAKAGE_COLUMNS
            or column in present
            or column
            in {
                "entry_date",
                "era",
                "ticker",
                "year",
                "pct_from_52w_high",
                "pct_above_sma50",
            }
        ):
            continue
        if column in NESTED_KEYS:
            continue
        numeric = pd.to_numeric(frame[column], errors="coerce")
        if numeric.notna().any() and column not in _feature_names():
            ignored.append(str(column))
    notes["ignored_columns"] = sorted(set(ignored))
    return out.reset_index(drop=True), notes


def _feature_names() -> tuple[str, ...]:
    return tuple(PRIMARY_GRIDS) + ("pts_52w",) + BOTH_SIDED_EXTRAS


def _attach_features(source: pd.DataFrame, out: pd.DataFrame, notes: dict[str, Any]) -> None:
    if "signal_score" in source.columns:
        out["signal_score"] = pd.to_numeric(source["signal_score"], errors="coerce")
    if "pts_52w" in source.columns:
        out["pts_52w"] = pd.to_numeric(source["pts_52w"], errors="coerce")
    for name in BOTH_SIDED_EXTRAS:
        if name in source.columns:
            out[name] = pd.to_numeric(source[name], errors="coerce")
    if "pct_above_sma50" in source.columns and "distance_above_sma50" not in out.columns:
        out["distance_above_sma50"] = pd.to_numeric(source["pct_above_sma50"], errors="coerce")

    if "pct_below_52w_high" in source.columns:
        below = pd.to_numeric(source["pct_below_52w_high"], errors="coerce").to_numpy(dtype=float)
        med = _median(below)
        if med is not None and med > 1.5:
            below = below / 100.0
            notes["pct_below_source"] = "pct_below_52w_high_percent"
        else:
            notes["pct_below_source"] = "pct_below_52w_high"
        out["pct_below_52w_high"] = below
    elif "pct_from_52w_high" in source.columns:
        ratio = pd.to_numeric(source["pct_from_52w_high"], errors="coerce").to_numpy(dtype=float)
        med = _median(ratio)
        if med is not None and med > 1.5:
            ratio = ratio / 100.0
            notes["pct_below_source"] = "pct_from_52w_high_percent"
        else:
            notes["pct_below_source"] = "pct_from_52w_high"
        out["pct_below_52w_high"] = 1.0 - ratio

    if "sector_breadth50" in source.columns:
        breadth = pd.to_numeric(source["sector_breadth50"], errors="coerce").to_numpy(dtype=float)
        med = _median(breadth)
        if med is not None and med > 1.5:
            breadth = breadth / 100.0
            notes["sector_breadth_scale"] = "percent_to_fraction"
        else:
            notes["sector_breadth_scale"] = "fraction"
        out["sector_breadth50"] = breadth

    if "atr_pct" in source.columns:
        atr = pd.to_numeric(source["atr_pct"], errors="coerce").to_numpy(dtype=float)
        med = _median(atr)
        if med is not None and med <= 0.5:
            atr = atr * 100.0
            notes["atr_pct_scale"] = "fraction_to_percent"
        else:
            notes["atr_pct_scale"] = "already_percent"
        out["atr_pct"] = atr


def _configured_limits(skill_dir: Path | None) -> Any:
    from core.shadow_setup_filter import thresholds_from_config

    return thresholds_from_config(skill_dir)


def _with_value(values: tuple[float, ...], extra: float) -> tuple[float, ...]:
    merged = {round(float(value), 6) for value in values}
    merged.add(round(float(extra), 6))
    return tuple(sorted(merged))


def build_catalog(
    frame: pd.DataFrame,
    *,
    skill_dir: Path | None = None,
    extras: Iterable[Rule] | None = None,
    include_exploratory: bool = True,
) -> tuple[list[Rule], dict[str, tuple[float, ...]]]:
    """Pre-registered grids, plus quantile singles fit on discovery only."""
    limits = _configured_limits(skill_dir)
    grids: dict[str, tuple[float, ...]] = {
        "signal_score": _with_value(PRIMARY_GRIDS["signal_score"][1], limits.max_signal_score),
        "pct_below_52w_high": _with_value(PRIMARY_GRIDS["pct_below_52w_high"][1], limits.min_pct_below_52w),
        "sector_breadth50": _with_value(PRIMARY_GRIDS["sector_breadth50"][1], limits.max_sector_breadth50),
        "atr_pct": _with_value(PRIMARY_GRIDS["atr_pct"][1], limits.min_atr_pct),
    }
    ops = {feature: spec[0] for feature, spec in PRIMARY_GRIDS.items()}
    available = [feature for feature in PRIMARY_GRIDS if feature in frame.columns]
    families: list[list[Clause]] = []
    for feature in available:
        families.append([Clause(feature, ops[feature], threshold) for threshold in grids[feature]])
    rules: list[Rule] = []
    seen: set[str] = set()

    def _add(clauses: tuple[Clause, ...], *, family: str) -> None:
        rule = Rule(clauses=clauses, family=family)
        if not rule.rule_id or rule.rule_id in seen:
            return
        seen.add(rule.rule_id)
        rules.append(rule)

    for family in families:
        for clause in family:
            _add((clause,), family="grid")
    for i, left in enumerate(families):
        for right in families[i + 1 :]:
            for a in left:
                for b in right:
                    _add((a, b), family="grid")
    if len(families) >= 3:
        for i, first in enumerate(families):
            for j, second in enumerate(families[i + 1 :], start=i + 1):
                for third in families[j + 1 :]:
                    for a in first:
                        for b in second:
                            for c in third:
                                _add((a, b, c), family="grid")
    if len(families) == 4:
        for a in families[0]:
            for b in families[1]:
                for c in families[2]:
                    for d in families[3]:
                        _add((a, b, c, d), family="grid")

    if include_exploratory and not frame.empty:
        discovery = frame.loc[frame["entry_day"] < np.datetime64(OOS_START)]
        fit_on = discovery if len(discovery) >= 30 else frame
        for feature, op in ONE_SIDED_EXTRAS.items():
            if feature not in fit_on.columns:
                continue
            thresholds = _quantile_grid(fit_on[feature].to_numpy(dtype=float))
            grids[feature] = thresholds
            for threshold in thresholds:
                _add((Clause(feature, op, threshold),), family="exploratory")
        for feature in BOTH_SIDED_EXTRAS:
            if feature not in fit_on.columns:
                continue
            thresholds = _quantile_grid(fit_on[feature].to_numpy(dtype=float))
            grids[feature] = thresholds
            for threshold in thresholds:
                _add((Clause(feature, "<=", threshold),), family="exploratory")
                _add((Clause(feature, ">=", threshold),), family="exploratory")

    for extra in extras or ():
        if not isinstance(extra, Rule) or not extra.rule_id:
            continue
        if extra.rule_id in seen:
            continue
        seen.add(extra.rule_id)
        rules.append(Rule(clauses=extra.clauses, name=extra.name, family=extra.family or "hypothesis"))

    named = _named_keys(limits)
    labeled: list[Rule] = []
    for rule in rules:
        key = tuple((clause.feature, clause.op, round(clause.threshold, 6)) for clause in rule.clauses)
        label = named.get(key, rule.name)
        if label:
            labeled.append(Rule(clauses=rule.clauses, name=label, family="named" if label else rule.family))
        else:
            labeled.append(rule)
    return labeled, grids


def _quantile_grid(values: np.ndarray) -> tuple[float, ...]:
    finite = values[np.isfinite(values)]
    if finite.size < 30 or np.unique(np.round(finite, 6)).size < 4:
        return ()
    thresholds = {round(float(np.quantile(finite, q)), 6) for q in QUANTILES}
    return tuple(sorted(thresholds))


def _named_keys(limits: Any) -> dict[tuple[tuple[str, str, float], ...], str]:
    r1 = (("signal_score", "<=", round(float(limits.max_signal_score), 6)),)
    r1b = (("pct_below_52w_high", ">=", round(float(limits.min_pct_below_52w), 6)),)
    r2 = (("sector_breadth50", "<=", round(float(limits.max_sector_breadth50), 6)),)
    r4 = (("atr_pct", ">=", round(float(limits.min_atr_pct), 6)),)
    r3 = tuple(sorted((r1[0], r1b[0], r2[0])))
    r3r4 = tuple(sorted((*r3, r4[0])))
    return {r1: "R1", r1b: "R1b", r2: "R2", r4: "R4", r3: "R3", r3r4: "R3+R4"}


def _adjacent(grid: tuple[float, ...], threshold: float) -> list[float]:
    below = [value for value in grid if value < threshold - 1e-9]
    above = [value for value in grid if value > threshold + 1e-9]
    out: list[float] = []
    if below:
        out.append(below[-1])
    if above:
        out.append(above[0])
    return out


def neighbor_rules(rule: Rule, grids: dict[str, tuple[float, ...]]) -> list[Rule]:
    """One-step threshold moves. An interior threshold has a neighbor on each side."""
    out: list[Rule] = []
    for index, clause in enumerate(rule.clauses):
        grid = grids.get(clause.feature) or ()
        for threshold in _adjacent(grid, clause.threshold):
            clauses = list(rule.clauses)
            clauses[index] = Clause(clause.feature, clause.op, threshold)
            out.append(Rule(clauses=tuple(clauses), family="neighbor"))
    return out


def _empty_slice() -> dict[str, Any]:
    return {
        "n": 0,
        "n_evaluable": 0,
        "trades_per_day": None,
        "mean_return": None,
        "mean_r": None,
        "n_r": 0,
        "win_rate": None,
        "sortino": None,
        "max_drawdown": None,
        "baseline_n": 0,
        "baseline_mean": None,
        "excess_mean": None,
        "sum_return": 0.0,
        "baseline_sum": 0.0,
    }


def _sortino(values: np.ndarray) -> float | None:
    if values.size < 2:
        return None
    downside = np.minimum(values, 0.0)
    deviation = math.sqrt(float(np.mean(downside**2)))
    if deviation < 1e-12:
        return None
    return float(np.mean(values) / deviation)


def _max_drawdown(exit_day: np.ndarray, values: np.ndarray) -> float | None:
    if values.size == 0:
        return None
    order = np.argsort(exit_day, kind="mergesort")
    days = exit_day[order]
    rets = np.clip(values[order], -0.95, None)
    if days.size == 1:
        equity = 1.0 + float(rets[0])
        return 0.0 if equity >= 1.0 else float(1.0 - equity)
    cuts = np.flatnonzero(days[1:] != days[:-1]) + 1
    starts = np.r_[0, cuts]
    ends = np.r_[cuts, days.size]
    day_mean = np.add.reduceat(rets, starts) / (ends - starts)
    equity = np.cumprod(1.0 + day_mean)
    peak = np.maximum.accumulate(np.maximum(equity, 1e-12))
    drawdown = (peak - equity) / peak
    return float(np.max(drawdown))


def _slice_metrics(
    net: np.ndarray,
    r_multiple: np.ndarray,
    entry_day: np.ndarray,
    exit_day: np.ndarray,
    kept: np.ndarray,
    evaluable: np.ndarray,
) -> dict[str, Any]:
    base_idx = np.flatnonzero(evaluable)
    kept_idx = np.flatnonzero(kept)
    if base_idx.size == 0:
        return _empty_slice()
    base_net = net[base_idx]
    kept_net = net[kept_idx]
    baseline_mean = float(np.mean(base_net))
    mean = float(np.mean(kept_net)) if kept_net.size else None
    dates = entry_day[base_idx]
    n_dates = int(np.unique(dates).size)
    r_kept = r_multiple[kept_idx]
    r_finite = r_kept[np.isfinite(r_kept)]
    return {
        "n": int(kept_idx.size),
        "n_evaluable": int(base_idx.size),
        "trades_per_day": (float(kept_idx.size) / n_dates) if n_dates else None,
        "mean_return": mean,
        "mean_r": float(np.mean(r_finite)) if r_finite.size else None,
        "n_r": int(r_finite.size),
        "win_rate": float(np.mean(kept_net > 0)) if kept_net.size else None,
        "sortino": _sortino(kept_net) if kept_net.size else None,
        "max_drawdown": _max_drawdown(exit_day[kept_idx], kept_net) if kept_net.size else None,
        "baseline_n": int(base_idx.size),
        "baseline_mean": baseline_mean,
        "excess_mean": (mean - baseline_mean) if mean is not None else None,
        "sum_return": float(np.sum(kept_net)) if kept_net.size else 0.0,
        "baseline_sum": float(np.sum(base_net)),
    }


def _masks(frame: pd.DataFrame, rule: Rule) -> tuple[np.ndarray, np.ndarray]:
    evaluable = np.ones(len(frame), dtype=bool)
    kept = np.ones(len(frame), dtype=bool)
    for clause in rule.clauses:
        values = frame[clause.feature].to_numpy(dtype=float)
        finite = np.isfinite(values)
        evaluable &= finite
        if clause.op == "<=":
            kept &= finite & (values <= clause.threshold)
        elif clause.op == ">=":
            kept &= finite & (values >= clause.threshold)
        else:
            kept &= False
            evaluable &= False
    return kept, evaluable


def _group_codes(labels: list[str]) -> dict[str, np.ndarray]:
    return {label: np.flatnonzero(np.asarray(labels, dtype=object) == label) for label in sorted(set(labels))}


def evaluate_rules(frame: pd.DataFrame, rules: list[Rule]) -> list[dict[str, Any]]:
    """Metrics for each rule. ``exit_reason`` is not read."""
    if frame.empty or not rules:
        return []
    net = frame["net_return"].to_numpy(dtype=float)
    r_multiple = frame["r_multiple"].to_numpy(dtype=float)
    entry_day = frame["entry_day"].to_numpy()
    exit_day = frame["exit_day"].to_numpy()
    oos_cut = np.datetime64(OOS_START)
    discovery = entry_day < oos_cut
    oos = ~discovery
    era_idx = _group_codes(frame["era"].tolist())
    year_idx = _group_codes([str(year) for year in frame["year"].tolist()])
    rows: list[dict[str, Any]] = []
    for rule in rules:
        missing = [clause.feature for clause in rule.clauses if clause.feature not in frame.columns]
        if missing:
            rows.append(
                {
                    "rule_id": rule.rule_id,
                    "name": rule.name,
                    "family": rule.family,
                    "clauses": [_clause_dict(clause) for clause in rule.clauses],
                    "missing_features": missing,
                    "slices": {},
                }
            )
            continue
        kept, evaluable = _masks(frame, rule)
        slices = {
            "all": _slice_metrics(net, r_multiple, entry_day, exit_day, kept, evaluable),
            "discovery": _slice_metrics(net, r_multiple, entry_day, exit_day, kept & discovery, evaluable & discovery),
            "oos": _slice_metrics(net, r_multiple, entry_day, exit_day, kept & oos, evaluable & oos),
        }
        dropped = evaluable & ~kept & oos
        oos_kept_net = net[kept & oos]
        oos_dropped_net = net[dropped]
        slices["oos"]["welch_se"] = _welch_se(oos_kept_net, oos_dropped_net)
        slices["oos"]["kept_vs_dropped"] = _mean_diff(oos_kept_net, oos_dropped_net)
        eras = {}
        for era, idx in era_idx.items():
            mask = np.zeros(len(frame), dtype=bool)
            mask[idx] = True
            eras[era] = _slice_metrics(net, r_multiple, entry_day, exit_day, kept & mask, evaluable & mask)
        years = {}
        for year, idx in year_idx.items():
            mask = np.zeros(len(frame), dtype=bool)
            mask[idx] = True
            block = _slice_metrics(net, r_multiple, entry_day, exit_day, kept & mask, evaluable & mask)
            years[year] = {
                "n": block["n"],
                "mean_return": block["mean_return"],
                "baseline_mean": block["baseline_mean"],
            }
        rows.append(
            {
                "rule_id": rule.rule_id,
                "name": rule.name,
                "family": rule.family,
                "clauses": [_clause_dict(clause) for clause in rule.clauses],
                "missing_features": [],
                "slices": slices,
                "eras": eras,
                "years": years,
            }
        )
    return rows


def _clause_dict(clause: Clause) -> dict[str, Any]:
    return {"feature": clause.feature, "op": clause.op, "threshold": clause.threshold}


def _welch_se(left: np.ndarray, right: np.ndarray) -> float | None:
    if left.size < 2 or right.size < 2:
        return None
    var_left = float(np.var(left, ddof=1))
    var_right = float(np.var(right, ddof=1))
    return math.sqrt(var_left / left.size + var_right / right.size)


def _mean_diff(left: np.ndarray, right: np.ndarray) -> float | None:
    if left.size == 0 or right.size == 0:
        return None
    return float(np.mean(left) - np.mean(right))


def _round_slice(block: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in block.items():
        if key in {"sum_return", "baseline_sum", "welch_se"}:
            continue
        if isinstance(value, float):
            out[key] = round(value, 6)
        else:
            out[key] = value
    if "welch_se" in block and isinstance(block["welch_se"], float):
        out["welch_se"] = round(block["welch_se"], 6)
    return out


def _discovery_excess(row: dict[str, Any]) -> float | None:
    block = (row.get("slices") or {}).get("discovery") or {}
    value = block.get("excess_mean")
    return float(value) if isinstance(value, (int, float)) and math.isfinite(float(value)) else None


def apply_gates(
    rows: list[dict[str, Any]],
    rules: list[Rule],
    grids: dict[str, tuple[float, ...]],
    gates: SearchGates,
    *,
    n_trials: int,
) -> list[dict[str, Any]]:
    by_id = {row["rule_id"]: row for row in rows}
    haircut_z = expected_max_z(n_trials)
    judged: list[dict[str, Any]] = []
    for rule in rules:
        row = by_id.get(rule.rule_id)
        if row is None:
            continue
        reasons: list[str] = []
        if row.get("missing_features"):
            reasons.append("feature_missing")
        discovery = (row.get("slices") or {}).get("discovery") or _empty_slice()
        oos = (row.get("slices") or {}).get("oos") or _empty_slice()
        if int(discovery.get("n") or 0) < gates.min_discovery_n:
            reasons.append("insufficient_discovery_n")
        if int(oos.get("n") or 0) < gates.min_oos_n:
            reasons.append("insufficient_oos_n")
        excess = oos.get("excess_mean")
        if not isinstance(excess, (int, float)) or float(excess) <= 0.0:
            reasons.append("oos_not_above_baseline")
        se = oos.get("welch_se")
        kept_vs_dropped = oos.get("kept_vs_dropped")
        if isinstance(se, (int, float)) and isinstance(kept_vs_dropped, (int, float)) and se > 0:
            deflated = float(kept_vs_dropped) - haircut_z * float(se)
        elif isinstance(kept_vs_dropped, (int, float)) and float(kept_vs_dropped) > 0 and (se is None or se == 0):
            deflated = float(kept_vs_dropped)
        else:
            deflated = None
        if deflated is None or deflated <= 0.0:
            reasons.append("deflated_oos_not_above_baseline")
        votes, beat, eligible = _era_votes(row, gates)
        if eligible < gates.min_voting_eras:
            reasons.append("insufficient_era_coverage")
        elif eligible == 0 or (beat / eligible) < gates.era_majority:
            reasons.append("era_vote")
        neighbors = neighbor_rules(rule, grids)
        neighbor_ids = [item.rule_id for item in neighbors]
        if not neighbors:
            reasons.append("unstable_threshold")
        else:
            for neighbor in neighbors:
                neighbor_row = by_id.get(neighbor.rule_id)
                neighbor_excess = _discovery_excess(neighbor_row) if neighbor_row else None
                neighbor_n = int(((neighbor_row or {}).get("slices") or {}).get("discovery", {}).get("n") or 0)
                if neighbor_excess is None or neighbor_excess <= 0.0 or neighbor_n < gates.min_era_n:
                    reasons.append("unstable_threshold")
                    break
        deduped: list[str] = []
        for reason in reasons:
            if reason not in deduped:
                deduped.append(reason)
        public_eras = {era: _round_slice(block) for era, block in (row.get("eras") or {}).items() if era in KNOWN_ERAS}
        judged.append(
            {
                "rule_id": rule.rule_id,
                "name": rule.name,
                "family": rule.family,
                "clauses": row.get("clauses") or [],
                "passed": not deduped,
                "reasons": deduped,
                "n": (row.get("slices") or {}).get("all", {}).get("n"),
                "trades_per_day": _maybe_round((row.get("slices") or {}).get("all", {}).get("trades_per_day")),
                "mean_return": _maybe_round((row.get("slices") or {}).get("all", {}).get("mean_return")),
                "mean_r": _maybe_round((row.get("slices") or {}).get("all", {}).get("mean_r")),
                "win_rate": _maybe_round((row.get("slices") or {}).get("all", {}).get("win_rate")),
                "sortino": _maybe_round((row.get("slices") or {}).get("all", {}).get("sortino")),
                "max_drawdown": _maybe_round((row.get("slices") or {}).get("all", {}).get("max_drawdown")),
                "discovery": _round_slice(discovery),
                "oos": {
                    **_round_slice(oos),
                    "deflated_excess": _maybe_round(deflated),
                    "haircut_z": round(haircut_z, 4),
                },
                "eras": public_eras,
                "years": row.get("years") or {},
                "era_votes": votes,
                "era_beat": beat,
                "era_eligible": eligible,
                "neighbor_ids": neighbor_ids,
            }
        )
    judged.sort(
        key=lambda item: (
            bool(item["passed"]),
            item["oos"].get("deflated_excess")
            if isinstance(item["oos"].get("deflated_excess"), (int, float))
            else -1e9,
            item["oos"].get("excess_mean") if isinstance(item["oos"].get("excess_mean"), (int, float)) else -1e9,
            item["n"] or 0,
            item["rule_id"],
        ),
        reverse=True,
    )
    for rank, item in enumerate(judged, start=1):
        item["rank"] = rank
    return judged


def _maybe_round(value: Any) -> float | None:
    if isinstance(value, (int, float)) and math.isfinite(float(value)):
        return round(float(value), 6)
    return None


def _era_votes(row: dict[str, Any], gates: SearchGates) -> tuple[dict[str, str], int, int]:
    votes: dict[str, str] = {}
    beat = 0
    eligible = 0
    eras = row.get("eras") or {}
    for era in VOTING_ERAS:
        block = eras.get(era) or {}
        n = int(block.get("n") or 0)
        base_n = int(block.get("baseline_n") or 0)
        excess = block.get("excess_mean")
        if n < gates.min_era_n or base_n < gates.min_era_n or not isinstance(excess, (int, float)):
            votes[era] = "thin"
            continue
        eligible += 1
        if float(excess) > 0.0:
            votes[era] = "beat"
            beat += 1
        else:
            votes[era] = "miss"
    return votes, beat, eligible


def _combine(row: dict[str, Any], eras: tuple[str, ...], field: str) -> tuple[float, float]:
    total_n = 0.0
    total_sum = 0.0
    for era in eras:
        block = (row.get("eras") or {}).get(era) or {}
        if field == "kept":
            total_n += float(block.get("n") or 0)
            total_sum += float(block.get("sum_return") or 0.0)
        else:
            total_n += float(block.get("baseline_n") or 0)
            total_sum += float(block.get("baseline_sum") or 0.0)
    return total_n, total_sum


def walk_forward_report(rows: list[dict[str, Any]], gates: SearchGates) -> list[dict[str, Any]]:
    """Pick on earlier eras, then score the pick on the next era and on 2022+."""
    folds = (
        (("late_bull",), "volatility_chop"),
        (("late_bull", "volatility_chop"), "crash_recovery"),
        (DISCOVERY_ERAS, "oos"),
    )
    report: list[dict[str, Any]] = []
    for train, test in folds:
        selected = _select_on_train(rows, train, gates)
        test_excess = None
        test_n = None
        if selected is not None:
            if test == "oos":
                block = (selected.get("slices") or {}).get("oos") or {}
            else:
                block = (selected.get("eras") or {}).get(test) or {}
            test_excess = _maybe_round(block.get("excess_mean"))
            test_n = block.get("n")
        report.append(
            {
                "train": list(train),
                "test": "2022_plus" if test == "oos" else test,
                "selected": None if selected is None else selected.get("rule_id"),
                "selected_name": None if selected is None else selected.get("name"),
                "test_n": test_n,
                "test_excess_mean": test_excess,
            }
        )
    return report


def _select_on_train(rows: list[dict[str, Any]], eras: tuple[str, ...], gates: SearchGates) -> dict[str, Any] | None:
    best: dict[str, Any] | None = None
    best_key: tuple[float, int, str] | None = None
    for row in rows:
        if row.get("missing_features"):
            continue
        n, total = _combine(row, eras, "kept")
        base_n, base_sum = _combine(row, eras, "baseline")
        if n < gates.min_discovery_n or base_n <= 0:
            continue
        excess = (total / n) - (base_sum / base_n)
        if excess <= 0:
            continue
        key = (excess, int(n), str(row.get("rule_id")))
        if best_key is None or key > best_key:
            best_key = key
            best = row
    return best


def baseline_report(frame: pd.DataFrame) -> dict[str, Any]:
    if frame.empty:
        return {}
    net = frame["net_return"].to_numpy(dtype=float)
    r_multiple = frame["r_multiple"].to_numpy(dtype=float)
    entry_day = frame["entry_day"].to_numpy()
    exit_day = frame["exit_day"].to_numpy()
    all_rows = np.ones(len(frame), dtype=bool)
    oos = entry_day >= np.datetime64(OOS_START)
    discovery = ~oos
    block = _slice_metrics(net, r_multiple, entry_day, exit_day, all_rows, all_rows)
    return {
        "all": _round_slice(block),
        "discovery": _round_slice(_slice_metrics(net, r_multiple, entry_day, exit_day, discovery, discovery)),
        "oos": _round_slice(_slice_metrics(net, r_multiple, entry_day, exit_day, oos, oos)),
        "n_dates": int(np.unique(entry_day).size),
        "date_min": str(np.min(entry_day)),
        "date_max": str(np.max(entry_day)),
    }


def search_rules(
    records: Iterable[dict[str, Any]],
    *,
    skill_dir: Path | None = None,
    gates: SearchGates | None = None,
    catalog: list[Rule] | None = None,
    grids: dict[str, tuple[float, ...]] | None = None,
    extra_rules: Iterable[Rule] | None = None,
    include_exploratory: bool = True,
) -> dict[str, Any]:
    """Evaluate the catalog. Passing rules are recommendations, not live filters."""
    limits = gates or SearchGates()
    frame, notes = normalize_trades(records)
    if catalog is None:
        catalog, built_grids = build_catalog(
            frame,
            skill_dir=skill_dir,
            extras=extra_rules,
            include_exploratory=include_exploratory,
        )
        grids = built_grids
    else:
        catalog = list(catalog)
        for extra in extra_rules or ():
            if all(extra.rule_id != rule.rule_id for rule in catalog):
                catalog.append(extra)
        grids = dict(grids or {})
        for rule in catalog:
            for clause in rule.clauses:
                current = list(grids.get(clause.feature) or ())
                if all(abs(value - clause.threshold) > 1e-9 for value in current):
                    current.append(clause.threshold)
                grids[clause.feature] = tuple(sorted(current))
    expanded: list[Rule] = []
    seen = {rule.rule_id for rule in catalog}
    expanded.extend(catalog)
    for rule in catalog:
        for neighbor in neighbor_rules(rule, grids):
            if neighbor.rule_id not in seen and all(clause.feature in frame.columns for clause in neighbor.clauses):
                seen.add(neighbor.rule_id)
                expanded.append(neighbor)
    evaluated = evaluate_rules(frame, expanded)
    n_trials = len(expanded)
    judged = apply_gates(evaluated, expanded, grids, limits, n_trials=n_trials)
    # Neighbors added only for stability stay in the JSON but rank after primary rules
    # that were in the requested catalog. They remain eligible to pass.
    passing = [row["rule_id"] for row in judged if row["passed"]]
    return {
        "ok": True,
        "orders_touched": False,
        "live_scan_changed": False,
        "cost_usd": 0.0,
        "stops_changed": False,
        "oos_start": limits.oos_start,
        "n_trials": n_trials,
        "expected_max_z": round(expected_max_z(n_trials), 4),
        "gates": {
            "min_oos_n": limits.min_oos_n,
            "min_discovery_n": limits.min_discovery_n,
            "min_era_n": limits.min_era_n,
            "era_majority": limits.era_majority,
            "min_voting_eras": limits.min_voting_eras,
            "voting_eras": list(VOTING_ERAS),
        },
        "n_trades": int(len(frame)),
        "features": notes,
        "baseline": baseline_report(frame),
        "walk_forward": walk_forward_report(evaluated, limits),
        "rules": judged,
        "passing": passing,
        "shadow_recommendations": [
            {"rule_id": row["rule_id"], "name": row["name"], "clauses": row["clauses"]}
            for row in judged
            if row["passed"]
        ],
        "feature_grids": {feature: [float(value) for value in values] for feature, values in grids.items()},
        "exit_reason_audit": audit_exit_reasons(frame),
    }


def audit_exit_reasons(frame: pd.DataFrame) -> dict[str, Any]:
    """Describe exit labels. The search never filters on them.

    The current backtest writer assigns ``trailing_stop`` when the close hits
    the stop and ``time_exit`` when the hold limit is reached, then copies that
    string into the chunk. A trailing stop can also lock in a gain, so winners
    on ``trailing_stop`` are not proof the labels were swapped. MAE uses lows
    and the stop uses closes, so a ``time_exit`` can show an MAE through the
    stop without the label being wrong.
    """
    writer = (
        "backtest._evaluate_position_exit returns trailing_stop from _stop_triggered "
        "and time_exit when bars_held reaches the hold limit. The chunk writer copies "
        "that string. This code path does not swap the labels. The search ignores exit_reason."
    )
    if frame.empty or "exit_reason" not in frame.columns:
        return {"writer": writer, "n": 0, "by_reason": {}, "flags": ["exit_reason_absent"]}
    reasons = frame["exit_reason"].fillna("").astype(str)
    net = frame["net_return"].to_numpy(dtype=float)
    by_reason: dict[str, Any] = {}
    for label in sorted(set(reasons.tolist())):
        mask = (reasons == label).to_numpy()
        values = net[mask]
        if values.size == 0:
            continue
        by_reason[label or "(blank)"] = {
            "n": int(values.size),
            "mean_return": round(float(np.mean(values)), 6),
            "median_return": round(float(np.median(values)), 6),
            "win_rate": round(float(np.mean(values > 0)), 6),
        }
    flags: list[str] = []
    stop = by_reason.get("trailing_stop")
    time_exit = by_reason.get("time_exit")
    if stop and time_exit and stop["n"] >= 30 and time_exit["n"] >= 30:
        if (
            stop["win_rate"] > time_exit["win_rate"] + 0.20
            and stop["median_return"] > 0
            and time_exit["median_return"] < 0
        ):
            flags.append("winner_profile_on_trailing_stop")
    if "mae" in frame.columns and "stop_pct" in frame.columns:
        mae = frame["mae"].to_numpy(dtype=float)
        stop_pct = frame["stop_pct"].to_numpy(dtype=float)
        time_mask = (reasons == "time_exit").to_numpy() & np.isfinite(mae) & np.isfinite(stop_pct) & (stop_pct > 0)
        if int(time_mask.sum()) >= 30:
            through = mae[time_mask] <= (-stop_pct[time_mask])
            share = float(np.mean(through))
            by_reason.setdefault("time_exit", {})["mae_through_stop_share"] = round(share, 6)
            if share >= 0.25:
                flags.append("time_exit_mae_through_stop")
                flags.append("mae_uses_lows_stop_uses_closes")
    if not flags:
        flags.append("no_label_swap_detected")
    return {"writer": writer, "n": int(len(frame)), "by_reason": by_reason, "flags": flags}


def quality_floor_report(skill_dir: Path | None = None) -> dict[str, Any]:
    """What the live quality gate actually compares against ``signal_score``.

    ``QUALITY_MIN_SIGNAL_SCORE`` is a minimum, not a cap. The scanner calls
    ``get_quality_min_signal_score_effective``. With the SMA multiplier at 0,
    that effective floor sits well below the configured number, and it does
    not remove the high-score cluster R1 is aimed at.
    """
    from config import (
        get_quality_min_signal_score,
        get_quality_min_signal_score_effective,
        get_quality_require_breakout_volume,
        get_score_pts_sma_cap,
        get_score_pts_sma_multiplier,
        quality_signal_score_ceiling,
    )

    configured = float(get_quality_min_signal_score(skill_dir))
    effective = float(get_quality_min_signal_score_effective(skill_dir))
    ceiling = float(quality_signal_score_ceiling(skill_dir))
    return {
        "configured": configured,
        "effective": effective,
        "ceiling": ceiling,
        "sma_cap": float(get_score_pts_sma_cap(skill_dir)),
        "sma_multiplier": float(get_score_pts_sma_multiplier(skill_dir)),
        "require_breakout_volume": bool(get_quality_require_breakout_volume(skill_dir)),
        "scanner_uses": "get_quality_min_signal_score_effective",
        "note": (
            f"Configured QUALITY_MIN_SIGNAL_SCORE={configured:g}. "
            f"The live gate uses the effective floor {effective:g} "
            f"(score ceiling {ceiling:g}). It drops scores below that floor. "
            "It does not cap high signal_score, so it does not implement R1."
        ),
    }


def parse_hypotheses(text: str, *, allowed: set[str] | None = None) -> list[Rule]:
    """Turn a model reply into rules. Unknown features and bad ops are dropped."""
    payload = _extract_json(text)
    if payload is None:
        return []
    raw_rules = payload.get("rules") if isinstance(payload, dict) else payload
    if isinstance(payload, dict) and "feature" in payload:
        raw_rules = [payload]
    if not isinstance(raw_rules, list):
        return []
    rules: list[Rule] = []
    for raw in raw_rules[:8]:
        clauses_raw = raw.get("clauses") if isinstance(raw, dict) else None
        if clauses_raw is None and isinstance(raw, dict) and "feature" in raw:
            clauses_raw = [raw]
        if not isinstance(clauses_raw, list):
            continue
        clauses: list[Clause] = []
        for clause in clauses_raw[:4]:
            if not isinstance(clause, dict):
                continue
            feature = str(clause.get("feature") or "")
            op = str(clause.get("op") or "")
            threshold = _finite(clause.get("threshold"))
            if op not in {"<=", ">="} or threshold is None:
                continue
            if allowed is not None and feature not in allowed:
                continue
            if feature not in _feature_names() and feature not in PRIMARY_GRIDS:
                continue
            clauses.append(Clause(feature, op, threshold))
        if clauses:
            rules.append(Rule(clauses=tuple(clauses), family="hypothesis"))
    return rules


def _extract_json(text: str) -> Any:
    raw = (text or "").strip()
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        start = raw.find("{")
        end = raw.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            return json.loads(raw[start : end + 1])
        except json.JSONDecodeError:
            return None


def hypothesis_prompt(features: list[str], grids: dict[str, tuple[float, ...]]) -> str:
    preview = {feature: [float(value) for value in grids.get(feature, ())] for feature in features}
    return "\n".join(
        [
            "Propose up to 8 entry filters as JSON.",
            "A rule is a list of clauses. Each clause has feature, op (<= or >=), and threshold.",
            "Use only the features below. Stay near the listed grids.",
            "Do not propose stops, exits, position size, or orders.",
            json.dumps({"features": preview}, sort_keys=True),
            '{"rules":[{"clauses":[{"feature":"signal_score","op":"<=","threshold":34}]}]}',
        ]
    )


def render_setup_rules_md(report: dict[str, Any]) -> str:
    """Ranked table for the nightly markdown report."""
    lines = [
        "## Setup rules",
        "",
        "Offline entry-filter search. Cost $0. Orders untouched. Live scan output unchanged.",
        "A pass is a recommendation. It is not installed into the scanner.",
        "Stops and exits were not searched. The live stop stays 2.5x ATR with a 5% floor.",
        "Max drawdown compounds the mean net return of trades that exit on each day. It is not a sized portfolio.",
        f"Holdout starts {report.get('oos_start') or OOS_START}. "
        f"Trials={report.get('n_trials')} expected_max_z={report.get('expected_max_z')}.",
    ]
    if report.get("reason") and not report.get("rules"):
        lines.extend(["", f"No search: {report.get('reason')}"])
    baseline = report.get("baseline") if isinstance(report.get("baseline"), dict) else {}
    base_all = baseline.get("all") if isinstance(baseline.get("all"), dict) else {}
    base_oos = baseline.get("oos") if isinstance(baseline.get("oos"), dict) else {}
    if base_all:
        lines.append(
            "Baseline "
            f"n={base_all.get('n')} mean={_pct(base_all.get('mean_return'))} "
            f"win={_pct(base_all.get('win_rate'))} sortino={_plain(base_all.get('sortino'))} "
            f"maxDD={_pct(base_all.get('max_drawdown'))} "
            f"oos_n={base_oos.get('n')} oos_mean={_pct(base_oos.get('mean_return'))}"
        )
    issues = report.get("data_issues") if isinstance(report.get("data_issues"), dict) else {}
    if issues:
        lines.extend(["", "### Data notes"])
        floor = issues.get("quality_floor") if isinstance(issues.get("quality_floor"), dict) else {}
        if floor.get("note"):
            lines.append(f"- Quality floor: {floor.get('note')}")
        exit_audit = issues.get("exit_reason") if isinstance(issues.get("exit_reason"), dict) else {}
        if exit_audit:
            lines.append(f"- Exit labels: flags={exit_audit.get('flags')}. {exit_audit.get('writer')}")
        features = issues.get("features") if isinstance(issues.get("features"), dict) else {}
        if features.get("features_missing"):
            lines.append(f"- Features missing from this pool: {features.get('features_missing')}")
        if features.get("ignored_columns"):
            lines.append(f"- Numeric columns not searched: {features.get('ignored_columns')}")
        if features.get("atr_pct_scale"):
            lines.append(f"- atr_pct scale: {features.get('atr_pct_scale')}")
        if features.get("pct_below_source"):
            lines.append(f"- distance from 52-week high: {features.get('pct_below_source')}")
        if issues.get("pool"):
            lines.append(f"- Pool: {issues.get('pool')}")
    folds = report.get("walk_forward") if isinstance(report.get("walk_forward"), list) else []
    if folds:
        lines.extend(["", "### Walk-forward picks"])
        lines.append("The pick is the best discovery excess. The test column was not used to choose it.")
        for fold in folds:
            if not isinstance(fold, dict):
                continue
            lines.append(
                f"- train={fold.get('train')} test={fold.get('test')} "
                f"selected={fold.get('selected_name') or fold.get('selected')} "
                f"test_excess={_pct(fold.get('test_excess_mean'))} test_n={fold.get('test_n')}"
            )
    rules = [row for row in (report.get("rules") or []) if isinstance(row, dict)]
    show = _rows_for_table(rules)
    if show:
        lines.extend(
            [
                "",
                "### Ranked rules",
                "",
                "| Rank | Rule | Pass | n | /day | mean | mean R | win | Sortino | max DD | OOS mean | OOS vs base | eras | Why |",
                "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
            ]
        )
        if base_all:
            lines.append(
                "|  | baseline (no filter) |  | "
                f"{base_all.get('n')} | {_plain(base_all.get('trades_per_day'))} | "
                f"{_pct(base_all.get('mean_return'))} | {_plain(base_all.get('mean_r'))} | "
                f"{_pct(base_all.get('win_rate'))} | {_plain(base_all.get('sortino'))} | "
                f"{_pct(base_all.get('max_drawdown'))} | {_pct(base_oos.get('mean_return'))} |  |  | reference |"
            )
        for row in show:
            oos = row.get("oos") if isinstance(row.get("oos"), dict) else {}
            label = row.get("name") or row.get("rule_id")
            if row.get("name") and row.get("rule_id"):
                label = f"{row.get('name')}: {row.get('rule_id')}"
            eras = f"{row.get('era_beat')}/{row.get('era_eligible')}"
            why = "pass" if row.get("passed") else ", ".join(row.get("reasons") or [])
            lines.append(
                "| "
                + " | ".join(
                    [
                        str(row.get("rank")),
                        str(label),
                        "yes" if row.get("passed") else "no",
                        str(row.get("n")),
                        _plain(row.get("trades_per_day")),
                        _pct(row.get("mean_return")),
                        _plain(row.get("mean_r")),
                        _pct(row.get("win_rate")),
                        _plain(row.get("sortino")),
                        _pct(row.get("max_drawdown")),
                        _pct(oos.get("mean_return")),
                        _pct(oos.get("excess_mean")),
                        eras,
                        why or "",
                    ]
                )
                + " |"
            )
    recs = report.get("shadow_recommendations") or []
    lines.extend(
        [
            "",
            f"Passing rules: {len(recs)}. The table shows the top 15 and every named shadow rule. The JSON lists every trial.",
            "Shadow logging of passing rules stays off unless SHADOW_SETUP_FILTER_RECOMMENDED=true.",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def _rows_for_table(rules: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Top of the ranking plus every named shadow rule. The JSON keeps the rest."""
    named = [row for row in rules if row.get("name")]
    top = rules[:15]
    chosen: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in top + named:
        rule_id = str(row.get("rule_id") or "")
        if rule_id in seen:
            continue
        seen.add(rule_id)
        chosen.append(row)
    chosen.sort(key=lambda row: int(row.get("rank") or 10**9))
    return chosen


def _pct(value: Any) -> str:
    number = _finite(value)
    if number is None:
        return "n/a"
    return f"{number * 100:.2f}%"


def _plain(value: Any) -> str:
    number = _finite(value)
    if number is None:
        return "n/a"
    return f"{number:.2f}"


def chunks_dir(skill_dir: Path) -> Path:
    import os

    override = os.environ.get("BACKTEST_ARTIFACT_DIR", "").strip()
    root = Path(override) if override else Path(skill_dir) / "validation_artifacts"
    return root / "multi_era_chunks"


def recommended_rules_path(skill_dir: Path) -> Path:
    return Path(skill_dir) / "data" / "shadow_setup_filter" / RECOMMENDED_NAME


def write_recommended_rules(skill_dir: Path, report: dict[str, Any]) -> Path:
    path = recommended_rules_path(skill_dir)
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": "setup_rule_search",
        "live_scan_changed": False,
        "orders_touched": False,
        "rules": report.get("shadow_recommendations") or [],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def run_setup_rule_phase(
    skill_dir: Path | str,
    *,
    trades_path: Path | str | None = None,
    extra_rules: Iterable[Rule] | None = None,
    write_shadow: bool = True,
    gates: SearchGates | None = None,
) -> dict[str, Any]:
    """Run the search for one night. Returns a report and does not trade."""
    root = Path(skill_dir)
    from config import (
        get_setup_rule_search_era_majority,
        get_setup_rule_search_min_discovery_n,
        get_setup_rule_search_min_era_n,
        get_setup_rule_search_min_oos_n,
        get_setup_rule_search_run_id,
        get_setup_rule_search_trades_path,
    )

    limits = gates or SearchGates(
        min_oos_n=get_setup_rule_search_min_oos_n(root),
        min_discovery_n=get_setup_rule_search_min_discovery_n(root),
        min_era_n=get_setup_rule_search_min_era_n(root),
        era_majority=get_setup_rule_search_era_majority(root),
    )
    run_id = get_setup_rule_search_run_id(root)
    override = str(trades_path or get_setup_rule_search_trades_path(root) or "")
    path = Path(override) if override else chunks_dir(root) / run_id
    floor = quality_floor_report(root)
    if not path.exists():
        report = _empty_report(limits, reason="pool_missing", path=path, run_id=run_id)
        report["data_issues"]["quality_floor"] = floor
        report["data_issues"]["exit_reason"] = audit_exit_reasons(pd.DataFrame())
        return report
    records = load_trade_records(path)
    if not records:
        report = _empty_report(limits, reason="pool_empty", path=path, run_id=run_id)
        report["data_issues"]["quality_floor"] = floor
        report["data_issues"]["exit_reason"] = audit_exit_reasons(pd.DataFrame())
        return report
    try:
        report = search_rules(records, skill_dir=root, gates=limits, extra_rules=extra_rules)
    except Exception:
        LOG.exception("Setup-rule search failed for %s", path)
        report = _empty_report(limits, reason="search_failed", path=path, run_id=run_id)
        report["data_issues"]["quality_floor"] = floor
        return report
    report["run_id"] = path.name if override else run_id
    report["configured_run_id"] = run_id
    report["path"] = str(path)
    report["data_issues"] = {
        "quality_floor": floor,
        "exit_reason": report.get("exit_reason_audit") or {},
        "features": report.get("features") or {},
        "pool": f"n={report.get('n_trades')} path={path}",
        "stops": "not searched; ADAPTIVE_STOP_ATR_MULT default 2.5 and ADAPTIVE_STOP_MIN_PCT default 0.05 left unchanged",
    }
    report["complete"] = True
    if write_shadow:
        try:
            write_recommended_rules(root, report)
            report["recommended_rules_path"] = str(recommended_rules_path(root))
        except OSError:
            LOG.warning("Setup-rule recommendations were not written", exc_info=True)
    LOG.info(
        "Setup-rule search n=%s trials=%s passing=%s cost_usd=0",
        report.get("n_trades"),
        report.get("n_trials"),
        len(report.get("passing") or []),
    )
    return report


def _empty_report(gates: SearchGates, *, reason: str, path: Path, run_id: str) -> dict[str, Any]:
    return {
        "ok": False,
        "complete": True,
        "reason": reason,
        "orders_touched": False,
        "live_scan_changed": False,
        "cost_usd": 0.0,
        "stops_changed": False,
        "oos_start": gates.oos_start,
        "n_trials": 0,
        "expected_max_z": 0.0,
        "n_trades": 0,
        "run_id": run_id,
        "path": str(path),
        "rules": [],
        "passing": [],
        "shadow_recommendations": [],
        "baseline": {},
        "walk_forward": [],
        "data_issues": {"pool": f"{reason}: {path}"},
    }


def gates_from_config(skill_dir: Path | None = None) -> SearchGates:
    from config import (
        get_setup_rule_search_era_majority,
        get_setup_rule_search_min_discovery_n,
        get_setup_rule_search_min_era_n,
        get_setup_rule_search_min_oos_n,
    )

    return SearchGates(
        min_oos_n=get_setup_rule_search_min_oos_n(skill_dir),
        min_discovery_n=get_setup_rule_search_min_discovery_n(skill_dir),
        min_era_n=get_setup_rule_search_min_era_n(skill_dir),
        era_majority=get_setup_rule_search_era_majority(skill_dir),
    )
