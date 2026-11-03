/**
 * Today-screen Grok review. Asks the hybrid handoff about a scan row.
 * Advisory only: the verdict does not stage an order.
 */

import { state } from "../modules/state.js";
import { api } from "../modules/api.js";

const CONTEXT_KEYS = [
  "ticker",
  "signal_score",
  "price",
  "stop_pct",
  "sma_50",
  "volume_ratio",
  "_data_quality",
  "strategy",
  "strategy_id",
  "rank",
  "qual_raw",
];

const SEARCH_ROLES = new Set(["research", "reasoning"]);

export function contextForTicker(ticker, rows) {
  const symbol = String(ticker || "").trim().toUpperCase();
  const list = Array.isArray(rows) ? rows : [];
  const row = list.find((item) => String(item?.ticker || item?.symbol || "").trim().toUpperCase() === symbol);
  const context = { ticker: symbol };
  if (!row) return context;
  CONTEXT_KEYS.forEach((key) => {
    const value = row[key];
    if (value != null && value !== "") context[key] = contextValue(key, value);
  });
  return context;
}

function contextValue(key, value) {
  const num = typeof value === "number" ? value : Number(value);
  if (!Number.isFinite(num)) return value;
  if (key === "stop_pct") {
    const pct = Math.abs(num) <= 1.5 ? num * 100 : num;
    return `${pct.toFixed(1)}%`;
  }
  if (key === "price" || key === "sma_50") return Math.round(num * 100) / 100;
  if (key === "signal_score" || key === "qual_raw" || key === "rank") return Math.round(num * 10) / 10;
  if (key === "volume_ratio") return Math.round(num * 100) / 100;
  return value;
}

function tickerFromDetail() {
  const text = String(document.getElementById("scanDetailTicker")?.textContent || "").trim().toUpperCase();
  if (!text || text === "SELECT A TICKER") return "";
  return text.replace(/[^A-Z0-9.\-]/g, "").slice(0, 12);
}

function scanRows() {
  const shortlist = Array.isArray(state.latestShortlistSignals) ? state.latestShortlistSignals : [];
  const latest = Array.isArray(state.latestSignals) ? state.latestSignals : [];
  return shortlist.length ? shortlist : latest;
}

function setStatus(text) {
  const el = document.getElementById("grokReviewStatus");
  if (el) el.textContent = text;
}

const LAST_OUTPUT_KEY = "tradingbot.grokReview.last";

function addParagraph(host, text, className) {
  const value = String(text || "").trim();
  if (!value) return;
  const el = document.createElement("p");
  if (className) el.className = className;
  el.textContent = value;
  host.appendChild(el);
}

function plainLanguage(text) {
  return readableNumbers(
    String(text || "")
      .replaceAll("sma_50", "50-day average")
      .replaceAll("volume_ratio", "volume ratio")
      .replaceAll("signal_score", "signal score")
      .replaceAll("stop_pct", "stop")
      .replaceAll("_data_quality", "data quality")
      .replaceAll("specialist_briefs", "specialist notes"),
  );
}

function readableNumbers(text) {
  return String(text || "")
    .replace(/\b0\.\d{4,}\b/g, (raw) => {
      const num = Number(raw);
      if (!Number.isFinite(num)) return raw;
      return num < 0.5 ? `${(num * 100).toFixed(1)}%` : num.toFixed(2);
    })
    .replace(/\b\d+\.\d{3,}(?!%)\b/g, (raw) => {
      const num = Number(raw);
      return Number.isFinite(num) ? num.toFixed(2) : raw;
    })
    .replace(/(\d+\.\d{2,})%/g, (raw) => `${Number.parseFloat(raw).toFixed(1)}%`);
}

function sentences(text) {
  return String(text || "")
    .split(/(?<=[.!?])\s+/)
    .map((part) => part.trim())
    .filter(Boolean);
}

function isBoilerplate(text) {
  return /do not place an order|plugin as live|share count is undefined|not an order|no order is placed|no pull request|no code change/i.test(
    text,
  );
}

function firstUsefulSentence(text) {
  return sentences(plainLanguage(text)).find((line) => !isBoilerplate(line)) || "";
}

function usableNote(text) {
  const value = firstUsefulSentence(text);
  if (!value || /^none\b/i.test(value) || value.length > 120 || isBoilerplate(value)) return "";
  if (/^stand aside\b/i.test(value)) return "";
  if (!/\b(file|branch|test|fix|pull request|pytest|ruff)\b/i.test(value)) return "";
  return value;
}

function verdictLabel(verdict) {
  return (
    {
      proceed: "Proceed",
      revise: "Revise",
      reject: "Stand aside",
      error: "Error",
      skipped: "Skipped",
      incomplete: "Incomplete",
    }[String(verdict || "").toLowerCase()] || String(verdict || "No result")
  );
}

function confidenceLabel(value) {
  const num = Number(value);
  if (!Number.isFinite(num)) return "";
  const pct = num <= 1 ? Math.round(num * 100) : Math.round(num);
  return `${pct}%`;
}

