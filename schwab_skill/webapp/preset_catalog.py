"""Single source for strategy preset env values and human-readable copy (local + SaaS)."""

from __future__ import annotations

from typing import Any

PRESET_PROFILES: dict[str, dict[str, str]] = {
    "conservative": {
        "POSITION_SIZE_USD": "300",
        "MAX_TRADES_PER_DAY": "3",
        "QUALITY_GATES_MODE": "hard",
        "EVENT_RISK_MODE": "live",
        "EVENT_ACTION": "block",
        "EXEC_QUALITY_MODE": "live",
    },
    "balanced": {
        "POSITION_SIZE_USD": "500",
        "MAX_TRADES_PER_DAY": "5",
        "QUALITY_GATES_MODE": "soft",
        "EVENT_RISK_MODE": "live",
        "EVENT_ACTION": "downsize",
        "EXEC_QUALITY_MODE": "live",
    },
    "aggressive": {
        "POSITION_SIZE_USD": "900",
        "MAX_TRADES_PER_DAY": "8",
        "QUALITY_GATES_MODE": "soft",
        "EVENT_RISK_MODE": "shadow",
        "EVENT_ACTION": "downsize",
        "EXEC_QUALITY_MODE": "shadow",
    },
}

PROFILE_BLURBS: dict[str, str] = {
    "conservative": "Smaller size, stricter filters, and full blocks near earnings.",
    "balanced": "Moderate size with softer filters; near earnings, sizes shrink instead of blocking.",
    "aggressive": "Larger size; earnings and execution checks stay in log-only mode until you tighten them.",
}

SETTING_LABELS: dict[str, str] = {
    "POSITION_SIZE_USD": "Position size",
    "MAX_TRADES_PER_DAY": "Daily trade cap",
    "QUALITY_GATES_MODE": "Signal quality",
    "EVENT_RISK_MODE": "Earnings & news rules",
    "EVENT_ACTION": "If a risk event is flagged",
    "EXEC_QUALITY_MODE": "Spread & slippage checks",
}


def humanize_setting(key: str, value: str) -> str:
    v = str(value or "").strip()
    k = str(key or "").strip()
    if k == "POSITION_SIZE_USD":
        return f"About ${v} per new buy (sized from price)."
    if k == "MAX_TRADES_PER_DAY":
        return f"Up to {v} new live trades per day."
    if k == "QUALITY_GATES_MODE":
        lv = v.lower()
        if lv == "hard":
            return "Stricter filter; fewer names pass."
        if lv == "soft":
            return "Softer filter; more names can pass."
        return v or "—"
    if k == "EVENT_RISK_MODE":
        lv = v.lower()
        if lv == "live":
            return "Rules apply to real orders."
        if lv == "shadow":
            return "Rules run in the background only (no effect on live orders)."
        if lv == "off":
            return "Turned off."
        return v or "—"
    if k == "EVENT_ACTION":
        lv = v.lower()
        if lv == "block":
            return "Block new buys when flagged."
        if lv == "downsize":
            return "Use a smaller size when flagged."
        return v or "—"
    if k == "EXEC_QUALITY_MODE":
        lv = v.lower()
        if lv == "live":
            return "Wide spread or bad quotes can block live orders."
        if lv == "shadow":
            return "Checks run in the background only."
        if lv == "off":
            return "Turned off."
        return v or "—"
    return v or "—"


def setting_label(key: str) -> str:
    return SETTING_LABELS.get(key, key.replace("_", " ").title())


def settings_display_map(settings: dict[str, str]) -> dict[str, dict[str, str]]:
    return {
        k: {"raw": str(v), "plain": humanize_setting(k, str(v)), "label": setting_label(k)}
        for k, v in settings.items()
    }


def build_preset_catalog_payload() -> dict[str, Any]:
    out: dict[str, Any] = {}
    for name, settings in PRESET_PROFILES.items():
        out[name] = {
            "blurb": PROFILE_BLURBS.get(name, ""),
            "settings": dict(settings),
            "settings_display": settings_display_map(settings),
        }
    return out
