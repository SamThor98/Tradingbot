#!/usr/bin/env python3
"""Start the local dashboard on port 8182 with HTTPS for Schwab OAuth.

Schwab requires an https://127.0.0.1 callback. This script:
  - fast-forwards local ``main`` to ``origin/main`` (skip with --no-pull)
  - stops a stale listener on the dashboard port by PID (skip with --no-replace)
  - refuses to boot if Scan studio files are missing
  - ensures a self-signed localhost certificate exists
  - optionally syncs SCHWAB_*_CALLBACK_URL in .env to the active port
  - runs Alembic once (skipped on uvicorn --reload re-imports)
  - launches uvicorn on https://127.0.0.1:8182

Usage (from schwab_skill):
  python scripts/start_local_dashboard.py
  python scripts/start_local_dashboard.py --port 8182 --no-sync-env
  python scripts/start_local_dashboard.py --signal-stack-enforced
  python scripts/start_local_dashboard.py --signal-stack-enforced --multi-sleeve-rth-shadow
  python scripts/start_local_dashboard.py --entry-timing-experiment
  python scripts/start_local_dashboard.py --entry-timing-live
"""
from __future__ import annotations

import argparse
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = SKILL_DIR if (SKILL_DIR / ".git").exists() else SKILL_DIR.parent
ENV_PATH = SKILL_DIR / ".env"
SCAN_STUDIO_JS = SKILL_DIR / "webapp" / "static" / "panels" / "scanStudio.js"


def parse_netstat_listening_pids(text: str, port: int) -> list[int]:
    """Parse Windows ``netstat -ano`` LISTENING rows for one TCP port."""
    pids: set[int] = set()
    needle = re.compile(rf":{port}\s")
    for line in text.splitlines():
        if "LISTENING" not in line.upper():
            continue
        if not needle.search(line):
            continue
        parts = line.split()
        if not parts:
            continue
        try:
            pids.add(int(parts[-1]))
        except ValueError:
            continue
    return sorted(pids)


def parse_lsof_pids(text: str) -> list[int]:
    """Parse ``lsof -t`` output into PIDs."""
    pids: set[int] = set()
    for line in text.splitlines():
        token = line.strip()
        if token.isdigit():
            pids.add(int(token))
    return sorted(pids)


def listener_pids(port: int) -> list[int]:
    """PIDs listening on TCP ``port`` (Windows netstat or Unix lsof)."""
    me = os.getpid()
    found: list[int] = []
    if os.name == "nt":
        try:
            out = subprocess.check_output(
                ["netstat", "-ano"],
                text=True,
                errors="replace",
                timeout=10,
            )
        except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
            return []
        found = parse_netstat_listening_pids(out, port)
    else:
        try:
            out = subprocess.check_output(
                ["lsof", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"],
                text=True,
                errors="replace",
                timeout=10,
            )
            found = parse_lsof_pids(out)
        except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
            found = []
    return [pid for pid in found if pid > 1 and pid != me]


def free_listen_port(port: int) -> None:
    """Stop whatever is bound to the operator dashboard port (by PID only)."""
    pids = listener_pids(port)
    if not pids:
        return
    for pid in pids:
        print(f"Stopping stale process {pid} on port {port}")
        if os.name == "nt":
            subprocess.call(["taskkill", "/PID", str(pid), "/F"], timeout=15)
        else:
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                continue
    deadline = time.time() + 4
    while time.time() < deadline:
        leftover = listener_pids(port)
        if not leftover:
            return
        time.sleep(0.2)
    for pid in listener_pids(port):
        print(f"Force-stopping process {pid} on port {port}")
        if os.name == "nt":
            subprocess.call(["taskkill", "/PID", str(pid), "/F"], timeout=15)
        else:
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                continue


def _git(cwd: Path, *git_args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *git_args],
        cwd=cwd,
        text=True,
        capture_output=True,
        timeout=120,
    )


