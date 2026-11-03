---
source: schwab_skill/scripts/analyze_scan_confidence.py
created: 2026-09-21
updated: 2026-09-26
tags: [strategy, ranking, research, shadow]
---

# Scan Confidence

> Shadow survival readout on the scan table, and the evidence that the live
> ranking score is pointed the wrong way.

## What it answers

Not "how good does this setup look?" but "does this setup look like the trades
that ran 21-40 days, or the ones that were trailed out inside 20 days?" That
cohort split is where the realized book's P&L comes from (see
[[signal-ranking]] and the Phase 2 edge audit).

`setup_confidence` is a 0-100 score attached in Stage B. It is **display only**
— it never sorts, filters, or drops. Safety stays caps-only via
`apply_rank_v2_risk_caps`, matching [[analog-overlay]] and rank v2.

## Headline findings

From `scripts/analyze_scan_confidence.py` on the frozen `control_legacy_aug`
book (16,433 trades, five eras, walk-forward leave-one-era-out). Full writeup:
`validation_artifacts/scan_confidence/report.md`.

**The live ranking is inverted, not merely weak.** At matched ~24% retention:

| Arm | PF mean | Worst era | PF 95% CI |
|---|---|---|---|
| `current_live_p76_top5` (incumbent) | 1.0276 | 0.9368 | 0.944–1.138 |
| `analog_top5` (Stage 2i walk-forward) | 1.1618 | 1.0072 | — |
| baseline, every trade | 1.1637 | 1.0322 | 1.113–1.213 |
| `inv_signal_score_p76` (live score **reversed**) | 1.3897 | 1.0931 | 1.201–1.398 |
| `inv_pts52w_p76` (single known variable) | 1.4212 | 1.0881 | 1.212–1.425 |
| `confidence_p76` | 1.4296 | 1.1212 | 1.315–1.556 |

Taking every trade beats the live selection. Sorting by *minus* `signal_score`
beats it by more.

**Extension drives early stop-outs, monotonically.** By quintile of
`close_vs_sma200_pct` at entry (least to most extended), the share of trades
closed inside 20 days at a loss runs 16.9% → 18.3% → 21.3% → 24.5% → 28.6%.

**Every chart/trend variable has negative IC against realized return**, in all
five eras: `sma_20_slope` −0.0541, `sma_200_slope` −0.0540,
`trend_strength_score` −0.0513, `weinstein_score` −0.0475,
`close_vs_sma200_pct` −0.0466.

## The nuance against the historical blend

The historical blend ([[signal-ranking]] Stage 2h) found `close_vs_sma200_pct`
was the one feature with **positive** OOS IC (+0.044) against `ret_40d_fwd`.
Here the same variable scores **−0.0466** against realized `net_return`. Both
are correct:

| Measured against | IC | Population |
|---|---|---|
| `ret_40d_fwd` | +0.0440 | full 66k candidate panel |
| `ret_40d_fwd` | −0.0005 | entered trades only |
| `net_return` | −0.0466 | entered trades only |

Conditioning on a confirmed breakout narrows the sample, and the stop/trail
policy sits between price drift and the account. A name can drift up on average
and still lose money for this strategy because it takes a deeper drawdown first
— early stop-outs average roughly −9.2% worst excursion versus −2.6% for the
21-40 day winners.

**A feature is only predictive relative to an exit policy.** Ranking studies
fitted on raw forward returns do not transfer to a trailed book.

## Same variables on the other books

`scripts/compare_confidence_across_books.py` rescores the columns that exist
on every augmented trade record. Full table:
`validation_artifacts/scan_confidence/cross_book.md`.

IC versus realized `net_return` (eras sharing the majority sign):

| Feature | Stage 2 control | 52w-capped Stage 2 | Pullback (4 eras on disk) | PEAD primary |
|---|---|---|---|---|
| `signal_score` | −0.0262 (5/5) | −0.0189 (4/5) | −0.0286 (3/4) | −0.0332 (4/5) |
| `close_vs_sma200_pct` | −0.0466 (5/5) | −0.0362 (5/5) | −0.0263 (3/4) | −0.0374 (5/5) |
| `pts_52w` | −0.0235 (5/5) | −0.0107 (4/5) | −0.0133 (2/4) | −0.0468 (5/5) |
| `edge_score` | −0.0143 (4/5) | −0.0141 (4/5) | −0.0500 (3/4) | +0.0289 (3/5) |

What changes by entry family:

