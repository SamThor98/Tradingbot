# P1 — Dashboard professional facelift (correctness + visual)

Paste this into **Agent mode**. Companion rules: `.cursor/rules/webapp-frontend.mdc`, `.cursor/rules/webapp-figma-workflow.mdc`, `.cursor/rules/project-conventions.mdc`. Companion wiki: `wiki/frontend-route-contract.md`, `wiki/webapp-dashboard.md`, `wiki/static-module-layout.md`, `wiki/section-migration-map.md`. Design tokens: `schwab_skill/docs/FRONTEND_DESIGN_SYSTEM.md` + Old Logan DS (`FMWpss3fGm0yzbjiyP3wSx`).

This prompt is **not** the earlier “flow / basis / function” pass. That pass made screens understandable. This pass makes every visible number, chart, and surface **correct, complete, and professional** — no engineer leftovers.

---

## 0. Session start (required)

State, then proceed:

1. **Priority:** P1 dashboard UX. Do **not** change scanner filters, `core/scoring_rank_v2.py`, backtest engine, or LIVE plugin promotion unless a UI field is empty or wrong **because of an API/UI unit mismatch**.
2. **Files you expect to touch:** `schwab_skill/webapp/static/**` (panels, modules, `index.html`, `readability.css`). Tests under `schwab_skill/tests/js/` and any `test_*router*` / panel contract tests you touch. Wiki only if you add a `?section=` alias.
3. **Validation:**
   - `cd schwab_skill && python -m ruff check webapp/static` (if you touch Python)
   - `node --test "tests/js/*.test.mjs"` from `schwab_skill/`
   - `python -m pytest -q tests/test_js_unit.py tests/test_static_router.py tests/test_scan_triage_table_width_contract.py`
   - Browser: **https://127.0.0.1:8182/** only (`python scripts/start_local_dashboard.py`). Never port 8000.

---

## 1. Goal (one sentence)

Make the Old Logan Capital operator dashboard look and behave like a finished product: every chart is the right chart, every percent is the right unit, every panel has a complete professional state, and **no operator ever sees code, JSON, snake_case keys, file paths, or empty beige holes**.

---

## 2. What this is / is not

**This is**

- A **correctness audit** of numbers, diagrams, and logic on all five tabs.
- A **presentation cleanup**: replace raw dumps with labeled cards, tables, and charts.
- A **visual uplift** inside the existing Old Logan editorial theme (cream / forest / gold).
- A **gap hunt**: vacant grid tracks, collapsed-but-empty cards, mid-row table spacers, charts that render at 0 height, panels that say “No run yet.” in a `<pre>`.

**This is not**

- A React / Tailwind / new-framework rewrite.
- A Figma-first wave unless I later say “approved, implement from Figma”.
- A dark-mode, purple-gradient, glassmorphism, or emoji restyle.
- A change to the frozen route contract (`?screen=`, `?section=`, `#id`, Ctrl/Cmd+1–5).
- Softening or hiding `data_quality`, guardrails, or kill-switch banners.
- Inventing prices, filings, PF, or win rates. Missing → em dash `—` plus a one-line reason.

---

## 3. Hard product constraints (do not regress)

| Rule | Detail |
|------|--------|
| Local URL | https://127.0.0.1:8182/ only |
| Theme | `body[data-theme="editorial"]`. New CSS in `readability.css` only. No new hex in panel JS — use `--ol-*` / `YourThemeConfig`. |
| Tokens | `ol/bg #f5f1e8`, `ol/surface #fffefb`, `ol/accent #1a3a2e`, `ol/gold #c9a962`, `ol/ok #2d5a4a`, `ol/warn #8a5c1f`, `ol/bad #c94949`, `ol/text #1a1a1a`, `ol/muted #5a5a5a` |
| Architecture | Vanilla ES modules. `api.get` / `api.post` only. `ApiResponse(ok, data, error)`. |
| Scan triage table | Exactly 8 columns. **Never** add `scan-triage-col-spacer`. Enforced by `tests/test_scan_triage_table_width_contract.py`. |
| Research layout | Full-width single column. Parked `#sectorsSection` / `#moversSection` must not leave a vacant second track. |
| Frozen aliases | `SECTION_ALIASES` may gain aliases; never remove or repoint. |
| Display modes | Simple / Standard / Pro still work. Raw JSON that today auto-opens in Pro must **die**, not hide behind Pro. |
| Fail-closed | Surface `data_quality` and guardrail errors verbatim in plain English. Do not invent a green “OK”. |

---

## 4. Definition of done (operator-facing)

An operator on a first visit must never see:

