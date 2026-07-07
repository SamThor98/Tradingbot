#!/usr/bin/env python3
"""One-screen P0 signal-edge blocker status and next command."""

from __future__ import annotations

import json
import os
import socket
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

ART = SKILL_DIR / "validation_artifacts"


def _token_dir() -> Path:
    raw = (os.getenv("SCHWAB_TOKEN_DIR") or "").strip()
    return Path(raw) if raw else SKILL_DIR


def _port_open(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.3)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def _port_holder_hint(port: int) -> str:
    """Best-effort label for what is listening on a local port (Windows)."""
    if not _port_open(port):
        return "free"
    try:
        import subprocess

        out = subprocess.check_output(
            ["netstat", "-ano"],
            text=True,
            errors="replace",
            timeout=5,
        )
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
        if "run_dual_auth_browser" in low or "fix_schwab_auth" in low:
            return "CLI OAuth waiting — finish Schwab in browser (run_dual_auth_browser)"
        if "uvicorn" in low or "start_local_dashboard" in low:
            return "dashboard/oauth"
        return "in use"
    except Exception:
        return "in use"


def _load_json(name: str) -> dict | None:
    path = ART / name
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def main() -> int:
    from scripts.resume_signal_edge_after_auth import _schwab_refresh_ok

    print("=== Signal-edge blocker status ===")

    token_dir = _token_dir()
    market_ok = (token_dir / "tokens_market.enc").is_file()
    account_ok = (token_dir / "tokens_account.enc").is_file()
    print(f"tokens_market.enc: {'OK' if market_ok else 'MISSING'}")
    print(f"tokens_account.enc: {'OK' if account_ok else 'MISSING'}")

    auth_ok, auth_msg = _schwab_refresh_ok()
    print(f"Schwab refresh: {'OK' if auth_ok else 'FAIL'} — {auth_msg}")

    port = 8182
    print(f"port {port}: {_port_holder_hint(port)}")

    progress = _load_json("multi_era_backtest_schwab_only_control_legacy_aug_progress.json")
    if progress:
        completed = [str(r.get("era")) for r in progress.get("completed") or []]
        print(f"multi-era control_legacy_aug: {len(completed)}/5 eras {completed}")
    else:
        print("multi-era progress: missing")

    stack = _load_json("signal_stack_counterfactual_control_legacy_aug.json")
    if stack and isinstance(stack.get("scenarios"), dict):
        row = stack["scenarios"].get("exit_grace_breakout_buffer_0.010") or {}
        print(
            f"stack offline (control 5-era artifact): pf_mean={row.get('pf_mean')} worst={row.get('worst_era_pf')} "
            f"gates={'PASS' if row.get('passes_promotion_gates') else 'FAIL'}"
        )
        print(
            "  note: 3-era subset (recent+bear+crash) still PASS offline at pf_mean~1.54 worst~1.28 "
            "when old eras are excluded"
        )

    bare_stack = _load_json("signal_stack_counterfactual_stage2_only_aug.json")
    if bare_stack and isinstance(bare_stack.get("scenarios"), dict):
        row = bare_stack["scenarios"].get("exit_grace_breakout_buffer_0.010") or {}
        print(
            f"stack offline (bare stage2 5-era): pf_mean={row.get('pf_mean')} worst={row.get('worst_era_pf')} "
            f"gates={'PASS' if row.get('passes_promotion_gates') else 'FAIL'}"
        )

    all_eras = ("recent_current", "bear_rates", "crash_recovery", "volatility_chop", "late_bull")
    if progress:
        completed_set = {str(r.get("era")) for r in progress.get("completed") or []}
        pending = [e for e in all_eras if e not in completed_set]
        if pending:
            print(f"pending multi-era backfill: {pending}")
            print(
                "  stage2 bare stack proxy on pending eras: late_bull~0.84 volatility_chop~0.66 "
                "(control 5-era backfill is the gate test after OAuth)"
            )

    print("\n=== Next command ===")
    if not auth_ok:
        connect_url = f"https://127.0.0.1:{port}/?section=connect"
        account_oauth = f"https://127.0.0.1:{port}/api/oauth/schwab/start"
        market_oauth = f"https://127.0.0.1:{port}/api/oauth/schwab/market/start"
        if not _port_open(port):
            print("1) python scripts/start_local_dashboard.py")
            print(f"2) Open {connect_url}")
            print("3) Complete Schwab login in browser (account first, then market)")
            print("4) python scripts/_test_refresh.py")
        elif "CLI OAuth" in _port_holder_hint(port):
            print("CLI OAuth is running on port 8182 (run_dual_auth_browser).")
            print("  Complete MARKET then ACCOUNT approval in the Schwab browser tab.")
            print("  Do NOT start the dashboard until tokens are saved.")
        else:
            print(f"Dashboard is running on port {port}.")
            print(f"  Connect UI: {connect_url}")
            print(f"  Account OAuth: {account_oauth}")
            print(f"  Market OAuth:  {market_oauth}")
            print("  Or run: python scripts/open_schwab_connect.py")
            print("Complete both Schwab approvals in browser, then:")
            print("  python scripts/_test_refresh.py")
            print("Do NOT run fix_schwab_auth.py while the dashboard holds port 8182.")
            print("If dashboard OAuth keeps failing:")
            print("  1) Stop the dashboard (free port 8182)")
            print("  2) python scripts/fix_schwab_auth.py")
            print("  3) python scripts/start_local_dashboard.py")
            print("  4) python scripts/resume_signal_edge_after_auth.py --run --max-workers 4")
        print("5) python scripts/resume_signal_edge_after_auth.py --run --max-workers 4")
        print("   Or auto-resume after OAuth:")
        print("   python scripts/resume_signal_edge_after_auth.py --run --watch-auth-seconds 7200 --max-workers 4")
        return 1

    print("python scripts/resume_signal_edge_after_auth.py --run --max-workers 4")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
