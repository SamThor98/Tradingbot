import { paintStatusStrip } from "./statusStripCore.js";

export function setSystemStatusStrip(id, stateName, title, detail = "") {
  const strip = document.getElementById(id);
  if (!strip) return;
  paintStatusStrip(strip, stateName, title, detail, "system-status-pill");
}
