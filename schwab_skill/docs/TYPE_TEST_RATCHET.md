# Type/Test Ratchet

This runbook defines the no-regression quality ratchet for typing and contract
tests.

## Current Baseline

- Mypy baseline file: `.quality/mypy_baseline.json`
- Validation command: `python scripts/validate_typecheck_ratchet.py`
- Policy: mypy errors must stay at or below `max_errors` in baseline.

## Weekly Expansion Schedule

1. **Monday**: select 1-2 additional modules for type coverage expansion.
2. **Tuesday**: run mypy on candidate modules and fix low-friction issues.
3. **Wednesday**: add modules to `[tool.mypy].files` in `pyproject.toml`.
4. **Thursday**: extend contract tests for changed boundaries.
5. **Friday**: run `validate_all.py --profile local --strict`, update baseline
   only if approved by reviewer.

## Contract Test Focus Areas

- Scanner payload schema normalization (`tests/test_scan_payload.py`).
- Validation orchestration and gate ordering (`tests/test_validate_all_orchestration.py`).
- Release/promotion guard decisions (`tests/test_promotion_guard.py`).

## Baseline Update Rule

Changing `.quality/mypy_baseline.json` requires:

1. A written rationale in PR description.
2. Reviewer approval.
3. Follow-up issue to drive the baseline downward, not upward.
