"""Operator desk: scan, filter, grade, and backtest through the dashboard.

The desk calls the same HTTP tools the website uses. Grades come from the
existing shadow debate grade and do not change rank or place orders. Profit
factors are quoted only from the signal-gate ledger or a stored backtest row.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

import requests

LOG = logging.getLogger(__name__)

DISPLAY_MIN_SCORE = 50.0
_local_scan_status: Callable[[], dict[str, Any]] | None = None
PROMOTION_PF_MEAN = 1.20
PROMOTION_WORST_ERA_PF = 1.00
LEDGER_NAME = "phase1_progress_signal_gate.json"
MAX_OPINIONS = 8
FAIL_LINES = frozenset(
    {
        "Stack fails.",
        "Contraction fails.",
        "Stop is through the 50-day.",
        "Extended.",
    }
)
DROP_LABELS = {
    "stage2_fail": "failed the Stage 2 uptrend",
    "vcp_fail": "failed the VCP check",
    "entry_timing_blocked": "blocked by the entry-timing gate",
    "pts_52w_cap_blocked": "blocked by the 52-week extension cap",
}
BLOCKED_QUALITY = frozenset({"stale", "conflict", "no_price_data"})


def _float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _clip(text: str, limit: int) -> str:
    body = " ".join(str(text or "").split())
    if len(body) <= limit:
        return body
    return body[: limit - 1].rstrip() + "…"


class DashboardTools:
    """HTTP client for the local or hosted dashboard API."""

    def __init__(
        self,
        base_url: str,
        *,
        api_key: str = "",
        verify: bool | None = None,
        session: Any | None = None,
    ) -> None:
        self.base_url = str(base_url or "").rstrip("/")
        self.api_key = str(api_key or "").strip()
        host = (urlparse(self.base_url).hostname or "").lower()
        self.verify = bool(verify) if verify is not None else host not in {"127.0.0.1", "localhost", "::1"}
        self._session = session or requests.Session()
        if not self.verify:
            try:
                import urllib3

                urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
            except Exception:
                LOG.debug("Could not silence insecure-request warnings")

    def get(self, path: str, *, params: dict[str, Any] | None = None, timeout: float = 30) -> dict[str, Any]:
        return self.request("GET", path, params=params, timeout=timeout)

    def post(
        self,
        path: str,
        body: dict[str, Any] | None = None,
        *,
        params: dict[str, Any] | None = None,
        timeout: float = 60,
    ) -> dict[str, Any]:
        return self.request("POST", path, params=params, json_body=body or {}, timeout=timeout)

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        timeout: float = 30,
    ) -> dict[str, Any]:
        headers: dict[str, str] = {}
        if self.api_key:
            headers["X-API-Key"] = self.api_key
        resp = None
        for attempt in range(2):
            try:
                resp = self._session.request(
                    method,
                    f"{self.base_url}{path}",
                    params=params,
                    json=json_body,
                    headers=headers,
                    timeout=timeout,
                    verify=self.verify,
                )
                break
            except requests.exceptions.ConnectionError as exc:
                # A pooled keep-alive often dies with RemoteDisconnected and the
                # next socket succeeds. One fresh connection, then surface it.
                self._drop_pooled_connections()
                if attempt == 0:
                    LOG.warning("Operator desk %s %s dropped; retrying: %s", method, path, exc)
                    continue
                LOG.warning("Operator desk %s %s failed: %s", method, path, exc)
                return {"ok": False, "data": None, "error": str(exc)}
            except Exception as exc:
                LOG.warning("Operator desk %s %s failed: %s", method, path, exc)
                return {"ok": False, "data": None, "error": str(exc)}
        if resp is None:
            return {"ok": False, "data": None, "error": "Dashboard request failed."}
        try:
            payload = resp.json()
        except Exception:
            return {"ok": False, "data": None, "error": f"HTTP {resp.status_code}"}
        if not isinstance(payload, dict):
            return {"ok": resp.status_code < 400, "data": payload, "error": None}
        if resp.status_code >= 400 or payload.get("ok") is False:
            err = payload.get("error") or payload.get("detail") or f"HTTP {resp.status_code}"
            return {"ok": False, "data": payload.get("data"), "error": str(err)}
        if "ok" in payload:
            return {"ok": bool(payload.get("ok")), "data": payload.get("data"), "error": payload.get("error")}
        return {"ok": True, "data": payload, "error": None}

    def _drop_pooled_connections(self) -> None:
        try:
            self._session.close()
        except Exception:
            LOG.debug("Operator desk session reset failed", exc_info=True)


def set_local_scan_status(reader: Callable[[], dict[str, Any]] | None) -> Callable[[], dict[str, Any]] | None:
    """Register an in-process scan snapshot. Returns the previous reader.

    The dashboard calls this so the desk can read the job without an HTTP
    loopback. A full-universe scan can starve that loop and the status
    socket closes with no response. Other processes leave the reader unset.
    """
    global _local_scan_status
    previous = _local_scan_status
    _local_scan_status = reader
    return previous


def _poll_scan_status(tools: DashboardTools, *, timeout: float) -> dict[str, Any]:
    """Prefer the in-memory job while it is active. Idle still uses HTTP so last_scan is included."""
    reader = _local_scan_status
    if reader is not None:
        try:
            data = reader()
        except Exception as exc:
            LOG.warning("In-process scan status failed: %s", exc)
        else:
            if isinstance(data, dict) and str(data.get("status") or "") not in {"", "idle"}:
                return {"ok": True, "data": data, "error": None}
    return tools.get("/api/scan/status", timeout=timeout)


def load_signal_gate(skill_dir: Path) -> dict[str, Any]:
    """Quote the phase-1 ledger. Missing file is reported, not filled in."""
    path = Path(skill_dir) / LEDGER_NAME
    out: dict[str, Any] = {
        "source": LEDGER_NAME,
        "found": False,
        "config_id": None,
        "pf_mean_treatment": None,
        "worst_era_pf_treatment": None,
        "passes_guardrails": None,
        "promotion_pf_mean": PROMOTION_PF_MEAN,
        "promotion_worst_era_pf": PROMOTION_WORST_ERA_PF,
        "clears_promotion": False,
    }
    if not path.exists():
        out["summary"] = f"{LEDGER_NAME} is missing. Promotion status is unknown."
        return out
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        out["summary"] = f"{LEDGER_NAME} could not be read: {exc}"
        return out
    ranking = payload.get("ranking") if isinstance(payload, dict) else None
    row = None
    if isinstance(ranking, list):
        for item in ranking:
            if isinstance(item, dict) and item.get("config_id") == "control_legacy":
                row = item
                break
        if row is None and ranking and isinstance(ranking[0], dict):
            row = ranking[0]
    if not isinstance(row, dict):
        out["summary"] = f"{LEDGER_NAME} has no ranking row."
        return out
    pf = _float(row.get("pf_mean_treatment"))
    worst = _float(row.get("worst_era_pf_treatment"))
    clears = (
        pf is not None
        and worst is not None
        and pf >= PROMOTION_PF_MEAN
        and worst >= PROMOTION_WORST_ERA_PF
    )
    out.update(
        {
            "found": True,
            "config_id": row.get("config_id"),
            "pf_mean_treatment": pf,
            "worst_era_pf_treatment": worst,
            "passes_guardrails": row.get("passes_guardrails"),
            "clears_promotion": clears,
        }
    )
    pf_txt = "n/a" if pf is None else f"{pf:.3f}"
    worst_txt = "n/a" if worst is None else f"{worst:.3f}"
    gate_txt = "clears" if clears else "does not clear"
    out["summary"] = (
        f"{LEDGER_NAME} {row.get('config_id')}: PF mean {pf_txt}, "
        f"worst-era {worst_txt}. Promotion needs PF mean >= {PROMOTION_PF_MEAN:.2f} "
        f"and worst-era >= {PROMOTION_WORST_ERA_PF:.2f}. This row {gate_txt} promotion."
    )
    return out


def research_scan_body() -> dict[str, Any]:
    """Full S&P 1500 and every runnable scan-studio sleeve for this scan only.

    Does not change the saved focus mode. The account focus (currently a
    100-name target) stays in place for other scans.
    """
    from core.scan_catalog import STRATEGIES, known_strategy_ids

    known = known_strategy_ids()
    strategy_ids = [
        str(row.get("id"))
        for row in STRATEGIES
        if row.get("runnable") and str(row.get("id") or "") in known
    ]
    return {
        "universe_preset": "sp1500",
        "strategy_ids": strategy_ids,
        "strategy_overrides": {
            "signal_universe_mode": "broad",
            "quality_watchlist_prefilter_enabled": False,
        },
    }


def baseline_backtest_spec(today: date | None = None) -> dict[str, Any]:
    """Trailing one-year replay of the default universe. Not the multi-era gate."""
    end = (today or date.today()) - timedelta(days=1)
    start = end - timedelta(days=365)
    return {
        "schema_version": 1,
        "theory_name": "Operator desk trailing replay",
        "universe_mode": "watchlist",
        "universe_preset": "sp1500",
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "strategy_ids": [],
        "overrides": {
            "signal_universe_mode": "broad",
            "quality_watchlist_prefilter_enabled": False,
        },
    }


def filter_signals(
    signals: list[dict[str, Any]] | None,
    diagnostics_summary: dict[str, Any] | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split scan rows. This is a display filter, not a new execution gate."""
    summary = diagnostics_summary or {}
    blocked = bool(summary.get("scan_blocked"))
    headline = str(summary.get("headline") or "").strip()
    book_dq = str(summary.get("data_quality") or "").strip().lower()
    kept: list[dict[str, Any]] = []
    dropped: list[dict[str, Any]] = []
    for row in signals or []:
        if not isinstance(row, dict):
            continue
        ticker = str(row.get("ticker") or row.get("symbol") or "").strip().upper()
        score = _float(row.get("signal_score"))
        row_dq = str(row.get("_data_quality") or row.get("data_quality") or book_dq or "").strip().lower()
        reasons: list[str] = []
        if not ticker:
            reasons.append("missing ticker")
        if blocked:
            reasons.append(headline or "scan blocked")
        if row_dq in BLOCKED_QUALITY:
            reasons.append(f"data_quality={row_dq}")
        if score is None:
            reasons.append("no signal_score")
        item = {
            "ticker": ticker,
            "signal_score": score,
            "data_quality": row_dq or None,
            "below_bar": score is None or score < DISPLAY_MIN_SCORE,
            "reasons": reasons,
            "row": row,
        }
        if reasons:
            dropped.append(item)
        else:
            kept.append(item)
    kept.sort(key=lambda item: float(item["signal_score"] or 0.0), reverse=True)
    return kept, dropped


