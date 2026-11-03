"""Settled-lessons snapshot for the Today card.

Reads self-study, the hypothesis ledger, the bare-signal profit-factor gate,
and the current strategy proposal. Operator decisions are appended to a
journal. This module never writes ``.env``, never edits ``strategy_update.json``,
and never places an order.
"""

from __future__ import annotations

import hashlib
import json
import math
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.multi_sleeve_constitution import R7_MIN_N, R7_PRIMARY_HORIZON
from core.pead_canary_splits import summarize_s1_splits
from core.r7_promotion import summarize_sleeve_local
from self_study import (
    CONVICTION_BANDS,
    MIN_BAND_SAMPLES_FOR_THRESHOLD,
    MIN_ROUND_TRIPS_FOR_LEARNING,
    _band_shrunk_avg,
    is_demo_calibration_seed,
    suggest_min_conviction_from_bands,
)

PF_MEAN_MIN = 1.20
WORST_ERA_PF_MIN = 1.00
BASE_RATE = 0.50
PEAD_SLEEVE_ID = "S1"
_ACTIONS = ("keep", "sharpen", "revert")
_ACTION_SUMMARY = {
    "keep": "Kept — no change. Settings were not touched.",
    "sharpen": "Sharpened — left in shadow. Settings were not touched.",
    "revert": "Reverted — proposal discarded. Settings were not touched.",
}

_LOCK = threading.Lock()
_DECISIONS_NAME = "settled_lesson_decisions.jsonl"
_STATE_NAME = "settled_lesson_state.json"


class SettledLessonRejected(Exception):
    """Operator action refused. ``snapshot`` is the current card payload."""

    def __init__(self, message: str, snapshot: dict[str, Any]) -> None:
        super().__init__(message)
        self.snapshot = snapshot


def _read_json(path: Path) -> Any | None:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _artifact_dir(skill_dir: Path) -> Path:
    return skill_dir / "validation_artifacts"


def _state_path(skill_dir: Path) -> Path:
    return _artifact_dir(skill_dir) / _STATE_NAME


def _decisions_path(skill_dir: Path) -> Path:
    return _artifact_dir(skill_dir) / _DECISIONS_NAME


def _load_state(skill_dir: Path) -> dict[str, Any]:
    raw = _read_json(_state_path(skill_dir))
    if isinstance(raw, dict) and isinstance(raw.get("decisions"), dict):
        return raw
    return {"decisions": {}}


def _ledger_records(skill_dir: Path) -> list[dict[str, Any]]:
    raw = _read_json(skill_dir / ".hypothesis_ledger.json")
    if isinstance(raw, dict) and isinstance(raw.get("records"), list):
        return [r for r in raw["records"] if isinstance(r, dict)]
    if isinstance(raw, list):
        return [r for r in raw if isinstance(r, dict)]
    return []


