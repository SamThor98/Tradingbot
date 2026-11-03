"""Shared Discord operator-desk handlers.

Slash commands and plain phrases in the confirm channel both call this module.
Discord I/O stays in discord_confirm.py.
"""

from __future__ import annotations

import logging
import os
import re
import threading
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

LOG = logging.getLogger(__name__)

DENIAL_MESSAGE = "That action is limited to the configured Discord operator."
_UNCONFIGURED_DENIAL = DENIAL_MESSAGE + " DISCORD_USER_ID is not set."
SCAN_STARTED = "Scan started. I'll post the result in this channel when it finishes."
SCAN_ALREADY_RUNNING = "A scan is already running. I'll post the result here when it finishes."
_operator_scan_guard = threading.Lock()
_operator_scan_active = False

_MUTATING = frozenset({"scan", "halt", "resume"})
_TICKER = re.compile(r"^[A-Za-z]{1,5}(?:\.[A-Za-z])?$")
_LEADING_MENTION = re.compile(r"^(?:<@!?\d+>\s*)+")
_PHRASES = {
    "how's the book": "portfolio",
    "hows the book": "portfolio",
    "how is the book": "portfolio",
    "pause buys": "halt",
    "resume buys": "resume",
}
_SINGLE = {
    "ping": "ping",
    "help": "help",
    "status": "status",
    "portfolio": "portfolio",
    "book": "portfolio",
    "sectors": "sectors",
    "scan": "scan",
    "today": "today",
    "pending": "pending",
    "pause": "halt",
    "halt": "halt",
    "resume": "resume",
}
_STATUS_FIELDS = ("Bot", "Market API", "Account API", "Last Scan", "Next Scan")
_DESC_MAX = 4096
_FIELD_MAX = 1024


@dataclass(frozen=True)
class OperatorIntent:
    name: str
    mutating: bool
    ticker: str | None = None
    section: str | None = None
    skip_mirofish: bool = False
    skip_edgar: bool = False


@dataclass
class OperatorContext:
    skill_dir: Path
    pending: dict[str, dict[str, Any]] = field(default_factory=dict)
    last_scan: dict[str, Any] = field(default_factory=dict)


@dataclass
class OperatorReply:
    content: str | None = None
    embeds: list[dict[str, Any]] = field(default_factory=list)


def configured_operator_id(env: Mapping[str, str] | None = None) -> str:
    """Process env wins, then the bot's loaded env dict."""
    raw = os.environ.get("DISCORD_USER_ID")
    if raw is not None and str(raw).strip() != "":
        return str(raw).strip()
    if env:
        return str(env.get("DISCORD_USER_ID") or "").strip()
    return ""


def is_authorized_operator(user_id: object, env: Mapping[str, str] | None = None) -> bool:
    """True only when the Discord user matches DISCORD_USER_ID. Unset id fails closed."""
    expected = configured_operator_id(env)
    if not expected or user_id is None:
        return False
    return str(user_id).strip() == expected


def operator_denial(user_id: object, env: Mapping[str, str] | None = None) -> str | None:
    """Denial text for a mutating action, or None when the user may proceed."""
    if is_authorized_operator(user_id, env):
        return None
    if configured_operator_id(env):
        return DENIAL_MESSAGE
    return _UNCONFIGURED_DENIAL


def mutation_denial(
    intent: OperatorIntent,
    user_id: object,
    env: Mapping[str, str] | None = None,
) -> str | None:
    if not intent.mutating:
        return None
    return operator_denial(user_id, env)


def begin_operator_scan() -> bool:
    """Reserve the one Discord-triggered scan. False when one is already reserved."""
    global _operator_scan_active
    with _operator_scan_guard:
        if _operator_scan_active:
            return False
        _operator_scan_active = True
        return True


def end_operator_scan() -> None:
    """Release the Discord scan reservation."""
    global _operator_scan_active
    with _operator_scan_guard:
        _operator_scan_active = False


