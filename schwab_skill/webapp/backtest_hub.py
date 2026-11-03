"""Backtest landing hub: queue summary, outcomes, allowlisted artifacts."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .backtest_labels import attach_run_labels
from .backtest_leaderboard import rank_backtests
from .backtest_outcomes import evaluate_run, fallback_share
from .decision_dashboard_service import (
    build_decision_dashboard_snapshot,
    latest_ablation_status,
    latest_registry_decision,
    latest_slo_gate_status,
    latest_validation_status,
    load_validation_artifact,
    signal_edge_validation_status,
)

# Basename-only. Paths, ``..``, and absolute names are rejected.
ARTIFACT_ALLOWLIST: dict[str, dict[str, str]] = {
    "phase2_edge_audit.json": {
        "kind": "phase2",
        "title": "Promotion verdict (multi-era)",
        "dir": "validation_artifacts",
    },
    "continuous_validation_status.json": {
        "kind": "validation",
        "title": "Latest validation run",
        "dir": "validation_artifacts",
    },
    "latest_ablation_report.json": {
        "kind": "ablation",
        "title": "Ablation / parameter tests",
        "dir": "validation_artifacts",
    },
    "latest_slo_gate_status.json": {
        "kind": "slo",
        "title": "Reliability gate (SLO)",
        "dir": "validation_artifacts",
    },
    "signal_edge_shadow_counterfactual_control_legacy_aug.json": {
        "kind": "signal_edge",
        "title": "Signal-edge rank filter evidence",
        "dir": "validation_artifacts",
    },
    "entry_timing_shadow_counterfactual_control_legacy_aug.json": {
        "kind": "signal_edge",
        "title": "Entry-timing evidence",
        "dir": "validation_artifacts",
    },
    "early_stopout_cohorts_control_legacy_aug.json": {
        "kind": "signal_edge",
        "title": "Early-stopout cohorts",
        "dir": "validation_artifacts",
    },
    "rank_filter_counterfactual_control_legacy_aug.json": {
        "kind": "signal_edge",
        "title": "Rank-filter counterfactual",
        "dir": "validation_artifacts",
    },
    "open_research_sort_exit.json": {
        "kind": "open_research",
        "title": "Sort, exit replay, PEAD coverage",
        "dir": "validation_artifacts",
    },
    ".backtest_results.json": {
        "kind": "performance",
        "title": "Performance snapshot (last CI backtest)",
        "dir": "skill",
    },
}

FORBIDDEN_NAMES = frozenset({".env", "tokens_account.enc", "tokens_market.enc"})


def resolve_artifact_path(skill_dir: Path, name: str) -> Path | None:
    """Return an allowlisted path or None. Never follows ``..`` or abs paths."""
    raw = str(name or "").strip()
    if not raw or raw in FORBIDDEN_NAMES:
        return None
    if "/" in raw or "\\" in raw or ".." in raw:
        return None
    meta = ARTIFACT_ALLOWLIST.get(raw)
    if meta is None:
        return None
    root = Path(skill_dir).resolve()
    if meta["dir"] == "skill":
        candidate = (root / raw).resolve()
    else:
        candidate = (root / "validation_artifacts" / raw).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    return candidate


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists() or not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def _mtime_iso(path: Path) -> str | None:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat()
    except Exception:
        return None


def created_at_sort_key(row: Any) -> datetime:
    ts = getattr(row, "created_at", None)
    if ts is None:
        return datetime.min
    tzinfo = getattr(ts, "tzinfo", None)
    if tzinfo is None:
        return ts
    try:
        return ts.astimezone(timezone.utc).replace(tzinfo=None)
    except Exception:
        return ts.replace(tzinfo=None)


def result_metrics_summary(result: Any) -> dict[str, Any] | None:
    if not isinstance(result, dict) or "total_trades" not in result:
        return None
    out: dict[str, Any] = {
        "total_trades": result.get("total_trades"),
        "win_rate_net": result.get("win_rate_net"),
        "total_return_net_pct": result.get("total_return_net_pct"),
        "cagr_net_pct": result.get("cagr_net_pct"),
        "max_drawdown_net_pct": result.get("max_drawdown_net_pct"),
        "profit_factor_net": result.get("profit_factor_net"),
    }
    findings = result.get("findings")
    if isinstance(findings, str) and findings.strip():
        snippet = findings.strip()
        out["findings_preview"] = snippet[:280] + ("…" if len(snippet) > 280 else "")
    hold = result.get("avg_holding_days")
    if hold is not None:
        out["avg_holding_days"] = hold
    integrity = result.get("data_integrity") if isinstance(result.get("data_integrity"), dict) else {}
    if integrity:
        share = fallback_share(integrity)
        out["fallback_share"] = round(float(share), 4)
        mode = integrity.get("membership_filter_mode")
        if mode:
            out["membership_filter_mode"] = mode
        loaded = integrity.get("membership_input_universe")
        if loaded is not None:
            try:
                out["membership_input_universe"] = int(loaded)
            except (TypeError, ValueError):
                pass
        out["history_fetch_total"] = int(integrity.get("history_fetch_total") or 0)
        out["history_fetch_too_short"] = int(integrity.get("history_fetch_too_short") or 0)
    ranks = trade_ranks_for_summary(result)
    if ranks is not None:
        out["trade_ranks"] = ranks
    return out


def stored_result_parts(result: Any) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Split a stored payload into a finished result and an in-run tape.

    A running replay stores ``{"progress": {...}}`` in ``result_json`` until
    the final metrics replace it. That blob is not a finished result.
    """
    if not isinstance(result, dict) or not result:
        return None, None
    if "total_trades" in result:
        return result, None
    progress = result.get("progress")
    if isinstance(progress, dict):
        return None, progress
    return None, None


