"""R7 promotion process contract: sleeve-local ledger gates."""

from __future__ import annotations

from typing import Any

from core.multi_sleeve_constitution import R7_MIN_N, R7_PRIMARY_HORIZON, SLEEVE_S0


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
        sid = str(r.get("sleeve_id") or SLEEVE_S0).upper()
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