1. **Code text** — `<pre class="code-block">`, `<code>`, snake_case identifiers (`composite_score=55.1` is OK only after `formatDecisionReason`), `.env` filenames, Python paths, “type ENABLE”, task UUIDs as the primary label.
2. **Raw JSON** — pretty-printed objects, `JSON.stringify` in the DOM, “Raw report / Raw result / Technical checklist” dumps, `{...}` blobs in cells.
3. **Wrong percentages** — 0.42 shown as `0.4%` or `4200%`; win rate already in points run through `pct()`; bars filled from the wrong scale.
4. **Wrong diagrams** — empty chart hosts, PF bars with no gate mark, reliability bars treating 55 as 0.55, heatmaps with unlabeled buckets, equity/drawdown with swapped keys, scan overlays on the wrong price.
5. **Layout gaps** — dead beige shelves after Actions, empty CSS grid tracks, cards with a title and no body, 40px of unexplained whitespace, “Loading…” that never resolves, collapsed `<details>` that look like a hole.
6. **Logic holes** — buttons that do nothing, KPIs with no basis, success chrome on empty data, error objects stringified instead of `user_message`.

Every fetch-driven panel must have five real states: **Success · Partial · Empty · Loading · Error**. Partial = some KPIs present, others `—` with why. Empty is a designed empty state (headline + next action), not a blank card.

---

## 5. Workstream A — Number honesty (percentages and units)

This is the highest-severity logic bug on the dashboard. The API is inconsistent: some fields are **0–1 ratios**, some are **percent points** (55.2 = 55.2%), some are **bps**. The UI has **many local formatters that disagree**.

### Canonical helpers (use these; delete local copies)

In `schwab_skill/webapp/static/modules/format.js`:

| Helper | Input | Output |
|--------|--------|--------|
| `pct(x)` | 0–1 ratio (`0.423`) | `42.3%` |
| `formatPercentPoints(x)` / `formatPP(x)` | already points (`55.2`) | `55.20%` |
| `formatBps(x)` | integer-ish bps | `12.0 bps` or `%` if `asPercent` |
| `formatMoney` / `formatCents` | dollars vs cents | never mix |
| `formatSignedDelta` | signed change | `+1.20` / `-0.40` |
| `unknown()` | missing | `—` |

**Rule:** before formatting a field, read the **Python producer** (not the JS guess). Add a one-line comment at the call site: `// ratio 0–1 from GET /api/... field foo` or `// percent points from ...`. If two producers disagree, fix the UI to the producer and add a JS unit test. Do **not** invent a third heuristic like `abs(n) <= 1.5`.

### Known landmines (audit every call site)

| Location | Bug pattern |
|----------|-------------|
| `modules/format.js` `pct` vs `formatPercentPoints` | Correct API, but callers pick the wrong one |
| `panels/reviewLoop.js` local `pct()` | Does **not** scale. `coverage_pct` from `core/packet_feature_analysis.py` is **already points** (`resolved/n*100`) — local helper is right for coverage, **wrong** if reused on `win_rate` (that file multiplies `win_rate` by 100 and treats `avg_return_pct` as points). Confirm each field against the producer. |
| `panels/decisionDashboard.js` `_fmtSignedPct` | Always `n * 100`. Ablation `relative_lift_vs_baseline` from `scripts/score_ablation_report.py` is a **ratio** (`(v-b)/|b|`) — `* 100` is correct **only** for that field. Do not reuse on point fields. |
| `panels/pricingModel.js` `fmtPctPoints` | Name says points, body does `n * 100`. Confirm WACC / implied growth / implied WACC units from the pricing API. One of name or body is a lie. |
| `panels/eventProbability.js` | `fmtPct` treats as points; `fmtProb` treats as ratio. Confirm `valuation_adjustment_pct` vs `event_mispricing_score` vs probabilities. |
| `panels/sharadarFundamentals.js` + `modules/sharadarBrief.js` | Heuristic `abs(n) <= 1.5 ? n*100 : n` — this **will** mis-print ROE / ownership. Read the Sharadar payload units and pick one helper. |
| `panels/report.js` | Mix of `pct()`, `fmtPctValue` (`abs(n)<=1`), and raw `* 100` on trust scores |
| `panels/calibrationCharts.js` | `fmtPctFraction` always `*100`; `fmtWinRate` uses `n<=1` heuristic; `fmtSignedPct` assumes points |
| `panels/performance.js` | `formatPercentPoints` on `win_rate`, `avg_return_pct`, `max_drawdown_pct` — confirm each vs `/api` + backtest result |
| `panels/backtest.js` / `backtestHub.js` | `win_rate_net` vs `total_return_net_pct` vs `cagr_net_pct` vs `max_drawdown_net_pct` — names suggest mixed units |
| `panels/positionIntel.js` `fmtPctFrac` | Always `*100` for HV20 / GARCH / `p_win` / `p_keep` |
| `panels/pendingBoard.js` + `scanTable.js` + `app.js` | `pct(pUp)` and `ev10d * 100` — confirm `/api/scan` calibrated p(up) is 0–1 |
| `panels/scanTable.js` rank | Mixes `score/100` and `research/100` — do not “fix” rank math; only fix **displayed** percents |
| `modules/signalScores.js` | Already has `n>1 && n<=100 ? n/100` — do not fork another heuristic in panels |
| `panels/decisionCharts.js` `fmtPct` | Assumes **points** for `early_stopout_pct` and `retention_pct` — confirm against signal-edge payload |
| `app.js` health error rate | `(srvErr/req)*100` — this is a derived rate, OK if both are counts |

