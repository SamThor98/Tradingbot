/**
 * Operator desk controls. Starts a background desk job and polls its brief.
 * The server calls the same scan, check, and backtest routes as the rest of the dashboard.
 */

import { api } from "../modules/api.js";

let bound = false;
let pollTimer = 0;

function setStatus(text) {
  const el = document.getElementById("operatorDeskStatus");
  if (el) el.textContent = text;
}

function setBrief(text) {
  const el = document.getElementById("operatorDeskBrief");
  if (el) el.textContent = text || "";
}

function stopPoll() {
  if (pollTimer) window.clearInterval(pollTimer);
  pollTimer = 0;
}

async function poll(jobId) {
  const out = await api.get(`/api/operator-desk/status?job_id=${encodeURIComponent(jobId)}`, { timeoutMs: 30000 });
  if (!out.ok) {
    setStatus(out.error || "Desk status failed.");
    stopPoll();
    return;
  }
  const row = out.data || {};
  if (row.status === "running") {
    setStatus("Desk is running. A scan can take several minutes.");
    return;
  }
  stopPoll();
  const btn = document.getElementById("operatorDeskRunBtn");
  if (btn) btn.disabled = false;
  if (row.status === "failed") {
    setStatus(row.error || "Desk failed.");
    return;
  }
  const brief = row.brief || {};
  const posted = brief.discord_sent ? " Discord brief posted." : "";
  setStatus(`Desk finished.${posted}`);
  setBrief(brief.text || "");
}

export function bindOperatorDesk() {
  const btn = document.getElementById("operatorDeskRunBtn");
  if (!btn || bound) return;
  bound = true;
  btn.addEventListener("click", async () => {
    stopPoll();
    btn.disabled = true;
    setBrief("");
    setStatus("Starting desk…");
    const ticker = document.getElementById("operatorDeskTicker")?.value || "";
    const body = {
      run_scan: Boolean(document.getElementById("operatorDeskScan")?.checked),
      queue_backtest: Boolean(document.getElementById("operatorDeskBacktest")?.checked),
      notify_discord: Boolean(document.getElementById("operatorDeskDiscord")?.checked),
      ticker: ticker.trim() || null,
    };
    const out = await api.post("/api/operator-desk/run", body, { timeoutMs: 60000 });
    if (!out.ok) {
      btn.disabled = false;
      setStatus(out.error || "Desk did not start.");
      return;
    }
    const jobId = out.data?.job_id;
    if (!jobId) {
      btn.disabled = false;
      setStatus("Desk did not return a job id.");
      return;
    }
    setStatus("Desk is running.");
    pollTimer = window.setInterval(() => {
      void poll(jobId);
    }, 3000);
    void poll(jobId);
  });
}
