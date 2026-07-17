/**
 * Focused sign-in page: shares JWT localStorage key with the main dashboard (`tradingbot.jwt`).
 */
import { renderAuthState, wireManualJwtBlock } from "./modules/authPresentation.js";

const AUTH_TOKEN_KEY = "tradingbot.jwt";
const LEGACY_AUTH_TOKEN_KEYS = ["supabasetoken", "supabaseToken", "supabase_token"];
const SUPABASE_ESM = "https://esm.sh/@supabase/supabase-js@2.49.1";

/** Set by /static/auth-jwt-utils.js (loaded before this module). */
const AuthJwt = globalThis.TradingBotAuthJwt || {
  normalizeUserJwt(raw) {
    let t = String(raw ?? "").trim();
    if (/^bearer\s+/i.test(t)) t = t.replace(/^bearer\s+/i, "").trim();
    return t;
  },
  isProbablyAccessJwt() {
    return true;
  },
  JWT_BAD_SHAPE_HINT: "",
};

let supabaseClient = null;

function setMessage(text) {
  const el = document.getElementById("loginMessage");
  if (el) el.textContent = text || "";
}

function clearLegacyApiJwtKeys() {
  if (typeof AuthJwt.clearLegacyApiJwtKeys === "function") {
    AuthJwt.clearLegacyApiJwtKeys(localStorage, LEGACY_AUTH_TOKEN_KEYS);
    return;
  }
  LEGACY_AUTH_TOKEN_KEYS.forEach((key) => localStorage.removeItem(key));
}

function normalizeUserJwt(raw) {
  return AuthJwt.normalizeUserJwt(raw);
}

function readStoredApiJwt() {
  if (typeof AuthJwt.readStoredApiJwt === "function") {
    return AuthJwt.readStoredApiJwt({
      storage: localStorage,
      authTokenKey: AUTH_TOKEN_KEY,
      legacyAuthTokenKeys: LEGACY_AUTH_TOKEN_KEYS,
      normalizeUserJwt,
      isProbablyAccessJwt: AuthJwt.isProbablyAccessJwt,
      jwtBadShapeHint: AuthJwt.JWT_BAD_SHAPE_HINT,
    });
  }
  const n = normalizeUserJwt(localStorage.getItem(AUTH_TOKEN_KEY) || "");
  return n && AuthJwt.isProbablyAccessJwt(n) ? n : "";
}

function clearStoredApiJwt() {
  if (typeof AuthJwt.clearStoredApiJwt === "function") {
    AuthJwt.clearStoredApiJwt(localStorage, AUTH_TOKEN_KEY, LEGACY_AUTH_TOKEN_KEYS);
    return;
  }
  localStorage.removeItem(AUTH_TOKEN_KEY);
  clearLegacyApiJwtKeys();
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

async function createCookieSession(token) {
  const clean = normalizeUserJwt(token);
  if (!clean || !AuthJwt.isProbablyAccessJwt(clean)) return;
  try {
    await fetch("/api/auth/session", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "include",
      body: JSON.stringify({ access_token: clean }),
    });
  } catch (e) {
    console.warn("auth cookie set failed", e);
  }
}

async function clearCookieSession() {
  try {
    await fetch("/api/auth/session", {
      method: "DELETE",
      credentials: "include",
    });
  } catch (e) {
    console.warn("auth cookie clear failed", e);
  }
}

function persistJwt(session) {
  const at = normalizeUserJwt(session?.access_token ?? "");
  if (at && AuthJwt.isProbablyAccessJwt(at)) {
    localStorage.setItem(AUTH_TOKEN_KEY, at);
    clearLegacyApiJwtKeys();
    void createCookieSession(at);
    const inp = document.getElementById("loginJwt");
    if (inp) inp.value = "";
  }
}

function updateSbUi(session) {
  const out = document.getElementById("loginSbOut");
  const inn = document.getElementById("loginSbIn");
  const label = document.getElementById("loginSbLabel");
  if (!out || !inn) return;
  renderAuthState(
    { signedOutEl: out, signedInEl: inn, labelEl: label },
    {
      state: session?.user ? "signed-in" : "signed-out",
      email: session?.user ? session.user.email || session.user.id || "Signed in" : "",
    },
  );
  if (session?.user) setMessage("You are signed in. Continue to the dashboard.");
}

