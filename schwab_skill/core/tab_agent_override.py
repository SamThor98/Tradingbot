"""Manual override for tab agents. Analysis prompts only.

``approve`` and ``reject`` write the review verdict file with ``reviewer``
``sam``. ``promote`` and ``rollback`` change which prompt text is live.
Nothing in this module places an order or changes size.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from core.tab_agent import STATUS_PENDING, STATUS_RETIRED, STATUS_SHADOW, TabAgentError, get_tab_agent
from core.tab_agent_review import read_verdict, write_verdict

TABS = ("today", "debate", "research", "backtest", "system")
VERDICT_STATUSES = {STATUS_PENDING, STATUS_SHADOW}
PROMOTE_STATUSES = {STATUS_PENDING, STATUS_SHADOW}


def _tab(tab_id: str) -> str:
    key = str(tab_id or "").strip().lower()
    if key not in TABS:
        raise TabAgentError(f"Unknown tab {tab_id!r}. Use {', '.join(TABS)}.")
    return key


def resolve_version(
    skill_dir: Path | str,
    tab_id: str,
    version_id: str | None,
    *,
    allowed: set[str],
) -> dict[str, Any]:
    """Pick ``version_id``, or the only version whose status is allowed."""
    tab = _tab(tab_id)
    reg = get_tab_agent(tab).registry(Path(skill_dir))
    wanted = str(version_id or "").strip()
    if wanted:
        row = reg.get(tab, wanted)
        if row is None:
            raise TabAgentError(f"Unknown prompt version: {wanted}")
        if row.get("status") not in allowed:
            raise TabAgentError(f"Version {wanted} is {row.get('status')}; expected {' or '.join(sorted(allowed))}.")
        return row
    matches = [row for row in reg.versions(tab) if row.get("status") in allowed]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise TabAgentError(f"No {'/'.join(sorted(allowed))} version on {tab}. Pass --version.")
    ids = ", ".join(str(row.get("id")) for row in matches)
    raise TabAgentError(f"More than one candidate on {tab}. Pass --version. Candidates: {ids}")


def _score_index(score: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rows = score.get("versions") if isinstance(score.get("versions"), list) else []
    return {str(row.get("version_id") or ""): row for row in rows if isinstance(row, dict)}


def _comparisons(score: dict[str, Any]) -> dict[str, str]:
    rows = score.get("comparisons") if isinstance(score.get("comparisons"), list) else []
    out: dict[str, str] = {}
    for row in rows:
        if isinstance(row, dict) and row.get("version_id"):
            out[str(row["version_id"])] = str(row.get("edge_vs_live") or "")
    return out


def _brief_version(
    skill_dir: Path,
    tab: str,
    version: dict[str, Any] | None,
    stats: dict[str, dict[str, Any]],
    edges: dict[str, str],
) -> dict[str, Any] | None:
    if not version:
        return None
    version_id = str(version.get("id") or "")
    row = stats.get(version_id) or {}
    verdict = read_verdict(skill_dir, tab, version_id)
    return {
        "id": version_id,
        "prompt_key": version.get("prompt_key"),
        "status": version.get("status"),
        "rationale": version.get("rationale"),
        "created_at": version.get("created_at"),
        "n": row.get("n"),
        "n_live": row.get("n_live"),
        "n_replay": row.get("n_replay"),
        "primary_metric": row.get("primary_metric"),
        "primary_score": row.get("primary_score"),
        "max_drawdown": row.get("max_drawdown"),
        "win_rate": row.get("win_rate"),
        "avg_realized_return_pct": row.get("avg_realized_return_pct"),
        "edge_vs_live": edges.get(version_id),
        "review": None
        if verdict is None
        else {"verdict": verdict.get("verdict"), "reviewer": verdict.get("reviewer"), "notes": verdict.get("notes")},
    }


def latest_digest(skill_dir: Path | str) -> dict[str, Any] | None:
    root = Path(skill_dir) / ".tab_agent_digest"
    if not root.exists():
        return None
    files = sorted(root.glob("*.json"))
    if not files:
        return None
    from core.tab_agent_digest import read_digest

    return read_digest(skill_dir, files[-1].stem)


def tab_status(skill_dir: Path | str, tab_id: str) -> dict[str, Any]:
    """Live prompt, shadow prompt, scores, and reviews still waiting on a verdict."""
    tab = _tab(tab_id)
    root = Path(skill_dir)
    agent = get_tab_agent(tab)
    status = agent.status(root)
    score = agent.score(root)
    stats = _score_index(score)
    edges = _comparisons(score)
    prompts = []
    pending_reviews: list[dict[str, Any]] = []
    for group in status.get("prompts") or []:
        if not isinstance(group, dict):
            continue
        live = _brief_version(root, tab, group.get("live"), stats, edges)
        shadow = _brief_version(root, tab, group.get("shadow"), stats, edges)
        pending = []
        for row in group.get("pending") or []:
            brief = _brief_version(root, tab, row, stats, edges)
            if brief:
                pending.append(brief)
        retired = []
        for row in (group.get("retired") or [])[-3:]:
            brief = _brief_version(root, tab, row, stats, edges)
            if brief:
                retired.append(brief)
        for brief in [shadow, *pending]:
            if brief and brief.get("review") is None:
                pending_reviews.append(brief)
        prompts.append(
            {
                "prompt_key": group.get("prompt_key"),
                "live": live,
                "shadow": shadow,
                "pending": pending,
                "retired": retired,
            }
        )
    return {
        "tab_id": tab,
        "orders_touched": False,
        "objective": score.get("objective") or status.get("objective"),
        "prompts": prompts,
        "pending_reviews": pending_reviews,
    }


def board(skill_dir: Path | str) -> dict[str, Any]:
    """Both tabs plus the newest digest. Safe to render on the dashboard."""
    return {
        "orders_touched": False,
        "digest": latest_digest(skill_dir),
        "tabs": [tab_status(skill_dir, tab) for tab in TABS],
    }


def record_verdict(
    skill_dir: Path | str,
    tab_id: str,
    version_id: str | None,
    verdict: str,
    *,
    notes: str = "",
) -> dict[str, Any]:
    """Write Sam's approve or reject file. Reject also retires the shadow prompt."""
    tab = _tab(tab_id)
    root = Path(skill_dir)
    row = resolve_version(root, tab, version_id, allowed=VERDICT_STATUSES)
    written = write_verdict(root, tab, str(row.get("id")), verdict, reviewer="sam", notes=notes)
    retired = None
    if written["verdict"] == "reject":
        retired = get_tab_agent(tab).reject(root, str(row.get("id")))
    from core.tab_agent_governor import write_change_report

    write_change_report(
        root,
        {
            "tab_id": tab,
            "action": f"operator_{written['verdict']}",
            "prompt_key": row.get("prompt_key"),
            "version_id": row.get("id"),
            "why": notes or f"Sam recorded a {written['verdict']} verdict.",
            "before": {},
            "after": {},
            "what_changed": "Review verdict only. No order, size, or execution setting was changed.",
            "sample_size": None,
        },
    )
    return {"orders_touched": False, "verdict": written, "version": retired or row}


