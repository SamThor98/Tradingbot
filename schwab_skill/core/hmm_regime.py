"""Advisory Gaussian HMM on SPY daily bars.

The model advises. Regime v2 and the order checks still decide. This module
never imports execution, never places an order, and only publishes the
forward-filtered state probability (not a Viterbi path or a smoothed posterior).

Features use completed bars. Each bar's z-score uses only earlier bars. A refit
trains through the prior session and is reused for 30 days, so the latest
decision bar is outside the fit.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

LOG = logging.getLogger(__name__)

FEATURE_NAMES: tuple[str, ...] = (
    "log_return",
    "realized_vol",
    "range_pct",
    "volume_ratio",
    "trend",
)
MIN_BARS = 756
MIN_PERIODS = 60
REFIT_DAYS = 30
STALE_DAYS = 5
TAKEOVER_PROB = 0.70
CONFIRM_BARS = 2
MIN_HOLD_BARS = 3
STRESS_NEXT_PROB = 0.25
UNCERTAIN_GAP = 0.15
BIC_MARGIN = 10.0
LL_DROP_FREEZE = 1.0
MAX_BARS = 1500
EM_ITERS = 20
CACHE_VERSION = 1
VIEW_TTL_SEC = 600.0
VIEW_TTL_FAIL_SEC = 60.0
_VIEW_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}

PLAYBOOKS: dict[str, dict[str, Any]] = {
    "CALM_UP": {
        "style": "trend-following",
        "entry": "New longs only while this state stays latched.",
        "exit": "Leave on a confirmed switch or when invalidation hits.",
        "stop": "Keep the position stop already on the book. The HMM sets none.",
        "take_profit": "No HMM target. The exit manager still owns takes.",
        "max_size": "Advisory multiplier is the filtered probability, cut in half if stress is next.",
        "invalidation": "Probability under 0.70, top-two gap under 0.15, or stress-next at 0.25 or above.",
        "book_allows_entry": True,
    },
    "CHOP": {
        "style": "mean-reversion",
        "entry": "Fade extensions back toward the mean. This momentum book does not take that trade.",
        "exit": "Stand aside until a trend state latches.",
        "stop": "No new risk, so no new stop.",
        "take_profit": "None.",
        "max_size": "Zero for this book.",
        "invalidation": "A trend state clears 0.70 for two bars.",
        "book_allows_entry": False,
    },
    "DRIFT_DOWN": {
        "style": "stand-aside",
        "entry": "No new longs.",
        "exit": "Stay flat until the latched state is no longer a downtrend.",
        "stop": "No new risk from this card.",
        "take_profit": "None.",
        "max_size": "Zero.",
        "invalidation": "Downtrend state loses the latch.",
        "book_allows_entry": False,
    },
    "CRASH": {
        "style": "stand-aside",
        "entry": "No new longs. Reduced size is still zero on this book.",
        "exit": "Stay flat while crash remains likely.",
        "stop": "No new risk from this card.",
        "take_profit": "None.",
        "max_size": "Zero.",
        "invalidation": "Crash probability falls and another state latches.",
        "book_allows_entry": False,
    },
    "MIXED": {
        "style": "stand-aside",
        "entry": "No new longs while the label is mixed.",
        "exit": "Wait for a named state.",
        "stop": "No new risk from this card.",
        "take_profit": "None.",
        "max_size": "Zero.",
        "invalidation": "A named state latches.",
        "book_allows_entry": False,
    },
}

_BAD_DATA_QUALITY = {"conflict", "bad", "stale", "outage", "fail", "failed", "degraded", "missing"}


def unavailable_view(reason: str) -> dict[str, Any]:
    """Card payload when there is nothing safe to show as a state."""
    return {
        "status": "unavailable",
        "reason": reason,
        "advisory_only": True,
        "orders_placed": 0,
        "live_gate": "regime_v2",
        "probability_kind": "forward_filtered",
        "action": "flat",
        "action_reason": reason,
        "model_size_multiplier": 0.0,
        "advisory_size_multiplier": 0.0,
        "entries_frozen": False,
        "freeze_reasons": [],
    }


def choose_states(bic_by_states: dict[int, float], *, margin: float = BIC_MARGIN) -> int:
    """Smallest state count whose BIC is within ``margin`` of the best score."""
    finite = {int(k): float(v) for k, v in bic_by_states.items() if np.isfinite(v)}
    if not finite:
        raise ValueError("no finite BIC")
    best = min(finite.values())
    eligible = [k for k, v in finite.items() if v <= best + margin]
    return min(eligible)


def build_feature_frame(bars: pd.DataFrame) -> pd.DataFrame:
    """Causal feature columns. Row t uses the completed bar t and earlier bars only."""
    frame = _ohlcv(bars)
    close = frame["close"].astype(float)
    high = frame["high"].astype(float)
    low = frame["low"].astype(float)
    volume = frame["volume"].astype(float).clip(lower=0.0)
    log_ret = np.log(close / close.shift(1))
    realized = log_ret.rolling(20, min_periods=20).std(ddof=0) * np.sqrt(252.0)
    span = (high - low) / close.where(close > 0)
    vol_ma = volume.rolling(20, min_periods=20).mean()
    vol_ratio = volume / vol_ma.where(vol_ma > 0)
    sma50 = close.rolling(50, min_periods=50).mean()
    trend = close / sma50.where(sma50 > 0) - 1.0
    feat = pd.DataFrame(
        {
            "log_return": log_ret,
            "realized_vol": realized,
            "range_pct": span,
            "volume_ratio": vol_ratio,
            "trend": trend,
        },
        index=frame.index,
    )
    return feat.replace([np.inf, -np.inf], np.nan)


def causal_zscores(features: pd.DataFrame, *, min_periods: int = MIN_PERIODS) -> pd.DataFrame:
    """Z-score each column with the mean and std of strictly earlier rows."""
    past_mean = features.expanding(min_periods=min_periods).mean().shift(1)
    past_std = features.expanding(min_periods=min_periods).std(ddof=0).shift(1)
    flat = past_mean.notna() & (past_std.isna() | (past_std < 1e-8))
    safe_std = past_std.mask(past_std < 1e-8)
    scored = (features - past_mean) / safe_std
    # A column with no historical variation carries no information. Keep the row.
    scored = scored.mask(flat, 0.0)
    return scored.replace([np.inf, -np.inf], np.nan)


def apply_decision_rules(
    filtered: np.ndarray,
    *,
    labels: list[str],
    stress: list[bool],
    transition: np.ndarray,
    dates: list[pd.Timestamp] | None = None,
) -> dict[str, Any]:
    """Latch a state from forward-filtered probabilities. Past rows ignore later rows."""
    probs = np.asarray(filtered, dtype=np.float64)
    trans = np.asarray(transition, dtype=np.float64)
    if probs.ndim != 2 or probs.shape[1] != len(labels):
        raise ValueError("filtered shape does not match labels")
    active: int | None = None
    pending: int | None = None
    pending_count = 0
    hold_left = 0
    bars_in_state = 0
    switches: list[dict[str, Any]] = []
    actions: list[str] = []
    last: dict[str, Any] = {}
    stress_idx = [i for i, flag in enumerate(stress) if flag]

    for t in range(probs.shape[0]):
        row = probs[t]
        order = np.argsort(-row)
        top = int(order[0])
        second = int(order[1]) if len(order) > 1 else top
        gap = float(row[top] - row[second])
        uncertain = gap < UNCERTAIN_GAP
        nxt = row @ trans
        stress_next = float(sum(float(nxt[i]) for i in stress_idx)) if stress_idx else 0.0
        leader = top if float(row[top]) >= TAKEOVER_PROB else None
        if leader is None:
            pending = None
            pending_count = 0
        elif leader == active:
            pending = leader
            pending_count = CONFIRM_BARS
        elif leader == pending:
            pending_count += 1
        else:
            pending = leader
            pending_count = 1

        switched = False
        prev = active
        can_switch = (
            leader is not None
            and pending_count >= CONFIRM_BARS
            and not uncertain
            and hold_left <= 0
            and leader != active
        )
        if can_switch:
            active = leader
            switched = True
            hold_left = MIN_HOLD_BARS
            bars_in_state = 1
            as_of = _iso(dates[t]) if dates is not None else str(t)
            switches.append(
                {
                    "as_of": as_of,
                    "from_label": labels[prev] if prev is not None else None,
                    "to_label": labels[leader],
                    "reason": f"filtered probability {float(row[leader]):.2f} held for {CONFIRM_BARS} bars",
                }
            )
            if len(switches) > 8:
                switches = switches[-8:]
        elif active is not None:
            bars_in_state += 1
            hold_left = max(0, hold_left - 1)

        if active is None:
            action = "size_zero"
            model_size = 0.0
            if uncertain:
                reason = f"top two states are within {UNCERTAIN_GAP:.2f} (gap {gap:.2f}); size to zero"
            else:
                reason = f"no state has held {TAKEOVER_PROB:.2f} for {CONFIRM_BARS} bars"
        elif uncertain:
            action = "size_zero"
            model_size = 0.0
            reason = f"top two states are within {UNCERTAIN_GAP:.2f} (gap {gap:.2f}); size to zero"
        elif stress_next >= STRESS_NEXT_PROB:
            action = "cut_half"
            model_size = 0.5 * float(row[active])
            reason = f"P(stress next)={stress_next:.2f} is at least {STRESS_NEXT_PROB:.2f}; cut size to half"
        elif switched:
            action = "switch"
            model_size = float(row[active])
            reason = f"switched to {labels[active]} at filtered probability {float(row[active]):.2f}"
        else:
            action = "hold"
            model_size = float(row[active])
            reason = f"holding {labels[active]} at filtered probability {float(row[active]):.2f}"

        actions.append(action)
        last = {
            "action": action,
            "action_reason": reason,
            "model_size_multiplier": model_size,
            "active_index": active,
            "current_label": labels[active] if active is not None else None,
            "current_probability": float(row[active]) if active is not None else None,
            "leading_label": labels[top],
            "leading_probability": float(row[top]),
            "uncertain": uncertain,
            "top_gap": gap,
            "stress_next_probability": stress_next,
            "next_probabilities": [float(v) for v in nxt],
            "confirms": int(pending_count if pending is not None else 0),
            "bars_in_state": int(bars_in_state),
            "hold_bars_remaining": int(hold_left),
            "probabilities": [float(v) for v in row],
        }
    return {"last": last, "switches": switches, "actions": actions}


def evaluate_hmm(
    bars: pd.DataFrame,
    *,
    diagnostics: dict[str, Any] | None = None,
    cache_path: Path | None = None,
    min_bars: int = MIN_BARS,
    min_periods: int = MIN_PERIODS,
    max_iter: int = EM_ITERS,
    k_min: int = 2,
    k_max: int = 5,
    today: date | None = None,
    include_trace: bool = False,
) -> dict[str, Any]:
    """Fit or reuse a frozen HMM and score the latest bar with a causal filter."""
    diag = diagnostics or {}
    try:
        index, raw, scored = _aligned_matrices(bars, min_periods=min_periods)
    except ValueError as exc:
        return unavailable_view(str(exc))
    if len(index) > MAX_BARS:
        index = index[-MAX_BARS:]
        raw = raw[-MAX_BARS:]
        scored = scored[-MAX_BARS:]
    if len(index) < min_bars:
        view = unavailable_view("need_3y_history")
        view["bars_used"] = int(len(index))
        view["feature_names"] = list(FEATURE_NAMES)
        return view

    last_day = _day(index[-1])
    cached = _read_cache(cache_path)
    reused = False
    fit_through = _day(index[-2])
    model: dict[str, Any]
    if cached is not None and _cache_reusable(cached, last_day):
        model = cached
        reused = True
        fit_through = _day(cached["fit_through"])
    else:
        train_end = int(np.searchsorted(index.values, np.datetime64(fit_through), side="right"))
        if train_end < max(40, min_bars // 2):
            view = unavailable_view("short_train")
            view["bars_used"] = int(len(index))
            return view
        try:
            model = _fit_model(
                scored[:train_end],
                raw[:train_end],
                k_min=k_min,
                k_max=k_max,
                max_iter=max_iter,
            )
        except ValueError as exc:
            LOG.warning("HMM regime fit failed: %s", exc)
            return unavailable_view("fit_failed")
        model["fit_through"] = fit_through.date().isoformat()
        model["feature_names"] = list(FEATURE_NAMES)
        if cached is not None:
            drift = _label_drift(cached.get("labels"), model["labels"])
            if drift:
                model["freeze_reasons"] = list(model.get("freeze_reasons") or []) + [drift]
        _write_cache(cache_path, model)
        LOG.info(
            "HMM regime refit: states=%s through=%s",
            model.get("n_states"),
            model.get("fit_through"),
        )

    params = _params_from_model(model)
    filtered, pred_ll = _forward_filter(scored, params["pi"], params["A"], params["mu"], params["var"])
    dates = [pd.Timestamp(ts) for ts in index]
    rules = apply_decision_rules(
        filtered,
        labels=params["labels"],
        stress=params["stress"],
        transition=params["A"],
        dates=dates,
    )
    view = _assemble_view(
        model=model,
        params=params,
        rules=rules,
        filtered=filtered,
        pred_ll=pred_ll,
        index=index,
        fit_through=fit_through,
        reused=reused,
        today=today or date.today(),
        diagnostics=diag,
        bars_used=int(len(index)),
    )
    if include_trace:
        view["_trace_dates"] = [_iso(ts) for ts in dates]
        view["_trace_probs"] = filtered.tolist()
        view["_trace_actions"] = list(rules["actions"])
    return view


def load_spy_hmm_view(
    skill_dir: Path | str,
    diagnostics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Fetch SPY daily bars and return the advisory card. Failures stay flat."""
    root = Path(skill_dir)
    key = str(root.resolve())
    now = time.monotonic()
    hit = _VIEW_CACHE.get(key)
    if hit is not None and now - hit[0] < _ttl(hit[1]):
        return hit[1]
    try:
        from market_data import get_daily_history

        bars = get_daily_history("SPY", days=1600, skill_dir=root)
    except Exception as exc:
        LOG.warning("HMM regime SPY history failed: %s", exc)
        view = unavailable_view("history_unavailable")
        _VIEW_CACHE[key] = (now, view)
        return view
    if bars is None or getattr(bars, "empty", True):
        view = unavailable_view("history_empty")
        _VIEW_CACHE[key] = (now, view)
        return view
    try:
        view = evaluate_hmm(
            bars,
            diagnostics=diagnostics,
            cache_path=root / ".hmm_regime_spy.json",
        )
    except Exception as exc:
        LOG.warning("HMM regime evaluate failed: %s", exc)
        view = unavailable_view("evaluate_failed")
    _VIEW_CACHE[key] = (now, {k: v for k, v in view.items() if not str(k).startswith("_")})
    return _VIEW_CACHE[key][1]