_STRATEGY_BRIEF = {
    "breakout_confirm": "Breakout confirm",
    "gap_and_go": "Gap and go",
    "range_expansion": "Range expansion",
    "trend_breakout": "Stage 2 breakout",
    "pullback": "Trend pullback",
    "pead_primary": "PEAD",
    "donchian_20": "Donchian 20",
    "nr7_breakout": "NR7",
    "weekly_swing": "Weekly Stage 2",
    "weekly_vcp": "Weekly volume dry",
    "weekly_breakout": "Weekly breakout",
    "monthly_position": "10-month SMA",
    "monthly_52w_high": "Monthly 52w high",
    "monthly_pullback": "Monthly SMA pullback",
    "opening_range_breakout": "RVOL close",
    "st_reversal_5d": "5-day bounce",
    "overnight_gap_fade": "Gap-down recovery",
    "weekly_reversal": "Weekly bounce",
    "momentum_12_1": "12-1 strength",
    "tsmom_12m": "12-month trend",
}


def strategy_labels(row: dict[str, Any] | None) -> list[str]:
    """Every sleeve this row passed. A name can carry more than one."""
    signal = row if isinstance(row, dict) else {}
    attr = signal.get("strategy_attribution") if isinstance(signal.get("strategy_attribution"), dict) else {}
    ids: list[str] = []
    for raw in attr.get("passed_ids") or []:
        sid = str(raw or "").strip().lower()
        if sid and sid not in ids:
            ids.append(sid)
    if not ids:
        for key in ("catalog_id", "top_live", "top_shadow"):
            sid = str(attr.get(key) or "").strip().lower()
            if sid and sid not in ids and sid != "unknown":
                ids.append(sid)
    labels: list[str] = []
    for sid in ids:
        label = _STRATEGY_BRIEF.get(sid)
        if not label:
            from core.scan_catalog import get_strategy

            got = get_strategy(sid)
            label = str(got["display_name"]) if got else sid.replace("_", " ")
        if label not in labels:
            labels.append(label)
    return labels


