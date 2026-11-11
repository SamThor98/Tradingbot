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
# Eight imaginary trades that lost 5%. Mixed into a book with no observed losers,
# so a short winning streak cannot print an infinite Sortino.
_PRIOR_DOWNSIDE = 0.05
_PRIOR_TRADES = 8.0
DEFAULT_MIN_PARTICIPATION = 0.60
DEFAULT_MIN_LONGS = 8


# Model-memory cutoff. gpt-4o-mini's training ends around October 2023.
# Rebuilt cases before this date are tagged and cannot be the only source of a win.
PRE_CUTOFF = date(2023, 11, 1)


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
    source: str = "live"
    payload: dict[str, Any] | None = None
    pre_cutoff: bool = False
    era: str = ""
    cohort_return_pct: float | None = None

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


def conservative_sortino(returns_pct: list[float]) -> float | None:
    """Sortino after mixing in a downside prior.

    The sample downside deviation is blended with eight pseudo-trades at a 5%
    loss. A book with no losers stays finite, and a handful of winners stays small.
    """
    if not returns_pct:
        return None
    fractions = [float(value) / 100.0 for value in returns_pct]
    count = float(len(fractions))
    mean = sum(fractions) / count
    squares = sum(min(value, 0.0) ** 2 for value in fractions)
    downside = math.sqrt(squares / count)
    shrunk = math.sqrt((count * downside * downside + _PRIOR_TRADES * _PRIOR_DOWNSIDE**2) / (count + _PRIOR_TRADES))
    if shrunk <= 1e-12:
        return 0.0
    return mean / shrunk


def _with_penalty(
    book: dict[str, Any],
    penalty: float,
    path_returns: list[float],
    scored_returns: list[float],
) -> dict[str, Any]:
    scored = dict(book)
    raw = scored.get("sortino")
    unbounded = bool(scored.get("primary_unbounded"))
    method = "sample"
    if unbounded:
        shrunk = conservative_sortino(scored_returns)
        adjusted = None if shrunk is None else round(shrunk - penalty, 6)
        method = "shrunk"
    elif raw is None:
        adjusted = None
    else:
        adjusted = round(float(raw) - penalty, 6)
    fractions = [float(value) / 100.0 for value in path_returns]
    scored.update(
        {
            "penalty": round(penalty, 6),
            "sortino_adj": adjusted,
            "sortino_method": method,
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
    return _with_penalty(book, penalty, returns, returns)


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
    return _with_penalty(book, penalty, path, returns)


def beats(candidate: dict[str, Any], baseline: dict[str, Any], margin: float) -> bool:
    """True when the adjusted Sortino clears the baseline by ``margin``."""
    left = candidate.get("sortino_adj")
    right = baseline.get("sortino_adj")
    if left is None or right is None:
        return False
    return float(left) >= float(right) + float(margin)


def participation_reason(
    book: dict[str, Any],
    live: dict[str, Any],
    *,
    rate: float = DEFAULT_MIN_PARTICIPATION,
    min_longs: int = DEFAULT_MIN_LONGS,
) -> str | None:
    """Why a book traded too little to be a winner. None when it took a real share.

    ``n_longs`` is the taken trades. Cash rows do not count. The book needs an
    absolute minimum and, when live took trades, at least ``rate`` of that count.
    """
    longs = int(book.get("n_longs") or 0)
    live_longs = int(live.get("n_longs") or 0)
    if longs < int(min_longs):
        return "too_few_trades"
    if live_longs > 0 and longs < float(rate) * live_longs:
        return "low_participation"
    return None


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
    min_participation: float = DEFAULT_MIN_PARTICIPATION,
    min_longs: int = DEFAULT_MIN_LONGS,
) -> tuple[bool, str]:
    """A winner beats live on both windows, with enough trades and a tolerable drawdown."""
    if int(train.get("n") or 0) < int(min_train):
        return False, "insufficient_train_sample"
    if int(holdout.get("n") or 0) < int(min_holdout):
        return False, "insufficient_holdout_sample"
    for label, book, live in (("train", train, live_train), ("holdout", holdout, live_holdout)):
        reason = participation_reason(book, live, rate=min_participation, min_longs=min_longs)
        if reason:
            return False, f"{label}_{reason}"
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


@dataclass(frozen=True)
class LabLoad:
    """Labeled cases after fixture removal and (ticker, as_of) dedupe.

    The row cap is not applied here. A newest-N cut before the date split
    can put every remaining row in the holdout.
    """

    cases: list[LabCase]
    fixtures_excluded: int
    duplicates_removed: int


def load_lab_cases(skill_dir: Path | str, *, limit: int | None = None, horizon_days: int) -> list[LabCase]:
    """Read one packet file and one replay file. No directory walk.

    ``limit`` is accepted and ignored. Sampling happens after the date split,
    per era, so a cap cannot empty a side.
    """
    _ = limit
    return load_lab_book(skill_dir, horizon_days=horizon_days).cases


def load_lab_book(skill_dir: Path | str, *, horizon_days: int) -> LabLoad:
    """Packets and replay rows, fixtures out, one row per ticker and date."""
    from core.decision_packet import load_packets
    from core.fixture_packet import is_fixture_packet
    from core.tab_agent import _packet_return
    from core.tab_agent_lab_plan import lab_decision_card
    from core.tab_agent_replay import load_samples

    root = Path(skill_dir)
    merged: dict[tuple[str, str], LabCase] = {}
    fixtures = 0
    duplicates = 0
    fixture_keys: set[tuple[str, str]] = set()
    for packet in load_packets(root):
        if not isinstance(packet, dict):
            continue
        if is_fixture_packet(packet) or _row_is_fixture(packet):
            fixtures += 1
            key = _packet_key(packet)
            if key is not None:
                fixture_keys.add(key)
            continue
        case = _case_from_packet(packet, horizon_days=horizon_days, packet_return=_packet_return)
        if case is None:
            continue
        case = _with_live_card(case, packet, lab_decision_card)
        key = (case.ticker, case.as_of)
        if key in merged:
            duplicates += 1
        merged[key] = case
    for row in load_samples(root):
        if not isinstance(row, dict):
            continue
        if _row_is_fixture(row):
            fixtures += 1
            continue
        case = _case_from_replay(row)
        if case is None:
            continue
        key = (case.ticker, case.as_of)
        if key in fixture_keys:
            fixtures += 1
            continue
        previous = merged.get(key)
        if previous is None:
            merged[key] = _with_live_card(case, row, lab_decision_card)
            continue
        duplicates += 1
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
            source=previous.source or "live",
            payload=previous.payload if previous.payload else case.payload,
            pre_cutoff=previous.pre_cutoff,
            era=previous.era or case.era,
            cohort_return_pct=previous.cohort_return_pct,
        )
    ordered = sorted(merged.values(), key=lambda case: (case.as_of, case.ticker, case.case_id))
    return LabLoad(cases=ordered, fixtures_excluded=fixtures, duplicates_removed=duplicates)


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


