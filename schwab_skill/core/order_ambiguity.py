"""Lock a symbol when an order request does not return a clear result.

A timeout or HTTP 5xx is unknown: the order may have landed. The symbol stays
locked until the operator checks Schwab and marks the outcome Landed or Absent.
A 4xx rejection is a clear refusal and does not lock the symbol.
"""

from __future__ import annotations

import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from _io_utils import atomic_write_json

AMBIGUOUS_ORDER_ID = "AMBIGUOUS"
_FILE_NAME = ".order_ambiguity.json"
_LOCK = threading.Lock()


class AmbiguityLocked(Exception):
    """An earlier order for this symbol still has no clear result."""

    def __init__(self, ticker: str, record: dict[str, Any]):
        self.ticker = ticker
        self.record = record
        super().__init__(unknown_outcome_message(ticker, str(record.get("reason") or "unresolved order")))


class AmbiguityStoreError(Exception):
    """The lock file could not be read. New orders stay refused."""

    def __init__(self, ticker: str, detail: str):
        self.ticker = ticker
        super().__init__(
            f"GUARDRAIL: Refusing orders for {ticker}. "
            "The order-ambiguity ledger could not be read, so a prior order may still be unresolved. "
            f"{detail}"
        )


def unknown_outcome_message(ticker: str, reason: str) -> str:
    symbol = ticker.upper()
    return (
        f"ORDER OUTCOME UNKNOWN for {symbol}: {reason}. "
        f"No further orders for {symbol} until you confirm the order at Schwab and resolve it."
    )


def _path(skill_dir: Path) -> Path:
    return Path(skill_dir) / _FILE_NAME


def _empty() -> dict[str, Any]:
    return {"locks": {}}


def _load(skill_dir: Path) -> dict[str, Any]:
    path = _path(skill_dir)
    if not path.exists():
        return _empty()
    try:
        import json

        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise AmbiguityStoreError("*", f"Unreadable ledger: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("locks"), dict):
        raise AmbiguityStoreError("*", "Ledger is missing the locks object.")
    return data


def _save(skill_dir: Path, data: dict[str, Any]) -> None:
    atomic_write_json(_path(skill_dir), data, indent=2)


def get_lock(skill_dir: Path, ticker: str) -> dict[str, Any] | None:
    symbol = ticker.upper().strip()
    with _LOCK:
        data = _load(Path(skill_dir))
    record = (data.get("locks") or {}).get(symbol)
    return dict(record) if isinstance(record, dict) else None


def locks_by_symbol(skill_dir: Path) -> dict[str, dict[str, Any]]:
    with _LOCK:
        data = _load(Path(skill_dir))
    locks = data.get("locks") or {}
    out: dict[str, dict[str, Any]] = {}
    for symbol, record in locks.items():
        if isinstance(record, dict):
            out[str(symbol).upper()] = dict(record)
    return out


def begin_intent(
    skill_dir: Path,
    ticker: str,
    action: str,
    *,
    position_key: str | None = None,
) -> str:
    """Write the lock before the request is sent. Returns the intent id."""
    symbol = ticker.upper().strip()
    intent_id = uuid.uuid4().hex
    record = {
        "ticker": symbol,
        "intent_id": intent_id,
        "action": action,
        "status": "pending",
        "reason": f"{action} sent; result not recorded yet",
        "position_key": position_key,
        "at": datetime.now(timezone.utc).isoformat(),
    }
    with _LOCK:
        data = _load(Path(skill_dir))
        existing = (data.get("locks") or {}).get(symbol)
        if isinstance(existing, dict):
            raise AmbiguityLocked(symbol, existing)
        data.setdefault("locks", {})[symbol] = record
        _save(Path(skill_dir), data)
    return intent_id


def finish_known(skill_dir: Path, ticker: str, intent_id: str) -> None:
    """Clear the lock after Schwab accepted or refused the request."""
    symbol = ticker.upper().strip()
    with _LOCK:
        data = _load(Path(skill_dir))
        locks = data.setdefault("locks", {})
        current = locks.get(symbol)
        if isinstance(current, dict) and str(current.get("intent_id") or "") == intent_id:
            locks.pop(symbol, None)
            _save(Path(skill_dir), data)


def mark_unknown(skill_dir: Path, ticker: str, intent_id: str, reason: str) -> None:
    """Keep the lock. The request may have landed."""
    symbol = ticker.upper().strip()
    with _LOCK:
        data = _load(Path(skill_dir))
        locks = data.setdefault("locks", {})
        current = locks.get(symbol)
        if not isinstance(current, dict) or str(current.get("intent_id") or "") != intent_id:
            current = {
                "ticker": symbol,
                "intent_id": intent_id,
                "action": "unknown",
                "position_key": None,
            }
        current["status"] = "unknown"
        current["reason"] = reason[:300]
        current["at"] = datetime.now(timezone.utc).isoformat()
        locks[symbol] = current
        _save(Path(skill_dir), data)


def take_lock(skill_dir: Path, ticker: str) -> dict[str, Any] | None:
    """Remove and return the lock, for an operator resolve."""
    symbol = ticker.upper().strip()
    with _LOCK:
        data = _load(Path(skill_dir))
        locks = data.setdefault("locks", {})
        current = locks.pop(symbol, None)
        if isinstance(current, dict):
            _save(Path(skill_dir), data)
            return dict(current)
    return None
