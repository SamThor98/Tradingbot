"""Plain-English backtest labels. Machine ids (theory_name, strategy_ids) stay unchanged."""

from __future__ import annotations

import re
from datetime import date
from typing import Any

from core.scan_catalog import get_universe, lookup_strategy

KNOB_LABELS: dict[str, str] = {
    "stage2_52w_pct": "Near 52-week high",
    "stage2_sma_upward_days": "200 SMA rising days",
    "vcp_days": "VCP quiet days",
    "quality_min_signal_score": "Min signal score",
    "sma_days": "SMA days",
    "quality_breakout_volume_min_ratio": "Breakout volume vs 50-day avg",
    "quality_require_breakout_volume": "Require breakout volume",
    "scan_vcp_gate_mode": "VCP gate",
    "quality_gates_mode": "Quality gates",
}

HIDDEN_KNOBS = frozenset(
    {
        "skip_mirofish",
        "forensic_enabled",
        "forensic_filter_mode",
    }
)

STATUS_LABELS: dict[str, str] = {
    "queued": "Waiting",
    "running": "Running",
    "success": "Done",
    "failed": "Failed",
    "cancelled": "Cancelled",
}

EXIT_REASON_LABELS: dict[str, str] = {
    "trailing_stop": "Trailing stop",
    "exits_trailing_stop": "Trailing stop",
    "time_exit": "Time exit",
    "exits_time_exit": "Time exit",
    "sma50_break": "50-day SMA break",
    "exits_sma50_break": "50-day SMA break",
    "vcp_invalidation": "VCP invalidation",
    "exits_vcp_invalidation": "VCP invalidation",
    "final_liquidation": "End-of-window liquidation",
    "exits_final_liquidation": "End-of-window liquidation",
}

_WAVE_RE = re.compile(r"^w(\d+)$", re.IGNORECASE)
_YEARS_RE = re.compile(r"^(\d+)y$", re.IGNORECASE)
_CAMPAIGN_RE = re.compile(r"^(?:wknd\s+)?w\d+\b", re.IGNORECASE)
_PRESET_THEORY_RE = re.compile(r"^last\s+\d+\s+years?$", re.IGNORECASE)
_ACRONYMS = {
    "api": "API",
    "etf": "ETF",
    "etfs": "ETFs",
    "sec": "SEC",
    "dcf": "DCF",
    "vcp": "VCP",
    "sma": "SMA",
    "pead": "PEAD",
    "pf": "PF",
}


def humanize_token(raw: str) -> str:
    """Turn snake_case / kebab ids into a readable label. Never leaves underscores."""
    text = str(raw or "").strip().replace("-", "_")
    if not text:
        return ""
    if text in KNOB_LABELS:
        return KNOB_LABELS[text]
    if text in STATUS_LABELS:
        return STATUS_LABELS[text]
    if text in EXIT_REASON_LABELS:
        return EXIT_REASON_LABELS[text]
    parts = [p for p in re.split(r"[_\s]+", text) if p]
    pretty: list[str] = []
    for part in parts:
        lower = part.lower()
        if lower in _ACRONYMS:
            pretty.append(_ACRONYMS[lower])
        elif part.isdigit():
            pretty.append(part)
        else:
            pretty.append(part[:1].upper() + part[1:].lower())
    return " ".join(pretty)


def status_label(status: str | None) -> str:
    key = str(status or "").strip().lower()
    return STATUS_LABELS.get(key) or humanize_token(key) or "Unknown"


def exit_reason_label(reason: str | None) -> str:
    key = str(reason or "").strip()
    if not key:
        return ""
    mapped = EXIT_REASON_LABELS.get(key) or EXIT_REASON_LABELS.get(key.lower())
    if mapped:
        return mapped
    return humanize_token(key)


def universe_display_name(universe_id: str | None) -> str:
    key = str(universe_id or "").strip().lower()
    if not key or key in {"unknown", "watchlist"}:
        return ""
    if key == "tickers":
        return "Custom tickers"
    row = get_universe(key)
    if row and row.get("display_name"):
        return str(row["display_name"])
    return humanize_token(key)


