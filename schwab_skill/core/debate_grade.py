"""Shadow investment grade from an entry-time checklist.

The checklist is not a rescaling of ``signal_score``. ``pts_52w`` is used only
as extension risk. Weights are frozen. ``qual_raw`` is the correlation feature;
the letter is a display layer and is not tuned on the audit sample.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

# Frozen hypothesis. Do not fit these on the scoring-audit sample.
WEIGHTS_PRIMARY: dict[str, float] = {
    "trend": 0.40,
    "setup": 0.25,
    "asymmetry": 0.25,
    "crowding": 0.10,
}
WEIGHTS_EQUAL: dict[str, float] = {
    "trend": 0.25,
    "setup": 0.25,
    "asymmetry": 0.25,
    "crowding": 0.25,
}

COVERAGE_MIN = 0.5
PTS_52W_SCALE = 40.0
PTS_52W_CAP = 37.0
VOLUME_DRYUP_MIN_PTS = 10.0
TREND_FORCE_D_BELOW = 40.0
ASYMMETRY_CAP_C_BELOW = 30.0
# Stop-units of SMA50 cushion that map to a full distance score. The binary pillar stays in qual_raw.
ASYMMETRY_DISTANCE_FULL_STOPS = 3.0
DISAGREEMENT_CAP = 0.65
LETTER_CAP_C = 64.99
LETTER_CAP_D = 49.99

PILLAR_NAMES = ("trend", "setup", "asymmetry", "crowding")
# Frozen extra weight for the dated business side column. It is not inside qual_raw.
BUSINESS_SIDE_WEIGHT = 0.10
_FORENSIC_FAIL_FLAGS = frozenset({"beneish_manipulator", "altman_distress"})

TRADE_FIELDS: tuple[str, ...] = (
    "qual_raw",
    "qual_raw_equal",
    "qual_raw_with_business",
    "qual_score",
    "qual_letter",
    "qual_trend",
    "qual_setup",
    "qual_asymmetry",
    "qual_asymmetry_distance",
    "qual_raw_with_asymmetry_distance",
    "qual_crowding",
    "qual_business",
    "debate_score",
    "qual_coverage_trend",
    "qual_coverage_setup",
    "qual_coverage_asymmetry",
    "qual_coverage_crowding",
    "qual_disagreement",
    "disagreement_source",
    "qual_abstain",
    "debate_grade_would_skip",
)


def _safe_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(out):
        return None
    return out


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def letter_for_score(score: float) -> str:
    if score >= 80.0:
        return "A"
    if score >= 65.0:
        return "B"
    if score >= 50.0:
        return "C"
    if score >= 35.0:
        return "D"
    return "F"


def combine_pillars(
    values: dict[str, float | None],
    weights: dict[str, float] | None = None,
) -> float | None:
    """Weighted mean of present pillars. Crowding is risk, so it enters as 100 minus the pillar."""
    use = weights or WEIGHTS_PRIMARY
    numerator = 0.0
    denominator = 0.0
    for name, weight in use.items():
        raw = values.get(name)
        if raw is None:
            continue
        contribution = (100.0 - float(raw)) if name == "crowding" else float(raw)
        numerator += float(weight) * contribution
        denominator += float(weight)
    if denominator <= 0.0:
        return None
    return numerator / denominator


def _pillar_from_items(items: list[float | None]) -> tuple[float | None, float | None]:
    """Return (score 0-100, coverage). Score is null when coverage is under 0.5."""
    if not items:
        return None, None
    present = [item for item in items if item is not None]
    coverage = len(present) / len(items)
    if coverage < COVERAGE_MIN or not present:
        return None, round(coverage, 6)
    score = 100.0 * (sum(present) / len(present))
    return round(score, 6), round(coverage, 6)


def _flag(signal: dict[str, Any], key: str) -> float | None:
    if key not in signal or signal.get(key) is None:
        return None
    return 1.0 if bool(signal.get(key)) else 0.0


def _component(signal: dict[str, Any], key: str) -> float | None:
    comps = signal.get("score_components")
    if isinstance(comps, dict) and comps.get(key) is not None:
        return _safe_float(comps.get(key))
    return _safe_float(signal.get(key))


def _data_quality(signal: dict[str, Any]) -> str:
    raw = signal.get("_data_quality")
    if raw is None:
        raw = signal.get("data_quality")
    return str(raw or "").strip().lower()


def _disagreement(signal: dict[str, Any]) -> tuple[float | None, str]:
    miro = signal.get("mirofish_result")
    votes = miro.get("agent_votes") if isinstance(miro, dict) else None
    if not isinstance(votes, list) or len(votes) < 2:
        return None, "unavailable"
    from agent_intelligence import compute_vote_disagreement

    return float(compute_vote_disagreement(votes)), "votes"


def _asymmetry_inputs(signal: dict[str, Any]) -> tuple[float, float, float] | None:
    price = _safe_float(signal.get("price"))
    sma50 = _safe_float(signal.get("sma_50"))
    stop_pct = _safe_float(signal.get("stop_pct"))
    if price is None or sma50 is None or stop_pct is None:
        return None
    if price <= 0.0 or sma50 <= 0.0 or stop_pct <= 0.0:
        return None
    return price, sma50, stop_pct


def _asymmetry_item(signal: dict[str, Any]) -> float | None:
    """Pass when the SMA50 cushion is at least the entry stop. Does not use pts_52w."""
    inputs = _asymmetry_inputs(signal)
    if inputs is None:
        return None
    price, sma50, stop_pct = inputs
    cushion = (price - sma50) / price
    return 1.0 if cushion >= stop_pct else 0.0


def _asymmetry_distance(signal: dict[str, Any]) -> float | None:
    """Continuous cushion in stop-units, scaled 0-100. Known at entry. Does not enter qual_raw."""
    inputs = _asymmetry_inputs(signal)
    if inputs is None:
        return None
    price, sma50, stop_pct = inputs
    cushion = (price - sma50) / price
    stops = cushion / stop_pct
    return round(_clamp(stops, 0.0, ASYMMETRY_DISTANCE_FULL_STOPS) / ASYMMETRY_DISTANCE_FULL_STOPS * 100.0, 6)


def _apply_caps(
    qual_raw: float,
    *,
    trend: float | None,
    asymmetry: float | None,
    disagreement: float | None,
    disagreement_source: str,
) -> float:
    score = float(qual_raw)
    if trend is not None and trend < TREND_FORCE_D_BELOW:
        score = min(score, LETTER_CAP_D)
    if asymmetry is not None and asymmetry < ASYMMETRY_CAP_C_BELOW:
        score = min(score, LETTER_CAP_C)
    if (
        disagreement_source == "votes"
        and disagreement is not None
        and disagreement > DISAGREEMENT_CAP
    ):
        score = min(score, LETTER_CAP_C)
    return round(score, 6)


def _seat_lines(
    *,
    trend: float | None,
    setup: float | None,
    asymmetry: float | None,
    crowding_fail: bool | None,
    abstain: bool,
) -> list[dict[str, str]]:
    if abstain:
        return [
            {"seat": "Trend", "line": "Abstained."},
            {"seat": "Setup", "line": "Abstained."},
            {"seat": "Risk", "line": "Abstained."},
            {"seat": "Skeptic", "line": "Abstained."},
            {"seat": "Business", "line": "Abstained."},
        ]

    def _pass_fail(score: float | None, pass_line: str, fail_line: str) -> str:
        if score is None:
            return "Abstained."
        return pass_line if score >= 50.0 else fail_line

    if crowding_fail is None:
        skeptic = "Abstained."
    elif crowding_fail:
        skeptic = "Extended."
    else:
        skeptic = "Extension is inside the cap."
    return [
        {"seat": "Trend", "line": _pass_fail(trend, "Stack holds.", "Stack fails.")},
        {"seat": "Setup", "line": _pass_fail(setup, "Contraction holds.", "Contraction fails.")},
        {
            "seat": "Risk",
            "line": _pass_fail(asymmetry, "Stop sits under the 50-day.", "Stop is through the 50-day."),
        },
        {"seat": "Skeptic", "line": skeptic},
        {"seat": "Business", "line": "Abstained."},
    ]


def _chair_sentence(grade: dict[str, Any]) -> str:
    if grade.get("qual_abstain"):
        return "No grade. Price data is stale or in conflict."
    parts: list[str] = []
    for seat in grade.get("seats") or []:
        if not isinstance(seat, dict):
            continue
        line = str(seat.get("line") or "")
        if str(seat.get("seat")) == "Business" and line == "Abstained.":
            continue
        if line:
            parts.append(f"{seat.get('seat')}: {line}")
    return " ".join(parts)


def _sleeve_for_grade(signal: dict[str, Any]) -> dict[str, Any] | None:
    """Triggered non-daily sleeve, when this row was not admitted as Stage 2.

    The daily VCP checklist scores those rows as failed contraction even when
    the sleeve that admitted them is intact.
    """
    family = str(signal.get("entry_family") or "").strip().lower()
    if family in {"", "stage2", "both"}:
        return None
    plugins = signal.get("strategy_plugins")
    if not isinstance(plugins, list):
        return None
    triggered: list[dict[str, Any]] = []
    for plugin in plugins:
        if not isinstance(plugin, dict) or not plugin.get("triggered"):
            continue
        name = str(plugin.get("name") or "").strip().lower()
        if name in {"", "trend_breakout", "breakout"}:
            continue
        triggered.append(plugin)
    if not triggered:
        return None
    return max(triggered, key=lambda plugin: float(plugin.get("raw_score") or 0.0))


def _sleeve_grade(sleeve: dict[str, Any], base: dict[str, Any]) -> dict[str, Any]:
    score = round(_clamp(float(sleeve.get("raw_score") or 0.0), 0.0, 100.0), 6)
    name = str(sleeve.get("name") or "sleeve").replace("_", " ")
    grade = dict(base)
    grade.update(
        {
            "qual_raw": score,
            "qual_raw_equal": score,
            "qual_score": score,
            "qual_letter": letter_for_score(score),
            "qual_abstain": False,
            "would_skip": False,
            "grade_basis": "sleeve",
            "sleeve_name": str(sleeve.get("name") or ""),
            "chair": (
                f"{name} score {score:.0f}. "
                "This is that sleeve's checklist, not the daily Stage 2 grade."
            ),
        }
    )
    grade["seats"] = _seat_lines(
        trend=None, setup=None, asymmetry=None, crowding_fail=None, abstain=True
    )
    return grade


def grade_from_signal(signal: dict[str, Any]) -> dict[str, Any]:
    """Pure grade. Does not read the network and does not change ``signal_score``."""
    quality = _data_quality(signal)
    disagreement, disagreement_source = _disagreement(signal)
    empty_scores: dict[str, Any] = {
        "version": 1,
        "mode": "shadow",
        "qual_raw": None,
        "qual_raw_equal": None,
        "qual_score": None,
        "qual_letter": None,
        "qual_trend": None,
        "qual_setup": None,
        "qual_asymmetry": None,
        "qual_asymmetry_distance": None,
        "qual_raw_with_asymmetry_distance": None,
        "qual_crowding": None,
        "qual_business": None,
        "qual_raw_with_business": None,
        "debate_score": None,
        "qual_coverage_trend": None,
        "qual_coverage_setup": None,
        "qual_coverage_asymmetry": None,
        "qual_coverage_crowding": None,
        "qual_disagreement": None,
        "disagreement_source": disagreement_source,
        "qual_abstain": True,
        "would_skip": False,
        "crowding_fail": None,
        "label": "Uncalibrated shadow. Does not change rank.",
    }
    if quality in {"stale", "conflict", "no_price_data"}:
        empty_scores["seats"] = _seat_lines(
            trend=None, setup=None, asymmetry=None, crowding_fail=None, abstain=True
        )
        empty_scores["chair"] = _chair_sentence(empty_scores)
        return empty_scores

    sleeve = _sleeve_for_grade(signal)
    if sleeve is not None:
        return _sleeve_grade(sleeve, empty_scores)

    trend, trend_cov = _pillar_from_items(
        [_flag(signal, "trend_stack_ok"), _flag(signal, "sma200_rising")]
    )
    pts_volume = _component(signal, "pts_volume")
    volume_item = None if pts_volume is None else (1.0 if pts_volume >= VOLUME_DRYUP_MIN_PTS else 0.0)
    setup, setup_cov = _pillar_from_items([_flag(signal, "vcp_present"), volume_item])
    asymmetry, asymmetry_cov = _pillar_from_items([_asymmetry_item(signal)])

    pts_52w = _component(signal, "pts_52w")
    if pts_52w is None:
        crowding, crowding_cov, crowding_fail = None, None, None
    else:
        crowding = round(_clamp(pts_52w / PTS_52W_SCALE, 0.0, 1.0) * 100.0, 6)
        crowding_cov = 1.0
        crowding_fail = bool(pts_52w > PTS_52W_CAP)

    values = {
        "trend": trend,
        "setup": setup,
        "asymmetry": asymmetry,
        "crowding": crowding,
    }
    qual_raw = combine_pillars(values, WEIGHTS_PRIMARY)
    qual_raw_equal = combine_pillars(values, WEIGHTS_EQUAL)
    if qual_raw is None:
        empty_scores["qual_coverage_trend"] = trend_cov
        empty_scores["qual_coverage_setup"] = setup_cov
        empty_scores["qual_coverage_asymmetry"] = asymmetry_cov
        empty_scores["qual_coverage_crowding"] = crowding_cov
        empty_scores["disagreement_source"] = disagreement_source
        empty_scores["qual_disagreement"] = disagreement
        empty_scores["seats"] = _seat_lines(
            trend=trend,
            setup=setup,
            asymmetry=asymmetry,
            crowding_fail=crowding_fail,
            abstain=True,
        )
        empty_scores["chair"] = "No grade. Not enough checklist items were present at entry."
        return empty_scores

    qual_score = _apply_caps(
        qual_raw,
        trend=trend,
        asymmetry=asymmetry,
        disagreement=disagreement,
        disagreement_source=disagreement_source,
    )
    trend_fails = trend is not None and trend < TREND_FORCE_D_BELOW
    would_skip = bool(crowding_fail) or trend_fails
    grade: dict[str, Any] = {
        "version": 1,
        "mode": "shadow",
        "qual_raw": round(float(qual_raw), 6),
        "qual_raw_equal": None if qual_raw_equal is None else round(float(qual_raw_equal), 6),
        "qual_score": qual_score,
        "qual_letter": letter_for_score(qual_score),
        "qual_trend": trend,
        "qual_setup": setup,
        "qual_asymmetry": asymmetry,
        "qual_asymmetry_distance": None,
        "qual_raw_with_asymmetry_distance": None,
        "qual_crowding": crowding,
        "qual_business": None,
        "qual_raw_with_business": None,
        "debate_score": None,
        "qual_coverage_trend": trend_cov,
        "qual_coverage_setup": setup_cov,
        "qual_coverage_asymmetry": asymmetry_cov,
        "qual_coverage_crowding": crowding_cov,
        "qual_disagreement": None if disagreement is None else round(float(disagreement), 6),
        "disagreement_source": disagreement_source,
        "qual_abstain": False,
        "would_skip": would_skip,
        "crowding_fail": crowding_fail,
        "weights": dict(WEIGHTS_PRIMARY),
        "label": "Uncalibrated shadow. Does not change rank.",
    }
    grade["seats"] = _seat_lines(
        trend=trend,
        setup=setup,
        asymmetry=asymmetry,
        crowding_fail=crowding_fail,
        abstain=False,
    )
    grade["chair"] = _chair_sentence(grade)
    return grade


def _iso_day(value: Any) -> str:
    text = str(value or "").strip()
    return text[:10] if len(text) >= 10 else ""


def business_observation_current(signal: dict[str, Any]) -> bool:
    """True when a dated business observation is on or before the entry date.

    A missing ``business_as_of`` is not an observation. A missing ``entry_date``
    means the grade is for a live scan, so today's stamp is current. An
    observation after ``entry_date`` is lookahead and is ignored.
    """
    as_of = _iso_day(signal.get("business_as_of"))
    if not as_of:
        return False
    entry = _iso_day(signal.get("entry_date"))
    if not entry:
        return True
    return as_of <= entry


def _business_items(signal: dict[str, Any]) -> list[float | None]:
    if not business_observation_current(signal):
        return []
    items: list[float | None] = []
    tag = str(signal.get("sec_risk_tag") or "").strip().lower()
    if tag in {"low", "medium", "high"}:
        items.append(0.0 if tag == "high" else 1.0)
    guidance = str(signal.get("guidance_signal") or "").strip().lower()
    if guidance in {"positive", "negative"}:
        items.append(1.0 if guidance == "positive" else 0.0)
    if signal.get("forensic_observed") is True:
        flags = {str(flag) for flag in (signal.get("forensic_flags") or [])}
        items.append(0.0 if flags & _FORENSIC_FAIL_FLAGS else 1.0)
    return items


def _stamp_business(signal: dict[str, Any], grade: dict[str, Any]) -> None:
    """Side column only. ``qual_raw`` stays the frozen four-pillar feature."""
    if grade.get("qual_abstain") or grade.get("qual_raw") is None:
        return
    score, _coverage = _pillar_from_items(_business_items(signal))
    grade["qual_business"] = score
    grade["qual_raw_with_business"] = None
    if score is None:
        return
    values = {name: grade.get(f"qual_{name}") for name in PILLAR_NAMES}
    values["business"] = score
    weights = dict(WEIGHTS_PRIMARY)
    weights["business"] = BUSINESS_SIDE_WEIGHT
    combined = combine_pillars(values, weights)
    if combined is not None:
        grade["qual_raw_with_business"] = round(float(combined), 6)
    for seat in grade.get("seats") or []:
        if isinstance(seat, dict) and seat.get("seat") == "Business":
            seat["line"] = "Books pass the dated checklist." if score >= 50.0 else "Books fail the dated checklist."
    grade["chair"] = _chair_sentence(grade)


def _stamp_asymmetry_distance(signal: dict[str, Any], grade: dict[str, Any]) -> None:
    """Side column only. The binary asymmetry pillar inside ``qual_raw`` stays put."""
    if grade.get("qual_abstain") or grade.get("qual_raw") is None:
        return
    distance = _asymmetry_distance(signal)
    grade["qual_asymmetry_distance"] = distance
    grade["qual_raw_with_asymmetry_distance"] = None
    if distance is None:
        return
    values = {name: grade.get(f"qual_{name}") for name in PILLAR_NAMES}
    values["asymmetry"] = distance
    combined = combine_pillars(values, WEIGHTS_PRIMARY)
    if combined is not None:
        grade["qual_raw_with_asymmetry_distance"] = round(float(combined), 6)


def stamp_pit_business(signal: dict[str, Any], filings: list[dict[str, Any]] | None) -> None:
    """Fill business fields from filings dated on or before entry. Leaves a later filing unused."""
    if signal.get("business_as_of"):
        return
    entry = _iso_day(signal.get("entry_date"))
    if not entry or not filings:
        return
    from sec_enrichment import pit_business_from_filings

    fields = pit_business_from_filings(filings, entry)
    if not fields:
        return
    signal["business_as_of"] = fields["business_as_of"]
    signal["sec_risk_tag"] = fields["sec_risk_tag"]


def refresh_display_caps(grade: dict[str, Any]) -> None:
    """Re-apply letter caps after a committee updates disagreement. ``qual_raw`` stays put."""
    if str(grade.get("grade_basis") or "") == "sleeve":
        raw_sleeve = _safe_float(grade.get("qual_raw"))
        if raw_sleeve is None or grade.get("qual_abstain"):
            return
        grade["qual_score"] = round(raw_sleeve, 6)
        grade["qual_letter"] = letter_for_score(float(grade["qual_score"]))
        return
    raw = _safe_float(grade.get("qual_raw"))
    if raw is None or grade.get("qual_abstain"):
        return
    grade["qual_score"] = _apply_caps(
        raw,
        trend=_safe_float(grade.get("qual_trend")),
        asymmetry=_safe_float(grade.get("qual_asymmetry")),
        disagreement=_safe_float(grade.get("qual_disagreement")),
        disagreement_source=str(grade.get("disagreement_source") or ""),
    )
    grade["qual_letter"] = letter_for_score(float(grade["qual_score"]))


def research_grade_signal(
    *,
    technical: dict[str, Any] | None = None,
    sec_analyze: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Checklist inputs for a dossier. Trend is the SMA stack only, not the 52-week stage rule."""
    tech = technical if isinstance(technical, dict) else {}
    sec = sec_analyze if isinstance(sec_analyze, dict) else {}
    signal: dict[str, Any] = {
        "price": tech.get("current_price"),
        "sma_50": tech.get("sma_50"),
        "signal_score": tech.get("signal_score"),
        "data_quality": "ok" if tech.get("current_price") else "no_price_data",
    }
    if "vcp" in tech:
        signal["vcp_present"] = bool(tech.get("vcp"))
    try:
        price = float(tech.get("current_price"))
        sma50 = float(tech.get("sma_50"))
        sma150 = float(tech.get("sma_150"))
        sma200 = float(tech.get("sma_200"))
    except (TypeError, ValueError):
        price = sma50 = sma150 = sma200 = float("nan")
    if all(math.isfinite(v) and v > 0.0 for v in (price, sma50, sma150, sma200)):
        signal["trend_stack_ok"] = bool(price > sma50 > sma150 > sma200)
    filing_date = _iso_day(sec.get("filing_date"))
    if filing_date:
        signal["business_as_of"] = filing_date
        signal["entry_date"] = filing_date
        if sec.get("guidance_signal"):
            signal["guidance_signal"] = sec.get("guidance_signal")
    return signal


