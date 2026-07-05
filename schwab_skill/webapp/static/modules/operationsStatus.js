import { paintStatusStrip } from "./statusStripCore.js";

/** Shared 5-state strip for Today / Operations panels (scan, pending, workflow). */
export function setOperationsStatusStrip(id, stateName, title, detail = "") {
  const strip = document.getElementById(id);
  if (!strip) return;
  paintStatusStrip(strip, stateName, title, detail, "operations-status-pill");
}
