/**
 * Unit tests for webapp/static/modules/router.js alias map invariants.
 *
 * The Python contract tests (tests/test_static_router.py) already assert the
 * frozen alias set against wiki/frontend-route-contract.md; these checks
 * exercise the module in-process instead of via regex on the source.
 */
import test from "node:test";
import assert from "node:assert/strict";

import { SECTION_ALIASES } from "../../webapp/static/modules/router.js";

test("SECTION_ALIASES is a frozen object", () => {
  assert.equal(Object.isFrozen(SECTION_ALIASES), true);
});

test("alias keys are lowercase and values are non-empty element ids", () => {
  for (const [key, value] of Object.entries(SECTION_ALIASES)) {
    assert.equal(key, key.toLowerCase(), `alias key not lowercase: ${key}`);
    assert.equal(typeof value, "string");
    assert.ok(value.length > 0, `empty target for alias: ${key}`);
    assert.doesNotMatch(value, /\s/, `target id contains whitespace: ${value}`);
  }
});

test("core deep-link aliases resolve to their contract targets", () => {
  assert.equal(SECTION_ALIASES.backtest, "backtestSection");
  assert.equal(SECTION_ALIASES.pending, "pendingSection");
  assert.equal(SECTION_ALIASES.scan, "scanSection");
  assert.equal(SECTION_ALIASES.connect, "onboardingSection");
  assert.equal(SECTION_ALIASES.health, "healthRibbon");
  assert.equal(SECTION_ALIASES.sec, "secCompareSection");
});
