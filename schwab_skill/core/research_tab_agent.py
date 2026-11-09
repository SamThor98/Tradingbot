"""Research tab agent over the quick-check verdict prompts.

Scored on the shared 20-day scale-out objective. HOLD OFF is cash (0%).
STRONG SETUP and WATCH use the labeled long return. No orders.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Callable

from core.tab_agent import STATUS_PENDING, TabAgent
from core.tab_agent_objective import OBJECTIVE_ID, compare_books, evaluate_book, load_objective

LOG = logging.getLogger(__name__)

LONG_VERDICTS = {"STRONG SETUP", "WATCH"}
CASH_VERDICTS = {"HOLD OFF"}

IMPROVER_SYSTEM = (
    "You revise one Research quick-check prompt. "
    "The objective is a higher Sortino or Sharpe of the 20-day scale-out return. "
    "HOLD OFF is cash and scores 0%. Never suggest a short or an option. "
    "Do not penalize market cap. Do not cap the name count. "
    "Keep the token STRONG_SETUP_MIN_SCORE=<number> inside the quality bounds when you edit verdict_rules. "
    "Keep the brace tokens in the rationale template. "
    "You do not place orders. Return JSON with keys text and rationale. "
    "Optional keys: quality_threshold, min_avg_dollar_volume, max_spread_pct. "
    "rationale must say when the sample is too small."
)


class ResearchTabAgent(TabAgent):
    tab_id = "research"
    metric_name = OBJECTIVE_ID

    def prompt_catalog(self) -> dict[str, str]:
        from core.research_quick_check import prompt_catalog

        return prompt_catalog()

    def score(
        self,
        skill_dir: Path,
        *,
        registry: Any = None,
        since_ts: str | None = None,
    ) -> dict[str, Any]:
        from core.decision_packet import load_packets

        reg = registry or self.registry(skill_dir)
        config = load_objective(skill_dir)
        packets = [row for row in load_packets(skill_dir) if _is_quick_check(row)]
        if since_ts:
            packets = [row for row in packets if str(row.get("created_at") or "") >= since_ts]
        versions = reg.versions(self.tab_id)
        stats = [
            _version_book(version, packets, config, skill_dir=skill_dir, since_ts=since_ts) for version in versions
        ]
        by_id = {row["version_id"]: row for row in stats}
        comparisons = []
        for row in stats:
            if row.get("status") == "live":
                continue
            baseline = by_id.get(str(row.get("parent") or ""))
            if baseline is None:
                baseline = next(
                    (
                        item
                        for item in stats
                        if item.get("prompt_key") == row.get("prompt_key") and item.get("status") == "live"
                    ),
                    None,
                )
            comparisons.append(
                {
                    "version_id": row["version_id"],
                    "prompt_key": row.get("prompt_key"),
                    "baseline_version_id": None if baseline is None else baseline.get("version_id"),
                    "edge_vs_live": compare_books(row, baseline, config=config),
                    "auto_promote": False,
                }
            )
        objective = config.as_dict()
        from core.tab_agent_replay import annotate_tab_score

        return annotate_tab_score(
            {
                "tab_id": self.tab_id,
                "objective": objective,
                "objective_id": OBJECTIVE_ID,
                "auto_promote": False,
                "winner_rule": objective["statement"],
                "versions": stats,
                "comparisons": comparisons,
                "join_note": "Quick-check packets join on prompt version. HOLD OFF scores as cash. Rationale is not scored.",
            },
            skill_dir,
            self.tab_id,
            config,
        )

    def propose(
        self,
        skill_dir: Path,
        *,
        registry: Any = None,
        llm: Callable[[str, str], str] | None = None,
        prompt_key: str | None = None,
    ) -> dict[str, Any]:
        from core.today_tab_agent import parse_prompt_proposal

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
                f"Prompt key: {key}\nObjective: {score.get('winner_rule')}\n"
                f"n={stat.get('n')} {stat.get('primary_metric')}={stat.get('primary_score')} "
                f"max_drawdown={stat.get('max_drawdown')}\n\n"
                "Current prompt:\n" + str(live.get("text") or "")
            )
            try:
                raw = caller(IMPROVER_SYSTEM, user)
            except Exception as exc:
                LOG.warning("Research prompt proposal failed for %s: %s", key, exc)
                skipped.append({"prompt_key": key, "reason": "llm_error"})
                continue
            parsed = parse_prompt_proposal(raw or "")
            if parsed is None or parsed["text"].strip() == str(live.get("text") or "").strip():
                skipped.append({"prompt_key": key, "reason": "no_proposal"})
                continue
            if any(
                parsed.get(field) is not None
                for field in ("min_avg_dollar_volume", "max_spread_pct", "quality_threshold")
            ):
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


def _default_llm(system: str, user: str) -> str:
    from engine_analysis import _call_llm, _load_env

    return _call_llm(user, system, _load_env())


def _is_quick_check(packet: dict[str, Any]) -> bool:
    refs = packet.get("refs") if isinstance(packet.get("refs"), dict) else {}
    return refs.get("source") == "quick_check" or packet.get("setup_type") == "quick_check"


def _labeled_return(packet: dict[str, Any], horizon_days: int) -> float | None:
    from core.tab_agent import _packet_return

    parsed = _packet_return(packet, horizon_days=horizon_days)
    if parsed is None:
        return None
    return parsed[1]


def _decision_return(packet: dict[str, Any], verdict: str | None, config: Any) -> tuple[str, float] | None:
    label = str(verdict or "").upper()
    if label in CASH_VERDICTS:
        return str(packet.get("created_at") or "")[:10], 0.0
    if label not in LONG_VERDICTS:
        return None
    ret = _labeled_return(packet, config.horizon_days)
    if ret is None:
        return None
    return str(packet.get("created_at") or "")[:10], ret


def _version_book(
    version: dict[str, Any],
    packets: list[dict[str, Any]],
    config: Any,
    *,
    skill_dir: Path | None = None,
    since_ts: str | None = None,
) -> dict[str, Any]:
    vid = str(version.get("id") or "")
    key = str(version.get("prompt_key") or "")
    from core.tab_agent_replay import non_decision_book

    if key == "rationale":
        return non_decision_book(version)
    returns: list[float] = []
    dates: list[str] = []
    decisions: list[str] = []
    for packet in packets:
        refs = packet.get("refs") if isinstance(packet.get("refs"), dict) else {}
        if key == "rationale":
            matched = str(refs.get("rationale_version") or "") == vid
            verdict = refs.get("verdict")
        elif str(refs.get("shadow_prompt_version") or "") == vid:
            matched = True
            verdict = refs.get("shadow_verdict")
        else:
            matched = str(refs.get("prompt_version") or "") == vid
            verdict = refs.get("verdict")
        if not matched:
            continue
        parsed = _decision_return(packet, str(verdict or ""), config)
        if parsed is None:
            continue
        dates.append(parsed[0])
        returns.append(parsed[1])
        decisions.append("cash" if str(verdict or "").upper() in CASH_VERDICTS else "long")
    from core.tab_agent_replay import attach_replay, decision_flags

    merged = attach_replay(
        returns,
        dates,
        skill_dir=skill_dir,
        tab_id="research",
        version_id=vid,
        weight=config.replay_weight,
        since_ts=since_ts,
        live_decisions=decisions,
    )
    book = evaluate_book(
        merged.returns,
        merged.dates,
        config=config,
        weights=merged.weights,
        sources=merged.sources,
        decisions=merged.decisions,
    )
    book.update(decision_flags(merged.decisions))
    book.update(
        {
            "version_id": vid,
            "prompt_key": key,
            "status": version.get("status"),
            "parent": version.get("parent"),
        }
    )
    return book
