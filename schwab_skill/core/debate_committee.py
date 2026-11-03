"""Sampled shadow committee. Writes ``debate_score`` beside ``qual_raw``.

The committee never runs inside the backtest and never changes ``qual_raw``
or ``signal_score``. Letter caps may move ``qual_score`` after real votes.
"""

from __future__ import annotations

import json
import logging
import math
import statistics
import zlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

LOG = logging.getLogger(__name__)
COMMITTEE_LOG_FILE = ".debate_committee_log.jsonl"

SEAT_NAMES = ("trend", "setup", "risk", "skeptic", "business")
SCORE_MOVE_CAP = 15.0
SAMPLE_BUCKETS = 100
SAMPLE_KEEP_BELOW = 40

_SYSTEM = (
    "You score a checklist. Use only the numbers in the user payload. "
    "Do not invent prices, filings, or returns. "
    "Reply with JSON only: "
    '{"seats":[{"name":"trend","score":0,"claim":"..."}],"chair":"..."}. '
    "Seat names are trend, setup, risk, skeptic, business. Scores are 0-100. "
    "If qual_business is null, the business seat score must be null. "
    "Long-only equities: never recommend a short, an inverse ETF, or an option. "
    "A low score means no trade and stay in cash. "
    "Do not penalize market cap. Weight a clean chart, fundamentals, sector strength, "
    "and a tight stop above any earnings or news catalyst."
)
_ROUND1 = "First round. Score each seat from the checklist numbers."
_ROUND2 = "Second round. Move each score by at most 15 points from round1."

CompleteFn = Callable[[str], str]


def _in_sample(ticker: str) -> bool:
    name = str(ticker or "").strip().upper()
    if not name:
        return False
    return (zlib.crc32(name.encode("utf-8")) % SAMPLE_BUCKETS) < SAMPLE_KEEP_BELOW


def _parse_round(text: str) -> dict[str, Any] | None:
    raw = str(text or "")
    start = raw.find("{")
    end = raw.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        payload = json.loads(raw[start : end + 1])
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    seats_in = payload.get("seats")
    if not isinstance(seats_in, list):
        return None
    by_name: dict[str, dict[str, Any]] = {}
    for row in seats_in:
        if not isinstance(row, dict):
            continue
        name = str(row.get("name") or "").strip().lower()
        if name not in SEAT_NAMES or name in by_name:
            continue
        claim = str(row.get("claim") or "")[:180]
        if row.get("score") is None:
            by_name[name] = {"name": name, "score": None, "claim": claim}
            continue
        try:
            num = float(row.get("score"))
        except (TypeError, ValueError):
            continue
        if not math.isfinite(num):
            continue
        by_name[name] = {"name": name, "score": max(0.0, min(100.0, num)), "claim": claim}
    scored = [seat for seat in by_name.values() if seat.get("score") is not None]
    if len(scored) < 2:
        return None
    return {"seats": by_name, "chair": str(payload.get("chair") or "")[:400]}


def _cap_moves(first: dict[str, Any], second: dict[str, Any]) -> dict[str, Any]:
    merged: dict[str, dict[str, Any]] = {}
    first_seats = first.get("seats") if isinstance(first.get("seats"), dict) else {}
    second_seats = second.get("seats") if isinstance(second.get("seats"), dict) else {}
    for name, seat in second_seats.items():
        score = seat.get("score")
        base = (first_seats.get(name) or {}).get("score")
        if score is not None and base is not None:
            score = max(float(base) - SCORE_MOVE_CAP, min(float(base) + SCORE_MOVE_CAP, float(score)))
        merged[name] = {"name": name, "score": score, "claim": seat.get("claim") or ""}
    for name, seat in first_seats.items():
        merged.setdefault(name, seat)
    chair = str(second.get("chair") or first.get("chair") or "")
    return {"seats": merged, "chair": chair}


def _force_business_abstain(parsed: dict[str, Any], grade: dict[str, Any]) -> None:
    if grade.get("qual_business") is not None:
        return
    seats = parsed.get("seats")
    if isinstance(seats, dict) and "business" in seats:
        seats["business"]["score"] = None
        if not seats["business"].get("claim"):
            seats["business"]["claim"] = "Abstained."


def prompt_catalog() -> dict[str, str]:
    """Built-in analysis prompts. The tab agent versions these and nothing else."""
    return {"system": _SYSTEM, "round1": _ROUND1, "round2": _ROUND2}


