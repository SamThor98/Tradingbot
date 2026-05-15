"""
Point-in-time fundamentals snapshots (Yahoo-backed).

Persists one JSON blob per ticker per UTC calendar day under
``.fundamentals_snapshots/<TICKER>/<YYYY-MM-DD>.json`` so backtests and audits
can replay fundamentals as-of a date instead of calling live Yahoo inside the
simulation loop (lookahead).

When ``SCHWAB_ONLY_DATA=true``, captures are refused — there is no Schwab
replacement for full statements today.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

LOG = logging.getLogger(__name__)
SKILL_DIR = Path(__file__).resolve().parent


def snapshot_root(skill_dir: Path | None = None) -> Path:
    return (skill_dir or SKILL_DIR) / ".fundamentals_snapshots"


def snapshot_path(ticker: str, as_of_date: str, skill_dir: Path | None = None) -> Path:
    tkr = str(ticker or "").strip().upper()
    return snapshot_root(skill_dir) / tkr / f"{as_of_date}.json"


def load_snapshot(ticker: str, as_of_date: str, skill_dir: Path | None = None) -> dict[str, Any] | None:
    path = snapshot_path(ticker, as_of_date, skill_dir)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def capture_daily_snapshot(ticker: str, skill_dir: Path | None = None) -> dict[str, Any]:
    """Pull a minimal fundamentals bundle via yfinance and atomically persist."""
    from config import get_schwab_only_data

    sd = Path(skill_dir or SKILL_DIR)
    if get_schwab_only_data(sd):
        return {"ok": False, "reason": "schwab_only_data", "ticker": str(ticker or "").strip().upper()}

    tkr = str(ticker or "").strip().upper()
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    path = snapshot_path(tkr, day, sd)
    try:
        import yfinance as yf

        from _io_utils import atomic_write_json, yfinance_call

        with yfinance_call():
            t = yf.Ticker(tkr)
            info = dict(t.info or {})
            qfin = t.quarterly_financials
            qbs = t.quarterly_balance_sheet
            qcf = t.quarterly_cashflow

        def _sheet_blob(frame: Any) -> dict[str, Any]:
            if frame is None or getattr(frame, "empty", True):
                return {}
            try:
                return json.loads(frame.to_json(date_format="iso"))
            except Exception:
                return {"serialize_error": True}

        payload: dict[str, Any] = {
            "ok": True,
            "ticker": tkr,
            "as_of_utc": datetime.now(timezone.utc).isoformat(),
            "calendar_date_utc": day,
            "info": info,
            "quarterly_financials": _sheet_blob(qfin),
            "quarterly_balance_sheet": _sheet_blob(qbs),
            "quarterly_cashflow": _sheet_blob(qcf),
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(path, payload, indent=2)
        LOG.info("Fundamentals snapshot written %s", path)
        return payload
    except Exception as exc:
        LOG.warning("Fundamentals snapshot failed for %s: %s", tkr, exc)
        return {"ok": False, "reason": f"{type(exc).__name__}", "ticker": tkr}
