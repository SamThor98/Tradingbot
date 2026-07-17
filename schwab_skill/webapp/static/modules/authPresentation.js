/**
 * Unified auth/session presentation (flag: unified_auth_block).
 *
 * One source of truth for how signed-out / verify-pending / signed-in states
 * look and behave across the three auth hosts:
 *   - topbar shelf (#supabaseAuthBlock, index.html),
 *   - onboarding inline block (#connectAuthInline, panels/onboarding.js),
 *   - the focused sign-in page (login.html / login.js).
 *
 * Design constraints:
 *   - Presentation only. Auth *logic* (token storage, cookie bridge, Supabase
 *     client lifecycle) stays in modules/auth.js / login.js; callers inject
 *     the primitives they already own. This module has zero imports so the
 *     standalone login page can use it without dragging in dashboard state.
 *   - The verify-email cooldown uses the SAME localStorage key the onboarding
 *     panel used before this module existed, so enabling/disabling the
 *     unified_auth_block flag never resets an in-flight cooldown.
 *
 * Rollout: call sites keep their legacy code paths and only delegate here
 * when the unified_auth_block flag is ON (see wiki [[section-migration-map]],
 * Checkpoint C removes the legacy paths).
 */

/* ── Verify-email cooldown (single source) ───────────────────────────── */

/** Same key the onboarding panel used pre-unification (continuity on flip). */
export const VERIFY_COOLDOWN_KEY = "tradingbot.connect.verify_email_cooldown_until_ms";
export const VERIFY_EMAIL_COOLDOWN_MS = 60 * 1000;
export const VERIFY_EMAIL_RATE_LIMIT_COOLDOWN_MS = 5 * 60 * 1000;

export function readVerifyCooldownUntil() {
  try {
    const n = Number(localStorage.getItem(VERIFY_COOLDOWN_KEY) || "");
    return Number.isFinite(n) && n > 0 ? Math.floor(n) : 0;
  } catch {
    return 0;
  }
}

function writeVerifyCooldownUntil(tsMs) {
  try {
    const safe = Number(tsMs);
    if (!Number.isFinite(safe) || safe <= Date.now()) {
      localStorage.removeItem(VERIFY_COOLDOWN_KEY);
    } else {
      localStorage.setItem(VERIFY_COOLDOWN_KEY, String(Math.floor(safe)));
    }
  } catch {
    /* storage unavailable — cooldown becomes session-best-effort */
  }
}

export function formatCooldownLabel(msLeft) {
  const totalSec = Math.max(1, Math.ceil(msLeft / 1000));
  if (totalSec < 60) return `${totalSec}s`;
  const min = Math.floor(totalSec / 60);
  const sec = totalSec % 60;
  return sec > 0 ? `${min}m ${sec}s` : `${min}m`;
}

/** Buttons registered for cooldown display: { btn, label } entries. */
const _cooldownButtons = new Set();
let _cooldownTimer = null;

function refreshCooldownButtons() {
  const until = readVerifyCooldownUntil();
  const msLeft = until - Date.now();
  _cooldownButtons.forEach((entry) => {
    const { btn, label } = entry;
    if (!btn || !btn.isConnected) return;
    const base = typeof label === "function" ? label() : String(label || "Verify email");
    if (msLeft > 0) {
      btn.disabled = true;
      btn.textContent = `${base} (${formatCooldownLabel(msLeft)})`;
    } else {
      btn.disabled = false;
      btn.textContent = base;
    }
  });
  if (msLeft <= 0 && _cooldownTimer) {
    clearInterval(_cooldownTimer);
    _cooldownTimer = null;
    writeVerifyCooldownUntil(0);
  }
}

function ensureCooldownTicker() {
  if (_cooldownTimer) return;
  if (readVerifyCooldownUntil() <= Date.now()) return;
  _cooldownTimer = window.setInterval(refreshCooldownButtons, 1000);
}

/**
 * Register a verify/sign-in button so its label and disabled state track the
 * shared cooldown. `label` may be a string or a function (re-evaluated each
 * tick so "Verify email" can flip to "Sign in" after first verification).
 */
export function attachVerifyCooldownButton(btn, { label = "Verify email" } = {}) {
  if (!btn) return;
  _cooldownButtons.add({ btn, label });
  refreshCooldownButtons();
  ensureCooldownTicker();
}

/** Start (or restart) the shared cooldown and refresh all registered buttons. */
export function startVerifyCooldown(ms) {
  writeVerifyCooldownUntil(Date.now() + Math.max(1000, Number(ms) || 0));
  refreshCooldownButtons();
  ensureCooldownTicker();
}

/* ── Shared verify-email (magic link / OTP) request ──────────────────── */

/**
 * One verify/sign-in email flow shared by every host. Handles the empty-email
 * guard, the cooldown gate, the Supabase signInWithOtp call, and rate-limit
 * detection. Returns { ok, message }; `onStatus(message)` (optional) receives
 * the same user-facing string for inline status surfaces.
 */
