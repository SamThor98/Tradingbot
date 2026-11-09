"""System tab agent: stated confidence versus realized outcomes.

Brier score and reliability bins are reported in the daily digest. The
wording prompt may move the high and low confidence cutoffs only inside
configured bounds. Only matured packets are samples. High confidence takes
the labeled return. Low confidence is scored on the return it missed: a
skipped winner is a penalty and a skipped loser is a credit. The band
between the cutoffs is not a sample. A book with no matured packets is not
scored. No orders.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Callable

from core.tab_agent import STATUS_PENDING, TabAgent
from core.tab_agent_objective import OBJECTIVE_ID, compare_books, evaluate_book, load_objective

LOG = logging.getLogger(__name__)

PROMPT_KEY = "confidence_wording"
_HIGH_RE = re.compile(r"HIGH_CONFIDENCE_MIN\s*=\s*(\d+(?:\.\d+)?)")
_LOW_RE = re.compile(r"LOW_CONFIDENCE_MAX\s*=\s*(\d+(?:\.\d+)?)")
_BIN = 0.1
_GAP = 0.15
_MIN_BIN = 5

BUILTIN_WORDING = (
    "State confidence as a probability between 0 and 1. "
    "Do not invent a probability the checklist does not support. "
    "Long-only. A low-confidence name is a skip, not a short. "
    "HIGH_CONFIDENCE_MIN=0.70 means the book takes the labeled long return. "
    "LOW_CONFIDENCE_MAX=0.40 means the book skips the name and is scored on the "
    "return it missed: a missed winner is a penalty and an avoided loser is a credit. "
    "Confidence between the cutoffs is not a sample. Only matured outcomes count. "
    "Do not penalize market cap. A catalyst is only a tiebreaker."
)

IMPROVER_SYSTEM = (
    "You revise the confidence-wording prompt. "
    "Keep HIGH_CONFIDENCE_MIN=<number> and LOW_CONFIDENCE_MAX=<number> inside the stated bounds. "
    "The objective is a higher Sortino of the confidence book and a lower Brier score. "
    "High confidence takes the matured return. Low confidence is penalized for missed "
    "winners and credited for avoided losers. Do not score a skip as zero. "
    "Long-only. Never suggest a short or an option. You do not place orders. "
    "Return JSON with keys text and rationale. rationale must say when the sample is too small."
)


def prompt_catalog() -> dict[str, str]:
    return {PROMPT_KEY: BUILTIN_WORDING}


def _bounds(skill_dir: Path | None) -> tuple[tuple[float, float], tuple[float, float]]:
    from config import get_tab_agent_confidence_high_bounds, get_tab_agent_confidence_low_bounds

    return get_tab_agent_confidence_high_bounds(skill_dir), get_tab_agent_confidence_low_bounds(skill_dir)


def parse_thresholds(text: str, skill_dir: Path | None = None) -> tuple[float, float]:
    """Clamp the wording cutoffs. High stays above low."""
    from config import get_tab_agent_confidence_high_min, get_tab_agent_confidence_low_max

    high_bounds, low_bounds = _bounds(skill_dir)
    high = get_tab_agent_confidence_high_min(skill_dir)
    low = get_tab_agent_confidence_low_max(skill_dir)
    high_match = _HIGH_RE.search(text or "")
    low_match = _LOW_RE.search(text or "")
    if high_match:
        high = float(high_match.group(1))
    if low_match:
        low = float(low_match.group(1))
    high = max(high_bounds[0], min(high_bounds[1], high))
    low = max(low_bounds[0], min(low_bounds[1], low))
    if low >= high:
        low = min(low, high - 0.05)
        low = max(low_bounds[0], low)
    return high, low


def _confidence(packet: dict[str, Any]) -> float | None:
    refs = packet.get("refs") if isinstance(packet.get("refs"), dict) else {}
    for raw in (refs.get("stated_confidence"), packet.get("p_up_calibrated")):
        try:
            if raw is None or raw == "":
                continue
            value = float(raw)
        except (TypeError, ValueError):
            continue
        if 0.0 <= value <= 1.0:
            return value
    return None


def _outcome_bit(packet: dict[str, Any]) -> float | None:
    outcome = packet.get("outcome") if isinstance(packet.get("outcome"), dict) else {}
    label = str(outcome.get("label") or "").lower()
    if label == "win":
        return 1.0
    if label == "loss":
        return 0.0
    if label == "scratch":
        return 0.5
    return None


def _source(packet: dict[str, Any]) -> str:
    refs = packet.get("refs") if isinstance(packet.get("refs"), dict) else {}
    return str(refs.get("source") or packet.get("setup_type") or "packet")


def calibration_report(packets: list[dict[str, Any]]) -> dict[str, Any]:
    """Brier and reliability by source. A wide bin is marked miscalibrated."""
    grouped: dict[str, list[tuple[float, float]]] = {}
    for packet in packets:
        confidence = _confidence(packet)
        outcome = _outcome_bit(packet)
        if confidence is None or outcome is None:
            continue
        grouped.setdefault(_source(packet), []).append((confidence, outcome))
    by_tab: dict[str, Any] = {}
    for name, pairs in grouped.items():
        brier = sum((p - y) ** 2 for p, y in pairs) / len(pairs)
        bins: dict[str, list[tuple[float, float]]] = {}
        for confidence, outcome in pairs:
            lo = int(confidence / _BIN) * _BIN
            lo = min(lo, 0.9)
            key = f"{lo:.1f}-{lo + _BIN:.1f}"
            bins.setdefault(key, []).append((confidence, outcome))
        gaps = []
        miscalibrated = False
        for key, rows in sorted(bins.items()):
            if len(rows) < _MIN_BIN:
                continue
            mean_p = sum(p for p, _y in rows) / len(rows)
            freq = sum(y for _p, y in rows) / len(rows)
            gap = round(mean_p - freq, 4)
            if abs(gap) >= _GAP:
                miscalibrated = True
            gaps.append(
                {
                    "bin": key,
                    "n": len(rows),
                    "mean_confidence": round(mean_p, 4),
                    "frequency": round(freq, 4),
                    "gap": gap,
                }
            )
        by_tab[name] = {
            "n": len(pairs),
            "brier": round(brier, 6),
            "miscalibrated": miscalibrated,
            "gaps": gaps,
        }
    return {"by_tab": by_tab, "bin_width": _BIN, "gap_flag": _GAP}


def _labeled_return(packet: dict[str, Any], horizon_days: int) -> float | None:
    from core.tab_agent import _packet_return

    parsed = _packet_return(packet, horizon_days=horizon_days)
    if parsed is None:
        return None
    return parsed[1]


class SystemTabAgent(TabAgent):
    tab_id = "system"
    metric_name = OBJECTIVE_ID

    def prompt_catalog(self) -> dict[str, str]:
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
        packets = load_packets(skill_dir)
        if since_ts:
            packets = [row for row in packets if str(row.get("created_at") or "") >= since_ts]
        versions = reg.versions(self.tab_id)
        stats = [_version_book(version, packets, config, skill_dir) for version in versions]
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
        return {
            "tab_id": self.tab_id,
            "objective": objective,
            "objective_id": OBJECTIVE_ID,
            "auto_promote": False,
            "winner_rule": objective["statement"],
            "versions": stats,
            "comparisons": comparisons,
            "calibration": calibration_report(packets),
            "join_note": (
                "Only matured packets are samples. High confidence takes the labeled return. "
                "Low confidence is scored as the negative of that return, so missed winners "
                "penalize Sortino and avoided losers credit it. The equity curve stays in cash "
                "on a skip: those penalties are not compounded as positions. "
                "n counts scored samples. n_longs counts only the high-confidence takes. "
                "Confidence between the cutoffs is not a sample. "
                "No matured packets means the book is not scored."
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
                f"Prompt key: {key}\nObjective: {score.get('winner_rule')}\n"
                f"Calibration: {score.get('calibration')}\n"
                f"n={stat.get('n')} {stat.get('primary_metric')}={stat.get('primary_score')}\n\n"
                f"Current prompt:\n{live.get('text')}"
            )
            try:
                raw = caller(IMPROVER_SYSTEM, user)
            except Exception as exc:
                LOG.warning("System prompt proposal failed for %s: %s", key, exc)
                skipped.append({"prompt_key": key, "reason": "llm_error"})
                continue
            parsed = parse_prompt_proposal(raw or "")
            if parsed is None or parsed["text"].strip() == str(live.get("text") or "").strip():
                skipped.append({"prompt_key": key, "reason": "no_proposal"})
                continue
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


def _version_book(
    version: dict[str, Any],
    packets: list[dict[str, Any]],
    config: Any,
    skill_dir: Path | None = None,
) -> dict[str, Any]:
    """Score one wording on matured returns.

    High confidence takes the realized percent on both the ratio and the equity
    curve. Low confidence contributes the negative of that return to Sortino,
    and cash (0) to the equity curve. A skip is not a position, so missed
    winners cannot compound into a near-total drawdown. The open band between
    the cutoffs is abstain. Pending packets are not samples. An empty matured
    book is not scored.

    ``n`` is every scored sample. ``n_longs`` is only the high-confidence
    takes, so a book of skips has ``n_longs`` 0 while ``n`` is the skip count.
    """
    high, low = parse_thresholds(str(version.get("text") or ""), skill_dir)
    returns: list[float] = []
    path_returns: list[float] = []
    dates: list[str] = []
    decisions: list[str] = []
    n_abstain = 0
    n_immature = 0
    n_missing_confidence = 0
    for packet in packets:
        confidence = _confidence(packet)
        if confidence is None:
            n_missing_confidence += 1
            continue
        realized = _labeled_return(packet, config.horizon_days)
        if realized is None:
            n_immature += 1
            continue
        day = str(packet.get("created_at") or "")[:10]
        if confidence >= high:
            dates.append(day)
            returns.append(realized)
            path_returns.append(realized)
            decisions.append("long")
        elif confidence <= low:
            dates.append(day)
            returns.append(-float(realized))
            path_returns.append(0.0)
            decisions.append("cash")
        else:
            n_abstain += 1
    if not returns:
        book = evaluate_book([], [], config=config, decisions=[])
        book["scored"] = False
        book["not_scored_reason"] = "no_matured_packets"
        book["primary_score"] = None
        book["sortino"] = None
        book["sharpe"] = None
    else:
        book = evaluate_book(
            returns,
            dates,
            config=config,
            decisions=decisions,
            path_returns_pct=path_returns,
        )
        book["scored"] = True
        book["not_scored_reason"] = None
    book.update(
        {
            "version_id": version.get("id"),
            "prompt_key": version.get("prompt_key"),
            "status": version.get("status"),
            "parent": version.get("parent"),
            "high_confidence_min": high,
            "low_confidence_max": low,
            "scoring": "confidence_separation",
            "drawdown_basis": "cash_on_skip",
            "n_skips": sum(1 for decision in decisions if decision == "cash"),
            "n_abstain": n_abstain,
            "n_immature": n_immature,
            "n_missing_confidence": n_missing_confidence,
        }
    )
    return book
