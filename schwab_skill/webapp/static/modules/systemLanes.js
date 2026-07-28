/**
 * System screen lane navigation — Ready / Decide / Calibrate / Shadow /
 * Review / Connect / Advanced. Keeps the command strip visible and shows
 * one lane body at a time (decision facelift IA).
 */

import { state } from "./state.js";
import { safeText } from "./format.js";

export const SYSTEM_LANES = Object.freeze([
  "ready",
  "decide",
  "calibrate",
  "shadow",
  "review",
  "connect",
  "advanced",
]);

/** Friendly ?section= aliases → lane id */
export const SYSTEM_LANE_ALIASES = Object.freeze({
  ready: "ready",
  decide: "decide",
  promote: "decide",
  decision: "decide",
  calibrate: "calibrate",
  calibration: "calibrate",
  shadow: "shadow",
  shadowscoreboard: "shadow",
  "shadow-workbench": "shadow",
  workbench: "shadow",
  review: "review",
  reviewloop: "review",
  connect: "connect",
  settings: "connect",
  onboarding: "connect",
  advanced: "advanced",
  status: "advanced",
  health: "ready",
});

/** Panels belonging to each lane (command strip / alert stay always-on). */
export const SYSTEM_LANE_PANELS = Object.freeze({
  ready: ["healthRibbon", "systemStatusCompact"],
  decide: ["systemDecisionPanel", "decisionDashboardCard"],
  calibrate: ["systemQualityDiagnostics", "calibrationSection"],
  shadow: ["systemQualityDiagnostics", "shadowScoreboardSection"],
  review: ["systemQualityDiagnostics", "reviewLoopSection"],
  connect: [
    "settingsSummaryLanding",
    "onboardingSection",
    "settingsSection",
    "settingsAccountPanel",
  ],
  advanced: ["statusDetailsPanel"],
});

const ALWAYS_ON = Object.freeze([
  "systemAlertBanner",
  "systemSummaryLanding",
  "systemLaneNav",
]);

export function normalizeSystemLane(raw) {
  const key = safeText(raw || "").toLowerCase();
  if (SYSTEM_LANES.includes(key)) return key;
  if (SYSTEM_LANE_ALIASES[key]) return SYSTEM_LANE_ALIASES[key];
  return "ready";
}

function setLaneHidden(el, hidden) {
  if (!el) return;
  el.classList.toggle("system-lane-hidden", hidden);
  el.setAttribute("aria-hidden", hidden ? "true" : "false");
  if (el.tagName === "DETAILS" && !hidden) {
    el.open = true;
  }
}

/**
 * Show panels for `lane`, hide other lane panels. Command strip stays visible.
 * @param {string} lane
 * @param {{ updateUrl?: boolean }} [opts]
 */