export async function requestVerificationEmail({
  supabase,
  email,
  redirectTo,
  verified = false,
  onStatus = null,
} = {}) {
  const say = (message) => {
    if (typeof onStatus === "function") onStatus(message);
    return message;
  };
  const cleanEmail = String(email || "").trim();
  if (!supabase) {
    return { ok: false, message: say("Browser auth is not configured on this host.") };
  }
  if (!cleanEmail) {
    return { ok: false, message: say("Enter your email first.") };
  }
  const until = readVerifyCooldownUntil();
  if (until > Date.now()) {
    return {
      ok: false,
      message: say(`Please wait ${formatCooldownLabel(until - Date.now())} before requesting another email.`),
    };
  }
  const { error } = await supabase.auth.signInWithOtp({
    email: cleanEmail,
    options: {
      shouldCreateUser: true,
      emailRedirectTo: redirectTo || `${window.location.origin}/login`,
    },
  });
  if (error) {
    const msg = error.message || "Verification request failed.";
    if (msg.toLowerCase().includes("rate limit")) {
      startVerifyCooldown(VERIFY_EMAIL_RATE_LIMIT_COOLDOWN_MS);
      return {
        ok: false,
        message: say(
          `Email rate limit reached. Please wait ${formatCooldownLabel(VERIFY_EMAIL_RATE_LIMIT_COOLDOWN_MS)} before retrying.`,
        ),
      };
    }
    return { ok: false, message: say(msg) };
  }
  startVerifyCooldown(VERIFY_EMAIL_COOLDOWN_MS);
  return {
    ok: true,
    message: say(
      verified
        ? "Sign-in link sent. Open your inbox and continue from the link."
        : "Verification email sent. Open your inbox and continue from the sign-in link.",
    ),
  };
}

/* ── Auth-state rendering (one renderer, three hosts) ─────────────────── */

/**
 * Render an auth host into one of three states. The host is a map of the
 * elements each surface already has (ids unchanged), so no DOM is recreated:
 *   { signedOutEl, signedInEl, labelEl, statusEl, verifyBtn }
 * `opts`: { state: "signed-in" | "signed-out" | "verify-pending",
 *           email, message, verified }
 */
export function renderAuthState(host = {}, opts = {}) {
  const { signedOutEl = null, signedInEl = null, labelEl = null, statusEl = null, verifyBtn = null } = host;
  const stateName = String(opts.state || "signed-out");
  const email = String(opts.email || "").trim();
  const verified = Boolean(opts.verified);

  const signedIn = stateName === "signed-in";
  if (signedOutEl) signedOutEl.classList.toggle("hidden", signedIn);
  if (signedInEl) signedInEl.classList.toggle("hidden", !signedIn);
  if (labelEl) labelEl.textContent = signedIn ? email || "Signed in" : "";
  if (verifyBtn && !signedIn) {
    // Returning verified users see "Sign in", not the confusing "Verify email".
    verifyBtn.textContent = verified ? "Sign in" : "Verify email";
  }
  if (statusEl) {
    const fallback = signedIn
      ? ""
      : stateName === "verify-pending"
        ? "Check your inbox and continue from the sign-in link."
        : verified
          ? "Your email is already verified. Sign in to reconnect."
          : "Verify once, then connect Schwab.";
    statusEl.textContent = String(opts.message ?? fallback);
  }
}

/* ── Manual JWT entry block (single wiring) ───────────────────────────── */

async function defaultCopyText(text) {
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

/**
 * Wire a manual JWT fallback block (input + save + copy) with one shared
 * implementation. Token storage/cookie logic is injected so the dashboard
 * and the login page keep their existing persistence semantics:
 *   { input, saveBtn, copyBtn, allowManual,
 *     normalizeJwt(raw), isProbablyJwt(token), badShapeHint,
 *     readStoredToken(), saveToken(token), clearToken(),
 *     onMessage(text, severity) }
 */
export function wireManualJwtBlock({
  input = null,
  saveBtn = null,
  copyBtn = null,
  allowManual = true,
  normalizeJwt = (raw) => String(raw ?? "").trim(),
  isProbablyJwt = () => true,
  badShapeHint = "",
  readStoredToken = () => "",
  saveToken = null,
  clearToken = null,
  onMessage = null,
} = {}) {
  const say = (text, severity = "info") => {
    if (typeof onMessage === "function") onMessage(text, severity);
  };
  if (input) {
    input.value = allowManual ? readStoredToken() : "";
    input.disabled = !allowManual;
  }
  if (saveBtn) {
    saveBtn.disabled = !allowManual;
    saveBtn.addEventListener("click", () => {
      if (!allowManual) return;
      const val = normalizeJwt(input?.value ?? "");
      if (val) {
        if (!isProbablyJwt(val)) {
          say(badShapeHint || "That does not look like an access token.", "error");
          return;
        }
        if (typeof saveToken === "function") saveToken(val);
        say("JWT token saved locally.", "info");
      } else {
        if (typeof clearToken === "function") clearToken();
        say("JWT token cleared.", "warn");
      }
    });
  }
  if (copyBtn) {
    copyBtn.disabled = !allowManual;
    copyBtn.addEventListener("click", async () => {
      if (!allowManual) return;
      const val = normalizeJwt(input?.value || readStoredToken());
      if (!val) {
        say("No token found to copy.", "warn");
        return;
      }
      try {
        const ok = await defaultCopyText(val);
        say(ok ? "Token copied." : "Copy blocked by browser.", ok ? "info" : "warn");
      } catch {
        say("Copy failed. Browser denied clipboard access.", "warn");
      }
    });
  }
}
