from __future__ import annotations

import re
from typing import Any


_LEADERSHIP_LINES = (
    "prepared:",
    "analyst:",
    "coverage:",
    "region:",
    "document type:",
    "current price:",
    "recommendation:",
)

_PHRASE_REWRITES: tuple[tuple[str, str], ...] = (
    (
        "is evaluated through a blended institutional framework that integrates market structure, valuation underwriting, filing intelligence, and scenario-based risk control.",
        "is assessed across market structure, valuation, filing signals, and scenario risk.",
    ),
    (
        "The objective is not to defend a side, but to rank the probability distribution and identify whether reward-to-risk is improving or deteriorating.",
        "The objective is to rank probabilities and determine whether reward-to-risk is improving.",
    ),
    (
        "for informational research workflows",
        "for research workflows",
    ),
    (
        "At a high level, ",
        "",
    ),
    (
        "Treat this as a structured starting point — any execution decision should follow position-sizing rules and the explicit invalidation criteria listed in the Catalyst and Risk Matrix.",
        "Use this as a decision input. Enforce position sizing and explicit invalidation rules before execution.",
    ),
    (
        "for underwriting purposes, focus on whether",
        "focus on whether",
    ),
    (
        "should be considered before treating multiples as directly comparable.",
        "must be normalized before using multiples for comparison.",
    ),
    (
        "is read through a growth, margin, capital-efficiency, and balance-sheet lens.",
        "is assessed across growth, margins, capital efficiency, and balance-sheet quality.",
    ),
    (
        "The table below captures TTM trends and quarterly liquidity posture.",
        "The table summarizes TTM trends and liquidity posture.",
    ),
    (
        "should be read together with SEC and catalyst evidence, not in isolation.",
        "must be read with SEC and catalyst evidence, not in isolation.",
    ),
    (
        "It is not investment advice.",
        "This is not investment advice.",
    ),
)

_HOUSE_STYLE_EXCLUDED_PREFIXES = (
    "## cover page",
    "## references",
    "## limitations",
    "## disclaimer",
)

_SECTION_SOURCE_HINTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("executive investment summary", ("report_stack", "finnhub", "sec_analyze")),
    ("company and business model", ("report_stack", "finnhub")),
    ("fundamental performance analysis", ("finnhub", "report_stack")),
    ("valuation and technical positioning", ("report_stack", "finnhub")),
    ("sec narrative", ("sec_analyze", "sec_compare")),
    ("portfolio fit", ("portfolio", "sector_context", "report_stack")),
    ("catalyst and risk matrix", ("finnhub", "report_stack", "sec_analyze")),
    ("insider activity", ("finnhub",)),
    ("sell-side analyst activity", ("finnhub",)),
    ("capital returns and corporate actions", ("finnhub",)),
    ("news and sentiment pulse", ("finnhub",)),
)


def _house_style_why(section_heading: str) -> str:
    low = section_heading.lower()
    if "executive investment summary" in low:
        return "This sets portfolio-level decision context before deep-dive underwriting."
    if "company and business model" in low:
        return "Business-model quality drives durability of earnings and multiple support."
    if "fundamental performance analysis" in low:
        return "Growth, margins, and balance-sheet quality determine re-rating versus compression risk."
    if "valuation and technical positioning" in low:
        return "Entry quality improves when valuation support and trend structure align."
    if "sec narrative" in low:
        return "Filing language changes often reveal risk earlier than headline metrics."
    if "portfolio fit" in low:
        return "Sizing discipline and concentration guardrails protect downside outcomes."
    if "catalyst and risk matrix" in low:
        return "Catalysts define upside path; explicit risks define invalidation path."
    if "insider activity" in low:
        return "Insider behavior can confirm or challenge management confidence."
    if "sell-side analyst activity" in low:
        return "Analyst revision direction can affect near-term expectations and sentiment."
    if "capital returns and corporate actions" in low:
        return "Capital allocation quality influences long-run compounding and valuation support."
    if "news and sentiment pulse" in low:
        return "Newsflow intensity and tone can shift short-horizon risk/reward."
    return "This section summarizes evidence needed for a high-quality investment decision."


def _safe_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _section_source_names(section_heading: str) -> tuple[str, ...]:
    low = section_heading.lower()
    for needle, names in _SECTION_SOURCE_HINTS:
        if needle in low:
            return names
    return ("report_stack",)


def _source_health_ratio(source_rows: list[dict[str, Any]], source_names: tuple[str, ...]) -> float:
    if not source_names:
        return 1.0
    rows_by_name = {str(row.get("name") or ""): row for row in source_rows}
    score = 0.0
    for name in source_names:
        row = rows_by_name.get(name)
        if not row:
            score += 0.45
            continue
        status = str(row.get("status") or "").lower()
        if status == "ok":
            score += 1.0
        elif status == "degraded":
            score += 0.55
        else:
            score += 0.35
    return max(0.0, min(1.0, score / max(1, len(source_names))))


