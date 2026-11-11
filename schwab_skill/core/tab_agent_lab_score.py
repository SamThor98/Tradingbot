"""Walk-forward scores for the overnight prompt lab.

Cases come from the decision-packet file and the replay sample file. Both
already carry scale-out labels, so the lab does not walk the disk or refetch
bars. Sortino is primary. Max drawdown and equity-curve smoothness are
secondary. A winner has to clear the train window and the holdout.

Nothing here places an order or writes a live prompt.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from core.tab_agent_objective import ObjectiveConfig, equity_path, evaluate_book, load_objective

# Decision prompts only. Grok review roles and the research rationale do not
# choose long versus cash, so a tournament on them would copy the baseline.
DECISION_KEYS: dict[str, tuple[str, ...]] = {
    "today": ("institutional_trend", "mean_reversion", "retail_fomo"),
    "debate": ("system", "round1", "round2"),
    "research": ("verdict_rules",),
    "backtest": ("strategy_chat",),
    "system": ("confidence_wording",),
}

_CASH_RE = re.compile(r"LAB_CASH_BELOW\s*=\s*(\d+(?:\.\d+)?)")
_UNBOUNDED = 25.0


@dataclass(frozen=True)
class LabCase:
    case_id: str
    as_of: str
    ticker: str
    long_return_pct: float
    signal_score: float | None = None
    stated_confidence: float | None = None
    avg_dollar_volume: float | None = None
    spread_pct: float | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "as_of": self.as_of,
            "ticker": self.ticker,
            "signal_score": self.signal_score,
            "avg_dollar_volume": self.avg_dollar_volume,
            "spread_pct": self.spread_pct,
        }


def split_walk_forward(cases: list[LabCase], holdout_weeks: int) -> tuple[list[LabCase], list[LabCase]]:
    """Hold out the most recent ``holdout_weeks``. Earlier dates are the train window."""
    if not cases:
        return [], []
    last = date.fromisoformat(max(case.as_of for case in cases))
    cutoff = last - timedelta(days=7 * max(1, int(holdout_weeks)))
    train: list[LabCase] = []
    holdout: list[LabCase] = []
    for case in cases:
        if date.fromisoformat(case.as_of) > cutoff:
            holdout.append(case)
        else:
            train.append(case)
    return train, holdout


def equity_smoothness(path: list[float]) -> float | None:
    """R-squared of log equity against a straight line. 1 is a straight curve."""
    if len(path) < 3:
        return None
    ys = [math.log(max(float(value), 1e-9)) for value in path]
    count = len(ys)
    xs = list(range(count))
    mean_x = sum(xs) / count
    mean_y = sum(ys) / count
    var_x = sum((x - mean_x) ** 2 for x in xs)
    if var_x <= 0:
        return None
    cov = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys, strict=False))
    slope = cov / var_x
    intercept = mean_y - slope * mean_x
    ss_tot = sum((y - mean_y) ** 2 for y in ys)
    if ss_tot <= 1e-18:
        return 1.0
    ss_res = sum((y - (intercept + slope * x)) ** 2 for x, y in zip(xs, ys, strict=False))
    return round(max(0.0, 1.0 - ss_res / ss_tot), 6)


def length_penalty(text: str, live_text: str, *, drift_weight: float) -> float:
    """Charge length drift and extra sentences. The live prompt itself is zero."""
    live = live_text or ""
    body = text or ""
    if body == live:
        return 0.0
    live_len = max(len(live), 1)
    drift = abs(len(body) - live_len) / live_len
    live_sentences = max(1, live.count(".") + live.count("\n"))
    sentences = max(1, body.count(".") + body.count("\n"))
    complexity = max(0.0, (sentences - live_sentences) / live_sentences)
    return round(float(drift_weight) * drift + float(drift_weight) * complexity, 6)


def _with_penalty(book: dict[str, Any], penalty: float, path_returns: list[float]) -> dict[str, Any]:
    scored = dict(book)
    raw = scored.get("sortino")
    unbounded = bool(scored.get("primary_unbounded"))
    if unbounded:
        adjusted = round(_UNBOUNDED - penalty, 6)
    elif raw is None:
        adjusted = None
    else:
        adjusted = round(float(raw) - penalty, 6)
    fractions = [float(value) / 100.0 for value in path_returns]
    scored.update(
        {
            "penalty": round(penalty, 6),
            "sortino_adj": adjusted,
            "smoothness": equity_smoothness(equity_path(fractions)),
            "primary_unbounded": unbounded,
        }
    )
    return scored


def score_long_cash(
    cases: list[LabCase],
    decisions: list[str],
    *,
    config: ObjectiveConfig,
    text: str = "",
    live_text: str = "",
    drift_weight: float = 0.15,
) -> dict[str, Any]:
    """One book. Cash is 0%. Win rate ignores cash rows. Shorts are dropped."""
    rows: list[tuple[str, float, str]] = []
    for case, decision in zip(cases, decisions, strict=False):
        side = str(decision or "").lower()
        if side not in {"long", "cash"}:
            continue
        ret = 0.0 if side == "cash" else float(case.long_return_pct)
        rows.append((case.as_of, ret, side))
    rows.sort(key=lambda item: item[0])
    dates = [row[0] for row in rows]
    returns = [row[1] for row in rows]
    labels = [row[2] for row in rows]
    book = evaluate_book(returns, dates, config=config, decisions=labels)
    penalty = length_penalty(text, live_text, drift_weight=drift_weight)
    return _with_penalty(book, penalty, returns)


def score_system_text(
    cases: list[LabCase],
    text: str,
    *,
    config: ObjectiveConfig,
    live_text: str = "",
    drift_weight: float = 0.15,
    skill_dir: Path | None = None,
) -> dict[str, Any]:
    """High confidence takes the return. A low-confidence skip is scored on the return it missed.

    The equity curve stays in cash on a skip, matching the system tab agent.
    """
    from core.system_tab_agent import parse_thresholds

    high, low = parse_thresholds(text, skill_dir)
    rows: list[tuple[str, float, float, str]] = []
    for case in cases:
        if case.stated_confidence is None:
            continue
        confidence = float(case.stated_confidence)
        if confidence >= high:
            rows.append((case.as_of, float(case.long_return_pct), float(case.long_return_pct), "long"))
        elif confidence <= low:
            rows.append((case.as_of, -float(case.long_return_pct), 0.0, "cash"))
    rows.sort(key=lambda item: item[0])
    dates = [row[0] for row in rows]
    returns = [row[1] for row in rows]
    path = [row[2] for row in rows]
    labels = [row[3] for row in rows]
    book = evaluate_book(returns, dates, config=config, decisions=labels, path_returns_pct=path)
    book["n_skips"] = sum(1 for label in labels if label == "cash")
    book["high_confidence_min"] = high
    book["low_confidence_max"] = low
    book["scoring"] = "confidence_separation"
    penalty = length_penalty(text, live_text, drift_weight=drift_weight)
    return _with_penalty(book, penalty, path)


def beats(candidate: dict[str, Any], baseline: dict[str, Any], margin: float) -> bool:
    """True when the adjusted Sortino clears the baseline by ``margin``."""
    left = candidate.get("sortino_adj")
    right = baseline.get("sortino_adj")
    if left is None or right is None:
        return False
    return float(left) >= float(right) + float(margin)


def holdout_gate(
    train: dict[str, Any],
    holdout: dict[str, Any],
    live_train: dict[str, Any],
    live_holdout: dict[str, Any],
    *,
    margin: float,
    min_train: int,
    min_holdout: int,
    drawdown_limit: float,
) -> tuple[bool, str]:
    """A winner beats live on both windows, with enough rows and a tolerable drawdown."""
    if int(train.get("n") or 0) < int(min_train):
        return False, "insufficient_train_sample"
    if int(holdout.get("n") or 0) < int(min_holdout):
        return False, "insufficient_holdout_sample"
    if not beats(train, live_train, margin):
        return False, "train_margin"
    if not beats(holdout, live_holdout, margin):
        return False, "holdout_margin"
    limit = -abs(float(drawdown_limit))
    for label, book in (("train", train), ("holdout", holdout)):
        drawdown = book.get("max_drawdown")
        if drawdown is not None and float(drawdown) < limit:
            return False, f"{label}_drawdown"
    return True, "winner"


def rank_key(book: dict[str, Any]) -> tuple[float, float, float]:
    """Train order: adjusted Sortino, then a smoother curve, then a shallower drawdown."""
    sortino = book.get("sortino_adj")
    smooth = book.get("smoothness")
    drawdown = book.get("max_drawdown")
    return (
        float("-inf") if sortino is None else float(sortino),
        float("-inf") if smooth is None else float(smooth),
        float("-inf") if drawdown is None else float(drawdown),
    )


def offline_decision(text: str, case: LabCase) -> str:
    """Deterministic long/cash rule used by the dry-run and by tests.

    Illiquid names are cash. ``LAB_CASH_BELOW`` skips a weak signal score.
    The live prompt has no such token, so it takes every liquid name.
    """
    if case.avg_dollar_volume is not None and float(case.avg_dollar_volume) < 20_000_000:
        return "cash"
    if case.spread_pct is not None and float(case.spread_pct) > 0.01:
        return "cash"
    match = _CASH_RE.search(text or "")
    score = 100.0 if case.signal_score is None else float(case.signal_score)
    if match and score < float(match.group(1)):
        return "cash"
    return "long"


def bundle_text(texts: dict[str, str], keys: tuple[str, ...]) -> str:
    return "\n".join(str(texts.get(key) or "") for key in keys)


def load_lab_cases(skill_dir: Path | str, *, limit: int, horizon_days: int) -> list[LabCase]:
    """Read one packet file and one replay file. No directory walk."""
    from core.decision_packet import load_packets
    from core.tab_agent import _packet_return
    from core.tab_agent_replay import load_samples

    root = Path(skill_dir)
    merged: dict[tuple[str, str], LabCase] = {}
    for packet in load_packets(root):
        case = _case_from_packet(packet, horizon_days=horizon_days, packet_return=_packet_return)
        if case is not None:
            merged[(case.ticker, case.as_of)] = case
    for row in load_samples(root):
        case = _case_from_replay(row)
        if case is None:
            continue
        key = (case.ticker, case.as_of)
        previous = merged.get(key)
        if previous is None:
            merged[key] = case
            continue
        merged[key] = LabCase(
            case_id=previous.case_id,
            as_of=previous.as_of,
            ticker=previous.ticker,
            long_return_pct=case.long_return_pct,
            signal_score=previous.signal_score if previous.signal_score is not None else case.signal_score,
            stated_confidence=previous.stated_confidence,
            avg_dollar_volume=previous.avg_dollar_volume
            if previous.avg_dollar_volume is not None
            else case.avg_dollar_volume,
            spread_pct=previous.spread_pct if previous.spread_pct is not None else case.spread_pct,
        )
    ordered = sorted(merged.values(), key=lambda case: (case.as_of, case.ticker, case.case_id))
    if limit and len(ordered) > int(limit):
        ordered = ordered[-int(limit) :]
    return ordered


def _num(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _case_from_packet(packet: dict[str, Any], *, horizon_days: int, packet_return: Any) -> LabCase | None:
    parsed = packet_return(packet, horizon_days=horizon_days)
    if parsed is None:
        return None
    ticker = str(packet.get("ticker") or "").upper().strip()
    as_of = str(packet.get("created_at") or "")[:10]
    if not ticker or len(as_of) < 10:
        return None
    refs = packet.get("refs") if isinstance(packet.get("refs"), dict) else {}
    confidence = _num(refs.get("stated_confidence"))
    if confidence is None:
        confidence = _num(packet.get("p_up_calibrated"))
    signal = _num(packet.get("signal_score"))
    if signal is None:
        signal = _num(refs.get("signal_score"))
    volume = _num(packet.get("avg_dollar_volume"))
    if volume is None:
        volume = _num(refs.get("avg_dollar_volume"))
    spread = _num(packet.get("spread_pct"))
    if spread is None:
        spread = _num(refs.get("spread_pct"))
    return LabCase(
        case_id=f"{ticker}:{as_of}",
        as_of=as_of,
        ticker=ticker,
        long_return_pct=float(parsed[1]),
        signal_score=signal,
        stated_confidence=confidence,
        avg_dollar_volume=volume,
        spread_pct=spread,
    )


def _case_from_replay(row: dict[str, Any]) -> LabCase | None:
    if row.get("scored") is False:
        return None
    ticker = str(row.get("ticker") or "").upper().strip()
    as_of = str(row.get("as_of") or "")[:10]
    if not ticker or len(as_of) < 10:
        return None
    raw = row.get("counterfactual_return_pct")
    if raw is None and str(row.get("decision") or "") == "long":
        raw = row.get("realized_return_pct")
    value = _num(raw)
    if value is None:
        return None
    return LabCase(
        case_id=f"{ticker}:{as_of}",
        as_of=as_of,
        ticker=ticker,
        long_return_pct=value,
        signal_score=_num(row.get("signal_score")),
        avg_dollar_volume=_num(row.get("avg_dollar_volume")),
        spread_pct=_num(row.get("spread_pct")),
    )


def objective_for(skill_dir: Path | None) -> ObjectiveConfig:
    return load_objective(skill_dir)


def synthetic_cases(tab: str, *, last: date | None = None) -> list[LabCase]:
    """Sixteen weekly cohorts. The same pattern is in the train window and the holdout.

    Weak signal scores lose money. Low-confidence names lose money. A tighter
    prompt can beat a prompt that takes every liquid name, on both windows.
    """
    end = last or date(2026, 11, 8)
    rows: list[LabCase] = []
    for week in range(16):
        as_of = (end - timedelta(days=7 * (15 - week))).isoformat()
        if tab == "system":
            pattern = (
                (0.90, 4.0, 90.0),
                (0.74, -8.0, 74.0),
                (0.32, -5.0, 32.0),
                (0.45, -3.0, 45.0),
            )
        else:
            pattern = (
                (None, 5.0, 90.0),
                (None, 2.0, 75.0),
                (None, -1.0, 50.0),
                (None, -6.0, 20.0),
            )
        for index, (confidence, ret, score) in enumerate(pattern):
            rows.append(
                LabCase(
                    case_id=f"{tab}-{as_of}-{index}",
                    as_of=as_of,
                    ticker=f"N{index}",
                    long_return_pct=ret,
                    signal_score=score,
                    stated_confidence=confidence,
                    avg_dollar_volume=40_000_000.0,
                    spread_pct=0.002,
                )
            )
    return rows


def flipped_holdout_cases() -> list[LabCase]:
    """Train matches ``synthetic_cases``. The holdout pays the names a tight filter skips."""
    rows = synthetic_cases("today")
    train, holdout = split_walk_forward(rows, 6)
    flipped: list[LabCase] = []
    for case in holdout:
        score = float(case.signal_score or 0)
        ret = 6.0 if score < 40 else -4.0
        flipped.append(
            LabCase(
                case_id=case.case_id,
                as_of=case.as_of,
                ticker=case.ticker,
                long_return_pct=ret,
                signal_score=case.signal_score,
                stated_confidence=case.stated_confidence,
                avg_dollar_volume=case.avg_dollar_volume,
                spread_pct=case.spread_pct,
            )
        )
    return train + flipped
