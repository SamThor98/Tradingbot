"""Harvest plugin shadow evidence after scans and build Discord fact packets.

Does not flip plugin modes, place orders, or send Discord itself. Callers persist
``last_scan`` and post briefs. Never skip ``data_quality == ok`` for session counts.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from core.plugin_shadow_evidence import (
    build_plugin_shadow_record,
    evidence_session_date,
    evidence_session_is_rth,
    load_plugin_shadow_records,
    upsert_plugin_shadow_record,
)

LOG = logging.getLogger(__name__)

TZ_NY = ZoneInfo("America/New_York")
STATE_DIRNAME = "experiment_collector"
HARVEST_STATE_FILENAME = "harvest_state.json"
CAMPAIGN_STATE_FILENAME = "campaign_state.json"
LOCAL_USER_ID = "local"
LAST_SCAN_SIGNALS_CAP = 120
SHADOW_MODES = frozenset({"shadow"})


def collector_state_dir(skill_dir: Path) -> Path:
    return Path(skill_dir) / "validation_artifacts" / STATE_DIRNAME


def harvest_state_path(skill_dir: Path) -> Path:
    return collector_state_dir(skill_dir) / HARVEST_STATE_FILENAME


def campaign_state_path(skill_dir: Path) -> Path:
    return collector_state_dir(skill_dir) / CAMPAIGN_STATE_FILENAME


def today_et(now: datetime | None = None) -> str:
    clock = now or datetime.now(TZ_NY)
    if clock.tzinfo is None:
        clock = clock.replace(tzinfo=TZ_NY)
    return clock.astimezone(TZ_NY).date().isoformat()


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8")


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return raw if isinstance(raw, dict) else {}


def persist_local_last_scan(
    skill_dir: Path,
    *,
    diagnostics: dict[str, Any],
    signals: list[dict[str, Any]] | None = None,
    signals_found: int | None = None,
    scan_at: str | None = None,
    sqlite_path: Path | None = None,
) -> bool:
    """Write AppState last_scan without importing webapp.main (bot-safe)."""
    db_path = sqlite_path or (Path(skill_dir) / "webapp" / "webapp.db")
    if not db_path.exists():
        LOG.debug("last_scan persist skipped: no sqlite at %s", db_path)
        return False
    finished_at = scan_at or datetime.now(timezone.utc).isoformat()
    rows = list(signals or [])[:LAST_SCAN_SIGNALS_CAP]
    last_scan = {
        "at": finished_at,
        "signals_found": int(signals_found if signals_found is not None else len(signals or [])),
        "signals": rows,
        "diagnostics": diagnostics,
    }
    try:
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker

        from webapp.models import AppState

        engine = create_engine(
            f"sqlite:///{db_path.resolve().as_posix()}",
            connect_args={"check_same_thread": False},
        )
        Session = sessionmaker(bind=engine)
        db = Session()
        try:
            existing = db.query(AppState).filter(AppState.user_id == LOCAL_USER_ID, AppState.key == "last_scan").first()
            if existing is not None and isinstance(existing.value_json, dict) and existing.value_json.get("signals"):
                prev = db.query(AppState).filter(AppState.user_id == LOCAL_USER_ID, AppState.key == "prev_scan").first()
                if prev is None:
                    db.add(
                        AppState(
                            user_id=LOCAL_USER_ID,
                            key="prev_scan",
                            value_json=existing.value_json,
                        )
                    )
                else:
                    prev.value_json = existing.value_json
            if existing is None:
                db.add(AppState(user_id=LOCAL_USER_ID, key="last_scan", value_json=last_scan))
            else:
                existing.value_json = last_scan
            db.commit()
        finally:
            db.close()
        return True
    except Exception:
        LOG.warning("last_scan persist failed", exc_info=True)
        return False


def campaign_plugins(skill_dir: Path) -> list[dict[str, Any]]:
    """Plugins currently in SHADOW that have a session evidence_key."""
    from core.plugin_mode_workbench import (
        DEFAULT_SESSION_TARGET,
        count_ok_sessions,
        plugin_catalog,
        resolve_modes,
    )

    modes = resolve_modes(skill_dir)
    records = load_plugin_shadow_records(skill_dir)
    out: list[dict[str, Any]] = []
    for spec in plugin_catalog():
        if not spec.evidence_key:
            continue
        mode = str(modes.get(spec.id) or "off").strip().lower()
        if mode not in SHADOW_MODES:
            continue
        collected = count_ok_sessions(records, spec)
        target = int(spec.session_target or DEFAULT_SESSION_TARGET)
        out.append(
            {
                "id": spec.id,
                "label": spec.label,
                "mode": mode,
                "collected": collected,
                "target": target,
                "ok": collected >= target,
            }
        )
    return out


def campaign_key(plugins: list[dict[str, Any]]) -> str:
    return ",".join(sorted(str(p.get("id") or "") for p in plugins if p.get("id")))


def _harvest_scan(
    skill_dir: Path,
    diagnostics: dict[str, Any] | None,
    *,
    signals: list[dict[str, Any]] | None = None,
    signals_found: int | None = None,
    scan_at: str | None = None,
    scan_label: str | None = None,
    persist_last_scan: bool = False,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Append/upsert plugin shadow evidence from a completed scan.

    Counts a session only when ``data_quality == ok``, the scan is during
    weekday Regular Trading Hours (09:30–16:00 ET), and at least one wired
    plugin is not off. Same ET date replaces the prior row, except a
    non-RTH scan never overwrites an RTH row.
    """
    from config import get_experiment_collector_enabled

    et_date = today_et(now)
    finished_at = scan_at or datetime.now(timezone.utc).isoformat()
    found = int(signals_found if signals_found is not None else len(signals or []))
    diag = diagnostics if isinstance(diagnostics, dict) else {}
    dq = str(diag.get("data_quality") or "").strip().lower()

    if persist_last_scan:
        persist_local_last_scan(
            skill_dir,
            diagnostics=diag,
            signals=signals,
            signals_found=found,
            scan_at=finished_at,
        )

    result: dict[str, Any] = {
        "et_date": et_date,
        "scan_at": finished_at,
        "signals_found": found,
        "data_quality": diag.get("data_quality"),
        "counted": False,
        "reason": "ok",
        "action": None,
        "enabled": True,
        "hypotheses_appended": 0,
        "hypotheses_skipped": 0,
        "s1_hypotheses_appended": 0,
        "s1_hypotheses_skipped": 0,
    }

    if not get_experiment_collector_enabled(skill_dir):
        result["enabled"] = False
        result["reason"] = "disabled"
        _write_json(harvest_state_path(skill_dir), result)
        return result

    if dq == "ok":
        try:
            from config import get_hypothesis_ledger_enabled
            from hypothesis_ledger import (
                append_hypotheses_from_canary_sleeve,
                append_hypotheses_from_signals,
            )

            if get_hypothesis_ledger_enabled(skill_dir):
                hypo = append_hypotheses_from_signals(
                    signals,
                    skill_dir,
                    et_date=et_date,
                    scan_at=finished_at,
                )
                result["hypotheses_appended"] = int(hypo.get("appended") or 0)
                result["hypotheses_skipped"] = int(hypo.get("skipped") or 0)
                s1 = append_hypotheses_from_canary_sleeve(
                    diag,
                    skill_dir,
                    et_date=et_date,
                    scan_at=finished_at,
                )
                result["s1_hypotheses_appended"] = int(s1.get("appended") or 0)
                result["s1_hypotheses_skipped"] = int(s1.get("skipped") or 0)
        except Exception as exc:
            LOG.warning("Hypothesis harvest failed", exc_info=True)
            result["hypothesis_error"] = type(exc).__name__

    record = build_plugin_shadow_record(
        diag,
        scan_at=finished_at,
        signals_found=found,
        scan_label=scan_label,
    )
    if record is None:
        result["reason"] = "plugins_off"
        _write_json(harvest_state_path(skill_dir), result)
        return result

    if dq != "ok":
        result["reason"] = "data_quality_not_ok"
        _write_json(harvest_state_path(skill_dir), result)
        return result

    if not evidence_session_is_rth({"scan_at": finished_at}):
        result["reason"] = "not_rth"
        _write_json(harvest_state_path(skill_dir), result)
        return result

    upsert = upsert_plugin_shadow_record(skill_dir, record)
    result["counted"] = True
    result["reason"] = "counted"
    result["action"] = upsert.get("action")
    result["et_date"] = upsert.get("et_date") or et_date
    _write_json(harvest_state_path(skill_dir), result)
    return result