def sync_origin_main() -> None:
    """Fast-forward local ``main`` to ``origin/main`` so 8182 serves Scan studio."""
    git_dir = REPO_ROOT / ".git"
    if not git_dir.exists():
        return
    print("Fetching origin main…")
    fetched = _git(REPO_ROOT, "fetch", "origin", "main")
    if fetched.returncode != 0:
        print((fetched.stderr or fetched.stdout or "git fetch failed").strip())
        print("Continuing with local files.")
        return
    branch = _git(REPO_ROOT, "rev-parse", "--abbrev-ref", "HEAD")
    name = (branch.stdout or "").strip()
    if name not in {"main", "master"}:
        print(f"On branch {name}; not fast-forwarding main.")
        return
    merged = _git(REPO_ROOT, "merge", "--ff-only", "origin/main")
    if merged.returncode != 0:
        print((merged.stderr or merged.stdout or "fast-forward failed").strip())
        print("Local main is dirty or has diverged. 8182 will keep serving this working tree.")
        return
    out = (merged.stdout or "").strip()
    if out:
        print(out)


def require_scan_studio() -> None:
    if SCAN_STUDIO_JS.is_file():
        print(f"Scan studio: {SCAN_STUDIO_JS}")
        return
    raise SystemExit(
        "Scan studio is missing from this working tree.\n"
        "This dashboard is not the Scan studio build. From the repo root run:\n"
        "  git fetch origin main && git switch main && git merge --ff-only origin/main\n"
        "Then start again: python scripts/start_local_dashboard.py"
    )


def _sync_callback_env(port: int) -> str:
    """Local Schwab apps register a single root callback URL per app."""
    callback = f"https://127.0.0.1:{port}/"
    if not ENV_PATH.exists():
        return callback
    lines = ENV_PATH.read_text(encoding="utf-8").splitlines()
    out: list[str] = []
    saw_account = False
    saw_market = False
    for line in lines:
        if line.startswith("SCHWAB_CALLBACK_URL="):
            out.append(f"SCHWAB_CALLBACK_URL={callback}")
            saw_account = True
            continue
        if line.startswith("SCHWAB_MARKET_CALLBACK_URL="):
            out.append(f"SCHWAB_MARKET_CALLBACK_URL={callback}")
            saw_market = True
            continue
        out.append(line)
    if not saw_account:
        out.append(f"SCHWAB_CALLBACK_URL={callback}")
    if not saw_market:
        out.append(f"SCHWAB_MARKET_CALLBACK_URL={callback}")
    ENV_PATH.write_text("\n".join(out).rstrip() + "\n", encoding="utf-8")
    return callback


