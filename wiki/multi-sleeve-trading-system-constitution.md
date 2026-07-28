---
source: n/a (grilled design session 2026-07-27)
created: 2026-07-27
updated: 2026-07-27
tags: [architecture, design, portfolio, multi-sleeve, promotion, p0]
---

# Multi-Sleeve Trading System Constitution

> Locked target architecture for an all-encompassing long-only swing system:
> specialist sleeves under one risk constitution, scored on a multi-objective
> Pareto — not PF-alone filter stacking.

This page is the **design constitution** from a 2026-07-27 grilling session.
It is **not yet fully implemented**. Executable rollout for the live Stage2
stack remains [[signal-quality-rollout]]; PEAD sleeve details remain [[pead]]
and `schwab_skill/docs/PEAD_CANARY_SLEEVE_DESIGN.md`. Phased implementation:
[[multi-sleeve-phased-build-plan]].

## North star

Improve **risk-adjusted portfolio outcomes** under explicit floors and a soft
ranking order. Stage2 scan PF remains necessary; it is not sufficient.

Plugins and new filters must declare one of:

- **S0 policy** (Stage2 sleeve brain)
- **S1 policy** (PEAD sleeve brain)
- **New research sleeve**
- **Allocator-only**

Silent cross-contamination (e.g. Stage2 breakout buffer applied to PEAD) is
forbidden.

## Objective contract (Pareto)

### Hard floors (must clear)

| Metric | Floor |
|--------|------:|
| PF mean (equal-weight eras) | ≥ 1.20 |
| Worst-era PF | ≥ 1.00 |
| Max drawdown | ≤ 20% |
| Thin-era veto | min trades/era (enforce in sweeps; do not promote on starved eras) |

### Soft rank (among floor-clearers)

1. **Sharpe** (higher better)
2. **Retention** / deployable capacity (prefer not starving the book)
3. **Expectancy**

**Out of soft-rank:** raw hit rate (lies under asymmetric R:R).

### Evaluation friction

- Offline promotion never waives floors for a hot short live window.
- Costs attributed by sleeve `|Δw|` so high-churn sleeves cannot steal Sharpe
  from quiet ones (see Attribution).

## Architecture overview

```text
Capital Allocator (regime / DD / correlation / cash)     ← hybrid model E
        │
        ├─ S0 Stage2 / VCP stack     → selection, entry, exit, sizing
        ├─ S1 PEAD-primary (canary)  → own selection, entry, exit, sizing
        ├─ S3 Cash / regime weight   → residual + forced risk-off
        └─ Future: S5 RS, S4 event   → same sleeve contract, shadow first

              ↓ sleeve desired-weight vectors

        Net exposure book (model C)  → one real ticker ledger
              ↓
        Broker execution (daily + hysteresis)
```

“All encompassing” means: selection, timing, exits, sizing, and regime live
as **policies on this skeleton**, not as one omniscient mega-filter.

## Universal sleeve contract

Every funded sleeve must satisfy:

| Clause | Rule |
|--------|------|
| Identity | Distinct entry thesis + exit policy |
| Offline floors | Same Pareto floors on multi-era replay before shadow capital |
| Mode ladder | OFF → SHADOW → LIVE only; never skip shadow |
| Live fidelity | Phase-1 style: qualifying RTH / `data_quality=ok` sessions in retention band before LIVE |
| Capital | Max weight + max positions + correlated cluster caps at LIVE |
| Kill switch | Sleeve-local and global (DQ, regime, DD, crash) |
| Research vs exec | Advisory plugins never silently become entry gates |
| Overlap | Handled by **net book**, not by blocking signals |

Promotion process detail: [[promotion-playbook]] + R7 below; hypothesis
calibration: [[hypothesis-ledger]].

## Target sleeve roster

| ID | Sleeve | Tier | Thesis |
|----|--------|------|--------|
| **S0** | Stage2 / VCP live stack | **Core** | Trend breakout continuation |
| **S1** | PEAD-primary | **Canary → LIVE** | Post-earnings drift; PEAD-native exits |
| **S3** | Cash / regime | **Core** | Explicit uninvested weight; kill target |
| **S5** | Sector / relative strength | **Research (next)** | Names from top-quintile sectors vs SPY |
| **S4** | Event / catalyst | **Research (after S5)** | Short-hold SEC / guidance shocks |
| **S6** | Prob-rank / better scoring | **Not a sleeve** | Brain upgrade **inside S0** ([[probabilistic-ranking-research-architecture]]) |
| **S2** | Failed-breakout fade | **Parked** | Do not fund until S0/S1 attribution is clean |

## Capital caps (hard)