def harvest_scan(
    skill_dir: Path,
    diagnostics: dict[str, Any] | None,
    *,
    signals: list[dict[str, Any]] | None = None,
    signals_found: int | None = None,
    scan_at: str | None = None,
    scan_label: str | None = None,
    persist_last_scan: bool = False,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Harvest one scan. A failure is stored as ``harvest_failed`` and returned.

    Callers keep scanning. The 16:15 brief pings when the stored reason is
    neither ``plugins_off`` nor ``disabled``.
    """
    try:
        return _harvest_scan(
            skill_dir,
            diagnostics,
            signals=signals,
            signals_found=signals_found,
            scan_at=scan_at,
            scan_label=scan_label,
            persist_last_scan=persist_last_scan,
            now=now,
        )
    except Exception as exc:
        LOG.exception("Experiment harvest failed")
        et_date = today_et(now)
        if scan_at:
            parsed = evidence_session_date({"scan_at": scan_at})
            if parsed:
                et_date = parsed
        found = int(signals_found if signals_found is not None else len(signals or []))
        result = {
            "et_date": et_date,
            "scan_at": scan_at or datetime.now(timezone.utc).isoformat(),
            "signals_found": found,
            "data_quality": (diagnostics or {}).get("data_quality") if isinstance(diagnostics, dict) else None,
            "counted": False,
            "reason": "harvest_failed",
            "action": None,
            "enabled": None,
            "error": type(exc).__name__,
            "hypotheses_appended": 0,
            "hypotheses_skipped": 0,
            "s1_hypotheses_appended": 0,
            "s1_hypotheses_skipped": 0,
        }
        try:
            _write_json(harvest_state_path(skill_dir), result)
        except Exception:
            LOG.exception("Could not persist harvest failure")
        return result


def load_harvest_state(skill_dir: Path) -> dict[str, Any]:
    return _read_json(harvest_state_path(skill_dir))


def _r7_snapshot(skill_dir: Path) -> dict[str, Any] | None:
    """Sleeve-local T+5 progress. None when the ledger cannot be read."""
    try:
        from core.multi_sleeve_constitution import R7_MIN_N, R7_PRIMARY_HORIZON
        from core.r7_promotion import summarize_sleeve_local
        from hypothesis_ledger import load_ledger_data

        data = load_ledger_data(skill_dir)
        records = [r for r in (data.get("records") or []) if isinstance(r, dict)]
        horizon = str(R7_PRIMARY_HORIZON)
        out: dict[str, Any] = {}
        for sleeve in ("S0", "S1"):
            row = summarize_sleeve_local(records, sleeve_id=sleeve, horizon=horizon)
            row["min_n"] = int(R7_MIN_N)
            out[sleeve] = row
        return out
    except Exception:
        LOG.warning("R7 snapshot failed", exc_info=True)
        return None


def build_brief_packet(
    skill_dir: Path,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Structured facts for the 16:15 Discord brief."""
    et_date = today_et(now)
    harvest = load_harvest_state(skill_dir)
    plugins = campaign_plugins(skill_dir)
    harvest_date = str(harvest.get("et_date") or "")
    scan_today = harvest_date == et_date
    dq = str(harvest.get("data_quality") or "")
    counted = bool(harvest.get("counted")) if scan_today else False
    reason = str(harvest.get("reason") or "missing_harvest")
    if not scan_today:
        reason = "missing_harvest"
    ping = (not scan_today) or (scan_today and not counted and reason != "plugins_off" and reason != "disabled")
    complete = bool(plugins) and all(bool(p.get("ok")) for p in plugins)
    key = campaign_key(plugins)
    prior = _read_json(campaign_state_path(skill_dir))
    already = str(prior.get("notified_key") or "") == key and complete
    campaign_fire = complete and bool(key) and not already
    return {
        "et_date": et_date,
        "scan_today": scan_today,
        "counted": counted,
        "reason": reason,
        "data_quality": dq if scan_today else None,
        "signals_found": harvest.get("signals_found") if scan_today else None,
        "plugins": plugins,
        "campaign_key": key,
        "campaign_complete": complete,
        "campaign_fire": campaign_fire,
        "ping": ping,
        "enabled": harvest.get("enabled", True) if scan_today else True,
        "hypotheses_appended": harvest.get("hypotheses_appended") if scan_today else None,
        "hypotheses_skipped": harvest.get("hypotheses_skipped") if scan_today else None,
        "error": harvest.get("error") if scan_today else None,
        "hypothesis_error": harvest.get("hypothesis_error") if scan_today else None,
        "r7": _r7_snapshot(skill_dir),
    }


def mark_campaign_notified(skill_dir: Path, packet: dict[str, Any]) -> None:
    _write_json(
        campaign_state_path(skill_dir),
        {
            "notified_key": packet.get("campaign_key"),
            "notified_at": datetime.now(timezone.utc).isoformat(),
            "et_date": packet.get("et_date"),
        },
    )


def format_daily_brief(packet: dict[str, Any]) -> str:
    lines = [
        f"**{packet.get('et_date')}** experiment harvest",
        f"Counted: **{'yes' if packet.get('counted') else 'no'}** ({packet.get('reason')})",
    ]
    dq = packet.get("data_quality")
    if dq:
        lines.append(f"data_quality: **{dq}**")
    found = packet.get("signals_found")
    if found is not None:
        lines.append(f"Signals: **{found}**")
    appended = packet.get("hypotheses_appended")
    skipped = packet.get("hypotheses_skipped")
    if appended is not None or skipped is not None:
        lines.append(
            f"Hypotheses: **{int(appended or 0)}** appended, {int(skipped or 0)} skipped"
        )
    plugins = packet.get("plugins") or []
    if not plugins:
        lines.append("Campaign set: none (no SHADOW+wired plugins)")
    else:
        bits = [f"{p.get('id')} {p.get('collected')}/{p.get('target')}" for p in plugins]
        lines.append("Sessions: " + ", ".join(bits))
    lines.append("Review the shadow scoreboard. Do not promote.")
    err = packet.get("error")
    if err:
        lines.append(f"Harvest error: **{err}**")
    hypo_err = packet.get("hypothesis_error")
    if hypo_err:
        lines.append(f"Hypothesis harvest error: **{hypo_err}**")
    lines.extend(_r7_brief_lines(packet.get("r7")))
    return "\n".join(lines)


def _r7_brief_lines(r7: Any) -> list[str]:
    lines = [
        "Open question: sleeve-local T+5 expectancy. One change. Do not promote.",
    ]
    if not isinstance(r7, dict):
        lines.append("R7: unread")
        return lines
    for sleeve in ("S0", "S1"):
        row = r7.get(sleeve) if isinstance(r7.get(sleeve), dict) else {}
        n = int(row.get("n") or 0)
        need = int(row.get("min_n") or 40)
        hit = row.get("hit_rate")
        exp = row.get("expectancy")
        hit_s = "n/a" if hit is None else f"{float(hit):.0%}"
        exp_s = "n/a" if exp is None else f"{float(exp):+.2f}%"
        lines.append(f"{sleeve} T+5 **{n}/{need}** hit {hit_s} mean {exp_s}")
    return lines


def format_campaign_brief(packet: dict[str, Any]) -> str:
    plugins = packet.get("plugins") or []
    bits = [f"{p.get('id')} {p.get('collected')}/{p.get('target')}" for p in plugins]
    body = (
        f"Campaign complete for **{packet.get('et_date')}**.\n"
        f"SHADOW+wired sessions: {', '.join(bits) or 'none'}.\n"
        "Review the shadow scoreboard. Session count is not a promotion gate. Do not promote."
    )
    return body + "\n" + "\n".join(_r7_brief_lines(packet.get("r7")))


def deliver_experiment_briefs(
    skill_dir: Path,
    *,
    now: datetime | None = None,
    notify: bool = True,
) -> dict[str, Any]:
    """Build and optionally post daily + campaign Discord briefs."""
    from config import get_experiment_collector_enabled
    from notifier import send_alert

    if not get_experiment_collector_enabled(skill_dir):
        return {"skipped": True, "reason": "disabled"}

    packet = build_brief_packet(skill_dir, now=now)
    posted: list[str] = []
    if notify:
        kind = "experiment_brief"
        daily_kind = "data_failure" if packet.get("ping") else kind
        sent = send_alert(
            format_daily_brief(packet),
            kind=daily_kind,
            env_path=Path(skill_dir) / ".env",
            operator_context={
                "data_quality": packet.get("data_quality"),
            },
        )
        if sent:
            posted.append("daily")
        if packet.get("campaign_fire"):
            camp_ok = send_alert(
                format_campaign_brief(packet),
                kind=kind,
                env_path=Path(skill_dir) / ".env",
            )
            if camp_ok:
                mark_campaign_notified(skill_dir, packet)
                posted.append("campaign")
    return {"skipped": False, "packet": packet, "posted": posted}
