/**
 * Schwab onboarding wizard panel.
 *
 * Surfaces the five-step "connect account → connect market → verify
 * tokens → test scan → paper order" sequence as a horizontal stepper
 * with a single "Connect Schwab" CTA and a card grid of past attempts.
 * Status is stored on
 * `state.onboarding`; `refreshOnboarding` pulls fresh data from
 * `/api/onboarding/status` and renders the connection meta line, the
 * stepper, the CTA, and the retrospective cards.
 *
 * `startOnboarding`, `runOnboardingStep`, and the new
 * `triggerSchwabOAuth` helper accept an injected `runLazyApi` so the
 * panel section is gated by the same lazy-load machinery in `app.js`.
 */

import { state } from "../modules/state.js";
import { api } from "../modules/api.js";
import { createCookieAuthSession, ensureCookieAuthSession, getSupabaseClient } from "../modules/auth.js";
import { safeText, prettyJson } from "../modules/format.js";
import { logEvent, updateActionCenter } from "../modules/logger.js";
import { showToast } from "../modules/notifications.js";

const STEP_NAMES = {
  connect: "Link Schwab",
  verify_token_health: "Verify Tokens",
  test_scan: "Test Scan",
  test_paper_order: "Paper Order",
};
const STEP_DESCS = {
  connect: "Token files exist for market & account sessions.",
  verify_token_health: "Live API check: market token, account token, and quote probe.",
  test_scan: "Run the signal scanner and confirm no fatal errors.",
  test_paper_order: "Shadow-mode order to verify execution path.",
};

/** Ordered list of steps shown in the visual stepper. */
const STEPPER_ORDER = [
  "account",
  "market",
  "verify_token_health",
  "test_scan",
  "test_paper_order",
];

