"""Adaptive backtest study: diagnose one finished trial, then propose one next spec.

The screen utility is a proxy. Promotion stays on the multi-era gates
(PF mean >= 1.20, worst-era PF >= 1.00). This module does not queue runs,
write champion params, or turn plugins to LIVE.
"""

from __future__ import annotations

import copy
import math
from datetime import datetime
from typing import Any

MIN_TRADES = 35
TRIAL_BUDGET = 12
STALL_ROUNDS = 3
MIN_UTILITY_DELTA = 0.02
FOREST_MIN_TRIALS = 4
FALLBACK_SHARE = 0.20
CAPACITY_SHARE = 0.25
PF_CAP = 5.0
DD_PENALTY_SCALE = 50.0

MULTI_ERA_HINT = (
    "This screen is not a promotion. Replay the held spec with "
    "python scripts/run_multi_era_backtest_schwab_only.py and judge "
    "PF mean >= 1.20 and worst-era PF >= 1.00."
)

# Production defaults from config.py getters. A missing override means this value.
ANCHOR_DEFAULTS: dict[str, Any] = {
    "stage2_52w_pct": 0.85,
    "stage2_sma_upward_days": 20,
    "vcp_days": 4,
    "scan_vcp_gate_mode": "shadow",
    "quality_gates_mode": "shadow",
    "quality_min_signal_score": 50,
    "quality_breakout_volume_min_ratio": 0.9,
    "rank_filter_shadow_min_percentile_rank_v2": 76,
    "backtest_hold_days": 40,
    "backtest_min_hold_days_before_trail": 15,
    "backtest_portfolio_max_positions": 10,
}

FAMILY_KNOBS: dict[str, tuple[str, ...]] = {
    "stage2": ("stage2_52w_pct", "stage2_sma_upward_days"),
    "vcp": ("vcp_days", "scan_vcp_gate_mode"),
    "quality": (
        "quality_gates_mode",
        "quality_min_signal_score",
        "quality_breakout_volume_min_ratio",
    ),
    "rank": ("rank_filter_shadow_min_percentile_rank_v2",),
    "exit": ("backtest_hold_days", "backtest_min_hold_days_before_trail"),
    "capacity": ("backtest_portfolio_max_positions",),
}

FAMILY_ORDER: tuple[str, ...] = ("stage2", "vcp", "quality", "rank", "exit")

FAMILY_LABEL: dict[str, str] = {
    "stage2": "Stage 2",
    "vcp": "VCP",
    "quality": "Quality",
    "rank": "Rank trim",
    "exit": "Exits",
    "capacity": "Capacity",
}

KNOB_FAMILY: dict[str, str] = {
    knob: family for family, knobs in FAMILY_KNOBS.items() for knob in knobs
}

# Ordered loose → tight so "down" loosens and "up" tightens, except exits and capacity
# where "up" is the diagnosis direction (longer hold, more grace, more slots).
KNOB_GRID: dict[str, tuple[Any, ...]] = {
    "stage2_52w_pct": (0.75, 0.80, 0.85, 0.90, 0.95),
    "stage2_sma_upward_days": (10, 15, 20, 25, 30),
    "vcp_days": (2, 4, 6, 8, 12),
    "scan_vcp_gate_mode": ("shadow", "hard"),
    "quality_gates_mode": ("off", "shadow", "soft", "hard"),
    "quality_min_signal_score": (40, 45, 50, 55, 60),
    "quality_breakout_volume_min_ratio": (0.7, 0.9, 1.2, 1.5),
    "rank_filter_shadow_min_percentile_rank_v2": (60, 68, 76, 84, 90),
    "backtest_hold_days": (20, 30, 40, 50, 60),
    "backtest_min_hold_days_before_trail": (5, 10, 15, 20, 25),
    "backtest_portfolio_max_positions": (8, 10, 14, 18, 20),
}

_FLOAT_KNOBS = frozenset({"stage2_52w_pct", "quality_breakout_volume_min_ratio"})
_INT_KNOBS = frozenset(set(KNOB_GRID) - _FLOAT_KNOBS - {"scan_vcp_gate_mode", "quality_gates_mode"})


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _num(value: Any) -> float | None:
    if value in ("inf", "Infinity"):
        return float("inf")
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number:  # NaN
        return None
    return number


def _intish(value: Any) -> int:
    number = _num(value)
    if number is None or not math.isfinite(number):
        return 0
    return int(number)


def _same(left: Any, right: Any) -> bool:
    if isinstance(left, str) or isinstance(right, str):
        return str(left) == str(right)
    left_n = _num(left)
    right_n = _num(right)
    if left_n is None or right_n is None:
        return left == right
    return abs(left_n - right_n) < 1e-6


