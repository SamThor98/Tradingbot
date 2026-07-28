"""Compare live-scan PEAD-primary shadow admits to offline pead_primary rules."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.entry_timing_live_compare import load_last_scan_diagnostics

ARTIFACT_BASENAME = "pead_primary_shadow_compare_{stamp}.json"


def _safe_int(raw: Any, default: int = 0) -> int:
    try:
        return int(raw)
    except Exception:
        return default


def _safe_float(raw: Any) -> float | None:
    try:
        return float(raw)
    except Exception:
        return None


def check_shadow_non_execution_invariant(
    signals: list[Any] | None,
    diagnostics: dict[str, Any],
) -> dict[str, Any]:
    """Ensure no PEAD-only name entered the executable signal / order path."""
    pead_only_in_signals: list[str] = []
    for row in signals or []:
        if not isinstance(row, dict):
            continue
        family = str(row.get("entry_family") or "stage2").strip().lower()
        if family == "pead_primary":
            pead_only_in_signals.append(str(row.get("ticker") or "").upper())

    shadow_names = diagnostics.get("pead_primary_shadow_names") or []
    shadow_tickers = {
        str(n.get("ticker") or "").upper()
        for n in shadow_names
        if isinstance(n, dict) and n.get("ticker")
    }

    return {
        "ok": len(pead_only_in_signals) == 0,
        "pead_only_in_executable_signals": pead_only_in_signals,
        "shadow_name_count": len(shadow_tickers),
        "executable_still_stage2_only": len(pead_only_in_signals) == 0,
    }


def check_saved_shadow_rows_match_offline_rules(
    shadow_names: list[dict[str, Any]],
) -> dict[str, Any]:
    """Re-check beat/surprise fields on saved shadow rows (no market refetch)."""
    mismatches: list[dict[str, Any]] = []
    checked = 0
    for row in shadow_names:
        if not isinstance(row, dict):
            continue
        ticker = str(row.get("ticker") or "").upper()
        if not ticker:
            continue
        checked += 1
        beat = bool(row.get("pead_beat"))
        surprise = _safe_float(row.get("pead_surprise_pct"))
        if not beat or surprise is None or surprise <= 0.0:
            mismatches.append(
                {
                    "ticker": ticker,
                    "pead_beat": row.get("pead_beat"),
                    "pead_surprise_pct": row.get("pead_surprise_pct"),
                    "reason": "saved_row_fails_offline_beat_rule",
                }
            )
        if row.get("executable") is not False and row.get("executable") is not None:
            mismatches.append(
                {
                    "ticker": ticker,
                    "reason": "shadow_row_marked_executable",
                }
            )
    return {
        "ok": len(mismatches) == 0,
        "checked": checked,
        "mismatches": mismatches,
    }


def build_pead_canary_sleeve_block(
    diagnostics: dict[str, Any],
    *,
    shadow_names: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Paper/diagnostics canary sleeve = capacity top-N PEAD-only shadow names.

    Never implies executable PEAD. See docs/PEAD_CANARY_SLEEVE_DESIGN.md.
    """
    names = shadow_names
    if names is None:
        names = [
            n
            for n in (diagnostics.get("pead_primary_shadow_names") or [])
            if isinstance(n, dict)
        ]
    capacity_rows = [n for n in names if bool(n.get("capacity_top_n"))]
    # Fallback: if older scans lack the flag, take first N by list order.
    if not capacity_rows:
        top_n = _safe_int(diagnostics.get("pead_primary_capacity_rank_top_n"), 5)
        if top_n <= 0:
            top_n = 5
        capacity_rows = list(names[:top_n])
    dq = str(diagnostics.get("data_quality") or "").strip().lower()
    names_out = [
        {
            "ticker": str(n.get("ticker") or "").upper(),
            "edge_score": n.get("edge_score"),
            "stage_a_score": n.get("stage_a_score"),
            "pead_beat": n.get("pead_beat"),
            "pead_surprise_pct": n.get("pead_surprise_pct"),
            "adv_usd": n.get("adv_usd"),
            "executable": False,
            "sleeve_id": "S1",
        }
        for n in capacity_rows
    ]
    # R4 paper scores for S1 ranking (shadow only)
    try:
        from core.sleeve_research_chips import r4_pead_sleeve_score

        for row in names_out:
            row["r4_score"] = r4_pead_sleeve_score(row)
            if row.get("edge_score") is None:
                row["edge_score"] = row["r4_score"]
    except Exception:
        pass

    paper_weights: dict[str, float] = {}
    try:
        from core.portfolio_allocator import evaluate_allocator
        from core.sleeve_weights import build_sleeve_desired_weights

        decision = evaluate_allocator(mode="shadow")
        paper_weights = build_sleeve_desired_weights(
            names_out,
            sleeve_id="S1",
            top_n=3,
            sleeve_cap=decision.s1_cap,
            risk_mult=1.0,
            score_field="edge_score",
        )
    except Exception:
        paper_weights = {}

    return {
        "design_doc": "docs/PEAD_CANARY_SLEEVE_DESIGN.md",
        "sleeve_id": "S1",
        "executable": False,
        "allow_live": False,
        "rank_arm": diagnostics.get("pead_primary_capacity_rank_arm") or "top5_by_edge_score",
        "rank_top_n": _safe_int(diagnostics.get("pead_primary_capacity_rank_top_n"), 5),
        "sort_key": diagnostics.get("pead_primary_shadow_sort_key") or "edge_score_desc,ticker_asc",
        "n_names": len(capacity_rows),
        "names": names_out,
        "tickers": [str(n.get("ticker") or "").upper() for n in capacity_rows if n.get("ticker")],
        "desired_weights_paper": paper_weights,
        "overlap_with_stage2": _safe_int(diagnostics.get("overlap_with_stage2")),
        "shadow_truncated": _safe_int(diagnostics.get("pead_primary_shadow_truncated")),
        "data_quality": diagnostics.get("data_quality"),
        "rth_dq_ok": dq == "ok",
        "enablement_ready": False,
    }