def _headers(skill_dir: Path | None) -> tuple[str, str, str]:
    """Live system and round headers. Built-ins unless the debate agent is enabled."""
    if skill_dir is None:
        return _SYSTEM, _ROUND1, _ROUND2
    try:
        from config import get_tab_agent_debate_enabled

        if not get_tab_agent_debate_enabled(skill_dir):
            return _SYSTEM, _ROUND1, _ROUND2
        from core.debate_tab_agent import live_prompt_text

        system = live_prompt_text(skill_dir, "system") or _SYSTEM
        round1 = live_prompt_text(skill_dir, "round1") or _ROUND1
        round2 = live_prompt_text(skill_dir, "round2") or _ROUND2
        return system, round1, round2
    except Exception as exc:
        LOG.debug("Debate prompt registry unavailable: %s", exc)
        return _SYSTEM, _ROUND1, _ROUND2


def _prompt(
    signal: dict[str, Any],
    grade: dict[str, Any],
    prior: dict[str, Any] | None,
    skill_dir: Path | None = None,
    *,
    headers: tuple[str, str, str] | None = None,
) -> str:
    payload = {
        "ticker": signal.get("ticker"),
        "qual_trend": grade.get("qual_trend"),
        "qual_setup": grade.get("qual_setup"),
        "qual_asymmetry": grade.get("qual_asymmetry"),
        "qual_crowding": grade.get("qual_crowding"),
        "qual_business": grade.get("qual_business"),
        "price": signal.get("price"),
        "sma_50": signal.get("sma_50"),
        "stop_pct": signal.get("stop_pct"),
        "checklist": grade.get("seats"),
    }
    _system, round1, round2 = headers or _headers(skill_dir)
    if prior is None:
        header = round1
    else:
        header = round2
        payload["round1"] = {
            "chair": prior.get("chair"),
            "seats": [
                {"name": name, "score": seat.get("score"), "claim": seat.get("claim")}
                for name, seat in (prior.get("seats") or {}).items()
            ],
        }
    return header + "\n" + json.dumps(payload, default=str)


def _default_complete(prompt: str, skill_dir: Path | None = None) -> str:
    from core.llm_endpoint import resolve_llm_endpoint

    endpoint = resolve_llm_endpoint()
    if not endpoint.api_key:
        return ""
    system, _round1, _round2 = _headers(skill_dir)
    try:
        from openai import OpenAI

        client = OpenAI(
            api_key=endpoint.api_key,
            base_url=endpoint.base_url or None,
            timeout=12.0,
            max_retries=0,
        )
        resp = client.chat.completions.create(
            model=endpoint.model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            max_tokens=400,
            temperature=0,
        )
        return (resp.choices[0].message.content or "").strip()
    except Exception as exc:
        LOG.warning("Debate committee call failed: %s", exc)
        return ""


def _endpoint_ready() -> bool:
    from core.llm_endpoint import resolve_llm_endpoint

    endpoint = resolve_llm_endpoint()
    return bool(endpoint.api_key)


