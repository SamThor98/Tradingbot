"""Historical replay samples for the Today, Debate, and Research tab agents.

The prompt sees only fields stored on the scan date. Bars after the 20-day
scale-out horizon are not used to label the trade. The model may still know
later events; replay samples are weighted below live samples, and a promotion
still needs the live-sample floor.

Analysis only. This module does not place or size orders.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
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
    "relative_strength",
    "sector_relative_strength",
    "sector_winning",
    "sector",
    "sector_etf",
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
    decisions: list[str] | None = None


# These prompts explain, review, or falsify. They do not choose long versus cash.
NON_DECISION_PROMPTS: dict[str, frozenset[str]] = {
    "today": frozenset(
        {
            "grok_research",
            "grok_reasoning",
            "grok_code_review",
            "grok_quant_analysis",
            "grok_adversarial_review",
        }
    ),
    "research": frozenset({"rationale"}),
}
DEGENERATE_MIN_N = 8


def is_decision_prompt(tab_id: str, prompt_key: str | None) -> bool:
    """True when this prompt is the long/cash choice the book should be scored on."""
    return str(prompt_key or "") not in NON_DECISION_PROMPTS.get(str(tab_id or ""), frozenset())


def non_decision_book(version: dict[str, Any]) -> dict[str, Any]:
    """A prompt that never chooses a trade. Outcome metrics would only echo the baseline."""
    return {
        "version_id": str(version.get("id") or ""),
        "prompt_key": version.get("prompt_key"),
        "status": version.get("status"),
        "parent": version.get("parent"),
        "decision_prompt": False,
        "scored": False,
        "n": 0,
        "n_live": 0,
        "n_replay": 0,
        "primary_metric": None,
        "primary_score": None,
        "max_drawdown": None,
        "win_rate": None,
        "n_longs": None,
        "avg_realized_return_pct": None,
        "note": "Not a long/cash prompt. Excluded from outcome scoring.",
    }


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
    live_decisions: list[str] | None = None,
) -> ReplayBook:
    """Append stored replay rows for one prompt version. Live rows stay at weight 1."""
    live_returns = list(returns)
    live_dates = list(dates)
    if skill_dir is None or not version_id:
        return ReplayBook(live_returns, live_dates, None, None, live_decisions)
    cutoff = str(since_ts or "")[:10]
    clipped = max(0.0, min(1.0, float(weight)))
    extra_returns: list[float] = []
    extra_dates: list[str] = []
    extra_decisions: list[str] = []
    for sample in load_samples(skill_dir):
        if sample.get("scored") is False:
            continue
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
        extra_decisions.append(str(sample.get("decision") or "long").lower())
    if not extra_returns:
        return ReplayBook(live_returns, live_dates, None, None, live_decisions)
    base_decisions = list(live_decisions) if live_decisions is not None else ["long"] * len(live_returns)
    return ReplayBook(
        live_returns + extra_returns,
        live_dates + extra_dates,
        [1.0] * len(live_returns) + [clipped] * len(extra_returns),
        ["live"] * len(live_returns) + ["replay"] * len(extra_returns),
        base_decisions + extra_decisions,
    )


def attach_replay_combination(
    returns: list[float],
    dates: list[str],
    *,
    skill_dir: Path | None,
    combination_id: str,
    member_ids: set[str],
    weight: float,
    since_ts: str | None = None,
    live_decisions: list[str] | None = None,
) -> ReplayBook:
    """Attach one replay row per case for a whole debate combination.

    A row stored under the combination id counts once. Older rows that copied
    the same verdict onto each member version count once when their version-id
    set for that ticker and day is exactly the combination.
    """
    live_returns = list(returns)
    live_dates = list(dates)
    if skill_dir is None or not combination_id:
        return ReplayBook(live_returns, live_dates, None, None, live_decisions)
    cutoff = str(since_ts or "")[:10]
    clipped = max(0.0, min(1.0, float(weight)))
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for sample in load_samples(skill_dir):
        if sample.get("scored") is False or str(sample.get("tab_id") or "") != "debate":
            continue
        # Rows with a median are re-labeled at the active quality bar instead of
        # replaying the decision stored for the old bar.
        if sample.get("debate_score") is not None:
            continue
        as_of = str(sample.get("as_of") or "")[:10]
        ticker = str(sample.get("ticker") or "")
        if not ticker or len(as_of) < 10 or (cutoff and as_of < cutoff):
            continue
        grouped.setdefault((ticker, as_of), []).append(sample)
    extra_returns: list[float] = []
    extra_dates: list[str] = []
    extra_decisions: list[str] = []
    members = {str(item) for item in member_ids if str(item)}
    for (_ticker, as_of), rows in grouped.items():
        chosen = next((row for row in rows if str(row.get("version_id") or "") == combination_id), None)
        if chosen is None:
            ids = {str(row.get("version_id") or "") for row in rows}
            if ids != members:
                continue
            chosen = rows[0]
        try:
            extra_returns.append(float(chosen["realized_return_pct"]))
        except (KeyError, TypeError, ValueError):
            continue
        extra_dates.append(as_of)
        extra_decisions.append(str(chosen.get("decision") or "long").lower())
    if not extra_returns:
        return ReplayBook(live_returns, live_dates, None, None, live_decisions)
    base_decisions = list(live_decisions) if live_decisions is not None else ["long"] * len(live_returns)
    return ReplayBook(
        live_returns + extra_returns,
        live_dates + extra_dates,
        [1.0] * len(live_returns) + [clipped] * len(extra_returns),
        ["live"] * len(live_returns) + ["replay"] * len(extra_returns),
        base_decisions + extra_decisions,
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
    "Reply with one JSON object. seats is a list of {name, score, claim}. "
    "Names are trend, setup, risk, skeptic, and business. "
    "score is an integer from 1 to 100 taken from the checklist. "
    "An all-zero card is invalid and will be discarded. "
    "Also include chair. Do not copy an example score."
)
_DEBATE_COMPACT_RETRY = (
    "The previous reply was truncated or was not JSON. "
    "Reply with one compact JSON object and no markdown fence. "
    "seats is five objects with name and integer score only. claim may be empty. Include chair. "
    "Do not decide cash unless the scores say so."
)
DEBATE_MAX_TOKENS = 800
_FADE_CONTRACT = (
    "Reply with one JSON object using your schema. Include fade_score from -100 to 100. "
    "Positive fade_score means the move is more likely to fade, so the trade is cash. "
    "Negative fade_score means a long. Do not add a separate decision field."
)
_CONVICTION_CONTRACT = (
    "Reply with one JSON object using your schema. Include conviction_score from -100 to 100. "
    "Positive conviction is a long. Zero or negative is cash."
)


def _contract_for(prompt_key: str | None) -> str:
    key = str(prompt_key or "")
    if key == "mean_reversion":
        return _FADE_CONTRACT
    if key in {"institutional_trend", "retail_fomo"}:
        return _CONVICTION_CONTRACT
    return _JSON_CONTRACT


def parse_decision(
    text: str,
    *,
    tab: str,
    quality_threshold: float,
    prompt_key: str | None = None,
) -> str | None:
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
    persona = _persona_decision(chosen, prompt_key)
    if persona is not None:
        return persona
    if chosen is not None:
        mapped = _decision_from_payload(chosen)
        if mapped is not None:
            return mapped
    return _last_explicit_verdict(text)


def _usable_round(text: str) -> dict[str, Any] | None:
    """A committee card with at least two scores that are not the all-zero template."""
    from core.debate_committee import _parse_round

    parsed = _parse_round(text)
    if not isinstance(parsed, dict):
        return None
    seats = parsed.get("seats") if isinstance(parsed.get("seats"), dict) else {}
    scores = [
        float(seat["score"]) for seat in seats.values() if isinstance(seat, dict) and seat.get("score") is not None
    ]
    if len(scores) < 2 or _all_zero(scores):
        return None
    return parsed


def committee_median(text: str) -> float | None:
    """Median of parsed seat scores. None when the reply has no committee JSON."""
    return _debate_score(text, _json_objects(text))


def _json_objects(text: str) -> list[dict[str, Any]]:
    from core.debate_committee import unwrap_model_json

    raw = unwrap_model_json(text)
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


def _all_zero(scores: list[float]) -> bool:
    return bool(scores) and all(abs(score) < 1e-9 for score in scores)


def _persona_decision(payload: dict[str, Any] | None, prompt_key: str | None) -> str | None:
    """Map a persona's own schema. Mean-reversion fade is not the generic long/cash contract."""
    if not isinstance(payload, dict):
        return None
    key = str(prompt_key or "")
    if key == "mean_reversion" or (key == "" and "fade_score" in payload and "conviction_score" not in payload):
        if "fade_score" not in payload:
            return None
        try:
            fade = float(payload["fade_score"])
        except (TypeError, ValueError):
            return None
        # Positive fade means the extension is more likely to reverse. That is cash.
        return "cash" if fade > 0 else "long"
    if "conviction_score" in payload and key in {"", "institutional_trend", "retail_fomo"}:
        try:
            conviction = float(payload["conviction_score"])
        except (TypeError, ValueError):
            return None
        return "long" if conviction > 0 else "cash"
    return None


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
        if len(scores) >= 2 and not _all_zero(scores):
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


