# Guardrail Validation Matrix

This matrix defines concrete validation cases for Stage 2 + VCP scanning, advisory overlays, execution safety, and rollout gates.

It is intended to complement `VALIDATION_MATRIX.md` and act as a promotion gate checklist for guardrail changes.

## Severity and Decision Policy

- `P0` (critical): any failure blocks promotion and blocks deploy.
- `P1` (high): warn in `shadow`, block in `live`.
- `P2` (medium): track and triage; does not block by itself.

Minimum sample policy for performance/statistical claims:

- `n_closed_trades >= 100` globally.
- `n >= 20` per cohort bucket before interpreting expectancy differences.

## Validation Matrix

| Test ID | Severity | Guardrail Area | Scenario | Expected Behavior | Pass Criteria | Primary Command(s) |
|---|---|---|---|---|---|---|
| `GR-001` | P0 | Regime gate | SPY below 200 SMA and bear blocking enabled | Global scan block | `scan_blocked=1` and reason `bear_regime_spy_below_200sma` in diagnostics | `python scripts/validate_signal_quality.py` |
| `GR-002` | P0 | Regime gate | SPY above 200 SMA | Scan proceeds to Stage B | No global block reason; candidates pass to Stage B path | `python scripts/validate_signal_quality.py` |
| `GR-003` | P0 | Quality gates | `QUALITY_GATES_MODE=off` vs baseline fixture | Behavior preserving | Decision parity with baseline (allow/reject and scores unchanged) | `python scripts/validate_plugin_modes.py` |
| `GR-004` | P0 | Quality gates | `QUALITY_GATES_MODE=shadow` | Diagnostics-only influence | Decisions unchanged; shadow counters increment | `python scripts/validate_shadow_mode.py` |
| `GR-005` | P1 | Quality gates | `QUALITY_GATES_MODE=hard` on weak fixtures | Strict rejection | All weak-reason candidates filtered; reasons logged | `python scripts/validate_signal_quality.py` |
| `GR-006` | P0 | Quality gates exception | Weak breakout volume case | Always hard-blocked | Rejected in all modes per policy exception | `python scripts/validate_signal_quality.py` |
| `GR-007` | P0 | Plugin rollout | OFF -> SHADOW -> LIVE for each plugin | Correct rollout semantics | `off=no-op`, `shadow=diagnostics only`, `live=bounded behavior change` | `python scripts/validate_plugin_modes.py` |
| `GR-008` | P1 | Uncertainty controls | High disagreement / low confidence fixture | Downsize/suppress only within clamps | Size multiplier bounded by config; no positive oversizing in high uncertainty | `python scripts/validate_agent_intelligence.py` |
| `GR-009` | P0 | Execution safety | Shadow execution path exercised | No live broker submission | No live submit call; intent/simulation path only | `python scripts/validate_shadow_mode.py` |
| `GR-010` | P0 | Execution safety | Fill-confirmation path requiring stop attach | Stop safeguard intact | Stop order intent exists or failure is explicitly surfaced/diagnosed | `python scripts/validate_execution_quality.py` |
| `GR-011` | P0 | Risk caps | Over-cap position/sector/correlation fixture | Trade blocked or downsized | No cap violations in emitted order intents | `python scripts/validate_execution_quality.py` |
| `GR-012` | P1 | Event risk | Earnings/macro blackout windows | Guardrail action applied | Suppress/downsize aligns with configured mode | `python scripts/validate_event_risk.py` |
| `GR-013` | P0 | Data resilience | Schwab auth/429/timeout failure | Safe fallback behavior | Fallback reason logged, no crash, safe empty/alternative path | `python scripts/validate_observability_gates.py` |
| `GR-014` | P0 | Failure handling | Inject plugin/guardrail exception | Fail-safe operation | Scanner continues, diagnostics/log reason present, no unsafe forced-live action | `python scripts/validate_plugin_modes.py` |
| `GR-015` | P1 | Counterfactual logging | Rejected signals with logging enabled | Rejection evidence persisted | Counterfactual records include reason + horizon metadata | `python scripts/validate_hypothesis_chain.py` |
| `GR-016` | P1 | Advisory calibration | Probability buckets on scored outcomes | No calibration regression | Monotonic tendency or no-regression vs baseline calibration artifact | `python scripts/validate_advisory_model.py` |
| `GR-017` | P1 | Backtest robustness | Multi-era conservative-cost run | Edge not purely fragile | Net PF and DD deltas within threshold vs baseline | `python scripts/validate_pf_robustness.py` |
| `GR-021` | P1 | Hold-duration guardrail | Validate long-hold edge concentration from phase1 artifact | 21-40d expectancy must dominate 0-20d expectancy | Global delta and per-era pass-count thresholds met | `python scripts/validate_hold_duration_guardrail.py` |
| `GR-022` | P1 | Regime counterfactual guardrail | Validate SPY 50SMA+rising filter in chop/bear eras | Filter improves bear/chop expectancy with acceptable retention | bear/chop deltas and kept-trade floor pass thresholds | `python scripts/validate_regime_counterfactual_guardrail.py` |
| `GR-018` | P1 | Canary stability | 3-5 session shadow/live canary | Operationally stable canary | No exception spike, no observability guard breach | `python scripts/validate_observability_gates.py` |
| `GR-019` | P0 | Contract stability | UI/API payload checks post-change | Backward compatibility | Existing contracts pass; added fields optional | `python scripts/validate_ui_payloads.py` |
| `GR-020` | P0 | Promotion safety | Dry-run promotion flow | Explicit rationale and ledger policy enforced | Emit promote/no-promote rationale artifact; apply requires promotion ledger entry | `python scripts/validate_promotion_flow.py` |

## Thresholds (Suggested Starting Values)

Use these defaults unless strategy-specific runbooks override them:

- `hit_rate_delta_10d >= +0.01` for promotion.
- `brier_delta <= -0.005` (lower is better).
- `max_drawdown_delta <= +0.50%` absolute.
- `exceptions_delta <= +5%` relative.
- No single non-regime guardrail suppresses more than `40%` of candidates without explicit approval.

## Run Cadence

- PR/push quick lane:
  - `python scripts/validate_all.py --profile ci --skip-backtest --strict`
- Scheduled safety lane:
  - `python scripts/validate_all.py --profile ci --strict`
- Promotion lane:
  - `python scripts/validate_all.py --profile ci --promotion --strict`
  - `python scripts/validate_all.py --profile ci --pf-robust --strict`

## Artifact Contract

Every guardrail validation run should emit machine-readable evidence:

- Directory: `validation_artifacts/`
- Include per-test entries:
  - `test_id`
  - `severity`
  - `status`
  - `mode` (`off|shadow|live`)
  - `n_samples`
  - `metrics_before`
  - `metrics_after`
  - `decision`
  - `reason_codes`

Promotion decisions should reference both:

- this guardrail matrix, and
- the cross-environment matrix in `VALIDATION_MATRIX.md`.

## Ownership and Review

- Quant/strategy owner: reviews `GR-016` / `GR-017` weekly.
- Platform owner: reviews `GR-009` / `GR-013` / `GR-019` daily on active rollout weeks.
- Promotion approver: verifies `GR-001` to `GR-020` with no `P0` failures before enabling `--apply`.
