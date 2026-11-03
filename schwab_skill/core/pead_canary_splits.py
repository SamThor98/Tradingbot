"""Observational splits on the paper S1 PEAD canary.

The sleeve stays the capacity top 5 by ``edge_score``. These flags do not
remove a name, change that sort, or open an order path.

- Extended: ``close_vs_sma200_pct`` at or above the frozen PEAD 80th
  percentile (default 0.2028). Same cutoff as ``core.pead_extension_drop``.
- High surprise: fractional surprise above 0.15, the same band the scanner
  and backtest already treat as a large beat.
"""

from __future__ import annotations

from typing import Any

from core.pead_extension_drop import sma200_distance

# Matches ``s > 0.15`` in signal_scanner.py and backtest.py.
HIGH_SURPRISE_MIN = 0.15
SLEEVE_ID = "S1"


def _surprise(row: dict[str, Any]) -> float | None:
    raw = row.get("pead_surprise_pct")
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def annotate_pead_shadow_name(row: dict[str, Any], *, skill_dir: Any = None) -> dict[str, Any]:
    """Copy one shadow name and attach the two pre-registered flags.

    Missing distance or surprise stays None. A missing flag is not a pass
    and not a fail.
    """
    from config import get_pead_extension_drop_min

    out = dict(row)
    cutoff = float(get_pead_extension_drop_min(skill_dir))
    distance = sma200_distance(out)
    out["pead_extension_drop_min"] = cutoff
    out["pead_extension_drop_basis"] = "close_vs_sma200_pct_p80"
    prior_drop = row.get("pead_extension_drop_would_drop")
    if distance is None:
        out["close_vs_sma200_pct"] = row.get("close_vs_sma200_pct")
        out["pead_extension_drop_would_drop"] = prior_drop if prior_drop is True or prior_drop is False else None
    else:
        rounded = round(float(distance), 4)
        out["close_vs_sma200_pct"] = rounded
        out["pead_extension_drop_would_drop"] = rounded >= cutoff
    surprise = _surprise(out)
    prior_large = row.get("pead_surprise_large")
    if surprise is None:
        out["pead_surprise_large"] = prior_large if prior_large is True or prior_large is False else None
    else:
        out["pead_surprise_large"] = bool(surprise > HIGH_SURPRISE_MIN)
    out["pead_surprise_large_min"] = HIGH_SURPRISE_MIN
    out["executable"] = False
    return out


def _context(record: dict[str, Any]) -> dict[str, Any]:
    ctx = record.get("raw_context")
    return ctx if isinstance(ctx, dict) else {}


def _outcome_at_horizon(record: dict[str, Any], horizon: str) -> dict[str, Any] | None:
    out = record.get("outcomes")
    if not isinstance(out, dict):
        return None
    for key in (horizon, str(horizon)):
        metrics = out.get(key)
        if isinstance(metrics, dict):
            return metrics
    if str(horizon).isdigit():
        metrics = out.get(int(horizon))
        if isinstance(metrics, dict):
            return metrics
    return None


def _tri_bool(raw: Any) -> bool | None:
    if raw is True or raw is False:
        return raw
    return None


def extension_would_drop(record: dict[str, Any]) -> bool | None:
    """True when the name was extended at entry. None when it was not measured."""
    ctx = _context(record)
    if "pead_extension_drop_would_drop" in ctx:
        return _tri_bool(ctx.get("pead_extension_drop_would_drop"))
    if "pead_extension_drop_would_drop" in record:
        return _tri_bool(record.get("pead_extension_drop_would_drop"))
    return None


def surprise_is_large(record: dict[str, Any]) -> bool | None:
    """True when entry surprise is above the large-beat band. None if unknown."""
    ctx = _context(record)
    if "pead_surprise_large" in ctx:
        flagged = _tri_bool(ctx.get("pead_surprise_large"))
        if flagged is not None:
            return flagged
    if "pead_surprise_large" in record:
        flagged = _tri_bool(record.get("pead_surprise_large"))
        if flagged is not None:
            return flagged
    surprise = _surprise(ctx) if "pead_surprise_pct" in ctx else _surprise(record)
    if surprise is None:
        return None
    return bool(surprise > HIGH_SURPRISE_MIN)