function citationLabel(item) {
  if (typeof item === "string") return item.trim();
  if (item && typeof item === "object") return String(item.url || item.title || "").trim();
  return "";
}

function rememberOutput(data) {
  try {
    sessionStorage.setItem(LAST_OUTPUT_KEY, JSON.stringify(data));
  } catch {
    /* The visible panel is the output. Storage is only a refresh convenience. */
  }
}

function renderResult(data) {
  const host = document.getElementById("grokReviewResult");
  if (!host) return;
  host.replaceChildren();
  const stages = Array.isArray(data?.stages) ? data.stages : [];
  const grok = stages.find((stage) => stage.stage === "grok") || {};
  const result = grok.result || {};
  const review = result.review || null;
  const usage = data?.usage || result.usage || {};
  const verdict = String(review?.verdict || grok.status || "no result").toLowerCase();
  const ticker = String(
    result.context?.ticker || data?.context?.ticker || (plainLanguage(review?.summary).match(/\bon ([A-Z]{1,6})\b/) || [])[1] || "",
  ).trim();

  const hint = document.querySelector("#grokReviewFold .today-fold-hint");
  if (hint) hint.textContent = `${verdictLabel(verdict)}. No orders.`;

  const head = document.createElement("div");
  head.className = "grok-review-head";
  const pill = document.createElement("span");
  pill.className = `grok-review-pill grok-review-pill--${verdict.replace(/[^a-z]/g, "") || "unknown"}`;
  pill.textContent = verdictLabel(verdict);
  head.appendChild(pill);
  if (ticker) {
    const name = document.createElement("strong");
    name.textContent = ticker;
    head.appendChild(name);
  }
  const advisory = document.createElement("span");
  advisory.className = "muted small";
  advisory.textContent = "Advisory. Not an order.";
  head.appendChild(advisory);
  host.appendChild(head);

  const reason = sentences(plainLanguage(review?.summary))
    .filter((line) => !isBoilerplate(line))
    .slice(0, 2);
  if (reason.length) addParagraph(host, reason.join(" "), "grok-review-summary");
  addParagraph(host, grok.error || result.error, "grok-review-error");

  const findings = Array.isArray(review?.findings) ? review.findings : [];
  if (findings.length) {
    const section = document.createElement("section");
    section.className = "grok-review-block";
    const title = document.createElement("h4");
    title.textContent = "Findings";
    section.appendChild(title);
    findings.forEach((item) => {
      const card = document.createElement("article");
      card.className = "grok-review-finding";
      const row = document.createElement("div");
      row.className = "grok-review-row";
      const claim = document.createElement("p");
      claim.className = "grok-review-claim";
      claim.textContent = firstUsefulSentence(item?.claim) || plainLanguage(item?.claim);
      row.appendChild(claim);
      const confidence = confidenceLabel(item?.confidence);
      if (confidence) {
        const meta = document.createElement("span");
        meta.className = "grok-review-confidence";
        meta.textContent = confidence;
        row.appendChild(meta);
      }
      card.appendChild(row);
      const evidence = firstUsefulSentence(item?.evidence);
      if (evidence) {
        const detail = document.createElement("p");
        detail.className = "grok-review-evidence";
        detail.textContent = evidence;
        card.appendChild(detail);
      }
      section.appendChild(card);
    });
    host.appendChild(section);
  }

  const risks = (Array.isArray(review?.risks) ? review.risks : [])
    .map((risk) => firstUsefulSentence(risk))
    .filter((risk) => risk && !isBoilerplate(risk));
  if (risks.length) {
    const section = document.createElement("section");
    section.className = "grok-review-block";
    const title = document.createElement("h4");
    title.textContent = "Risks";
    section.appendChild(title);
    const list = document.createElement("ul");
    list.className = "grok-review-risks";
    risks.forEach((risk) => {
      const li = document.createElement("li");
      li.textContent = risk;
      list.appendChild(li);
    });
    section.appendChild(list);
    host.appendChild(section);
  }

  if (!review) {
    const text = String(result.text || "").trim();
    if (text && !text.startsWith("{")) {
      const block = document.createElement("pre");
      block.className = "grok-review-text";
      block.textContent = text;
      host.appendChild(block);
    }
  }

  const sources = (Array.isArray(result.citations) ? result.citations : []).map(citationLabel).filter(Boolean);
  if (sources.length) {
    const section = document.createElement("section");
    section.className = "grok-review-block";
    const title = document.createElement("h4");
    title.textContent = "Sources";
    section.appendChild(title);
    const list = document.createElement("ul");
    list.className = "grok-review-citations";
    sources.forEach((source) => {
      const li = document.createElement("li");
      li.textContent = source;
      list.appendChild(li);
    });
    section.appendChild(list);
    host.appendChild(section);
  }

  const cursor = stages.find((stage) => stage.stage === "cursor");
  const github = stages.find((stage) => stage.stage === "github");
  const validation = stages.find((stage) => stage.stage === "validation");
  const notes = [];
  const cursorNote = usableNote(cursor?.task?.summary);
  if (cursorNote) notes.push(`Cursor: ${cursorNote}`);
  const githubNote = usableNote(github?.intent?.title || github?.intent?.body);
  if (githubNote) notes.push(`GitHub: ${githubNote}`);
  const checks = (Array.isArray(validation?.accepted_commands) ? validation.accepted_commands : [])
    .map((command) => String(command || "").trim())
    .filter(Boolean);
  if (checks.length) notes.push(`Checks: ${checks.join("; ")}`);
  if (notes.length) addParagraph(host, notes.join(" "), "muted small");

  const tokens = Number(usage.total_tokens) || 0;
  const cost = usage.cost_usd == null ? "" : ` · about $${Number(usage.cost_usd).toFixed(4)}`;
  if (tokens || cost) {
    addParagraph(host, `${tokens} tokens${cost}`, "grok-review-meta");
  }
}

