#!/usr/bin/env python3
"""Catch up decision packets and label matured outcomes.

The local packet file stops growing when scans run without this pass, and
pending rows stay pending until a backfill actually writes them. This command:

1. Creates missing staged packets from ``webapp/webapp.db`` ``scan_results``
   rows on or after ``--since`` (one packet per ticker per UTC day).
2. Labels every still-pending packet whose horizon has matured, including
   packets that were already on disk before ``--since``.

Scan rows older than ``WEB_LOCAL_SCAN_RESULT_RETENTION_DAYS`` (default 30)
are already gone from SQLite, so this cannot rebuild those names. It still
labels whatever pending packets remain in ``decision_packets.json``.

From ``schwab_skill/``:

    python scripts/backfill_decision_outcomes.py --since 2026-09-03

That is the one-time command for the book that froze on 2026-09-03.
Add ``--dry-run`` to print counts without writing. Horizon defaults to
``DECISION_PACKET_HORIZON_DAYS`` (10 trading days).
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))


def _parse_since(raw: str) -> datetime:
    text = raw.strip()
    if "T" in text:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    else:
        dt = datetime.fromisoformat(text + "T00:00:00")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--since",
        default="2026-09-03",
        help="Inclusive UTC date (or ISO timestamp) for scan_results import. Default: 2026-09-03.",
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=SKILL_DIR / "webapp" / "webapp.db",
        help="SQLite file that holds scan_results (local dashboard default).",
    )
    parser.add_argument(
        "--user-id",
        default="local",
        help="scan_results.user_id to import. Default: local (WEB_LOCAL_USER_ID).",
    )
    parser.add_argument(
        "--skill-dir",
        type=Path,
        default=SKILL_DIR,
        help="Directory that contains decision_packets.json.",
    )
    parser.add_argument(
        "--horizon-days",
        type=int,
        default=None,
        help="Trading-day outcome horizon. Default: DECISION_PACKET_HORIZON_DAYS or 10.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Count missing packets and pending rows without writing the store.",
    )
    args = parser.parse_args(argv)

    from core.decision_packet import load_local_scan_signals, load_packets, record_historical_scan_packets
    from core.outcome_backfill import run_local_backfill

    since = _parse_since(args.since)
    if not args.db.exists():
        print(f"No scan database at {args.db}; labeling packets already on disk.")
        rows: list[dict] = []
    else:
        rows = load_local_scan_signals(args.db, since=since, user_id=args.user_id)
        print(f"scan_results since {since.date().isoformat()} for user {args.user_id!r}: {len(rows)}")

    planned = record_historical_scan_packets(args.skill_dir, rows, dry_run=True)
    print(
        "staged packets: would_record=%s skipped_duplicate=%s skipped_filtered=%s"
        % (
            planned.get("recorded"),
            planned.get("skipped_duplicate"),
            planned.get("skipped_filtered"),
        )
    )
    if args.dry_run:
        pending = sum(
            1
            for packet in load_packets(args.skill_dir)
            if str((packet.get("outcome") or {}).get("label") or "pending") in ("pending", "unknown")
        )
        print(f"dry-run: not writing. pending packets currently on disk: {pending}")
        return 0

    written = record_historical_scan_packets(args.skill_dir, rows, dry_run=False)
    print(
        "wrote staged packets: recorded=%s skipped_duplicate=%s"
        % (written.get("recorded"), written.get("skipped_duplicate"))
    )
    backfill = run_local_backfill(
        args.skill_dir,
        horizon_days=args.horizon_days,
        force=True,
    )
    print(
        "outcome backfill: resolved=%s pending=%s total=%s horizon_days=%s history_days=%s"
        % (
            backfill.get("resolved"),
            backfill.get("pending"),
            backfill.get("total"),
            backfill.get("horizon_days"),
            backfill.get("history_days"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
