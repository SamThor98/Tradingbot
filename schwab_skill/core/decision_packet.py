"""Phase 4 decision-packet store: build, persist, load, and backfill outcomes.

Every approved/rejected/filtered decision is snapshotted into a
:class:`DecisionPacket` and appended to a rolling JSON store
(``decision_packets.json``), mirroring the shape/behavior of
``execution_safety_metrics.json``. Kept scan signals are recorded as
``kind="staged"`` (one per ticker per UTC day) from the scan paths. Side-effect-safe:
recording never breaks the trade path.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.contracts.decision_packet import DecisionPacket, PacketOutcome
from core.contracts.provenance import Provenance, utc_now

LOG = logging.getLogger(__name__)

_STORE_FILE = "decision_packets.json"
# Rolling cap. Scan survivors are one packet per ticker per UTC day, so the
# store has to hold several months of graded decisions plus the older
# approved-trade book without evicting it on the first catch-up.
_MAX_PACKETS = 8000
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


def _as_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _utc_day(raw: Any) -> str | None:
    if isinstance(raw, datetime):
        return _as_utc(raw).date().isoformat()
    if not isinstance(raw, str) or not raw.strip():
        return None
    text = raw.strip().replace("Z", "+00:00")
    try:
        return _as_utc(datetime.fromisoformat(text)).date().isoformat()
    except ValueError:
        return None


def build_packet(
    *,
    ticker: str,
    kind: str = "approved",
    signal: dict[str, Any] | None = None,
    market: dict[str, Any] | None = None,
    execution: dict[str, Any] | None = None,
    policy_id: str | None = None,
    created_at: datetime | None = None,
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

    as_of = _as_utc(created_at) if created_at is not None else utc_now()
    return DecisionPacket(
        packet_id=uuid.uuid4().hex[:12],
        created_at=as_of,
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
        refs={
            "order_ref": execu.get("order_ref"),
            "state": execu.get("state"),
            "stop_pct": _f(sig.get("stop_pct")),
            "stop_price": _f(sig.get("stop_price") or sig.get("stop")),
            "verdict": sig.get("verdict"),
            "rationale": sig.get("rationale"),
            "prompt_version": sig.get("prompt_version"),
            "prompt_key": sig.get("prompt_key"),
            "stated_confidence": _f(sig.get("stated_confidence")),
        },
        provenance=Provenance(source="computed", as_of=as_of, confidence="high"),
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
    """Replace the packet list, preserving sibling store keys (backfill stamp)."""
    try:
        path = _store_path(skill_dir)
        data = _load_raw(path)
        data["packets"] = list(packets)
        _save_raw(path, data)
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


def _prepare_scan_signal(signal: dict[str, Any]) -> dict[str, Any]:
    """Copy a scan row so packet fields land even when the scanner used other keys."""
    sig = dict(signal)
    if sig.get("rank_score") is None:
        for key in ("composite_score", "signal_score", "score"):
            if sig.get(key) is not None:
                sig["rank_score"] = sig.get(key)
                break
    if sig.get("price") is None:
        for key in ("close", "last_price", "entry_price"):
            if sig.get(key) is not None:
                sig["price"] = sig.get(key)
                break
    ticker = str(sig.get("ticker") or sig.get("symbol") or "").upper()
    if ticker:
        sig["ticker"] = ticker
    return sig


def _market_from_diagnostics(diagnostics: dict[str, Any] | None) -> dict[str, Any]:
    try:
        from core.cockpit_service import build_market

        market = build_market(diagnostics if isinstance(diagnostics, dict) else {})
        return market if isinstance(market, dict) else {}
    except Exception as exc:
        LOG.debug("scan packet market context skipped: %s", exc)
        return {}


def _kept_scan_signal(signal: dict[str, Any]) -> bool:
    status = str(signal.get("_filter_status") or "kept").strip().lower()
    return status in ("", "kept")


def _record_staged_batch(
    skill_dir: Path | None,
    batch: list[tuple[dict[str, Any], datetime, dict[str, Any] | None]],
    *,
    dry_run: bool = False,
) -> dict[str, int]:
    """Append staged scan packets, one per ticker per UTC day."""
    empty = {"recorded": 0, "skipped_duplicate": 0, "skipped_filtered": 0}
    try:
        from config import get_decision_packet_scan_recording_enabled

        if not get_decision_packet_scan_recording_enabled(skill_dir):
            return {**empty, "skipped_disabled": 1}
    except Exception as exc:
        LOG.debug("scan packet recording flag unread: %s", exc)

    path = _store_path(skill_dir)
    data = _load_raw(path)
    rows = data.setdefault("packets", [])
    seen: set[tuple[str, str, str]] = set()
    for raw in rows:
        if not isinstance(raw, dict):
            continue
        seen.add(
            (
                str(raw.get("ticker") or "").upper(),
                _utc_day(raw.get("created_at")) or "",
                str(raw.get("kind") or ""),
            )
        )

    market_cache: dict[int, dict[str, Any]] = {}
    recorded = 0
    skipped_duplicate = 0
    skipped_filtered = 0
    for signal, created_at, diagnostics in batch:
        if not isinstance(signal, dict):
            continue
        if not _kept_scan_signal(signal):
            skipped_filtered += 1
            continue
        prepared = _prepare_scan_signal(signal)
        ticker = str(prepared.get("ticker") or "")
        day = _utc_day(created_at)
        if not ticker or not day:
            continue
        key = (ticker, day, "staged")
        if key in seen:
            skipped_duplicate += 1
            continue
        cache_key = id(diagnostics) if isinstance(diagnostics, dict) else 0
        if cache_key not in market_cache:
            market_cache[cache_key] = _market_from_diagnostics(diagnostics)
        packet = build_packet(
            ticker=ticker,
            kind="staged",
            signal=prepared,
            market=market_cache[cache_key],
            execution={"state": "scanned"},
            created_at=_as_utc(created_at),
        )
        rows.append(packet.model_dump(mode="json"))
        seen.add(key)
        recorded += 1

    if len(rows) > _MAX_PACKETS:
        del rows[: len(rows) - _MAX_PACKETS]
    if recorded and not dry_run:
        _save_raw(path, data)
    return {
        "recorded": recorded,
        "skipped_duplicate": skipped_duplicate,
        "skipped_filtered": skipped_filtered,
    }


def record_scan_packets(
    skill_dir: Path | None,
    signals: list[dict[str, Any]] | None,
    *,
    diagnostics: dict[str, Any] | None = None,
    created_at: datetime | None = None,
    dry_run: bool = False,
) -> dict[str, int]:
    """Snapshot kept scan signals into the packet store.

    One staged packet per ticker per UTC day. Re-scans the same day do not
    create another row. Filtered shortlist names stay out of this store; they
    already land in the counterfactual log.
    """
    as_of = _as_utc(created_at) if created_at is not None else utc_now()
    batch = [(sig, as_of, diagnostics) for sig in (signals or []) if isinstance(sig, dict)]
    return _record_staged_batch(skill_dir, batch, dry_run=dry_run)


def record_historical_scan_packets(
    skill_dir: Path | None,
    rows: list[dict[str, Any]],
    *,
    dry_run: bool = False,
) -> dict[str, int]:
    """Record staged packets from prior scan_results rows.

    Each row is ``{"signal": dict, "created_at": datetime}``.
    """
    batch: list[tuple[dict[str, Any], datetime, dict[str, Any] | None]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        signal = row.get("signal")
        created = row.get("created_at")
        if not isinstance(signal, dict) or not isinstance(created, datetime):
            continue
        diagnostics = row.get("diagnostics") if isinstance(row.get("diagnostics"), dict) else None
        batch.append((signal, created, diagnostics))
    return _record_staged_batch(skill_dir, batch, dry_run=dry_run)


def _parse_stored_dt(raw: Any) -> datetime | None:
    if isinstance(raw, datetime):
        return _as_utc(raw)
    if not isinstance(raw, str) or not raw.strip():
        return None
    text = raw.strip().replace("Z", "+00:00")
    if " " in text and "T" not in text:
        text = text.replace(" ", "T", 1)
    try:
        return _as_utc(datetime.fromisoformat(text))
    except ValueError:
        return None


def _parse_payload(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return dict(raw)
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return dict(parsed) if isinstance(parsed, dict) else {}
    return {}


def load_local_scan_signals(
    db_path: Path | str,
    *,
    since: datetime,
    user_id: str = "local",
) -> list[dict[str, Any]]:
    """Read kept scan signals from the local dashboard ``scan_results`` table.

    Returns rows shaped for :func:`record_historical_scan_packets`, oldest first.
    Missing database or table yields an empty list (catch-up still labels
    packets that are already on disk).
    """
    path = Path(db_path)
    if not path.exists():
        return []
    since_utc = _as_utc(since)
    user = str(user_id or "").strip()
    out: list[dict[str, Any]] = []
    try:
        uri_path = path.resolve().as_posix()
        conn = sqlite3.connect(f"file:{uri_path}?mode=ro", uri=True)
    except sqlite3.Error as exc:
        LOG.warning("scan_results open failed for %s: %s", path, exc)
        return []
    try:
        conn.row_factory = sqlite3.Row
        try:
            cursor = conn.execute("SELECT ticker, payload_json, created_at, user_id FROM scan_results")
        except sqlite3.Error as exc:
            LOG.warning("scan_results read failed for %s: %s", path, exc)
            return []
        for row in cursor:
            if user and str(row["user_id"] or "") != user:
                continue
            created = _parse_stored_dt(row["created_at"])
            if created is None or created < since_utc:
                continue
            signal = _parse_payload(row["payload_json"])
            ticker = str(row["ticker"] or signal.get("ticker") or signal.get("symbol") or "").upper()
            if not ticker:
                continue
            signal["ticker"] = ticker
            out.append({"signal": signal, "created_at": created})
    finally:
        conn.close()
    out.sort(key=lambda item: item["created_at"])
    return out
