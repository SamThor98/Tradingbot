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
from core.tab_agent_contract import KEEP_CONTRACT, anchor_reminder, contract_skip, keeps_contract
from core.tab_agent_objective import OBJECTIVE_ID, compare_books, evaluate_book, load_objective

LOG = logging.getLogger(__name__)

# A high committee score is the positive call. A low score is the pass.
ENDORSE_AT = 60.0
PASS_BELOW = 50.0
DEBATE_PROMPT_KEYS = ("system", "round1", "round2")


def combination_id(prompt_versions: Any, quality_threshold: float | None = None) -> str:
    """Stable id for one debate.

    The key is the set of prompt versions. A quality threshold is part of the
    scored combination. Replay model calls omit it, because the seat scores do
    not depend on the bar.
    """
    versions = prompt_versions if isinstance(prompt_versions, dict) else {}
    parts = [f"{key}={str(versions.get(key) or 'builtin')}" for key in DEBATE_PROMPT_KEYS]
    if quality_threshold is not None:
        number = float(quality_threshold)
        shown = str(int(number)) if number.is_integer() else f"{number:.1f}"
        parts.append(f"q={shown}")
    return "combo:" + "|".join(parts)


def combination_members(prompt_versions: Any) -> dict[str, str]:
    versions = prompt_versions if isinstance(prompt_versions, dict) else {}
    return {key: str(versions.get(key) or "builtin") for key in DEBATE_PROMPT_KEYS}


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
) + KEEP_CONTRACT


def live_prompt_text(skill_dir: Path | str, key: str) -> str | None:
    from config import get_tab_agent_debate_enabled

    if not get_tab_agent_debate_enabled(Path(skill_dir)):
        return None
    row = DebateTabAgent().registry(Path(skill_dir)).live_version("debate", key)
    text = str(row.get("text") or "").strip() if row else ""
    if text and keeps_contract("debate", key, text):
        return text
    if text:
        LOG.warning("Debate prompt %s dropped its contract; using the built-in text", key)
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
        shadow_text = str(shadow.get("text") or "").strip() if shadow else ""
        live_text = str(live.get("text") or "").strip() if live else ""
        if shadow_text and keeps_contract("debate", key, shadow_text):
            texts[key] = shadow_text
            found = True
        elif live_text and keeps_contract("debate", key, live_text):
            texts[key] = live_text
        else:
            if shadow_text or live_text:
                LOG.warning("Debate prompt %s dropped its contract; using the built-in text", key)
            texts[key] = fallback
    if not found:
        return None
    return texts["system"], texts["round1"], texts["round2"]


