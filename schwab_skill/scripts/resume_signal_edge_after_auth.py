#!/usr/bin/env python3
"""Resume P0 signal-edge pipeline after Schwab OAuth is healthy.

Checks refresh-token validity, then (when --run):
  1. Smoke entry-timing scan
  2. Resume control_legacy_aug multi-era (late_bull + volatility_chop)
  3. Refresh entry-timing replay cache (new-era trades)
  4. Refresh stack counterfactual + phase2 readiness validators

Usage (from schwab_skill/):
  python scripts/resume_signal_edge_after_auth.py
  python scripts/resume_signal_edge_after_auth.py --run --max-workers 4
  python scripts/resume_signal_edge_after_auth.py --run --watch-auth-seconds 7200
"""

from __future__ import annotations

import argparse
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import requests

SKILL_DIR = Path(__file__).resolve().parent.parent
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

from schwab_auth import _decrypt, _get_encryption_key  # noqa: E402

SCRIPTS = SKILL_DIR / "scripts"
ART = SKILL_DIR / "validation_artifacts"
DEFAULT_RUN_ID = "control_legacy_aug"
ENV_OVERRIDES = ART / "phase1_env_overrides" / f"{DEFAULT_RUN_ID}_stack.json"
FALLBACK_ENV_OVERRIDES = ART / "phase1_env_overrides" / f"{DEFAULT_RUN_ID}.json"
# Multi-era generates baseline control trades; stack replay is offline only.
MULTI_ERA_ENV_OVERRIDES = FALLBACK_ENV_OVERRIDES
TOKEN_URL = "https://api.schwabapi.com/v1/oauth/token"


def _token_dir() -> Path:
    raw = (os.getenv("SCHWAB_TOKEN_DIR") or "").strip()
    return Path(raw) if raw else SKILL_DIR


def _port_open(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.3)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def _port_holder_hint(port: int) -> str:
    if not _port_open(port):
        return "free"
    try:
        import subprocess

        out = subprocess.check_output(["netstat", "-ano"], text=True, errors="replace", timeout=5)
        pid = None
        for line in out.splitlines():
            if f":{port}" in line and "LISTENING" in line:
                parts = line.split()
                if parts:
                    pid = parts[-1]
                    break
        if not pid or pid == "0":
            return "in use"
        cmd = subprocess.check_output(
            ["wmic", "process", "where", f"processid={pid}", "get", "commandline"],
            text=True,
            errors="replace",
            timeout=5,
        )
        low = cmd.lower()
        if "run_dual_auth_browser" in low:
            return "cli_oauth"
        if "uvicorn" in low or "start_local_dashboard" in low:
            return "dashboard"
        return "in use"
    except Exception:
        return "in use"


def _print_oauth_help() -> None:
    port = 8182
    holder = _port_holder_hint(port)
    print("\nNext step (interactive, requires browser):")
    if holder == "cli_oauth":
        print(f"  CLI OAuth is on port {port} (run_dual_auth_browser).")
        print("  Complete MARKET then ACCOUNT in the Schwab browser tab.")
        print("  Use the Auth URL printed in that terminal (state must match).")
    elif holder == "dashboard":
        print(f"  Dashboard is on port {port}. Use Connect UI:")
        print("    python scripts/open_schwab_connect.py")
        print(f"    https://127.0.0.1:{port}/?section=connect")
    elif _port_open(port):
        print(f"  Port {port} is in use. Finish Schwab OAuth or free the port.")
    else:
        print("  python scripts/start_local_dashboard.py")
        print("  python scripts/open_schwab_connect.py")
        print("  Or: stop dashboard, then python scripts/fix_schwab_auth.py")
    print("Then re-run:")
    print("  python scripts/resume_signal_edge_after_auth.py --run")


