"""Rank completed dashboard backtests. Display-only — not a LIVE promotion verdict."""

from __future__ import annotations

from typing import Any

from .backtest_labels import attach_run_labels, format_backtest_label, window_years_from_spec
from .backtest_outcomes import FALLBACK_SHARE, LIVE_MEMBERSHIP_MODES, PF_GATE, build_horizon_actions
from .backtest_outcomes import fallback_share as integrity_fallback_share

MIN_TRADES = 35
THIN_TRADES = 10
LARGE_N = 100
SCAN_CAP = 400
QUALIFIED_RETURN = 40
THIN_RETURN = 10
WINDOW_RETURN = 10
UNIVERSE_RETURN = 8

CORE_UNIVERSES = frozenset({"sp1500", "sp500", "nasdaq100"})
CORE_UNIVERSE_ORDER: tuple[str, ...] = ("nasdaq100", "sp500", "sp1500")
HORIZON_YEARS: tuple[int, ...] = (2, 5, 8)
THIN_UNIVERSES = frozenset({"sector_etfs", "focused"})
TRUST_LABELS = {
    0: "Core large-n",
    1: "Large-n",
    2: "Qualified",
    3: "Thin index",
}
SORT_MODE = "trust_then_robustness_then_pf"


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


def _finite_pf(summary: dict[str, Any] | None) -> float | None:
    payload = summary if isinstance(summary, dict) else {}
    n = _num(payload.get("profit_factor_net"))
    if n is None or n == float("inf"):
        return None
    return n


def _strategy_ids(spec: dict[str, Any], labels: dict[str, Any]) -> list[str]:
    raw = spec.get("strategy_ids")
    if isinstance(raw, list):
        ids = [str(s).strip().lower() for s in raw if str(s).strip()]
        if ids:
            return ids
    parts = labels.get("parts") if isinstance(labels.get("parts"), dict) else {}
    extra = parts.get("strategy_ids")
    if isinstance(extra, list):
        return [str(s).strip().lower() for s in extra if str(s).strip()]
    return []


def sleeve_key(strategy_ids: list[str] | None) -> str:
    return ",".join(sorted({s for s in (strategy_ids or []) if s}))


def cluster_key(universe_id: str, strategy_ids: list[str], window_years: Any) -> str:
    years = window_years if window_years is not None else ""
    return f"{universe_id}|{sleeve_key(strategy_ids)}|{years}"


def _window_bucket(years: Any) -> int | None:
    n = _num(years)
    if n is None or n == float("inf"):
        return None
    return int(n)


def trust_tier(*, trades: int, universe_id: str) -> int:
    """Lower is more believable. Core large-n beats thin ETF fireworks."""
    uni = str(universe_id or "").strip().lower()
    if trades >= LARGE_N and uni in CORE_UNIVERSES:
        return 0
    if trades >= LARGE_N:
        return 1
    if uni not in THIN_UNIVERSES:
        return 2
    return 3


def _row_payload(run: dict[str, Any], *, reason: str | None = None) -> dict[str, Any]:
    spec = run.get("spec") if isinstance(run.get("spec"), dict) else {}
    summary = run.get("result_summary") if isinstance(run.get("result_summary"), dict) else {}
    labels = format_backtest_label(spec)
    parts = labels.get("parts") if isinstance(labels.get("parts"), dict) else {}
    years = run.get("window_years")
    if years is None:
        years = parts.get("window_years")
    if years is None:
        years = window_years_from_spec(spec)
    universe_id = str(parts.get("universe_id") or spec.get("universe_preset") or "").strip().lower()
    strategy_ids = _strategy_ids(spec, labels)
    strategy_names = [str(n) for n in (parts.get("strategies") or []) if n]
    universe_name = str(parts.get("universe") or "")
    window_text = str(parts.get("window_label") or "")
    trades = _intish(summary.get("total_trades"))
    tier = trust_tier(trades=trades, universe_id=universe_id)
    cluster_label = " · ".join(p for p in (universe_name, " + ".join(strategy_names), window_text) if p)
    fallback_share, membership_mode = _integrity_fields(run, summary)
    not_schwab_clean = fallback_share is not None and fallback_share >= FALLBACK_SHARE
    live_membership = str(membership_mode or "") in LIVE_MEMBERSHIP_MODES
    return {
        "id": run.get("id"),
        "display_label": run.get("display_label") or labels["title"],
        "display_subtitle": run.get("display_subtitle") or labels["subtitle"],
        "cluster_label": cluster_label or (run.get("display_label") or labels["title"]),
        "profit_factor_net": summary.get("profit_factor_net"),
        "total_trades": trades,
        "win_rate_net": summary.get("win_rate_net"),
        "total_return_net_pct": summary.get("total_return_net_pct"),
        "max_drawdown_net_pct": summary.get("max_drawdown_net_pct"),
        "window_years": years,
        "window_label": window_text,
        "universe_id": universe_id,
        "strategy_ids": strategy_ids,
        "sleeve_key": sleeve_key(strategy_ids),
        "cluster_key": cluster_key(universe_id, strategy_ids, years),
        "trust_tier": tier,
        "trust_label": TRUST_LABELS[tier],
        "variant_count": 1,
        "fallback_share": fallback_share,
        "membership_filter_mode": membership_mode,
        "not_schwab_clean": not_schwab_clean,
        "live_membership": live_membership,
        "status": run.get("status"),
        "created_at": run.get("created_at"),
        "reason": reason,
        "_spec": spec,
    }


