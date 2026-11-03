"""Rule-based outcomes, briefing, and next steps for a single backtest run.

No LLM. Thresholds match the existing single-window PF hint (1.20) and
the operator-facing copy on the Backtest landing page.
"""

from __future__ import annotations

import copy
from datetime import datetime
from typing import Any

PF_GATE = 1.20
PF_BREAKEVEN = 1.0
FEW_TRADES = 10
LARGE_N_TRADES = 100
DEEP_DRAWDOWN_PCT = 25.0
MAX_STEPS = 3
MAX_DRIVERS = 3
RISK_FLOOR = 0.0025
MAX_POSITIONS_CAP = 20
FALLBACK_SHARE = 0.20
CAPACITY_SHARE = 0.25
THIN_UNIVERSE_PRESETS = frozenset({"sector_etfs", "focused", "custom", "tickers"})
CONFIGURED_HOLD_DAYS = 40
HORIZON_ACTION_YEARS: tuple[int, ...] = (2, 5, 8)
HORIZON_ACTION_LABELS: dict[int, str] = {
    2: "Test last 2 years",
    5: "Test last 5 years",
    8: "Test last 8 years",
}
LIVE_MEMBERSHIP_MODES = frozenset(
    {
        "membership_missing_live_fallback",
        "named_universe_live_constituents",
    }
)
CONFIRM_UNIVERSES: tuple[tuple[str, str], ...] = (
    ("nasdaq100", "Nasdaq-100"),
    ("sp500", "S&P 500"),
    ("sp1500", "S&P 1500"),
)

FUNNEL_KEYS: tuple[tuple[str, str], ...] = (
    ("quality_gates_filtered", "Quality gates blocked entries"),
    ("stage2_fail", "Failed Stage 2"),
    ("vcp_fail", "Failed VCP"),
    ("breakout_not_confirmed", "Breakout not confirmed"),
    ("regime_blocked", "Regime gate blocked"),
    ("forensic_filtered", "Forensic filter blocked"),
    ("entry_family_fail", "Entry family failed"),
    ("confluence_blocked", "Confluence gate blocked"),
    ("pead_primary_fail", "Earnings-drift filter blocked"),
    ("liquidity_filtered", "Liquidity filter blocked"),
)

EXIT_KEYS: tuple[tuple[str, str], ...] = (
    ("exits_trailing_stop", "Trailing stop"),
    ("exits_time_exit", "Time exit"),
    ("exits_sma50_break", "50-day SMA break"),
    ("exits_vcp_invalidation", "VCP invalidation"),
    ("exits_final_liquidation", "End-of-window liquidation"),
)


def _num(value: Any) -> float | None:
    if value in ("inf", "Infinity"):
        return float("inf")
    try:
        n = float(value)
    except (TypeError, ValueError):
        return None
    if n != n:  # NaN
        return None
    return n


def _intish(value: Any) -> int:
    n = _num(value)
    if n is None or n == float("inf"):
        return 0
    return int(n)


