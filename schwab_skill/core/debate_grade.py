"""Grade debate-committee calls against labeled decision-packet outcomes.

Join order: ``packet_id`` when the log has one, otherwise ticker plus UTC date.
Older rows without ``packet_id`` can still match a same-day packet. Rows that
match neither stay unmatched and are excluded from rates.

Approve calls are the committee's positive calls. Pass calls with a realized
return are the counterfactual: what the names they skipped would have done.
Conviction calibration uses approve calls only. Small samples stay
``insufficient_sample``. Nothing here promotes a prompt.
"""

from __future__ import annotations

from typing import Any

from core.tab_agent import SCRATCH_BAND_PCT, utc_day, wilson_ci

_BUCKETS: tuple[tuple[float, float], ...] = ((0.0, 40.0), (40.0, 70.0), (70.0, 100.01))


def _f(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _outcome(packet: dict[str, Any], *, horizon_days: int) -> tuple[str, float] | None:
    outcome = packet.get("outcome") if isinstance(packet.get("outcome"), dict) else {}
    label = str(outcome.get("label") or "pending").lower()
    if label in ("pending", "unknown", ""):
        return None
    horizon = outcome.get("horizon_days")
    if horizon is None or int(horizon) != int(horizon_days):
        return None
    ret = _f(outcome.get("realized_return_pct"))
    if ret is None:
        return None
    return label, ret


def _indexes(packets: list[dict[str, Any]]) -> tuple[dict[str, dict[str, Any]], dict[tuple[str, str], dict[str, Any]]]:
    by_id: dict[str, dict[str, Any]] = {}
    by_day: dict[tuple[str, str], dict[str, Any]] = {}
    for packet in packets:
        if not isinstance(packet, dict):
            continue
        packet_id = str(packet.get("packet_id") or "").strip()
        if packet_id and packet_id not in by_id:
            by_id[packet_id] = packet
        ticker = str(packet.get("ticker") or "").upper()
        day = utc_day(packet.get("created_at"))
        if ticker and day and (ticker, day) not in by_day:
            by_day[(ticker, day)] = packet
    return by_id, by_day


def _join(
    event: dict[str, Any],
    by_id: dict[str, dict[str, Any]],
    by_day: dict[tuple[str, str], dict[str, Any]],
) -> tuple[dict[str, Any] | None, str]:
    packet_id = str(event.get("packet_id") or "").strip()
    if packet_id and packet_id in by_id:
        return by_id[packet_id], "packet_id"
    ticker = str(event.get("ticker") or "").upper()
    day = str(event.get("date") or "") or (utc_day(event.get("ts")) or "")
    packet = by_day.get((ticker, day))
    if packet is not None:
        return packet, "ticker_day"
    embedded = _f(event.get("forward_return_pct"))
    if embedded is not None:
        label = "win" if embedded > SCRATCH_BAND_PCT else "loss" if embedded < -SCRATCH_BAND_PCT else "scratch"
        return {
            "outcome": {
                "label": label,
                "realized_return_pct": embedded,
                "horizon_days": event.get("horizon_days") or 10,
            },
            "ticker": ticker,
        }, "embedded_return"
    return None, "unmatched"


def _rate(labels: list[str], returns: list[float]) -> dict[str, Any]:
    n = len(labels)
    wins = sum(1 for label in labels if label == "win")
    return {
        "n": n,
        "wins": wins,
        "win_rate": round(wins / n, 4) if n else None,
        "win_rate_ci95": list(wilson_ci(wins, n) or []) or None,
        "avg_realized_return_pct": round(sum(returns) / len(returns), 4) if returns else None,
    }


def _calibration(convictions: list[float], labels: list[str], *, min_samples: int) -> dict[str, Any]:
    bins = []
    total = len(labels)
    ece = 0.0
    for lo, hi in _BUCKETS:
        idxs = [i for i, value in enumerate(convictions) if lo <= value < hi]
        n = len(idxs)
        if n == 0:
            continue
        wins = sum(1 for i in idxs if labels[i] == "win")
        mean_conv = sum(convictions[i] for i in idxs) / n
        empirical = wins / n
        ece += (n / total) * abs((mean_conv / 100.0) - empirical)
        bins.append(
            {
                "lo": lo,
                "hi": hi if hi <= 100 else 100.0,
                "n": n,
                "mean_conviction": round(mean_conv, 2),
                "predicted_win_rate": round(mean_conv / 100.0, 4),
                "win_rate": round(empirical, 4),
                "win_rate_ci95": list(wilson_ci(wins, n) or []) or None,
            }
        )
    sufficient = total >= min_samples
    return {
        "n": total,
        "sufficient": sufficient,
        "ece": round(ece, 4) if total else None,
        "bins": bins,
        "verdict": "readable" if sufficient else "insufficient_sample",
    }


def _ci_direction(left: list[float] | None, right: list[float] | None) -> str | None:
    if not left or not right or len(left) != 2 or len(right) != 2:
        return None
    if left[0] > right[1]:
        return "higher"
    if left[1] < right[0]:
        return "lower"
    return "overlap"


def grade_debate_log(
    *,
    versions: list[dict[str, Any]],
    packets: list[dict[str, Any]],
    events: list[dict[str, Any]] | None = None,
    min_samples: int = 30,
    horizon_days: int = 10,
) -> dict[str, Any]:
    """Score committee calls. ``auto_promote`` is always false."""
    events = events or []
    min_samples = max(1, int(min_samples))
    by_id, by_day = _indexes(packets)
    join_counts = {"packet_id": 0, "ticker_day": 0, "embedded_return": 0, "unmatched": 0, "unlabeled": 0}
    graded: list[dict[str, Any]] = []
    for event in events:
        if not isinstance(event, dict):
            continue
        decision = str(event.get("decision") or "").lower()
        if decision not in ("approve", "pass"):
            continue
        packet, how = _join(event, by_id, by_day)
        join_counts[how] = join_counts.get(how, 0) + 1
        if packet is None:
            continue
        parsed = _outcome(packet, horizon_days=horizon_days)
        if parsed is None:
            join_counts["unlabeled"] += 1
            continue
        graded.append(
            {
                "decision": decision,
                "conviction": _f(event.get("conviction")),
                "label": parsed[0],
                "return_pct": parsed[1],
                "prompt_versions": event.get("prompt_versions")
                if isinstance(event.get("prompt_versions"), dict)
                else {},
                "ticker": str(event.get("ticker") or packet.get("ticker") or "").upper(),
                "date": str(event.get("date") or ""),
            }
        )

    def _calls_for(version_id: str, prompt_key: str) -> list[dict[str, Any]]:
        rows = []
        seen: set[tuple[str, str, str]] = set()
        for call in graded:
            versions_used = call["prompt_versions"]
            if versions_used:
                if str(versions_used.get(prompt_key) or "") != version_id:
                    continue
            elif prompt_key != "chair":
                continue
            dedupe = (call["ticker"], call["date"], call["decision"])
            if dedupe in seen:
                continue
            seen.add(dedupe)
            rows.append(call)
        return rows

    version_stats: list[dict[str, Any]] = []
    for version in versions:
        vid = str(version.get("id") or "")
        key = str(version.get("prompt_key") or "")
        calls = _calls_for(vid, key) if vid else []
        approves = [c for c in calls if c["decision"] == "approve"]
        passes = [c for c in calls if c["decision"] == "pass"]
        approve_stats = _rate([c["label"] for c in approves], [c["return_pct"] for c in approves])
        pass_stats = _rate([c["label"] for c in passes], [c["return_pct"] for c in passes])
        correct_passes = sum(1 for c in passes if c["return_pct"] < -SCRATCH_BAND_PCT)
        version_stats.append(
            {
                "version_id": vid,
                "prompt_key": key,
                "status": version.get("status"),
                "parent": version.get("parent"),
                "n": approve_stats["n"],
                "wins": approve_stats["wins"],
                "sufficient": approve_stats["n"] >= min_samples,
                "win_rate": approve_stats["win_rate"],
                "win_rate_ci95": approve_stats["win_rate_ci95"],
                "avg_realized_return_pct": approve_stats["avg_realized_return_pct"],
                "horizon_days": int(horizon_days),
                "suppress_n": pass_stats["n"],
                "suppress_precision": round(correct_passes / pass_stats["n"], 4) if pass_stats["n"] else None,
                "suppress_precision_ci95": list(wilson_ci(correct_passes, pass_stats["n"]) or []) or None,
                "suppress_sufficient": pass_stats["n"] >= min_samples,
                "pass_counterfactual": {
                    **pass_stats,
                    "sufficient": pass_stats["n"] >= min_samples,
                    "verdict": "readable" if pass_stats["n"] >= min_samples else "insufficient_sample",
                    "correct_pass_rate": round(correct_passes / pass_stats["n"], 4) if pass_stats["n"] else None,
                },
            }
        )

    by_vid = {row["version_id"]: row for row in version_stats}
    comparisons = []
    for stat in version_stats:
        if stat.get("status") == "live":
            continue
        baseline = by_vid.get(str(stat.get("parent") or ""))
        if baseline is None:
            baseline = next(
                (
                    row
                    for row in version_stats
                    if row.get("prompt_key") == stat.get("prompt_key") and row.get("status") == "live"
                ),
                None,
            )
        edge = "insufficient_sample"
        if baseline is not None and stat["n"] >= min_samples and baseline["n"] >= min_samples:
            direction = _ci_direction(stat.get("win_rate_ci95"), baseline.get("win_rate_ci95"))
            cand_avg = stat.get("avg_realized_return_pct")
            base_avg = baseline.get("avg_realized_return_pct")
            if direction == "higher" and cand_avg is not None and base_avg is not None and cand_avg > base_avg:
                edge = "better_than_live"
            elif direction == "lower" and cand_avg is not None and base_avg is not None and cand_avg < base_avg:
                edge = "worse_than_live"
            else:
                edge = "no_separation"
        comparisons.append(
            {
                "version_id": stat["version_id"],
                "prompt_key": stat["prompt_key"],
                "baseline_version_id": None if baseline is None else baseline.get("version_id"),
                "edge_vs_live": edge,
                "auto_promote": False,
            }
        )

    approve_calls = [c for c in graded if c["decision"] == "approve" and c["conviction"] is not None]
    # Dedupe calibration on ticker+date so four prompt keys do not quadruple one call.
    seen_cal: set[tuple[str, str]] = set()
    cal_conv: list[float] = []
    cal_labels: list[str] = []
    for call in approve_calls:
        key = (call["ticker"], call["date"])
        if key in seen_cal:
            continue
        seen_cal.add(key)
        cal_conv.append(float(call["conviction"]))
        cal_labels.append(call["label"])
    passes = [c for c in graded if c["decision"] == "pass"]
    seen_pass: set[tuple[str, str]] = set()
    pass_labels: list[str] = []
    pass_returns: list[float] = []
    for call in passes:
        key = (call["ticker"], call["date"])
        if key in seen_pass:
            continue
        seen_pass.add(key)
        pass_labels.append(call["label"])
        pass_returns.append(call["return_pct"])
    pass_stats = _rate(pass_labels, pass_returns)
    correct_passes = sum(1 for value in pass_returns if value < -SCRATCH_BAND_PCT)

    return {
        "min_samples": min_samples,
        "horizon_days": int(horizon_days),
        "auto_promote": False,
        "winner_rule": (
            "Approve-call win rate uses labeled packet outcomes at the horizon. "
            "A version is better than live only when both approve samples meet the minimum, "
            "the 95% Wilson intervals do not overlap, and average realized return is higher. "
            "That flag is informational. Promotion is manual."
        ),
        "join": {
            **join_counts,
            "note": (
                "Rows with packet_id join on that id. Rows without it join on ticker and UTC date "
                "when a packet exists. New logs always store prompt version ids, ticker, date, and packet_id."
            ),
        },
        "calibration": _calibration(cal_conv, cal_labels, min_samples=min_samples),
        "passes": {
            **pass_stats,
            "sufficient": pass_stats["n"] >= min_samples,
            "verdict": "readable" if pass_stats["n"] >= min_samples else "insufficient_sample",
            "correct_pass_rate": round(correct_passes / pass_stats["n"], 4) if pass_stats["n"] else None,
            "note": "Counterfactual result of names the committee passed, where a realized return exists.",
        },
        "versions": version_stats,
        "comparisons": comparisons,
    }
