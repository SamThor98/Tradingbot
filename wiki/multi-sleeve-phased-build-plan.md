---
source: n/a (from multi-sleeve constitution grilling 2026-07-27)
created: 2026-07-27
updated: 2026-07-27
tags: [runbook, build-plan, multi-sleeve, p0, architecture]
---

# Multi-Sleeve Phased Build Plan

> Executable roadmap for [[multi-sleeve-trading-system-constitution]].
> Thin vertical slice first (live S0+S3, paper S1); full net book when S1 earns LIVE.
> Research wave runs in parallel once R7 is law — do not block edge learning on perfect plumbing.

**Status:** engineering scaffold for Phases 0–8 landed 2026-07-28.
Live/operator exit criteria (fidelity sessions, multi-era promotes, canary weeks)
remain **open** — see “Completion reality” below.
**Constitution lock date:** 2026-07-27.

## How to use this plan

1. Kick off **one phase at a time** (or named parallel tracks inside a phase).
2. A phase is **done** only when its **exit criteria** pass — not when code lands.
3. Every behavior change: offline floors (PF / worst-era / DD≤20%) + soft-rank Sharpe→retention→expectancy + R7 when applicable.
4. Mode ladder: OFF → SHADOW → LIVE; never skip shadow for risk-affecting pieces.
5. One policy delta per experiment (R7).

### Parallelism rule

| Track | What |
|-------|------|
| **Platform** | Allocator, weights, net book, attribution |
| **Process** | R7 ledger/promotion contract |
| **Edge** | R3 / R2 / R4 / R1 / R5 research chips |

Process (Phase 1) unblocks Edge. Platform Phase 2–3 can overlap Edge after Phase 1.
**Do not** start full net-book Phase 7 until S1 fidelity exit criteria pass.

### Validation defaults (every phase)

From `schwab_skill/`:

- Fast: `python -m ruff check .` and targeted `python -m pytest -q`
- Behavior: `python scripts/validate_all.py --profile local --strict --skip-backtest` when routes/config/execution touch
- Multi-era / PF: phase-specific scripts noted below; never waive constitution floors

---

## Phase 0 — Baseline freeze

**Goal:** Know exactly what “current live” is before changing architecture.

| Item | Action |
|------|--------|
| Document | Snapshot live env stack (buffer, exit grace 15/40, rank-v2 p76, pts_52w≤37) into a dated artifact |
| Ledger | Confirm `HYPOTHESIS_LEDGER_ENABLED` path records S0 alerts |
| Counterfactual | Confirm `COUNTERFACTUAL_LOGGING_ENABLED` is on for the enforced stack |
| Metrics | Capture baseline: signals/week, retention band, book DD if available, PF offline reference |

**Deliverables**

- `validation_artifacts/multi_sleeve_baseline_YYYYMMDD.json` (or equivalent dated note)
- Short “do not regress” checklist pointing at [[signal-quality-rollout]]

**Exit criteria**

- [ ] Baseline artifact written and linked from constitution implementation status
- [ ] Ledger append verified on one real or fixture scan path
- [ ] No code behavior change in this phase

**Depends on:** nothing  
**Est. effort:** small (hours)

---

## Phase 1 — R7 promotion law (process track)

**Goal:** Make “best” falsifiable before inventing more filters.

**Deliverables**

| Piece | Notes |
|-------|--------|
| Sleeve tag on ledger rows | `sleeve_id=S0` (later S1) on every recorded decision |
| Scoring summary | Sleeve-local aggregates at T+1/5/20 + expectancy |
| Promotion guard | Hard veto: offline floors fail **or** ledger expectancy ≤ 0 at N≥40 sleeve-local |
| One-change checklist | Script or doc gate: refuse stacked env diffs in promotion ledger reason |
| Wiki/runbook | Point [[hypothesis-ledger]] + [[promotion-playbook]] at R7 table |

**Likely touch**