- The live score stays negative on PEAD and pullback. That part is not Stage 2-only.
- Keeping the least-extended quarter by SMA200 distance **fails worst-era on Stage 2** (control worst **0.738**) and **lowers pullback PF** versus taking every pullback (1.242 vs 1.274). On PEAD the same cut raises both the mean and the worst era (2.041 / 1.305). A high average is not a promotion, and it is not one rule for every book.
- `pts_52w` is stronger on PEAD than on Stage 2, and nearly washed out once the cap is already inside the Stage 2 book. On pullback the sign does not hold, matching the earlier no-op cap result.
- `edge_score` and volume points are the strongest negatives on pullback and mildly positive on PEAD (3/5 eras). Do not copy a volume rank across entry families.
- After the 52-week cap is already in the Stage 2 book, live p76 + top-5 no longer sits below the baseline (1.245 / 1.110 vs 1.214 / 1.105). The "ranking destroys the book" result is the uncapped Stage 2 book and PEAD, where the same stack gives back baseline PF (PEAD 1.550 down to 1.328).

Pullback `recent_current` chunks are missing, so this file's pullback worst era is not the catalog five-era 0.959. PEAD surprise was not stored on those chunks.

## Follow-up measurements

`scripts/run_open_research.py` wrote
`validation_artifacts/open_research_sort_exit.json` on 2026-09-23.
`SCAN_LIVE_SORT_KEY` was not changed. The earlier censored path replay
(legacy PF 0.622, grace-all 1.164) is retired: those paths ended on the
original exit and could not test a longer hold.

- **Walk-forward less-extended sort.** On `control_legacy_aug` (16,433 trades), fitting the p76 cutoff of inverted `pts_52w` on the other eras keeps 3,939 trades at PF **1.3862 / worst 1.0298**. That clears both floors and beats taking every trade (1.1637 / 1.0322) and the live top-5 by `signal_score` (1.1550 / 1.0214). The same day's top-5 by inverted `pts_52w` is **1.1911 / 1.0227** and misses the 1.20 mean. On the capped book, taking every trade already clears (1.2137 / 1.1054); the walk-forward percentile is 1.3251 / 1.2073, and the day's top-5 is 1.2131 / 1.1212. Verdict: `shadow_ready_sort_key_not_promoted`. The percentile filter is the result that clears. It is not a change to the live sort key.
- **Exit replay with bars after the stored exit.** 16,250 of 16,433 control trades (98.9%) have a cached bar after the original exit, so the replay is interpretable. Stored returns on that subset are 1.1657 / 1.0297. Replaying `exit_grace_t15_h40` is **1.3240 / 1.1431**. Almost none of those exits are the 40-day clock (1 time exit); 10,923 are VCP invalidation, 4,645 trailing stop, 681 SMA50 break. A wider stop (`min(0.12, stop/0.9)`) on the walk-forward most-extended quintile only is 1.2008 / **0.8997** (n=3,370) and misses the worst-era floor. Do not widen stops on extended names from this run, and do not flip an exit mode from the grace number alone.
- **PEAD surprise.** The extension drop is now a shadow stamp, not a removal. `PEAD_EXTENSION_DROP_MODE=shadow` marks `entry_family=pead_primary` when `close_vs_sma200_pct` is at least **0.2028** (the full-book 80th percentile). That frozen cut keeps 21,937 trades at 1.5945 / 1.2077, next to the walk-forward drop's 21,781 at 1.5989 / 1.2046. Stage 2 and `both` are not stamped. `live` is refused. After warming 957 missing tickers, 20-day as-of surprise coverage is **97.7%** (was 11.9%, then 36.9% before the warm). Spearman IC versus net return is **+0.0379**, same sign in 4 of 5 eras. Surprise stays off the rank.
- **`distribution_score` p76.** Unchanged from the prior screen: IC +0.0377 in 5/5 eras, top quartile PF 1.3225 / worst **0.9571**. Inverting `pts_52w` on the same book is 1.4212 / 1.0881. Do not attach it.

## Fundamentals and insider data: tested, not signal

Tested with real point-in-time Sharadar data via
[[pit-research-platform]] conventions — 56,205 TTM filings across 1,460 of
1,468 tickers and 291,280 ticker-days of Form 4 filings, joined on **filing
date strictly before entry** (minimum staleness 1 day, median ~45).

- **Fundamentals**: ROE, P/E, debt, margins, book value all sit within ~0.02 of
  zero IC with signs that flip between eras. The only stable group is free cash
  flow (`fund_fcf`, `fund_fcfps`, `fund_fcf_yield`), same sign in 5/5 eras but
  weak; a fundamentals-only blend reaches PF 1.3687 / worst 1.0634.
