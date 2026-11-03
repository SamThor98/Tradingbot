"""Weekday digest of tab-agent versions, promotions, and errors.

Writes ``schwab_skill/.tab_agent_digest/YYYY-MM-DD.md`` and a JSON twin.
A scheduled reader can send the file to the owner. This module does not
place orders or send mail.
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

LOG = logging.getLogger(__name__)

DIGEST_DIR = ".tab_agent_digest"


def _paths(skill_dir: Path | str, day: str) -> tuple[Path, Path]:
    root = Path(skill_dir) / DIGEST_DIR
    return root / f"{day}.md", root / f"{day}.json"


def _tab_ids() -> list[str]:
    tabs = ["today"]
    try:
        from core.debate_tab_agent import DebateTabAgent  # noqa: F401

        tabs.append("debate")
    except Exception:
        pass
    return tabs


def _day_changes(skill_dir: Path, day: str) -> list[dict[str, Any]]:
    from core.tab_agent_governor import load_change_reports

    rows = []
    for row in load_change_reports(skill_dir, limit=500):
        stamp = str(row.get("ts") or "")
        if stamp.startswith(day):
            rows.append(row)
    return rows


def build_digest(skill_dir: Path | str, day: date | None = None) -> dict[str, Any]:
    root = Path(skill_dir)
    stamp = day or datetime.now(timezone.utc).date()
    day_text = stamp.isoformat()
    tabs: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    for tab_id in _tab_ids():
        try:
            from core.tab_agent import get_tab_agent

            agent = get_tab_agent(tab_id)
            status = agent.status(root)
            score = agent.score(root)
        except Exception as exc:
            errors.append({"tab_id": tab_id, "error": str(exc)})
            tabs.append({"tab_id": tab_id, "error": str(exc)})
            continue
        versions = score.get("versions") if isinstance(score.get("versions"), list) else []
        live = [
            {
                "prompt_key": row.get("prompt_key"),
                "version_id": row.get("version_id"),
                "n": row.get("n"),
                "primary_metric": row.get("primary_metric"),
                "primary_score": row.get("primary_score"),
                "max_drawdown": row.get("max_drawdown"),
            }
            for row in versions
            if row.get("status") == "live"
        ]
        events = [row for row in _day_changes(root, day_text) if row.get("tab_id") == tab_id]
        tabs.append(
            {
                "tab_id": tab_id,
                "objective": score.get("objective") or status.get("objective"),
                "live": live,
                "comparisons": score.get("comparisons") or [],
                "events": [
                    {
                        "action": row.get("action"),
                        "prompt_key": row.get("prompt_key"),
                        "version_id": row.get("version_id"),
                        "why": row.get("why"),
                        "before": row.get("before"),
                        "after": row.get("after"),
                        "sample_size": row.get("sample_size"),
                    }
                    for row in events
                ],
                "errors": [],
            }
        )
    return {
        "day": day_text,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "tabs": tabs,
        "errors": errors,
        "note": "Analysis prompts only. No order, size, or execution setting is changed by this digest.",
    }


def _markdown(payload: dict[str, Any]) -> str:
    lines = [
        f"# Tab agent digest {payload.get('day')}",
        "",
        str(payload.get("note") or ""),
        "",
    ]
    for tab in payload.get("tabs") or []:
        lines.append(f"## {tab.get('tab_id')}")
        if tab.get("error"):
            lines.append(f"Error: {tab.get('error')}")
            lines.append("")
            continue
        objective = tab.get("objective") if isinstance(tab.get("objective"), dict) else {}
        if objective.get("statement"):
            lines.append(str(objective.get("statement")))
            lines.append("")
        lines.append("Live versions:")
        for row in tab.get("live") or []:
            lines.append(
                f"- {row.get('prompt_key')} {row.get('version_id')} "
                f"n={row.get('n')} {row.get('primary_metric')}={row.get('primary_score')} "
                f"maxDD={row.get('max_drawdown')}"
            )
        if not tab.get("live"):
            lines.append("- none")
        lines.append("")
        lines.append("Events:")
        events = tab.get("events") or []
        if not events:
            lines.append("- none")
        for event in events:
            before = event.get("before") if isinstance(event.get("before"), dict) else {}
            after = event.get("after") if isinstance(event.get("after"), dict) else {}
            lines.append(
                f"- {event.get('action')} {event.get('prompt_key')} {event.get('version_id')} "
                f"n={event.get('sample_size')} "
                f"before {before.get('primary_metric')}={before.get('primary_score')} n={before.get('n')} "
                f"after {after.get('primary_metric')}={after.get('primary_score')} n={after.get('n')}"
            )
            if event.get("why"):
                lines.append(f"  {event.get('why')}")
        lines.append("")
    if payload.get("errors"):
        lines.append("## Errors")
        for row in payload["errors"]:
            lines.append(f"- {row.get('tab_id')}: {row.get('error')}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def write_daily_digest(skill_dir: Path | str, day: date | None = None) -> dict[str, Any]:
    payload = build_digest(skill_dir, day)
    md_path, json_path = _paths(skill_dir, str(payload["day"]))
    try:
        md_path.parent.mkdir(parents=True, exist_ok=True)
        md_path.write_text(_markdown(payload), encoding="utf-8")
        json_path.write_text(json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8")
    except OSError as exc:
        LOG.warning("Tab-agent digest write failed: %s", exc)
        payload.setdefault("errors", []).append({"tab_id": "*", "error": str(exc)})
    payload["markdown_path"] = str(md_path)
    payload["json_path"] = str(json_path)
    return payload


def read_digest(skill_dir: Path | str, day: str | None = None) -> dict[str, Any] | None:
    stamp = day or datetime.now(timezone.utc).date().isoformat()
    _md_path, json_path = _paths(skill_dir, stamp[:10])
    if not json_path.exists():
        return None
    try:
        payload = json.loads(json_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None