### Required tests

Extend `tests/js/format.test.mjs` (and add `tests/js/percentUnits.test.mjs` if needed) so every **public** percent field used in the UI has a fixture: `{ field, sampleApiValue, expectedLabel }`. A reviewer must be able to see that `0.423` never becomes `4230%` and `55.2` never becomes `0.6%`.

**Never fabricate.** If the API unit is still ambiguous after reading the producer, render `—` and a “unit unconfirmed” note in diagnostics — do not guess.

---

## 6. Workstream B — Incorrect diagrams and charts

Audit every visual. A diagram that is pretty but wrong is a regression.

### Chart inventory (fix scale, keys, empty, and theme)

| Surface | Module | What to verify |
|---------|--------|----------------|
| Scan ticker candles + overlays | `app.js` + `modules/chartOverlays.js` | SMA50/200, prior-high breakout, stop line use **price** units; overlay only when candles exist; empty host is a designed message, not a beige box |
| Cockpit SPY | `panels/cockpit.js` | Theme from `getLightweightChartsProps()`; resize does not leave a 0-height canvas |
| Backtest equity + drawdown | `panels/backtestCharts.js` | `equity_curve.equity` vs `drawdown_curve.drawdown_pct` — do not swap; hide wrap only when **both** empty; drawdown axis labeled as % |
| Decision PF bars | `panels/decisionCharts.js` | Fallback scale `(pf / 2) * 100` is a magic 2.0 — replace with a labeled scale that includes the **1.20 mean / 1.00 worst-era gate lines**. Empty ≠ 0-width green bar. |
| Calibration reliability | `panels/calibrationCharts.js` | Hit-rate bars: confirm `hit_rate` is 0–1 before `* 100` width. Gate at 50% must sit at the true 0.5, not at 50px guess. |
| Portfolio correlation heatmap | `panels/portfolioRisk.js` | `data-corr-bucket` colors match Figma `70:40` (already patched once — do not regress). Diagonal = 1.00. Missing pair = `—`, not `0`. |
| Weight vs risk | `panels/portfolioRisk.js` | Weight % and risk-contribution % share one scale; tooltip units labeled |
| Stress / MC / tail | `panels/portfolioRisk.js` | Charts must not imply dollar loss from a percent field or vice versa |
| Scan funnel / dropoff | `panels/scanDiagnostics.js` | Bar width = count / max(count), **not** a percent of universe unless labeled that way. Object values must not `JSON.stringify` into the funnel. |
| Sharadar sparklines | `panels/sharadarFundamentals.js` | Y-axis unit matches the series (ROE points vs dollars) |
| Performance series | `panels/performance.js` | Value formatters match axis units |
| Health gauges | `panels/healthRibbon.js` | `vGauge` clamp is display-only; do not show 55% “health” from a 0–1 error rate without labeling |
| Backtest hub PF histogram | `panels/backtestHub.js` | Height `n/2.4*100` is another magic scale — label the axis or use a real max |

### Diagram rules

1. **One unit per axis.** Label it (`PF`, `%`, `$`).
2. **Gate marks** are data-driven (1.20, 0.50, etc.), not CSS guesses.
3. **No chart library chrome** that looks broken: 0×0 canvases, “Chart library unavailable.” as a raw string in a hole — use the same empty/error card as other panels.
4. **Theme** via `modules/chartThemeAdapters.js` / `YourThemeConfig.js` only.
5. **Do not draw a series from the wrong key.** If `drawdown_pct` is missing, say so — do not plot `equity` on the drawdown chart.

---

## 7. Workstream C — Logic gaps (function, not chrome)

Walk every primary control on every tab. If it does not do what the label says, fix the wiring or remove the control.

Checklist (not exhaustive — hunt more):