def _ttl(view: dict[str, Any]) -> float:
    if view.get("status") == "unavailable":
        return VIEW_TTL_FAIL_SEC
    return VIEW_TTL_SEC


def _assemble_view(
    *,
    model: dict[str, Any],
    params: dict[str, Any],
    rules: dict[str, Any],
    filtered: np.ndarray,
    pred_ll: np.ndarray,
    index: pd.DatetimeIndex,
    fit_through: pd.Timestamp,
    reused: bool,
    today: date,
    diagnostics: dict[str, Any],
    bars_used: int,
) -> dict[str, Any]:
    last = dict(rules["last"])
    labels: list[str] = params["labels"]
    stress: list[bool] = params["stress"]
    trans: np.ndarray = params["A"]
    freeze_reasons = [str(r) for r in (model.get("freeze_reasons") or []) if r]
    train_end = int(np.searchsorted(index.values, np.datetime64(_day(fit_through)), side="right"))
    train_end = max(1, min(train_end, len(pred_ll)))
    train_ll = float(np.nanmean(pred_ll[1:train_end])) if train_end > 2 else None
    live_slice = pred_ll[max(1, len(pred_ll) - 21) :]
    live_ll = float(np.nanmean(live_slice)) if len(live_slice) else None
    if (
        train_ll is not None
        and live_ll is not None
        and np.isfinite(train_ll)
        and np.isfinite(live_ll)
        and live_ll < train_ll - LL_DROP_FREEZE
    ):
        freeze_reasons.append("live_loglik_drop")
    entries_frozen = bool(freeze_reasons)

    stale = (_day(today) - _day(index[-1])).days > STALE_DAYS
    bad_dq = _bad_quality(diagnostics.get("data_quality"))
    scan_blocked = bool(diagnostics.get("scan_blocked"))

    action = str(last.get("action") or "flat")
    reason = str(last.get("action_reason") or "")
    model_size = float(last.get("model_size_multiplier") or 0.0)
    active_label = last.get("current_label")
    playbook = _playbook(active_label)
    allows = bool(playbook and playbook["book_allows_entry"])
    advisory = model_size if allows and not entries_frozen else 0.0
    status = "ok"

    if bad_dq:
        status = "flat"
        action = "flat"
        model_size = 0.0
        advisory = 0.0
        reason = "data quality is not ok; flat"
        entries_frozen = True
        if "data_quality" not in freeze_reasons:
            freeze_reasons.append("data_quality")
    elif stale:
        status = "flat"
        action = "flat"
        model_size = 0.0
        advisory = 0.0
        reason = f"last SPY bar is older than {STALE_DAYS} days; flat"
    elif bool(last.get("uncertain")) or last.get("active_index") is None:
        status = "flat"
        action = "size_zero"
        model_size = 0.0
        advisory = 0.0
    elif entries_frozen:
        status = "flat"
        action = "entries_frozen"
        advisory = 0.0
        reason = "entries frozen: " + ", ".join(freeze_reasons)
    elif not allows:
        action = "stand_aside"
        advisory = 0.0
        reason = f"{active_label} playbook is {_playbook_style(playbook)}; this momentum book stands aside"
        status = "flat"
    elif action == "cut_half":
        status = "ok"
    else:
        status = "ok"

    if scan_blocked:
        reason = (reason + " Regime v2 still blocks the scan.").strip()

    durations = [_duration(float(trans[i, i])) for i in range(len(labels))]
    active_idx = last.get("active_index")
    states = []
    next_probs = last.get("next_probabilities") or [None] * len(labels)
    latest = filtered[-1]
    for i, label in enumerate(labels):
        states.append(
            {
                "state_id": i,
                "label": label,
                "probability": _round(float(latest[i])),
                "next_probability": _round(float(next_probs[i])) if next_probs[i] is not None else None,
                "expected_duration_bars": _round(durations[i], 2) if durations[i] is not None else None,
                "mean_log_return": _round(float(model["mean_log_return"][i]), 6),
                "realized_vol": _round(float(model["realized_vol"][i]), 4),
                "is_stress": bool(stress[i]),
                "is_active": active_idx == i,
            }
        )

    expected = durations[active_idx] if isinstance(active_idx, int) else None
    return {
        "status": status,
        "reason": None if status == "ok" else reason,
        "advisory_only": True,
        "orders_placed": 0,
        "live_gate": "regime_v2",
        "probability_kind": "forward_filtered",
        "as_of": _iso(index[-1]),
        "current_label": active_label,
        "current_probability": _round(last["current_probability"]) if last.get("current_probability") is not None else None,
        "leading_label": last.get("leading_label"),
        "leading_probability": _round(last["leading_probability"]) if last.get("leading_probability") is not None else None,
        "states": states,
        "stress_next_probability": _round(last.get("stress_next_probability")),
        "uncertain": bool(last.get("uncertain")),
        "top_gap": _round(last.get("top_gap")),
        "confirms": int(last.get("confirms") or 0),
        "confirms_required": CONFIRM_BARS,
        "takeover_probability": TAKEOVER_PROB,
        "min_hold_bars": MIN_HOLD_BARS,
        "bars_in_state": int(last.get("bars_in_state") or 0),
        "hold_bars_remaining": int(last.get("hold_bars_remaining") or 0),
        "expected_duration_bars": _round(expected, 2) if expected is not None else None,
        "action": action,
        "action_reason": reason,
        "model_size_multiplier": _round(model_size, 4),
        "advisory_size_multiplier": _round(advisory, 4),
        "active_playbook": _playbook_payload(active_label, playbook),
        "entries_frozen": entries_frozen,
        "freeze_reasons": freeze_reasons,
        "refit_as_of": str(model.get("fit_through") or fit_through.date().isoformat()),
        "refit_reused": reused,
        "selected_states": int(model["n_states"]),
        "bic_by_states": {str(k): _round(v, 2) for k, v in (model.get("bic_by_states") or {}).items()},
        "mean_train_loglik": _round(train_ll, 4) if train_ll is not None else None,
        "mean_live_loglik": _round(live_ll, 4) if live_ll is not None else None,
        "transition_labels": list(labels),
        "transition_matrix": [[_round(float(v), 3) for v in row] for row in trans],
        "recent_switches": rules["switches"],
        "thresholds": {
            "takeover_probability": TAKEOVER_PROB,
            "confirm_bars": float(CONFIRM_BARS),
            "min_hold_bars": float(MIN_HOLD_BARS),
            "stress_next_probability": STRESS_NEXT_PROB,
            "uncertain_gap": UNCERTAIN_GAP,
        },
        "scan_blocked_by_regime_v2": scan_blocked,
        "bars_used": bars_used,
        "feature_names": list(FEATURE_NAMES),
    }


