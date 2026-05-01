"""Shared release/promotion gate checks for apply flows."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _load_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def ensure_release_gate_for_apply(
    *,
    skill_dir: Path,
    target: str,
    require_slo_status: bool = False,
) -> tuple[bool, list[str]]:
    """Return (ok, reasons) for release/promotion apply gate checks."""
    artifact_dir = skill_dir / "validation_artifacts"
    latest_report_path = artifact_dir / "latest_validation_report.json"
    latest_slo_status_path = artifact_dir / "latest_slo_gate_status.json"
    error_budget_path = artifact_dir / "error_budget_status.json"

    reasons: list[str] = []
    report = _load_json(latest_report_path)
    if not report:
        reasons.append("missing_latest_validation_report")
    else:
        if not bool(report.get("passed")):
            reasons.append("latest_validation_not_passed")
        results_raw = report.get("results")
        results = results_raw if isinstance(results_raw, list) else []
        obs_step = next((r for r in results if isinstance(r, dict) and r.get("name") == "validate_observability_gates"), None)
        if not obs_step or int(obs_step.get("returncode", 1) or 1) != 0:
            reasons.append("observability_gate_not_passing")
        delta = report.get("baseline_delta")
        if not isinstance(delta, dict):
            delta = {}
        regressed_raw = delta.get("regressed")
        regressed = regressed_raw if isinstance(regressed_raw, list) else []
        if regressed:
            reasons.append(f"baseline_regressions_present:{','.join(str(x) for x in regressed)}")

    slo_status = _load_json(latest_slo_status_path)
    if require_slo_status and not slo_status:
        reasons.append("missing_latest_slo_gate_status")
    if slo_status and not bool(slo_status.get("passed")):
        reasons.append("latest_slo_gate_not_passed")

    error_budget = _load_json(error_budget_path)
    if error_budget and bool(error_budget.get("release_freeze")):
        reasons.append("error_budget_release_freeze")

    ok = len(reasons) == 0
    if ok:
        print(f"Release gate passed for target={target!r}")
    else:
        print(
            json.dumps(
                {
                    "target": target,
                    "passed": False,
                    "reasons": reasons,
                    "checked_at": datetime.now(timezone.utc).isoformat(),
                },
                indent=2,
            )
        )
    return ok, reasons