def attach_dossier_grade(dossier: dict[str, Any], skill_dir: Path | None = None) -> dict[str, Any]:
    """Attach the checklist grade to a research dossier. Does not call the committee."""
    sections = dossier.get("sections") if isinstance(dossier.get("sections"), dict) else {}
    fundamentals = sections.get("technical_valuation_fundamentals") if isinstance(sections, dict) else {}
    raw = fundamentals.get("raw_report") if isinstance(fundamentals, dict) else {}
    technical = raw.get("technical") if isinstance(raw, dict) else {}
    sec_narr = sections.get("sec_narrative") if isinstance(sections, dict) else {}
    analyze = sec_narr.get("analyze") if isinstance(sec_narr, dict) else {}
    signal = research_grade_signal(
        technical=technical if isinstance(technical, dict) else {},
        sec_analyze=analyze if isinstance(analyze, dict) else {},
    )
    signal["ticker"] = dossier.get("ticker")
    attach_debate_grade(signal, diagnostics={}, skill_dir=skill_dir)
    grade = signal.get("debate_grade")
    if isinstance(grade, dict):
        dossier["debate_grade"] = grade
    return dossier


def flatten_debate_grade(grade: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key in TRADE_FIELDS:
        if key == "debate_grade_would_skip":
            out[key] = bool(grade.get("would_skip"))
        else:
            out[key] = grade.get(key)
    return out


def attach_debate_grade(
    signal: dict[str, Any],
    diagnostics: dict[str, Any] | None = None,
    skill_dir: Path | None = None,
) -> dict[str, Any]:
    """Attach ``debate_grade`` once. Leaves ``signal_score`` unchanged. ``live`` is coerced to shadow."""
    from config import get_debate_grade_mode

    diag = diagnostics if isinstance(diagnostics, dict) else {}
    mode = get_debate_grade_mode(skill_dir)
    if mode == "live":
        diag["debate_grade_live_coerced"] = int(diag.get("debate_grade_live_coerced", 0) or 0) + 1
        mode = "shadow"
    if mode == "off":
        return signal
    before = signal.get("signal_score")
    grade = grade_from_signal(signal)
    _stamp_business(signal, grade)
    _stamp_asymmetry_distance(signal, grade)
    grade["mode"] = "shadow"
    signal["debate_grade"] = grade
    if signal.get("signal_score") != before:
        signal["signal_score"] = before
    diag["debate_grade_evaluated"] = int(diag.get("debate_grade_evaluated", 0) or 0) + 1
    if grade.get("qual_abstain"):
        diag["debate_grade_abstain"] = int(diag.get("debate_grade_abstain", 0) or 0) + 1
    if grade.get("would_skip"):
        diag["debate_grade_would_skip"] = int(diag.get("debate_grade_would_skip", 0) or 0) + 1
    return signal


def stamp_checklist_fields(
    df: Any,
    skill_dir: Path | None = None,
    *,
    include_points: bool = True,
    include_vcp: bool = True,
) -> dict[str, Any]:
    """Entry-time flags from a point-in-time OHLCV frame. Omits a flag it cannot measure."""
    out: dict[str, Any] = {}
    if df is None or getattr(df, "empty", True):
        return out
    from config import get_stage2_sma_upward_days
    from stage_analysis import (
        SMA_50,
        SMA_150,
        SMA_200,
        _slope_per_step,
        add_indicators,
        check_vcp_volume,
        compute_signal_components,
    )

    work = add_indicators(df)
    latest = work.iloc[-1]
    try:
        price = float(latest["close"])
        sma50 = float(latest[SMA_50])
        sma150 = float(latest[SMA_150])
        sma200 = float(latest[SMA_200])
    except (TypeError, ValueError, KeyError):
        price = sma50 = sma150 = sma200 = float("nan")
    if all(math.isfinite(v) and v > 0.0 for v in (price, sma50, sma150, sma200)):
        out["trend_stack_ok"] = bool(price > sma50 > sma150 > sma200)
    upward_days = int(get_stage2_sma_upward_days(skill_dir))
    sma200_series = work[SMA_200].dropna()
    n_days = upward_days + 1
    if len(sma200_series) >= n_days:
        out["sma200_rising"] = bool(_slope_per_step(sma200_series.iloc[-n_days:]) > 0.0)
    if include_vcp:
        try:
            out["vcp_present"] = bool(check_vcp_volume(work, skill_dir))
        except Exception:
            pass
    comps: dict[str, Any] = {}
    if include_points:
        try:
            comps = compute_signal_components(work, skill_dir=skill_dir)
        except Exception:
            comps = {}
    pts_52w = _safe_float(comps.get("pts_52w")) if isinstance(comps, dict) else None
    pts_volume = _safe_float(comps.get("pts_volume")) if isinstance(comps, dict) else None
    if pts_52w is not None:
        out["pts_52w"] = pts_52w
    if pts_volume is not None:
        out["pts_volume"] = pts_volume
    return out
