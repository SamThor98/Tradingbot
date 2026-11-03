"""Persist an adaptive backtest study and turn its proposal into a view.

File IO and the experiment registry live here. The next-test decision lives in
``core.backtest_study``, which does not touch disk.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.backtest_study import TRIAL_BUDGET, propose_next, window_years

_STATUS_FOR_ACTION = {
    "start": "active",
    "queue": "active",
    "wait": "active",
    "stop": "stopped",
    "hold": "hold",
}


def studies_dir(skill_dir: Path) -> Path:
    return Path(skill_dir) / "artifacts" / "backtest_studies"


def _active_path(skill_dir: Path) -> Path:
    return studies_dir(skill_dir) / "active.json"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _strip_results(trials: list[dict[str, Any]]) -> list[dict[str, Any]]:
    cleaned: list[dict[str, Any]] = []
    for row in trials:
        item = {key: value for key, value in row.items() if key != "result"}
        cleaned.append(item)
    return cleaned


def load_active_study(skill_dir: Path) -> dict[str, Any] | None:
    path = _active_path(skill_dir)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def save_study(skill_dir: Path, study: dict[str, Any]) -> None:
    folder = studies_dir(skill_dir)
    folder.mkdir(parents=True, exist_ok=True)
    study_id = str(study.get("study_id") or "study")
    body = json.dumps(study, indent=2, sort_keys=True)
    (folder / f"{study_id}.json").write_text(body, encoding="utf-8")
    _active_path(skill_dir).write_text(body, encoding="utf-8")


def normalize_anchor(spec: dict[str, Any]) -> dict[str, Any]:
    anchor = dict(spec)
    anchor.pop("theory_name", None)
    anchor["schema_version"] = 1
    if (window_years(anchor) or 0) >= 5:
        overrides = dict(anchor.get("overrides") or {})
        overrides["skip_mirofish"] = True
        anchor["overrides"] = overrides
    return anchor


def new_study(spec: dict[str, Any], *, budget: int = TRIAL_BUDGET, auto: bool = False) -> dict[str, Any]:
    study_id = uuid.uuid4().hex[:8]
    return {
        "study_id": study_id,
        "created_at": _utc_now(),
        "status": "active",
        "budget": int(budget),
        "auto": bool(auto),
        "anchor_spec": normalize_anchor(spec),
        "trials": [],
        "dead_directions": [],
        "registry_recorded": False,
        "proposal": None,
    }


def stamp_theory(spec: dict[str, Any], study_id: str, hypothesis: str) -> dict[str, Any]:
    stamped = dict(spec)
    text = f"study {study_id} | {hypothesis}".replace("\n", " ")
    stamped["theory_name"] = text[:200]
    stamped["schema_version"] = 1
    return stamped


def hydrate_trials(study: dict[str, Any], runs_by_id: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    hydrated: list[dict[str, Any]] = []
    for row in study.get("trials") or []:
        if not isinstance(row, dict):
            continue
        item = dict(row)
        run = runs_by_id.get(str(item.get("run_id") or ""))
        if isinstance(run, dict):
            if run.get("status"):
                item["status"] = str(run.get("status"))
            if isinstance(run.get("result"), dict):
                item["result"] = run["result"]
        item.setdefault("status", "queued")
        hydrated.append(item)
    return hydrated


def apply_proposal(study: dict[str, Any], proposal: dict[str, Any]) -> None:
    action = str(proposal.get("action") or "wait")
    study["status"] = _STATUS_FOR_ACTION.get(action, "active")
    study["dead_directions"] = list(proposal.get("dead_directions") or [])
    study["proposal"] = proposal
    study["trials"] = _strip_results(list(study.get("trials") or []))


def maybe_record_decision(study: dict[str, Any], proposal: dict[str, Any], skill_dir: Path) -> None:
    if study.get("registry_recorded"):
        return
    if str(proposal.get("action") or "") not in {"stop", "hold"}:
        return
    if not proposal.get("decision_kind"):
        return
    from experiment_registry import append_registry_event

    append_registry_event(
        event_type="backtest_study_decision",
        target="backtest_study",
        decision="hold",
        rationale=[str(proposal.get("hypothesis") or ""), str(proposal.get("rationale") or "")],
        gates={
            "min_trades": 35,
            "trial_budget": int(study.get("budget") or TRIAL_BUDGET),
            "pf_mean_gate": 1.20,
            "worst_era_pf_gate": 1.00,
            "screen_only": True,
        },
        metadata={
            "study_id": study.get("study_id"),
            "decision_kind": proposal.get("decision_kind"),
            "multi_era_hint": proposal.get("multi_era_hint"),
        },
        skill_dir=skill_dir,
    )
    study["registry_recorded"] = True


def refresh_proposal(
    study: dict[str, Any],
    runs_by_id: dict[str, dict[str, Any]],
    *,
    skill_dir: Path,
) -> dict[str, Any]:
    trials = hydrate_trials(study, runs_by_id)
    proposal = propose_next(study, trials)
    if str(proposal.get("action") or "") == "wait" and trials:
        latest = trials[-1]
        if latest.get("hypothesis"):
            proposal = dict(proposal)
            proposal["hypothesis"] = latest.get("hypothesis")
            proposal["family"] = latest.get("family")
            proposal["rationale"] = "This test is queued. The next factor waits until it finishes."
            proposal["role"] = latest.get("role")
    apply_proposal(study, proposal)
    maybe_record_decision(study, proposal, skill_dir)
    save_study(skill_dir, study)
    return proposal


def idle_view() -> dict[str, Any]:
    return build_view(
        None,
        {
            "action": "start",
            "hypothesis": "Start a study from the current index and window.",
            "rationale": "The first replay is the anchor. Each later test changes one factor.",
            "used_model": False,
            "family": None,
            "factor_effects": [],
            "spec": None,
            "multi_era_hint": None,
            "dead_directions": [],
            "role": None,
        },
    )


def build_view(study: dict[str, Any] | None, proposal: dict[str, Any]) -> dict[str, Any]:
    if study is None:
        return {
            "active": False,
            "study_id": None,
            "status": "idle",
            "action": "start",
            "hypothesis": str(proposal.get("hypothesis") or ""),
            "rationale": str(proposal.get("rationale") or ""),
            "used_model": False,
            "family": None,
            "factor_effects": [],
            "proposal_spec": None,
            "trials_used": 0,
            "budget": TRIAL_BUDGET,
            "multi_era_hint": None,
            "dead_directions": [],
            "role": None,
            "queued_run_id": None,
            "auto": False,
        }
    return {
        "active": str(study.get("status") or "") == "active",
        "study_id": study.get("study_id"),
        "status": study.get("status"),
        "action": proposal.get("action"),
        "hypothesis": proposal.get("hypothesis"),
        "rationale": proposal.get("rationale"),
        "used_model": bool(proposal.get("used_model")),
        "family": proposal.get("family"),
        "factor_effects": list(proposal.get("factor_effects") or []),
        "proposal_spec": proposal.get("spec"),
        "trials_used": len(study.get("trials") or []),
        "budget": int(study.get("budget") or TRIAL_BUDGET),
        "multi_era_hint": proposal.get("multi_era_hint"),
        "dead_directions": list(proposal.get("dead_directions") or []),
        "role": proposal.get("role"),
        "queued_run_id": None,
        "auto": bool(study.get("auto")),
    }


def append_trial(study: dict[str, Any], proposal: dict[str, Any], run_id: str, spec: dict[str, Any]) -> None:
    study.setdefault("trials", []).append(
        {
            "run_id": run_id,
            "role": proposal.get("role"),
            "family": proposal.get("family"),
            "knob": proposal.get("knob"),
            "direction": proposal.get("direction"),
            "parent_run_id": proposal.get("parent_run_id"),
            "hypothesis": proposal.get("hypothesis"),
            "status": "queued",
            "spec": spec,
        }
    )
