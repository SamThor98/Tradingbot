"""Sleeve desired-weight builder: conviction select, vol-target, net book, hysteresis."""

from __future__ import annotations

import math
from typing import Any

from core.multi_sleeve_constitution import (
    HYSTERESIS_ABS_WEIGHT,
    SLEEVE_S0,
    SLEEVE_S1,
    SLEEVE_S3,
    TOP_N_S0,
    TOP_N_S1,
)
from core.portfolio_allocator import (
    AllocatorDecision,
    apply_name_and_cluster_caps,
    idle_s1_to_cash,
    scale_sleeve_bundle,
)
from core.portfolio_analytics import normalize_weights


def _score_key(row: dict[str, Any], score_field: str) -> float:
    try:
        return float(row.get(score_field) or 0.0)
    except (TypeError, ValueError):
        return 0.0


def select_top_n(
    rows: list[dict[str, Any]],
    *,
    n: int,
    score_field: str = "signal_score",
    ticker_field: str = "ticker",
) -> list[dict[str, Any]]:
    """Pick top-N by score descending, ticker ascending tie-break."""
    cleaned = [r for r in rows if isinstance(r, dict) and r.get(ticker_field)]
    cleaned.sort(
        key=lambda r: (-_score_key(r, score_field), str(r.get(ticker_field) or "").upper()),
    )
    return cleaned[: max(0, int(n))]


def vol_target_weights(
    rows: list[dict[str, Any]],
    *,
    vol_field: str = "realized_vol",
    ticker_field: str = "ticker",
    default_vol: float = 0.02,
) -> dict[str, float]:
    """Equal-risk (1/vol) weights across rows; falls back to equal weight."""
    inv: dict[str, float] = {}
    for r in rows:
        tkr = str(r.get(ticker_field) or "").upper()
        if not tkr:
            continue
        try:
            vol = float(r.get(vol_field)) if r.get(vol_field) is not None else default_vol
        except (TypeError, ValueError):
            vol = default_vol
        if not math.isfinite(vol) or vol <= 0:
            vol = default_vol
        inv[tkr] = 1.0 / vol
    if not inv:
        return {}
    return normalize_weights(inv, renormalize=True)


def build_sleeve_desired_weights(
    rows: list[dict[str, Any]],
    *,
    sleeve_id: str,
    top_n: int,
    sleeve_cap: float,
    risk_mult: float,
    score_field: str = "signal_score",
    vol_field: str = "realized_vol",
) -> dict[str, float]:
    """Conviction selects set; vol-target weights; scale to sleeve_cap * risk_mult."""
    selected = select_top_n(rows, n=top_n, score_field=score_field)
    raw = vol_target_weights(selected, vol_field=vol_field)
    return scale_sleeve_bundle(raw, sleeve_cap=sleeve_cap, risk_mult=risk_mult)


def net_exposure_book(
    sleeve_weights: dict[str, dict[str, float]],
    *,
    sector_by_ticker: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Sum sleeve desired weights into one ticker ledger; apply name/cluster caps.

    ``sleeve_weights`` maps sleeve_id -> {ticker: weight}.
    """
    combined: dict[str, float] = {}
    for _sleeve, wmap in (sleeve_weights or {}).items():
        for tkr, w in (wmap or {}).items():
            key = str(tkr).upper()
            combined[key] = combined.get(key, 0.0) + float(w)
    capped = apply_name_and_cluster_caps(combined, sector_by_ticker=sector_by_ticker)
    cash = max(0.0, 1.0 - sum(capped.values()))
    return {
        "weights": capped,
        "cash_weight": cash,
        "gross": sum(capped.values()),
        "n_names": len(capped),
    }


def apply_hysteresis(
    target: dict[str, float],
    current: dict[str, float],
    *,
    band: float = HYSTERESIS_ABS_WEIGHT,
) -> dict[str, float]:
    """Keep current weight unless |Δw| > band or membership flips."""
    tgt = {str(k).upper(): float(v) for k, v in (target or {}).items() if float(v) > 0}
    cur = {str(k).upper(): float(v) for k, v in (current or {}).items() if float(v) > 0}
    out: dict[str, float] = {}
    all_tickers = set(tgt) | set(cur)
    for tkr in all_tickers:
        tw = tgt.get(tkr, 0.0)
        cw = cur.get(tkr, 0.0)
        if (tw > 0) != (cw > 0):
            # membership change → take target
            if tw > 0:
                out[tkr] = tw
            continue
        if abs(tw - cw) > band:
            out[tkr] = tw
        elif cw > 0:
            out[tkr] = cw
    return out


def build_multi_sleeve_targets(
    *,
    s0_rows: list[dict[str, Any]],
    s1_rows: list[dict[str, Any]] | None,
    decision: AllocatorDecision,
    current_weights: dict[str, float] | None = None,
    sector_by_ticker: dict[str, str] | None = None,
    s0_score_field: str = "signal_score",
    s1_score_field: str = "edge_score",
    s1_paper_only: bool = True,
) -> dict[str, Any]:
    """Build S0 (+ optional S1) desired weights and net book under allocator decision."""
    s0_w = build_sleeve_desired_weights(
        s0_rows,
        sleeve_id=SLEEVE_S0,
        top_n=TOP_N_S0,
        sleeve_cap=decision.s0_cap,
        risk_mult=decision.s0_new_risk_mult,
        score_field=s0_score_field,
    )
    s1_w: dict[str, float] = {}
    if s1_rows and decision.s1_new_risk_mult > 0 and not s1_paper_only:
        s1_w = build_sleeve_desired_weights(
            s1_rows,
            sleeve_id=SLEEVE_S1,
            top_n=TOP_N_S1,
            sleeve_cap=decision.s1_cap,
            risk_mult=decision.s1_new_risk_mult,
            score_field=s1_score_field,
        )
    elif s1_rows:
        # Paper S1: compute would-be weights but do not net into live book
        s1_w_paper = build_sleeve_desired_weights(
            s1_rows,
            sleeve_id=SLEEVE_S1,
            top_n=TOP_N_S1,
            sleeve_cap=decision.s1_cap,
            risk_mult=decision.s1_new_risk_mult,
            score_field=s1_score_field,
        )
    else:
        s1_w_paper = {}

    s1_for_net = s1_w if not s1_paper_only else {}
    idle_cash = idle_s1_to_cash(decision.s1_cap, sum(s1_for_net.values()))
    sleeve_map = {SLEEVE_S0: s0_w}
    if s1_for_net:
        sleeve_map[SLEEVE_S1] = s1_for_net
    book = net_exposure_book(sleeve_map, sector_by_ticker=sector_by_ticker)
    book["cash_weight"] = max(book.get("cash_weight", 0.0), idle_cash)
    book[SLEEVE_S3] = book["cash_weight"]

    traded = apply_hysteresis(book["weights"], current_weights or {})
    return {
        "sleeves": {
            SLEEVE_S0: s0_w,
            SLEEVE_S1: s1_w if not s1_paper_only else s1_w_paper,
            SLEEVE_S3: {"CASH": book["cash_weight"]},
        },
        "s1_paper_only": s1_paper_only,
        "net_book": book,
        "trade_targets": traded,
        "allocator": decision.as_dict(),
    }
