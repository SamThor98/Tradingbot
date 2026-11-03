"""Debate-committee tab agent over the real ``_SYSTEM`` and round headers.

The committee still writes ``debate_score`` and does not change ``qual_raw``
or ``signal_score``. This agent versions the analysis prompts, scores the
logged calls on the shared risk-adjusted objective, and lets the governor
promote or roll them back. It never places orders.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Callable

from core.tab_agent import STATUS_PENDING, TabAgent
from core.tab_agent_objective import OBJECTIVE_ID, compare_books, evaluate_book, load_objective

LOG = logging.getLogger(__name__)

# A high committee score is the positive call. A low score is the pass.
ENDORSE_AT = 60.0
PASS_BELOW = 50.0

IMPROVER_SYSTEM = (
    "You revise one debate-committee analysis prompt. "
    "The objective is a higher Sortino or Sharpe of the 20-day scale-out return "
    "(half off at 2R, the rest trailed) and a smoother equity curve. Win rate is not the goal. "
    "Long-only equities. Never suggest a short, inverse ETF, or option. "
    "A low score means no trade / stay in cash, and cash scores as 0% because it avoids drawdown. "
    "Do not penalize market cap. Liquidity is average dollar volume and spread, tuned only inside the stated bounds. "
    "Do not cap the number of names. Optimize the quality threshold, not a top-N. "
    "Clean chart and price action, fundamentals, sector and market strength, and a tight stop with good reward-to-risk "
    "are high weight. Catalysts and news are the lowest-priority tiebreaker. "
    "You do not place orders or change position size. "
    "Return one JSON object with keys text and rationale. "
    "Optional keys: min_avg_dollar_volume, max_spread_pct, quality_threshold. "
    "Keep the JSON reply contract if you are revising the system prompt. "
    "rationale must admit when the sample is too small."
)


def live_prompt_text(skill_dir: Path | str, key: str) -> str | None:
    from config import get_tab_agent_debate_enabled

    if not get_tab_agent_debate_enabled(Path(skill_dir)):
        return None
    row = DebateTabAgent().registry(Path(skill_dir)).live_version("debate", key)
    if row and str(row.get("text") or "").strip():
        return str(row["text"])
    return None


def live_version_ids(skill_dir: Path | str) -> dict[str, str]:
    reg = DebateTabAgent().registry(Path(skill_dir))
    out: dict[str, str] = {}
    for key in ("system", "round1", "round2"):
        row = reg.live_version("debate", key)
        out[key] = str(row["id"]) if row else "builtin"
    return out


def shadow_headers(skill_dir: Path | str) -> tuple[str, str, str] | None:
    """Shadow system and round headers, or None when no shadow candidate exists."""
    from core.debate_committee import prompt_catalog

    reg = DebateTabAgent().registry(Path(skill_dir))
    found = False
    texts: dict[str, str] = {}
    for key, fallback in prompt_catalog().items():
        shadow = next((row for row in reg.versions("debate", key) if row.get("status") == "shadow"), None)
        live = reg.live_version("debate", key)
        if shadow and str(shadow.get("text") or "").strip():
            texts[key] = str(shadow["text"])
            found = True
        elif live and str(live.get("text") or "").strip():
            texts[key] = str(live["text"])
        else:
            texts[key] = fallback
    if not found:
        return None
    return texts["system"], texts["round1"], texts["round2"]


class DebateTabAgent(TabAgent):
    tab_id = "debate"
    metric_name = OBJECTIVE_ID

    def prompt_catalog(self) -> dict[str, str]:
        from core.debate_committee import prompt_catalog

        return prompt_catalog()

    def score(
        self,
        skill_dir: Path,
        *,
        registry: Any = None,
        since_ts: str | None = None,
    ) -> dict[str, Any]:
        from core.debate_committee import load_debate_log
        from core.decision_packet import load_packets

        reg = registry or self.registry(skill_dir)
        config = load_objective(skill_dir)
        events = _filter_since(load_debate_log(skill_dir), since_ts)
        packets = load_packets(skill_dir)
        versions = reg.versions(self.tab_id)
        stats = [_version_book(version, events, packets, config) for version in versions]
        by_id = {row["version_id"]: row for row in stats}
        comparisons = []
        for row in stats:
            if row.get("status") == "live":
                continue
            baseline = by_id.get(str(row.get("parent") or ""))
            if baseline is None:
                baseline = next(
                    (item for item in stats if item.get("prompt_key") == row.get("prompt_key") and item.get("status") == "live"),
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
        return {
            "tab_id": self.tab_id,
            "objective": objective,
            "objective_id": OBJECTIVE_ID,
            "auto_promote": False,
            "winner_rule": objective["statement"],
            "versions": stats,
            "comparisons": comparisons,
            "join_note": (
                "Committee rows join a decision packet on packet_id when present, otherwise ticker plus as_of date. "
                "Scores at or above 60 are the endorsed book. Scores below 50 are the pass counterfactual."
            ),
        }

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
                f"Prompt key: {key}\n"
                f"Objective: {score.get('winner_rule')}\n"
                f"Endorsed book n={stat.get('n')} {stat.get('primary_metric')}={stat.get('primary_score')} "
                f"max_drawdown={stat.get('max_drawdown')}\n"
                f"Pass counterfactual n={(stat.get('pass_book') or {}).get('n')} "
                f"avg_return_pct={(stat.get('pass_book') or {}).get('avg_realized_return_pct')}\n\n"
                "Current prompt:\n" + str(live.get("text") or "")
            )
            try:
                raw = caller(IMPROVER_SYSTEM, user)
            except Exception as exc:
                LOG.warning("Debate prompt proposal failed for %s: %s", key, exc)
                skipped.append({"prompt_key": key, "reason": "llm_error"})
                continue
            parsed = parse_prompt_proposal(raw or "")
            if parsed is None or parsed["text"].strip() == str(live.get("text") or "").strip():
                skipped.append({"prompt_key": key, "reason": "no_proposal"})
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


def _default_llm(system: str, user: str) -> str:
    from engine_analysis import _call_llm, _load_env

    return _call_llm(user, system, _load_env())


def _filter_since(events: list[dict[str, Any]], since_ts: str | None) -> list[dict[str, Any]]:
    if not since_ts:
        return events
    return [row for row in events if str(row.get("ts") or "") >= since_ts]


def _joined_return(event: dict[str, Any], packets: list[dict[str, Any]], *, horizon_days: int) -> float | None:
    from core.tab_agent import utc_day

    packet = None
    packet_id = str(event.get("packet_id") or "").strip()
    if packet_id:
        packet = next((row for row in packets if str(row.get("packet_id") or "") == packet_id), None)
    if packet is None:
        ticker = str(event.get("ticker") or "").upper()
        day = str(event.get("as_of") or "")[:10]
        packet = next(
            (
                row
                for row in packets
                if str(row.get("ticker") or "").upper() == ticker and utc_day(row.get("created_at")) == day
            ),
            None,
        )
    if packet is None:
        return None
    outcome = packet.get("outcome") if isinstance(packet.get("outcome"), dict) else {}
    if str(outcome.get("label") or "pending") in {"pending", "unknown", ""}:
        return None
    try:
        horizon = int(outcome.get("horizon_days"))
    except (TypeError, ValueError):
        return None
    if horizon != int(horizon_days):
        return None
    try:
        return float(outcome.get("realized_return_pct"))
    except (TypeError, ValueError):
        return None


def _version_book(version: dict[str, Any], events: list[dict[str, Any]], packets: list[dict[str, Any]], config: Any) -> dict[str, Any]:
    key = str(version.get("prompt_key") or "")
    vid = str(version.get("id") or "")
    endorsed: list[float] = []
    endorsed_dates: list[str] = []
    passes: list[float] = []
    passes_dates: list[str] = []
    seen: set[tuple[str, str]] = set()
    for event in events:
        versions = event.get("prompt_versions") if isinstance(event.get("prompt_versions"), dict) else {}
        if str(versions.get(key) or "") != vid:
            continue
        ticker = str(event.get("ticker") or "").upper()
        day = str(event.get("as_of") or "")[:10]
        if not ticker or len(day) < 10 or (ticker, day) in seen:
            continue
        seen.add((ticker, day))
        ret = _joined_return(event, packets, horizon_days=config.horizon_days)
        if ret is None:
            continue
        try:
            score = float(event.get("debate_score"))
        except (TypeError, ValueError):
            continue
        side = str(event.get("side") or "long").lower()
        if side in {"short", "option", "put", "call", "inverse"}:
            continue
        bar = float(config.quality_threshold)
        if score >= bar:
            endorsed.append(ret)
            endorsed_dates.append(day)
        elif score < PASS_BELOW:
            # The decision was cash. Score 0, and keep the skipped long as a counterfactual.
            endorsed.append(0.0)
            endorsed_dates.append(day)
            passes.append(ret)
            passes_dates.append(day)
    book = evaluate_book(endorsed, endorsed_dates, config=config)
    book.update(
        {
            "version_id": vid,
            "prompt_key": key,
            "status": version.get("status"),
            "parent": version.get("parent"),
            "pass_book": evaluate_book(passes, passes_dates, config=config),
        }
    )
    return book
