"""Autonomous shadow, promotion, and rollback for tab agents.

A shadow candidate is promoted only when ``compare_books`` says it beats live
on the shared risk-adjusted objective with a large enough sample and without
a materially worse drawdown. A promoted version is rolled back when its
post-promotion window degrades versus the predecessor on that same rule.
Every change appends a report Sam can read. Manual approve and rollback
remain overrides and are labeled as such.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.tab_agent_objective import compare_books, load_objective

LOG = logging.getLogger(__name__)

CHANGE_LOG_FILE = ".tab_agent_changes.jsonl"
DEGRADED = {"worse_than_live", "drawdown_worse", "drawdown_duration_worse"}


def load_change_reports(skill_dir: Path | str, *, limit: int = 30) -> list[dict[str, Any]]:
    path = Path(skill_dir) / CHANGE_LOG_FILE
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except Exception as exc:
        LOG.warning("Tab-agent change log read failed: %s", exc)
        return []
    for line in lines:
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows[-max(1, int(limit)) :]


def write_change_report(skill_dir: Path | str, report: dict[str, Any]) -> dict[str, Any]:
    path = Path(skill_dir) / CHANGE_LOG_FILE
    body = dict(report)
    body.setdefault("ts", datetime.now(timezone.utc).isoformat())
    body["summary"] = render_change_report(body)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(body, default=str) + "\n")
    except Exception as exc:
        LOG.warning("Tab-agent change log write failed: %s", exc)
    LOG.info("Tab agent %s: %s", body.get("action"), body.get("summary"))
    return body


def render_change_report(report: dict[str, Any]) -> str:
    before = report.get("before") if isinstance(report.get("before"), dict) else {}
    after = report.get("after") if isinstance(report.get("after"), dict) else {}
    return "\n".join(
        [
            f"{str(report.get('action') or '').upper()} {report.get('tab_id')} {report.get('prompt_key')} {report.get('version_id')}",
            f"Why: {report.get('why')}",
            f"Sample: candidate n={after.get('n')} live/predecessor n={before.get('n')}",
            (
                "Before: "
                f"{before.get('primary_metric')}={before.get('primary_score')} "
                f"maxDD={before.get('max_drawdown')} duration_days={before.get('max_drawdown_duration_days')} "
                f"win_rate={before.get('win_rate')} avg_return_pct={before.get('avg_realized_return_pct')}"
            ),
            (
                "After: "
                f"{after.get('primary_metric')}={after.get('primary_score')} "
                f"maxDD={after.get('max_drawdown')} duration_days={after.get('max_drawdown_duration_days')} "
                f"win_rate={after.get('win_rate')} avg_return_pct={after.get('avg_realized_return_pct')}"
            ),
            f"What changed: {report.get('what_changed')}",
        ]
    )


def _stats_for(report: dict[str, Any], version_id: str) -> dict[str, Any]:
    for row in report.get("versions") or []:
        if row.get("version_id") == version_id:
            return row
    return {}


def _brief(stats: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "n",
        "primary_metric",
        "primary_score",
        "primary_unbounded",
        "max_drawdown",
        "max_drawdown_duration_days",
        "win_rate",
        "avg_realized_return_pct",
    )
    return {key: stats.get(key) for key in keys}


def govern_agent(agent: Any, skill_dir: Path) -> dict[str, Any]:
    """Promote shadow winners and roll back degraded promotions. Analysis prompts only."""
    from core.tab_agent import STATUS_LIVE, STATUS_SHADOW

    score = agent.score(skill_dir)
    actions: list[dict[str, Any]] = []
    reg = agent.registry(skill_dir)
    config = load_objective(skill_dir)

    for version in list(reg.versions(agent.tab_id)):
        if version.get("status") != STATUS_LIVE or not version.get("predecessor_id") or not version.get("promoted_at"):
            continue
        window = agent.score(skill_dir, since_ts=str(version.get("promoted_at")))
        current = _stats_for(window, str(version.get("id")))
        frozen = version.get("predecessor_stats") if isinstance(version.get("predecessor_stats"), dict) else {}
        if not frozen or int(current.get("n") or 0) < config.min_samples:
            continue
        label = compare_books(current, frozen, config=config)
        if label not in DEGRADED:
            continue
        predecessor_id = str(version.get("predecessor_id"))
        reg.rollback(agent.tab_id, predecessor_id)
        actions.append(
            write_change_report(
                skill_dir,
                {
                    "tab_id": agent.tab_id,
                    "action": "auto_rollback",
                    "prompt_key": version.get("prompt_key"),
                    "version_id": predecessor_id,
                    "replaced_version_id": version.get("id"),
                    "why": f"Post-promotion window is {label} versus the predecessor book.",
                    "before": _brief(frozen),
                    "after": _brief(current),
                    "what_changed": "Restored the predecessor analysis prompt. No order, size, or execution setting was changed.",
                    "sample_size": current.get("n"),
                },
            )
        )

    score = agent.score(skill_dir)
    by_id = {row.get("version_id"): row for row in score.get("versions") or []}
    from core.tab_agent_review import review_gate

    for comparison in score.get("comparisons") or []:
        if comparison.get("edge_vs_live") != "better_than_live":
            continue
        version_id = str(comparison.get("version_id") or "")
        candidate = reg.get(agent.tab_id, version_id)
        if candidate is None or candidate.get("status") != STATUS_SHADOW:
            continue
        gate = review_gate(
            skill_dir,
            agent.tab_id,
            candidate,
            wait_hours=config.review_wait_hours,
        )
        if gate == "rejected":
            reg.reject(agent.tab_id, version_id)
            actions.append(
                write_change_report(
                    skill_dir,
                    {
                        "tab_id": agent.tab_id,
                        "action": "review_reject",
                        "prompt_key": candidate.get("prompt_key"),
                        "version_id": version_id,
                        "why": "External review verdict rejected this prompt. Metrics were not enough.",
                        "before": _brief(by_id.get(version_id) or {}),
                        "after": {},
                        "what_changed": "Shadow prompt retired. No order, size, or execution setting was changed.",
                        "sample_size": (by_id.get(version_id) or {}).get("n"),
                    },
                )
            )
            continue
        if gate == "waiting":
            continue
        prompt_key = str(candidate.get("prompt_key") or "")
        live = reg.live_version(agent.tab_id, prompt_key)
        if live is None:
            continue
        why = (
            "Shadow beat live on the risk-adjusted objective inside the drawdown guardrail, "
            "with a sufficient sample, and the reviewer "
            + ("approved it." if gate == "approved" else f"left no verdict for {config.review_wait_hours}h.")
        )
        reg.promote(
            agent.tab_id,
            version_id,
            predecessor_stats=_brief(by_id.get(live.get("id")) or {}),
        )
        actions.append(
            write_change_report(
                skill_dir,
                {
                    "tab_id": agent.tab_id,
                    "action": "auto_promote",
                    "prompt_key": prompt_key,
                    "version_id": version_id,
                    "predecessor_id": live.get("id"),
                    "why": why,
                    "before": _brief(by_id.get(live.get("id")) or {}),
                    "after": _brief(by_id.get(version_id) or {}),
                    "what_changed": str(candidate.get("rationale") or "Analysis prompt text replaced."),
                    "sample_size": (by_id.get(version_id) or {}).get("n"),
                },
            )
        )
    return {"tab_id": agent.tab_id, "actions": actions, "auto_promote": True}
