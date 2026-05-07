/**
 * Async surface state machine for panels.
 *
 * Every fetch-driven UI surface in the dashboard goes through four states:
 *   loading → (success | empty | error)
 *
 * This module gives panels a single helper instead of each one re-inventing
 * the four state strings, the shape of the empty/error markup, and the
 * "show success but kept stale data while refetching" affordance.
 *
 * It also bundles a tiny retry-with-backoff for *idempotent* GETs only —
 * mutations stay one-shot and surface their error to the user.
 */

import { safeText } from "./format.js";

export const ASYNC_LOADING = "loading";
export const ASYNC_EMPTY = "empty";
export const ASYNC_ERROR = "error";
export const ASYNC_SUCCESS = "success";
export const ASYNC_STALE = "stale"; // success but a refetch is in flight
export const ASYNC_SIGNED_OUT = "signed_out"; // auth missing, not a "real" error

/**
 * Apply a state to a container element. Adds `data-async-state="..."` so CSS
 * can colour an outline / ribbon, and replaces inner content with the helper
 * markup the caller provided (or a sensible default).
 *
 * @param {HTMLElement|null} el
 * @param {string} stateName ASYNC_*
 * @param {object} opts
 * @param {string} [opts.message]  Human message for empty/error.
 * @param {string} [opts.html]     Raw HTML to render. Takes precedence over message.
 * @param {() => void} [opts.onRetry] When set on the error state, render a "Retry" button wired to this.
 * @param {string} [opts.signInHref] When set on the signed-out state, render a "Sign in" link.
 */
export function setAsyncState(el, stateName, opts = {}) {
  if (!el) return;
  const state = [
    ASYNC_LOADING,
    ASYNC_EMPTY,
    ASYNC_ERROR,
    ASYNC_SUCCESS,
    ASYNC_STALE,
    ASYNC_SIGNED_OUT,
  ].includes(stateName)
    ? stateName
    : ASYNC_LOADING;
  el.setAttribute("data-async-state", state);
  if (opts.html !== undefined) {
    el.innerHTML = opts.html;
    if (state === ASYNC_ERROR && typeof opts.onRetry === "function") {
      const retry = el.querySelector("[data-async-retry]");
      if (retry) retry.addEventListener("click", () => opts.onRetry());
    }
    return;
  }
  if (state === ASYNC_LOADING) {
    el.innerHTML = `<div class="async-state async-state--loading muted" role="status" aria-live="polite">
      <span class="async-spinner" aria-hidden="true"></span>
      <span>${safeText(opts.message || "Loading…")}</span>
    </div>`;
    return;
  }
  if (state === ASYNC_EMPTY) {
    el.innerHTML = `<div class="async-state async-state--empty muted">${safeText(opts.message || "No data yet.")}</div>`;
    return;
  }
  if (state === ASYNC_ERROR) {
    const reason = safeText(opts.message || "Request failed.");
    const retryHtml = typeof opts.onRetry === "function"
      ? `<button type="button" class="btn small secondary" data-async-retry>Retry</button>`
      : "";
    el.innerHTML = `<div class="async-state async-state--error" role="alert">
      <span>${reason}</span>
      ${retryHtml}
    </div>`;
    if (typeof opts.onRetry === "function") {
      const retry = el.querySelector("[data-async-retry]");
      if (retry) retry.addEventListener("click", () => opts.onRetry());
    }
    return;
  }
  if (state === ASYNC_SIGNED_OUT) {
    const reason = safeText(opts.message || "Sign in to load this data.");
    const href = safeText(opts.signInHref || "#supabaseAuthBlock");
    el.innerHTML = `<div class="signed-out-banner" role="status">
      <strong>Signed out.</strong>
      <span>${reason}</span>
      <a class="btn small secondary" href="${href}">Sign in</a>
    </div>`;
    return;
  }
  // success / stale: caller renders content; we just stamp the attribute.
}

/**
 * One-shot wrapper for a fetch-driven render: paints loading immediately,
 * awaits the work, then paints success/empty/error from the returned shape.
 *
 * `work()` should return:
 *   - `{ ok: true, data }`            → success (caller renders inside `onSuccess`)
 *   - `{ ok: true, data: <empty> }`   → caller may detect empty and call `setAsyncState(el, ASYNC_EMPTY)`
 *   - `{ ok: false, error|user_message, hint }` → error
 *
 * `onSuccess` receives the `{ ok, data, ... }` envelope and is responsible
 * for painting the actual rows. We stamp `data-async-state="success"` on the
 * container automatically *after* `onSuccess` returns without throwing.
 *
 * @param {HTMLElement|null} el
 * @param {() => Promise<object>} work
 * @param {object} cb
 * @param {(envelope: object) => (Promise<void>|void)} cb.onSuccess
 * @param {string} [cb.loadingMessage]
 * @param {() => void} [cb.onRetry]   Bound into the error state's Retry button.
 */