- `hypothesis_ledger.py`, `scripts/score_hypothesis_outcomes.py`
- `scripts/decide_and_promote_*` / promotion guard hooks
- `config.py` / env for R7 thresholds
- tests under `tests/test_hypothesis_ledger.py`

**Exit criteria**

- [ ] Sleeve-local summary runnable with N and expectancy visible
- [ ] Veto path unit-tested (expectancy ≤ 0 → no promote)
- [ ] Operator can run one dry-run promote decision for an S0 policy candidate
- [ ] Constitution R7 table matches code defaults

**Depends on:** Phase 0  
**Unblocks:** all Edge phases (3b, 5, 6, 8)  
**Est. effort:** small–medium

---

## Phase 2 — Thin allocator: S0 + S3 risk constitution

**Goal:** Enforce caps, DD staircase, asymmetric regime, crash mode on **new risk** — without requiring multi-sleeve netting yet.

**Deliverables**

| Piece | Mode path |
|-------|-----------|
| `ALLOCATOR_MODE=off\|shadow\|live` | Shadow logs would-block / would-cap |
| Sleeve caps | S0≤80%, S1 reserved 15% as **cash hold** (S3), gross≤100%, name≤8%, cluster≤25% |
| DQ policy | Align with constitution: block new risk when DQ not `ok` (no auto-flatten) |
| SPY&lt;200 | S0 block new; S1 half-cap only when S1 exists (stub: reserve cash) |
| DD staircase | 12% soft / 18% hard new-risk / 20% floor review |
| Crash | SPY −7% / 10d trip; clear &gt;20 SMA + 3 sessions no new 10d low |
| Diagnostics | Allocator decision blob on scans / portfolio endpoints |

**Likely touch**

- New module e.g. `core/portfolio_allocator.py` (or extend [[guardrails]] carefully)
- `config.py`, `execution.py` guardrail wrapper hooks
- Dashboard diagnostics (optional, keep thin)
- Validators: `scripts/validate_allocator_constitution.py` (new)

**Exit criteria**

- [ ] Shadow: would-block reasons stable across ≥2 RTH/`ok` sessions
- [ ] Unit tests for crash trip/clear, DD staircase, cap math
- [ ] Live (after shadow): new risk respects caps/kills; **no** unexpected auto-flattens
- [ ] S0 live stack retention still in band ([[signal-quality-rollout]])

**Depends on:** Phase 0 (Phase 1 can overlap)  
**Est. effort:** medium

---

## Phase 3 — S0 weight expression + daily hysteresis

**Goal:** Speak the net-book language with a **single** live sleeve (S0) + S3 residual.

**Deliverables**

| Piece | Spec |
|-------|------|
| Desired-weight builder | Conviction selects top-5; vol-target within S0; scale to allowed S0 cap after allocator |
| Daily rebalance plan | Targets vs positions; hysteresis ~1% or membership change |
| Execution bridge | Translate Δw to orders through existing guardrails (no new order path inventiveness) |
| Paper report | Daily target vector logged for attribution |

**Likely touch**

- `core/portfolio_analytics.py` / new `core/sleeve_weights.py`
- `execution.py` or a thin rebalance job in webapp/main loop
- tests for vol-target + hysteresis

**Exit criteria**

- [ ] Deterministic fixture: same signals → same desired weights
- [ ] Hysteresis proven (no churn on &lt;1% noise)
- [ ] Shadow rebalance diffs reviewed for ≥1 week **or** 5 sessions before live sizing changes
- [ ] Name/cluster caps applied after weights

**Depends on:** Phase 2 (shadow allocator at minimum)  
**Est. effort:** medium

---

## Phase 4 — Attribution model D (skeleton)

**Goal:** Honest sleeve science before S1 capital.

**Deliverables**

| Piece | Use |
|-------|-----|
| Counterfactual S0 book | Mark-to-market as-if S0 solo under caps |
| Filled-share stub | When only S0 trades, filled-share ≈ counterfactual (sanity) |
| Cost split | Cost pool ∝ `|Δw|` |
| Artifact writer | Daily/session JSON for promotion reviews |

