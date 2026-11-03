#!/usr/bin/env python3
"""Backfill `.counterfactual_log.jsonl` from persisted `last_scan`.

The 2026-09-04 live scan computed meta-policy shadow actions but wrote
zero counterfactual rows (`diagnostics.counterfactual_logged=0`). This
replays those already-computed decisions into the learning log so
`score_counterfactual_outcomes.py` can label them after T+N.

Does not place orders or change scan behavior.

Usage (from schwab_skill):
  python scripts/backfill_counterfactual_from_last_scan.py --dry-run
  python scripts/backfill_counterfactual_from_last_scan.py
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SKILL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL_DIR))

from agent_intelligence import (  # noqa: E402
    COUNTERFACTUAL_LOG_FILE,
    meta_policy_counterfactual_reason,
)
from config import get_counterfactual_max_horizon_days  # noqa: E402

DB_PATH = SKILL_DIR / "webapp" / "webapp.db"
LOG_PATH = SKILL_DIR / COUNTERFACTUAL_LOG_FILE


def _parse_json(raw: Any) -> dict[str, Any]:
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    if isinstance(raw, str):
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return payload if isinstance(payload, dict) else {}
    return {}


def load_last_scan(db_path: Path) -> dict[str, Any]:
    if not db_path.exists():
        return {}
    con = sqlite3.connect(db_path)
    row = con.execute(
        "SELECT value_json FROM app_state WHERE key = ?",
        ("last_scan",),
    ).fetchone()
    return _parse_json(row[0] if row else None)


def _scan_timestamp(last_scan: dict[str, Any]) -> str:
    for key in ("at", "finished_at", "ts", "time"):
        raw = last_scan.get(key)
        if isinstance(raw, str) and raw.strip():
            return raw.strip()
    return datetime.now(timezone.utc).isoformat()


def _existing_keys(path: Path) -> set[tuple[str, str, str]]:
    keys: set[tuple[str, str, str]] = set()
    if not path.exists():
        return keys
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        keys.add(
            (
                str(row.get("ts") or ""),
                str(row.get("ticker") or "").upper(),
                str(row.get("reason") or ""),
            )
        )
    return keys


def events_from_last_scan(
    last_scan: dict[str, Any],
    *,
    horizon_days: int,
) -> list[dict[str, Any]]:
    ts = _scan_timestamp(last_scan)
    signals = [s for s in (last_scan.get("signals") or []) if isinstance(s, dict)]
    shortlist = [s for s in (last_scan.get("shortlist_signals") or []) if isinstance(s, dict)]
    fired_tickers = {str(s.get("ticker") or "").upper() for s in signals}
    events: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    def _append(signal: dict[str, Any], reason: str) -> None:
        ticker = str(signal.get("ticker") or "").upper()
        if not ticker or not reason:
            return
        key = (ticker, reason)
        if key in seen:
            return
        seen.add(key)
        events.append(
            {
                "ts": ts,
                "ticker": ticker,
                "reason": reason,
                "horizon_days": int(horizon_days),
                "signal_score": signal.get("signal_score"),
                "mirofish_conviction": signal.get("mirofish_conviction"),
                "meta_policy": signal.get("meta_policy"),
                "prediction_market": signal.get("prediction_market"),
                "advisory": signal.get("advisory"),
                "source": "last_scan_backfill",
            }
        )

    for signal in [*shortlist, *signals]:
        reason = meta_policy_counterfactual_reason(
            keep=True,
            meta=signal.get("meta_policy") if isinstance(signal.get("meta_policy"), dict) else None,
        )
        if reason:
            _append(signal, reason)

    for signal in signals:
        if signal.get("_demo"):
            continue
        ticker = str(signal.get("ticker") or "").upper()
        if ticker and ticker in fired_tickers:
            _append(signal, "fired")

    return events


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--db-path", default=str(DB_PATH))
    parser.add_argument("--log-path", default=str(LOG_PATH))
    args = parser.parse_args()

    last_scan = load_last_scan(Path(args.db_path))
    if not last_scan:
        print("FAIL: no last_scan in app_state")
        return 1

    events = events_from_last_scan(
        last_scan,
        horizon_days=get_counterfactual_max_horizon_days(SKILL_DIR),
    )
    existing = _existing_keys(Path(args.log_path))
    new_events = [
        e
        for e in events
        if (str(e.get("ts") or ""), str(e.get("ticker") or ""), str(e.get("reason") or ""))
        not in existing
    ]
    print(
        f"last_scan ts={_scan_timestamp(last_scan)} "
        f"signals={len(last_scan.get('signals') or [])} "
        f"candidates={len(events)} new={len(new_events)}"
    )
    reasons: dict[str, int] = {}
    for event in new_events:
        reasons[str(event["reason"])] = reasons.get(str(event["reason"]), 0) + 1
    print(f"new_reason_counts={reasons}")

    if args.dry_run:
        print("dry-run: no write")
        return 0

    if not new_events:
        print("No new counterfactual rows to write.")
        return 0

    path = Path(args.log_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for event in new_events:
            handle.write(json.dumps(event, default=str) + "\n")
    print(f"Wrote {len(new_events)} rows to {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