def strategy_display_name(strategy_id: str | None) -> str:
    key = str(strategy_id or "").strip().lower()
    if not key:
        return ""
    row = lookup_strategy(key)
    if row and row.get("display_name"):
        return str(row["display_name"])
    return humanize_token(key)


def _format_knob_value(value: Any) -> str:
    if value is True:
        return ""
    if value is False:
        return "off"
    if isinstance(value, float):
        rendered = f"{value:.4f}".rstrip("0").rstrip(".")
        return rendered
    text = str(value).strip()
    if text.lower() in {"true", "on", "1"}:
        return ""
    if text.lower() in {"false", "off", "0"}:
        return "off"
    return text


def format_knob(key: str, value: Any) -> str:
    name = str(key or "").strip()
    if not name or name in HIDDEN_KNOBS:
        return ""
    label = KNOB_LABELS.get(name) or humanize_token(name)
    pretty_val = _format_knob_value(value)
    if not pretty_val:
        return label
    return f"{label} {pretty_val}"


def window_years_from_spec(spec: dict[str, Any] | None) -> int | None:
    payload = spec if isinstance(spec, dict) else {}
    theory = str(payload.get("theory_name") or "")
    match = re.search(r"\b(\d+)y\b", theory, flags=re.IGNORECASE)
    if match:
        years = int(match.group(1))
        if years > 0:
            return years
    start = str(payload.get("start_date") or "")[:10]
    end = str(payload.get("end_date") or "")[:10]
    try:
        start_d = date.fromisoformat(start)
        end_d = date.fromisoformat(end)
    except ValueError:
        return None
    days = (end_d - start_d).days
    if days <= 0:
        return None
    return max(1, int(round(days / 365.25)))


def window_label(years: int | None) -> str:
    if years is None:
        return ""
    n = int(years)
    return "1 year" if n == 1 else f"{n} years"


def _overrides_dict(spec: dict[str, Any]) -> dict[str, Any]:
    raw = spec.get("overrides")
    return raw if isinstance(raw, dict) else {}


def _is_campaign_theory(theory: str) -> bool:
    return bool(_CAMPAIGN_RE.match(theory.strip()))


def _is_preset_theory(theory: str) -> bool:
    """Date-preset labels like 'last 8 years' are not operator titles."""
    return bool(_PRESET_THEORY_RE.match(theory.strip()))


def _parse_campaign_tokens(theory: str) -> dict[str, Any]:
    rest = re.sub(r"^wknd\s+", "", theory.strip(), flags=re.IGNORECASE)
    wave: int | None = None
    universe_id = ""
    strategy_ids: list[str] = []
    years: int | None = None
    knobs: list[tuple[str, str]] = []
    for bit in rest.split():
        wave_match = _WAVE_RE.match(bit)
        if wave_match:
            wave = int(wave_match.group(1))
            continue
        years_match = _YEARS_RE.match(bit)
        if years_match:
            years = int(years_match.group(1))
            continue
        if "=" in bit:
            key, _, val = bit.partition("=")
            knobs.append((key.strip(), val.strip()))
            continue
        if "+" in bit:
            strategy_ids.extend(p.strip() for p in bit.split("+") if p.strip())
            continue
        if get_universe(bit.lower()):
            universe_id = bit.lower()
            continue
        if lookup_strategy(bit.lower()):
            strategy_ids.append(bit.lower())
            continue
        if not universe_id:
            universe_id = bit.lower()
        else:
            strategy_ids.append(bit.lower())
    return {
        "wave": wave,
        "universe_id": universe_id,
        "strategy_ids": strategy_ids,
        "window_years": years,
        "knobs": knobs,
    }


def _strip_underscores(text: str) -> str:
    return str(text or "").replace("_", " ").strip()


