"""
Phase 1 — overlay A/B sweep orchestrator (full Schwab universe).

Runs the multi-era backtest harness once per config:
  - Config 0:  control (all overlays off)
  - Config 1:  meta_policy=live
  - Config 2:  exec_quality=live
  - Configs 3-11: exit_manager=live across R x hold grid (3x3=9)
  - Configs 12-14: event_risk=live across EVENT_BLOCK_EARNINGS_DAYS in {1,2,3}

Each config invokes ``scripts/run_multi_era_backtest_schwab_only.py`` as a
subprocess with a unique ``--run-tag`` and an ``--env-overrides`` JSON file.
That script already handles per-chunk parallelism, crash-resume, and chunk
caching, so re-launching this orchestrator picks up where it left off.

After each config completes:
  * Read its ``multi_era_backtest_schwab_only_<tag>.json`` artifact
  * Compute per-era PF/return/DD deltas vs the control config
  * Apply the kickoff guardrails:
       - min trades per era >= 50 (else "thin")
       - no era regressed by >0.10 PF
  * Write per-config result to ``validation_artifacts/phase1_results/<tag>.json``
  * Append to top-level checkpoint ``validation_artifacts/phase1_progress.json``

When all configs finish, prints a ranked Pareto table (PF mean, worst-era PF,
worst-era DD, total trades) and writes the markdown summary to
``validation_artifacts/phase1_overlay_sweep_<run_id>.md``.

Resumable: simply re-run; configs whose result file already exists are skipped.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SKILL_DIR = Path(__file__).resolve().parent.parent
ARTIFACT_DIR = SKILL_DIR / "validation_artifacts"
RESULTS_DIR = ARTIFACT_DIR / "phase1_results"
DEFAULT_PROGRESS_PATH = ARTIFACT_DIR / "phase1_progress.json"
ENV_DIR = ARTIFACT_DIR / "phase1_env_overrides"
MULTI_ERA_SCRIPT = SKILL_DIR / "scripts" / "run_multi_era_backtest_schwab_only.py"

PER_ERA_MIN_TRADES = 50
MAX_PER_ERA_PF_REGRESSION = 0.10


@dataclass
class SweepConfig:
    config_id: str
    description: str
    env: dict[str, str] = field(default_factory=dict)


# Neutral baseline pins mirroring control_legacy_aug.json. Sweep configs used
# to inherit these from the live .env, which drifted under them: by 2026-06-26
# the .env had entry-timing LIVE enforcement plus the hard breakout-volume
# quality gate, and a control_legacy re-run recorded 0 trades in all five eras
# with a normal exclusion profile. Pin them so sweep results are reproducible
# regardless of local .env state.
_NEUTRAL_BASELINE_PINS: dict[str, str] = {
    "ENTRY_TIMING_SHADOW_MODE": "off",
    "QUALITY_GATES_MODE": "shadow",
    "QUALITY_REQUIRE_BREAKOUT_VOLUME": "false",
    "SCAN_VCP_GATE_MODE": "shadow",
    "SCAN_SECTOR_GATE_MODE": "shadow",
    "SCAN_VCP_PENALTY_POINTS": "0",
    "SCAN_SECTOR_PENALTY_POINTS": "0",
    "SCAN_SECTOR_UNRESOLVED_PENALTY_POINTS": "0",
}


def _build_configs() -> list[SweepConfig]:
    configs: list[SweepConfig] = []
    # Two controls so the sweep can be reported vs the legacy "everything off"
    # baseline AND vs the production-default baseline simultaneously. See
    # phase1_prologue_overlay_wiring_bug.md for the discovery that motivated
    # this split.
    configs.append(SweepConfig(
        config_id="control_legacy",
        description="All overlays off (matches every prior multi-era artifact).",
        env={
            "META_POLICY_MODE": "off",
            "UNCERTAINTY_MODE": "off",
            "EVENT_RISK_MODE": "off",
            "EXIT_MANAGER_MODE": "off",
            "EXEC_QUALITY_MODE": "off",
            **_NEUTRAL_BASELINE_PINS,
        },
    ))
    configs.append(SweepConfig(
        config_id="control_legacy_exits",
        description="Legacy exit sim: hold=20, no trail grace, no soft-exit deferral.",
        env={
            "META_POLICY_MODE": "off",
            "UNCERTAINTY_MODE": "off",
            "EVENT_RISK_MODE": "off",
            "EXIT_MANAGER_MODE": "off",
            "EXEC_QUALITY_MODE": "off",
            "BACKTEST_HOLD_DAYS": "20",
            "BACKTEST_MIN_HOLD_DAYS_BEFORE_TRAIL": "0",
            "BACKTEST_MIN_HOLD_DEFER_SOFT_EXITS": "false",
        },
    ))
    configs.append(SweepConfig(
        config_id="control_prod_default",
        description="event_risk=live, exec_quality=live (current production defaults).",
        env={
            "META_POLICY_MODE": "off",
            "UNCERTAINTY_MODE": "off",
            "EVENT_RISK_MODE": "live",
            "EVENT_BLOCK_EARNINGS_DAYS": "2",
            "EVENT_ACTION": "block",
            "EXIT_MANAGER_MODE": "off",
            "EXEC_QUALITY_MODE": "live",
        },
    ))
    # Isolated meta-policy.
    configs.append(SweepConfig(
        config_id="meta_policy_live",
        description="Meta-policy + uncertainty in live mode (defaults).",
        env={
            "META_POLICY_MODE": "live",
            "UNCERTAINTY_MODE": "live",
            "EVENT_RISK_MODE": "off",
            "EXIT_MANAGER_MODE": "off",
            "EXEC_QUALITY_MODE": "off",
        },
    ))
    # Isolated exec quality.
    configs.append(SweepConfig(
        config_id="exec_quality_live",
        description="Exec-quality liquidity-aware slippage adjustment.",
        env={
            "META_POLICY_MODE": "off",
            "UNCERTAINTY_MODE": "off",
            "EVENT_RISK_MODE": "off",
            "EXIT_MANAGER_MODE": "off",
            "EXEC_QUALITY_MODE": "live",
        },
    ))
    # Exit grace: defer trailing/soft exits so winners can reach the 21-40d edge
    # bucket (see phase1_trade_diagnostics hold-bucket decomposition).
    _exit_grace_base = {
        "META_POLICY_MODE": "off",
        "UNCERTAINTY_MODE": "off",
        "EVENT_RISK_MODE": "off",
        "EXIT_MANAGER_MODE": "off",
        "EXEC_QUALITY_MODE": "off",
        "BACKTEST_MIN_HOLD_DEFER_SOFT_EXITS": "true",
    }
    for min_trail, hold in ((15, 40), (10, 40), (15, 30)):
        configs.append(SweepConfig(
            config_id=f"exit_grace_t{min_trail}_h{hold}",
            description=(
                f"Exit grace: defer soft/trailing exits until day {min_trail}, "
                f"max hold {hold} days (no exit-manager overlay)."
            ),
            env={
                **_exit_grace_base,
                "BACKTEST_MIN_HOLD_DAYS_BEFORE_TRAIL": str(min_trail),
                "BACKTEST_HOLD_DAYS": str(hold),
            },
        ))
    # Exit manager 3x3 sweep: R-mult x max-hold.
    for r_mult in (1.0, 1.5, 2.0):
        for hold in (15, 25, 40):
            configs.append(SweepConfig(
                config_id=f"exit_R{r_mult:.1f}_H{hold:02d}",
                description=f"Exit manager live: partial TP at {r_mult}R, max hold {hold} days.",
                env={
                    "META_POLICY_MODE": "off",
                    "UNCERTAINTY_MODE": "off",
                    "EVENT_RISK_MODE": "off",
                    "EXIT_MANAGER_MODE": "live",
                    "EXEC_QUALITY_MODE": "off",
                    "EXIT_PARTIAL_TP_R_MULT": str(r_mult),
                    "EXIT_MAX_HOLD_DAYS": str(hold),
                },
            ))
    # Diagnostic Q1 — Stage-2-only ablation. Strip every downstream filter
    # (VCP penalty, sector penalty, quality gates, forensic, PEAD, advisory)
    # so we can measure the entry edge of the bare Stage 2 + breakout signal.
    # All overlays are off and all penalty points are zeroed; SPY > 200 SMA
    # regime gate stays on (it's a sanity baseline, not a filter under test).
    # See validation_artifacts/phase1_strategy_pivot_2026-04-19.md.
    configs.append(SweepConfig(
        config_id="stage2_only",
        description="Q1 diagnostic: Stage 2 + breakout only — all gates/overlays off.",
        env={
            "META_POLICY_MODE": "off",
            "UNCERTAINTY_MODE": "off",
            "EVENT_RISK_MODE": "off",
            "EXIT_MANAGER_MODE": "off",
            "EXEC_QUALITY_MODE": "off",
            "QUALITY_GATES_ENABLED": "false",
            "FORENSIC_ENABLED": "false",
            "PEAD_ENABLED": "false",
            "ADVISORY_MODEL_ENABLED": "false",
            "SCAN_VCP_GATE_MODE": "shadow",
            "SCAN_SECTOR_GATE_MODE": "shadow",
            "SCAN_VCP_PENALTY_POINTS": "0",
            "SCAN_SECTOR_PENALTY_POINTS": "0",
            "SCAN_SECTOR_UNRESOLVED_PENALTY_POINTS": "0",
        },
    ))
    # Event risk sweep on block-window days.
    for days in (1, 2, 3):
        configs.append(SweepConfig(
            config_id=f"event_block{days}d",
            description=f"Event risk live: block entries within {days} days of earnings.",
            env={
                "META_POLICY_MODE": "off",
                "UNCERTAINTY_MODE": "off",
                "EVENT_RISK_MODE": "live",
                "EXIT_MANAGER_MODE": "off",
                "EXEC_QUALITY_MODE": "off",
                "EVENT_BLOCK_EARNINGS_DAYS": str(days),
                "EVENT_ACTION": "block",
            },
        ))
    # ── Signal-gate sweep (base-signal fix program) ─────────────────────────
    # Phase 2 verdict: base signal PF mean 1.005, killed by <20-day false
    # breakouts. These configs attack entry quality directly. All overlays off
    # (control_legacy baseline) so the gate effect is isolated.
    # Neutral pins keep the live .env (entry-timing live enforcement, hard
    # breakout-volume gate) from leaking into treatments; gate configs below
    # override individual keys on top of this base.
    _signal_gate_base = {
        "META_POLICY_MODE": "off",
        "UNCERTAINTY_MODE": "off",
        "EVENT_RISK_MODE": "off",
        "EXIT_MANAGER_MODE": "off",
        "EXEC_QUALITY_MODE": "off",
        **_NEUTRAL_BASELINE_PINS,
    }
    # Confluence gate: Stage2+VCP must be confirmed by PEAD-positive or
    # advisory-high (require_count=1) / by both (require_count=2).
    for require_count, tag in ((1, "either"), (2, "both")):
        configs.append(SweepConfig(
            config_id=f"confluence_{tag}",
            description=(
                f"Confluence gate live: require {require_count} independent confirmation(s) "
                "(PEAD-positive / advisory-high) on top of Stage 2 + VCP."
            ),
            env={
                **_signal_gate_base,
                "CONFLUENCE_GATE_MODE": "live",
                "CONFLUENCE_REQUIRE_COUNT": str(require_count),
            },
        ))
    # Breakout follow-through: require 2 consecutive closes above prior high.
    configs.append(SweepConfig(
        config_id="breakout_2bar",
        description="Breakout confirmation window: 2 consecutive bars of follow-through.",
        env={
            **_signal_gate_base,
            "BREAKOUT_CONFIRM_BARS": "2",
        },
    ))
    # VCP window ablation: measure dry-up strictly before the breakout bar.
    # The legacy VCP check includes the entry bar, which forces every accepted
    # signal to break out on BELOW-average volume (and made the volume-gate
    # configs below mathematically unsatisfiable in the 2026-06-10 sweep).
    configs.append(SweepConfig(
        config_id="vcp_pre_breakout",
        description="VCP dry-up measured on bars before the breakout bar (no volume gate).",
        env={
            **_signal_gate_base,
            "VCP_EXCLUDE_BREAKOUT_BARS": "1",
        },
    ))
    # Breakout volume confirmation: hard-require latest/avg50 volume ratio.
    # Requires VCP_EXCLUDE_BREAKOUT_BARS >= 1: with the legacy VCP window the
    # entry bar is below-average volume by construction and no signal can pass
    # a ratio >= 1.0 gate.
    # QUALITY_GATES_MODE=soft with soft-min 99 isolates the hard volume gate:
    # the backtest's quality filter is a no-op in shadow mode, and soft-min 99
    # keeps unrelated soft reasons (low_signal_score etc.) from contaminating
    # the treatment.
    for ratio in ("1.00", "1.20", "1.50"):
        configs.append(SweepConfig(
            config_id=f"breakout_vol_{ratio.replace('.', '')}",
            description=f"Hard breakout-volume gate: latest/avg50 volume >= {ratio} (VCP measured pre-breakout).",
            env={
                **_signal_gate_base,
                "VCP_EXCLUDE_BREAKOUT_BARS": "1",
                "QUALITY_GATES_MODE": "soft",
                "QUALITY_SOFT_MIN_REASONS": "99",
                "QUALITY_REQUIRE_BREAKOUT_VOLUME": "true",
                "QUALITY_BREAKOUT_VOLUME_MIN_RATIO": ratio,
            },
        ))
    # Combined best-guess: confluence (either) + 2-bar follow-through + 1.2x volume.
    configs.append(SweepConfig(
        config_id="signal_gate_combo",
        description="Combo: confluence(either) + 2-bar breakout + 1.2x volume gate (VCP pre-breakout).",
        env={
            **_signal_gate_base,
            "CONFLUENCE_GATE_MODE": "live",
            "CONFLUENCE_REQUIRE_COUNT": "1",
            "BREAKOUT_CONFIRM_BARS": "2",
            "VCP_EXCLUDE_BREAKOUT_BARS": "2",
            "QUALITY_GATES_MODE": "soft",
            "QUALITY_SOFT_MIN_REASONS": "99",
            "QUALITY_REQUIRE_BREAKOUT_VOLUME": "true",
            "QUALITY_BREAKOUT_VOLUME_MIN_RATIO": "1.20",
        },
    ))
    return configs


def _write_env_overrides_file(cfg: SweepConfig) -> Path:
    ENV_DIR.mkdir(parents=True, exist_ok=True)
    path = ENV_DIR / f"{cfg.config_id}.json"
    path.write_text(json.dumps(cfg.env, indent=2), encoding="utf-8")
    return path


def _result_path(config_id: str) -> Path:
    return RESULTS_DIR / f"{config_id}.json"


def _multi_era_artifact_path(config_id: str) -> Path:
    return ARTIFACT_DIR / f"multi_era_backtest_schwab_only_{config_id}.json"


def _artifact_is_complete(config_id: str) -> bool:
    """True if the multi-era artifact exists and covers every era with no failures.

    The multi-era subprocess can exit nonzero for reasons unrelated to the
    backtest itself (e.g. CPython exit code 120 when stdout/stderr flushing
    fails at interpreter shutdown on Windows/OneDrive consoles — observed
    2026-06-10, which discarded four completed multi-hour runs). The artifact
    on disk is the source of truth, so trust it over the process return code.
    """
    p = _multi_era_artifact_path(config_id)
    if not p.exists():
        return False
    try:
        payload = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return False
    if payload.get("failed_eras"):
        return False
    eras = {str(r.get("era")) for r in payload.get("results", [])}
    expected = {"recent_current", "bear_rates", "crash_recovery", "volatility_chop", "late_bull"}
    return expected.issubset(eras)


def _load_control_results(config_id: str = "control_legacy") -> dict[str, dict[str, Any]] | None:
    p = _multi_era_artifact_path(config_id)
    if not p.exists():
        return None
    try:
        payload = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None
    return {str(r["era"]): r for r in payload.get("results", [])}


def _summarise_per_era(
    treatment: dict[str, dict[str, Any]],
    control: dict[str, dict[str, Any]] | None,
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    pf_deltas: list[float] = []
    pf_treatment: list[float] = []
    thin_eras: list[str] = []
    regressed_eras: list[dict[str, Any]] = []
    for era, t in treatment.items():
        c = (control or {}).get(era) if control else None
        t_pf = t.get("profit_factor_net")
        c_pf = (c or {}).get("profit_factor_net") if c else None
        try:
            t_pf_f = float(t_pf) if t_pf not in (None, "inf") else None
        except (TypeError, ValueError):
            t_pf_f = None
        try:
            c_pf_f = float(c_pf) if c_pf not in (None, "inf") else None
        except (TypeError, ValueError):
            c_pf_f = None
        pf_delta = (t_pf_f - c_pf_f) if (t_pf_f is not None and c_pf_f is not None) else None
        if pf_delta is not None:
            pf_deltas.append(pf_delta)
        if t_pf_f is not None:
            pf_treatment.append(t_pf_f)
        trades = int(t.get("total_trades", 0) or 0)
        if trades < PER_ERA_MIN_TRADES:
            thin_eras.append(era)
        if pf_delta is not None and pf_delta < -MAX_PER_ERA_PF_REGRESSION:
            regressed_eras.append({"era": era, "pf_delta": round(pf_delta, 4)})
        rows.append({
            "era": era,
            "trades": trades,
            "win_rate_net": t.get("win_rate_net"),
            "pf_treatment": t_pf_f,
            "pf_control": c_pf_f,
            "pf_delta": round(pf_delta, 4) if pf_delta is not None else None,
            "dd_treatment": t.get("max_drawdown_net_pct"),
            "dd_control": (c or {}).get("max_drawdown_net_pct") if c else None,
            "ret_treatment": t.get("total_return_net_pct"),
            "ret_control": (c or {}).get("total_return_net_pct") if c else None,
        })
    pf_mean_treatment = sum(pf_treatment) / len(pf_treatment) if pf_treatment else 0.0
    pf_mean_delta = sum(pf_deltas) / len(pf_deltas) if pf_deltas else 0.0
    return {
        "rows": rows,
        "pf_mean_treatment": round(pf_mean_treatment, 4),
        "pf_mean_delta": round(pf_mean_delta, 4),
        "worst_era_pf_treatment": round(min(pf_treatment), 4) if pf_treatment else None,
        "thin_eras": thin_eras,
        "regressed_eras": regressed_eras,
        "passes_guardrails": (not thin_eras) and (not regressed_eras),
    }


def _read_multi_era(config_id: str) -> dict[str, dict[str, Any]] | None:
    p = _multi_era_artifact_path(config_id)
    if not p.exists():
        return None
    try:
        payload = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None
    return {str(r["era"]): r for r in payload.get("results", [])}


def _write_progress(state: dict[str, Any], path: Path | None = None) -> None:
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    target = path or DEFAULT_PROGRESS_PATH
    # Atomic write with OneDrive/AV-friendly retry (see
    # run_multi_era_backtest_schwab_only.py::_safe_atomic_write for context).
    import time as _time

    tmp = target.with_suffix(target.suffix + ".tmp")
    contents = json.dumps(state, indent=2)
    try:
        tmp.write_text(contents, encoding="utf-8")
    except PermissionError:
        for attempt in range(6):
            _time.sleep(0.25 * (2 ** attempt))
            try:
                tmp.write_text(contents, encoding="utf-8")
                break
            except PermissionError:
                continue
        else:
            return  # progress write is non-fatal
    for attempt in range(6):
        try:
            os.replace(tmp, target)
            return
        except PermissionError:
            _time.sleep(0.25 * (2 ** attempt))
        except FileNotFoundError:
            return
    try:
        target.write_text(contents, encoding="utf-8")
    except Exception:
        pass


def _run_one(
    cfg: SweepConfig,
    max_workers: int,
    chunk_size: int,
    ticker_limit: int,
    *,
    no_resume: bool = False,
) -> int:
    env_path = _write_env_overrides_file(cfg)
    cmd = [
        sys.executable,
        str(MULTI_ERA_SCRIPT),
        "--run-tag", cfg.config_id,
        "--env-overrides", str(env_path),
        "--chunk-size", str(chunk_size),
        "--max-workers", str(max_workers),
    ]
    if ticker_limit and ticker_limit > 0:
        cmd += ["--ticker-limit", str(ticker_limit)]
    if no_resume:
        cmd += ["--no-resume"]
    print(f"[phase1] launching {cfg.config_id}: {cfg.description}")
    proc = subprocess.run(cmd, cwd=str(SKILL_DIR))
    return int(proc.returncode)


def _persist_result(cfg: SweepConfig, control_results: dict[str, dict[str, Any]] | None) -> dict[str, Any] | None:
    treatment = _read_multi_era(cfg.config_id)
    if treatment is None:
        return None
    summary = _summarise_per_era(treatment, control_results)
    payload = {
        "config_id": cfg.config_id,
        "description": cfg.description,
        "env": cfg.env,
        "summary": summary,
        "treatment_results": treatment,
        "control_results": control_results or {},
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    _result_path(cfg.config_id).write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="Phase 1 overlay sweep orchestrator")
    parser.add_argument("--max-workers", type=int, default=4, help="Parallel chunk subprocesses per era.")
    parser.add_argument("--chunk-size", type=int, default=120, help="Ticker chunk size per chunk.")
    parser.add_argument(
        "--only",
        nargs="*",
        default=None,
        help="Run only the given config_ids (default: all).",
    )
    parser.add_argument(
        "--skip-completed",
        action="store_true",
        default=True,
        help="Skip configs whose result file already exists (default).",
    )
    parser.add_argument("--no-skip-completed", dest="skip_completed", action="store_false")
    parser.add_argument(
        "--smoke",
        action="store_true",
        help="Smoke mode: run only control + first exit_manager + first event_risk config to validate plumbing.",
    )
    parser.add_argument(
        "--ticker-limit",
        type=int,
        default=0,
        help="Truncate the universe to the first N tickers (0 = full Schwab; non-zero = smoke).",
    )
    parser.add_argument(
        "--progress-path",
        default="",
        help="Override the progress JSON path. Use a unique value per parallel orchestrator "
             "(e.g. phase1_progress_a.json, phase1_progress_b.json) to avoid write races.",
    )
    parser.add_argument(
        "--no-resume",
        action="store_true",
        help="Ignore existing multi-era progress/chunks for each config (required for ticker-limit smoke).",
    )
    args = parser.parse_args()

    # Fail fast on broken Schwab auth before launching multi-hour configs.
    # (June 2026: five signal-gate configs ran to completion with expired
    # tokens and recorded PF 0.0 / all-eras-thin as if they were results.)
    try:
        from run_multi_era_backtest_schwab_only import _auth_preflight_ok
    except ImportError:
        from scripts.run_multi_era_backtest_schwab_only import _auth_preflight_ok
    if not _auth_preflight_ok():
        return 5

    no_resume = bool(args.no_resume or (args.ticker_limit and args.ticker_limit > 0))

    progress_path: Path | None = (
        Path(args.progress_path) if args.progress_path else DEFAULT_PROGRESS_PATH
    )

    configs = _build_configs()
    if args.only:
        wanted = set(args.only)
        configs = [c for c in configs if c.config_id in wanted]
    if args.smoke:
        smoke_ids = {"control_legacy", "control_prod_default", "exit_R1.5_H25"}
        configs = [c for c in configs if c.config_id in smoke_ids]

    state = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "total_configs": len(configs),
        "completed": [],
        "failed": [],
        "currently_running": None,
        "progress_path": str(progress_path),
    }
    _write_progress(state, progress_path)

    # Always run controls first so treatment runs can compute deltas.
    def _order(c: SweepConfig) -> tuple[int, str]:
        if c.config_id == "control_legacy":
            return (0, c.config_id)
        if c.config_id == "control_legacy_exits":
            return (1, c.config_id)
        if c.config_id == "control_prod_default":
            return (2, c.config_id)
        return (3, c.config_id)
    configs.sort(key=_order)

    for cfg in configs:
        if args.skip_completed and _result_path(cfg.config_id).exists():
            print(f"[phase1] skipping completed {cfg.config_id}")
            state["completed"].append(cfg.config_id)
            _write_progress(state, progress_path)
            continue
        state["currently_running"] = cfg.config_id
        _write_progress(state, progress_path)
        rc = _run_one(
            cfg,
            max_workers=args.max_workers,
            chunk_size=args.chunk_size,
            ticker_limit=args.ticker_limit,
            no_resume=no_resume,
        )
        if rc != 0 and not _artifact_is_complete(cfg.config_id):
            state["failed"].append({"config_id": cfg.config_id, "rc": rc})
            print(f"[phase1] config {cfg.config_id} failed with rc={rc}")
            _write_progress(state, progress_path)
            continue
        if rc != 0:
            print(
                f"[phase1] config {cfg.config_id} exited rc={rc} but artifact is "
                "complete; persisting result anyway."
            )
        control_results = _load_control_results()
        result = _persist_result(cfg, control_results)
        if result is None:
            state["failed"].append({"config_id": cfg.config_id, "reason": "missing_artifact"})
        else:
            state["completed"].append(cfg.config_id)
            s = result["summary"]
            print(
                f"[phase1] {cfg.config_id} done. "
                f"PF mean treatment={s['pf_mean_treatment']:.3f} "
                f"PF mean delta={s['pf_mean_delta']:+.3f} "
                f"worst era PF={s['worst_era_pf_treatment']} "
                f"thin_eras={s['thin_eras']} "
                f"regressed_eras={s['regressed_eras']} "
                f"passes_guardrails={s['passes_guardrails']}"
            )
        state["currently_running"] = None
        _write_progress(state, progress_path)

    # Final ranking once everything is in.
    rankable: list[dict[str, Any]] = []
    for cfg in configs:
        rp = _result_path(cfg.config_id)
        if not rp.exists():
            continue
        try:
            d = json.loads(rp.read_text(encoding="utf-8"))
        except Exception:
            continue
        s = d["summary"]
        rankable.append({
            "config_id": cfg.config_id,
            "description": cfg.description,
            "pf_mean_treatment": s["pf_mean_treatment"],
            "pf_mean_delta": s["pf_mean_delta"],
            "worst_era_pf_treatment": s["worst_era_pf_treatment"],
            "thin_eras": s["thin_eras"],
            "regressed_eras": s["regressed_eras"],
            "passes_guardrails": s["passes_guardrails"],
        })
    rankable.sort(key=lambda r: (-r["pf_mean_delta"], -float(r["worst_era_pf_treatment"] or 0)))
    state["ranking"] = rankable
    state["finished_at"] = datetime.now(timezone.utc).isoformat()
    _write_progress(state, progress_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