/** True when this page load is a Supabase email / magic-link / PKCE callback. */
function isAuthCallbackLanding() {
  const hash = String(window.location.hash || "").replace(/^#/, "");
  if (hash.includes("access_token=") || hash.includes("type=magiclink") || hash.includes("type=recovery")) {
    return true;
  }
  try {
    const params = new URLSearchParams(window.location.search || "");
    return params.has("code") || params.has("error_description");
  } catch {
    return false;
  }
}

function continueToDashboard() {
  const params = new URLSearchParams(window.location.search || "");
  if (!params.has("section")) params.set("section", "connect");
  // Drop one-time auth params; session is already persisted.
  params.delete("code");
  params.delete("error");
  params.delete("error_description");
  params.delete("error_code");
  const q = params.toString();
  window.location.replace(`/${q ? `?${q}` : ""}`);
}

async function initSupabase(url, anonKey) {
  let createClient;
  try {
    const mod = await import(SUPABASE_ESM);
    createClient = mod.createClient;
  } catch (e) {
    console.warn(e);
    setMessage("Could not load Supabase from CDN; paste a JWT below.");
    return;
  }
  supabaseClient = createClient(url, anonKey, {
    auth: { autoRefreshToken: true, persistSession: true, detectSessionInUrl: true },
  });
  const fromCallback = isAuthCallbackLanding();
  const {
    data: { session },
  } = await supabaseClient.auth.getSession();
  persistJwt(session);
  updateSbUi(session);
  if (fromCallback && session?.access_token) {
    setMessage("Signed in. Opening dashboard…");
    continueToDashboard();
    return;
  }
  supabaseClient.auth.onAuthStateChange((_e, next) => {
    persistJwt(next);
    updateSbUi(next);
    if (fromCallback && next?.access_token) {
      setMessage("Signed in. Opening dashboard…");
      continueToDashboard();
    }
  });

  document.getElementById("loginSbSignIn")?.addEventListener("click", async () => {
    const email = document.getElementById("loginSbEmail")?.value?.trim() || "";
    const password = document.getElementById("loginSbPass")?.value || "";
    if (!email || !password) {
      setMessage("Enter email and password.");
      return;
    }
    const { error } = await supabaseClient.auth.signInWithPassword({ email, password });
    if (error) setMessage(error.message);
    else setMessage("Signed in.");
  });
  document.getElementById("loginSbSignUp")?.addEventListener("click", async () => {
    const email = document.getElementById("loginSbEmail")?.value?.trim() || "";
    const password = document.getElementById("loginSbPass")?.value || "";
    if (!email || !password) {
      setMessage("Enter email and password to sign up.");
      return;
    }
    const { error } = await supabaseClient.auth.signUp({ email, password });
    if (error) setMessage(error.message);
    else setMessage("Check email if confirmation is required, then sign in.");
  });
  document.getElementById("loginSbReset")?.addEventListener("click", async () => {
    const email = document.getElementById("loginSbEmail")?.value?.trim() || "";
    if (!email) {
      setMessage("Enter your account email, then click Send reset email.");
      return;
    }
    const redirectTo = `${window.location.origin}/login`;
    const { error } = await supabaseClient.auth.resetPasswordForEmail(email, { redirectTo });
    if (error) setMessage(error.message);
    else setMessage("Password reset email sent. Check your inbox.");
  });
  document.getElementById("loginSbSignOut")?.addEventListener("click", async () => {
    await supabaseClient.auth.signOut();
    await clearCookieSession();
    clearStoredApiJwt();
    const inp = document.getElementById("loginJwt");
    if (inp) inp.value = "";
    setMessage("Signed out.");
  });
}

async function main() {
  const jwtInput = document.getElementById("loginJwt");
  const wrap = document.getElementById("loginSupabase");

  wireManualJwtBlock({
    input: jwtInput,
    saveBtn: document.getElementById("loginJwtSave"),
    copyBtn: document.getElementById("loginJwtCopy"),
    normalizeJwt: normalizeUserJwt,
    isProbablyJwt: AuthJwt.isProbablyAccessJwt,
    badShapeHint: AuthJwt.JWT_BAD_SHAPE_HINT,
    readStoredToken: readStoredApiJwt,
    saveToken: (token) => {
      localStorage.setItem(AUTH_TOKEN_KEY, token);
      clearLegacyApiJwtKeys();
      void createCookieSession(token);
    },
    clearToken: () => {
      void clearCookieSession();
      clearStoredApiJwt();
    },
    onMessage: (text) => setMessage(text),
  });

  try {
    const res = await fetch("/api/public-config", { headers: { Accept: "application/json" } });
    const body = res.ok ? await res.json() : {};
    const data = body?.data && typeof body.data === "object" ? body.data : {};
    const sb = data.supabase;
    if (sb?.url && sb?.anon_key) {
      wrap?.classList.remove("hidden");
      await initSupabase(sb.url, sb.anon_key);
    } else {
      wrap?.classList.add("hidden");
      setMessage("Paste a JWT and save, or use the dashboard if this host does not expose Supabase sign-in.");
    }
  } catch {
    setMessage("Could not load server config. You can still paste and save a JWT.");
  }
}

void main();