def _derive_stop_pct(frame: Any, entry_pos: int, entry: float, skill_dir: Path | None) -> tuple[float, str]:
    """ATR stop on bars through the entry. Same clamps as the live adaptive stop."""
    from config import (
        get_adaptive_stop_atr_mult,
        get_adaptive_stop_base_pct,
        get_adaptive_stop_enabled,
        get_adaptive_stop_max_pct,
        get_adaptive_stop_min_pct,
        get_adaptive_stop_trend_lookback,
    )

    base = float(get_adaptive_stop_base_pct(skill_dir))
    if not get_adaptive_stop_enabled(skill_dir):
        return max(0.01, base), "base_pct"
    min_pct = float(get_adaptive_stop_min_pct(skill_dir))
    max_pct = float(get_adaptive_stop_max_pct(skill_dir))
    mult = float(get_adaptive_stop_atr_mult(skill_dir))
    lookback = max(10, int(get_adaptive_stop_trend_lookback(skill_dir)))

    def _clamp(value: float) -> float:
        return max(min_pct, min(max_pct, value))

    prefix = frame.iloc[: int(entry_pos) + 1]
    columns = getattr(prefix, "columns", [])
    if len(prefix) < 15 or "high" not in columns or "low" not in columns or "close" not in columns:
        return _clamp(base), "base_pct"
    try:
        highs = [float(value) for value in prefix["high"].tolist()]
        lows = [float(value) for value in prefix["low"].tolist()]
        closes = [float(value) for value in prefix["close"].tolist()]
    except (TypeError, ValueError):
        return _clamp(base), "base_pct"
    true_ranges: list[float] = []
    for index in range(1, len(closes)):
        true_ranges.append(
            max(highs[index] - lows[index], abs(highs[index] - closes[index - 1]), abs(lows[index] - closes[index - 1]))
        )
    if len(true_ranges) < 14 or entry <= 0:
        return _clamp(base), "base_pct"
    atr = sum(true_ranges[-14:]) / 14.0
    if atr <= 0:
        return _clamp(base), "base_pct"
    stop_pct = (atr / entry) * mult
    if len(closes) > lookback and closes[-(lookback + 1)] > 0:
        trend = closes[-1] / closes[-(lookback + 1)] - 1.0
        if trend < -0.03:
            stop_pct *= 1.2
        elif trend > 0.06:
            stop_pct *= 0.9
    return _clamp(stop_pct), "atr"


