"""Today-tab agent over the scanner's MiroFish persona prompts.

The graded prompts are ``engine_analysis.AGENT_SYSTEMS`` (institutional trend,
mean reversion, retail FOMO). The decision brief on the Today tab is a
template, not an LLM prompt, so it is not versioned here.

Shadow runs call the candidate system prompt and record the version id. Their
votes are not folded into the conviction score that the scanner uses.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Callable

from core.tab_agent import (
    STATUS_PENDING,
    PromptRegistry,
    TabAgent,
    load_attributions,
    score_prompt_versions,
)

LOG = logging.getLogger(__name__)

IMPROVER_SYSTEM = (
    "You revise one system prompt for a stock-scan persona. "
    "You do not place orders, size trades, or change risk limits. "
    "Return exactly one JSON object with keys text and rationale. "
    "text is the full replacement system prompt. "
    "rationale states what changed and must admit when the sample is too small to prove an edge."
)


def _catalog() -> dict[str, str]:
    from engine_analysis import AGENT_SYSTEMS

    return {str(key): str(text) for key, text in AGENT_SYSTEMS.items()}


def resolve_for_scan(skill_dir: Path | str) -> tuple[dict[str, str], dict[str, str], list[dict[str, str]]]:
    """Live persona prompts, optional shadow prompts, and attribution bindings.

    When the Today agent is off, this returns the built-in prompts and does
    not read or write the registry.
    """
    from config import get_tab_agent_shadow_enabled, get_tab_agent_today_enabled
    from engine_analysis import AGENT_SYSTEMS

    builtin = {str(key): str(text) for key, text in AGENT_SYSTEMS.items()}
    if not get_tab_agent_today_enabled(Path(skill_dir)):
        return builtin, {}, []
    agent = TodayTabAgent()
    reg = agent.registry(Path(skill_dir))
    shadow_on = get_tab_agent_shadow_enabled(Path(skill_dir))
    live: dict[str, str] = {}
    shadow: dict[str, str] = {}
    bindings: list[dict[str, str]] = []
    for key, fallback in builtin.items():
        current = reg.live_version(agent.tab_id, key)
        if current and str(current.get("text") or "").strip():
            live[key] = str(current["text"])
            bindings.append({"prompt_key": key, "version_id": str(current["id"]), "role": "live"})
        else:
            live[key] = fallback
        if not shadow_on:
            continue
        shadow_row = next(
            (row for row in reg.versions(agent.tab_id, key) if row.get("status") == "shadow"),
            None,
        )
        if shadow_row and str(shadow_row.get("text") or "").strip():
            shadow[key] = str(shadow_row["text"])
            bindings.append({"prompt_key": key, "version_id": str(shadow_row["id"]), "role": "shadow"})
    return live, shadow, bindings


def load_counterfactual_events(skill_dir: Path | str) -> list[dict[str, Any]]:
    from agent_intelligence import COUNTERFACTUAL_LOG_FILE

    path = Path(skill_dir) / COUNTERFACTUAL_LOG_FILE
    if not path.exists():
        return []
    out: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except Exception as exc:
        LOG.warning("Counterfactual log read failed: %s", exc)
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


def parse_prompt_proposal(raw: str) -> dict[str, str] | None:
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
    body = str(data.get("text") or "").strip()
    rationale = str(data.get("rationale") or "").strip()
    if len(body) < 40:
        return None
    if not rationale:
        rationale = "Revised from recent hits and misses. Sample may be too small to prove an edge."
    return {"text": body, "rationale": rationale}


def _default_llm(system: str, user: str) -> str:
    from engine_analysis import _call_llm, _load_env

    return _call_llm(user, system, _load_env())


class TodayTabAgent(TabAgent):
    tab_id = "today"
    metric_name = "realized_10d"

    def prompt_catalog(self) -> dict[str, str]:
        return _catalog()

    def score(self, skill_dir: Path, *, registry: PromptRegistry | None = None) -> dict[str, Any]:
        from config import get_decision_packet_horizon_days, get_tab_agent_min_samples
        from core.decision_packet import load_packets

        reg = registry or self.registry(skill_dir)
        horizon = get_decision_packet_horizon_days(skill_dir)
        report = score_prompt_versions(
            versions=reg.versions(self.tab_id),
            packets=load_packets(skill_dir),
            events=load_counterfactual_events(skill_dir),
            attributions=load_attributions(skill_dir, tab_id=self.tab_id),
            min_samples=get_tab_agent_min_samples(skill_dir),
            horizon_days=horizon,
        )
        report["tab_id"] = self.tab_id
        report["metric"] = self.metric_name
        return report

    def propose(
        self,
        skill_dir: Path,
        *,
        registry: PromptRegistry | None = None,
        llm: Callable[[str, str], str] | None = None,
        prompt_key: str | None = None,
    ) -> dict[str, Any]:
        reg = registry or self.registry(skill_dir)
        caller = llm or _default_llm
        score = score_prompt_versions(
            versions=reg.versions(self.tab_id),
            packets=_safe_packets(skill_dir),
            events=load_counterfactual_events(skill_dir),
            attributions=load_attributions(skill_dir, tab_id=self.tab_id),
            min_samples=_min_samples(skill_dir),
            horizon_days=_horizon(skill_dir),
        )
        created: list[dict[str, Any]] = []
        skipped: list[dict[str, str]] = []
        catalog = self.prompt_catalog()
        keys = [prompt_key] if prompt_key else list(catalog)
        for key in keys:
            if key not in catalog:
                skipped.append({"prompt_key": str(key), "reason": "unknown_prompt"})
                continue
            live = reg.live_version(self.tab_id, key)
            if live is None:
                skipped.append({"prompt_key": key, "reason": "no_live_prompt"})
                continue
            if any(row.get("status") == STATUS_PENDING for row in reg.versions(self.tab_id, key)):
                skipped.append({"prompt_key": key, "reason": "pending_exists"})
                continue
            user = _improver_user(key, str(live.get("text") or ""), score, skill_dir, str(live.get("id") or ""))
            try:
                raw = caller(IMPROVER_SYSTEM, user)
            except Exception as exc:
                LOG.warning("Today prompt proposal failed for %s: %s", key, exc)
                skipped.append({"prompt_key": key, "reason": "llm_error"})
                continue
            parsed = parse_prompt_proposal(raw or "")
            if parsed is None:
                skipped.append({"prompt_key": key, "reason": "no_proposal"})
                continue
            if parsed["text"].strip() == str(live.get("text") or "").strip():
                skipped.append({"prompt_key": key, "reason": "unchanged"})
                continue
            created.append(
                reg.add_version(
                    self.tab_id,
                    prompt_key=key,
                    text=parsed["text"],
                    parent=str(live.get("id") or "") or None,
                    rationale=parsed["rationale"],
                    status=STATUS_PENDING,
                )
            )
        return {
            "tab_id": self.tab_id,
            "created": created,
            "skipped": skipped,
            "auto_promote": False,
            "score": score,
        }


def _safe_packets(skill_dir: Path) -> list[dict[str, Any]]:
    try:
        from core.decision_packet import load_packets

        return load_packets(skill_dir)
    except Exception as exc:
        LOG.warning("Decision packet load failed for Today agent: %s", exc)
        return []


def _min_samples(skill_dir: Path) -> int:
    from config import get_tab_agent_min_samples

    return get_tab_agent_min_samples(skill_dir)


def _horizon(skill_dir: Path) -> int:
    from config import get_decision_packet_horizon_days

    return get_decision_packet_horizon_days(skill_dir)


def _examples(skill_dir: Path, version_id: str, *, limit: int = 6) -> tuple[list[str], list[str]]:
    from core.tab_agent import utc_day

    hits: list[str] = []
    misses: list[str] = []
    uses = {
        (str(row.get("ticker") or "").upper(), str(row.get("day") or ""))
        for row in load_attributions(skill_dir, tab_id="today")
        if row.get("version_id") == version_id
    }
    for packet in _safe_packets(skill_dir):
        ticker = str(packet.get("ticker") or "").upper()
        day = utc_day(packet.get("created_at")) or ""
        if uses and (ticker, day) not in uses:
            continue
        outcome = packet.get("outcome") if isinstance(packet.get("outcome"), dict) else {}
        label = str(outcome.get("label") or "pending")
        ret = outcome.get("realized_return_pct")
        line = f"{ticker} {day} label={label} return_pct={ret} setup={packet.get('setup_type')}"
        if label == "win" and len(hits) < limit:
            hits.append(line)
        elif label == "loss" and len(misses) < limit:
            misses.append(line)
    return hits, misses


def _improver_user(prompt_key: str, live_text: str, score: dict[str, Any], skill_dir: Path, version_id: str) -> str:
    stat = next((row for row in score.get("versions") or [] if row.get("version_id") == version_id), {})
    hits, misses = _examples(skill_dir, version_id)
    return (
        f"Prompt key: {prompt_key}\n"
        f"Labeled samples: {stat.get('n', 0)} (minimum before a comparison is readable: {score.get('min_samples')}).\n"
        f"Win rate: {stat.get('win_rate')} CI95: {stat.get('win_rate_ci95')}\n"
        f"Average realized return %: {stat.get('avg_realized_return_pct')} at horizon {score.get('horizon_days')} trading days.\n"
        f"Suppress precision: {stat.get('suppress_precision')} on n={stat.get('suppress_n')}.\n"
        "Hits:\n"
        + ("\n".join(hits) if hits else "(none)")
        + "\nMisses:\n"
        + ("\n".join(misses) if misses else "(none)")
        + "\n\nCurrent system prompt:\n"
        + live_text
    )
