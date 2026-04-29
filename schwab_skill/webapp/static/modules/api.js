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

  /**
   * Typed SEC analyzer fetch used by integrity scoring.
   * @param {string} ticker
   * @param {string} formType
   * @param {{timeoutMs?: number}} [options]
   */
  getSecAnalysis(ticker, formType = "10-K", options = {}) {
    const safeTicker = String(ticker || "").trim().toUpperCase();
    const safeForm = String(formType || "10-K").trim().toUpperCase();
    return this.get(`/api/sec/analyze/${encodeURIComponent(safeTicker)}?form_type=${encodeURIComponent(safeForm)}`, options);
  },

  /**
   * SEC compare wrapper for over-time/ticker-vs-ticker analysis.
   * @param {{mode?: string, ticker: string, tickerB?: string, formType?: string, highlightChangesOnly?: boolean, ruthlessMode?: boolean}} params
   * @param {{timeoutMs?: number}} [options]
   */
  getSecCompare(params = {}, options = {}) {
    const qs = new URLSearchParams();
    qs.set("mode", String(params.mode || "ticker_over_time").trim());
    qs.set("ticker", String(params.ticker || "").trim().toUpperCase());
    qs.set("form_type", String(params.formType || "10-K").trim().toUpperCase());
    if (params.tickerB) qs.set("ticker_b", String(params.tickerB).trim().toUpperCase());
    if (params.highlightChangesOnly) qs.set("highlight_changes_only", "true");
    if (params.ruthlessMode) qs.set("ruthless_mode", "true");
    return this.get(`/api/sec/compare?${qs.toString()}`, options);
  },

  /**
   * /report helper (section optional) for fundamentals fallback.
   * @param {string} ticker
   * @param {{section?: string, skipMirofish?: boolean, skipEdgar?: boolean}} [params]
   * @param {{timeoutMs?: number}} [options]
   */
  getReport(ticker, params = {}, options = {}) {
    const safeTicker = String(ticker || "").trim().toUpperCase();
    const qs = new URLSearchParams();
    if (params.section) qs.set("section", String(params.section).trim().toLowerCase());
    if (params.skipMirofish) qs.set("skip_mirofish", "true");
    if (params.skipEdgar) qs.set("skip_edgar", "true");
    const suffix = qs.toString() ? `?${qs.toString()}` : "";
    return this.get(`/api/report/${encodeURIComponent(safeTicker)}${suffix}`, options);
  },
};
