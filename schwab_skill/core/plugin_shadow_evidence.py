"""Append-only ledger for REGIME_V2 / CORRELATION_GUARD shadow scan evidence.

Wave B hygiene: collect would-* / score baselines across sessions before any
LIVE promotion. Does not change plugin behavior.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

LOG = logging.getLogger(__name__)

LEDGER_DIRNAME = "plugin_shadow_evidence"
LEDGER_FILENAME = "shadow_scans.jsonl"


def shadow_evidence_dir(skill_dir: Path) -> Path:
    return Path(skill_dir) / "validation_artifacts" / LEDGER_DIRNAME


def ledger_path(skill_dir: Path) -> Path:
    return shadow_evidence_dir(skill_dir) / LEDGER_FILENAME


def _pead_primary_shadow_block(diagnostics: dict[str, Any]) -> dict[str, Any]:
    eff = str(
        diagnostics.get("strategy_pead_primary_effective_mode")
        or diagnostics.get("strategy_pead_primary_mode")
        or "off"
    ).strip().lower()
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
        "would_rank_capacity_top_n": int(
            diagnostics.get("pead_primary_would_rank_capacity_top_n") or 0
        ),
        "capacity_rank_arm": diagnostics.get("pead_primary_capacity_rank_arm"),
        "shadow_sort_key": diagnostics.get("pead_primary_shadow_sort_key"),
        "shadow_truncated": int(diagnostics.get("pead_primary_shadow_truncated") or 0),
        "shadow_names_listed": listed,
        "lookback_days": diagnostics.get("pead_primary_lookback_days"),
        "canary_sleeve": canary,
        "executable_still_stage2_only": True,
    }


def build_plugin_shadow_record(
    diagnostics: dict[str, Any],
    *,
    scan_at: str | None = None,
    signals_found: int | None = None,
    scan_label: str | None = None,
) -> dict[str, Any] | None:
    """Build one ledger row from scan diagnostics.

    Returns None when regime, correlation, and PEAD-primary modes are all off.
    """
    regime_mode = str(diagnostics.get("regime_v2_mode") or "off").strip().lower()
    corr_mode = str(diagnostics.get("correlation_guard_mode") or "off").strip().lower()
    pead_eff = str(
        diagnostics.get("strategy_pead_primary_effective_mode")
        or diagnostics.get("strategy_pead_primary_mode")
        or "off"
    ).strip().lower()
    if regime_mode == "off" and corr_mode == "off" and pead_eff == "off":
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
    would_demote = [
        int((r.get("correlation_guard") or {}).get("would_demote") or 0) for r in records
    ]
    dq_ok = sum(1 for r in records if str(r.get("data_quality") or "").lower() == "ok")
    pead_rows = [
        r.get("pead_primary")
        for r in records
        if isinstance(r.get("pead_primary"), dict)
        and str(r["pead_primary"].get("effective_mode") or "off") != "off"
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
        "pead_primary_admitted_mean": (
            round(sum(pead_admitted) / len(pead_admitted), 3) if pead_admitted else None
        ),
        "latest_scan_at": records[-1].get("scan_at") or records[-1].get("ts"),
    }