export async function renderAsync(el, work, cb = {}) {
  if (!el) return;
  setAsyncState(el, ASYNC_LOADING, { message: cb.loadingMessage });
  let envelope;
  try {
    envelope = await work();
  } catch (err) {
    setAsyncState(el, ASYNC_ERROR, {
      message: err?.message || "Unexpected error.",
      onRetry: cb.onRetry,
    });
    return;
  }
  if (!envelope || envelope.ok === false) {
    const msg =
      envelope?.user_message || envelope?.error || envelope?.hint || "Request failed.";
    setAsyncState(el, ASYNC_ERROR, {
      message: msg,
      onRetry: cb.onRetry,
    });
    return;
  }
  try {
    await cb.onSuccess?.(envelope);
    if (el.getAttribute("data-async-state") === ASYNC_LOADING) {
      el.setAttribute("data-async-state", ASYNC_SUCCESS);
    }
  } catch (err) {
    setAsyncState(el, ASYNC_ERROR, {
      message: err?.message || "Render failed.",
      onRetry: cb.onRetry,
    });
  }
}

/**
 * Mark a panel as "stale: success but refetching". Use this for
 * stale-while-revalidate flows so the user knows the visible numbers are not
 * the latest the server knows about.
 *
 * @param {HTMLElement|null} el
 * @param {string} note A small note like "refreshing — last updated 12s ago".
 */
export function markStaleRefreshing(el, note = "") {
  if (!el) return;
  el.setAttribute("data-async-state", ASYNC_STALE);
  if (note) el.setAttribute("data-async-note", note);
}

/**
 * Sleep helper used by `retryGet` (and exported because it's occasionally
 * useful in panels for debouncing animations).
 */
export function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/**
 * Retry an idempotent fetch with capped exponential backoff.
 *
 * **Use only for GETs / lookups.** Mutations (POST/PATCH/DELETE) must NOT be
 * retried automatically — the caller should surface the error and require an
 * explicit user click. We refuse to wrap functions whose name suggests a
 * mutation as a small guardrail.
 *
 * @param {() => Promise<{ok:boolean,error?:string,retryable?:boolean,status?:number,data?:any}>} fetcher
 * @param {object} [opts]
 * @param {number} [opts.attempts=3]      Total attempts (incl. first).
 * @param {number} [opts.baseDelayMs=400]
 * @param {number} [opts.maxDelayMs=4000]
 * @param {(attempt:number, envelope:object) => boolean} [opts.shouldRetry]
 *        Defaults to "retry on `retryable !== false` and not 4xx".
 */
export async function retryGet(fetcher, opts = {}) {
  if (typeof fetcher !== "function") {
    return { ok: false, error: "retryGet: fetcher must be a function." };
  }
  const fnName = String(fetcher.name || "").toLowerCase();
  if (/post|patch|delete|put|create|update|approve|cancel|enable|disable|halt|reject/.test(fnName)) {
    return {
      ok: false,
      error: `retryGet refuses to retry mutation-like function "${fetcher.name}". Use a one-shot call.`,
    };
  }

  const attempts = Math.max(1, Number(opts.attempts) || 3);
  const baseDelayMs = Math.max(0, Number(opts.baseDelayMs) || 400);
  const maxDelayMs = Math.max(baseDelayMs, Number(opts.maxDelayMs) || 4000);
  const shouldRetry =
    typeof opts.shouldRetry === "function"
      ? opts.shouldRetry
      : (_attempt, env) => {
          if (!env || env.ok === true) return false;
          if (env.retryable === false) return false;
          if (Number.isFinite(env.status) && env.status >= 400 && env.status < 500 && env.status !== 429) {
            return false;
          }
          return true;
        };

  let last;
  for (let i = 0; i < attempts; i += 1) {
    last = await fetcher();
    if (last && last.ok === true) return last;
    if (i === attempts - 1) break;
    if (!shouldRetry(i, last)) break;
    const exp = baseDelayMs * 2 ** i;
    const jitter = Math.random() * baseDelayMs;
    const wait = Math.min(maxDelayMs, exp + jitter);
    await sleep(wait);
  }
  return last || { ok: false, error: "retryGet: no response." };
}

/**
 * Make a primary action button "busy": disable, capture original label, swap
 * to a spinner+label, and return a release fn that restores the original
 * state when called from a `finally` block.
 *
 * Use this for approve/cancel/queue/save buttons so rage-clicks and keyboard
 * repeats can't double-fire mutations.
 *
 * @param {HTMLButtonElement|null} btn
 * @param {string} [busyLabel="Working…"]
 * @returns {() => void}
 */
export function busyButton(btn, busyLabel = "Working…") {
  if (!btn) return () => {};
  if (btn.dataset.asyncBusy === "true") {
    // already busy — refuse second fire
    return () => {};
  }
  btn.dataset.asyncBusy = "true";
  btn.dataset.asyncOriginalText = btn.textContent || "";
  btn.dataset.asyncOriginalDisabled = btn.disabled ? "true" : "false";
  btn.disabled = true;
  btn.innerHTML = `<span class="async-spinner" aria-hidden="true"></span><span>${safeText(busyLabel)}</span>`;
  return () => {
    if (btn.dataset.asyncBusy !== "true") return;
    btn.disabled = btn.dataset.asyncOriginalDisabled === "true";
    btn.textContent = btn.dataset.asyncOriginalText || "";
    delete btn.dataset.asyncBusy;
    delete btn.dataset.asyncOriginalText;
    delete btn.dataset.asyncOriginalDisabled;
  };
}