class DebateTabAgent(TabAgent):
    tab_id = "debate"
    metric_name = OBJECTIVE_ID

    def prompt_catalog(self) -> dict[str, str]:
        from core.debate_committee import prompt_catalog
        from core.debate_quality import QUALITY_PROMPT_KEY, QUALITY_TEXT

        catalog = dict(prompt_catalog())
        catalog[QUALITY_PROMPT_KEY] = QUALITY_TEXT
        return catalog

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
        from core.debate_quality import load_debate_cases, sweep_thresholds

        live_ids, shadow_ids, has_shadow = _live_and_shadow_ids(reg)
        live_bar = _bar_for(reg, "live", config)
        shadow_bar = _bar_for(reg, "shadow", config)
        if not has_shadow:
            shadow_bar = live_bar
        grouped: dict[str, list[dict[str, Any]]] = {}
        for event in events:
            members = combination_members(event.get("prompt_versions"))
            grouped.setdefault(combination_id(members), []).append(event)
        live_events = grouped.get(combination_id(live_ids), [])
        live_key = combination_id(live_ids, live_bar)
        stats = [
            _combination_book(
                live_key,
                "live",
                live_ids,
                live_events,
                packets,
                config,
                skill_dir=skill_dir,
                since_ts=since_ts,
                threshold=live_bar,
            )
        ]
        comparisons = []
        shadow_key = combination_id(shadow_ids, shadow_bar)
        quality_shadow = next(
            (row for row in reg.versions(self.tab_id, "quality") if row.get("status") == "shadow"),
            None,
        )
        threshold_differs = abs(float(shadow_bar) - float(live_bar)) > 1e-9
        if (has_shadow or threshold_differs) and shadow_key != live_key:
            members = [shadow_ids[key] for key in DEBATE_PROMPT_KEYS if shadow_ids[key] != live_ids[key]]
            if quality_shadow is not None and threshold_differs:
                members.append(str(quality_shadow.get("id") or ""))
            shadow_events = grouped.get(combination_id(shadow_ids), [])
            shadow_book = _combination_book(
                shadow_key,
                "shadow",
                shadow_ids,
                shadow_events,
                packets,
                config,
                skill_dir=skill_dir,
                since_ts=since_ts,
                parent=live_key,
                threshold=shadow_bar,
            )
            stats.append(shadow_book)
            if threshold_differs and not has_shadow:
                edge = _threshold_edge(skill_dir, live_ids, live_bar, shadow_bar, config)
            else:
                edge = compare_books(shadow_book, stats[0], config=config)
            comparisons.append(
                {
                    "version_id": shadow_key,
                    "prompt_key": "combination",
                    "combination": True,
                    "member_version_ids": [item for item in members if item],
                    "baseline_version_id": live_key,
                    "edge_vs_live": edge,
                    "auto_promote": False,
                }
            )
        cases = load_debate_cases(skill_dir, live_ids)
        sweep = sweep_thresholds(cases, config, live_threshold=live_bar)
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
                "quality_threshold": live_bar,
                "quality_shadow_threshold": None if not threshold_differs else shadow_bar,
                "quality_sweep": sweep,
                "join_note": (
                    "One debate combination is the system, round-1, and round-2 prompts plus the quality bar. "
                    "A median seat score at or above the bar is a long. Below the bar is cash. "
                    "The bar is tuned on earlier replay cases and accepted only when later cases still improve Sortino, "
                    "with at least 30 taken longs and the drawdown inside the guard. No model call is required to re-score it."
                ),
            },
            skill_dir,
            self.tab_id,
            config,
        )

    def status(self, skill_dir: Path) -> dict[str, Any]:
        """One Debate row for the live combination, plus its shadow combination."""
        reg = self.registry(skill_dir)
        live_ids, shadow_ids, has_shadow = _live_and_shadow_ids(reg)
        config = load_objective(skill_dir)
        live_bar = _bar_for(reg, "live", config)
        shadow_bar = _bar_for(reg, "shadow", config)
        live_key = combination_id(live_ids, live_bar)
        shadow_key = combination_id(shadow_ids, shadow_bar)
        shadow = None
        if shadow_key != live_key:
            shadow = {
                "id": shadow_key,
                "prompt_key": "combination",
                "status": "shadow",
                "prompt_versions": shadow_ids,
            }
        return {
            "tab_id": self.tab_id,
            "metric": self.metric_name,
            "objective": load_objective(skill_dir).as_dict(),
            "prompts": [
                {
                    "prompt_key": "combination",
                    "combination": True,
                    "live": {
                        "id": live_key,
                        "prompt_key": "combination",
                        "status": "live",
                        "prompt_versions": live_ids,
                    },
                    "pending": [],
                    "shadow": shadow,
                    "retired": [],
                }
            ],
        }

    def approve(self, skill_dir: Path, version_id: str) -> dict[str, Any]:
        row = super().approve(skill_dir, version_id)
        from core.debate_quality import sync_quality_policy

        sync_quality_policy(skill_dir)
        return row

    def rollback(self, skill_dir: Path, version_id: str) -> dict[str, Any]:
        row = super().rollback(skill_dir, version_id)
        from core.debate_quality import sync_quality_policy

        sync_quality_policy(skill_dir)
        return row

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
        keys = [prompt_key] if prompt_key else [key for key in catalog if key != "quality"]
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
            stat = next(
                (
                    row
                    for row in score.get("versions") or []
                    if row.get("status") == "live" and row.get("prompt_key") == "combination"
                ),
                {},
            )
            user = (
                f"Prompt key: {key}\n"
                f"Objective: {score.get('winner_rule')}\n"
                f"Endorsed book n={stat.get('n')} {stat.get('primary_metric')}={stat.get('primary_score')} "
                f"max_drawdown={stat.get('max_drawdown')}\n"
                f"Pass counterfactual n={(stat.get('pass_book') or {}).get('n')} "
                f"avg_return_pct={(stat.get('pass_book') or {}).get('avg_realized_return_pct')}\n"
                f"{anchor_reminder(self.tab_id, key)}\n\n"
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
            blocked = contract_skip(self.tab_id, key, parsed["text"])
            if blocked:
                skipped.append(blocked)
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


def _live_and_shadow_ids(reg: Any) -> tuple[dict[str, str], dict[str, str], bool]:
    live_ids: dict[str, str] = {}
    shadow_ids: dict[str, str] = {}
    has_shadow = False
    for key in DEBATE_PROMPT_KEYS:
        live = reg.live_version("debate", key)
        live_id = str(live["id"]) if live else "builtin"
        live_ids[key] = live_id
        shadow = next((row for row in reg.versions("debate", key) if row.get("status") == "shadow"), None)
        if shadow is not None:
            shadow_ids[key] = str(shadow["id"])
            has_shadow = True
        else:
            shadow_ids[key] = live_id
    return live_ids, shadow_ids, has_shadow


def _bar_for(reg: Any, role: str, config: Any) -> float:
    from core.debate_quality import clamp_threshold, threshold_from_text

    if role == "shadow":
        row = next((item for item in reg.versions("debate", "quality") if item.get("status") == "shadow"), None)
    else:
        row = reg.live_version("debate", "quality")
    parsed = threshold_from_text(None if row is None else str(row.get("text") or ""))
    if parsed is None:
        parsed = float(config.quality_threshold)
    return clamp_threshold(parsed)


def _threshold_edge(
    skill_dir: Path,
    prompt_versions: dict[str, str],
    live_bar: float,
    shadow_bar: float,
    config: Any,
) -> str:
    from core.debate_quality import load_debate_cases, sweep_thresholds

    report = sweep_thresholds(load_debate_cases(skill_dir, prompt_versions), config, live_threshold=live_bar)
    chosen = float(report.get("chosen_threshold") or live_bar)
    if report.get("accepted") and abs(chosen - float(shadow_bar)) < 1e-6:
        return "better_than_live"
    reason = str(report.get("reason") or "no_separation")
    if reason in {"drawdown_worse", "insufficient_sample"}:
        return reason
    return "no_separation"


def _combination_book(
    combo_id: str,
    status: str,
    prompt_versions: dict[str, str],
    events: list[dict[str, Any]],
    packets: list[dict[str, Any]],
    config: Any,
    *,
    skill_dir: Path | None = None,
    since_ts: str | None = None,
    parent: str | None = None,
    threshold: float | None = None,
) -> dict[str, Any]:
    """Score every call of one prompt-version set as a single book."""
    endorsed: list[float] = []
    endorsed_dates: list[str] = []
    decisions: list[str] = []
    passes: list[float] = []
    passes_dates: list[str] = []
    seen: set[tuple[str, str]] = set()
    bar = float(threshold if threshold is not None else config.quality_threshold)
    for event in events:
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
        if score >= bar:
            endorsed.append(ret)
            endorsed_dates.append(day)
            decisions.append("long")
        else:
            # Under the bar the book stays in cash and keeps the skipped long as a counterfactual.
            endorsed.append(0.0)
            endorsed_dates.append(day)
            decisions.append("cash")
            passes.append(ret)
            passes_dates.append(day)
    from core.debate_quality import load_debate_cases
    from core.tab_agent_replay import decision_flags

    replay_cases = [] if skill_dir is None else load_debate_cases(skill_dir, prompt_versions)
    replay_returns: list[float] = []
    replay_dates: list[str] = []
    replay_decisions: list[str] = []
    for case in replay_cases:
        key = (str(case["ticker"]), str(case["as_of"])[:10])
        if key in seen:
            continue
        seen.add(key)
        if float(case["debate_score"]) >= bar:
            replay_returns.append(float(case["counterfactual_return_pct"]))
            replay_decisions.append("long")
        else:
            replay_returns.append(0.0)
            replay_decisions.append("cash")
            passes.append(float(case["counterfactual_return_pct"]))
            passes_dates.append(str(case["as_of"]))
        replay_dates.append(str(case["as_of"]))
    from core.tab_agent_replay import attach_replay_combination

    legacy = attach_replay_combination(
        [],
        [],
        skill_dir=skill_dir,
        combination_id=combination_id(prompt_versions),
        member_ids=set(prompt_versions.values()),
        weight=config.replay_weight,
        since_ts=since_ts,
        live_decisions=[],
    )
    legacy_returns = list(legacy.returns)
    legacy_dates = list(legacy.dates)
    legacy_decisions = list(legacy.decisions or [])
    weights = None
    sources = None
    if replay_returns or legacy_returns:
        weights = (
            [1.0] * len(endorsed)
            + [float(config.replay_weight)] * len(replay_returns)
            + [float(config.replay_weight)] * len(legacy_returns)
        )
        sources = ["live"] * len(endorsed) + ["replay"] * len(replay_returns) + ["replay"] * len(legacy_returns)
    book = evaluate_book(
        endorsed + replay_returns + legacy_returns,
        endorsed_dates + replay_dates + legacy_dates,
        config=config,
        weights=weights,
        sources=sources,
        decisions=decisions + replay_decisions + legacy_decisions,
    )
    book.update(decision_flags(decisions + replay_decisions + legacy_decisions))
    book.update(
        {
            "version_id": combo_id,
            "prompt_key": "combination",
            "combination": True,
            "prompt_versions": dict(prompt_versions),
            "quality_threshold": bar,
            "status": status,
            "parent": parent,
            "pass_book": evaluate_book(passes, passes_dates, config=config),
        }
    )
    return book