def maybe_attach_committee(
    signal: dict[str, Any],
    diagnostics: dict[str, Any] | None = None,
    skill_dir: Path | None = None,
    *,
    complete: CompleteFn | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """One JSON round and one revision on a stable sample of shadow scans."""
    from agent_intelligence import compute_vote_disagreement
    from config import (
        get_debate_committee_max_per_scan,
        get_debate_committee_mode,
        get_tab_agent_debate_enabled,
        get_tab_agent_debate_max_names,
    )
    from core.debate_grade import refresh_display_caps

    diag = diagnostics if isinstance(diagnostics, dict) else {}
    mode = get_debate_committee_mode(skill_dir)
    if mode == "live":
        diag["debate_committee_live_coerced"] = int(diag.get("debate_committee_live_coerced", 0) or 0) + 1
        mode = "shadow"
    if mode != "shadow":
        return signal
    grade = signal.get("debate_grade")
    if not isinstance(grade, dict) or grade.get("qual_abstain") or grade.get("qual_raw") is None:
        return signal
    if complete is None and not _endpoint_ready():
        diag["debate_committee_skipped_no_key"] = int(diag.get("debate_committee_skipped_no_key", 0) or 0) + 1
        return signal
    ran = int(diag.get("debate_committee_ran", 0) or 0)
    ticker = str(signal.get("ticker") or "")
    try:
        agent_on = get_tab_agent_debate_enabled(skill_dir)
    except Exception:
        agent_on = False
    if agent_on:
        from core.tab_agent_objective import clears_quality_bar, load_objective

        gate = load_objective(skill_dir)
        try:
            qual = float(grade.get("qual_raw"))
        except (TypeError, ValueError):
            qual = None
        if not clears_quality_bar(qual, config=gate):
            diag["debate_committee_below_quality"] = int(diag.get("debate_committee_below_quality", 0) or 0) + 1
            return signal
        cap = get_tab_agent_debate_max_names(skill_dir)
        if cap > 0 and ran >= cap:
            return signal
    else:
        max_n = get_debate_committee_max_per_scan(skill_dir)
        if ran >= max_n:
            return signal
        if not force and not _in_sample(ticker):
            return signal

    caller = complete or (lambda text: _default_complete(text, skill_dir))
    raw_before = grade.get("qual_raw")
    score_before = signal.get("signal_score")
    live_headers = _headers(skill_dir)
    first = _parse_round(caller(_prompt(signal, grade, None, skill_dir, headers=live_headers)))
    if first is None:
        diag["debate_committee_parse_failed"] = int(diag.get("debate_committee_parse_failed", 0) or 0) + 1
        return signal
    _force_business_abstain(first, grade)
    second = _parse_round(caller(_prompt(signal, grade, first, skill_dir, headers=live_headers)))
    final = _cap_moves(first, second) if second is not None else first
    _force_business_abstain(final, grade)

    scored = [seat for seat in final["seats"].values() if seat.get("score") is not None]
    if len(scored) < 2:
        diag["debate_committee_parse_failed"] = int(diag.get("debate_committee_parse_failed", 0) or 0) + 1
        return signal
    votes = [
        {
            "seat": seat["name"],
            "score": (float(seat["score"]) - 50.0) * 2.0,
            "continuation_probability": float(seat["score"]) / 100.0,
        }
        for seat in scored
    ]
    grade["debate_score"] = round(float(statistics.median(float(seat["score"]) for seat in scored)), 6)
    grade["qual_disagreement"] = round(float(compute_vote_disagreement(votes)), 6)
    grade["disagreement_source"] = "votes"
    grade["committee"] = {
        "sampled": True,
        "chair": final.get("chair") or "",
        "seats": [
            {"seat": seat["name"], "score": seat.get("score"), "line": seat.get("claim") or ""}
            for seat in final["seats"].values()
        ],
    }
    refresh_display_caps(grade)
    if grade.get("qual_raw") != raw_before:
        grade["qual_raw"] = raw_before
    if signal.get("signal_score") != score_before:
        signal["signal_score"] = score_before
    diag["debate_committee_ran"] = ran + 1
    shadow_score = _shadow_debate_score(signal, grade, skill_dir, caller)
    log_committee_sample(signal, grade, skill_dir, shadow_debate_score=shadow_score)
    return signal


def _shadow_debate_score(
    signal: dict[str, Any],
    grade: dict[str, Any],
    skill_dir: Path | None,
    caller: CompleteFn,
) -> float | None:
    """Extra shadow rounds. The returned score is for the log only."""
    if skill_dir is None:
        return None
    try:
        from config import get_tab_agent_debate_enabled, get_tab_agent_debate_shadow_enabled

        if not (get_tab_agent_debate_enabled(skill_dir) and get_tab_agent_debate_shadow_enabled(skill_dir)):
            return None
        from core.debate_tab_agent import shadow_headers

        headers = shadow_headers(skill_dir)
    except Exception as exc:
        LOG.debug("Debate shadow prompts unavailable: %s", exc)
        return None
    if headers is None:
        return None
    try:
        first = _parse_round(caller(_prompt(signal, grade, None, skill_dir, headers=headers)))
        if first is None:
            return None
        _force_business_abstain(first, grade)
        second = _parse_round(caller(_prompt(signal, grade, first, skill_dir, headers=headers)))
        final = _cap_moves(first, second) if second is not None else first
        scored = [seat for seat in final["seats"].values() if seat.get("score") is not None]
        if len(scored) < 2:
            return None
        return round(float(statistics.median(float(seat["score"]) for seat in scored)), 6)
    except Exception as exc:
        LOG.debug("Debate shadow round failed: %s", exc)
        return None


def _prompt_version_ids(skill_dir: Path | None) -> dict[str, str]:
    if skill_dir is None:
        return {"system": "builtin", "round1": "builtin", "round2": "builtin"}
    try:
        from config import get_tab_agent_debate_enabled

        if not get_tab_agent_debate_enabled(skill_dir):
            return {"system": "builtin", "round1": "builtin", "round2": "builtin"}
        from core.debate_tab_agent import live_version_ids

        return live_version_ids(skill_dir)
    except Exception as exc:
        LOG.debug("Debate prompt version ids unavailable: %s", exc)
        return {"system": "builtin", "round1": "builtin", "round2": "builtin"}


def _packet_id_for(skill_dir: Path | None, ticker: str, day: str) -> str | None:
    if skill_dir is None or not ticker or not day:
        return None
    try:
        from core.decision_packet import load_packets
        from core.tab_agent import utc_day

        staged = None
        other = None
        for packet in load_packets(skill_dir):
            if str(packet.get("ticker") or "").upper() != ticker:
                continue
            if utc_day(packet.get("created_at")) != day:
                continue
            if packet.get("kind") == "staged" and staged is None:
                staged = packet
            elif other is None:
                other = packet
        chosen = staged or other
        if not chosen:
            return None
        packet_id = str(chosen.get("packet_id") or "").strip()
        return packet_id or None
    except Exception as exc:
        LOG.debug("Debate packet id lookup failed: %s", exc)
        return None


def log_committee_sample(
    signal: dict[str, Any],
    grade: dict[str, Any],
    skill_dir: Path | None,
    *,
    shadow_debate_score: float | None = None,
) -> None:
    """Append one sampled score. Scans write this. The backtest does not."""
    if skill_dir is None or grade.get("debate_score") is None:
        return
    path = Path(skill_dir) / COMMITTEE_LOG_FILE
    as_of = datetime.now(timezone.utc).date().isoformat()
    ticker = str(signal.get("ticker") or "").upper()
    row = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "as_of": as_of,
        "ticker": ticker,
        "debate_score": grade.get("debate_score"),
        "qual_disagreement": grade.get("qual_disagreement"),
        "disagreement_source": "votes",
        "prompt_versions": _prompt_version_ids(skill_dir),
        "packet_id": _packet_id_for(skill_dir, ticker, as_of),
        "shadow_debate_score": shadow_debate_score,
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, default=str) + "\n")
    except Exception as exc:
        LOG.warning("Debate committee log write failed for %s: %s", row.get("ticker"), exc)


