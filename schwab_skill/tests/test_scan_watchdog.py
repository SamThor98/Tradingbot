"""Local scan watchdog: a live full-universe worker is not a failed scan."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import webapp.main as webapp_main

APP_JS = Path(__file__).resolve().parent.parent / "webapp" / "static" / "app.js"


def _running(*, elapsed_s: float) -> None:
    started = datetime.now(timezone.utc) - timedelta(seconds=elapsed_s)
    webapp_main._scan_job.update(
        {
            "job_id": "job-watchdog",
            "status": "running",
            "started_at": started.isoformat(),
            "finished_at": None,
            "signals_found": None,
            "diagnostics": None,
            "diagnostics_summary": None,
            "strategy_summary": None,
            "signals": [],
            "shortlist_signals": [],
            "error": None,
        }
    )


def test_live_worker_stays_running_past_one_hour(monkeypatch) -> None:
    monkeypatch.setattr(webapp_main, "_SCAN_STALE_SECONDS", 10800)
    monkeypatch.setattr(
        webapp_main,
        "_scan_worker_thread",
        SimpleNamespace(ident=1, is_alive=lambda: True),
    )
    original = dict(webapp_main._scan_job)
    try:
        _running(elapsed_s=5400)
        with webapp_main._scan_lock:
            assert webapp_main._expire_stale_scan_job_locked() is False
        assert webapp_main._scan_job["status"] == "running"
        assert webapp_main._scan_job["error"] is None
    finally:
        webapp_main._scan_job.clear()
        webapp_main._scan_job.update(original)


def test_dead_worker_fails_without_waiting_for_the_cap(monkeypatch) -> None:
    monkeypatch.setattr(webapp_main, "_SCAN_STALE_SECONDS", 10800)
    monkeypatch.setattr(
        webapp_main,
        "_scan_worker_thread",
        SimpleNamespace(ident=7, is_alive=lambda: False),
    )
    original = dict(webapp_main._scan_job)
    try:
        _running(elapsed_s=10)
        with webapp_main._scan_lock:
            assert webapp_main._expire_stale_scan_job_locked() is True
        assert webapp_main._scan_job["status"] == "failed"
        assert "exited without a result" in str(webapp_main._scan_job["error"])
    finally:
        webapp_main._scan_job.clear()
        webapp_main._scan_job.update(original)


def test_unstarted_worker_is_not_abandoned(monkeypatch) -> None:
    monkeypatch.setattr(webapp_main, "_SCAN_STALE_SECONDS", 10800)
    monkeypatch.setattr(
        webapp_main,
        "_scan_worker_thread",
        SimpleNamespace(ident=None, is_alive=lambda: False),
    )
    original = dict(webapp_main._scan_job)
    try:
        _running(elapsed_s=10)
        with webapp_main._scan_lock:
            assert webapp_main._expire_stale_scan_job_locked() is False
        assert webapp_main._scan_job["status"] == "running"
    finally:
        webapp_main._scan_job.clear()
        webapp_main._scan_job.update(original)


def test_live_worker_past_hard_cap_is_a_watchdog_timeout(monkeypatch) -> None:
    monkeypatch.setattr(webapp_main, "_SCAN_STALE_SECONDS", 100)
    monkeypatch.setattr(
        webapp_main,
        "_scan_worker_thread",
        SimpleNamespace(ident=1, is_alive=lambda: True),
    )
    original = dict(webapp_main._scan_job)
    try:
        _running(elapsed_s=101)
        with webapp_main._scan_lock:
            assert webapp_main._expire_stale_scan_job_locked() is True
        assert webapp_main._scan_job["status"] == "failed"
        assert "watchdog timeout" in str(webapp_main._scan_job["error"])
    finally:
        webapp_main._scan_job.clear()
        webapp_main._scan_job.update(original)


def test_dashboard_keeps_polling_through_watchdog_timeout() -> None:
    app_js = APP_JS.read_text(encoding="utf-8")
    assert "function scanStillWorking" in app_js
    assert 'err.includes("watchdog timeout")' in app_js
    resume = app_js.split("async function maybeResumeLocalScanPolling", 1)[1].split(
        "function applyEntryTimingExperimentPreflight", 1
    )[0]
    assert "scanStillWorking(data)" in resume
    wait = app_js.split("async function waitForScanCompletion", 1)[1].split(
        "function openQueueScanDialog", 1
    )[0]
    assert "scanStillWorking(data)" in wait
    assert "const maxPolls = 2160" in wait
