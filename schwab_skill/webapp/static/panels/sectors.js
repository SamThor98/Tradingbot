/**
 * Sector-strength panel — draws one card per sector ETF with its
 * SPY-relative bar. Pure DOM render driven by `/api/sectors`.
 *
 * Uses `setAsyncState` so the panel container always carries an explicit
 * loading/empty/error/success state — no silent "we look fine because we
 * have stale rows" rendering after a failed refetch.
 */

import { api } from "../modules/api.js";
import { safeText, safeNum, formatDecimal } from "../modules/format.js";
import { logEvent } from "../modules/logger.js";
import {
  setAsyncState,
  ASYNC_LOADING,
  ASYNC_EMPTY,
  ASYNC_ERROR,
  ASYNC_SUCCESS,
  ASYNC_SIGNED_OUT,
} from "../modules/asyncState.js";

export async function refreshSectors() {
  const grid = document.getElementById("sectorGrid");
  if (!grid) return;
  setAsyncState(grid, ASYNC_LOADING, { message: "Loading sectors…" });
  const out = await api.get("/api/sectors");
  if (!out.ok) {
    const msg = out.user_message || out.error;
    logEvent({ kind: "system", severity: "warn", message: `Sector load failed: ${msg}` });
    if (out.status === 401) {
      setAsyncState(grid, ASYNC_SIGNED_OUT, {
        message: "Sign in to load sector strength data.",
      });
      return;
    }
    const hintHtml = out.hint ? `<div class="muted small">${safeText(out.hint)}</div>` : "";
    setAsyncState(grid, ASYNC_ERROR, {
      html: `<div class="async-state async-state--error" role="alert">
        <div>
          <div>Sectors unavailable: ${safeText(msg)}</div>
          ${hintHtml}
        </div>
        <button class="btn small secondary" type="button" data-async-retry>Retry</button>
      </div>`,
      onRetry: () => void refreshSectors(),
    });
    return;
  }
  const rows = out.data?.rows || [];
  if (!rows.length) {
    setAsyncState(grid, ASYNC_EMPTY, { message: "No sector data returned." });
    return;
  }
  // Switch to success-with-content. We render markup first, then stamp the
  // attribute so CSS can outline the panel as "fresh data".
  grid.innerHTML = "";
  grid.setAttribute("data-async-state", ASYNC_SUCCESS);
  const maxAbsVs = Math.max(1, ...rows.map((r) => Math.abs(safeNum(r.vs_spy, 0))));
  rows.forEach((row) => {
    const card = document.createElement("div");
    card.className = `sector-card ${row.winning ? "win" : "loss"}`;
    const vs = safeNum(row.vs_spy, 0);
    const barPct = Math.round((Math.abs(vs) / maxAbsVs) * 100);
    card.innerHTML = `
      <div class="${row.winning ? "sector-winning" : "sector-lagging"}"><strong>${safeText(row.etf)}</strong> ${safeText(row.name || "")}</div>
      <div class="${row.winning ? "sector-winning" : "sector-lagging"} mono-nums">${formatDecimal(row.return_pct, 2, "—")}% vs SPY ${formatDecimal(vs, 2, "—")}%</div>
      <div class="sector-bar-track" aria-hidden="true" title="Relative strength vs SPY (within this grid)">
        <div class="sector-bar-fill ${row.winning ? "sector-bar-fill--win" : "sector-bar-fill--loss"}" style="width:${barPct}%"></div>
      </div>
      <div class="${row.winning ? "pill good" : "pill bad"}">${row.winning ? "Winning" : "Lagging"}</div>
    `;
    grid.appendChild(card);
  });
}
