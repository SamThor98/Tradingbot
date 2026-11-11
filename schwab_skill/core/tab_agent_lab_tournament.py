"""One generation of rival prompts: mutate, critique, validate, score.

The generator receives train scores only. Holdout books are stored beside the
candidate and are not rendered into the next prompt. Debate is one book for
its three prompts. The system tab keeps the missed-return rule.

This module does not write the registry and does not place orders.
"""

from __future__ import annotations

import json
import re
from typing import Any, Callable

from core.tab_agent_contract import anchor_reminder, keeps_contract, missing_anchors
from core.tab_agent_lab_score import (
    DECISION_KEYS,
    LabCase,
    bundle_text,
    offline_decision,
    rank_key,
    score_long_cash,
    score_system_text,
)
from core.tab_agent_objective import ObjectiveConfig

OPERATORS = ("tighten_cash", "smooth_curve", "liquidity", "hold_window", "crossover")
_OPERATOR_LINE = {
    "tighten_cash": " Stay in cash when the setup is weak. LAB_CASH_BELOW=40.",
    "smooth_curve": " Prefer a smooth scale-out over a jagged winner. LAB_CASH_BELOW=55.",
    "liquidity": " Filter on average dollar volume and spread, never market cap. LAB_CASH_BELOW=40.",
    "hold_window": " Hold two to eight weeks and scale out of the long. LAB_CASH_BELOW=30.",
    "crossover": " Merge the stricter cash rule from the stronger parent.",
}
_CRITIQUE = (
    " Failure mode: an extended or illiquid name is cash, not a long."
    " Scale out over two to eight weeks instead of holding for a single spike."
)
_SYSTEM_SHIFTS = {
    "tighten_cash": (0.80, 0.50),
    "smooth_curve": (0.76, 0.48),
    "liquidity": (0.74, 0.46),
    "hold_window": (0.72, 0.44),
}
_HIGH_RE = re.compile(r"HIGH_CONFIDENCE_MIN\s*=\s*(\d+(?:\.\d+)?)")
_LOW_RE = re.compile(r"LOW_CONFIDENCE_MAX\s*=\s*(\d+(?:\.\d+)?)")
_LAB_RE = re.compile(r"LAB_CASH_BELOW\s*=\s*\d+(?:\.\d+)?")

Propose = Callable[..., str | None]
Decide = Callable[..., str | None]


