#!/usr/bin/env python3
"""Start the local dashboard on port 8182 with HTTPS for Schwab OAuth.

Schwab requires an https://127.0.0.1 callback. This script:
  - ensures a self-signed localhost certificate exists
  - optionally syncs SCHWAB_*_CALLBACK_URL in .env to the active port
  - runs Alembic once (skipped on uvicorn --reload re-imports)
  - launches uvicorn on https://127.0.0.1:8182

Usage (from schwab_skill):
  python scripts/start_local_dashboard.py
  python scripts/start_local_dashboard.py --port 8182 --no-sync-env
  python scripts/start_local_dashboard.py --reload
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
ENV_PATH = SKILL_DIR / ".env"


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
    args = parser.parse_args()

    sys.path.insert(0, str(SKILL_DIR))
    from run_dual_auth_browser import _make_cert

    cert_path, key_path = _make_cert()
    callback = f"https://{args.host}:{args.port}/"
    if not args.no_sync_env:
        callback = _sync_callback_env(args.port)

    _run_alembic_once()

    print(f"Dashboard URL : https://{args.host}:{args.port}/")
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
    return subprocess.call(cmd, cwd=SKILL_DIR, env=env)


if __name__ == "__main__":
    raise SystemExit(main())
