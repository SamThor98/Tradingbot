/**
 * Shared browser auth: Supabase access JWT in localStorage + HttpOnly session cookie via /api/auth/session.
 * Depends on /static/auth-jwt-utils.js (TradingBotAuthJwt). Exposes window.TradingBotAuthClient.
 */
(function (w) {
  "use strict";

  const TOKEN_KEY = "tradingbot.jwt";
  const LEGACY_KEYS = ["supabasetoken", "supabaseToken", "supabase_token"];

  function jwtHelper() {
    return w.TradingBotAuthJwt || {
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
  }

  function normalizeUserJwt(raw) {
    return jwtHelper().normalizeUserJwt(raw);
  }

  function clearLegacyKeys() {
    LEGACY_KEYS.forEach((key) => {
      try {
        w.localStorage?.removeItem(key);
      } catch (_) {
        /* ignore */
      }
    });
  }

  function readStoredApiJwt() {
    const AuthJwt = jwtHelper();
    const accept = (raw) => {
      const n = normalizeUserJwt(raw);
      if (!n) return "";
      if (!AuthJwt.isProbablyAccessJwt(n)) {
        console.warn(AuthJwt.JWT_BAD_SHAPE_HINT);
        clearStoredApiJwt();
        return "";
      }
      return n;
    };
    let current = "";
    try {
      current = accept(w.localStorage?.getItem(TOKEN_KEY) || "");
    } catch (_) {
      return "";
    }
    if (current) return current;
    for (const key of LEGACY_KEYS) {
      let legacy = "";
      try {
        legacy = (w.localStorage?.getItem(key) || "").trim();
      } catch (_) {
        continue;
      }
      if (!legacy) continue;
      const migrated = accept(legacy);
      if (!migrated) continue;
      try {
        w.localStorage?.setItem(TOKEN_KEY, migrated);
      } catch (_) {
        /* ignore */
      }
      clearLegacyKeys();
      return migrated;
    }
    return "";
  }

  function clearStoredApiJwt() {
    try {
      w.localStorage?.removeItem(TOKEN_KEY);
    } catch (_) {
      /* ignore */
    }
    clearLegacyKeys();
  }

  async function clearCookieSession() {
    try {
      await fetch("/api/auth/session", {
        method: "DELETE",
        credentials: "include",
        headers: { Accept: "application/json" },
      });
    } catch (_) {
      /* ignore */
    }
  }

  async function createCookieSession(token) {
    const clean = normalizeUserJwt(token);
    const AuthJwt = jwtHelper();
    if (!clean || !AuthJwt.isProbablyAccessJwt(clean)) return false;
    try {
      const out = await fetch("/api/auth/session", {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify({ access_token: clean }),
      });
      return out.ok;
    } catch (_) {
      return false;
    }
  }

  /**
   * @returns {Promise<{ authenticated: boolean, invalidToken?: boolean, sub?: string, email?: string }>}
   */
  async function fetchCookieSessionStatus() {
    try {
      const out = await fetch("/api/auth/session", {
        method: "GET",
        credentials: "include",
        headers: { Accept: "application/json" },
      });
      if (!out.ok) return { authenticated: false };
      const body = await out.json();
      const data = body?.data && typeof body.data === "object" ? body.data : {};
      if (data.invalid_token) {
        await clearCookieSession();
        clearStoredApiJwt();
        return { authenticated: false, invalidToken: true };
      }
      return {
        authenticated: Boolean(data.authenticated),
        sub: data.sub,
        email: data.email,
      };
    } catch (_) {
      return { authenticated: false };
    }
  }

  async function hasCookieAuthSession() {
    const st = await fetchCookieSessionStatus();
    return Boolean(st.authenticated);
  }

  /**
   * Bearer JWT for Authorization header (empty when relying on HttpOnly cookie only).
   * @param {{ manualInputId?: string, supabaseClient?: object | null, authMode?: string }} opts
   */
  async function getBearerAccessToken(opts) {
    const manualInputId = opts?.manualInputId || "";
    const supabaseClient = opts?.supabaseClient ?? null;
    const authMode = opts?.authMode || "jwt";

    const manual = normalizeUserJwt(manualInputId ? w.document?.getElementById(manualInputId)?.value ?? "" : "");
    if (manual) {
      const AuthJwt = jwtHelper();
      if (!AuthJwt.isProbablyAccessJwt(manual)) {
        console.warn(AuthJwt.JWT_BAD_SHAPE_HINT);
        return "";
      }
      return manual;
    }
    const stored = readStoredApiJwt();
    if (stored) return stored;
    if (authMode === "supabase" && supabaseClient?.auth?.getSession) {
      const { data, error } = await supabaseClient.auth.getSession();
      if (error) console.warn("auth.getSession", error);
      const sessionToken = normalizeUserJwt(data?.session?.access_token ?? "");
      const AuthJwt = jwtHelper();
      if (sessionToken && AuthJwt.isProbablyAccessJwt(sessionToken)) return sessionToken;
    }
    return "";
  }

  function persistAccessToken(token, manualInputId) {
    const at = normalizeUserJwt(token);
    const AuthJwt = jwtHelper();
    if (!at || !AuthJwt.isProbablyAccessJwt(at)) return;
    try {
      w.localStorage?.setItem(TOKEN_KEY, at);
    } catch (_) {
      /* ignore */
    }
    clearLegacyKeys();
    void createCookieSession(at);
    if (manualInputId) {
      const inp = w.document?.getElementById(manualInputId);
      if (inp) inp.value = "";
    }
  }

  /**
   * Refresh Supabase access token when the SDK has a refresh token (keeps HttpOnly app cookie in sync).
   * @returns {Promise<string>} New access token or ""
   */
  async function refreshSessionFromSupabase(supabaseClient) {
    if (!supabaseClient?.auth?.refreshSession) return "";
    try {
      const { data, error } = await supabaseClient.auth.refreshSession();
      if (error) {
        console.warn("auth.refreshSession", error);
        return "";
      }
      const at = normalizeUserJwt(data?.session?.access_token ?? "");
      const AuthJwt = jwtHelper();
      if (!at || !AuthJwt.isProbablyAccessJwt(at)) return "";
      return at;
    } catch (e) {
      console.warn("auth.refreshSession failed", e);
      return "";
    }
  }

  w.TradingBotAuthClient = {
    TOKEN_KEY,
    LEGACY_KEYS,
    normalizeUserJwt,
    readStoredApiJwt,
    clearStoredApiJwt,
    clearLegacyKeys,
    createCookieSession,
    clearCookieSession,
    fetchCookieSessionStatus,
    hasCookieAuthSession,
    getBearerAccessToken,
    persistAccessToken,
    refreshSessionFromSupabase,
  };
})(typeof window !== "undefined" ? window : globalThis);
