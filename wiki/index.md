---
source: n/a (wiki catalog)
created: 2026-04-30
updated: 2026-10-29
tags: [wiki, index, catalog]
---

# Wiki Index

> Central catalog of all knowledge compiled into this wiki.
> Every page in the wiki should be reachable from this index.

## Project Overview

- [[project-overview]] — High-level summary of the TradingBot project
- [[governance-source-of-truth]] — precedence model for rules, skills, and wiki docs
- [[agent-session-handoff]] — Unreviewed session notes and the read-only signal-edge review

## Architecture & Design

- [[system-overview]] — End-to-end pipeline diagram and component map
- [[multi-sleeve-trading-system-constitution]] — Locked multi-sleeve target architecture (Pareto, allocator, net book, research wave)
- [[multi-sleeve-phased-build-plan]] — Phased build roadmap (thin slice → S1 LIVE → research wave)
- [[schwab-auth]] — Dual OAuth2 sessions (market + account)
- [[signal-scanner]] — Two-stage scan pipeline (Stage A + Stage B)
- [[execution-engine]] — Order placement, guardrails, plugin hooks
- [[guardrails]] — Risk limits, circuit breaker, sector caps
- [[advisory-model]] — Calibrated P(up in 10 days) probability scoring
- [[agent-intelligence]] — Dynamic weighting, meta-policy, uncertainty throttling
- [[grok-agent-orchestration]] — xAI Grok specialist inside the hybrid handoff (default off)
- [[autonomous-investment-network]] — Feasibility of a continuous research network on the existing card and promotion path
- [[autonomous-investment-network-build-plan]] — Phased build: reject-rate report, then a latch so finished cards cannot requeue
- [[meta-policy]] — Final-stage emit / suppress / downweight combiner
- [[mirofish-engine]] — Multi-persona simulation backbone
- [[feature-store]] — Ops SQL feature logging + research Parquet warehouse split
- [[pit-research-platform]] — PIT universe/fundamentals, triple-barrier labels, CPCV, factor analytics, warehouse
- [[probabilistic-ranking-research-architecture]] — Prob-rank research platform; **KEEP SHADOW** locked 2026-07-18
- [[prediction-market]] — Polymarket overlay and provider abstraction
- [[event-probability-layer]] — Event probabilities → scenario DCF, sensitivities, EMS (research only)
- [[companyfacts-pricing]] — SEC XBRL companyfacts warehouse + Research pricing workbench
- [[sharadar-bundle]] — Sharadar Bundle PIT snapshot, dossier overlay, compare
- [[database-schema]] — SQLAlchemy tables and relationships
- [[discord-integration]] — Webhook alerts and notification types
- [[webapp-dashboard]] — FastAPI local dashboard
- [[portfolio-book]] — Portfolio Book: P/L calendar, tax estimate, journal
- [[saas-api]] — Multi-tenant production API
- [[static-module-layout]] — Frontend ES module map (`/static/modules/*.js`)

## Frontend Redesign

- [[ux-kpi-baseline]] — Locked UX KPIs and instrumentation event taxonomy
- [[frontend-route-contract]] — Frozen screen map, hash, and `?section=` alias contract
- [[section-migration-map]] — Section-to-screen migration map and rollout checkpoints
- [[usability-test-script]] — Moderated 5-task usability script for 3-5 users

## Trading Strategies

