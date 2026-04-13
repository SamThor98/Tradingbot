/**
 * Sign-in page: email/password via Supabase when configured; optional advanced token paste.
 */
const SUPABASE_ESM = "https://esm.sh/@supabase/supabase-js@2.49.1";

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

const AuthClient = globalThis.TradingBotAuthClient || {
  readStoredApiJwt() {
    return "";
  },
  clearStoredApiJwt() {},
  createCookieSession() {
    return Promise.resolve(false);
  },
  clearCookieSession() {
    return Promise.resolve();
  },
  persistAccessToken() {},
};

let supabaseClient = null;

function setMessage(text) {
  const el = document.getElementById("loginMessage");
  if (el) el.textContent = text || "";
}

function normalizeUserJwt(raw) {
  return AuthJwt.normalizeUserJwt(raw);
}

function updateSbUi(session) {
  const out = document.getElementById("loginSbOut");
  const inn = document.getElementById("loginSbIn");
  const label = document.getElementById("loginSbLabel");
  if (!out || !inn) return;
  if (session?.user) {
    out.classList.add("hidden");
    inn.classList.remove("hidden");
    if (label) label.textContent = session.user.email || session.user.id || "Signed in";
    setMessage("You are signed in. Open the dashboard when you are ready.");
  } else {
    inn.classList.add("hidden");
    out.classList.remove("hidden");
    if (label) label.textContent = "";
  }
}

function persistJwt(session) {
  AuthClient.persistAccessToken(session?.access_token ?? "", "loginJwt");
}

async function initSupabase(url, anonKey) {
  let createClient;
  try {
    const mod = await import(SUPABASE_ESM);
    createClient = mod.createClient;
  } catch (e) {
    console.warn(e);
    setMessage("Could not load sign-in library. Use Advanced to paste a token, or try again later.");
    return;
  }
  supabaseClient = createClient(url, anonKey, {
    auth: { autoRefreshToken: true, persistSession: true, detectSessionInUrl: true },
  });
  const {
    data: { session },
  } = await supabaseClient.auth.getSession();
  persistJwt(session);
  updateSbUi(session);
  supabaseClient.auth.onAuthStateChange((_e, next) => {
    persistJwt(next);
    updateSbUi(next);
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
  document.getElementById("loginSbSignOut")?.addEventListener("click", async () => {
    await supabaseClient.auth.signOut();
    await AuthClient.clearCookieSession();
    AuthClient.clearStoredApiJwt();
    const inp = document.getElementById("loginJwt");
    if (inp) inp.value = "";
    setMessage("Signed out.");
  });
}

async function main() {
  const jwtInput = document.getElementById("loginJwt");
  const wrap = document.getElementById("loginSupabase");
  if (jwtInput) jwtInput.value = AuthClient.readStoredApiJwt();

  document.getElementById("loginJwtSave")?.addEventListener("click", () => {
    const val = normalizeUserJwt(jwtInput?.value ?? "");
    if (val) {
      if (!AuthJwt.isProbablyAccessJwt(val)) {
        setMessage(AuthJwt.JWT_BAD_SHAPE_HINT);
        return;
      }
      AuthClient.persistAccessToken(val, "loginJwt");
      setMessage("Token saved for this browser.");
    } else {
      void AuthClient.clearCookieSession();
      AuthClient.clearStoredApiJwt();
      setMessage("Cleared.");
    }
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
      setMessage("This host has no browser sign-in configured. Use Advanced to paste a token, or ask your admin to set SUPABASE_URL and SUPABASE_ANON_KEY.");
    }
  } catch {
    setMessage("Could not load server config. You can still use Advanced to paste a token.");
  }
}

void main();
