"""Book feature orchestration: calendar, tax, journal, EOD snapshots."""

from __future__ import annotations

import json
import logging
from calendar import monthrange
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from core.book_ledger import (
    aggregate_calendar,
    build_realized_ledger,
    lookback_start_for_year,
    tax_estimate_from_ledger,
)
from core.schwab_transactions import fetch_trades_for_skill

LOG = logging.getLogger(__name__)


def _marked_open_equity(equity: float | None, cash: float | None, positions_json: str) -> float | None:
    """Stock-book mark: sum position MVs when present, else equity − cash."""
    try:
        rows = json.loads(positions_json or "[]")
    except Exception:
        rows = []
    if isinstance(rows, list) and rows:
        total = 0.0
        any_mv = False
        for row in rows:
            if not isinstance(row, dict):
                continue
            mv = row.get("market_value")
            if mv is None:
                continue
            try:
                total += float(mv)
                any_mv = True
            except (TypeError, ValueError):
                continue
        if any_mv:
            return total
    if equity is None:
        return None
    try:
        eq = float(equity)
        c = float(cash) if cash is not None else 0.0
        return eq - c
    except (TypeError, ValueError):
        return None


def mtm_deltas_from_snapshots(db: Any, *, user_id: str, start: date, end: date) -> dict[str, float]:
    """Day Δ of marked open equity between consecutive snapshots."""
    from webapp.models import PortfolioEquitySnapshot

    rows = (
        db.query(PortfolioEquitySnapshot)
        .filter(
            PortfolioEquitySnapshot.user_id == user_id,
            PortfolioEquitySnapshot.snapshot_date >= start,
            PortfolioEquitySnapshot.snapshot_date <= end,
        )
        .order_by(PortfolioEquitySnapshot.snapshot_date.asc())
        .all()
    )
    marks: list[tuple[date, float]] = []
    for row in rows:
        m = _marked_open_equity(row.equity, row.cash, row.positions_json)
        if m is not None:
            marks.append((row.snapshot_date, m))

    # Also need day before `start` for first delta
    prev = (
        db.query(PortfolioEquitySnapshot)
        .filter(
            PortfolioEquitySnapshot.user_id == user_id,
            PortfolioEquitySnapshot.snapshot_date < start,
        )
        .order_by(PortfolioEquitySnapshot.snapshot_date.desc())
        .first()
    )
    prev_mark: float | None = None
    if prev is not None:
        prev_mark = _marked_open_equity(prev.equity, prev.cash, prev.positions_json)

    out: dict[str, float] = {}
    for day, mark in marks:
        if prev_mark is not None:
            out[day.isoformat()] = mark - prev_mark
        prev_mark = mark
    return out


def capture_book_snapshot(
    db: Any,
    *,
    skill_dir: Path,
    user_id: str = "local",
    auth: Any | None = None,
    snapshot_date: date | None = None,
) -> dict[str, Any]:
    """Record today's portfolio equity/positions snapshot for MTM."""
    from core.portfolio_equity_snapshot import record_equity_snapshot
    from core.providers import PortfolioProvider
    from execution import get_account_status
    from sector_strength import get_ticker_sector_etf

    day = snapshot_date or datetime.now(timezone.utc).date()
    status_data = get_account_status(auth=auth, skill_dir=skill_dir)
    if isinstance(status_data, str):
        return {"ok": False, "error": status_data, "snapshot_date": day.isoformat()}
    state = PortfolioProvider.normalize_account(
        status_data,
        sector_lookup=lambda t: get_ticker_sector_etf(t, skill_dir=skill_dir),
    )
    saved = record_equity_snapshot(
        db, state, user_id=user_id, source="schwab", snapshot_date=day
    )
    return {
        "ok": bool(saved),
        "snapshot_date": day.isoformat(),
        "equity": state.equity,
        "cash": state.cash,
        "positions": len(state.positions or []),
        "error": None if saved else "snapshot_not_saved",
    }