**Likely touch**

- Extend counterfactual logging / new `core/sleeve_attribution.py`
- Scripts to summarize sleeve PF/Sharpe/DD vs net book

**Exit criteria**

- [ ] Counterfactual S0 metrics reproducible from fixtures
- [ ] Cost attribution unit-tested
- [ ] Doc: how to read artifacts for an R7 promote decision

**Depends on:** Phase 3 (desired weights exist)  
**Est. effort:** medium

---

## Phase 5 — S1 PEAD paper sleeve + R4

**Goal:** Orthogonal edge under constitution without contaminating S0.

**Deliverables**

| Piece | Spec |
|-------|------|
| S1 signal path | PEAD-primary admission; **no** Stage2 1% buffer / pts_52w / rank-v2 defaults |
| S1 exits | PEAD-native grace profile from offline clears |
| Paper desired weights | Top-3, vol-target, cap 15% (paper only) |
| Idle budget | Shows as S3 in allocator shadow diagnostics |
| R4 scoring | PEAD magnitude × surprise × liquidity (shadow rank inside S1) |
| Fidelity monitor | Retention / would-trade diagnostics; DQ-aware session log |

**Likely touch**

- PEAD canary code paths (`docs/PEAD_CANARY_SLEEVE_DESIGN.md`, existing PEAD dual-admit scripts)
- Sleeve registry: S0 vs S1 outputs into weight builder (S1 paper)
- [[pead]] wiki update when behavior freezes

**Exit criteria**

- [ ] Offline S1 (or PEAD-primary peer) still clears constitution floors on its own replay
- [ ] Paper S1 runs ≥ fidelity gate (≥2 RTH/`ok` analogous sessions) with stable diagnostics
- [ ] Zero S0/S1 filter cross-wiring in Stage A
- [ ] R4 shadow metrics logged; no LIVE S1 capital yet

**Depends on:** Phase 1 (R7 tags), Phase 4 preferred (attribution ready)  
**Est. effort:** medium–large

---

## Phase 6 — S0 edge chips R3 → R2

**Goal:** Raise Sharpe/worst-era without starving retention; judged by R7 + Pareto soft-rank.

**6a — R3 Capacity-aware rank**

- Penalize low ADV / dollar volume in S0 ranking
- Sweep under pts_52w + p76 stack; soft-rank retention
- Shadow → fidelity → live only if floors + retention band hold

**6b — R2 Loser autopsy anti-features**

- Build vetoes only from losing cohorts (extension, thin float, earnings proximity, etc.)
- One veto family per experiment
- Same promotion path as R3

**Exit criteria (each chip)**

- [ ] Offline clears PF / worst-era / DD≤20%
- [ ] Soft-rank not worse on Sharpe **or** justified retention trade per constitution
- [ ] R7: N≥40 sleeve-local, expectancy &gt; 0 (or veto)
- [ ] Live fidelity sessions in band
- [ ] Promotion ledger entry

**Depends on:** Phase 1; ideally Phase 2 so live risk caps exist  
**Parallel OK with:** Phase 5  
**Est. effort:** medium each; do **not** stack 6a+6b in one promote

---

## Phase 7 — S1 LIVE canary + full net book C

**Goal:** Real multi-sleeve execution under model C.

**Deliverables**

| Piece | Spec |
|-------|------|
| Net book | Merge S0+S1 desired weights → one ticker ledger |
| Caps | S1 ≤15% live; idle → S3; name 8%; cluster 25% |
| Asymmetric regime | S1 half-cap when SPY&lt;200 unless crash (then 0 new risk) |
| Attribution D live | Counterfactual promote path + filled-share monitor both running |
| Canary size | Start at ≤15%; 25% only later explicit promotion |

**Exit criteria**

- [ ] Shadow netting reviewed (overlap cases, cancel/net math) on fixtures + paper week
- [ ] S1 LIVE fidelity gate pass under [[canary-rollout]] spirit
- [ ] Combined book DD staircase/crash never violated in canary window
- [ ] Sleeve attribution disagrees with “A-only mark” on at least one overlap fixture (proves D matters)
- [ ] Rollback drill: S1 → shadow in one env flip

