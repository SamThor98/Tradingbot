"""The test suite cannot write the operator decision-packet store."""

from __future__ import annotations

from core.decision_packet import SKILL_DIR, build_packet, record_packet


def test_record_packet_without_a_skill_dir_does_not_touch_the_real_store() -> None:
    path = SKILL_DIR / "decision_packets.json"
    before = path.read_bytes() if path.exists() else None
    packet = build_packet(
        ticker="AAPL",
        signal={"price": 185.50},
        execution={"order_ref": "ORD123"},
    )
    assert record_packet(None, packet) is False
    after = path.read_bytes() if path.exists() else None
    assert after == before