def public_train_rows(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Fields the next generator is allowed to see. Holdout numbers stay out."""
    rows: list[dict[str, Any]] = []
    for cand in candidates:
        if any("holdout" in str(key).lower() for key in cand):
            raise RuntimeError("Generator feedback contained a holdout field.")
        if cand.get("dropped"):
            continue
        train = cand.get("train") if isinstance(cand.get("train"), dict) else {}
        row = {
            "id": cand.get("id"),
            "operator": cand.get("operator"),
            "prompt_key": cand.get("prompt_key"),
            "train_sortino": train.get("sortino_adj"),
            "train_max_drawdown": train.get("max_drawdown"),
            "train_smoothness": train.get("smoothness"),
            "train_n": train.get("n"),
        }
        blob = json.dumps(row, default=str).lower()
        if "holdout" in blob:
            raise RuntimeError("Generator feedback contained a holdout field.")
        rows.append(row)
    return rows


def generator_user(
    *,
    tab: str,
    prompt_key: str,
    operator: str,
    parent_text: str,
    partner_text: str | None,
    history: list[dict[str, Any]],
) -> str:
    """The improviser prompt. ``history`` must already be train-only rows."""
    feedback = public_train_rows(
        [
            {
                "id": row.get("id"),
                "operator": row.get("operator"),
                "prompt_key": row.get("prompt_key"),
                "train": {
                    "sortino_adj": row.get("train_sortino"),
                    "max_drawdown": row.get("train_max_drawdown"),
                    "smoothness": row.get("train_smoothness"),
                    "n": row.get("train_n"),
                },
            }
            for row in history
        ]
    )
    lines = [
        f"Tab: {tab}",
        f"Prompt key: {prompt_key}",
        f"Operator: {operator}",
        "Objective: higher Sortino of long-only scale-out returns, then a smoother equity curve",
        "and a shallower max drawdown. Long only. Holds run two to eight weeks and scale out.",
        "Filter on liquidity, not market cap. Cash is a valid 0% outcome.",
        "You do not place orders.",
        anchor_reminder(tab, prompt_key),
        "Train scores from earlier candidates. Later dates are sealed and are not listed here:",
        json.dumps(feedback, default=str),
        "",
        "Current prompt:",
        parent_text,
    ]
    if partner_text:
        lines.extend(["", "Second parent to cross with:", partner_text])
    lines.append('Return one JSON object with keys "text" and "rationale". Keep every required token.')
    return "\n".join(lines)


def critic_user(draft: str, *, tab: str, prompt_key: str) -> str:
    return "\n".join(
        [
            "Attack this prompt. Name the failure mode that hurts Sortino, deepens drawdown,",
            "or makes the equity curve jagged. Then revise the prompt.",
            "Long only. Two to eight week holds. Scale out. Liquidity, not market cap.",
            "Do not place orders.",
            anchor_reminder(tab, prompt_key),
            'Return one JSON object with keys "text" and "rationale".',
            "",
            draft,
        ]
    )


def assert_generator_blind(user: str, sealed_marker: str) -> None:
    """Refuse to send a user message that contains a sealed holdout marker."""
    if sealed_marker and sealed_marker in user:
        raise RuntimeError("Generator prompt included a holdout score.")


def plan_specs(
    *,
    tab: str,
    generation: int,
    count: int,
    live_texts: dict[str, str],
    parents: list[dict[str, Any]],
    rng: Any,
) -> list[dict[str, Any]]:
    """Diverse operators. Later generations cross the current train leaders."""
    keys = [key for key in DECISION_KEYS.get(tab, ()) if key in live_texts]
    if not keys:
        return []
    ranked = sorted(
        [row for row in parents if not row.get("dropped") and isinstance(row.get("train"), dict)],
        key=lambda row: rank_key(row["train"]),
        reverse=True,
    )
    specs: list[dict[str, Any]] = []
    for index in range(count):
        operator = OPERATORS[index % len(OPERATORS)]
        if generation > 0 and ranked and operator == "crossover":
            operator = "crossover"
        elif generation == 0 and operator == "crossover":
            operator = OPERATORS[index % (len(OPERATORS) - 1)]
        key = keys[index % len(keys)]
        partner_id = None
        if operator == "crossover" and ranked:
            partner_id = ranked[index % len(ranked)].get("id")
        specs.append(
            {
                "id": f"{tab}-g{generation}-c{index}-{operator}",
                "tab": tab,
                "generation": generation,
                "operator": operator,
                "prompt_key": "combination" if tab == "debate" else key,
                "focus_key": key,
                "partner_id": partner_id,
            }
        )
    _ = rng  # reserved so a future stochastic operator stays seeded by the caller
    return specs


def offline_revise(
    tab: str,
    text: str,
    operator: str,
    partner_text: str | None = None,
) -> str:
    """Append a mutation and a critique sentence. Anchors already in ``text`` stay."""
    revised = text
    if tab == "system":
        revised = _revise_system(text, operator, partner_text)
    else:
        line = _OPERATOR_LINE[operator]
        if operator == "crossover" and partner_text:
            borrowed = _LAB_RE.search(partner_text)
            if borrowed:
                line = " Crossover kept the stronger cash rule. " + borrowed.group(0) + "."
        if line.strip() not in revised:
            revised = revised.rstrip() + line
    if _CRITIQUE.strip() not in revised:
        revised = revised.rstrip() + _CRITIQUE
    return revised


def _revise_system(text: str, operator: str, partner_text: str | None) -> str:
    if operator == "crossover" and partner_text:
        high = _mean_match(_HIGH_RE, text, partner_text, 0.70)
        low = _mean_match(_LOW_RE, text, partner_text, 0.40)
        return _set_thresholds(text, high, low)
    pair = _SYSTEM_SHIFTS.get(operator)
    if pair is None:
        return text
    return _set_thresholds(text, pair[0], pair[1])


def _mean_match(pattern: re.Pattern[str], left: str, right: str, default: float) -> float:
    values = []
    for body in (left, right):
        match = pattern.search(body or "")
        if match:
            values.append(float(match.group(1)))
    if not values:
        return default
    return round(sum(values) / len(values), 2)


def _set_thresholds(text: str, high: float, low: float) -> str:
    revised = _HIGH_RE.sub(f"HIGH_CONFIDENCE_MIN={high:.2f}", text, count=1)
    return _LOW_RE.sub(f"LOW_CONFIDENCE_MAX={low:.2f}", revised, count=1)


def texts_for_spec(
    spec: dict[str, Any],
    live_texts: dict[str, str],
    parents: list[dict[str, Any]],
    propose: Propose,
    history: list[dict[str, Any]],
) -> dict[str, str] | None:
    """Build the candidate's prompt map. None when the contract does not survive."""
    tab = str(spec["tab"])
    keys = DECISION_KEYS[tab]
    partner = _partner_texts(parents, spec.get("partner_id"))
    revised: dict[str, str] = {}
    focus = str(spec.get("focus_key") or keys[0])
    targets = keys if tab == "debate" else (focus,)
    for key in keys:
        parent_text = str(live_texts.get(key) or "")
        if key not in targets:
            revised[key] = parent_text
            continue
        partner_text = None if not partner else str(partner.get(key) or "")
        try:
            text = propose(
                tab=tab,
                prompt_key=key,
                parent_text=parent_text,
                partner_text=partner_text,
                operator=str(spec["operator"]),
                history=history,
            )
        except Exception:
            return None
        if not text:
            return None
        if not keeps_contract(tab, key, text):
            return None
        revised[key] = text
    return revised


def _partner_texts(parents: list[dict[str, Any]], partner_id: Any) -> dict[str, str] | None:
    if not partner_id:
        return None
    for row in parents:
        if row.get("id") == partner_id and isinstance(row.get("texts"), dict):
            return {str(key): str(value) for key, value in row["texts"].items()}
    return None


def score_texts(
    tab: str,
    texts: dict[str, str],
    cases: list[LabCase],
    *,
    config: ObjectiveConfig,
    live_texts: dict[str, str],
    decide: Decide,
    drift_weight: float,
    skill_dir: Any = None,
) -> dict[str, Any]:
    """Score one candidate on one window. The decider does not receive returns."""
    keys = DECISION_KEYS[tab]
    live_blob = bundle_text(live_texts, keys)
    blob = bundle_text(texts, keys)
    if tab == "system":
        key = keys[0]
        return score_system_text(
            cases,
            texts.get(key, ""),
            config=config,
            live_text=live_texts.get(key, ""),
            drift_weight=drift_weight,
            skill_dir=skill_dir,
        )
    decisions: list[str] = []
    kept: list[LabCase] = []
    for case in cases:
        try:
            side = decide(tab=tab, texts=texts, case=case)
        except Exception:
            side = None
        if side not in {"long", "cash"}:
            continue
        decisions.append(str(side))
        kept.append(case)
    return score_long_cash(
        kept,
        decisions,
        config=config,
        text=blob,
        live_text=live_blob,
        drift_weight=drift_weight,
    )


def run_generation(
    *,
    tab: str,
    generation: int,
    count: int,
    live_texts: dict[str, str],
    parents: list[dict[str, Any]],
    train_cases: list[LabCase],
    holdout_cases: list[LabCase],
    config: ObjectiveConfig,
    propose: Propose,
    decide: Decide,
    drift_weight: float,
    rng: Any,
    skill_dir: Any = None,
    workers: int = 1,
) -> list[dict[str, Any]]:
    """Score ``count`` rivals. Holdout books are sealed under ``sealed``."""
    history = public_train_rows(parents)
    specs = plan_specs(
        tab=tab,
        generation=generation,
        count=count,
        live_texts=live_texts,
        parents=parents,
        rng=rng,
    )
    if workers <= 1 or len(specs) <= 1:
        return [
            _one_candidate(
                spec,
                live_texts=live_texts,
                parents=parents,
                history=history,
                train_cases=train_cases,
                holdout_cases=holdout_cases,
                config=config,
                propose=propose,
                decide=decide,
                drift_weight=drift_weight,
                skill_dir=skill_dir,
            )
            for spec in specs
        ]
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        return list(
            pool.map(
                lambda spec: _one_candidate(
                    spec,
                    live_texts=live_texts,
                    parents=parents,
                    history=history,
                    train_cases=train_cases,
                    holdout_cases=holdout_cases,
                    config=config,
                    propose=propose,
                    decide=decide,
                    drift_weight=drift_weight,
                    skill_dir=skill_dir,
                ),
                specs,
            )
        )


def _one_candidate(
    spec: dict[str, Any],
    *,
    live_texts: dict[str, str],
    parents: list[dict[str, Any]],
    history: list[dict[str, Any]],
    train_cases: list[LabCase],
    holdout_cases: list[LabCase],
    config: ObjectiveConfig,
    propose: Propose,
    decide: Decide,
    drift_weight: float,
    skill_dir: Any,
) -> dict[str, Any]:
    tab = str(spec["tab"])
    user = generator_user(
        tab=tab,
        prompt_key=str(spec["focus_key"]),
        operator=str(spec["operator"]),
        parent_text=str(live_texts.get(spec["focus_key"]) or ""),
        partner_text=None,
        history=history,
    )
    base = {
        "id": spec["id"],
        "tab": tab,
        "generation": spec["generation"],
        "operator": spec["operator"],
        "prompt_key": spec["prompt_key"],
        "focus_key": spec["focus_key"],
        "generator_user": user,
    }
    texts = texts_for_spec(spec, live_texts, parents, propose, history)
    if texts is None:
        missing = _missing_for_live(tab, live_texts)
        base.update({"dropped": "contract", "missing": missing, "texts": {}, "train": None, "sealed": None})
        return base
    train = score_texts(
        tab,
        texts,
        train_cases,
        config=config,
        live_texts=live_texts,
        decide=decide,
        drift_weight=drift_weight,
        skill_dir=skill_dir,
    )
    sealed = score_texts(
        tab,
        texts,
        holdout_cases,
        config=config,
        live_texts=live_texts,
        decide=decide,
        drift_weight=drift_weight,
        skill_dir=skill_dir,
    )
    base.update({"dropped": None, "texts": texts, "train": _brief(train), "sealed": _brief(sealed)})
    return base


def _missing_for_live(tab: str, live_texts: dict[str, str]) -> str:
    parts: list[str] = []
    for key, text in live_texts.items():
        missing = missing_anchors(tab, key, text)
        if missing:
            parts.append(f"{key}: {', '.join(missing)}")
    return "; ".join(parts)


def _brief(book: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "n",
        "n_longs",
        "n_skips",
        "sortino",
        "sortino_adj",
        "sortino_method",
        "primary_unbounded",
        "max_drawdown",
        "smoothness",
        "win_rate",
        "penalty",
        "avg_realized_return_pct",
        "high_confidence_min",
        "low_confidence_max",
    )
    return {key: book.get(key) for key in keys if key in book or book.get(key) is not None}


def offline_propose(
    *,
    tab: str,
    prompt_key: str,
    parent_text: str,
    partner_text: str | None,
    operator: str,
    history: list[dict[str, Any]],
) -> str:
    """Local revision. ``history`` is checked so a holdout leak fails the candidate."""
    public_train_rows(
        [
            {
                "id": row.get("id"),
                "operator": row.get("operator"),
                "prompt_key": row.get("prompt_key"),
                "train": {
                    "sortino_adj": row.get("train_sortino"),
                    "max_drawdown": row.get("train_max_drawdown"),
                    "smoothness": row.get("train_smoothness"),
                    "n": row.get("train_n"),
                },
            }
            for row in history
        ]
    )
    _ = prompt_key
    return offline_revise(tab, parent_text, operator, partner_text)


def offline_decide(*, tab: str, texts: dict[str, str], case: LabCase) -> str:
    _ = tab
    return offline_decision(bundle_text(texts, tuple(texts)), case)


def select_winner(
    candidates: list[dict[str, Any]],
    live_train: dict[str, Any],
    live_holdout: dict[str, Any],
    *,
    margin: float,
    min_train: int,
    min_holdout: int,
    drawdown_limit: float,
    min_participation: float = 0.60,
    min_longs: int = 8,
) -> tuple[dict[str, Any] | None, dict[str, int]]:
    """Best gated candidate by train Sortino, smoothness, then drawdown."""
    from core.tab_agent_lab_score import holdout_gate

    failures: dict[str, int] = {}
    winners: list[dict[str, Any]] = []
    for cand in candidates:
        if cand.get("dropped") or not isinstance(cand.get("train"), dict) or not isinstance(cand.get("sealed"), dict):
            failures["dropped"] = failures.get("dropped", 0) + 1
            continue
        ok, reason = holdout_gate(
            cand["train"],
            cand["sealed"],
            live_train,
            live_holdout,
            margin=margin,
            min_train=min_train,
            min_holdout=min_holdout,
            drawdown_limit=drawdown_limit,
            min_participation=min_participation,
            min_longs=min_longs,
        )
        cand["gate"] = reason
        if not ok:
            failures[reason] = failures.get(reason, 0) + 1
            continue
        winners.append(cand)
    if not winners:
        return None, failures
    winners.sort(key=lambda row: rank_key(row["train"]), reverse=True)
    return winners[0], failures
