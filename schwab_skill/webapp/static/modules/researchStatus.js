import { paintStatusStrip } from "./statusStripCore.js";

/** Shared 5-state strip for Research screen panels. */
export function setResearchStatusStrip(id, stateName, title, detail = "") {
  const strip = document.getElementById(id);
  if (!strip) return;
  paintStatusStrip(strip, stateName, title, detail, "research-status-pill");
}