def deliberate_name(item: dict[str, Any]) -> dict[str, Any]:
    """One name, two tools: the scanner score and the shadow grade seats."""
    from core.debate_grade import grade_from_signal

    row = item.get("row") if isinstance(item.get("row"), dict) else {}
    existing = row.get("debate_grade") if isinstance(row.get("debate_grade"), dict) else None
    grade = existing or grade_from_signal(row)
    seats = [seat for seat in (grade.get("seats") or []) if isinstance(seat, dict)]
    active = [seat for seat in seats if str(seat.get("line") or "") not in {"", "Abstained."}]
    fails = [seat for seat in active if str(seat.get("line") or "") in FAIL_LINES]
    if grade.get("qual_abstain") or not active:
        stance = "abstain"
    elif grade.get("would_skip"):
        stance = "pass"
    elif fails and len(fails) < len(active):
        stance = "split"
    elif fails:
        stance = "pass"
    else:
        stance = "constructive"
    reasoning = str(grade.get("chair") or grade.get("label") or "").strip()
    if grade.get("would_skip") and "would skip" not in reasoning.lower():
        reasoning = f"Grade would skip. {reasoning}".strip()
    score = item.get("signal_score")
    score_txt = "n/a" if score is None else f"{float(score):.0f}"
    if item.get("below_bar"):
        scanner_reason = f"Scan kept it at score {score_txt}. That is below the score 50 display bar."
        scanner_stance = "below bar"
    else:
        scanner_reason = f"Score {score_txt} cleared the display filter (score >= 50)."
        scanner_stance = "kept"
    opinions = [
        {
            "source": "scanner",
            "stance": scanner_stance,
            "reason": scanner_reason,
        },
        {
            "source": "grade",
            "stance": stance,
            "reason": reasoning or "No chair sentence.",
        },
    ]
    return {
        "ticker": item.get("ticker"),
        "signal_score": score,
        "data_quality": item.get("data_quality"),
        "stance": stance,
        "letter": grade.get("qual_letter"),
        "would_skip": bool(grade.get("would_skip")),
        "reasoning": reasoning,
        "seats": [{"seat": seat.get("seat"), "line": seat.get("line")} for seat in seats],
        "label": grade.get("label"),
        "opinions": opinions,
        "disagreement": (not item.get("below_bar")) and stance != "constructive",
        "below_bar": bool(item.get("below_bar")),
        "book": item.get("book"),
        "strategies": strategy_labels(row),
    }


def _scan_body(payload: dict[str, Any] | None) -> dict[str, Any]:
    data = payload or {}
    if str(data.get("status") or "") == "idle" and isinstance(data.get("last_scan"), dict):
        return data["last_scan"]
    return data


