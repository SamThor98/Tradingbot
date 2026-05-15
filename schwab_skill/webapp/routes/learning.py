"""Learning and performance routes: challenger, evolve, data-provider, performance, calibration."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request

from ..calibration_snapshot import build_calibration_snapshot
from ..recovery_map import map_failure as _map_failure
from ..schemas import ApiResponse

router = APIRouter(tags=["learning"])

SKILL_DIR = Path(__file__).resolve().parent.parent.parent
BACKTEST_RESULTS_PATH = SKILL_DIR / ".backtest_results.json"
TRADE_OUTCOMES_PATH = SKILL_DIR / ".trade_outcomes.json"
EXECUTION_METRICS_PATH = SKILL_DIR / "execution_safety_metrics.json"
VALIDATION_ARTIFACT_DIR = SKILL_DIR / "validation_artifacts"
_DATA_PROVIDER_INSTANCE: Any | None = None
_DATA_PROVIDER_LOCK = threading.Lock()
_ABLATION_STATUS_PATH = VALIDATION_ARTIFACT_DIR / "ablation_cycle_status.json"
_ABLATION_LOCK = threading.Lock()
_ABLATION_THREAD: threading.Thread | None = None
_ABLATION_STATE: dict[str, Any] = {
    "run_status": "idle",
    "started_at": None,
    "finished_at": None,
    "last_error": None,
    "returncode": None,
    "raw_artifact": None,
    "report_json": None,
    "report_md": None,
}


def _ok(data: Any = None) -> ApiResponse:
    return ApiResponse(ok=True, data=data)


def _err_response(endpoint: str, exc: Exception) -> ApiResponse:
    mapped = _map_failure(str(exc), source=endpoint)
    headline = f"{mapped.get('title', 'Error')}: {mapped.get('summary', 'Something went wrong.')}"
    raw = str(mapped.get("raw_error") or "").strip()
    summary = str(mapped.get("summary") or "")
    err_out = headline
    if raw and raw.lower() not in summary.lower():
        err_out = f"{headline} — {raw[:220]}"
    return ApiResponse(ok=False, error=err_out, data={"recovery": mapped})


def _read_json_file(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_ablation_state_locked() -> None:
    VALIDATION_ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    _ABLATION_STATUS_PATH.write_text(json.dumps(_ABLATION_STATE, indent=2), encoding="utf-8")


def _latest_ablation_report_snapshot() -> dict[str, Any]:
    latest = VALIDATION_ARTIFACT_DIR / "latest_ablation_report.json"
    report_path: Path | None = latest if latest.exists() else None
    if report_path is None:
        runs = sorted(VALIDATION_ARTIFACT_DIR.glob("ablation_report_*.json"))
        if runs:
            report_path = runs[-1]
    if report_path is None:
        return {"exists": False}
    payload = _read_json_file(report_path, {})
    summary = payload.get("summary") if isinstance(payload, dict) else {}
    leaderboard = payload.get("leaderboard") if isinstance(payload, dict) else []
    best = leaderboard[0] if isinstance(leaderboard, list) and leaderboard else None
    return {
        "exists": True,
        "path": str(report_path),
        "generated_at": payload.get("generated_at") if isinstance(payload, dict) else None,
        "summary": summary if isinstance(summary, dict) else {},
        "best": best if isinstance(best, dict) else None,
    }


def _extract_marker_path(stdout: str, marker: str) -> str | None:
    for line in (stdout or "").splitlines():
        if marker in line:
            _, _, tail = line.partition(marker)
            out = tail.strip()
            if out:
                return out
    return None


def _run_ablation_cycle_async() -> None:
    with _ABLATION_LOCK:
        _ABLATION_STATE["run_status"] = "running"
        _ABLATION_STATE["started_at"] = _utc_now_iso()
        _ABLATION_STATE["finished_at"] = None
        _ABLATION_STATE["last_error"] = None
        _ABLATION_STATE["returncode"] = None
        _ABLATION_STATE["raw_artifact"] = None
        _ABLATION_STATE["report_json"] = None
        _ABLATION_STATE["report_md"] = None
        _write_ablation_state_locked()

    run_cmd = [
        sys.executable,
        str(SKILL_DIR / "scripts" / "run_param_ablation.py"),
        "--manifest",
        str(SKILL_DIR / "scripts" / "ablation_manifest_v1.json"),
    ]
    run_proc = subprocess.run(
        run_cmd,
        cwd=str(SKILL_DIR),
        capture_output=True,
        text=True,
    )
    raw_path = _extract_marker_path(run_proc.stdout or "", "Ablation raw artifact:")
    if run_proc.returncode != 0:
        with _ABLATION_LOCK:
            _ABLATION_STATE["run_status"] = "failed"
            _ABLATION_STATE["finished_at"] = _utc_now_iso()
            _ABLATION_STATE["returncode"] = int(run_proc.returncode)
            _ABLATION_STATE["last_error"] = (
                (run_proc.stderr or "").strip() or (run_proc.stdout or "").strip() or "ablation_raw_failed"
            )[:400]
            _write_ablation_state_locked()
        return

    if not raw_path:
        with _ABLATION_LOCK:
            _ABLATION_STATE["run_status"] = "failed"
            _ABLATION_STATE["finished_at"] = _utc_now_iso()
            _ABLATION_STATE["returncode"] = 3
            _ABLATION_STATE["last_error"] = "Unable to resolve raw ablation artifact path."
            _write_ablation_state_locked()
        return

    score_cmd = [
        sys.executable,
        str(SKILL_DIR / "scripts" / "score_ablation_report.py"),
        "--raw-artifact",
        raw_path,
    ]
    score_proc = subprocess.run(
        score_cmd,
        cwd=str(SKILL_DIR),
        capture_output=True,
        text=True,
    )
    report_json = _extract_marker_path(score_proc.stdout or "", "Ablation report JSON:")
    report_md = _extract_marker_path(score_proc.stdout or "", "Ablation report Markdown:")
    if score_proc.returncode != 0:
        with _ABLATION_LOCK:
            _ABLATION_STATE["run_status"] = "failed"
            _ABLATION_STATE["finished_at"] = _utc_now_iso()
            _ABLATION_STATE["returncode"] = int(score_proc.returncode)
            _ABLATION_STATE["raw_artifact"] = raw_path
            _ABLATION_STATE["last_error"] = (
                (score_proc.stderr or "").strip() or (score_proc.stdout or "").strip() or "ablation_score_failed"
            )[:400]
            _write_ablation_state_locked()
        return

    with _ABLATION_LOCK:
        _ABLATION_STATE["run_status"] = "completed"
        _ABLATION_STATE["finished_at"] = _utc_now_iso()
        _ABLATION_STATE["returncode"] = 0
        _ABLATION_STATE["raw_artifact"] = raw_path
        _ABLATION_STATE["report_json"] = report_json
        _ABLATION_STATE["report_md"] = report_md
        _ABLATION_STATE["last_error"] = None
        _write_ablation_state_locked()


def _require_api_key_if_set(
    request: Request,
    x_api_key: str | None = Header(default=None),
    x_user: str | None = Header(default=None),
) -> dict[str, str]:
    configured = os.getenv("WEB_API_KEY", "").strip()
    if not configured:
        if not (os.getenv("RENDER") or "").strip():
            return {"actor": (x_user or "unsafe-local-user").strip() or "unsafe-local-user"}
        env = (os.getenv("ENV") or os.getenv("APP_ENV") or "").strip().lower()
        production_like = env in ("prod", "production", "staging") or bool((os.getenv("RENDER") or "").strip())
        unsafe = (os.getenv("WEB_ALLOW_UNSAFE_LOCAL_WRITES") or "").strip().lower() in (
            "1",
            "true",
            "yes",
            "on",
        )
        host = (request.url.hostname or "").strip()
        if not host:
            host = str(request.headers.get("host") or "").split(":")[0].strip()
        if not host and request.client is not None:
            host = str(request.client.host or "").strip()
        loopback = host in {"127.0.0.1", "localhost", "::1"}
        if unsafe or (not production_like) or loopback:
            return {"actor": (x_user or "unsafe-local-user").strip() or "unsafe-local-user"}
        raise HTTPException(
            status_code=503,
            detail="WEB_API_KEY is required for write operations. Configure WEB_API_KEY on the server.",
        )
    if not x_api_key or x_api_key != configured:
        raise HTTPException(status_code=401, detail="Invalid or missing X-API-Key.")
    return {"actor": (x_user or "web-user").strip() or "web-user"}


def _get_validation_status() -> dict[str, Any]:
    from ..main import _latest_validation_status
    return _latest_validation_status()


def _get_data_provider_singleton() -> Any:
    global _DATA_PROVIDER_INSTANCE
    if _DATA_PROVIDER_INSTANCE is not None:
        return _DATA_PROVIDER_INSTANCE
    with _DATA_PROVIDER_LOCK:
        if _DATA_PROVIDER_INSTANCE is None:
            from data_provider import DataProvider

            _DATA_PROVIDER_INSTANCE = DataProvider(skill_dir=SKILL_DIR)
    return _DATA_PROVIDER_INSTANCE


def _get_challenger_summary() -> dict[str, Any]:
    try:
        from challenger_mode import ChallengerRunner

        runner = ChallengerRunner(skill_dir=SKILL_DIR)
        latest = runner.get_latest_comparison()
        win_rate = runner.get_win_rate_summary()
        strategy_update = _read_json_file(SKILL_DIR / "strategy_update.json", {})
        can_run = bool(isinstance(strategy_update, dict) and strategy_update.get("env_overrides"))
        return {"available": True, "latest": latest, "win_rate": win_rate, "can_run": can_run}
    except Exception:
        return {"available": False}


@router.get("/api/performance", response_model=ApiResponse)
def performance() -> ApiResponse:
    backtest = _read_json_file(BACKTEST_RESULTS_PATH, {})
    outcomes = _read_json_file(TRADE_OUTCOMES_PATH, [])
    metrics = _read_json_file(EXECUTION_METRICS_PATH, {"days": {}})
    days = metrics.get("days", {}) if isinstance(metrics, dict) else {}

    shadow_actions = 0
    live_actions = 0
    for bucket in days.values() if isinstance(days, dict) else []:
        events = (bucket or {}).get("events", {}) if isinstance(bucket, dict) else {}
        shadow_actions += int(events.get("action_shadow", 0) or 0)
        live_actions += int(events.get("action_live", 0) or 0)

    total_outcomes = len(outcomes) if isinstance(outcomes, list) else 0
    return _ok({
        "backtest": {
            "source": str(BACKTEST_RESULTS_PATH.name),
            "run_at": backtest.get("run_at") if isinstance(backtest, dict) else None,
            "total_trades": backtest.get("total_trades") if isinstance(backtest, dict) else None,
            "win_rate": backtest.get("win_rate_net") if isinstance(backtest, dict) else None,
            "avg_return_pct": backtest.get("avg_return_net_pct") if isinstance(backtest, dict) else None,
            "max_drawdown_pct": backtest.get("max_drawdown_net_pct") if isinstance(backtest, dict) else None,
        },
        "shadow_paper": {
            "source": "execution_safety_metrics.json",
            "shadow_actions": shadow_actions,
            "notes": "Derived from shadow execution event counters.",
        },
        "live": {
            "source": ".trade_outcomes.json",
            "live_actions": live_actions,
            "recorded_outcomes": total_outcomes,
            "latest_outcomes": (outcomes[-5:] if isinstance(outcomes, list) else []),
        },
        "validation": {
            "status": _get_validation_status(),
            "artifacts_present": VALIDATION_ARTIFACT_DIR.exists(),
        },
        "separation_guard": {
            "commingled_metric_allowed": False,
            "message": "Backtest, shadow/paper, and live are reported as separate buckets only.",
        },
        "challenger": _get_challenger_summary(),
    })


@router.get("/api/calibration/summary", response_model=ApiResponse)
def api_calibration_summary() -> ApiResponse:
    return _ok(build_calibration_snapshot(SKILL_DIR))


@router.get("/api/challenger/latest", response_model=ApiResponse)
def challenger_latest() -> ApiResponse:
    try:
        from challenger_mode import ChallengerRunner

        runner = ChallengerRunner(skill_dir=SKILL_DIR)
        latest = runner.get_latest_comparison()
        if not latest:
            return _ok({"status": "no_data", "message": "No challenger runs yet."})
        return _ok(latest)
    except Exception as e:
        return _err_response("challenger_latest", e)


@router.get("/api/challenger/history", response_model=ApiResponse)
def challenger_history(n: int = 10) -> ApiResponse:
    try:
        from challenger_mode import ChallengerRunner

        runner = ChallengerRunner(skill_dir=SKILL_DIR)
        return _ok({
            "history": runner.get_comparison_history(n),
            "win_rate": runner.get_win_rate_summary(),
        })
    except Exception as e:
        return _err_response("challenger_history", e)


@router.post("/api/challenger/run", response_model=ApiResponse)
def challenger_run(
    _auth: dict[str, str] = Depends(_require_api_key_if_set),
) -> ApiResponse:
    try:
        from challenger_mode import ChallengerRunner

        runner = ChallengerRunner(skill_dir=SKILL_DIR)
        result = runner.run()
        return _ok(result)
    except Exception as e:
        return _err_response("challenger_run", e)


@router.get("/api/data-provider/status", response_model=ApiResponse)
def data_provider_status() -> ApiResponse:
    try:
        provider = _get_data_provider_singleton()
        return _ok(provider.status())
    except Exception as e:
        return _err_response("data_provider_status", e)


@router.post("/api/evolve/run", response_model=ApiResponse)
def evolve_run(
    _auth: dict[str, str] = Depends(_require_api_key_if_set),
) -> ApiResponse:
    try:
        from evolve_logic import LearningEngine

        engine = LearningEngine(skill_dir=SKILL_DIR)
        result = engine.run(apply=False)
        return _ok(result)
    except Exception as e:
        return _err_response("evolve_run", e)


@router.get("/api/ablation/status", response_model=ApiResponse)
def ablation_status() -> ApiResponse:
    with _ABLATION_LOCK:
        status = dict(_ABLATION_STATE)
        running = bool(_ABLATION_THREAD is not None and _ABLATION_THREAD.is_alive())
    if not status.get("started_at") and _ABLATION_STATUS_PATH.exists():
        persisted = _read_json_file(_ABLATION_STATUS_PATH, {})
        if isinstance(persisted, dict):
            status.update(persisted)
    status["running"] = running
    status["latest_report"] = _latest_ablation_report_snapshot()
    return _ok(status)


@router.post("/api/ablation/run", response_model=ApiResponse)
def ablation_run(
    _auth: dict[str, str] = Depends(_require_api_key_if_set),
) -> ApiResponse:
    global _ABLATION_THREAD
    with _ABLATION_LOCK:
        if _ABLATION_THREAD is not None and _ABLATION_THREAD.is_alive():
            return _ok(
                {
                    "started": False,
                    "already_running": True,
                    "status": dict(_ABLATION_STATE),
                }
            )
        _ABLATION_THREAD = threading.Thread(target=_run_ablation_cycle_async, daemon=True)
        _ABLATION_THREAD.start()
        status = dict(_ABLATION_STATE)
    return _ok({"started": True, "already_running": False, "status": status})
