"""
Tenant-scoped dashboard routes for SaaS (status, portfolio, pending trades, onboarding, OAuth).

Registered only from main_saas to avoid widening the local single-user attack surface.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import struct
import time
import urllib.parse
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from fastapi.responses import RedirectResponse, Response
from sqlalchemy.orm import Session as OrmSession

from challenger_mode import ChallengerRunner
from core.execution_service import submit_order
from core.scan_service import run_scan
from evolve_logic import LearningEngine
from execution import get_account_status, get_position_size_usd
from finnhub_data import get_finnhub_research_snapshot
from full_report import REPORT_SECTION_MAP, generate_full_report, quick_check, report_to_json
from market_data import (
    extract_schwab_last_price,
    get_current_quote,
    get_current_quote_with_status,
    get_daily_history_with_meta,
)
from schwab_auth import DualSchwabAuth
from sec_filing_compare import (
    analyze_latest_filing_for_ticker,
    compare_ticker_over_time,
    compare_ticker_vs_ticker,
)
from sector_strength import get_sector_heatmap

from ._shared import (
    build_portfolio_risk_analytics as _build_portfolio_risk_analytics,
)
from ._shared import (
    build_portfolio_summary as _build_portfolio_summary,  # noqa: F401  (re-export)
)
from ._shared import (
    quote_health_hint as _quote_health_hint,  # noqa: F401  (re-export)
)
from ._shared import (
    trade_to_dict as _trade_to_dict,  # noqa: F401  (re-export)
)
from .audit import log_audit
from .billing_stripe import user_has_paid_entitlement
from .checklist_language import with_plain_language
from .learning_state import (
    LEARNING_LAST_RUN_KEY,
    append_challenger_result,
    load_challenger_history,
    load_state_json,
    load_strategy_update,
    load_trade_outcomes,
    save_learning_last_run,
    save_strategy_update,
    upsert_trade_outcome,
)
from .models import AppState, BacktestRun, Order, PendingTrade, ScanResult, User, UserCredential
from .oauth_schwab import (
    SCHWAB_OAUTH_KIND_ACCOUNT,
    SCHWAB_OAUTH_KIND_MARKET,
    exchange_schwab_code_for_tokens,
    schwab_authorize_url,
    sign_schwab_oauth_state,
    verify_schwab_oauth_state,
)
from .preset_catalog import PRESET_PROFILES, build_preset_catalog_payload
from .recovery_map import map_failure
from .redaction import safe_exception_message
from .report_v2 import build_report_v2
from .route_helpers import (
    apply_profile_to_runtime as _shared_apply_profile_to_runtime,
)
from .route_helpers import (
    is_loopback_host as _shared_is_loopback_host,
)
from .route_helpers import (
    ok as _shared_ok,
)
from .route_helpers import (
    request_id as _shared_request_id,
)
from .route_helpers import (
    request_origin as _shared_request_origin,
)
from .route_helpers import (
    resolve_schwab_redirect_uri as _shared_resolve_schwab_redirect_uri,
)
from .route_helpers import (
    saas_error_response as _shared_saas_error_response,
)
from .route_helpers import (
    simple_err as _shared_simple_err,
)
from .schemas import ApiResponse, ApproveTradeRequest, CreatePendingTrade
from .security import (
    decrypt_secret,
    encrypt_secret,
    get_current_user,
    parse_json,
    parse_scopes,
    require_paid_entitlement,
    utcnow_iso,
)
from .tenant_runtime import tenant_skill_dir, user_has_account_session

LOG = logging.getLogger(__name__)

router = APIRouter()

ONBOARDING_TARGET_MINUTES = 20
DEFAULT_AUTOMATION_OPT_IN = False
DEFAULT_UI_MODE = "standard"
DEFAULT_PROFILE = "balanced"
_TWO_FA_STATE_KEY = "security_2fa"
SKILL_DIR = Path(__file__).resolve().parent.parent
VALIDATION_ARTIFACT_DIR = SKILL_DIR / "validation_artifacts"


def _db() -> OrmSession:
    from .db import SessionLocal

    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _json_default(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _ok(data: Any = None) -> ApiResponse:
    return _shared_ok(data)


def _err(message: str, data: Any = None) -> ApiResponse:
    return _shared_simple_err(message, data)


def _saas_error_response(exc: Exception, *, source: str, fallback: str) -> ApiResponse:
    return _shared_saas_error_response(exc, source=source, fallback=fallback)


def _save_state(db: OrmSession, user_id: str, key: str, payload: dict[str, Any]) -> None:
    row = db.query(AppState).filter(AppState.user_id == user_id, AppState.key == key).first()
    if not row:
        row = AppState(user_id=user_id, key=key, value_json=json.dumps(payload, default=_json_default))
        db.add(row)
    else:
        row.value_json = json.dumps(payload, default=_json_default)
    db.commit()


def _load_state(db: OrmSession, user_id: str, key: str, default: dict[str, Any]) -> dict[str, Any]:
    row = db.query(AppState).filter(AppState.user_id == user_id, AppState.key == key).first()
    if not row:
        return default
    parsed = parse_json(row.value_json, default)
    return parsed if isinstance(parsed, dict) else default


def _latest_validation_status() -> dict[str, Any]:
    status_file = VALIDATION_ARTIFACT_DIR / "continuous_validation_status.json"
    if status_file.exists():
        try:
            data = json.loads(status_file.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                latest_artifacts = data.get("latest_artifacts") or {}
                return {
                    "source": "continuous_validation_status",
                    "exists": True,
                    "run_status": data.get("run_status"),
                    "passed": bool(data.get("passed")) if data.get("passed") is not None else None,
                    "started_at": data.get("started_at"),
                    "finished_at": data.get("finished_at"),
                    "generated_at": data.get("generated_at"),
                    "current_step": data.get("current_step"),
                    "current_step_index": data.get("current_step_index"),
                    "completed_steps": data.get("completed_steps"),
                    "total_steps": data.get("total_steps"),
                    "progress_pct": data.get("progress_pct"),
                    "failed_steps": list(data.get("failed_steps") or []),
                    "latest_artifacts": latest_artifacts if isinstance(latest_artifacts, dict) else {},
                }
        except Exception:
            pass

    validate_runs = sorted(VALIDATION_ARTIFACT_DIR.glob("validate_all_*.json"))
    if not validate_runs:
        return {
            "source": "none",
            "exists": False,
            "run_status": "idle",
            "passed": None,
            "started_at": None,
            "finished_at": None,
            "generated_at": None,
            "current_step": None,
            "current_step_index": 0,
            "completed_steps": 0,
            "total_steps": 0,
            "progress_pct": 0,
            "failed_steps": [],
            "latest_artifacts": {},
        }
    latest = validate_runs[-1]
    try:
        payload = json.loads(latest.read_text(encoding="utf-8"))
    except Exception:
        payload = {}
    failed_steps = list(payload.get("failed_steps") or [])
    generated_at = payload.get("generated_at")
    if not generated_at:
        try:
            generated_at = datetime.fromtimestamp(latest.stat().st_mtime, tz=timezone.utc).isoformat()
        except Exception:
            generated_at = None
    try:
        rel_path = str(latest.relative_to(SKILL_DIR))
    except ValueError:
        rel_path = str(latest)
    return {
        "source": "validate_all_summary",
        "exists": True,
        "run_status": "completed",
        "passed": bool(payload.get("passed")) if "passed" in payload else None,
        "started_at": None,
        "finished_at": generated_at,
        "generated_at": generated_at,
        "current_step": None,
        "current_step_index": 0,
        "completed_steps": 0,
        "total_steps": 0,
        "progress_pct": 100,
        "failed_steps": failed_steps,
        "latest_artifacts": {"validate_all": rel_path},
    }


def _safe_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _build_trade_outcome_payload(
    *,
    user_id: str,
    ticker: str,
    side: str,
    qty: int,
    price: float | None,
    result: dict[str, Any] | None,
    signal: dict[str, Any] | None = None,
) -> dict[str, Any]:
    safe_result = result if isinstance(result, dict) else {}
    safe_signal = signal if isinstance(signal, dict) else {}
    fill = (
        safe_result.get("fill_price")
        or safe_result.get("average_price")
        or safe_result.get("avg_fill_price")
        or price
    )
    return {
        "source": "saas_trade_approval",
        "user_id": user_id,
        "order_id": str(safe_result.get("orderId") or safe_result.get("order_id") or ""),
        "ticker": ticker.upper(),
        "side": side.upper(),
        "qty": int(qty),
        "fill_price": _safe_float(fill),
        "date": datetime.now(timezone.utc).date().isoformat(),
        "return_pct": safe_result.get("return_pct"),
        "pnl_pct": safe_result.get("pnl_pct"),
        "mirofish_conviction": safe_signal.get("mirofish_conviction"),
        "sector_etf": safe_signal.get("sector_etf"),
        "captured_at": datetime.now(timezone.utc).isoformat(),
    }


def _learning_outcomes_for_user(db: OrmSession, user_id: str) -> list[dict[str, Any]]:
    outcomes = load_trade_outcomes(db, user_id)
    if outcomes:
        return outcomes

    rows = (
        db.query(Order)
        .filter(Order.user_id == user_id, Order.status == "executed")
        .order_by(Order.created_at.asc())
        .limit(500)
        .all()
    )
    out: list[dict[str, Any]] = []
    for row in rows:
        payload = parse_json(row.result_json, {})
        payload = payload if isinstance(payload, dict) else {}
        out.append(
            {
                "source": "saas_order_table_fallback",
                "order_id": str(payload.get("orderId") or payload.get("order_id") or row.id),
                "ticker": str(row.ticker or "").upper(),
                "side": str(row.side or "BUY").upper(),
                "qty": int(row.qty or 0),
                "fill_price": _safe_float(
                    payload.get("fill_price")
                    or payload.get("average_price")
                    or payload.get("avg_fill_price")
                    or row.price
                ),
                "date": (row.created_at.isoformat()[:10] if row.created_at else datetime.now(timezone.utc).date().isoformat()),
                "return_pct": payload.get("return_pct"),
                "pnl_pct": payload.get("pnl_pct"),
            }
        )
    return out


def _challenger_win_rate(history: list[dict[str, Any]]) -> dict[str, Any]:
    if not history:
        return {"total_runs": 0}
    verdicts = [h.get("verdict") for h in history if isinstance(h, dict)]
    total = len(verdicts)
    if total <= 0:
        return {"total_runs": 0}
    return {
        "total_runs": total,
        "challenger_wins": verdicts.count("challenger_better"),
        "champion_wins": verdicts.count("champion_better"),
        "ties": verdicts.count("tie"),
        "challenger_win_rate_pct": round((verdicts.count("challenger_better") / total) * 100, 1),
        "avg_score_delta": round(
            sum(float(h.get("score_delta", 0) or 0) for h in history if isinstance(h, dict)) / total,
            2,
        ),
    }


def _challenger_summary(db: OrmSession, user_id: str) -> dict[str, Any]:
    history = load_challenger_history(db, user_id)
    update = load_strategy_update(db, user_id)
    return {
        "available": True,
        "latest": history[-1] if history else None,
        "win_rate": _challenger_win_rate(history),
        "can_run": bool(update and update.get("env_overrides")),
    }


def _apply_profile_to_runtime(profile: str) -> dict[str, str]:
    return _shared_apply_profile_to_runtime(profile)


def _saas_pretrade_checklist(trade: PendingTrade, signal: dict[str, Any]) -> dict[str, Any]:
    max_trades = int(os.getenv("MAX_TRADES_PER_DAY", "20") or 20)
    max_total_account = float(os.getenv("MAX_TOTAL_ACCOUNT_VALUE", "500000") or 500000)
    est_value = float((trade.price or 0) * (trade.qty or 0))
    est_risk_pct = (
        round((est_value / max_total_account) * 100.0, 2) if max_total_account > 0 and est_value > 0 else None
    )
    high_value_threshold = _high_value_2fa_threshold_usd()
    event_risk = signal.get("event_risk") if isinstance(signal, dict) else {}
    regime = signal.get("regime_v2") if isinstance(signal, dict) else {}
    blocked: list[str] = []
    if _global_live_trading_kill_switch_on():
        blocked.append("platform_kill_switch")
    if isinstance(event_risk, dict) and event_risk.get("mode") == "live" and event_risk.get("flagged") and event_risk.get("action") == "block":
        blocked.append("event_risk_block")
    if isinstance(regime, dict) and str(regime.get("mode", "off")) == "live":
        score = float(regime.get("score", 100) or 100)
        gate = float(os.getenv("REGIME_V2_ENTRY_MIN_SCORE", "55") or 55)
        if score < gate:
            blocked.append("regime_v2_block")

    return with_plain_language(
        {
            "risk_percent_estimate": est_risk_pct,
            "estimated_notional_usd": round(est_value, 2),
            "high_value_2fa_threshold_usd": high_value_threshold,
            "requires_high_value_2fa": bool(est_value >= high_value_threshold if high_value_threshold > 0 else False),
            "daily_loss_limit_usd": _daily_loss_limit_usd(),
            "max_daily_trades": max_trades,
            "live_trades_today": 0,
            "shadow_trades_today": 0,
            "event_risk": event_risk if isinstance(event_risk, dict) else {},
            "regime_status": regime if isinstance(regime, dict) else {},
            "blocked": bool(blocked),
            "block_reasons": blocked,
            "requires_explicit_approval": True,
        }
    )


def _tenant_api_health_snapshot(db: OrmSession, user_id: str) -> dict[str, Any]:
    linked = user_has_account_session(db, user_id)
    market_ok = account_ok = quote_ok = False
    qmeta: dict[str, Any] = {}
    if linked:
        try:
            with tenant_skill_dir(db, user_id) as skill_dir:
                with DualSchwabAuth(skill_dir=skill_dir, auto_refresh=False) as auth:
                    market_ok = bool(auth.get_market_token())
                    account_ok = bool(auth.get_account_token())
                    quote, qmeta = get_current_quote_with_status("AAPL", auth=auth, skill_dir=skill_dir)
                    quote_ok = extract_schwab_last_price(quote) is not None
        except Exception as exc:
            err_msg = safe_exception_message(exc, fallback="token_probe_failed")[:200]
            return {
                "schwab_linked": True,
                "market_token_ok": False,
                "account_token_ok": False,
                "quote_ok": False,
                "error": err_msg,
                "quote_health": {
                    "symbol": "AAPL",
                    "ok": False,
                    "reason": err_msg,
                    "operator_hint": None,
                },
            }
    qh: dict[str, Any] = {
        "symbol": "AAPL",
        "ok": quote_ok,
        "reason": None if quote_ok else (qmeta.get("reason") if qmeta else "not_linked_or_probe_failed"),
        "operator_hint": _quote_health_hint(qmeta, quote_ok) if qmeta else None,
    }
    return {
        "schwab_linked": linked,
        "market_token_ok": market_ok,
        "account_token_ok": account_ok,
        "quote_ok": quote_ok,
        "quote_health": qh,
    }


def _request_id(request: Request) -> str | None:
    return _shared_request_id(request)


def _is_loopback_host(hostname: str) -> bool:
    return _shared_is_loopback_host(hostname)


def _request_origin(request: Request) -> str:
    return _shared_request_origin(request)


def _resolve_schwab_redirect_uri(request: Request, *, market: bool) -> str:
    return _shared_resolve_schwab_redirect_uri(request, market=market)


def _global_live_trading_kill_switch_on() -> bool:
    return (os.getenv("LIVE_TRADING_KILL_SWITCH") or "").strip().lower() in ("1", "true", "yes", "on")


def _daily_loss_limit_usd() -> float:
    raw = (os.getenv("DAILY_LOSS_LIMIT_USD") or "1500").strip()
    try:
        return max(0.0, float(raw))
    except ValueError:
        return 1500.0


def _high_value_2fa_threshold_usd() -> float:
    raw = (os.getenv("HIGH_VALUE_2FA_THRESHOLD_USD") or "10000").strip()
    try:
        return max(0.0, float(raw))
    except ValueError:
        return 10000.0


def _totp_step_seconds() -> int:
    raw = (os.getenv("TWO_FA_TOTP_STEP_SECONDS") or "30").strip()
    try:
        return max(15, min(120, int(raw)))
    except ValueError:
        return 30


def _totp_digits() -> int:
    raw = (os.getenv("TWO_FA_TOTP_DIGITS") or "6").strip()
    try:
        return 8 if int(raw) >= 7 else 6
    except ValueError:
        return 6


def _totp_drift_windows() -> int:
    raw = (os.getenv("TWO_FA_TOTP_DRIFT_WINDOWS") or "1").strip()
    try:
        return max(0, min(3, int(raw)))
    except ValueError:
        return 1


def _normalize_base32_secret(secret: str) -> str:
    cleaned = "".join(ch for ch in (secret or "").upper() if ch.isalnum())
    if not cleaned:
        raise ValueError("Missing TOTP secret.")
    pad = "=" * ((8 - (len(cleaned) % 8)) % 8)
    base64.b32decode(cleaned + pad, casefold=True)
    return cleaned


def _generate_totp_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode("utf-8").rstrip("=")


def _totp_code(secret_b32: str, counter: int, digits: int) -> str:
    normalized = _normalize_base32_secret(secret_b32)
    pad = "=" * ((8 - (len(normalized) % 8)) % 8)
    key = base64.b32decode(normalized + pad, casefold=True)
    msg = struct.pack(">Q", int(counter))
    digest = hmac.new(key, msg, hashlib.sha1).digest()
    off = digest[-1] & 0x0F
    dbc = struct.unpack(">I", digest[off : off + 4])[0] & 0x7FFFFFFF
    mod = 10**digits
    return str(dbc % mod).zfill(digits)


def _verify_totp_code(secret_b32: str, code: str) -> bool:
    normalized_code = "".join(ch for ch in str(code or "") if ch.isdigit())
    if len(normalized_code) not in (6, 8):
        return False
    step = _totp_step_seconds()
    digits = _totp_digits()
    window = _totp_drift_windows()
    now_counter = int(time.time() // step)
    for delta in range(-window, window + 1):
        candidate = _totp_code(secret_b32, now_counter + delta, digits)
        if hmac.compare_digest(candidate, normalized_code.zfill(digits)):
            return True
    return False


def _two_fa_state(db: OrmSession, user_id: str) -> dict[str, Any]:
    return _load_state(
        db,
        user_id,
        _TWO_FA_STATE_KEY,
        default={
            "enabled": False,
            "totp_secret_enc": None,
            "enabled_at": None,
            "pending_secret_enc": None,
            "pending_created_at": None,
        },
    )


def _save_two_fa_state(db: OrmSession, user_id: str, state: dict[str, Any]) -> None:
    _save_state(db, user_id, _TWO_FA_STATE_KEY, state)


def _sum_intraday_pnl(account_status: dict[str, Any]) -> float:
    total = 0.0
    for acc in account_status.get("accounts", []):
        sec = acc.get("securitiesAccount", acc)
        for pos in sec.get("positions", []):
            try:
                total += float(pos.get("currentDayProfitLoss", 0) or 0)
            except Exception:
                continue
    return round(total, 2)


def _build_report_verdicts(report: dict[str, Any]) -> dict[str, Any]:
    technical = report.get("technical") or {}
    dcf = report.get("dcf") or {}
    health = report.get("health") or {}
    miro = report.get("mirofish") or {}
    signal_score = float(technical.get("signal_score", 0) or 0)
    mos = float(dcf.get("margin_of_safety", 0) or 0)
    health_flags = health.get("flags") or []
    conviction = float(miro.get("conviction_score", 0) or 0)

    def bucket(score: float, high: float, low: float) -> str:
        if score >= high:
            return "bullish"
        if score <= low:
            return "bearish"
        return "neutral"

    return {
        "technical": {
            "verdict": bucket(signal_score, 65.0, 45.0),
            "takeaway": "Trend setup aligned."
            if technical.get("stage_2") and technical.get("vcp")
            else "Setup quality is mixed.",
        },
        "dcf": {
            "verdict": bucket(mos, 10.0, -10.0),
            "takeaway": "Valuation supports upside." if mos >= 0 else "Valuation indicates premium pricing.",
        },
        "health": {
            "verdict": "bullish"
            if len(health_flags) == 0
            else ("bearish" if len(health_flags) >= 3 else "neutral"),
            "takeaway": "Balance sheet and margins are stable."
            if len(health_flags) == 0
            else "Review flagged financial risks.",
        },
        "mirofish": {
            "verdict": bucket(conviction, 30.0, -30.0),
            "takeaway": (miro.get("summary") or "No sentiment synthesis available.")[:220],
        },
    }


def _sec_analysis_settings_sd(skill_dir: Path) -> dict[str, Any]:
    from config import (
        get_edgar_user_agent,
        get_sec_filing_analysis_enabled,
        get_sec_filing_cache_hours,
        get_sec_filing_compare_enabled,
        get_sec_filing_llm_summary_enabled,
        get_sec_filing_max_chars,
        get_sec_filing_max_compare_items,
    )

    return {
        "analysis_enabled": bool(get_sec_filing_analysis_enabled(skill_dir)),
        "compare_enabled": bool(get_sec_filing_compare_enabled(skill_dir)),
        "user_agent": get_edgar_user_agent(skill_dir),
        "cache_hours": float(get_sec_filing_cache_hours(skill_dir)),
        "max_chars": int(get_sec_filing_max_chars(skill_dir)),
        "max_compare_items": int(get_sec_filing_max_compare_items(skill_dir)),
        "llm_enabled": bool(get_sec_filing_llm_summary_enabled(skill_dir)),
    }


def _normalize_sec_analysis_payload_sd(payload: dict[str, Any], *, analysis_mode: str = "full_text") -> dict[str, Any]:
    data = dict(payload or {})
    confidence = int(data.get("confidence", 0) or 0)
    why = list(data.get("why") or [])
    limits = list(data.get("limits") or [])
    evidence = list(data.get("evidence") or [])
    summary_headline = str(data.get("summary_headline") or "").strip()
    if not summary_headline:
        verdict = str(data.get("verdict") or "neutral")
        summary_headline = (
            f"{data.get('ticker', '')} {data.get('form', '')} filing reads {verdict} "
            f"with confidence {confidence}/100."
        ).strip()
    narrative_summary = str(data.get("narrative_summary") or "").strip()
    if not narrative_summary:
        narrative_summary = " ".join(why[:2]).strip() or str(data.get("high_level_takeaway") or "").strip()
    data["summary_headline"] = summary_headline
    data["narrative_summary"] = narrative_summary
    data["confidence"] = confidence
    data["limits"] = limits
    data["evidence"] = evidence
    data["analysis_mode"] = analysis_mode
    data["data_freshness"] = {
        "from_cache": bool(data.get("from_cache", False)),
        "source": str(data.get("source") or ""),
    }
    return data


def _normalize_sec_compare_payload_sd(payload: dict[str, Any], *, analysis_mode: str = "full_text") -> dict[str, Any]:
    data = dict(payload or {})
    compare_data = dict(data.get("compare") or {})
    similarities = compare_data.get("similarities") or []
    differences = compare_data.get("differences") or []
    investor_takeaway = str(compare_data.get("investor_takeaway") or "").strip()
    compare_data.setdefault(
        "summary_headline",
        "SEC compare completed with meaningful differences." if differences else "SEC compare completed with broad alignment.",
    )
    compare_data.setdefault(
        "narrative_summary",
        (
            f"{investor_takeaway} "
            f"Shared signal: {(similarities[0] if similarities else 'limited overlap noted.')} "
            f"Key difference: {(differences[0] if differences else 'no major contrast highlighted.')}."
        ).strip(),
    )
    compare_data.setdefault("top_differences", differences[:3])
    compare_data.setdefault("top_commonalities", similarities[:3])
    if "change_summary" not in compare_data:
        compare_data["change_summary"] = {
            "new_risks": [],
            "resolved_risks": [],
            "guidance_shift": "unchanged",
            "evidence_ranked": [],
            "plain_english_rationale": [],
        }
    compare_data["analysis_mode"] = analysis_mode
    compare_data.setdefault("compare_confidence", 0)
    compare_data.setdefault("limits", [])
    compare_data.setdefault("evidence", compare_data.get("change_summary", {}).get("evidence_ranked", []))
    left = data.get("left") or data.get("latest") or {}
    right = data.get("right") or data.get("prior") or {}
    compare_data["data_freshness"] = {
        "left_from_cache": bool((left or {}).get("from_cache", False)),
        "right_from_cache": bool((right or {}).get("from_cache", False)),
        "left_source": str((left or {}).get("source") or ""),
        "right_source": str((right or {}).get("source") or ""),
    }
    data["compare"] = compare_data
    return data


def _source_entry_sd(
    name: str,
    *,
    status: str,
    detail: str = "",
    as_of: str | None = None,
    fallback_used: bool = False,
) -> dict[str, Any]:
    return {
        "name": name,
        "status": status,
        "detail": detail,
        "as_of": as_of,
        "fallback_used": fallback_used,
    }


def _pick_catalysts_and_risks_sd(finnhub: dict[str, Any]) -> dict[str, list[str]]:
    news_rows = finnhub.get("news") if isinstance(finnhub, dict) else []
    earnings_rows = finnhub.get("earnings") if isinstance(finnhub, dict) else []
    trends = finnhub.get("recommendation_trends") if isinstance(finnhub, dict) else {}
    catalysts: list[str] = []
    risks: list[str] = []

    if isinstance(trends, dict):
        buy = int(trends.get("buy", 0) or 0) + int(trends.get("strong_buy", 0) or 0)
        sell = int(trends.get("sell", 0) or 0) + int(trends.get("strong_sell", 0) or 0)
        if buy > sell:
            catalysts.append(f"Analyst trend skew is constructive ({buy} buy vs {sell} sell votes).")
        elif sell > buy:
            risks.append(f"Analyst trend skew is cautious ({sell} sell vs {buy} buy votes).")

    if isinstance(earnings_rows, list):
        for row in earnings_rows[:3]:
            if not isinstance(row, dict):
                continue
            surprise_pct = _safe_float(row.get("surprise_percent"))
            period = str(row.get("period") or "").strip()
            if surprise_pct is None:
                continue
            if surprise_pct >= 5:
                catalysts.append(f"Earnings surprise +{surprise_pct:.1f}% ({period or 'recent'}).")
            elif surprise_pct <= -5:
                risks.append(f"Earnings miss {surprise_pct:.1f}% ({period or 'recent'}).")

    if isinstance(news_rows, list):
        for row in news_rows[:5]:
            if not isinstance(row, dict):
                continue
            headline = str(row.get("headline") or "").strip()
            if not headline:
                continue
            low = headline.lower()
            if any(tok in low for tok in ("upgrade", "contract", "beat", "launch", "partnership")):
                catalysts.append(headline)
            if any(tok in low for tok in ("downgrade", "investigation", "lawsuit", "miss", "delay", "cut")):
                risks.append(headline)

    return {
        "catalysts": catalysts[:6],
        "risks": risks[:6],
    }


def _compose_research_dossier_sd(
    ticker: str,
    *,
    skill_dir: Path,
) -> dict[str, Any]:
    symbol = ticker.upper().strip()
    generated_at = datetime.now(timezone.utc).isoformat()
    source_metadata: list[dict[str, Any]] = []

    with DualSchwabAuth(skill_dir=skill_dir, auto_refresh=False) as auth:
        report = generate_full_report(
            ticker=symbol,
            skip_mirofish=False,
            skip_edgar=False,
            auth=auth,
            skill_dir=skill_dir,
        )
    report_data = json.loads(report_to_json(report))
    section_verdicts = _build_report_verdicts(report_data)
    source_metadata.append(
        _source_entry_sd(
            "report_stack",
            status="ok",
            detail="technical, dcf, comps, health, edgar, mirofish",
            as_of=str(report_data.get("generated_at") or generated_at),
        )
    )

    sec_cfg = _sec_analysis_settings_sd(skill_dir)
    sec_analysis: dict[str, Any] = {}
    if sec_cfg["analysis_enabled"]:
        sec_out = analyze_latest_filing_for_ticker(
            ticker=symbol,
            form_type="10-K",
            user_agent=sec_cfg["user_agent"],
            skill_dir=skill_dir,
            cache_hours=sec_cfg["cache_hours"],
            max_chars=sec_cfg["max_chars"],
            enable_llm=sec_cfg["llm_enabled"],
        )
        if sec_out.get("ok"):
            sec_analysis = _normalize_sec_analysis_payload_sd(sec_out)
            source_metadata.append(_source_entry_sd("sec_analyze", status="ok", detail="latest filing narrative"))
        else:
            sec_analysis = {"ok": False, "error": str(sec_out.get("error") or "SEC analysis unavailable")}
            source_metadata.append(
                _source_entry_sd("sec_analyze", status="degraded", detail=str(sec_out.get("error") or "analysis failed"), fallback_used=True)
            )
    else:
        sec_analysis = {"ok": False, "error": "SEC filing analysis disabled by config"}
        source_metadata.append(_source_entry_sd("sec_analyze", status="disabled", detail="analysis disabled"))

    sec_compare_data: dict[str, Any] = {}
    if sec_cfg["analysis_enabled"] and sec_cfg["compare_enabled"]:
        sec_compare_out = compare_ticker_over_time(
            symbol,
            form_type="10-K",
            user_agent=sec_cfg["user_agent"],
            skill_dir=skill_dir,
            cache_hours=sec_cfg["cache_hours"],
            max_chars=sec_cfg["max_chars"],
            enable_llm=sec_cfg["llm_enabled"],
            highlight_changes_only=False,
        )
        if sec_compare_out.get("ok"):
            sec_compare_data = _normalize_sec_compare_payload_sd(sec_compare_out)
            source_metadata.append(_source_entry_sd("sec_compare", status="ok", detail="over-time 10-K compare"))
        else:
            sec_compare_data = {"ok": False, "error": str(sec_compare_out.get("error") or "SEC compare unavailable")}
            source_metadata.append(
                _source_entry_sd("sec_compare", status="degraded", detail=str(sec_compare_out.get("error") or "compare failed"), fallback_used=True)
            )
    else:
        sec_compare_data = {"ok": False, "error": "SEC compare disabled by config"}
        source_metadata.append(_source_entry_sd("sec_compare", status="disabled", detail="compare disabled"))

    portfolio_summary: dict[str, Any] = {}
    portfolio_risk: dict[str, Any] = {}
    try:
        status_data = get_account_status(skill_dir=skill_dir)
        if isinstance(status_data, dict):
            portfolio_summary = _build_portfolio_summary(status_data)
            portfolio_risk = _build_portfolio_risk_analytics(portfolio_summary, skill_dir=skill_dir)
            source_metadata.append(_source_entry_sd("portfolio", status="ok", detail="positions and risk context"))
        else:
            source_metadata.append(_source_entry_sd("portfolio", status="degraded", detail="account status unavailable", fallback_used=True))
    except Exception as exc:  # noqa: BLE001
        portfolio_summary = {}
        portfolio_risk = {}
        source_metadata.append(_source_entry_sd("portfolio", status="degraded", detail=str(exc)[:180], fallback_used=True))

    sector_context: dict[str, Any]
    try:
        sector_context = get_sector_heatmap(skill_dir=skill_dir)
        source_metadata.append(_source_entry_sd("sector_context", status="ok", detail="relative sector heatmap"))
    except Exception as exc:  # noqa: BLE001
        sector_context = {"ok": False, "error": str(exc)}
        source_metadata.append(_source_entry_sd("sector_context", status="degraded", detail=str(exc)[:180], fallback_used=True))

    finnhub = get_finnhub_research_snapshot(symbol, skill_dir=skill_dir)
    finnhub_errors = list(finnhub.get("errors") or []) if isinstance(finnhub, dict) else []
    finnhub_status = "ok" if finnhub.get("ok") else ("disabled" if not finnhub.get("enabled") else "degraded")
    source_metadata.append(
        _source_entry_sd(
            "finnhub",
            status=finnhub_status,
            detail=", ".join(finnhub_errors) if finnhub_errors else "news, targets, recommendations, earnings, metrics",
            as_of=str(finnhub.get("as_of") or generated_at),
            fallback_used=finnhub_status != "ok",
        )
    )

    report_v2 = build_report_v2(report_data, portfolio_summary=portfolio_summary or None)
    signal_score = _safe_float((report_data.get("technical") or {}).get("signal_score")) or 0.0
    margin_of_safety = _safe_float((report_data.get("dcf") or {}).get("margin_of_safety")) or 0.0
    confidence_score = max(0.0, min(100.0, (signal_score * 0.7) + max(-20.0, min(20.0, margin_of_safety))))
    catalyst_risk = _pick_catalysts_and_risks_sd(finnhub if isinstance(finnhub, dict) else {})

    return {
        "ticker": symbol,
        "generated_at": generated_at,
        "executive_pitch": {
            "thesis": str((report_v2.get("thesis") or {}).get("claim") or f"{symbol} setup requires review of report stack and SEC context."),
            "recommendation": str((report_v2.get("ic_snapshot") or {}).get("recommendation") or "WATCH"),
            "confidence_label": str((report_v2.get("ic_snapshot") or {}).get("confidence_label") or "Moderate"),
            "confidence_score": round(confidence_score, 1),
            "time_horizon": str((report_v2.get("ic_snapshot") or {}).get("time_horizon") or "3-6 months"),
        },
        "sections": {
            "technical_valuation_fundamentals": {
                "report_v2": report_v2,
                "section_verdicts": section_verdicts,
                "raw_report": report_data,
            },
            "sec_narrative": {
                "analyze": sec_analysis,
                "compare": sec_compare_data,
            },
            "portfolio_and_sector_context": {
                "portfolio_summary": portfolio_summary,
                "portfolio_risk": portfolio_risk,
                "sector_heatmap": sector_context,
            },
            "finnhub_catalysts_risks": {
                "snapshot": finnhub,
                "catalysts": catalyst_risk["catalysts"],
                "risks": catalyst_risk["risks"],
            },
        },
        "source_metadata": source_metadata,
        "fallback_notes": [entry["detail"] for entry in source_metadata if entry.get("fallback_used") and entry.get("detail")],
    }


def _dossier_to_markdown_sd(dossier: dict[str, Any]) -> str:
    ticker = str(dossier.get("ticker") or "—")
    generated_at = str(dossier.get("generated_at") or "")
    pitch = dossier.get("executive_pitch") or {}
    sections = dossier.get("sections") or {}
    sec_narr = sections.get("sec_narrative") or {}
    portfolio = sections.get("portfolio_and_sector_context") or {}
    fin = sections.get("finnhub_catalysts_risks") or {}
    fundamentals = sections.get("technical_valuation_fundamentals") or {}
    report_v2 = fundamentals.get("report_v2") or {}
    raw_report = fundamentals.get("raw_report") or {}
    technical = raw_report.get("technical") or {}
    dcf = raw_report.get("dcf") or {}
    health = raw_report.get("health") or {}
    sec_analyze = sec_narr.get("analyze") or {}
    sec_compare = ((sec_narr.get("compare") or {}).get("compare") or {})
    quote = (fin.get("snapshot") or {}).get("quote") or {}
    pt = (fin.get("snapshot") or {}).get("price_target") or {}
    trends = (fin.get("snapshot") or {}).get("recommendation_trends") or {}
    catalysts = list(fin.get("catalysts") or [])
    risks = list(fin.get("risks") or [])
    source_rows = list(dossier.get("source_metadata") or [])
    fallback_notes = list(dossier.get("fallback_notes") or [])
    source_index = {str(row.get("name") or ""): i + 1 for i, row in enumerate(source_rows)}

    def _num(value: Any, digits: int = 2) -> str:
        v = _safe_float(value)
        if v is None:
            return "n/a"
        return f"{v:.{digits}f}"

    def _pct(value: Any, digits: int = 1) -> str:
        v = _safe_float(value)
        if v is None:
            return "n/a"
        return f"{v:.{digits}f}%"

    snapshot = fin.get("snapshot") or {}
    profile = snapshot.get("profile") or {}
    metrics = snapshot.get("metrics") or {}
    earnings = list(snapshot.get("earnings") or [])
    news = list(snapshot.get("news") or [])
    sec_summary = str(sec_analyze.get("narrative_summary") or sec_analyze.get("error") or "SEC analysis unavailable.")
    compare_summary = str(sec_compare.get("narrative_summary") or (sec_narr.get("compare") or {}).get("error") or "SEC compare unavailable.")
    hhi_label = (((portfolio.get("portfolio_risk") or {}).get("concentration") or {}).get("hhi_label") or "Unavailable")
    positions_count = (portfolio.get("portfolio_summary") or {}).get("positions_count", "n/a")
    total_mv = (portfolio.get("portfolio_summary") or {}).get("total_market_value", "n/a")
    recommendation = pitch.get("recommendation", "WATCH")
    confidence_label = pitch.get("confidence_label", "Moderate")
    confidence_score = pitch.get("confidence_score", "n/a")
    bull_votes = int(trends.get("buy", 0) or 0) + int(trends.get("strong_buy", 0) or 0)
    bear_votes = int(trends.get("sell", 0) or 0) + int(trends.get("strong_sell", 0) or 0)

    def _money(value: Any) -> str:
        v = _safe_float(value)
        if v is None:
            return "n/a"
        if abs(v) >= 1000:
            return f"${v/1000:.2f}B"
        return f"${v:.2f}M"

    def _ratio_pct(value: Any, digits: int = 1) -> str:
        v = _safe_float(value)
        if v is None:
            return "n/a"
        if abs(v) <= 1:
            v *= 100
        return f"{v:.{digits}f}%"

    def _cite(*names: str) -> str:
        ids = [source_index.get(name) for name in names if source_index.get(name)]
        if not ids:
            return ""
        return " " + "".join(f"[{idx}]" for idx in sorted(set(ids)))

    lines: list[str] = [
        f"# {ticker} — Institutional Research Report",
        "",
        "## Cover Page",
        "",
        f"Prepared: {generated_at} | Analyst: TradingBot Research Engine",
        f"Coverage: {profile.get('finnhub_industry') or 'Equity Research'} | Region: {profile.get('country') or 'Global'}",
        f"Document Type: Institutional Investment Note{_cite('report_stack', 'finnhub', 'sec_analyze')}",
        "",
        "---",
        "",
        f"Current Price: ${_num(quote.get('current'))} | 52-Week Range: ${_num(metrics.get('52week_low'))}–${_num(metrics.get('52week_high'))} | Consensus Target (Finnhub Mean): ${_num(pt.get('mean'))}",
        "",
        f"Recommendation: **{recommendation}** | Confidence: **{confidence_label} ({confidence_score}/100)** | Horizon: **{pitch.get('time_horizon', '3-6 months')}**",
        "",
        "Business Strategy & Operations · Fundamental Performance · Valuation · Risk & Catalyst Analysis",
        "",
        "## Executive Investment Summary",
        "",
        str(pitch.get("thesis") or "No thesis generated."),
        "",
        f"{ticker} currently screens with technical score {_num(technical.get('signal_score'), 0)}/100 and DCF margin of safety {_pct(dcf.get('margin_of_safety'))}. "
        f"Street positioning from Finnhub reads {bull_votes} bullish vs {bear_votes} bearish recommendation votes, while portfolio concentration context is **{hhi_label}**.{_cite('finnhub', 'report_stack', 'portfolio')}",
        "",
        "## Part I: Company and Business Model",
        "",
        f"- Issuer: {profile.get('name') or ticker} | Industry: {profile.get('finnhub_industry') or 'n/a'} | Exchange: {profile.get('exchange') or 'n/a'}",
        f"- Geography/Currency: {profile.get('country') or 'n/a'} / {profile.get('currency') or 'n/a'}",
        f"- Market Cap (Finnhub): {_money(profile.get('market_cap'))} | IPO: {profile.get('ipo') or 'n/a'}{_cite('finnhub')}",
        f"- Core thesis context: {(report_v2.get('thesis') or {}).get('claim') or 'Derived from integrated report stack.'}{_cite('report_stack')}",
        "",
        "## Part II: Fundamental Performance Analysis",
        "",
        "| Fundamental Metric | Value | Commentary |",
        "|---|---:|---|",
        f"| Revenue Growth (TTM YoY) | {_ratio_pct(metrics.get('revenue_growth_ttm_yoy'))} | Growth momentum from Finnhub metrics feed |",
        f"| EPS Growth (TTM YoY) | {_ratio_pct(metrics.get('eps_growth_ttm_yoy'))} | Earnings trajectory check |",
        f"| Operating Margin (TTM) | {_ratio_pct(metrics.get('operating_margin_ttm'))} | Operating efficiency trend |",
        f"| Net Margin (TTM) | {_ratio_pct(metrics.get('net_margin_ttm'))} | Bottom-line profitability quality |",
        f"| ROE / ROA (TTM) | {_ratio_pct(metrics.get('roe_ttm'))} / {_ratio_pct(metrics.get('roa_ttm'))} | Capital efficiency read-through |",
        f"| Current Ratio / Debt-Equity | {_num(metrics.get('current_ratio_quarterly'))} / {_num(metrics.get('debt_to_equity_quarterly'))} | Liquidity and leverage posture |",
        "",
        "### Earnings Quality (Recent Prints)",
        "",
            f"Earnings dispersion and surprise cadence remain central to near-term rerating potential and should be read with valuation compression/expansion risk in mind.{_cite('finnhub')}",
            "",
        "| Period | Actual EPS | Estimate EPS | Surprise % |",
        "|---|---:|---:|---:|",
    ]
    if earnings:
        for row in earnings[:6]:
            lines.append(
                f"| {row.get('period') or 'n/a'} | {_num(row.get('actual'))} | {_num(row.get('estimate'))} | {_pct(row.get('surprise_percent'))} |"
            )
    else:
        lines.append("| n/a | n/a | n/a | n/a |")

    lines.extend(
        [
            "",
            "## Part III: Valuation and Technical Positioning",
            "",
            "| Valuation / Technical | Value |",
            "|---|---:|",
            f"| DCF Margin of Safety | {_pct(dcf.get('margin_of_safety'))} |",
            f"| P/E (TTM) | {_num(metrics.get('pe_ttm'))} |",
            f"| P/B (Annual) | {_num(metrics.get('pb_annual'))} |",
            f"| P/S (TTM) | {_num(metrics.get('ps_ttm'))} |",
            f"| EV / EBITDA | {_num(metrics.get('ev_to_ebitda'))} |",
            f"| EV / Sales | {_num(metrics.get('ev_to_sales'))} |",
            f"| Technical Signal Score | {_num(technical.get('signal_score'), 0)} |",
            f"| Stage 2 / VCP | {bool(technical.get('stage_2'))} / {bool(technical.get('vcp'))} |",
            "",
            f"Technical structure implies {'constructive trend continuation' if technical.get('stage_2') else 'non-trending or transitional tape'} with sector monitor {technical.get('sector_etf') or 'n/a'}. "
            f"From a valuation perspective, margin-of-safety and multiple profile should be read together with SEC and catalyst evidence, not in isolation.{_cite('report_stack', 'finnhub', 'sec_analyze')}",
            "",
            "## Part IV: SEC Narrative and Comparative Filing Deltas",
            "",
            f"- Analyze Headline: {sec_analyze.get('summary_headline') or sec_analyze.get('error') or 'Unavailable'}{_cite('sec_analyze')}",
            f"- Analyze Narrative: {sec_summary}{_cite('sec_analyze')}",
            f"- Compare Headline: {sec_compare.get('summary_headline') or (sec_narr.get('compare') or {}).get('error') or 'Unavailable'}{_cite('sec_compare')}",
            f"- Compare Narrative: {compare_summary}{_cite('sec_compare')}",
            "",
            "## Part V: Portfolio Fit and Risk Budget Context",
            "",
            f"- Open positions: {positions_count}",
            f"- Total market value: {total_mv}",
            f"- Concentration label: {hhi_label}",
            f"- Risk budget impact: {(report_v2.get('portfolio_fit') or {}).get('risk_budget_impact') or 'Unavailable'}{_cite('portfolio', 'report_stack')}",
            "",
            "## Part VI: Catalyst and Risk Matrix",
            "",
            "| Type | Item |",
            "|---|---|",
        ]
    )
    if catalysts:
        lines.extend([f"| Catalyst | {item} |" for item in catalysts[:8]])
    else:
        lines.append("| Catalyst | No clear catalysts extracted from available feeds. |")
    if risks:
        lines.extend([f"| Risk | {item} |" for item in risks[:8]])
    else:
        lines.append("| Risk | No clear risks extracted from available feeds. |")

    lines.extend(["", "### Newsflow Digest (Finnhub)", ""])
    if news:
        for item in news[:8]:
            headline = str(item.get("headline") or "").strip()
            summary = str(item.get("summary") or "").strip()
            source = str(item.get("source") or "").strip()
            if headline:
                lines.append(f"- {headline} ({source or 'source n/a'})")
                if summary:
                    lines.append(f"  - {summary[:220]}")
    else:
        lines.append("- No recent Finnhub news items were returned.")

    lines.extend(["", "## Key Metrics at a Glance", ""])
    lines.extend(
        [
            "| Metric | Value | Source |",
            "|---|---:|---|",
            f"| Current Price | ${_num(quote.get('current'))} | Finnhub quote |",
            f"| 52-Week High | ${_num(metrics.get('52week_high'))} | Finnhub metrics |",
            f"| 52-Week Low | ${_num(metrics.get('52week_low'))} | Finnhub metrics |",
            f"| DCF Margin of Safety | {_pct(dcf.get('margin_of_safety'))} | Full report DCF |",
            f"| Health Flag Count | {len(health.get('flags') or [])} | Full report health |",
            f"| Portfolio Concentration | {hhi_label} | Portfolio risk analytics |",
        ]
    )

    lines.extend(["", "## References", ""])
    if source_rows:
        for idx, row in enumerate(source_rows, start=1):
            lines.append(
                f"{idx}. {row.get('name')}: {row.get('status')} | {row.get('detail') or ''} | as_of={row.get('as_of') or 'n/a'}"
            )
    else:
        lines.append("1. Integrated report stack sources were unavailable.")
    lines.extend(["", "## Limitations & Fallback Notes", ""])
    if fallback_notes:
        lines.extend([f"- {item}" for item in fallback_notes])
    else:
        lines.append("- None")
    lines.extend(
        [
            "",
            "## Disclaimer",
            "",
            "This report is generated for informational research workflows. It is not investment advice.",
        ]
    )
    lines.append("")
    return "\n".join(lines)


def _escape_pdf_text_sd(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _text_to_simple_pdf_sd(text: str) -> bytes:
    lines = [line[:105] for line in text.splitlines()]
    if not lines:
        lines = ["Research dossier export."]
    line_specs: list[tuple[str, int, int]] = []
    for line in lines:
        s = line.rstrip()
        if s.startswith("# "):
            line_specs.append((s[2:].strip(), 15, 24))
        elif s.startswith("## "):
            line_specs.append((s[3:].strip(), 13, 20))
        elif s.startswith("### "):
            line_specs.append((s[4:].strip(), 12, 18))
        else:
            line_specs.append((s, 10, 14))

    chunks: list[list[tuple[str, int, int]]] = []
    current: list[tuple[str, int, int]] = []
    y = 780
    min_y = 60
    for spec in line_specs:
        _, _size, leading = spec
        if y - leading < min_y and current:
            chunks.append(current)
            current = []
            y = 780
        current.append(spec)
        y -= leading
    if current:
        chunks.append(current)

    objs: list[bytes] = []
    objs.append(b"1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n")
    page_count = max(1, len(chunks))
    first_page_obj = 3
    first_font_obj = first_page_obj + (page_count * 2)
    kids = " ".join(f"{first_page_obj + (idx * 2)} 0 R" for idx in range(page_count))
    objs.append(f"2 0 obj << /Type /Pages /Kids [{kids}] /Count {page_count} >> endobj\n".encode("ascii"))

    for idx, chunk in enumerate(chunks):
        page_obj_num = first_page_obj + (idx * 2)
        content_obj_num = page_obj_num + 1
        stream_ops: list[str] = []
        y_pos = 780
        for line, size, leading in chunk:
            stream_ops.append("BT")
            stream_ops.append(f"/F1 {size} Tf")
            stream_ops.append(f"50 {y_pos} Td")
            stream_ops.append(f"({_escape_pdf_text_sd(line)}) Tj")
            stream_ops.append("ET")
            y_pos -= leading
        footer = f"-- {idx + 1} of {page_count} --"
        stream_ops.extend(
            [
                "BT",
                "/F1 9 Tf",
                "260 28 Td",
                f"({_escape_pdf_text_sd(footer)}) Tj",
                "ET",
            ]
        )
        stream = "\n".join(stream_ops).encode("latin-1", "replace")
        objs.append(
            (
                f"{page_obj_num} 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                f"/Resources << /Font << /F1 {first_font_obj} 0 R >> >> /Contents {content_obj_num} 0 R >> endobj\n"
            ).encode("ascii")
        )
        objs.append(
            f"{content_obj_num} 0 obj << /Length {len(stream)} >> stream\n".encode("ascii")
            + stream
            + b"\nendstream endobj\n"
        )

    objs.append(f"{first_font_obj} 0 obj << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> endobj\n".encode("ascii"))

    out = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for obj in objs:
        offsets.append(len(out))
        out.extend(obj)
    xref_start = len(out)
    out.extend(f"xref\n0 {len(objs) + 1}\n".encode("ascii"))
    out.extend(b"0000000000 65535 f \n")
    for off in offsets[1:]:
        out.extend(f"{off:010d} 00000 n \n".encode("ascii"))
    out.extend(
        (
            f"trailer << /Size {len(objs) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref_start}\n%%EOF\n"
        ).encode("ascii")
    )
    return bytes(out)


@router.get("/api/status", response_model=ApiResponse)
def tenant_status(user: User = Depends(get_current_user), db: OrmSession = Depends(_db)) -> ApiResponse:
    try:
        checked_at = datetime.now(timezone.utc).isoformat()
        snap = _tenant_api_health_snapshot(db, user.id)
        market_token_ok = bool(snap.get("market_token_ok"))
        account_token_ok = bool(snap.get("account_token_ok"))
        last_scan = _load_state(
            db,
            user.id,
            "last_scan",
            default={
                "at": None,
                "signals_found": None,
                "diagnostics": None,
                "diagnostics_summary": None,
                "strategy_summary": None,
            },
        )
        return _ok(
            {
                "market_token_ok": market_token_ok,
                "account_token_ok": account_token_ok,
                "market_state": "Connected" if market_token_ok else "Disconnected",
                "account_state": "Connected" if account_token_ok else "Disconnected",
                "checked_at": checked_at,
                "last_scan": last_scan,
                "validation_status": _latest_validation_status(),
                "connection_status": "connected" if snap.get("schwab_linked") else "disconnected",
                "api_health": snap,
            }
        )
    except Exception as exc:
        return _saas_error_response(exc, source="status", fallback="Unable to load tenant status.")


@router.get("/api/health/deep", response_model=ApiResponse)
def tenant_health_deep(user: User = Depends(get_current_user), db: OrmSession = Depends(_db)) -> ApiResponse:
    try:
        db_ok = True
        snap = _tenant_api_health_snapshot(db, user.id)
        market_token_ok = bool(snap.get("market_token_ok"))
        account_token_ok = bool(snap.get("account_token_ok"))
        quote_ok = bool(snap.get("quote_ok"))
        qh = snap.get("quote_health") or {
            "symbol": "AAPL",
            "ok": quote_ok,
            "reason": None if quote_ok else (snap.get("error") or "not_linked_or_probe_failed"),
            "operator_hint": None,
        }
        return _ok(
            {
                "db_ok": db_ok,
                "market_token_ok": market_token_ok,
                "account_token_ok": account_token_ok,
                "quote_ok": quote_ok,
                "quote_health": qh,
                "metrics": {"requests_total": 0, "errors_total": 0},
            }
        )
    except Exception as exc:
        return _saas_error_response(exc, source="health_deep", fallback="Deep health check is temporarily unavailable.")


@router.get("/api/recovery/map", response_model=ApiResponse)
def tenant_recovery_map(error: str, source: str = "unknown") -> ApiResponse:
    return _ok(map_failure(error, source=source))


@router.get("/api/portfolio", response_model=ApiResponse)
def tenant_portfolio(user: User = Depends(get_current_user), db: OrmSession = Depends(_db)) -> ApiResponse:
    if not user_has_account_session(db, user.id):
        return _err("Link Schwab account before loading portfolio.")
    try:
        with tenant_skill_dir(db, user.id) as skill_dir:
            status_data = get_account_status(skill_dir=skill_dir)
        if isinstance(status_data, str):
            return _err(status_data)
        return _ok(_build_portfolio_summary(status_data))
    except Exception as exc:
        return _saas_error_response(exc, source="portfolio", fallback="Unable to load portfolio right now.")


@router.get("/api/portfolio/risk", response_model=ApiResponse)
def tenant_portfolio_risk(user: User = Depends(get_current_user), db: OrmSession = Depends(_db)) -> ApiResponse:
    if not user_has_account_session(db, user.id):
        return _err("Link Schwab account before loading risk analytics.")
    try:
        with tenant_skill_dir(db, user.id) as skill_dir:
            status_data = get_account_status(skill_dir=skill_dir)
            if isinstance(status_data, str):
                return _err(status_data)
            summary = _build_portfolio_summary(status_data)
            return _ok(_build_portfolio_risk_analytics(summary, skill_dir=skill_dir))
    except Exception as exc:
        return _saas_error_response(exc, source="portfolio_risk", fallback="Unable to load portfolio risk right now.")


@router.get("/api/sectors", response_model=ApiResponse)
def tenant_sectors(user: User = Depends(get_current_user), db: OrmSession = Depends(_db)) -> ApiResponse:
    if not user_has_account_session(db, user.id):
        return _err("Link Schwab account before loading sectors.")
    try:
        with tenant_skill_dir(db, user.id) as skill_dir:
            with DualSchwabAuth(skill_dir=skill_dir, auto_refresh=False) as auth:
                heatmap = get_sector_heatmap(auth=auth, skill_dir=skill_dir)
        return _ok(heatmap)
    except Exception as exc:
        return _saas_error_response(exc, source="sectors", fallback="Unable to load sector heatmap right now.")


@router.get("/api/pending-trades", response_model=ApiResponse)
def tenant_list_pending(
    status: str | None = None,
    sort: str = "newest",
    user: User = Depends(get_current_user),
    db: OrmSession = Depends(_db),
) -> ApiResponse:
    rows_query = db.query(PendingTrade).filter(PendingTrade.user_id == user.id)
    if status and status.lower() != "all":
        rows_query = rows_query.filter(PendingTrade.status == status.lower().strip())
    if sort == "oldest":
        rows_query = rows_query.order_by(PendingTrade.created_at.asc())
    else:
        rows_query = rows_query.order_by(PendingTrade.created_at.desc())
    rows = rows_query.all()
    return _ok([_trade_to_dict(r) for r in rows])


@router.post("/api/pending-trades", response_model=ApiResponse)
def tenant_create_pending(
    payload: CreatePendingTrade,
    user: User = Depends(require_paid_entitlement),
    db: OrmSession = Depends(_db),
) -> ApiResponse:
    if not user_has_account_session(db, user.id):
        return _err("Link Schwab account before creating pending trades.")
    try:
        ticker = payload.ticker.upper().strip()
        signal = payload.signal or {}
        with tenant_skill_dir(db, user.id) as skill_dir:
            with DualSchwabAuth(skill_dir=skill_dir, auto_refresh=False) as auth:
                quote = get_current_quote(ticker, auth=auth, skill_dir=skill_dir)
                last_price = payload.price or extract_schwab_last_price(quote) or float(signal.get("price", 0) or 0)

                qty = payload.qty
                if qty is None:
                    usd_size = get_position_size_usd(
                        ticker=ticker,
                        price=last_price if last_price > 0 else None,
                        skill_dir=skill_dir,
                    )
                    qty = max(1, int(usd_size / last_price)) if last_price > 0 else 1

        trade_id = uuid.uuid4().hex[:8]
        row = PendingTrade(
            id=trade_id,
            user_id=user.id,
            ticker=ticker,
            qty=qty,
            price=last_price if last_price > 0 else None,
            status="pending",
            signal_json=json.dumps(signal, default=_json_default),
            note=payload.note,
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return _ok(_trade_to_dict(row))
    except Exception as exc:
        return _saas_error_response(exc, source="pending_trade_create", fallback="Unable to stage trade right now.")


@router.post("/api/pending-trades/clear-pending", response_model=ApiResponse)
def tenant_clear_all_pending(
    user: User = Depends(get_current_user),
    db: OrmSession = Depends(_db),
) -> ApiResponse:
    rows = db.query(PendingTrade).filter(PendingTrade.user_id == user.id, PendingTrade.status == "pending").all()
    for row in rows:
        row.status = "rejected"
    db.commit()
    return _ok({"cleared": len(rows)})


@router.post("/api/pending-trades/delete-all", response_model=ApiResponse)
def tenant_delete_all_pending(
    user: User = Depends(get_current_user),
    db: OrmSession = Depends(_db),
) -> ApiResponse:
    rows = db.query(PendingTrade).filter(PendingTrade.user_id == user.id).all()
    status_breakdown: dict[str, int] = {}
    for row in rows:
        status_breakdown[row.status] = status_breakdown.get(row.status, 0) + 1
        db.delete(row)
    db.commit()
    return _ok({"deleted": len(rows), "by_status": status_breakdown})


@router.get("/api/calibration/summary", response_model=ApiResponse)
def tenant_calibration_summary(
    user: User = Depends(get_current_user),
    db: OrmSession = Depends(_db),
) -> ApiResponse:
    row = (
        db.query(AppState)
        .filter(AppState.user_id == user.id, AppState.key == "calibration_snapshot")
        .first()
    )
    if not row:
        return _ok(
            {
                "empty": True,
                "hint": "Populated when a scan finds .self_study.json or .hypothesis_ledger.json in the worker session. "
                "Set HYPOTHESIS_LEDGER_ENABLED on API/workers to forward into tenant env.",
            }
        )
    data = parse_json(row.value_json, {})
    return _ok(data if isinstance(data, dict) else {"raw": data})


@router.post("/api/evolve/run", response_model=ApiResponse)
def tenant_evolve_run(
    user: User = Depends(require_paid_entitlement),
    db: OrmSession = Depends(_db),
) -> ApiResponse:
    outcomes = _learning_outcomes_for_user(db, user.id)
    if not outcomes:
        payload = {"status": "no_outcomes", "message": "No persisted trade outcomes found for this tenant."}
        save_learning_last_run(
            db, user.id, component="evolve", status=payload["status"], message=payload["message"], data=payload
        )
        return _ok(payload)
    try:
        with tenant_skill_dir(db, user.id) as skill_dir:
            engine = LearningEngine(
                skill_dir=skill_dir,
                outcomes_records=outcomes,
                write_strategy_file=False,
            )
            result = engine.run(apply=False)
        if result.get("status") == "ok":
            strategy_update = result.get("strategy_update")
            if isinstance(strategy_update, dict) and strategy_update.get("env_overrides"):
                save_strategy_update(db, user.id, strategy_update)
        save_learning_last_run(
            db,
            user.id,
            component="evolve",
            status=str(result.get("status") or "unknown"),
            message=str(result.get("message") or ""),
            data=result if isinstance(result, dict) else {},
        )
        return _ok(result)
    except Exception as exc:
        message = safe_exception_message(exc, fallback="Learning run failed.")
        save_learning_last_run(db, user.id, component="evolve", status="failed", message=message, data={})
        return _saas_error_response(exc, source="learning_evolve", fallback="Post-mortem analysis failed.")


@router.get("/api/challenger/latest", response_model=ApiResponse)
def tenant_challenger_latest(
    user: User = Depends(get_current_user),
    db: OrmSession = Depends(_db),
) -> ApiResponse:
    history = load_challenger_history(db, user.id)
    if not history:
        return _ok({"status": "no_data", "message": "No challenger runs yet."})
    return _ok(history[-1])


@router.get("/api/challenger/history", response_model=ApiResponse)
def tenant_challenger_history(
    n: int = 10,
    user: User = Depends(get_current_user),
    db: OrmSession = Depends(_db),
) -> ApiResponse:
    history = load_challenger_history(db, user.id)
    tail = history[-max(1, int(n)) :]
    return _ok({"history": tail, "win_rate": _challenger_win_rate(history)})


@router.post("/api/challenger/run", response_model=ApiResponse)
def tenant_challenger_run(
    user: User = Depends(require_paid_entitlement),
    db: OrmSession = Depends(_db),
) -> ApiResponse:
    update = load_strategy_update(db, user.id)
    if not update:
        payload = {
            "status": "no_update",
            "message": "No learning strategy update found. Run /api/evolve/run first.",
        }
        save_learning_last_run(
            db, user.id, component="challenger", status=payload["status"], message=payload["message"], data=payload
        )
        return _ok(payload)
    try:
        with tenant_skill_dir(db, user.id) as skill_dir:
            runner = ChallengerRunner(
                skill_dir=skill_dir,
                strategy_update_data=update,
                history_loader=lambda: load_challenger_history(db, user.id),
                history_saver=lambda comp: append_challenger_result(db, user.id, comp),
            )
            result = runner.run()
        save_learning_last_run(
            db,
            user.id,
            component="challenger",
            status=str(result.get("status") or "unknown"),
            message=str(result.get("message") or ""),
            data=result if isinstance(result, dict) else {},
        )
        return _ok(result)
    except Exception as exc:
        message = safe_exception_message(exc, fallback="Challenger run failed.")
        save_learning_last_run(db, user.id, component="challenger", status="failed", message=message, data={})
        return _saas_error_response(exc, source="challenger", fallback="Challenger scan failed.")


@router.get("/api/scan/status", response_model=ApiResponse)
def tenant_scan_status(user: User = Depends(get_current_user), db: OrmSession = Depends(_db)) -> ApiResponse:
    """SaaS has no in-process scan worker; expose last_scan so the dashboard refresh path matches local shape."""
    last_scan = _load_state(
        db,
        user.id,
        "last_scan",
        default={
            "at": None,
            "signals_found": None,
            "job_id": None,
            "diagnostics": None,
            "diagnostics_summary": None,
            "strategy_summary": None,
        },
    )
    return _ok({"status": "idle", "last_scan": last_scan})


@router.get("/api/decision-card/{ticker}", response_model=ApiResponse)
def tenant_decision_card(
    ticker: str,
    user: User = Depends(get_current_user),
    db: OrmSession = Depends(_db),
) -> ApiResponse:
    symbol = ticker.upper().strip()
    row = (
        db.query(ScanResult)
        .filter(ScanResult.user_id == user.id, ScanResult.ticker == symbol)
        .order_by(ScanResult.created_at.desc())
        .first()
    )
    if not row:
        return ApiResponse(ok=False, error=f"{symbol} is not in recent scan results. Run scan first.")
    signal = parse_json(row.payload_json, {})
    if not isinstance(signal, dict) or not signal:
        return ApiResponse(ok=False, error=f"{symbol} scan payload is unavailable.")

    price = float(signal.get("price", 0) or 0)
    with tenant_skill_dir(db, user.id) as skill_dir:
        size_usd = get_position_size_usd(ticker=symbol, price=price if price > 0 else None, skill_dir=skill_dir)

    qty = max(1, int(size_usd / price)) if price > 0 else 1
    stop_pct = max(0.03, min(0.15, 0.07))
    stop_level = round(price * (1.0 - stop_pct), 2) if price > 0 else None
    entry_zone = (
        {"low": round(price * 0.995, 2), "high": round(price * 1.005, 2)}
        if price > 0
        else {"low": None, "high": None}
    )
    confidence_bucket = str(((signal.get("advisory") or {}).get("confidence_bucket") or "unknown")).lower()
    score = float(signal.get("signal_score", 0) or 0)
    conviction = signal.get("mirofish_conviction")
    reasons = [
        f"signal_score={score:.1f}",
        f"confidence={confidence_bucket}",
        f"strategy={((signal.get('strategy_attribution') or {}).get('top_live') or 'unknown')}",
    ]
    if conviction is not None:
        reasons.append(f"mirofish_conviction={conviction}")
    ev = signal.get("event_risk")
    if isinstance(ev, dict) and ev.get("flagged"):
        rlist = ev.get("reasons") or []
        reasons.append(f"event_risk={','.join(str(x) for x in rlist)}")

    mock_trade = PendingTrade(
        id="preview",
        user_id=user.id,
        ticker=symbol,
        qty=qty,
        price=price,
        status="pending",
        signal_json=json.dumps(signal),
        note=None,
    )
    checklist = _saas_pretrade_checklist(mock_trade, signal)

    return _ok(
        {
            "ticker": symbol,
            "entry_zone": entry_zone,
            "stop_invalidation": stop_level,
            "size": {"qty": qty, "usd": size_usd},
            "confidence": {
                "bucket": confidence_bucket,
                "signal_score": score,
                "mirofish_conviction": conviction,
            },
            "key_reasons": reasons[:6],
            "block_reason": (checklist.get("block_reasons") or [None])[0],
            "checklist": checklist,
        }
    )


@router.get("/api/check/{ticker}", response_model=ApiResponse)
def tenant_check_ticker(
    ticker: str,
    user: User = Depends(get_current_user),
    db: OrmSession = Depends(_db),
) -> ApiResponse:
    if not user_has_account_session(db, user.id):
        return _err("Link Schwab account before running a quick check.")
    try:
        with tenant_skill_dir(db, user.id) as skill_dir:
            with DualSchwabAuth(skill_dir=skill_dir, auto_refresh=False) as auth:
                data = quick_check(ticker.upper().strip(), auth=auth, skill_dir=skill_dir)
        return _ok(data)
    except Exception as exc:
        return _saas_error_response(exc, source="quick_check", fallback="Quick ticker check failed.")


@router.get("/api/chart/{ticker}", response_model=ApiResponse)
def tenant_chart_data(
    ticker: str,
    days: int = 120,
    user: User = Depends(get_current_user),
    db: OrmSession = Depends(_db),
) -> ApiResponse:
    if not user_has_account_session(db, user.id):
        return _err("Link Schwab account before loading chart data.")
    try:
        with tenant_skill_dir(db, user.id) as skill_dir:
            with DualSchwabAuth(skill_dir=skill_dir, auto_refresh=False) as auth:
                df, meta = get_daily_history_with_meta(
                    ticker.upper().strip(),
                    days=min(365, max(30, days)),
                    auth=auth,
                    skill_dir=skill_dir,
                )
        if df is None or df.empty:
            return ApiResponse(
                ok=False,
                error=f"No price data for {ticker}",
                data={
                    "ticker": ticker.upper().strip(),
                    "provider": meta.get("provider"),
                    "used_fallback": meta.get("used_fallback"),
                    "fallback_reason": meta.get("fallback_reason"),
                },
            )
        candles: list[dict[str, Any]] = []
        for _, row in df.iterrows():
            ts = row.get("datetime") or row.get("date") or row.name
            try:
                if hasattr(ts, "timestamp"):
                    epoch = int(ts.timestamp())
                else:
                    from datetime import datetime as _dt

                    epoch = int(_dt.fromisoformat(str(ts)).timestamp())
            except Exception:
                continue
            candles.append(
                {
                    "time": epoch,
                    "open": round(float(row.get("open", 0)), 2),
                    "high": round(float(row.get("high", 0)), 2),
                    "low": round(float(row.get("low", 0)), 2),
                    "close": round(float(row.get("close", 0)), 2),
                    "volume": int(row.get("volume", 0) or 0),
                }
            )
        candles.sort(key=lambda c: c["time"])
        return _ok({"ticker": ticker.upper().strip(), "candles": candles})
    except Exception as exc:
        return _saas_error_response(exc, source="chart_data", fallback="Chart data lookup failed.")


@router.get("/api/report/{ticker}", response_model=ApiResponse)
def tenant_report_ticker(
    ticker: str,
    section: str | None = None,
    skip_mirofish: bool = False,
    skip_edgar: bool = False,
    user: User = Depends(get_current_user),
    db: OrmSession = Depends(_db),
) -> ApiResponse:
    if not user_has_account_session(db, user.id):
        return _err("Link Schwab account before loading a full report.")
    try:
        with tenant_skill_dir(db, user.id) as skill_dir:
            with DualSchwabAuth(skill_dir=skill_dir, auto_refresh=False) as auth:
                section_key = None
                if section:
                    section_key = REPORT_SECTION_MAP.get(section.lower().strip())
                    if not section_key:
                        return ApiResponse(
                            ok=False,
                            error=(
                                f"Invalid section '{section}'. Use: tech, dcf, comps, health, edgar, mirofish."
                            ),
                        )
                report = generate_full_report(
                    ticker.upper().strip(),
                    skip_mirofish=skip_mirofish,
                    skip_edgar=skip_edgar,
                    auth=auth,
                    skill_dir=skill_dir,
                )
                portfolio_summary: dict[str, Any] | None = None
                try:
                    status_data = get_account_status(skill_dir=skill_dir)
                    if isinstance(status_data, dict):
                        portfolio_summary = _build_portfolio_summary(status_data)
                except Exception:
                    portfolio_summary = None
            data = json.loads(report_to_json(report))
            try:
                data["finnhub_snapshot"] = get_finnhub_research_snapshot(ticker.upper().strip(), skill_dir=skill_dir)
            except Exception:  # noqa: BLE001
                data["finnhub_snapshot"] = {"enabled": False, "ok": False, "errors": ["finnhub_snapshot_failed"]}
            data["report_v2"] = build_report_v2(data, portfolio_summary=portfolio_summary)
            section_verdicts = _build_report_verdicts(data)
            if section_key:
                section_data = data.get(section_key)
                return _ok(
                    {
                        "ticker": data.get("ticker"),
                        "generated_at": data.get("generated_at"),
                        "section": section_key,
                        "data": section_data,
                        "report_v2": data.get("report_v2"),
                        "section_verdicts": section_verdicts,
                        "section_quick_verdict": section_verdicts.get(section_key, {}),
                    }
                )
            data["section_verdicts"] = section_verdicts
            return _ok(data)
    except Exception as exc:
        return _saas_error_response(exc, source="report", fallback="Full report generation failed.")


@router.get("/api/sec/compare", response_model=ApiResponse)
def tenant_sec_compare(
    mode: str = "ticker_vs_ticker",
    ticker: str = "",
    ticker_b: str = "",
    form_type: str = "10-K",
    highlight_changes_only: bool = False,
    user: User = Depends(get_current_user),
    db: OrmSession = Depends(_db),
) -> ApiResponse:
    if not user_has_account_session(db, user.id):
        return _err("Link Schwab account before SEC compare.")
    try:
        with tenant_skill_dir(db, user.id) as skill_dir:
            cfg = _sec_analysis_settings_sd(skill_dir)
            if not cfg["analysis_enabled"]:
                return ApiResponse(ok=False, error="SEC filing analysis is disabled by configuration.")
            if not cfg["compare_enabled"]:
                return ApiResponse(ok=False, error="SEC filing compare is disabled by configuration.")
            safe_mode = mode.strip().lower()
            safe_form = form_type.upper().strip()
            safe_ticker = ticker.upper().strip()
            safe_ticker_b = ticker_b.upper().strip()
            if cfg["max_compare_items"] < 2:
                return ApiResponse(ok=False, error="SEC compare limit is below required minimum.")

            if safe_mode == "ticker_vs_ticker":
                if not safe_ticker or not safe_ticker_b:
                    return ApiResponse(ok=False, error="ticker and ticker_b are required for ticker_vs_ticker mode.")
                out = compare_ticker_vs_ticker(
                    safe_ticker,
                    safe_ticker_b,
                    form_type=safe_form,
                    user_agent=cfg["user_agent"],
                    skill_dir=skill_dir,
                    cache_hours=cfg["cache_hours"],
                    max_chars=cfg["max_chars"],
                    enable_llm=cfg["llm_enabled"],
                    highlight_changes_only=bool(highlight_changes_only),
                )
            elif safe_mode == "ticker_over_time":
                if not safe_ticker:
                    return ApiResponse(ok=False, error="ticker is required for ticker_over_time mode.")
                out = compare_ticker_over_time(
                    safe_ticker,
                    form_type=safe_form,
                    user_agent=cfg["user_agent"],
                    skill_dir=skill_dir,
                    cache_hours=cfg["cache_hours"],
                    max_chars=cfg["max_chars"],
                    enable_llm=cfg["llm_enabled"],
                    highlight_changes_only=bool(highlight_changes_only),
                )
            else:
                return ApiResponse(ok=False, error="Invalid mode. Use ticker_vs_ticker or ticker_over_time.")

            if not out.get("ok"):
                return ApiResponse(ok=False, error=str(out.get("error", "SEC compare failed")))
            return _ok(_normalize_sec_compare_payload_sd(out))
    except Exception as exc:
        return _saas_error_response(exc, source="sec_compare", fallback="SEC compare failed.")


@router.get("/api/research/dossier/{ticker}", response_model=ApiResponse)
def tenant_research_dossier(
    ticker: str,
    user: User = Depends(get_current_user),
    db: OrmSession = Depends(_db),
) -> ApiResponse:
    if not user_has_account_session(db, user.id):
        return _err("Link Schwab account before generating a research dossier.")
    try:
        with tenant_skill_dir(db, user.id) as skill_dir:
            dossier = _compose_research_dossier_sd(ticker, skill_dir=skill_dir)
        return _ok(dossier)
    except Exception as exc:
        return _saas_error_response(exc, source="research_dossier", fallback="Research dossier generation failed.")


@router.get("/api/research/dossier/{ticker}/export")
def tenant_research_dossier_export(
    ticker: str,
    format: str = Query(default="json", pattern="^(json|md|pdf)$"),
    user: User = Depends(get_current_user),
    db: OrmSession = Depends(_db),
) -> Response:
    if not user_has_account_session(db, user.id):
        err = _err("Link Schwab account before downloading a research dossier.")
        return Response(content=json.dumps(err.model_dump(), indent=2), media_type="application/json", status_code=409)
    try:
        with tenant_skill_dir(db, user.id) as skill_dir:
            dossier = _compose_research_dossier_sd(ticker, skill_dir=skill_dir)
        symbol = str(dossier.get("ticker") or ticker.upper().strip())
        safe_symbol = "".join(ch for ch in symbol if ch.isalnum() or ch in ("-", "_")) or "TICKER"
        if format == "json":
            body = json.dumps(dossier, indent=2, sort_keys=True).encode("utf-8")
            filename = f"{safe_symbol.lower()}_research_dossier.json"
            media_type = "application/json"
        elif format == "md":
            body = _dossier_to_markdown_sd(dossier).encode("utf-8")
            filename = f"{safe_symbol.lower()}_research_dossier.md"
            media_type = "text/markdown; charset=utf-8"
        else:
            body = _text_to_simple_pdf_sd(_dossier_to_markdown_sd(dossier))
            filename = f"{safe_symbol.lower()}_research_dossier.pdf"
            media_type = "application/pdf"
        headers = {"Content-Disposition": f'attachment; filename="{filename}"'}
        return Response(content=body, media_type=media_type, headers=headers)
    except Exception as exc:
        err = _saas_error_response(exc, source="research_dossier_export", fallback="Dossier export failed.")
        return Response(content=json.dumps(err.model_dump(), indent=2), media_type="application/json", status_code=500)


@router.get("/api/performance", response_model=ApiResponse)
def tenant_performance(user: User = Depends(get_current_user), db: OrmSession = Depends(_db)) -> ApiResponse:
    latest_bt = (
        db.query(BacktestRun)
        .filter(BacktestRun.user_id == user.id)
        .order_by(BacktestRun.created_at.desc())
        .first()
    )
    bt_payload: dict[str, Any] = {
        "source": "saas_backtest_runs",
        "run_at": None,
        "total_trades": None,
        "win_rate": None,
        "avg_return_pct": None,
        "max_drawdown_pct": None,
    }
    if latest_bt and latest_bt.result_json:
        parsed = parse_json(latest_bt.result_json, {})
        if isinstance(parsed, dict):
            bt_payload["run_at"] = latest_bt.created_at.isoformat() if latest_bt.created_at else None
            bt_payload["total_trades"] = parsed.get("total_trades")
            bt_payload["win_rate"] = parsed.get("win_rate_net")
            bt_payload["avg_return_pct"] = parsed.get("avg_return_net_pct")
            bt_payload["max_drawdown_pct"] = parsed.get("max_drawdown_net_pct")

    ord_rows = (
        db.query(Order)
        .filter(Order.user_id == user.id, Order.status == "executed")
        .order_by(Order.created_at.desc())
        .limit(50)
        .all()
    )
    live_n = (
        db.query(Order)
        .filter(Order.user_id == user.id, Order.status == "executed")
        .count()
    )
    latest_outcomes: list[dict[str, Any]] = []
    for o in ord_rows[:5]:
        res = parse_json(o.result_json, {})
        if not isinstance(res, dict):
            res = {}
        latest_outcomes.append(
            {
                "ticker": o.ticker,
                "side": o.side,
                "qty": o.qty,
                "fill_price": res.get("fill_price") or res.get("average_price") or o.price,
                "date": o.created_at.isoformat() if o.created_at else None,
                "mirofish_conviction": res.get("mirofish_conviction"),
                "sector_etf": res.get("sector_etf") or "—",
            }
        )

    return _ok(
        {
            "backtest": bt_payload,
            "shadow_paper": {
                "source": "saas_aggregate",
                "shadow_actions": 0,
                "notes": "Per-tenant shadow counters are not stored in the hosted API; live rows below reflect executed orders.",
            },
            "live": {
                "source": "saas_orders",
                "live_actions": live_n,
                "recorded_outcomes": live_n,
                "latest_outcomes": latest_outcomes,
            },
            "validation": {
                "status": _latest_validation_status(),
                "artifacts_present": VALIDATION_ARTIFACT_DIR.exists(),
            },
            "separation_guard": {
                "commingled_metric_allowed": False,
                "message": "Backtest, shadow/paper, and live are reported as separate buckets only.",
            },
            "challenger": _challenger_summary(db, user.id),
            "learning_status": load_state_json(db, user.id, LEARNING_LAST_RUN_KEY, {}),
        }
    )


@router.post("/api/trades/{trade_id}/approve", response_model=ApiResponse)
def tenant_approve_trade(
    request: Request,
    trade_id: str,
    payload: ApproveTradeRequest,
    confirm_live: bool = False,
    user: User = Depends(require_paid_entitlement),
    db: OrmSession = Depends(_db),
) -> ApiResponse:
    if not user_has_account_session(db, user.id):
        return _err("Link Schwab account before approving trades.")
    db_user = db.query(User).filter(User.id == user.id).first()
    if not db_user:
        return _err("User not found.")
    if _global_live_trading_kill_switch_on():
        raise HTTPException(
            status_code=403,
            detail="Platform kill switch is enabled. New live orders are blocked by policy.",
        )
    if getattr(db_user, "trading_halted", False):
        raise HTTPException(
            status_code=403,
            detail="Trading is paused for this account. Resume under account settings before approving live orders.",
        )
    if not db_user.live_execution_enabled:
        raise HTTPException(
            status_code=403,
            detail="Live trading is off. Enable it under Strategy Presets after reviewing risk, then approve again.",
        )
    row = db.query(PendingTrade).filter(PendingTrade.id == trade_id, PendingTrade.user_id == user.id).first()
    if not row:
        return ApiResponse(ok=False, error="Trade not found.")
    if row.status != "pending":
        return ApiResponse(ok=False, error=f"Trade already {row.status}.")

    typed = (payload.typed_ticker or "").strip().upper()
    if typed != row.ticker.upper():
        return ApiResponse(
            ok=False,
            error="typed_ticker must exactly match the staged trade ticker (re-type to confirm the live order).",
        )

    order_notional = float((row.price or 0) * (row.qty or 0))
    day_pnl_usd = 0.0
    with tenant_skill_dir(db, user.id) as skill_dir:
        account_status = get_account_status(skill_dir=skill_dir)
    if isinstance(account_status, str):
        raise HTTPException(
            status_code=503,
            detail="Could not evaluate daily loss guardrail because account status lookup failed.",
        )
    if isinstance(account_status, dict):
        day_pnl_usd = _sum_intraday_pnl(account_status)
    day_loss_limit = _daily_loss_limit_usd()
    if day_loss_limit > 0 and day_pnl_usd <= -abs(day_loss_limit):
        raise HTTPException(
            status_code=403,
            detail=(
                f"Daily loss limit hit (${day_loss_limit:,.2f}). "
                f"Current intraday P/L is ${day_pnl_usd:,.2f}; live approvals are blocked."
            ),
        )

    two_fa = _two_fa_state(db, user.id)
    needs_high_value_2fa = order_notional >= _high_value_2fa_threshold_usd()
    if needs_high_value_2fa:
        if not bool(two_fa.get("enabled")):
            raise HTTPException(
                status_code=403,
                detail=(
                    "High-value execution requires 2FA. "
                    "Enable TOTP under /api/security/2fa/setup before approving this order."
                ),
            )
        secret = decrypt_secret(str(two_fa.get("totp_secret_enc") or "")) or ""
        if not _verify_totp_code(secret, str(payload.otp_code or "")):
            raise HTTPException(status_code=401, detail="High-value execution requires a valid 2FA code.")

    signal = json.loads(row.signal_json or "{}")
    settings = _load_state(db, user.id, "ui_settings", {})
    automation_opt_in = bool(settings.get("automation_opt_in", DEFAULT_AUTOMATION_OPT_IN))
    if not automation_opt_in and not confirm_live:
        checklist = _saas_pretrade_checklist(row, signal if isinstance(signal, dict) else {})
        return ApiResponse(
            ok=False,
            error="Explicit live confirmation required. Review checklist and retry with confirm_live=true.",
            data={"checklist": checklist, "automation_opt_in": automation_opt_in},
        )

    with tenant_skill_dir(db, user.id) as skill_dir:
        result = submit_order(
            ticker=row.ticker,
            qty=row.qty,
            side="BUY",
            order_type="MARKET",
            price_hint=row.price,
            mirofish_conviction=signal.get("mirofish_conviction"),
            sector_etf=signal.get("sector_etf"),
            skill_dir=skill_dir,
        )

    if isinstance(result, str):
        row.status = "failed"
        row.note = (row.note or "") + f" | {result}" if row.note else result
        db.commit()
        db.refresh(row)
        log_audit(
            db,
            action="trade_approve_failed",
            user_id=user.id,
            detail={
                "trade_id": trade_id,
                "ticker": row.ticker,
                "error_excerpt": result[:240],
            },
            request_id=_request_id(request),
        )
        return ApiResponse(
            ok=False,
            error=result,
            data={
                "trade": _trade_to_dict(row),
                "recovery": map_failure(result, source="execution"),
            },
        )

    row.status = "executed"
    db.commit()
    db.refresh(row)
    try:
        upsert_trade_outcome(
            db,
            user.id,
            _build_trade_outcome_payload(
                user_id=user.id,
                ticker=row.ticker,
                side="BUY",
                qty=int(row.qty or 0),
                price=_safe_float(row.price),
                result=result if isinstance(result, dict) else {},
                signal=signal if isinstance(signal, dict) else {},
            ),
        )
    except Exception:
        pass
    log_audit(
        db,
        action="trade_approved_executed",
        user_id=user.id,
        detail={"trade_id": trade_id, "ticker": row.ticker, "qty": row.qty},
        request_id=_request_id(request),
    )
    return _ok({"trade": _trade_to_dict(row), "result": result})


@router.post("/api/trades/{trade_id}/reject", response_model=ApiResponse)
def tenant_reject_trade(
    trade_id: str,
    user: User = Depends(get_current_user),
    db: OrmSession = Depends(_db),
) -> ApiResponse:
    row = db.query(PendingTrade).filter(PendingTrade.id == trade_id, PendingTrade.user_id == user.id).first()
    if not row:
        return ApiResponse(ok=False, error="Trade not found.")
    if row.status != "pending":
        return ApiResponse(ok=False, error=f"Trade already {row.status}.")
    row.status = "rejected"
    db.commit()
    db.refresh(row)
    return _ok(_trade_to_dict(row))


@router.post("/api/trades/{trade_id}/delete", response_model=ApiResponse)
def tenant_delete_trade(
    trade_id: str,
    user: User = Depends(get_current_user),
    db: OrmSession = Depends(_db),
) -> ApiResponse:
    row = db.query(PendingTrade).filter(PendingTrade.id == trade_id, PendingTrade.user_id == user.id).first()
    if not row:
        return ApiResponse(ok=False, error="Trade not found.")
    db.delete(row)
    db.commit()
    return _ok({"deleted": trade_id})


@router.get("/api/trades/{trade_id}/preflight", response_model=ApiResponse)
def tenant_preflight_trade(
    trade_id: str,
    user: User = Depends(get_current_user),
    db: OrmSession = Depends(_db),
) -> ApiResponse:
    row = db.query(PendingTrade).filter(PendingTrade.id == trade_id, PendingTrade.user_id == user.id).first()
    if not row:
        return ApiResponse(ok=False, error="Trade not found.")
    signal = json.loads(row.signal_json or "{}")
    checklist = _saas_pretrade_checklist(row, signal if isinstance(signal, dict) else {})
    two_fa = _two_fa_state(db, user.id)
    return _ok(
        {
            "trade": _trade_to_dict(row),
            "checklist": checklist,
            "high_value_2fa": {
                "enabled": bool(two_fa.get("enabled")),
                "required": bool(checklist.get("requires_high_value_2fa")),
                "threshold_usd": _high_value_2fa_threshold_usd(),
            },
        }
    )


@router.get("/api/settings/profiles", response_model=ApiResponse)
def tenant_get_profiles(expert: bool = False, user: User = Depends(get_current_user), db: OrmSession = Depends(_db)) -> ApiResponse:
    settings = _load_state(db, user.id, "ui_settings", {})
    profile = str(settings.get("profile", DEFAULT_PROFILE)).strip().lower()
    profile = profile if profile in PRESET_PROFILES else DEFAULT_PROFILE
    active = dict(PRESET_PROFILES.get(profile, {}))
    payload: dict[str, Any] = {
        "mode": settings.get("mode", DEFAULT_UI_MODE),
        "profile": profile,
        "automation_opt_in": bool(settings.get("automation_opt_in", DEFAULT_AUTOMATION_OPT_IN)),
        "profiles": sorted(PRESET_PROFILES.keys()),
        "active_profile_settings": active,
    }
    if expert:
        payload["expert_runtime_overrides"] = {k: os.environ.get(k) for k in sorted(active.keys())}
    payload["preset_catalog"] = build_preset_catalog_payload()
    return _ok(payload)


@router.post("/api/settings/profile", response_model=ApiResponse)
def tenant_set_profile(
    profile: str = DEFAULT_PROFILE,
    mode: str = DEFAULT_UI_MODE,
    automation_opt_in: bool = False,
    user: User = Depends(get_current_user),
    db: OrmSession = Depends(_db),
) -> ApiResponse:
    p = str(profile or DEFAULT_PROFILE).strip().lower()
    if p not in PRESET_PROFILES:
        return ApiResponse(ok=False, error=f"Invalid profile '{profile}'.")
    mode_n = str(mode or DEFAULT_UI_MODE).strip().lower()
    if mode_n not in {"standard", "expert"}:
        return ApiResponse(ok=False, error="Invalid mode. Use standard or expert.")
    runtime = _apply_profile_to_runtime(p)
    settings = {
        "mode": mode_n,
        "profile": p,
        "automation_opt_in": bool(automation_opt_in),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    _save_state(db, user.id, "ui_settings", settings)
    return _ok({"settings": settings, "runtime_overrides": runtime})


@router.get("/api/security/2fa/status", response_model=ApiResponse)
def tenant_two_fa_status(user: User = Depends(get_current_user), db: OrmSession = Depends(_db)) -> ApiResponse:
    state = _two_fa_state(db, user.id)
    return _ok(
        {
            "enabled": bool(state.get("enabled")),
            "high_value_threshold_usd": _high_value_2fa_threshold_usd(),
        }
    )


@router.post("/api/security/2fa/setup", response_model=ApiResponse)
def tenant_two_fa_setup(user: User = Depends(get_current_user), db: OrmSession = Depends(_db)) -> ApiResponse:
    secret = _generate_totp_secret()
    state = _two_fa_state(db, user.id)
    state["pending_secret_enc"] = encrypt_secret(secret)
    state["pending_created_at"] = utcnow_iso()
    _save_two_fa_state(db, user.id, state)
    issuer = (os.getenv("TWO_FA_ISSUER") or "TradingBot").strip() or "TradingBot"
    label = urllib.parse.quote(f"{issuer}:{user.email or user.id}")
    issuer_q = urllib.parse.quote(issuer)
    otp_uri = f"otpauth://totp/{label}?secret={secret}&issuer={issuer_q}&digits={_totp_digits()}&period={_totp_step_seconds()}"
    return _ok(
        {
            "secret": secret,
            "otpauth_uri": otp_uri,
            "digits": _totp_digits(),
            "period_seconds": _totp_step_seconds(),
        }
    )


@router.post("/api/security/2fa/enable", response_model=ApiResponse)
def tenant_two_fa_enable(
    payload: dict[str, Any] | None = Body(default=None),
    user: User = Depends(get_current_user),
    db: OrmSession = Depends(_db),
) -> ApiResponse:
    code = str((payload or {}).get("otp_code") or "").strip()
    state = _two_fa_state(db, user.id)
    pending_enc = str(state.get("pending_secret_enc") or "").strip()
    if not pending_enc:
        raise HTTPException(status_code=409, detail="Run 2FA setup first.")
    secret = decrypt_secret(pending_enc) or ""
    if not _verify_totp_code(secret, code):
        raise HTTPException(status_code=401, detail="Invalid 2FA code.")
    state["enabled"] = True
    state["enabled_at"] = utcnow_iso()
    state["totp_secret_enc"] = encrypt_secret(secret)
    state["pending_secret_enc"] = None
    state["pending_created_at"] = None
    _save_two_fa_state(db, user.id, state)
    return _ok({"enabled": True})


@router.post("/api/security/2fa/disable", response_model=ApiResponse)
def tenant_two_fa_disable(
    payload: dict[str, Any] | None = Body(default=None),
    user: User = Depends(get_current_user),
    db: OrmSession = Depends(_db),
) -> ApiResponse:
    code = str((payload or {}).get("otp_code") or "").strip()
    state = _two_fa_state(db, user.id)
    if not bool(state.get("enabled")):
        return _ok({"enabled": False})
    secret = decrypt_secret(str(state.get("totp_secret_enc") or "")) or ""
    if not _verify_totp_code(secret, code):
        raise HTTPException(status_code=401, detail="Invalid 2FA code.")
    state["enabled"] = False
    state["enabled_at"] = None
    state["totp_secret_enc"] = None
    state["pending_secret_enc"] = None
    state["pending_created_at"] = None
    _save_two_fa_state(db, user.id, state)
    return _ok({"enabled": False})


@router.post("/api/onboarding/start", response_model=ApiResponse)
def tenant_onboarding_start(user: User = Depends(get_current_user), db: OrmSession = Depends(_db)) -> ApiResponse:
    state = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "target_minutes": ONBOARDING_TARGET_MINUTES,
        "steps": {
            "connect": {"ok": False, "at": None},
            "verify_token_health": {"ok": False, "at": None},
            "test_scan": {"ok": False, "at": None},
            "test_paper_order": {"ok": False, "at": None},
        },
    }
    _save_state(db, user.id, "onboarding_wizard", state)
    return _ok(state)


@router.post("/api/onboarding/step/{step}", response_model=ApiResponse)
def tenant_onboarding_step(
    step: str,
    user: User = Depends(get_current_user),
    db: OrmSession = Depends(_db),
) -> ApiResponse:
    current = _load_state(
        db,
        user.id,
        "onboarding_wizard",
        default={
            "started_at": datetime.now(timezone.utc).isoformat(),
            "target_minutes": ONBOARDING_TARGET_MINUTES,
            "steps": {},
        },
    )
    steps = current.setdefault("steps", {})
    step_key = str(step or "").strip().lower()
    now_iso = datetime.now(timezone.utc).isoformat()

    if step_key == "connect":
        linked = user_has_account_session(db, user.id)
        steps["connect"] = {
            "ok": linked,
            "at": now_iso,
            "details": {"schwab_linked": linked},
            "fix_path": "Use Connect Schwab (account) and Connect Schwab (market) on the dashboard, or paste tokens via API if your host allows it.",
        }
    elif step_key == "verify_token_health":
        snap = _tenant_api_health_snapshot(db, user.id)
        ok = bool(snap.get("schwab_linked") and snap.get("market_token_ok") and snap.get("account_token_ok") and snap.get("quote_ok"))
        steps["verify_token_health"] = {
            "ok": ok,
            "at": now_iso,
            "details": snap,
            "fix_path": "Finish both Schwab connect buttons (account and market), then refresh this page.",
        }
    elif step_key == "test_scan":
        if not user_has_paid_entitlement(user):
            return ApiResponse(ok=False, error="Active subscription required for test scan.")
        if not user_has_account_session(db, user.id):
            steps["test_scan"] = {"ok": False, "at": now_iso, "details": {"error": "Schwab not linked"}}
        else:
            try:
                with tenant_skill_dir(db, user.id) as skill_dir:
                    scan_out = run_scan(skill_dir=skill_dir)
                    signals = scan_out.signals
                    diagnostics = scan_out.diagnostics
                scan_ok = diagnostics.get("scan_blocked", 0) == 0 and diagnostics.get("exceptions", 0) == 0
                steps["test_scan"] = {
                    "ok": bool(scan_ok),
                    "at": now_iso,
                    "details": {
                        "signals_found": len(signals),
                        "diagnostics_summary": {k: diagnostics.get(k) for k in ("watchlist_size", "exceptions", "scan_blocked")},
                    },
                }
            except Exception as e:
                steps["test_scan"] = {
                    "ok": False,
                    "at": now_iso,
                    "details": {"error": str(e)},
                    "recovery": map_failure(str(e), source="signal_scanner"),
                }
    elif step_key == "test_paper_order":
        if not user_has_paid_entitlement(user):
            return ApiResponse(ok=False, error="Active subscription required for paper order test.")
        if not user_has_account_session(db, user.id):
            steps["test_paper_order"] = {"ok": False, "at": now_iso, "details": {"error": "Schwab not linked"}}
        else:
            previous_shadow = os.environ.get("EXECUTION_SHADOW_MODE")
            os.environ["EXECUTION_SHADOW_MODE"] = "1"
            try:
                with tenant_skill_dir(db, user.id) as skill_dir:
                    with DualSchwabAuth(skill_dir=skill_dir, auto_refresh=False) as auth:
                        quote = get_current_quote("AAPL", auth=auth, skill_dir=skill_dir)
                        price = extract_schwab_last_price(quote) or 100.0
                        result = submit_order(
                            ticker="AAPL",
                            qty=1,
                            side="BUY",
                            order_type="MARKET",
                            price_hint=price,
                            skill_dir=skill_dir,
                        )
                ok = isinstance(result, dict) and bool(result.get("shadow_mode"))
                steps["test_paper_order"] = {
                    "ok": ok,
                    "at": now_iso,
                    "details": result if isinstance(result, dict) else {"result": result},
                }
            except Exception as e:
                steps["test_paper_order"] = {
                    "ok": False,
                    "at": now_iso,
                    "details": {"error": str(e)},
                    "recovery": map_failure(str(e), source="execution"),
                }
            finally:
                if previous_shadow is None:
                    os.environ.pop("EXECUTION_SHADOW_MODE", None)
                else:
                    os.environ["EXECUTION_SHADOW_MODE"] = previous_shadow
    else:
        return ApiResponse(ok=False, error="Unknown onboarding step.")

    _save_state(db, user.id, "onboarding_wizard", current)
    return _ok(current)


@router.get("/api/oauth/schwab/authorize-url", response_model=ApiResponse)
def schwab_authorize_url_endpoint(request: Request, user: User = Depends(get_current_user)) -> ApiResponse:
    client_id = (os.getenv("SCHWAB_ACCOUNT_APP_KEY") or "").strip()
    redirect_uri = _resolve_schwab_redirect_uri(request, market=False)
    if not client_id:
        raise HTTPException(
            status_code=503,
            detail="Configure SCHWAB_ACCOUNT_APP_KEY for OAuth.",
        )
    try:
        state = sign_schwab_oauth_state(user.id, SCHWAB_OAUTH_KIND_ACCOUNT)
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(
            status_code=503,
            detail=safe_exception_message(exc, fallback="OAuth state signing is unavailable."),
        ) from exc
    url = schwab_authorize_url(client_id, redirect_uri, state)
    return _ok({"url": url, "state": state})


@router.get("/api/oauth/schwab/market/authorize-url", response_model=ApiResponse)
def schwab_market_authorize_url_endpoint(request: Request, user: User = Depends(get_current_user)) -> ApiResponse:
    client_id = (os.getenv("SCHWAB_MARKET_APP_KEY") or "").strip()
    redirect_uri = _resolve_schwab_redirect_uri(request, market=True)
    if not client_id:
        raise HTTPException(
            status_code=503,
            detail="Configure SCHWAB_MARKET_APP_KEY for market OAuth.",
        )
    try:
        state = sign_schwab_oauth_state(user.id, SCHWAB_OAUTH_KIND_MARKET)
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(
            status_code=503,
            detail=safe_exception_message(exc, fallback="OAuth state signing is unavailable."),
        ) from exc
    url = schwab_authorize_url(client_id, redirect_uri, state)
    return _ok({"url": url, "state": state})


@router.get("/api/oauth/schwab/callback")
def schwab_oauth_callback(
    request: Request,
    code: str = "",
    state: str = "",
    error: str = "",
    db: OrmSession = Depends(_db),
):
    front = (os.getenv("SAAS_FRONTEND_URL") or "http://127.0.0.1:8000").rstrip("/")

    def red(qs: str) -> RedirectResponse:
        return RedirectResponse(f"{front}/?{qs}", status_code=302)

    if error:
        return red(f"schwab_oauth=error&message={urllib.parse.quote(error)}")
    verified = verify_schwab_oauth_state(state)
    if not verified or not code.strip():
        return red("schwab_oauth=error&message=" + urllib.parse.quote("invalid_or_expired_state"))
    user_id, kind = verified
    if kind != SCHWAB_OAUTH_KIND_ACCOUNT:
        return red(
            "schwab_oauth=error&message="
            + urllib.parse.quote("wrong_oauth_flow_use_account_authorize_link")
        )

    client_id = (os.getenv("SCHWAB_ACCOUNT_APP_KEY") or "").strip()
    client_secret = (os.getenv("SCHWAB_ACCOUNT_APP_SECRET") or "").strip()
    redirect_uri = _resolve_schwab_redirect_uri(request, market=False)
    if not client_id or not client_secret:
        return red("schwab_oauth=error&message=" + urllib.parse.quote("server_oauth_not_configured"))

    try:
        tok = exchange_schwab_code_for_tokens(client_id, client_secret, code, redirect_uri)
    except Exception as exc:
        safe_error = safe_exception_message(exc, fallback="oauth_exchange_failed")
        return red("schwab_oauth=error&message=" + urllib.parse.quote(safe_error[:180]))

    access = str(tok.get("access_token") or "").strip()
    refresh = str(tok.get("refresh_token") or "").strip()
    if not access or not refresh:
        return red("schwab_oauth=error&message=" + urllib.parse.quote("token_response_missing_tokens"))

    try:
        row = db.query(UserCredential).filter(UserCredential.user_id == user_id).first()
        if not row:
            row = UserCredential(user_id=user_id)
            db.add(row)

        row.access_token_enc = encrypt_secret(access)
        row.refresh_token_enc = encrypt_secret(refresh)
        row.token_type = (str(tok.get("token_type") or "Bearer").strip() or "Bearer")
        exp_in = tok.get("expires_in")
        if exp_in is not None:
            try:
                row.expires_at = datetime.now(timezone.utc) + timedelta(seconds=int(exp_in))
            except Exception:
                row.expires_at = None
        scope_raw = tok.get("scope")
        if isinstance(scope_raw, str) and scope_raw.strip():
            parts = [p.strip() for p in scope_raw.replace(",", " ").split() if p.strip()]
            row.scopes = parse_scopes(parts)
        else:
            row.scopes = parse_scopes(None)
        row.account_token_payload_enc = encrypt_secret(json.dumps(tok, default=_json_default))

        db.commit()
        db.refresh(row)
    except Exception as exc:
        try:
            db.rollback()
        except Exception:
            pass
        LOG.exception("schwab_oauth_callback: failed to persist tokens for user_id=%s", user_id)
        safe_error = safe_exception_message(exc, fallback="token_storage_failed")
        return red("schwab_oauth=error&message=" + urllib.parse.quote(safe_error[:180]))

    _save_state(
        db,
        user_id,
        "onboarding",
        {
            "linked_at": utcnow_iso(),
            "schwab_linked": True,
            "wizard_required": False,
        },
    )
    log_audit(
        db,
        action="oauth_schwab_callback",
        user_id=user_id,
        detail={},
        request_id=_request_id(request),
    )
    return red("schwab_oauth=ok")


@router.get("/api/oauth/schwab/market/callback")
def schwab_market_oauth_callback(
    request: Request,
    code: str = "",
    state: str = "",
    error: str = "",
    db: OrmSession = Depends(_db),
):
    front = (os.getenv("SAAS_FRONTEND_URL") or "http://127.0.0.1:8000").rstrip("/")

    def red(qs: str) -> RedirectResponse:
        return RedirectResponse(f"{front}/?{qs}", status_code=302)

    if error:
        return red(f"schwab_market_oauth=error&message={urllib.parse.quote(error)}")
    verified = verify_schwab_oauth_state(state)
    if not verified or not code.strip():
        return red(
            "schwab_market_oauth=error&message=" + urllib.parse.quote("invalid_or_expired_state")
        )
    user_id, kind = verified
    if kind != SCHWAB_OAUTH_KIND_MARKET:
        return red(
            "schwab_market_oauth=error&message="
            + urllib.parse.quote("wrong_oauth_flow_use_market_authorize_link")
        )

    client_id = (os.getenv("SCHWAB_MARKET_APP_KEY") or "").strip()
    client_secret = (os.getenv("SCHWAB_MARKET_APP_SECRET") or "").strip()
    redirect_uri = _resolve_schwab_redirect_uri(request, market=True)
    if not client_id or not client_secret:
        return red(
            "schwab_market_oauth=error&message="
            + urllib.parse.quote("server_market_oauth_not_configured")
        )

    try:
        tok = exchange_schwab_code_for_tokens(client_id, client_secret, code, redirect_uri)
    except Exception as exc:
        safe_error = safe_exception_message(exc, fallback="oauth_exchange_failed")
        return red(
            "schwab_market_oauth=error&message=" + urllib.parse.quote(safe_error[:180])
        )

    access = str(tok.get("access_token") or "").strip()
    refresh = str(tok.get("refresh_token") or "").strip()
    if not access or not refresh:
        return red(
            "schwab_market_oauth=error&message="
            + urllib.parse.quote("token_response_missing_tokens")
        )

    try:
        row = db.query(UserCredential).filter(UserCredential.user_id == user_id).first()
        if not row:
            row = UserCredential(user_id=user_id)
            db.add(row)

        row.market_token_payload_enc = encrypt_secret(json.dumps(tok, default=_json_default))

        db.commit()
        db.refresh(row)
    except Exception as exc:
        try:
            db.rollback()
        except Exception:
            pass
        LOG.exception(
            "schwab_market_oauth_callback: failed to persist tokens for user_id=%s", user_id
        )
        safe_error = safe_exception_message(exc, fallback="token_storage_failed")
        return red("schwab_market_oauth=error&message=" + urllib.parse.quote(safe_error[:180]))

    log_audit(
        db,
        action="oauth_schwab_market_callback",
        user_id=user_id,
        detail={},
        request_id=_request_id(request),
    )
    return red("schwab_market_oauth=ok")