def _integrity_fields(run: dict[str, Any], summary: dict[str, Any]) -> tuple[float | None, str | None]:
    share = _num(summary.get("fallback_share"))
    mode = summary.get("membership_filter_mode")
    integrity = run.get("data_integrity")
    if not isinstance(integrity, dict):
        integrity = summary.get("data_integrity") if isinstance(summary.get("data_integrity"), dict) else {}
    if share is None and integrity:
        share = integrity_fallback_share(integrity)
    if not mode and isinstance(integrity, dict):
        mode = integrity.get("membership_filter_mode")
    mode_text = str(mode).strip() if mode else ""
    return share, (mode_text or None)


def _variant_sort_key(row: dict[str, Any]) -> tuple[int, float, int, float, float]:
    """Best clone inside a cluster — PF, not robustness."""
    pf = _num(row.get("profit_factor_net")) or 0.0
    trades = _intish(row.get("total_trades"))
    dd = _num(row.get("max_drawdown_net_pct"))
    if dd is None:
        dd = 0.0
    ret = _num(row.get("total_return_net_pct")) or 0.0
    tier = row.get("trust_tier")
    if not isinstance(tier, int):
        tier = trust_tier(trades=trades, universe_id=str(row.get("universe_id") or ""))
    return (tier, -pf, -trades, dd, -ret)


def _sort_key(row: dict[str, Any]) -> tuple[int, float, float, float, int, float, float]:
    """Trust first. Core large-n then robustness, then PF. Display-only."""
    pf = _num(row.get("profit_factor_net")) or 0.0
    trades = _intish(row.get("total_trades"))
    dd = _num(row.get("max_drawdown_net_pct"))
    if dd is None:
        dd = 0.0
    ret = _num(row.get("total_return_net_pct")) or 0.0
    tier = row.get("trust_tier")
    if not isinstance(tier, int):
        tier = trust_tier(trades=trades, universe_id=str(row.get("universe_id") or ""))
    score = _num(row.get("robustness_score")) or 0.0
    pf_min = _num(row.get("robustness_pf_min")) or 0.0
    if tier != 0:
        score = 0.0
        pf_min = 0.0
    return (tier, -score, -pf_min, -pf, -trades, dd, -ret)


def classify_run(run: dict[str, Any]) -> str:
    """Return qualified, thin, or excluded."""
    if not run:
        return "excluded"
    if str(run.get("status") or "") != "success":
        return "excluded"
    if not run.get("has_result") and not run.get("result_summary"):
        return "excluded"
    summary = run.get("result_summary") if isinstance(run.get("result_summary"), dict) else {}
    pf = _finite_pf(summary)
    if pf is None:
        return "excluded"
    trades = _intish(summary.get("total_trades"))
    spec = run.get("spec") if isinstance(run.get("spec"), dict) else {}
    years = run.get("window_years")
    if years is None:
        years = window_years_from_spec(spec)
    if years is None:
        return "excluded"
    if trades >= MIN_TRADES:
        return "qualified"
    if trades >= THIN_TRADES:
        return "thin"
    return "excluded"


