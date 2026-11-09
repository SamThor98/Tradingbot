"""Tune the debate quality bar from stored seat scores. No model calls.

The bar is the median seat score a name must clear to be a long. Anything
under it is cash. The sweep trains on earlier cases and keeps a threshold
only when the later cases still improve Sortino, inside the drawdown guard,
with at least 30 taken longs. This module does not place or size orders.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from core.tab_agent_objective import ObjectiveConfig, evaluate_book, load_objective

QUALITY_PROMPT_KEY = "quality"
THRESHOLD_RE = re.compile(r"QUALITY_THRESHOLD\s*=\s*(\d+(?:\.\d+)?)")
TUNE_LOW = 40
TUNE_HIGH = 80
TUNE_STEP = 5
HOLDOUT_FRACTION = 0.30
MIN_TAKEN_LONGS = 30
MIN_HOLDOUT_LIFT = 0.05

QUALITY_TEXT = (
    "QUALITY_THRESHOLD=60\n"
    "Endorse a long only when the median seat score clears this bar. "
    "A lower median stays in cash. Do not cap the number of names. "
    "Do not place orders or change position size."
)


def threshold_from_text(text: str | None) -> float | None:
    match = THRESHOLD_RE.search(str(text or ""))
    if not match:
        return None
    try:
        return float(match.group(1))
    except ValueError:
        return None


def format_threshold(value: float) -> str:
    number = float(value)
    if number.is_integer():
        return str(int(number))
    return f"{number:.1f}"


def quality_text(threshold: float) -> str:
    return QUALITY_TEXT.replace("QUALITY_THRESHOLD=60", f"QUALITY_THRESHOLD={format_threshold(threshold)}", 1)


def clamp_threshold(value: float, *, low: float = TUNE_LOW, high: float = TUNE_HIGH) -> float:
    return max(float(low), min(float(high), float(value)))


def grid(low: float = TUNE_LOW, high: float = TUNE_HIGH, step: int = TUNE_STEP) -> list[float]:
    start = int(clamp_threshold(low))
    stop = int(clamp_threshold(high))
    return [float(point) for point in range(start, stop + 1, max(1, int(step)))]


def load_debate_cases(skill_dir: Path | None, prompt_versions: dict[str, str] | None = None) -> list[dict[str, Any]]:
    """One row per ticker and day, using the stored median and the scale-out return."""
    from core.debate_tab_agent import DEBATE_PROMPT_KEYS, combination_id
    from core.tab_agent_replay import load_samples

    wanted = prompt_versions or {}
    prompt_key = combination_id(wanted) if wanted else ""
    chosen: dict[tuple[str, str], dict[str, Any]] = {}
    for sample in load_samples(skill_dir):
        if str(sample.get("tab_id") or "") != "debate" or sample.get("scored") is False:
            continue
        if not _matches_prompts(sample, wanted, prompt_key, DEBATE_PROMPT_KEYS):
            continue
        try:
            score = float(sample["debate_score"])
            raw = sample.get("counterfactual_return_pct")
            if raw is None and str(sample.get("decision") or "") == "long":
                raw = sample.get("realized_return_pct")
            outcome = float(raw)
        except (KeyError, TypeError, ValueError):
            continue
        ticker = str(sample.get("ticker") or "").upper()
        day = str(sample.get("as_of") or "")[:10]
        if not ticker or len(day) < 10:
            continue
        chosen[(ticker, day)] = {
            "ticker": ticker,
            "as_of": day,
            "debate_score": score,
            "counterfactual_return_pct": outcome,
        }
    return [chosen[key] for key in sorted(chosen)]


def _matches_prompts(sample: dict[str, Any], wanted: dict[str, str], prompt_key: str, keys: tuple[str, ...]) -> bool:
    if not wanted:
        return sample.get("debate_score") is not None
    versions = sample.get("prompt_versions") if isinstance(sample.get("prompt_versions"), dict) else {}
    if versions:
        return all(str(versions.get(key) or "") == str(wanted.get(key) or "") for key in keys)
    version_id = str(sample.get("version_id") or "")
    return version_id == prompt_key or version_id.startswith(prompt_key + "|q=")


def book_at_threshold(cases: list[dict[str, Any]], threshold: float, config: ObjectiveConfig) -> dict[str, Any]:
    """Long when the stored median clears the bar. Cash otherwise. No model call."""
    returns: list[float] = []
    dates: list[str] = []
    decisions: list[str] = []
    for case in cases:
        dates.append(str(case.get("as_of") or ""))
        if float(case["debate_score"]) >= float(threshold):
            returns.append(float(case["counterfactual_return_pct"]))
            decisions.append("long")
        else:
            returns.append(0.0)
            decisions.append("cash")
    book = evaluate_book(returns, dates, config=config, decisions=decisions)
    book["quality_threshold"] = float(threshold)
    return book


def _split(cases: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str | None]:
    dates = sorted({str(case["as_of"]) for case in cases})
    if len(dates) < 2:
        return list(cases), [], None
    cut = int(len(dates) * (1.0 - HOLDOUT_FRACTION))
    cut = min(max(cut, 1), len(dates) - 1)
    split = dates[cut]
    tune = [case for case in cases if str(case["as_of"]) < split]
    hold = [case for case in cases if str(case["as_of"]) >= split]
    if not tune or not hold:
        return list(cases), [], None
    return tune, hold, split


def _within_guard(book: dict[str, Any], config: ObjectiveConfig) -> bool:
    drawdown = book.get("max_drawdown")
    if drawdown is None:
        return False
    return float(drawdown) >= -float(config.drawdown_tolerance)


def sweep_thresholds(
    cases: list[dict[str, Any]],
    config: ObjectiveConfig,
    *,
    live_threshold: float,
) -> dict[str, Any]:
    """Pick a bar on earlier cases and accept it only when later cases agree."""
    live = clamp_threshold(live_threshold)
    tune, hold, split = _split(cases)
    rows: list[dict[str, Any]] = []
    for threshold in grid():
        full = book_at_threshold(cases, threshold, config)
        trained = book_at_threshold(tune, threshold, config)
        held = book_at_threshold(hold, threshold, config) if hold else None
        eligible = int(full.get("n_longs") or 0) >= MIN_TAKEN_LONGS and _within_guard(full, config)
        rows.append(
            {
                "threshold": threshold,
                "sortino": full.get("primary_score"),
                "n": full.get("n"),
                "n_longs": full.get("n_longs"),
                "win_rate": full.get("win_rate"),
                "max_drawdown": full.get("max_drawdown"),
                "tune_sortino": trained.get("primary_score"),
                "tune_unbounded": bool(trained.get("primary_unbounded")),
                "tune_longs": trained.get("n_longs"),
                "holdout_sortino": None if held is None else held.get("primary_score"),
                "holdout_unbounded": False if held is None else bool(held.get("primary_unbounded")),
                "holdout_longs": None if held is None else held.get("n_longs"),
                "holdout_drawdown": None if held is None else held.get("max_drawdown"),
                "eligible": eligible,
            }
        )
    live_row = book_at_threshold(cases, live, config)
    live_hold = book_at_threshold(hold, live, config) if hold else None
    ranked = sorted(
        (row for row in rows if row["eligible"]),
        key=lambda row: (
            1 if row.get("tune_unbounded") else 0,
            float("-inf")
            if row.get("tune_sortino") is None and not row.get("tune_unbounded")
            else float(row.get("tune_sortino") or 0.0),
            int(row.get("n_longs") or 0),
        ),
        reverse=True,
    )
    chosen = live
    accepted = False
    reason = "no_eligible_threshold"
    for row in ranked:
        reason = _holdout_reason(row, live_hold, config)
        if reason == "accepted":
            chosen = float(row["threshold"])
            accepted = abs(chosen - live) > 1e-9
            reason = "accepted" if accepted else "already_live"
            break
    return {
        "live_threshold": live,
        "chosen_threshold": chosen,
        "accepted": accepted,
        "reason": reason,
        "split_date": split,
        "tune_cases": len(tune),
        "holdout_cases": len(hold),
        "min_taken_longs": MIN_TAKEN_LONGS,
        "min_holdout_lift": MIN_HOLDOUT_LIFT,
        "bounds": [TUNE_LOW, TUNE_HIGH],
        "live_sortino": live_row.get("primary_score"),
        "live_longs": live_row.get("n_longs"),
        "live_drawdown": live_row.get("max_drawdown"),
        "rows": rows,
        "llm_calls": 0,
    }


def _holdout_reason(row: dict[str, Any], live_hold: dict[str, Any] | None, config: ObjectiveConfig) -> str:
    if not row.get("eligible"):
        return "ineligible"
    if live_hold is None:
        return "no_holdout"
    hold_dd = row.get("holdout_drawdown")
    if hold_dd is None or float(hold_dd) < -float(config.drawdown_tolerance):
        return "drawdown_worse"
    hold_score = row.get("holdout_sortino")
    live_score = live_hold.get("primary_score")
    if row.get("holdout_unbounded"):
        if live_hold.get("primary_unbounded"):
            return "no_separation"
        return "accepted"
    if hold_score is None:
        return "insufficient_sample"
    if live_score is None:
        return "accepted" if float(hold_score) > 0 else "no_separation"
    if float(hold_score) < float(live_score) + MIN_HOLDOUT_LIFT:
        return "no_separation"
    return "accepted"


def tune_debate_threshold(skill_dir: Path | str) -> dict[str, Any]:
    """Sweep stored scores and, when one qualifies, store it as a shadow bar.

    Does not call the model and does not promote. Promotion stays on the
    review path.
    """
    from core.debate_tab_agent import DebateTabAgent
    from core.tab_agent_review import write_review_request

    root = Path(skill_dir)
    agent = DebateTabAgent()
    reg = agent.registry(root)
    live = reg.live_version("debate", QUALITY_PROMPT_KEY)
    config = load_objective(root)
    live_threshold = threshold_from_text(None if live is None else str(live.get("text") or ""))
    if live_threshold is None:
        live_threshold = float(config.quality_threshold)
    from core.debate_tab_agent import live_version_ids

    cases = load_debate_cases(root, live_version_ids(root))
    report = sweep_thresholds(cases, config, live_threshold=float(live_threshold))
    report["cases"] = len(cases)
    report["shadow_version_id"] = None
    if not report["accepted"]:
        return report
    if any(row.get("status") == "shadow" for row in reg.versions("debate", QUALITY_PROMPT_KEY)):
        report["reason"] = "candidate_exists"
        report["accepted"] = False
        return report
    if live is None:
        report["reason"] = "no_live_prompt"
        report["accepted"] = False
        return report
    text = quality_text(float(report["chosen_threshold"]))
    created = reg.add_version(
        "debate",
        prompt_key=QUALITY_PROMPT_KEY,
        text=text,
        parent=str(live.get("id") or "") or None,
        rationale=(
            f"Walk-forward bar {format_threshold(float(report['chosen_threshold']))}. "
            f"Tuned before {report.get('split_date')} and checked on later cases. "
            "No model call. Sample may still be small on the holdout."
        ),
        status="shadow",
    )
    write_review_request(
        root,
        tab_id="debate",
        version=created,
        parent_text=str(live.get("text") or ""),
        score_row={
            "n": report.get("live_longs"),
            "primary_metric": "sortino",
            "primary_score": report.get("live_sortino"),
        },
        objective=config.as_dict(),
    )
    report["shadow_version_id"] = created.get("id")
    return report


def sync_quality_policy(skill_dir: Path | str) -> float | None:
    """Copy the live quality-bar text into the shared objective policy."""
    from core.debate_tab_agent import DebateTabAgent
    from core.tab_agent_objective import apply_policy_update

    reg = DebateTabAgent().registry(Path(skill_dir))
    live = reg.live_version("debate", QUALITY_PROMPT_KEY)
    value = threshold_from_text(None if live is None else str(live.get("text") or ""))
    if value is None:
        return None
    apply_policy_update(skill_dir, {"quality_threshold": value})
    return value