| Sleeve / rule | Cap |
|---------------|-----|
| S0 max desired gross | **80%** |
| S1 max | **15%** until a thick live era clears floors; then up to **25%** only by explicit promotion |
| Idle S1 budget | Stays in **S3** (cash) — does **not** auto-fill S0 |
| Combined gross | **100%** (no leverage v1) |
| Single name (after netting) | **8%** |
| Correlated cluster (sector/factor) | **25%** |

## Allocator model E (hybrid)

- **Fixed caps** as above (no trailing-PF / Sharpe weight chasing until ledgers are thick).
- **Regime / DQ / DD / crash** may cut sleeve weight or all new risk to **zero**.
- Performance-tilted allocation is **out** for v1–v2.

### Kill / regime constitution

| Trigger | Action |
|---------|--------|
| Global `data_quality` not `ok` | **Block new risk**; do **not** auto-flatten |
| SPY &lt; 200 SMA | **S0** new entries blocked; **S1** may remain at **half cap** until crash |
| Book DD from peak | Soft cut new risk at **12%**; hard block new risk at **18%**; review at **20%** floor |
| Sleeve live breach (PF/era/DD) | That sleeve weight → **0** until re-promote |
| Calendar (FOMC / op-ex) | **No** v1 gates |

### Crash mode

| Spec | Rule |
|------|------|
| Trip | SPY **−7%** peak-to-trough within **10** trading days |
| Vol confirm | Optional only; **not required** for v1 |
| On trip | Block **all** new risk (S0 and S1); S3 absorbs; existing positions follow **normal sleeve exits** (no panic MOC flatten) |
| Clear | SPY close back **above 20 SMA** **and** **3** sessions without a new 10d low |

## Net exposure book (model C)

- Sleeves submit **desired weight vectors**.
- The **combined net book** is what trades.
- Sleeve P&amp;L for science is **synthetic** (see Attribution).
- Overlap is allowed at the signal layer; netting produces one ticker target.

### Desired weights (per sleeve)

| Rule | Spec |
|------|------|
| Who enters | Conviction / score selects membership |
| How much | **Vol-target** within sleeve, then scale to sleeve cap |
| S0 cardinality | Top **5** |
| S1 cardinality | Top **3** |
| After net | Apply name 8% and cluster 25% caps |

### Rebalance cadence

- **Daily** rebuild from that day’s sleeve outputs.
- **Hysteresis:** trade only if absolute weight change ≳ **1%** or membership enters/exits top-N.
- No every-scan thrash.

## Alpha constitution (v1 content)

| Sleeve | Entry | Exit | Non-goals |
|--------|-------|------|-----------|
| **S0** | Live stack: Stage2/VCP → 1% breakout buffer → rank-v2 p76 → pts_52w≤37; sort `signal_score` until separately justified | Exit grace **15** / hold **40** | Do not bolt PEAD or hard volume confluence into Stage A |
| **S1** | PEAD-primary admission (canary / dual-admit design) | PEAD-native exit-grace profile that cleared PEAD offline | Do **not** transfer Stage2 1% buffer, pts_52w, or rank-v2 as default PEAD policy unless a PEAD-only sweep clears floors |
| **S3** | Residual + forced cash from caps/kills | N/A | Not a scan signal |

Operating stack detail: [[signal-quality-rollout]], `schwab_skill/SIGNAL_QUALITY_ROLLOUT.md`.

## Attribution (model D)

| Path | Use |
|------|-----|
| **Counterfactual sleeve books** | Promotion science — each sleeve trades as-if solo under caps |
| **Filled-share on net book** | Live monitoring — allocate fills proportional to pre-net desired weight |
| **Weight-path mark alone** | Insufficient for promotion when sleeves overlap |
| **Costs** | Shared cost pool ∝ sleeve `|Δw|` |

## Market posture

- **Long-only** swing equities (multi-day holds).
- **No leverage** (100% gross).
- **No options** in v1.
- S0/S1: equities; S3: cash or cash-like ETF later.
- Scan universe **stable** until capacity research (R3) says otherwise.
- **Tax ignored** in offline Pareto v1.

Shorts / failed-breakout fade (S2) only as a future researched sleeve — never
sneaked into S0.

## Build path (thin vertical slice C)

See [[multi-sleeve-phased-build-plan]] for milestones, exit criteria, and
validation commands.

1. **Now:** Live **S0 + S3** under allocator caps, kills, DD staircase, and crash mode.
2. **Parallel:** **S1** in paper / counterfactual until canary fidelity passes.
3. **Research:** Wave 1 unblocked on current stack (do not wait for perfect plumbing).
4. **Then:** Full net-book C when S1 earns LIVE capital.