def build_calendar_payload(
    db: Any,
    *,
    skill_dir: Path,
    user_id: str,
    year: int,
    month: int,
    auth: Any | None = None,
) -> dict[str, Any]:
    today = datetime.now(timezone.utc).date()
    month_start = date(year, month, 1)
    month_end = date(year, month, monthrange(year, month)[1])
    fetch_start = lookback_start_for_year(year, today)
    fetch_end = min(today, month_end)

    raw, meta = fetch_trades_for_skill(
        skill_dir=skill_dir, start=fetch_start, end=fetch_end, auth=auth
    )
    ledger = build_realized_ledger(raw)
    mtm = mtm_deltas_from_snapshots(db, user_id=user_id, start=month_start, end=month_end)
    cal = aggregate_calendar(ledger, mtm_by_day=mtm, year=year, month=month)
    cal["meta"] = {
        **meta,
        "year": year,
        "month": month,
        "fetch_start": fetch_start.isoformat(),
        "fetch_end": fetch_end.isoformat(),
        "disclaimer": "Realized from Schwab TRADE history (FIFO). MTM from local EOD snapshots (positions only).",
    }
    return cal


def build_tax_payload(
    db: Any,
    *,
    skill_dir: Path,
    user_id: str,
    tax_year: int | None = None,
    auth: Any | None = None,
) -> dict[str, Any]:
    prefs = get_tax_prefs(db, user_id=user_id)
    year = int(tax_year or prefs.get("tax_year") or datetime.now(timezone.utc).year)
    today = datetime.now(timezone.utc).date()
    fetch_start = lookback_start_for_year(year, today)
    fetch_end = min(today, date(year, 12, 31))
    raw, meta = fetch_trades_for_skill(
        skill_dir=skill_dir, start=fetch_start, end=fetch_end, auth=auth
    )
    ledger = build_realized_ledger(raw)
    rates_ok = bool(prefs.get("rates_configured"))
    estimate = tax_estimate_from_ledger(
        ledger,
        tax_year=year,
        federal_st_rate=prefs.get("federal_st_rate"),
        federal_lt_rate=prefs.get("federal_lt_rate"),
        state_rate=prefs.get("state_rate"),
        rates_configured=rates_ok,
    )
    estimate["meta"] = meta
    estimate["prefs"] = {
        "federal_st_rate": prefs.get("federal_st_rate"),
        "federal_lt_rate": prefs.get("federal_lt_rate"),
        "state_rate": prefs.get("state_rate"),
        "tax_year": prefs.get("tax_year"),
        "rates_configured": rates_ok,
    }
    return estimate


def get_tax_prefs(db: Any, *, user_id: str) -> dict[str, Any]:
    from webapp.models import BookTaxPrefs

    row = db.query(BookTaxPrefs).filter(BookTaxPrefs.user_id == user_id).one_or_none()
    year = datetime.now(timezone.utc).year
    if row is None:
        return {
            "federal_st_rate": None,
            "federal_lt_rate": None,
            "state_rate": None,
            "tax_year": year,
            "rates_configured": False,
        }
    return {
        "federal_st_rate": row.federal_st_rate,
        "federal_lt_rate": row.federal_lt_rate,
        "state_rate": row.state_rate,
        "tax_year": row.tax_year or year,
        "rates_configured": bool(row.rates_configured),
    }


def save_tax_prefs(
    db: Any,
    *,
    user_id: str,
    federal_st_rate: float,
    federal_lt_rate: float,
    state_rate: float = 0.0,
    tax_year: int | None = None,
) -> dict[str, Any]:
    from webapp.models import BookTaxPrefs

    year = int(tax_year or datetime.now(timezone.utc).year)
    row = db.query(BookTaxPrefs).filter(BookTaxPrefs.user_id == user_id).one_or_none()
    if row is None:
        row = BookTaxPrefs(user_id=user_id)
        db.add(row)
    row.federal_st_rate = float(federal_st_rate)
    row.federal_lt_rate = float(federal_lt_rate)
    row.state_rate = float(state_rate)
    row.tax_year = year
    row.rates_configured = True
    db.commit()
    return get_tax_prefs(db, user_id=user_id)


def list_journal(
    db: Any,
    *,
    user_id: str,
    open_symbols: list[str],
) -> dict[str, Any]:
    from webapp.models import BookJournalNote, BookJournalTicker

    open_set = {s.upper() for s in open_symbols if s}
    tickers = (
        db.query(BookJournalTicker)
        .filter(BookJournalTicker.user_id == user_id)
        .order_by(BookJournalTicker.symbol.asc())
        .all()
    )
    note_counts = (
        db.query(BookJournalNote.symbol)
        .filter(BookJournalNote.user_id == user_id)
        .all()
    )
    noted = {str(r[0]).upper() for r in note_counts}

    # Open positions always appear; noted/thesis tickers persist after exit
    symbols = sorted(open_set | {t.symbol.upper() for t in tickers} | noted)
    by_sym = {t.symbol.upper(): t for t in tickers}
    rows = []
    for sym in symbols:
        t = by_sym.get(sym)
        n_count = (
            db.query(BookJournalNote)
            .filter(BookJournalNote.user_id == user_id, BookJournalNote.symbol == sym)
            .count()
        )
        rows.append(
            {
                "symbol": sym,
                "status": "open" if sym in open_set else "exited",
                "thesis": (t.thesis_text if t else "") or "",
                "note_count": int(n_count),
                "updated_at": t.updated_at.isoformat() if t and t.updated_at else None,
            }
        )
    return {"tickers": rows, "open_count": len(open_set)}


