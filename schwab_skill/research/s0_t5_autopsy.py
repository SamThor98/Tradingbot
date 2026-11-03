"""Read-only S0 T+5 autopsy. Does not drop names or change a live flag.

The only pre-registered split is extension more than 12% above the 50-day
average, matching ``r2_anti_feature_flags``. Other cuts are descriptive.
A shadow counter is warranted only when both sides have at least 8 names and
the flagged side's mean T+5 return is at least 2 points worse.
"""

from __future__ import annotations

from typing import Any

MIN_SIDE_N = 8
MIN_MEAN_GAP = 2.0
EXTENSION_FLAG = 0.12
HORIZON = "5"


def _num(value: Any) -> float | None:
    try:
        if value is None:
            return None
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number:
        return None
    return number


def s0_t5_rows(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One row per S0 record that already has a T+5 return."""
    rows: list[dict[str, Any]] = []
    for record in records:
        if not isinstance(record, dict):
            continue
        if str(record.get("sleeve_id") or "S0").upper() != "S0":
            continue
        outcomes = record.get("outcomes") if isinstance(record.get("outcomes"), dict) else {}
        metrics = outcomes.get(HORIZON)
        if not isinstance(metrics, dict) or metrics.get("return_pct") is None:
            continue
        prediction = record.get("prediction") if isinstance(record.get("prediction"), dict) else {}
        levels = prediction.get("levels") if isinstance(prediction.get("levels"), dict) else {}
        raw = record.get("raw_context") if isinstance(record.get("raw_context"), dict) else {}
        advisory = prediction.get("advisory") if isinstance(prediction.get("advisory"), dict) else {}
        price = _num(prediction.get("entry_reference_px"))
        sma50 = _num(levels.get("sma_50"))
        sma200 = _num(levels.get("sma_200"))
        extension = (price - sma50) / sma50 if price is not None and sma50 not in (None, 0.0) else None
        extension_200 = (price - sma200) / sma200 if price is not None and sma200 not in (None, 0.0) else None
        day = str(record.get("et_date") or record.get("created_at") or "")[:10]
        rows.append(
            {
                "ticker": str(record.get("ticker") or "").upper(),
                "day": day,
                "return_pct": float(metrics["return_pct"]),
                "thesis_hit": bool(metrics.get("thesis_hit")),
                "signal_score": _num(raw.get("signal_score")),
                "mirofish_conviction": _num(raw.get("mirofish_conviction")),
                "advisory_p_up": _num(advisory.get("p_up_10d")),
                "extension_pct_sma50": extension,
                "extension_pct_sma200": extension_200,
                "extended_gt_12pct_sma50": bool(extension is not None and extension > EXTENSION_FLAG),
            }
        )
    return rows


def _arm(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {"n": 0, "hit_rate": None, "mean_return_pct": None}
    hits = sum(1 for row in rows if row.get("thesis_hit"))
    mean = sum(float(row["return_pct"]) for row in rows) / len(rows)
    return {
        "n": len(rows),
        "hit_rate": round(hits / len(rows), 4),
        "mean_return_pct": round(mean, 4),
    }


def _split(rows: list[dict[str, Any]], predicate: Any) -> dict[str, Any]:
    flagged = [row for row in rows if predicate(row)]
    other = [row for row in rows if not predicate(row)]
    flagged_arm = _arm(flagged)
    other_arm = _arm(other)
    flagged_mean = flagged_arm.get("mean_return_pct")
    other_mean = other_arm.get("mean_return_pct")
    gap = None
    if flagged_mean is not None and other_mean is not None:
        gap = round(float(other_mean) - float(flagged_mean), 4)
    warrant = (
        int(flagged_arm["n"]) >= MIN_SIDE_N
        and int(other_arm["n"]) >= MIN_SIDE_N
        and gap is not None
        and gap >= MIN_MEAN_GAP
    )
    return {
        "flagged": flagged_arm,
        "other": other_arm,
        "other_minus_flagged_mean": gap,
        "shadow_counter_warranted": warrant,
    }


def autopsy_report(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Summarize pre-entry splits. Exploratory cuts cannot authorize a counter."""
    rows = s0_t5_rows(records)
    registered = _split(rows, lambda row: bool(row.get("extended_gt_12pct_sma50")))
    exploratory = {
        "conviction_le_0": _split(
            [row for row in rows if row.get("mirofish_conviction") is not None],
            lambda row: float(row["mirofish_conviction"]) <= 0,
        ),
        "above_sma200": _split(
            [row for row in rows if row.get("extension_pct_sma200") is not None],
            lambda row: float(row["extension_pct_sma200"]) > 0,
        ),
    }
    for split in exploratory.values():
        split["shadow_counter_warranted"] = False
        split["descriptive_only"] = True
    by_day: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_day.setdefault(str(row.get("day") or ""), []).append(row)
    days = {day: _arm(group) for day, group in sorted(by_day.items())}
    return {
        "horizon": HORIZON,
        "n": len(rows),
        "all": _arm(rows),
        "pre_registered": {
            "name": "extended_gt_12pct_sma50",
            "rule": "extension_pct_sma50 > 0.12",
            "min_side_n": MIN_SIDE_N,
            "min_mean_gap": MIN_MEAN_GAP,
            **registered,
        },
        "decision": "shadow_counter" if registered["shadow_counter_warranted"] else "no_shadow_counter",
        "exploratory": exploratory,
        "by_day": days,
        "rows": rows,
    }