def _coerce(knob: str, value: Any) -> Any:
    if knob in _FLOAT_KNOBS:
        number = _num(value)
        return round(float(number if number is not None else ANCHOR_DEFAULTS[knob]), 4)
    if knob in _INT_KNOBS:
        return _intish(value if value is not None else ANCHOR_DEFAULTS[knob])
    text = str(value if value is not None else ANCHOR_DEFAULTS[knob]).strip().lower()
    grid = KNOB_GRID[knob]
    if text not in grid:
        return ANCHOR_DEFAULTS[knob]
    return text


def _json_value(knob: str, value: Any) -> Any:
    coerced = _coerce(knob, value)
    if knob in _FLOAT_KNOBS:
        return float(coerced)
    if knob in _INT_KNOBS:
        return int(coerced)
    return str(coerced)


def resolved_overrides(spec: dict[str, Any] | None) -> dict[str, Any]:
    """Catalog knobs with production defaults filled in."""
    overrides = _dict(_dict(spec).get("overrides"))
    return {knob: _coerce(knob, overrides.get(knob, default)) for knob, default in ANCHOR_DEFAULTS.items()}


def _grid_index(knob: str, value: Any) -> int:
    grid = KNOB_GRID[knob]
    coerced = _coerce(knob, value)
    for idx, item in enumerate(grid):
        if _same(item, coerced):
            return idx
    best = min(range(len(grid)), key=lambda i: abs((_num(grid[i]) or 0) - (_num(coerced) or 0)))
    return best


def _step_value(knob: str, value: Any, direction: str) -> Any | None:
    grid = KNOB_GRID[knob]
    idx = _grid_index(knob, value)
    nxt = idx + (1 if direction == "up" else -1)
    if nxt < 0 or nxt >= len(grid):
        return None
    return _coerce(knob, grid[nxt])


def window_years(spec: dict[str, Any] | None) -> int | None:
    payload = spec if isinstance(spec, dict) else {}
    try:
        start = datetime.strptime(str(payload.get("start_date") or "")[:10], "%Y-%m-%d")
        end = datetime.strptime(str(payload.get("end_date") or "")[:10], "%Y-%m-%d")
    except ValueError:
        return None
    days = (end - start).days
    if days <= 0:
        return None
    return max(1, int(round(days / 365.25)))


def _shift_years(spec: dict[str, Any], years: int) -> dict[str, Any]:
    out = copy.deepcopy(spec)
    end = datetime.strptime(str(out.get("end_date"))[:10], "%Y-%m-%d")
    try:
        start = end.replace(year=end.year - int(years))
    except ValueError:
        start = end.replace(year=end.year - int(years), day=28)
    out["start_date"] = start.strftime("%Y-%m-%d")
    overrides = _dict(out.get("overrides"))
    if years >= 5:
        overrides["skip_mirofish"] = True
    if overrides:
        out["overrides"] = overrides
    return out


def _with_universe(spec: dict[str, Any], preset: str) -> dict[str, Any]:
    out = copy.deepcopy(spec)
    out["universe_preset"] = preset
    out["universe_mode"] = "watchlist"
    out["tickers"] = []
    return out


def spec_from_values(template: dict[str, Any], values: dict[str, Any]) -> dict[str, Any]:
    """Copy template dates and universe. Write only catalog knobs that differ from defaults."""
    out = copy.deepcopy(template)
    overrides = {k: v for k, v in _dict(out.get("overrides")).items() if k not in KNOB_FAMILY}
    for knob, default in ANCHOR_DEFAULTS.items():
        value = _coerce(knob, values.get(knob, default))
        if not _same(value, default):
            overrides[knob] = _json_value(knob, value)
    if overrides:
        out["overrides"] = overrides
    else:
        out.pop("overrides", None)
    return out


def _universe_id(spec: dict[str, Any] | None) -> str:
    payload = spec if isinstance(spec, dict) else {}
    return str(payload.get("universe_preset") or payload.get("universe_mode") or "").strip().lower()


def _profit_factor(result: dict[str, Any] | None) -> float | None:
    pf = _num(_dict(result).get("profit_factor_net"))
    if pf is None:
        return None
    if not math.isfinite(pf):
        return PF_CAP
    return min(PF_CAP, pf)


def _trades(result: dict[str, Any] | None) -> int | None:
    number = _num(_dict(result).get("total_trades"))
    if number is None or not math.isfinite(number):
        return None
    return int(number)


def screen_utility(result: dict[str, Any] | None, anchor_result: dict[str, Any] | None) -> float | None:
    """Net profit factor, scaled down under 35 trades, penalized when drawdown worsens."""
    pf = _profit_factor(result)
    trades = _trades(result)
    if pf is None or trades is None:
        return None
    utility = pf * min(1.0, trades / float(MIN_TRADES))
    drawdown = _num(_dict(result).get("max_drawdown_net_pct"))
    anchor_dd = _num(_dict(anchor_result).get("max_drawdown_net_pct"))
    if drawdown is not None and anchor_dd is not None and drawdown > anchor_dd:
        utility -= (drawdown - anchor_dd) / DD_PENALTY_SCALE
    return utility


