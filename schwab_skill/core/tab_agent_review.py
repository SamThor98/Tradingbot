"""Review-file contract for external quant and backtest reviewers.

Every proposed prompt version writes:

    schwab_skill/.tab_agent_reviews/<tab>-<version>.json

A reviewer writes the matching verdict file:

    schwab_skill/.tab_agent_reviews/<tab>-<version>.verdict.json

    {"verdict": "approve" | "reject", "notes": "...", "reviewer": "quant"}

Auto-promotion requires metrics that beat live and either an approve verdict
or no verdict file after the configured wait (default 48 hours). A reject
verdict blocks promotion. This module never places an order.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from difflib import unified_diff
from pathlib import Path
from typing import Any

LOG = logging.getLogger(__name__)

REVIEW_DIR = ".tab_agent_reviews"


def _dir(skill_dir: Path | str) -> Path:
    return Path(skill_dir) / REVIEW_DIR


def request_path(skill_dir: Path | str, tab_id: str, version_id: str) -> Path:
    return _dir(skill_dir) / f"{tab_id}-{version_id}.json"


def verdict_path(skill_dir: Path | str, tab_id: str, version_id: str) -> Path:
    return _dir(skill_dir) / f"{tab_id}-{version_id}.verdict.json"


def _diff(parent: str, proposed: str, prompt_key: str) -> str:
    parent_lines = (parent or "").splitlines(keepends=True)
    proposed_lines = (proposed or "").splitlines(keepends=True)
    return "".join(
        unified_diff(
            parent_lines,
            proposed_lines,
            fromfile=f"{prompt_key}.live",
            tofile=f"{prompt_key}.candidate",
        )
    )


def write_review_request(
    skill_dir: Path | str,
    *,
    tab_id: str,
    version: dict[str, Any],
    parent_text: str,
    score_row: dict[str, Any] | None = None,
    objective: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Write the request a reviewer bot reads. Returns the payload."""
    from core.tab_agent_objective import load_objective

    version_id = str(version.get("id") or "")
    row = score_row or {}
    config = objective or load_objective(Path(skill_dir)).as_dict()
    payload = {
        "tab_id": tab_id,
        "version_id": version_id,
        "prompt_key": version.get("prompt_key"),
        "parent_version_id": version.get("parent"),
        "created_at": version.get("created_at"),
        "rationale": version.get("rationale"),
        "diff": _diff(parent_text, str(version.get("text") or ""), str(version.get("prompt_key") or "prompt")),
        "shadow_scores": {
            "n": row.get("n"),
            "primary_metric": row.get("primary_metric"),
            "primary_score": row.get("primary_score"),
            "max_drawdown": row.get("max_drawdown"),
            "max_drawdown_duration_days": row.get("max_drawdown_duration_days"),
            "win_rate": row.get("win_rate"),
            "avg_realized_return_pct": row.get("avg_realized_return_pct"),
        },
        "sample_size": row.get("n"),
        "objective": config,
        "verdict_file": verdict_path(skill_dir, tab_id, version_id).name,
        "verdict_contract": {
            "verdict": "approve or reject",
            "notes": "free text",
            "reviewer": "quant or backtester",
        },
    }
    path = request_path(skill_dir, tab_id, version_id)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8")
    except OSError as exc:
        LOG.warning("Tab-agent review request write failed: %s", exc)
    return payload


def read_verdict(skill_dir: Path | str, tab_id: str, version_id: str) -> dict[str, Any] | None:
    path = verdict_path(skill_dir, tab_id, version_id)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        LOG.warning("Tab-agent verdict read failed: %s", exc)
        return {"verdict": "reject", "notes": f"unreadable verdict file: {exc}", "reviewer": "parser"}
    if not isinstance(payload, dict):
        return {"verdict": "reject", "notes": "verdict file was not an object", "reviewer": "parser"}
    verdict = str(payload.get("verdict") or "").strip().lower()
    if verdict not in {"approve", "reject"}:
        return {"verdict": "reject", "notes": "verdict must be approve or reject", "reviewer": "parser"}
    payload["verdict"] = verdict
    return payload


def _parse_ts(raw: Any) -> datetime | None:
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        stamp = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp.astimezone(timezone.utc)


def review_gate(
    skill_dir: Path | str,
    tab_id: str,
    version: dict[str, Any],
    *,
    wait_hours: int,
    now: datetime | None = None,
) -> str:
    """Return approved, rejected, waiting, or elapsed."""
    verdict = read_verdict(skill_dir, tab_id, str(version.get("id") or ""))
    if verdict is not None:
        return "approved" if verdict.get("verdict") == "approve" else "rejected"
    created = _parse_ts(version.get("created_at"))
    if created is None:
        return "waiting"
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    if current - created >= timedelta(hours=max(1, int(wait_hours))):
        return "elapsed"
    return "waiting"
