/**
 * Unit tests for webapp/static/modules/asyncState.js.
 *
 * setAsyncState / renderAsync only need setAttribute/innerHTML/querySelector,
 * so a tiny fake element stands in for the DOM.
 */
import test from "node:test";
import assert from "node:assert/strict";

import {
  buildOperatorAlertHtml,
  setAsyncState,
  renderAsync,
  retryGet,
  busyButton,
  ASYNC_LOADING,
  ASYNC_EMPTY,
  ASYNC_ERROR,
  ASYNC_SUCCESS,
  ASYNC_SIGNED_OUT,
} from "../../webapp/static/modules/asyncState.js";

function fakeEl() {
  return {
    attrs: {},
    innerHTML: "",
    listeners: [],
    setAttribute(k, v) {
      this.attrs[k] = v;
    },
    getAttribute(k) {
      return Object.prototype.hasOwnProperty.call(this.attrs, k) ? this.attrs[k] : null;
    },
    querySelector() {
      return null;
    },
  };
}

test("buildOperatorAlertHtml renders error tone with alert semantics", () => {
  const html = buildOperatorAlertHtml({ headline: "Down", detail: "Server unreachable", retry: true });
  assert.match(html, /operator-alert--bad/);
  assert.match(html, /role="alert"/);
  assert.match(html, /aria-live="assertive"/);
  assert.match(html, /data-async-retry/);
  assert.match(html, /Down/);
});

test("buildOperatorAlertHtml renders neutral tone with status semantics", () => {
  const html = buildOperatorAlertHtml({ tone: "neutral" });
  assert.match(html, /operator-alert--neutral/);
  assert.match(html, /role="status"/);
  assert.match(html, /aria-live="polite"/);
  assert.doesNotMatch(html, /data-async-retry.*button/);
});

test("setAsyncState stamps data-async-state and default markup", () => {
  const el = fakeEl();
  setAsyncState(el, ASYNC_LOADING, { message: "Loading rows…" });
  assert.equal(el.attrs["data-async-state"], "loading");
  assert.match(el.innerHTML, /Loading rows…/);

  setAsyncState(el, ASYNC_EMPTY, { message: "Nothing yet" });
  assert.equal(el.attrs["data-async-state"], "empty");
  assert.match(el.innerHTML, /Nothing yet/);

  setAsyncState(el, ASYNC_ERROR, { message: "Boom" });
  assert.equal(el.attrs["data-async-state"], "error");
  assert.match(el.innerHTML, /Boom/);

  setAsyncState(el, ASYNC_SIGNED_OUT);
  assert.equal(el.attrs["data-async-state"], "signed_out");
  assert.match(el.innerHTML, /Sign in/);
});

test("setAsyncState falls back to loading for unknown states and keeps content on success", () => {
  const el = fakeEl();
  setAsyncState(el, "not-a-state");
  assert.equal(el.attrs["data-async-state"], "loading");

  const success = fakeEl();
  success.innerHTML = "<table>rows</table>";
  setAsyncState(success, ASYNC_SUCCESS);
  assert.equal(success.attrs["data-async-state"], "success");
  assert.equal(success.innerHTML, "<table>rows</table>");
});

test("renderAsync paints success after onSuccess and error on failed envelope", async () => {
  const el = fakeEl();
  let painted = null;
  await renderAsync(el, async () => ({ ok: true, data: [1, 2] }), {
    onSuccess: (env) => {
      painted = env.data;
      el.innerHTML = "rows";
    },
  });
  assert.deepEqual(painted, [1, 2]);
  assert.equal(el.attrs["data-async-state"], "success");

  const errEl = fakeEl();
  await renderAsync(errEl, async () => ({ ok: false, user_message: "Nope" }), { onSuccess: () => {} });
  assert.equal(errEl.attrs["data-async-state"], "error");
  assert.match(errEl.innerHTML, /Nope/);
});

test("renderAsync surfaces thrown work() and onSuccess() errors", async () => {
  const el = fakeEl();
  await renderAsync(el, async () => {
    throw new Error("fetch exploded");
  }, {});
  assert.equal(el.attrs["data-async-state"], "error");
  assert.match(el.innerHTML, /fetch exploded/);

  const el2 = fakeEl();
  await renderAsync(el2, async () => ({ ok: true }), {
    onSuccess: () => {
      throw new Error("render exploded");
    },
  });
  assert.equal(el2.attrs["data-async-state"], "error");
  assert.match(el2.innerHTML, /render exploded/);
});

test("retryGet refuses mutation-like function names", async () => {
  const out = await retryGet(async function approveTrade() {
    return { ok: true };
  });
  assert.equal(out.ok, false);
  assert.match(out.error, /refuses to retry/);
});

test("retryGet retries retryable failures then succeeds", async () => {
  let calls = 0;
  const out = await retryGet(
    async function fetchStatus() {
      calls += 1;
      if (calls < 3) return { ok: false, error: "flaky", retryable: true };
      return { ok: true, data: "done" };
    },
    { attempts: 3, baseDelayMs: 1, maxDelayMs: 2 },
  );
  assert.equal(out.ok, true);
  assert.equal(calls, 3);
});

test("retryGet stops immediately on non-retryable 4xx", async () => {
  let calls = 0;
  const out = await retryGet(
    async function fetchThing() {
      calls += 1;
      return { ok: false, status: 404, error: "missing" };
    },
    { attempts: 3, baseDelayMs: 1 },
  );
  assert.equal(out.ok, false);
  assert.equal(calls, 1);
});

test("busyButton disables, swaps label, and restores exactly once", () => {
  const btn = {
    dataset: {},
    disabled: false,
    textContent: "Approve",
    innerHTML: "Approve",
  };
  const release = busyButton(btn, "Working…");
  assert.equal(btn.disabled, true);
  assert.match(btn.innerHTML, /Working…/);
  // Second acquire while busy is refused (no-op release).
  const release2 = busyButton(btn);
  release2();
  assert.equal(btn.disabled, true);
  release();
  assert.equal(btn.disabled, false);
  assert.equal(btn.textContent, "Approve");
});