def revalidate_shadow_tickers_offline(
    tickers: list[str],
    *,
    skill_dir: Path,
    as_of: Any | None = None,
    auth: Any | None = None,
) -> dict[str, Any]:
    """Fetch history + re-run evaluate_pead_primary_entry (optional deep parity)."""
    from market_data import get_daily_history_with_meta
    from stage_analysis import add_indicators, evaluate_pead_primary_entry

    results: list[dict[str, Any]] = []
    fails: list[dict[str, Any]] = []
    for ticker in tickers:
        tkr = str(ticker or "").upper().strip()
        if not tkr:
            continue
        try:
            df, meta = get_daily_history_with_meta(tkr, days=300, auth=auth, skill_dir=skill_dir)
            if df is None or getattr(df, "empty", True):
                fails.append({"ticker": tkr, "reason": "df_empty", "meta": meta})
                continue
            df = add_indicators(df)
            eval_out = evaluate_pead_primary_entry(
                tkr,
                df,
                skill_dir=skill_dir,
                as_of=as_of,
            )
            row = {"ticker": tkr, "admitted": bool(eval_out.get("admitted")), "eval": eval_out}
            results.append(row)
            if not eval_out.get("admitted"):
                fails.append(
                    {
                        "ticker": tkr,
                        "reason": eval_out.get("fail_reason") or "not_admitted",
                        "eval": eval_out,
                    }
                )
        except Exception as exc:
            fails.append({"ticker": tkr, "reason": f"exception:{type(exc).__name__}", "detail": str(exc)})
    return {
        "ok": len(fails) == 0 and len(results) > 0,
        "evaluated": len(results),
        "fails": fails,
        "results": results,
    }


