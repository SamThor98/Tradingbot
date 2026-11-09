"""Fixture-packet quarantine is dry-run by default and idempotent."""

from __future__ import annotations

import json
from pathlib import Path

from scripts.quarantine_fixture_packets import quarantine


def _store(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "packets": [
                    {
                        "packet_id": "fixture",
                        "ticker": "AAPL",
                        "entry_price": 185.5,
                        "refs": {"order_ref": "ORD123"},
                    },
                    {
                        "packet_id": "real",
                        "ticker": "MSFT",
                        "entry_price": 420.1,
                        "refs": {"order_ref": "999888"},
                    },
                    {
                        "packet_id": "same-price",
                        "ticker": "AAPL",
                        "entry_price": 185.5,
                        "refs": {"order_ref": "LIVE-1"},
                    },
                ]
            }
        )
        + "\n",
        encoding="utf-8",
    )


def test_dry_run_changes_nothing_and_apply_is_idempotent(tmp_path: Path) -> None:
    store = tmp_path / "decision_packets.json"
    _store(store)
    before = store.read_bytes()
    plan = quarantine(store, apply=False)
    assert plan["dry_run"] is True
    assert plan["matched"] == 1
    assert plan["to_quarantine"] == 1
    assert plan["applied"] is False
    assert store.read_bytes() == before
    assert not (tmp_path / "decision_packets.fixture_quarantine.json").exists()

    applied = quarantine(store, apply=True)
    assert applied["applied"] is True
    assert applied["backup"]
    backup = Path(str(applied["backup"]))
    assert backup.exists()
    assert backup.read_bytes() == before
    kept = json.loads(store.read_text(encoding="utf-8"))["packets"]
    assert [row["packet_id"] for row in kept] == ["real", "same-price"]
    quarantined = json.loads((tmp_path / "decision_packets.fixture_quarantine.json").read_text(encoding="utf-8"))
    assert [row["packet_id"] for row in quarantined["packets"]] == ["fixture"]

    again = quarantine(store, apply=True)
    assert again["matched"] == 0
    assert again["applied"] is False
    assert list(tmp_path.glob("*.bak-*")) == [backup]
    again_rows = json.loads((tmp_path / "decision_packets.fixture_quarantine.json").read_text(encoding="utf-8"))
    assert [row["packet_id"] for row in again_rows["packets"]] == ["fixture"]
