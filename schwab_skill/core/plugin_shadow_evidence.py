"""Append-only ledger for REGIME_V2 / CORRELATION_GUARD shadow scan evidence.

Wave B hygiene: collect would-* / score baselines across sessions before any
LIVE promotion. Does not change plugin behavior.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

TZ_NY = ZoneInfo("America/New_York")
RTH_OPEN_MINUTES = 9 * 60 + 30
RTH_CLOSE_MINUTES = 16 * 60

LOG = logging.getLogger(__name__)

LEDGER_DIRNAME = "plugin_shadow_evidence"
LEDGER_FILENAME = "shadow_scans.jsonl"


def shadow_evidence_dir(skill_dir: Path) -> Path:
    return Path(skill_dir) / "validation_artifacts" / LEDGER_DIRNAME


def ledger_path(skill_dir: Path) -> Path:
    return shadow_evidence_dir(skill_dir) / LEDGER_FILENAME


def _parse_scan_datetime(raw: Any) -> datetime | None:
    text = str(raw or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(TZ_NY)


def evidence_session_date(row: dict[str, Any]) -> str | None:
    """ET calendar date (YYYY-MM-DD) for a ledger row, or None if unparseable."""
    parsed = _parse_scan_datetime(row.get("scan_at") or row.get("ts"))
    if parsed is None:
        return None
    return parsed.date().isoformat()


def evidence_session_is_rth(row: dict[str, Any] | str | None) -> bool:
    """True when scan_at/ts is a weekday 09:30–16:00 America/New_York.

    Fail-closed: missing or unparseable timestamps are not RTH.
    """
    if isinstance(row, dict):
        raw = row.get("scan_at") or row.get("ts")
    else:
        raw = row
    parsed = _parse_scan_datetime(raw)
    if parsed is None:
        return False
    if parsed.weekday() >= 5:
        return False
    minutes = parsed.hour * 60 + parsed.minute
    return RTH_OPEN_MINUTES <= minutes < RTH_CLOSE_MINUTES


def _pead_primary_shadow_block(diagnostics: dict[str, Any]) -> dict[str, Any]:
    eff = (
        str(
            diagnostics.get("strategy_pead_primary_effective_mode")
            or diagnostics.get("strategy_pead_primary_mode")
            or "off"
        )
        .strip()
        .lower()
    )
    shadow_names = diagnostics.get("pead_primary_shadow_names") or []
    listed = len(shadow_names) if isinstance(shadow_names, list) else 0
    try:
        from core.pead_primary_shadow_compare import build_pead_canary_sleeve_block

        canary = build_pead_canary_sleeve_block(diagnostics)
    except Exception:
        canary = {
            "executable": False,
            "enablement_ready": False,
            "n_names": 0,
            "tickers": [],
        }
    return {
        "mode": str(diagnostics.get("strategy_pead_primary_mode") or "off").strip().lower(),
        "effective_mode": eff,
        "live_coerced_to_shadow": int(diagnostics.get("pead_primary_live_coerced_to_shadow") or 0),
        "evaluated": int(diagnostics.get("pead_primary_evaluated") or 0),
        "admitted": int(diagnostics.get("pead_primary_admitted") or 0),
        "overlap_with_stage2": int(diagnostics.get("overlap_with_stage2") or 0),
        "would_rank_top_n": int(diagnostics.get("pead_primary_would_rank_top_n") or 0),
        "would_rank_capacity_top_n": int(diagnostics.get("pead_primary_would_rank_capacity_top_n") or 0),
        "capacity_rank_arm": diagnostics.get("pead_primary_capacity_rank_arm"),
        "shadow_sort_key": diagnostics.get("pead_primary_shadow_sort_key"),
        "shadow_truncated": int(diagnostics.get("pead_primary_shadow_truncated") or 0),
        "shadow_names_listed": listed,
        "lookback_days": diagnostics.get("pead_primary_lookback_days"),
        "canary_sleeve": canary,
        "executable_still_stage2_only": True,
    }


def _diag_mode(diagnostics: dict[str, Any], *keys: str) -> str:
    for key in keys:
        raw = diagnostics.get(key)
        if raw is None:
            continue
        text = str(raw).strip().lower()
        if text:
            return text
    return "off"


def _entry_timing_shadow_block(diagnostics: dict[str, Any]) -> dict[str, Any]:
    mode = _diag_mode(diagnostics, "entry_timing_shadow_mode")
    return {
        "mode": mode,
        "effective_mode": mode,
        "live_enforced": int(diagnostics.get("entry_timing_live_enforced") or 0),
        "blocked": int(diagnostics.get("entry_timing_blocked") or 0),
        "would_filter_any": int(diagnostics.get("entry_shadow_would_filter_any") or 0),
        "would_filter_breakout_buffer": int(
            diagnostics.get("entry_shadow_would_filter_breakout_buffer") or 0
        ),
    }


def _rank_filter_v2_shadow_block(diagnostics: dict[str, Any]) -> dict[str, Any]:
    mode = _diag_mode(diagnostics, "rank_filter_v2_mode")
    evaluated = int(diagnostics.get("rank_filter_v2_evaluated") or 0)
    dropped = int(diagnostics.get("rank_filter_v2_dropped") or 0)
    would_drop = int(diagnostics.get("rank_filter_v2_would_drop") or dropped or 0)
    return {
        "mode": mode,
        "effective_mode": mode,
        "evaluated": evaluated,
        "dropped": dropped,
        "would_drop": would_drop,
        "min_percentile": diagnostics.get("rank_filter_v2_min_percentile"),
    }


def _prob_rank_shadow_block(diagnostics: dict[str, Any]) -> dict[str, Any]:
    mode = _diag_mode(diagnostics, "prob_rank_mode")
    evaluated = int(
        diagnostics.get("prob_rank_evaluated") or diagnostics.get("prob_rank_scored") or 0
    )
    selected = int(
        diagnostics.get("prob_rank_selected") or diagnostics.get("prob_rank_would_keep") or 0
    )
    return {
        "mode": mode,
        "effective_mode": mode,
        "evaluated": evaluated,
        "selected": selected,
        "would_drop": int(diagnostics.get("prob_rank_would_drop") or 0),
        "unscored": int(diagnostics.get("prob_rank_unscored") or 0),
    }


def build_plugin_shadow_record(
    diagnostics: dict[str, Any],
    *,
    scan_at: str | None = None,
    signals_found: int | None = None,
    scan_label: str | None = None,
) -> dict[str, Any] | None:
    """Build one ledger row from scan diagnostics.

    Returns None when every wired plugin mode is off.
    """
    regime_mode = _diag_mode(diagnostics, "regime_v2_mode")
    corr_mode = _diag_mode(diagnostics, "correlation_guard_mode")
    pead_eff = _diag_mode(
        diagnostics,
        "strategy_pead_primary_effective_mode",
        "strategy_pead_primary_mode",
    )
    entry_mode = _diag_mode(diagnostics, "entry_timing_shadow_mode")
    rank_mode = _diag_mode(diagnostics, "rank_filter_v2_mode")
    prob_mode = _diag_mode(diagnostics, "prob_rank_mode")
    if all(
        m == "off"
        for m in (regime_mode, corr_mode, pead_eff, entry_mode, rank_mode, prob_mode)
    ):
        return None

    rank_eval = int(diagnostics.get("rank_filter_v2_evaluated") or 0)
    rank_drop = int(diagnostics.get("rank_filter_v2_dropped") or 0)
    rank_ret = round(100.0 * (rank_eval - rank_drop) / rank_eval, 2) if rank_eval else None

    multi_sleeve: dict[str, Any] = {"mode": "off", "enabled": False}
    try:
        from config import (
            get_allocator_mode,
            get_r3_capacity_shadow,
            get_s1_live_enabled,
            get_s1_promoted_cap,
        )
        from core.multi_sleeve_pipeline import build_multi_sleeve_diagnostics

        alloc_mode = str(get_allocator_mode() or "off").strip().lower()
        pead_block = _pead_primary_shadow_block(diagnostics)
        canary = pead_block.get("canary_sleeve") if isinstance(pead_block, dict) else None
        # signals may not be in diagnostics; use empty S0 list — pack still useful for S1 paper
        multi_sleeve = build_multi_sleeve_diagnostics(
            signals=[],
            pead_canary=canary if isinstance(canary, dict) else None,
            mode=alloc_mode,
            data_quality=str(diagnostics.get("data_quality") or "ok"),
            s1_live=bool(get_s1_live_enabled()),
            s1_promoted_cap=bool(get_s1_promoted_cap()),
            r3_capacity_shadow=bool(get_r3_capacity_shadow()),
        )
    except Exception as exc:
        multi_sleeve = {"mode": "error", "enabled": False, "error": str(exc)}

    return {
        "ts": datetime.now(timezone.utc).isoformat(),
        "scan_at": scan_at,
        "scan_label": scan_label,
        "signals_found": signals_found,
        "data_quality": diagnostics.get("data_quality"),
        "stack": {
            "entry_timing_live_enforced": diagnostics.get("entry_timing_live_enforced"),
            "entry_timing_blocked": diagnostics.get("entry_timing_blocked"),
            "pts_52w_cap_mode": diagnostics.get("pts_52w_cap_mode"),
            "pts_52w_cap_blocked": diagnostics.get("pts_52w_cap_blocked"),
            "rank_filter_v2_mode": diagnostics.get("rank_filter_v2_mode"),
            "rank_filter_v2_min_percentile": diagnostics.get("rank_filter_v2_min_percentile"),
            "rank_filter_v2_evaluated": rank_eval,
            "rank_filter_v2_dropped": rank_drop,
            "rank_retention_pct": rank_ret,
        },
        "regime_v2": {
            "mode": regime_mode,
            "score": diagnostics.get("regime_v2_score"),
            "bucket": diagnostics.get("regime_v2_bucket"),
            "blocked": int(diagnostics.get("regime_v2_blocked") or 0),
            "entry_min_score": diagnostics.get("regime_v2_entry_min_score"),
        },
        "correlation_guard": {
            "mode": corr_mode,
            "would_demote": int(diagnostics.get("correlation_guard_would_demote") or 0),
            "demoted": int(diagnostics.get("correlation_guard_demoted") or 0),
            "pair_demotions": int(diagnostics.get("correlation_guard_pair_demotions") or 0),
        },
        "pead_primary": _pead_primary_shadow_block(diagnostics),
        "entry_timing": _entry_timing_shadow_block(diagnostics),
        "rank_filter_v2": _rank_filter_v2_shadow_block(diagnostics),
        "prob_rank": _prob_rank_shadow_block(diagnostics),
        "multi_sleeve": multi_sleeve,
        "quality_gates_mode": diagnostics.get("quality_gates_mode"),
        "prob_rank_mode": diagnostics.get("prob_rank_mode"),
    }


def append_plugin_shadow_record(skill_dir: Path, record: dict[str, Any]) -> Path:
    path = ledger_path(skill_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, default=str) + "\n")
    return path


def _rewrite_ledger(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            for row in records:
                fh.write(json.dumps(row, default=str) + "\n")
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def upsert_plugin_shadow_record(skill_dir: Path, record: dict[str, Any]) -> dict[str, Any]:
    """Insert or replace the ledger row for this record's ET calendar date.

    Rows without a parseable date always append (legacy undated evidence).
    A Regular Trading Hours (RTH) row is never replaced by a non-RTH scan
    on the same ET date.
    """
    path = ledger_path(skill_dir)
    et_date = evidence_session_date(record)
    existing = load_plugin_shadow_records(skill_dir)
    if not et_date:
        append_plugin_shadow_record(skill_dir, record)
        return {"action": "appended_undated", "path": str(path), "et_date": None}

    replaced = False
    kept_rth = False
    out: list[dict[str, Any]] = []
    new_is_rth = evidence_session_is_rth(record)
    for row in existing:
        if evidence_session_date(row) == et_date:
            if not replaced:
                if evidence_session_is_rth(row) and not new_is_rth:
                    out.append(row)
                    kept_rth = True
                else:
                    out.append(record)
                replaced = True
            continue
        out.append(row)
    if not replaced:
        out.append(record)
        action = "inserted"
    elif kept_rth:
        action = "kept_rth"
    else:
        action = "replaced"
    if action != "kept_rth":
        _rewrite_ledger(path, out)
    return {"action": action, "path": str(path), "et_date": et_date}


def load_plugin_shadow_records(skill_dir: Path) -> list[dict[str, Any]]:
    path = ledger_path(skill_dir)
    if not path.exists():
        return []
    out: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            LOG.warning("Skipping corrupt plugin shadow ledger line")
            continue
        if isinstance(row, dict):
            out.append(row)
    return out


def summarize_plugin_shadow_records(records: list[dict[str, Any]]) -> dict[str, Any]:
    n = len(records)
    if n == 0:
        return {"n_scans": 0}

    regime_scores = [
        float(r["regime_v2"]["score"])
        for r in records
        if isinstance(r.get("regime_v2"), dict) and r["regime_v2"].get("score") is not None
    ]
    would_demote = [int((r.get("correlation_guard") or {}).get("would_demote") or 0) for r in records]
    dq_ok = sum(1 for r in records if str(r.get("data_quality") or "").lower() == "ok")
    pead_rows = [
        r.get("pead_primary")
        for r in records
        if isinstance(r.get("pead_primary"), dict) and str(r["pead_primary"].get("effective_mode") or "off") != "off"
    ]
    pead_admitted = [int(p.get("admitted") or 0) for p in pead_rows]
    pead_dq_ok = sum(
        1
        for r in records
        if str(r.get("data_quality") or "").lower() == "ok"
        and isinstance(r.get("pead_primary"), dict)
        and str(r["pead_primary"].get("effective_mode") or "off") != "off"
    )
    return {
        "n_scans": n,
        "data_quality_ok_n": dq_ok,
        "regime_v2_score_mean": round(sum(regime_scores) / len(regime_scores), 3) if regime_scores else None,
        "regime_v2_score_min": round(min(regime_scores), 3) if regime_scores else None,
        "regime_v2_score_max": round(max(regime_scores), 3) if regime_scores else None,
        "correlation_would_demote_mean": round(sum(would_demote) / n, 3),
        "correlation_would_demote_max": max(would_demote) if would_demote else 0,
        "pead_primary_shadow_n": len(pead_rows),
        "pead_primary_dq_ok_n": pead_dq_ok,
        "pead_primary_admitted_mean": (round(sum(pead_admitted) / len(pead_admitted), 3) if pead_admitted else None),
        "latest_scan_at": records[-1].get("scan_at") or records[-1].get("ts"),
    }