def scale_out_return(
    case: dict[str, Any],
    frame: Any,
    skill_dir: Path | None,
    *,
    now: datetime,
) -> dict[str, Any] | None:
    """20-day scale-out on history that ends at the horizon.

    A missing stored stop is derived from ATR on bars up to the entry, using the
    same clamps as the live adaptive stop. Bars after the horizon stay out.
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
    stop_source = "stored"
    if case.get("stop_pct") not in (None, ""):
        packet["stop_pct"] = case.get("stop_pct")
    elif case.get("stop_price") not in (None, ""):
        packet["stop_price"] = case.get("stop_price")
    else:
        from core.outcome_backfill import _locate_entry

        entry_pos = _locate_entry(window, created)
        entry = case.get("entry_price")
        try:
            entry_value = float(entry) if entry not in (None, "") else float(window["close"].iloc[entry_pos or 0])
        except (TypeError, ValueError, IndexError):
            entry_value = 0.0
        if entry_pos is None or entry_value <= 0:
            stop_source = "none"
        else:
            derived, stop_source = _derive_stop_pct(window, int(entry_pos), entry_value, skill_dir)
            packet["stop_pct"] = derived
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
            "stop_source": stop_source,
        }
    except (TypeError, ValueError, KeyError):
        return None


# A case is filled when each group has one point-in-time value. Price history
# supplies the technical groups and relative strength versus SPY. Sector comes
# from the stored scan, the bundled sector-name map, or the on-disk sector
# cache. The business pillar stays missing unless the scan recorded it.
_FILL_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("stage_2", ("stage_2", "stage2")),
    ("vcp", ("vcp", "vcp_present")),
    ("sma_50", ("sma_50",)),
    ("sma_200", ("sma_200",)),
    ("relative_strength", ("rs_rating", "relative_strength", "sector_rs_rank", "sector_relative_strength")),
    ("sector", ("sector", "sector_etf")),
    ("volume", ("avg_dollar_volume", "dollar_volume", "relative_volume")),
    ("qual_trend", ("qual_trend",)),
    ("qual_setup", ("qual_setup",)),
    ("qual_asymmetry", ("qual_asymmetry",)),
    ("qual_crowding", ("qual_crowding",)),
    ("qual_business", ("qual_business",)),
    ("qual_raw", ("qual_raw",)),
)
_MERGE_KEYS = (
    {key for _name, keys in _FILL_GROUPS for key in keys}
    | set(_PIT_FIELDS)
    | {
        "trend_stack_ok",
        "sma200_rising",
        "pts_52w",
        "pts_volume",
        "score_components",
    }
)


def _blank(value: Any) -> bool:
    return value is None or value == ""


def _merge_missing_fields(dst: dict[str, Any], src: dict[str, Any]) -> None:
    """Copy point-in-time fields that the destination does not already have."""
    if not src:
        return
    for key in _MERGE_KEYS:
        if _blank(dst.get(key)) and not _blank(src.get(key)):
            dst[key] = src[key]
    src_grade = src.get("debate_grade") if isinstance(src.get("debate_grade"), dict) else {}
    if not src_grade:
        return
    grade = dst.get("debate_grade") if isinstance(dst.get("debate_grade"), dict) else {}
    for _name, keys in _FILL_GROUPS:
        if not str(_name).startswith("qual_"):
            continue
        for key in keys:
            if _blank(grade.get(key)) and not _blank(src_grade.get(key)):
                grade[key] = src_grade[key]
            if _blank(dst.get(key)) and not _blank(src_grade.get(key)):
                dst[key] = src_grade[key]
    if grade:
        dst["debate_grade"] = grade


def missing_pit_fields(signal: dict[str, Any] | None) -> list[str]:
    """Names of the point-in-time groups that are still empty."""
    payload = signal if isinstance(signal, dict) else {}
    grade = payload.get("debate_grade") if isinstance(payload.get("debate_grade"), dict) else {}
    refs = payload.get("refs") if isinstance(payload.get("refs"), dict) else {}
    missing: list[str] = []
    for name, keys in _FILL_GROUPS:
        found = False
        for key in keys:
            if not _blank(payload.get(key)) or not _blank(grade.get(key)) or not _blank(refs.get(key)):
                found = True
                break
        if not found:
            missing.append(name)
    return missing


def _bars_on_or_before(frame: Any, created: Any) -> Any | None:
    """Bars whose session date is on or before the case date. Later bars stay out."""
    import pandas as pd

    if frame is None or getattr(frame, "empty", True):
        return None
    stamp = _parse_dt(created)
    if stamp is None:
        return None
    work = frame
    if not isinstance(getattr(work, "index", None), pd.DatetimeIndex):
        if "date" not in getattr(work, "columns", []):
            return None
        work = work.set_index("date")
    idx = pd.to_datetime(work.index)
    if getattr(idx, "tz", None) is not None:
        idx = idx.tz_convert("UTC").tz_localize(None)
    work = work.copy()
    work.index = idx
    cutoff = pd.Timestamp(stamp)
    if cutoff.tzinfo is not None:
        cutoff = cutoff.tz_convert("UTC").tz_localize(None)
    prefix = work.loc[work.index.normalize() <= cutoff.normalize()]
    if getattr(prefix, "empty", True):
        return None
    return prefix


def fill_case_from_history(
    case: dict[str, Any],
    frame: Any,
    skill_dir: Path | None,
    benchmarks: Callable[[str], Any] | None = None,
) -> list[str]:
    """Fill technical, relative-strength, sector, and checklist fields.

    Bars after the case date stay out. ``benchmarks`` returns a price frame for
    SPY or a sector ETF. Sector lookup reads the stored map and does not call
    the network. Returns the field groups that are still missing. The case is
    kept either way.
    """
    signal = case.get("signal") if isinstance(case.get("signal"), dict) else {}
    case["signal"] = signal
    if _blank(signal.get("ticker")) and not _blank(case.get("ticker")):
        signal["ticker"] = case.get("ticker")
    if _blank(signal.get("price")) and not _blank(case.get("entry_price")):
        signal["price"] = case.get("entry_price")
    if _blank(signal.get("stop_pct")) and not _blank(case.get("stop_pct")):
        signal["stop_pct"] = case.get("stop_pct")
    if _blank(signal.get("stop_price")) and not _blank(case.get("stop_price")):
        signal["stop_price"] = case.get("stop_price")
    prefix = _bars_on_or_before(frame, case.get("created_at"))
    if prefix is not None:
        _fill_technicals(signal, prefix, skill_dir)
    _fill_sector(signal, skill_dir)
    _fill_relative_strength(signal, frame, case.get("created_at"), benchmarks)
    _fill_qual_from_grade(signal)
    return missing_pit_fields(signal)


def _fill_sector(signal: dict[str, Any], skill_dir: Path | None) -> None:
    """Sector ETF from the scan, the bundled name map, or the yfinance cache."""
    if not _blank(signal.get("sector_etf")) or not _blank(signal.get("sector")):
        if _blank(signal.get("sector_etf")) and not _blank(signal.get("sector")):
            from sector_strength import SECTOR_TO_ETF

            mapped = SECTOR_TO_ETF.get(str(signal.get("sector") or "").strip().lower())
            if mapped:
                signal["sector_etf"] = mapped
        return
    ticker = str(signal.get("ticker") or "").upper()
    if not ticker:
        return
    from sector_strength import cached_ticker_sector_etf

    etf = cached_ticker_sector_etf(ticker, skill_dir)
    if etf:
        signal["sector_etf"] = etf


def _close_return(frame: Any, lookback: int) -> float | None:
    """Simple return over ``lookback`` sessions ending at the last bar."""
    if frame is None or getattr(frame, "empty", True) or len(frame) <= int(lookback):
        return None
    if "close" not in getattr(frame, "columns", []):
        return None
    try:
        end = float(frame["close"].iloc[-1])
        start = float(frame["close"].iloc[-1 - int(lookback)])
    except (TypeError, ValueError, IndexError):
        return None
    if start <= 0 or not math.isfinite(start) or not math.isfinite(end):
        return None
    return (end - start) / start


def _fill_relative_strength(
    signal: dict[str, Any],
    frame: Any,
    created: Any,
    benchmarks: Callable[[str], Any] | None,
) -> None:
    """21-session outperformance versus SPY, the same window as the sector filter.

    The stock, SPY, and the sector ETF are each cut on the case date. A later
    bar cannot move the number. The value is percentage points, so 2.5 means
    the name beat SPY by 2.5 points over those 21 sessions.
    """
    if benchmarks is None:
        return
    from sector_strength import LOOKBACK_DAYS

    lookback = int(LOOKBACK_DAYS)
    prefix = _bars_on_or_before(frame, created)
    stock_ret = _close_return(prefix, lookback)
    try:
        spy = _bars_on_or_before(benchmarks("SPY"), created)
    except Exception as exc:
        LOG.debug("Replay SPY history failed: %s", exc)
        spy = None
    spy_ret = _close_return(spy, lookback)
    if (
        stock_ret is not None
        and spy_ret is not None
        and _blank(signal.get("relative_strength"))
        and _blank(signal.get("rs_rating"))
    ):
        signal["relative_strength"] = round((stock_ret - spy_ret) * 100.0, 4)
        signal["relative_strength_vs"] = "SPY"
        signal["relative_strength_lookback_days"] = lookback
    etf = str(signal.get("sector_etf") or "").upper()
    if not etf or spy_ret is None or not _blank(signal.get("sector_relative_strength")):
        return
    try:
        etf_frame = _bars_on_or_before(benchmarks(etf), created)
    except Exception as exc:
        LOG.debug("Replay sector ETF history failed for %s: %s", etf, exc)
        return
    etf_ret = _close_return(etf_frame, lookback)
    if etf_ret is None:
        return
    signal["sector_relative_strength"] = round((etf_ret - spy_ret) * 100.0, 4)
    if _blank(signal.get("sector_winning")):
        signal["sector_winning"] = bool(etf_ret > spy_ret)


def payload_hash(payload: dict[str, Any]) -> str:
    """Stable id of the point-in-time card sent to the model."""
    body = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()[:16]


def stamp_input_hash(case: dict[str, Any]) -> str:
    payload = pit_payload(
        case.get("signal") if isinstance(case.get("signal"), dict) else {}, str(case.get("as_of") or "")
    )
    digest = payload_hash(payload)
    case["input_hash"] = digest
    case["_pit_payload"] = payload
    return digest


COST_NOTE = (
    "A stored call is reused only when the prompt id and the point-in-time payload hash match. "
    "Debate is two model calls for one combination, not three copies of one verdict. "
    "Older rows were stored under each of system, round1, and round2 and have no payload hash, "
    "so they do not count as this combination. Filling relative strength, sector, and checklist "
    "fields changes the payload, so those cases are priced once. The next dry-run of the same "
    "payloads should show them as already stored."
)


def _fill_technicals(signal: dict[str, Any], prefix: Any, skill_dir: Path | None) -> None:
    from core.debate_grade import stamp_checklist_fields
    from stage_analysis import SMA_50, SMA_200, add_indicators, is_stage_2

    try:
        stamped = stamp_checklist_fields(prefix, skill_dir)
    except Exception as exc:
        LOG.debug("Replay checklist stamp failed: %s", exc)
        stamped = {}
    if isinstance(stamped, dict):
        for key, value in stamped.items():
            if _blank(signal.get(key)) and not _blank(value):
                signal[key] = value
    if _blank(signal.get("vcp")) and not _blank(signal.get("vcp_present")):
        signal["vcp"] = bool(signal.get("vcp_present"))
    if _blank(signal.get("stage_2")) and _blank(signal.get("stage2")):
        try:
            signal["stage_2"] = bool(is_stage_2(prefix, skill_dir))
        except Exception as exc:
            LOG.debug("Replay stage-2 fill failed: %s", exc)
    try:
        indicated = add_indicators(prefix)
    except Exception as exc:
        LOG.debug("Replay indicator fill failed: %s", exc)
        indicated = None
    if indicated is not None and not getattr(indicated, "empty", True):
        latest = indicated.iloc[-1]
        if len(indicated) >= 50 and _blank(signal.get("sma_50")):
            try:
                signal["sma_50"] = float(latest[SMA_50])
            except (TypeError, ValueError, KeyError):
                pass
        if len(indicated) >= 200 and _blank(signal.get("sma_200")):
            try:
                signal["sma_200"] = float(latest[SMA_200])
            except (TypeError, ValueError, KeyError):
                pass
        if _volume_missing(signal) and "volume" in indicated.columns and "close" in indicated.columns:
            window = indicated.iloc[-50:]
            try:
                dollars = [float(c) * float(v) for c, v in zip(window["close"], window["volume"], strict=False)]
                dollars = [value for value in dollars if math.isfinite(value) and value >= 0]
            except (TypeError, ValueError):
                dollars = []
            if dollars:
                signal["avg_dollar_volume"] = round(sum(dollars) / len(dollars), 4)


def _volume_missing(signal: dict[str, Any]) -> bool:
    return all(_blank(signal.get(key)) for key in ("avg_dollar_volume", "dollar_volume", "relative_volume"))


def _fill_qual_from_grade(signal: dict[str, Any]) -> None:
    if not any(
        _blank(signal.get(key)) for key in ("qual_trend", "qual_setup", "qual_asymmetry", "qual_crowding", "qual_raw")
    ):
        return
    try:
        from core.debate_grade import grade_from_signal

        grade = grade_from_signal(signal)
    except Exception as exc:
        LOG.debug("Replay checklist grade failed: %s", exc)
        return
    if not isinstance(grade, dict):
        return
    stored = signal.get("debate_grade") if isinstance(signal.get("debate_grade"), dict) else {}
    for key in ("qual_trend", "qual_setup", "qual_asymmetry", "qual_crowding", "qual_business", "qual_raw"):
        if _blank(signal.get(key)) and not _blank(grade.get(key)):
            signal[key] = grade[key]
        if _blank(stored.get(key)) and not _blank(grade.get(key)):
            stored[key] = grade[key]
    if stored:
        signal["debate_grade"] = stored


def tally_unfilled(cases: list[dict[str, Any]]) -> dict[str, Any]:
    """Count cases that still lack a point-in-time group after the fill."""
    counts: dict[str, int] = {}
    examples: list[dict[str, Any]] = []
    n = 0
    for case in cases:
        signal = case.get("signal") if isinstance(case.get("signal"), dict) else {}
        missing = missing_pit_fields(signal)
        if not missing:
            continue
        n += 1
        for name in missing:
            counts[name] = counts.get(name, 0) + 1
        if len(examples) < 10:
            examples.append({"ticker": case.get("ticker"), "as_of": case.get("as_of"), "missing": missing})
    return {"unfilled_cases": n, "unfilled_field_counts": counts, "unfilled_examples": examples}


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
    """Matured staged packets and scan_results rows. Recent scans stay out.

    A thin packet is filled from the stored scan for the same ticker and day.
    The scan row is the point-in-time record. It does not replace fields the
    packet already has.
    """
    from core.decision_packet import load_local_scan_signals, load_packets

    gap = maturity_calendar_days(horizon_days)
    cases: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    scan_index: dict[tuple[str, str], dict[str, Any]] = {}
    database = db_path if db_path is not None else Path(skill_dir) / "webapp" / "webapp.db"
    scan_rows: list[dict[str, Any]] = []
    if database.exists():
        scan_rows = load_local_scan_signals(database, since=since, user_id=user_id)
        for row in scan_rows:
            signal = row.get("signal") if isinstance(row.get("signal"), dict) else {}
            created = row.get("created_at")
            if not isinstance(created, datetime):
                continue
            symbol = str(signal.get("ticker") or signal.get("symbol") or "").upper()
            if symbol:
                scan_index[(symbol, created.date().isoformat())] = signal

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
        merged = dict(signal)
        extra = scan_index.get((symbol, day))
        if isinstance(extra, dict) and extra is not signal:
            _merge_missing_fields(merged, extra)
        refs = merged.get("refs") if isinstance(merged.get("refs"), dict) else {}
        if refs:
            _merge_missing_fields(merged, refs)
        entry = merged.get("entry_price")
        if entry is None:
            entry = merged.get("price")
        if entry is None:
            entry = refs.get("price")
        stop, stop_price = _stop_fields(merged, refs, entry)
        cases.append(
            {
                "ticker": symbol,
                "as_of": day,
                "created_at": created,
                "entry_price": entry,
                "stop_pct": stop,
                "stop_price": stop_price,
                "signal": merged,
            }
        )

    for packet in load_packets(skill_dir):
        if not isinstance(packet, dict):
            continue
        created = _parse_dt(packet.get("created_at"))
        if created is None:
            continue
        _add(str(packet.get("ticker") or ""), created, packet)
    for row in scan_rows:
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
    prompt_keys: set[str] | None = None,
) -> dict[str, Any]:
    """Score matured scans with the live prompt and any shadow. Dry-run only estimates.

    Already stored ``(version, ticker, day)`` rows are skipped, so a second run
    resumes. Debate prices one committee (round 1 then round 2) and writes one
    sample for that combination of system, round1, and round2.
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
    if prompt_keys and tab_id != "debate":
        chosen = [row for row in chosen if str(row.get("prompt_key") or "") in prompt_keys]
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
        (
            str(row.get("version_id") or ""),
            str(row.get("ticker") or ""),
            str(row.get("as_of") or "")[:10],
            str(row.get("input_hash") or ""),
        )
        for row in load_samples(root)
    }
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
        "estimated_llm_calls": 0,
        "estimated_cost_usd": 0.0,
        "usd_per_call": price,
        "max_llm_calls": cap,
        "calls_under_cap": 0,
        "llm_calls": 0,
        "stored": 0,
        "skipped_unlabeled": 0,
        "skipped_duplicate": 0,
        "skipped_unparsed": 0,
        "parse_failures": 0,
        "parse_retries": 0,
        "skipped_short": 0,
        "skipped_non_decision": 0,
        "truncated": False,
        "source": "replay",
        "replay_weight": config.replay_weight,
        "min_live_samples": config.min_live_samples,
        "caveat": CAVEAT,
        "estimate_note": (
            "The estimate skips a stored row only when its prompt id and payload hash match this case. "
            "One parse retry per failed reply is extra and still stops at max_llm_calls."
        ),
        "cost_note": COST_NOTE,
        "unfilled_cases": 0,
        "unfilled_field_counts": {},
        "unfilled_examples": [],
        "history_fill": False,
    }
    history_cache: dict[str, Any] = {}
    # Pytest dry-runs stay offline. An operator dry-run still fills from bars
    # known on the case date so the unfilled count is real before any model call.
    load_history = history_provider is not None or not dry_run or not os.environ.get("PYTEST_CURRENT_TEST")
    if load_history:
        provider = history_provider or _default_history(root)

        def _benchmarks(symbol: str) -> Any:
            key = str(symbol or "").upper()
            if key not in history_cache:
                try:
                    history_cache[key] = provider(key)
                except Exception as exc:
                    LOG.warning("Replay benchmark history failed for %s: %s", key, exc)
                    history_cache[key] = None
            return history_cache.get(key)

        for case in cases:
            _remember_history(case, provider, history_cache)
            fill_case_from_history(
                case,
                history_cache.get(str(case.get("ticker") or "")),
                root,
                benchmarks=_benchmarks,
            )
    for case in cases:
        stamp_input_hash(case)
    estimated = _remaining_calls(cases, passes, seen)
    report["estimated_llm_calls"] = estimated
    report["estimated_cost_usd"] = round(estimated * price, 4)
    report["calls_under_cap"] = min(estimated, cap)
    report["truncated"] = estimated > cap
    report.update(tally_unfilled(cases))
    report["history_fill"] = bool(load_history)
    if dry_run:
        return report
    if not chosen:
        report["ok"] = False
        report["error"] = "no_live_or_shadow_prompt"
        return report
    if tab_id != "debate":
        report["skipped_non_decision"] = len(cases) * sum(
            1 for row in ordered if not is_decision_prompt(tab_id, str(row.get("prompt_key") or ""))
        )
    if complete is not None:
        caller = complete
    elif tab_id == "debate":
        caller = _debate_complete
    else:
        caller = _default_complete
    provider = history_provider or _default_history(root)
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
        decision_rows = [row for row in versions if is_decision_prompt(tab, str(row.get("prompt_key") or ""))]
        return [{"calls": 1, "versions": [row]} for row in decision_rows]
    return _debate_passes(versions)


