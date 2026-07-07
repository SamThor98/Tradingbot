/**
 * Plain-language glossary for trading jargon (audit finding F17).
 *
 * Two consumers:
 *   1. `glossaryTitle(term)` — feed a `title=""` attribute on a static label.
 *   2. `decorateGlossary(html, terms)` — wrap known terms in
 *      `<abbr class="glossary-term" title="…">` inside already-escaped HTML.
 *
 * Keep definitions short and factual; they render as native tooltips.
 * NOTE for decorateGlossary: definitions must not contain other glossary
 * terms, or a later pass would decorate text inside a title attribute.
 */

export const GLOSSARY = Object.freeze({
  PF: "Profit factor: gross wins divided by gross losses. Promotion gate: mean at or above 1.20, worst era at or above 1.00.",
  VCP: "Volatility contraction pattern: price range tightens on falling volume before a potential breakout.",
  "Stage 2": "Uptrend phase in Weinstein stage analysis: price above rising long-term moving averages.",
  ECE: "Expected calibration error: how far predicted probabilities drift from realized hit rates.",
  SLO: "Service-level objective: the reliability target for scans and data feeds.",
  shadow: "Shadow mode: the plugin computes decisions for comparison but never affects live orders.",
  bps: "Basis points: 1 bps = 0.01%.",
});

/** Default subset that is safe to auto-decorate inside prose. */
export const DECORATE_TERMS = Object.freeze(["PF", "VCP", "Stage 2", "ECE", "SLO"]);

export function glossaryTitle(term) {
  return GLOSSARY[term] || "";
}

function escapeRegExp(s) {
  return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

/**
 * Wrap known jargon in <abbr> tooltips. Input must already be HTML-escaped
 * plain text (no attributes containing the terms).
 *
 * @param {string} html
 * @param {readonly string[]} [terms]
 * @returns {string}
 */
export function decorateGlossary(html, terms = DECORATE_TERMS) {
  let out = String(html || "");
  for (const term of terms) {
    const def = GLOSSARY[term];
    if (!def) continue;
    const re = new RegExp(`\\b${escapeRegExp(term)}\\b`, "g");
    out = out.replace(re, `<abbr class="glossary-term" title="${def}">${term}</abbr>`);
  }
  return out;
}
