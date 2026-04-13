/**
 * Shared helpers for Supabase access JWTs (browser + classic script bundles).
 * Loaded before auth-client.js, app.js, login.js, and simple.js.
 */
(function (w) {
  "use strict";

  function normalizeUserJwt(raw) {
    let t = String(raw ?? "").trim();
    if (/^bearer\s+/i.test(t)) t = t.replace(/^bearer\s+/i, "").trim();
    return t;
  }

  function isProbablyAccessJwt(token) {
    if (!token || typeof token !== "string") return false;
    const parts = token.split(".");
    return parts.length === 3 && parts.every((p) => p.length > 0);
  }

  const JWT_BAD_SHAPE_HINT =
    "That value does not look like a sign-in token. Use email and password to sign in, or paste only the access token from Supabase (one long string with two dots)—not the anon key or a refresh token.";

  w.TradingBotAuthJwt = {
    normalizeUserJwt,
    isProbablyAccessJwt,
    JWT_BAD_SHAPE_HINT,
  };
})(typeof window !== "undefined" ? window : globalThis);