def data_quality_reason(result: dict[str, Any] | None) -> str | None:
    payload = _dict(result)
    integrity = _dict(payload.get("data_integrity"))
    fallback = _intish(integrity.get("history_fallback_used"))
    fetch_total = _intish(integrity.get("history_fetch_total"))
    empty = _intish(integrity.get("history_fetch_empty"))
    share = (fallback / fetch_total) if fetch_total else (1.0 if fallback else 0.0)
    if fallback > 0 and share >= FALLBACK_SHARE:
        return "History fallback share is high. Stop. Do not search on this window."
    if empty > 0:
        return "Price history is incomplete. Stop. Do not search on this window."
    quality = payload.get("data_quality")
    if isinstance(quality, dict) and str(quality.get("tone") or "") in {"warn", "bad"}:
        return str(quality.get("summary") or "Data quality is degraded. Stop.")
    if isinstance(quality, str) and quality.strip().lower() in {"degraded", "bad", "warn"}:
        return "Data quality is degraded. Stop."
    return None


def _capacity_censored(result: dict[str, Any] | None) -> bool:
    diag = _dict(_dict(result).get("diagnostics"))
    entries = _intish(diag.get("entries"))
    dropped = _intish(diag.get("capital_filtered")) + _intish(diag.get("position_limit_filtered"))
    if dropped <= 0:
        return False
    return entries == 0 or dropped >= max(3, int(entries * CAPACITY_SHARE))


def _exit_shares(result: dict[str, Any] | None) -> dict[str, float]:
    diag = _dict(_dict(result).get("diagnostics"))
    keys = ("exits_trailing_stop", "exits_time_exit", "exits_sma50_break", "exits_vcp_invalidation")
    counts = {key: _intish(diag.get(key)) for key in keys}
    total = sum(counts.values())
    if total <= 0:
        return {}
    return {key: counts[key] / total for key in keys}


def _top_funnel(result: dict[str, Any] | None) -> str | None:
    diag = _dict(_dict(result).get("diagnostics"))
    keys = ("quality_gates_filtered", "stage2_fail", "vcp_fail")
    best_key = None
    best_n = 0
    for key in keys:
        count = _intish(diag.get(key))
        if count > best_n:
            best_key = key
            best_n = count
    return best_key


def implicated_family(result: dict[str, Any] | None) -> str | None:
    shares = _exit_shares(result)
    if shares.get("exits_trailing_stop", 0.0) >= 0.5 or shares.get("exits_time_exit", 0.0) >= 0.5:
        return "exit"
    funnel = _top_funnel(result)
    if funnel == "quality_gates_filtered":
        return "quality"
    if funnel == "stage2_fail":
        return "stage2"
    if funnel == "vcp_fail":
        return "vcp"
    return None


def _first_probe(family: str, values: dict[str, Any], result: dict[str, Any] | None) -> tuple[str, str] | None:
    if family == "stage2":
        return ("stage2_52w_pct", "down")
    if family == "vcp":
        if _step_value("vcp_days", values["vcp_days"], "down") is not None:
            return ("vcp_days", "down")
        return ("scan_vcp_gate_mode", "down")
    if family == "quality":
        if values["quality_gates_mode"] in {"hard", "soft"}:
            return ("quality_gates_mode", "down")
        return ("quality_min_signal_score", "down")
    if family == "rank":
        return ("rank_filter_shadow_min_percentile_rank_v2", "down")
    if family == "exit":
        shares = _exit_shares(result)
        if shares.get("exits_trailing_stop", 0.0) >= 0.5:
            return ("backtest_min_hold_days_before_trail", "up")
        return ("backtest_hold_days", "up")
    if family == "capacity":
        return ("backtest_portfolio_max_positions", "up")
    return None


def _dead_key(knob: str, direction: str) -> tuple[str, str]:
    return (knob, direction)


def _is_dead(dead: list[dict[str, Any]], knob: str, direction: str) -> bool:
    want = _dead_key(knob, direction)
    for row in dead:
        if _dead_key(str(row.get("knob") or ""), str(row.get("direction") or "")) == want:
            return True
    return False


def _step_is_harmful(parent: dict[str, Any] | None, child: dict[str, Any] | None) -> bool:
    parent_pf = _profit_factor(parent)
    child_pf = _profit_factor(child)
    parent_n = _trades(parent)
    child_n = _trades(child)
    if child_n == 0:
        return True
    if None in (parent_pf, child_pf, parent_n, child_n):
        return False
    return bool(child_pf < parent_pf and child_n < parent_n)


