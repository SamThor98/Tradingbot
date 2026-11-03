"""Historical replay samples for the Today, Debate, and Research tab agents.

The prompt sees only fields stored on the scan date. Bars after the 20-day
scale-out horizon are not used to label the trade. The model may still know
later events; replay samples are weighted below live samples, and a promotion
still needs the live-sample floor.

Analysis only. This module does not place or size orders.
"""

from __future__ import annotations

import json
import logging
import math
import re
import statistics
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

LOG = logging.getLogger(__name__)

REPLAY_FILE = ".tab_agent_replay.jsonl"
TABS = ("today", "debate", "research")
CALLS_PER_CASE = {"today": 1, "debate": 2, "research": 1}
CAVEAT = (
    "Replay sends only point-in-time fields, but the model may already know later prices, "
    "news, and filings. Replay samples are weighted below live samples and cannot by themselves "
    "authorize a promotion."
)

_PIT_FIELDS = (
    "ticker",
    "symbol",
    "price",
    "entry_price",
    "close",
    "stop_pct",
    "stop_price",
    "stage_2",
    "vcp",
    "signal_score",
    "rank_score",
    "score",
    "composite_score",
    "setup_type",
    "sma_50",
    "sma_200",
    "avg_dollar_volume",
    "dollar_volume",
    "spread_pct",
    "relative_volume",
    "rs_rating",
    "sector",
    "industry",
)
_LEAK_FIELDS = {
    "outcome",
    "realized_return_pct",
    "by_horizon",
    "future_return",
    "future_close",
    "label",
    "resolved_at",
}


@dataclass
class ReplayBook:
    returns: list[float]
    dates: list[str]
    weights: list[float] | None
    sources: list[str] | None


def replay_path(skill_dir: Path | None) -> Path:
    root = Path(skill_dir) if skill_dir is not None else Path(".")
    return root / REPLAY_FILE


def load_samples(skill_dir: Path | None) -> list[dict[str, Any]]:
    path = replay_path(skill_dir)
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        LOG.warning("Replay sample read failed: %s", exc)
        return []
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict) and row.get("source") == "replay":
            rows.append(row)
    return rows


