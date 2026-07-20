"""Book routes: P/L calendar, tax estimate, trading journal (local dashboard)."""

from __future__ import annotations

import json
import os
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..db import get_db
from ..recovery_map import map_failure as _map_failure
from ..route_helpers import require_api_key_if_set as _require_api_key_if_set
from ..schemas import ApiResponse

router = APIRouter(tags=["book"])

SKILL_DIR = Path(__file__).resolve().parent.parent.parent
LOCAL_USER = (os.getenv("WEB_LOCAL_USER_ID", "local") or "local").strip() or "local"


def _ok(data: Any = None) -> ApiResponse:
    return ApiResponse(ok=True, data=data)


def _err(endpoint: str, exc: Exception) -> ApiResponse:
    mapped = _map_failure(str(exc), source=endpoint)
    headline = f"{mapped.get('title', 'Error')}: {mapped.get('summary', 'Something went wrong.')}"
    raw = str(mapped.get("raw_error") or "").strip()
    summary = str(mapped.get("summary") or "")
    err_out = headline
    if raw and raw.lower() not in summary.lower():
        err_out = f"{headline} — {raw[:220]}"
    return ApiResponse(ok=False, error=err_out, data={"recovery": mapped})


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
def book_calendar(
    year: int | None = Query(default=None, ge=2000, le=2100),
    month: int | None = Query(default=None, ge=1, le=12),
    db: Session = Depends(get_db),
) -> ApiResponse:
    try:
        from core.book_service import build_calendar_payload

        now = datetime.now(timezone.utc)
        y = int(year or now.year)
        m = int(month or now.month)
        return _ok(
            build_calendar_payload(
                db, skill_dir=SKILL_DIR, user_id=LOCAL_USER, year=y, month=m
            )
        )
    except Exception as exc:
        return _err("book_calendar", exc)


@router.get("/api/book/tax", response_model=ApiResponse)
def book_tax(
    tax_year: int | None = Query(default=None, ge=2000, le=2100),
    db: Session = Depends(get_db),
) -> ApiResponse:
    try:
        from core.book_service import build_tax_payload

        return _ok(
            build_tax_payload(
                db, skill_dir=SKILL_DIR, user_id=LOCAL_USER, tax_year=tax_year
            )
        )
    except Exception as exc:
        return _err("book_tax", exc)


@router.get("/api/book/tax/prefs", response_model=ApiResponse)
def book_tax_prefs_get(db: Session = Depends(get_db)) -> ApiResponse:
    try:
        from core.book_service import get_tax_prefs

        return _ok(get_tax_prefs(db, user_id=LOCAL_USER))
    except Exception as exc:
        return _err("book_tax_prefs_get", exc)


@router.post("/api/book/tax/prefs", response_model=ApiResponse)
def book_tax_prefs_put(
    body: TaxPrefsBody,
    _auth: dict[str, str] = Depends(_require_api_key_if_set),
    db: Session = Depends(get_db),
) -> ApiResponse:
    try:
        from core.book_service import save_tax_prefs

        return _ok(
            save_tax_prefs(
                db,
                user_id=LOCAL_USER,
                federal_st_rate=body.federal_st_rate,
                federal_lt_rate=body.federal_lt_rate,
                state_rate=body.state_rate,
                tax_year=body.tax_year,
            )
        )
    except Exception as exc:
        return _err("book_tax_prefs_put", exc)


@router.post("/api/book/snapshot", response_model=ApiResponse)
def book_snapshot(
    _auth: dict[str, str] = Depends(_require_api_key_if_set),
    db: Session = Depends(get_db),
) -> ApiResponse:
    try:
        from core.book_service import capture_book_snapshot

        result = capture_book_snapshot(db, skill_dir=SKILL_DIR, user_id=LOCAL_USER)
        if not result.get("ok"):
            return ApiResponse(ok=False, error=result.get("error") or "snapshot_failed", data=result)
        return _ok(result)
    except Exception as exc:
        return _err("book_snapshot", exc)


@router.get("/api/book/export/status", response_model=ApiResponse)
def book_export_status() -> ApiResponse:
    try:
        from core.book_export import read_export_status

        return _ok(read_export_status(SKILL_DIR))
    except Exception as exc:
        return _err("book_export_status", exc)


@router.get("/api/book/export")
def book_export(
    tax_year: int | None = Query(default=None, ge=2000, le=2100),
    _auth: dict[str, str] = Depends(_require_api_key_if_set),
) -> Response:
    """Regenerate canonical YTD workbook on disk and download a copy."""
    try:
        from core.book_export import export_ytd_workbook

        year = int(tax_year or datetime.now(timezone.utc).year)
        result = export_ytd_workbook(
            skill_dir=SKILL_DIR, tax_year=year, source="button"
        )
        if not result.get("ok"):
            err = _err("book_export", RuntimeError(result.get("error") or "export_failed"))
            return Response(
                content=json.dumps(err.model_dump(), indent=2),
                media_type="application/json",
                status_code=409 if "locked" in str(result.get("error") or "").lower() else 500,
            )
        body = result.get("xlsx_bytes") or b""
        filename = str(result.get("filename") or f"book_ytd_{year}.xlsx")
        headers = {"Content-Disposition": f'attachment; filename="{filename}"'}
        return Response(
            content=body,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers=headers,
        )
    except Exception as exc:
        error = _err("book_export", exc)
        return Response(
            content=json.dumps(error.model_dump(), indent=2),
            media_type="application/json",
            status_code=500,
        )


@router.get("/api/book/journal", response_model=ApiResponse)
def book_journal_list(db: Session = Depends(get_db)) -> ApiResponse:
    try:
        from core.book_service import list_journal, open_position_symbols
        from execution import get_account_status

        open_syms: list[str] = []
        status = get_account_status(skill_dir=SKILL_DIR)
        if isinstance(status, dict):
            open_syms = open_position_symbols(status, skill_dir=SKILL_DIR)
        return _ok(list_journal(db, user_id=LOCAL_USER, open_symbols=open_syms))
    except Exception as exc:
        return _err("book_journal_list", exc)


@router.get("/api/book/journal/{symbol}", response_model=ApiResponse)
def book_journal_get(symbol: str, db: Session = Depends(get_db)) -> ApiResponse:
    try:
        from core.book_service import get_journal_ticker

        return _ok(get_journal_ticker(db, user_id=LOCAL_USER, symbol=symbol))
    except Exception as exc:
        return _err("book_journal_get", exc)


@router.post("/api/book/journal/{symbol}/thesis", response_model=ApiResponse)
def book_journal_thesis(
    symbol: str,
    body: ThesisBody,
    _auth: dict[str, str] = Depends(_require_api_key_if_set),
    db: Session = Depends(get_db),
) -> ApiResponse:
    try:
        from core.book_service import upsert_thesis

        return _ok(upsert_thesis(db, user_id=LOCAL_USER, symbol=symbol, thesis=body.thesis))
    except Exception as exc:
        return _err("book_journal_thesis", exc)


@router.post("/api/book/journal/notes", response_model=ApiResponse)
def book_journal_add_note(
    body: JournalNoteBody,
    _auth: dict[str, str] = Depends(_require_api_key_if_set),
    db: Session = Depends(get_db),
) -> ApiResponse:
    try:
        from core.book_service import add_journal_note

        note_day: date | None = None
        if body.note_date:
            note_day = date.fromisoformat(body.note_date[:10])
        note = add_journal_note(
            db,
            user_id=LOCAL_USER,
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
        return _err("book_journal_add_note", exc)