def trade_ranks_for_summary(result: Any) -> dict[str, Any] | None:
    if not isinstance(result, dict):
        return None
    ranks = result.get("trade_ranks")
    if not isinstance(ranks, dict):
        return None
    best = [row for row in (ranks.get("best") or []) if isinstance(row, dict)][:5]
    worst = [row for row in (ranks.get("worst") or []) if isinstance(row, dict)][:5]
    if not best and not worst:
        return None
    try:
        closed = int(ranks.get("closed"))
    except (TypeError, ValueError):
        closed = len(best) + len(worst)
    return {"closed": closed, "best": best, "worst": worst}


def public_trade_progress(progress: Any) -> dict[str, Any] | None:
    """Tape shown while a replay is still running."""
    if not isinstance(progress, dict):
        return None
    ranks = progress.get("trade_ranks") if isinstance(progress.get("trade_ranks"), dict) else progress
    try:
        closed = int(ranks.get("closed") if ranks.get("closed") is not None else progress.get("closed") or 0)
    except (TypeError, ValueError):
        closed = 0
    best = [row for row in (ranks.get("best") or []) if isinstance(row, dict)][:5]
    worst = [row for row in (ranks.get("worst") or []) if isinstance(row, dict)][:5]
    if closed <= 0 and not best and not worst:
        return None
    return {"closed": closed, "best": best, "worst": worst}


def annotate_run_list_item(item: dict[str, Any], parsed: Any) -> None:
    """Set list fields from a stored result without treating live progress as done."""
    final, progress = stored_result_parts(parsed)
    item["has_result"] = final is not None
    if final is not None:
        summary = result_metrics_summary(final)
        if summary is not None:
            item["result_summary"] = summary
        return
    tape = public_trade_progress(progress)
    if tape is not None:
        item["progress"] = tape


