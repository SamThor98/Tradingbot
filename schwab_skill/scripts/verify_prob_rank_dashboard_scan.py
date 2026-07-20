#!/usr/bin/env python3
"""POST a focused scan to the local dashboard and verify prob-rank shadow diagnostics."""

from __future__ import annotations

import argparse
import json
import ssl
import time
import urllib.error
import urllib.request
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
DEFAULT_OUT = SKILL_DIR / "validation_artifacts" / "prob_rank_shadow_smoke" / "dashboard_scan_verify.json"
DEFAULT_TICKERS = [
    "AAPL",
    "MSFT",
    "NVDA",
    "META",
    "AMZN",
    "GOOGL",
    "JPM",
    "XOM",
    "JNJ",
    "WMT",
    "AVGO",
    "LLY",
    "COST",
    "ORCL",
    "AMD",
    "CRM",
    "BAC",
    "V",
    "MA",
    "HD",
]


def _get_json(url: str, *, ctx: ssl.SSLContext, data: bytes | None = None) -> dict:
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"} if data is not None else {},
        method="POST" if data is not None else "GET",
    )
    with urllib.request.urlopen(req, context=ctx, timeout=120) as resp:
        return json.loads(resp.read().decode())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", type=str, default="https://127.0.0.1:8182")
    parser.add_argument("--out", type=str, default=str(DEFAULT_OUT))
    parser.add_argument("--poll-seconds", type=int, default=180)
    args = parser.parse_args(argv)

    ctx = ssl._create_unverified_context()
    body = json.dumps({"universe_mode": "tickers", "tickers": DEFAULT_TICKERS}).encode()
    try:
        start = _get_json(f"{args.base_url.rstrip('/')}/api/scan?async_mode=true", ctx=ctx, data=body)
    except urllib.error.URLError as exc:
        print(f"FAIL: cannot reach dashboard: {exc}")
        return 2

    data0 = start.get("data") or {}
    print(
        "start ok=",
        start.get("ok"),
        "status=",
        data0.get("status"),
        "started=",
        data0.get("started"),
    )

    final: dict = {}
    deadline = time.time() + max(30, args.poll_seconds)
    i = 0
    while time.time() < deadline:
        time.sleep(2)
        i += 1
        st = _get_json(f"{args.base_url.rstrip('/')}/api/scan/status", ctx=ctx)
        data = st.get("data") or {}
        status = str(data.get("status") or "")
        print(f"poll {i} status={status} signals={data.get('signals_found')}")
        if status in {"done", "completed", "error"}:
            final = data
            break
        # When job finishes, status may flip to idle with last_scan payload.
        if status == "idle":
            last = data.get("last_scan") if isinstance(data.get("last_scan"), dict) else data
            if last.get("diagnostics") is not None and i > 1:
                final = last
                final["status"] = "idle"
                break
    if not final:
        raw = _get_json(f"{args.base_url.rstrip('/')}/api/scan/status", ctx=ctx).get("data") or {}
        final = raw.get("last_scan") if isinstance(raw.get("last_scan"), dict) else raw

    diag = final.get("diagnostics") or {}
    signals = final.get("signals") or []
    keys = [
        "prob_rank_mode",
        "prob_rank_scored",
        "prob_rank_unscored",
        "prob_rank_would_keep",
        "prob_rank_would_drop",
        "prob_rank_top_n",
        "prob_rank_dropped",
        "stage_a_candidates",
    ]
    sample = None
    for s in signals:
        if s.get("expected_return_40d") is not None or s.get("prob_rank"):
            sample = {
                "ticker": s.get("ticker"),
                "expected_return_40d": s.get("expected_return_40d"),
                "prob_rank_model_id": s.get("prob_rank_model_id"),
                "prob_rank_selection": s.get("prob_rank_selection"),
            }
            break

    scored = int(diag.get("prob_rank_scored") or 0)
    dropped = int(diag.get("prob_rank_dropped") or 0)
    mode = str(diag.get("prob_rank_mode") or "")
    ok = mode == "shadow" and dropped == 0 and (
        scored > 0 or any(s.get("expected_return_40d") is not None for s in signals)
    )
    summary = {
        "ok": ok,
        "status": final.get("status"),
        "signals_found": final.get("signals_found")
        if final.get("signals_found") is not None
        else len(signals),
        "diag": {k: diag.get(k) for k in keys},
        "sample": sample,
        "error": final.get("error"),
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    print("VERIFY", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