def _run_alembic_once() -> None:
    alembic_ini = SKILL_DIR / "alembic.ini"
    if not alembic_ini.is_file():
        return
    print("Running database migrations (once)...")
    rc = subprocess.call(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=SKILL_DIR,
    )
    if rc != 0:
        raise SystemExit(f"Alembic upgrade failed with exit code {rc}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Start local HTTPS dashboard for Schwab OAuth.")
    parser.add_argument("--port", type=int, default=int(os.getenv("LOCAL_WEB_PORT", "8182")))
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument(
        "--no-sync-env",
        action="store_true",
        help="Do not rewrite SCHWAB_CALLBACK_URL / SCHWAB_MARKET_CALLBACK_URL in .env",
    )
    parser.add_argument(
        "--reload",
        action="store_true",
        help="Enable auto-reload on webapp/ changes (slower; can loop on OneDrive sync)",
    )
    parser.add_argument(
        "--no-pull",
        action="store_true",
        help="Do not fetch/fast-forward origin/main before start",
    )
    parser.add_argument(
        "--no-replace",
        action="store_true",
        help="Do not stop an existing listener on this port",
    )
    parser.add_argument(
        "--signal-stack-shadow",
        action="store_true",
        help="Upsert P0 stack SHADOW vars (exit grace + breakout buffer) into .env before starting",
    )
    parser.add_argument(
        "--signal-stack-enforced",
        action="store_true",
        help="Upsert promoted P0 stack (live 1%% buffer + live exit grace) into .env before starting",
    )
    parser.add_argument(
        "--entry-timing-experiment",
        action="store_true",
        help="Upsert P0 entry-timing SHADOW experiment vars into .env before starting",
    )
    parser.add_argument(
        "--entry-timing-live",
        action="store_true",
        help="Upsert P0 entry-timing LIVE vars (1%% breakout buffer) into .env before starting",
    )
    parser.add_argument(
        "--multi-sleeve-rth-shadow",
        action="store_true",
        help="Upsert allocator SHADOW + hypothesis ledger vars for RTH evidence weeks",
    )
    args = parser.parse_args()

    if not args.no_pull:
        sync_origin_main()
    require_scan_studio()
    if not args.no_replace:
        free_listen_port(args.port)

    sys.path.insert(0, str(SKILL_DIR))
    if args.signal_stack_enforced:
        from core.env_local import apply_signal_stack_enforced_env

        changed = apply_signal_stack_enforced_env(ENV_PATH)
        if changed:
            print(f"Signal stack enforced env updated in {ENV_PATH}: {', '.join(changed)}")
        else:
            print(f"Signal stack enforced env already set in {ENV_PATH}")
    elif args.entry_timing_live:
        from core.env_local import apply_entry_timing_live_env

        changed = apply_entry_timing_live_env(ENV_PATH)
        if changed:
            print(f"Entry-timing LIVE env updated in {ENV_PATH}: {', '.join(changed)}")
        else:
            print(f"Entry-timing LIVE env already set in {ENV_PATH}")
    elif args.signal_stack_shadow:
        from core.env_local import apply_signal_stack_shadow_env

        changed = apply_signal_stack_shadow_env(ENV_PATH)
        if changed:
            print(f"Signal stack shadow env updated in {ENV_PATH}: {', '.join(changed)}")
        else:
            print(f"Signal stack shadow env already set in {ENV_PATH}")
    elif args.entry_timing_experiment:
        from core.env_local import apply_entry_timing_experiment_env

        changed = apply_entry_timing_experiment_env(ENV_PATH)
        if changed:
            print(f"Entry-timing experiment env updated in {ENV_PATH}: {', '.join(changed)}")
        else:
            print(f"Entry-timing experiment env already set in {ENV_PATH}")

    if args.multi_sleeve_rth_shadow:
        from core.env_local import apply_multi_sleeve_rth_shadow_env

        changed = apply_multi_sleeve_rth_shadow_env(ENV_PATH)
        if changed:
            print(f"Multi-sleeve RTH shadow env updated in {ENV_PATH}: {', '.join(changed)}")
        else:
            print(f"Multi-sleeve RTH shadow env already set in {ENV_PATH}")

    from run_dual_auth_browser import _make_cert

    cert_path, key_path = _make_cert()
    callback = f"https://{args.host}:{args.port}/"
    if not args.no_sync_env:
        callback = _sync_callback_env(args.port)

    _run_alembic_once()

    print(f"Dashboard URL : https://{args.host}:{args.port}/")
    print("This is the operator UI (not uvicorn :8000).")
    print(f"Schwab callback: {callback}")
    print("Register that callback URL on BOTH Schwab Developer Portal apps.")
    print("Accept the browser certificate warning once (self-signed localhost cert).")
    print("Waiting for uvicorn… (first load ~10–20s). Open the URL after 'Application startup complete'.")

    cmd = [
        sys.executable,
        "-m",
        "uvicorn",
        "webapp.main:app",
        "--host",
        args.host,
        "--port",
        str(args.port),
        "--ssl-keyfile",
        str(key_path),
        "--ssl-certfile",
        str(cert_path),
    ]
    if args.reload:
        cmd.extend(["--reload", "--reload-dir", "webapp"])
    env = os.environ.copy()
    env["WEBAPP_SKIP_ALEMBIC"] = "1"
    rc = subprocess.call(cmd, cwd=SKILL_DIR, env=env)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
