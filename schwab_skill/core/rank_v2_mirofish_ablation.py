"""Shadow/diagnostic-only rank-v2 MiroFish ablation.

Recomputes ``rank_score_v2`` with current weights and with ``pts_mirofish=0``
so operators can see how much crowd-conviction points move live ranking.

This module does **not** change fills, LIVE rank, Stage A filters, or plugin
modes. It is a read-only counterfactual on a signal book.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from core.scoring_rank_v2 import compute_rank_score_v2, rank_v2_from_signal_row

SKILL_DIR = Path(__file__).resolve().parent.parent
FALLBACK_TOP_N = 10


def resolve_ablation_top_n(
    top_n: int | None = None,
    *,
    skill_dir: Path | None = None,
) -> int:
    """Use explicit N, else SIGNAL_TOP_N, else 10 when top-N is uncapped."""
    if top_n is not None:
        try:
            n = int(top_n)
        except (TypeError, ValueError):
            n = 0
        if n > 0:
            return n
    from config import get_signal_top_n

    configured = int(get_signal_top_n(skill_dir or SKILL_DIR))
    if configured > 0:
        return configured
    return FALLBACK_TOP_N


def parse_signals_payload(payload: Any) -> list[dict[str, Any]]:
    """Accept a list, ``{signals: [...]}``, or ``{last_scan: {signals: [...]}}``."""
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if not isinstance(payload, dict):
        return []
    signals = payload.get("signals")
    if isinstance(signals, list):
        return [row for row in signals if isinstance(row, dict)]
    last_scan = payload.get("last_scan")
    if isinstance(last_scan, dict) and isinstance(last_scan.get("signals"), list):
        return [row for row in last_scan["signals"] if isinstance(row, dict)]
    data = payload.get("data")
    if isinstance(data, dict) and isinstance(data.get("signals"), list):
        return [row for row in data["signals"] if isinstance(row, dict)]
    return []


def _safe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(out) or math.isinf(out):
        return None
    return out


def _score_components(row: dict[str, Any]) -> dict[str, Any]:
    comps = row.get("score_components")
    return comps if isinstance(comps, dict) else {}


def _component_raw(row: dict[str, Any], key: str) -> Any:
    comps = _score_components(row)
    if key in comps:
        return comps.get(key)
    return row.get(key)


def _component_float(row: dict[str, Any], key: str, default: float = 0.0) -> float:
    val = _safe_float(_component_raw(row, key))
    return default if val is None else val


def _mirofish_status(row: dict[str, Any]) -> str:
    comps = _score_components(row)
    if "pts_mirofish" not in comps and "pts_mirofish" not in row:
        return "missing"
    raw = _component_raw(row, "pts_mirofish")
    if raw is None:
        return "missing"
    val = _safe_float(raw)
    if val is None:
        return "missing"
    if val == 0.0:
        return "zero"
    return "present"


def _average_ranks(values: list[float]) -> list[float]:
    n = len(values)
    order = sorted(range(n), key=lambda i: values[i])
    ranks = [0.0] * n
    i = 0
    while i < n:
        j = i
        while j + 1 < n and values[order[j + 1]] == values[order[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


def _pearson(xs: list[float], ys: list[float]) -> float | None:
    n = len(xs)
    if n < 3 or n != len(ys):
        return None
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    num = 0.0
    dx = 0.0
    dy = 0.0
    for x, y in zip(xs, ys, strict=True):
        vx = x - mean_x
        vy = y - mean_y
        num += vx * vy
        dx += vx * vx
        dy += vy * vy
    if dx <= 0.0 or dy <= 0.0:
        return None
    return num / math.sqrt(dx * dy)


def _spearman(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) != len(ys) or len(xs) < 3:
        return None
    if len(set(xs)) <= 1 or len(set(ys)) <= 1:
        return None
    return _pearson(_average_ranks(xs), _average_ranks(ys))


def _round(value: float | None, digits: int = 4) -> float | None:
    if value is None:
        return None
    return round(float(value), digits)


def _zero_mirofish_row(row: dict[str, Any]) -> dict[str, Any]:
    out = dict(row)
    out["pts_mirofish"] = 0.0
    comps = _score_components(row)
    if comps:
        out["score_components"] = {**comps, "pts_mirofish": 0.0}
    return out


def _rank_v2_kwargs(
    *,
    signal_weight: float,
    volume_weight: float,
    mirofish_weight: float,
    exclude_52w: bool,
) -> dict[str, Any]:
    return {
        "exclude_52w": bool(exclude_52w),
        "signal_weight": float(signal_weight),
        "volume_weight": float(volume_weight),
        "mirofish_weight": float(mirofish_weight),
    }


def _score_row(
    row: dict[str, Any],
    *,
    pts_mirofish: float | None,
    skill_dir: Path | None,
    weights: dict[str, Any] | None,
) -> float:
    """Score one signal. ``pts_mirofish=None`` keeps the row as-is."""
    if weights is None:
        packed = dict(row) if pts_mirofish is None else _zero_mirofish_row(row)
        if pts_mirofish is not None:
            packed["pts_mirofish"] = float(pts_mirofish)
            comps = _score_components(packed)
            if comps:
                packed["score_components"] = {**comps, "pts_mirofish": float(pts_mirofish)}
        return float(rank_v2_from_signal_row(packed, skill_dir=skill_dir))

    from core.scoring_composite import resolve_rank_volume_points

    comps = _score_components(row)
    flags = row.get("forensic_flags")
    if not isinstance(flags, list):
        flags = []
    sec_tag = row.get("sec_risk_tag")
    rel = _safe_float(row.get("reliability_score"))
    exe = _safe_float(row.get("execution_score"))
    miro = (
        float(pts_mirofish)
        if pts_mirofish is not None
        else _component_float(row, "pts_mirofish", 0.0)
    )
    return compute_rank_score_v2(
        signal_score=float(_safe_float(row.get("signal_score")) or 0.0),
        pts_volume=resolve_rank_volume_points(
            comps.get("pts_volume") or row.get("pts_volume"),
            latest_volume=row.get("latest_volume"),
            avg_vol_50=row.get("avg_vol_50"),
        ),
        pts_mirofish=miro,
        pts_52w=_component_float(row, "pts_52w", 0.0),
        reliability_score=rel,
        execution_score=exe,
        sec_risk_tag=str(sec_tag or "unknown"),
        forensic_flags=flags,
        **weights,
    )


def _ordered_tickers(scored: list[tuple[str, float]]) -> list[str]:
    return [ticker for ticker, _score in sorted(scored, key=lambda item: (-item[1], item[0]))]


def ablate_rank_v2_mirofish(
    signals: list[dict[str, Any]] | None,
    *,
    top_n: int | None = None,
    skill_dir: Path | None = None,
    signal_weight: float | None = None,
    volume_weight: float | None = None,
    mirofish_weight: float | None = None,
    exclude_52w: bool | None = None,
) -> dict[str, Any]:
    """Compare live rank-v2 vs the same blend with ``pts_mirofish`` zeroed.

    Parameters
    ----------
    signals:
        Signal-like dicts. Reads ``ticker``, ``signal_score``, ``pts_volume``,
        ``pts_mirofish``, ``pts_52w`` (top-level or under ``score_components``).
    top_n:
        Diagnostic window. Default is ``SIGNAL_TOP_N`` when > 0, else 10.
    """
    sd = skill_dir or SKILL_DIR
    window = resolve_ablation_top_n(top_n, skill_dir=sd)

    weights: dict[str, Any] | None = None
    if any(v is not None for v in (signal_weight, volume_weight, mirofish_weight, exclude_52w)):
        from config import (
            get_rank_v2_exclude_52w,
            get_rank_v2_mirofish_weight,
            get_rank_v2_signal_weight,
            get_rank_v2_volume_weight,
        )

        weights = _rank_v2_kwargs(
            signal_weight=float(
                signal_weight if signal_weight is not None else get_rank_v2_signal_weight(sd)
            ),
            volume_weight=float(
                volume_weight if volume_weight is not None else get_rank_v2_volume_weight(sd)
            ),
            mirofish_weight=float(
                mirofish_weight if mirofish_weight is not None else get_rank_v2_mirofish_weight(sd)
            ),
            exclude_52w=bool(
                exclude_52w if exclude_52w is not None else get_rank_v2_exclude_52w(sd)
            ),
        )

    from config import (
        get_rank_v2_exclude_52w,
        get_rank_v2_mirofish_weight,
        get_rank_v2_signal_weight,
        get_rank_v2_volume_weight,
    )

    used_weights = {
        "signal": float(
            weights["signal_weight"] if weights else get_rank_v2_signal_weight(sd)
        ),
        "volume": float(
            weights["volume_weight"] if weights else get_rank_v2_volume_weight(sd)
        ),
        "mirofish": float(
            weights["mirofish_weight"] if weights else get_rank_v2_mirofish_weight(sd)
        ),
        "exclude_52w": bool(
            weights["exclude_52w"] if weights else get_rank_v2_exclude_52w(sd)
        ),
    }

    rows_out: list[dict[str, Any]] = []
    baseline_pairs: list[tuple[str, float]] = []
    ablated_pairs: list[tuple[str, float]] = []
    seen: set[str] = set()
    n_missing = 0
    n_zero = 0
    n_present = 0
    skipped_no_ticker = 0

    for raw in signals or []:
        if not isinstance(raw, dict):
            continue
        ticker = str(raw.get("ticker") or "").strip().upper()
        if not ticker:
            skipped_no_ticker += 1
            continue
        if ticker in seen:
            continue
        seen.add(ticker)
        status = _mirofish_status(raw)
        if status == "missing":
            n_missing += 1
        elif status == "zero":
            n_zero += 1
        else:
            n_present += 1

        baseline = _score_row(raw, pts_mirofish=None, skill_dir=sd, weights=weights)
        ablated = _score_row(raw, pts_mirofish=0.0, skill_dir=sd, weights=weights)
        baseline_pairs.append((ticker, baseline))
        ablated_pairs.append((ticker, ablated))
        rows_out.append(
            {
                "ticker": ticker,
                "signal_score": _safe_float(raw.get("signal_score")),
                "pts_volume": _component_float(raw, "pts_volume", 0.0),
                "pts_mirofish": _component_float(raw, "pts_mirofish", 0.0),
                "pts_52w": _component_float(raw, "pts_52w", 0.0),
                "pts_mirofish_status": status,
                "rank_score_v2": baseline,
                "rank_score_v2_mirofish_zero": ablated,
            }
        )

    n = len(rows_out)
    baseline_order = _ordered_tickers(baseline_pairs)
    ablated_order = _ordered_tickers(ablated_pairs)
    baseline_rank = {ticker: i + 1 for i, ticker in enumerate(baseline_order)}
    ablated_rank = {ticker: i + 1 for i, ticker in enumerate(ablated_order)}

    rank_deltas: list[int] = []
    for item in rows_out:
        ticker = str(item["ticker"])
        b_rank = baseline_rank[ticker]
        a_rank = ablated_rank[ticker]
        delta = a_rank - b_rank
        item["rank_baseline"] = b_rank
        item["rank_ablated"] = a_rank
        item["rank_delta"] = delta
        rank_deltas.append(abs(delta))

    window_n = min(window, n) if n else window
    baseline_top = baseline_order[:window_n]
    ablated_top = ablated_order[:window_n]
    baseline_set = set(baseline_top)
    ablated_set = set(ablated_top)
    enter = sorted(ablated_set - baseline_set)
    leave = sorted(baseline_set - ablated_set)
    inter = baseline_set & ablated_set
    union = baseline_set | ablated_set
    jaccard = (len(inter) / len(union)) if union else None
    overlap_frac = (len(inter) / window_n) if window_n else None

    baseline_scores = [score for _t, score in baseline_pairs]
    ablated_scores = [score for _t, score in ablated_pairs]
    spearman_full = _spearman(baseline_scores, ablated_scores)

    union_tickers = [t for t in baseline_order if t in union]
    top_n_spearman = None
    if len(union_tickers) >= 3:
        top_n_spearman = _spearman(
            [float(baseline_rank[t]) for t in union_tickers],
            [float(ablated_rank[t]) for t in union_tickers],
        )

    frac_missing_or_zero = ((n_missing + n_zero) / n) if n else None
    mean_abs = (sum(rank_deltas) / len(rank_deltas)) if rank_deltas else None

    return {
        "ok": True,
        "mode": "shadow_diagnostic_only",
        "changes_live_rank": False,
        "changes_fills": False,
        "n_signals": n,
        "skipped_no_ticker": skipped_no_ticker,
        "top_n": window,
        "top_n_effective": window_n,
        "weights": used_weights,
        "spearman": _round(spearman_full),
        "spearman_full_scores": _round(spearman_full),
        "top_n_spearman": _round(top_n_spearman),
        "top_n_overlap": {
            "baseline": baseline_top,
            "ablated": ablated_top,
            "intersection_count": len(inter),
            "union_count": len(union),
            "jaccard": _round(jaccard),
            "overlap_frac": _round(overlap_frac),
            "enter": enter,
            "leave": leave,
            "enter_count": len(enter),
            "leave_count": len(leave),
        },
        "mean_abs_rank_delta": _round(mean_abs),
        "mirofish_coverage": {
            "n_missing": n_missing,
            "n_zero": n_zero,
            "n_present": n_present,
            "fraction_missing_or_zero": _round(frac_missing_or_zero),
        },
        "rows": rows_out,
    }
