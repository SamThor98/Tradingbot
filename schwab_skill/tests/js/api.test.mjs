/**
 * Unit tests for the in-flight GET deduplication in webapp/static/modules/api.js.
 *
 * api.js (via auth.js) touches document/localStorage/fetch at call time only,
 * so minimal global shims are installed before the dynamic import.
 */
import test from "node:test";
import assert from "node:assert/strict";

globalThis.localStorage = {
  getItem: () => null,
  setItem: () => {},
  removeItem: () => {},
};
globalThis.document = {
  getElementById: () => null,
};

let fetchCalls = [];
globalThis.fetch = async (path, options = {}) => {
  fetchCalls.push({ path, method: String(options.method || "GET").toUpperCase() });
  const seq = fetchCalls.length;
  return {
    ok: true,
    status: 200,
    text: async () => JSON.stringify({ ok: true, data: { seq, path } }),
  };
};

const { api } = await import("../../webapp/static/modules/api.js");

test.beforeEach(() => {
  fetchCalls = [];
});

function callsTo(path) {
  return fetchCalls.filter((c) => c.path === path);
}

test("concurrent identical GETs share one network request", async () => {
  const [a, b] = await Promise.all([api.get("/api/status"), api.get("/api/status")]);
  assert.equal(callsTo("/api/status").length, 1);
  assert.equal(a.ok, true);
  assert.deepEqual(a, b);
});

test("sequential GETs are not cached — the second call refetches", async () => {
  const first = await api.get("/api/status");
  const second = await api.get("/api/status");
  assert.equal(callsTo("/api/status").length, 2);
  assert.notEqual(first.data.seq, second.data.seq);
});

test("GETs to different paths or with different options are independent", async () => {
  await Promise.all([
    api.get("/api/status"),
    api.get("/api/pending-trades"),
    api.get("/api/status", { timeoutMs: 5000 }),
  ]);
  assert.equal(callsTo("/api/status").length, 2);
  assert.equal(callsTo("/api/pending-trades").length, 1);
});

test("auth header resolution does not issue side-channel requests", async () => {
  await api.get("/api/status");
  assert.deepEqual(
    fetchCalls.map((c) => c.path),
    ["/api/status"],
    "expected no extra fetches (e.g. /api/auth/session) per API call",
  );
});

test("POSTs are never deduplicated", async () => {
  await Promise.all([api.post("/api/scan", { a: 1 }), api.post("/api/scan", { a: 1 })]);
  assert.equal(fetchCalls.filter((c) => c.method === "POST").length, 2);
});