def previous_summary_map(
    rows: list[Any],
    *,
    parse_result: Any,
) -> dict[str, dict[str, Any]]:
    completed: list[tuple[Any, dict[str, Any]]] = []
    for row in sorted(rows, key=created_at_sort_key):
        raw = getattr(row, "result_json", None)
        parsed = parse_result(raw) if raw else {}
        if not isinstance(parsed, dict):
            parsed = {}
        summary = result_metrics_summary(parsed)
        if getattr(row, "status", None) == "success" and summary:
            completed.append((row, summary))
    prev: dict[str, dict[str, Any]] = {}
    last_summary: dict[str, Any] | None = None
    for row, summary in completed:
        if last_summary is not None:
            prev[str(row.id)] = last_summary
        last_summary = summary
    return prev


def queue_position_map(rows: list[Any]) -> dict[str, int]:
    active = [row for row in rows if getattr(row, "status", None) in {"queued", "running"}]
    active.sort(key=created_at_sort_key)
    return {str(row.id): idx + 1 for idx, row in enumerate(active)}


def serialize_run(
    row: Any,
    *,
    result: dict[str, Any] | None,
    spec: dict[str, Any] | None,
    include_result: bool = False,
    queue_busy: bool = False,
    queue_position: int | None = None,
    previous_summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    status = getattr(row, "status", None)
    err = getattr(row, "error_message", None)
    spec = spec or {}
    evaluated = evaluate_run(
        status=status,
        result=result,
        error_message=err,
        queue_busy=queue_busy,
        spec=spec,
        queue_position=queue_position,
        previous_summary=previous_summary,
    )
    created = getattr(row, "created_at", None)
    item: dict[str, Any] = {
        "id": getattr(row, "id", None),
        "celery_task_id": getattr(row, "celery_task_id", None),
        "status": status,
        "spec": spec,
        "error_message": err,
        "created_at": created.isoformat() if created is not None else None,
        "has_result": bool(result),
        "queue_position": queue_position,
        "outcome": evaluated["outcome"],
        "next_steps": evaluated["next_steps"],
        "briefing": evaluated.get("briefing") or {},
        "requeue_cta": evaluated.get("requeue_cta"),
        "horizon_actions": evaluated.get("horizon_actions") or [],
    }
    if result:
        summary = result_metrics_summary(result)
        if summary:
            item["result_summary"] = summary
    if include_result and result:
        item["result"] = result
    return attach_run_labels(item)


def _card(
    *,
    kind: str,
    title: str,
    exists: bool,
    generated_at: str | None,
    verdict: str,
    next_step: str,
    artifact_name: str | None = None,
    detail: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "kind": kind,
        "title": title,
        "exists": exists,
        "generated_at": generated_at,
        "verdict": verdict if exists else "Not generated yet",
        "next_step": next_step if exists else "Run this check from System when you need the evidence.",
        "artifact_name": artifact_name,
        "detail": detail or {},
    }


def _phase2_card(skill_dir: Path) -> dict[str, Any]:
    name = "phase2_edge_audit.json"
    payload = load_validation_artifact(skill_dir, name)
    path = resolve_artifact_path(skill_dir, name)
    if not payload:
        return _card(
            kind="phase2",
            title="Promotion verdict (multi-era)",
            exists=False,
            generated_at=None,
            verdict="Not generated yet",
            next_step="",
            artifact_name=name,
        )
    verdict = str(payload.get("verdict") or payload.get("recommendation") or "unknown")
    if isinstance(payload.get("recommendation"), dict):
        rec = payload["recommendation"]
        verdict = str(rec.get("verdict") or rec.get("action") or verdict)
    next_step = {
        "proceed": "Bare-signal gate is clear. Keep plugins in shadow until you decide otherwise.",
        "iterate_with_caution": "Keep iterating. Do not go LIVE on this evidence alone.",
        "halt_fix_signal_first": "Do not promote plugins. Fix the base signal first.",
        "halt_insufficient_data": "Not enough eras. Finish the multi-era replay before promoting.",
    }.get(verdict, "Read the audit detail and stay on the conservative side.")
    return _card(
        kind="phase2",
        title="Promotion verdict (multi-era)",
        exists=True,
        generated_at=payload.get("generated_at") or ( _mtime_iso(path) if path else None),
        verdict=verdict,
        next_step=next_step,
        artifact_name=name,
        detail={
            "pf_mean": payload.get("pf_mean") or (payload.get("stage2_only") or {}).get("pf_mean"),
            "worst_era_pf": payload.get("worst_era_pf")
            or (payload.get("stage2_only") or {}).get("worst_era_pf"),
        },
    )


def _validation_card(skill_dir: Path) -> dict[str, Any]:
    status = latest_validation_status(skill_dir)
    exists = bool(status.get("exists"))
    passed = status.get("passed")
    if passed is True:
        verdict = "Passed"
        next_step = "Validation is green. Use it as supporting evidence, not a live go."
    elif passed is False:
        verdict = "Failed"
        next_step = "Open System and fix the failed validation steps before promoting."
    elif status.get("run_status") == "running":
        verdict = "Running"
        next_step = "Wait for the validation run to finish."
    else:
        verdict = "Not generated yet"
        next_step = ""
    artifacts = status.get("latest_artifacts") if isinstance(status.get("latest_artifacts"), dict) else {}
    name = "continuous_validation_status.json"
    if artifacts.get("validate_all"):
        name = str(Path(str(artifacts["validate_all"])).name)
        if name not in ARTIFACT_ALLOWLIST:
            name = "continuous_validation_status.json"
    return _card(
        kind="validation",
        title="Latest validation run",
        exists=exists,
        generated_at=status.get("generated_at") or status.get("finished_at"),
        verdict=verdict,
        next_step=next_step,
        artifact_name=name if exists else "continuous_validation_status.json",
        detail={"run_status": status.get("run_status"), "failed_steps": status.get("failed_steps") or []},
    )


def _ablation_card(skill_dir: Path) -> dict[str, Any]:
    ablation = latest_ablation_status(skill_dir)
    exists = bool(ablation.get("exists"))
    best = ablation.get("best") if isinstance(ablation.get("best"), dict) else None
    if not exists:
        return _card(
            kind="ablation",
            title="Ablation / parameter tests",
            exists=False,
            generated_at=None,
            verdict="Not generated yet",
            next_step="",
            artifact_name="latest_ablation_report.json",
        )
    passed = bool(best and best.get("pass") is True)
    return _card(
        kind="ablation",
        title="Ablation / parameter tests",
        exists=True,
        generated_at=ablation.get("generated_at"),
        verdict="Best variant passed" if passed else "No passing champion yet",
        next_step=(
            "The best variant cleared its gates. Compare it to your queued runs."
            if passed
            else "Stay on the current settings until a variant passes."
        ),
        artifact_name="latest_ablation_report.json",
        detail=ablation.get("summary") or {},
    )


def _slo_card(skill_dir: Path) -> dict[str, Any]:
    slo = latest_slo_gate_status(skill_dir)
    exists = bool(slo.get("exists"))
    passed = slo.get("passed")
    if not exists:
        verdict = "Not generated yet"
        next_step = ""
    elif passed is True:
        verdict = "Passed"
        next_step = "Reliability gate is green."
    else:
        verdict = "Failed"
        next_step = "Fix SLO failures on System before treating the stack as ready."
    return _card(
        kind="slo",
        title="Reliability gate (SLO)",
        exists=exists,
        generated_at=slo.get("checked_at"),
        verdict=verdict,
        next_step=next_step,
        artifact_name="latest_slo_gate_status.json",
        detail={"failures": slo.get("failures") or []},
    )


def _signal_edge_card(skill_dir: Path) -> dict[str, Any]:
    edge = signal_edge_validation_status(skill_dir)
    rec = edge.get("entry_timing_recommendation") if isinstance(edge, dict) else None
    action = None
    if isinstance(rec, dict):
        action = rec.get("action")
    exists = bool(edge) and (
        edge.get("state") not in (None, "")
        or any(
            resolve_artifact_path(skill_dir, n) and resolve_artifact_path(skill_dir, n).exists()  # type: ignore[union-attr]
            for n in (
                "signal_edge_shadow_counterfactual_control_legacy_aug.json",
                "entry_timing_shadow_counterfactual_control_legacy_aug.json",
            )
        )
    )
    if not exists:
        return _card(
            kind="signal_edge",
            title="Signal-edge recommendations",
            exists=False,
            generated_at=None,
            verdict="Not generated yet",
            next_step="",
            artifact_name="entry_timing_shadow_counterfactual_control_legacy_aug.json",
        )
    verdict = str(action or edge.get("state") or "shadow_only")
    return _card(
        kind="signal_edge",
        title="Signal-edge recommendations",
        exists=True,
        generated_at=None,
        verdict=verdict,
        next_step="Follow the binding constraint — do not promote rank filters while entry timing is the limiter.",
        artifact_name="entry_timing_shadow_counterfactual_control_legacy_aug.json",
        detail={"state": edge.get("state"), "binding_constraint": edge.get("binding_constraint")},
    )


def _performance_card(skill_dir: Path) -> dict[str, Any]:
    name = ".backtest_results.json"
    path = resolve_artifact_path(skill_dir, name)
    payload = _read_json(path) if path else None
    if not payload:
        return _card(
            kind="performance",
            title="Performance snapshot (last CI backtest)",
            exists=False,
            generated_at=None,
            verdict="Not generated yet",
            next_step="",
            artifact_name=name,
        )
    pf = payload.get("profit_factor") or payload.get("profit_factor_net")
    return _card(
        kind="performance",
        title="Performance snapshot (last CI backtest)",
        exists=True,
        generated_at=payload.get("run_at") or (_mtime_iso(path) if path else None),
        verdict=f"PF {pf}" if pf is not None else "Snapshot on file",
        next_step="This is the last CI/file snapshot — compare it to your queued runs on this page.",
        artifact_name=name,
        detail={
            "trades": payload.get("trades") or payload.get("total_trades"),
            "win_rate": payload.get("win_rate"),
            "profit_factor": pf,
        },
    )


def _registry_card(skill_dir: Path) -> dict[str, Any]:
    latest = latest_registry_decision(skill_dir)
    if not latest:
        return _card(
            kind="registry",
            title="Latest promotion-ledger decision",
            exists=False,
            generated_at=None,
            verdict="Not generated yet",
            next_step="",
        )
    return _card(
        kind="registry",
        title="Latest promotion-ledger decision",
        exists=True,
        generated_at=latest.get("recorded_at"),
        verdict=str(latest.get("decision") or latest.get("event_type") or "recorded"),
        next_step="Ledger decisions stay on System. Use this as context for your queued runs.",
        detail={"target": latest.get("target"), "rationale": latest.get("rationale") or []},
    )


def _open_research_card(skill_dir: Path) -> dict[str, Any]:
    name = "open_research_sort_exit.json"
    path = resolve_artifact_path(skill_dir, name)
    payload = _read_json(path) if path else None
    if not payload:
        return _card(
            kind="open_research",
            title="Sort, exit replay, PEAD coverage",
            exists=False,
            generated_at=None,
            verdict="Not generated yet",
            next_step="",
            artifact_name=name,
        )
    verdict = str(payload.get("verdict") or "unknown")
    next_step = {
        "shadow_ready_sort_key_not_promoted": (
            "The walk-forward less-extended percentile clears the floors. "
            "Leave SCAN_LIVE_SORT_KEY on signal_score. Daily top-5 by that score "
            "does not clear the mean floor on the control book."
        ),
        "do_not_promote_sort_key": "Leave the live sort on signal_score.",
        "clears_floors_but_not_enough_lift": (
            "The percentile clears the floors without enough lift to change the live sort."
        ),
    }.get(verdict, "Read the artifact. The live sort key stays signal_score.")
    return _card(
        kind="open_research",
        title="Sort, exit replay, PEAD coverage",
        exists=True,
        generated_at=payload.get("generated_at") or (_mtime_iso(path) if path else None),
        verdict=verdict,
        next_step=next_step,
        artifact_name=name,
        detail={
            "control_wf_inv_pts_pf": payload.get("control_wf_inv_pts_pf"),
            "control_take_every_pf": payload.get("control_take_every_pf"),
            "control_top5_signal_pf": payload.get("control_top5_signal_pf"),
            "exit_grace_pf": payload.get("exit_grace_pf"),
            "pead_coverage_20d": payload.get("pead_coverage_20d"),
        },
    )


def related_evidence_cards(skill_dir: Path) -> list[dict[str, Any]]:
    if skill_dir is None or not Path(skill_dir).exists():
        empty = [
            _phase2_card(Path(".")),
            _validation_card(Path(".")),
            _ablation_card(Path(".")),
            _slo_card(Path(".")),
            _signal_edge_card(Path(".")),
            _performance_card(Path(".")),
            _registry_card(Path(".")),
            _open_research_card(Path(".")),
        ]
        for card in empty:
            card["exists"] = False
            card["verdict"] = "Not generated yet"
            card["next_step"] = "Not available in this environment."
        return empty
    root = Path(skill_dir)
    return [
        _phase2_card(root),
        _validation_card(root),
        _ablation_card(root),
        _slo_card(root),
        _signal_edge_card(root),
        _performance_card(root),
        _registry_card(root),
        _open_research_card(root),
    ]


def list_artifact_catalog(skill_dir: Path) -> list[dict[str, Any]]:
    root = Path(skill_dir) if skill_dir else Path(".")
    items: list[dict[str, Any]] = []
    for name, meta in ARTIFACT_ALLOWLIST.items():
        path = resolve_artifact_path(root, name)
        exists = bool(path and path.exists())
        items.append(
            {
                "name": name,
                "kind": meta["kind"],
                "title": meta["title"],
                "exists": exists,
                "generated_at": _mtime_iso(path) if exists and path else None,
            }
        )
    return items


def artifact_detail(skill_dir: Path, name: str) -> dict[str, Any] | None:
    path = resolve_artifact_path(skill_dir, name)
    if path is None:
        return None
    meta = ARTIFACT_ALLOWLIST[name]
    payload = _read_json(path)
    return {
        "name": name,
        "kind": meta["kind"],
        "title": meta["title"],
        "exists": payload is not None,
        "generated_at": _mtime_iso(path) if path.exists() else None,
        "summary": payload if isinstance(payload, dict) else {},
    }


def build_hub(
    *,
    skill_dir: Path,
    runs: list[dict[str, Any]],
    queue_busy: bool,
    concurrency_note: str,
    last_scan: dict[str, Any] | None = None,
    leaderboard_runs: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    queued = [r for r in runs if r.get("status") == "queued"]
    running = [r for r in runs if r.get("status") == "running"]
    failed = [r for r in runs if r.get("status") == "failed"]
    latest = runs[0] if runs else None
    latest_success = next(
        (
            r
            for r in runs
            if r.get("status") == "success" and (r.get("has_result") or r.get("result_summary"))
        ),
        None,
    )
    decision = None
    try:
        decision = build_decision_dashboard_snapshot(
            skill_dir=skill_dir,
            last_scan=last_scan or {},
        )
    except Exception:
        decision = None
    board_source = leaderboard_runs
    if board_source is None:
        board_source = [r for r in runs if r.get("status") == "success" and r.get("has_result")]
    return {
        "queue": {
            "busy": queue_busy,
            "queued": len(queued),
            "running": len(running),
            "failed": len(failed),
            "total": len(runs),
            "concurrency_note": concurrency_note,
        },
        "latest_run": latest,
        "latest_success": latest_success,
        "runs": runs,
        "related": related_evidence_cards(skill_dir),
        "promotion_readiness": (decision or {}).get("promotion_readiness") if decision else None,
        "leaderboard": rank_backtests(board_source),
    }