function esc(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

async function copyTextToClipboard(text) {
  const value = String(text || "");
  if (!value) return false;
  if (navigator?.clipboard?.writeText) {
    await navigator.clipboard.writeText(value);
    return true;
  }
  const ta = document.createElement("textarea");
  ta.value = value;
  ta.setAttribute("readonly", "");
  ta.style.position = "absolute";
  ta.style.left = "-9999px";
  document.body.appendChild(ta);
  ta.select();
  const ok = document.execCommand("copy");
  document.body.removeChild(ta);
  return ok;
}

function keyRowHtml(label, value) {
  const v = String(value || "");
  const disabled = v ? "" : "disabled";
  return `<div class="inline-form compact" style="margin-top:6px;">
    <label class="field-label">${esc(label)}</label>
    <input type="text" readonly value="${esc(v)}" />
    <button type="button" class="btn small secondary onboarding-copy-btn" data-copy-value="${esc(v)}" ${disabled}>Copy</button>
  </div>`;
}

function renderAuthBootstrapSection(portalConfig) {
  const cfg = state.publicConfig || {};
  const authSetup = cfg.auth_setup && typeof cfg.auth_setup === "object" ? cfg.auth_setup : {};
  const missing = [];
  if (!cfg.schwab_oauth) missing.push("SCHWAB_ACCOUNT_APP_KEY + SCHWAB_ACCOUNT_APP_SECRET");
  if (!cfg.schwab_market_oauth) missing.push("SCHWAB_MARKET_APP_KEY + SCHWAB_MARKET_APP_SECRET");
  if (cfg.saas_mode && authSetup.jwt_verification_ready === false) {
    missing.push("SUPABASE_URL and/or SUPABASE_JWT_SECRET");
  }
  if (cfg.saas_mode && authSetup.supabase_sign_in_available === false) {
    missing.push("SUPABASE_URL + SUPABASE_ANON_KEY");
  }

  const authPill = missing.length
    ? '<span class="pill bad small">Auth setup incomplete</span>'
    : '<span class="pill good small">Auth setup ready</span>';
  const missingList = missing.length
    ? `<ul class="onboarding-help-list muted" style="margin: 8px 0 0;">
        ${missing.map((m) => `<li>${esc(m)}</li>`).join("")}
      </ul>`
    : '<p class="muted" style="margin:8px 0 0;">All required auth pieces are configured for this host.</p>';

  const supabaseUrl = cfg?.supabase?.url || "";
  const supabaseAnon = cfg?.supabase?.anon_key || "";
  const portal = portalConfig && typeof portalConfig === "object" ? portalConfig : {};

  const keyRows = [
    keyRowHtml("Supabase URL", supabaseUrl),
    keyRowHtml("Supabase anon key", supabaseAnon),
    keyRowHtml("Account callback URL", portal.account_callback_url || ""),
    keyRowHtml("Market callback URL", portal.market_callback_url || ""),
    keyRowHtml("Frontend return URL", portal.frontend_return_url || ""),
    keyRowHtml("Account authorize start URL", portal.account_authorize_start_url || ""),
    keyRowHtml("Market authorize start URL", portal.market_authorize_start_url || ""),
  ].join("");

  return `<section class="card" style="margin-bottom:10px;">
    <div class="section-title">
      <h3 style="margin:0;">Auth bootstrap</h3>
      ${authPill}
    </div>
    <p class="muted" style="margin: 6px 0 0;">
      Missing auth setup is listed first. Available keys/URLs are auto-populated below so you can copy them directly.
    </p>
    ${missingList}
    <details class="onboarding-help" style="margin-top:10px;">
      <summary>Auto-populated keys and OAuth URLs</summary>
      <div class="onboarding-help-body">
        ${keyRows}
      </div>
    </details>
  </section>`;
}

function wireOnboardingCopyButtons(rootEl) {
  if (!rootEl) return;
  const copyButtons = rootEl.querySelectorAll(".onboarding-copy-btn");
  copyButtons.forEach((btn) => {
    btn.addEventListener("click", async () => {
      const value = btn.getAttribute("data-copy-value") || "";
      if (!value) return;
      try {
        const ok = await copyTextToClipboard(value);
        if (ok) {
          showToast("Copied to clipboard.", "success", 2200);
        } else {
          showToast("Copy was blocked by this browser.", "warn", 2800);
        }
      } catch {
        showToast("Copy failed.", "error", 3200);
      }
    });
  });
}

const STEPPER_COPY = {
  account: {
    title: "Connect your Schwab brokerage account",
    desc: "We will open Schwab so you can approve account access.",
  },
  market: {
    title: "Connect Schwab market data",
    desc: "We will open Schwab so you can approve market data access.",
  },
  verify_token_health: {
    title: "Verify your tokens are live",
    desc: "Quick API probe to confirm both Schwab tokens accept requests right now.",
  },
  test_scan: {
    title: "Run a test scan",
    desc: "Scans the universe end-to-end and confirms no fatal errors.",
  },
  test_paper_order: {
    title: "Place a paper order",
    desc: "Shadow-mode order so we know the execution path is wired correctly.",
  },
  done: {
    title: "Setup complete — you're cleared to scan and trade.",
    desc: "Your Schwab connection is ready. Optional health checks remain below.",
  },
};

/** Maps a derived current step → the action that completes it. */
function actionForStep(step, deps) {
  const { runLazyApi, runStep, triggerAccountConnect, triggerMarketConnect } = deps;
  switch (step) {
    case "account":
      return triggerAccountConnect;
    case "market":
      return triggerMarketConnect;
    case "verify_token_health":
    case "test_scan":
    case "test_paper_order":
      return () => runStep(step, { runLazyApi });
    case "done":
      return () => runStep("verify_token_health", { runLazyApi });
    default:
      return null;
  }
}

/**
 * Decide which step the user should tackle next.
 *
 * Account and market OAuth completion are derived from token presence.
 * We treat "connected" as complete once both OAuth links are done; deeper
 * verify/test checks remain available as optional diagnostics.
 */
function deriveCurrentStep(data) {
  const ah = data?.api_health || {};
  if (!ah.account_token_ok) return "account";
  if (!ah.market_token_ok) return "market";
  return "done";
}

function stepStatus(step, data) {
  const ah = data?.api_health || {};
  const steps = data?.steps || {};
  if (step === "account") return ah.account_token_ok ? "done" : "pending";
  if (step === "market") return ah.market_token_ok ? "done" : "pending";
  const s = steps[step] || {};
  if (s.ok) return "done";
  if (s.at) return "failed";
  return "pending";
}

function renderStepper(data, currentStep) {
  const stepper = document.getElementById("onboardingStepper");
  if (!stepper) return;
  for (const step of STEPPER_ORDER) {
    const li = stepper.querySelector(`li[data-step="${step}"]`);
    if (!li) continue;
    const status = stepStatus(step, data);
    const isCurrent = step === currentStep;
    li.dataset.status = status;
    li.classList.toggle("current", isCurrent && currentStep !== "done");
    li.classList.toggle("done", status === "done");
    li.classList.toggle("failed", status === "failed");
    const label = li.querySelector(".step-state");
    if (label) {
      label.textContent =
        status === "done" ? "done" : status === "failed" ? "retry" : isCurrent ? "next" : "pending";
    }
  }
  // Mark "done" by adding `complete` to the whole stepper for styling.
  stepper.classList.toggle("complete", currentStep === "done");
}

function renderNextCta(data, currentStep, deps) {
  const titleEl = document.getElementById("onboardingNextTitle");
  const descEl = document.getElementById("onboardingNextDesc");
  const btn = document.getElementById("onboardingNextBtn");
  if (!titleEl || !descEl || !btn) return;
  const copy = STEPPER_COPY[currentStep] || STEPPER_COPY.account;
  titleEl.textContent = copy.title;
  descEl.textContent = copy.desc;
  btn.textContent = currentStep === "done" ? "Connected" : "Connect Schwab";

  const handler = actionForStep(currentStep, deps);
  // Replace listener (clone trick) so re-renders don't pile up listeners.
  const fresh = btn.cloneNode(true);
  btn.parentNode.replaceChild(fresh, btn);
  if (handler) {
    fresh.disabled = currentStep === "done";
    fresh.addEventListener("click", async (e) => {
      e.preventDefault();
      if (currentStep === "done") return;
      fresh.disabled = true;
      try {
        await handler();
      } finally {
        fresh.disabled = false;
      }
    });
  } else {
    fresh.disabled = true;
  }
}

export function renderOnboardingCards(data, { portalConfig = null } = {}) {
  const cards = document.getElementById("onboardingCards");
  const det = document.getElementById("onboardingJsonDetails");
  const pre = document.getElementById("onboardingOutput");
  if (!cards) return;
  if (!data) {
    cards.innerHTML = `${renderAuthBootstrapSection(portalConfig)}<p class="muted">Run the wizard or click individual steps above.</p>`;
    wireOnboardingCopyButtons(cards);
    if (det) det.classList.add("hidden");
    return;
  }
  const steps = data.steps || {};
  let html = `${renderAuthBootstrapSection(portalConfig)}<div style="display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: 10px;">`;
  for (const [key, label] of Object.entries(STEP_NAMES)) {
    const step = steps[key] || {};
    const ok = Boolean(step.ok);
    const borderColor = ok ? "rgba(52, 211, 153, 0.45)" : step.at ? "rgba(251, 113, 133, 0.45)" : "rgba(100, 116, 139, 0.35)";
    const bgColor = ok ? "rgba(6, 78, 59, 0.2)" : step.at ? "rgba(127, 29, 29, 0.15)" : "rgba(10, 16, 34, 0.6)";
    const statusPill = ok
      ? '<span class="pill good small">Pass</span>'
      : step.at ? '<span class="pill bad small">Fail</span>' : '<span class="pill neutral small">Not run</span>';
    const fixPath = step.fix_path ? `<p class="muted" style="font-size: 0.78rem; margin: 6px 0 0;">${safeText(step.fix_path)}</p>` : "";
    html += `<div style="border-radius: 12px; border: 1px solid ${borderColor}; background: ${bgColor}; padding: 12px;">
      <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px;">
        <strong style="font-size: 0.88rem;">${label}</strong>
        ${statusPill}
      </div>
      <p class="muted" style="font-size: 0.8rem; margin: 0;">${STEP_DESCS[key]}</p>
      ${fixPath}
    </div>`;
  }
  html += "</div>";
  const elapsed = data.elapsed_minutes;
  const done = data.completed_under_target;
  if (elapsed != null) {
    html += `<p class="muted" style="margin-top: 10px;">Elapsed: ${elapsed} min${done ? ' · <span class="pill good small">Under target</span>' : ""}</p>`;
  }
  cards.innerHTML = html;
  wireOnboardingCopyButtons(cards);
  if (det) det.classList.remove("hidden");
  if (pre) pre.textContent = prettyJson(data);
}

/**
 * Kick off the Schwab account OAuth flow. Exposed so the stepper CTA
 * and the legacy "Connect Schwab (account)" button share one path.
 */
function _flashOAuthError(title, message) {
  logEvent({ kind: "system", severity: "error", message });
  updateActionCenter({ title, message, severity: "error" });
  try { showToast(message, "error", 6000); } catch { /* ignore */ }
}

async function ensureSessionForSchwabConnect() {
  if (await ensureCookieAuthSession()) return true;

  const sb = getSupabaseClient();
  if (!sb) {
    _flashOAuthError(
      "Authentication required",
      "Email verification is required before connecting Schwab. Browser auth is not configured on this host.",
    );
    return false;
  }

  try {
    const {
      data: { session },
    } = await sb.auth.getSession();
    if (session?.access_token) {
      const ok = await createCookieAuthSession(session.access_token);
      if (ok && (await ensureCookieAuthSession())) return true;
    }
  } catch {
    // fall through to OTP flow
  }

  const email = window.prompt("Enter your email to verify before connecting Schwab:");
  const cleanEmail = String(email || "").trim();
  if (!cleanEmail) {
    showToast("Email verification is required before connecting Schwab.", "warn", 4200);
    return false;
  }
  const redirectTo = `${window.location.origin}/?section=connect`;
  const { error } = await sb.auth.signInWithOtp({
    email: cleanEmail,
    options: {
      shouldCreateUser: true,
      emailRedirectTo: redirectTo,
    },
  });
  if (error) {
    _flashOAuthError("Could not start email verification", error.message || "Verification request failed.");
    return false;
  }
  showToast("Verification email sent. Open the sign-in link, then click Connect Schwab again.", "info", 7000);
  updateActionCenter({
    title: "Email verification sent",
    message: "Open the link in your inbox to finish sign-in, then retry Connect Schwab.",
    severity: "warn",
  });
  return false;
}

export async function triggerSchwabAccountOAuth() {
  if (!(await ensureSessionForSchwabConnect())) return;
  if (!state.publicConfig?.schwab_oauth) {
    _flashOAuthError(
      "Schwab account OAuth not configured",
      "The server is missing SCHWAB_ACCOUNT_APP_KEY / SCHWAB_ACCOUNT_APP_SECRET. Set them in your hosting env and redeploy.",
    );
    return;
  }
  const redirectPath = "/api/oauth/schwab/start";
  try {
    // Redirect-first path avoids fragile fetch-only startup failures.
    window.location.assign(redirectPath);
  } catch {
    try {
      const out = await api.get("/api/oauth/schwab/authorize-url");
      if (!out.ok || !out.data?.url) {
        _flashOAuthError(
          "Could not start Schwab account OAuth",
          out.error || "The /api/oauth/schwab/authorize-url request failed. Check server logs.",
        );
        return;
      }
      window.location.href = out.data.url;
    } catch (err) {
      _flashOAuthError(
        "Could not start Schwab account OAuth",
        `OAuth start failed: ${err?.message || err || "unknown error"}`,
      );
    }
  }
}

export async function triggerSchwabMarketOAuth() {
  if (!(await ensureSessionForSchwabConnect())) return;
  if (!state.publicConfig?.schwab_market_oauth) {
    _flashOAuthError(
      "Schwab market OAuth not configured",
      "The server is missing SCHWAB_MARKET_APP_KEY / SCHWAB_MARKET_APP_SECRET. Set them in your hosting env and redeploy.",
    );
    return;
  }
  const redirectPath = "/api/oauth/schwab/market/start";
  try {
    // Redirect-first path avoids fragile fetch-only startup failures.
    window.location.assign(redirectPath);
  } catch {
    try {
      const out = await api.get("/api/oauth/schwab/market/authorize-url");
      if (!out.ok || !out.data?.url) {
        _flashOAuthError(
          "Could not start Schwab market OAuth",
          out.error || "The /api/oauth/schwab/market/authorize-url request failed. Check server logs.",
        );
        return;
      }
      window.location.href = out.data.url;
    } catch (err) {
      _flashOAuthError(
        "Could not start Schwab market OAuth",
        `OAuth start failed: ${err?.message || err || "unknown error"}`,
      );
    }
  }
}

export async function refreshOnboarding({ runLazyApi = async () => {} } = {}) {
  const meta = document.getElementById("onboardingMeta");
  if (meta) meta.textContent = "Loading onboarding status...";
  const out = await api.get("/api/onboarding/status");
  let portalConfig = null;
  try {
    const portalOut = await api.get("/api/oauth/schwab/portal-config");
    if (portalOut?.ok && portalOut?.data && typeof portalOut.data === "object") {
      portalConfig = portalOut.data;
    }
  } catch {
    portalConfig = null;
  }
  const section = document.getElementById("onboardingSection");
  if (!meta) return;
  if (!out.ok) {
    renderOnboardingCards(null, { portalConfig });
    renderStepper({}, "account");
    renderNextCta({}, "account", {
      runLazyApi,
      runStep: runOnboardingStep,
      triggerAccountConnect: triggerSchwabAccountOAuth,
      triggerMarketConnect: triggerSchwabMarketOAuth,
    });
    meta.textContent = `Onboarding status failed: ${out.user_message || out.error}`;
    return;
  }
  state.onboarding = out.data;
  if (section) section.style.display = "block";
  const conn = out.data?.connection_status || (out.data?.schwab_linked ? "connected" : "disconnected");
  const ah = out.data?.api_health || {};
  const apiLine = ah.schwab_linked
    ? `API: market ${ah.market_token_ok ? "ok" : "—"} · account ${ah.account_token_ok ? "ok" : "—"} · quotes ${ah.quote_ok ? "ok" : "—"}`
    : "API: connect Schwab to probe tokens and quotes.";
  const haltLine = state.publicConfig.platform_live_trading_kill_switch ? " · Global operator halt: ON" : "";
  meta.textContent = `Connection: ${conn} · ${apiLine}${haltLine}`;

  const currentStep = deriveCurrentStep(out.data);
  renderStepper(out.data, currentStep);
  renderNextCta(out.data, currentStep, {
    runLazyApi,
    runStep: runOnboardingStep,
    triggerAccountConnect: triggerSchwabAccountOAuth,
    triggerMarketConnect: triggerSchwabMarketOAuth,
  });
  renderOnboardingCards(out.data, { portalConfig });
}

export async function startOnboarding({ runLazyApi = async () => {} } = {}) {
  await runLazyApi("onboarding");
  const out = await api.post("/api/onboarding/start", {});
  if (!out.ok) {
    logEvent({ kind: "system", severity: "error", message: `Onboarding start failed: ${out.error}` });
    renderOnboardingCards(null);
    updateActionCenter({ title: "Schwab setup", message: out.error || "Could not start onboarding.", severity: "error" });
    return;
  }
  logEvent({ kind: "system", severity: "info", message: "Setup wizard started." });
  await refreshOnboarding({ runLazyApi });
}

export async function runOnboardingStep(step, { runLazyApi = async () => {} } = {}) {
  await runLazyApi("onboarding");
  const out = await api.post(`/api/onboarding/step/${step}`, {});
  if (!out.ok) {
    logEvent({ kind: "system", severity: "error", message: `Onboarding step failed: ${out.error}` });
    updateActionCenter({ title: "Schwab setup", message: out.error || `Step ${step} failed.`, severity: "error" });
    return;
  }
  logEvent({ kind: "system", severity: "info", message: `Onboarding step complete: ${step}.` });
  await refreshOnboarding({ runLazyApi });
}
