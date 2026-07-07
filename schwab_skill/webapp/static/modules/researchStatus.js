import { paintStatusStrip } from "./statusStripCore.js";
import { paintResearchSurface } from "./researchPanelSnapshot.js";

/** Shared 5-state strip for Research screen panels. */
export function setResearchStatusStrip(id, stateName, title, detail = "") {
  const strip = document.getElementById(id);
  if (!strip) return;
  paintStatusStrip(strip, stateName, title, detail, "research-status-pill");
}

/**
 * Prefer this for Wave 3 panels (status strip + OUTPUT/DATA/ACTION snapshot).
 * Falls back to strip-only when snapshotId is omitted.
 */
export function setResearchPanelStatus(opts = {}) {
  if (opts.snapshotId) {
    return paintResearchSurface(opts);
  }
  setResearchStatusStrip(opts.stripId, opts.stateName, opts.title, opts.detail || "");
  return opts.stateName;
}

export { paintResearchSurface, paintResearchPanelSnapshot, syncResearchSectionState } from "./researchPanelSnapshot.js";