def dead_directions(trials: list[dict[str, Any]], anchor_result: dict[str, Any] | None) -> list[dict[str, Any]]:
    by_id = {str(row.get("run_id") or ""): row for row in trials}
    found: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for row in trials:
        if str(row.get("role") or "") != "screen" or str(row.get("status") or "") != "success":
            continue
        knob = str(row.get("knob") or "")
        direction = str(row.get("direction") or "")
        if not knob or not direction:
            continue
        parent = by_id.get(str(row.get("parent_run_id") or ""))
        parent_result = _dict(parent).get("result") if parent else anchor_result
        if not _step_is_harmful(parent_result if isinstance(parent_result, dict) else anchor_result, row.get("result")):
            continue
        key = _dead_key(knob, direction)
        if key in seen:
            continue
        seen.add(key)
        found.append({"knob": knob, "direction": direction, "family": KNOB_FAMILY.get(knob)})
    return found


def _values_key(values: dict[str, Any]) -> str:
    parts = [f"{knob}={_coerce(knob, values.get(knob, ANCHOR_DEFAULTS[knob]))}" for knob in ANCHOR_DEFAULTS]
    return "|".join(parts)


def trial_fingerprint(spec: dict[str, Any]) -> str:
    values = resolved_overrides(spec)
    years = window_years(spec)
    return f"{_universe_id(spec)}|{years}|{_values_key(values)}"


def _changed_families(before: dict[str, Any], after: dict[str, Any]) -> set[str]:
    families: set[str] = set()
    for knob, family in KNOB_FAMILY.items():
        if not _same(before.get(knob), after.get(knob)):
            families.add(family)
    return families


def _family_screens(trials: list[dict[str, Any]], family: str) -> list[dict[str, Any]]:
    rows = []
    for row in trials:
        if str(row.get("role") or "") != "screen":
            continue
        if str(row.get("status") or "") != "success":
            continue
        if str(row.get("family") or "") != family:
            continue
        if not isinstance(row.get("result"), dict):
            continue
        rows.append(row)
    return rows


def _anchor_trial(trials: list[dict[str, Any]]) -> dict[str, Any] | None:
    for row in trials:
        if str(row.get("role") or "") == "anchor":
            return row
    return trials[0] if trials else None


def _in_flight(trials: list[dict[str, Any]]) -> bool:
    return any(str(row.get("status") or "") in {"queued", "running", "pending"} for row in trials)


def _beats(result: dict[str, Any] | None, anchor_result: dict[str, Any] | None) -> bool:
    trades = _trades(result)
    if trades is None or trades < MIN_TRADES:
        return False
    utility = screen_utility(result, anchor_result)
    anchor_u = screen_utility(anchor_result, anchor_result)
    if utility is None or anchor_u is None:
        return False
    return utility >= anchor_u + MIN_UTILITY_DELTA


def _same_window(left: dict[str, Any], right: dict[str, Any]) -> bool:
    return _universe_id(left) == _universe_id(right) and window_years(left) == window_years(right)


def _search_context(
    anchor_spec: dict[str, Any],
    trials: list[dict[str, Any]],
    anchor_result: dict[str, Any] | None,
    anchor_run_id: str | None,
) -> tuple[dict[str, Any], dict[str, Any] | None, str | None]:
    """Knob search stays on the latest anchor or widen that already has 35 trades."""
    chosen_spec = anchor_spec
    chosen_result = anchor_result
    chosen_id = anchor_run_id
    for row in trials:
        if str(row.get("status") or "") != "success":
            continue
        if str(row.get("role") or "") not in {"anchor", "widen"}:
            continue
        result = row.get("result")
        if not isinstance(result, dict) or (_trades(result) or 0) < MIN_TRADES:
            continue
        spec = _dict(row.get("spec"))
        if not spec.get("start_date"):
            continue
        chosen_spec = spec
        chosen_result = result
        chosen_id = str(row.get("run_id") or "") or chosen_id
    return chosen_spec, chosen_result, chosen_id


def _on_context(row: dict[str, Any], context_spec: dict[str, Any]) -> bool:
    spec = _dict(row.get("spec"))
    if not spec:
        return False
    return _same_window(spec, context_spec)


def _base_values(
    anchor_values: dict[str, Any],
    trials: list[dict[str, Any]],
    anchor_result: dict[str, Any] | None,
) -> tuple[dict[str, Any], str | None]:
    """Lock knobs from families that already beat the anchor. Parent is the best beater."""
    base = dict(anchor_values)
    parent_id: str | None = None
    best_utility = screen_utility(anchor_result, anchor_result)
    for family in FAMILY_ORDER:
        beaters = []
        for row in _family_screens(trials, family):
            if _beats(row.get("result"), anchor_result):
                beaters.append(row)
        if not beaters:
            continue
        winner = max(beaters, key=lambda row: screen_utility(row.get("result"), anchor_result) or -1e9)
        winner_values = resolved_overrides(_dict(winner.get("spec")))
        for knob in FAMILY_KNOBS[family]:
            base[knob] = winner_values[knob]
        utility = screen_utility(winner.get("result"), anchor_result)
        if best_utility is None or (utility is not None and utility >= best_utility):
            best_utility = utility
            parent_id = str(winner.get("run_id") or "") or None
    return base, parent_id


