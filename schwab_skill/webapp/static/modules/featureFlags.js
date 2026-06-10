/**
 * Frontend-only UI feature flags for the phased redesign rollout.
 *
 * Resolution order (later wins):
 *   1. `FLAG_DEFAULTS` below (all redesign flags ship OFF),
 *   2. `localStorage["tradingbot.flags"]` — JSON object of overrides that
 *      persist across reloads (set via `setFlagOverride` or devtools),
 *   3. `?ff=flag_a,!flag_b` URL param — comma-separated session overrides;
 *      a leading `!` disables the flag. URL overrides are also persisted to
 *      localStorage so a tester can share one link and keep the state.
 *
 * Rollback contract: flipping a flag requires no deploy — clear the override
 * (`?ff=!flag` or `clearFlagOverrides()`) and reload. See the wiki page
 * [[section-migration-map]] for the rollout checkpoints each flag gates.
 *
 * No backend involvement by design (front-end-design skill: frontend-only).
 */

export const FLAGS_STORAGE_KEY = "tradingbot.flags";

/** Known flags and their shipped defaults. Unknown flag names are ignored. */
export const FLAG_DEFAULTS = Object.freeze({
  /** Unified ranked status feed replacing the action-center fan-out. */
  priority_feed: false,
  /** Slim Operations landing: sectors/movers demoted to collapsed disclosures. */
  ops_slim_default: false,
  /** Single auth/session presentation shared by topbar, onboarding, login. */
  unified_auth_block: false,
  /** Per-screen controller bootstrap (init/prime) instead of monolith wiring. */
  screen_controllers: false,
});

/** Resolved flag map for this session. Populated by `initFeatureFlags()`. */
let resolved = { ...FLAG_DEFAULTS };

function readStoredOverrides() {
  try {
    const raw = localStorage.getItem(FLAGS_STORAGE_KEY);
    if (!raw) return {};
    const parsed = JSON.parse(raw);
    return parsed && typeof parsed === "object" ? parsed : {};
  } catch {
    return {};
  }
}

function writeStoredOverrides(overrides) {
  try {
    const keys = Object.keys(overrides);
    if (keys.length === 0) {
      localStorage.removeItem(FLAGS_STORAGE_KEY);
    } else {
      localStorage.setItem(FLAGS_STORAGE_KEY, JSON.stringify(overrides));
    }
  } catch {
    /* storage unavailable — session-only flags still work */
  }
}

/**
 * Parse a `?ff=` parameter value ("flag_a,!flag_b") into an override map.
 * Exported for unit testing.
 */
export function parseFlagParam(raw) {
  const out = {};
  String(raw || "")
    .split(",")
    .map((s) => s.trim().toLowerCase())
    .filter(Boolean)
    .forEach((token) => {
      const off = token.startsWith("!") || token.startsWith("-");
      const name = off ? token.slice(1) : token;
      if (Object.prototype.hasOwnProperty.call(FLAG_DEFAULTS, name)) {
        out[name] = !off;
      }
    });
  return out;
}

/**
 * Resolve flags from defaults + localStorage + URL. Call once during boot,
 * before any flag-gated wiring. Persists URL overrides and strips `ff` from
 * the address bar (replaceState, same pattern as the router deep links).
 */
export function initFeatureFlags() {
  const stored = readStoredOverrides();
  let fromUrl = {};
  try {
    const u = new URL(window.location.href);
    const rawParam = u.searchParams.get("ff");
    if (rawParam !== null) {
      fromUrl = parseFlagParam(rawParam);
      u.searchParams.delete("ff");
      const q = u.searchParams.toString();
      window.history.replaceState({}, "", `${u.pathname}${q ? `?${q}` : ""}${u.hash || ""}`);
    }
  } catch {
    /* URL parsing failed — defaults + stored still apply */
  }
  const merged = {};
  Object.keys(FLAG_DEFAULTS).forEach((name) => {
    if (Object.prototype.hasOwnProperty.call(fromUrl, name)) {
      merged[name] = Boolean(fromUrl[name]);
    } else if (Object.prototype.hasOwnProperty.call(stored, name)) {
      merged[name] = Boolean(stored[name]);
    }
  });
  if (Object.keys(fromUrl).length > 0) {
    writeStoredOverrides({ ...stored, ...fromUrl });
  }
  resolved = { ...FLAG_DEFAULTS, ...merged };
  try {
    // Expose body classes so CSS can gate layout without JS churn:
    // body.flag-priority_feed, body.flag-ops_slim_default, ...
    Object.entries(resolved).forEach(([name, on]) => {
      document.body.classList.toggle(`flag-${name}`, Boolean(on));
    });
  } catch {
    /* no DOM (tests) */
  }
  return { ...resolved };
}

/** True when the named flag is enabled this session. */
export function isFlagEnabled(name) {
  return Boolean(resolved[String(name || "").toLowerCase()]);
}

/** Persist a single override (devtools / future settings UI). */
export function setFlagOverride(name, value) {
  const key = String(name || "").toLowerCase();
  if (!Object.prototype.hasOwnProperty.call(FLAG_DEFAULTS, key)) return false;
  const stored = readStoredOverrides();
  stored[key] = Boolean(value);
  writeStoredOverrides(stored);
  resolved[key] = Boolean(value);
  try {
    document.body.classList.toggle(`flag-${key}`, Boolean(value));
  } catch {
    /* ignore */
  }
  return true;
}

/** Remove all persisted overrides (instant rollback to shipped defaults). */
export function clearFlagOverrides() {
  writeStoredOverrides({});
  resolved = { ...FLAG_DEFAULTS };
  try {
    Object.entries(resolved).forEach(([name, on]) => {
      document.body.classList.toggle(`flag-${name}`, Boolean(on));
    });
  } catch {
    /* ignore */
  }
}

/** Snapshot of the resolved flags (for debug panels / logging). */
export function getResolvedFlags() {
  return { ...resolved };
}
