---
source: schwab_skill/docs/PROBABILISTIC_RANKING_RESEARCH_ARCHITECTURE.md
created: 2026-07-17
updated: 2026-07-18
tags: [research, ranking, feature-store, ml, backtest, promotion, decision-lock]
---

# Probabilistic Ranking Research Architecture

> Design for evolving TradingBot from rule-based gating into a probabilistic
> ranking research platform. Canonical full text:
> `schwab_skill/docs/PROBABILISTIC_RANKING_RESEARCH_ARCHITECTURE.md`.
> Phases B–F implemented. **Go/no-go LOCKED 2026-07-18: KEEP SHADOW.**

## Locked verdict (2026-07-18)

**KEEP SHADOW** — not LIVE, not KILL. Do not re-litigate from secondary metrics.

| Contract | Result |
|---|---|
| Primary: top-5/day vs rank-v2 (954 cohorts, purged OOS) | Prob PF mean **1.207** / worst **1.026** vs v2 **1.204** / **1.024**; recent_current **1.061** vs **1.138**; attribution **near_tie** |
| Secondary: p75 CF `2c9efe271d` | Prob **1.411 / 1.068** vs v2 **1.062 / 0.926** → `promote_shadow` only (context) |
| Live ledger | 42 rows (2 live + 40 CF seeds); not broken; not a LIVE gate |
| LIVE floors | Unchanged: PF mean ≥ 1.20, worst-era ≥ 1.00 |

**Will:** keep `PROB_RANK_MODE=shadow` + `lgbm_ret_40d_fwd_2c9efe271d`.  
**Won't:** enable live; new model families; weaken floors; re-argue from p75 alone.  
**Next (one):** accumulate live (non-CF) Stage-B shadow ledger rows; reopen LIVE only on decisive top-5/day edge.

Artifact: `validation_artifacts/prob_rank_shadow_evidence/go_nogo_verdict_2026-07-18.json`

## Why (research platform)

Evidence from [[backtest]] / `BACKTEST_CATALOG.md` (2026-07-17): ranking already
separates quality; the platform learns continuous relationships instead of
adding hard gates. See [[signal-ranking]] for rank-v2 control.

## Locked design decisions

1. Stage 2 still generates candidates; ML **ranks only** (never invents buys).
2. **Research Parquet** warehouse (`research_store/`) + **ops SQL** [[feature-store]] — same feature names.
3. Primary model: LightGBM on `ret_40d_fwd`; advisory logistic is baseline/feature only.
4. Research selection: daily cross-sectional top-N; `RANK_FILTER_V2` p75 is the control.
5. `PROB_RANK_MODE` = off → shadow → live (never skip shadow).
6. PF mean ≥ 1.20 / worst-era ≥ 1.00 remain hard floors for LIVE.

## Migration status

| Phase | Status |
|---|---|
| B Feature platform | Done |
| C Dataset + LightGBM | Done |
| D Shadow `PROB_RANK_MODE` | Done — local shadow + `2c9efe271d` |
| E Portfolio + promotion | Done |
| F Ops orchestration | Done |
| Go/no-go | **KEEP SHADOW** (2026-07-18) |

## Related Pages

- [[backtest]] — Live-parity harness used for dual-run evaluation
- [[feature-store]] — Ops SQL store vs research Parquet warehouse
- [[signal-ranking]] — Legacy composite / rank-v2 control
- [[advisory-model]] — Baseline logistic; not the production ranker
- [[signal-quality-rollout]] — Live stack including rank-v2 p75
- [[promotion-playbook]] — Hard PF floors and promotion process
- [[feature-flags]] — `PROB_RANK_*` env knobs
- [[sector-strength]] — Regime / sector context features

---

*Last compiled: 2026-07-18 (KEEP SHADOW locked)*