def format_backtest_label(spec: dict[str, Any] | None) -> dict[str, Any]:
    """Build operator-facing title/subtitle from a StrategySpec dict."""
    payload = spec if isinstance(spec, dict) else {}
    theory = str(payload.get("theory_name") or "").strip()
    spec_universe = str(payload.get("universe_preset") or payload.get("universe_mode") or "").strip()
    spec_strategies = [
        str(sid).strip() for sid in (payload.get("strategy_ids") or []) if str(sid).strip()
    ]
    years = window_years_from_spec(payload)
    knobs: list[str] = []
    wave: int | None = None
    universe_id = spec_universe
    strategy_ids = list(spec_strategies)

    if theory and _is_campaign_theory(theory):
        parsed = _parse_campaign_tokens(theory)
        wave = parsed["wave"]
        if parsed["universe_id"]:
            universe_id = parsed["universe_id"]
        if parsed["strategy_ids"]:
            strategy_ids = parsed["strategy_ids"]
        if parsed["window_years"]:
            years = parsed["window_years"]
        knobs.extend(format_knob(k, v) for k, v in parsed["knobs"])
    else:
        for key, val in _overrides_dict(payload).items():
            knobs.append(format_knob(key, val))

    knobs = [k for k in knobs if k]
    universe_name = universe_display_name(universe_id)
    if payload.get("universe_mode") == "tickers" or universe_id in {"custom", "tickers"}:
        n_tickers = len(payload.get("tickers") or [])
        universe_name = "Custom tickers" if not n_tickers else f"Custom tickers ({n_tickers})"
    strategy_names = [strategy_display_name(sid) for sid in strategy_ids]
    strategy_names = [n for n in strategy_names if n]
    years_text = window_label(years)

    title_bits: list[str] = []
    if wave is not None:
        title_bits.append(f"Wave {wave}")
    if universe_name:
        title_bits.append(universe_name)
    if strategy_names:
        title_bits.append(" + ".join(strategy_names))
    if years_text:
        title_bits.append(years_text)

    if title_bits:
        title = " · ".join(title_bits)
        if (
            theory
            and not _is_campaign_theory(theory)
            and not _is_preset_theory(theory)
            and "_" not in theory
            and theory not in knobs
        ):
            knobs = [theory, *knobs]
    elif theory and not _is_preset_theory(theory) and not _is_campaign_theory(theory) and "_" not in theory:
        title = theory
    elif theory:
        title = humanize_token(theory.replace("wknd", "").strip()) or "Backtest"
    else:
        title = "Backtest"

    subtitle = " · ".join(knobs)
    title = _strip_underscores(title)
    subtitle = _strip_underscores(subtitle)
    return {
        "title": title,
        "subtitle": subtitle,
        "parts": {
            "wave": wave,
            "universe": universe_name,
            "universe_id": universe_id,
            "strategies": strategy_names,
            "strategy_ids": strategy_ids,
            "window_years": years,
            "window_label": years_text,
            "knobs": knobs,
        },
    }


def attach_run_labels(item: dict[str, Any]) -> dict[str, Any]:
    """Stamp display_label fields onto a serialized run dict."""
    spec = item.get("spec") if isinstance(item.get("spec"), dict) else {}
    formatted = format_backtest_label(spec)
    title = formatted["title"]
    summary = item.get("result_summary") if isinstance(item.get("result_summary"), dict) else {}
    preset = str(spec.get("universe_preset") or "")
    universe = get_universe(preset) or {}
    try:
        loaded = int(summary.get("membership_input_universe"))
        expected = int(universe.get("approx_size") or 0)
    except (TypeError, ValueError):
        loaded = 0
        expected = 0
    if expected and loaded and loaded < expected // 2:
        title = f"{title} · {loaded} names"
    item["display_label"] = title
    item["display_subtitle"] = formatted["subtitle"]
    item["display_parts"] = formatted["parts"]
    item["status_label"] = status_label(item.get("status"))
    item["window_years"] = formatted["parts"].get("window_years")
    return item
