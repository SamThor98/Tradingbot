#!/usr/bin/env python3
"""Production-safe light-load probe for read-only web endpoints."""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SKILL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL_DIR))

from scripts.web_safe_routes import safe_read_routes


def _endpoint_list(include_saas: bool, endpoints_file: str) -> list[str]:
    if endpoints_file:
        path = Path(endpoints_file)
        if not path.is_absolute():
            path = SKILL_DIR / endpoints_file
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, list):
            raise ValueError("endpoints file must contain a JSON list of paths")
        return [str(x).strip() for x in data if str(x).strip()]
    return safe_read_routes(include_saas=include_saas)


def _request_once(base_url: str, path: str, timeout_sec: float) -> dict[str, Any]:
    url = f"{base_url.rstrip('/')}{path}"
    started = time.perf_counter()
    try:
        req = urllib.request.Request(url, method="GET", headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
            _ = resp.read()
            status = int(resp.getcode() or 0)
            latency_ms = (time.perf_counter() - started) * 1000.0
            return {"ok": 200 <= status < 400, "status": status, "latency_ms": latency_ms, "path": path}
    except urllib.error.HTTPError as exc:
        latency_ms = (time.perf_counter() - started) * 1000.0
        return {"ok": False, "status": int(exc.code or 0), "latency_ms": latency_ms, "path": path}
    except Exception as exc:  # noqa: BLE001
        latency_ms = (time.perf_counter() - started) * 1000.0
        return {"ok": False, "status": 0, "latency_ms": latency_ms, "path": path, "error": str(exc)}


def _run_phase(
    *,
    base_url: str,
    endpoints: list[str],
    requests_per_endpoint: int,
    concurrency: int,
    timeout_sec: float,
) -> list[dict[str, Any]]:
    jobs: list[tuple[str, int]] = []
    for path in endpoints:
        for idx in range(max(1, requests_per_endpoint)):
            jobs.append((path, idx))
    results: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
        futures = [pool.submit(_request_once, base_url, path, timeout_sec) for path, _ in jobs]
        for fut in as_completed(futures):
            results.append(fut.result())
    return results


def _percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = max(0, min(len(ordered) - 1, int(round((p / 100.0) * (len(ordered) - 1)))))
    return ordered[idx]


def main() -> int:
    parser = argparse.ArgumentParser(description="Production-safe light-load web probe")
    parser.add_argument("--base-url", required=True, help="Base URL like http://127.0.0.1:8000")
    parser.add_argument("--include-saas", action="store_true", help="Include SaaS-only readiness/live endpoints")
    parser.add_argument("--endpoints-file", default="", help="Optional JSON file containing endpoint path list")
    parser.add_argument("--burst-requests-per-endpoint", type=int, default=5)
    parser.add_argument("--steady-requests-per-endpoint", type=int, default=8)
    parser.add_argument("--burst-concurrency", type=int, default=8)
    parser.add_argument("--steady-concurrency", type=int, default=4)
    parser.add_argument("--timeout-sec", type=float, default=10.0)
    parser.add_argument("--max-error-rate-pct", type=float, default=5.0)
    parser.add_argument("--max-p95-ms", type=float, default=1500.0)
    args = parser.parse_args()

    endpoints = _endpoint_list(include_saas=bool(args.include_saas), endpoints_file=args.endpoints_file)
    if not endpoints:
        raise SystemExit("No endpoints configured for load smoke probe.")

    burst = _run_phase(
        base_url=args.base_url,
        endpoints=endpoints,
        requests_per_endpoint=max(1, args.burst_requests_per_endpoint),
        concurrency=max(1, args.burst_concurrency),
        timeout_sec=max(1.0, args.timeout_sec),
    )
    steady = _run_phase(
        base_url=args.base_url,
        endpoints=endpoints,
        requests_per_endpoint=max(1, args.steady_requests_per_endpoint),
        concurrency=max(1, args.steady_concurrency),
        timeout_sec=max(1.0, args.timeout_sec),
    )
    merged = burst + steady
    latencies = [float(x["latency_ms"]) for x in merged]
    failures = [x for x in merged if not x.get("ok")]
    total = len(merged)
    error_rate = (len(failures) / total * 100.0) if total else 100.0
    p95 = _percentile(latencies, 95.0)
    summary = {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "base_url": args.base_url.rstrip("/"),
        "endpoints": endpoints,
        "totals": {
            "requests": total,
            "failed": len(failures),
            "error_rate_pct": round(error_rate, 3),
            "latency_p50_ms": round(_percentile(latencies, 50.0), 2),
            "latency_p95_ms": round(p95, 2),
            "latency_avg_ms": round(statistics.mean(latencies), 2) if latencies else 0.0,
        },
        "thresholds": {
            "max_error_rate_pct": float(args.max_error_rate_pct),
            "max_p95_ms": float(args.max_p95_ms),
        },
        "failures": failures[:50],
    }
    out_path = SKILL_DIR / "validation_artifacts" / "latest_web_load_smoke.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    error_failed = error_rate > float(args.max_error_rate_pct)
    latency_failed = p95 > float(args.max_p95_ms)
    if error_failed or latency_failed:
        reasons: list[str] = []
        if error_failed:
            reasons.append(f"error_rate_exceeded:{error_rate:.2f}>{args.max_error_rate_pct}")
        if latency_failed:
            reasons.append(f"p95_exceeded:{p95:.2f}>{args.max_p95_ms}")
        print("FAIL: web load smoke thresholds failed")
        print(json.dumps({"reasons": reasons, "summary": summary}, indent=2))
        print(f"Artifact: {out_path}")
        return 1

    print("PASS: web load smoke thresholds satisfied")
    print(json.dumps(summary, indent=2))
    print(f"Artifact: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