def _drop_counts(diagnostics: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Positive hard-block counters. Shadow would-filter rows stay out."""
    rows: list[dict[str, Any]] = []
    for key, raw in (diagnostics or {}).items():
        if not isinstance(raw, int) or raw <= 0:
            continue
        if "would_" in key or key.endswith("_ms"):
            continue
        if key.endswith(("_fail", "_blocked", "_filtered")):
            rows.append({"key": key, "value": raw})
    rows.sort(key=lambda item: int(item["value"]), reverse=True)
    return rows[:6]


def _funnel_stage(funnel: dict[str, Any] | None, key: str) -> dict[str, Any]:
    for stage in (funnel or {}).get("stages") or []:
        if isinstance(stage, dict) and stage.get("key") == key:
            return stage
    return {}


def _label_drop(key: str, value: int) -> str:
    label = DROP_LABELS.get(key, key.replace("_", " "))
    return f"{value} {label}"


def empty_book_decision(scan: dict[str, Any]) -> str | None:
    """Say why a finished scan left no name to grade."""
    data = scan.get("data") if isinstance(scan.get("data"), dict) else {}
    if int(data.get("signals_found") or 0) != 0:
        return None
    funnel = data.get("funnel") if isinstance(data.get("funnel"), dict) else {}
    if not funnel:
        return None
    watch = funnel.get("watchlist")
    stage2 = _funnel_stage(funnel, "stage2")
    final = _funnel_stage(funnel, "final")
    passed = stage2.get("value")
    removed = final.get("filtered")
    later = [
        _label_drop(str(row.get("key")), int(row.get("value") or 0))
        for row in (data.get("drop_counts") or [])
        if row.get("key") not in {None, "stage2_fail"}
    ][:3]
    dq = data.get("data_quality") or "unknown"
    head = f"Decision: pass. {passed if passed is not None else '?'} of {watch if watch is not None else '?'} cleared Stage 2."
    if removed:
        head += f" The final cut removed {removed}."
    else:
        head += " The kept book is empty."
    if later:
        head += " Recorded blocks: " + "; ".join(later) + "."
        head += " Those counts can overlap."
    head += f" No ticker was left to grade. data_quality={dq}."
    return head


def _top_blockers(summary: dict[str, Any] | None) -> list[str]:
    rows = (summary or {}).get("top_blockers") or []
    out: list[str] = []
    for row in rows[:3]:
        if isinstance(row, dict) and row.get("key"):
            out.append(f"{row.get('key')}={row.get('value')}")
    return out


def _scan_still_running(data: dict[str, Any]) -> bool:
    """True while a scan thread may still overwrite the status payload."""
    status = str(data.get("status") or "")
    if status in {"running", "queued"}:
        return True
    # The dashboard watchdog marks the job failed at its stale limit, then the
    # worker can still finish and write completed. Keep waiting through that.
    error = str(data.get("error") or "").lower()
    return status == "failed" and "watchdog timeout" in error


def _plain_transport_failure(error: Any) -> str | None:
    """Human line for a dropped HTTP call. Other errors stay as written."""
    text = " ".join(str(error or "").split())
    lowered = text.lower()
    dropped = (
        text.startswith("(")
        or "remotedisconnected" in lowered
        or "connection aborted" in lowered
        or "connection reset" in lowered
        or "connectionerror" in lowered
    )
    if not dropped:
        return None
    return "Could not read scan status. The scan may still be running."


def run_scan_agent(
    tools: DashboardTools,
    *,
    run_scan: bool,
    scan_body: dict[str, Any] | None = None,
    timeout_s: float = 1200,
    poll_s: float = 5,
    wait_if_running: bool = False,
    sleeper: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    started_job = None
    if run_scan:
        posted = tools.post(
            "/api/scan",
            scan_body if scan_body is not None else research_scan_body(),
            params={"async_mode": "true"},
            timeout=60,
        )
        if not posted.get("ok"):
            summary = _plain_transport_failure(posted.get("error")) or str(posted.get("error") or "Scan failed to start.")
            return {"agent": "scan", "ok": False, "summary": summary, "data": {"error": posted.get("error")}}
        started_job = (posted.get("data") or {}).get("job_id")
    waiting = bool(run_scan or wait_if_running)
    deadline = clock() + timeout_s
    last: dict[str, Any] = {"ok": False, "data": {}, "error": "scan was not polled"}
    while True:
        last = _poll_scan_status(tools, timeout=30)
        if not last.get("ok"):
            # One dropped status read is not a finished scan. Keep the job
            # until the desk timeout, then say the scan may still be running.
            if waiting and clock() < deadline:
                sleeper(poll_s)
                continue
            plain = _plain_transport_failure(last.get("error"))
            if plain:
                summary = plain
            elif waiting:
                summary = "Scan is still running. No research list until it finishes."
            else:
                summary = str(last.get("error") or "Scan status check failed.")
            return {
                "agent": "scan",
                "ok": False,
                "summary": summary,
                "data": {"job_id": started_job, "error": last.get("error")},
            }
        data = last.get("data") or {}
        status = str(data.get("status") or "")
        if _scan_still_running(data) and waiting:
            if clock() >= deadline:
                return {
                    "agent": "scan",
                    "ok": False,
                    "summary": "Scan is still running. No research list until it finishes.",
                    "data": {"job_id": started_job, "status": status or "running"},
                }
            sleeper(poll_s)
            continue
        break
    body = _scan_body(last.get("data") or {})
    status = str((last.get("data") or {}).get("status") or body.get("status") or "")
    if status == "failed":
        return {
            "agent": "scan",
            "ok": False,
            "summary": str((last.get("data") or {}).get("error") or "scan failed"),
            "data": {"job_id": started_job, "status": "failed"},
        }
    summary = body.get("diagnostics_summary") if isinstance(body.get("diagnostics_summary"), dict) else {}
    diagnostics = body.get("diagnostics") if isinstance(body.get("diagnostics"), dict) else {}
    signals = body.get("signals") if isinstance(body.get("signals"), list) else []
    shortlist = body.get("shortlist_signals") if isinstance(body.get("shortlist_signals"), list) else []
    dq = summary.get("data_quality")
    blockers = _top_blockers(summary)
    headline = summary.get("headline")
    found = body.get("signals_found")
    count = found if isinstance(found, int) else len(signals)
    watched = diagnostics.get("watchlist_size")
    preset = diagnostics.get("universe_preset") or "sp1500"
    bits = [f"{'Fresh' if run_scan else 'Last'} scan of {watched if watched is not None else '?'} {preset} names: {count} kept."]
    if headline:
        bits.append(str(headline))
    if dq:
        bits.append(f"data_quality={dq}.")
    if blockers:
        bits.append("Drop-offs: " + ", ".join(blockers) + ".")
    if shortlist and len(shortlist) != count:
        bits.append(f"Shortlist held {len(shortlist)} names before the final cut.")
    return {
        "agent": "scan",
        "ok": True,
        "summary": " ".join(bits),
        "data": {
            "fresh": bool(run_scan),
            "watchlist_size": watched,
            "universe_preset": preset,
            "signals_found": count,
            "shortlist_count": len(shortlist),
            "data_quality": dq,
            "data_quality_reasons": list(summary.get("data_quality_reasons") or []),
            "scan_blocked": bool(summary.get("scan_blocked")),
            "headline": headline,
            "top_blockers": blockers,
            "funnel": summary.get("funnel") if isinstance(summary.get("funnel"), dict) else {},
            "drop_counts": _drop_counts(diagnostics),
            **correlation_guard_snapshot(diagnostics),
            "signals": signals,
            "shortlist_signals": shortlist,
            "diagnostics_summary": summary,
        },
    }


def research_candidates(
    signals: list[dict[str, Any]] | None,
    shortlist: list[dict[str, Any]] | None,
    diagnostics_summary: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Best names to research, ranked by this scan's signal_score.

    Final-book rows come first at the same score. A missing signal_score falls
    back to composite_score. Stale or conflicting rows are left out.
    """
    summary = diagnostics_summary or {}
    book_dq = str(summary.get("data_quality") or "").strip().lower()
    seen: set[str] = set()
    pool: list[dict[str, Any]] = []
    for book, rows in (("final", signals or []), ("shortlist", shortlist or [])):
        for row in rows:
            if not isinstance(row, dict):
                continue
            ticker = str(row.get("ticker") or row.get("symbol") or "").strip().upper()
            if not ticker or ticker in seen:
                continue
            row_dq = str(row.get("_data_quality") or row.get("data_quality") or book_dq or "").strip().lower()
            if row_dq in BLOCKED_QUALITY:
                continue
            score = _float(row.get("signal_score"))
            if score is None:
                score = _float(row.get("composite_score"))
            if score is None:
                continue
            seen.add(ticker)
            pool.append(
                {
                    "ticker": ticker,
                    "signal_score": score,
                    "data_quality": row_dq or None,
                    "below_bar": score < DISPLAY_MIN_SCORE,
                    "book": book,
                    "reasons": [],
                    "row": row,
                }
            )
    pool.sort(key=lambda item: (-float(item["signal_score"] or 0.0), item.get("book") != "final"))
    return pool


def run_backtest_agent(
    tools: DashboardTools,
    *,
    queue: bool,
    today: date | None = None,
) -> dict[str, Any]:
    listed = tools.get("/api/backtest-runs", params={"limit": 20}, timeout=30)
    if not listed.get("ok"):
        return {"agent": "backtest", "ok": False, "summary": listed.get("error") or "backtest list failed", "data": {}}
    runs = listed.get("data") if isinstance(listed.get("data"), list) else []
    busy = [row for row in runs if str(row.get("status") or "") in {"queued", "running"}]
    finished = [
        row
        for row in runs
        if str(row.get("status") or "") == "success" and isinstance(row.get("result_summary"), dict)
    ]
    latest = finished[0] if finished else None
    queued_id = None
    queue_error = None
    if queue and busy:
        queue_error = "A backtest is already queued or running."
    elif queue:
        spec = baseline_backtest_spec(today)
        posted = tools.post("/api/backtest-runs", {"spec": spec}, timeout=60)
        if posted.get("ok"):
            queued_id = (posted.get("data") or {}).get("run_id")
        else:
            queue_error = posted.get("error") or "backtest queue failed"
    parts: list[str] = []
    metrics = None
    book_failed = False
    if latest:
        metrics = dict(latest.get("result_summary") or {})
        pf = metrics.get("profit_factor_net")
        trades = metrics.get("total_trades")
        pf_txt = "n/a" if pf is None else str(pf)
        parts.append(
            f"Latest finished replay {latest.get('id')}: profit factor {pf_txt} on {trades} trades. "
            "That is one stored window, not the multi-era promotion test."
        )
        try:
            too_short = int(metrics.get("history_fetch_too_short") or 0)
            fetch_total = int(metrics.get("history_fetch_total") or 0)
        except (TypeError, ValueError):
            too_short = 0
            fetch_total = 0
        if fetch_total > 0 and too_short >= fetch_total:
            book_failed = True
            parts.append("Every symbol was shorter than the indicator window, so this replay never started.")
        mode = str(metrics.get("membership_filter_mode") or "")
        if mode == "membership_missing_live_fallback":
            parts.append("The membership file is missing, so the names are today's constituents.")
        preset = str((latest.get("spec") or {}).get("universe_preset") or "")
        expected = {"sp1500": 1500, "sp500": 500}.get(preset)
        try:
            loaded = int(metrics.get("membership_input_universe"))
        except (TypeError, ValueError):
            loaded = 0
        if expected and loaded and loaded < expected // 2:
            parts.append(f"It loaded {loaded} names, not the {expected}-name book.")
    else:
        parts.append("No finished backtest is stored.")
    if queued_id:
        parts.append(f"Queued trailing 365-day SP1500 replay {queued_id}.")
    elif queue_error:
        parts.append(str(queue_error))
    elif not queue:
        parts.append("Did not queue a new replay.")
    return {
        "agent": "backtest",
        "ok": queue_error is None and not book_failed,
        "summary": " ".join(parts),
        "data": {
            "queued_run_id": queued_id,
            "queue_error": queue_error,
            "busy": bool(busy),
            "latest_run_id": None if latest is None else latest.get("id"),
            "latest_metrics": metrics,
        },
    }


def run_equity_agent(tools: DashboardTools, ticker: str) -> dict[str, Any]:
    symbol = str(ticker or "").strip().upper()
    if not symbol:
        return {"agent": "equity", "ok": False, "summary": "Ticker is empty.", "data": {}}
    checked = tools.get(f"/api/check/{symbol}", timeout=90)
    if not checked.get("ok"):
        return {"agent": "equity", "ok": False, "summary": checked.get("error") or "equity check failed", "data": {}}
    data = checked.get("data") if isinstance(checked.get("data"), dict) else {}
    description = str(data.get("description") or "").replace("**", "")
    dq = data.get("data_quality")
    summary = description or f"Quick check returned no description for {symbol}."
    if dq:
        summary = f"{summary} data_quality={dq}."
    return {
        "agent": "equity",
        "ok": True,
        "summary": summary,
        "data": {
            "ticker": symbol,
            "title": data.get("title"),
            "description": description,
            "data_quality": dq,
            "debate_grade": data.get("debate_grade") if isinstance(data.get("debate_grade"), dict) else None,
        },
    }


_SEAT_NOTE = {
    "Stack fails.": "trend fails",
    "Contraction fails.": "setup fails",
    "Stop is through the 50-day.": "stop through 50d",
    "Extended.": "extended",
    "Books fail the dated checklist.": "books fail",
}
FIELD_LIMIT = 1000


def _seat_notes(card: dict[str, Any]) -> list[str]:
    """Flags worth a second look. Routine passes stay off the line."""
    notes: list[str] = []
    for seat in card.get("seats") or []:
        if not isinstance(seat, dict):
            continue
        line = str(seat.get("line") or "")
        if not line or line == "Abstained.":
            continue
        mapped = _SEAT_NOTE.get(line)
        if mapped:
            notes.append(mapped)
        elif line in FAIL_LINES:
            notes.append(line.rstrip("."))
    return notes


def _name_line(card: dict[str, Any], *, mention_bar: bool, bold_ticker: bool = False) -> str:
    letter = card.get("letter") or "—"
    score = card.get("signal_score")
    score_txt = "n/a" if score is None else f"{float(score):.0f}"
    stance = str(card.get("stance") or "")
    ticker = str(card.get("ticker") or "—")
    label = f"**{ticker}**" if bold_ticker else ticker
    notes: list[str] = []
    strategies = [str(item) for item in (card.get("strategies") or []) if item]
    if strategies:
        notes.append(" · ".join(strategies))
    if card.get("book") == "shortlist":
        notes.append("shortlist")
    if mention_bar and card.get("below_bar"):
        notes.append("below 50")
    if stance and stance != "constructive":
        notes.append(stance)
    notes.extend(_seat_notes(card))
    base = f"{label} {score_txt} {letter}"
    if notes:
        return f"{base} · {' · '.join(notes)}"
    return base


def _pack_blocks(title: str, blocks: list[str], *, limit: int = FIELD_LIMIT) -> list[dict[str, Any]]:
    """Pack whole lines into Discord fields. A line is never split across fields."""
    chunks: list[list[str]] = []
    current: list[str] = []
    size = 0
    for raw in blocks:
        text = str(raw or "").strip()
        if not text:
            continue
        if len(text) > limit:
            text = _clip(" ".join(text.split()), limit)
        extra = len(text) if not current else len(text) + 1
        if current and size + extra > limit:
            chunks.append(current)
            current = [text]
            size = len(text)
        else:
            current.append(text)
            size += extra
    if current:
        chunks.append(current)
    fields: list[dict[str, Any]] = []
    total = len(chunks)
    for index, chunk in enumerate(chunks, start=1):
        name = title if total == 1 else f"{title} ({index}/{total})"
        fields.append({"name": name, "value": "\n".join(chunk), "inline": False})
    return fields


def _gate_brief(gate: dict[str, Any] | None) -> str:
    if not gate or not gate.get("found"):
        return ""
    pf = gate.get("pf_mean_treatment")
    worst = gate.get("worst_era_pf_treatment")
    pf_txt = "n/a" if pf is None else f"{float(pf):.3f}"
    worst_txt = "n/a" if worst is None else f"{float(worst):.3f}"
    status = "clears" if gate.get("clears_promotion") else "short"
    return f"Gate {status} · PF {pf_txt} · worst {worst_txt}"


def _setup_lines(names: list[dict[str, Any]], *, bold_ticker: bool) -> list[str]:
    shown = names[:MAX_OPINIONS]
    below = sum(1 for card in names if card.get("below_bar"))
    mixed = 0 < below < len(names)
    return [_name_line(card, mention_bar=mixed, bold_ticker=bold_ticker) for card in shown]


def _action_brief(names: list[dict[str, Any]], *, cleared_count: int, data_quality: Any) -> str:
    shown = [card for card in names[:MAX_OPINIONS] if not card.get("below_bar")]
    if shown:
        head = "Review " + ", ".join(str(card.get("ticker")) for card in shown)
    elif names:
        head = f"No entry · {cleared_count} cleared 50"
    else:
        head = "No names"
    if data_quality is not None and str(data_quality).strip().lower() not in {"", "ok"}:
        head += f" · dq {data_quality}"
    return head


def compose_brief(
    *,
    gate: dict[str, Any],
    scan: dict[str, Any],
    names: list[dict[str, Any]],
    cleared_count: int,
    dropped_count: int,
    backtest: dict[str, Any],
    equity: dict[str, Any] | None,
    health_ok: bool,
) -> str:
    decision = empty_book_decision(scan) if not names else None
    lines = []
    if decision:
        lines.append(decision)
    lines.extend(
        [
            "Advisory only. No orders. Grades stay shadow and do not change rank.",
            str(gate.get("summary") or "Signal gate ledger was not read."),
        ]
    )
    if not health_ok:
        lines.append("Dashboard health check failed.")
    if not decision:
        scan_line = str(scan.get("summary") or "Scan agent did not report.")
        lines.append(_plain_transport_failure(scan_line) or scan_line)
    if names:
        lines.append(f"{cleared_count} cleared 50 · graded {len(names)} · {dropped_count} filtered")
        lines.extend(_setup_lines(names, bold_ticker=False))
    elif scan.get("ok") is False:
        lines.append("No research list until that scan finishes.")
    else:
        lines.append(f"No gradable name. {dropped_count} row(s) were filtered.")
    lines.append(str(backtest.get("summary") or "Backtest agent did not report."))
    if equity is not None:
        lines.append(str(equity.get("summary") or "Equity check did not report."))
        grade_stance = next((card.get("stance") for card in names if card.get("ticker") == equity.get("data", {}).get("ticker")), None)
        description = str((equity.get("data") or {}).get("description") or "")
        if grade_stance and description and grade_stance not in description.lower():
            lines.append(
                f"Equity check and grade are separate opinions on {equity.get('data', {}).get('ticker')}: "
                f"grade stance is {grade_stance}."
            )
    return "\n".join(lines)


def brief_embed(
    text: str,
    *,
    data_quality: Any = None,
    data_quality_reasons: list[Any] | None = None,
    gate: dict[str, Any] | None = None,
    scan: dict[str, Any] | None = None,
    names: list[dict[str, Any]] | None = None,
    cleared_count: int = 0,
    dropped_count: int = 0,
    backtest: dict[str, Any] | None = None,
    equity: dict[str, Any] | None = None,
    health_ok: bool = True,
) -> dict[str, Any]:
    """One action line plus classified names. Each name stays inside one field."""
    scan_body = scan or {}
    name_rows = names or []
    decision = empty_book_decision(scan_body) if scan_body and not name_rows else None
    parts: list[str] = []
    if not health_ok:
        parts.append("Health failed")
    if scan_body.get("ok") is False:
        scan_line = str(scan_body.get("summary") or "Scan did not finish.")
        parts.append(_plain_transport_failure(scan_line) or scan_line)
    elif decision:
        parts.append(decision)
    else:
        parts.append(_action_brief(name_rows, cleared_count=cleared_count, data_quality=data_quality))
    reasons = "; ".join(str(item) for item in (data_quality_reasons or [])[:2])
    if reasons and data_quality is not None and str(data_quality).strip().lower() != "ok":
        parts.append(reasons)
    gate_line = _gate_brief(gate)
    if gate_line:
        parts.append(gate_line)
    if backtest is not None:
        data = backtest.get("data") if isinstance(backtest.get("data"), dict) else {}
        if data.get("queued_run_id"):
            parts.append(f"Replay queued {data['queued_run_id']}")
        elif data.get("queue_error"):
            parts.append(str(data["queue_error"]))
    fields: list[dict[str, Any]] = []
    if name_rows:
        fields.extend(_pack_blocks("Setups", _setup_lines(name_rows, bold_ticker=True)))
    elif scan_body.get("ok") is False:
        fields.append({"name": "Setups", "value": "Waiting on the scan.", "inline": False})
    elif scan is not None:
        fields.append({"name": "Setups", "value": f"None. {dropped_count} filtered.", "inline": False})
    if equity is not None:
        equity_data = equity.get("data") if isinstance(equity.get("data"), dict) else {}
        ticker = equity_data.get("ticker") or "ticker"
        summary = _clip(str(equity.get("summary") or "No check."), 400)
        fields.append({"name": str(ticker), "value": summary or "—", "inline": False})
    if not fields:
        blocks = [line.strip() for line in str(text or "").splitlines() if line.strip()]
        fields.extend(_pack_blocks("Setups", blocks[:8] or ["No brief."]))
    return {
        "title": "Operator Desk",
        "description": "\n".join(part for part in parts if part) or "No brief.",
        "color": 0x9B59B6,
        "fields": fields,
        "footer": {"text": "No order sent"},
    }


WATCH_LOG_NAME = "live_stack_watch.jsonl"


def correlation_guard_snapshot(diagnostics: dict[str, Any] | None) -> dict[str, Any]:
    """Mode and demotion counts from a scan diagnostic blob."""
    body = diagnostics or {}

    def _count(key: str) -> int | None:
        raw = body.get(key)
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            return None
        return int(raw)

    mode = body.get("correlation_guard_mode")
    return {
        "correlation_guard_mode": mode if isinstance(mode, str) and mode else None,
        "correlation_guard_would_demote": _count("correlation_guard_would_demote"),
        "correlation_guard_demoted": _count("correlation_guard_demoted"),
    }


def live_stack_watch_row(brief: dict[str, Any], *, logged_at: datetime | None = None) -> dict[str, Any]:
    """One append-only row for the 30-day live-stack watch."""
    from zoneinfo import ZoneInfo

    scan = brief.get("scan") if isinstance(brief.get("scan"), dict) else {}
    moment = logged_at or datetime.now(ZoneInfo("UTC"))
    session_day = moment.astimezone(ZoneInfo("America/New_York")).date().isoformat()
    return {
        "logged_at": moment.isoformat(),
        "session_date": session_day,
        "scan_ok": scan.get("ok"),
        "data_quality": scan.get("data_quality"),
        "signals_found": scan.get("signals_found"),
        "correlation_guard_mode": scan.get("correlation_guard_mode"),
        "correlation_guard_would_demote": scan.get("correlation_guard_would_demote"),
        "correlation_guard_demoted": scan.get("correlation_guard_demoted"),
    }


def append_live_stack_watch(skill_dir: Path, brief: dict[str, Any]) -> Path:
    """Append one watch row. Does not change scan output or order behavior."""
    path = Path(skill_dir) / "validation_artifacts" / WATCH_LOG_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    row = live_stack_watch_row(brief)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, separators=(",", ":")) + "\n")
    return path


