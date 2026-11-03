#!/usr/bin/env python3
"""Keep the local 8182 backtest queue filled.

Talks to the operator dashboard (https://127.0.0.1:8182). One in-process
worker; this process only POSTs when a waiting slot is free. Does not place
live orders. Default is no cutoff (``--no-deadline``). ``--until`` is optional
if a stop time is needed later.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import date, datetime
from pathlib import Path
from typing import Any

SKILL_DIR = Path(__file__).resolve().parents[1]
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

from config import bootstrap_dotenv_into_environ
from core.hypothesis_card import augment_campaign_plan, settle_finished_queue_cards
from core.weekend_backtest_campaign import (
    CHICAGO,
    MIN_HORIZON_YEARS,
    campaign_deadline,
    campaign_end_date,
    campaign_plan,
    fingerprints_from_runs,
    select_remaining_campaign_jobs,
)
from webapp.backtest_labels import window_years_from_spec

LOG = logging.getLogger("weekend_backtest_campaign")
STATE_DIR = SKILL_DIR / "artifacts" / "weekend_backtest_campaign"
LOCK_PATH = STATE_DIR / "supervisor.lock"
STATE_PATH = STATE_DIR / "state.json"
DEFAULT_BASE_URL = "https://127.0.0.1:8182"
MAX_WAITING = 8


def _json_default(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        import subprocess

        proc = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        return str(pid) in (proc.stdout or "")
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _kill_pid(pid: int) -> None:
    if pid <= 0 or pid == os.getpid():
        return
    if os.name == "nt":
        subprocess.call(["taskkill", "/PID", str(pid), "/F"], timeout=15)
        return
    try:
        os.kill(pid, 15)
    except OSError:
        return


def acquire_lock(*, force: bool = False) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    existing = 0
    if LOCK_PATH.is_file():
        try:
            existing = int((LOCK_PATH.read_text(encoding="utf-8") or "0").strip() or "0")
        except ValueError:
            existing = 0
    if existing and _pid_alive(existing) and existing != os.getpid():
        if not force:
            raise SystemExit(f"Campaign supervisor already running (pid {existing})")
        LOG.info("Stopping previous supervisor pid %s", existing)
        _kill_pid(existing)
        time.sleep(0.5)
    LOCK_PATH.write_text(str(os.getpid()), encoding="utf-8")


def release_lock() -> None:
    try:
        if LOCK_PATH.is_file() and LOCK_PATH.read_text(encoding="utf-8").strip() == str(os.getpid()):
            LOCK_PATH.unlink()
    except OSError:
        return


def load_state() -> dict[str, Any]:
    if not STATE_PATH.is_file():
        return {
            "submitted": [],
            "errors": [],
            "cancelled": [],
            "started_at": datetime.now(CHICAGO).isoformat(),
        }
    try:
        raw = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"submitted": [], "errors": [], "cancelled": []}
    if not isinstance(raw, dict):
        return {"submitted": [], "errors": [], "cancelled": []}
    return raw


def save_state(state: dict[str, Any]) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = STATE_PATH.with_suffix(".json.tmp")
    payload = json.dumps(state, indent=2, default=_json_default)
    last_exc: Exception | None = None
    for attempt in range(5):
        try:
            tmp.write_text(payload, encoding="utf-8")
            tmp.replace(STATE_PATH)
            return
        except OSError as exc:
            last_exc = exc
            time.sleep(0.2 * (attempt + 1))
    LOG.warning("Could not persist campaign state: %s", last_exc)


class DashboardClient:
    def __init__(self, base_url: str, api_key: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self._ctx = ssl._create_unverified_context()

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        if self.api_key:
            headers["X-API-Key"] = self.api_key
        return headers

    def request(self, method: str, path: str, body: dict[str, Any] | None = None) -> tuple[int, Any]:
        data = None if body is None else json.dumps(body).encode("utf-8")
        req = urllib.request.Request(
            f"{self.base_url}{path}",
            data=data,
            headers=self._headers(),
            method=method,
        )
        try:
            with urllib.request.urlopen(req, context=self._ctx, timeout=60) as resp:
                raw = resp.read().decode("utf-8")
                payload: Any = json.loads(raw) if raw else {}
                return int(resp.status), payload
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            try:
                payload = json.loads(raw) if raw else {"detail": str(exc)}
            except json.JSONDecodeError:
                payload = {"detail": raw or str(exc)}
            return int(exc.code), payload

    def list_runs(self, limit: int = 100) -> list[dict[str, Any]]:
        status, payload = self.request("GET", f"/api/backtest-runs?limit={limit}")
        if status != 200:
            raise RuntimeError(f"list runs failed ({status}): {payload}")
        data = payload.get("data") if isinstance(payload, dict) else None
        if isinstance(data, list):
            return [row for row in data if isinstance(row, dict)]
        return []

    def queue_spec(self, spec: dict[str, Any]) -> tuple[int, Any]:
        return self.request("POST", "/api/backtest-runs", {"spec": spec})

    def cancel(self, run_id: str) -> tuple[int, Any]:
        return self.request("POST", f"/api/backtest-runs/{run_id}/cancel")


def _queue_counts(runs: list[dict[str, Any]]) -> tuple[int, int]:
    waiting = sum(1 for row in runs if row.get("status") == "queued")
    running = sum(1 for row in runs if row.get("status") == "running")
    return waiting, running


def _known_fingerprints(
    runs: list[dict[str, Any]],
    submitted: list[dict[str, Any]],
    *,
    plan_fingerprints: set[str] | None = None,
) -> set[str]:
    known: set[str] = set()
    for row in submitted:
        fp = row.get("fingerprint")
        if fp:
            known.add(str(fp))
    known.update(fingerprints_from_runs(runs, plan_fingerprints=plan_fingerprints))
    return known


def cancel_leftover_queued(client: DashboardClient, state: dict[str, Any]) -> int:
    cancelled = 0
    for row in client.list_runs(100):
        if row.get("status") != "queued":
            continue
        run_id = str(row.get("id") or "")
        if not run_id:
            continue
        status, payload = client.cancel(run_id)
        if status == 200:
            cancelled += 1
            state.setdefault("cancelled", []).append({"run_id": run_id, "at": datetime.now(CHICAGO).isoformat()})
            LOG.info("Cancelled leftover queued run %s", run_id)
        else:
            LOG.warning("Cancel %s failed (%s): %s", run_id, status, payload)
    return cancelled


def cancel_short_window_queued(
    client: DashboardClient,
    state: dict[str, Any],
    *,
    min_years: int = MIN_HORIZON_YEARS,
) -> int:
    """Free waiting slots that are still 2y so 5y/8y jobs can fill them."""
    cancelled = 0
    for row in client.list_runs(100):
        if row.get("status") != "queued":
            continue
        spec = row.get("spec") if isinstance(row.get("spec"), dict) else {}
        years = window_years_from_spec(spec)
        if years is not None and int(years) >= int(min_years):
            continue
        run_id = str(row.get("id") or "")
        if not run_id:
            continue
        status, payload = client.cancel(run_id)
        if status == 200:
            cancelled += 1
            state.setdefault("cancelled", []).append(
                {
                    "run_id": run_id,
                    "at": datetime.now(CHICAGO).isoformat(),
                    "reason": "short_window",
                    "window_years": years,
                }
            )
            LOG.info("Cancelled short-window queued run %s (%sy)", run_id, years)
        else:
            LOG.warning("Cancel %s failed (%s): %s", run_id, status, payload)
    return cancelled


def run_supervisor(
    *,
    base_url: str,
    until: datetime | None,
    poll_seconds: float,
    dry_run: bool,
    force: bool,
    horizon_only: bool = False,
) -> int:
    bootstrap_dotenv_into_environ(SKILL_DIR)
    pinned = load_state().get("end_date") if not dry_run else None
    if pinned:
        try:
            end_date = date.fromisoformat(str(pinned)[:10])
        except ValueError:
            end_date = campaign_end_date()
    else:
        end_date = campaign_end_date()
    plan, card_notes = augment_campaign_plan(campaign_plan(end_date=end_date), SKILL_DIR)
    for note in card_notes:
        LOG.info("%s", note)
    LOG.info(
        "Campaign plan: %s jobs, deadline %s, end_date %s, horizon_only=%s",
        len(plan),
        until.isoformat() if until else "none",
        end_date.isoformat(),
        horizon_only,
    )
    if dry_run:
        for row in plan:
            spec = row["spec"]
            label = "card" if row.get("source") == "hypothesis_card" else f"w{row['wave']}"
            print(
                f"{label} {spec['universe_preset']} "
                f"{','.join(spec['strategy_ids'])} {spec['start_date']}..{spec['end_date']} "
                f"{spec['theory_name']}"
            )
        return 0

    acquire_lock(force=force)
    state = load_state()
    state["deadline"] = until.isoformat() if until else "none"
    state["end_date"] = end_date.isoformat()
    state["plan_count"] = len(plan)
    state["base_url"] = base_url
    save_state(state)

    client = DashboardClient(base_url, (os.getenv("WEB_API_KEY") or "").strip())
    try:
        if horizon_only:
            try:
                dropped = cancel_short_window_queued(client, state)
                if dropped:
                    LOG.info("Freed %s short-window waiting slots for 5y/8y", dropped)
                    save_state(state)
            except Exception:
                LOG.exception("Short-window cancel pass failed")
        while True:
            now = datetime.now(CHICAGO)
            if until is not None and now >= until:
                LOG.info("Deadline reached; cancelling leftover queued jobs")
                try:
                    cancel_leftover_queued(client, state)
                except Exception:
                    LOG.exception("Deadline cancel pass failed")
                state["finished_at"] = now.isoformat()
                state["finish_reason"] = "deadline"
                save_state(state)
                return 0

            try:
                runs = client.list_runs(100)
            except Exception as exc:
                LOG.warning("Dashboard unreachable: %s", exc)
                time.sleep(max(15.0, poll_seconds))
                continue

            waiting, running = _queue_counts(runs)
            try:
                settled = settle_finished_queue_cards(SKILL_DIR, runs)
            except Exception:
                LOG.exception("Hypothesis card settle failed")
                settled = []
            if settled:
                settled_ids = set(settled)
                plan = [row for row in plan if row.get("card_id") not in settled_ids]
                for card_id in settled:
                    LOG.info("Hypothesis card settled: %s", card_id)
            known = _known_fingerprints(
                runs,
                list(state.get("submitted") or []),
                plan_fingerprints={str(row.get("fingerprint") or "") for row in plan},
            )
            remaining = select_remaining_campaign_jobs(plan, known, horizon_only=horizon_only)
            submitted_n = len(state.get("submitted") or [])
            LOG.info(
                "Queue waiting=%s running=%s submitted=%s remaining=%s",
                waiting,
                running,
                submitted_n,
                len(remaining),
            )
            state["last_poll"] = {
                "at": now.isoformat(),
                "waiting": waiting,
                "running": running,
                "remaining": len(remaining),
            }
            save_state(state)

            if not remaining and waiting == 0 and running == 0:
                if until is None:
                    LOG.info("Plan drained; idle until refill (no deadline)")
                    time.sleep(max(15.0, poll_seconds))
                    continue
                state["finished_at"] = now.isoformat()
                state["finish_reason"] = "plan_complete"
                save_state(state)
                LOG.info("Campaign plan drained")
                return 0

            if remaining and waiting < MAX_WAITING:
                nxt = remaining[0]
                status, payload = client.queue_spec(nxt["spec"])
                if status in (200, 201):
                    data = payload.get("data") if isinstance(payload, dict) else {}
                    run_id = ""
                    if isinstance(data, dict):
                        run_id = str(data.get("run_id") or "")
                    state.setdefault("submitted", []).append(
                        {
                            "fingerprint": nxt["fingerprint"],
                            "theory_name": nxt["theory_name"],
                            "run_id": run_id,
                            "at": datetime.now(CHICAGO).isoformat(),
                        }
                    )
                    save_state(state)
                    LOG.info("Queued %s run_id=%s", nxt["theory_name"], run_id)
                    time.sleep(1.0)
                    continue
                if status == 409:
                    LOG.info("Queue full; waiting")
                else:
                    LOG.warning("Queue POST failed (%s): %s", status, payload)
                    state.setdefault("errors", []).append(
                        {
                            "at": datetime.now(CHICAGO).isoformat(),
                            "theory_name": nxt["theory_name"],
                            "status": status,
                            "detail": str(payload)[:500],
                        }
                    )
                    save_state(state)
                    if status == 400:
                        # Do not spin on a spec the server will never accept.
                        state.setdefault("submitted", []).append(
                            {
                                "fingerprint": nxt["fingerprint"],
                                "theory_name": nxt["theory_name"],
                                "run_id": "",
                                "skipped": "http_400",
                                "at": datetime.now(CHICAGO).isoformat(),
                            }
                        )
                        save_state(state)
                        continue

            time.sleep(max(5.0, poll_seconds))
    finally:
        release_lock()


def main() -> int:
    parser = argparse.ArgumentParser(description="Fill the local backtest queue (no cutoff by default).")
    parser.add_argument("--base-url", default=os.getenv("LOCAL_DASHBOARD_URL", DEFAULT_BASE_URL))
    parser.add_argument(
        "--until",
        default="",
        help="Optional ISO stop time. Ignored when --no-deadline is set.",
    )
    parser.add_argument(
        "--no-deadline",
        action="store_true",
        default=True,
        help="Keep enqueueing with no stop time (default).",
    )
    parser.add_argument(
        "--deadline",
        dest="no_deadline",
        action="store_false",
        help="Honor --until or Sunday 23:59 America/Chicago.",
    )
    parser.add_argument("--poll-seconds", type=float, default=45.0)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true", help="Replace a stale supervisor lock")
    parser.add_argument(
        "--horizon-only",
        action="store_true",
        help="Queue only 5y/8y Nasdaq-100 / S&P 500 / S&P 1500 jobs; cancel waiting 2y.",
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    until: datetime | None
    if args.no_deadline:
        until = None
    elif args.until.strip():
        until = datetime.fromisoformat(args.until.strip())
        if until.tzinfo is None:
            until = until.replace(tzinfo=CHICAGO)
        else:
            until = until.astimezone(CHICAGO)
    else:
        until = campaign_deadline()

    return run_supervisor(
        base_url=str(args.base_url),
        until=until,
        poll_seconds=float(args.poll_seconds),
        dry_run=bool(args.dry_run),
        force=bool(args.force),
        horizon_only=bool(args.horizon_only),
    )


if __name__ == "__main__":
    raise SystemExit(main())
