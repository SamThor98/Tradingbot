"""Shared decision-dashboard and signal-edge snapshot builders for local + SaaS."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _read_json_file(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _safe_int(raw: Any, default: int = 0) -> int:
    try:
        return int(raw)
    except Exception:
        return default


def _validation_artifact_dir(skill_dir: Path) -> Path:
    return skill_dir / "validation_artifacts"


def latest_validation_status(skill_dir: Path) -> dict[str, Any]:
    artifact_dir = _validation_artifact_dir(skill_dir)
    status_file = artifact_dir / "continuous_validation_status.json"
    if status_file.exists():
        try:
            data = json.loads(status_file.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                latest_artifacts = data.get("latest_artifacts") or {}
                return {
                    "source": "continuous_validation_status",
                    "exists": True,
                    "run_status": data.get("run_status"),
                    "passed": bool(data.get("passed")) if data.get("passed") is not None else None,
                    "started_at": data.get("started_at"),
                    "finished_at": data.get("finished_at"),
                    "generated_at": data.get("generated_at"),
                    "current_step": data.get("current_step"),
                    "current_step_index": data.get("current_step_index"),
                    "completed_steps": data.get("completed_steps"),
                    "total_steps": data.get("total_steps"),
                    "progress_pct": data.get("progress_pct"),
                    "failed_steps": list(data.get("failed_steps") or []),
                    "latest_artifacts": latest_artifacts if isinstance(latest_artifacts, dict) else {},
                }
        except Exception:
            pass

    validate_runs = sorted(artifact_dir.glob("validate_all_*.json"))
    if not validate_runs:
        return {
            "source": "none",
            "exists": False,
            "run_status": "idle",
            "passed": None,
            "started_at": None,
            "finished_at": None,
            "generated_at": None,
            "current_step": None,
            "current_step_index": 0,
            "completed_steps": 0,
            "total_steps": 0,
            "progress_pct": 0,
            "failed_steps": [],
            "latest_artifacts": {},
        }
    latest = validate_runs[-1]
    try:
        payload = json.loads(latest.read_text(encoding="utf-8"))
    except Exception:
        payload = {}
    failed_steps = list(payload.get("failed_steps") or [])
    generated_at = payload.get("generated_at")
    if not generated_at:
        try:
            generated_at = datetime.fromtimestamp(latest.stat().st_mtime, tz=timezone.utc).isoformat()
        except Exception:
            generated_at = None
    try:
        rel_path = str(latest.relative_to(skill_dir))
    except ValueError:
        rel_path = str(latest)
    return {
        "source": "validate_all_summary",
        "exists": True,
        "run_status": "completed",
        "passed": bool(payload.get("passed")) if "passed" in payload else None,
        "started_at": None,
        "finished_at": generated_at,
        "generated_at": generated_at,
        "current_step": None,
        "current_step_index": 0,
        "completed_steps": 0,
        "total_steps": 0,
        "progress_pct": 100,
        "failed_steps": failed_steps,
        "latest_artifacts": {"validate_all": rel_path},
    }


def latest_ablation_status(skill_dir: Path) -> dict[str, Any]:
    artifact_dir = _validation_artifact_dir(skill_dir)
    latest_report = artifact_dir / "latest_ablation_report.json"
    report_path: Path | None = latest_report if latest_report.exists() else None
    source = "latest_ablation_report"
    if report_path is None:
        runs = sorted(artifact_dir.glob("ablation_report_*.json"))
        if runs:
            report_path = runs[-1]
            source = "ablation_report_summary"
    if report_path is None:
        return {
            "source": "none",
            "exists": False,
            "generated_at": None,
            "summary": {"variant_count": 0, "pass_count": 0, "fail_count": 0},
            "best": None,
            "top_variants": [],
            "latest_artifacts": {},
        }
    try:
        payload = json.loads(report_path.read_text(encoding="utf-8"))
    except Exception:
        payload = {}
    generated_at = payload.get("generated_at")
    if not generated_at:
        try:
            generated_at = datetime.fromtimestamp(report_path.stat().st_mtime, tz=timezone.utc).isoformat()
        except Exception:
            generated_at = None
    leaderboard = payload.get("leaderboard") if isinstance(payload.get("leaderboard"), list) else []
    summary_raw = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    variant_count = int(summary_raw.get("variant_count") or len(leaderboard))
    pass_count = int(summary_raw.get("pass_count") or 0)
    fail_count = int(summary_raw.get("fail_count") or max(0, variant_count - pass_count))
    best = leaderboard[0] if leaderboard else None
    if not isinstance(best, dict):
        best = None
    top_variants: list[dict[str, Any]] = []
    for row in leaderboard[:5]:
        if not isinstance(row, dict):
            continue
        top_variants.append(
            {
                "variant_id": row.get("variant_id"),
                "pass": row.get("pass"),
                "relative_lift_vs_baseline": row.get("relative_lift_vs_baseline"),
                "ci_relative_lift_lower": row.get("ci_relative_lift_lower"),
                "ci_relative_lift_upper": row.get("ci_relative_lift_upper"),
                "regression_flags": list(row.get("regression_flags") or []),
            }
        )
    try:
        rel_path = str(report_path.relative_to(skill_dir))
    except ValueError:
        rel_path = str(report_path)
    return {
        "source": source,
        "exists": True,
        "generated_at": generated_at,
        "summary": {
            "variant_count": variant_count,
            "pass_count": pass_count,
            "fail_count": fail_count,
        },
        "best": best,
        "top_variants": top_variants,
        "latest_artifacts": {"ablation_report": rel_path},
    }


def latest_slo_gate_status(skill_dir: Path) -> dict[str, Any]:
    path = _validation_artifact_dir(skill_dir) / "latest_slo_gate_status.json"
    payload = _read_json_file(path, {})
    if not isinstance(payload, dict):
        payload = {}
    passed_raw = payload.get("passed")
    passed = bool(passed_raw) if isinstance(passed_raw, bool) else None
    failures = payload.get("failures")
    return {
        "exists": path.exists(),
        "checked_at": payload.get("checked_at"),
        "passed": passed,
        "failures": list(failures) if isinstance(failures, list) else [],
    }


def latest_registry_decision(skill_dir: Path) -> dict[str, Any] | None:
    registry_path = _validation_artifact_dir(skill_dir) / "experiment_registry.jsonl"
    if not registry_path.exists():
        return None
    try:
        lines = registry_path.read_text(encoding="utf-8").splitlines()
    except Exception:
        return None
    for raw in reversed(lines):
        line = raw.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except Exception:
            continue
        if not isinstance(row, dict):
            continue
        return {
            "recorded_at": row.get("recorded_at"),
            "event_type": row.get("event_type"),
            "target": row.get("target"),
            "decision": row.get("decision"),
            "rationale": list(row.get("rationale") or []),
        }
    return None


def load_validation_artifact(skill_dir: Path, name: str) -> dict[str, Any] | None:
    path = _validation_artifact_dir(skill_dir) / name
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def signal_edge_validation_status(skill_dir: Path, run_id: str = "control_legacy_aug") -> dict[str, Any]:
    """Offline P0 signal-edge evidence for leadership dashboard."""
    shadow = load_validation_artifact(skill_dir, f"signal_edge_shadow_counterfactual_{run_id}.json")
    early = load_validation_artifact(skill_dir, f"early_stopout_cohorts_{run_id}.json")
    entry_timing = load_validation_artifact(skill_dir, f"entry_timing_shadow_counterfactual_{run_id}.json")
    rank = load_validation_artifact(skill_dir, f"rank_filter_counterfactual_{run_id}.json")

    shadow_rec = (shadow or {}).get("recommendation") if shadow else None
    early_rec = (early or {}).get("recommendation") if early else None
    early_baseline = (early or {}).get("baseline") if early else None

    rank_action = None
    if isinstance(shadow_rec, dict):
        rank_action = shadow_rec.get("action")
    elif isinstance(rank, dict):
        rank_action = (rank.get("recommendation") or {}).get("action")

    entry_rec = (entry_timing or {}).get("recommendation") if entry_timing else None
    entry_action = entry_rec.get("action") if isinstance(entry_rec, dict) else None
    binding_constraint = "entry_quality" if entry_action else None
    if entry_action in {
        "fix_entry_timing_not_rank_filter",
        "revise_shadow_thresholds",
        "keep_entry_timing_shadow_only",
        "experiment_breakout_buffer_only",
    }:
        binding_constraint = "entry_timing_not_rank_filter"

    enforce_rank_filter = rank_action == "shadow_rank_filter"
    state = "shadow_only"
    if enforce_rank_filter:
        state = "rank_filter_candidate"
    elif entry_action == "experiment_breakout_buffer_only":
        state = "experiment_shadow"
    elif binding_constraint:
        state = "fix_entry_first"

    experiment_row = None
    if isinstance(entry_rec, dict):
        experiment_row = entry_rec.get("breakout_buffer_only")
    if experiment_row is None and isinstance(entry_timing, dict):
        for row in entry_timing.get("breakout_buffer_only_sweep") or []:
            if row.get("min_breakout_buffer_pct") == 0.01:
                experiment_row = row
                break

    live_compare = load_validation_artifact(skill_dir, f"live_entry_shadow_compare_{run_id}.json")
    stack_art = load_validation_artifact(skill_dir, f"signal_stack_counterfactual_{run_id}.json")
    live_compare_summary = None
    if isinstance(live_compare, dict):
        comparison = live_compare.get("comparison") if isinstance(live_compare.get("comparison"), dict) else {}
        live_metrics = live_compare.get("live") if isinstance(live_compare.get("live"), dict) else {}
        live_compare_summary = {
            "verdict": comparison.get("verdict"),
            "generated_at": live_compare.get("generated_at"),
            "would_filter_pct": live_metrics.get("would_filter_pct"),
            "would_filter_pct_stage2": live_metrics.get("would_filter_pct_stage2"),
            "rate_source": live_metrics.get("rate_source"),
            "stage_a_candidates": live_metrics.get("stage_a_candidates"),
            "entry_shadow_would_filter_any": live_metrics.get("entry_shadow_would_filter_any"),
            "entry_shadow_stage2_evaluated": live_metrics.get("entry_shadow_stage2_evaluated"),
            "entry_shadow_stage2_would_filter_any": live_metrics.get("entry_shadow_stage2_would_filter_any"),
            "entry_timing_shadow_profile": live_metrics.get("entry_timing_shadow_profile"),
            "delta_would_filter_pp": comparison.get("delta_would_filter_pp"),
            "errors": comparison.get("errors") or [],
            "warnings": comparison.get("warnings") or [],
            "skipped": bool(live_compare.get("skipped")),
        }

    stack_summary = None
    if isinstance(stack_art, dict):
        scenarios = stack_art.get("scenarios") if isinstance(stack_art.get("scenarios"), dict) else {}
        stack_row = scenarios.get("exit_grace_breakout_buffer_0.010") or {}
        stack_rec = stack_art.get("recommendation") if isinstance(stack_art.get("recommendation"), dict) else {}
        promotion_gates = stack_art.get("promotion_gates")
        if not isinstance(promotion_gates, dict):
            promotion_gates = {"pf_mean_min": 1.20, "worst_era_pf_min": 1.00}
        scenario_rows: list[dict[str, Any]] = []
        for key, row in scenarios.items():
            if not isinstance(row, dict):
                continue
            scenario_rows.append(
                {
                    "key": key,
                    "label": row.get("label") or key,
                    "pf_mean": row.get("pf_mean"),
                    "worst_era_pf": row.get("worst_era_pf"),
                    "retention_pct": row.get("retention_pct"),
                    "early_stopout_pct": row.get("early_stopout_pct"),
                    "passes_promotion_gates": row.get("passes_promotion_gates"),
                }
            )
        stack_summary = {
            "generated_at": stack_art.get("generated_at"),
            "pf_mean": stack_row.get("pf_mean"),
            "worst_era_pf": stack_row.get("worst_era_pf"),
            "retention_pct": stack_row.get("retention_pct"),
            "passes_promotion_gates": stack_row.get("passes_promotion_gates"),
            "recommendation": stack_rec.get("action"),
            "reason": stack_rec.get("reason"),
            "promotion_gates": promotion_gates,
            "scenarios": scenario_rows,
        }

    from config import get_entry_timing_experiment_readiness

    experiment_env = get_entry_timing_experiment_readiness(skill_dir)

    return {
        "run_id": run_id,
        "state": state,
        "binding_constraint": binding_constraint,
        "experiment_env": experiment_env,
        "live_entry_shadow_compare": live_compare_summary,
        "signal_stack_counterfactual": stack_summary,
        "early_stopout_pct": (early_baseline or {}).get("early_stopout_pct"),
        "hold_21_40d_pf": (early_baseline or {}).get("hold_21_40d_pf"),
        "rank_filter_recommendation": rank_action,
        "entry_quality_recommendation": entry_action or early_rec.get("action") if isinstance(early_rec, dict) else None,
        "entry_quality_reason": early_rec.get("reason") if isinstance(early_rec, dict) else None,
        "entry_timing_recommendation": entry_action,
        "entry_timing_reason": entry_rec.get("reason") if isinstance(entry_rec, dict) else None,
        "entry_timing_experiment": experiment_row,
        "offline_experiment_targets": {
            "would_drop_retention_pct": (experiment_row or {}).get("retention_pct"),
            "delta_early_stopout_pp": (experiment_row or {}).get("delta_early_stopout_pp"),
            "delta_overlap_pf_mean": (experiment_row or {}).get("delta_overlap_pf_mean"),
            "env": {
                "ENTRY_SHADOW_DISABLE_SMA50_FILTERS": "true",
                "ENTRY_SHADOW_MIN_BREAKOUT_BUFFER_PCT": "0.01",
            },
        },
        "shadow_generated_at": (shadow or {}).get("generated_at"),
        "early_stop_generated_at": (early or {}).get("generated_at"),
        "entry_timing_generated_at": (entry_timing or {}).get("generated_at"),
        "artifacts": {
            "shadow_counterfactual": f"validation_artifacts/signal_edge_shadow_counterfactual_{run_id}.json",
            "early_stopout_cohorts": f"validation_artifacts/early_stopout_cohorts_{run_id}.json",
            "entry_timing_shadow_counterfactual": f"validation_artifacts/entry_timing_shadow_counterfactual_{run_id}.json",
            "rank_filter_counterfactual": f"validation_artifacts/rank_filter_counterfactual_{run_id}.json",
        },
    }


def signal_edge_scan_preflight(skill_dir: Path, run_id: str = "control_legacy_aug") -> dict[str, Any]:
    """Compact scan-time guidance for the P0 entry-timing experiment path."""
    from config import get_entry_timing_breakout_buffer_readiness
    from core.env_local import entry_timing_experiment_file_readiness

    edge = signal_edge_validation_status(skill_dir, run_id)
    experiment_env = edge.get("experiment_env") if isinstance(edge.get("experiment_env"), dict) else {}
    live_compare = edge.get("live_entry_shadow_compare") if isinstance(edge.get("live_entry_shadow_compare"), dict) else {}
    entry_rec = edge.get("entry_timing_recommendation")
    experiment_recommended = entry_rec == "experiment_breakout_buffer_only"
    profile_status = get_entry_timing_breakout_buffer_readiness(skill_dir)
    file_env = entry_timing_experiment_file_readiness(skill_dir / ".env")
    process_mode = str(profile_status.get("mode") or "")
    file_mode = str(file_env.get("mode") or "")
    profile_ready = bool(profile_status.get("ready"))
    process_ready = bool(experiment_env.get("ready")) or (profile_ready and process_mode == "live")
    file_ready = bool(file_env.get("profile_ready")) or bool(file_env.get("ready"))
    needs_dashboard_restart = profile_ready and file_mode != process_mode
    stale_last_scan = live_compare.get("verdict") in {"skip", "stale_scan"} and profile_ready and process_mode != "live"

    warnings: list[str] = []
    if profile_ready and process_mode == "live":
        warnings.append("Entry timing LIVE enforcement is active — expect ~50% fewer Stage A candidates.")
    if experiment_recommended and needs_dashboard_restart:
        warnings.append(
            f".env has mode={file_mode} but process has mode={process_mode}. "
            "Restart uvicorn, then Run Scan."
        )
    elif experiment_recommended and not profile_ready:
        warnings.append(
            "Offline replay recommends breakout-buffer-only path, but profile env is not configured. "
            "Run scripts/apply_entry_timing_experiment_env.py or apply_entry_timing_live_env.py."
        )
        for item in profile_status.get("missing_env") or []:
            warnings.append(f"Set {item}")
    elif experiment_recommended and profile_ready and process_mode == "shadow" and stale_last_scan:
        warnings.append(
            "Experiment env is loaded, but last_scan predates it. Run Scan to refresh shadow counters."
        )
    elif profile_ready and process_mode == "live" and live_compare.get("verdict") == "fail":
        warnings.append("Live enforcement active but compare verdict failed — run a fresh full scan.")

    from core.entry_timing_live_compare import (
        assess_entry_timing_live_promotion_readiness,
        assess_stage2b_readiness,
        load_entry_timing_evidence_log,
    )

    evidence_log = load_entry_timing_evidence_log(skill_dir, run_id)
    stage2b = evidence_log.get("stage2b")
    if not isinstance(stage2b, dict):
        stage2b = assess_stage2b_readiness(evidence_log.get("records") or [])

    live_promotion = assess_entry_timing_live_promotion_readiness(skill_dir, run_id=run_id)

    return {
        "experiment_recommended": experiment_recommended,
        "experiment_env_ready": process_ready,
        "experiment_env_file_ready": file_ready,
        "entry_timing_mode": process_mode,
        "entry_timing_profile_ready": profile_ready,
        "entry_timing_live_enforced": process_mode == "live",
        "needs_dashboard_restart": needs_dashboard_restart,
        "stale_last_scan": stale_last_scan,
        "entry_timing_recommendation": entry_rec,
        "expected_profile": experiment_env.get("expected_profile"),
        "current_profile": experiment_env.get("profile"),
        "file_profile": file_env.get("profile"),
        "missing_env": list(experiment_env.get("missing_env") or []),
        "recommended_env": dict(experiment_env.get("recommended_env") or {}),
        "live_compare_verdict": live_compare.get("verdict"),
        "stage2b_ready": bool(stage2b.get("ready")),
        "stage2b_pass_scans": stage2b.get("pass_scans"),
        "stage2b_required_pass_scans": stage2b.get("required_pass_scans"),
        "stage2b_messages": list(stage2b.get("messages") or []),
        "entry_timing_live_promotion_ready": bool(live_promotion.get("ready")),
        "entry_timing_live_promotion_errors": list(live_promotion.get("errors") or []),
        "warnings": warnings,
        "ready_for_experiment_scan": process_ready if experiment_recommended else True,
    }


def signal_edge_shadow_summary(diagnostics: dict[str, Any]) -> dict[str, Any] | None:
    mode = str(diagnostics.get("signal_edge_shadow_mode") or "").strip().lower()
    if not mode:
        return None
    rank_meta = diagnostics.get("rank_filter_shadow")
    return {
        "mode": mode,
        "rank_filter_would_drop_composite": _safe_int(diagnostics.get("rank_filter_would_drop_composite")),
        "rank_filter_would_drop_rank_v2": _safe_int(diagnostics.get("rank_filter_would_drop_rank_v2")),
        "rank_filter_would_drop_signal": _safe_int(diagnostics.get("rank_filter_would_drop_signal")),
        "rank_filter_would_drop_any": _safe_int(diagnostics.get("rank_filter_would_drop_any")),
        "stage2_shadow_would_filter": _safe_int(diagnostics.get("stage2_shadow_would_filter")),
        "thresholds": (rank_meta or {}).get("thresholds") if isinstance(rank_meta, dict) else {},
    }


def build_decision_dashboard_snapshot(*, skill_dir: Path, last_scan: dict[str, Any]) -> dict[str, Any]:
    validation = latest_validation_status(skill_dir)
    ablation = latest_ablation_status(skill_dir)
    slo = latest_slo_gate_status(skill_dir)
    diagnostics_summary = (
        last_scan.get("diagnostics_summary")
        if isinstance(last_scan, dict) and isinstance(last_scan.get("diagnostics_summary"), dict)
        else {}
    )
    last_scan_diagnostics = (
        last_scan.get("diagnostics")
        if isinstance(last_scan, dict) and isinstance(last_scan.get("diagnostics"), dict)
        else {}
    )
    strategy_summary = (
        last_scan.get("strategy_summary")
        if isinstance(last_scan, dict) and isinstance(last_scan.get("strategy_summary"), dict)
        else {}
    )
    validation_passed = validation.get("passed") is True
    ablation_best = ablation.get("best") if isinstance(ablation, dict) else None
    ablation_exists = bool(isinstance(ablation, dict) and ablation.get("exists") is True)
    ablation_passed = bool(
        ablation_exists
        and isinstance(ablation_best, dict)
        and ablation_best.get("pass") is True
    )
    slo_passed = slo.get("passed") is True
    gate_ready = bool(validation_passed and slo_passed and (ablation_passed if ablation_exists else True))
    readiness_checks = [
        {"name": "validation", "passed": validation.get("passed")},
        {"name": "ablation", "passed": ablation_passed if ablation_exists else None},
        {"name": "slo_gate", "passed": slo.get("passed")},
    ]
    if validation.get("run_status") == "running":
        readiness_checks.append({"name": "validation_running", "passed": False})
    latest_decision = latest_registry_decision(skill_dir)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "reliability": {
            "validation_passed": validation.get("passed"),
            "validation_run_status": validation.get("run_status"),
            "slo_gate_passed": slo.get("passed"),
            "slo_failures": list(slo.get("failures") or []),
            "state": "healthy" if gate_ready else "at_risk",
        },
        "strategy_quality": {
            "last_scan_at": last_scan.get("at") if isinstance(last_scan, dict) else None,
            "signals_found": last_scan.get("signals_found") if isinstance(last_scan, dict) else None,
            "dominant_strategy": strategy_summary.get("dominant_live_strategy"),
            "dominant_count": strategy_summary.get("dominant_count"),
            "data_quality": diagnostics_summary.get("data_quality"),
            "scan_blocked": diagnostics_summary.get("scan_blocked"),
            "top_blocker": (
                ((diagnostics_summary.get("top_blockers") or [{}])[0]).get("key")
                if isinstance(diagnostics_summary.get("top_blockers"), list)
                else None
            ),
            "signal_edge_shadow": diagnostics_summary.get("signal_edge_shadow")
            or signal_edge_shadow_summary(last_scan_diagnostics),
        },
        "signal_edge": signal_edge_validation_status(skill_dir),
        "scan_preflight": signal_edge_scan_preflight(skill_dir),
        "promotion_readiness": {
            "release_gate_ready": gate_ready,
            "checks": readiness_checks,
            "latest_decision": latest_decision,
        },
        "ablation": ablation,
    }


def build_shadow_scoreboard_payload(
    *,
    skill_dir: Path,
    diagnostics: dict[str, Any],
    scan_at: str | None = None,
) -> dict[str, Any]:
    from config import (
        get_confluence_gate_mode,
        get_correlation_guard_mode,
        get_exit_manager_mode,
        get_management_integrity_mode,
        get_regime_v2_mode,
    )
    from core import cockpit_service
    from execution_persistence import get_execution_safety_summary

    summary = get_execution_safety_summary(skill_dir=skill_dir, days=7)
    modes = {
        "confluence_gate": get_confluence_gate_mode(skill_dir),
        "correlation_guard": get_correlation_guard_mode(skill_dir),
        "regime_v2": get_regime_v2_mode(skill_dir),
        "management_integrity": get_management_integrity_mode(skill_dir),
        "exit_manager": get_exit_manager_mode(skill_dir),
    }
    return cockpit_service.build_shadow_scoreboard(
        diagnostics if isinstance(diagnostics, dict) else {},
        summary,
        modes=modes,
        scan_at=scan_at,
    )
