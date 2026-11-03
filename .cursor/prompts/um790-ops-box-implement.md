# UM790 Pro ops box — implement the day/night loop

Paste this into **Agent mode** to finish wiring the Minisforum UM790 Pro as the always-on TradingBot host: local dashboard on **https://127.0.0.1:8182** during RTH, overnight P0 Schwab-only backtests on extra cores. Companion wiki: `wiki/local-ops-box.md`. Companion canvas: keep [UM790 ops box](C:\Users\Sam Thoresen\.cursor\projects\d-TradingBot\canvases\um790-ops-box.canvas.tsx) open if present.

---

## 1. Goal (one sentence)

Make this UM790 Pro a **repeatable ops box**: NVMe working tree, **Wi-Fi as the ops network** (no Ethernet path), scan workers capped at 6, dashboard on 8182 during RTH, and a **separate**, BelowNormal, hardware-profiled overnight five-era job — without promoting plugins or starving the live scan.

## 2. Machine and north star

| Item | Value |
|------|--------|
| SKU | Minisforum UM790 Pro (Venus) |
| CPU | AMD Ryzen 9 7940HS, 8 physical / 16 logical |
| RAM | ~62 GB |
| GPU | Radeon 780M iGPU — **do not use** for pandas backtests or local LLM as a MiroFish substitute |
| Canonical tree | `C:\src\TradingBot` on internal NVMe |
| Avoid | USB volume (`D:\TradingBot` Crucial X9), OneDrive |
| Dashboard | `python scripts/start_local_dashboard.py` → **https://127.0.0.1:8182/** (never port 8000) |
| Workers | `SCAN_STAGE_A_MAX_WORKERS=6`, `SCAN_STAGE_B_MAX_WORKERS=6`, research `--max-workers 6` |
| Promotion gates | PF mean ≥ 1.20, worst-era PF ≥ 1.00. Faster hardware does **not** skip this. |

Priority: **P0** (ops throughput for base-signal research). Do not treat this session as plugin LIVE promotion or SaaS polish.

## 3. Already done (do not redo)

Inspect first. As of 2026-09-11 these exist and should be **extended**, not replaced:

- `schwab_skill/core/hardware_profile.py` — host facts, worker caps (`SCAN_STAGE_A_WORKER_CAP=8`, `B=6`, `RESEARCH=12`), USB warnings; Wi-Fi-up is a valid ops network
- `schwab_skill/scripts/setup_local_ops_box.py` — diagnose + optional `--apply-env`
- `schwab_skill/scripts/apply_local_ops_box_env.py` — upsert `SCAN_STAGE_*` only; refuses to invent `.env`
- `schwab_skill/tests/test_hardware_profile.py`
- `wiki/local-ops-box.md` + index entry
- Git on PATH; AC sleep/hibernate/disk timeout Never
- Copy at `C:\src\TradingBot` with `.venv`, `.env`, `tokens_market.enc`, `tokens_account.enc`
- `schwab_skill/scripts/_lower_python_priority.ps1` — **too coarse** (all Python). Do not call this while the dashboard must stay responsive; new research launcher must lower **only the research process tree**

Do **not** robocopy the whole tree again unless `C:\src\TradingBot` is missing `schwab_skill/core/hardware_profile.py` or `.venv\Scripts\python.exe`.

## 4. Deliverable (implement all of this)

### A. Research launcher (required)

Add `schwab_skill/scripts/run_overnight_ops_research.py` (name may match repo style if a close sibling exists — do not duplicate).

Behavior:

1. `from __future__ import annotations`; skill_dir on `sys.path`; reuse `collect_host_facts` / `assess_ops_box` / `recommend_workers`.
2. Default command: `scripts/run_multi_era_backtest_schwab_only.py --run-tag um790_ops --max-workers <profile.research_max_workers>` (6 on this SKU).
3. **Refuse** (exit non-zero, print reason) if repo bus type is USB / USB3 / USB4.
4. **Refuse** weekday 09:30–16:00 America/New_York unless `--force`. Reuse `core.plugin_shadow_evidence.evidence_session_is_rth` (same pattern as `scripts/run_pead_canary_paper_session.py`).
5. **Refuse** if this process *is* the dashboard / uvicorn on 8182. Wiki rule: never run the full-universe multi-era job in the same process as the dashboard. Concurrent after-hours research in a **separate** process is OK; do not `taskkill` 8182.
6. Set **this** process (and children it spawns) to Windows `BELOW_NORMAL_PRIORITY_CLASS`. Do not demote every Python PID on the box.
7. Network: Wi-Fi-up is accepted. Hard-fail only if **neither** Ethernet nor Wi-Fi is up. Do **not** tell the operator to plug RJ45 — this host has no Ethernet path. Optionally remind them to run `scripts/harden_ops_box_wifi.ps1`.
8. Optional `--job optimizer` → `run_optimization_batch.py --runs 24 --rounds 8 --stall-rounds 4 --timeout-seconds 2400 --retry-on-fail 1` (from `BEST_FIT_CADENCE.md` weekly). Optional `--job ablation` → existing `run_param_ablation.py` manifest path. Default job remains five-era Schwab-only.
9. PEAD cache-warming, if exposed, stays `--max-workers 1`.
10. No live orders. No `.env` plugin mode changes. No `REGIME_V2` / `CORRELATION_GUARD` / `PROB_RANK` LIVE.

