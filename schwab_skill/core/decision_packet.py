"""Phase 4 decision-packet store: build, persist, load, and backfill outcomes.

Every approved/rejected/filtered decision is snapshotted into a
:class:`DecisionPacket` and appended to a rolling JSON store
(``decision_packets.json``), mirroring the shape/behavior of
``execution_safety_metrics.json``. Side-effect-safe: recording never breaks the
trade path.
"""

from __future__ import annotations

import json
import logging
import uuid
from pathlib import Path
from typing import Any

from core.contracts.decision_packet import DecisionPacket, PacketOutcome
from core.contracts.provenance import Provenance, utc_now

LOG = logging.getLogger(__name__)

_STORE_FILE = "decision_packets.json"
_MAX_PACKETS = 1000
SKILL_DIR = Path(__file__).resolve().parent.parent


def _store_path(skill_dir: Path | None) -> Path:
    return Path(skill_dir or SKILL_DIR) / _STORE_FILE


def _load_raw(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"packets": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("packets"), list):
            return data
    except Exception as exc:
        LOG.warning("Ignoring unreadable decision-packet store %s: %s", path, exc)
    return {"packets": []}


def _save_raw(path: Path, data: dict[str, Any]) -> None:
    try:
        from _io_utils import atomic_write_json

        atomic_write_json(path, data, indent=2)
    except Exception as exc:  # pragma: no cover
        LOG.warning("Could not persist decision packets to %s: %s", path, exc)


def _f(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def build_packet(
    *,
    ticker: str,
    kind: str = "approved",
    signal: dict[str, Any] | None = None,
    market: dict[str, Any] | None = None,
    execution: dict[str, Any] | None = None,
    policy_id: str | None = None,
) -> DecisionPacket:
    """Assemble a DecisionPacket from the decision-time context objects."""
    sig = signal or {}
    mkt = market or {}
    execu = execution or {}
    advisory = sig.get("advisory") if isinstance(sig.get("advisory"), dict) else {}
    attribution = sig.get("strategy_attribution") if isinstance(sig.get("strategy_attribution"), dict) else {}
    eq = execu.get("quality") if isinstance(execu.get("quality"), dict) else {}

    mgmt_snapshot: dict[str, Any] | None = None
    mi = sig.get("management_integrity")
    if isinstance(mi, dict) and mi:
        mgmt_snapshot = {
            "score": mi.get("score"),
            "score_bucket": mi.get("score_bucket"),
            "profile": mi.get("profile"),
            "red_flag_count": mi.get("red_flag_count"),
            "source": mi.get("source"),
        }

    return DecisionPacket(
        packet_id=uuid.uuid4().hex[:12],
        created_at=utc_now(),
        ticker=str(ticker).upper(),
        kind=kind if kind in {"approved", "rejected", "staged", "filtered"} else "approved",
        regime_state=mkt.get("regime_state"),
        regime_score=_f(mkt.get("regime_score")),
        volatility_state=mkt.get("volatility_state"),
        setup_type=attribution.get("top_live") or sig.get("setup_type"),
        gate_disposition=sig.get("_filter_status"),
        policy_id=policy_id or (execu.get("intent") or {}).get("policy_id"),
        rank_score=_f(sig.get("rank_score")),
        edge_score=_f(sig.get("edge_score")),
        p_up_calibrated=_f(sig.get("p_up_calibrated") or (advisory or {}).get("p_up_10d")),
        expected_slippage_bps=_f(eq.get("realized_slippage_bps") or eq.get("expected_slippage_bps")),
        entry_price=_f(sig.get("price")),
        management_integrity=mgmt_snapshot,
        outcome=PacketOutcome(),
        refs={"order_ref": execu.get("order_ref"), "state": execu.get("state")},
        provenance=Provenance(source="computed", as_of=utc_now(), confidence="high"),
    )


def record_packet(skill_dir: Path | None, packet: DecisionPacket) -> bool:
    """Append a packet to the rolling store. Returns True on success."""
    try:
        path = _store_path(skill_dir)
        data = _load_raw(path)
        packets = data.setdefault("packets", [])
        packets.append(packet.model_dump(mode="json"))
        if len(packets) > _MAX_PACKETS:
            del packets[: len(packets) - _MAX_PACKETS]
        _save_raw(path, data)
        return True
    except Exception as exc:  # pragma: no cover
        LOG.debug("record_packet skipped: %s", exc)
        return False


def load_packets(skill_dir: Path | None = None, *, limit: int | None = None) -> list[dict[str, Any]]:
    data = _load_raw(_store_path(skill_dir))
    packets = [p for p in data.get("packets", []) if isinstance(p, dict)]
    if limit is not None:
        return packets[-max(0, int(limit)) :]
    return packets


def overwrite_packets(skill_dir: Path | None, packets: list[dict[str, Any]]) -> bool:
    """Replace the full packet list (used by outcome backfill after mutation)."""
    try:
        _save_raw(_store_path(skill_dir), {"packets": list(packets)})
        return True
    except Exception as exc:  # pragma: no cover
        LOG.debug("overwrite_packets skipped: %s", exc)
        return False


def backfill_outcome(
    skill_dir: Path | None,
    packet_id: str,
    *,
    label: str,
    realized_return_pct: float | None = None,
    horizon_days: int | None = None,
    realized_slippage_bps: float | None = None,
) -> bool:
    """Backfill a resolved outcome onto a stored packet."""
    try:
        path = _store_path(skill_dir)
        data = _load_raw(path)
        changed = False
        for p in data.get("packets", []):
            if isinstance(p, dict) and p.get("packet_id") == packet_id:
                p["outcome"] = {
                    "label": label,
                    "realized_return_pct": realized_return_pct,
                    "horizon_days": horizon_days,
                    "realized_slippage_bps": realized_slippage_bps,
                    "resolved_at": utc_now().isoformat(),
                }
                changed = True
                break
        if changed:
            _save_raw(path, data)
        return changed
    except Exception as exc:  # pragma: no cover
        LOG.debug("backfill_outcome skipped: %s", exc)
        return False
