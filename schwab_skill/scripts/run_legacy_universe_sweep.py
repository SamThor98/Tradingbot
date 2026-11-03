#!/usr/bin/env python3
"""Run control_legacy_aug across Scan studio universes.

Smoke (default) is recent_current only, smallest universe first.
Full is the five-era Schwab-only harness per universe.

  python scripts/run_legacy_universe_sweep.py
  python scripts/run_legacy_universe_sweep.py --mode full --universes nasdaq100,sp500
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SKILL_DIR = Path(__file__).resolve().parent.parent
ARTIFACT_DIR = SKILL_DIR / "validation_artifacts"
ENV_OVERRIDES = SKILL_DIR / "research" / "env_overrides" / "control_legacy_aug.json"
MULTI_ERA = SKILL_DIR / "scripts" / "run_multi_era_backtest_schwab_only.py"

# Scan-table indexes that have a wired constituent feed. custom needs pasted
# tickers; russell2000 is advertised but not available yet.
SKIP_PRESETS = frozenset({"custom", "russell2000"})
SMOKE_ERA = "recent_current"


def runnable_scan_universes() -> list[dict[str, Any]]:
    sys.path.insert(0, str(SKILL_DIR))
    from core.scan_catalog import UNIVERSES

    rows: list[dict[str, Any]] = []
    for raw in UNIVERSES:
        preset = str(raw.get("id") or "").strip().lower()
        if not preset or preset in SKIP_PRESETS:
            continue
        if not bool(raw.get("available", True)):
            continue
        rows.append(dict(raw))
    return rows


def _default_presets(*, include_sp1500: bool) -> list[str]:
    rows = runnable_scan_universes()
    rows.sort(key=lambda r: int(r.get("approx_size") or 10**9))
    presets = [str(r["id"]) for r in rows]
    if not include_sp1500:
        presets = [p for p in presets if p != "sp1500"]
    return presets


def _run_tag(preset: str, mode: str) -> str:
    suffix = "recent" if mode == "smoke" else "full"
    return f"control_legacy_aug_{preset}_{suffix}"


def _summarize_artifact(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"artifact": str(path), "status": "missing"}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"artifact": str(path), "status": "unreadable", "error": str(exc)}
    rows = [r for r in (payload.get("results") or []) if isinstance(r, dict)]
    pfs = [float(r.get("profit_factor_net") or 0.0) for r in rows if r.get("profit_factor_net") is not None]
    trades = sum(int(r.get("total_trades") or 0) for r in rows)
    return {
        "artifact": str(path),
        "status": "ok",
        "eras": [str(r.get("era") or r.get("name") or "") for r in rows],
        "total_trades": trades,
        "pf_mean": round(sum(pfs) / len(pfs), 3) if pfs else None,
        "worst_era_pf": round(min(pfs), 3) if pfs else None,
        "per_era_pf": {str(r.get("era") or r.get("name") or ""): r.get("profit_factor_net") for r in rows},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Sweep control_legacy_aug across scan-catalog indexes.")
    parser.add_argument("--mode", choices=("smoke", "full"), default="smoke")
    parser.add_argument(
        "--universes",
        default="",
        help="Comma-separated presets (default: all wired scan-table indexes, smallest first).",
    )
    parser.add_argument(
        "--include-sp1500",
        action="store_true",
        help="Include S&P 1500 in the default smoke list (omitted by default; it is a long run).",
    )
    parser.add_argument("--chunk-size", type=int, default=80)
    parser.add_argument("--max-workers", type=int, default=3)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if args.universes.strip():
        presets = [p.strip().lower() for p in args.universes.split(",") if p.strip()]
    else:
        presets = _default_presets(include_sp1500=bool(args.include_sp1500) or args.mode == "full")

    if not ENV_OVERRIDES.exists():
        print(f"missing env overrides: {ENV_OVERRIDES}")
        return 3

    sys.path.insert(0, str(SKILL_DIR))
    from scripts.run_multi_era_backtest_schwab_only import _load_universe_tickers

    planned: list[dict[str, Any]] = []
    for preset in presets:
        try:
            tickers = _load_universe_tickers(preset)
        except ValueError as exc:
            print(f"skip {preset}: {exc}")
            continue
        planned.append(
            {
                "preset": preset,
                "ticker_count": len(tickers),
                "run_tag": _run_tag(preset, args.mode),
            }
        )

    if not planned:
        print("no runnable universes")
        return 3

    print(f"Legacy universe sweep mode={args.mode} universes={[p['preset'] for p in planned]}")
    for row in planned:
        print(f"  {row['preset']}: {row['ticker_count']} tickers -> {row['run_tag']}")
    if args.dry_run:
        return 0

    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    python = os.getenv("PYTHON") or sys.executable
    results: list[dict[str, Any]] = []
    for row in planned:
        run_tag = str(row["run_tag"])
        cmd = [
            python,
            "-u",
            str(MULTI_ERA),
            "--run-tag",
            run_tag,
            "--env-overrides",
            str(ENV_OVERRIDES),
            "--universe-preset",
            str(row["preset"]),
            "--chunk-size",
            str(max(1, int(args.chunk_size))),
            "--max-workers",
            str(max(1, int(args.max_workers))),
        ]
        if args.mode == "smoke":
            cmd.extend(["--only-eras", SMOKE_ERA])
        print(f"\n=== {row['preset']} ({row['ticker_count']} names) ===")
        proc = subprocess.run(cmd, cwd=str(SKILL_DIR))
        artifact = ARTIFACT_DIR / f"multi_era_backtest_schwab_only_{run_tag}.json"
        summary = _summarize_artifact(artifact)
        summary.update({"preset": row["preset"], "ticker_count": row["ticker_count"], "returncode": proc.returncode})
        results.append(summary)
        print(
            f"[{row['preset']}] rc={proc.returncode} trades={summary.get('total_trades')} "
            f"pf_mean={summary.get('pf_mean')} worst={summary.get('worst_era_pf')}"
        )

    out = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": args.mode,
        "config": "control_legacy_aug",
        "results": results,
    }
    out_path = ARTIFACT_DIR / f"legacy_universe_sweep_{args.mode}.json"
    out_path.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"\nSweep summary: {out_path}")
    failed = [r for r in results if int(r.get("returncode") or 0) != 0]
    return 0 if not failed else 2


if __name__ == "__main__":
    raise SystemExit(main())
