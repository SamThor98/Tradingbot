/**
 * Shared helpers for Supabase access JWTs (browser + classic script bundles).
 * Loaded before app.js / login.js / simple.js.
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
    "That value is not a Supabase access token. It must be one long string with two dots (three parts). Sign in with email/password, or paste the access token—not the refresh token or anon key.";

  w.TradingBotAuthJwt = {
    normalizeUserJwt,
    isProbablyAccessJwt,
    JWT_BAD_SHAPE_HINT,
  };
})(typeof window !== "undefined" ? window : globalThis);
