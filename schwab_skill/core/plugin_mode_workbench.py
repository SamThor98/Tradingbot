"""Plugin mode workbench: roster, testing gaps, local mode writes.

Local dashboard only for writes. SaaS callers should pass writes_enabled=False
and omit the write API — this module is the seam for a future tenant override store.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

LOG = logging.getLogger(__name__)

DEFAULT_SESSION_TARGET = 5
PF_MEAN_FLOOR = 1.20
PF_WORST_ERA_FLOOR = 1.00
LEDGER_MAX_AGE_HOURS = 24.0
AUDIT_DIRNAME = "plugin_mode_audit"
AUDIT_FILENAME = "mode_writes.jsonl"

# Module-level freeze after soft-reload verify failure (process local).
_RESTART_REQUIRED = False
_RESTART_DETAIL = ""

STANDARD_MODES = ("off", "shadow", "live")
QUALITY_GATES_MODES = ("off", "shadow", "soft", "hard")


@dataclass(frozen=True)
class PluginSpec:
    id: str
    label: str
    env_key: str
    getter: Callable[[Path | None], str]
    scope: str
    purpose: str = ""
    session_target: int = DEFAULT_SESSION_TARGET
    allowed_modes: tuple[str, ...] = STANDARD_MODES
    evidence_key: str | None = None  # key inside plugin_shadow_evidence rows
    artifact_name: str | None = None  # validation_artifacts/*.json basename template
    artifact_scenario_hints: tuple[str, ...] = ()


COUNTER_PLAIN: dict[str, str] = {
    "confirmed": "confirmed",
    "would_block": "would have blocked",
    "blocked": "blocked",
    "would_demote": "would have demoted",
    "demoted": "demoted",
    "scan_blocked": "scan blocks",
    "exec_blocked": "execution blocks",
    "exec_sized": "size reductions",
    "scored": "scored",
    "would_filter": "would have filtered",
    "filtered": "filtered",
    "unavailable": "data unavailable",
    "errors": "errors",
    "would_partial_tp": "would take partial profit",
    "would_move_stop": "would move stop",
    "would_time_stop": "would time-stop",
    "would_drop": "would have dropped",
    "evaluated": "evaluated",
    "would_drop_any": "would have dropped",
    "stage2_would_filter": "uptrend shadow filters",
    "admitted": "admitted to paper sleeve",
    "would_rank_top_n": "would rank in top-N",
    "selected": "selected",
}

SCOPE_PLAIN: dict[str, str] = {
    "scan": "During scan",
    "execution": "During trades",
    "scan+execution": "Scan + trades",
}


def _import_getters() -> dict[str, Callable[[Path | None], str]]:
    from config import (
        get_allocator_mode,
        get_confluence_gate_mode,
        get_correlation_guard_mode,
        get_early_stop_gate_mode,
        get_entry_timing_shadow_mode,
        get_event_risk_mode,
        get_exec_quality_mode,
        get_exit_manager_mode,
        get_management_integrity_mode,
        get_meta_policy_mode,
        get_pre_trade_gates_mode,
        get_prob_rank_mode,
        get_pts_52w_cap_mode,
        get_quality_gates_mode,
        get_rank_filter_v2_mode,
        get_rank_score_v2_mode,
        get_regime_v2_mode,
        get_signal_edge_shadow_mode,
        get_strategy_ensemble_mode,
        get_strategy_pead_primary_mode,
        get_strategy_pullback_mode,
        get_uncertainty_mode,
    )

    return {
        "confluence_gate": get_confluence_gate_mode,
        "correlation_guard": get_correlation_guard_mode,
        "regime_v2": get_regime_v2_mode,
        "management_integrity": get_management_integrity_mode,
        "exit_manager": get_exit_manager_mode,
        "quality_gates": get_quality_gates_mode,
        "rank_filter_v2": get_rank_filter_v2_mode,
        "rank_score_v2": get_rank_score_v2_mode,
        "entry_timing": get_entry_timing_shadow_mode,
        "signal_edge": get_signal_edge_shadow_mode,
        "pts_52w_cap": get_pts_52w_cap_mode,
        "exec_quality": get_exec_quality_mode,
        "event_risk": get_event_risk_mode,
        "strategy_pead_primary": get_strategy_pead_primary_mode,
        "prob_rank": get_prob_rank_mode,
        "allocator": get_allocator_mode,
        "early_stop_gate": get_early_stop_gate_mode,
        "pre_trade_gates": get_pre_trade_gates_mode,
        "strategy_ensemble": get_strategy_ensemble_mode,
        "strategy_pullback": get_strategy_pullback_mode,
        "meta_policy": get_meta_policy_mode,
        "uncertainty": get_uncertainty_mode,
    }


def _catalog_rows(g: dict[str, Callable[[Path | None], str]]) -> list[PluginSpec]:
    return [
        PluginSpec(
            "confluence_gate",
            "Confluence gate",
            "CONFLUENCE_GATE_MODE",
            g["confluence_gate"],
            "scan",
            purpose="Requires multiple confirming signals before a name stays qualified.",
        ),
        PluginSpec(
            "correlation_guard",
            "Correlation guard",
            "CORRELATION_GUARD_MODE",
            g["correlation_guard"],
            "scan",
            purpose="Demotes highly correlated pairs so the book is not one crowded bet.",
            evidence_key="correlation_guard",
        ),
        PluginSpec(
            "regime_v2",
            "Regime v2",
            "REGIME_V2_MODE",
            g["regime_v2"],
            "scan+execution",
            purpose="Slows or blocks entries when the market regime score is weak.",
            evidence_key="regime_v2",
        ),
        PluginSpec(
            "management_integrity",
            "Management integrity",
            "MANAGEMENT_INTEGRITY_MODE",
            g["management_integrity"],
            "scan",
            purpose="Flags weak management-integrity scores before they become live risk.",
        ),
        PluginSpec(
            "exit_manager",
            "Exit manager",
            "EXIT_MANAGER_MODE",
            g["exit_manager"],
            "execution",
            purpose="Manages partial takes, stop moves, and time stops after entry.",
            artifact_name="signal_stack_counterfactual_{run_id}.json",
            artifact_scenario_hints=("exit_grace_breakout_buffer_0.010", "exit_grace"),
        ),
        PluginSpec(
            "quality_gates",
            "Quality gates",
            "QUALITY_GATES_MODE",
            g["quality_gates"],
            "scan",
            purpose="Drops weak breakouts and low-quality setups from the shortlist.",
            allowed_modes=QUALITY_GATES_MODES,
        ),
        PluginSpec(
            "rank_filter_v2",
            "Rank filter v2",
            "RANK_FILTER_V2_MODE",
            g["rank_filter_v2"],
            "scan",
            purpose="Keeps only the strongest percentile of ranked candidates.",
            artifact_name="rank_filter_counterfactual_{run_id}.json",
        ),
        PluginSpec(
            "rank_score_v2",
            "Rank score v2",
            "RANK_SCORE_V2_MODE",
            g["rank_score_v2"],
            "scan",
            purpose="Uses the v2 ranking score instead of the legacy composite.",
        ),
        PluginSpec(
            "entry_timing",
            "Entry timing",
            "ENTRY_TIMING_SHADOW_MODE",
            g["entry_timing"],
            "scan",
            purpose="Requires a breakout buffer so late/chasing entries are filtered.",
            artifact_name="entry_timing_shadow_counterfactual_{run_id}.json",
        ),
        PluginSpec(
            "signal_edge",
            "Signal edge shadow",
            "SIGNAL_EDGE_SHADOW_MODE",
            g["signal_edge"],
            "scan",
            purpose="Annotates rank/stage-2 drop counters without changing fills.",
            artifact_name="signal_edge_shadow_counterfactual_{run_id}.json",
        ),
        PluginSpec(
            "pts_52w_cap",
            "PTS 52w cap",
            "PTS_52W_CAP_MODE",
            g["pts_52w_cap"],
            "scan",
            purpose="Caps how extended a name can be vs its 52-week range at Stage A.",
            artifact_name="signal_stack_counterfactual_{run_id}.json",
        ),
        PluginSpec(
            "exec_quality",
            "Exec quality",
            "EXEC_QUALITY_MODE",
            g["exec_quality"],
            "execution",
            purpose="Guards fill quality (spread/slippage style checks) at order time.",
        ),
        PluginSpec(
            "event_risk",
            "Event risk",
            "EVENT_RISK_MODE",
            g["event_risk"],
            "scan",
            purpose="Avoids entries into known event windows (earnings, etc.).",
        ),
        PluginSpec(
            "strategy_pead_primary",
            "PEAD primary",
            "STRATEGY_PEAD_PRIMARY_MODE",
            g["strategy_pead_primary"],
            "scan",
            purpose="Paper earnings-drift sleeve (not executable until explicitly allowed).",
            evidence_key="pead_primary",
        ),
        PluginSpec(
            "prob_rank",
            "Prob rank",
            "PROB_RANK_MODE",
            g["prob_rank"],
            "scan",
            purpose="Ranks candidates by model probability instead of heuristic score alone.",
        ),
        PluginSpec(
            "allocator",
            "Allocator",
            "ALLOCATOR_MODE",
            g["allocator"],
            "scan",
            purpose="Multi-sleeve capital allocation overlay (shadow first).",
        ),
        PluginSpec(
            "early_stop_gate",
            "Early stop gate",
            "EARLY_STOP_GATE_MODE",
            g["early_stop_gate"],
            "scan",
            purpose="Surfaces early-stopout risk cohorts before promotion.",
            artifact_name="early_stopout_cohorts_{run_id}.json",
        ),
        PluginSpec(
            "pre_trade_gates",
            "Pre-trade gates",
            "PRE_TRADE_GATES_MODE",
            g["pre_trade_gates"],
            "execution",
            purpose="Final pre-trade checks before an order is sent.",
        ),
        PluginSpec(
            "strategy_ensemble",
            "Strategy ensemble",
            "STRATEGY_ENSEMBLE_MODE",
            g["strategy_ensemble"],
            "scan",
            purpose="Blends strategy votes instead of a single strategy label.",
        ),
        PluginSpec(
            "strategy_pullback",
            "Strategy pullback",
            "STRATEGY_PULLBACK_MODE",
            g["strategy_pullback"],
            "scan",
            purpose="Pullback-entry overlay on top of the base momentum path.",
        ),
        PluginSpec(
            "meta_policy",
            "Meta policy",
            "META_POLICY_MODE",
            g["meta_policy"],
            "scan",
            purpose="Higher-level policy overlay that can demote or size candidates.",
        ),
        PluginSpec(
            "uncertainty",
            "Uncertainty",
            "UNCERTAINTY_MODE",
            g["uncertainty"],
            "scan",
            purpose="Down-weights or flags high-uncertainty model outputs.",
        ),
    ]


def plugin_catalog() -> list[PluginSpec]:
    return _catalog_rows(_import_getters())


def plugin_spec_by_id(plugin_id: str) -> PluginSpec | None:
    needle = str(plugin_id or "").strip().lower()
    for spec in plugin_catalog():
        if spec.id == needle:
            return spec
    return None


def is_live_tier(mode: str, *, allowed_modes: tuple[str, ...] = STANDARD_MODES) -> bool:
    m = str(mode or "off").strip().lower()
    if "soft" in allowed_modes or "hard" in allowed_modes:
        return m in {"live", "soft", "hard"}
    return m == "live"


def normalize_requested_mode(spec: PluginSpec, mode: str) -> str:
    m = str(mode or "").strip().lower()
    if m == "live" and "live" not in spec.allowed_modes and "soft" in spec.allowed_modes:
        return "soft"
    if m not in spec.allowed_modes:
        raise ValueError(
            f"Invalid mode {mode!r} for {spec.id}; allowed: {', '.join(spec.allowed_modes)}"
        )
    return m


def restart_required() -> bool:
    return bool(_RESTART_REQUIRED)


def restart_detail() -> str:
    return str(_RESTART_DETAIL or "")


def clear_restart_required() -> None:
    global _RESTART_REQUIRED, _RESTART_DETAIL
    _RESTART_REQUIRED = False
    _RESTART_DETAIL = ""


def mark_restart_required(detail: str) -> None:
    global _RESTART_REQUIRED, _RESTART_DETAIL
    _RESTART_REQUIRED = True
    _RESTART_DETAIL = str(detail or "Mode write persisted but process reload did not verify.")


def audit_path(skill_dir: Path) -> Path:
    return Path(skill_dir) / "validation_artifacts" / AUDIT_DIRNAME / AUDIT_FILENAME


def append_mode_audit(skill_dir: Path, row: dict[str, Any]) -> Path:
    path = audit_path(skill_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        **row,
        "ts": row.get("ts") or datetime.now(timezone.utc).isoformat(),
    }
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(payload, default=str) + "\n")
    return path


def _i(src: dict[str, Any], key: str) -> int:
    try:
        return int(src.get(key) or 0)
    except (TypeError, ValueError):
        return 0


def _diag_mode(diag: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        raw = diag.get(key)
        if isinstance(raw, dict):
            nested = raw.get("mode")
            if nested is not None and str(nested).strip():
                return str(nested).strip().lower()
        elif raw is not None and str(raw).strip():
            return str(raw).strip().lower()
    return None


def resolve_modes(skill_dir: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for spec in plugin_catalog():
        try:
            out[spec.id] = str(spec.getter(skill_dir) or "off").strip().lower()
        except Exception as exc:
            LOG.warning("Failed to resolve mode for %s: %s", spec.id, exc)
            out[spec.id] = "off"
    return out


def count_ok_sessions(records: list[dict[str, Any]], spec: PluginSpec) -> int:
    """Count RTH/ok evidence sessions for a plugin (fail-closed when absent)."""
    if not spec.evidence_key:
        return 0
    n = 0
    for row in records:
        if str(row.get("data_quality") or "").lower() != "ok":
            continue
        block = row.get(spec.evidence_key)
        if not isinstance(block, dict):
            continue
        mode = str(block.get("effective_mode") or block.get("mode") or "").strip().lower()
        if mode in {"shadow", "live", "soft", "hard"}:
            n += 1
    return n


def _load_promotion_helpers():
    import sys

    scripts_dir = Path(__file__).resolve().parents[1] / "scripts"
    scripts_str = str(scripts_dir)
    if scripts_str not in sys.path:
        sys.path.insert(0, scripts_str)
    import promotion_ledger  # type: ignore

    return promotion_ledger


def ledger_target(spec: PluginSpec) -> str:
    return f"{spec.env_key}=live"


def find_ledger_approval(spec: PluginSpec) -> dict[str, Any] | None:
    pl = _load_promotion_helpers()
    rows = pl.read_entries()
    ok, _msg = pl.verify_chain(rows)
    if not ok:
        return None
    # Accept either =live or quality soft/hard historical targets.
    for target in (ledger_target(spec), f"{spec.env_key}=soft", f"{spec.env_key}=hard"):
        found = pl.find_recent_approval(
            target, max_age_hours=LEDGER_MAX_AGE_HOURS, entries=rows
        )
        if found:
            return found
    return None


def base_signal_gate_status(skill_dir: Path, *, run_id: str = "control_legacy_aug") -> dict[str, Any]:
    """Fail-closed base-signal PF floors from stack counterfactual artifact."""
    path = Path(skill_dir) / "validation_artifacts" / f"signal_stack_counterfactual_{run_id}.json"
    if not path.exists():
        return {
            "ok": False,
            "detail": "Missing signal_stack_counterfactual artifact",
            "pf_mean": None,
            "worst_era_pf": None,
        }
    try:
        art = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"ok": False, "detail": f"Unreadable stack artifact: {exc}", "pf_mean": None, "worst_era_pf": None}
    scenarios = art.get("scenarios") if isinstance(art, dict) else None
    if not isinstance(scenarios, dict):
        return {"ok": False, "detail": "Stack artifact has no scenarios", "pf_mean": None, "worst_era_pf": None}

    preferred_keys = (
        "bare_stage2",
        "bare",
        "control",
        "control_legacy",
        "exit_grace_breakout_buffer_0.010",
    )
    row: dict[str, Any] | None = None
    used_key = None
    for key in preferred_keys:
        cand = scenarios.get(key)
        if isinstance(cand, dict):
            row = cand
            used_key = key
            break
    if row is None:
        for key, cand in scenarios.items():
            if isinstance(cand, dict) and cand.get("pf_mean") is not None:
                row = cand
                used_key = key
                break
    if row is None:
        return {"ok": False, "detail": "No scenario rows with PF", "pf_mean": None, "worst_era_pf": None}

    try:
        pf_mean = float(row.get("pf_mean"))
        worst = float(row.get("worst_era_pf"))
    except (TypeError, ValueError):
        return {
            "ok": False,
            "detail": f"Non-numeric PF in scenario {used_key}",
            "pf_mean": row.get("pf_mean"),
            "worst_era_pf": row.get("worst_era_pf"),
        }
    ok = pf_mean >= PF_MEAN_FLOOR and worst >= PF_WORST_ERA_FLOOR
    return {
        "ok": ok,
        "detail": (
            f"{used_key}: PF mean {pf_mean:.3f} (need ≥{PF_MEAN_FLOOR}), "
            f"worst-era {worst:.3f} (need ≥{PF_WORST_ERA_FLOOR})"
        ),
        "pf_mean": pf_mean,
        "worst_era_pf": worst,
        "scenario": used_key,
    }


def would_have_band_status(counters: dict[str, Any]) -> dict[str, Any]:
    """Informational band: any would-* activity is expected during shadow."""
    would_keys = [k for k in counters if str(k).startswith("would_") or k in {"scan_blocked", "exec_blocked"}]
    total = sum(_i(counters, k) for k in would_keys)
    if total <= 0:
        return {"ok": True, "detail": "No would-have friction this window", "would_total": 0}
    return {
        "ok": True,
        "detail": f"{total} would-have action(s) logged (evidence gathering)",
        "would_total": total,
    }


def build_live_checklist(
    *,
    skill_dir: Path,
    spec: PluginSpec,
    mode: str,
    counters: dict[str, Any],
    sessions: int,
    session_target: int,
) -> dict[str, Any]:
    base = base_signal_gate_status(skill_dir)
    band = would_have_band_status(counters)
    ledger = find_ledger_approval(spec)
    sessions_ok = sessions >= int(session_target)
    items = [
        {
            "id": "sessions",
            "label": "RTH/ok shadow sessions",
            "ok": sessions_ok,
            "detail": f"{sessions} / {session_target}",
        },
        {
            "id": "band",
            "label": "Would-have band",
            "ok": bool(band.get("ok")),
            "detail": band.get("detail"),
        },
        {
            "id": "base_signal",
            "label": "Base-signal PF floors",
            "ok": bool(base.get("ok")),
            "detail": base.get("detail"),
        },
        {
            "id": "ledger",
            "label": "Signed promotion ledger",
            "ok": ledger is not None,
            "detail": (
                f"seq={ledger.get('seq')} at {ledger.get('ts')}"
                if ledger
                else f"Need signed {ledger_target(spec)} (or mint via LIVE toggle)"
            ),
        },
    ]
    # LIVE hard gate: sessions + base_signal must pass; ledger may be minted in same request.
    hard_ok = sessions_ok and bool(base.get("ok")) and bool(band.get("ok"))
    lacking = [it["id"] for it in items if not it["ok"] and it["id"] != "ledger"]
    if not sessions_ok:
        lacking_summary = f"lacking {session_target - sessions} RTH session(s)"
    elif not base.get("ok"):
        lacking_summary = "base-signal PF floors not met"
    else:
        lacking_summary = "ready for LIVE checklist" if hard_ok else "checklist incomplete"
    return {
        "ready_for_live_write": hard_ok,
        "items": items,
        "lacking": lacking,
        "lacking_summary": lacking_summary,
        "ledger_present": ledger is not None,
        "current_mode": mode,
    }


def _plugin_counters(
    plugin_id: str,
    diag: dict[str, Any],
    events: dict[str, Any],
) -> dict[str, int]:
    if plugin_id == "confluence_gate":
        return {
            "confirmed": _i(diag, "confluence_confirmed"),
            "would_block": _i(diag, "confluence_would_block"),
            "blocked": _i(diag, "confluence_blocked"),
        }
    if plugin_id == "correlation_guard":
        return {
            "would_demote": _i(diag, "correlation_guard_would_demote"),
            "demoted": _i(diag, "correlation_guard_demoted"),
        }
    if plugin_id == "regime_v2":
        return {
            "scan_blocked": _i(diag, "regime_v2_blocked"),
            "exec_blocked": _i(events, "regime_v2_blocked"),
            "exec_sized": _i(events, "regime_v2_sized"),
        }
    if plugin_id == "management_integrity":
        mi = diag.get("management_integrity") if isinstance(diag.get("management_integrity"), dict) else {}
        return {
            "scored": _i(mi, "scored"),
            "would_filter": _i(mi, "would_filter"),
            "unavailable": _i(mi, "unavailable"),
            "errors": _i(mi, "errors"),
        }
    if plugin_id == "exit_manager":
        return {
            "would_partial_tp": _i(events, "exit_manager_shadow_would_partial_tp"),
            "would_move_stop": _i(events, "exit_manager_shadow_would_move_stop"),
            "would_time_stop": _i(events, "exit_manager_shadow_would_time_stop"),
        }
    if plugin_id == "quality_gates":
        return {
            "would_filter": _i(diag, "quality_gates_would_filter"),
            "filtered": _i(diag, "quality_gates_filtered"),
        }
    if plugin_id == "rank_filter_v2":
        return {
            "would_drop": _i(diag, "rank_filter_v2_dropped"),
            "evaluated": _i(diag, "rank_filter_v2_evaluated"),
        }
    if plugin_id == "entry_timing":
        return {
            "would_filter": _i(diag, "entry_shadow_would_filter_any"),
            "blocked": _i(diag, "entry_timing_blocked"),
        }
    if plugin_id == "signal_edge":
        return {
            "would_drop_any": _i(diag, "rank_filter_would_drop_any"),
            "stage2_would_filter": _i(diag, "stage2_shadow_would_filter"),
        }
    if plugin_id == "pts_52w_cap":
        return {"blocked": _i(diag, "pts_52w_cap_blocked")}
    if plugin_id == "strategy_pead_primary":
        return {
            "admitted": _i(diag, "pead_primary_admitted"),
            "would_rank_top_n": _i(diag, "pead_primary_would_rank_top_n"),
            "evaluated": _i(diag, "pead_primary_evaluated"),
        }
    if plugin_id == "prob_rank":
        return {
            "evaluated": _i(diag, "prob_rank_evaluated"),
            "selected": _i(diag, "prob_rank_selected"),
        }
    return {}


def _load_artifact(skill_dir: Path, name: str) -> dict[str, Any] | None:
    path = Path(skill_dir) / "validation_artifacts" / name
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _fmt_pf(val: Any) -> str | None:
    try:
        return f"{float(val):.2f}"
    except (TypeError, ValueError):
        return None


def _extract_backtest_stats(spec: PluginSpec, art: dict[str, Any]) -> dict[str, Any]:
    """Pull a short offline summary from a counterfactual / cohort artifact."""
    pf_mean = None
    worst = None
    retention = None
    passes = None
    detail = None
    label = None

    scenarios = art.get("scenarios") if isinstance(art.get("scenarios"), dict) else None
    if scenarios:
        row = None
        used = None
        for hint in spec.artifact_scenario_hints:
            cand = scenarios.get(hint)
            if isinstance(cand, dict):
                row, used = cand, hint
                break
        if row is None:
            for key, cand in scenarios.items():
                if isinstance(cand, dict) and cand.get("pf_mean") is not None:
                    row, used = cand, key
                    break
        if isinstance(row, dict):
            pf_mean = row.get("pf_mean")
            worst = row.get("worst_era_pf")
            retention = row.get("retention_pct")
            passes = row.get("passes_promotion_gates")
            label = str(row.get("label") or used or "scenario")
    else:
        # Flat recommendation / baseline shapes (entry timing, early stop, rank).
        rec = art.get("recommendation") if isinstance(art.get("recommendation"), dict) else {}
        baseline = art.get("baseline") if isinstance(art.get("baseline"), dict) else {}
        exp = rec.get("breakout_buffer_only") if isinstance(rec.get("breakout_buffer_only"), dict) else None
        src = exp or baseline or rec
        if isinstance(src, dict):
            pf_mean = src.get("pf_mean") or src.get("delta_overlap_pf_mean") or src.get("hold_21_40d_pf")
            worst = src.get("worst_era_pf")
            retention = src.get("retention_pct") or src.get("would_drop_retention_pct")
            if src.get("action"):
                detail = f"Offline action: {src.get('action')}"
            if src.get("early_stopout_pct") is not None:
                detail = (
                    f"Early stopout {src.get('early_stopout_pct')}%"
                    + (f" · {detail}" if detail else "")
                )

    pf_s = _fmt_pf(pf_mean)
    worst_s = _fmt_pf(worst)
    lines: list[str] = []
    if pf_s is not None:
        lines.append(f"PF mean {pf_s}")
    if worst_s is not None:
        lines.append(f"worst-era {worst_s}")
    if retention is not None:
        try:
            lines.append(f"retention {float(retention):.0f}%")
        except (TypeError, ValueError):
            pass
    if passes is True:
        lines.append("clears promotion floors")
    elif passes is False:
        lines.append("below promotion floors")
    if detail:
        lines.append(detail)
    if not lines:
        return {
            "available": False,
            "headline": "No offline numbers in artifact",
            "lines": [],
            "pf_mean": None,
            "worst_era_pf": None,
            "passes_promotion_gates": None,
        }
    headline = " · ".join(lines[:3])
    if label:
        headline = f"{label}: {headline}"
    return {
        "available": True,
        "headline": headline,
        "lines": lines,
        "pf_mean": pf_mean,
        "worst_era_pf": worst,
        "retention_pct": retention,
        "passes_promotion_gates": passes,
        "label": label,
    }


def build_plugin_stats(
    *,
    skill_dir: Path,
    spec: PluginSpec,
    mode: str,
    counters: dict[str, Any],
    sessions: int,
    session_target: int,
    checklist: dict[str, Any],
    run_id: str = "control_legacy_aug",
) -> dict[str, Any]:
    """Plain-language last-scan / evidence / backtest block for one plugin card."""
    scan_lines: list[str] = []
    for key, val in counters.items():
        try:
            n = int(val or 0)
        except (TypeError, ValueError):
            continue
        if n <= 0:
            continue
        label = COUNTER_PLAIN.get(key, key.replace("_", " "))
        scan_lines.append(f"{n} {label}")
    if not scan_lines:
        scan_lines.append("No would-have actions on the last scan")

    if sessions >= session_target:
        evidence_line = f"{sessions} of {session_target} good (RTH/ok) shadow sessions collected"
        evidence_tone = "good"
    elif sessions > 0:
        evidence_line = (
            f"{sessions} of {session_target} good sessions — "
            f"need {session_target - sessions} more before LIVE"
        )
        evidence_tone = "warn"
    elif spec.evidence_key:
        evidence_line = f"No RTH/ok shadow sessions yet (target {session_target})"
        evidence_tone = "warn"
    else:
        evidence_line = "Session ledger not wired for this guardrail yet"
        evidence_tone = "neutral"

    backtest: dict[str, Any] = {
        "available": False,
        "headline": "No offline backtest attached",
        "lines": [],
    }
    if spec.artifact_name:
        art_name = spec.artifact_name.format(run_id=run_id)
        art = _load_artifact(skill_dir, art_name)
        if art:
            backtest = _extract_backtest_stats(spec, art)
            backtest["artifact"] = art_name
        else:
            backtest = {
                "available": False,
                "headline": f"Missing {art_name}",
                "lines": [],
                "artifact": art_name,
            }

    mode_l = str(mode or "off").lower()
    if mode_l == "off":
        next_step = "Turn on Observe only to start collecting would-have evidence."
    elif is_live_tier(mode_l, allowed_modes=spec.allowed_modes):
        next_step = "Already enforced — monitor weekly; demote if behavior looks wrong."
    elif checklist.get("ready_for_live_write"):
        next_step = "Evidence looks ready — expand LIVE checklist, then promote with confirm."
    else:
        next_step = f"Still gathering evidence — {checklist.get('lacking_summary') or 'see checklist'}."

    return {
        "purpose": spec.purpose,
        "scope_plain": SCOPE_PLAIN.get(spec.scope, spec.scope),
        "last_scan": {
            "headline": scan_lines[0],
            "lines": scan_lines,
            "would_total": sum(
                int(v or 0)
                for k, v in counters.items()
                if str(k).startswith("would_") or k in {"scan_blocked", "exec_blocked", "blocked"}
            ),
        },
        "evidence": {
            "headline": evidence_line,
            "tone": evidence_tone,
            "collected": sessions,
            "target": session_target,
        },
        "backtest": backtest,
        "next_step": next_step,
    }


def _diag_mode_for(plugin_id: str, diag: dict[str, Any]) -> str | None:
    mapping = {
        "confluence_gate": ("confluence_gate_mode",),
        "correlation_guard": ("correlation_guard_mode",),
        "regime_v2": ("regime_v2_mode",),
        "management_integrity": ("management_integrity",),
        "quality_gates": ("quality_gates_mode",),
        "rank_filter_v2": ("rank_filter_v2_mode",),
        "rank_score_v2": ("rank_score_v2_mode",),
        "entry_timing": ("entry_timing_shadow_mode",),
        "signal_edge": ("signal_edge_shadow_mode",),
        "pts_52w_cap": ("pts_52w_cap_mode",),
        "strategy_pead_primary": (
            "strategy_pead_primary_effective_mode",
            "strategy_pead_primary_mode",
        ),
        "prob_rank": ("prob_rank_mode",),
        "allocator": ("allocator_mode",),
        "early_stop_gate": ("early_stop_gate_mode",),
        "exec_quality": ("exec_quality_mode",),
        "event_risk": ("event_risk_mode",),
        "exit_manager": ("exit_manager_mode",),
        "pre_trade_gates": ("pre_trade_gates_mode",),
        "strategy_ensemble": ("strategy_ensemble_mode",),
        "strategy_pullback": ("strategy_pullback_mode",),
        "meta_policy": ("meta_policy_mode",),
        "uncertainty": ("uncertainty_mode",),
    }
    keys = mapping.get(plugin_id) or ()
    return _diag_mode(diag, *keys) if keys else None


def build_workbench_payload(
    *,
    skill_dir: Path,
    diagnostics: dict[str, Any] | None,
    execution_summary: dict[str, Any] | None,
    scan_at: str | None = None,
    writes_enabled: bool = False,
) -> dict[str, Any]:
    from core.plugin_shadow_evidence import load_plugin_shadow_records

    diag = diagnostics if isinstance(diagnostics, dict) else {}
    events = (execution_summary or {}).get("events") or {}
    modes = resolve_modes(skill_dir)
    records = load_plugin_shadow_records(skill_dir)

    # Clear restart freeze if process modes now match .env for all catalog keys.
    if _RESTART_REQUIRED:
        try:
            from core.env_local import parse_env_file

            file_env = parse_env_file(Path(skill_dir) / ".env")
            mismatched = False
            for spec in plugin_catalog():
                file_val = str(file_env.get(spec.env_key) or "").strip().lower()
                proc_val = str(modes.get(spec.id) or "").strip().lower()
                if file_val and file_val != proc_val:
                    mismatched = True
                    break
            if not mismatched:
                clear_restart_required()
        except Exception:
            pass

    plugins: list[dict[str, Any]] = []
    working_on = 0
    lacking_sessions = 0
    for spec in plugin_catalog():
        mode = str(
            _diag_mode_for(spec.id, diag) or modes.get(spec.id) or "off"
        ).strip().lower()
        counters = _plugin_counters(spec.id, diag, events)
        sessions = count_ok_sessions(records, spec)
        checklist = build_live_checklist(
            skill_dir=skill_dir,
            spec=spec,
            mode=mode,
            counters=counters,
            sessions=sessions,
            session_target=spec.session_target,
        )
        stats = build_plugin_stats(
            skill_dir=skill_dir,
            spec=spec,
            mode=mode,
            counters=counters,
            sessions=sessions,
            session_target=spec.session_target,
            checklist=checklist,
        )
        tier = "already_live" if is_live_tier(mode, allowed_modes=spec.allowed_modes) else "working_on"
        if tier == "working_on":
            working_on += 1
            if sessions < spec.session_target:
                lacking_sessions += 1
        plugins.append(
            {
                "id": spec.id,
                "label": spec.label,
                "env_key": spec.env_key,
                "mode": mode,
                "scope": spec.scope,
                "purpose": spec.purpose,
                "tier": tier,
                "allowed_modes": list(spec.allowed_modes),
                "counters": counters,
                "sessions": {
                    "collected": sessions,
                    "target": spec.session_target,
                    "ok": sessions >= spec.session_target,
                    "lacking_summary": (
                        None
                        if sessions >= spec.session_target
                        else f"lacking {spec.session_target - sessions} RTH session(s)"
                    ),
                },
                "checklist": checklist,
                "stats": stats,
                "context": (
                    {
                        "score": diag.get("regime_v2_score"),
                        "bucket": diag.get("regime_v2_bucket"),
                    }
                    if spec.id == "regime_v2"
                    else None
                ),
            }
        )

    working = [p for p in plugins if p["tier"] == "working_on"]
    live = [p for p in plugins if p["tier"] == "already_live"]
    return {
        "scan_at": scan_at,
        "execution_window_days": int((execution_summary or {}).get("window_days") or 0) or None,
        "execution_days_present": int((execution_summary or {}).get("days_present") or 0),
        "plugins": plugins,
        "tiers": {
            "working_on": working,
            "already_live": live,
        },
        "summary": {
            "working_on_count": working_on,
            "already_live_count": len(live),
            "lacking_sessions_count": lacking_sessions,
            "teaser": (
                f"{working_on} in progress · {lacking_sessions} lacking sessions"
                if working_on
                else f"{len(live)} live · none in shadow/off work queue"
            ),
        },
        "writes_enabled": bool(writes_enabled),
        "restart_required": restart_required(),
        "restart_detail": restart_detail() if restart_required() else None,
        "promote_phrase_template": "PROMOTE {PLUGIN_ID}",
    }


def promote_confirm_phrase(plugin_id: str) -> str:
    return f"PROMOTE {str(plugin_id or '').strip().upper()}"


def verify_api_key(provided: str | None, *, configured: str | None = None) -> bool:
    expected = (configured if configured is not None else os.environ.get("WEB_API_KEY") or "").strip()
    if not expected:
        # Local without WEB_API_KEY: re-auth skipped (nothing to challenge).
        return True
    return bool(provided) and str(provided).strip() == expected


_PHRASE_RE = re.compile(r"^PROMOTE\s+([A-Z0-9_]+)$")


def phrase_matches(plugin_id: str, phrase: str | None) -> bool:
    raw = str(phrase or "").strip().upper()
    expected = promote_confirm_phrase(plugin_id)
    if raw == expected:
        return True
    m = _PHRASE_RE.match(raw)
    if not m:
        return False
    return m.group(1) == str(plugin_id or "").strip().upper()


def set_plugin_mode(
    *,
    skill_dir: Path,
    plugin_id: str,
    mode: str,
    reason: str = "",
    confirm_phrase: str | None = None,
    api_key: str | None = None,
    confirm_demote: bool = False,
    writes_enabled: bool = True,
) -> dict[str, Any]:
    """Upsert .env, soft-reload, verify; mint ledger for LIVE when checklist green."""
    if not writes_enabled:
        raise PermissionError("Plugin mode writes are disabled in this runtime.")
    if restart_required():
        raise RuntimeError(
            restart_detail()
            or "Restart required before further mode toggles (last reload did not verify)."
        )

    spec = plugin_spec_by_id(plugin_id)
    if spec is None:
        raise KeyError(f"Unknown plugin_id: {plugin_id}")

    requested = normalize_requested_mode(spec, mode)
    current = str(spec.getter(skill_dir) or "off").strip().lower()
    if current == requested:
        return {
            "plugin_id": spec.id,
            "mode": current,
            "changed": False,
            "restart_required": False,
            "ledger_appended": None,
            "message": "Mode already set",
        }

    demoting_from_live = is_live_tier(current, allowed_modes=spec.allowed_modes) and not is_live_tier(
        requested, allowed_modes=spec.allowed_modes
    )
    if demoting_from_live and not confirm_demote:
        raise PermissionError(
            "Demoting out of LIVE requires confirm_demote=true "
            "(this turns off live enforcement)."
        )

    promoting_to_live = is_live_tier(requested, allowed_modes=spec.allowed_modes) and not is_live_tier(
        current, allowed_modes=spec.allowed_modes
    )
    ledger_row = None
    if promoting_to_live:
        if not phrase_matches(spec.id, confirm_phrase):
            raise PermissionError(
                f"LIVE requires typed phrase exactly: {promote_confirm_phrase(spec.id)}"
            )
        if not verify_api_key(api_key):
            raise PermissionError("LIVE requires API-key re-auth (api_key must match WEB_API_KEY).")

        # Build checklist from current evidence (counters empty ok — sessions/base matter).
        from core.plugin_shadow_evidence import load_plugin_shadow_records

        sessions = count_ok_sessions(load_plugin_shadow_records(skill_dir), spec)
        checklist = build_live_checklist(
            skill_dir=skill_dir,
            spec=spec,
            mode=current,
            counters={},
            sessions=sessions,
            session_target=spec.session_target,
        )
        if not checklist.get("ready_for_live_write"):
            raise PermissionError(
                f"LIVE blocked — {checklist.get('lacking_summary')}. "
                "No break-glass in the dashboard."
            )
        if not checklist.get("ledger_present"):
            pl = _load_promotion_helpers()
            ok, msg = pl.verify_chain(pl.read_entries())
            if not ok:
                raise PermissionError(f"Promotion ledger chain invalid: {msg}")
            ledger_row = pl.append_entry(
                ledger_target(spec),
                reason=reason or f"Dashboard LIVE enable for {spec.id}",
            )

    from config import clear_env_cache
    from core.env_local import reload_env_file_into_process, upsert_env_file

    env_path = Path(skill_dir) / ".env"
    changed_keys = upsert_env_file(env_path, {spec.env_key: requested})
    reload_env_file_into_process(env_path, keys=[spec.env_key])
    clear_env_cache()

    verified = str(spec.getter(skill_dir) or "").strip().lower()
    if verified != requested:
        mark_restart_required(
            f"Wrote {spec.env_key}={requested} to .env but process still reads {verified!r}. "
            "Restart the dashboard, then refresh the scoreboard."
        )
        append_mode_audit(
            skill_dir,
            {
                "plugin_id": spec.id,
                "env_key": spec.env_key,
                "from": current,
                "to": requested,
                "verified": verified,
                "restart_required": True,
                "ledger_seq": (ledger_row or {}).get("seq"),
                "reason": reason,
                "changed_keys": changed_keys,
            },
        )
        return {
            "plugin_id": spec.id,
            "mode": verified,
            "requested_mode": requested,
            "changed": True,
            "restart_required": True,
            "restart_detail": restart_detail(),
            "ledger_appended": ledger_row,
            "message": "Persisted to .env; restart required to apply",
        }

    append_mode_audit(
        skill_dir,
        {
            "plugin_id": spec.id,
            "env_key": spec.env_key,
            "from": current,
            "to": requested,
            "verified": verified,
            "restart_required": False,
            "ledger_seq": (ledger_row or {}).get("seq"),
            "reason": reason,
            "changed_keys": changed_keys,
        },
    )
    return {
        "plugin_id": spec.id,
        "mode": verified,
        "changed": True,
        "restart_required": False,
        "ledger_appended": ledger_row,
        "message": f"{spec.label} set to {verified}",
    }