- Scan → detail → stage → approve still works after render changes.
- Research Brief vs Deep: ticker typed in one place flows; no second empty ticker box.
- Parked Research sections (`sectors`, `movers`) still open via `?section=` without punching a hole in Portfolio.
- Backtest: queue, cancel queued-only, click-to-load briefing, live monitor. `#btResult` must not be a JSON `<pre>`.
- System: health retry, reconnect, calibration empty/error, review-loop retry. Kill-switch / `data_quality` stay loud.
- Intel: conviction / GARCH / options tables — empty book is a designed empty state, not a blank table.
- Settings merged into System: Connect / presets / 2FA — no “type ENABLE” as the visual hero; the confirm control can still require the word, but the page copy is prose.
- Approve dialog: replace “Technical checklist” JSON with a human checklist (pass / fail / n/a rows).
- Trade drawer: Decision + Recovery outputs become cards, not `<pre>`.
- Command palette still jumps to frozen aliases.
- Keyboard Ctrl/Cmd+1–5 unchanged.
- `refreshAll()` still scoped to the visible screen.

**Copy:** use `modules/humanize.js`, `modules/decisionPlainLanguage.js`, `modules/glossary.js`. Snake_case keys become labels. `formatDiagnosticValue` in `scanDiagnostics.js` must stop `JSON.stringify` of objects — flatten to chips.

---

## 8. Workstream D — No code, no raw JSON, no engineer UI

### Kill list (operator-visible dumps)

Replace each with a **typed view** (KPI tiles, definition lists, tables). Do not move them behind `<details>` and call it done. Pro mode does not get a JSON escape hatch on these surfaces.

| DOM / module | Today | Replace with |
|--------------|-------|----------------|
| `#checkOutput` / `panels/quickCheck.js` | `prettyJson(data)` | Brief card (verdict, p(up), gates, next action) |
| `#learningRaw` | raw `<pre>` | Learning review cards (already partly in cockpit — finish it) |
| `#onboardingOutput` / `onboarding.js` | `prettyJson` | Step list with status pills |
| `#profileRaw` / `profile.js` | `prettyJson` | Settings summary; drop `<code class="preset-value">` even in expert UI |
| `#performanceRaw` / `performance.js` | `prettyJson` | Metric tiles + table |
| `#btResult` / `backtest.js` | `prettyJson(result)` + auto-open in Pro | Briefing already exists on the hub — **delete** the raw result block |
| `#dossierOutput` / `dossier.js` | `JSON.stringify` | Keep IR document; delete the sibling dump |
| `#reportOutput` / `report.js` | `JSON.stringify` | Keep writeup; delete the dump |
| `#tradeDrawerDecisionOutput` / `#tradeDrawerRecoveryOutput` | `prettyJson` | Decision / recovery cards |
| `panels/approveDialog.js` | Technical checklist `<pre>` | Pass/fail rows |
| `panels/reviewLoop.js` | “Raw report” `<details>` | Already have tables — delete the dump |
| `panels/calibration.js` | `calibration-raw-details` JSON | Keep tiles/bars; delete raw wrap |
| `panels/backtestHub.js` | `.bt-related-json` slice(0,4000) | Related-evidence **cards** (“Not generated yet.” is fine) |
| `panels/strategyChat.js` | toolResults `<pre>` + `#scTaskIdCopy` | Human tool summary; task id only in a copy-tooltip if needed |
| `panels/portfolioBook.js` | `JSON.stringify(n.template)` | Structured journal fields |
| `panels/peadCanary.js` | “Enable … in `<code>.env</code>`” | “Paper sleeve is off. Turn on PEAD shadow in System → Connect.” |
| Settings copy in `index.html` | `` <code>$300/$500/$900</code> ``, `` <code>ENABLE</code> `` | Prose: “Conservative caps a position at $300…” |
| `scanDiagnostics.js` `formatDiagnosticValue` | `JSON.stringify(object)` | Labeled chips |

`prettyJson` may remain in `format.js` for tests or non-DOM use. **Zero** operator-visible calls.

Errors: never `JSON.stringify(out.error)`. Use `out.user_message` or a short fallback (“Request failed — retry”).

---

## 9. Workstream E — Visual uplift (professional, no gaps)

Goal: a **buy-side research terminal**, not a lab notebook. Calm editorial. Dense but not cramped.

### Visual rules

