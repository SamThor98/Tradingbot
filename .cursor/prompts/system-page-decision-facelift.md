# System page — Decision Facelift (master prompt)

Paste this into Agent mode when you want a full System-screen redesign wave.
Companion rules: `.cursor/rules/webapp-figma-workflow.mdc`, `.cursor/rules/webapp-frontend.mdc`.
Figma file: [Old Logan Capital Design System](https://www.figma.com/design/FMWpss3fGm0yzbjiyP3wSx/Old-Logan-Capital-Design-System) (`FMWpss3fGm0yzbjiyP3wSx`).
Verify URL: `https://127.0.0.1:8182/?screen=diagnostics`

---

## 1. Goal (one sentence)

Redesign the **System** screen so an operator can answer three questions in under 10 seconds — *Can I trade? What must I fix? What should I decide next?* — without wading through a wall of diagnostics.

## 2. Deliverable

1. **Figma first** (net-new frames on page `System Diagnostics v2`) → user approves → then code.
2. Five states per primary surface: **Success · Partial · Empty · Loading · Error**.
3. Code lands in vanilla ES modules + `readability.css` (`body[data-theme="editorial"]` only). No hex in panel JS.
4. Preserve all existing DOM ids / APIs unless a migration table is approved.

## 3. Problem statement (current pain)

The System tab is a **vertical dump** of zones, ribbons, disclosures, settings leftovers, calibration, shadow scoreboard, review loop, and nested decision dashboards. Operators cannot tell:

- What is blocking trading **right now**
- What is informational vs actionable
- Which panel is primary vs advanced
- What decision each number supports

Settings were **merged into System** without a navigation model. Result: cognitive overload, duplicated health KPIs, and collapsed disclosures that hide the only decision-useful content.

## 4. North-star UX principles

1. **Decision over documentation.** Every visible block must answer a question or enable an action. If it doesn’t, bury it under Advanced.
2. **One primary verdict above the fold.** Large status: `TRADE READY` / `FIX BLOCKERS` / `DEGRADED` / `UNKNOWN`.
3. **Progressive disclosure.** Default viewport = verdict + ≤3 blockers + ≤4 health tiles + sticky lane nav. Everything else behind tabs or `<details>`.
4. **Actionable blockers only.** Each blocker is a row: problem · why it matters · one CTA (`Reconnect`, `Open health`, `Refresh validation`).
5. **Same language as trading.** Prefer “Can’t stage trades — broker sign-in expired” over “Auth probe failed (token_age)”.
6. **No duplicate KPIs.** Auth/Quotes/Validation appear once in the command strip; don’t repeat in Zone 1 + ribbon + status details.
7. **Calm editorial DS.** Old Logan tokens only (`ol/bg`, `ol/surface`, `ol/accent`, `ol/gold`, `ol/ok`, `ol/warn`, `ol/bad`, `ol/text`, `ol/muted`). Avoid purple gradients, dark-mode defaults, glow, emoji spam, pill clusters.
8. **Scan path.** Eye path: Verdict → Blockers → Next decision → Lane tabs. Never force scroll past 2 screens to find readiness.

## 5. Target information architecture

```text
System (screen)
├── A. Command strip (always visible)
│   ├── Verdict chip + one-line status
│   ├── Blocker list (0–N, max 3 shown + “+N more”)
│   ├── Health tiles: Auth · Quotes · API errors · Validation  (single source of truth)
│   └── Primary CTAs: Refresh health · Reconnect (if needed)
│
├── B. Sticky lane nav (in-page tabs, not new screens)
│   ├── Ready     → command strip focus (default)
│   ├── Decide    → promotion / signal-edge / strategy compare
│   ├── Calibrate → calibration + self-study
│   ├── Shadow    → plugin shadow scoreboard
│   ├── Review    → trade review loop
│   ├── Connect   → settings summary / onboarding / tokens (was Settings merge)
│   └── Advanced  → raw status details, provenance, dumps
│
└── C. Lane body (one lane visible at a time)
```

### Lane jobs-to-be-done

| Lane | Operator question | Primary modules / DOM | Default open? |
|------|-------------------|------------------------|---------------|
| Ready | Can I trade right now? | `#systemSummaryLanding`, `#healthRibbon`, `#systemAlertBanner` | **Yes** |
| Decide | Should I promote / change stack? | `#systemDecisionPanel`, `#decisionDashboardCard` | No |
| Calibrate | Is the model drifting? | `#calibrationSection` | No |
| Shadow | What would plugins have done? | `#shadowScoreboardSection` | No |
| Review | What did we get wrong recently? | `#reviewLoopSection` | No |
| Connect | Is Schwab / live trading wired? | `#settingsSummaryLanding`, `#settingsAccountPanel`, onboarding | No |
| Advanced | Need raw tokens / validation steps? | `#statusDetailsPanel` | No |

### First viewport budget (Ready lane)

Allowed above the fold:

- Brand/chrome already in app shell (don’t re-hero)
- One verdict
- One short status sentence
- Blocker list (or empty “No blockers”)
- Four health tiles
- One CTA group

Forbidden above the fold:

- Ablation cycle buttons
- Calibration charts
- Shadow scoreboard tables
- Institutional dossier
- Portfolio triage (belongs on Research)
- Long muted paragraphs / workflow strip nostalgia

## 6. Decision taxonomy (map content → decisions)

Every panel must declare which decision it serves:

| Decision | Pass criteria (plain English) | Source signals |
|----------|-------------------------------|----------------|
| D1 Trade readiness | Auth OK + quotes OK + no hard banner | health ribbon / `/api/health` |
| D2 Data trust | `data_quality=ok` on last scan; low API error rate | last_scan diagnostics, API error tile |
| D3 Promote / hold stack | PF floors + fidelity sessions in band | decision dashboard / signal edge board |
| D4 Plugin posture | Shadow evidence enough; don’t flip LIVE casually | shadow scoreboard |
| D5 Recalibrate | Drift / reliability bar outside band | calibration |
| D6 Post-trade learn | False positives / review packets | review loop |
| D7 Connectivity | Tokens fresh; live toggles intentional | settings merge |

UI rule: each lane body starts with a **Decision card**:
`Decision · Status (Go/Hold/Fix) · Why (1 line) · Evidence (≤3 bullets) · Action`

## 7. Figma wave plan

**Page:** `System Diagnostics v2` (already in file; currently empty).

Create frames (desktop 1440×900 minimum; also 1280):

| Frame name | State | Notes |
|------------|-------|-------|
| System — Ready · Success | Success | Verdict TRADE READY, 0 blockers |
| System — Ready · Partial | Partial | 1–2 warn blockers, trade still allowed with caution |
| System — Ready · Fix | Error/Partial | FIX BLOCKERS, Auth expired CTA |
| System — Ready · Loading | Loading | Skeleton tiles |
| System — Ready · Empty | Empty | Never-checked / first boot |
| System — Decide · Success | Success | Signal-edge gates + one promote recommendation |
| System — Calibrate · Success | Success | Reuse Calibration Success patterns (node `5:2`) |
| System — Shadow · Success | Success | Compact scoreboard, not raw JSON |
| System — Connect · Success | Success | Connection priority only |
| System — Advanced · Success | Success | Collapsed by default pattern |

Annotate each frame with:

- DOM `#id`s remapped
- `?screen=diagnostics&section=<alias>`
- API endpoints
- Pass/fail color meaning (`ol/ok|warn|bad`)

**Skills required:** `/figma-use`, `/figma-generate-design`, Old Logan variables, KPI tiles (`7:24`), health ribbon patterns, disclosure cards.

**Capture baseline:** `generate_figma_design` from `https://127.0.0.1:8182/?screen=diagnostics` into the same file as reference, then delete capture after redesign parity.

**Approval gate:** Do **not** implement code until user approves Ready + Decide frames.

## 8. Implementation constraints (after approval)

### In scope

- `schwab_skill/webapp/static/index.html` — System section structure / lane nav
- `app.js` — landing / alert / health wiring; lane state
- `modules/systemPanelContract.js`, `modules/systemStatus.js`
- `panels/decisionDashboard.js`, `healthRibbon` / calibration / shadow / review panels as needed
- `readability.css` editorial overrides
- `modules/router.js` + `wiki/frontend-route-contract.md` for new `?section=` aliases
- Contract tests: router, system panel, static routes

### Out of scope

- Changing promotion math / PF floors
- Enabling `ALLOCATOR_MODE=live` or S1 live
- Research / Ops scan triage redesign
- SaaS-only surfaces

### Hard rules

- Keep fail-closed visibility: never soften `data_quality` or guardrail errors
- Keep 8-column scan triage invariant untouched (different screen)
- No React/Tailwind dump — adapt Figma to vanilla modules
- Prefer collapsing Settings into Connect lane over a second top-level tab

## 9. Success criteria

Operator can, without training:

1. See trade readiness in **< 3 seconds**
2. Clear or navigate to the top blocker in **one click**
3. Switch to Decide / Calibrate / Shadow without scrolling a manifesto
4. Never confuse a shadow would-have with a live trading block
5. On Partial/Error states, always see a next action (not just red text)

QA checklist:

- [ ] Browser: Ready Success / Fix / Loading on 8182
- [ ] Keyboard: lane nav + skip links work
- [ ] Deep links: `?screen=diagnostics&section=health|calibration|…`
- [ ] Pytest: router + system contracts green
- [ ] Contrast: editorial theme OK/Warn/Bad readable

## 10. Session start checklist (agent)

1. Priority: **P1 dashboard UX**
2. State goal + Figma page `System Diagnostics v2`
3. Read current `#systemSummaryLanding` … `#systemQualityDiagnostics` in `index.html`
4. Figma Phase 1–2 only unless user says **“approved, implement”**
5. One wave per session (Ready states first; Decide second)

## 11. Copy-paste kickoff

```text
Run the System page Decision Facelift per .cursor/prompts/system-page-decision-facelift.md

Wave: Ready lane — all 5 states in Figma on page "System Diagnostics v2"
File: FMWpss3fGm0yzbjiyP3wSx
Verify later: https://127.0.0.1:8182/?screen=diagnostics

Do NOT implement code until I approve the Ready Success + Ready Fix frames.
Follow webapp-figma-workflow.mdc. Use ol/* tokens. Decision > documentation.
```

---

*Created 2026-07-28 for TradingBot System screen IA reset.*

## Status (2026-07-28)

**Figma (page System Diagnostics v2):** Ready Success / Fix / Partial / Loading + Decide / Calibrate / Shadow / Review / Connect / Advanced Success frames are in the file.

**Code (first ship):** System lane nav is live on `?screen=diagnostics` — command strip always on; lane buttons show one body at a time (`modules/systemLanes.js`). Deep links: `?screen=diagnostics&lane=decide` (also section aliases like calibration / shadow / review).

**Still open:** Per-lane visual polish beyond shell (optional); operator feedback on lane copy.
