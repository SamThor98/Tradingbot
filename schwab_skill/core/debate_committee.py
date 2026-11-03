"""Debate committee prompts and an observational log.

The committee was not previously in this tree. This module is the system
prompt (``_SYSTEM``) and the round prompts (``_prompt``). It logs
approve/pass calls for later grading. It does not place orders and it does
not change scanner conviction.

``TAB_AGENT_DEBATE_ENABLED`` defaults off. While it is off, ``review_signals``
returns before any LLM call. Shadow rounds run only when
``TAB_AGENT_DEBATE_SHADOW_ENABLED`` is also on.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

LOG = logging.getLogger(__name__)

DEBATE_LOG_FILE = ".debate_committee_log.jsonl"

_SYSTEM = (
    "You are the chair of a small equity-research committee. "
    "You weigh a bull round and a bear round on a scan candidate. "
    "You do not place orders, size positions, or override risk limits. "
    "When asked for a decision, return one JSON object with decision "
    "(approve or pass) and conviction from 0 to 100."
)

_ROUND_PROMPTS: dict[str, str] = {
    "bull": (
        "Bull round. Argue the strongest honest case to approve {ticker}. "
        "Use only the context below. Do not invent prices or filings.\n\n{context}"
    ),
    "bear": (
        "Bear round. Argue the strongest honest case to pass on {ticker}. "
        "Use only the context below. Name the main way this setup fails.\n\n{context}"
    ),
    "chair": (
        "Chair round for {ticker}. You have the bull and bear notes below. "
        "Return exactly one JSON object and no other text: "
        '{"decision": "approve" or "pass", "conviction": number 0-100, "reason": string}. '
        "approve means the setup is worth a tracked paper call. pass means skip it. "
        "Conviction is how sure you are of that decision, not a price target.\n\n{context}"
    ),
}


def _prompt(round_name: str, context: str, *, template: str | None = None, ticker: str = "") -> str:
    """Fill one round prompt. ``template`` overrides the built-in text."""
    key = str(round_name or "").strip().lower()
    body = template if template is not None else _ROUND_PROMPTS.get(key, "")
    if not body:
        raise KeyError(f"Unknown debate round: {round_name}")
    return body.replace("{ticker}", ticker or "the name").replace("{context}", context)


def prompt_catalog() -> dict[str, str]:
    """Built-in prompts keyed for the version registry."""
    return {"system": _SYSTEM, **_ROUND_PROMPTS}


def resolve_prompts(skill_dir: Path | str) -> dict[str, Any]:
    """Live prompt text and version ids. Built-ins when the debate agent is off."""
    from config import get_tab_agent_debate_enabled, get_tab_agent_debate_shadow_enabled

    catalog = prompt_catalog()
    if not get_tab_agent_debate_enabled(Path(skill_dir)):
        return {"enabled": False, "live": catalog, "shadow": {}, "versions": {}, "shadow_versions": {}}
    from core.debate_tab_agent import DebateTabAgent

    agent = DebateTabAgent()
    reg = agent.registry(Path(skill_dir))
    live: dict[str, str] = {}
    versions: dict[str, str] = {}
    shadow: dict[str, str] = {}
    shadow_versions: dict[str, str] = {}
    shadow_on = get_tab_agent_debate_shadow_enabled(Path(skill_dir))
    for key, fallback in catalog.items():
        current = reg.live_version(agent.tab_id, key)
        if current and str(current.get("text") or "").strip():
            live[key] = str(current["text"])
            versions[key] = str(current["id"])
        else:
            live[key] = fallback
        if not shadow_on:
            continue
        shadow_row = next((row for row in reg.versions(agent.tab_id, key) if row.get("status") == "shadow"), None)
        if shadow_row and str(shadow_row.get("text") or "").strip():
            shadow[key] = str(shadow_row["text"])
            shadow_versions[key] = str(shadow_row["id"])
    return {
        "enabled": True,
        "live": live,
        "shadow": shadow,
        "versions": versions,
        "shadow_versions": shadow_versions,
    }


def parse_committee_decision(raw: str) -> dict[str, Any] | None:
    text = (raw or "").strip()
    if not text:
        return None
    if text.startswith("```"):
        lines = [line for line in text.splitlines() if not line.strip().startswith("```")]
        text = "\n".join(lines).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    decision = str(data.get("decision") or "").strip().lower()
    if decision not in ("approve", "pass"):
        return None
    try:
        conviction = float(data.get("conviction"))
    except (TypeError, ValueError):
        conviction = None
    if conviction is None:
        return None
    conviction = max(0.0, min(100.0, conviction))
    return {
        "decision": decision,
        "conviction": round(conviction, 2),
        "reason": str(data.get("reason") or "").strip()[:400],
    }


def _default_llm(system: str, user: str) -> str:
    from engine_analysis import _call_llm, _load_env

    return _call_llm(user, system, _load_env())


def _context_for(signal: dict[str, Any]) -> str:
    ticker = str(signal.get("ticker") or signal.get("symbol") or "").upper()
    parts = [f"ticker={ticker}"]
    for key in ("price", "rank_score", "signal_score", "setup_type", "_filter_status"):
        if signal.get(key) is not None:
            parts.append(f"{key}={signal.get(key)}")
    return "\n".join(parts)


def _packet_id_for(skill_dir: Path, ticker: str, day: str) -> str | None:
    try:
        from core.decision_packet import load_packets
        from core.tab_agent import utc_day
    except Exception:
        return None
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


def _already_logged(path: Path, ticker: str, day: str) -> bool:
    if not path.exists():
        return False
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except Exception:
        return False
    for line in lines:
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(row, dict):
            continue
        if str(row.get("ticker") or "").upper() == ticker and str(row.get("date") or "") == day:
            if row.get("role", "live") == "live":
                return True
    return False


def load_debate_log(skill_dir: Path | str) -> list[dict[str, Any]]:
    path = Path(skill_dir) / DEBATE_LOG_FILE
    if not path.exists():
        return []
    out: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except Exception as exc:
        LOG.warning("Debate log read failed: %s", exc)
        return []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            out.append(row)
    return out


def review_signals(
    skill_dir: Path | str,
    signals: list[dict[str, Any]] | None,
    *,
    llm: Callable[[str, str], str] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Log a committee call per kept signal. No-op, including zero LLM calls, when disabled."""
    from config import get_tab_agent_debate_enabled, get_tab_agent_debate_max_names, get_tab_agent_debate_shadow_enabled

    root = Path(skill_dir)
    if not get_tab_agent_debate_enabled(root):
        return {"ran": False, "reason": "disabled", "calls": 0, "logged": 0}
    resolved = resolve_prompts(root)
    caller = llm or _default_llm
    when = now or datetime.now(timezone.utc)
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    day = when.astimezone(timezone.utc).date().isoformat()
    path = root / DEBATE_LOG_FILE
    cap = get_tab_agent_debate_max_names(root)
    calls = 0
    logged = 0
    skipped = 0
    shadow_on = get_tab_agent_debate_shadow_enabled(root) and bool(resolved.get("shadow"))
    kept = [sig for sig in (signals or []) if isinstance(sig, dict)]
    for signal in kept:
        if logged >= cap:
            break
        ticker = str(signal.get("ticker") or signal.get("symbol") or "").upper().strip()
        if not ticker:
            continue
        status = str(signal.get("_filter_status") or "kept").strip().lower()
        if status not in ("", "kept"):
            continue
        if _already_logged(path, ticker, day):
            skipped += 1
            continue
        context = _context_for(signal)
        live = resolved["live"]
        try:
            bull = caller(live["system"], _prompt("bull", context, template=live["bull"], ticker=ticker))
            calls += 1
            bear = caller(live["system"], _prompt("bear", context, template=live["bear"], ticker=ticker))
            calls += 1
            chair_context = f"{context}\n\nBull:\n{bull}\n\nBear:\n{bear}"
            chair_raw = caller(live["system"], _prompt("chair", chair_context, template=live["chair"], ticker=ticker))
            calls += 1
        except Exception as exc:
            LOG.warning("Debate committee call failed for %s: %s", ticker, exc)
            continue
        parsed = parse_committee_decision(chair_raw)
        if parsed is None:
            skipped += 1
            continue
        shadow_decision = None
        shadow_conviction = None
        if shadow_on:
            shadow_live = dict(live)
            shadow_live.update(resolved["shadow"])
            try:
                shadow_raw = caller(
                    shadow_live["system"],
                    _prompt("chair", chair_context, template=shadow_live["chair"], ticker=ticker),
                )
                calls += 1
                shadow_parsed = parse_committee_decision(shadow_raw)
                if shadow_parsed:
                    shadow_decision = shadow_parsed["decision"]
                    shadow_conviction = shadow_parsed["conviction"]
            except Exception as exc:
                LOG.warning("Debate shadow call failed for %s: %s", ticker, exc)
        row = {
            "ts": when.astimezone(timezone.utc).isoformat(),
            "date": day,
            "ticker": ticker,
            "packet_id": _packet_id_for(root, ticker, day),
            "decision": parsed["decision"],
            "conviction": parsed["conviction"],
            "reason": parsed["reason"],
            "prompt_versions": dict(resolved.get("versions") or {}),
            "role": "live",
            "shadow_decision": shadow_decision,
            "shadow_conviction": shadow_conviction,
            "shadow_prompt_versions": dict(resolved.get("shadow_versions") or {}) if shadow_on else {},
        }
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(row) + "\n")
            logged += 1
        except Exception as exc:
            LOG.warning("Debate log write failed for %s: %s", ticker, exc)
    return {"ran": True, "calls": calls, "logged": logged, "skipped": skipped}
