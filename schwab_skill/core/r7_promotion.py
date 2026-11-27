"""R7 promotion process contract: sleeve-local ledger gates.

``select_top_n_per_et_date`` and ``day_clustered_mean_ci`` feed the daily
scoreboard. ``r7_may_promote`` still scores every ledger row and does not
apply the top-N cut.
"""

from __future__ import annotations

import random
from typing import Any

from core.multi_sleeve_constitution import R7_MIN_N, R7_PRIMARY_HORIZON, SLEEVE_S0

# Day-cluster bootstrap for the brief. Fixed seed so a replay prints the same interval.
R7_BOOTSTRAP_SEED = 42
R7_BOOTSTRAP_DRAWS = 1000


def _sleeve_id(record: dict[str, Any]) -> str:
    return str(record.get("sleeve_id") or SLEEVE_S0).upper()


def _et_day(record: dict[str, Any]) -> str:
    explicit = str(record.get("et_date") or "").strip()
    if explicit:
        return explicit[:10]
    return str(record.get("created_at") or "")[:10]


def _signal_score(record: dict[str, Any]) -> float | None:
    """Score used to rank an S0 name. Missing or non-finite scores sort last."""
    raw = record.get("raw_context")
    candidates: list[Any] = []
    if isinstance(raw, dict):
        candidates.append(raw.get("signal_score"))
    candidates.append(record.get("signal_score"))
    for value in candidates:
        if value is None or isinstance(value, bool):
            continue
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if number != number or number in (float("inf"), float("-inf")):
            continue
        return number
    return None


def _rank_key(record: dict[str, Any]) -> tuple[int, float, str]:
    score = _signal_score(record)
    ticker = str(record.get("ticker") or "").upper()
    if score is None:
        return (1, 0.0, ticker)
    return (0, -score, ticker)


def select_top_n_per_et_date(
    records: list[dict[str, Any]],
    *,
    sleeve_id: str,
    top_n: int,
) -> list[dict[str, Any]]:
    """Keep the top ``top_n`` names per ET date for one sleeve.

    Rank is signal score descending, then ticker ascending. A day with fewer
    names keeps every name. The same ticker on one day counts once, at its
    best score. Callers pass ``TOP_N_S0`` for the S0 scoreboard; this function
    does not read promotion gates.
    """
    wanted = str(sleeve_id).upper()
    limit = max(0, int(top_n))
    grouped: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        if not isinstance(record, dict):
            continue
        if _sleeve_id(record) != wanted:
            continue
        grouped.setdefault(_et_day(record), []).append(record)
    selected: list[dict[str, Any]] = []
    for day in sorted(grouped):
        rows = sorted(grouped[day], key=_rank_key)
        kept: list[dict[str, Any]] = []
        seen: set[str] = set()
        for row in rows:
            ticker = str(row.get("ticker") or "").upper()
            if ticker:
                if ticker in seen:
                    continue
                seen.add(ticker)
            kept.append(row)
            if len(kept) >= limit:
                break
        selected.extend(kept)
    return selected


def _percentile(sorted_vals: list[float], p: float) -> float:
    if len(sorted_vals) == 1:
        return float(sorted_vals[0])
    pos = (len(sorted_vals) - 1) * float(p)
    lo = int(pos)
    hi = min(lo + 1, len(sorted_vals) - 1)
    weight = pos - lo
    return float(sorted_vals[lo]) * (1.0 - weight) + float(sorted_vals[hi]) * weight


def _horizon_return(record: dict[str, Any], horizon: str) -> float | None:
    metrics = _outcome_at_horizon(record, horizon)
    if not metrics or metrics.get("return_pct") is None:
        return None
    try:
        value = float(metrics["return_pct"])
    except (TypeError, ValueError):
        return None
    if value != value or value in (float("inf"), float("-inf")):
        return None
    return value


