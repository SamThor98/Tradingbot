/**
 * Unit tests for webapp/static/modules/router.js alias map invariants.
 *
 * The Python contract tests (tests/test_static_router.py) already assert the
 * frozen alias set against wiki/frontend-route-contract.md; these checks
 * exercise the module in-process instead of via regex on the source.
 */
import test from "node:test";
import assert from "node:assert/strict";

import { SECTION_ALIASES, applyQuerySectionDeepLink, isSupabaseAuthCallbackHash } from "../../webapp/static/modules/router.js";

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

test("isSupabaseAuthCallbackHash detects magic-link token hashes", () => {
  assert.equal(isSupabaseAuthCallbackHash(""), false);
  assert.equal(isSupabaseAuthCallbackHash("#onboardingSection"), false);
  assert.equal(
    isSupabaseAuthCallbackHash("#access_token=abc&refresh_token=def&type=magiclink"),
    true,
  );
});

test("handleRouteHash ignores Supabase auth callback hashes", async () => {
  const { handleRouteHash } = await import("../../webapp/static/modules/router.js");
  const original = globalThis.window;
  let scrolled = false;
  globalThis.window = {
    location: { hash: "#access_token=tok&type=magiclink" },
    document: {
      getElementById: () => {
        throw new Error("should not resolve auth hash as a section id");
      },
    },
    requestAnimationFrame: (cb) => cb(),
    dispatchEvent: () => {},
  };
  try {
    handleRouteHash();
    assert.equal(scrolled, false);
  } finally {
    globalThis.window = original;
  }
});

test("applyQuerySectionDeepLink preserves Supabase auth hash", () => {
  const original = globalThis.window;
  const replaceStateCalls = [];
  globalThis.window = {
    location: {
      href: "https://app.example/?section=connect#access_token=tok&type=magiclink",
      pathname: "/",
      hash: "#access_token=tok&type=magiclink",
    },
    history: {
      replaceState(_state, _title, url) {
        replaceStateCalls.push(url);
      },
    },
    document: {
      getElementById: (id) => (id === "onboardingSection" ? {} : null),
    },
  };
  try {
    const id = applyQuerySectionDeepLink();
    assert.equal(id, "");
    assert.equal(replaceStateCalls.length, 1);
    assert.match(replaceStateCalls[0], /#access_token=tok&type=magiclink$/);
    assert.doesNotMatch(replaceStateCalls[0], /#onboardingSection/);
  } finally {
    globalThis.window = original;
  }
});
