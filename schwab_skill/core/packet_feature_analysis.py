"""Cohort lift analysis for shadow features captured on decision packets.

Supports management-integrity snapshots already stored on packets.
Used by the review loop and ``scripts/analyze_packet_cohorts.py`` to decide
whether a feature earns a single-era pilot before composite-weight tuning.
"""

from __future__ import annotations

from typing import Any, Callable


def _f(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _outcome_label(packet: dict[str, Any]) -> str:
    outcome = packet.get("outcome") if isinstance(packet.get("outcome"), dict) else {}
    return str(outcome.get("label") or "pending").lower()


def _resolved_return(packet: dict[str, Any]) -> float | None:
    outcome = packet.get("outcome") if isinstance(packet.get("outcome"), dict) else {}
    label = _outcome_label(packet)
    if label in {"pending", "unknown"}:
        return None
    return _f(outcome.get("realized_return_pct"))


def _horizon_days(packet: dict[str, Any]) -> int | None:
    outcome = packet.get("outcome") if isinstance(packet.get("outcome"), dict) else {}
    h = outcome.get("horizon_days")
    try:
        return int(h) if h is not None else None
    except (TypeError, ValueError):
        return None


def _avg(xs: list[float]) -> float | None:
    return round(sum(xs) / len(xs), 4) if xs else None


def _win_rate(returns: list[float]) -> float | None:
    if not returns:
        return None
    wins = sum(1 for r in returns if r > 0)
    return round(wins / len(returns), 4)


def _cohort_metrics(packets: list[dict[str, Any]], bucket_key: Callable[[dict[str, Any]], str | None]) -> dict[str, Any]:
    groups: dict[str, list[float]] = {}
    pending = 0
    missing_feature = 0
    for p in packets:
        bucket = bucket_key(p)
        if bucket is None:
            missing_feature += 1
            continue
        ret = _resolved_return(p)
        if ret is None:
            pending += 1
            continue
        groups.setdefault(bucket, []).append(ret)

    cohorts: dict[str, Any] = {}
    for bucket, returns in sorted(groups.items()):
        cohorts[bucket] = {
            "resolved": len(returns),
            "win_rate": _win_rate(returns),
            "avg_return_pct": _avg(returns),
        }
    return {
        "cohorts": cohorts,
        "pending": pending,
        "missing_feature": missing_feature,
    }


def management_integrity_bucket(packet: dict[str, Any]) -> str | None:
    mi = packet.get("management_integrity")
    if not isinstance(mi, dict) or not mi:
        return None
    return str(mi.get("score_bucket") or "unknown").lower()


def split_by_horizon_era(
    packets: list[dict[str, Any]],
    *,
    short_max_days: int = 20,
    long_min_days: int = 21,
    long_max_days: int = 40,
) -> dict[str, list[dict[str, Any]]]:
    """Split resolved packets into ≤short_max and long_min–long_max day eras."""
    short: list[dict[str, Any]] = []
    long: list[dict[str, Any]] = []
    other: list[dict[str, Any]] = []
    for p in packets:
        if _outcome_label(p) in {"pending", "unknown"}:
            continue
        h = _horizon_days(p)
        if h is None:
            other.append(p)
        elif h <= short_max_days:
            short.append(p)
        elif long_min_days <= h <= long_max_days:
            long.append(p)
        else:
            other.append(p)
    return {
        f"le_{short_max_days}d": short,
        f"{long_min_days}_{long_max_days}d": long,
        "other_horizon": other,
    }


def feature_lift_report(
    packets: list[dict[str, Any]] | None,
    *,
    short_max_days: int = 20,
    long_min_days: int = 21,
    long_max_days: int = 40,
) -> dict[str, Any]:
    """Full lift report for management integrity with era splits."""
    rows = [p for p in (packets or []) if isinstance(p, dict)]
    resolved = sum(1 for p in rows if _outcome_label(p) not in {"pending", "unknown"})
    with_mgmt = sum(1 for p in rows if management_integrity_bucket(p) is not None)

    era_splits = split_by_horizon_era(
        rows,
        short_max_days=short_max_days,
        long_min_days=long_min_days,
        long_max_days=long_max_days,
    )

    def _era_analysis(subset: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "management_integrity": _cohort_metrics(subset, management_integrity_bucket),
        }

    return {
        "total_packets": len(rows),
        "resolved_packets": resolved,
        "coverage_pct": round(resolved / len(rows) * 100, 1) if rows else 0.0,
        "management_integrity_packets": with_mgmt,
        "era_splits": {
            era: _era_analysis(subset) for era, subset in era_splits.items() if subset
        },
        "all_resolved": _era_analysis([p for p in rows if _resolved_return(p) is not None]),
        "pilot_recommendation": _pilot_recommendation(rows, era_splits),
    }


def _baseline_avg_return(packets: list[dict[str, Any]]) -> float | None:
    returns = [_resolved_return(p) for p in packets]
    clean = [r for r in returns if r is not None]
    return _avg(clean)


def _best_cohort_lift(
    packets: list[dict[str, Any]],
    bucket_fn: Callable[[dict[str, Any]], str | None],
    *,
    min_samples: int = 5,
) -> tuple[str | None, float | None]:
    baseline = _baseline_avg_return(packets)
    if baseline is None:
        return None, None
    metrics = _cohort_metrics(packets, bucket_fn)
    best_bucket: str | None = None
    best_lift: float | None = None
    for bucket, stats in (metrics.get("cohorts") or {}).items():
        resolved = int(stats.get("resolved") or 0)
        avg = stats.get("avg_return_pct")
        if resolved < min_samples or avg is None:
            continue
        lift = float(avg) - baseline
        if best_lift is None or lift > best_lift:
            best_lift = lift
            best_bucket = bucket
    return best_bucket, round(best_lift, 4) if best_lift is not None else None


def _pilot_recommendation(
    packets: list[dict[str, Any]],
    era_splits: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    """Suggest which feature (if any) merits a single-era pilot."""
    short_key = next((k for k in era_splits if k.startswith("le_")), None)
    long_key = next((k for k in era_splits if k.endswith("d") and not k.startswith("le_")), None)
    short_rows = era_splits.get(short_key or "", [])
    long_rows = era_splits.get(long_key or "", [])

    short_bucket, short_lift = _best_cohort_lift(short_rows, management_integrity_bucket)
    long_bucket, long_lift = _best_cohort_lift(long_rows, management_integrity_bucket)
    candidate = {
        "feature": "management_integrity",
        "short_era_best_bucket": short_bucket,
        "short_era_lift_pct": short_lift,
        "long_era_best_bucket": long_bucket,
        "long_era_lift_pct": long_lift,
    }

    ready = False
    if short_lift is not None:
        ready = (
            short_lift > 0
            and (long_lift is None or long_lift >= 0)
            and len(short_rows) >= 5
        )
    return {
        "ready_for_single_era_pilot": ready,
        "recommended_feature": candidate["feature"] if ready else None,
        "candidates": [candidate],
        "note": (
            "Composite SCORE_COMPOSITE_*_WEIGHT tuning is deferred until a single-era "
            "pilot confirms lift on the ≤20d vs 21–40d split."
        ),
    }