- [[scan-catalog]] — Timeframe grouping, strategy descriptions, and named scan universes
- [[horizon-strategies]] — Shadow/research evaluators per timeframe (resampled weekly/monthly, daily Donchian/NR7, session-structure intraday)
- [[stage-2-analysis]] — Weinstein Stage 2 trend qualification
- [[vcp-detection]] — Volume Contraction Pattern identification
- [[sector-strength]] — Relative sector performance filter
- [[signal-ranking]] — Composite scoring and top-N selection
- [[analog-overlay]] — Shadow winner-neighborhood PF on the scan table
- [[scan-confidence]] — Shadow survival readout; what actually predicts a winning trade
- [[vcp-invalidation-exit]] — Grace replay that skips only the VCP exit; not a live change
- [[purged-net-rank]] — Embargoed ridge on net return; loses to inverted pts_52w
- [[bt-2026-10-20-entry-bar-close-location]] - Entry-bar CLV >= 0.5 filter; killed (no OOS lift)
- [[bt-2026-10-20-crowded-breakout-days]] - Skip days with more than 12 breakouts; killed in round 2 (next-open + PIT: not significant, not robust)
- [[bt-2026-10-20-day5-failure-exit]] - Exit underwater trades at day 5; killed (PF worse in every era)
- [[bt-2026-10-20-next-open-gap-skip]] - Skip next-open gap-ups over 2%; killed (no effect)
- [[bt-2026-10-20-wide-stop-skip]] - Skip stops wider than IS p80; killed (OOS harm, significant on PIT S&P 500)
- [[bt-2026-10-20-spy-200d-regime-filter]] - SPY>200d entry filter (+cash-in-SPY, SPY timing); killed, nothing beats SPY buy-and-hold OOS
- [[bt-2026-10-20-exits-holding-period]] - Time/ATR/SMA50 exits on fixed entries; nothing beats SPY OOS; entries lag SPY at 5-120d; ATR5 inconclusive
- [[bt-2026-10-21-weekly-swing-stretch]] - weekly_swing replay on ALL, BIIB and universe by 50d stretch; no edge vs SPY, stretch effect not reliable
- [[bt-2026-10-22-weekly-swing-six-names-breadth]] - UNP/SXT/WELL/SAFT/ITGR/SLAB relative history; <1%-of-50d and 200+ breadth not edges
- [[bt-2026-10-22-trend-breakout-vs-weekly-swing]] - trend_breakout core vs weekly_swing vs both (CHEF/CRM/SAIC + pool); TB alone no edge, TB+same-week WS +0.6-1.5pp IS&OOS (CORRECTED 10-27: lookahead, see [[bt-2026-10-27-both-lookahead-free]])
- [[bt-2026-10-23-debate-candidates]] — LTC/LQDT/CENTA (both), JNJ/VRTX (TB), EMR/REGN (WS): LTC contradicts pooled both-edge; JNJ best but not firing
- [[bt-2026-10-27-debate-candidates]] — FBNC/NTNX/PKG (both), PAYX/ADUS/CRM/CACI/TTEK/BDX/RLI (WS): pooled both-signal edge is a Mon-Thu entry-before-confirmation artifact; no candidate positive, CRM and ADUS significantly negative
- [[bt-2026-10-27-both-lookahead-free]] - Lookahead-free re-test of TB + same-week WS (entry after the Friday confirmation): no edge, OOS -0.23% T20 / -0.32% STATE vs SPY; the Oct 22 +0.38% was a +0.7pp lookahead premium; PKG own history
- [[bt-2026-10-28-ws-only-candidates]] - CRM/TTEK/ADUS/PGNY/WDAY/BDX/CTAS/CACI/SONO/USPH/DHR/ELF (weekly_swing-only): no significantly positive own history; ELF positive but two-trade driven; CRM, ADUS, USPH, DHR significantly negative; CACI and SONO sector-map mismatches
- [[bt-2026-10-29-ws-new-names]] - HRB/FIZZ/DFIN (weekly_swing): HRB only positive (shrunk +1.7%, n=11, CI incl 0); FIZZ 0 of 7 beat SPY; DFIN state-only, sector mis-mapped to XLK
- [[sector-map-mismatches]] - Repo sector map errors (CACI XLK->XLI, SONO XLK->XLY, PKG XLY->XLB); vs-sector excess unreliable, vs-SPY unaffected; mapping unchanged; Cursor fix prompt ready (override file behind SECTOR_MAP_OVERRIDES_MODE, default off, on only after 2026-11-07)
- [[bt-2026-10-29-earnings-in-hold-and-vetoes]] - Earnings in first 10 sessions: no OOS mean penalty, stop-gap rate ~2x (half-size as risk rule, not skip); small-sample per-name vetoes: noise for WS, weak -0.5%/trade for TB (OOS n.s.), own-history rank corr ~0 raw or shrunk
- [[bt-2026-11-02-debate-candidates]] - 12 names (WS/TB/lookahead-free both): no setup beats its pool except INSW T20 (+4.5% shrunk, Holm p=0.007), which is tanker beta (+1.1 vs peers, IS -11); DHR STATE significantly negative; bank cluster = one bet (rho 0.82, N_eff 1.2)
- [[scan-quality-2026-10-21]] - Scan desk readout: 100-name universe, degraded quotes, signal_score no edge on past picks, extension hurts
- [[scan-quality-2026-10-22]] - Scan desk readout: universe fixed to 1503, data ok, 221 signals (stage2_fail=0 suspect), high extension keeps losing
- [[scan-quality-2026-10-23]] - Scan desk readout: Stage 2 back (1198 fail), 45 signals, 10 executable, both-shadow log started (LTC, LQDT, CENTA pass filters)
- [[scan-quality-2026-10-27]] - Scan desk readout: Stage 2 ran (1176 fail), 37 signals, 5 executable, both-shadow 10-23 filtered names lead; 10-26 had no standard scan
- [[scan-quality-2026-10-28]] - Scan desk readout: Stage 2 ran (1178 fail), 32 signals, 4 executable (all filtered out), 12 WS-only qualifiers, both-shadow cohorts all below SPY
- [[scan-quality-2026-10-29]] - Scan desk readout: Schwab primary data down (all 1503 on yfinance fallback), Stage 2 ran (1161 fail), 33 signals, 3 executable both-names (all stretched), 12 WS-only qualifiers
- [[pead]] — Post-earnings announcement drift scoring
- [[forensic-accounting]] — Sloan, Beneish, Altman financial checks
- [[quality-gates]] — Weak signal filtering modes
- [[adaptive-stops]] — ATR-based trailing stop sizing
- [[plugin-modes]] — Execution, exit, event risk, regime, correlation plugins
- [[hypothesis-ledger]] — Decision quality tracking
- [[hypothesis-cards]] — Pre-registered backtest cards scored from artifacts, not by the agent
- [[self-study]] — Automated trade outcome analysis
- [[settled-lessons]] — Today card that records keep / sharpen / revert without applying settings
- [[evolve-logic]] — Post-mortem feature → threshold tuner