def _legal_step(
    values: dict[str, Any],
    knob: str,
    direction: str,
    dead: list[dict[str, Any]],
    tried: set[str],
    template: dict[str, Any],
) -> dict[str, Any] | None:
    if _is_dead(dead, knob, direction):
        return None
    nxt = _step_value(knob, values.get(knob), direction)
    if nxt is None or _same(nxt, values.get(knob)):
        return None
    candidate = dict(values)
    candidate[knob] = nxt
    spec = spec_from_values(template, candidate)
    if trial_fingerprint(spec) in tried:
        return None
    changed = _changed_families(values, candidate)
    if changed != {KNOB_FAMILY[knob]}:
        return None
    return {"knob": knob, "direction": direction, "values": candidate, "spec": spec, "family": KNOB_FAMILY[knob]}


def _probe_step(
    family: str,
    values: dict[str, Any],
    result: dict[str, Any] | None,
    dead: list[dict[str, Any]],
    tried: set[str],
    template: dict[str, Any],
) -> dict[str, Any] | None:
    probe = _first_probe(family, values, result)
    if probe is None:
        return None
    knob, direction = probe
    found = _legal_step(values, knob, direction, dead, tried, template)
    if found is not None:
        return found
    opposite = "down" if direction == "up" else "up"
    found = _legal_step(values, knob, opposite, dead, tried, template)
    if found is not None:
        return found
    for other in FAMILY_KNOBS[family]:
        if other == knob:
            continue
        for alt in ("down", "up"):
            found = _legal_step(values, other, alt, dead, tried, template)
            if found is not None:
                return found
    return None


def _ordinal(knob: str, value: Any) -> float:
    return float(_grid_index(knob, value))


def _forest_step(
    family: str,
    values: dict[str, Any],
    trials: list[dict[str, Any]],
    anchor_result: dict[str, Any] | None,
    dead: list[dict[str, Any]],
    tried: set[str],
    template: dict[str, Any],
) -> dict[str, Any] | None:
    screens = _family_screens(trials, family)
    if len(screens) < FOREST_MIN_TRIALS:
        return None
    knobs = FAMILY_KNOBS[family]
    train: list[tuple[dict[str, Any], float]] = []
    anchor_utility = screen_utility(anchor_result, anchor_result)
    if anchor_utility is not None:
        train.append((values, anchor_utility))
    for row in screens:
        utility = screen_utility(row.get("result"), anchor_result)
        if utility is None:
            continue
        train.append((resolved_overrides(_dict(row.get("spec"))), utility))
    if len(train) < FOREST_MIN_TRIALS:
        return None
    candidates = []
    for knob in knobs:
        for direction in ("down", "up"):
            step = _legal_step(values, knob, direction, dead, tried, template)
            if step is not None:
                candidates.append(step)
    if not candidates:
        return None
    try:
        from sklearn.ensemble import RandomForestRegressor
    except ImportError:
        return None
    model = RandomForestRegressor(
        n_estimators=40,
        max_depth=3,
        random_state=0,
        n_jobs=1,
    )
    features = [[_ordinal(knob, row_values[knob]) for knob in knobs] for row_values, _utility in train]
    target = [utility for _row_values, utility in train]
    model.fit(features, target)
    best: dict[str, Any] | None = None
    best_lcb = -1e18
    for step in candidates:
        point = [[_ordinal(knob, step["values"][knob]) for knob in knobs]]
        preds = [float(est.predict(point)[0]) for est in model.estimators_]
        if not preds:
            continue
        mean = sum(preds) / len(preds)
        variance = sum((item - mean) ** 2 for item in preds) / len(preds)
        lcb = mean - math.sqrt(variance)
        if lcb > best_lcb:
            best_lcb = lcb
            best = step
    if best is None:
        return None
    changed = _changed_families(values, best["values"])
    if changed != {family}:
        return None
    best["used_model"] = True
    best["lcb"] = best_lcb
    return best


def _knob_phrase(knob: str, before: Any, after: Any) -> str:
    return f"{knob} { _json_value(knob, before) } → { _json_value(knob, after) }"


