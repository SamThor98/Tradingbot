"""YTD Book Excel export: Fills / Closed / OpenLots / ClosedOptions / Summary / Notes."""

from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from core.book_ledger import (
    build_options_realized_ledger,
    build_realized_ledger,
    closed_row_analysis,
    lookback_start_for_year,
)
from core.schwab_transactions import fetch_trades_for_skill
from core.xlsx_workbook import read_sheet_rows, sheets_to_xlsx

LOG = logging.getLogger(__name__)

_NOTES_HEADERS = ["trade_key", "symbol", "close_date", "note", "tags", "updated_at"]
_STATUS_FILENAME = "book_ytd_export_status.json"


def _f(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_day(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    text = str(value).strip()
    if not text:
        return None
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).date()
    except Exception:
        try:
            return date.fromisoformat(text[:10])
        except Exception:
            return None


def resolve_export_path(skill_dir: Path, tax_year: int) -> Path:
    from config import get_book_ytd_export_path

    return get_book_ytd_export_path(skill_dir, tax_year=tax_year)


def status_path(skill_dir: Path) -> Path:
    return skill_dir / "exports" / _STATUS_FILENAME


def notes_sidecar_path(xlsx_path: Path) -> Path:
    return xlsx_path.with_suffix(".notes.json")


def read_export_status(skill_dir: Path) -> dict[str, Any]:
    path = status_path(skill_dir)
    if not path.is_file():
        return {
            "ok": None,
            "tax_year": None,
            "path": None,
            "finished_at": None,
            "error": None,
            "source": None,
            "closed_count": None,
            "message": "No export yet",
        }
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {"ok": False, "error": "invalid_status"}
    except Exception as exc:
        return {"ok": False, "error": f"status_unreadable: {exc}"}


def write_export_status(skill_dir: Path, payload: dict[str, Any]) -> None:
    path = status_path(skill_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _load_notes(xlsx_path: Path) -> dict[str, dict[str, str]]:
    """Load notes keyed by trade_key from sidecar + existing Notes sheet."""
    notes: dict[str, dict[str, str]] = {}
    side = notes_sidecar_path(xlsx_path)
    if side.is_file():
        try:
            raw = json.loads(side.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                for key, row in raw.items():
                    if isinstance(row, dict) and key:
                        notes[str(key)] = {
                            "trade_key": str(key),
                            "symbol": str(row.get("symbol") or ""),
                            "close_date": str(row.get("close_date") or ""),
                            "note": str(row.get("note") or ""),
                            "tags": str(row.get("tags") or ""),
                            "updated_at": str(row.get("updated_at") or ""),
                        }
        except Exception:
            LOG.warning("Failed to read notes sidecar %s", side, exc_info=True)

    sheet_rows = read_sheet_rows(xlsx_path, "Notes")
    if sheet_rows and len(sheet_rows) >= 2:
        headers = [str(h).strip().lower() for h in sheet_rows[0]]
        idx = {name: headers.index(name) for name in _NOTES_HEADERS if name in headers}
        if "trade_key" in idx:
            for row in sheet_rows[1:]:
                if not row:
                    continue

                def _get(name: str) -> str:
                    i = idx.get(name)
                    if i is None or i >= len(row):
                        return ""
                    return str(row[i] or "")

                key = _get("trade_key").strip()
                if not key:
                    continue
                excel_note = _get("note")
                excel_tags = _get("tags")
                excel_updated = _get("updated_at")
                prev = notes.get(key)
                # Prefer Excel edits when note/tags differ from sidecar
                if prev is None or excel_note != (prev.get("note") or "") or excel_tags != (
                    prev.get("tags") or ""
                ):
                    notes[key] = {
                        "trade_key": key,
                        "symbol": _get("symbol") or (prev or {}).get("symbol", ""),
                        "close_date": _get("close_date") or (prev or {}).get("close_date", ""),
                        "note": excel_note,
                        "tags": excel_tags,
                        "updated_at": excel_updated
                        or (prev or {}).get("updated_at", "")
                        or datetime.now(timezone.utc).isoformat(),
                    }
    return notes


def _save_notes_sidecar(xlsx_path: Path, notes: dict[str, dict[str, str]]) -> None:
    side = notes_sidecar_path(xlsx_path)
    side.parent.mkdir(parents=True, exist_ok=True)
    side.write_text(json.dumps(notes, indent=2, sort_keys=True), encoding="utf-8")


def _flatten_fills(raw_trades: list[dict[str, Any]]) -> list[list[Any]]:
    header = [
        "activity_id",
        "trade_date",
        "symbol",
        "asset_type",
        "underlying",
        "effect",
        "qty",
        "price",
        "cost",
        "fees",
        "net_amount",
        "description",
    ]
    rows: list[list[Any]] = [header]
    for tx in raw_trades:
        if not isinstance(tx, dict):
            continue
        day = _parse_day(tx.get("tradeDate") or tx.get("time") or tx.get("settlementDate"))
        activity_id = tx.get("activityId")
        desc = str(tx.get("description") or "")
        net = _f(tx.get("netAmount"))
        fees = 0.0
        for item in tx.get("transferItems") or []:
            if isinstance(item, dict) and item.get("feeType"):
                amt = _f(item.get("amount"))
                if amt is not None:
                    fees += abs(amt)
        for item in tx.get("transferItems") or []:
            if not isinstance(item, dict) or item.get("feeType"):
                continue
            inst = item.get("instrument") or {}
            if not isinstance(inst, dict):
                continue
            sym = str(inst.get("symbol") or "").upper().strip()
            if not sym:
                continue
            asset = str(inst.get("assetType") or "").upper() or "EQUITY"
            if asset in ("FUTURE", "FOREX", "FIXED_INCOME", "MUTUAL_FUND", "INDEX"):
                continue
            rows.append(
                [
                    activity_id,
                    day.isoformat() if day else "",
                    sym,
                    asset,
                    str(inst.get("underlyingSymbol") or "").upper().strip(),
                    str(item.get("positionEffect") or ""),
                    _f(item.get("amount")),
                    _f(item.get("price")),
                    _f(item.get("cost")),
                    round(fees, 2),
                    net,
                    desc,
                ]
            )
    return rows


def _closed_sheet(rows: list[dict[str, Any]]) -> list[list[Any]]:
    header = [
        "trade_key",
        "activity_id",
        "symbol",
        "open_date",
        "close_date",
        "qty",
        "cost_basis",
        "proceeds",
        "fees",
        "realized_pl",
        "return_pct",
        "hold_days",
        "holding",
        "win_loss",
        "close_month",
        "close_weekday",
        "description",
        "unmatched_flag",
    ]
    out: list[list[Any]] = [header]
    for r in rows:
        out.append(
            [
                r.get("trade_key"),
                r.get("activity_id"),
                r.get("symbol"),
                r.get("open_date"),
                r.get("close_date"),
                r.get("qty"),
                r.get("cost_basis"),
                r.get("proceeds"),
                r.get("fees"),
                r.get("realized_pl"),
                r.get("return_pct"),
                r.get("hold_days"),
                r.get("holding"),
                r.get("win_loss"),
                r.get("close_month"),
                r.get("close_weekday"),
                r.get("description"),
                r.get("unmatched_flag", ""),
            ]
        )
    return out


def _options_closed_sheet(rows: list[dict[str, Any]]) -> list[list[Any]]:
    header = [
        "trade_key",
        "activity_id",
        "symbol",
        "underlying",
        "open_date",
        "close_date",
        "qty",
        "cost_basis",
        "proceeds",
        "fees",
        "realized_pl",
        "return_pct",
        "hold_days",
        "holding",
        "win_loss",
        "close_month",
        "close_weekday",
        "description",
    ]
    out: list[list[Any]] = [header]
    for r in rows:
        out.append(
            [
                r.get("trade_key"),
                r.get("activity_id"),
                r.get("symbol"),
                r.get("underlying"),
                r.get("open_date"),
                r.get("close_date"),
                r.get("qty"),
                r.get("cost_basis"),
                r.get("proceeds"),
                r.get("fees"),
                r.get("realized_pl"),
                r.get("return_pct"),
                r.get("hold_days"),
                r.get("holding"),
                r.get("win_loss"),
                r.get("close_month"),
                r.get("close_weekday"),
                r.get("description"),
            ]
        )
    return out


def _open_lots_sheet(open_lots: list[Any], *, as_of: date) -> list[list[Any]]:
    header = [
        "symbol",
        "asset_class",
        "open_date",
        "qty",
        "cost_basis",
        "cost_per_unit",
        "days_held",
    ]
    out: list[list[Any]] = [header]
    for lot in open_lots:
        qty = float(lot.qty)
        cost = float(lot.cost_total)
        out.append(
            [
                lot.symbol,
                lot.asset_class,
                lot.open_day.isoformat(),
                round(qty, 4),
                round(cost, 2),
                round(cost / qty, 4) if qty > 0 else None,
                max(0, (as_of - lot.open_day).days),
            ]
        )
    return out


def _summary_metrics(closed: list[dict[str, Any]], *, tax_year: int, meta: dict[str, Any]) -> list[list[Any]]:
    pls = [float(r["realized_pl"]) for r in closed if r.get("realized_pl") is not None]
    wins = [p for p in pls if p > 0]
    losses = [p for p in pls if p < 0]
    total = sum(pls) if pls else 0.0
    win_rate = (100.0 * len(wins) / len(pls)) if pls else None
    avg_win = (sum(wins) / len(wins)) if wins else None
    avg_loss = (sum(losses) / len(losses)) if losses else None
    expectancy = (total / len(pls)) if pls else None
    gross_win = sum(wins)
    gross_loss = abs(sum(losses))
    # Blank when no losses (undefined/infinite PF) or no trades
    profit_factor: float | None = None
    if gross_loss > 1e-9:
        profit_factor = round(gross_win / gross_loss, 4)

    by_day: dict[str, float] = {}
    by_month: dict[int, float] = {}
    for r in closed:
        d = str(r.get("close_date") or "")
        pl = float(r.get("realized_pl") or 0.0)
        if d:
            by_day[d] = by_day.get(d, 0.0) + pl
        m = r.get("close_month")
        if isinstance(m, int):
            by_month[m] = by_month.get(m, 0.0) + pl

    best_day = max(by_day.items(), key=lambda kv: kv[1]) if by_day else (None, None)
    worst_day = min(by_day.items(), key=lambda kv: kv[1]) if by_day else (None, None)

    st = sum(float(r["realized_pl"]) for r in closed if r.get("holding") == "st")
    lt = sum(float(r["realized_pl"]) for r in closed if r.get("holding") == "lt")
    fees = sum(float(r.get("fees") or 0.0) for r in closed)

    rows: list[list[Any]] = [
        ["Metric", "Value"],
        ["Tax year", tax_year],
        ["Last refresh (UTC)", datetime.now(timezone.utc).isoformat()],
        ["Closed trades", len(closed)],
        ["Total realized P/L", round(total, 2)],
        ["Fees (allocated on closes)", round(fees, 2)],
        ["Short-term realized P/L", round(st, 2)],
        ["Long-term realized P/L", round(lt, 2)],
        ["Win rate %", round(win_rate, 2) if win_rate is not None else ""],
        ["Avg win", round(avg_win, 2) if avg_win is not None else ""],
        ["Avg loss", round(avg_loss, 2) if avg_loss is not None else ""],
        ["Expectancy (per close)", round(expectancy, 2) if expectancy is not None else ""],
        ["Profit factor", profit_factor if profit_factor is not None else ""],
        ["Best day", best_day[0] or ""],
        ["Best day P/L", round(best_day[1], 2) if best_day[1] is not None else ""],
        ["Worst day", worst_day[0] or ""],
        ["Worst day P/L", round(worst_day[1], 2) if worst_day[1] is not None else ""],
        ["Schwab fetch start", meta.get("fetch_start") or meta.get("start") or ""],
        ["Schwab fetch end", meta.get("fetch_end") or meta.get("end") or ""],
        ["Raw TRADE count", meta.get("count")],
        ["Equity unmatched closes", meta.get("closes_unmatched")],
        ["Options unmatched closes", meta.get("options_closes_unmatched")],
        [
            "Caveat",
            "Schwab TRADE window capped ~364 days; FIFO long-only equities; "
            "single-leg options only; not tax advice / not 1099-B.",
        ],
        ["Limitation", "Short equities not modeled — closes without opens are unmatched."],
        [],
        ["Month", "Realized P/L"],
    ]
    for m in range(1, 13):
        if m in by_month:
            rows.append([m, round(by_month[m], 2)])
        else:
            rows.append([m, 0.0])
    return rows


def _notes_sheet(
    notes: dict[str, dict[str, str]],
    closed_keys: list[tuple[str, str, str]],
) -> list[list[Any]]:
    """closed_keys: (trade_key, symbol, close_date)."""
    out: list[list[Any]] = [_NOTES_HEADERS]
    seen: set[str] = set()
    now = datetime.now(timezone.utc).isoformat()
    for key, symbol, close_date in closed_keys:
        seen.add(key)
        prev = notes.get(key) or {}
        out.append(
            [
                key,
                prev.get("symbol") or symbol,
                prev.get("close_date") or close_date,
                prev.get("note") or "",
                prev.get("tags") or "",
                prev.get("updated_at") or now,
            ]
        )
    # Preserve orphan notes (trades no longer in year window)
    for key, prev in sorted(notes.items()):
        if key in seen:
            continue
        out.append(
            [
                key,
                prev.get("symbol") or "",
                prev.get("close_date") or "",
                prev.get("note") or "",
                prev.get("tags") or "",
                prev.get("updated_at") or "",
            ]
        )
    return out


def _atomic_write_xlsx(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=path.stem + ".", suffix=".xlsx.tmp", dir=str(path.parent))
    os.close(fd)
    tmp_path = Path(tmp_name)
    try:
        tmp_path.write_bytes(payload)
        try:
            os.replace(tmp_path, path)
        except OSError as exc:
            raise PermissionError(f"Export file locked or unwritable: {path}") from exc
    finally:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass


def build_ytd_workbook_bytes(
    *,
    skill_dir: Path,
    tax_year: int,
    auth: Any | None = None,
    xlsx_path: Path | None = None,
    notes_seed: dict[str, dict[str, str]] | None = None,
) -> tuple[bytes, dict[str, Any]]:
    """Build workbook bytes and meta (does not write disk).

    ``notes_seed`` merges in durable notes (e.g. SaaS AppState) before the
    on-disk Notes sheet / sidecar, so ephemeral tenant skill dirs keep annotations.
    """
    today = datetime.now(timezone.utc).date()
    fetch_start = lookback_start_for_year(tax_year, today)
    fetch_end = min(today, date(tax_year, 12, 31))
    raw, meta = fetch_trades_for_skill(
        skill_dir=skill_dir, start=fetch_start, end=fetch_end, auth=auth
    )
    if meta.get("error") and not raw:
        raise RuntimeError(str(meta["error"]))

    eq_ledger = build_realized_ledger(raw)
    opt_ledger = build_options_realized_ledger(raw)

    closed_eq = [
        {**closed_row_analysis(f, tax_year=tax_year), "unmatched_flag": ""}
        for f in eq_ledger.fills
        if f.trade_date.year == tax_year
    ]
    closed_opt = [
        closed_row_analysis(f, tax_year=tax_year)
        for f in opt_ledger.fills
        if f.trade_date.year == tax_year
    ]

    path = xlsx_path or resolve_export_path(skill_dir, tax_year)
    notes = dict(notes_seed or {})
    disk_notes = _load_notes(path)
    for key, row in disk_notes.items():
        prev = notes.get(key)
        if prev is None:
            notes[key] = row
            continue
        # Prefer non-empty disk edits when they differ
        if (row.get("note") or row.get("tags")) and (
            row.get("note") != prev.get("note") or row.get("tags") != prev.get("tags")
        ):
            notes[key] = row

    meta_out = {
        **meta,
        "fetch_start": fetch_start.isoformat(),
        "fetch_end": fetch_end.isoformat(),
        "tax_year": tax_year,
        "closes_unmatched": eq_ledger.closes_unmatched,
        "options_closes_unmatched": opt_ledger.closes_unmatched,
        "closed_count": len(closed_eq),
        "closed_options_count": len(closed_opt),
        "open_lots_count": len(eq_ledger.open_lots) + len(opt_ledger.open_lots),
        "path": str(path),
    }

    note_keys = [
        (str(r["trade_key"]), str(r.get("symbol") or ""), str(r.get("close_date") or ""))
        for r in closed_eq + closed_opt
        if r.get("trade_key")
    ]
    # Sync notes dict for sidecar write by caller
    notes_sheet = _notes_sheet(notes, note_keys)
    # Rebuild notes map from sheet for sidecar
    notes_out: dict[str, dict[str, str]] = {}
    for row in notes_sheet[1:]:
        if not row or not row[0]:
            continue
        notes_out[str(row[0])] = {
            "trade_key": str(row[0]),
            "symbol": str(row[1] if len(row) > 1 else ""),
            "close_date": str(row[2] if len(row) > 2 else ""),
            "note": str(row[3] if len(row) > 3 else ""),
            "tags": str(row[4] if len(row) > 4 else ""),
            "updated_at": str(row[5] if len(row) > 5 else ""),
        }

    open_all = list(eq_ledger.open_lots) + list(opt_ledger.open_lots)
    sheets = [
        ("Fills", _flatten_fills(raw)),
        ("Closed", _closed_sheet(closed_eq)),
        ("OpenLots", _open_lots_sheet(open_all, as_of=today)),
        ("ClosedOptions", _options_closed_sheet(closed_opt)),
        ("Summary", _summary_metrics(closed_eq, tax_year=tax_year, meta=meta_out)),
        ("Notes", notes_sheet),
    ]
    payload = sheets_to_xlsx(sheets)
    meta_out["_notes"] = notes_out
    return payload, meta_out


def export_ytd_workbook(
    *,
    skill_dir: Path,
    tax_year: int | None = None,
    auth: Any | None = None,
    source: str = "button",
    persist_disk: bool = True,
    notes_seed: dict[str, dict[str, str]] | None = None,
) -> dict[str, Any]:
    """Build YTD workbook; optionally persist canonical file + notes sidecar.

    Set ``persist_disk=False`` for SaaS/ephemeral skill dirs (download bytes only).
    Returns status dict including ``xlsx_bytes`` and ``notes`` for durable stores.
    """
    year = int(tax_year or datetime.now(timezone.utc).year)
    path = resolve_export_path(skill_dir, year)
    finished = datetime.now(timezone.utc).isoformat()
    try:
        payload, meta = build_ytd_workbook_bytes(
            skill_dir=skill_dir,
            tax_year=year,
            auth=auth,
            xlsx_path=path if persist_disk else None,
            notes_seed=notes_seed,
        )
        notes = meta.pop("_notes", {})
        if persist_disk:
            _atomic_write_xlsx(path, payload)
            _save_notes_sidecar(path, notes)
        status = {
            "ok": True,
            "tax_year": year,
            "path": str(path) if persist_disk else None,
            "finished_at": finished,
            "error": None,
            "source": source,
            "closed_count": meta.get("closed_count"),
            "closed_options_count": meta.get("closed_options_count"),
            "open_lots_count": meta.get("open_lots_count"),
            "raw_trade_count": meta.get("count"),
            "closes_unmatched": meta.get("closes_unmatched"),
            "message": f"Export OK · {path.name}",
            "filename": path.name,
            "persist_disk": persist_disk,
            "notes": notes,
        }
        if persist_disk:
            write_export_status(
                skill_dir,
                {k: v for k, v in status.items() if k not in {"xlsx_bytes", "notes"}},
            )
        status["xlsx_bytes"] = payload
        return status
    except Exception as exc:
        LOG.warning("Book YTD export failed: %s", exc)
        status = {
            "ok": False,
            "tax_year": year,
            "path": str(path) if persist_disk else None,
            "finished_at": finished,
            "error": str(exc),
            "source": source,
            "closed_count": None,
            "message": f"Export failed: {exc}",
            "filename": path.name,
            "persist_disk": persist_disk,
            "notes": notes_seed or {},
        }
        if persist_disk:
            try:
                write_export_status(skill_dir, status)
            except Exception:
                LOG.debug("Could not persist export failure status", exc_info=True)
        return status
