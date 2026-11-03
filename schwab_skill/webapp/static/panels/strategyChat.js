/**
 * Strategy chat panel — the conversational frontend that lets users
 * describe a backtest in natural language. Owns the message bubble
 * renderer, the "queued" callout that appears once a chat tool result
 * contains a `queue_backtest` task id, and the `sendStrategyChat`
 * round-trip against `/api/strategy-chat`.
 *
 * `switchBacktestHubTab` and `refreshBacktestRuns` live on the
 * backtest panel module; they're injected to avoid pulling that
 * module into strategyChat (and to keep them swappable in tests).
 */

import { state } from "../modules/state.js";
import { api } from "../modules/api.js";
import { safeText } from "../modules/format.js";
import { toolResultsSummaryHtml } from "../modules/typedView.js";
import { logEvent } from "../modules/logger.js";

export function strategyChatPayloadMessages() {
  return (Array.isArray(state.strategyChatMessages) ? state.strategyChatMessages : [])
    .filter((m) => m && (m.role === "user" || m.role === "assistant"))
    .map((m) => ({ role: m.role, content: String(m.content ?? "") }));
}

export function scrollStrategyChatToEnd() {
  const el = document.getElementById("scMessages");
  if (el) el.scrollTop = el.scrollHeight;
}

export function adaptLocalBacktestChatUi() {
  const local = !state.publicConfig?.saas_mode;
  const tab = document.getElementById("btHubTabChat");
  if (tab) tab.textContent = local ? "Shortcuts" : "Chat assistant";
  const summary = document.querySelector("#backtestSection .bt-composer-details > summary");
  if (summary) {
    summary.textContent = local ? "Edit settings — form and shortcuts" : "Edit settings — form and chat";
  }
  const sub = document.querySelector("#strategyChatPanel .backtest-chat-subhead, #strategyChatPanel .chat-subhead");
  if (sub) {
    sub.textContent = local
      ? "Chat is not on this computer. These buttons queue a 5-year or 8-year replay the same way the form does."
      : "The assistant validates your settings and can queue the same backtest job as the form. Tool details stay collapsed unless you expand them.";
  }
  const inputRow = document.querySelector("#strategyChatPanel .chat-input-row");
  if (inputRow) inputRow.classList.toggle("hidden", local);
  const chips = document.getElementById("scEmptyChips");
  if (chips) chips.classList.remove("hidden");
}

export function renderStrategyChatMessages() {
  const el = document.getElementById("scMessages");
  const chips = document.getElementById("scEmptyChips");
  const panel = document.getElementById("strategyChatPanel");
  if (!el) return;
  const msgs = Array.isArray(state.strategyChatMessages) ? state.strategyChatMessages : [];
  el.innerHTML = "";

  adaptLocalBacktestChatUi();

  // Local install: degraded banner per Wave 5 (SaaS-only surface).
  if (panel && !state.publicConfig?.saas_mode) {
    let banner = panel.querySelector("[data-strategy-chat-degraded]");
    if (!banner) {
      banner = document.createElement("div");
      banner.setAttribute("data-strategy-chat-degraded", "");
      banner.className = "async-state operator-alert operator-alert--warn";
      banner.setAttribute("role", "status");
      banner.innerHTML = `<div class="operator-alert__body">
        <strong class="operator-alert__headline">Chat is not available here</strong>
        <p class="operator-alert__detail">Use the buttons below (or Queue last 5 / 8 years on Backtest home). They queue the same job as the form.</p>
      </div>`;
      panel.insertBefore(banner, el);
    }
  }

  if (!msgs.length) {
    const hint = document.createElement("div");
    hint.className = "chat-empty-hint";
    hint.textContent = state.publicConfig?.saas_mode
      ? "Describe the universe, date range, and any rule tweaks. Examples below."
      : "Use a Queue button below. Typing here cannot start a backtest on this computer.";
    el.appendChild(hint);
    if (chips) chips.classList.remove("hidden");
    return;
  }
  if (chips) chips.classList.toggle("hidden", Boolean(state.publicConfig?.saas_mode));
  msgs.forEach((m) => {
    const wrap = document.createElement("div");
    const role = m.role === "user" ? "user" : "assistant";
    wrap.className = `chat-bubble chat-bubble-${role}`;
    const roleEl = document.createElement("div");
    roleEl.className = "chat-bubble-role";
    roleEl.textContent = role === "user" ? "You" : "Assistant";
    wrap.appendChild(roleEl);
    const body = document.createElement("div");
    body.textContent = m.content != null ? String(m.content) : "";
    wrap.appendChild(body);
    if (role === "assistant" && m.resendable && m.failedUserText) {
      const resend = document.createElement("button");
      resend.type = "button";
      resend.className = "btn small secondary";
      resend.setAttribute("data-strategy-chat-resend", "");
      resend.textContent = "Resend";
      resend.addEventListener("click", () => {
        const input = document.getElementById("scInput");
        if (input) input.value = String(m.failedUserText || "");
        void sendStrategyChat(state._strategyChatDeps || {});
      });
      wrap.appendChild(resend);
    }
    if (role === "assistant" && Array.isArray(m.toolResults) && m.toolResults.length) {
      const box = document.createElement("div");
      box.className = "chat-tool-summary";
      box.innerHTML = toolResultsSummaryHtml(m.toolResults);
      wrap.appendChild(box);
    }
    el.appendChild(wrap);
  });
  scrollStrategyChatToEnd();
}

