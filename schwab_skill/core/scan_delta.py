"""Phase 2 stateful scan intelligence: "what changed since last cycle".

Pure functions that diff two scan signal lists (previous vs current) into a
``ScanDelta`` and derive the three adaptive watchlists:
- **breaking out now** — breakout just confirmed this cycle
- **setup improving** — rank_score rose meaningfully vs last cycle
- **risk rising** — SEC / forensic / event risk worsened

No I/O — callers pass the persisted ``last_scan`` payloads. Fully offline-testable.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

# SEC risk ordering for "risk rising" detection.
_SEC_RISK_RANK = {"low": 0, "none": 0, "": 0, "medium": 1, "med": 1, "high": 2, "severe": 3}


def _f(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _ticker(sig: dict[str, Any]) -> str:
    return str((sig or {}).get("ticker", "")).upper()


def _index(signals: list[dict[str, Any]] | None) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for s in signals or []:
        if isinstance(s, dict):
            t = _ticker(s)
            if t:
                out[t] = s
    return out


def _rank(sig: dict[str, Any]) -> float | None:
    return _f(sig.get("rank_score")) or _f(sig.get("composite_score")) or _f(sig.get("signal_score"))


def _sec_rank(sig: dict[str, Any]) -> int:
    tag = str(sig.get("sec_risk_tag") or "").strip().lower()
    return _SEC_RISK_RANK.get(tag, 0)


def _event_flagged(sig: dict[str, Any]) -> bool:
    er = sig.get("event_risk")
    return bool(isinstance(er, dict) and er.get("flagged"))


def _improve_min(skill_dir: Path | None) -> float:
    try:
        from config import get_scan_delta_improve_min

        return get_scan_delta_improve_min(skill_dir)
    except Exception:
        return 5.0


def compute_delta(
    prev_signals: list[dict[str, Any]] | None,
    curr_signals: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    """Diff previous vs current scan signal lists."""
    prev = _index(prev_signals)
    curr = _index(curr_signals)
    prev_keys = set(prev)
    curr_keys = set(curr)

    new_tickers = sorted(curr_keys - prev_keys)
    dropped_tickers = sorted(prev_keys - curr_keys)

    rank_moves: list[dict[str, Any]] = []
    gate_flips: list[dict[str, Any]] = []
    for t in sorted(curr_keys & prev_keys):
        pr, cr = _rank(prev[t]), _rank(curr[t])
        if pr is not None and cr is not None:
            delta = round(cr - pr, 2)
            if abs(delta) >= 0.01:
                rank_moves.append({"ticker": t, "prev": round(pr, 2), "curr": round(cr, 2), "delta": delta})
        prev_status = str(prev[t].get("_filter_status") or "")
        curr_status = str(curr[t].get("_filter_status") or "")
        if prev_status and curr_status and prev_status != curr_status:
            gate_flips.append({"ticker": t, "from": prev_status, "to": curr_status})

    rank_moves.sort(key=lambda r: abs(r["delta"]), reverse=True)

    return {
        "new_tickers": new_tickers,
        "dropped_tickers": dropped_tickers,
        "rank_moves": rank_moves,
        "gate_flips": gate_flips,
        "counts": {
            "new": len(new_tickers),
            "dropped": len(dropped_tickers),
            "rank_moves": len(rank_moves),
            "gate_flips": len(gate_flips),
        },
        "has_prior": bool(prev_keys),
    }


def adaptive_watchlists(
    prev_signals: list[dict[str, Any]] | None,
    curr_signals: list[dict[str, Any]] | None,
    *,
    skill_dir: Path | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Derive the three adaptive watchlists from prev vs current scans."""
    prev = _index(prev_signals)
    curr = _index(curr_signals)
    improve_min = _improve_min(skill_dir)

    breaking_out: list[dict[str, Any]] = []
    improving: list[dict[str, Any]] = []
    risk_rising: list[dict[str, Any]] = []

    for t, sig in curr.items():
        before = prev.get(t)

        # breaking out now: confirmed this cycle (newly true, or new entry that is confirmed)
        if bool(sig.get("breakout_confirmed")):
            was = bool(before.get("breakout_confirmed")) if before else False
            if not was:
                breaking_out.append({"ticker": t, "rank_score": _rank(sig), "new": before is None})

        # setup improving: rank rose by >= threshold
        if before is not None:
            pr, cr = _rank(before), _rank(sig)
            if pr is not None and cr is not None and (cr - pr) >= improve_min:
                improving.append({"ticker": t, "prev": round(pr, 2), "curr": round(cr, 2), "delta": round(cr - pr, 2)})

        # risk rising: SEC risk worsened, new forensic flags, or event risk newly flagged
        reasons: list[str] = []
        if before is not None:
            if _sec_rank(sig) > _sec_rank(before):
                reasons.append(f"sec_risk:{before.get('sec_risk_tag')}→{sig.get('sec_risk_tag')}")
            new_flags = set(map(str, sig.get("forensic_flags") or [])) - set(
                map(str, before.get("forensic_flags") or [])
            )
            if new_flags:
                reasons.append("forensic:" + ",".join(sorted(new_flags))[:80])
            if _event_flagged(sig) and not _event_flagged(before):
                reasons.append("event_risk_newly_flagged")
        if reasons:
            risk_rising.append({"ticker": t, "reasons": reasons})

    breaking_out.sort(key=lambda r: r.get("rank_score") or -1e9, reverse=True)
    improving.sort(key=lambda r: r["delta"], reverse=True)

    return {
        "breaking_out_now": breaking_out,
        "setup_improving": improving,
        "risk_rising": risk_rising,
    }
