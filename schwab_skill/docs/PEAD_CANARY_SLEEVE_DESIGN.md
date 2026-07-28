# PEAD canary sleeve — design only (non-executable)

> Status: **design draft** (2026-07-23). Not enabled. Do **not** set
> `STRATEGY_PEAD_PRIMARY_ALLOW_LIVE=true`. Stage2 remains the only executable
> entry family.

Source of truth for evidence: `docs/BACKTEST_CATALOG.md` §11 / open items 14–17,
`SIGNAL_QUALITY_ROLLOUT.md` Stage 2e–2f,
`validation_artifacts/pead_primary_capacity_cf_pead_primary_aug_fixed_full_c40.json`.

---

## 1. Purpose

Run a **paper / diagnostics-only** PEAD sleeve beside live Stage2 so operators can
compare capacity-ranked PEAD admits to Stage2 executables session-by-session —
without any PEAD-only order path.

## 2. Non-goals (locked)

- No PEAD-only executable signals or orders
- No Stage2 × PEAD intersection hard gate
- No merged Stage2+PEAD shared ranking/sizing stack
- No Regime v2 / Correlation Guard / PROB_RANK live for this sleeve
- No Stage2 1% breakout buffer transferred onto PEAD
- No `ALLOW_LIVE=true` in this design phase

## 3. Operating stack (shadow / paper)

| Layer | Choice | Notes |
|---|---|---|
| Executable entries | Stage2 only | 1% breakout buffer + exit grace 15/40 + rank-v2 p76 + pts_52w≤37 |
| PEAD mode | `STRATEGY_PEAD_PRIMARY_MODE=shadow` | `ALLOW_LIVE=false` always in this design |
| PEAD entry | beat + liquidity | Dual-admit shadow admit |
| PEAD rank / capacity | **`top5_by_edge_score`** | `PEAD_PRIMARY_SHADOW_RANK_TOP_N=5` |
| PEAD list soft cap | `PEAD_PRIMARY_SHADOW_MAX_NAMES=50` | Provenance; capacity top-5 is the sleeve |
| PEAD exit (research note) | `exit_grace_t15_h40` | Not a live PEAD exit manager |
| PEAD sizing (paper note) | max-positions + risk/trade, score-priority fill | Capacity CF defaults; paper equity only |
| Do not default | pts_52w≤37 / rank-v2 p76 as PEAD policy | Clear offline; stay non-default |

## 4. Sleeve definition (what “canary” means here)

**Canary sleeve** = the PEAD-only names marked `capacity_top_n=true` on each
dual-admit scan (≤5 names), plus any Stage2∩PEAD overlap names for comparison.

- Paper book tracks those names for the session (entry family, edge_score,
  surprise, overlap flag).
- Overlap (`entry_family=both`) may already be Stage2-executable; the sleeve
  **does not** change that path — it only annotates PEAD provenance.
- PEAD-only names remain `executable=false` forever under this design.

## 5. Session protocol

```bash
# Prefer RTH hours; require data_quality=ok for enablement-bar sessions
python scripts/run_pead_primary_shadow_scan.py --max-tickers 0 --label full_sp1500_rth_<YYYYMMDD>
python scripts/compare_pead_primary_shadow_to_offline.py --write-artifact
```

**Pass gates (every session):**

1. `pead_primary_evaluated` > 0  
2. `executable_stage2_only=True` / 0 PEAD-only in executable signals  
3. Compare verdict `pass`  
4. Capacity arm still `top5_by_edge_score` (or documented intentional change)

**Record:** eval / admit / overlap / leak / capacity_top_n / dq / compare artifact path
in `BACKTEST_CATALOG.md` decision log.

## 6. Reporting (minimal)

Per session, surface (CLI / compare artifact / shadow ledger):

| Field | Source |
|---|---|
| Capacity top-5 tickers | `canary_sleeve.tickers` on compare report (from `capacity_top_n` shadow rows) |
| Edge scores | `canary_sleeve.names[].edge_score` |
| Overlap with Stage2 | `overlap_with_stage2` + `entry_family=both` |
| Leak check | compare invariant `executable_still_stage2_only` |
| Soft-cap truncated count | `pead_primary_shadow_truncated` |

```bash
python scripts/compare_pead_primary_shadow_to_offline.py --write-artifact
python scripts/summarize_pead_dual_admit_sessions.py
```

Ledger: continue appending via `plugin_shadow_evidence` (`pead_primary.canary_sleeve`) / shadow scan JSONL.
No new executable order stream.

## 7. Paper sizing sketch (not live capital)

Use capacity-CF defaults as **notes only**:

- `BACKTEST_PORTFOLIO_MAX_POSITIONS` (typically 10)
- `BACKTEST_RISK_PER_TRADE_PCT` with score-priority fill on `edge_score`
- Sleeve fill capped at **5** PEAD-only names/day (the canary), independent of
  Stage2 live book

Do **not** allocate broker cash or place PEAD-only orders under this design.

## 8. Enablement bar (future — not this doc’s approval)

Before any enablement discussion (still not approved here):

1. ≥1 additional **RTH-hours** dual-admit session with `data_quality=ok` and
   compare `pass` (beyond the post-close `dq=stale` session)
2. Explicit operator decision + promotion ledger target (if ever used)
3. Separate design for any paper→live bridge (out of scope here)

Until then: keep `STRATEGY_PEAD_PRIMARY_ALLOW_LIVE=false`.

## 9. Explicit reject list

| Idea | Verdict |
|---|---|
| Stage2 1% buffer on PEAD | Reject (worst-era break offline) |
| denser top8 / edge_score_p70 as default canary | Optional research only; canary stays top5 |
| top3_by_edge_score as default | Reject (PF mean 1.492 fail) |
| PEAD-only in `signals` / Stage B | Reject |

---

*Design draft + compare/ledger canary reporting wired — no runtime enablement. Last updated: 2026-07-23.*