def day_clustered_mean_ci(
    records: list[dict[str, Any]],
    *,
    sleeve_id: str,
    horizon: str = R7_PRIMARY_HORIZON,
    draws: int = R7_BOOTSTRAP_DRAWS,
    seed: int = R7_BOOTSTRAP_SEED,
) -> dict[str, Any]:
    """95% interval for the pooled horizon mean, resampling ET dates.

    Each draw samples days with replacement and keeps every return from the
    drawn days. A day drawn twice is included twice. One day, or no returns,
    does not invent a spread: a single cluster collapses onto the mean.
    """
    wanted = str(sleeve_id).upper()
    grouped: dict[str, list[float]] = {}
    for record in records:
        if not isinstance(record, dict) or _sleeve_id(record) != wanted:
            continue
        value = _horizon_return(record, horizon)
        if value is None:
            continue
        grouped.setdefault(_et_day(record), []).append(value)
    n = sum(len(values) for values in grouped.values())
    n_draws = max(1, int(draws))
    out: dict[str, Any] = {
        "method": "day_cluster_bootstrap",
        "level": 0.95,
        "seed": int(seed),
        "draws": n_draws,
        "n": n,
        "n_days": len(grouped),
        "mean": None,
        "low": None,
        "high": None,
    }
    if n == 0:
        return out
    days = sorted(grouped)
    flat = [value for day in days for value in grouped[day]]
    mean = sum(flat) / len(flat)
    rng = random.Random(int(seed))
    means: list[float] = []
    n_days = len(days)
    for _ in range(n_draws):
        sample: list[float] = []
        for _pick in range(n_days):
            sample.extend(grouped[days[rng.randrange(n_days)]])
        means.append(sum(sample) / len(sample))
    means.sort()
    out["mean"] = round(mean, 4)
    out["low"] = round(_percentile(means, 0.025), 4)
    out["high"] = round(_percentile(means, 0.975), 4)
    return out


def _outcome_at_horizon(record: dict[str, Any], horizon: str) -> dict[str, Any] | None:
    out = record.get("outcomes")
    if not isinstance(out, dict):
        return None
    # keys may be int or str
    for key in (horizon, int(horizon) if str(horizon).isdigit() else horizon, str(horizon)):
        m = out.get(key)
        if isinstance(m, dict):
            return m
    return None


def summarize_sleeve_local(
    records: list[dict[str, Any]],
    *,
    sleeve_id: str,
    horizon: str = R7_PRIMARY_HORIZON,
) -> dict[str, Any]:
    """Aggregate expectancy / hit rate for one sleeve at primary horizon."""
    hits = 0
    n = 0
    rets: list[float] = []
    for r in records:
        if not isinstance(r, dict):
            continue
        sid = _sleeve_id(r)
        if sid != str(sleeve_id).upper():
            continue
        metrics = _outcome_at_horizon(r, horizon)
        if not metrics:
            continue
        if "thesis_hit" not in metrics and "return_pct" not in metrics:
            continue
        n += 1
        if metrics.get("thesis_hit") is True:
            hits += 1
        rp = metrics.get("return_pct")
        if rp is not None:
            try:
                rets.append(float(rp))
            except (TypeError, ValueError):
                pass
    expectancy = (sum(rets) / len(rets)) if rets else None
    return {
        "sleeve_id": str(sleeve_id).upper(),
        "horizon": str(horizon),
        "n": n,
        "hit_rate": round(hits / n, 4) if n else None,
        "expectancy": round(expectancy, 6) if expectancy is not None else None,
        "mean_return_pct": round(expectancy, 4) if expectancy is not None else None,
    }


def r7_promotion_reasons(
    records: list[dict[str, Any]],
    *,
    sleeve_id: str,
    offline_floors_ok: bool,
    min_n: int = R7_MIN_N,
    horizon: str = R7_PRIMARY_HORIZON,
    one_change: bool = True,
) -> list[str]:
    """Return veto reasons; empty list means R7 ledger gates pass (offline still required)."""
    reasons: list[str] = []
    if not offline_floors_ok:
        reasons.append("offline_floors_failed")
    if not one_change:
        reasons.append("multiple_policy_deltas")
    summ = summarize_sleeve_local(records, sleeve_id=sleeve_id, horizon=horizon)
    n = int(summ.get("n") or 0)
    if n < int(min_n):
        # Not enough samples: do not veto on expectancy yet (insufficient evidence),
        # but do block promotion for insufficient N.
        reasons.append(f"insufficient_n:{n}<{min_n}")
        return reasons
    exp = summ.get("expectancy")
    if exp is None:
        reasons.append("missing_expectancy")
    elif float(exp) <= 0.0:
        reasons.append(f"expectancy_non_positive:{exp}")
    return reasons


def r7_may_promote(
    records: list[dict[str, Any]],
    *,
    sleeve_id: str,
    offline_floors_ok: bool,
    min_n: int = R7_MIN_N,
    horizon: str = R7_PRIMARY_HORIZON,
    one_change: bool = True,
) -> dict[str, Any]:
    reasons = r7_promotion_reasons(
        records,
        sleeve_id=sleeve_id,
        offline_floors_ok=offline_floors_ok,
        min_n=min_n,
        horizon=horizon,
        one_change=one_change,
    )
    return {
        "ok": len(reasons) == 0,
        "reasons": reasons,
        "summary": summarize_sleeve_local(records, sleeve_id=sleeve_id, horizon=horizon),
    }