def build_pead_primary_shadow_compare_report(
    diagnostics: dict[str, Any],
    *,
    signals: list[Any] | None = None,
    live_meta: dict[str, Any] | None = None,
    revalidate: bool = False,
    skill_dir: Path | None = None,
    auth: Any | None = None,
) -> dict[str, Any]:
    """Build pass/fail report for PEAD shadow dual-admit parity."""
    mode = str(
        diagnostics.get("strategy_pead_primary_effective_mode")
        or diagnostics.get("strategy_pead_primary_mode")
        or "off"
    ).lower()
    shadow_names = [
        n for n in (diagnostics.get("pead_primary_shadow_names") or []) if isinstance(n, dict)
    ]
    invariant = check_shadow_non_execution_invariant(signals, diagnostics)
    saved_rules = check_saved_shadow_rows_match_offline_rules(shadow_names)

    deep: dict[str, Any] | None = None
    if revalidate and skill_dir is not None and shadow_names:
        as_of = None
        if live_meta and live_meta.get("scan_at"):
            as_of = live_meta.get("scan_at")
        deep = revalidate_shadow_tickers_offline(
            [str(n.get("ticker") or "") for n in shadow_names],
            skill_dir=skill_dir,
            as_of=as_of,
            auth=auth,
        )

    errors: list[str] = []
    warnings: list[str] = []
    if mode == "off":
        warnings.append("strategy_pead_primary mode is off - nothing to compare")
    if not invariant["ok"]:
        errors.append(
            "PEAD-only names present on executable signals: "
            + ",".join(invariant["pead_only_in_executable_signals"][:20])
        )
    if mode not in {"off", "shadow", "live"}:
        errors.append(f"unexpected pead mode: {mode}")
    if mode == "live" and _safe_int(diagnostics.get("pead_primary_live_coerced_to_shadow")) != 1:
        # live without allow still reports live in configured mode; executable must stay stage2
        pass
    if not saved_rules["ok"] and shadow_names:
        errors.append(f"saved shadow rows fail offline beat rule ({len(saved_rules['mismatches'])})")
    if deep is not None and not deep.get("ok"):
        errors.append(f"deep revalidate failed for {len(deep.get('fails') or [])} tickers")

    # Soft structural expectations when mode is shadow.
    if mode in {"shadow", "live"}:
        evaluated = _safe_int(diagnostics.get("pead_primary_evaluated"))
        if evaluated <= 0:
            warnings.append(
                "pead_primary_evaluated is 0 - earnings cache may be cold / mode not active in Stage A"
            )

    verdict = "fail" if errors else ("warn" if warnings else "pass")
    if mode == "off" and not errors:
        verdict = "skip"

    canary = build_pead_canary_sleeve_block(diagnostics, shadow_names=shadow_names)

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "verdict": verdict,
        "errors": errors,
        "warnings": warnings,
        "mode": {
            "configured": diagnostics.get("strategy_pead_primary_mode"),
            "effective": mode,
            "live_coerced_to_shadow": _safe_int(diagnostics.get("pead_primary_live_coerced_to_shadow")),
            "lookback_days": diagnostics.get("pead_primary_lookback_days"),
        },
        "counters": {
            "pead_primary_evaluated": _safe_int(diagnostics.get("pead_primary_evaluated")),
            "pead_primary_admitted": _safe_int(diagnostics.get("pead_primary_admitted")),
            "overlap_with_stage2": _safe_int(diagnostics.get("overlap_with_stage2")),
            "pead_primary_would_rank_top_n": _safe_int(diagnostics.get("pead_primary_would_rank_top_n")),
            "pead_primary_would_rank_capacity_top_n": _safe_int(
                diagnostics.get("pead_primary_would_rank_capacity_top_n")
            ),
            "pead_primary_capacity_rank_arm": diagnostics.get("pead_primary_capacity_rank_arm"),
            "pead_primary_capacity_rank_top_n": _safe_int(
                diagnostics.get("pead_primary_capacity_rank_top_n")
            ),
            "pead_primary_shadow_truncated": _safe_int(diagnostics.get("pead_primary_shadow_truncated")),
            "pead_primary_shadow_sort_key": diagnostics.get("pead_primary_shadow_sort_key"),
            "shadow_names_listed": len(shadow_names),
        },
        "canary_sleeve": canary,
        "invariant": invariant,
        "saved_row_rules": saved_rules,
        "deep_revalidate": deep,
        "live_meta": live_meta or {},
        "data_quality": diagnostics.get("data_quality"),
    }


def write_pead_primary_shadow_compare_report(
    report: dict[str, Any],
    skill_dir: Path,
) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = skill_dir / "validation_artifacts" / ARTIFACT_BASENAME.format(stamp=stamp)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    return out


def load_scan_bundle(
    *,
    sqlite_path: Path | None = None,
    diagnostics_json: Path | None = None,
) -> tuple[dict[str, Any] | None, list[Any] | None, dict[str, Any]]:
    """Load diagnostics + signals from last_scan or a JSON blob."""
    diagnostics, meta = load_last_scan_diagnostics(
        sqlite_path=sqlite_path,
        diagnostics_json=diagnostics_json,
    )
    signals: list[Any] | None = None
    if diagnostics_json and diagnostics_json.exists():
        try:
            payload = json.loads(diagnostics_json.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                if isinstance(payload.get("signals"), list):
                    signals = payload["signals"]
                elif isinstance(payload.get("last_scan"), dict):
                    signals = payload["last_scan"].get("signals")
        except Exception:
            signals = None
    if signals is None and sqlite_path and sqlite_path.exists():
        try:
            from sqlalchemy import create_engine
            from sqlalchemy.orm import sessionmaker

            from webapp.models import AppState

            engine = create_engine(f"sqlite:///{sqlite_path}")
            Session = sessionmaker(bind=engine)
            db = Session()
            try:
                row = (
                    db.query(AppState)
                    .filter(AppState.user_id == "local", AppState.key == "last_scan")
                    .first()
                )
                if row and isinstance(row.value_json, dict):
                    signals = row.value_json.get("signals")
                    if meta.get("scan_at") is None:
                        meta["scan_at"] = row.value_json.get("scan_at") or row.value_json.get("updated_at")
            finally:
                db.close()
        except Exception as exc:
            meta["signals_load_error"] = str(exc)
    return diagnostics, signals if isinstance(signals, list) else None, meta