export function applySystemLane(lane, { updateUrl = false } = {}) {
  const next = normalizeSystemLane(lane);
  state.systemLane = next;
  document.body.setAttribute("data-system-lane", next);

  const showIds = new Set([...(SYSTEM_LANE_PANELS[next] || []), ...ALWAYS_ON]);
  const allLaneIds = new Set(ALWAYS_ON);
  for (const ids of Object.values(SYSTEM_LANE_PANELS)) {
    ids.forEach((id) => allLaneIds.add(id));
  }

  for (const id of allLaneIds) {
    const el = document.getElementById(id);
    if (!el) continue;
    if (ALWAYS_ON.includes(id)) {
      setLaneHidden(el, false);
      continue;
    }
    setLaneHidden(el, !showIds.has(id));
  }

  // Nested quality panels: only the active calibrate/shadow/review child shows.
  const qualityKids = [
    ["calibrationSection", "calibrate"],
    ["shadowScoreboardSection", "shadow"],
    ["reviewLoopSection", "review"],
  ];
  for (const [id, owner] of qualityKids) {
    const el = document.getElementById(id);
    if (!el) continue;
    if (["calibrate", "shadow", "review"].includes(next)) {
      setLaneHidden(el, next !== owner);
    }
  }

  const nav = document.getElementById("systemLaneNav");
  if (nav) {
    nav.querySelectorAll("[data-system-lane]").forEach((btn) => {
      const key = normalizeSystemLane(btn.getAttribute("data-system-lane"));
      const active = key === next;
      btn.classList.toggle("is-active", active);
      btn.setAttribute("aria-pressed", active ? "true" : "false");
    });
  }

  const qualityTitle = document.getElementById("systemQualityLaneTitle");
  const qualityHint = document.getElementById("systemQualityLaneHint");
  if (qualityTitle && qualityHint) {
    if (next === "calibrate") {
      qualityTitle.textContent = "Calibrate · learning & drift";
      qualityHint.textContent = "Self-study · hypothesis ledger · reliability";
    } else if (next === "shadow") {
      qualityTitle.textContent = "Shadow · plugin would-haves";
      qualityHint.textContent = "Evidence sessions · not live capital";
    } else if (next === "review") {
      qualityTitle.textContent = "Review · false positives & packets";
      qualityHint.textContent = "Weekly loop · tuning proposals";
    } else {
      qualityTitle.textContent = "Calibration & plugin quality";
      qualityHint.textContent = "Self-study, shadow scoreboard, trade review loop";
    }
  }

  // Soft-scroll active lane body into view (skip command strip).
  const focusId = (SYSTEM_LANE_PANELS[next] || [])[0];
  const focusEl = focusId ? document.getElementById(focusId) : null;
  if (focusEl && !focusEl.classList.contains("system-lane-hidden")) {
    try {
      focusEl.scrollIntoView({ behavior: "smooth", block: "nearest" });
    } catch {
      /* ignore */
    }
  }

  if (updateUrl && typeof window !== "undefined" && window.history?.replaceState) {
    try {
      const url = new URL(window.location.href);
      if (next === "ready") url.searchParams.delete("lane");
      else url.searchParams.set("lane", next);
      window.history.replaceState({}, "", `${url.pathname}${url.search}${url.hash}`);
    } catch {
      /* ignore */
    }
  }
}

/**
 * Build the command-strip "Next decision" card copy from health + blockers.
 * @param {{ blockers?: string[], authState?: string, quoteOk?: boolean|null, systemState?: string }} input
 */
export function buildSystemNextDecision(input = {}) {
  const blockers = Array.isArray(input.blockers) ? input.blockers : [];
  const auth = safeText(input.authState).toLowerCase();
  const systemState = safeText(input.systemState).toLowerCase();

  if (blockers.length) {
    const first = blockers[0];
    const needsConnect =
      /auth|schwab|reconnect|sign-in|token/i.test(first) ||
      auth === "disconnected" ||
      auth === "unverified";
    return {
      decision: "fix",
      title: needsConnect ? "Do not trade until Auth is green." : "Fix blockers before new risk.",
      why: first,
      ctaLabel: needsConnect ? "Open Connect" : "Open Ready",
      ctaHref: needsConnect ? "#settingsSummaryLanding" : "#healthRibbon",
      ctaLane: needsConnect ? "connect" : "ready",
    };
  }
  if (systemState === "unknown" || auth === "unknown" || auth === "") {
    return {
      decision: "hold",
      title: "Wait for health probes.",
      why: "No decision until Auth / Quotes / Validation resolve.",
      ctaLabel: "Health tiles",
      ctaHref: "#healthRibbon",
      ctaLane: "ready",
    };
  }
  return {
    decision: "hold",
    title: "Hold stack — allocator stays shadow.",
    why: "Trade readiness is green. Open Decide before flipping ALLOCATOR_MODE=live.",
    ctaLabel: "Open Decide",
    ctaHref: "#systemDecisionPanel",
    ctaLane: "decide",
  };
}

export function getSystemLaneFromUrl(search = "") {
  try {
    const params = new URLSearchParams(search || window.location.search || "");
    const lane = params.get("lane");
    if (lane) return normalizeSystemLane(lane);
    const section = safeText(params.get("section") || "").toLowerCase();
    if (section && SYSTEM_LANE_ALIASES[section]) return SYSTEM_LANE_ALIASES[section];
  } catch {
    /* ignore */
  }
  return "ready";
}

export function wireSystemLaneNav() {
  const nav = document.getElementById("systemLaneNav");
  if (!nav || nav.dataset.laneBound === "1") return;
  nav.dataset.laneBound = "1";
  nav.addEventListener("click", (e) => {
    const btn = e.target?.closest?.("[data-system-lane]");
    if (!btn || !nav.contains(btn)) return;
    e.preventDefault();
    applySystemLane(btn.getAttribute("data-system-lane"), { updateUrl: true });
  });
}
