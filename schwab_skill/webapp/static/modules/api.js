/**
 * Authenticated JSON-over-HTTP client used by every UI panel.
 *
 * Wraps `fetch` with:
 *  - 90s default timeout (overridable via options.timeoutMs)
 *  - per-request `X-Request-ID` header for log correlation
 *  - bearer JWT (from `auth.getApiAccessToken`) when present
 *  - `X-API-Key` from localStorage when the public-config requires it
 *  - same-origin credentials so cookie sessions work
 *  - normalized `{ ok, data, error, status? }` return shape
 *
 * Always returns a resolved object (no throws) so callers can do
 * `if (!out.ok) showError(out.error)` without try/catch boilerplate.
 */

import { state } from "./state.js";
import { getApiAccessToken } from "./auth.js";

function classifyApiError(status, rawError) {
  const msg = String(rawError || "").trim();
  if (status === 401) {
    return {
      userMessage: "Authentication required. Sign in again and retry.",
      hint: "Session may be missing or expired.",
      retryable: true,
    };
  }
  if (status === 403) {
    return {
      userMessage: "This action is blocked by policy or account permissions.",
      hint: msg || "Check account controls and feature flags.",
      retryable: false,
    };
  }
  if (status === 404) {
    return {
      userMessage: "Requested resource was not found.",
      hint: msg || "Endpoint or record may no longer exist.",
      retryable: false,
    };
  }
  if (status === 409) {
    return {
      userMessage: "Request conflicts with current account/runtime state.",
      hint: msg || "Complete required setup steps and retry.",
      retryable: true,
    };
  }
  if (status === 422) {
    return {
      userMessage: "Request payload is invalid.",
      hint: msg || "Check required fields and value formats.",
      retryable: false,
    };
  }
  if (status === 429) {
    return {
      userMessage: "Rate limit hit. Wait briefly before retrying.",
      hint: msg || "Too many requests in a short window.",
      retryable: true,
    };
  }
  if (status >= 500) {
    return {
      userMessage: "Server error. Retry in a moment.",
      hint: msg || "Backend is temporarily unavailable.",
      retryable: true,
    };
  }
  if (msg) {
    return {
      userMessage: msg,
      hint: "",
      retryable: true,
    };
  }
  return {
    userMessage: "Request failed.",
    hint: "",
    retryable: true,
  };
}

export const api = {
  async request(path, options = {}) {
    const timeoutMs = Number(options.timeoutMs || 90000);
    const fetchOptions = { ...options };
    delete fetchOptions.timeoutMs;
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), timeoutMs);
    const headers = {
      "Content-Type": "application/json",
      ...(fetchOptions.headers || {}),
    };
    if (!headers["X-Request-ID"]) {
      headers["X-Request-ID"] = `ui-${Date.now()}-${Math.random().toString(16).slice(2, 10)}`;
    }

    const token = await getApiAccessToken();
    if (token) headers.Authorization = `Bearer ${token}`;

    const apiKey = state.publicConfig?.api_key_required ? (localStorage.getItem("tradingbot.api_key") || "") : "";
    if (apiKey) headers["X-API-Key"] = apiKey;

    try {
      const res = await fetch(path, {
        ...fetchOptions,
        credentials: fetchOptions.credentials ?? "same-origin",
        headers,
        signal: controller.signal,
      });
      const text = await res.text();
      let data;
      try {
        data = text ? JSON.parse(text) : {};
      } catch {
        data = { ok: false, error: `Invalid JSON response (${res.status})` };
      }
      if (!res.ok) {
        const mapped = classifyApiError(
          res.status,
          data?.error || data?.detail || `HTTP ${res.status}`,
        );
        return {
          ok: false,
          error: data?.error || data?.detail || `HTTP ${res.status}`,
          user_message: mapped.userMessage,
          hint: mapped.hint,
          retryable: mapped.retryable,
          status: res.status,
          data: data?.data ?? null,
        };
      }
      return data;
    } catch (err) {
      if (err?.name === "AbortError") {
        return {
          ok: false,
          error: "Request timed out. Please retry.",
          user_message: "Request timed out. Please retry.",
          hint: "The server took too long to respond.",
          retryable: true,
        };
      }
      const msg = err?.message || "Request failed.";
      return {
        ok: false,
        error: msg,
        user_message: msg,
        hint: "",
        retryable: true,
      };
    } finally {
      clearTimeout(timeout);
    }
  },

  get(path, options = {}) {
    return this.request(path, { method: "GET", ...options });
  },

  post(path, body = {}, options = {}) {
    return this.request(path, { method: "POST", body: JSON.stringify(body), ...options });
  },

  patch(path, body = {}, options = {}) {
    return this.request(path, { method: "PATCH", body: JSON.stringify(body), ...options });
  },
};
