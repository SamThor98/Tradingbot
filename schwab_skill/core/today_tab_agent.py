"""Today-tab agent over MiroFish personas and Grok review roles.

The scan brief itself is a local template. The LLM prompts on this tab are
``engine_analysis.AGENT_SYSTEMS`` and ``core.grok_agent._ROLE_PROMPTS``.
Both are seeded into one registry. Shadow candidates are scored on the shared
objective and promoted only after the review gate. The agent never places orders.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Callable

from core.tab_agent import STATUS_PENDING, PromptRegistry, TabAgent, load_attributions, score_prompt_versions
from core.tab_agent_contract import KEEP_CONTRACT, anchor_reminder, contract_skip, keeps_contract
from core.tab_agent_objective import OBJECTIVE_ID

LOG = logging.getLogger(__name__)

IMPROVER_SYSTEM = (
    "You revise one system prompt used by the Today scanner or Grok review. "
    "The objective is a higher Sortino or Sharpe of the 20-day scale-out return "
    "(half off at 2R, the rest trailed) and a smoother equity curve. Win rate is not the goal. "
    "Long-only equities. Never suggest a short, inverse ETF, or option. "
    "No trade / stay in cash is a valid outcome in a weak regime and scores as 0%, which avoids drawdown. "
    "Do not penalize market cap. Liquidity is average dollar volume and spread, tuned only inside the stated bounds. "
    "Do not cap the number of names. Optimize the quality threshold, not a top-N. "
    "Clean chart and price action, fundamentals, sector and market strength, and a tight stop with good reward-to-risk "
    "are high weight. Catalysts and news are the lowest-priority tiebreaker. "
    "You do not place orders or change size. Return one JSON object with keys text and rationale. "
    "Optional keys: min_avg_dollar_volume, max_spread_pct, quality_threshold. "
    "rationale must say when the sample is too small to prove an edge."
) + KEEP_CONTRACT


def _mirofish_catalog() -> dict[str, str]:
    from engine_analysis import AGENT_SYSTEMS

    return {str(key): str(text) for key, text in AGENT_SYSTEMS.items()}


def _grok_catalog() -> dict[str, str]:
    from core.grok_agent import _ROLE_PROMPTS

    return {f"grok_{key}": str(text) for key, text in _ROLE_PROMPTS.items()}


def resolve_for_scan(skill_dir: Path | str) -> tuple[dict[str, str], dict[str, str], list[dict[str, str]]]:
    """Live and shadow MiroFish system prompts. Built-ins when the agent is off."""
    from config import get_tab_agent_shadow_enabled, get_tab_agent_today_enabled

    builtin = _mirofish_catalog()
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
        current_text = str(current.get("text") or "").strip() if current else ""
        if current and current_text and keeps_contract(agent.tab_id, key, current_text):
            live[key] = current_text
            bindings.append({"prompt_key": key, "version_id": str(current["id"]), "role": "live"})
        else:
            if current_text:
                LOG.warning("Today prompt %s dropped its contract; using the built-in text", key)
            live[key] = fallback
        if not shadow_on:
            continue
        shadow_row = next((row for row in reg.versions(agent.tab_id, key) if row.get("status") == "shadow"), None)
        shadow_text = str(shadow_row.get("text") or "").strip() if shadow_row else ""
        if shadow_row and shadow_text and keeps_contract(agent.tab_id, key, shadow_text):
            shadow[key] = shadow_text
            bindings.append({"prompt_key": key, "version_id": str(shadow_row["id"]), "role": "shadow"})
        elif shadow_text:
            LOG.warning("Today shadow prompt %s dropped its contract; it will not be called", key)
    return live, shadow, bindings


def live_grok_role_text(skill_dir: Path | str, role: str) -> str | None:
    """Registry text for a Grok role, or None when the Today agent is off."""
    from config import get_tab_agent_today_enabled

    if not get_tab_agent_today_enabled(Path(skill_dir)):
        return None
    key = f"grok_{role}"
    current = TodayTabAgent().registry(Path(skill_dir)).live_version("today", key)
    text = str(current.get("text") or "").strip() if current else ""
    if text and keeps_contract("today", key, text):
        return text
    if text:
        LOG.warning("Grok role %s dropped its contract; using the built-in text", role)
    return None


def grok_role_binding(skill_dir: Path | str, role: str) -> dict[str, str] | None:
    if live_grok_role_text(skill_dir, role) is None and not _enabled(skill_dir):
        return None
    from config import get_tab_agent_today_enabled

    if not get_tab_agent_today_enabled(Path(skill_dir)):
        return None
    current = TodayTabAgent().registry(Path(skill_dir)).live_version("today", f"grok_{role}")
    if not current:
        return None
    return {"prompt_key": f"grok_{role}", "version_id": str(current["id"]), "role": "live"}


def _enabled(skill_dir: Path | str) -> bool:
    from config import get_tab_agent_today_enabled

    return get_tab_agent_today_enabled(Path(skill_dir))


class TodayTabAgent(TabAgent):
    tab_id = "today"
    metric_name = OBJECTIVE_ID

    def prompt_catalog(self) -> dict[str, str]:
        catalog = _mirofish_catalog()
        catalog.update(_grok_catalog())
        return catalog

    def score(
        self,
        skill_dir: Path,
        *,
        registry: PromptRegistry | None = None,
        since_ts: str | None = None,
    ) -> dict[str, Any]:
        from core.decision_packet import load_packets

        reg = registry or self.registry(skill_dir)
        report = score_prompt_versions(
            versions=reg.versions(self.tab_id),
            packets=load_packets(skill_dir),
            attributions=load_attributions(skill_dir, tab_id=self.tab_id),
            skill_dir=skill_dir,
            since_ts=since_ts,
        )
        report["tab_id"] = self.tab_id
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
        score = self.score(skill_dir, registry=reg)
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
            if any(row.get("status") in {STATUS_PENDING, "shadow"} for row in reg.versions(self.tab_id, key)):
                skipped.append({"prompt_key": key, "reason": "candidate_exists"})
                continue
            stat = next((row for row in score.get("versions") or [] if row.get("version_id") == live.get("id")), {})
            user = (
                f"Prompt key: {key}\n"
                f"Objective: {score.get('winner_rule')}\n"
                f"Samples: {stat.get('n', 0)} primary {stat.get('primary_metric')}={stat.get('primary_score')} "
                f"max_drawdown={stat.get('max_drawdown')} duration_days={stat.get('max_drawdown_duration_days')}\n"
                f"Secondary win_rate={stat.get('win_rate')} avg_return_pct={stat.get('avg_realized_return_pct')}\n"
                "Revise the prompt to improve the risk-adjusted score without deepening drawdowns.\n"
                f"{anchor_reminder(self.tab_id, key)}\n\n"
                "Current prompt:\n" + str(live.get("text") or "")
            )
            try:
                raw = caller(IMPROVER_SYSTEM, user)
            except Exception as exc:
                LOG.warning("Today prompt proposal failed for %s: %s", key, exc)
                skipped.append({"prompt_key": key, "reason": "llm_error"})
                continue
            parsed = parse_prompt_proposal(raw or "")
            if parsed is None or parsed["text"].strip() == str(live.get("text") or "").strip():
                skipped.append({"prompt_key": key, "reason": "no_proposal"})
                continue
            blocked = contract_skip(self.tab_id, key, parsed["text"])
            if blocked:
                skipped.append(blocked)
                continue
            if any(parsed.get(field) is not None for field in ("min_avg_dollar_volume", "max_spread_pct", "quality_threshold")):
                from core.tab_agent_objective import apply_policy_update

                apply_policy_update(skill_dir, parsed)
            row = reg.add_version(
                self.tab_id,
                prompt_key=key,
                text=parsed["text"],
                parent=str(live.get("id") or "") or None,
                rationale=parsed["rationale"],
                status="shadow",
            )
            from core.tab_agent_review import write_review_request

            write_review_request(
                skill_dir,
                tab_id=self.tab_id,
                version=row,
                parent_text=str(live.get("text") or ""),
                score_row=stat,
                objective=score.get("objective") if isinstance(score.get("objective"), dict) else None,
            )
            created.append(row)
        return {"tab_id": self.tab_id, "created": created, "skipped": skipped, "auto_promote": False, "score": score}


def parse_prompt_proposal(raw: str) -> dict[str, Any] | None:
    import json

    text = (raw or "").strip()
    if text.startswith("```"):
        text = "\n".join(line for line in text.splitlines() if not line.strip().startswith("```")).strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    body = str(data.get("text") or "").strip()
    rationale = str(data.get("rationale") or "").strip() or "Revised for risk-adjusted return. Sample may be too small."
    if len(body) < 40:
        return None
    parsed: dict[str, Any] = {"text": body, "rationale": rationale}
    for field in ("min_avg_dollar_volume", "max_spread_pct", "quality_threshold"):
        if data.get(field) is not None:
            parsed[field] = data.get(field)
    return parsed


def _default_llm(system: str, user: str) -> str:
    from engine_analysis import _call_llm, _load_env

    return _call_llm(user, system, _load_env())
