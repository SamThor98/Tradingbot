"""
Phase 2 — Stage 1: Edge audit.

Question this script answers
----------------------------
  Does the bare entry signal (Stage 2 + breakout, all overlays off) carry a
  defensible edge across all five eras? If not, no amount of overlay tuning
  or guardrail adjustment will produce a robust strategy and Phase 2 should
  halt in favor of signal/regime work.

How it works
------------
* Loads two existing run_ids from validation_artifacts/multi_era_chunks/:
    - stage2_only:    bare Stage 2 + breakout, every gate / overlay off.
    - control_legacy: same backtest harness with the production-default
                      pre-overlay scoring (the historical baseline).
* Computes per-era PF, win rate, expectancy, equity-curve max DD,
  total return, trade count, avg hold, median stop. Same code path as
  every Phase 2 script via phase2_common.
* Computes deltas: stage2_only - control_legacy per era.
* Applies a verdict matrix on stage2_only that decides whether to proceed
  to Stage 2 (replay engine instrumentation), iterate cautiously, or halt.

Verdict thresholds
------------------
The thresholds below are deliberately conservative. They encode the rule:
"don't spend three to four days of compute on Optuna unless the bare signal
already shows a real, regime-stable edge." Tighten or loosen as you accumulate
evidence about your strategy's noise floor.

  PROCEED:    pf_mean >= PROCEED_PF_MEAN AND worst_era_pf >= PROCEED_WORST
  ITERATE:    pf_mean >= ITERATE_PF_MEAN AND worst_era_pf >= ITERATE_WORST
              (and not PROCEED)
  HALT:       otherwise

The script also reports the *relative* picture (stage2_only vs control_legacy):
    overlays_helping     control_legacy PF_mean materially exceeds stage2_only
    overlays_neutral     within +/- 0.05 PF_mean
    overlays_hurting     stage2_only PF_mean materially exceeds control_legacy
This relative finding is independent of the absolute verdict and can flip the
recommendation (e.g. PROCEED but overlays_hurting -> recommend stripping
overlays before further tuning).

Outputs
-------
  validation_artifacts/phase2_edge_audit.json
  validation_artifacts/phase2_edge_audit.md
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SKILL_DIR = Path(__file__).resolve().parent.parent
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

from scripts.phase2_common import (  # noqa: E402
    ARTIFACT_DIR,
    ERA_BOUNDS,
    EraStats,
    Trade,
    fmt_pct,
    fmt_pct_unit,
    fmt_pf,
    load_trades,
    per_era_stats,
)

LOG = logging.getLogger(__name__)

PROCEED_PF_MEAN = 1.20
PROCEED_WORST = 1.00
ITERATE_PF_MEAN = 1.05
ITERATE_WORST = 0.85

# Relative-overlay thresholds (treatment - control on PF mean).
OVERLAYS_MATERIAL_DELTA = 0.05

# An audit cannot honestly verdict "proceed" or "iterate" with fewer than this
# many eras of data — single-era PF is too noisy and the per-era guardrails
# in phase1_overlay_sweep.py (worst-era PF, no era regressed > 0.10) lose their
# meaning. Tune up if you add more historical eras to the harness.
MIN_ERAS_FOR_VERDICT = 3


@dataclass
class RunSummary:
    run_id: str
    eras: list[EraStats]
    pf_mean: float | None
    worst_era_pf: float | None
    n_total: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "n_total": self.n_total,
            "pf_mean": self.pf_mean,
            "worst_era_pf": self.worst_era_pf,
            "eras": [e.to_dict() for e in self.eras],
        }


def _summarise(run_id: str, trades: list[Trade]) -> RunSummary:
    eras = per_era_stats(trades)
    pfs: list[float] = []
    for s in eras:
        if s.pf is None:
            continue
        if isinstance(s.pf, float) and s.pf == float("inf"):
            continue
        pfs.append(float(s.pf))
    pf_mean = sum(pfs) / len(pfs) if pfs else None
    worst = min(pfs) if pfs else None
    return RunSummary(
        run_id=run_id,
        eras=eras,
        pf_mean=pf_mean,
        worst_era_pf=worst,
        n_total=sum(s.n for s in eras),
    )


def _verdict(stage2: RunSummary) -> str:
    pf = stage2.pf_mean
    worst = stage2.worst_era_pf
    if pf is None or worst is None:
        return "halt_insufficient_data"
    # Hard floor on era coverage: single-era audits are not honest enough to
    # justify funding three to four days of Optuna compute, regardless of PF.
    if len(stage2.eras) < MIN_ERAS_FOR_VERDICT:
        return "halt_insufficient_data"
    if pf >= PROCEED_PF_MEAN and worst >= PROCEED_WORST:
        return "proceed"
    if pf >= ITERATE_PF_MEAN and worst >= ITERATE_WORST:
        return "iterate_with_caution"
    return "halt_fix_signal_first"


def _aligned_pf_means(stage2: RunSummary, control: RunSummary) -> tuple[float | None, float | None, list[str]]:
    """
    Restrict both runs to the eras that exist in *both*, then return their
    PF means and the era list used. This prevents the apples-to-oranges
    failure where a partially-completed bare-signal sweep is compared
    against a fully-populated control whose mean includes eras the bare
    sweep never ran.
    """
    s_eras = {e.era: e for e in stage2.eras}
    c_eras = {e.era: e for e in control.eras}
    common = sorted(s_eras.keys() & c_eras.keys())
    s_pfs: list[float] = []
    c_pfs: list[float] = []
    for era in common:
        sp = s_eras[era].pf
        cp = c_eras[era].pf
        if sp is None or cp is None:
            continue
        if isinstance(sp, float) and sp == float("inf"):
            continue
        if isinstance(cp, float) and cp == float("inf"):
            continue
        s_pfs.append(float(sp))
        c_pfs.append(float(cp))
    s_mean = sum(s_pfs) / len(s_pfs) if s_pfs else None
    c_mean = sum(c_pfs) / len(c_pfs) if c_pfs else None
    return s_mean, c_mean, common


def _overlay_finding(stage2: RunSummary, control: RunSummary) -> str:
    """
    Compare the bare and control PF means *only on eras both runs ran*.
    Otherwise a partially-completed bare sweep can falsely look like the
    overlays are hurting (or helping) just because the control mean is
    averaging over more eras.
    """
    s_pf, c_pf, _common = _aligned_pf_means(stage2, control)
    if s_pf is None or c_pf is None:
        return "unknown"
    delta = s_pf - c_pf
    if delta > OVERLAYS_MATERIAL_DELTA:
        return "overlays_hurting"
    if delta < -OVERLAYS_MATERIAL_DELTA:
        return "overlays_helping"
    return "overlays_neutral"


def _recommendation(
    verdict: str,
    finding: str,
    *,
    bare_eras: list[str] | None = None,
    aligned_eras: list[str] | None = None,
) -> str:
    """Combine the absolute verdict with the relative overlay finding."""
    if verdict == "halt_fix_signal_first":
        return (
            "STOP. Bare signal does not show a defensible edge across eras. "
            "No amount of overlay/guardrail tuning will fix this. Recommend "
            "going upstream: signal generation, regime gate, or universe "
            "selection. Phase 2 should not be funded until this changes."
        )
    if verdict == "halt_insufficient_data":
        eras_present = ", ".join(bare_eras) if bare_eras else "(none)"
        return (
            "STOP. The bare-signal sweep does not have data for enough eras "
            f"to support an honest verdict (need >= {MIN_ERAS_FOR_VERDICT}, "
            f"have {len(bare_eras or [])}: {eras_present}). Per-era PF on a "
            "single era is too noisy to gate Phase 2 spend. Options:\n"
            "  (a) Backfill the bare sweep by re-running phase1_overlay_sweep.py "
            "with --only stage2_only across the missing eras before continuing.\n"
            "  (b) Pick a different bare baseline (any run_id with all 5 eras "
            "complete) and re-run this audit with --bare-run-id <that_id>.\n"
            "  (c) Skip Stage 1 and trust phase1_trade_diagnostics.py "
            "counterfactuals as your edge evidence (weaker but free)."
        )
    base = {
        "proceed": "Bare signal shows a defensible edge.",
        "iterate_with_caution": (
            "Bare signal shows a marginal edge. Phase 2 is worth doing but "
            "expect modest gains; budget compute conservatively."
        ),
    }[verdict]
    overlay_msg = {
        "overlays_helping": (
            "Overlays are adding edge vs. the bare signal. Continue to Stage 2 "
            "(replay engine) and refine overlay parameters with Optuna."
        ),
        "overlays_neutral": (
            "Overlays are roughly neutral. Continue to Stage 2 but treat the "
            "first replay sweep as an opportunity to *remove* overlays that "
            "do not pay rent rather than tune more aggressively."
        ),
        "overlays_hurting": (
            "Overlays are *costing* edge. Before Stage 2, run a sweep that "
            "removes one overlay at a time and re-checks PF. Do NOT proceed "
            "to Optuna until the overlay stack is at parity or better than "
            "the bare signal."
        ),
        "unknown": "Overlay impact could not be determined; gather control data first.",
    }[finding]
    return f"{base} {overlay_msg}"


def _markdown(
    stage2: RunSummary,
    control: RunSummary,
    verdict: str,
    finding: str,
    recommendation: str,
    *,
    aligned_eras: list[str] | None = None,
    aligned_bare_pf: float | None = None,
    aligned_control_pf: float | None = None,
) -> str:
    lines: list[str] = []
    lines.append("# Phase 2 — Stage 1 edge audit")
    lines.append("")
    lines.append(f"_Generated: {datetime.now(timezone.utc).isoformat()}_  ")
    lines.append(f"_Verdict: **{verdict}**_  ")
    lines.append(f"_Overlay finding: **{finding}**_  ")
    lines.append("")
    lines.append("## Recommendation")
    lines.append("")
    lines.append(recommendation)
    lines.append("")
    lines.append("## Headline numbers")
    lines.append("")
    lines.append("| run_id | eras present | total trades | PF mean (raw) | worst-era PF |")
    lines.append("|---|---|---:|---:|---:|")
    for r in (stage2, control):
        eras_present = ", ".join(e.era for e in r.eras) or "—"
        lines.append(
            f"| `{r.run_id}` | {eras_present} | {r.n_total} | {fmt_pf(r.pf_mean)} | {fmt_pf(r.worst_era_pf)} |"
        )
    lines.append("")
    if aligned_eras:
        delta_str = (
            "%+.3f" % (aligned_bare_pf - aligned_control_pf)
            if (aligned_bare_pf is not None and aligned_control_pf is not None)
            else "n/a"
        )
        lines.append(f"**Aligned PF mean** (only on eras present in both runs: {', '.join(aligned_eras)}):")
        lines.append("")
        lines.append(f"- bare = {fmt_pf(aligned_bare_pf)}  control = {fmt_pf(aligned_control_pf)}  Δ = {delta_str}")
        lines.append("")
    else:
        lines.append("**Aligned PF mean: not computable — runs share no eras.**")
        lines.append("")
    lines.append("## Per-era detail")
    lines.append("")
    lines.append("| era | run | n | PF | win | exp | avg hold | med stop | max DD | total ret |")
    lines.append("|---|---|---:|---:|---:|---:|---:|---:|---:|---:|")
    for era in ERA_BOUNDS:
        for r in (stage2, control):
            s = next((e for e in r.eras if e.era == era), None)
            if s is None:
                continue
            lines.append(
                f"| {era} | `{r.run_id}` | {s.n} | {fmt_pf(s.pf)} "
                f"| {fmt_pct_unit(s.win_rate)} | {fmt_pct_unit(s.expectancy)} "
                f"| {s.avg_hold_days:.1f}d | {s.median_stop_pct * 100:.2f}% "
                f"| {s.max_dd_pct:.2f}% | {fmt_pct(s.total_return_pct)} |"
            )
        lines.append("|  |  |  |  |  |  |  |  |  |  |")
    lines.append("")
    lines.append("## Per-era PF delta (stage2_only - control_legacy)")
    lines.append("")
    lines.append("| era | stage2_only PF | control_legacy PF | Δ |")
    lines.append("|---|---:|---:|---:|")
    for era in ERA_BOUNDS:
        s2 = next((e for e in stage2.eras if e.era == era), None)
        cl = next((e for e in control.eras if e.era == era), None)
        if s2 is None or cl is None:
            continue
        try:
            d = float(s2.pf) - float(cl.pf) if (s2.pf is not None and cl.pf is not None) else None
        except (TypeError, ValueError):
            d = None
        lines.append(f"| {era} | {fmt_pf(s2.pf)} | {fmt_pf(cl.pf)} | {('%+.3f' % d) if d is not None else 'n/a'} |")
    lines.append("")
    lines.append("## Threshold reference")
    lines.append("")
    lines.append(
        f"- PROCEED requires PF_mean >= {PROCEED_PF_MEAN:.2f} AND worst-era PF >= {PROCEED_WORST:.2f}.\n"
        f"- ITERATE requires PF_mean >= {ITERATE_PF_MEAN:.2f} AND worst-era PF >= {ITERATE_WORST:.2f}.\n"
        f"- Overlays-material threshold = +/- {OVERLAYS_MATERIAL_DELTA:.2f} PF_mean.\n"
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Phase 2 Stage 1 edge audit")
    parser.add_argument(
        "--bare-run-id",
        default="stage2_only",
        help="Sub-directory of multi_era_chunks/ that holds the bare-signal run.",
    )
    parser.add_argument(
        "--control-run-id",
        default="control_legacy",
        help="Sub-directory of multi_era_chunks/ that holds the comparison run.",
    )
    parser.add_argument(
        "--out-prefix",
        default="phase2_edge_audit",
        help="Prefix (without extension) for the JSON and MD outputs.",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    LOG.info("Loading bare run %s ...", args.bare_run_id)
    bare_trades = load_trades(args.bare_run_id)
    LOG.info("Loaded %d trades for %s", len(bare_trades), args.bare_run_id)

    LOG.info("Loading control run %s ...", args.control_run_id)
    control_trades = load_trades(args.control_run_id)
    LOG.info("Loaded %d trades for %s", len(control_trades), args.control_run_id)

    bare_summary = _summarise(args.bare_run_id, bare_trades)
    control_summary = _summarise(args.control_run_id, control_trades)

    verdict = _verdict(bare_summary)
    finding = _overlay_finding(bare_summary, control_summary)
    _s_pf, _c_pf, aligned_eras = _aligned_pf_means(bare_summary, control_summary)
    bare_eras = [e.era for e in bare_summary.eras]
    recommendation = _recommendation(verdict, finding, bare_eras=bare_eras, aligned_eras=aligned_eras)

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "thresholds": {
            "proceed_pf_mean": PROCEED_PF_MEAN,
            "proceed_worst": PROCEED_WORST,
            "iterate_pf_mean": ITERATE_PF_MEAN,
            "iterate_worst": ITERATE_WORST,
            "overlays_material_delta": OVERLAYS_MATERIAL_DELTA,
            "min_eras_for_verdict": MIN_ERAS_FOR_VERDICT,
        },
        "verdict": verdict,
        "overlay_finding": finding,
        "recommendation": recommendation,
        "aligned_eras": aligned_eras,
        "aligned_pf_mean": {"bare": _s_pf, "control": _c_pf},
        "bare": bare_summary.to_dict(),
        "control": control_summary.to_dict(),
    }

    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    out_json = ARTIFACT_DIR / f"{args.out_prefix}.json"
    out_md = ARTIFACT_DIR / f"{args.out_prefix}.md"
    out_json.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    out_md.write_text(
        _markdown(
            bare_summary,
            control_summary,
            verdict,
            finding,
            recommendation,
            aligned_eras=aligned_eras,
            aligned_bare_pf=_s_pf,
            aligned_control_pf=_c_pf,
        ),
        encoding="utf-8",
    )

    LOG.info("Verdict: %s", verdict)
    LOG.info("Overlay finding: %s", finding)
    LOG.info("Bare PF mean: %s   worst era: %s", fmt_pf(bare_summary.pf_mean), fmt_pf(bare_summary.worst_era_pf))
    LOG.info(
        "Control PF mean: %s   worst era: %s", fmt_pf(control_summary.pf_mean), fmt_pf(control_summary.worst_era_pf)
    )
    LOG.info("Wrote %s", out_json)
    LOG.info("Wrote %s", out_md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