def _packet_key(packet: dict[str, Any]) -> tuple[str, str] | None:
    ticker = str(packet.get("ticker") or "").upper().strip()
    as_of = str(packet.get("created_at") or packet.get("as_of") or "")[:10]
    if not ticker or len(as_of) < 10:
        return None
    return ticker, as_of


def _row_is_fixture(row: dict[str, Any]) -> bool:
    """Reuse the packet signature for a replay row that still carries it."""
    from core.fixture_packet import is_fixture_packet

    refs = row.get("refs") if isinstance(row.get("refs"), dict) else {}
    order = row.get("order_ref")
    if order is None:
        order = refs.get("order_ref")
    entry = row.get("entry_price")
    if entry is None:
        entry = refs.get("entry_price")
    packet = {
        "ticker": row.get("ticker"),
        "entry_price": entry,
        "order_ref": order,
        "refs": {"order_ref": order} if order else refs,
    }
    return is_fixture_packet(packet)


def _with_live_card(case: LabCase, source: dict[str, Any], card_fn: Any) -> LabCase:
    """Attach the shared lab card. Live rows keep ticker and date."""
    if case.payload:
        return case
    signal = dict(source)
    refs = source.get("refs") if isinstance(source.get("refs"), dict) else {}
    for key, value in refs.items():
        signal.setdefault(key, value)
    signal.setdefault("ticker", case.ticker)
    if case.signal_score is not None:
        signal.setdefault("signal_score", case.signal_score)
    if case.avg_dollar_volume is not None:
        signal.setdefault("avg_dollar_volume", case.avg_dollar_volume)
    if case.spread_pct is not None:
        signal.setdefault("spread_pct", case.spread_pct)
    try:
        pre_cutoff = date.fromisoformat(case.as_of) < PRE_CUTOFF
    except ValueError:
        pre_cutoff = False
    return LabCase(
        case_id=case.case_id,
        as_of=case.as_of,
        ticker=case.ticker,
        long_return_pct=case.long_return_pct,
        signal_score=case.signal_score,
        stated_confidence=case.stated_confidence,
        avg_dollar_volume=case.avg_dollar_volume,
        spread_pct=case.spread_pct,
        source="live",
        payload=card_fn(signal, case.as_of),
        pre_cutoff=pre_cutoff,
        era=case.era,
        cohort_return_pct=case.cohort_return_pct,
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