def _thin_reason(run: dict[str, Any]) -> str:
    spec = run.get("spec") if isinstance(run.get("spec"), dict) else {}
    labels = format_backtest_label(spec)
    parts = labels.get("parts") if isinstance(labels.get("parts"), dict) else {}
    universe_id = str(parts.get("universe_id") or spec.get("universe_preset") or "").strip().lower()
    if universe_id in THIN_UNIVERSES:
        return "Thin index — too few trades to rank."
    return "Too few trades to rank."


def _cluster_qualified(qualified: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    order: list[str] = []
    for row in qualified:
        key = str(row.get("cluster_key") or row.get("id") or "")
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(row)
    clustered: list[dict[str, Any]] = []
    for key in order:
        members = sorted(groups[key], key=_variant_sort_key)
        best = dict(members[0])
        pfs = [_num(m.get("profit_factor_net")) for m in members]
        finite = [p for p in pfs if p is not None and p != float("inf")]
        best["variant_count"] = len(members)
        best["cluster_key"] = key
        best["display_label"] = best.get("cluster_label") or best.get("display_label")
        if finite:
            best["cluster_pf_min"] = min(finite)
            best["cluster_pf_max"] = max(finite)
        clustered.append(best)
    clustered = _attach_robustness(clustered)
    clustered.sort(key=_sort_key)
    return clustered


def _attach_robustness(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Min finite PF across core indexes (same window) and across 2y/5y/8y when present."""
    by_sleeve_window: dict[tuple[str, int], dict[str, float]] = {}
    by_sleeve_universe: dict[tuple[str, str], dict[int, float]] = {}
    for row in rows:
        sleeve = str(row.get("sleeve_key") or sleeve_key(list(row.get("strategy_ids") or [])))
        if not sleeve:
            continue
        uni = str(row.get("universe_id") or "").strip().lower()
        years = _window_bucket(row.get("window_years"))
        pf = _finite_pf({"profit_factor_net": row.get("profit_factor_net")})
        if pf is None or years is None:
            continue
        if uni in CORE_UNIVERSES:
            uni_map = by_sleeve_window.setdefault((sleeve, years), {})
            prev = uni_map.get(uni)
            uni_map[uni] = pf if prev is None else min(prev, pf)
        hor_map = by_sleeve_universe.setdefault((sleeve, uni), {})
        prev_h = hor_map.get(years)
        hor_map[years] = pf if prev_h is None else min(prev_h, pf)
    out: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        sleeve = str(item.get("sleeve_key") or sleeve_key(list(item.get("strategy_ids") or [])))
        uni = str(item.get("universe_id") or "").strip().lower()
        years = _window_bucket(item.get("window_years"))
        own = _finite_pf({"profit_factor_net": item.get("profit_factor_net")})
        universe_pfs = dict(by_sleeve_window.get((sleeve, years), {})) if years is not None else {}
        horizon_pfs = dict(by_sleeve_universe.get((sleeve, uni), {}))
        universes = [u for u in CORE_UNIVERSE_ORDER if u in universe_pfs]
        horizons = [y for y in HORIZON_YEARS if y in horizon_pfs]
        mins: list[float] = []
        mins.extend(universe_pfs[u] for u in universes)
        mins.extend(horizon_pfs[y] for y in horizons)
        if own is not None:
            mins.append(own)
        pf_min = min(mins) if mins else own
        n_uni = len(universes)
        n_horiz = len(horizons)
        score = 0.0
        if pf_min is not None:
            score = (100.0 * n_uni) + (10.0 * n_horiz) + min(float(pf_min), 9.99)
        item["robustness_universes"] = universes
        item["robustness_horizons"] = horizons
        item["robustness_pf_min"] = pf_min
        item["robustness_score"] = round(score, 4) if score else 0.0
        out.append(item)
    return out


def _horizon_sibling_index(rows: list[dict[str, Any]]) -> dict[tuple[str, str, int], Any]:
    best: dict[tuple[str, str, int], dict[str, Any]] = {}
    for row in rows:
        sleeve = str(row.get("sleeve_key") or sleeve_key(list(row.get("strategy_ids") or [])))
        uni = str(row.get("universe_id") or "").strip().lower()
        years = _window_bucket(row.get("window_years"))
        if not sleeve or years not in HORIZON_YEARS:
            continue
        key = (sleeve, uni, years)
        prev = best.get(key)
        if prev is None or _variant_sort_key(row) < _variant_sort_key(prev):
            best[key] = row
    return {key: row.get("id") for key, row in best.items()}


def _attach_horizon_actions(rows: list[dict[str, Any]], index: dict[tuple[str, str, int], Any]) -> None:
    for row in rows:
        spec = row.get("_spec") if isinstance(row.get("_spec"), dict) else {}
        sleeve = str(row.get("sleeve_key") or sleeve_key(list(row.get("strategy_ids") or [])))
        uni = str(row.get("universe_id") or "").strip().lower()
        existing: dict[int, Any] = {}
        for years in HORIZON_YEARS:
            rid = index.get((sleeve, uni, years))
            if rid:
                existing[years] = rid
        row["horizon_actions"] = build_horizon_actions(
            spec,
            current_years=_window_bucket(row.get("window_years")),
            existing_by_years=existing,
        )


def _strip_internal_row_fields(rows: list[dict[str, Any]]) -> None:
    for row in rows:
        row.pop("_spec", None)


def rank_backtests(runs: list[dict[str, Any]] | None) -> dict[str, Any]:
    """Rank successful runs. Inf PF and tiny samples never make the podium."""
    source = list(runs or [])
    qualified: list[dict[str, Any]] = []
    thin: list[dict[str, Any]] = []
    excluded = 0
    for run in source:
        bucket = classify_run(run)
        if bucket == "qualified":
            qualified.append(_row_payload(run))
        elif bucket == "thin":
            thin.append(_row_payload(run, reason=_thin_reason(run)))
        else:
            excluded += 1
    qualified.sort(key=_variant_sort_key)
    thin.sort(key=_variant_sort_key)
    clustered = _cluster_qualified(qualified)
    index = _horizon_sibling_index(qualified + thin)
    _attach_horizon_actions(clustered, index)
    _attach_horizon_actions(thin, index)
    _strip_internal_row_fields(clustered)
    _strip_internal_row_fields(thin)
    for idx, row in enumerate(clustered, start=1):
        row["rank"] = idx
    return {
        "criteria": {
            "min_trades": MIN_TRADES,
            "thin_trades": THIN_TRADES,
            "large_n_trades": LARGE_N,
            "sort": SORT_MODE,
            "cluster": "universe+strategies+window",
            "robustness": "min_finite_pf_across_core_universes_and_horizons",
            "pf_gate": PF_GATE,
            "fallback_share_flag": FALLBACK_SHARE,
        },
        "qualified": _payload_rows(clustered),
        "thin": thin[:THIN_RETURN],
        "scanned": len(source),
        "excluded_count": excluded,
        "qualified_count": len(clustered),
        "pre_cluster_count": len(qualified),
        "thin_count": len(thin),
    }


def _payload_rows(qualified: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep the global top slice plus enough rows that filters are not empty."""
    seen: set[Any] = set()
    out: list[dict[str, Any]] = []

    def add(rows: list[dict[str, Any]], limit: int) -> None:
        added = 0
        for row in rows:
            rid = row.get("id")
            if rid in seen:
                continue
            seen.add(rid)
            out.append(row)
            added += 1
            if added >= limit:
                break

    add(qualified, QUALIFIED_RETURN)
    for years in (2, 5, 8):
        add([row for row in qualified if row.get("window_years") == years], WINDOW_RETURN)
    for uni in ("sp1500", "sp500", "nasdaq100", "sector_etfs", "focused"):
        add([row for row in qualified if row.get("universe_id") == uni], UNIVERSE_RETURN)
    out.sort(key=_sort_key)
    for idx, row in enumerate(out, start=1):
        row["rank"] = idx
    return out


def leaderboard_item_from_parts(
    *,
    run_id: str | None,
    status: str | None,
    spec: dict[str, Any] | None,
    summary: dict[str, Any] | None,
    created_at: str | None = None,
) -> dict[str, Any]:
    """Lightweight run dict for ranking (skips evaluate_run)."""
    item: dict[str, Any] = {
        "id": run_id,
        "status": status,
        "spec": spec if isinstance(spec, dict) else {},
        "has_result": bool(summary),
        "created_at": created_at,
    }
    if summary:
        item["result_summary"] = summary
    return attach_run_labels(item)