def get_journal_ticker(db: Any, *, user_id: str, symbol: str) -> dict[str, Any]:
    from webapp.models import BookJournalNote, BookJournalTicker

    sym = symbol.upper().strip()
    t = (
        db.query(BookJournalTicker)
        .filter(BookJournalTicker.user_id == user_id, BookJournalTicker.symbol == sym)
        .one_or_none()
    )
    notes = (
        db.query(BookJournalNote)
        .filter(BookJournalNote.user_id == user_id, BookJournalNote.symbol == sym)
        .order_by(BookJournalNote.note_date.desc(), BookJournalNote.id.desc())
        .all()
    )
    return {
        "symbol": sym,
        "thesis": (t.thesis_text if t else "") or "",
        "notes": [_note_dict(n) for n in notes],
    }


def upsert_thesis(db: Any, *, user_id: str, symbol: str, thesis: str) -> dict[str, Any]:
    from webapp.models import BookJournalTicker

    sym = symbol.upper().strip()
    row = (
        db.query(BookJournalTicker)
        .filter(BookJournalTicker.user_id == user_id, BookJournalTicker.symbol == sym)
        .one_or_none()
    )
    if row is None:
        row = BookJournalTicker(user_id=user_id, symbol=sym, thesis_text=thesis or "")
        db.add(row)
    else:
        row.thesis_text = thesis or ""
    db.commit()
    return get_journal_ticker(db, user_id=user_id, symbol=sym)


def add_journal_note(
    db: Any,
    *,
    user_id: str,
    symbol: str,
    mode: str,
    body: str = "",
    note_type: str = "other",
    note_date: date | None = None,
    fill_activity_id: str | None = None,
    template: dict[str, Any] | None = None,
) -> dict[str, Any]:
    from webapp.models import BookJournalNote, BookJournalTicker

    sym = symbol.upper().strip()
    mode_n = "full" if str(mode).lower() == "full" else "quick"
    day = note_date or datetime.now(timezone.utc).date()
    # Ensure ticker row exists so exited names stay listed
    t = (
        db.query(BookJournalTicker)
        .filter(BookJournalTicker.user_id == user_id, BookJournalTicker.symbol == sym)
        .one_or_none()
    )
    if t is None:
        db.add(BookJournalTicker(user_id=user_id, symbol=sym, thesis_text=""))
    note = BookJournalNote(
        user_id=user_id,
        symbol=sym,
        mode=mode_n,
        note_type=(note_type or "other")[:32],
        body=body or "",
        note_date=day,
        fill_activity_id=fill_activity_id,
        template_json=template or {},
    )
    db.add(note)
    db.commit()
    db.refresh(note)
    return _note_dict(note)


def _note_dict(n: Any) -> dict[str, Any]:
    return {
        "id": n.id,
        "symbol": n.symbol,
        "mode": n.mode,
        "note_type": n.note_type,
        "body": n.body or "",
        "note_date": n.note_date.isoformat() if n.note_date else None,
        "fill_activity_id": n.fill_activity_id,
        "template": n.template_json or {},
        "created_at": n.created_at.isoformat() if n.created_at else None,
    }


def open_position_symbols(
    status_data: dict[str, Any] | list[Any] | Any,
    *,
    skill_dir: Path | None = None,
) -> list[str]:
    """Extract open equity symbols from account status or portfolio summary."""
    if isinstance(status_data, dict):
        positions = status_data.get("positions")
        if isinstance(positions, list):
            out = []
            for p in positions:
                if isinstance(p, dict):
                    sym = p.get("symbol") or p.get("ticker")
                    if sym:
                        out.append(str(sym).upper())
            if out:
                return sorted(set(out))
    try:
        from core.providers import PortfolioProvider

        state = PortfolioProvider.normalize_account(status_data)
        return sorted({p.ticker.upper() for p in (state.positions or []) if p.ticker})
    except Exception:
        LOG.debug("open_position_symbols normalize failed", exc_info=True)
        return []