def _debate_passes(versions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One live combination, and one shadow combination when a shadow prompt exists."""
    from core.debate_tab_agent import DEBATE_PROMPT_KEYS, combination_id

    def _pick(status: str, key: str) -> dict[str, Any] | None:
        return next(
            (
                row
                for row in versions
                if str(row.get("status") or "") == status and str(row.get("prompt_key") or "") == key
            ),
            None,
        )

    live_rows: list[dict[str, Any]] = []
    shadow_rows: list[dict[str, Any]] = []
    has_shadow = False
    for key in DEBATE_PROMPT_KEYS:
        live = _pick("live", key) or {"id": "builtin", "prompt_key": key, "text": "", "status": "live"}
        shadow = _pick("shadow", key)
        live_rows.append(live)
        if shadow is not None:
            shadow_rows.append(shadow)
            has_shadow = True
        else:
            shadow_rows.append(live)

    def _pack(rows: list[dict[str, Any]], role: str) -> dict[str, Any]:
        ids = {str(row.get("prompt_key") or ""): str(row.get("id") or "builtin") for row in rows}
        members = {key: ids.get(key, "builtin") for key in DEBATE_PROMPT_KEYS}
        return {
            "calls": 2,
            "versions": rows,
            "combination_id": combination_id(members),
            "prompt_versions": members,
            "role": role,
        }

    passes = [_pack(live_rows, "live")]
    shadow_pass = _pack(shadow_rows, "shadow")
    if has_shadow and shadow_pass["combination_id"] != passes[0]["combination_id"]:
        passes.append(shadow_pass)
    return passes


def _remaining_calls(
    cases: list[dict[str, Any]],
    passes: list[dict[str, Any]],
    seen: set[tuple[str, str, str, str]],
) -> int:
    total = 0
    for case in cases:
        ticker = str(case.get("ticker") or "")
        day = str(case.get("as_of") or "")[:10]
        for block in passes:
            digest = str(case.get("input_hash") or "")
            combo = str(block.get("combination_id") or "")
            if combo:
                if (combo, ticker, day, digest) not in seen:
                    total += int(block["calls"])
                continue
            if any((str(row.get("id") or ""), ticker, day, digest) not in seen for row in block["versions"]):
                total += int(block["calls"])
    return total


def _case_payload(case: dict[str, Any]) -> dict[str, Any]:
    cached = case.get("_pit_payload")
    if isinstance(cached, dict) and case.get("input_hash"):
        return cached
    stamp_input_hash(case)
    payload = case.get("_pit_payload")
    return payload if isinstance(payload, dict) else {}


def _sample_key(version: dict[str, Any], case: dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        str(version.get("id") or ""),
        str(case.get("ticker") or ""),
        str(case.get("as_of") or "")[:10],
        str(case.get("input_hash") or ""),
    )


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
    seen: set[tuple[str, str, str, str]],
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
        "counterfactual_return_pct": float(labeled["realized_return_pct"]),
        "horizon_days": config.horizon_days,
        "exit_model": labeled.get("exit_model") or "scale_out",
        "stop_source": labeled.get("stop_source") or "stored",
        "scored": True,
        "weight": config.replay_weight,
        "input_hash": str(case.get("input_hash") or ""),
        "created_at": clock.isoformat(),
    }
    if extra and extra.get("raw_reply"):
        sample["raw_reply"] = str(extra.pop("raw_reply"))[:800]
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
    prompt_key = str(version.get("prompt_key") or "")
    decision = parse_decision(text, tab=tab, quality_threshold=threshold, prompt_key=prompt_key)
    if decision is not None or state["stop"]:
        return decision, text
    _log_parse_failure(tab, version, case, text, retry=False)
    report["parse_retries"] += 1
    retried = _call_model(
        caller,
        system,
        user + "\n\nThe previous reply did not parse. " + _contract_for(prompt_key),
        report=report,
        state=state,
        cap=cap,
    )
    if retried is None:
        report["parse_failures"] += 1
        report["skipped_unparsed"] += 1
        return None, text
    decision = parse_decision(retried, tab=tab, quality_threshold=threshold, prompt_key=prompt_key)
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
    seen: set[tuple[str, str, str, str]],
    report: dict[str, Any],
    state: dict[str, Any],
    cap: int,
    clock: datetime,
) -> None:
    payload = _case_payload(case)
    for block in passes:
        for version in block["versions"]:
            if state["stop"]:
                return
            if not is_decision_prompt(tab_id, str(version.get("prompt_key") or "")):
                report["skipped_non_decision"] += 1
                continue
            if _sample_key(version, case) in seen:
                report["skipped_duplicate"] += 1
                continue
            user = json.dumps(payload, default=str) + "\n" + _contract_for(str(version.get("prompt_key") or ""))
            text = _call_model(caller, str(version.get("text") or ""), user, report=report, state=state, cap=cap)
            if text is None:
                return
            decision, raw = _decide_text(
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
                extra={"raw_reply": raw},
            )


def _run_debate_case(
    skill_dir: Path,
    case: dict[str, Any],
    passes: list[dict[str, Any]],
    labeled: dict[str, Any],
    *,
    config: Any,
    caller: Callable[[str, str], str],
    seen: set[tuple[str, str, str, str]],
    report: dict[str, Any],
    state: dict[str, Any],
    cap: int,
    clock: datetime,
) -> None:
    from core.debate_committee import _ROUND1, _ROUND2, _SYSTEM, _cap_moves

    payload = _case_payload(case)
    threshold = float(config.quality_threshold)
    for block in passes:
        versions = list(block["versions"])
        combo = str(block.get("combination_id") or "")
        if combo and _sample_key({"id": combo}, case) in seen:
            report["skipped_duplicate"] += 1
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
        parsed1 = _usable_round(raw1)
        if parsed1 is None and not state["stop"]:
            _log_parse_failure("debate", by_key.get("round1") or versions[0], case, raw1, retry=False)
            report["parse_retries"] += 1
            raw1 = _call_model(
                caller,
                system,
                user1 + "\n\n" + _DEBATE_COMPACT_RETRY,
                report=report,
                state=state,
                cap=cap,
            )
            parsed1 = _usable_round(raw1 or "")
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
                block,
                scores,
                labeled=labeled,
                config=config,
                clock=clock,
                seen=seen,
                report=report,
                threshold=threshold,
            )
            return
        parsed2 = _usable_round(raw2)
        if parsed2 is None and not state["stop"]:
            _log_parse_failure("debate", by_key.get("round2") or versions[0], case, raw2 or "", retry=False)
            report["parse_retries"] += 1
            raw2 = _call_model(
                caller,
                system,
                user2 + "\n\n" + _DEBATE_COMPACT_RETRY,
                report=report,
                state=state,
                cap=cap,
            )
            parsed2 = _usable_round(raw2 or "")
        if parsed2 is None:
            report["parse_failures"] += 1
            _log_parse_failure("debate", by_key.get("round2") or versions[0], case, raw2 or "", retry=True)
            final = parsed1
        else:
            final = _cap_moves(parsed1, parsed2)
        scores = [float(seat["score"]) for seat in final["seats"].values() if seat.get("score") is not None]
        if len(scores) < 2 or _all_zero(scores):
            report["parse_failures"] += 1
            report["skipped_unparsed"] += 1
            continue
        _store_debate_samples(
            skill_dir,
            case,
            block,
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
    block: dict[str, Any],
    scores: list[float],
    *,
    labeled: dict[str, Any],
    config: Any,
    clock: datetime,
    seen: set[tuple[str, str, str, str]],
    report: dict[str, Any],
    threshold: float,
) -> None:
    """Store the committee median once, keyed by the set of prompt versions."""
    median = float(statistics.median(scores))
    decision = "long" if median >= threshold else "cash"
    version = {
        "id": block.get("combination_id"),
        "prompt_key": "combination",
        "status": block.get("role") or "live",
    }
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
        extra={
            "debate_score": round(median, 6),
            "prompt_versions": block.get("prompt_versions") or {},
        },
    )


def _remember_history(case: dict[str, Any], provider: Callable[[str], Any], cache: dict[str, Any]) -> None:
    ticker = str(case.get("ticker") or "")
    if ticker in cache:
        return
    try:
        cache[ticker] = provider(ticker)
    except Exception as exc:
        LOG.warning("Replay history failed for %s: %s", ticker, exc)
        cache[ticker] = None


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


def _debate_complete(system: str, user: str) -> str:
    """Debate cards are longer than a one-line verdict. 200 tokens truncates the JSON."""
    from engine_analysis import _call_llm, _load_env

    return _call_llm(user, system, _load_env(), max_tokens=DEBATE_MAX_TOKENS)


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


def decision_flags(decisions: list[str] | None) -> dict[str, Any]:
    """Flag a book whose every call was the same side. That score is the baseline."""
    labels = [item for item in (decisions or []) if item in {"long", "cash"}]
    if len(labels) < DEGENERATE_MIN_N:
        return {"decision_prompt": True, "degenerate": False, "scored": True}
    if len(set(labels)) > 1:
        return {"decision_prompt": True, "degenerate": False, "scored": True, "decision_variance": 1}
    reason = "all_long" if "long" in labels else "all_cash"
    return {
        "decision_prompt": True,
        "degenerate": True,
        "scored": True,
        "decision_variance": 0,
        "degenerate_reason": reason,
        "note": "Every decision was the same side, so this Sortino matches buy-every-scan or stay-in-cash.",
    }


def exit_fallback_stats(skill_dir: Path | None, tab_id: str) -> dict[str, Any]:
    rows = [
        row
        for row in load_samples(skill_dir)
        if str(row.get("tab_id") or "") == tab_id and row.get("scored") is not False
    ]
    count = len(rows)
    if not count:
        return {"n": 0, "exit_fallback_rate": None, "derived_stop_rate": None}
    fixed = sum(1 for row in rows if str(row.get("exit_model") or "").startswith("fixed_horizon"))
    derived = sum(1 for row in rows if row.get("stop_source") in {"atr", "base_pct"})
    return {
        "n": count,
        "exit_fallback_rate": round(fixed / count, 4),
        "derived_stop_rate": round(derived / count, 4),
    }


def buy_every_scan_baseline(skill_dir: Path | None, tab_id: str, config: Any) -> dict[str, Any]:
    """One long per scan date, using the scale-out the prompt could have taken."""
    from core.tab_agent_objective import evaluate_book

    chosen: dict[tuple[str, str], float] = {}
    for row in load_samples(skill_dir):
        if str(row.get("tab_id") or "") != tab_id or row.get("scored") is False:
            continue
        key = (str(row.get("ticker") or ""), str(row.get("as_of") or "")[:10])
        if not key[0] or len(key[1]) < 10 or key in chosen:
            continue
        raw = row.get("counterfactual_return_pct")
        if raw is None and str(row.get("decision") or "") == "long":
            raw = row.get("realized_return_pct")
        try:
            if raw is None:
                continue
            chosen[key] = float(raw)
        except (TypeError, ValueError):
            continue
    dates = [key[1] for key in chosen]
    returns = list(chosen.values())
    book = evaluate_book(returns, dates, config=config)
    book["name"] = "buy_every_scan"
    book["prompt_key"] = "buy_every_scan"
    return book


def annotate_tab_score(report: dict[str, Any], skill_dir: Path | None, tab_id: str, config: Any) -> dict[str, Any]:
    """Attach the buy-every-scan baseline, edge, and exit fallback rate."""
    baseline = buy_every_scan_baseline(skill_dir, tab_id, config)
    base_score = baseline.get("primary_score")
    for row in report.get("versions") or []:
        if not isinstance(row, dict):
            continue
        if row.get("decision_prompt") is False:
            row["edge_vs_baseline"] = None
            continue
        score = row.get("primary_score")
        if score is None or base_score is None:
            row["edge_vs_baseline"] = None
        else:
            row["edge_vs_baseline"] = round(float(score) - float(base_score), 6)
    report["baseline"] = baseline
    report["exit_fallback"] = exit_fallback_stats(skill_dir, tab_id)
    return report


def rescore_replay(
    skill_dir: Path,
    *,
    tab: str | None = None,
    history_provider: Callable[[str], Any] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Relabel stored rows without a model call.

    Non-decision prompts and all-zero debate cards stop counting. Rows that
    used a fixed hold get a derived stop when price history is available.
    Mean-reversion rows with no fade_score still need a fresh model call.
    """
    root = Path(skill_dir)
    rows = load_samples(root)
    clock = now or datetime.now(timezone.utc)
    if clock.tzinfo is None:
        clock = clock.replace(tzinfo=timezone.utc)
    provider = history_provider or _default_history(root)
    cache: dict[str, Any] = {}
    needs: set[str] = set()
    summary = {
        "ok": True,
        "rows": 0,
        "non_decision": 0,
        "zero_debate_unscored": 0,
        "relabeled": 0,
        "needs_llm": [],
        "llm_calls": 0,
    }
    for row in rows:
        tab_id = str(row.get("tab_id") or "")
        if tab and tab_id != tab:
            continue
        summary["rows"] += 1
        key = str(row.get("prompt_key") or "")
        if not is_decision_prompt(tab_id, key):
            row["scored"] = False
            row["non_decision"] = True
            summary["non_decision"] += 1
            continue
        if tab_id == "debate" and not row.get("raw_reply"):
            try:
                score = float(row.get("debate_score"))
            except (TypeError, ValueError):
                score = 0.0
            if score == 0.0 and str(row.get("decision") or "") == "cash":
                row["scored"] = False
                row["echo_zero"] = True
                summary["zero_debate_unscored"] += 1
                needs.add("debate")
                continue
        if key == "mean_reversion" and row.get("fade_score") is None and not row.get("raw_reply"):
            row["scored"] = False
            row["needs_native_parse"] = True
            needs.add("today:mean_reversion")
            continue
        if str(row.get("stop_source") or "") == "stored" and str(row.get("exit_model") or "") == "scale_out":
            continue
        created = _parse_dt(str(row.get("as_of") or "") + "T00:00:00+00:00")
        ticker = str(row.get("ticker") or "")
        if created is None or not ticker:
            continue
        if ticker not in cache:
            try:
                cache[ticker] = provider(ticker)
            except Exception as exc:
                LOG.warning("Rescore history failed for %s: %s", ticker, exc)
                cache[ticker] = None
        labeled = scale_out_return(
            {
                "ticker": ticker,
                "created_at": created,
                "entry_price": row.get("entry_price"),
                "stop_pct": row.get("stop_pct"),
                "stop_price": row.get("stop_price"),
            },
            cache.get(ticker),
            root,
            now=clock,
        )
        if labeled is None:
            continue
        row["counterfactual_return_pct"] = labeled["realized_return_pct"]
        row["exit_model"] = labeled["exit_model"]
        row["stop_source"] = labeled.get("stop_source")
        if str(row.get("decision") or "") == "long":
            row["realized_return_pct"] = labeled["realized_return_pct"]
        summary["relabeled"] += 1
    summary["needs_llm"] = sorted(needs)
    text = "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows)
    replay_path(root).write_text(text, encoding="utf-8")
    return summary