def run_operator_desk(
    tools: DashboardTools,
    *,
    skill_dir: Path,
    run_scan: bool = True,
    queue_backtest: bool = False,
    notify_discord: bool = False,
    ticker: str | None = None,
    scan_timeout_s: float | None = None,
    wait_if_running: bool = False,
    sleeper: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
    today: date | None = None,
    sender: Callable[[dict[str, Any]], bool] | None = None,
) -> dict[str, Any]:
    """Run the desk agents and optionally post the brief."""
    health = tools.get("/api/health", timeout=20)
    gate = load_signal_gate(skill_dir)
    if scan_timeout_s is None:
        from config import get_operator_desk_scan_timeout_sec

        scan_timeout_s = float(get_operator_desk_scan_timeout_sec(skill_dir))
    scan = run_scan_agent(
        tools,
        run_scan=run_scan,
        scan_body=research_scan_body() if run_scan else None,
        timeout_s=scan_timeout_s,
        wait_if_running=wait_if_running,
        sleeper=sleeper,
        clock=clock,
    )
    scan_data = scan.get("data") or {}
    kept, dropped = filter_signals(scan_data.get("signals"), scan_data.get("diagnostics_summary"))
    cleared = [item for item in kept if not item.get("below_bar")]
    ranked = research_candidates(
        scan_data.get("signals"),
        scan_data.get("shortlist_signals"),
        scan_data.get("diagnostics_summary"),
    )
    below = [item for item in ranked if item.get("below_bar")]
    names = [deliberate_name(item) for item in ranked[:MAX_OPINIONS]]
    for card in names:
        card.pop("row", None)
    backtest = run_backtest_agent(tools, queue=queue_backtest, today=today)
    equity = run_equity_agent(tools, ticker) if str(ticker or "").strip() else None
    text = compose_brief(
        gate=gate,
        scan=scan,
        names=names,
        cleared_count=len(cleared),
        dropped_count=len(dropped),
        backtest=backtest,
        equity=equity,
        health_ok=bool(health.get("ok")),
    )
    public_names = []
    for card in names:
        public = dict(card)
        public.pop("row", None)
        public_names.append(public)
    discord_sent = False
    if notify_discord:
        from notifier import send_embed_alert

        post = sender or send_embed_alert
        discord_sent = bool(
            post(
                brief_embed(
                    text,
                    data_quality=scan_data.get("data_quality"),
                    data_quality_reasons=scan_data.get("data_quality_reasons"),
                    gate=gate,
                    scan=scan,
                    names=names,
                    cleared_count=len(cleared),
                    dropped_count=len(dropped),
                    backtest=backtest,
                    equity=equity,
                    health_ok=bool(health.get("ok")),
                )
            )
        )
    agents = [
        {"id": "health", "ok": bool(health.get("ok")), "summary": "passed" if health.get("ok") else (health.get("error") or "failed")},
        {"id": "gate", "ok": bool(gate.get("found")), "summary": gate.get("summary")},
        {"id": "scan", "ok": bool(scan.get("ok")), "summary": scan.get("summary")},
        {"id": "filter", "ok": True, "summary": f"{len(cleared)} cleared score >= 50, {len(below)} below bar, {len(dropped)} filtered"},
        {"id": "grade", "ok": True, "summary": f"{len(names)} shadow grades"},
        {"id": "backtest", "ok": bool(backtest.get("ok")), "summary": backtest.get("summary")},
    ]
    if equity is not None:
        agents.append({"id": "equity", "ok": bool(equity.get("ok")), "summary": equity.get("summary")})
    if notify_discord:
        agents.append({"id": "discord", "ok": discord_sent, "summary": "posted" if discord_sent else "not posted"})
    brief = {
        "text": text,
        "gate": {key: value for key, value in gate.items() if key != "summary"} | {"summary": gate.get("summary")},
        "scan": {
            "ok": scan.get("ok"),
            "summary": scan.get("summary"),
            "signals_found": scan_data.get("signals_found"),
            "data_quality": scan_data.get("data_quality"),
            "data_quality_reasons": scan_data.get("data_quality_reasons"),
            "scan_blocked": scan_data.get("scan_blocked"),
            "headline": scan_data.get("headline"),
            "correlation_guard_mode": scan_data.get("correlation_guard_mode"),
            "correlation_guard_would_demote": scan_data.get("correlation_guard_would_demote"),
            "correlation_guard_demoted": scan_data.get("correlation_guard_demoted"),
        },
        "names": public_names,
        "filtered_count": len(dropped),
        "backtest": backtest.get("data") | {"summary": backtest.get("summary"), "ok": backtest.get("ok")},
        "equity": None if equity is None else {"ok": equity.get("ok"), "summary": equity.get("summary"), "data": equity.get("data")},
        "discord_sent": discord_sent,
        "agents": agents,
    }
    try:
        append_live_stack_watch(skill_dir, brief)
    except Exception as exc:
        LOG.warning("Live stack watch log failed: %s", exc)
    return brief