**Depends on:** Phase 5 exit, Phase 3–4, Phase 2 live  
**Est. effort:** large

---

## Phase 8 — Later research (ordered)

Only after Phase 7 is stable (or explicitly parallelized with operator approval):

| Order | Chip | Notes |
|------:|------|-------|
| 1 | **R6** | Allocator vol damper (shrink caps before crash) — shadow first |
| 2 | **R1** | Path-dependent S0 entry state machine — needs bar/path data discipline |
| 3 | **R5** | New S5 RS sleeve — full sleeve contract, paper → canary |
| — | **S4** | After S5 proves attribution + non-redundant Sharpe |
| — | **S2** | Remains parked |

**Exit criteria:** same R7 + Pareto constitution per chip/sleeve.

---

## Milestone map (one glance)

```text
P0 Baseline
 └─ P1 R7 law ───────────────────────────────▶ Edge chips (P6, P8)
 └─ P2 Allocator S0+S3
      └─ P3 Weights + hysteresis
           └─ P4 Attribution D
                └─ P5 S1 paper + R4
                     └─ P7 S1 LIVE + net book C
                          └─ P8 R6 → R1 → S5 → (S4)
                └─ P6 R3 then R2 (∥ P5)
```

## Suggested kickoff order (first 30–60 days of calendar, not compute)

1. **Phase 0** (same day)
2. **Phase 1** (R7) — highest leverage
3. **Phase 2** shadow allocator
4. Start **Phase 6a R3** sweeps in parallel once R7 scoring works
5. **Phase 3** after allocator shadow looks sane
6. **Phase 4** then **Phase 5** S1 paper
7. Hold **Phase 7** until S1 paper fidelity + attribution exist

Exact calendar dates intentionally omitted — quality of exit criteria beats schedule theater.

## Completion reality (2026-07-28)

### Engineering done (this repo)

| Phase | Code status |
|------:|-------------|
| 0 | Baseline artifact: `schwab_skill/validation_artifacts/multi_sleeve_baseline_latest.json` + `scripts/freeze_multi_sleeve_baseline.py` |
| 1 | `sleeve_id` on ledger; `by_sleeve` summary; `core/r7_promotion.py`; `r7_sleeve_promotion_reasons` |
| 2 | `core/portfolio_allocator.py` + `ALLOCATOR_MODE` (default off); live block hook in `execution.py` |
| 3 | `core/sleeve_weights.py` (top-N, vol-target, hysteresis) |
| 4 | `core/sleeve_attribution.py` (model D) |
| 5 | PEAD canary block: `sleeve_id=S1`, R4 scores, `desired_weights_paper` |
| 6 | `core/sleeve_research_chips.py` (R3/R2/R4/R6/R1/R5 helpers); `R3_CAPACITY_SHADOW` flag |
| 7 | Net book + `MULTI_SLEEVE_S1_LIVE` path in weight builder (still default paper) |
| 8 | Research helpers + `scripts/validate_multi_sleeve_constitution.py` |

Validator: `python scripts/validate_multi_sleeve_constitution.py`  
Tests: `python -m pytest -q tests/test_multi_sleeve_constitution.py`

### Still required for true phase “exit criteria”

| Gate | Est. calendar |
|------|----------------|
| ALLOCATOR_MODE=shadow across ≥2 RTH/`ok` sessions, then live canary | **1–2 weeks** |
| R7 N≥40 sleeve-local scored rows (needs time + scoring) | **4–8 weeks** typical |
| R3/R2 offline multi-era sweeps + promote | **2–4 weeks** each |
| S1 paper fidelity then LIVE 15% canary | **3–6 weeks** |
| Full net-book live stability + Phase 8 chips | **+4–8 weeks** after S1 LIVE |

**Bottom line:** platform code for all phases is in-tree now; **operator/live completion of every exit box is ~8–16 weeks** of disciplined calendar time (not engineering hours alone).