CLI sketch:

```text
python scripts/run_overnight_ops_research.py
python scripts/run_overnight_ops_research.py --dry-run
python scripts/run_overnight_ops_research.py --force
python scripts/run_overnight_ops_research.py --job optimizer
```

`--dry-run` prints workers, bus type, RTH check, argv; does not start pandas.

### B. USB / NVMe guard on dashboard start (required)

In `scripts/start_local_dashboard.py`, after resolving `REPO_ROOT`, warn loudly if bus type is USB. Prefer **warn + continue** unless `--require-nvme` is passed (add that flag). Do not silently start from `D:\` as if it were the ops box. Never bind 8000.

### C. Windows Task Scheduler (required, idempotent)

Add `schwab_skill/scripts/install_ops_box_tasks.ps1` (or a Python wrapper that shells `schtasks`):

1. **Dashboard at logon** (optional flag `--with-logon-dashboard`, default **off** so an unattended USB checkout cannot become the live bot). When enabled: `C:\src\TradingBot\.venv\Scripts\python.exe scripts\start_local_dashboard.py --no-pull` with working directory `C:\src\TradingBot\schwab_skill`.
2. **Overnight research** weekdays **16:20 America/Chicago** (post 16:00 ET close; this host is CT). Action: NVMe venv python `scripts/run_overnight_ops_research.py` (no `--force`). Task name e.g. `TradingBotOvernightOps`. `/F` replace if exists.
3. Do not store secrets in the task XML. Working directory must be `C:\src\TradingBot\schwab_skill`.
4. `--remove` deletes the tasks. `--dry-run` prints schtasks lines only.

If `C:\src\TradingBot\.venv\Scripts\python.exe` is missing, fail closed and tell the operator to open the NVMe tree.

### D. Tests (required)

Extend `tests/test_hardware_profile.py` or add `tests/test_overnight_ops_research.py`:

- UM790-class workers still 6/6/6
- USB bus → launcher dry-run / preflight returns non-zero (inject `HostFacts`, do not hit Schwab)
- RTH clock → refuse without `--force` (freeze time or inject datetime)
- `--dry-run` does not spawn `run_multi_era_backtest_schwab_only.py`

Do not run a full five-era universe in CI or in this session during RTH.

### E. Wiki (required, small)

Update `wiki/local-ops-box.md`:

- Day vs night split
- New launcher + scheduled task commands
- Explicit: `_lower_python_priority.ps1` is not the overnight path (it demotes the dashboard)
- Still: do not set scan workers to 16; PEAD `--max-workers 1`; promotion gates unchanged

Keep `[[wikilinks]]` bidirectional; index already has `[[local-ops-box]]`.

### F. Host wiring on this PC (do after A–D compile)

Work from **`C:\src\TradingBot`** if that tree is complete; if Cursor is still on `D:\TradingBot`, implement in the open workspace **and** copy the new files to `C:\src\TradingBot` (do not reverse-sync secrets). Prefer one source of truth: NVMe.

1. `C:\src\TradingBot\.venv\Scripts\python.exe schwab_skill\scripts\setup_local_ops_box.py`
2. `--apply-env` only if `SCAN_STAGE_*` is not already 6
3. Network is Wi-Fi. Do not ask for an Ethernet cable. If Wi-Fi is down, that **is** a blocker. If Wi-Fi is up, run `harden_ops_box_wifi.ps1` once (power-saving off).
4. If nothing is listening on 8182 and it is RTH (or the operator needs the UI): start  
   `C:\src\TradingBot\.venv\Scripts\python.exe scripts\start_local_dashboard.py`  
   from `C:\src\TradingBot\schwab_skill` with `block_until_ms` 0 after confirming startup logs. Verify **https://127.0.0.1:8182/** (self-signed OK). Surface `data_quality` verbatim — do not soften.
5. Install the overnight scheduled task (`--dry-run` first, then apply).
6. **Do not** start `run_multi_era_backtest_schwab_only.py` during RTH. If this session is after 16:00 ET and the operator did not forbid it, you may start overnight research **in a separate process** at BelowNormal with `--run-tag um790_ops --max-workers 6`.

Tell the operator, in the summary, to **File → Open Folder → `C:\src\TradingBot`** if the IDE is still on `D:\`.

## 5. Out of scope / hard no

- Live orders (no `place_order` without explicit ticker, side, qty, order type confirmation)
- Flipping `REGIME_V2_MODE`, `CORRELATION_GUARD_MODE`, or `PROB_RANK` to LIVE
- `SCAN_STAGE_*_MAX_WORKERS=16` or mapping workers to logical thread count
- Using the iGPU / local LLM instead of MiroFish/OpenAI
- Running Alex Track B (`run_alex_multi_era_backtest.py`) as the live Stage 2 scanner
- Committing `.env`, `tokens_*.enc`, or `hardware_profile.json`
- Binding or verifying the operator UI on port 8000
- Killing the dashboard to “free cores” during RTH
- Full-universe multi-era inside pytest
- Inventing `.env` if missing — copy from the old PC or stop

## 6. Implementation constraints

- Smallest coherent diff; match `project-conventions.mdc` (`from __future__ import annotations`, `LOG.info("… %s", var)` never f-strings in logs)
- Config tunables stay in `config.py` / `.env`; do not add worker env vars that `hardware_profile.env_updates()` already covers
- Fail closed on USB research; Wi-Fi-only is the accepted ops network; fail only if no NIC is up
- Process env overrides `.env`
- Dashboard port contract: 8182 only for local operator UI

## 7. Validation

From `schwab_skill/` using the **NVMe venv** when available:

```text
python -m ruff check core/hardware_profile.py scripts/setup_local_ops_box.py scripts/apply_local_ops_box_env.py scripts/run_overnight_ops_research.py scripts/start_local_dashboard.py tests/test_hardware_profile.py tests/test_overnight_ops_research.py
python -m pytest -q tests/test_hardware_profile.py tests/test_overnight_ops_research.py tests/test_local_dashboard_port.py
python scripts/setup_local_ops_box.py --json
python scripts/run_overnight_ops_research.py --dry-run
```

Behavior change: `python scripts/validate_all.py --profile local --strict --skip-backtest` if you touch dashboard boot or env upsert.

UI: only **https://127.0.0.1:8182/**.

## 8. Success criteria

- [ ] Overnight research is one command, profile-driven workers, USB-blocked, RTH-blocked without `--force`
- [ ] Research priority does not demote the 8182 dashboard process
- [ ] Scheduled weekday post-close task points at `C:\src\TradingBot`, not `D:\`
- [ ] `SCAN_STAGE_A/B_MAX_WORKERS=6` on this host; not 16
- [ ] Dashboard still 8182; USB tree warned
- [ ] Wiki documents the loop
- [ ] Tests cover USB + RTH refuse without calling Schwab
- [ ] Summary lists: changed files, command outcomes, Cursor folder still on the human if they are on `D:\`, **do not** start overnight during RTH unless after the close

## 9. Session start (agent)

1. Priority **P0**. Files: launcher + tests + `start_local_dashboard.py` USB warn + `install_ops_box_tasks.ps1` + `wiki/local-ops-box.md`.
2. Read `core/hardware_profile.py`, `scripts/setup_local_ops_box.py`, `wiki/local-ops-box.md` before adding abstractions.
3. State blockers the human must do (Open Folder `C:\src\TradingBot` if still on USB). Do not ask them to plug Ethernet.
4. Implement A–E, then host wiring F. Do not skip tests.

## 10. Copy-paste kickoff

```text
Implement the UM790 Pro ops-box day/night loop per .cursor/prompts/um790-ops-box-implement.md

Canonical tree: C:\src\TradingBot (NVMe). Do not treat D:\TradingBot (USB) as live.
Dashboard: https://127.0.0.1:8182/ via scripts/start_local_dashboard.py — never port 8000.
Workers: SCAN_STAGE_A/B=6, research --max-workers 6. Do not set 16.

Build:
1) scripts/run_overnight_ops_research.py (USB refuse, RTH refuse unless --force, BelowNormal on this tree only, default five-era --run-tag um790_ops)
2) USB warn on start_local_dashboard.py
3) Idempotent scheduled task for weekday 16:20 CT
4) Tests (no full multi-era, no Schwab)
5) Update wiki/local-ops-box.md

Do not place orders. Do not flip REGIME_V2 / CORRELATION_GUARD / PROB_RANK to LIVE.
Do not start a five-era backtest during RTH. This host is Wi-Fi only — do not ask for Ethernet.
Reuse hardware_profile.py; do not rewrite it.
```

---

*Created 2026-09-11 for TradingBot UM790 Pro ops-box completion.*