def _format_confidence_line(
    section_heading: str,
    trust_payload: dict[str, Any],
    source_rows: list[dict[str, Any]],
) -> str:
    data_conf = _safe_float(trust_payload.get("data_confidence"))
    cite_conf = _safe_float(trust_payload.get("citation_completeness"))
    conflict = _safe_float(trust_payload.get("unresolved_conflict_ratio"))
    freshness_ok = bool((trust_payload.get("freshness_status") or {}).get("ok"))
    data_conf = max(0.0, min(1.0, data_conf if data_conf is not None else 0.0))
    cite_conf = max(0.0, min(1.0, cite_conf if cite_conf is not None else 0.0))
    conflict = max(0.0, min(1.0, conflict if conflict is not None else 1.0))
    base = (data_conf * 0.45) + (cite_conf * 0.35) + ((1.0 - conflict) * 0.20)
    if not freshness_ok:
        base -= 0.20
    source_quality = _source_health_ratio(source_rows, _section_source_names(section_heading))
    score = max(0.0, min(1.0, (base * 0.75) + (source_quality * 0.25)))
    if score >= 0.80:
        label = "High"
    elif score >= 0.62:
        label = "Medium"
    else:
        label = "Low"
    return (
        f"- {label} ({score * 100:.0f}/100): data confidence {data_conf * 100:.0f}%, "
        f"citation completeness {cite_conf * 100:.0f}%, unresolved conflicts {conflict * 100:.0f}%, "
        f"freshness {'ok' if freshness_ok else 'stale'}."
    )


def _inject_house_style(
    lines: list[str],
    *,
    trust_payload: dict[str, Any] | None,
    source_rows: list[dict[str, Any]],
) -> list[str]:
    out: list[str] = []
    i = 0
    while i < len(lines):
        row = lines[i]
        out.append(row)
        text = row.strip()
        low = text.lower()
        if text.startswith("## ") and not any(low.startswith(prefix) for prefix in _HOUSE_STYLE_EXCLUDED_PREFIXES):
            # Avoid duplicate insertion on re-polish.
            lookahead = [ln.strip().lower() for ln in lines[i + 1 : i + 8]]
            if "### what happened" not in lookahead:
                confidence_line = _format_confidence_line(
                    text,
                    trust_payload or {},
                    source_rows,
                )
                out.extend(
                    [
                        "",
                        "### What Happened",
                        "- Evidence in this section is presented below in narrative and table form.",
                        "",
                        "### Why It Matters",
                        f"- {_house_style_why(text)}",
                        "",
                        "### Confidence",
                        confidence_line,
                        "",
                        "### Sources",
                        "- Use inline citations [n] in each claim block and verify against the References section.",
                        "",
                    ]
                )
        i += 1
    return out


def _split_pipe_line(line: str) -> list[str] | None:
    text = line.strip()
    if not text:
        return None
    low = text.lower()
    if not any(low.startswith(prefix) for prefix in _LEADERSHIP_LINES):
        return None
    if " | " not in text:
        return None
    parts = [p.strip() for p in text.split("|") if p.strip()]
    if len(parts) <= 1:
        return None
    return [f"- {part}" for part in parts]


def _trim_long_line(line: str, *, max_len: int = 240) -> str:
    text = line.strip()
    if not text or text.startswith("|"):
        return line
    if len(text) <= max_len:
        return line
    sentences = re.split(r"(?<=[.!?])\s+", text)
    compact = " ".join(s.strip() for s in sentences[:2] if s.strip())
    if compact and len(compact) <= max_len:
        return compact
    return text[: max_len - 1].rstrip() + "…"


def _tighten_memo_voice(line: str) -> str:
    text = line.strip()
    if not text or text.startswith("|") or text.startswith("#") or text.startswith("- "):
        return line
    # Reduce verbose clause chaining and tighten implication language.
    text = re.sub(r"\bwhile\b", "and", text, flags=re.IGNORECASE)
    text = re.sub(r"\btherefore\b", "so", text, flags=re.IGNORECASE)
    text = re.sub(r"\bIn this context,\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\bFor underwriting purposes,\s*", "", text, flags=re.IGNORECASE)
    # Prefer direct memo wording over hedged phrasing.
    text = re.sub(r"\bcan\b(?=\s+\w+ed\b)", "may", text, flags=re.IGNORECASE)
    text = re.sub(r"\bshould\b", "must", text, flags=re.IGNORECASE)
    # Compress repeated connective prose.
    text = re.sub(r"\s{2,}", " ", text).strip()
    return text


def polish_dossier_markdown(
    markdown: str,
    *,
    trust_payload: dict[str, Any] | None = None,
    source_rows: list[dict[str, Any]] | None = None,
) -> str:
    lines = str(markdown or "").splitlines()
    normalized = _inject_house_style(
        lines,
        trust_payload=trust_payload,
        source_rows=source_rows or [],
    )
    out: list[str] = []
    for raw in normalized:
        row = raw.rstrip()
        split = _split_pipe_line(row)
        if split is not None:
            out.extend(split)
            continue
        for before, after in _PHRASE_REWRITES:
            row = row.replace(before, after)
        row = _tighten_memo_voice(row)
        row = _trim_long_line(row)
        out.append(row)

    # Collapse excessive vertical whitespace.
    collapsed: list[str] = []
    blank_run = 0
    for row in out:
        if row.strip():
            blank_run = 0
            collapsed.append(row)
            continue
        blank_run += 1
        if blank_run <= 1:
            collapsed.append("")
    return "\n".join(collapsed).strip() + "\n"