def help_text() -> str:
    return (
        "**Operator desk**\n"
        "`today` — regime, book, pending confirms, and the signal gate\n"
        "`check TICKER` — quick verdict\n"
        "`book` — positions and P/L\n"
        "`scan` — run the signal scan; the result posts when it finishes\n"
        "`pending` — open confirmations\n"
        "`pause buys` / `resume buys` — halt or resume new buys\n"
        "`status` `sectors` `ping` `help`\n"
        "Signal cards: Approve, Reject, or Check.\n"
        "`report TICKER` is the long desktop report."
    )


def _normalize(text: str) -> str:
    cleaned = _LEADING_MENTION.sub("", text.replace("\u2019", "'"))
    return re.sub(r"\s+", " ", cleaned).strip()


def parse_operator_text(text: str) -> OperatorIntent | None:
    """Parse a confirm-channel message. None means ordinary chat, not a command."""
    raw = _normalize(text or "")
    if not raw:
        return None
    bang = raw.startswith("!")
    body = raw[1:].strip() if bang else raw
    if not body:
        return OperatorIntent("unknown", False) if bang else None

    lowered = body.lower()
    phrase = _PHRASES.get(lowered)
    if phrase:
        return OperatorIntent(phrase, phrase in _MUTATING)

    parts = body.split(" ")
    command = parts[0].lower()
    args = parts[1:]
    if not args and lowered in _SINGLE:
        name = _SINGLE[lowered]
        return OperatorIntent(name, name in _MUTATING)

    if command == "check":
        ticker = args[0] if len(args) == 1 else ""
        if _TICKER.match(ticker):
            return OperatorIntent("check", False, ticker=ticker.upper())
        return OperatorIntent("check", False)

    if command == "report":
        ticker = args[0] if args else ""
        if _TICKER.match(ticker) and len(args) <= 2:
            section = args[1].lower() if len(args) == 2 else None
            return OperatorIntent("report", False, ticker=ticker.upper(), section=section)
        return OperatorIntent("report", False)

    if bang:
        return OperatorIntent("unknown", False)
    return None


def _clip(value: object, limit: int) -> str:
    text = str(value or "")
    if len(text) <= limit:
        return text
    if limit <= 3:
        return text[:limit]
    return text[: limit - 3] + "..."


def _field_value(embed: Mapping[str, Any], name: str) -> str | None:
    fields = embed.get("fields")
    if not isinstance(fields, list):
        return None
    for item in fields:
        if isinstance(item, Mapping) and str(item.get("name") or "") == name:
            value = str(item.get("value") or "").strip()
            if value:
                return value
    return None


def compose_today_embed(
    *,
    status: Mapping[str, Any],
    portfolio: Mapping[str, Any],
    pending_count: int,
    gate_summary: str,
    halted: bool,
    regime_line: str,
    data_quality_line: str,
) -> dict[str, Any]:
    """Phone-sized Today card. The gate line is the caller's quote, not a new calculation."""
    from datetime import datetime, timezone

    halt_line = "New buys paused." if halted else "New buys allowed."
    description = _clip(f"**{halt_line}**\n{gate_summary}".strip(), _DESC_MAX)
    fields: list[dict[str, Any]] = [
        {"name": "Regime", "value": _clip(regime_line or "unavailable", _FIELD_MAX), "inline": False},
        {
            "name": "Data quality",
            "value": _clip(data_quality_line or "unknown", _FIELD_MAX),
            "inline": False,
        },
        {
            "name": "Pending",
            "value": f"**{max(0, int(pending_count))}** open confirmation(s)",
            "inline": True,
        },
    ]
    book = str(portfolio.get("description") or portfolio.get("title") or "Book unavailable.")
    fields.append({"name": "Book", "value": _clip(book, _FIELD_MAX), "inline": False})
    position_names: list[str] = []
    raw_fields = portfolio.get("fields")
    if isinstance(raw_fields, list):
        for item in raw_fields:
            if not isinstance(item, Mapping):
                continue
            name = str(item.get("name") or "").strip()
            if name and name != "Additional Positions":
                position_names.append(name)
    if position_names:
        fields.append(
            {
                "name": "Positions",
                "value": _clip("\n".join(position_names[:5]), _FIELD_MAX),
                "inline": False,
            }
        )
    for name in _STATUS_FIELDS:
        value = _field_value(status, name)
        if value:
            fields.append({"name": name, "value": _clip(value, _FIELD_MAX), "inline": True})

    color = 0xE74C3C if halted else status.get("color") or 0x3498DB
    try:
        color_int = int(color)
    except (TypeError, ValueError):
        color_int = 0x3498DB
    return {
        "title": "Today",
        "description": description,
        "color": color_int,
        "fields": fields[:25],
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "footer": {"text": "Operator desk"},
    }