def append_sample(skill_dir: Path, sample: dict[str, Any]) -> None:
    path = replay_path(skill_dir)
    line = json.dumps(sample, sort_keys=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def attach_replay(
    returns: list[float],
    dates: list[str],
    *,
    skill_dir: Path | None,
    tab_id: str,
    version_id: str,
    weight: float,
    since_ts: str | None = None,
) -> ReplayBook:
    """Append stored replay rows for one prompt version. Live rows stay at weight 1."""
    live_returns = list(returns)
    live_dates = list(dates)
    if skill_dir is None or not version_id:
        return ReplayBook(live_returns, live_dates, None, None)
    cutoff = str(since_ts or "")[:10]
    clipped = max(0.0, min(1.0, float(weight)))
    extra_returns: list[float] = []
    extra_dates: list[str] = []
    for sample in load_samples(skill_dir):
        if str(sample.get("tab_id") or "") != str(tab_id):
            continue
        if str(sample.get("version_id") or "") != str(version_id):
            continue
        as_of = str(sample.get("as_of") or "")[:10]
        if cutoff and as_of < cutoff:
            continue
        try:
            extra_returns.append(float(sample["realized_return_pct"]))
        except (KeyError, TypeError, ValueError):
            continue
        extra_dates.append(as_of)
    if not extra_returns:
        return ReplayBook(live_returns, live_dates, None, None)
    return ReplayBook(
        live_returns + extra_returns,
        live_dates + extra_dates,
        [1.0] * len(live_returns) + [clipped] * len(extra_returns),
        ["live"] * len(live_returns) + ["replay"] * len(extra_returns),
    )


def pit_payload(signal: dict[str, Any], as_of: str) -> dict[str, Any]:
    """Fields the live prompt is allowed to see. Outcomes and future keys stay out."""
    refs = signal.get("refs") if isinstance(signal.get("refs"), dict) else {}
    payload: dict[str, Any] = {
        "as_of": as_of,
        "instruction": "Decide from these fields only. Long or cash. Never a short or an option.",
    }
    for key in _PIT_FIELDS:
        if key in _LEAK_FIELDS:
            continue
        if key in signal and key not in _LEAK_FIELDS:
            payload[key] = signal[key]
        elif key in refs:
            payload[key] = refs[key]
    grade = signal.get("debate_grade") if isinstance(signal.get("debate_grade"), dict) else {}
    for key in ("qual_trend", "qual_setup", "qual_asymmetry", "qual_crowding", "qual_business", "qual_raw"):
        if signal.get(key) is not None:
            payload[key] = signal.get(key)
        elif grade.get(key) is not None:
            payload[key] = grade.get(key)
    if "vcp" not in payload and signal.get("vcp_present") is not None:
        payload["vcp"] = bool(signal.get("vcp_present"))
    if "stage_2" not in payload and signal.get("stage2") is not None:
        payload["stage_2"] = bool(signal.get("stage2"))
    if payload.get("signal_score") is None:
        for key in ("signal_score", "rank_score", "composite_score", "score"):
            if signal.get(key) is not None:
                payload["signal_score"] = signal.get(key)
                break
            if refs.get(key) is not None:
                payload["signal_score"] = refs.get(key)
                break
    return payload


_VERDICT_RE = re.compile(r"\b(STRONG SETUP|WATCH|HOLD OFF|NO TRADE|STAY IN CASH|ENDORSE)\b", re.IGNORECASE)
_BOILERPLATE_RE = re.compile(
    r"\b(means|requires|is valid|never|do not|don't|otherwise|no short|not a short)\b",
    re.IGNORECASE,
)
_SHORT_RE = re.compile(r"\b(go short|short sell|buy\s+(?:a\s+|the\s+)?puts?|recommend a short)\b", re.IGNORECASE)
_SCORE_RE = re.compile(r"(?:debate[_\s-]*score|score)\s*[:=]?\s*(-?\d{1,3}(?:\.\d+)?)", re.IGNORECASE)
_JSON_CONTRACT = (
    "Reply with one JSON object and no other verdict. "
    '{"decision":"long"|"cash","verdict":"STRONG SETUP"|"WATCH"|"HOLD OFF"} '
    "decision is long for STRONG SETUP and WATCH, and cash for HOLD OFF. "
    "Words inside the instructions are not the verdict."
)
_DEBATE_CONTRACT = (
    "Reply with JSON only: "
    '{"seats":[{"name":"trend","score":0,"claim":"..."}],"chair":"..."} '
    "Scores are 0-100. A low score means cash."
)


def parse_decision(text: str, *, tab: str, quality_threshold: float) -> str | None:
    """Map one model reply to long or cash.

    JSON wins. A quoted rubric (``HOLD OFF means stay in cash``) is not a
    verdict. Debate uses the median seat score, not the first ``score`` field.
    """
    payloads = _json_objects(text)
    chosen = _preferred_payload(payloads)
    if _recommends_short(text, chosen):
        return None
    if tab == "debate":
        scored = _debate_score(text, payloads)
        if scored is not None:
            return "long" if scored >= float(quality_threshold) else "cash"
    if chosen is not None:
        mapped = _decision_from_payload(chosen)
        if mapped is not None:
            return mapped
    return _last_explicit_verdict(text)


def committee_median(text: str) -> float | None:
    """Median of parsed seat scores. None when the reply has no committee JSON."""
    return _debate_score(text, _json_objects(text))


def _json_objects(text: str) -> list[dict[str, Any]]:
    raw = str(text or "")
    found: list[dict[str, Any]] = []
    start: int | None = None
    depth = 0
    for index, char in enumerate(raw):
        if char == "{":
            if depth == 0:
                start = index
            depth += 1
        elif char == "}" and depth:
            depth -= 1
            if depth == 0 and start is not None:
                try:
                    payload = json.loads(raw[start : index + 1])
                except json.JSONDecodeError:
                    payload = None
                if isinstance(payload, dict):
                    found.append(payload)
                start = None
    return found


def _preferred_payload(payloads: list[dict[str, Any]]) -> dict[str, Any] | None:
    for payload in reversed(payloads):
        if any(key in payload for key in ("decision", "verdict", "seats", "score")):
            return payload
    return payloads[-1] if payloads else None


def _decision_from_payload(payload: dict[str, Any]) -> str | None:
    decision = str(payload.get("decision") or "").strip().lower().replace("-", "_")
    if decision in {"long", "buy", "endorse"}:
        return "long"
    if decision in {"cash", "hold", "hold_off", "pass", "no_trade"}:
        return "cash"
    verdict = _verdict_word(str(payload.get("verdict") or ""))
    if verdict is not None:
        return verdict
    return None


def _verdict_word(token: str) -> str | None:
    word = " ".join(token.upper().split())
    if word in {"HOLD OFF", "NO TRADE", "STAY IN CASH", "CASH"}:
        return "cash"
    if word in {"STRONG SETUP", "WATCH", "ENDORSE", "LONG"}:
        return "long"
    return None


def _last_explicit_verdict(text: str) -> str | None:
    """Last verdict token that is not a definition inside the prompt rubric."""
    found: list[str] = []
    for line in str(text or "").splitlines() or [str(text or "")]:
        if _BOILERPLATE_RE.search(line):
            continue
        hits = _VERDICT_RE.findall(line)
        if hits:
            found.append(hits[-1])
    if not found:
        for piece in re.split(r"[\n.]", str(text or "")):
            if _BOILERPLATE_RE.search(piece):
                continue
            hits = _VERDICT_RE.findall(piece)
            if hits:
                found.append(hits[-1])
    if not found:
        return None
    return _verdict_word(found[-1])


def _debate_score(text: str, payloads: list[dict[str, Any]]) -> float | None:
    from core.debate_committee import _parse_round

    for payload in reversed(payloads):
        if "seats" not in payload:
            continue
        parsed = _parse_round(json.dumps(payload))
        if parsed is None:
            continue
        scores = [float(seat["score"]) for seat in parsed["seats"].values() if seat.get("score") is not None]
        if len(scores) >= 2:
            return float(statistics.median(scores))
    for payload in reversed(payloads):
        if "seats" in payload or payload.get("score") is None:
            continue
        try:
            score = float(payload["score"])
        except (TypeError, ValueError):
            continue
        if 0 <= score <= 100:
            return score
    match = _SCORE_RE.search(str(text or ""))
    if match and not payloads:
        try:
            score = float(match.group(1))
        except ValueError:
            return None
        if 0 <= score <= 100:
            return score
    return None


def _recommends_short(text: str, payload: dict[str, Any] | None) -> bool:
    if payload is not None and str(payload.get("decision") or "").strip().lower() in {"short", "put"}:
        return True
    for line in str(text or "").splitlines() or [str(text or "")]:
        if _BOILERPLATE_RE.search(line):
            continue
        if _SHORT_RE.search(line):
            return True
    return False


def _suggests_short(text: str) -> bool:
    return _recommends_short(text, _preferred_payload(_json_objects(text)))


def maturity_calendar_days(horizon_days: int) -> int:
    """Calendar days that cover ``horizon_days`` trading sessions."""
    return int(math.ceil(int(horizon_days) * 7 / 5))


def truncate_history(frame: Any, created: datetime, horizon_days: int) -> Any | None:
    """Keep the entry bar and the next ``horizon_days`` bars. Later bars stay out."""
    from core.outcome_backfill import _locate_entry

    if frame is None or getattr(frame, "empty", True):
        return None
    try:
        entry_pos = _locate_entry(frame, created)
    except Exception as exc:
        LOG.debug("Replay entry locate failed: %s", exc)
        return None
    if entry_pos is None:
        return None
    end = int(entry_pos) + int(horizon_days) + 1
    if end > len(frame):
        return None
    return frame.iloc[:end].copy()


def scale_out_return(
    case: dict[str, Any],
    frame: Any,
    skill_dir: Path | None,
    *,
    now: datetime,
) -> dict[str, Any] | None:
    """20-day scale-out on history that ends at the horizon.

    A missing stop is left missing. The shared labeler then uses a fixed-horizon
    return instead of inventing an R multiple.
    """
    from core.outcome_backfill import _load_exit_params, label_packet_horizons

    created = case.get("created_at")
    if not isinstance(created, datetime):
        return None
    params = _load_exit_params(skill_dir)
    horizon = int(params["primary"])
    window = truncate_history(frame, created, horizon)
    if window is None:
        return None
    packet: dict[str, Any] = {
        "ticker": case.get("ticker"),
        "created_at": created.isoformat(),
        "entry_price": case.get("entry_price"),
        "side": "long",
        "outcome": {"label": "pending"},
    }
    if case.get("stop_pct") not in (None, ""):
        packet["stop_pct"] = case.get("stop_pct")
    if case.get("stop_price") not in (None, ""):
        packet["stop_price"] = case.get("stop_price")
    label_packet_horizons(packet, history_provider=lambda _ticker: window, params=params, now=now)
    outcome = packet.get("outcome") if isinstance(packet.get("outcome"), dict) else {}
    if str(outcome.get("label") or "pending") in {"pending", "unknown", ""}:
        return None
    try:
        if int(outcome.get("horizon_days")) != horizon:
            return None
        return {
            "realized_return_pct": float(outcome["realized_return_pct"]),
            "exit_model": str(outcome.get("exit_model") or "scale_out"),
        }
    except (TypeError, ValueError, KeyError):
        return None


def collect_cases(
    skill_dir: Path,
    *,
    since: datetime,
    now: datetime,
    horizon_days: int,
    db_path: Path | None = None,
    user_id: str = "local",
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """Matured staged packets and scan_results rows. Recent scans stay out."""
    from core.decision_packet import load_local_scan_signals, load_packets

    gap = maturity_calendar_days(horizon_days)
    cases: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    def _add(ticker: str, created: datetime, signal: dict[str, Any]) -> None:
        if created < since:
            return
        if (now.date() - created.date()).days < gap:
            return
        symbol = str(ticker or signal.get("ticker") or signal.get("symbol") or "").upper()
        if not symbol:
            return
        day = created.date().isoformat()
        if (symbol, day) in seen:
            return
        seen.add((symbol, day))
        refs = signal.get("refs") if isinstance(signal.get("refs"), dict) else {}
        entry = signal.get("entry_price")
        if entry is None:
            entry = signal.get("price")
        if entry is None:
            entry = refs.get("price")
        stop, stop_price = _stop_fields(signal, refs, entry)
        cases.append(
            {
                "ticker": symbol,
                "as_of": day,
                "created_at": created,
                "entry_price": entry,
                "stop_pct": stop,
                "stop_price": stop_price,
                "signal": signal,
            }
        )

    for packet in load_packets(skill_dir):
        if not isinstance(packet, dict):
            continue
        created = _parse_dt(packet.get("created_at"))
        if created is None:
            continue
        _add(str(packet.get("ticker") or ""), created, packet)
    database = db_path if db_path is not None else Path(skill_dir) / "webapp" / "webapp.db"
    if database.exists():
        for row in load_local_scan_signals(database, since=since, user_id=user_id):
            signal = row.get("signal") if isinstance(row.get("signal"), dict) else {}
            created = row.get("created_at")
            if not isinstance(created, datetime):
                continue
            _add(str(signal.get("ticker") or ""), created, signal)
    cases.sort(key=lambda item: item["created_at"])
    if limit is not None and limit >= 0:
        return cases[: int(limit)]
    return cases


def _stop_fields(signal: dict[str, Any], refs: dict[str, Any], entry: Any) -> tuple[Any, Any]:
    """Read a stored stop. ``stop`` may be a price or a percent; do not invent one."""
    stop_pct = signal.get("stop_pct")
    if stop_pct is None:
        stop_pct = refs.get("stop_pct")
    stop_price = signal.get("stop_price")
    if stop_price is None:
        stop_price = refs.get("stop_price")
    if stop_pct is not None or stop_price is not None:
        return stop_pct, stop_price
    raw = signal.get("stop")
    if raw is None:
        raw = refs.get("stop")
    try:
        number = float(raw) if raw is not None else None
        entry_price = float(entry) if entry is not None else None
    except (TypeError, ValueError):
        return stop_pct, stop_price
    if number is None:
        return stop_pct, stop_price
    if entry_price and 0 < number < entry_price:
        return None, number
    if 0 < number <= 100:
        return number, None
    return stop_pct, stop_price


def run_replay(
    skill_dir: Path,
    *,
    tab: str,
    since: str,
    limit: int | None = None,
    dry_run: bool = False,
    max_llm_calls: int = 25,
    now: datetime | None = None,
    db_path: Path | None = None,
    user_id: str = "local",
    complete: Callable[[str, str], str] | None = None,
    history_provider: Callable[[str], Any] | None = None,
    versions: list[dict[str, Any]] | None = None,
    progress_every: int = 10,
) -> dict[str, Any]:
    """Score matured scans with the live prompt and any shadow. Dry-run only estimates.

    Already stored ``(version, ticker, day)`` rows are skipped, so a second run
    resumes. Debate prices one committee (round 1 then round 2) and writes a
    sample for system, round1, and round2 from that median score.
    """
    from config import get_tab_agent_replay_usd_per_call
    from core.tab_agent_objective import load_objective

    root = Path(skill_dir)
    tab_id = str(tab or "").strip().lower()
    if tab_id not in TABS:
        raise ValueError(f"Replay tab must be one of {', '.join(TABS)}.")
    since_dt = _parse_since(since)
    clock = now or datetime.now(timezone.utc)
    if clock.tzinfo is None:
        clock = clock.replace(tzinfo=timezone.utc)
    config = load_objective(root)
    chosen = list(versions) if versions is not None else _live_and_shadow(root, tab_id)
    cases = collect_cases(
        root,
        since=since_dt,
        now=clock,
        horizon_days=config.horizon_days,
        db_path=db_path,
        user_id=user_id,
        limit=limit,
    )
    ordered = sorted(
        chosen, key=lambda row: (0 if row.get("status") == "live" else 1, str(row.get("prompt_key") or ""))
    )
    passes = _version_passes(tab_id, ordered)
    seen = {
        (str(row.get("version_id")), str(row.get("ticker")), str(row.get("as_of"))[:10]) for row in load_samples(root)
    }
    estimated = _remaining_calls(cases, passes, seen)
    price = get_tab_agent_replay_usd_per_call(root)
    cap = max(0, int(max_llm_calls))
    calls_each = 2 if tab_id == "debate" else 1
    report: dict[str, Any] = {
        "ok": True,
        "dry_run": bool(dry_run),
        "tab": tab_id,
        "since": since_dt.date().isoformat(),
        "cases": len(cases),
        "versions": len(chosen),
        "calls_per_case": calls_each,
        "estimated_llm_calls": estimated,
        "estimated_cost_usd": round(estimated * price, 4),
        "usd_per_call": price,
        "max_llm_calls": cap,
        "calls_under_cap": min(estimated, cap),
        "llm_calls": 0,
        "stored": 0,
        "skipped_unlabeled": 0,
        "skipped_duplicate": 0,
        "skipped_unparsed": 0,
        "parse_failures": 0,
        "parse_retries": 0,
        "skipped_short": 0,
        "truncated": estimated > cap,
        "source": "replay",
        "replay_weight": config.replay_weight,
        "min_live_samples": config.min_live_samples,
        "caveat": CAVEAT,
        "estimate_note": (
            "The estimate skips rows already stored and assumes every remaining case can be labeled. "
            "One parse retry per failed reply is extra and still stops at max_llm_calls."
        ),
    }
    if dry_run:
        return report
    if not chosen:
        report["ok"] = False
        report["error"] = "no_live_or_shadow_prompt"
        return report
    caller = complete or _default_complete
    provider = history_provider or _default_history(root)
    history_cache: dict[str, Any] = {}
    state = {"used": 0, "stop": False}
    every = max(0, int(progress_every))
    for index, case in enumerate(cases, start=1):
        if state["stop"]:
            break
        labeled = _cached_label(case, provider, history_cache, root, clock)
        if labeled is None:
            report["skipped_unlabeled"] += 1
        elif tab_id == "debate":
            _run_debate_case(
                root,
                case,
                passes,
                labeled,
                config=config,
                caller=caller,
                seen=seen,
                report=report,
                state=state,
                cap=cap,
                clock=clock,
            )
        else:
            _run_prompt_case(
                root,
                case,
                passes,
                labeled,
                tab_id=tab_id,
                config=config,
                caller=caller,
                seen=seen,
                report=report,
                state=state,
                cap=cap,
                clock=clock,
            )
        report["llm_calls"] = state["used"]
        if every and index % every == 0:
            _print_progress(report, index, len(cases))
    report["llm_calls"] = state["used"]
    report["truncated"] = bool(state["stop"])
    if every and (not cases or len(cases) % every != 0):
        _print_progress(report, len(cases), len(cases))
    return report


def _print_progress(report: dict[str, Any], done: int, total: int) -> None:
    cost = round(float(report.get("llm_calls") or 0) * float(report.get("usd_per_call") or 0), 4)
    print(
        f"replay progress cases={done}/{total} llm_calls={report.get('llm_calls')} "
        f"cost_usd={cost} stored={report.get('stored')} "
        f"parse_failures={report.get('parse_failures')} skipped_duplicate={report.get('skipped_duplicate')}",
        flush=True,
    )


def _version_passes(tab: str, versions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if tab != "debate":
        return [{"calls": 1, "versions": [row]} for row in versions]
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in versions:
        groups.setdefault(str(row.get("status") or "live"), []).append(row)
    passes: list[dict[str, Any]] = []
    for status in ("live", "shadow"):
        rows = groups.pop(status, None)
        if rows:
            passes.append({"calls": 2, "versions": rows})
    for rows in groups.values():
        if rows:
            passes.append({"calls": 2, "versions": rows})
    return passes


def _remaining_calls(
    cases: list[dict[str, Any]],
    passes: list[dict[str, Any]],
    seen: set[tuple[str, str, str]],
) -> int:
    total = 0
    for case in cases:
        ticker = str(case.get("ticker") or "")
        day = str(case.get("as_of") or "")[:10]
        for block in passes:
            if any((str(row.get("id") or ""), ticker, day) not in seen for row in block["versions"]):
                total += int(block["calls"])
    return total


def _sample_key(version: dict[str, Any], case: dict[str, Any]) -> tuple[str, str, str]:
    return (str(version.get("id") or ""), str(case.get("ticker") or ""), str(case.get("as_of") or "")[:10])


def _store_sample(
    skill_dir: Path,
    case: dict[str, Any],
    version: dict[str, Any],
    *,
    tab_id: str,
    decision: str,
    labeled: dict[str, Any],
    config: Any,
    clock: datetime,
    seen: set[tuple[str, str, str]],
    report: dict[str, Any],
    extra: dict[str, Any] | None = None,
) -> None:
    key = _sample_key(version, case)
    if key in seen:
        report["skipped_duplicate"] += 1
        return
    realized = 0.0 if decision == "cash" else float(labeled["realized_return_pct"])
    sample = {
        "source": "replay",
        "tab_id": tab_id,
        "prompt_key": version.get("prompt_key"),
        "version_id": version.get("id"),
        "role": version.get("status"),
        "ticker": case["ticker"],
        "as_of": case["as_of"],
        "decision": decision,
        "realized_return_pct": realized,
        "horizon_days": config.horizon_days,
        "exit_model": labeled.get("exit_model") or "scale_out",
        "weight": config.replay_weight,
        "created_at": clock.isoformat(),
    }
    if extra:
        sample.update(extra)
    append_sample(skill_dir, sample)
    seen.add(key)
    report["stored"] += 1


def _call_model(
    caller: Callable[[str, str], str],
    system: str,
    user: str,
    *,
    report: dict[str, Any],
    state: dict[str, Any],
    cap: int,
) -> str | None:
    if state["used"] + 1 > cap:
        state["stop"] = True
        report["truncated"] = True
        report["llm_calls"] = state["used"]
        return None
    try:
        text = str(caller(system, user) or "")
    except Exception as exc:
        LOG.warning("Replay LLM call failed: %s", exc)
        report["ok"] = False
        report["error"] = str(exc)
        state["stop"] = True
        return None
    state["used"] += 1
    report["llm_calls"] = state["used"]
    return text


def _log_parse_failure(tab: str, version: dict[str, Any], case: dict[str, Any], raw: str, *, retry: bool) -> None:
    LOG.warning(
        "Replay parse %s tab=%s prompt_key=%s ticker=%s as_of=%s raw=%s",
        "retry failed" if retry else "failed",
        tab,
        version.get("prompt_key"),
        case.get("ticker"),
        case.get("as_of"),
        str(raw or "")[:500],
    )


def _decide_text(
    text: str,
    *,
    tab: str,
    threshold: float,
    case: dict[str, Any],
    version: dict[str, Any],
    caller: Callable[[str, str], str],
    system: str,
    user: str,
    report: dict[str, Any],
    state: dict[str, Any],
    cap: int,
) -> tuple[str | None, str]:
    decision = parse_decision(text, tab=tab, quality_threshold=threshold)
    if decision is not None or state["stop"]:
        return decision, text
    _log_parse_failure(tab, version, case, text, retry=False)
    report["parse_retries"] += 1
    retried = _call_model(
        caller,
        system,
        user + "\n\nThe previous reply did not parse. " + _JSON_CONTRACT,
        report=report,
        state=state,
        cap=cap,
    )
    if retried is None:
        report["parse_failures"] += 1
        report["skipped_unparsed"] += 1
        return None, text
    decision = parse_decision(retried, tab=tab, quality_threshold=threshold)
    if decision is None:
        _log_parse_failure(tab, version, case, retried, retry=True)
        report["parse_failures"] += 1
        report["skipped_unparsed"] += 1
        if _suggests_short(retried):
            report["skipped_short"] += 1
            report["skipped_unparsed"] -= 1
    return decision, retried


def _run_prompt_case(
    skill_dir: Path,
    case: dict[str, Any],
    passes: list[dict[str, Any]],
    labeled: dict[str, Any],
    *,
    tab_id: str,
    config: Any,
    caller: Callable[[str, str], str],
    seen: set[tuple[str, str, str]],
    report: dict[str, Any],
    state: dict[str, Any],
    cap: int,
    clock: datetime,
) -> None:
    payload = pit_payload(case["signal"] if isinstance(case.get("signal"), dict) else {}, str(case.get("as_of") or ""))
    user = json.dumps(payload, default=str) + "\n" + _JSON_CONTRACT
    for block in passes:
        for version in block["versions"]:
            if state["stop"]:
                return
            if _sample_key(version, case) in seen:
                report["skipped_duplicate"] += 1
                continue
            text = _call_model(caller, str(version.get("text") or ""), user, report=report, state=state, cap=cap)
            if text is None:
                return
            decision, _raw = _decide_text(
                text,
                tab=tab_id,
                threshold=float(config.quality_threshold),
                case=case,
                version=version,
                caller=caller,
                system=str(version.get("text") or ""),
                user=user,
                report=report,
                state=state,
                cap=cap,
            )
            if decision is None or state["stop"] and decision is None:
                continue
            if decision is None:
                continue
            _store_sample(
                skill_dir,
                case,
                version,
                tab_id=tab_id,
                decision=decision,
                labeled=labeled,
                config=config,
                clock=clock,
                seen=seen,
                report=report,
            )


def _run_debate_case(
    skill_dir: Path,
    case: dict[str, Any],
    passes: list[dict[str, Any]],
    labeled: dict[str, Any],
    *,
    config: Any,
    caller: Callable[[str, str], str],
    seen: set[tuple[str, str, str]],
    report: dict[str, Any],
    state: dict[str, Any],
    cap: int,
    clock: datetime,
) -> None:
    from core.debate_committee import _ROUND1, _ROUND2, _SYSTEM, _cap_moves, _parse_round

    payload = pit_payload(case["signal"] if isinstance(case.get("signal"), dict) else {}, str(case.get("as_of") or ""))
    threshold = float(config.quality_threshold)
    for block in passes:
        versions = list(block["versions"])
        if all(_sample_key(row, case) in seen for row in versions):
            report["skipped_duplicate"] += len(versions)
            continue
        if state["stop"]:
            return
        by_key = {str(row.get("prompt_key") or ""): row for row in versions}
        system = str((by_key.get("system") or {}).get("text") or _SYSTEM)
        round1 = str((by_key.get("round1") or {}).get("text") or _ROUND1)
        round2 = str((by_key.get("round2") or {}).get("text") or _ROUND2)
        user1 = round1 + "\n" + json.dumps(payload, default=str) + "\n" + _DEBATE_CONTRACT
        raw1 = _call_model(caller, system, user1, report=report, state=state, cap=cap)
        if raw1 is None:
            return
        parsed1 = _parse_round(raw1)
        if parsed1 is None and not state["stop"]:
            _log_parse_failure("debate", by_key.get("round1") or versions[0], case, raw1, retry=False)
            report["parse_retries"] += 1
            raw1 = _call_model(
                caller,
                system,
                user1 + "\n\nThe previous reply did not parse. " + _DEBATE_CONTRACT,
                report=report,
                state=state,
                cap=cap,
            )
            parsed1 = _parse_round(raw1 or "")
        if parsed1 is None:
            report["parse_failures"] += 1
            report["skipped_unparsed"] += 1
            _log_parse_failure("debate", by_key.get("round1") or versions[0], case, raw1 or "", retry=True)
            continue
        prior = {
            "chair": parsed1.get("chair"),
            "seats": [
                {"name": name, "score": seat.get("score"), "claim": seat.get("claim")}
                for name, seat in (parsed1.get("seats") or {}).items()
            ],
        }
        user2 = round2 + "\n" + json.dumps({**payload, "round1": prior}, default=str) + "\n" + _DEBATE_CONTRACT
        raw2 = _call_model(caller, system, user2, report=report, state=state, cap=cap)
        if raw2 is None:
            final = parsed1
            scores = [float(seat["score"]) for seat in final["seats"].values() if seat.get("score") is not None]
            if len(scores) < 2:
                report["skipped_unparsed"] += 1
                continue
            _store_debate_samples(
                skill_dir,
                case,
                versions,
                scores,
                labeled=labeled,
                config=config,
                clock=clock,
                seen=seen,
                report=report,
                threshold=threshold,
            )
            return
        parsed2 = _parse_round(raw2)
        if parsed2 is None and not state["stop"]:
            _log_parse_failure("debate", by_key.get("round2") or versions[0], case, raw2 or "", retry=False)
            report["parse_retries"] += 1
            raw2 = _call_model(
                caller,
                system,
                user2 + "\n\nThe previous reply did not parse. " + _DEBATE_CONTRACT,
                report=report,
                state=state,
                cap=cap,
            )
            parsed2 = _parse_round(raw2 or "")
        if parsed2 is None:
            report["parse_failures"] += 1
            _log_parse_failure("debate", by_key.get("round2") or versions[0], case, raw2 or "", retry=True)
            final = parsed1
        else:
            final = _cap_moves(parsed1, parsed2)
        scores = [float(seat["score"]) for seat in final["seats"].values() if seat.get("score") is not None]
        if len(scores) < 2:
            report["parse_failures"] += 1
            report["skipped_unparsed"] += 1
            continue
        _store_debate_samples(
            skill_dir,
            case,
            versions,
            scores,
            labeled=labeled,
            config=config,
            clock=clock,
            seen=seen,
            report=report,
            threshold=threshold,
        )


def _store_debate_samples(
    skill_dir: Path,
    case: dict[str, Any],
    versions: list[dict[str, Any]],
    scores: list[float],
    *,
    labeled: dict[str, Any],
    config: Any,
    clock: datetime,
    seen: set[tuple[str, str, str]],
    report: dict[str, Any],
    threshold: float,
) -> None:
    median = float(statistics.median(scores))
    decision = "long" if median >= threshold else "cash"
    for version in versions:
        _store_sample(
            skill_dir,
            case,
            version,
            tab_id="debate",
            decision=decision,
            labeled=labeled,
            config=config,
            clock=clock,
            seen=seen,
            report=report,
            extra={"debate_score": round(median, 6)},
        )


def _cached_label(
    case: dict[str, Any],
    provider: Callable[[str], Any],
    cache: dict[str, Any],
    skill_dir: Path,
    now: datetime,
) -> dict[str, Any] | None:
    ticker = str(case.get("ticker") or "")
    if ticker not in cache:
        try:
            cache[ticker] = provider(ticker)
        except Exception as exc:
            LOG.warning("Replay history failed for %s: %s", ticker, exc)
            cache[ticker] = None
    return scale_out_return(case, cache.get(ticker), skill_dir, now=now)


def _live_and_shadow(skill_dir: Path, tab_id: str) -> list[dict[str, Any]]:
    from core.tab_agent import get_tab_agent

    agent = get_tab_agent(tab_id)
    registry = agent.registry(skill_dir)
    return [row for row in registry.versions(tab_id) if row.get("status") in {"live", "shadow"}]


def _default_complete(system: str, user: str) -> str:
    from engine_analysis import _call_llm, _load_env

    return _call_llm(user, system, _load_env())


def _default_history(skill_dir: Path) -> Callable[[str], Any]:
    from core.outcome_backfill import _default_history_provider

    return _default_history_provider(skill_dir, days=800)


def _parse_since(raw: str) -> datetime:
    text = str(raw or "").strip()
    if not text:
        raise ValueError("--since is required (YYYY-MM-DD).")
    if "T" in text or " " in text:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    else:
        parsed = datetime.fromisoformat(text + "T00:00:00")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _parse_dt(raw: Any) -> datetime | None:
    if isinstance(raw, datetime):
        return raw if raw.tzinfo else raw.replace(tzinfo=timezone.utc)
    if not isinstance(raw, str) or not raw.strip():
        return None
    text = raw.strip().replace("Z", "+00:00")
    if " " in text and "T" not in text:
        text = text.replace(" ", "T", 1)
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
