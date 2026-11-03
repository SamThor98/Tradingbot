"""Weekday Discord brief of the Tradeable scan rows.

The Tradeable bucket matches the dashboard filter: a kept row that can be
staged. Research sleeves, weekly and monthly horizons, and filtered rows stay
out. The brief does not start a scan and does not place an order.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

LOG = logging.getLogger(__name__)

SLOT_NAME = "tradeable_brief_slot.txt"
_RESEARCH_FAMILIES = frozenset({"horizon", "pead_primary"})
_RESEARCH_TIMEFRAMES = frozenset({"weekly", "monthly"})
_FIELD_VALUE_MAX = 1000


def brief_timezone(name: str) -> ZoneInfo:
    """Resolve the brief zone. An unknown name falls back to Chicago."""
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        LOG.warning("Tradeable brief timezone unknown: %s", name)
        return ZoneInfo("America/Chicago")


def is_tradeable_row(row: dict[str, Any] | None) -> bool:
    """True for a dashboard Tradeable row (kept and stageable)."""
    if not isinstance(row, dict):
        return False
    if row.get("executable") is False:
        return False
    family = str(row.get("entry_family") or "").strip().lower()
    if family in _RESEARCH_FAMILIES:
        return False
    attr = row.get("strategy_attribution") if isinstance(row.get("strategy_attribution"), dict) else {}
    timeframe = str(attr.get("timeframe") or row.get("scan_timeframe") or row.get("timeframe") or "").strip().lower()
    if timeframe in _RESEARCH_TIMEFRAMES:
        return False
    status = str(row.get("_filter_status") or "kept").strip().lower() or "kept"
    return status == "kept"


def tradeable_rows(scan: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Tradeable rows from the shortlist the Today tab uses, else final signals."""
    if not isinstance(scan, dict):
        return []
    shortlist = scan.get("shortlist_signals")
    source = shortlist if isinstance(shortlist, list) and shortlist else scan.get("signals")
    rows = [row for row in source or [] if is_tradeable_row(row)]
    rows.sort(
        key=lambda row: (
            -float(row.get("signal_score") or 0.0),
            str(row.get("ticker") or row.get("symbol") or ""),
        )
    )
    return rows


def tradeable_slot_key(now: datetime, *, hour: int, minute: int, tz: ZoneInfo) -> str | None:
    """Weekday slot id in the brief timezone. Other minutes return None."""
    local = now.replace(tzinfo=tz) if now.tzinfo is None else now.astimezone(tz)
    if local.weekday() >= 5 or local.hour != hour or local.minute != minute:
        return None
    return local.strftime("%Y-%m-%dT%H%M")


def claim_tradeable_slot(skill_dir: Path, key: str) -> bool:
    """True the first time this slot is claimed."""
    path = Path(skill_dir) / SLOT_NAME
    try:
        if path.is_file() and path.read_text(encoding="utf-8").strip() == key:
            return False
        path.write_text(key + "\n", encoding="utf-8")
    except Exception as exc:
        LOG.warning("Tradeable brief slot claim failed: %s", exc)
        return False
    return True


def _parse_when(raw: Any) -> datetime | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def _format_when(raw: Any, tz: ZoneInfo) -> str:
    parsed = _parse_when(raw)
    if parsed is None:
        return "unknown time"
    return parsed.astimezone(tz).strftime("%Y-%m-%d %H:%M %Z")


def _sleeve(row: dict[str, Any]) -> str:
    attr = row.get("strategy_attribution") if isinstance(row.get("strategy_attribution"), dict) else {}
    names = attr.get("passed_names")
    if isinstance(names, list):
        for name in names:
            text = str(name or "").strip()
            if text:
                return text
    return str(attr.get("top_live") or "").strip()


def _name_line(row: dict[str, Any]) -> str:
    ticker = str(row.get("ticker") or row.get("symbol") or "?").strip().upper() or "?"
    score = row.get("signal_score")
    try:
        score_txt = f"{float(score):.0f}"
    except (TypeError, ValueError):
        score_txt = "n/a"
    parts = [f"**{ticker}** {score_txt}"]
    sleeve = _sleeve(row)
    if sleeve:
        parts.append(sleeve)
    return " · ".join(parts)


def _data_quality(scan: dict[str, Any]) -> tuple[Any, list[Any]]:
    for key in ("diagnostics", "diagnostics_summary"):
        body = scan.get(key)
        if isinstance(body, dict) and body.get("data_quality") is not None:
            reasons = body.get("data_quality_reasons") or []
            return body.get("data_quality"), list(reasons) if isinstance(reasons, list) else []
    return None, []


def _pack_lines(lines: list[str]) -> list[dict[str, Any]]:
    fields: list[dict[str, Any]] = []
    bucket: list[str] = []
    size = 0
    for line in lines:
        extra = len(line) + (1 if bucket else 0)
        if bucket and size + extra > _FIELD_VALUE_MAX:
            fields.append(
                {
                    "name": "Tradeable" if not fields else "Tradeable (cont.)",
                    "value": "\n".join(bucket),
                    "inline": False,
                }
            )
            bucket = [line]
            size = len(line)
        else:
            bucket.append(line)
            size += extra
    if bucket:
        fields.append(
            {"name": "Tradeable" if not fields else "Tradeable (cont.)", "value": "\n".join(bucket), "inline": False}
        )
    return fields