def promote_version(skill_dir: Path | str, tab_id: str, version_id: str | None) -> dict[str, Any]:
    """Force a shadow prompt live and record an approve verdict. Still not an order."""
    tab = _tab(tab_id)
    root = Path(skill_dir)
    row = resolve_version(root, tab, version_id, allowed=PROMOTE_STATUSES)
    verdict = write_verdict(root, tab, str(row.get("id")), "approve", reviewer="sam", notes="Manual promote.")
    live = get_tab_agent(tab).approve(root, str(row.get("id")))
    return {"orders_touched": False, "verdict": verdict, "version": live}


def rollback_version(skill_dir: Path | str, tab_id: str, version_id: str | None) -> dict[str, Any]:
    tab = _tab(tab_id)
    root = Path(skill_dir)
    row = resolve_version(root, tab, version_id, allowed={STATUS_RETIRED})
    live = get_tab_agent(tab).rollback(root, str(row.get("id")))
    return {"orders_touched": False, "version": live}


def propose_revision(skill_dir: Path | str, tab_id: str, *, prompt_key: str | None = None) -> dict[str, Any]:
    tab = _tab(tab_id)
    result = get_tab_agent(tab).propose(Path(skill_dir), prompt_key=prompt_key)
    result["orders_touched"] = False
    return result
