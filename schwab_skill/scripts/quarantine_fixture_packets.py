#!/usr/bin/env python3
"""Quarantine approve-flow fixture packets from the decision-packet store.

The scan-to-execute test used a fake AAPL fill at 185.50 with order id
ORD123. Those rows are not trades. This script moves matching packets out of
``decision_packets.json`` into a sidecar file. It does not place or size orders.

Dry-run is the default. ``--apply`` writes a timestamped backup, then removes
the fixture rows. Running it again is a no-op once those rows are gone.

From ``schwab_skill/`` on the operator machine::

    python scripts/quarantine_fixture_packets.py
    python scripts/quarantine_fixture_packets.py --apply
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SKILL_DIR = Path(__file__).resolve().parent.parent
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

FIXTURE_TICKER = "AAPL"
FIXTURE_ENTRY = 185.5
FIXTURE_ORDER_REF = "ORD123"


def is_fixture_packet(packet: dict[str, Any]) -> bool:
    """True for the approve-test signature: AAPL, entry 185.5, order ORD123."""
    if str(packet.get("ticker") or "").upper() != FIXTURE_TICKER:
        return False
    refs = packet.get("refs") if isinstance(packet.get("refs"), dict) else {}
    order = refs.get("order_ref")
    if order is None:
        order = packet.get("order_ref")
    if str(order or "") != FIXTURE_ORDER_REF:
        return False
    try:
        entry = float(packet.get("entry_price"))
    except (TypeError, ValueError):
        return False
    return abs(entry - FIXTURE_ENTRY) < 1e-6


def _signature(packet: dict[str, Any]) -> str:
    packet_id = str(packet.get("packet_id") or "").strip()
    if packet_id:
        return "id:" + packet_id
    return json.dumps(packet, sort_keys=True, default=str)


def _load(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"packets": []}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("packets"), list):
        raise ValueError(f"{path} is not a decision-packet store")
    return data


def _quarantine_path(store: Path) -> Path:
    return store.with_suffix(".fixture_quarantine.json")


def quarantine(store: Path, *, apply: bool) -> dict[str, Any]:
    """Move fixture packets aside. Dry-run reports the rows and writes nothing."""
    data = _load(store)
    packets = [row for row in data.get("packets", []) if isinstance(row, dict)]
    matched = [row for row in packets if is_fixture_packet(row)]
    qpath = _quarantine_path(store)
    existing = _load(qpath) if qpath.exists() else {"packets": []}
    kept_quarantine = [row for row in existing.get("packets", []) if isinstance(row, dict)]
    seen = {_signature(row) for row in kept_quarantine}
    fresh = [row for row in matched if _signature(row) not in seen]
    report: dict[str, Any] = {
        "ok": True,
        "dry_run": not apply,
        "store": str(store),
        "quarantine": str(qpath),
        "fixture": {
            "ticker": FIXTURE_TICKER,
            "entry_price": FIXTURE_ENTRY,
            "order_ref": FIXTURE_ORDER_REF,
        },
        "matched": len(matched),
        "already_quarantined": len(matched) - len(fresh),
        "to_quarantine": len(fresh),
        "remaining_packets": len(packets) - len(matched),
        "backup": None,
        "applied": False,
    }
    if not apply or not matched:
        return report
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = store.with_name(f"{store.name}.bak-{stamp}")
    shutil.copy2(store, backup)
    data["packets"] = [row for row in packets if not is_fixture_packet(row)]
    store.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    existing["packets"] = kept_quarantine + fresh
    existing["updated_at"] = datetime.now(timezone.utc).isoformat()
    existing["reason"] = "approve-flow fixture: AAPL entry 185.5 order_ref ORD123"
    qpath.write_text(json.dumps(existing, indent=2) + "\n", encoding="utf-8")
    report["backup"] = str(backup)
    report["applied"] = True
    report["remaining_packets"] = len(data["packets"])
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--store",
        type=Path,
        default=SKILL_DIR / "decision_packets.json",
        help="Path to decision_packets.json. Default: the skill directory store.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Quarantine the fixture rows. Without this flag the command only prints the plan.",
    )
    args = parser.parse_args(argv)
    try:
        report = quarantine(args.store, apply=bool(args.apply))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