def _factor_effects(
    trials: list[dict[str, Any]],
    anchor_result: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    anchor_u = screen_utility(anchor_result, anchor_result)
    rows: list[dict[str, Any]] = []
    for family in (*FAMILY_ORDER, "capacity"):
        screens = _family_screens(trials, family)
        if not screens:
            rows.append(
                {
                    "family": family,
                    "label": FAMILY_LABEL[family],
                    "trials": 0,
                    "best_utility_delta": None,
                    "note": "Not tested",
                }
            )
            continue
        scored = [(row, screen_utility(row.get("result"), anchor_result)) for row in screens]
        scored = [(row, utility) for row, utility in scored if utility is not None]
        if not scored or anchor_u is None:
            rows.append(
                {
                    "family": family,
                    "label": FAMILY_LABEL[family],
                    "trials": len(screens),
                    "best_utility_delta": None,
                    "note": "No utility yet",
                }
            )
            continue
        _best_row, best_u = max(scored, key=lambda item: item[1])
        delta = best_u - anchor_u
        note = f"Best screen utility {delta:+.3f} vs anchor"
        rows.append(
            {
                "family": family,
                "label": FAMILY_LABEL[family],
                "trials": len(screens),
                "best_utility_delta": round(delta, 4),
                "note": note,
            }
        )
    return rows


def _proposal(
    *,
    action: str,
    hypothesis: str,
    rationale: str,
    trials: list[dict[str, Any]],
    anchor_result: dict[str, Any] | None,
    dead: list[dict[str, Any]],
    family: str | None = None,
    spec: dict[str, Any] | None = None,
    role: str | None = None,
    knob: str | None = None,
    direction: str | None = None,
    parent_run_id: str | None = None,
    used_model: bool = False,
    decision_kind: str | None = None,
) -> dict[str, Any]:
    hint = MULTI_ERA_HINT if action == "hold" else None
    return {
        "action": action,
        "hypothesis": hypothesis,
        "rationale": rationale,
        "family": family,
        "spec": spec,
        "role": role,
        "knob": knob,
        "direction": direction,
        "parent_run_id": parent_run_id,
        "used_model": used_model,
        "dead_directions": dead,
        "factor_effects": _factor_effects(trials, anchor_result),
        "multi_era_hint": hint,
        "decision_kind": decision_kind,
    }


def _stall(trials: list[dict[str, Any]], anchor_result: dict[str, Any] | None) -> bool:
    screens = [
        row
        for row in trials
        if str(row.get("role") or "") == "screen" and str(row.get("status") or "") == "success"
    ]
    if len(screens) < FOREST_MIN_TRIALS:
        return False
    recent = screens[-STALL_ROUNDS:]
    return all(not _beats(row.get("result"), anchor_result) for row in recent)


def _widen_spec(spec: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
    uni = _universe_id(spec)
    years = window_years(spec) or 0
    if uni not in {"sp500", "sp1500"}:
        nxt = "sp1500" if uni == "nasdaq100" else "nasdaq100"
        widened = _with_universe(spec, nxt)
        if (window_years(widened) or 0) >= 5:
            overrides = _dict(widened.get("overrides"))
            overrides["skip_mirofish"] = True
            widened["overrides"] = overrides
        return widened, f"Sample is thin. Replay the same knobs on {nxt}."
    if years < 5:
        return _shift_years(spec, 5), "Sample is thin. Replay the same knobs on 5 years."
    if years < 8:
        return _shift_years(spec, 8), "Sample is thin. Replay the same knobs on 8 years."
    return None, "Sample is still thin on a large index and an 8-year window. Stop."


def _confirm_years(years: int | None) -> int | None:
    current = years or 0
    if current < 5:
        return 5
    if current < 8:
        return 8
    return None


def _tried_fingerprints(trials: list[dict[str, Any]]) -> set[str]:
    found: set[str] = set()
    for row in trials:
        spec = row.get("spec")
        if isinstance(spec, dict):
            found.add(trial_fingerprint(spec))
    return found


def _screen_from_step(
    step: dict[str, Any],
    *,
    hypothesis: str,
    rationale: str,
    trials: list[dict[str, Any]],
    anchor_result: dict[str, Any] | None,
    dead: list[dict[str, Any]],
    parent_run_id: str | None,
    template: dict[str, Any],
) -> dict[str, Any]:
    spec = spec_from_values(template, step["values"])
    before = resolved_overrides(template)
    phrase = _knob_phrase(step["knob"], before.get(step["knob"]), step["values"][step["knob"]])
    return _proposal(
        action="queue",
        hypothesis=hypothesis or f"Change only {FAMILY_LABEL[step['family']]}: {phrase}.",
        rationale=rationale,
        trials=trials,
        anchor_result=anchor_result,
        dead=dead,
        family=step["family"],
        spec=spec,
        role="screen",
        knob=step["knob"],
        direction=step["direction"],
        parent_run_id=parent_run_id,
        used_model=bool(step.get("used_model")),
    )


def propose_next(study: dict[str, Any], trials: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Return exactly one next action for a study.

    ``trials`` rows need ``role``, ``status``, ``spec``, and ``result`` (when success).
    Hard vetoes beat the forest. The returned spec changes one factor family versus
    the current base, unless the action widens the sample or confirms another horizon.
    """
    payload = study if isinstance(study, dict) else {}
    rows = list(trials if trials is not None else payload.get("trials") or [])
    anchor_spec = _dict(payload.get("anchor_spec"))
    budget = int(payload.get("budget") or TRIAL_BUDGET)
    anchor = _anchor_trial(rows)
    anchor_result = anchor.get("result") if anchor and isinstance(anchor.get("result"), dict) else None
    dead = dead_directions(rows, anchor_result)

    if not anchor_spec.get("start_date") or not anchor_spec.get("end_date"):
        return _proposal(
            action="start",
            hypothesis="Start a study from the current index and window.",
            rationale="The first replay is the anchor. Later tests change one factor.",
            trials=rows,
            anchor_result=None,
            dead=dead,
        )

    if _in_flight(rows):
        return _proposal(
            action="wait",
            hypothesis="Wait for the replay already queued.",
            rationale="The next test is chosen after this result, not before it.",
            trials=rows,
            anchor_result=anchor_result,
            dead=dead,
        )

    if anchor is None or str(anchor.get("status") or "") != "success" or anchor_result is None:
        if anchor is not None and str(anchor.get("status") or "") == "failed":
            return _proposal(
                action="stop",
                hypothesis="Stop. The anchor replay failed.",
                rationale="Fix the error and start a new study. Do not search without a control.",
                trials=rows,
                anchor_result=None,
                dead=dead,
                decision_kind="abandon",
            )
        spec = copy.deepcopy(anchor_spec)
        return _proposal(
            action="queue",
            hypothesis="Anchor replay. Later tests change one factor at a time.",
            rationale="Run the control once on this index and window before moving any knob.",
            trials=rows,
            anchor_result=None,
            dead=dead,
            spec=spec,
            role="anchor",
        )

    latest = rows[-1]
    latest_result = latest.get("result") if isinstance(latest.get("result"), dict) else None
    quality = data_quality_reason(latest_result if latest_result is not None else anchor_result)
    if quality:
        return _proposal(
            action="stop",
            hypothesis="Stop. Data quality is not good enough to search.",
            rationale=quality,
            trials=rows,
            anchor_result=anchor_result,
            dead=dead,
            decision_kind="abandon",
        )

    if len(rows) >= budget:
        return _proposal(
            action="stop",
            hypothesis=f"Stop. Trial budget of {budget} is used.",
            rationale="No further child spec. A longer search would fit the screen window.",
            trials=rows,
            anchor_result=anchor_result,
            dead=dead,
            decision_kind="abandon",
        )

    if str(latest.get("role") or "") == "confirm" and _beats(latest_result, anchor_result):
        return _proposal(
            action="hold",
            hypothesis="Hold. The confirm window still leads the anchor.",
            rationale=MULTI_ERA_HINT,
            trials=rows,
            anchor_result=anchor_result,
            dead=dead,
            decision_kind="confirm",
        )

    context_spec, context_result, context_id = _search_context(
        anchor_spec,
        rows,
        anchor_result,
        str(anchor.get("run_id") or "") or None,
    )
    context_rows = [row for row in rows if _on_context(row, context_spec)] or [anchor]
    anchor_result = context_result
    dead = dead_directions(context_rows, anchor_result)
    anchor_values = resolved_overrides(context_spec)
    base_values, beater_parent = _base_values(anchor_values, context_rows, anchor_result)
    parent_run_id = beater_parent or context_id
    template = spec_from_values(context_spec, base_values)
    tried = _tried_fingerprints(rows)

    latest_spec = _dict(latest.get("spec")) or template
    if (
        str(latest.get("role") or "") in {"anchor", "screen"}
        and _same_window(latest_spec, template)
        and _beats(latest_result, anchor_result)
    ):
        years = _confirm_years(window_years(template))
        if years is None:
            return _proposal(
                action="hold",
                hypothesis="Hold. This screen already uses the long window.",
                rationale=MULTI_ERA_HINT,
                trials=rows,
                anchor_result=anchor_result,
                dead=dead,
                spec=None,
                decision_kind="confirm",
            )
        confirm_spec = spec_from_values(_shift_years(template, years), base_values)
        if trial_fingerprint(confirm_spec) not in tried:
            return _proposal(
                action="queue",
                hypothesis=f"Confirm the same knobs on {years} years. Do not change another factor.",
                rationale=(
                    "This screen beat the anchor on utility with enough trades. "
                    "The next replay is the other horizon, not another knob."
                ),
                trials=rows,
                anchor_result=anchor_result,
                dead=dead,
                family=None,
                spec=confirm_spec,
                role="confirm",
                parent_run_id=parent_run_id,
            )

    judge = latest_result if latest_result is not None else anchor_result
    judge_spec = latest_spec if latest_result is not None else template
    trades = _trades(judge)
    if trades is None or trades < MIN_TRADES:
        widened, why = _widen_spec(spec_from_values(judge_spec, resolved_overrides(judge_spec)))
        if widened is None:
            return _proposal(
                action="stop",
                hypothesis="Stop. The sample cannot be widened further.",
                rationale=why,
                trials=rows,
                anchor_result=anchor_result,
                dead=dead,
                decision_kind="abandon",
            )
        return _proposal(
            action="queue",
            hypothesis=why,
            rationale="Fewer than 35 trades. Do not tune knobs on a thin sample.",
            trials=rows,
            anchor_result=anchor_result,
            dead=dead,
            spec=widened,
            role="widen",
            parent_run_id=str(latest.get("run_id") or "") or parent_run_id,
        )

    if _capacity_censored(judge):
        step = _legal_step(
            resolved_overrides(judge_spec),
            "backtest_portfolio_max_positions",
            "up",
            dead,
            tried,
            judge_spec,
        )
        if step is None:
            return _proposal(
                action="stop",
                hypothesis="Stop. Capacity is still censored at the position cap.",
                rationale="Raising max positions further is outside the study grid.",
                trials=rows,
                anchor_result=anchor_result,
                dead=dead,
                family="capacity",
                decision_kind="abandon",
            )
        before = resolved_overrides(judge_spec)["backtest_portfolio_max_positions"]
        after = step["values"]["backtest_portfolio_max_positions"]
        return _proposal(
            action="queue",
            hypothesis=f"The book dropped signals. Raise max positions from {before} to {after}.",
            rationale="Capacity censored this profit factor. Change positions only.",
            trials=rows,
            anchor_result=anchor_result,
            dead=dead,
            family="capacity",
            spec=step["spec"],
            role="capacity",
            knob=step["knob"],
            direction=step["direction"],
            parent_run_id=str(latest.get("run_id") or "") or parent_run_id,
        )

    def _finish_search(step: dict[str, Any], hypothesis: str, rationale: str) -> dict[str, Any]:
        screen_count = sum(
            1
            for row in context_rows
            if str(row.get("role") or "") == "screen" and str(row.get("status") or "") == "success"
        )
        first_model_pick = bool(step.get("used_model")) and screen_count == FOREST_MIN_TRIALS
        if _stall(context_rows, anchor_result) and not first_model_pick:
            return _proposal(
                action="stop",
                hypothesis="Stop. Three screens in a row did not beat the anchor.",
                rationale="Keep the anchor. Another knob on this window is not earned.",
                trials=rows,
                anchor_result=anchor_result,
                dead=dead,
                decision_kind="abandon",
            )
        return _screen_from_step(
            step,
            hypothesis=hypothesis,
            rationale=rationale,
            trials=rows,
            anchor_result=anchor_result,
            dead=dead,
            parent_run_id=parent_run_id,
            template=template,
        )

    implicated = implicated_family(judge)
    locked = {
        family
        for family in FAMILY_ORDER
        if any(_beats(row.get("result"), anchor_result) for row in _family_screens(context_rows, family))
    }
    search_order: list[str] = []
    if implicated and implicated not in locked:
        search_order.append(implicated)
    search_order.extend(family for family in FAMILY_ORDER if family not in search_order and family not in locked)
    search_order.extend(family for family in FAMILY_ORDER if family not in search_order)

    for family in search_order:
        screens = _family_screens(context_rows, family)
        if len(screens) >= FOREST_MIN_TRIALS:
            modeled = _forest_step(family, base_values, context_rows, anchor_result, dead, tried, template)
            if modeled is not None:
                phrase = _knob_phrase(
                    modeled["knob"],
                    base_values.get(modeled["knob"]),
                    modeled["values"][modeled["knob"]],
                )
                return _finish_search(
                    modeled,
                    f"Model pick inside {FAMILY_LABEL[family]} only: {phrase}.",
                    (
                        "At least four screens exist in this family. "
                        "The next neighbor is the highest lower confidence bound, still one family."
                    ),
                )
        if family in locked:
            continue
        step = _probe_step(family, base_values, judge, dead, tried, template)
        if step is None:
            continue
        return _finish_search(
            step,
            "",
            "One-factor step. Universe and dates stay on the anchor.",
        )

    return _proposal(
        action="stop",
        hypothesis="Stop. No untried one-factor neighbor is left.",
        rationale="Dead ends and the grid are exhausted. Do not jump two families at once.",
        trials=rows,
        anchor_result=anchor_result,
        dead=dead,
        decision_kind="abandon",
    )
