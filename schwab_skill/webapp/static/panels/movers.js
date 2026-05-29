/**
 * Market-movers panel — top gainers / losers / most-active from Schwab's
 * /movers screener, surfaced on the main dashboard. Driven by
 * `/api/cockpit/movers` (flag-gated by MARKET_MOVERS_MODE; returns empty when
 * off or when the market token is unavailable).
 *
 * Uses `setAsyncState` for explicit loading/empty/error/success states, mirroring
 * the sectors panel.
 */

import { api } from "../modules/api.js";
import { safeText } from "../modules/format.js";
import { logEvent } from "../modules/logger.js";
import {
  setAsyncState,
  ASYNC_LOADING,
  ASYNC_EMPTY,
  ASYNC_ERROR,
  ASYNC_SUCCESS,
  ASYNC_SIGNED_OUT,
} from "../modules/asyncState.js";

function moverColumn(title, tickers, cls) {
  const items = (tickers || []).slice(0, 8);
  const body = items.length
    ? items.map((t) => `<span class="pill ${cls}">${safeText(t)}</span>`).join(" ")
    : '<span class="muted small">—</span>';
  return `<div class="mover-col"><div class="mover-col-title">${safeText(title)}</div><div class="mover-col-body">${body}</div></div>`;
}

export async function refreshMovers() {
  const grid = document.getElementById("moversGrid");
  if (!grid) return;
  setAsyncState(grid, ASYNC_LOADING, { message: "Loading movers…" });
  const out = await api.get("/api/cockpit/movers");
  if (!out.ok) {
    const msg = out.user_message || out.error;
    logEvent({ kind: "system", severity: "warn", message: `Movers load failed: ${msg}` });
    if (out.status === 401) {
      setAsyncState(grid, ASYNC_SIGNED_OUT, { message: "Sign in to load market movers." });
      return;
    }
    setAsyncState(grid, ASYNC_ERROR, {
      html: `<div class="async-state async-state--error" role="alert">
        <div>Movers unavailable: ${safeText(msg)}</div>
        <button class="btn small secondary" type="button" data-async-retry>Retry</button>
      </div>`,
      onRetry: () => void refreshMovers(),
    });
    return;
  }
  const movers = out.data?.movers || {};
  const total = (movers.gainers || []).length + (movers.losers || []).length + (movers.most_active || []).length;
  if (!total) {
    setAsyncState(grid, ASYNC_EMPTY, {
      message: "No movers yet (enable MARKET_MOVERS_MODE and link Schwab market data).",
    });
    return;
  }
  grid.setAttribute("data-async-state", ASYNC_SUCCESS);
  grid.innerHTML = `
    <div class="mover-grid">
      ${moverColumn("Gainers", movers.gainers, "good")}
      ${moverColumn("Losers", movers.losers, "bad")}
      ${moverColumn("Most active", movers.most_active, "neutral")}
    </div>
  `;
}