## RTH evidence week (operator checklist)

Applied via:

```bash
cd schwab_skill
python scripts/apply_multi_sleeve_rth_shadow_env.py
python scripts/validate_multi_sleeve_rth_shadow_env.py
# optional restart with both stacks:
python scripts/start_local_dashboard.py --signal-stack-enforced --multi-sleeve-rth-shadow
```

| Day / action | Check |
|---|---|
| Each RTH scan with `data_quality=ok` | Allocator stays **shadow** (diagnostics / no live blocks from allocator policy alone unless you later promote) |
| After alerts fire | `.hypothesis_ledger.json` grows with `sleeve_id=S0` |
| End of day or weekly | `python scripts/score_hypothesis_outcomes.py` |
| After ≥2 ok RTH sessions | Review allocator would-* behavior; only then consider `ALLOCATOR_MODE=live` |
| Toward R7 | Watch sleeve-local N toward 40 at T+5 |

**Do not** set `MULTI_SLEEVE_S1_LIVE=true` or `ALLOCATOR_MODE=live` during this collection window.

## Offline allocator CF (do not wait on RTH)

Research path for Phases 2–4 while RTH shadow runs:

```bash
cd schwab_skill
# smoke
python scripts/analyze_allocator_overlay_counterfactual.py --demo
# real book + live-stack proxy (pts≤37 + rank_v2 p76; no breakout buffer in chunks)
python scripts/analyze_allocator_overlay_counterfactual.py --run-id control_legacy_aug --apply-live-stack-proxy
```

Artifacts: `validation_artifacts/allocator_overlay_cf_*.json`

**2026-07-28 first real pass (stack proxy, 2110 trades):** no arm cleared PF floors (baseline itself ~1.15 mean — missing exit-grace/breakout buffer replay). Combined `top5_then_dd_and_crash` was best mean (~1.21) but worst-era failed. Next offline refinement: overlay on `exit_grace_breakout_buffer` replay book (same as signal-stack CF), not raw chunks.

### Promoted-stack sweep (2026-07-28, iteration 2) — DONE

```bash
python scripts/analyze_allocator_overlay_counterfactual.py --promoted-stack --sweep
```

Book: exit grace 15/40 + 1% buffer + pts≤37 + rank p76 → **859** trades.

| Arm | PF mean | Worst-era | Retention | Floors |
|---|---:|---:|---:|---|
| **control (promoted stack)** | **1.323** | **1.034** | **100%** | **pass** |
| max-edge `top3_dd_c3` | 1.582 | 1.040 | 38.8% | pass |
| capacity `top5_crash_c5` | 1.440 | 1.067 | 57.2% | pass |

**Verdict:** Live S0 baseline stays **control promoted stack**. Allocator tighten is optional shadow research (`top5_crash_c5` / `top5_dd_c5`), not required to clear floors. Reject live `top3_dd_*` until retention is explicitly accepted.

PEAD S1 (exit-grace only, 27k trades): control **1.549 / 1.191** clears; capacity-aware pick is **control** (aggressive DD/top-N destroys retention).

Canonical summary: `validation_artifacts/multi_sleeve_offline_iteration_verdict.json`

## Explicit non-work (until later phases)

- Performance-tilted sleeve weights
- Auto-flatten on DQ
- Mixing Stage2 buffer into PEAD
- S2 fade sleeve
- Options / leverage
- Building S5 before S1 paper attribution is honest

## Related Pages

- [[multi-sleeve-trading-system-constitution]] — locked design
- [[signal-quality-rollout]] — current S0 live stack
- [[promotion-playbook]] — off/shadow/live
- [[hypothesis-ledger]] — R7 recording
- [[canary-rollout]] — live canary discipline
- [[pead]] — S1 thesis
- [[guardrails]] — current risk limits
- [[validation]] — validate_all profiles
- [[backtest]] — multi-era replay
- [[governance-source-of-truth]] — precedence

---

*Last compiled: 2026-07-28*
