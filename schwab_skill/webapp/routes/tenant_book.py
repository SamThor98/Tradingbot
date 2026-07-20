"""SaaS Portfolio Book routes: calendar, tax, journal, YTD Excel export.

Mirrors local ``routes/book.py`` with JWT auth + per-tenant skill dirs.
Export is download-bytes only (Render filesystem is ephemeral); notes/status
persist in AppState.
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import User
from ..recovery_map import map_failure
from ..schemas import ApiResponse
from ..security import get_current_user
from ..tenant_runtime import tenant_skill_dir, user_has_account_session

LOG = logging.getLogger(__name__)

router = APIRouter(tags=["book"])

_BOOK_EXPORT_STATUS_KEY = "book_ytd_export_status"
_BOOK_EXPORT_NOTES_PREFIX = "book_ytd_notes_"


def _ok(data: Any = None) -> ApiResponse:
    return ApiResponse(ok=True, data=data)


def _err(message: str, data: Any = None) -> ApiResponse:
    return ApiResponse(ok=False, error=message, data=data)


def _book_err(endpoint: str, exc: Exception) -> ApiResponse:
    mapped = map_failure(str(exc), source=endpoint)
    headline = f"{mapped.get('title', 'Error')}: {mapped.get('summary', 'Something went wrong.')}"
    raw = str(mapped.get("raw_error") or "").strip()
    summary = str(mapped.get("summary") or "")
    err_out = headline
    if raw and raw.lower() not in summary.lower():
        err_out = f"{headline} — {raw[:220]}"
    return ApiResponse(ok=False, error=err_out, data={"recovery": mapped})


def _save_state(db: Session, user_id: str, key: str, payload: dict[str, Any]) -> None:
    from ..models import AppState

    row = db.query(AppState).filter(AppState.user_id == user_id, AppState.key == key).first()
    blob = json.dumps(payload, default=str)
    if row is None:
        db.add(AppState(user_id=user_id, key=key, value_json=blob))
    else:
        row.value_json = blob
    db.commit()


def _load_state(db: Session, user_id: str, key: str, default: dict[str, Any]) -> dict[str, Any]:
    from ..models import AppState

    row = db.query(AppState).filter(AppState.user_id == user_id, AppState.key == key).first()
    if row is None or not row.value_json:
        return dict(default)
    try:
        data = json.loads(row.value_json)
        return data if isinstance(data, dict) else dict(default)
    except Exception:
        return dict(default)


def _notes_key(tax_year: int) -> str:
    return f"{_BOOK_EXPORT_NOTES_PREFIX}{int(tax_year)}"


def _load_notes(db: Session, user_id: str, tax_year: int) -> dict[str, dict[str, str]]:
    raw = _load_state(db, user_id, _notes_key(tax_year), {})
    out: dict[str, dict[str, str]] = {}
    for key, row in raw.items():
        if isinstance(row, dict) and key:
            out[str(key)] = {
                "trade_key": str(key),
                "symbol": str(row.get("symbol") or ""),
                "close_date": str(row.get("close_date") or ""),
                "note": str(row.get("note") or ""),
                "tags": str(row.get("tags") or ""),
                "updated_at": str(row.get("updated_at") or ""),
            }
    return out


def _save_notes(db: Session, user_id: str, tax_year: int, notes: dict[str, dict[str, str]]) -> None:
    _save_state(db, user_id, _notes_key(tax_year), notes)


class TaxPrefsBody(BaseModel):
    federal_st_rate: float = Field(..., ge=0.0, le=1.0)
    federal_lt_rate: float = Field(..., ge=0.0, le=1.0)
    state_rate: float = Field(0.0, ge=0.0, le=1.0)
    tax_year: int | None = Field(default=None, ge=2000, le=2100)


class ThesisBody(BaseModel):
    thesis: str = ""


class JournalNoteBody(BaseModel):
    symbol: str
    mode: str = "quick"
    body: str = ""
    note_type: str = "other"
    note_date: str | None = None
    fill_activity_id: str | None = None
    template: dict[str, Any] | None = None


@router.get("/api/book/calendar", response_model=ApiResponse)
def tenant_book_calendar(
    year: int | None = Query(default=None, ge=2000, le=2100),
    month: int | None = Query(default=None, ge=1, le=12),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiResponse:
    if not user_has_account_session(db, user.id):
        return _err("Link Schwab account before loading the Book calendar.")
    try:
        from core.book_service import build_calendar_payload

        now = datetime.now(timezone.utc)
        y = int(year or now.year)
        m = int(month or now.month)
        with tenant_skill_dir(db, user.id) as skill_dir:
            return _ok(
                build_calendar_payload(
                    db, skill_dir=skill_dir, user_id=user.id, year=y, month=m
                )
            )
    except Exception as exc:
        return _book_err("book_calendar", exc)


@router.get("/api/book/tax", response_model=ApiResponse)
def tenant_book_tax(
    tax_year: int | None = Query(default=None, ge=2000, le=2100),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiResponse:
    if not user_has_account_session(db, user.id):
        return _err("Link Schwab account before loading Book tax estimates.")
    try:
        from core.book_service import build_tax_payload

        with tenant_skill_dir(db, user.id) as skill_dir:
            return _ok(
                build_tax_payload(
                    db, skill_dir=skill_dir, user_id=user.id, tax_year=tax_year
                )
            )
    except Exception as exc:
        return _book_err("book_tax", exc)


@router.get("/api/book/tax/prefs", response_model=ApiResponse)
def tenant_book_tax_prefs_get(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiResponse:
    try:
        from core.book_service import get_tax_prefs

        return _ok(get_tax_prefs(db, user_id=user.id))
    except Exception as exc:
        return _book_err("book_tax_prefs_get", exc)


@router.post("/api/book/tax/prefs", response_model=ApiResponse)
def tenant_book_tax_prefs_put(
    body: TaxPrefsBody,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiResponse:
    try:
        from core.book_service import save_tax_prefs

        return _ok(
            save_tax_prefs(
                db,
                user_id=user.id,
                federal_st_rate=body.federal_st_rate,
                federal_lt_rate=body.federal_lt_rate,
                state_rate=body.state_rate,
                tax_year=body.tax_year,
            )
        )
    except Exception as exc:
        return _book_err("book_tax_prefs_put", exc)


@router.post("/api/book/snapshot", response_model=ApiResponse)
def tenant_book_snapshot(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiResponse:
    if not user_has_account_session(db, user.id):
        return _err("Link Schwab account before capturing a Book snapshot.")
    try:
        from core.book_service import capture_book_snapshot

        with tenant_skill_dir(db, user.id) as skill_dir:
            result = capture_book_snapshot(db, skill_dir=skill_dir, user_id=user.id)
        if not result.get("ok"):
            return ApiResponse(ok=False, error=result.get("error") or "snapshot_failed", data=result)
        return _ok(result)
    except Exception as exc:
        return _book_err("book_snapshot", exc)


@router.get("/api/book/export/status", response_model=ApiResponse)
def tenant_book_export_status(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiResponse:
    try:
        status = _load_state(
            db,
            user.id,
            _BOOK_EXPORT_STATUS_KEY,
            {
                "ok": None,
                "tax_year": None,
                "path": None,
                "finished_at": None,
                "error": None,
                "source": None,
                "closed_count": None,
                "message": "No export yet",
            },
        )
        return _ok(status)
    except Exception as exc:
        return _book_err("book_export_status", exc)


@router.get("/api/book/export")
def tenant_book_export(
    tax_year: int | None = Query(default=None, ge=2000, le=2100),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    """Build YTD workbook in-memory and download (no durable disk write)."""
    if not user_has_account_session(db, user.id):
        err = _err("Link Schwab account before exporting Book YTD Excel.")
        return Response(
            content=json.dumps(err.model_dump(), indent=2),
            media_type="application/json",
            status_code=409,
        )
    try:
        from core.book_export import export_ytd_workbook

        year = int(tax_year or datetime.now(timezone.utc).year)
        notes_seed = _load_notes(db, user.id, year)
        with tenant_skill_dir(db, user.id) as skill_dir:
            result = export_ytd_workbook(
                skill_dir=skill_dir,
                tax_year=year,
                source="button",
                persist_disk=False,
                notes_seed=notes_seed,
            )
        status_payload = {
            k: v
            for k, v in result.items()
            if k not in {"xlsx_bytes", "notes"}
        }
        status_payload["message"] = (
            f"Export OK · {result.get('filename')}"
            if result.get("ok")
            else result.get("message") or "Export failed"
        )
        _save_state(db, user.id, _BOOK_EXPORT_STATUS_KEY, status_payload)
        if result.get("ok"):
            notes = result.get("notes") or {}
            if isinstance(notes, dict):
                _save_notes(db, user.id, year, notes)
            body = result.get("xlsx_bytes") or b""
            filename = str(result.get("filename") or f"book_ytd_{year}.xlsx")
            headers = {"Content-Disposition": f'attachment; filename="{filename}"'}
            return Response(
                content=body,
                media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                headers=headers,
            )
        err = _book_err("book_export", RuntimeError(result.get("error") or "export_failed"))
        return Response(
            content=json.dumps(err.model_dump(), indent=2),
            media_type="application/json",
            status_code=500,
        )
    except Exception as exc:
        LOG.warning("Tenant book export failed: %s", exc)
        err = _book_err("book_export", exc)
        try:
            _save_state(
                db,
                user.id,
                _BOOK_EXPORT_STATUS_KEY,
                {
                    "ok": False,
                    "finished_at": datetime.now(timezone.utc).isoformat(),
                    "error": str(exc),
                    "source": "button",
                    "message": f"Export failed: {exc}",
                },
            )
        except Exception:
            LOG.debug("Could not persist tenant export failure status", exc_info=True)
        return Response(
            content=json.dumps(err.model_dump(), indent=2),
            media_type="application/json",
            status_code=500,
        )


@router.get("/api/book/journal", response_model=ApiResponse)
def tenant_book_journal_list(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiResponse:
    try:
        from core.book_service import list_journal, open_position_symbols
        from execution import get_account_status

        open_syms: list[str] = []
        if user_has_account_session(db, user.id):
            with tenant_skill_dir(db, user.id) as skill_dir:
                status = get_account_status(skill_dir=skill_dir)
                if isinstance(status, dict):
                    open_syms = open_position_symbols(status, skill_dir=skill_dir)
        return _ok(list_journal(db, user_id=user.id, open_symbols=open_syms))
    except Exception as exc:
        return _book_err("book_journal_list", exc)


@router.get("/api/book/journal/{symbol}", response_model=ApiResponse)
def tenant_book_journal_get(
    symbol: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiResponse:
    try:
        from core.book_service import get_journal_ticker

        return _ok(get_journal_ticker(db, user_id=user.id, symbol=symbol))
    except Exception as exc:
        return _book_err("book_journal_get", exc)


@router.post("/api/book/journal/{symbol}/thesis", response_model=ApiResponse)
def tenant_book_journal_thesis(
    symbol: str,
    body: ThesisBody,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiResponse:
    try:
        from core.book_service import upsert_thesis

        return _ok(upsert_thesis(db, user_id=user.id, symbol=symbol, thesis=body.thesis))
    except Exception as exc:
        return _book_err("book_journal_thesis", exc)


@router.post("/api/book/journal/notes", response_model=ApiResponse)
def tenant_book_journal_add_note(
    body: JournalNoteBody,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiResponse:
    try:
        from core.book_service import add_journal_note

        note_day: date | None = None
        if body.note_date:
            note_day = date.fromisoformat(body.note_date[:10])
        note = add_journal_note(
            db,
            user_id=user.id,
            symbol=body.symbol,
            mode=body.mode,
            body=body.body,
            note_type=body.note_type,
            note_date=note_day,
            fill_activity_id=body.fill_activity_id,
            template=body.template,
        )
        return _ok(note)
    except Exception as exc:
        return _book_err("book_journal_add_note", exc)
