/**
 * Unified priority feed — one ranked status surface for the Operations
 * landing, replacing the last-writer-wins `#actionCenter` banner fan-out.
 *
 * Behind the `priority_feed` UI flag (see modules/featureFlags.js and the
 * wiki page [[section-migration-map]]). When enabled:
 *
 *   - `initPriorityFeed()` takes over the `#actionCenter` card and installs
 *     itself as the sink for `updateActionCenter` (modules/logger.js), so
 *     every existing writer (scan lifecycle, token health escalations,
 *     `prioritizeActionCenterFromHealth`, billing, auth prompts) lands in
 *     the feed without touching its call sites.
 *   - Items are deduped by `key` (bridged writers key on their title), ranked
 *     by severity (error > warn > success > info) then recency, and capped.
 *   - Items may carry an `href` deep link into the relevant surface (e.g.
 *     `#pendingSection`, Diagnostics health panels) — the deep views on
 *     Diagnostics remain the source of detail; the feed is the triage list.
 *
 * When the flag is off this module is inert and the legacy single-banner
 * action center behaves exactly as before.
 */

import { safeText } from "./format.js";
import { setActionCenterSink } from "./logger.js";

const SEVERITY_RANK = { error: 3, warn: 2, success: 1, info: 0 };
const MAX_ITEMS = 6;
const MAX_INFO_ITEMS = 2;

/** key -> { key, title, message, severity, href, hrefLabel, updatedAt } */
const items = new Map();

let host = null;
let onActionClick = null;
let installed = false;

function normalizeSeverity(severity) {
  const s = safeText(severity).toLowerCase();
  return Object.prototype.hasOwnProperty.call(SEVERITY_RANK, s) ? s : "info";
}

function keyFromTitle(title) {
  return safeText(title)
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "")
    .slice(0, 64) || "untitled";
}

function sortedItems() {
  return [...items.values()].sort((a, b) => {
    const rank = SEVERITY_RANK[b.severity] - SEVERITY_RANK[a.severity];
    if (rank !== 0) return rank;
    return b.updatedAt - a.updatedAt;
  });
}

function pruneItems() {
  const ordered = sortedItems();
  // Keep at most MAX_INFO_ITEMS informational rows so transient chatter
  // (scan progress, billing notes) never drowns actionable warnings.
  let infoSeen = 0;
  const keep = new Set();
  for (const item of ordered) {
    if (keep.size >= MAX_ITEMS) break;
    if (item.severity === "info" || item.severity === "success") {
      infoSeen += 1;
      if (infoSeen > MAX_INFO_ITEMS) continue;
    }
    keep.add(item.key);
  }
  for (const key of [...items.keys()]) {
    if (!keep.has(key)) items.delete(key);
  }
}

function render() {
  if (!host) return;
  const ordered = sortedItems();
  const top = ordered[0] || null;
  host.classList.remove("info", "success", "warn", "error");
  host.classList.add(top ? top.severity : "info");

  const list = host.querySelector(".priority-feed-list");
  if (!list) return;
  list.innerHTML = "";
  if (!top) {
    const empty = document.createElement("li");
    empty.className = "priority-feed-item priority-feed-empty muted";
    empty.textContent = "Ready. Run Scan to load candidates.";
    list.appendChild(empty);
    return;
  }
  ordered.forEach((item, idx) => {
    const li = document.createElement("li");
    li.className = `priority-feed-item severity-${item.severity}${idx === 0 ? " priority-feed-top" : ""}`;
    li.setAttribute("data-feed-key", item.key);

    const body = document.createElement("div");
    body.className = "priority-feed-item-body";
    const titleEl = document.createElement("strong");
    titleEl.className = "priority-feed-item-title";
    titleEl.textContent = item.title;
    const msgEl = document.createElement("p");
    msgEl.className = "priority-feed-item-text muted";
    msgEl.textContent = item.message || "";
    body.appendChild(titleEl);
    if (item.message) body.appendChild(msgEl);

    const actions = document.createElement("div");
    actions.className = "priority-feed-item-actions";
    if (item.href) {
      const link = document.createElement("a");
      link.className = "btn small secondary";
      link.href = item.href;
      link.textContent = item.hrefLabel || "Open";
      link.addEventListener("click", () => {
        if (typeof onActionClick === "function") {
          try {
            onActionClick({ key: item.key, severity: item.severity, href: item.href });
          } catch {
            /* instrumentation must never break navigation */
          }
        }
      });
      actions.appendChild(link);
    }
    const dismiss = document.createElement("button");
    dismiss.type = "button";
    dismiss.className = "priority-feed-dismiss";
    dismiss.setAttribute("aria-label", `Dismiss: ${item.title}`);
    dismiss.textContent = "\u00d7";
    dismiss.addEventListener("click", () => removePriorityItem(item.key));
    actions.appendChild(dismiss);

    li.appendChild(body);
    li.appendChild(actions);
    list.appendChild(li);
  });
}

/**
 * Upsert a feed item. `key` dedupes repeat writes from the same source;
 * omit it to key on the title (the bridge path from `updateActionCenter`).
 */
export function pushPriorityItem({ key = "", title = "Update", message = "", severity = "info", href = "", hrefLabel = "" } = {}) {
  if (!installed) return false;
  const k = safeText(key) || keyFromTitle(title);
  items.set(k, {
    key: k,
    title: safeText(title) || "Update",
    message: safeText(message),
    severity: normalizeSeverity(severity),
    href: safeText(href),
    hrefLabel: safeText(hrefLabel),
    updatedAt: Date.now(),
  });
  pruneItems();
  render();
  if (typeof window !== "undefined") {
    window.dispatchEvent(new CustomEvent("priority_feed_change"));
  }
  return true;
}

/** Top ranked feed item, if any (for System alert banner mirroring). */
export function getTopPriorityItem() {
  if (!installed) return null;
  const ordered = sortedItems();
  return ordered[0] || null;
}

/** Remove an item (resolved state — e.g. pending queue emptied). */
export function removePriorityItem(key) {
  if (!installed) return;
  if (items.delete(safeText(key))) {
    render();
    if (typeof window !== "undefined") {
      window.dispatchEvent(new CustomEvent("priority_feed_change"));
    }
  }
}

/** True once the feed has taken over the action-center surface. */
export function isPriorityFeedActive() {
  return installed;
}

/**
 * Convert the `#actionCenter` card into the feed host and route all
 * `updateActionCenter` writers into the feed. Call once during boot when the
 * `priority_feed` flag is enabled.
 */
export function initPriorityFeed({ onAction = null } = {}) {
  const wrap = document.getElementById("actionCenter");
  if (!wrap) return false;
  host = wrap;
  onActionClick = onAction;

  const body = wrap.querySelector(".action-center-body");
  if (body) {
    body.innerHTML = `
      <strong id="actionCenterTitle" class="priority-feed-heading">Priority feed</strong>
      <ul class="priority-feed-list" role="list"></ul>
      <p id="actionCenterText" class="visually-hidden" aria-hidden="true"></p>
    `;
  }
  wrap.classList.add("priority-feed");

  installed = true;
  setActionCenterSink((payload) => {
    pushPriorityItem(payload || {});
    return true; // handled — suppress the legacy single-banner write
  });
  render();
  return true;
}