def desk_slot_key(now: datetime, *, hour: int, minute: int) -> str | None:
    """Weekday slot id for the desk clock. Other minutes return None."""
    if now.weekday() >= 5 or now.hour != hour or now.minute != minute:
        return None
    return now.strftime("%Y-%m-%dT%H%M")


def claim_desk_slot(skill_dir: Path, key: str) -> bool:
    """True the first time this slot is claimed. Stops a second process from posting."""
    path = Path(skill_dir) / "operator_desk_slot.txt"
    try:
        if path.is_file() and path.read_text(encoding="utf-8").strip() == key:
            return False
        path.write_text(key + "\n", encoding="utf-8")
    except Exception as exc:
        LOG.warning("Operator desk slot claim failed: %s", exc)
        return False
    return True


def tick_operator_desk(skill_dir: Path, now: datetime | None = None) -> bool:
    """Start the desk when the New York clock hits the slot. Returns True if this process claimed it."""
    from zoneinfo import ZoneInfo

    from config import get_operator_desk_hour, get_operator_desk_minute, get_operator_desk_mode

    if get_operator_desk_mode(skill_dir) == "off":
        return False
    current = now or datetime.now(ZoneInfo("America/New_York"))
    key = desk_slot_key(
        current,
        hour=get_operator_desk_hour(skill_dir),
        minute=get_operator_desk_minute(skill_dir),
    )
    if key is None or not claim_desk_slot(skill_dir, key):
        return False

    def _go() -> None:
        try:
            result = run_scheduled_desk(skill_dir)
            LOG.info("Operator desk finished: skipped=%s", result.get("skipped"))
        except Exception as exc:
            LOG.warning("Operator desk failed: %s", exc)

    import threading

    threading.Thread(target=_go, daemon=True, name="operator-desk").start()
    return True


def run_scheduled_desk(skill_dir: Path) -> dict[str, Any]:
    """Weekday entry used by the bot clock. No-op when the desk mode is off."""
    import os

    from config import (
        get_operator_desk_base_url,
        get_operator_desk_mode,
        get_operator_desk_notify,
        get_operator_desk_queue_backtest,
        get_operator_desk_run_scan,
        get_operator_desk_scan_timeout_sec,
    )

    mode = get_operator_desk_mode(skill_dir)
    if mode == "off":
        return {"skipped": True, "reason": "OPERATOR_DESK_MODE=off"}
    tools = DashboardTools(
        get_operator_desk_base_url(skill_dir),
        api_key=os.environ.get("WEB_API_KEY", "").strip(),
    )
    LOG.info("Operator desk starting: mode=%s", mode)
    return run_operator_desk(
        tools,
        skill_dir=skill_dir,
        run_scan=get_operator_desk_run_scan(skill_dir),
        queue_backtest=get_operator_desk_queue_backtest(skill_dir),
        notify_discord=get_operator_desk_notify(skill_dir),
        scan_timeout_s=get_operator_desk_scan_timeout_sec(skill_dir),
    )
