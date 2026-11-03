/**
 * Compact Sharadar strip for Today / decision-card briefs.
 * Display only. Null chips are omitted. Does not change scan scores.
 */

import { escapeHtml, formatPercentPoints } from "./format.js";
import { humanizeProvenanceLine } from "./humanize.js";

function finiteNum(value) {
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}

function fmtMoney(value) {
  const n = finiteNum(value);
  if (n === null) return "";
  const abs = Math.abs(n);
  const sign = n < 0 ? "-" : "";
  if (abs >= 1e12) return `${sign}$${(abs / 1e12).toFixed(2)}T`;
  if (abs >= 1e9) return `${sign}$${(abs / 1e9).toFixed(2)}B`;
  if (abs >= 1e6) return `${sign}$${(abs / 1e6).toFixed(1)}M`;
  return `${sign}$${abs.toFixed(2)}`;
}

function fmtNum(value, digits = 2) {
  const n = finiteNum(value);
  if (n === null) return "";
  return n.toFixed(digits);
}

function fmtOwnership(value) {
  // percent points: SF3 holder percent summed in pit_fields_from_parts (tests: 6.5)
  const n = finiteNum(value);
  if (n === null) return "";
  return formatPercentPoints(n, 1);
}

function fmtShares(value) {
  const n = finiteNum(value);
  if (n === null) return "";
  if (Math.abs(n) >= 1e9) return `${(n / 1e9).toFixed(2)}B`;
  if (Math.abs(n) >= 1e6) return `${(n / 1e6).toFixed(1)}M`;
  return String(Math.round(n));
}

function chipLabel(id) {
  if (id === "marketcap") return "Market cap";
  if (id === "evebitda") return "EV / EBITDA";
  if (id === "pe") return "P/E";
  if (id === "ps") return "P/S";
  return id;
}

function chipText(chip) {
  if (chip.id === "marketcap") return fmtMoney(chip.value);
  return fmtNum(chip.value);
}

export function sharadarBriefStripHtml(overlay) {
  if (!overlay || overlay.available === false) return "";
  const chips = Array.isArray(overlay.chips) ? overlay.chips : [];
  const chipHtml = chips
    .map((chip) => {
      const text = chipText(chip);
      if (!text) return "";
      return `<span class="pill small sharadar-brief-chip">${escapeHtml(chipLabel(chip.id))} ${escapeHtml(text)}</span>`;
    })
    .filter(Boolean)
    .join("");
  const holders = Array.isArray(overlay.holders) ? overlay.holders.slice(0, 5) : [];
  const holderRows = holders
    .map((row) => {
      const name = String(row?.name || "").trim();
      if (!name) return "";
      const bits = [fmtShares(row.units), fmtMoney(row.value), row.percent == null ? "" : fmtOwnership(row.percent)].filter(
        Boolean,
      );
      return `<li><strong>${escapeHtml(name)}</strong>${bits.length ? ` · ${escapeHtml(bits.join(" · "))}` : ""}</li>`;
    })
    .filter(Boolean)
    .join("");
  const inst = overlay.institutional_ownership == null ? "" : fmtOwnership(overlay.institutional_ownership);
  const insiders = Array.isArray(overlay.insiders) ? overlay.insiders.slice(0, 8) : [];
  const insiderRows = insiders
    .map((row) => {
      const owner = String(row?.ownername || "").trim();
      if (!owner && !row?.filingdate) return "";
      const bits = [
        String(row.filingdate || "").slice(0, 10),
        owner,
        String(row.transactioncode || ""),
        fmtShares(row.transactionshares),
      ].filter(Boolean);
      return `<li>${escapeHtml(bits.join(" · "))}</li>`;
    })
    .filter(Boolean)
    .join("");
  const net = overlay.insider_net && typeof overlay.insider_net === "object" ? overlay.insider_net : null;
  const netLine =
    net && (Number(net.buys) || Number(net.sells))
      ? `<p class="muted small">Form 4 cluster: ${Number(net.buys) || 0} buy · ${Number(net.sells) || 0} sell</p>`
      : "";
  const actions = Array.isArray(overlay.actions) ? overlay.actions : [];
  const actionHtml = actions.length
    ? `<div class="sharadar-action-chips">${actions
        .slice(0, 6)
        .map((row) => `<span class="sharadar-action-chip">${escapeHtml(row.date || "")} · ${escapeHtml(row.event || "")}</span>`)
        .join("")}</div>`
    : "";
  if (!chipHtml && !holderRows && !insiderRows && !actionHtml) return "";
  const provenance = humanizeProvenanceLine(overlay.provenance);
  return `
    <div class="sharadar-brief-strip">
      ${provenance ? `<p class="muted small sharadar-brief-provenance">${escapeHtml(provenance)}</p>` : ""}
      ${chipHtml ? `<div class="scan-brief-triage-chips sharadar-brief-chips">${chipHtml}</div>` : ""}
      ${
        holderRows
          ? `<details class="scan-brief-detail">
              <summary>13F holders <span class="disclosure-hint muted">${holders.length}</span></summary>
              ${inst ? `<p class="muted small">Institutional % (sum of supplied percents): ${escapeHtml(inst)}</p>` : ""}
              <ul>${holderRows}</ul>
            </details>`
          : ""
      }
      ${
        insiderRows
          ? `<details class="scan-brief-detail">
              <summary>Form 4 <span class="disclosure-hint muted">${insiders.length}</span></summary>
              ${netLine}
              <ul>${insiderRows}</ul>
            </details>`
          : ""
      }
      ${
        actionHtml
          ? `<details class="scan-brief-detail">
              <summary>Corporate actions <span class="disclosure-hint muted">${actions.length}</span></summary>
              ${actionHtml}
            </details>`
          : ""
      }
    </div>
  `;
}