def fallback_share(integrity: dict[str, Any] | None) -> float:
    """Fraction of history fetches that used a fallback provider."""
    payload = integrity if isinstance(integrity, dict) else {}
    fallback = _intish(payload.get("history_fallback_used"))
    fetch_total = _intish(payload.get("history_fetch_total"))
    if fetch_total:
        return fallback / fetch_total
    return 1.0 if fallback else 0.0


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _clone_spec(spec: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(spec, dict):
        return {}
    return copy.deepcopy(spec)


def _cta(kind: str, label: str, **extra: Any) -> dict[str, Any]:
    out: dict[str, Any] = {"kind": kind, "label": label}
    out.update(extra)
    return out


def _step(
    step_id: str,
    title: str,
    why: str,
    action: str,
    cta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    row: dict[str, Any] = {"id": step_id, "title": title, "why": why, "action": action}
    if cta:
        row["cta"] = cta
    return row


def _spec_ready(spec: dict[str, Any] | None) -> bool:
    if not isinstance(spec, dict):
        return False
    return bool(spec.get("start_date") and spec.get("end_date"))


def _with_overrides(spec: dict[str, Any], **fields: Any) -> dict[str, Any]:
    out = _clone_spec(spec)
    ov = _dict(out.get("overrides"))
    for key, value in fields.items():
        if value is not None:
            ov[key] = value
    out["overrides"] = ov
    return out


def _shift_years(dt: datetime, years: int) -> datetime:
    try:
        return dt.replace(year=dt.year - years)
    except ValueError:
        return dt.replace(year=dt.year - years, day=28)


def _earlier_start_spec(spec: dict[str, Any], years: int = 3) -> dict[str, Any]:
    out = _clone_spec(spec)
    raw = str(out.get("start_date") or "")[:10]
    try:
        start = datetime.strptime(raw, "%Y-%m-%d")
        out["start_date"] = _shift_years(start, years).strftime("%Y-%m-%d")
    except ValueError:
        return out
    return out


def _universe_id(spec: dict[str, Any] | None) -> str:
    payload = spec if isinstance(spec, dict) else {}
    return str(payload.get("universe_preset") or payload.get("universe_mode") or "").strip().lower()


def _with_universe(spec: dict[str, Any], universe: str) -> dict[str, Any]:
    out = _clone_spec(spec)
    out["universe_preset"] = universe
    out["universe_mode"] = "watchlist"
    out["tickers"] = []
    return out


def _with_window_years(spec: dict[str, Any], years: int) -> dict[str, Any]:
    out = _clone_spec(spec)
    raw = str(out.get("end_date") or "")[:10]
    try:
        end = datetime.strptime(raw, "%Y-%m-%d")
        out["start_date"] = _shift_years(end, years).strftime("%Y-%m-%d")
    except ValueError:
        return out
    return out


def _horizon_spec(spec: dict[str, Any], years: int) -> dict[str, Any]:
    """Same sleeve and index; new dates. Long windows skip MiroFish so they finish."""
    out = _with_window_years(spec, years)
    if years >= 5:
        out = _with_overrides(out, skip_mirofish=True)
    return out


def build_horizon_actions(
    spec: dict[str, Any] | None,
    *,
    current_years: int | None = None,
    existing_by_years: dict[int, Any] | None = None,
) -> list[dict[str, Any]]:
    """2y / 5y / 8y row actions. Existing windows jump; missing windows queue."""
    payload = spec if isinstance(spec, dict) else {}
    years_now = current_years if current_years is not None else _window_years(payload)
    existing = existing_by_years if isinstance(existing_by_years, dict) else {}
    ready = _spec_ready(payload)
    actions: list[dict[str, Any]] = []
    for years in HORIZON_ACTION_YEARS:
        raw_existing = existing.get(years)
        existing_id = str(raw_existing) if raw_existing not in (None, "") else None
        is_current = years_now is not None and int(years_now) == int(years)
        item: dict[str, Any] = {
            "years": years,
            "label": HORIZON_ACTION_LABELS[years],
            "available": False,
            "reason": None,
            "existing_run_id": None,
            "cta": None,
        }
        if is_current:
            item["reason"] = "current"
            actions.append(item)
            continue
        if not ready:
            item["reason"] = "no_spec"
            actions.append(item)
            continue
        if existing_id:
            item["available"] = True
            item["existing_run_id"] = existing_id
            item["label"] = f"Already have {years}y"
            actions.append(item)
            continue
        item["available"] = True
        item["cta"] = _cta(
            "queue_spec",
            HORIZON_ACTION_LABELS[years],
            spec=_horizon_spec(payload, years),
        )
        actions.append(item)
    return actions


def _window_years(spec: dict[str, Any] | None) -> int | None:
    from .backtest_labels import window_years_from_spec

    return window_years_from_spec(spec if isinstance(spec, dict) else {})


def _next_confirm_universe(current: str) -> tuple[str, str] | None:
    uni = str(current or "").strip().lower()
    if uni in {"nasdaq100"}:
        return ("sp1500", "S&P 1500")
    if uni in {"sp500", "sp1500"}:
        return None
    for preset, label in CONFIRM_UNIVERSES:
        if preset != uni:
            return (preset, label)
    return ("nasdaq100", "Nasdaq-100")


def _sample_untrusted(trades: float | None, spec: dict[str, Any] | None) -> bool:
    n = 0 if trades is None else int(trades)
    if n < LARGE_N_TRADES:
        return True
    return _universe_id(spec) in THIN_UNIVERSE_PRESETS


def _confirm_universe_step(spec: dict[str, Any], trades: float | None) -> dict[str, Any] | None:
    if not _spec_ready(spec):
        return None
    n = 0 if trades is None else int(trades)
    if _universe_id(spec) == "sector_etfs":
        return _step(
            "confirm_larger_universe",
            "Replay on Nasdaq-100 and S&P 1500",
            (
                f"Sector ETFs can print a high profit factor that dies on a real book (n={n}). "
                "Do not promote this window."
            ),
            "Queue the same sleeve on Nasdaq-100, then S&P 1500. Do not promote.",
            cta=_cta("queue_spec", "Queue on Nasdaq-100", spec=_with_universe(spec, "nasdaq100")),
        )
    nxt = _next_confirm_universe(_universe_id(spec))
    if nxt is None:
        return None
    preset, label = nxt
    why = (
        f"Only {n} trades, or a small index, can print a high profit factor that dies on a real book."
        if n < LARGE_N_TRADES
        else "This index is too narrow to treat as the live universe."
    )
    return _step(
        "confirm_larger_universe",
        f"Confirm on {label}",
        why,
        f"Queue the same sleeve and dates on {label} before trusting this window.",
        cta=_cta("queue_spec", f"Queue on {label}", spec=_with_universe(spec, preset)),
    )


def _confirm_longer_window_step(spec: dict[str, Any]) -> dict[str, Any] | None:
    if not _spec_ready(spec):
        return None
    years = _window_years(spec)
    if years is None or years >= 5:
        return None
    longer = _horizon_spec(spec, 5)
    return _step(
        "confirm_longer_window",
        "Stress this sleeve on 5 years",
        "A 2-year bull window can look strong and then fail on 5y/8y.",
        "Queue the same index and sleeve with a 5-year window.",
        cta=_cta("queue_spec", "Queue last 5 years", spec=longer),
    )


def _raise_positions_spec(spec: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    port = _dict(result.get("portfolio_summary"))
    current = _intish(_dict(spec.get("overrides")).get("backtest_portfolio_max_positions"))
    if current <= 0:
        current = _intish(port.get("peak_concurrent")) or 10
    nxt = min(MAX_POSITIONS_CAP, max(current + 4, current * 2 if current < 8 else current + 5))
    return _with_overrides(spec, backtest_portfolio_max_positions=int(nxt))


def _halve_risk_spec(spec: dict[str, Any]) -> dict[str, Any]:
    current = _num(_dict(spec.get("overrides")).get("backtest_risk_per_trade_pct"))
    if current is None or current <= 0:
        current = 0.0075
    return _with_overrides(spec, backtest_risk_per_trade_pct=max(RISK_FLOOR, float(current) / 2.0))


def _counts(diag: dict[str, Any], keys: tuple[tuple[str, str], ...]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for key, label in keys:
        n = _intish(diag.get(key))
        if n > 0:
            rows.append({"id": key, "label": label, "count": n})
    rows.sort(key=lambda r: int(r["count"]), reverse=True)
    return rows


def _pf_delta_label(delta: float | None) -> str:
    if delta is None:
        return "—"
    if delta == float("inf"):
        return "+∞"
    sign = "+" if delta >= 0 else ""
    return f"{sign}{delta:.2f}"


def build_run_briefing(
    result: dict[str, Any] | None,
    previous_summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Plain-language drivers from fields the engine already writes."""
    result = _dict(result)
    diag = _dict(result.get("diagnostics"))
    port = _dict(result.get("portfolio_summary"))
    integrity = _dict(result.get("data_integrity"))
    drivers: list[dict[str, Any]] = []
    data_quality: dict[str, Any] | None = None

    fallback = _intish(integrity.get("history_fallback_used"))
    fetch_total = _intish(integrity.get("history_fetch_total"))
    empty_fetch = _intish(integrity.get("history_fetch_empty"))
    fallback_share_val = fallback_share(integrity)
    if fallback > 0 and fallback_share_val >= FALLBACK_SHARE:
        data_quality = {
            "tone": "warn",
            "summary": (
                f"{fallback} history fetches used a fallback provider"
                + (f" ({fallback_share_val:.0%} of {fetch_total})." if fetch_total else ".")
                + " Do not treat this window as Schwab-clean."
            ),
        }
        drivers.append(
            {
                "id": "data_fallback",
                "tone": "warn",
                "title": "Data is not Schwab-clean",
                "detail": data_quality["summary"],
            }
        )
    elif empty_fetch > 0:
        data_quality = {
            "tone": "warn",
            "summary": f"{empty_fetch} symbols returned empty history. Coverage is incomplete.",
        }
        drivers.append(
            {
                "id": "data_empty",
                "tone": "warn",
                "title": "Incomplete price history",
                "detail": data_quality["summary"],
            }
        )

    entries = _intish(diag.get("entries"))
    capacity = _intish(diag.get("capital_filtered")) + _intish(diag.get("position_limit_filtered"))
    if capacity > 0 and (entries == 0 or capacity >= max(3, int(entries * CAPACITY_SHARE))):
        drivers.append(
            {
                "id": "capacity",
                "tone": "warn",
                "title": "The book was full",
                "detail": (
                    f"{capacity} signals were dropped for capital or position-limit. "
                    "Profit factor on the trades that filled is censored."
                ),
            }
        )

    funnel = _counts(diag, FUNNEL_KEYS)
    trades = _num(result.get("total_trades"))
    if funnel and (trades is None or trades < FEW_TRADES):
        top = funnel[0]
        drivers.append(
            {
                "id": str(top["id"]),
                "tone": "warn",
                "title": str(top["label"]),
                "detail": f"{top['count']} names died here — the main reason the sample is thin.",
            }
        )

    exits = _counts(diag, EXIT_KEYS)
    exit_total = sum(int(r["count"]) for r in exits)
    if exits and exit_total >= FEW_TRADES:
        top_exit = exits[0]
        share = int(top_exit["count"]) / exit_total
        if share >= 0.5:
            drivers.append(
                {
                    "id": str(top_exit["id"]),
                    "tone": "neutral",
                    "title": f"Most exits were {str(top_exit['label']).lower()}",
                    "detail": f"{top_exit['count']} of {exit_total} exits ({share:.0%}).",
                }
            )

    dd = _num(result.get("max_drawdown_net_pct"))
    if dd is not None and dd >= DEEP_DRAWDOWN_PCT:
        drivers.append(
            {
                "id": "drawdown",
                "tone": "warn",
                "title": "Drawdown is deep",
                "detail": f"Max drawdown was {dd:.1f}%. Treat return as fragile until sizing is tighter.",
            }
        )

    hold_days = _intish(result.get("avg_holding_days"))
    hold_note = None
    if hold_days > 0 and hold_days == CONFIGURED_HOLD_DAYS:
        hold_note = (
            f"avg_holding_days is {hold_days} because that is the configured hold cap, "
            "not a measured average duration."
        )
        drivers.append(
            {
                "id": "configured_hold",
                "tone": "neutral",
                "title": "Holding period is the configured cap",
                "detail": hold_note,
            }
        )

    vs_previous = None
    prev = _dict(previous_summary)
    if prev and result.get("total_trades") is not None:
        this_pf = _num(result.get("profit_factor_net"))
        prev_pf = _num(prev.get("profit_factor_net"))
        this_tr = _num(result.get("total_trades"))
        prev_tr = _num(prev.get("total_trades"))
        this_ret = _num(result.get("total_return_net_pct"))
        prev_ret = _num(prev.get("total_return_net_pct"))
        this_dd = _num(result.get("max_drawdown_net_pct"))
        prev_dd = _num(prev.get("max_drawdown_net_pct"))
        pf_delta = None
        if this_pf is not None and prev_pf is not None:
            pf_delta = this_pf - prev_pf
        headline = "Compared with the previous completed run."
        if pf_delta is not None:
            if pf_delta > 0.02:
                headline = f"PF {_pf_delta_label(pf_delta)} vs the previous window."
            elif pf_delta < -0.02:
                headline = f"PF {_pf_delta_label(pf_delta)} vs the previous window."
            else:
                headline = "Profit factor is roughly unchanged vs the previous window."
        vs_previous = {
            "headline": headline,
            "pf_delta": pf_delta,
            "trades_delta": (this_tr - prev_tr) if this_tr is not None and prev_tr is not None else None,
            "return_delta": (this_ret - prev_ret) if this_ret is not None and prev_ret is not None else None,
            "dd_delta": (this_dd - prev_dd) if this_dd is not None and prev_dd is not None else None,
            "previous": {
                "profit_factor_net": prev.get("profit_factor_net"),
                "total_trades": prev.get("total_trades"),
                "total_return_net_pct": prev.get("total_return_net_pct"),
                "max_drawdown_net_pct": prev.get("max_drawdown_net_pct"),
            },
        }

    seen: set[str] = set()
    unique_drivers: list[dict[str, Any]] = []
    for d in drivers:
        did = str(d.get("id") or "")
        if did in seen:
            continue
        seen.add(did)
        unique_drivers.append(d)
        if len(unique_drivers) >= MAX_DRIVERS:
            break

    trades_sample = result.get("trades_sample") if isinstance(result.get("trades_sample"), list) else []
    sample_rows: list[dict[str, Any]] = []
    for trade in trades_sample[:5]:
        if not isinstance(trade, dict):
            continue
        pct = _num(trade.get("net_return_pct"))
        if pct is None:
            raw = _num(trade.get("net_return"))
            pct = (raw * 100.0) if raw is not None else None
        sample_rows.append(
            {
                "ticker": trade.get("ticker") or trade.get("symbol"),
                "entry_date": trade.get("entry_date") or trade.get("entry"),
                "exit_date": trade.get("exit_date") or trade.get("exit"),
                "net_return_pct": pct,
                "exit_reason": trade.get("exit_reason"),
            }
        )

    return {
        "drivers": unique_drivers,
        "funnel": funnel,
        "exits": exits,
        "vs_previous": vs_previous,
        "data_quality": data_quality,
        "portfolio": {
            "ending_equity": port.get("ending_equity"),
            "peak_concurrent": port.get("peak_concurrent"),
            "capacity_filtered": port.get("capacity_filtered") or _intish(diag.get("capital_filtered")),
            "avg_concurrent": port.get("avg_concurrent"),
        }
        if port
        else None,
        "trades_sample": sample_rows,
        "hold_note": hold_note,
    }


def _requeue_cta(spec: dict[str, Any] | None) -> dict[str, Any] | None:
    if not _spec_ready(spec):
        return None
    return _cta("queue_spec", "Re-queue this spec", spec=_clone_spec(spec))


def build_short_briefing(
    *,
    outcome: dict[str, Any] | None,
    briefing: dict[str, Any] | None,
    result: dict[str, Any] | None = None,
    next_steps: list[dict[str, Any]] | None = None,
    requeue_cta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Above-the-fold briefing: verdict, four tiles, ≤3 chips, one primary action."""
    outcome = _dict(outcome)
    briefing = _dict(briefing)
    result = _dict(result)
    steps = [s for s in (next_steps or []) if isinstance(s, dict)]
    chips: list[dict[str, Any]] = []
    dq = briefing.get("data_quality") if isinstance(briefing.get("data_quality"), dict) else None
    if dq:
        chips.append({"id": "not_schwab_clean", "tone": str(dq.get("tone") or "warn"), "label": "not Schwab-clean"})
    integrity = _dict(result.get("data_integrity"))
    mode = str(integrity.get("membership_filter_mode") or "")
    if mode in LIVE_MEMBERSHIP_MODES:
        chips.append({"id": "live_membership", "tone": "warn", "label": "live membership"})
    if briefing.get("hold_note"):
        chips.append({"id": "configured_hold", "tone": "neutral", "label": "hold cap, not measured duration"})
    trades = _num(result.get("total_trades"))
    if trades is not None and trades < LARGE_N_TRADES and len(chips) < 3:
        chips.append({"id": "thin_sample", "tone": "warn", "label": "thin sample"})
    primary: dict[str, Any] | None = None
    primary_id = None
    for step in steps:
        if step.get("cta"):
            primary = {"id": step.get("id"), "title": step.get("title"), "cta": step.get("cta")}
            primary_id = step.get("id")
            break
    if primary is None and requeue_cta:
        primary = {
            "id": "requeue",
            "title": requeue_cta.get("label") or "Re-queue this spec",
            "cta": requeue_cta,
        }
        primary_id = "requeue"
    if primary is None and steps:
        step = steps[0]
        primary = {"id": step.get("id"), "title": step.get("title"), "cta": None}
        primary_id = step.get("id")
    more = [
        {"id": step.get("id"), "title": step.get("title"), "cta": step.get("cta")}
        for step in steps
        if step.get("id") != primary_id
    ]
    return {
        "verdict": str(outcome.get("headline") or ""),
        "tone": str(outcome.get("tone") or "neutral"),
        "tiles": {
            "profit_factor_net": result.get("profit_factor_net"),
            "total_trades": result.get("total_trades"),
            "total_return_net_pct": result.get("total_return_net_pct"),
            "max_drawdown_net_pct": result.get("max_drawdown_net_pct"),
        },
        "chips": chips[:3],
        "primary": primary,
        "more": more,
    }


def _book_limit_notes(result: dict[str, Any] | None, spec: dict[str, Any] | None) -> list[str]:
    """Sentences that stop a named-index replay from reading as the full historical book."""
    integrity = _dict((result or {}).get("data_integrity"))
    notes: list[str] = []
    mode = str(integrity.get("membership_filter_mode") or "")
    if mode == "membership_missing_live_fallback":
        notes.append("The membership file is missing, so these are today's constituents, not the historical index.")
    elif mode == "named_universe_live_constituents":
        notes.append("This index uses today's constituents, not a point-in-time membership list.")
    loaded = _intish(integrity.get("membership_input_universe"))
    expected = {"sp1500": 1500, "sp500": 500, "nasdaq100": 100}.get(_universe_id(spec))
    if expected and loaded and loaded < expected // 2:
        notes.append(f"The replay loaded {loaded} names, not the {expected}-name book.")
    return notes


def _book_limit_outcome(
    outcome: dict[str, Any],
    result: dict[str, Any] | None,
    spec: dict[str, Any] | None,
) -> dict[str, Any]:
    notes = _book_limit_notes(result, spec)
    if not notes:
        return outcome
    out = dict(outcome)
    if str(out.get("tone") or "") == "ok":
        out["tone"] = "warn"
    summary = str(out.get("summary") or "").strip()
    extra = " ".join(notes)
    if extra not in summary:
        out["summary"] = f"{summary} {extra}".strip()
    integrity = _dict((result or {}).get("data_integrity"))
    loaded = _intish(integrity.get("membership_input_universe"))
    expected = {"sp1500": 1500, "sp500": 500, "nasdaq100": 100}.get(_universe_id(spec))
    headline = str(out.get("headline") or "")
    if expected and loaded and loaded < expected // 2 and "names" not in headline.lower():
        out["headline"] = f"{headline} · {loaded} names"
    return out


def _package_evaluation(
    *,
    outcome: dict[str, Any],
    next_steps: list[dict[str, Any]],
    briefing: dict[str, Any],
    requeue_cta: dict[str, Any] | None,
    result: dict[str, Any] | None = None,
    spec: dict[str, Any] | None = None,
) -> dict[str, Any]:
    briefing = dict(briefing or {})
    steps = list(next_steps or [])
    if isinstance(result, dict) and "total_trades" in result:
        outcome = _book_limit_outcome(outcome, result, spec)
    briefing["short"] = build_short_briefing(
        outcome=outcome,
        briefing=briefing,
        result=result,
        next_steps=steps,
        requeue_cta=requeue_cta,
    )
    return {
        "outcome": outcome,
        "next_steps": steps,
        "briefing": briefing,
        "requeue_cta": requeue_cta,
        "horizon_actions": build_horizon_actions(spec),
    }


def evaluate_run(
    *,
    status: str | None = None,
    result: dict[str, Any] | None = None,
    error_message: str | None = None,
    queue_busy: bool = False,
    spec: dict[str, Any] | None = None,
    queue_position: int | None = None,
    previous_summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return ``{outcome, next_steps, briefing, requeue_cta}`` for a run."""
    st = str(status or "").strip().lower()
    result = result if isinstance(result, dict) else None
    err = (error_message or "").strip()
    spec = spec if isinstance(spec, dict) else {}
    steps: list[dict[str, Any]] = []
    briefing = {
        "drivers": [],
        "funnel": [],
        "exits": [],
        "vs_previous": None,
        "data_quality": None,
        "portfolio": None,
        "trades_sample": [],
        "hold_note": None,
    }

    if st in {"cancelled", "canceled"}:
        return _package_evaluation(
            outcome={
                "tone": "neutral",
                "headline": "This run was cancelled",
                "summary": "It never started. Queue it again if you still want this window.",
            },
            next_steps=[
                _step(
                    "requeue_cancelled",
                    "Queue it again",
                    "Cancelled jobs do not produce a result.",
                    "Use Re-queue this spec, or edit settings and queue a new run.",
                    cta=_requeue_cta(spec),
                )
            ],
            briefing=briefing,
            requeue_cta=_requeue_cta(spec),
            spec=spec,
        )

    if st in {"queued", "pending"}:
        pos = queue_position if isinstance(queue_position, int) and queue_position > 0 else None
        if queue_busy or pos:
            where = f"position {pos}" if pos else "in line"
            headline = f"In line — {where}"
            summary = (
                "This computer runs one backtest at a time. Your job will start when the "
                "current replay finishes. You can still queue more, up to the waiting cap."
            )
        else:
            headline = "This backtest is queued"
            summary = "Results and next steps will appear here when the replay finishes."
        return _package_evaluation(
            outcome={"tone": "neutral", "headline": headline, "summary": summary},
            next_steps=[
                _step(
                    "wait_queue",
                    "Wait for this run",
                    "Only one historical replay runs locally at a time.",
                    "Stay on this page. Cancel it from the queue if you no longer need it.",
                )
            ],
            briefing=briefing,
            requeue_cta=None,
            spec=spec,
        )

    if st in {"running", "started"}:
        return _package_evaluation(
            outcome={
                "tone": "neutral",
                "headline": "This backtest is still running",
                "summary": "Results and next steps will appear here when the replay finishes.",
            },
            next_steps=[
                _step(
                    "wait_result",
                    "Wait for the result",
                    "The queue is working on this job.",
                    "Keep this tab open or refresh the list in a minute.",
                )
            ],
            briefing=briefing,
            requeue_cta=None,
            spec=spec,
        )

    if st in {"failed", "failure", "revoked"} or (
        st not in {"success"} and err and not result
    ):
        headline = "This run did not finish"
        summary = err or "The worker stopped before it could produce a result."
        steps.append(
            _step(
                "fix_and_requeue",
                "Fix the error and re-queue",
                summary,
                "Read the error, adjust dates, tickers, or settings, then queue the run again.",
                cta=_cta("open_form", "Edit settings") if _spec_ready(spec) else None,
            )
        )
        return _package_evaluation(
            outcome={"tone": "bad", "headline": headline, "summary": summary[:400]},
            next_steps=steps[:MAX_STEPS],
            briefing=briefing,
            requeue_cta=_requeue_cta(spec),
            spec=spec,
        )

    if not result or result.get("total_trades") is None:
        return _package_evaluation(
            outcome={
                "tone": "neutral",
                "headline": "No result to read yet",
                "summary": "Queue a backtest or pick a finished run from the list.",
            },
            next_steps=[
                _step(
                    "queue_first",
                    "Queue a backtest",
                    "There is no completed replay to interpret.",
                    "Pick a date range on this page and queue a run.",
                )
            ],
            briefing=briefing,
            requeue_cta=None,
            spec=spec,
        )

    briefing = build_run_briefing(result, previous_summary=previous_summary)
    driver_ids = {str(d.get("id")) for d in briefing.get("drivers") or []}
    trades = _num(result.get("total_trades"))
    pf = _num(result.get("profit_factor_net"))
    dd = _num(result.get("max_drawdown_net_pct"))
    ret = _num(result.get("total_return_net_pct"))
    findings = result.get("findings") if isinstance(result.get("findings"), str) else ""
    ready = _spec_ready(spec)

    if briefing.get("data_quality"):
        steps.append(
            _step(
                "fix_data",
                "Do not promote this window",
                str((briefing["data_quality"] or {}).get("summary") or "Data quality is degraded."),
                "Fix history coverage before treating profit factor as evidence.",
            )
        )

    if "capacity" in driver_ids and ready:
        raised = _raise_positions_spec(spec, result)
        nxt = _dict(raised.get("overrides")).get("backtest_portfolio_max_positions")
        steps.append(
            _step(
                "raise_capacity",
                "Raise max positions and re-run",
                "The book dropped signals. This profit factor is censored.",
                f"Queue the same dates with max open positions set to {nxt}.",
                cta=_cta("queue_spec", f"Queue with max positions {nxt}", spec=raised),
            )
        )

    quality_starved = "quality_gates_filtered" in driver_ids
    if quality_starved and ready and (trades is None or trades < FEW_TRADES):
        shadowed = _with_overrides(spec, quality_gates_mode="shadow")
        steps.append(
            _step(
                "shadow_quality_gates",
                "Try quality gates in shadow",
                "Quality gates blocked most entries. That is an experiment, not a LIVE change.",
                "Queue the same window with quality gates observing only.",
                cta=_cta("queue_spec", "Queue with gates in shadow", spec=shadowed),
            )
        )

    if trades is not None and trades <= 0:
        integrity = _dict(result.get("data_integrity"))
        too_short = _intish(integrity.get("history_fetch_too_short"))
        fetch_total = _intish(integrity.get("history_fetch_total"))
        if fetch_total > 0 and too_short >= fetch_total:
            return _package_evaluation(
                outcome={
                    "tone": "warn",
                    "headline": "Price history was too short",
                    "summary": (
                        f"All {fetch_total} symbols had fewer than {260} bars, so no timeline was built. "
                        "The window needs earlier history for the indicators. This is not a strategy result."
                    ),
                },
                next_steps=[
                    _step(
                        "widen_history",
                        "Re-queue the same dates",
                        "A one-year window needs warmup bars before the first day.",
                        "Queue the run again. New replays fetch that warmup automatically.",
                        cta=_requeue_cta(spec),
                    )
                ],
                briefing=briefing,
                requeue_cta=_requeue_cta(spec),
                result=result,
                spec=spec,
            )
        longer = _earlier_start_spec(spec) if ready else None
        steps.append(
            _step(
                "widen_universe",
                "Widen dates or universe",
                "Zero trades means there is no edge sample to judge.",
                "Try a longer date range or the full watchlist, then re-queue.",
                cta=_cta("queue_spec", "Queue a longer window", spec=longer) if longer else None,
            )
        )
        steps.append(
            _step(
                "relax_gates",
                "Do not treat this as a fail",
                "An empty replay cannot pass or fail the 1.20 profit-factor gate.",
                "Skip promotion talk until a run produces enough trades.",
            )
        )
        return _package_evaluation(
            outcome={
                "tone": "warn",
                "headline": "No trades in this window",
                "summary": "The rules never opened a position. That is not a strategy verdict — the window or universe was too tight.",
            },
            next_steps=steps[:MAX_STEPS],
            briefing=briefing,
            requeue_cta=_requeue_cta(spec),
            result=result,
            spec=spec,
        )

    if pf is not None and pf == float("inf"):
        n = int(trades) if trades is not None else 0
        tone = "warn"
        headline = "No losing trades — not a champion"
        summary = (
            f"Profit factor is infinite because there were no losing trades (n={n}). "
            "That is a sample artifact, not a ranking result."
        )
        if _universe_id(spec) == "sector_etfs" or _universe_id(spec) in THIN_UNIVERSE_PRESETS:
            confirm = _confirm_universe_step(spec, trades)
            if confirm:
                steps.append(confirm)
        elif n < LARGE_N_TRADES:
            confirm = _confirm_universe_step(spec, trades)
            if confirm:
                steps.append(confirm)
        longer = _confirm_longer_window_step(spec) if ready else None
        if longer:
            steps.append(longer)
        if n < FEW_TRADES and not any(s["id"] == "need_more_trades" for s in steps):
            widen = _earlier_start_spec(spec) if ready else None
            steps.append(
                _step(
                    "need_more_trades",
                    "Get more trades before judging",
                    f"Only {n} trades fired — too few for a stable read.",
                    "Widen the date range or universe so you have at least 10 trades.",
                    cta=_cta("queue_spec", "Queue a longer window", spec=widen) if widen else None,
                )
            )
    elif trades is not None and trades < FEW_TRADES:
        longer = _earlier_start_spec(spec) if ready else None
        steps.append(
            _step(
                "need_more_trades",
                "Get more trades before judging",
                f"Only {int(trades)} trades fired — too few for a stable read.",
                "Widen the date range or universe so you have at least 10 trades.",
                cta=_cta("queue_spec", "Queue a longer window", spec=longer) if longer else None,
            )
        )
        tone = "warn"
        headline = "Not enough trades to trust this result"
        summary = f"{int(trades)} trades is a thin sample. Treat the numbers as a hint, not a verdict."
    elif pf is not None and pf < PF_BREAKEVEN:
        tone = "bad"
        headline = "Lost money after costs"
        summary = (
            f"Net profit factor is {pf:.2f} (below 1.0). Winners did not cover losers after slippage and fees."
        )
        steps.append(
            _step(
                "do_not_promote",
                "Do not promote this setup",
                "A profit factor below 1.0 means the window lost money after costs.",
                "Read the findings below. Do not treat this as LIVE-ready.",
            )
        )
        if findings:
            steps.append(
                _step(
                    "read_findings",
                    "Read the findings",
                    "The engine already wrote a short diagnosis for this window.",
                    "Use the findings text to decide what to change before the next run.",
                )
            )
    elif pf is not None and _sample_untrusted(trades, spec):
        tone = "warn"
        n = int(trades) if trades is not None else 0
        pf_txt = "∞" if pf == float("inf") else f"{pf:.2f}"
        if _universe_id(spec) in THIN_UNIVERSE_PRESETS:
            headline = "Thin index — confirm on a larger book"
            summary = (
                f"Net profit factor is {pf_txt} on {n} trades in a narrow index. "
                "That is not evidence for the live universe."
            )
        else:
            headline = "Interesting, but the sample is still thin"
            summary = (
                f"Net profit factor is {pf_txt} on {n} trades. "
                f"Need about {LARGE_N_TRADES} trades on a core index before this ranks as evidence."
            )
        confirm = _confirm_universe_step(spec, trades)
        if confirm:
            steps.append(confirm)
        longer = _confirm_longer_window_step(spec)
        if longer:
            steps.append(longer)
        if not any(s["id"].startswith("confirm_") for s in steps):
            widen = _earlier_start_spec(spec) if ready else None
            steps.append(
                _step(
                    "need_more_trades",
                    "Get more trades before judging",
                    f"Only {n} trades fired — too few for a stable read.",
                    f"Widen the date range or universe so you have at least {LARGE_N_TRADES} trades.",
                    cta=_cta("queue_spec", "Queue a longer window", spec=widen) if widen else None,
                )
            )
    elif pf is not None and pf < PF_GATE:
        tone = "warn"
        headline = "Mixed — not at the 1.20 gate"
        summary = (
            f"Net profit factor is {pf:.2f}. The single-window promotion hint is 1.20. "
            "This is one window, not a multi-era verdict."
        )
        longer = _confirm_longer_window_step(spec) if ready else None
        if longer:
            steps.append(longer)
        else:
            fallback = _earlier_start_spec(spec) if ready else None
            steps.append(
                _step(
                    "iterate_window",
                    "Compare or try a longer window",
                    "The run is above breakeven but below the 1.20 hint.",
                    "Compare this run to the last one, or queue a longer date range.",
                    cta=_cta("queue_spec", "Queue a longer window", spec=fallback) if fallback else None,
                )
            )
    elif pf is not None:
        tone = "ok"
        universe = str((spec or {}).get("universe_preset") or "this universe").replace("_", " ")
        headline = f"Single window clears 1.20 — {universe}"
        summary = (
            f"Net profit factor is {pf:.2f}, which clears the single-window 1.20 hint. "
            "This is not the signed multi-era book, and it is not LIVE-ready by itself."
        )
        longer = _confirm_longer_window_step(spec) if ready else None
        if longer:
            steps.append(longer)
        steps.append(
            _step(
                "check_related",
                "Check related multi-era evidence",
                "One window can look strong while another era fails.",
                "Open Related evidence on this page and read the promotion verdict.",
                cta=_cta("scroll", "Open related evidence", target="backtestRelatedPanel"),
            )
        )
    else:
        tone = "neutral"
        headline = "Result is incomplete"
        summary = "This run did not report a net profit factor."
        steps.append(
            _step(
                "inspect_result",
                "Inspect the full result",
                "Profit factor is missing from the saved result.",
                "Open the selected run details or queue the same spec again.",
            )
        )

    if dd is not None and dd >= DEEP_DRAWDOWN_PCT:
        tighter = _halve_risk_spec(spec) if ready else None
        steps.append(
            _step(
                "tighten_risk",
                "Tighten risk before trusting the return",
                f"Max drawdown was {dd:.1f}% — a deep hole even if the window finished up.",
                "Lower risk per trade or max positions, then re-run the same dates.",
                cta=_cta("queue_spec", "Queue with tighter risk", spec=tighter) if tighter else None,
            )
        )
        if tone == "ok":
            tone = "warn"
            headline = "This window made money, but the drawdown is deep"
            if ret is not None:
                summary = (
                    f"Return was {ret:.1f}% with a {dd:.1f}% drawdown. "
                    "Tighten sizing before treating the return as deployable."
                )

    if not steps:
        steps.append(
            _step(
                "review_metrics",
                "Review the metrics",
                "The numbers are ready to read.",
                "Use profit factor, trades, and drawdown together — not return alone.",
            )
        )

    return _package_evaluation(
        outcome={"tone": tone, "headline": headline, "summary": summary},
        next_steps=steps[:MAX_STEPS],
        briefing=briefing,
        requeue_cta=_requeue_cta(spec),
        result=result,
        spec=spec,
    )