def _load_env(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    out: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in line:
            continue
        key, val = stripped.split("=", 1)
        out[key.strip()] = val.strip()
    return out


def _schwab_refresh_ok() -> tuple[bool, str]:
    """Return (ok, message) after probing market refresh token."""
    token_dir = _token_dir()
    market_path = token_dir / "tokens_market.enc"
    account_path = token_dir / "tokens_account.enc"
    if not market_path.exists():
        return False, "tokens_market.enc missing — complete Schwab OAuth in browser"
    if not account_path.exists():
        return False, "tokens_account.enc missing — complete account Schwab OAuth"

    env = _load_env(SKILL_DIR / ".env")
    app_key = env.get("SCHWAB_MARKET_APP_KEY", "")
    app_secret = env.get("SCHWAB_MARKET_APP_SECRET", "")
    if not app_key or not app_secret:
        return False, "SCHWAB_MARKET_APP_KEY/SECRET missing in .env"

    try:
        key = _get_encryption_key(app_secret)
        payload = _decrypt(market_path.read_bytes(), key)
    except Exception as exc:
        return False, f"cannot decrypt tokens_market.enc: {exc}"

    refresh = str(payload.get("refresh_token") or "").strip()
    if not refresh:
        return False, "no refresh_token in tokens_market.enc"

    resp = requests.post(
        TOKEN_URL,
        auth=(app_key, app_secret),
        data={"grant_type": "refresh_token", "refresh_token": refresh},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=30,
    )
    if resp.status_code == 200:
        return True, "market refresh_token OK"
    body = (resp.text or "").strip().replace("\n", " ")[:200]
    return False, f"refresh failed HTTP {resp.status_code}: {body}"


def _run_step(name: str, cmd: list[str], *, cwd: Path | None = None) -> bool:
    print(f"\n[resume] {name}", flush=True)
    proc = subprocess.run(cmd, cwd=str(cwd or SKILL_DIR), check=False)
    ok = proc.returncode == 0
    print(f"[resume] {name}: {'PASS' if ok else 'FAIL'} (rc={proc.returncode})", flush=True)
    return ok


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", default=DEFAULT_RUN_ID)
    parser.add_argument(
        "--run",
        action="store_true",
        help="Execute smoke scan + multi-era resume + artifact refresh (default: auth check only).",
    )
    parser.add_argument("--max-workers", type=int, default=4)
    parser.add_argument("--timeout-seconds", type=int, default=7200)
    parser.add_argument(
        "--watch-auth-seconds",
        type=int,
        default=0,
        help="Poll for Schwab OAuth tokens up to N seconds before failing (use with --run).",
    )
    args = parser.parse_args()

    ok, msg = _schwab_refresh_ok()
    if not ok and args.watch_auth_seconds > 0:
        deadline = time.time() + max(1, int(args.watch_auth_seconds))
        poll_sec = 15
        print(f"Schwab auth: FAIL — {msg}", flush=True)
        print(
            f"Watching for tokens up to {args.watch_auth_seconds}s "
            f"(complete Connect Schwab in browser)...",
            flush=True,
        )
        _print_oauth_help()
        while time.time() < deadline:
            time.sleep(min(poll_sec, max(0.0, deadline - time.time())))
            ok, msg = _schwab_refresh_ok()
            if ok:
                print(f"Schwab auth: OK — {msg}", flush=True)
                break
        else:
            print(f"Schwab auth: still FAIL — {msg}", flush=True)
            return 1
    elif not ok:
        print(f"Schwab auth: FAIL — {msg}")
        _print_oauth_help()
        return 1
    else:
        print(f"Schwab auth: OK — {msg}")

    if not args.run:
        print("\nAuth healthy. To resume pipeline:")
        print("  python scripts/resume_signal_edge_after_auth.py --run")
        return 0

    py = sys.executable
    steps_ok = True

    steps_ok &= _run_step(
        "entry_timing_smoke_scan",
        [py, str(SCRIPTS / "run_entry_timing_experiment_scan.py"), "--smoke"],
    )
    steps_ok &= _run_step(
        "compare_live_to_offline",
        [py, str(SCRIPTS / "compare_live_entry_shadow_to_offline.py"), "--write-artifact"],
    )

    if not MULTI_ERA_ENV_OVERRIDES.is_file():
        print(f"FAIL: missing multi-era env overrides {MULTI_ERA_ENV_OVERRIDES}")
        return 1
    multi_era_env = MULTI_ERA_ENV_OVERRIDES

    steps_ok &= _run_step(
        "multi_era_resume",
        [
            py,
            str(SCRIPTS / "run_multi_era_backtest_schwab_only.py"),
            "--run-tag",
            args.run_id,
            "--env-overrides",
            str(multi_era_env),
            "--max-workers",
            str(max(1, int(args.max_workers))),
            "--timeout-seconds",
            str(max(600, int(args.timeout_seconds))),
        ],
    )

    steps_ok &= _run_step(
        "entry_timing_replay_cache_refresh",
        [
            py,
            str(SCRIPTS / "analyze_entry_timing_shadow_counterfactual.py"),
            "--run-id",
            args.run_id,
            "--all",
        ],
    )

    steps_ok &= _run_step(
        "signal_stack_counterfactual",
        [py, str(SCRIPTS / "analyze_signal_stack_counterfactual.py"), "--run-id", args.run_id],
    )
    steps_ok &= _run_step(
        "weekly_check",
        [py, str(SCRIPTS / "entry_timing_live_weekly_check.py"), args.run_id],
    )
    steps_ok &= _run_step(
        "phase2_edge_audit_aug",
        [
            py,
            str(SCRIPTS / "phase2_edge_audit.py"),
            "--bare-run-id",
            "stage2_only_aug",
            "--control-run-id",
            args.run_id,
            "--out-prefix",
            "phase2_edge_audit_aug",
        ],
    )

    print("\n=== Resume summary ===")
    if steps_ok:
        print("All steps passed. Review validation_artifacts/phase2_edge_audit_aug.json")
        return 0
    print("One or more steps failed — inspect logs above.")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