function renderFailure(message) {
  renderResult({
    mode: "",
    stages: [{ stage: "grok", status: "error", result: { error: message || "Grok review failed.", text: "" } }],
    usage: {},
  });
}

async function loadCapabilities() {
  const out = await api.get("/api/agents/grok/capabilities");
  const mode = document.getElementById("grokReviewMode");
  if (!out.ok) {
    if (mode) mode.textContent = "Capabilities unavailable.";
    return;
  }
  const data = out.data || {};
  if (mode) {
    const key = data.key_configured ? "key set" : "no xAI key";
    const gate = data.mode === "off" ? " Off does not call xAI." : " Ask Grok writes the answer in Output.";
    mode.textContent = `Mode ${data.mode || "off"} · ${data.model || "grok-4.7"} · ${key}.${gate}`;
  }
}

function selectedRole() {
  return document.getElementById("grokReviewRole")?.value || "adversarial_review";
}

function applySearchDefault() {
  const search = document.getElementById("grokReviewSearch");
  if (!search) return;
  search.checked = SEARCH_ROLES.has(selectedRole());
}

function fillFromScan(ticker) {
  const input = document.getElementById("grokReviewTicker");
  const symbol = String(ticker || input?.value || tickerFromDetail() || "").trim().toUpperCase();
  if (input && symbol) input.value = symbol;
  return contextForTicker(symbol, scanRows());
}

async function askGrok() {
  const btn = document.getElementById("grokReviewAskBtn");
  const prompt = String(document.getElementById("grokReviewPrompt")?.value || "").trim();
  if (!prompt) {
    setStatus("Write a question first.");
    return;
  }
  const ticker = String(document.getElementById("grokReviewTicker")?.value || "").trim().toUpperCase();
  const effort = document.getElementById("grokReviewEffort")?.value || "";
  if (btn) btn.disabled = true;
  setStatus("Asking Grok. A reasoning call can take a minute.");
  const body = {
    prompt,
    role: selectedRole(),
    context: fillFromScan(ticker),
    search: Boolean(document.getElementById("grokReviewSearch")?.checked),
    task_id: ticker ? `scan_${ticker}` : "grok_review",
    execution: "sync",
  };
  if (effort) body.reasoning_effort = effort;
  const out = await api.post("/api/agents/grok", body, { timeoutMs: 180000 });
  if (btn) btn.disabled = false;
  if (!out.ok) {
    const message = out.error || out.user_message || "Grok review failed.";
    setStatus(message);
    renderFailure(message);
    return;
  }
  const grok = (out.data?.stages || []).find((stage) => stage.stage === "grok");
  setStatus(grok?.status === "skipped" ? "Skipped. Grok mode is off, so xAI was not called." : "Output is below. This is not an order.");
  rememberOutput(out.data);
  renderResult(out.data);
  document.getElementById("grokReviewResult")?.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

function openFold(ticker) {
  const fold = document.getElementById("grokReviewFold");
  if (fold) fold.open = true;
  fillFromScan(ticker);
  document.getElementById("grokReviewSection")?.scrollIntoView({ behavior: "smooth", block: "start" });
  document.getElementById("grokReviewPrompt")?.focus();
}

export function bindGrokReview() {
  if (document.body.dataset.grokReviewBound === "1") return;
  document.body.dataset.grokReviewBound = "1";
  document.getElementById("grokReviewRole")?.addEventListener("change", applySearchDefault);
  document.getElementById("grokReviewUseScanBtn")?.addEventListener("click", () => {
    const ticker = fillFromScan(tickerFromDetail());
    setStatus(ticker.ticker ? `Using ${ticker.ticker} from the open setup.` : "Select a scan row first.");
  });
  document.getElementById("grokReviewAskBtn")?.addEventListener("click", () => {
    void askGrok();
  });
  document.getElementById("scanDetailGrokBtn")?.addEventListener("click", () => {
    openFold(tickerFromDetail());
  });
  applySearchDefault();
  try {
    const saved = sessionStorage.getItem(LAST_OUTPUT_KEY);
    if (saved) {
      renderResult(JSON.parse(saved));
      setStatus("Last review is in Output. This is not an order.");
    }
  } catch {
    /* Ignore a stale saved output. */
  }
  void loadCapabilities();
}
