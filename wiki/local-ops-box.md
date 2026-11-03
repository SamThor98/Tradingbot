---
source: n/a (compiled from UM790 Pro setup + hardware_profile.py)
created: 2026-09-11
updated: 2026-09-11
tags: [ops, hardware, local-dashboard, backtest]
---

# Local Ops Box

> Dedicated always-on Windows mini PC for the local dashboard (`https://127.0.0.1:8182`) plus overnight P0 backtests. Not a GPU research cluster and not a SaaS deploy.

## Why a second box

The live scanner is bound by Schwab I/O and sleep/lock, not FLOPS. Extra cores buy overnight pandas/subprocess throughput (`scripts/run_multi_era_backtest_schwab_only.py --max-workers`) and a host that does not sleep when a laptop lid closes.

This machine does **not** skip promotion gates. Plugin LIVE flips and Stage A hard gates still need five-era PF evidence (mean ≥ 1.20, worst-era ≥ 1.00). See [[backtest]] and `schwab_skill/docs/BACKTEST_CATALOG.md`.

## Current host (2026-09-11)

Minisforum **UM790 Pro** (Venus series):

| Item | Value |
|------|--------|
| CPU | AMD Ryzen 9 7940HS, 8 cores / 16 threads |
| RAM | 64 GB |
| GPU | Radeon 780M iGPU (do not use for pandas backtests) |
| Internal disk | YMTC 1 TB NVMe (Windows, `C:`) |
| Preferred repo path | `C:\src\TradingBot` on that NVMe |
| Avoid | USB volume (Crucial X9 as `D:`) for SQLite, tokens, and caches |
| Network | **Wi-Fi only** (no Ethernet path on this install). RZ616 Wi-Fi 6E is enough for Schwab. |

Recommended knobs for this SKU (from `core/hardware_profile.py`):

| Knob | Value | Why |
|------|-------|-----|
| `SCAN_STAGE_A_MAX_WORKERS` | 6 | Leave 2 cores for Windows + dashboard; cap below 16 threads to limit Schwab 429s |
| `SCAN_STAGE_B_MAX_WORKERS` | 6 | Stage B is shortlist-only; 6 is a modest bump from the default 4 |
| Research `--max-workers` | 6 | One heavy pandas subprocess per physical core minus two |

Do **not** set scan workers to 16. Rate limits, not CPU, dominate RTH scans.

## Day-0 checklist

1. Network is **Wi-Fi**. This host has no Ethernet path — do not block on the 2.5G jack. Keep both UM790 antennas on.
2. Disable Wi-Fi adapter power saving so overnight jobs do not drop: `powershell -File scripts/harden_ops_box_wifi.ps1` (elevated if Windows refuses the change).
3. Power: sleep after = Never on AC. Keep the 19V brick in the barrel jack.
4. Working tree on **internal NVMe**, not OneDrive and not a USB SSD.
5. Copy `schwab_skill/.env`, `tokens_market.enc`, and `tokens_account.enc` from the previous PC. Do not commit them.
6. From `schwab_skill/`:
   ```
   python scripts/setup_local_ops_box.py
   python scripts/setup_local_ops_box.py --apply-env
   python scripts/start_local_dashboard.py
   ```
7. Confirm [[webapp-dashboard]] on **https://127.0.0.1:8182/** (never port 8000).

`setup_local_ops_box.py` writes `schwab_skill/artifacts/hardware_profile.json` (gitignored) and refuses to invent `.env`.

## Split ops from research

- **Ops:** `python scripts/start_local_dashboard.py` stays up during RTH. Never point a 6-worker multi-era job at that same Python process.
- **Research:** after the close, below-normal priority, tagged artifacts only:

```
python scripts/run_multi_era_backtest_schwab_only.py --run-tag um790_ops --max-workers 6
```

Alex Track B (`scripts/run_alex_multi_era_backtest.py`) is research-only and must not change the live Stage 2 scanner. PEAD cache-warming runs stay `--max-workers 1`.

## What this silicon is not for

- Promoting `REGIME_V2`, `CORRELATION_GUARD`, or `PROB_RANK` to LIVE because the box is faster
- Local LLM inference as a substitute for MiroFish/OpenAI API calls
- Raising Stage A worker counts until Schwab 429s appear
- New hard Stage A gates without a fresh five-era pass

## Related Pages

- [[scanner-tunables]] — `SCAN_STAGE_*` env vars
- [[webapp-dashboard]] — local UI on 8182
- [[deployment]] — SaaS/Render is a different host
- [[backup-restore]] — tokens, SQLite, and `.env` stay off USB/OneDrive
- [[backtest]] — overnight jobs and the Sunday-night 8182 queue campaign (`scripts/run_weekend_backtest_campaign.py`)
- [[schwab-oauth-setup]] — token copy vs re-auth
- [[troubleshooting]] — sleep, auth, and data_quality failures

---

*Last compiled: 2026-09-11 (Wi-Fi-only ops network)*
