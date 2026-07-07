#!/usr/bin/env python3
"""Delete poisoned multi-era chunks and unmark affected eras for resume.

Poisoned chunks (Schwab auth failure signature): trades=0, excluded_count=0,
file size < 1KB. The multi-era runner skips existing chunk files and may mark
eras complete even when every chunk is empty.

Usage (from schwab_skill/):
  python scripts/repair_poisoned_multi_era_chunks.py --run-id control_legacy_aug --dry-run
  python scripts/repair_poisoned_multi_era_chunks.py --run-id control_legacy_aug
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SKILL_DIR = Path(__file__).resolve().parent.parent
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

ARTIFACT_DIR = SKILL_DIR / "validation_artifacts"
ERAS = ("recent_current", "bear_rates", "crash_recovery", "volatility_chop", "late_bull")


def _is_zero_trade_chunk(payload: dict[str, Any]) -> bool:
    return len(payload.get("trades") or []) == 0


def _is_poisoned(payload: dict[str, Any], size: int) -> bool:
    trades = payload.get("trades") or []
    excluded = int(payload.get("excluded_count", 0) or 0)
    return len(trades) == 0 and excluded == 0 and size < 1024


def _collect_bad_chunks(run_id: str, eras: tuple[str, ...] | None = None) -> list[Path]:
    base = ARTIFACT_DIR / "multi_era_chunks" / run_id
    if not base.exists():
        return []
    era_filter = set(eras) if eras else None
    bad: list[Path] = []
    for chunk_path in sorted(base.glob("**/chunk_[0-9]*.json")):
        if chunk_path.name.endswith("_tickers.json"):
            continue
        if era_filter is not None:
            era_name = chunk_path.parent.name
            if era_name not in era_filter:
                continue
        try:
            payload = json.loads(chunk_path.read_text(encoding="utf-8"))
            size = int(chunk_path.stat().st_size)
        except Exception:
            continue
        if _is_poisoned(payload, size) or _is_zero_trade_chunk(payload):
            bad.append(chunk_path)
    return bad


def _era_trade_count(run_id: str, era: str) -> int:
    era_dir = ARTIFACT_DIR / "multi_era_chunks" / run_id / era
    if not era_dir.exists():
        return 0
    total = 0
    for chunk_path in era_dir.glob("chunk_[0-9]*.json"):
        if chunk_path.name.endswith("_tickers.json"):
            continue
        try:
            payload = json.loads(chunk_path.read_text(encoding="utf-8"))
            total += len(payload.get("trades") or [])
        except Exception:
            continue
    return total


def _patch_progress(run_id: str, eras_to_rerun: list[str], *, dry_run: bool) -> dict[str, Any]:
    progress_path = ARTIFACT_DIR / f"multi_era_backtest_schwab_only_{run_id}_progress.json"
    if not progress_path.exists():
        return {"progress_path": str(progress_path), "patched": False, "reason": "missing"}

    data = json.loads(progress_path.read_text(encoding="utf-8"))
    completed = list(data.get("completed") or [])
    before = [str(r.get("era")) for r in completed]
    rerun_set = set(eras_to_rerun)
    completed = [r for r in completed if str(r.get("era")) not in rerun_set]
    data["completed"] = completed
    data["status"] = "running"
    data["generated_at"] = datetime.now(timezone.utc).isoformat()
    data["repair_note"] = f"unmarked eras for rerun: {sorted(rerun_set)}"

    if not dry_run:
        progress_path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    return {
        "progress_path": str(progress_path),
        "patched": True,
        "eras_before": before,
        "eras_after": [str(r.get("era")) for r in completed],
        "eras_unmarked": sorted(rerun_set),
    }


def _patch_aggregate(run_id: str, eras_to_rerun: list[str], *, dry_run: bool) -> dict[str, Any]:
    agg_path = ARTIFACT_DIR / f"multi_era_backtest_schwab_only_{run_id}.json"
    if not agg_path.exists():
        return {"aggregate_path": str(agg_path), "patched": False, "reason": "missing"}

    data = json.loads(agg_path.read_text(encoding="utf-8"))
    rerun_set = set(eras_to_rerun)
    results = [r for r in list(data.get("results") or []) if str(r.get("era")) not in rerun_set]
    data["results"] = results
    data["generated_at"] = datetime.now(timezone.utc).isoformat()
    data["repair_note"] = f"removed eras pending rerun: {sorted(rerun_set)}"

    if not dry_run:
        agg_path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    return {
        "aggregate_path": str(agg_path),
        "patched": True,
        "eras_removed": sorted(rerun_set),
        "eras_remaining": [str(r.get("era")) for r in results],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True, help="multi_era_chunks run tag (e.g. control_legacy_aug)")
    parser.add_argument(
        "--eras",
        default="",
        help="Comma-separated era names to repair (default: auto-detect zero-trade eras)",
    )
    parser.add_argument("--dry-run", action="store_true", help="Report actions without writing/deleting")
    args = parser.parse_args()

    run_id = str(args.run_id).strip()
    if args.eras.strip():
        target_eras = tuple(e.strip() for e in args.eras.split(",") if e.strip())
    else:
        target_eras = ERAS

    bad_chunks = _collect_bad_chunks(run_id, target_eras)
    zero_trade_eras = [era for era in target_eras if _era_trade_count(run_id, era) == 0]
    eras_to_rerun = sorted(set(zero_trade_eras))

    print(f"run_id={run_id}")
    print(f"zero_trade_chunks={len(bad_chunks)} zero_trade_eras={eras_to_rerun}")

    if args.dry_run:
        for path in bad_chunks[:10]:
            print(f"  would delete: {path.relative_to(SKILL_DIR)}")
        if len(bad_chunks) > 10:
            print(f"  ... and {len(bad_chunks) - 10} more")
        if eras_to_rerun:
            print(f"  would unmark eras: {eras_to_rerun}")
        return 0

    deleted = 0
    for path in bad_chunks:
        try:
            path.unlink(missing_ok=True)
            deleted += 1
        except Exception as exc:
            print(f"WARN: failed to delete {path}: {exc}")

    progress_info = _patch_progress(run_id, eras_to_rerun, dry_run=False) if eras_to_rerun else {}
    aggregate_info = _patch_aggregate(run_id, eras_to_rerun, dry_run=False) if eras_to_rerun else {}

    out = {
        "run_id": run_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "deleted_zero_trade_chunks": deleted,
        "eras_to_rerun": eras_to_rerun,
        "progress": progress_info,
        "aggregate": aggregate_info,
    }
    out_path = ARTIFACT_DIR / f"repair_poisoned_{run_id}.json"
    out_path.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"Deleted {deleted} zero-trade chunks")
    if eras_to_rerun:
        print(f"Unmarked eras for resume: {eras_to_rerun}")
    print(f"Wrote {out_path}")
    print(
        "Next: python scripts/run_multi_era_backtest_schwab_only.py "
        f"--run-tag {run_id} --env-overrides validation_artifacts/phase1_env_overrides/{run_id}.json"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
