"""Watch pead_primary_aug_fixed_full_c40 progress; exit when done or stalled.

Usage:
  python scripts/watch_pead_full_c40.py
  python scripts/watch_pead_full_c40.py --stall-minutes 45 --poll-seconds 60
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
RUN_ID = "pead_primary_aug_fixed_full_c40"
PROGRESS = (
    SKILL_DIR
    / "validation_artifacts"
    / f"multi_era_backtest_schwab_only_{RUN_ID}_progress.json"
)
SUMMARY = SKILL_DIR / "validation_artifacts" / f"multi_era_backtest_schwab_only_{RUN_ID}.json"
CHUNKS = SKILL_DIR / "validation_artifacts" / "multi_era_chunks" / RUN_ID
LOG = SKILL_DIR / "validation_artifacts" / f"{RUN_ID}_watch.log"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _log(msg: str) -> None:
    line = f"{_now()} {msg}"
    print(line, flush=True)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def _load_progress() -> dict:
    if not PROGRESS.exists():
        return {}
    try:
        return json.loads(PROGRESS.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _disk_chunk_count(era: str) -> tuple[int, str | None, float | None]:
    era_dir = CHUNKS / era
    if not era_dir.is_dir():
        return 0, None, None
    chunks = sorted(p for p in era_dir.glob("chunk_*.json") if "_tickers" not in p.name)
    if not chunks:
        return 0, None, None
    latest = chunks[-1]
    return len(chunks), latest.name, time.time() - latest.stat().st_mtime


def _summarize_final() -> str:
    if not SUMMARY.exists():
        return "summary file missing"
    data = json.loads(SUMMARY.read_text(encoding="utf-8"))
    eras = data.get("eras") or data.get("era_results") or []
    if isinstance(eras, dict):
        rows = list(eras.values())
    else:
        rows = list(eras)
    pfs: list[float] = []
    lines = ["five-era summary:"]
    for row in rows:
        if not isinstance(row, dict):
            continue
        name = row.get("era") or row.get("name") or "?"
        pf = row.get("profit_factor_net")
        trades = row.get("total_trades")
        lines.append(f"  {name}: trades={trades} pf={pf}")
        try:
            if pf not in (None, "inf"):
                pfs.append(float(pf))
        except (TypeError, ValueError):
            pass
    if pfs:
        lines.append(f"  PF mean={sum(pfs)/len(pfs):.3f} worst={min(pfs):.3f} n_eras={len(pfs)}")
    top = data.get("profit_factor_mean_net") or data.get("pf_mean_net")
    worst = data.get("worst_era_profit_factor_net") or data.get("worst_era_pf_net")
    if top is not None or worst is not None:
        lines.append(f"  artifact pf_mean={top} worst={worst}")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--poll-seconds", type=int, default=90)
    ap.add_argument("--stall-minutes", type=float, default=45.0)
    args = ap.parse_args()

    last_key: tuple[str, int] | None = None
    last_advance = time.time()
    _log(f"watch start run_id={RUN_ID} stall_min={args.stall_minutes}")

    while True:
        prog = _load_progress()
        status = str(prog.get("status") or "")
        era = str(prog.get("current_era") or "")
        completed = int(prog.get("completed_count") or 0)
        total = int(prog.get("total_eras") or 5)
        era_state = prog.get("era_state") or {}
        done_chunks = 0
        total_chunks = 0
        if isinstance(era_state.get(era), dict):
            done_chunks = int(era_state[era].get("completed_chunks") or 0)
            total_chunks = int(era_state[era].get("total_chunks") or 0)
        disk_n, latest, age = _disk_chunk_count(era) if era else (0, None, None)

        key = (era, done_chunks)
        if last_key is None:
            last_key = key
            last_advance = time.time()
        elif key != last_key:
            _log(f"ADVANCE era={era} chunks={done_chunks}/{total_chunks} eras={completed}/{total}")
            last_key = key
            last_advance = time.time()

        stall_min = (time.time() - last_advance) / 60.0
        _log(
            f"status={status} era={era} chunks={done_chunks}/{total_chunks} "
            f"disk={disk_n} latest={latest} latest_age_s={None if age is None else round(age)} "
            f"eras={completed}/{total} stall_min={stall_min:.1f}"
        )

        if status == "completed" or SUMMARY.exists() and status != "running":
            if SUMMARY.exists():
                _log(_summarize_final())
            _log("DONE")
            return 0

        if status in {"failed", "error"}:
            _log(f"FAILED status={status} prog={prog}")
            return 1

        if stall_min >= args.stall_minutes:
            _log(
                f"STALL: no chunk/era advance for {stall_min:.1f} min "
                f"(threshold={args.stall_minutes}). Resume with: "
                f"python scripts/run_multi_era_backtest_schwab_only.py "
                f"--run-tag {RUN_ID} "
                f"--env-overrides research/env_overrides/pead_primary_aug.json "
                f"--chunk-size 40 --max-workers 1 --timeout-seconds 7200 --warm-earnings-cache"
            )
            return 2

        time.sleep(max(15, int(args.poll_seconds)))


if __name__ == "__main__":
    sys.exit(main())