def compose_pending_embed(pending: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    from datetime import datetime, timezone

    fields: list[dict[str, Any]] = []
    items = list(pending.items())
    for trade_id, trade in items[:10]:
        ticker = str(trade.get("ticker") or "?").upper()
        qty = trade.get("qty")
        price = trade.get("price")
        try:
            price_txt = f"${float(price):,.2f}" if price is not None else "price unavailable"
        except (TypeError, ValueError):
            price_txt = "price unavailable"
        mock = " (mock)" if trade.get("_mock") else ""
        fields.append(
            {
                "name": f"{ticker}{mock}",
                "value": _clip(f"BUY {qty} @ MARKET\nAbout {price_txt}\n`{trade_id}`", _FIELD_MAX),
                "inline": False,
            }
        )
    extra = len(items) - 10
    description = "No open confirmations." if not items else f"**{len(items)}** waiting."
    if extra > 0:
        description += f" +{extra} more not shown."
    return {
        "title": "Pending confirms",
        "description": description,
        "color": 0x3498DB,
        "fields": fields,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "footer": {"text": "Approve on the signal card to send the market buy"},
    }


def describe_regime(skill_dir: Path) -> str:
    try:
        from schwab_auth import DualSchwabAuth
        from sector_strength import is_market_regime_bullish

        bullish, ctx = is_market_regime_bullish(DualSchwabAuth(skill_dir=skill_dir), skill_dir)
    except Exception as exc:
        LOG.warning("Regime line failed: %s", exc)
        return f"unavailable ({type(exc).__name__})"
    if not isinstance(ctx, dict) or ctx.get("data_unavailable"):
        return "unavailable (SPY regime data missing)"
    label = "SPY above 200 SMA" if bullish else "SPY below 200 SMA"
    spy = ctx.get("spy_price")
    sma = ctx.get("spy_sma_200")
    if isinstance(spy, (int, float)) and isinstance(sma, (int, float)):
        return f"{label} (${float(spy):,.2f} vs ${float(sma):,.2f})"
    return label


def describe_data_quality(skill_dir: Path) -> str:
    try:
        from data_health import assess_scan_session_data_health, merge_operator_payload
        from schwab_auth import DualSchwabAuth

        payload = merge_operator_payload(
            assess_scan_session_data_health(DualSchwabAuth(skill_dir=skill_dir), skill_dir)
        )
    except Exception as exc:
        LOG.warning("Data quality line failed: %s", exc)
        return f"unavailable ({type(exc).__name__})"
    quality = str(payload.get("data_quality") or "unknown")
    reasons = [str(item) for item in (payload.get("data_quality_reasons") or [])[:4]]
    if reasons:
        return f"{quality}: " + "; ".join(reasons)
    return quality


def gather_today_embed(skill_dir: Path, *, pending_count: int) -> dict[str, Any]:
    from config import get_user_trading_halted
    from discord_confirm import _build_portfolio_embed, _build_status_embed
    from operator_desk import load_signal_gate

    status = _build_status_embed(skill_dir)
    portfolio = _build_portfolio_embed(skill_dir)
    gate = load_signal_gate(skill_dir)
    summary = str(gate.get("summary") or "").strip() or "phase1_progress_signal_gate.json quote unavailable."
    return compose_today_embed(
        status=status if isinstance(status, dict) else {},
        portfolio=portfolio if isinstance(portfolio, dict) else {},
        pending_count=pending_count,
        gate_summary=summary,
        halted=get_user_trading_halted(skill_dir),
        regime_line=describe_regime(skill_dir),
        data_quality_line=describe_data_quality(skill_dir),
    )


def apply_trading_pause(skill_dir: Path, *, paused: bool) -> str:
    from config import env_user_trading_halted, get_user_trading_halted, set_user_trading_halt_state

    set_user_trading_halt_state(paused, skill_dir)
    LOG.info("Local trading halt file set: paused=%s", paused)
    if paused:
        return "New buys paused."
    if env_user_trading_halted(skill_dir):
        return "Cleared the local pause, but USER_TRADING_HALTED is still set. New buys stay paused."
    if get_user_trading_halted(skill_dir):
        return "New buys stay paused."
    return "New buys resumed."


def _run_check(intent: OperatorIntent) -> OperatorReply:
    if not intent.ticker:
        return OperatorReply(content="Usage: `check TICKER`")
    from full_report import quick_check

    embed = quick_check(intent.ticker)
    if not isinstance(embed, dict):
        return OperatorReply(content=f"Check for **{intent.ticker}** returned no card.")
    return OperatorReply(embeds=[embed])


def _run_report(intent: OperatorIntent) -> OperatorReply:
    if not intent.ticker:
        return OperatorReply(content="Usage: `report TICKER`")
    from full_report import generate_full_report, report_to_discord_sections

    result = generate_full_report(
        intent.ticker,
        skip_mirofish=intent.skip_mirofish,
        skip_edgar=intent.skip_edgar,
    )
    raw = report_to_discord_sections(result, section_filter=intent.section)
    embeds = [item for item in raw if isinstance(item, dict)]
    if not embeds:
        return OperatorReply(content=f"Report for **{intent.ticker}** produced no sections.")
    return OperatorReply(content=f"**Report — {intent.ticker}**", embeds=embeds)


def _run_scan(ctx: OperatorContext) -> OperatorReply:
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from signal_scanner import run_scan_and_notify

    count = run_scan_and_notify(skill_dir=ctx.skill_dir, send_summary=False)
    now = datetime.now(ZoneInfo("America/New_York"))
    ctx.last_scan["time"] = now.strftime("%b %d %I:%M %p ET")
    ctx.last_scan["signals"] = count
    return OperatorReply(content=f"**Scan complete.** Found **{count}** signal(s). Check the channel for alerts.")


def execute_operator_intent(
    intent: OperatorIntent,
    ctx: OperatorContext,
    *,
    authorized: bool,
) -> OperatorReply:
    """Run one desk intent. Mutating intents refuse when authorized is false."""
    if intent.mutating and not authorized:
        return OperatorReply(content=DENIAL_MESSAGE)

    name = intent.name
    if name == "ping":
        return OperatorReply(content="Pong - bot is online.")
    if name in {"help", "unknown"}:
        prefix = "Unknown command.\n" if name == "unknown" else ""
        return OperatorReply(content=prefix + help_text())
    if name == "status":
        from discord_confirm import _build_status_embed

        return OperatorReply(embeds=[_build_status_embed(ctx.skill_dir)])
    if name == "portfolio":
        from discord_confirm import _build_portfolio_embed

        return OperatorReply(embeds=[_build_portfolio_embed(ctx.skill_dir)])
    if name == "sectors":
        from discord_confirm import _build_sectors_embed

        return OperatorReply(embeds=[_build_sectors_embed(ctx.skill_dir)])
    if name == "check":
        return _run_check(intent)
    if name == "report":
        return _run_report(intent)
    if name == "today":
        return OperatorReply(embeds=[gather_today_embed(ctx.skill_dir, pending_count=len(ctx.pending))])
    if name == "pending":
        return OperatorReply(embeds=[compose_pending_embed(ctx.pending)])
    if name == "halt":
        return OperatorReply(content=apply_trading_pause(ctx.skill_dir, paused=True))
    if name == "resume":
        return OperatorReply(content=apply_trading_pause(ctx.skill_dir, paused=False))
    if name == "scan":
        return _run_scan(ctx)
    return OperatorReply(content=help_text())
