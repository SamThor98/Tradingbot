#!/usr/bin/env python3
"""Summarize PEAD dual-admit / canary sleeve sessions from validation artifacts.

Read-only. Does not enable PEAD live or change env.

Usage (from schwab_skill/):
  python scripts/summarize_pead_dual_admit_sessions.py
  python scripts/summarize_pead_dual_admit_sessions.py --limit 10
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

SKILL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL_DIR))

ART = SKILL_DIR / "validation_artifacts"


def _load(path: Path) -> dict[str, Any] | None:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return raw if isinstance(raw, dict) else None


def _session_from_compare(path: Path, payload: dict[str, Any]) -> dict[str, Any]:
    counters = payload.get("counters") or {}
    canary = payload.get("canary_sleeve") or {}
    inv = payload.get("invariant") or {}
    return {
        "kind": "compare",
        "path": path.name,
        "generated_at": payload.get("generated_at"),
        "verdict": payload.get("verdict"),
        "evaluated": counters.get("pead_primary_evaluated"),
        "admitted": counters.get("pead_primary_admitted"),
        "overlap": counters.get("overlap_with_stage2"),
        "capacity_top_n": counters.get("pead_primary_would_rank_capacity_top_n"),
        "capacity_arm": counters.get("pead_primary_capacity_rank_arm"),
        "dq": payload.get("data_quality"),
        "leak_ok": bool(inv.get("executable_still_stage2_only")),
        "canary_tickers": canary.get("tickers") or [],
        "rth_dq_ok": canary.get("rth_dq_ok"),
        "enablement_ready": canary.get("enablement_ready", False),
    }


def _session_from_scan(path: Path, payload: dict[str, Any]) -> dict[str, Any]:
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else payload
    names = summary.get("shadow_names_sample") or []
    if not names and isinstance(payload.get("diagnostics_pead"), dict):
        names = (payload.get("diagnostics_pead") or {}).get("pead_primary_shadow_names") or []
    canary_tickers = [
        str(n.get("ticker") or "").upper()
        for n in names
        if isinstance(n, dict) and n.get("capacity_top_n") and n.get("ticker")
    ]
    dq = summary.get("data_quality")
    leaked = payload.get("leaked_pead_only") or []
    return {
        "kind": "scan",
        "path": path.name,
        "generated_at": summary.get("at"),
        "label": summary.get("label"),
        "verdict": "leak" if leaked else "scan_ok",
        "evaluated": summary.get("pead_primary_evaluated"),
        "admitted": summary.get("pead_primary_admitted"),
        "overlap": summary.get("overlap_with_stage2"),
        "capacity_top_n": summary.get("pead_primary_would_rank_capacity_top_n"),
        "capacity_arm": summary.get("pead_primary_capacity_rank_arm"),
        "dq": dq,
        "leak_ok": not bool(leaked),
        "canary_tickers": canary_tickers,
        "rth_dq_ok": str(dq or "").lower() == "ok",
        "enablement_ready": False,
    }


def collect_sessions(*, limit: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in sorted(ART.glob("pead_primary_shadow_compare_*.json"), reverse=True):
        payload = _load(path)
        if payload:
            rows.append(_session_from_compare(path, payload))
    # Fill gaps with scans that may not have a compare yet.
    seen_stamps = {r["path"].replace("pead_primary_shadow_compare_", "")[:15] for r in rows}
    for path in sorted(ART.glob("pead_primary_shadow_scan_*.json"), reverse=True):
        stamp = path.name.replace("pead_primary_shadow_scan_", "")[:15]
        if stamp in seen_stamps:
            continue
        payload = _load(path)
        if payload:
            rows.append(_session_from_scan(path, payload))
    rows.sort(key=lambda r: str(r.get("generated_at") or r.get("path") or ""), reverse=True)
    return rows[: max(1, int(limit))]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=8)
    args = parser.parse_args()
    if not ART.is_dir():
        print(f"FAIL: missing {ART}")
        return 1
    sessions = collect_sessions(limit=int(args.limit))
    print(
        f"{'when':<28} {'kind':<8} {'verdict':<8} "
        f"{'eval':>5} {'adm':>4} {'ov':>3} {'cap':>3} {'dq':<6} "
        f"{'leak_ok':<7} canary"
    )
    dq_ok_n = 0
    pass_n = 0
    for s in sessions:
        when = str(s.get("generated_at") or "")[:28]
        dq = str(s.get("dq") or "")[:6]
        if s.get("rth_dq_ok"):
            dq_ok_n += 1
        if s.get("verdict") == "pass":
            pass_n += 1
        tickers = ",".join(s.get("canary_tickers") or []) or "-"
        print(
            f"{when:<28} {str(s.get('kind')):<8} {str(s.get('verdict')):<8} "
            f"{str(s.get('evaluated')):>5} {str(s.get('admitted')):>4} "
            f"{str(s.get('overlap')):>3} {str(s.get('capacity_top_n')):>3} {dq:<6} "
            f"{str(bool(s.get('leak_ok'))):<7} {tickers}"
        )
    print(
        f"sessions={len(sessions)} compare_pass~={pass_n} rth_dq_ok={dq_ok_n} "
        f"enablement_ready=False (see docs/PEAD_CANARY_SLEEVE_DESIGN.md)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