def _accumulate(bucket: dict[str, Any], metrics: dict[str, Any]) -> None:
    if "thesis_hit" not in metrics and "return_pct" not in metrics:
        return
    bucket["n"] += 1
    if metrics.get("thesis_hit") is True:
        bucket["hits"] += 1
    raw = metrics.get("return_pct")
    if raw is None:
        return
    try:
        bucket["returns"].append(float(raw))
    except (TypeError, ValueError):
        return


def _finalize(bucket: dict[str, Any]) -> dict[str, Any]:
    n = int(bucket["n"])
    hits = int(bucket["hits"])
    rets: list[float] = bucket["returns"]
    mean = (sum(rets) / len(rets)) if rets else None
    return {
        "n": n,
        "hit_rate": round(hits / n, 4) if n else None,
        "expectancy": round(mean, 6) if mean is not None else None,
        "mean_return_pct": round(mean, 4) if mean is not None else None,
    }


def summarize_s1_splits(
    records: list[dict[str, Any]],
    *,
    sleeve_id: str = SLEEVE_ID,
    horizon: str = "5",
    skill_dir: Any = None,
) -> dict[str, Any]:
    """T+horizon table for the full sleeve and the two pre-registered subsets.

    Subsets omit rows whose flag was never stamped. They do not feed R7.
    """
    from config import get_pead_extension_drop_min

    wanted = str(sleeve_id or SLEEVE_ID).upper()
    buckets = {
        "all": {"n": 0, "hits": 0, "returns": []},
        "not_extended": {"n": 0, "hits": 0, "returns": []},
        "high_surprise": {"n": 0, "hits": 0, "returns": []},
    }
    extension_unmeasured = 0
    surprise_unmeasured = 0
    for record in records:
        if not isinstance(record, dict):
            continue
        if str(record.get("sleeve_id") or "").upper() != wanted:
            continue
        metrics = _outcome_at_horizon(record, str(horizon))
        if metrics is None:
            continue
        before = buckets["all"]["n"]
        _accumulate(buckets["all"], metrics)
        if buckets["all"]["n"] == before:
            continue
        extended = extension_would_drop(record)
        if extended is False:
            _accumulate(buckets["not_extended"], metrics)
        elif extended is None:
            extension_unmeasured += 1
        large = surprise_is_large(record)
        if large is True:
            _accumulate(buckets["high_surprise"], metrics)
        elif large is None:
            surprise_unmeasured += 1
    return {
        "sleeve_id": wanted,
        "horizon": str(horizon),
        "allow_live": False,
        "changes_rank": False,
        "extension_min": float(get_pead_extension_drop_min(skill_dir)),
        "high_surprise_min": HIGH_SURPRISE_MIN,
        "extension_unmeasured": extension_unmeasured,
        "surprise_unmeasured": surprise_unmeasured,
        "slices": {name: _finalize(bucket) for name, bucket in buckets.items()},
    }


def format_s1_split_lines(summary: dict[str, Any]) -> list[str]:
    """One line per slice for the paper-session log."""
    horizon = summary.get("horizon") or "5"
    lines: list[str] = []
    slices = summary.get("slices") if isinstance(summary.get("slices"), dict) else {}
    for name in ("all", "not_extended", "high_surprise"):
        row = slices.get(name) if isinstance(slices.get(name), dict) else {}
        lines.append(
            f"s1_split {name} horizon=T+{horizon} n={int(row.get('n') or 0)} "
            f"hit_rate={row.get('hit_rate')} mean_return_pct={row.get('mean_return_pct')}"
        )
    lines.append(
        "s1_split note=observational allow_live=false "
        f"extension_unmeasured={int(summary.get('extension_unmeasured') or 0)} "
        f"surprise_unmeasured={int(summary.get('surprise_unmeasured') or 0)}"
    )
    return lines