def _t5_book(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Hit rate at the primary horizon for advisory and scanner forecasts."""
    hits = 0
    n = 0
    horizon = str(R7_PRIMARY_HORIZON)
    for record in records:
        source = str(record.get("source") or "")
        if source not in ("advisory", "signal_scanner"):
            continue
        metrics = None
        outcomes = record.get("outcomes")
        if isinstance(outcomes, dict):
            for key in (horizon, int(horizon) if horizon.isdigit() else horizon, str(horizon)):
                candidate = outcomes.get(key)
                if isinstance(candidate, dict):
                    metrics = candidate
                    break
        if not isinstance(metrics, dict):
            continue
        if "thesis_hit" not in metrics and "return_pct" not in metrics:
            continue
        n += 1
        if metrics.get("thesis_hit") is True:
            hits += 1
    hit_rate = round(hits / n, 4) if n else None
    beats = None if hit_rate is None else bool(hit_rate > BASE_RATE)
    return {
        "window": f"T+{horizon}",
        "sample_size": n,
        "hit_rate": hit_rate,
        "base_rate": BASE_RATE,
        "beats_base_rate": beats,
        "source": "advisory" if n else "none",
    }


def _signal_gate(skill_dir: Path) -> dict[str, Any]:
    raw = _read_json(skill_dir / "phase1_progress_signal_gate.json")
    ranking = raw.get("ranking") if isinstance(raw, dict) else None
    row = None
    if isinstance(ranking, list):
        for item in ranking:
            if isinstance(item, dict) and item.get("config_id") == "control_legacy":
                row = item
                break
        if row is None:
            for item in ranking:
                if isinstance(item, dict):
                    row = item
                    break
    pf_mean = _finite(row.get("pf_mean_treatment") if isinstance(row, dict) else None)
    worst = _finite(row.get("worst_era_pf_treatment") if isinstance(row, dict) else None)
    passes = pf_mean is not None and worst is not None and pf_mean >= PF_MEAN_MIN and worst >= WORST_ERA_PF_MIN
    return {
        "source": (row or {}).get("config_id") if isinstance(row, dict) else None,
        "pf_mean": pf_mean,
        "worst_era_pf": worst,
        "pf_mean_min": PF_MEAN_MIN,
        "worst_era_pf_min": WORST_ERA_PF_MIN,
        "passes": passes,
        "on_file": isinstance(row, dict),
    }


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number


def _pocket(study: dict[str, Any] | None, *, evidence_ready: bool, gate_passes: bool) -> dict[str, Any]:
    study = study if isinstance(study, dict) else {}
    by_conviction = study.get("by_conviction") if isinstance(study.get("by_conviction"), dict) else {}
    global_avg = float(study.get("avg_return_pct") or 0.0)
    suggested = suggest_min_conviction_from_bands(by_conviction, global_avg) if by_conviction else None

    chosen_name = None
    chosen_band: dict[str, Any] | None = None
    if suggested is not None:
        for lo, hi in CONVICTION_BANDS:
            if lo == suggested and isinstance(by_conviction.get(f"{lo}-{hi}"), dict):
                chosen_name = f"{lo}-{hi}"
                chosen_band = by_conviction[chosen_name]
                break
    if chosen_band is None and by_conviction:
        best_count = -1
        for name, band in by_conviction.items():
            if not isinstance(band, dict):
                continue
            count = int(band.get("count") or 0)
            if count > best_count:
                best_count = count
                chosen_name = str(name)
                chosen_band = band

    kind = "conviction"
    samples = 0
    expectancy = None
    if isinstance(chosen_band, dict) and chosen_name:
        samples, shrunk = _band_shrunk_avg(chosen_band, global_avg)
        expectancy = round(shrunk, 2)
    else:
        by_sector = study.get("by_sector") if isinstance(study.get("by_sector"), dict) else {}
        best_sector = None
        best_count = -1
        for name, band in by_sector.items():
            if not isinstance(band, dict):
                continue
            count = int(band.get("count") or 0)
            if count > best_count:
                best_count = count
                best_sector = (str(name), band)
        if best_sector is not None:
            kind = "sector"
            chosen_name, chosen_band = best_sector
            samples = int(chosen_band.get("count") or 0)
            expectancy = _finite(chosen_band.get("avg_return_pct"))
            if expectancy is not None:
                expectancy = round(expectancy, 2)

    if not evidence_ready:
        label = "Paper"
    elif not gate_passes:
        label = "Blocked"
    else:
        label = "Shadow"

    if not chosen_name:
        detail = "No conviction band or sector has settled samples yet."
    else:
        exp_txt = f"{expectancy:.2f}%" if expectancy is not None else "—"
        detail = (
            f"{kind} {chosen_name}: {samples} samples, shrinkage-adjusted expectancy {exp_txt}."
        )
        if samples < MIN_BAND_SAMPLES_FOR_THRESHOLD and kind == "conviction":
            detail += f" Needs {MIN_BAND_SAMPLES_FOR_THRESHOLD} samples in a band before a floor is real."
    if label == "Blocked":
        detail += " Base-signal profit-factor gates are still closed, so this pocket cannot go live."
    elif label == "Shadow":
        detail += " Evidence is in shadow only. This card does not promote anything to live."
    elif label == "Paper":
        detail += " Still paper until the sample floor is met."

    return {
        "kind": kind if chosen_name else "none",
        "name": chosen_name,
        "samples": samples,
        "expectancy_pct": expectancy,
        "min_band_samples": MIN_BAND_SAMPLES_FOR_THRESHOLD,
        "label": label,
        "detail": detail,
    }


def _proposal_id(identity: dict[str, Any]) -> str:
    blob = json.dumps(identity, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def _proposal_from_strategy_update(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    updates = raw.get("updates") if isinstance(raw.get("updates"), list) else []
    first = next((u for u in updates if isinstance(u, dict)), None)
    if first is not None:
        target = str(first.get("env_key") or first.get("feature") or "threshold")
        summary = str(first.get("rationale") or "").strip()
        if not summary:
            summary = (
                f"Adjust {target} from {first.get('current_value')} to {first.get('suggested_value')}."
            )
        identity = {
            "source": "strategy_update",
            "target": target,
            "current": first.get("current_value"),
            "suggested": first.get("suggested_value"),
            "generated_at": raw.get("generated_at"),
        }
        return {
            "id": _proposal_id(identity),
            "source": "strategy_update",
            "target": target,
            "summary": summary,
        }
    overrides = raw.get("env_overrides") if isinstance(raw.get("env_overrides"), dict) else {}
    if not overrides:
        return None
    target, value = next(iter(overrides.items()))
    identity = {
        "source": "strategy_update",
        "target": str(target),
        "suggested": value,
        "generated_at": raw.get("generated_at"),
    }
    return {
        "id": _proposal_id(identity),
        "source": "strategy_update",
        "target": str(target),
        "summary": f"Proposed {target}={value}. Review only — settings stay unchanged.",
    }


def _proposal_from_review(skill_dir: Path) -> dict[str, Any] | None:
    store = skill_dir / "decision_packets.json"
    if not store.is_file():
        return None
    try:
        from core import decision_packet, trade_review, weight_feedback

        packets = decision_packet.load_packets(skill_dir)
        report = trade_review.weekly_report(packets)
        tuning = weight_feedback.propose(report)
    except Exception:
        return None
    proposals = tuning.get("proposals") if isinstance(tuning, dict) else None
    first = next((p for p in proposals or [] if isinstance(p, dict)), None)
    if first is None:
        return None
    target = str(first.get("target") or "review")
    summary = (
        f"{first.get('direction') or 'review'} {target}"
        f" ({first.get('scope') or 'book'}). {first.get('evidence') or ''}"
    ).strip()
    identity = {
        "source": "review_loop",
        "target": target,
        "direction": first.get("direction"),
        "scope": first.get("scope"),
        "evidence": first.get("evidence"),
    }
    return {
        "id": _proposal_id(identity),
        "source": "review_loop",
        "target": target,
        "summary": summary,
    }


def _latest_decision(skill_dir: Path, proposal_id: str | None) -> dict[str, Any] | None:
    if not proposal_id:
        return None
    decisions = _load_state(skill_dir).get("decisions") or {}
    row = decisions.get(proposal_id)
    if not isinstance(row, dict):
        return None
    action = str(row.get("action") or "")
    if action not in _ACTIONS:
        return None
    return {
        "action": action,
        "recorded_at": row.get("recorded_at"),
        "summary": row.get("summary") or _ACTION_SUMMARY[action],
    }


def build_settled_lessons(skill_dir: Path) -> dict[str, Any]:
    """Card payload. Missing files yield an empty, still-paper snapshot."""
    skill_dir = Path(skill_dir)
    study_raw = _read_json(skill_dir / ".self_study.json")
    study = study_raw if isinstance(study_raw, dict) else {}
    demo = bool(study) and is_demo_calibration_seed(study)
    try:
        round_trips = int(study.get("round_trips_count") or 0)
    except (TypeError, ValueError):
        round_trips = 0
    win_rate = _finite(study.get("win_rate"))
    evidence_ready = (not demo) and round_trips >= MIN_ROUND_TRIPS_FOR_LEARNING

    records = _ledger_records(skill_dir)
    probability = _t5_book(records)
    if probability["hit_rate"] is None and win_rate is not None and evidence_ready:
        hit = round(win_rate / 100.0, 4)
        probability = {
            "window": "round trips",
            "sample_size": round_trips,
            "hit_rate": hit,
            "base_rate": BASE_RATE,
            "beats_base_rate": bool(hit > BASE_RATE),
            "source": "self_study_win_rate",
        }

    pead = summarize_sleeve_local(records, sleeve_id=PEAD_SLEEVE_ID, horizon=str(R7_PRIMARY_HORIZON))
    pead_n = int(pead.get("n") or 0)
    pead_status = "scored" if pead_n >= int(R7_MIN_N) else "still_paper"

    gate = _signal_gate(skill_dir)
    pocket = _pocket(study, evidence_ready=evidence_ready, gate_passes=bool(gate["passes"]))

    if demo:
        headline = "Still paper — calibration demo seed is not evidence."
        status = "still_paper"
    elif not evidence_ready:
        headline = (
            f"Still paper — {round_trips} of {MIN_ROUND_TRIPS_FOR_LEARNING} round trips."
        )
        status = "still_paper"
    else:
        status = "scored"
        win_txt = f"{win_rate:.1f}%" if win_rate is not None else "—"
        if probability["source"] == "advisory" and probability["hit_rate"] is not None:
            beat = "beats" if probability["beats_base_rate"] else "does not beat"
            headline = (
                f"{round_trips} round trips, win rate {win_txt}. "
                f"{probability['window']} hit rate {probability['hit_rate'] * 100:.1f}% "
                f"{beat} the {int(BASE_RATE * 100)}% base rate "
                f"({probability['sample_size']} scored)."
            )
        elif probability["source"] == "self_study_win_rate" and probability["hit_rate"] is not None:
            beat = "beats" if probability["beats_base_rate"] else "does not beat"
            headline = (
                f"{round_trips} round trips, win rate {win_txt}. "
                f"That win rate {beat} the {int(BASE_RATE * 100)}% base rate. "
                "No advisory T+5 scores yet."
            )
        else:
            headline = f"{round_trips} round trips, win rate {win_txt}. No probability scores yet."

    if pead_status == "still_paper":
        headline += f" PEAD canary T+{R7_PRIMARY_HORIZON} is {pead_n} of {int(R7_MIN_N)}."

    proposal = _proposal_from_strategy_update(_read_json(skill_dir / "strategy_update.json"))
    if proposal is None:
        proposal = _proposal_from_review(skill_dir)
    decision = _latest_decision(skill_dir, proposal.get("id") if proposal else None)
    reverted = bool(decision and decision.get("action") == "revert")

    if not evidence_ready:
        stop_reason = "still_paper"
    elif proposal is None:
        stop_reason = "no_proposal"
    elif reverted:
        stop_reason = "reverted"
    else:
        stop_reason = None

    return {
        "settled": {
            "status": status,
            "headline": headline,
            "round_trips": round_trips,
            "min_round_trips": MIN_ROUND_TRIPS_FOR_LEARNING,
            "win_rate_pct": win_rate,
            "demo_seed": demo,
            "probability": probability,
        },
        "pead_canary": {
            "sleeve_id": PEAD_SLEEVE_ID,
            "horizon": str(R7_PRIMARY_HORIZON),
            "n": pead_n,
            "min_n": int(R7_MIN_N),
            "hit_rate": pead.get("hit_rate"),
            "expectancy": pead.get("expectancy"),
            "status": pead_status,
            "splits": summarize_s1_splits(
                records,
                sleeve_id=PEAD_SLEEVE_ID,
                horizon=str(R7_PRIMARY_HORIZON),
                skill_dir=skill_dir,
            ),
        },
        "pocket": pocket,
        "signal_gate": gate,
        "proposal": proposal,
        "decision": decision,
        "actions_enabled": stop_reason is None,
        "stop_reason": stop_reason,
        "protected": True,
        "note": "Decisions are recorded only. Settings, scanner code, and orders stay unchanged.",
    }


def record_settled_lesson_decision(skill_dir: Path, action: str) -> dict[str, Any]:
    """Append one operator decision. Refuses when evidence or a proposal is missing."""
    skill_dir = Path(skill_dir)
    action = str(action or "").strip().lower()
    if action not in _ACTIONS:
        snapshot = build_settled_lessons(skill_dir)
        raise SettledLessonRejected("Action must be keep, sharpen, or revert.", snapshot)

    with _LOCK:
        snapshot = build_settled_lessons(skill_dir)
        if not snapshot.get("actions_enabled"):
            reason = snapshot.get("stop_reason") or "unavailable"
            messages = {
                "still_paper": "Still paper — not enough settled evidence to adjudicate.",
                "no_proposal": "No proposal is waiting.",
                "reverted": "This proposal was already reverted.",
            }
            raise SettledLessonRejected(messages.get(str(reason), "Decision is not available."), snapshot)

        proposal = snapshot.get("proposal") or {}
        proposal_id = str(proposal.get("id") or "")
        existing = snapshot.get("decision") or {}
        if existing.get("action") == action:
            return snapshot

        recorded_at = _utc_now()
        summary = _ACTION_SUMMARY[action]
        entry = {
            "recorded_at": recorded_at,
            "action": action,
            "proposal_id": proposal_id,
            "proposal_source": proposal.get("source"),
            "proposal_target": proposal.get("target"),
            "summary": summary,
            "applied_to_env": False,
            "order_placed": False,
        }
        art = _artifact_dir(skill_dir)
        art.mkdir(parents=True, exist_ok=True)
        with _decisions_path(skill_dir).open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry) + "\n")

        state = _load_state(skill_dir)
        decisions = state.setdefault("decisions", {})
        decisions[proposal_id] = {
            "action": action,
            "recorded_at": recorded_at,
            "summary": summary,
        }
        _state_path(skill_dir).write_text(json.dumps(state, indent=2), encoding="utf-8")

    return build_settled_lessons(skill_dir)