Do **not** choose pure platform-first (year of plumbing, no edge learning) or
pure edge-first with no DD constitution on real capital.

## Research wave 1 (ordered)

| Order | ID | Idea | Bucket |
|------:|----|------|--------|
| 1 | **R7** | Hypothesis-ledger closed-loop promotion process | S0 process |
| 2 | **R3** | Capacity-aware rank (ADV / dollar-volume penalty) | S0 |
| 3 | **R2** | Loser autopsy → anti-feature vetoes | S0 |
| 4 | **R4** | PEAD magnitude × surprise × liquidity scoring | S1 |
| 5 | **R6** | Vol damper on allocator caps before crash trips | Allocator |
| 6 | **R1** | Path-dependent entry state machine (setup → pierce → hold → arm) | S0 |
| 7 | **R5** | Sector RS sleeve | New sleeve (S5) |

**Out of wave 1:** R8 / S2 failed-breakout fade.

## R7 promotion process contract

| Gate | Rule |
|------|------|
| Record | Every S0 alert / sleeve desired-weight change → [[hypothesis-ledger]] row |
| Score | T+1 / T+5 / T+20 direction + excess vs SPY; trade expectancy when round-trip exists |
| Primary horizon | **T+5** |
| Min N | ≥ **40** scored rows **sleeve-local** |
| Ledger quality | Expectancy &gt; 0; do not promote on hit rate alone (optional: T+5 excess hit ≥ 52% *or* clear expectancy with CI) |
| Offline | Multi-era must still clear PF / worst-era / DD floors + soft-rank |
| Live fidelity | ≥ 2 RTH / `ok` sessions in retention band before LIVE (Phase-1 pattern) |
| One change | Single policy delta per experiment |
| Veto | Offline clears but ledger expectancy ≤ 0 at min N → **no promote** |

Aligns with and extends [[promotion-playbook]] and [[hypothesis-ledger]].

## Relationship to current north star

Phase-2 style guidance (`halt_fix_signal_first`) still applies to **plugin
LIVE proliferation** on top of an unfinished base book. This constitution
**does not** waive bare-signal floors. It reframes “best system” as:

1. Keep S0 clearing floors with capacity-aware research (R3/R2/R1/R7).
2. Add **orthogonal** sleeve edge (S1, later S5/S4) under caps.
3. Let the **allocator** own portfolio DD/Sharpe instead of endless Stage A gates.

## Non-goals (explicit)

- Maximizing PF mean by crushing retention below deployable capacity.
- Performance-chasing sleeve weights on thin samples.
- One scanner that mixes Stage2 and PEAD admission logic.
- Auto-flattening the book on DQ blips.
- Daytrading / options / leverage in v1.
- Treating hit rate as a promotion objective.

## Implementation status

| Piece | Status |
|-------|--------|
| S0 live stack (buffer, exit grace, rank-v2 p76, pts_52w) | **Live** — see [[signal-quality-rollout]] |
| Pareto floors in research/promotion scripts | **Partial** (PF floors exist; DD≤20% soft-rank needs sweep tooling) |
| Allocator E + crash + DD staircase | **Code in** (`core/portfolio_allocator.py`, `ALLOCATOR_MODE` default **off**) |
| Net exposure book C | **Code in** (`core/sleeve_weights.py`); S1 live gated by `MULTI_SLEEVE_S1_LIVE` |
| Attribution D | **Code in** (`core/sleeve_attribution.py`) |
| S1 canary under this constitution | **Paper weights** on canary block; not LIVE capital |
| R7 as written | **Code in** (`core/r7_promotion.py` + ledger `sleeve_id`); needs live N≥40 |
| Phased build | [[multi-sleeve-phased-build-plan]] |

## Related Pages

- [[system-overview]] — current end-to-end pipeline
- [[signal-scanner]] — Stage A/B scan (S0 engine)
- [[signal-quality-rollout]] — live S0 stack rollout
- [[signal-ranking]] — composite / rank selection
- [[probabilistic-ranking-research-architecture]] — S0 brain upgrade (not a sleeve)
- [[pead]] — PEAD enrichment / S1 thesis root
- [[guardrails]] — risk limits (ancestor of allocator caps)
- [[hypothesis-ledger]] — R7 recording/scoring
- [[promotion-playbook]] — off → shadow → live
- [[plugin-modes]] — mode ladder discipline
- [[backtest]] — multi-era replay expectations
- [[canary-rollout]] — controlled live testing
- [[multi-sleeve-phased-build-plan]] — phased implementation roadmap
- [[governance-source-of-truth]] — precedence when docs conflict

---

*Last compiled: 2026-07-27*
*Grilling lock: shared understanding confirmed 2026-07-27 — no implementation implied by this page alone.*