- **Insider Form 4**: nothing. Every measure within ~0.01 of zero, and no
  insider variable held a consistent sign in *any* training fold, so the
  insider-only blend produced no score at all. Net insider buying is slightly
  negative if anything.

Build the panel with `python scripts/build_pit_fundamental_panel.py`
(`research/pit_fundamentals.py` does the fetch and as-of joins).

## Feature flag

```env
CONFIDENCE_RANK_MODE=shadow      # off | shadow | live; live is NOT approved
# CONFIDENCE_RANK_ARTIFACT=      # optional override path
EXTENSION_SORT_MODE=shadow       # off | shadow | live; live is refused
```

Artifact: `research_store/models/scan_confidence.json`
(`core/scoring_confidence.py` loads it; fail-closed when missing or when the
schema/feature names look like labels).

Diagnostics stamped per scan: `confidence_rank_mode`, `confidence_rank_scored`,
`confidence_rank_unscored`, `confidence_rank_model_id`, plus
`extension_sort_mode`, `extension_sort_scored`, `extension_sort_basis`
(`inv_pts_52w`). The extension stamp does not change `sort_score` or
`SCAN_LIVE_SORT_KEY`.

## Operator display

No new column. The Rank cell leads with the trust score, 0–100, where higher
means less extended. `52w NN` sits underneath as the raw extension points.
File confidence (`equity_confidence`, registry `equity_ledger_v1`) sits in the
why tooltip and the detail note. It is coverage × agreement × freshness across
the frozen factor ledger. Only inverted 52-week extension moves the Rank.
Chart stretch and strength are the same story. Volume, classic fundamentals,
and insider filings are measured with weight zero. Free cash flow and PEAD
surprise stay eligible and do not add Rank points. Reliability, execution,
and forensic flags remain caps. Missing filings lower coverage and stay missing.
The why tooltip carries the published evidence: on `control_legacy_aug` a p76
cut of inverted `pts_52w` is PF 1.4212 / worst era 1.0881, and the day's top 5
(PF 1.1911 / worst 1.0227) does not reorder the live book. Safety caps
(reliability, execution, forensic) lower the number after the map and are
named in the tooltip, along with survival confidence when that shadow
readout is present. Fundamentals stay in the file-confidence ledger and out of
the Rank. Older payloads
without `trust_score` still show `52w NN`. The scan table stays at nine
columns. A family filter (All / Stage 2 / PEAD / Pullback) sits on the
qualified table so one book's rule is not read as the other's.

## Go / No-Go

**Verdict 2026-09-21: `keep_shadow_single_variable_explains_it`.**

`confidence_p76` clears both promotion floors (PF mean ≥ 1.20, worst era ≥
1.00) and beats the live stack decisively. It does **not** beat simply
inverting `pts_52w` by a meaningful margin — +0.0084 PF against a +0.03 bar,
with overlapping confidence intervals.

- Do **not** set `CONFIDENCE_RANK_MODE=live`, `EXTENSION_SORT_MODE=live`, or `PEAD_EXTENSION_DROP_MODE=live`, and do not point `SCAN_LIVE_SORT_KEY` at `setup_confidence`. `EXTENSION_SORT_MODE=shadow` only stamps inverted `pts_52w` for the Rank cell. The PEAD extension stamp applies only to `pead_primary`.
- Keep and trust `pts_52w <= 37`; it does most of the work the blend claims.
- The open question this raises is whether `signal_score` should keep its job
  as the live sort key. The 2026-09-23 walk-forward says the less-extended
  **percentile** clears the floors and the **day's top-5** does not, so
  `SCAN_LIVE_SORT_KEY` stays `signal_score`.
- Do not promote `distribution_score`. Do not widen stops on extended names
  (worst era 0.8997). The grace replay is now measurable and stronger than
  the stored exit, and it is not an exit-mode change by itself. PEAD surprise
  is scored at 97.7% coverage with IC +0.0379; leave it off the rank.
- A purged ridge on `net_return` (Stage 2l) loses to inverted `pts_52w`
  (1.2348 / 0.9589 vs 1.3862 / 1.0298). Do not promote it. See [[purged-net-rank]].

## Related Pages

- [[signal-ranking]] — the score stack this measures
- [[analog-overlay]] — sibling shadow readout, neighborhood PF
- [[pit-research-platform]] — point-in-time discipline and leakage rules
- [[feature-flags]] — mode conventions
- [[stage-2-analysis]] — the trend qualification these variables come from
- [[purged-net-rank]] — embargoed model on the same book; not promoted
- [[vcp-invalidation-exit]] — grace replay that skips only the VCP exit

---

*Last compiled: 2026-09-26*