## Configuration Reference

- [[schwab-api-keys]] — OAuth credentials and token encryption
- [[scanner-tunables]] — Scanner pipeline env vars
- [[plugin-modes-config]] — Plugin mode env vars
- [[feature-flags]] — Boolean toggles and mode switches
- [[saas-infrastructure]] — Database, Redis, auth, billing env vars

## APIs & Integrations

- [[local-dashboard-endpoints]] — Local FastAPI routes on https://127.0.0.1:8182
- [[saas-endpoints]] — SaaS multi-tenant routes
- [[tenant-dashboard-endpoints]] — Per-tenant API router

## Operations & Deployment

- [[local-ops-box]] — Dedicated mini PC: dashboard on 8182 + overnight backtests
- [[operator-desk]] — Scheduled scan, grade, and backtest brief on the local dashboard
- [[deployment]] — How to deploy (Render, Docker, manual)
- [[schwab-oauth-setup]] — OAuth setup runbook
- [[troubleshooting]] — Common issues and recovery
- [[validation]] — Validation pipeline and matrix
- [[canary-rollout]] — Controlled live testing process
- [[signal-quality-rollout]] — Quality gate promotion plan
- [[multi-sleeve-phased-build-plan]] — Multi-sleeve platform + research phase roadmap
- [[promotion-playbook]] — Signed off → shadow → live promotion process
- [[thirty-day-signal-cut-plan]] — 2026-10-09 to 2026-11-07: freeze the live stack, close top-N, decide correlation guard, harvest PEAD paper
- [[backtest]] — Historical replay harness (live-parity rules) plus the dashboard Backtest tab; full results catalog in `schwab_skill/docs/BACKTEST_CATALOG.md`
- [[backtest-intelligence-overlay]] — Historical attribution for shadow plugins
- [[backup-restore]] — Backup strategy, restore drills, and DR targets
- [[incident-response-saas]] — SaaS incident triage and escalation flow
- [[slo-alerting]] — SLI/SLO targets, alert thresholds, and error budget policy
- [[ops-excellence-loop]] — Drill cadence, pass/fail criteria, and follow-through

## Trade Verdicts

- [[trade-verdict-2026-10-20]] - Debate chair verdict: no trade (0 live signals, degraded data)
- [[trade-verdict-2026-10-21]] - Debate chair verdict: no trade (ALL/BIIB no demonstrable edge, degraded data)
- [[trade-verdict-2026-10-22]] - Debate chair verdict: no trade (UNP no edge; WELL positive EV but binary earnings; round 2 rescan: CHEF/CRM/SAIC, still no trade)
- [[trade-verdict-2026-10-23]] - Debate chair verdict: no trade (no live tested signal; JNJ dissent at half size; LTC vetoed)
- [[trade-verdict-2026-10-27]] - Debate chair verdict: no trade (no tested edge; PKG tracking-size dissent; both-signals edge corrected as lookahead)
- [[trade-verdict-2026-10-28]] - Debate chair verdict: no trade (12 WS-only names, none executable, nothing survives correction; unanimous)
- [[trade-verdict-2026-10-29]] - Debate chair verdict: no trade (degraded data; 12 WS-only names; CTAS only clean name but fails correction; unanimous)

---

*This index is automatically maintained. Every new wiki page must be linked here.*

## Quant Logs

- [[quant-debate-2026-10-21]] - ALL/BIIB ATR stop/2R model, EV, sizing; live setup_confidence>45 test (inconclusive; uncapped in-sample version refuted)
- [[quant-debate-2026-10-22]] - UNP/SXT/WELL model, UNP earnings study and bear case; 221-signal day = Stage 2 not evaluated
- [[quant-debate-2026-10-23]] - 7-name EV ranking with liquidity/earnings flags; JNJ best
- [[quant-debate-2026-10-26]] - UTL (Unitil) quant numbers: EV +0.04R, TB history negative, earnings 11/2, pending trade 9f0ad8c3; verdict pass
- [[quant-debate-2026-10-27]] - 10-name scan-desk EV ranking (NTNX, TTEK, PKG top); PKG best at half size, backtester per-name history, outcome scoring of the 10-20 verdict
- [[quant-debate-2026-10-28]] - 12-name WS-only scan EV ranking (ELF, SONO, CTAS top); Holm/Bonferroni correction, per-name backtester WS history, bear-case no-trade vs trade, scoring of JNJ/SAIC/WELL/UTL/PKG; CTAS best-supported, case for no trade
- [[quant-debate-2026-10-29]] - 12-name WS-only EV ranking with the new earnings-in-hold rule; new HRB/FIZZ/DFIN, software gap (CRM fails uptrend), Holm correction; CTAS only candidate, lean no trade
- [[quant-debate-2026-11-02]] - 12-name midday-rescan EV ranking (INSW, KFY, DHR top); earnings half-size INSW/BDX, RGEN reported 10-27, Holm: nothing survives; lean no trade