1. **No vacant tracks.** If a sibling is `research-tab-hidden` or `hidden`, the visible card is full width. Hunt `layout-2`, empty `#*WorkspaceIntro` (already hidden — keep them hidden, do not restyle them into holes).
2. **No title-only cards.** Every `.card` has a body: content, empty state, or skeleton (`.ol-skeleton`).
3. **Consistent type scale** (already in `FRONTEND_DESIGN_SYSTEM.md`): body ≥16px, meta ≥13px, tables ≥14px. Numbers: `IBM Plex Mono` / `.mono-nums` **only** on numerals, not on sentences.
4. **One accent language.** Forest for go, gold for attention (not as small text on cream — AA), red for block. No new colors.
5. **KPI tiles** match Calibration / decision-gate pattern: label, value, one-line basis. No orphan numbers.
6. **Spacing:** 8px grid. If you see a “mysterious” 40–80px cream gap, it is a bug (hidden sibling, min-height on empty chart, collapsed details, spacer column). Close it.
7. **Charts** sit on `ol/surface` with a thin `ol/accent` border; reserve min-height only when a canvas is actually mounting.
8. **Empty / error / loading** use the same card chrome as success (status strip + snapshot), not a raw `<p class="muted">`.
9. **No emoji spam** in new copy. Existing 🧠 in cockpit learning — replace with a text label if you touch that block.
10. **Scan table:** leftover width absorbed by Ticker; Actions flush right. This is a hard invariant.
11. **Do not** add hero banners, marketing headlines, or a second top nav.

### Screen jobs (keep IA; polish presentation)

| Tab | Mode | Operator job |
|-----|------|----------------|
| Today | `operations` | Scan → review → approve |
| Research | `research` | Portfolio book + one-ticker check |
| Intel | `intel` | Conviction / vol / options on **held** names |
| System | `diagnostics` | Can I trade? What is broken? |
| Backtest | `backtest` | Queue, briefing, evidence — not a JSON lab |

### Responsive

Desktop first (1280 / 1440). Do not break the 8182 operator layout. If you touch layout CSS, check a ~390px width so cards stack instead of overflowing — no new mobile IA.

---

## 10. Implementation order (do not boil the ocean in one unstructured diff)

Do this as **one coherent campaign**, but land in this order so numbers are trusted before paint:

1. **Inventory pass (read-only, short):** list every `prettyJson` / `code-block` / local `fmtPct` / chart host. Do not start styling yet.
2. **Units + charts + dumps on Today** (`scanSection`, `scanDetailPanel`, `pendingSection`, approve dialog).
3. **Research** (Portfolio / Risk / Book / Quick check / dossier / report / pricing / Sharadar / event risk). Delete `#checkOutput`, `#dossierOutput`, `#reportOutput` dumps.
4. **Intel** (percent fractions + empty book).
5. **System** (decision PF chart scale, calibration reliability, review loop, health, onboarding/profile dumps).
6. **Backtest** (delete `#btResult` JSON; fix equity/drawdown/PF histogram scales; related-evidence cards).
7. **Global chrome** (trade drawer, command palette labels, settings prose).
8. **Visual gap sweep** last: walk each `?screen=` at 1440px and close holes.

If a panel is too large, finish its **correctness + dump removal** before its visual polish.

---

## 11. Verification (mandatory)

Start `python scripts/start_local_dashboard.py` if needed. Use the browser on **https://127.0.0.1:8182/**.

Walk:

1. `?screen=operations` — scan table (8 columns, no spacer), detail chart, pending cards. Approve dialog: no JSON.
2. `?screen=research` — Portfolio, Risk heatmap, Book, Quick check Brief + Deep. No vacant column. No `#checkOutput` JSON.
3. `?screen=intel` — empty and populated.
4. `?screen=diagnostics` — health, decision PF chart + gate labels, calibration, review loop. No raw wraps.
5. `?screen=backtest` — briefing, charts, related evidence. `#btResult` gone or not a `<pre>`.
6. Deep links: `?section=scan`, `pending`, `portfolio`, `calibration`, `leaderboard` still work.
7. Simple vs Pro display mode: Pro must **not** reintroduce JSON.

For each screen, capture Success and Empty (Error if you can trigger it). Hunt leftover `{}`, `null`, `undefined`, `true`, snake_case, and cream holes.

Tests: JS unit tests for formatters + any router/table contracts you touch. Do not run a full backtest to “verify UI”.

---

## 12. What to report when done

1. **Units table** — field, producer, unit, helper used (the important part).
2. **Dumps removed** — list of DOM ids / modules.
3. **Charts fixed** — what was wrong (scale, key, empty).
4. **Gaps closed** — layout holes and logic holes.
5. **Files changed**
6. **Commands + results**
7. **Risks / follow-ups** (any field still `—` because the producer is ambiguous)

Do not claim “looks professional” without the units table. Pretty and wrong is a fail.
