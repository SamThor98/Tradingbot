"""Compare live scan entry-timing shadow counters to offline replay evidence."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

DEFAULT_RUN_ID = "control_legacy_aug"
DEFAULT_WOULD_FILTER_BAND = (35.0, 65.0)
# Session-level live rates are compared to offline replay *daily* distribution,
# not the decade pooled mean (~58%). See compute_empirical_would_filter_band().
EMPIRICAL_BAND_QUANTILES = (0.10, 0.95)
MIN_DAILY_REPLAY_SAMPLES = 10
REPLAY_CACHE_BASENAME = "entry_timing_replay_cache_{run_id}.json"
EXPECTED_EXPERIMENT_PROFILE = "breakout_buffer_only_0.010"
MIN_STAGE_A_DEFAULT = 10
STAGE2B_MIN_PASS_SCANS = 2
STAGE2B_MIN_STAGE_A = 10
EVIDENCE_LOG_BASENAME = "entry_timing_live_evidence_log"


def _safe_int(raw: Any, default: int = 0) -> int:
    try:
        return int(raw)
    except Exception:
        return default


def _safe_float(raw: Any) -> float | None:
    try:
        val = float(raw)
    except Exception:
        return None
    return val if not math.isnan(val) else None


def load_validation_artifact(skill_dir: Path, name: str) -> dict[str, Any] | None:
    path = skill_dir / "validation_artifacts" / name
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def _percentile(sorted_vals: list[float], quantile: float) -> float:
    if not sorted_vals:
        return 0.0
    q = min(1.0, max(0.0, quantile))
    idx = (len(sorted_vals) - 1) * q
    lo = int(math.floor(idx))
    hi = int(math.ceil(idx))
    if lo == hi:
        return sorted_vals[lo]
    frac = idx - lo
    return sorted_vals[lo] * (1.0 - frac) + sorted_vals[hi] * frac


def compute_empirical_would_filter_band(
    skill_dir: Path,
    run_id: str = DEFAULT_RUN_ID,
    *,
    low_quantile: float = EMPIRICAL_BAND_QUANTILES[0],
    high_quantile: float = EMPIRICAL_BAND_QUANTILES[1],
    min_daily_samples: int = MIN_DAILY_REPLAY_SAMPLES,
    min_breakout_buffer_pct: float = 0.01,
) -> dict[str, Any]:
    """Derive a session-honest would-filter band from offline replay daily rates."""
    path = skill_dir / "validation_artifacts" / REPLAY_CACHE_BASENAME.format(run_id=run_id)
    if not path.exists():
        return {
            "band_low": DEFAULT_WOULD_FILTER_BAND[0],
            "band_high": DEFAULT_WOULD_FILTER_BAND[1],
            "band_source": "default_fixed",
            "replay_cache_path": str(path),
        }

    try:
        rows = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {
            "band_low": DEFAULT_WOULD_FILTER_BAND[0],
            "band_high": DEFAULT_WOULD_FILTER_BAND[1],
            "band_source": "default_fixed",
            "replay_cache_path": str(path),
            "error": str(exc),
        }

    if not isinstance(rows, list) or not rows:
        return {
            "band_low": DEFAULT_WOULD_FILTER_BAND[0],
            "band_high": DEFAULT_WOULD_FILTER_BAND[1],
            "band_source": "default_fixed",
            "replay_cache_path": str(path),
            "error": "empty replay cache",
        }

    daily: dict[str, list[bool]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        entry_date = row.get("entry_date")
        if not entry_date:
            continue
        entry_iso = str(entry_date)[:10]
        buffer_pct = _safe_float(row.get("breakout_buffer_pct"))
        if buffer_pct is None:
            continue
        daily.setdefault(entry_iso, []).append(buffer_pct < min_breakout_buffer_pct)

    daily_rates: list[float] = []
    for flags in daily.values():
        if len(flags) < min_daily_samples:
            continue
        daily_rates.append(sum(1 for flag in flags if flag) / len(flags) * 100.0)

    if len(daily_rates) < 5:
        return {
            "band_low": DEFAULT_WOULD_FILTER_BAND[0],
            "band_high": DEFAULT_WOULD_FILTER_BAND[1],
            "band_source": "default_fixed",
            "replay_cache_path": str(path),
            "daily_rate_days": len(daily_rates),
            "error": "insufficient daily replay samples",
        }

    daily_rates.sort()
    band_low = _percentile(daily_rates, low_quantile)
    band_high = _percentile(daily_rates, high_quantile)
    pooled_wf = sum(sum(flags) for flags in daily.values()) / sum(len(flags) for flags in daily.values()) * 100.0

    return {
        "band_low": round(band_low, 1),
        "band_high": round(band_high, 1),
        "band_source": "empirical_replay_daily",
        "band_quantiles": [low_quantile, high_quantile],
        "min_daily_samples": min_daily_samples,
        "replay_cache_path": str(path),
        "replay_rows": len(rows),
        "daily_rate_days": len(daily_rates),
        "pooled_would_filter_pct": round(pooled_wf, 1),
        "daily_rate_p50": round(_percentile(daily_rates, 0.50), 1),
        "daily_rate_p90": round(_percentile(daily_rates, 0.90), 1),
    }


def offline_entry_timing_targets(
    entry_artifact: dict[str, Any],
    *,
    band: tuple[float, float] | None = None,
    skill_dir: Path | None = None,
    run_id: str = DEFAULT_RUN_ID,
) -> dict[str, Any]:
    """Extract offline experiment targets from entry-timing counterfactual JSON."""
    rec = entry_artifact.get("recommendation") if isinstance(entry_artifact.get("recommendation"), dict) else {}
    replay = entry_artifact.get("live_shadow_replay") if isinstance(entry_artifact.get("live_shadow_replay"), dict) else {}

    experiment_row: dict[str, Any] | None = None
    if isinstance(rec.get("breakout_buffer_only"), dict):
        experiment_row = rec["breakout_buffer_only"]
    if experiment_row is None:
        for row in entry_artifact.get("breakout_buffer_only_sweep") or []:
            if isinstance(row, dict) and row.get("min_breakout_buffer_pct") == 0.01:
                experiment_row = row
                break

    retention_pct = _safe_float((experiment_row or {}).get("retention_pct"))
    would_filter_pct = (100.0 - retention_pct) if retention_pct is not None else None

    replayed = _safe_int(replay.get("replayed_trades"))
    replay_would_filter = _safe_int(replay.get("would_filter_any"))
    if would_filter_pct is None and replayed > 0:
        would_filter_pct = replay_would_filter / replayed * 100.0

    band_meta: dict[str, Any]
    if band is not None:
        band_meta = {
            "band_low": band[0],
            "band_high": band[1],
            "band_source": "explicit",
        }
    elif skill_dir is not None:
        band_meta = compute_empirical_would_filter_band(skill_dir, run_id)
    else:
        band_meta = {
            "band_low": DEFAULT_WOULD_FILTER_BAND[0],
            "band_high": DEFAULT_WOULD_FILTER_BAND[1],
            "band_source": "default_fixed",
        }

    return {
        "recommended_action": rec.get("action"),
        "retention_pct": retention_pct,
        "would_filter_pct_offline": would_filter_pct,
        "would_filter_band_low": band_meta["band_low"],
        "would_filter_band_high": band_meta["band_high"],
        "band_source": band_meta.get("band_source"),
        "band_quantiles": band_meta.get("band_quantiles"),
        "pooled_would_filter_pct": band_meta.get("pooled_would_filter_pct"),
        "daily_rate_days": band_meta.get("daily_rate_days"),
        "daily_rate_p50": band_meta.get("daily_rate_p50"),
        "daily_rate_p90": band_meta.get("daily_rate_p90"),
        "expected_profile": EXPECTED_EXPERIMENT_PROFILE,
        "delta_early_stopout_pp": _safe_float((experiment_row or {}).get("delta_early_stopout_pp")),
        "delta_overlap_pf_mean": _safe_float((experiment_row or {}).get("delta_overlap_pf_mean")),
        "offline_replayed_trades": replayed,
        "offline_would_filter_any": replay_would_filter,
    }


def extract_live_entry_shadow_metrics(diagnostics: dict[str, Any]) -> dict[str, Any]:
    """Pull live scan entry-timing shadow counters from scanner diagnostics."""
    stage_a = _safe_int(diagnostics.get("stage_a_candidates"))
    would_filter_stage_a = _safe_int(diagnostics.get("entry_shadow_would_filter_any"))
    stage_a_pct = (would_filter_stage_a / stage_a * 100.0) if stage_a > 0 else None

    stage2_eval = _safe_int(diagnostics.get("entry_shadow_stage2_evaluated"))
    would_filter_stage2 = _safe_int(diagnostics.get("entry_shadow_stage2_would_filter_any"))
    stage2_pct = (would_filter_stage2 / stage2_eval * 100.0) if stage2_eval > 0 else None

    mode = str(diagnostics.get("entry_timing_shadow_mode") or "").strip().lower()
    blocked = _safe_int(diagnostics.get("entry_timing_blocked"))
    live_enforced = mode == "live" or _safe_int(diagnostics.get("entry_timing_live_enforced")) == 1

    rate_source = "stage_a_candidates"
    would_filter_pct = stage_a_pct
    would_filter = would_filter_stage_a
    denominator = stage_a

    if live_enforced and blocked > 0:
        denominator = blocked + stage_a
        if denominator > 0:
            rate_source = "live_enforcement"
            would_filter = blocked
            would_filter_pct = blocked / denominator * 100.0
    elif stage_a < 10 and stage2_eval >= 10 and stage2_pct is not None:
        rate_source = "stage2_universe"
        would_filter_pct = stage2_pct
        would_filter = would_filter_stage2
        denominator = stage2_eval

    return {
        "stage_a_candidates": stage_a,
        "entry_shadow_would_filter_any": would_filter_stage_a,
        "entry_shadow_would_filter_breakout_buffer": _safe_int(
            diagnostics.get("entry_shadow_would_filter_breakout_buffer")
        ),
        "entry_shadow_stage2_evaluated": stage2_eval,
        "entry_shadow_stage2_would_filter_any": would_filter_stage2,
        "entry_shadow_stage2_would_filter_breakout_buffer": _safe_int(
            diagnostics.get("entry_shadow_stage2_would_filter_breakout_buffer")
        ),
        "entry_timing_blocked": blocked,
        "would_filter_pct": would_filter_pct,
        "would_filter_pct_stage_a": stage_a_pct,
        "would_filter_pct_stage2": stage2_pct,
        "rate_source": rate_source,
        "compare_denominator": denominator,
        "compare_would_filter": would_filter,
        "entry_timing_shadow_mode": mode,
        "entry_timing_shadow_profile": str(diagnostics.get("entry_timing_shadow_profile") or "").strip(),
        "entry_timing_live_enforced": live_enforced,
        "scan_at": diagnostics.get("scan_at"),
    }


def compare_live_to_offline(
    live: dict[str, Any],
    offline: dict[str, Any],
    *,
    expect_experiment: bool = True,
    min_stage_a: int = MIN_STAGE_A_DEFAULT,
    experiment_env_ready: bool | None = None,
) -> dict[str, Any]:
    """Return structured comparison with verdict pass|warn|fail|skip|stale_scan."""
    errors: list[str] = []
    warnings: list[str] = []

    stage_a = _safe_int(live.get("stage_a_candidates"))
    live_pct = _safe_float(live.get("would_filter_pct"))
    offline_pct = _safe_float(offline.get("would_filter_pct_offline"))
    band_low = _safe_float(offline.get("would_filter_band_low")) or DEFAULT_WOULD_FILTER_BAND[0]
    band_high = _safe_float(offline.get("would_filter_band_high")) or DEFAULT_WOULD_FILTER_BAND[1]
    mode = str(live.get("entry_timing_shadow_mode") or "")
    profile = str(live.get("entry_timing_shadow_profile") or "")
    expected = str(offline.get("expected_profile") or EXPECTED_EXPERIMENT_PROFILE)
    env_ready = experiment_env_ready

    if expect_experiment and mode not in {"shadow", "live"}:
        if env_ready:
            return {
                "verdict": "stale_scan",
                "errors": [],
                "warnings": [
                    "last scan predates the entry-timing experiment env — restart the dashboard if needed, "
                    "then Run Scan to refresh diagnostics"
                ],
                "live": live,
                "offline": offline,
                "delta_would_filter_pp": None,
            }
        return {
            "verdict": "skip",
            "errors": [],
            "warnings": [
                "live scan is not running entry-timing shadow (set ENTRY_TIMING_SHADOW_MODE=shadow "
                "and experiment env before comparing rates)"
            ],
            "live": live,
            "offline": offline,
            "delta_would_filter_pp": None,
        }

    if stage_a < min_stage_a:
        warnings.append(f"stage_a_candidates={stage_a} below min={min_stage_a}; rate comparison low confidence")

    rate_source = str(live.get("rate_source") or "stage_a_candidates")
    if rate_source == "stage2_universe":
        stage2_eval = _safe_int(live.get("entry_shadow_stage2_evaluated"))
        warnings.append(
            f"using stage2_universe rate ({live.get('compare_would_filter')}/{stage2_eval}) "
            "because stage_a sample is thin"
        )

    if expect_experiment and profile != expected:
        if profile in {"", "off", "default_sma50_and_buffer"}:
            if env_ready:
                return {
                    "verdict": "stale_scan",
                    "errors": [],
                    "warnings": [
                        f"last scan profile={profile or 'missing'}; expected {expected}. "
                        "Run a fresh scan with the experiment env loaded in the dashboard process."
                    ],
                    "live": live,
                    "offline": offline,
                    "delta_would_filter_pp": None,
                }
            return {
                "verdict": "skip",
                "errors": [],
                "warnings": [
                    f"live scan profile={profile or 'missing'}; expected {expected} "
                    "(set ENTRY_SHADOW_DISABLE_SMA50_FILTERS=true and ENTRY_SHADOW_MIN_BREAKOUT_BUFFER_PCT=0.01)"
                ],
                "live": live,
                "offline": offline,
                "delta_would_filter_pp": None,
            }
        errors.append(f"entry_timing_shadow_profile={profile} (expected {expected})")

    if live_pct is None:
        errors.append("live would_filter_pct unavailable (stage_a_candidates=0)")
    elif not (band_low <= live_pct <= band_high):
        pooled = _safe_float(offline.get("pooled_would_filter_pct"))
        offline_hint = f"pooled {pooled:.1f}%" if pooled is not None else (
            f"{offline_pct:.1f}%" if offline_pct is not None else "offline target"
        )
        # Single-session live_enforcement rates are noisier than decade pooled;
        # allow 1pp slack at the empirical band edge (still counts as pass).
        edge_slack = 1.0 if rate_source == "live_enforcement" else 0.0
        if not ((band_low - edge_slack) <= live_pct <= (band_high + edge_slack)):
            errors.append(
                f"live would_filter_pct={live_pct:.1f}% outside band [{band_low:.0f},{band_high:.0f}] "
                f"({offline_hint})"
            )

    delta_pp: float | None = None
    if live_pct is not None and offline_pct is not None:
        delta_pp = live_pct - offline_pct
        # Pooled offline mean is not a session comparator when live enforcement is active.
        if abs(delta_pp) > 20.0 and not errors and rate_source != "live_enforcement":
            warnings.append(f"live vs offline would-filter delta {delta_pp:+.1f}pp exceeds 20pp soft limit")

    if errors:
        verdict = "fail"
    elif warnings:
        verdict = "warn"
    else:
        verdict = "pass"

    return {
        "verdict": verdict,
        "errors": errors,
        "warnings": warnings,
        "live": live,
        "offline": offline,
        "delta_would_filter_pp": delta_pp,
    }


def load_last_scan_diagnostics(
    *,
    sqlite_path: Path | None = None,
    diagnostics_json: Path | None = None,
    last_scan_blob: dict[str, Any] | None = None,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """Load diagnostics dict from sqlite, JSON file, or an in-memory last_scan blob."""
    meta: dict[str, Any] = {"source": None}

    if isinstance(last_scan_blob, dict):
        diag = last_scan_blob.get("diagnostics")
        if isinstance(diag, dict):
            meta.update(
                {
                    "source": "blob",
                    "scan_at": last_scan_blob.get("at"),
                    "signals_found": last_scan_blob.get("signals_found"),
                }
            )
            return diag, meta

    if diagnostics_json is not None and diagnostics_json.exists():
        try:
            payload = json.loads(diagnostics_json.read_text(encoding="utf-8"))
        except Exception as exc:
            return None, {"source": "diagnostics_json", "error": str(exc)}
        if isinstance(payload, dict) and isinstance(payload.get("diagnostics"), dict):
            meta.update({"source": "diagnostics_json", "path": str(diagnostics_json), "scan_at": payload.get("at")})
            return payload["diagnostics"], meta
        if isinstance(payload, dict):
            meta.update({"source": "diagnostics_json", "path": str(diagnostics_json)})
            return payload, meta
        return None, {"source": "diagnostics_json", "error": "invalid JSON object"}

    if sqlite_path is not None and sqlite_path.exists():
        try:
            from sqlalchemy import create_engine
            from sqlalchemy.orm import sessionmaker

            from webapp.models import AppState

            engine = create_engine(
                f"sqlite:///{sqlite_path.resolve().as_posix()}",
                connect_args={"check_same_thread": False},
            )
            Session = sessionmaker(bind=engine)
            db = Session()
            try:
                row = (
                    db.query(AppState)
                    .filter(AppState.user_id == "local", AppState.key == "last_scan")
                    .first()
                )
                if not row:
                    return None, {"source": "sqlite", "path": str(sqlite_path), "error": "no last_scan row"}
                raw = row.value_json
                if not isinstance(raw, dict):
                    return None, {"source": "sqlite", "path": str(sqlite_path), "error": "last_scan not a dict"}
                diag = raw.get("diagnostics")
                if not isinstance(diag, dict):
                    return None, {"source": "sqlite", "path": str(sqlite_path), "error": "diagnostics missing"}
                meta.update(
                    {
                        "source": "sqlite",
                        "path": str(sqlite_path),
                        "scan_at": raw.get("at"),
                        "signals_found": raw.get("signals_found"),
                    }
                )
                return diag, meta
            finally:
                db.close()
        except Exception as exc:
            return None, {"source": "sqlite", "path": str(sqlite_path), "error": str(exc)}

    if sqlite_path is not None:
        return None, {"source": "sqlite", "path": str(sqlite_path), "error": "sqlite file missing"}
    return None, {"source": None, "error": "no live diagnostics source provided"}


def build_live_entry_shadow_compare_report(
    diagnostics: dict[str, Any],
    *,
    skill_dir: Path,
    run_id: str = DEFAULT_RUN_ID,
    live_meta: dict[str, Any] | None = None,
    expect_experiment: bool = True,
    min_stage_a: int = MIN_STAGE_A_DEFAULT,
    experiment_env_ready: bool | None = None,
) -> dict[str, Any] | None:
    """Build live-vs-offline compare report from scanner diagnostics."""
    from datetime import datetime, timezone

    entry_artifact = load_validation_artifact(skill_dir, f"entry_timing_shadow_counterfactual_{run_id}.json")
    if entry_artifact is None:
        return None

    offline = offline_entry_timing_targets(entry_artifact, skill_dir=skill_dir, run_id=run_id)
    live = extract_live_entry_shadow_metrics(diagnostics)
    meta = dict(live_meta or {})
    if meta.get("scan_at") and not live.get("scan_at"):
        live["scan_at"] = meta.get("scan_at")

    comparison = compare_live_to_offline(
        live,
        offline,
        expect_experiment=expect_experiment,
        min_stage_a=min_stage_a,
        experiment_env_ready=experiment_env_ready,
    )
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "run_id": run_id,
        "skipped": comparison.get("verdict") == "skip" and meta.get("source") is None,
        "live_meta": meta,
        "offline": offline,
        "live": live,
        "comparison": comparison,
    }


def entry_timing_evidence_log_path(skill_dir: Path, run_id: str = DEFAULT_RUN_ID) -> Path:
    return skill_dir / "validation_artifacts" / f"{EVIDENCE_LOG_BASENAME}_{run_id}.json"


def evidence_record_from_compare_report(report: dict[str, Any]) -> dict[str, Any]:
    """Compact evidence row from a live/offline compare report."""
    live = report.get("live") if isinstance(report.get("live"), dict) else {}
    comparison = report.get("comparison") if isinstance(report.get("comparison"), dict) else {}
    meta = report.get("live_meta") if isinstance(report.get("live_meta"), dict) else {}
    return {
        "scan_at": live.get("scan_at") or meta.get("scan_at"),
        "recorded_at": report.get("generated_at"),
        "source": meta.get("source"),
        "watchlist_size": _safe_int(meta.get("watchlist_size")),
        "signals_found": _safe_int(meta.get("signals_found")),
        "stage_a_candidates": _safe_int(live.get("stage_a_candidates")),
        "entry_shadow_would_filter_any": _safe_int(live.get("entry_shadow_would_filter_any")),
        "would_filter_pct": _safe_float(live.get("would_filter_pct")),
        "rate_source": live.get("rate_source"),
        "entry_shadow_stage2_evaluated": _safe_int(live.get("entry_shadow_stage2_evaluated")),
        "entry_shadow_stage2_would_filter_any": _safe_int(live.get("entry_shadow_stage2_would_filter_any")),
        "would_filter_pct_stage2": _safe_float(live.get("would_filter_pct_stage2")),
        "entry_timing_shadow_profile": live.get("entry_timing_shadow_profile"),
        "entry_timing_shadow_mode": live.get("entry_timing_shadow_mode"),
        "verdict": comparison.get("verdict"),
        "delta_would_filter_pp": _safe_float(comparison.get("delta_would_filter_pp")),
    }


def assess_stage2b_readiness(
    records: list[dict[str, Any]],
    *,
    min_pass_scans: int = STAGE2B_MIN_PASS_SCANS,
    min_stage_a: int = STAGE2B_MIN_STAGE_A,
    expected_profile: str = EXPECTED_EXPERIMENT_PROFILE,
) -> dict[str, Any]:
    """Stage 2b go/no-go: >= min_pass_scans aligned pass scans with sufficient Stage A sample."""
    qualifying = [
        row
        for row in records
        if row.get("verdict") == "pass"
        and _safe_int(row.get("stage_a_candidates")) >= min_stage_a
        and str(row.get("entry_timing_shadow_profile") or "") == expected_profile
    ]
    full_universe = [row for row in qualifying if _safe_int(row.get("watchlist_size")) >= 500]
    ready = len(qualifying) >= min_pass_scans
    messages: list[str] = []
    if ready:
        messages.append(
            f"Stage 2b shadow alignment met ({len(qualifying)}/{min_pass_scans} pass scans, "
            f"stage_a>={min_stage_a})."
        )
    else:
        need = min_pass_scans - len(qualifying)
        messages.append(
            f"Stage 2b needs {need} more pass scan(s) with stage_a>={min_stage_a} "
            f"and profile={expected_profile}."
        )
    if ready and len(full_universe) == 0:
        messages.append("No pass scan yet on watchlist>=500; run full SP1500 for highest confidence.")
    return {
        "ready": ready,
        "pass_scans": len(qualifying),
        "required_pass_scans": min_pass_scans,
        "min_stage_a": min_stage_a,
        "full_universe_pass_scans": len(full_universe),
        "expected_profile": expected_profile,
        "messages": messages,
        "qualifying_scans": [
            {
                "scan_at": row.get("scan_at"),
                "watchlist_size": row.get("watchlist_size"),
                "stage_a_candidates": row.get("stage_a_candidates"),
                "would_filter_pct": row.get("would_filter_pct"),
                "verdict": row.get("verdict"),
            }
            for row in qualifying
        ],
    }


def load_entry_timing_evidence_log(skill_dir: Path, run_id: str = DEFAULT_RUN_ID) -> dict[str, Any]:
    path = entry_timing_evidence_log_path(skill_dir, run_id)
    if not path.exists():
        return {"run_id": run_id, "records": []}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"run_id": run_id, "records": []}
    if not isinstance(payload, dict):
        return {"run_id": run_id, "records": []}
    records = payload.get("records")
    if not isinstance(records, list):
        payload["records"] = []
    return payload


def append_entry_timing_evidence_record(
    report: dict[str, Any],
    *,
    skill_dir: Path,
    run_id: str = DEFAULT_RUN_ID,
) -> dict[str, Any]:
    """Append a compare report to the rolling Stage 2b evidence log (dedupe by scan_at)."""
    from datetime import datetime, timezone

    if report.get("skipped"):
        return load_entry_timing_evidence_log(skill_dir, run_id)

    record = evidence_record_from_compare_report(report)
    scan_at = record.get("scan_at")
    if not scan_at or not record.get("verdict"):
        return load_entry_timing_evidence_log(skill_dir, run_id)

    log = load_entry_timing_evidence_log(skill_dir, run_id)
    records: list[dict[str, Any]] = []
    prior_by_scan: dict[str, dict[str, Any]] = {
        str(row.get("scan_at")): row for row in log.get("records") or [] if isinstance(row, dict) and row.get("scan_at")
    }
    for row in log.get("records") or []:
        if isinstance(row, dict) and row.get("scan_at") != scan_at:
            records.append(row)

    prior = prior_by_scan.get(str(scan_at))
    if prior and not _safe_int(record.get("watchlist_size")) and _safe_int(prior.get("watchlist_size")):
        record["watchlist_size"] = prior.get("watchlist_size")
    if prior and not _safe_int(record.get("signals_found")) and _safe_int(prior.get("signals_found")):
        record["signals_found"] = prior.get("signals_found")

    records.append(record)
    records.sort(key=lambda row: str(row.get("scan_at") or ""))

    log["run_id"] = run_id
    log["updated_at"] = datetime.now(timezone.utc).isoformat()
    log["records"] = records
    log["stage2b"] = assess_stage2b_readiness(records)

    path = entry_timing_evidence_log_path(skill_dir, run_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(log, indent=2), encoding="utf-8")
    return log


def write_live_entry_shadow_compare_report(
    diagnostics: dict[str, Any],
    *,
    skill_dir: Path,
    run_id: str = DEFAULT_RUN_ID,
    live_meta: dict[str, Any] | None = None,
    expect_experiment: bool = True,
    min_stage_a: int = MIN_STAGE_A_DEFAULT,
    experiment_env_ready: bool | None = None,
    append_evidence: bool = True,
) -> dict[str, Any] | None:
    """Write validation_artifacts/live_entry_shadow_compare_<run_id>.json."""
    report = build_live_entry_shadow_compare_report(
        diagnostics,
        skill_dir=skill_dir,
        run_id=run_id,
        live_meta=live_meta,
        expect_experiment=expect_experiment,
        min_stage_a=min_stage_a,
        experiment_env_ready=experiment_env_ready,
    )
    if report is None:
        return None
    out = skill_dir / "validation_artifacts" / f"live_entry_shadow_compare_{run_id}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    if append_evidence:
        append_entry_timing_evidence_record(report, skill_dir=skill_dir, run_id=run_id)
    return report


def assess_entry_timing_live_promotion_readiness(
    skill_dir: Path,
    *,
    run_id: str = DEFAULT_RUN_ID,
    min_pass_scans: int = STAGE2B_MIN_PASS_SCANS,
) -> dict[str, Any]:
    """Gate checklist before setting ENTRY_TIMING_SHADOW_MODE=live."""
    from config import (
        get_entry_shadow_disable_sma50_filters,
        get_entry_shadow_min_breakout_buffer_pct,
        get_entry_timing_shadow_mode,
        get_entry_timing_shadow_profile,
    )

    errors: list[str] = []
    notes: list[str] = []

    profile = get_entry_timing_shadow_profile(skill_dir)
    if profile != EXPECTED_EXPERIMENT_PROFILE:
        errors.append(f"profile={profile} expected {EXPECTED_EXPERIMENT_PROFILE}")
    if not get_entry_shadow_disable_sma50_filters(skill_dir):
        errors.append("ENTRY_SHADOW_DISABLE_SMA50_FILTERS=true required")
    if abs(get_entry_shadow_min_breakout_buffer_pct(skill_dir) - 0.01) > 1e-9:
        errors.append("ENTRY_SHADOW_MIN_BREAKOUT_BUFFER_PCT=0.01 required")

    evidence_log = load_entry_timing_evidence_log(skill_dir, run_id)
    stage2b = evidence_log.get("stage2b")
    if not isinstance(stage2b, dict):
        stage2b = assess_stage2b_readiness(evidence_log.get("records") or [], min_pass_scans=min_pass_scans)
    if not stage2b.get("ready"):
        errors.append(
            f"Stage 2b not ready ({stage2b.get('pass_scans')}/{stage2b.get('required_pass_scans')} pass scans)"
        )

    stack_art = load_validation_artifact(skill_dir, f"signal_stack_counterfactual_{run_id}.json")
    stack_row: dict[str, Any] = {}
    if not stack_art:
        errors.append(f"missing signal_stack_counterfactual_{run_id}.json")
    else:
        scenarios = stack_art.get("scenarios") if isinstance(stack_art.get("scenarios"), dict) else {}
        stack_row = scenarios.get("exit_grace_breakout_buffer_0.010") or {}
        if not stack_row.get("passes_promotion_gates"):
            errors.append("offline stack does not pass PF promotion gates")
        rec = stack_art.get("recommendation") if isinstance(stack_art.get("recommendation"), dict) else {}
        if rec.get("action") not in {"promote_stack_shadow_first", "promote_exit_grace_only"}:
            notes.append(f"stack recommendation={rec.get('action')}")

    diagnostics, meta = load_last_scan_diagnostics(sqlite_path=skill_dir / "webapp" / "webapp.db")
    compare_verdict = None
    env_ready_for_compare = get_entry_timing_shadow_mode(skill_dir) in {"shadow", "live"} and profile == EXPECTED_EXPERIMENT_PROFILE
    if diagnostics is None:
        errors.append(f"no last_scan diagnostics ({meta.get('error')})")
    else:
        report = build_live_entry_shadow_compare_report(
            diagnostics,
            skill_dir=skill_dir,
            run_id=run_id,
            live_meta=meta,
            experiment_env_ready=env_ready_for_compare,
        )
        if report is None:
            errors.append("could not build live/offline compare report")
        else:
            compare_verdict = (report.get("comparison") or {}).get("verdict")
            if compare_verdict != "pass":
                errors.append(f"latest live compare verdict={compare_verdict}")

    mode = get_entry_timing_shadow_mode(skill_dir)
    ready = not errors
    messages: list[str] = []
    if ready:
        if mode == "live":
            messages.append("Entry timing live enforcement is active.")
        else:
            messages.append(
                "Ready for ENTRY_TIMING_SHADOW_MODE=live (breakout buffer 1.0% only). "
                "Run scripts/apply_entry_timing_live_env.py after dashboard restart plan."
            )
    else:
        messages.append("Not ready for entry-timing live enforcement.")

    if ready and mode == "live":
        messages.append("Live enforcement validated — monitor Stage A throughput for one market week.")

    return {
        "ready": ready,
        "mode": mode,
        "profile": profile,
        "stage2b": stage2b,
        "stack_pf_mean": stack_row.get("pf_mean"),
        "stack_worst_era_pf": stack_row.get("worst_era_pf"),
        "compare_verdict": compare_verdict,
        "errors": errors,
        "notes": notes,
        "messages": messages,
    }
