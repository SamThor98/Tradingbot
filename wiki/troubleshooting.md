---
source: Brain/Runbooks/Troubleshooting.md
created: 2026-04-13
updated: 2026-08-20
tags: [runbook, troubleshooting]
---

# Troubleshooting

> Common issues and recovery paths.

## Quick Diagnostics

| Check | How |
|-------|-----|
| Token health | `GET /api/health/deep` or `python healthcheck.py` |
| Scan status | `GET /api/scan/status` |
| Validation | `GET /api/validation/status` |

## Common Issues

### Schwab Auth Failures
Re-run `python run_dual_auth.py`. See [[schwab-oauth-setup]].

### Scan Blocked by Bear Regime
Wait for SPY > 200 SMA, or set `SCAN_ALLOW_BEAR_REGIME=true`. See [[signal-scanner]].

### Circuit Breaker Open
Symptom: scan or dashboard toasts `Schwab connection unstable (circuit breaker)` right after a scan or while finishing OAuth.

Cause: Stage A hits a Schwab read/connect timeout; older builds tripped the process-wide breaker on the **first** timeout and then **blocked OAuth code exchange** for five minutes.

Fix in current code: consecutive-failure threshold, one network retry, OAuth bypass + `record_success()` on a live token response. If you still see it: wait for cooldown (`SCHWAB_CIRCUIT_COOLDOWN_SECONDS`, default 5 min) or restart the local dashboard process, then complete market OAuth again. See [[guardrails]], [[schwab-auth]].

### No Signals Found
Review diagnostics (`stage2_fail`, `vcp_fail`). Consider loosening thresholds. See [[scanner-tunables]].

### Data Quality Degraded
Check Schwab API status, verify token freshness. See [[feature-flags]].

## Recovery Map

`GET /api/recovery/map?error=...&source=...` translates errors to actionable fixes.

## Related Pages

- [[local-ops-box]] — sleep, USB disks, and Wi-Fi-only networking on the mini PC
- [[schwab-oauth-setup]] — auth troubleshooting
- [[schwab-auth]] — dual-session OAuth and breaker recovery
- [[guardrails]] — circuit breaker threshold and cooldown
- [[webapp-dashboard]] — health endpoints

---

*Last compiled: 2026-08-20*