def compose_tradeable_embed(
    scan: dict[str, Any] | None,
    *,
    now: datetime,
    tz: ZoneInfo,
) -> dict[str, Any]:
    """One Discord embed. Quotes the stored scan. Sends no order."""
    body = scan if isinstance(scan, dict) else {}
    rows = tradeable_rows(body)
    scanned = _parse_when(body.get("at"))
    when_txt = _format_when(body.get("at"), tz)
    local_now = now.astimezone(tz) if now.tzinfo else now.replace(tzinfo=tz)
    clock = local_now.strftime("%I:%M %p").lstrip("0")
    zone_label = "CT" if tz.key == "America/Chicago" else tz.key
    parts = [
        f"{clock} {zone_label} brief of the Tradeable list.",
        f"Latest scan: {when_txt}.",
    ]
    if scanned is not None and scanned.astimezone(tz).date() != local_now.date():
        parts.append("That scan is not from this morning.")
    elif scanned is None:
        parts.append("No scan is stored.")
    noun = "name" if len(rows) == 1 else "names"
    parts.append(f"{len(rows)} tradeable {noun}.")
    quality, reasons = _data_quality(body)
    if quality is not None and str(quality).strip().lower() != "ok":
        parts.append(f"data_quality={quality}")
        reason_txt = "; ".join(str(item) for item in reasons[:3] if str(item).strip())
        if reason_txt:
            parts.append(reason_txt)
    parts.append("No orders.")
    fields = _pack_lines([_name_line(row) for row in rows])
    if not fields:
        fields = [{"name": "Tradeable", "value": "None.", "inline": False}]
    return {
        "title": "Tradeable universe",
        "description": "\n".join(parts),
        "color": 0x2ECC71,
        "fields": fields,
        "footer": {"text": "No order sent"},
    }


def load_persisted_last_scan(skill_dir: Path) -> dict[str, Any] | None:
    """Read AppState last_scan from the local dashboard sqlite, if present."""
    db_path = Path(skill_dir) / "webapp" / "webapp.db"
    if not db_path.is_file():
        return None
    try:
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker

        from core.experiment_collector import LOCAL_USER_ID
        from webapp.models import AppState

        engine = create_engine(
            f"sqlite:///{db_path.resolve().as_posix()}",
            connect_args={"check_same_thread": False},
        )
        session = sessionmaker(bind=engine)()
        try:
            row = session.query(AppState).filter(AppState.user_id == LOCAL_USER_ID, AppState.key == "last_scan").first()
            value = row.value_json if row is not None else None
        finally:
            session.close()
    except Exception as exc:
        LOG.warning("Tradeable brief could not read last_scan: %s", exc)
        return None
    return value if isinstance(value, dict) else None


def deliver_tradeable_brief(
    skill_dir: Path,
    scan: dict[str, Any] | None,
    *,
    now: datetime,
    tz: ZoneInfo,
    sender: Callable[[dict[str, Any]], bool] | None = None,
) -> dict[str, Any]:
    """Post the embed. Returns the brief payload including whether Discord accepted it."""
    embed = compose_tradeable_embed(scan, now=now, tz=tz)
    sent = False
    if sender is not None:
        sent = bool(sender(embed))
    else:
        from notifier import send_embed_alert

        sent = bool(send_embed_alert(embed, env_path=Path(skill_dir) / ".env"))
    rows = tradeable_rows(scan)
    tickers = [str(row.get("ticker") or row.get("symbol") or "").strip().upper() for row in rows]
    LOG.info("Tradeable brief posted: sent=%s count=%s", sent, len(tickers))
    return {
        "discord_sent": sent,
        "count": len(tickers),
        "tickers": [ticker for ticker in tickers if ticker],
        "embed": embed,
    }


def tick_tradeable_brief(
    skill_dir: Path,
    now: datetime | None = None,
    *,
    scan_reader: Callable[[], dict[str, Any] | None] | None = None,
    sender: Callable[[dict[str, Any]], bool] | None = None,
) -> bool:
    """Post once when the brief clock hits the slot. Returns True if this process claimed it."""
    from config import (
        get_tradeable_brief_enabled,
        get_tradeable_brief_hour,
        get_tradeable_brief_minute,
        get_tradeable_brief_timezone,
    )

    if not get_tradeable_brief_enabled(skill_dir):
        return False
    tz = brief_timezone(get_tradeable_brief_timezone(skill_dir))
    current = now or datetime.now(tz)
    key = tradeable_slot_key(
        current,
        hour=get_tradeable_brief_hour(skill_dir),
        minute=get_tradeable_brief_minute(skill_dir),
        tz=tz,
    )
    if key is None or not claim_tradeable_slot(skill_dir, key):
        return False
    scan = None
    if scan_reader is not None:
        try:
            scan = scan_reader()
        except Exception as exc:
            LOG.warning("Tradeable brief scan reader failed: %s", exc)
    if not isinstance(scan, dict):
        scan = load_persisted_last_scan(skill_dir)
    try:
        deliver_tradeable_brief(skill_dir, scan, now=current, tz=tz, sender=sender)
    except Exception as exc:
        LOG.warning("Tradeable brief failed: %s", exc)
    return True