export function hideScQueueCallout() {
  const c = document.getElementById("scQueueCallout");
  if (c) {
    c.classList.add("hidden");
    c.innerHTML = "";
  }
}

export function showScQueueCallout(taskId, runId, { switchBacktestHubTab = () => {} } = {}) {
  const c = document.getElementById("scQueueCallout");
  if (!c || !taskId) return;
  const tid = safeText(taskId);
  const rid = runId ? safeText(runId) : "";
  c.classList.remove("hidden");
  c.innerHTML = `
    <strong>Backtest queued.</strong> It runs in the background (often a few minutes). Results appear in <strong>Recent runs</strong> below when finished.
    <div class="callout-actions">
      <button type="button" class="btn small secondary" id="scCopyTaskBtn">Copy support id</button>
      <button type="button" class="btn small secondary" id="scSwitchFormTabBtn">Open form tab</button>
    </div>
  `;
  document.getElementById("scCopyTaskBtn")?.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(tid);
      logEvent({ kind: "system", severity: "info", message: "Task id copied." });
    } catch {
      logEvent({ kind: "system", severity: "warn", message: "Could not copy task id." });
    }
  });
  document.getElementById("scSwitchFormTabBtn")?.addEventListener("click", () => switchBacktestHubTab("form"));
}

export async function sendStrategyChat({ refreshBacktestRuns = async () => {}, switchBacktestHubTab = () => {} } = {}) {
  state._strategyChatDeps = { refreshBacktestRuns, switchBacktestHubTab };
  if (state.strategyChatBusy) return;
  const input = document.getElementById("scInput");
  const text = input?.value?.trim() || "";
  if (!text) return;
  if (!Array.isArray(state.strategyChatMessages)) state.strategyChatMessages = [];
  // `/api/strategy-chat` only exists on the SaaS backend; answer locally
  // instead of letting the request 404.
  if (!state.publicConfig?.saas_mode) {
    hideScQueueCallout();
    state.strategyChatMessages.push({ role: "user", content: text });
    state.strategyChatMessages.push({
      role: "assistant",
      content:
        "Strategy chat requires the hosted (SaaS) backend and is not available on this local install. " +
        "Use Queue last 5 years / Queue last 8 years on Backtest home, or the Shortcuts buttons in Edit settings.",
    });
    input.value = "";
    renderStrategyChatMessages();
    return;
  }
  hideScQueueCallout();
  state.strategyChatMessages.push({ role: "user", content: text });
  // Preserve draft until the server accepts — Wave 5 Unavailable + Resend.
  const draft = text;
  input.value = "";
  renderStrategyChatMessages();
  state.strategyChatBusy = true;
  const sendBtn = document.getElementById("scSendBtn");
  if (sendBtn) sendBtn.disabled = true;
  try {
    const out = await api.post("/api/strategy-chat", { messages: strategyChatPayloadMessages() }, { timeoutMs: 180000 });
    if (!out.ok) {
      const errDetail = safeText(out.user_message || out.error || "Request unavailable.");
      logEvent({ kind: "system", severity: "error", message: `Strategy chat: ${errDetail}` });
      if (input) input.value = draft;
      state.strategyChatMessages.push({
        role: "assistant",
        content: `Assistant: Request unavailable: ${errDetail}`,
        resendable: true,
        failedUserText: draft,
      });
      renderStrategyChatMessages();
      return;
    }
    const assistant = out.data?.message || "";
    const tools = out.data?.tool_results;
    state.strategyChatMessages.push({
      role: "assistant",
      content: assistant || "(empty reply)",
      toolResults: Array.isArray(tools) && tools.length ? tools : null,
    });
    if (Array.isArray(tools)) {
      for (const t of tools) {
        if (t && t.tool === "queue_backtest" && t.result && t.result.task_id) {
          showScQueueCallout(t.result.task_id, t.result.run_id, { switchBacktestHubTab });
          break;
        }
      }
    }
    renderStrategyChatMessages();
    await refreshBacktestRuns();
  } finally {
    state.strategyChatBusy = false;
    if (sendBtn) sendBtn.disabled = false;
  }
}