def load_debate_log(skill_dir: Path | str | None) -> list[dict[str, Any]]:
    if skill_dir is None:
        return []
    path = Path(skill_dir) / COMMITTEE_LOG_FILE
    if not path.exists():
        return []
    out: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except Exception as exc:
        LOG.warning("Debate committee log read failed: %s", exc)
        return []
    for line in lines:
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            out.append(row)
    return out


def lookup_committee_sample(
    ticker: str,
    entry_date: str,
    skill_dir: Path | None,
) -> dict[str, Any] | None:
    """Latest sample whose ``as_of`` is on or before the entry date."""
    entry = str(entry_date or "").strip()[:10]
    name = str(ticker or "").strip().upper()
    if skill_dir is None or len(entry) < 10 or not name:
        return None
    path = Path(skill_dir) / COMMITTEE_LOG_FILE
    if not path.exists():
        return None
    chosen: dict[str, Any] | None = None
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except Exception as exc:
        LOG.debug("Debate committee log read failed: %s", exc)
        return None
    for line in lines:
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(row, dict):
            continue
        if str(row.get("ticker") or "").upper() != name:
            continue
        as_of = str(row.get("as_of") or "")[:10]
        if len(as_of) < 10 or as_of > entry:
            continue
        if chosen is None or as_of >= str(chosen.get("as_of") or "")[:10]:
            chosen = row
    return chosen


def join_committee_sample(signal: dict[str, Any], skill_dir: Path | None) -> None:
    """Copy a prior sample onto the grade at exit. Leaves ``qual_raw`` unchanged."""
    grade = signal.get("debate_grade")
    if not isinstance(grade, dict) or grade.get("debate_score") is not None:
        return
    sample = lookup_committee_sample(
        str(signal.get("ticker") or ""),
        str(signal.get("entry_date") or ""),
        skill_dir,
    )
    if not sample or sample.get("debate_score") is None:
        return
    raw_before = grade.get("qual_raw")
    grade["debate_score"] = sample.get("debate_score")
    grade["qual_disagreement"] = sample.get("qual_disagreement")
    grade["disagreement_source"] = "votes"
    from core.debate_grade import refresh_display_caps

    refresh_display_caps(grade)
    if grade.get("qual_raw") != raw_before:
        grade["qual_raw"] = raw_before


def attach_committee_sample_to_position(position: dict[str, Any], skill_dir: Path | None) -> None:
    """Stamp a closed live position with the sample that existed at entry."""
    sample = lookup_committee_sample(
        str(position.get("ticker") or ""),
        str(position.get("entry_date") or ""),
        skill_dir,
    )
    if not sample:
        return
    position["debate_score"] = sample.get("debate_score")
    position["qual_disagreement"] = sample.get("qual_disagreement")
    position["disagreement_source"] = "votes"
