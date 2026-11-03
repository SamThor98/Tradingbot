"""Debate-committee tab agent. Proposals stay pending until an explicit approve."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Callable

from core.tab_agent import STATUS_PENDING, PromptRegistry, TabAgent

LOG = logging.getLogger(__name__)

IMPROVER_SYSTEM = (
    "You revise one prompt for an equity-research debate committee. "
    "You do not place orders or change risk limits. "
    "Return exactly one JSON object with keys text and rationale. "
    "text is the full replacement prompt and must keep the {ticker} and {context} placeholders "
    "when the current prompt has them. "
    "rationale must say the sample is too small when the labeled count is below the minimum."
)


class DebateTabAgent(TabAgent):
    tab_id = "debate"
    metric_name = "debate_10d"

    def prompt_catalog(self) -> dict[str, str]:
        from core.debate_committee import prompt_catalog

        return prompt_catalog()

    def score(self, skill_dir: Path, *, registry: PromptRegistry | None = None) -> dict[str, Any]:
        from config import get_decision_packet_horizon_days, get_tab_agent_min_samples
        from core.debate_committee import load_debate_log
        from core.debate_grade import grade_debate_log
        from core.decision_packet import load_packets

        reg = registry or self.registry(skill_dir)
        report = grade_debate_log(
            versions=reg.versions(self.tab_id),
            packets=load_packets(skill_dir),
            events=load_debate_log(skill_dir),
            min_samples=get_tab_agent_min_samples(skill_dir),
            horizon_days=get_decision_packet_horizon_days(skill_dir),
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
        from core.debate_committee import load_debate_log, prompt_catalog
        from core.debate_grade import grade_debate_log
        from core.decision_packet import load_packets
        from core.today_tab_agent import parse_prompt_proposal

        reg = registry or self.registry(skill_dir)
        caller = llm or _default_llm
        packets = load_packets(skill_dir)
        events = load_debate_log(skill_dir)
        score = grade_debate_log(
            versions=reg.versions(self.tab_id),
            packets=packets,
            events=events,
            min_samples=_min_samples(skill_dir),
            horizon_days=_horizon(skill_dir),
        )
        created: list[dict[str, Any]] = []
        skipped: list[dict[str, str]] = []
        catalog = prompt_catalog()
        keys = [prompt_key] if prompt_key else list(catalog)
        misses = _wrong_lines(events, packets, horizon_days=_horizon(skill_dir))
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
            stat = next((row for row in score.get("versions") or [] if row.get("version_id") == live.get("id")), {})
            user = (
                f"Prompt key: {key}\n"
                f"Approve-call samples: {stat.get('n', 0)} (minimum {score.get('min_samples')}).\n"
                f"Approve win rate: {stat.get('win_rate')} CI95: {stat.get('win_rate_ci95')}\n"
                f"Average realized return %: {stat.get('avg_realized_return_pct')}\n"
                "Wrong calls (approve that lost, or pass that would have won):\n"
                + ("\n".join(misses) if misses else "(none yet)")
                + "\n\nCurrent prompt:\n"
                + str(live.get("text") or "")
            )
            try:
                raw = caller(IMPROVER_SYSTEM, user)
            except Exception as exc:
                LOG.warning("Debate prompt proposal failed for %s: %s", key, exc)
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


def _default_llm(system: str, user: str) -> str:
    from engine_analysis import _call_llm, _load_env

    return _call_llm(user, system, _load_env())


def _min_samples(skill_dir: Path) -> int:
    from config import get_tab_agent_min_samples

    return get_tab_agent_min_samples(skill_dir)


def _horizon(skill_dir: Path) -> int:
    from config import get_decision_packet_horizon_days

    return get_decision_packet_horizon_days(skill_dir)


def _wrong_lines(events: list[dict[str, Any]], packets: list[dict[str, Any]], *, horizon_days: int) -> list[str]:
    from core.debate_grade import _indexes, _join, _outcome

    by_id, by_day = _indexes(packets)
    lines: list[str] = []
    for event in events:
        if not isinstance(event, dict):
            continue
        decision = str(event.get("decision") or "").lower()
        packet, _how = _join(event, by_id, by_day)
        if packet is None:
            continue
        parsed = _outcome(packet, horizon_days=horizon_days)
        if parsed is None:
            continue
        label, ret = parsed
        wrong = (decision == "approve" and label == "loss") or (decision == "pass" and label == "win")
        if not wrong:
            continue
        lines.append(f"{event.get('ticker')} {event.get('date')} decision={decision} outcome={label} return_pct={ret}")
        if len(lines) >= 8:
            break
    return lines