def _playbook(label: str | None) -> dict[str, Any] | None:
    if not label:
        return None
    base = str(label)
    while len(base) > 2 and base[-1].isdigit() and base[-2] == "_":
        base = base[:-2]
    return PLAYBOOKS.get(base, PLAYBOOKS["MIXED"])


def _playbook_style(playbook: dict[str, Any] | None) -> str:
    if not playbook:
        return "unknown"
    return str(playbook.get("style") or "unknown")


def _playbook_payload(label: str | None, playbook: dict[str, Any] | None) -> dict[str, Any] | None:
    if playbook is None or label is None:
        return None
    return {
        "label": label,
        "style": str(playbook["style"]),
        "entry": str(playbook["entry"]),
        "exit": str(playbook["exit"]),
        "stop": str(playbook["stop"]),
        "take_profit": str(playbook["take_profit"]),
        "max_size": str(playbook["max_size"]),
        "invalidation": str(playbook["invalidation"]),
        "book_allows_entry": bool(playbook["book_allows_entry"]),
    }


def _bad_quality(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    return value.strip().lower() in _BAD_DATA_QUALITY


def _aligned_matrices(bars: pd.DataFrame, *, min_periods: int) -> tuple[pd.DatetimeIndex, np.ndarray, np.ndarray]:
    feat = build_feature_frame(bars)
    scored = causal_zscores(feat, min_periods=min_periods)
    mask = scored.notna().all(axis=1) & feat.notna().all(axis=1)
    scored = scored.loc[mask]
    raw = feat.loc[mask]
    if scored.empty:
        raise ValueError("no_feature_rows")
    return scored.index, raw.to_numpy(dtype=np.float64), scored.to_numpy(dtype=np.float64)


def _ohlcv(bars: pd.DataFrame) -> pd.DataFrame:
    if bars is None or not isinstance(bars, pd.DataFrame) or bars.empty:
        raise ValueError("history_empty")
    frame = bars.copy()
    frame.columns = [str(col).lower() for col in frame.columns]
    needed = {"open", "high", "low", "close", "volume"}
    if not needed.issubset(frame.columns):
        raise ValueError("bad_bars")
    if not isinstance(frame.index, pd.DatetimeIndex):
        if "date" in frame.columns:
            frame.index = pd.to_datetime(frame["date"])
        else:
            frame.index = pd.to_datetime(frame.index)
    frame = frame.sort_index()
    if getattr(frame.index, "tz", None) is not None:
        frame.index = frame.index.tz_convert("UTC").tz_localize(None)
    frame.index = pd.DatetimeIndex(pd.to_datetime(frame.index)).normalize()
    frame = frame[~frame.index.duplicated(keep="last")]
    close = pd.to_numeric(frame["close"], errors="coerce")
    frame = frame.loc[close > 0].copy()
    if frame.empty:
        raise ValueError("history_empty")
    return frame


def _fit_model(
    scored: np.ndarray,
    raw: np.ndarray,
    *,
    k_min: int,
    k_max: int,
    max_iter: int,
) -> dict[str, Any]:
    bic_by_states: dict[int, float] = {}
    fits: dict[int, dict[str, Any]] = {}
    upper = min(k_max, max(k_min, scored.shape[0] // 30))
    for k in range(k_min, upper + 1):
        try:
            fit = _em(scored, k, max_iter=max_iter)
        except ValueError:
            continue
        bic_by_states[k] = float(fit["bic"])
        fits[k] = fit
    if not fits:
        raise ValueError("fit_failed")
    chosen = choose_states(bic_by_states)
    fit = fits[chosen]
    labeled = _label_fit(fit, raw)
    labeled["bic_by_states"] = bic_by_states
    labeled["n_states"] = chosen
    labeled["freeze_reasons"] = []
    return labeled


def _em(x: np.ndarray, k: int, *, max_iter: int) -> dict[str, Any]:
    pi, trans, mu, var = _init_params(x, k)
    prev = -np.inf
    loglik = -np.inf
    gamma = np.full((x.shape[0], k), 1.0 / k)
    for _ in range(max_iter):
        log_b = _log_emit(x, mu, var)
        gamma, xi_sum, loglik = _expectation(log_b, np.log(pi + 1e-300), np.log(trans + 1e-300))
        if not np.isfinite(loglik):
            raise ValueError("non_finite_loglik")
        pi, trans, mu, var = _maximization(x, gamma, xi_sum)
        if loglik - prev < 1e-3:
            break
        prev = loglik
    n_params = (k - 1) + k * (k - 1) + 2 * k * x.shape[1]
    bic = -2.0 * float(loglik) + n_params * float(np.log(x.shape[0]))
    return {"pi": pi, "A": trans, "mu": mu, "var": var, "gamma": gamma, "loglik": float(loglik), "bic": bic}


def _init_params(x: np.ndarray, k: int) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    order = np.argsort(x[:, 0], kind="mergesort")
    bins = np.array_split(order, k)
    mu = np.zeros((k, x.shape[1]), dtype=np.float64)
    var = np.zeros((k, x.shape[1]), dtype=np.float64)
    for i, idx in enumerate(bins):
        block = x[idx] if len(idx) else x
        mu[i] = np.mean(block, axis=0)
        var[i] = np.var(block, axis=0) + 0.25
    trans = np.full((k, k), 0.05 / max(k - 1, 1), dtype=np.float64)
    np.fill_diagonal(trans, 0.95)
    trans = trans / trans.sum(axis=1, keepdims=True)
    pi = np.full(k, 1.0 / k, dtype=np.float64)
    return pi, trans, mu, var


def _expectation(
    log_b: np.ndarray,
    log_pi: np.ndarray,
    log_a: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, float]:
    t_len, k = log_b.shape
    log_alpha = np.empty((t_len, k), dtype=np.float64)
    log_alpha[0] = log_pi + log_b[0]
    for t in range(1, t_len):
        log_alpha[t] = log_b[t] + _logsumexp(log_alpha[t - 1][:, None] + log_a, axis=0)
    log_beta = np.zeros((t_len, k), dtype=np.float64)
    for t in range(t_len - 2, -1, -1):
        log_beta[t] = _logsumexp(log_a + log_b[t + 1][None, :] + log_beta[t + 1][None, :], axis=1)
    log_gamma = log_alpha + log_beta
    log_gamma -= _logsumexp(log_gamma, axis=1)[:, None]
    gamma = np.exp(log_gamma)
    loglik = float(_logsumexp(log_alpha[-1]))
    if t_len == 1:
        return gamma, np.zeros((k, k), dtype=np.float64), loglik
    log_xi = (
        log_alpha[:-1, :, None]
        + log_a[None, :, :]
        + log_b[1:, None, :]
        + log_beta[1:, None, :]
        - loglik
    )
    xi_sum = np.exp(log_xi).sum(axis=0)
    return gamma, xi_sum, loglik


def _maximization(
    x: np.ndarray,
    gamma: np.ndarray,
    xi_sum: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    k = gamma.shape[1]
    new_pi = gamma[0] / max(float(gamma[0].sum()), 1e-12)
    denom = gamma[:-1].sum(axis=0) + 1e-12
    new_trans = xi_sum / denom[:, None]
    new_trans = np.maximum(new_trans, 1e-4)
    new_trans = new_trans / new_trans.sum(axis=1, keepdims=True)
    weight = gamma.sum(axis=0) + 1e-12
    new_mu = (gamma.T @ x) / weight[:, None]
    new_var = np.empty_like(new_mu)
    for i in range(k):
        centered = x - new_mu[i]
        new_var[i] = np.sum(gamma[:, i][:, None] * centered * centered, axis=0) / weight[i]
    new_var = np.maximum(new_var, 1e-3)
    return new_pi, new_trans, new_mu, new_var


def _label_fit(fit: dict[str, Any], raw: np.ndarray) -> dict[str, Any]:
    gamma = np.asarray(fit["gamma"], dtype=np.float64)
    weight = gamma.sum(axis=0) + 1e-12
    mean_ret = (gamma * raw[:, 0][:, None]).sum(axis=0) / weight
    mean_vol = (gamma * raw[:, 1][:, None]).sum(axis=0) / weight
    order = np.argsort(mean_ret, kind="mergesort")
    pi, trans, mu, var = _permute(fit["pi"], fit["A"], fit["mu"], fit["var"], order)
    mean_ret = mean_ret[order]
    mean_vol = mean_vol[order]
    vol_cut = float(np.median(mean_vol))
    raw_labels = [_name_state(float(mean_ret[i]), float(mean_vol[i]), vol_cut) for i in range(len(order))]
    labels = _unique_labels(raw_labels)
    return {
        "pi": pi,
        "A": trans,
        "mu": mu,
        "var": var,
        "labels": labels,
        "stress": [_is_stress(label) for label in labels],
        "mean_log_return": mean_ret,
        "realized_vol": mean_vol,
        "loglik": fit["loglik"],
    }


def _name_state(mean_ret: float, mean_vol: float, vol_cut: float) -> str:
    if mean_ret <= -0.0015 and mean_vol >= vol_cut:
        return "CRASH"
    if mean_ret <= -0.0004:
        return "DRIFT_DOWN"
    if abs(mean_ret) < 0.0004:
        return "CHOP"
    if mean_ret >= 0.0004:
        return "CALM_UP"
    return "MIXED"


def _is_stress(label: str) -> bool:
    base = label
    while len(base) > 2 and base[-1].isdigit() and base[-2] == "_":
        base = base[:-2]
    return base in {"CRASH", "DRIFT_DOWN"}


def _unique_labels(labels: list[str]) -> list[str]:
    seen: dict[str, int] = {}
    out: list[str] = []
    for label in labels:
        seen[label] = seen.get(label, 0) + 1
        out.append(label if seen[label] == 1 else f"{label}_{seen[label]}")
    return out


def _permute(
    pi: np.ndarray,
    trans: np.ndarray,
    mu: np.ndarray,
    var: np.ndarray,
    order: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    return pi[order], trans[np.ix_(order, order)], mu[order], var[order]


def _forward_filter(
    x: np.ndarray,
    pi: np.ndarray,
    trans: np.ndarray,
    mu: np.ndarray,
    var: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    """Forward-filtered probabilities and one-step predictive log-likelihood."""
    log_b = _log_emit(x, mu, var)
    log_pi = np.log(np.asarray(pi, dtype=np.float64) + 1e-300)
    log_a = np.log(np.asarray(trans, dtype=np.float64) + 1e-300)
    t_len, k = log_b.shape
    filtered = np.zeros((t_len, k), dtype=np.float64)
    pred_ll = np.zeros(t_len, dtype=np.float64)
    state = log_pi + log_b[0]
    pred_ll[0] = float(_logsumexp(state))
    state = state - pred_ll[0]
    filtered[0] = np.exp(state)
    for t in range(1, t_len):
        pred = _logsumexp(state[:, None] + log_a, axis=0)
        joint = pred + log_b[t]
        pred_ll[t] = float(_logsumexp(joint))
        state = joint - pred_ll[t]
        filtered[t] = np.exp(state)
    return filtered, pred_ll


def _log_emit(x: np.ndarray, mu: np.ndarray, var: np.ndarray) -> np.ndarray:
    safe_var = np.maximum(var, 1e-3)
    diff = x[:, None, :] - mu[None, :, :]
    quad = np.sum((diff * diff) / safe_var[None, :, :], axis=2)
    log_det = np.sum(np.log(2.0 * np.pi * safe_var), axis=1)
    return -0.5 * (quad + log_det[None, :])


def _logsumexp(values: np.ndarray, axis: int | None = None) -> np.ndarray:
    arr = np.asarray(values, dtype=np.float64)
    peak = np.max(arr, axis=axis, keepdims=True)
    peak = np.where(np.isfinite(peak), peak, 0.0)
    total = peak + np.log(np.sum(np.exp(arr - peak), axis=axis, keepdims=True))
    if axis is None:
        return np.asarray(total, dtype=np.float64).reshape(())
    return np.squeeze(total, axis=axis)


def _params_from_model(model: dict[str, Any]) -> dict[str, Any]:
    pi = np.asarray(model["pi"], dtype=np.float64)
    trans = np.asarray(model["A"], dtype=np.float64)
    trans = np.maximum(trans, 0.0)
    trans = trans / np.maximum(trans.sum(axis=1, keepdims=True), 1e-12)
    pi = np.maximum(pi, 0.0)
    pi = pi / max(float(pi.sum()), 1e-12)
    return {
        "pi": pi,
        "A": trans,
        "mu": np.asarray(model["mu"], dtype=np.float64),
        "var": np.maximum(np.asarray(model["var"], dtype=np.float64), 1e-3),
        "labels": [str(label) for label in model["labels"]],
        "stress": [bool(flag) for flag in model["stress"]],
    }


def _label_drift(previous: Any, current: list[str]) -> str | None:
    if not isinstance(previous, list) or not previous:
        return None
    prev = {_strip_suffix(str(label)) for label in previous}
    now = {_strip_suffix(label) for label in current}
    if prev - now - {"MIXED"}:
        return "label_drift"
    return None


def _strip_suffix(label: str) -> str:
    base = label
    while len(base) > 2 and base[-1].isdigit() and base[-2] == "_":
        base = base[:-2]
    return base


def _duration(stay: float) -> float | None:
    if not np.isfinite(stay):
        return None
    if stay >= 0.999:
        return 999.0
    if stay <= 0.0:
        return 1.0
    return 1.0 / (1.0 - stay)


def _cache_reusable(cached: dict[str, Any], last_day: pd.Timestamp) -> bool:
    try:
        fit_through = _day(cached["fit_through"])
        labels = cached.get("labels")
        names = cached.get("feature_names")
        trans = np.asarray(cached["A"], dtype=np.float64)
    except (KeyError, TypeError, ValueError):
        return False
    if names != list(FEATURE_NAMES) or not isinstance(labels, list):
        return False
    if trans.ndim != 2 or trans.shape[0] != len(labels):
        return False
    age = int((last_day - fit_through).days)
    return 0 <= age <= REFIT_DAYS


def _read_cache(path: Path | None) -> dict[str, Any] | None:
    if path is None or not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or payload.get("version") != CACHE_VERSION:
        return None
    return payload


def _write_cache(path: Path | None, model: dict[str, Any]) -> None:
    if path is None:
        return
    payload = {
        "version": CACHE_VERSION,
        "fit_through": model.get("fit_through"),
        "feature_names": list(FEATURE_NAMES),
        "n_states": model.get("n_states"),
        "labels": list(model.get("labels") or []),
        "stress": [bool(flag) for flag in model.get("stress") or []],
        "pi": _tolist(model["pi"]),
        "A": _tolist(model["A"]),
        "mu": _tolist(model["mu"]),
        "var": _tolist(model["var"]),
        "mean_log_return": _tolist(model["mean_log_return"]),
        "realized_vol": _tolist(model["realized_vol"]),
        "bic_by_states": {str(k): float(v) for k, v in (model.get("bic_by_states") or {}).items()},
        "freeze_reasons": list(model.get("freeze_reasons") or []),
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload), encoding="utf-8")
        tmp.replace(path)
    except OSError as exc:
        LOG.warning("HMM regime cache write failed: %s", exc)


def _tolist(value: Any) -> list[Any]:
    arr = np.asarray(value, dtype=np.float64)
    return json.loads(json.dumps(np.round(arr, 6).tolist()))


def _day(value: Any) -> pd.Timestamp:
    stamp = pd.Timestamp(value)
    if stamp.tzinfo is not None:
        stamp = stamp.tz_convert("UTC").tz_localize(None)
    return stamp.normalize()


def _iso(value: Any) -> str:
    return _day(value).date().isoformat()


def _round(value: Any, digits: int = 4) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not np.isfinite(number):
        return None
    return round(number, digits)
