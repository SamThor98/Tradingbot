/** Fixed-position tooltip for long copy (avoids table overflow clipping). */

let _activeTip = null;
let _activeAnchor = null;

function hideFloatTooltip() {
  if (_activeTip) {
    _activeTip.remove();
    _activeTip = null;
    _activeAnchor = null;
  }
}

function positionTip(tip, anchor) {
  const rect = anchor.getBoundingClientRect();
  const margin = 8;
  let top = rect.top - tip.offsetHeight - margin;
  let left = rect.left + rect.width / 2 - tip.offsetWidth / 2;
  left = Math.max(margin, Math.min(left, window.innerWidth - tip.offsetWidth - margin));
  if (top < margin) top = rect.bottom + margin;
  tip.style.top = `${Math.round(top)}px`;
  tip.style.left = `${Math.round(left)}px`;
}

export function attachFloatTooltip(anchor, text) {
  if (!anchor || anchor.dataset.floatTipWired) return;
  const copy = String(text || "").trim();
  if (!copy) return;
  anchor.dataset.floatTipWired = "1";
  anchor.setAttribute("title", copy);

  const show = () => {
    hideFloatTooltip();
    const tip = document.createElement("div");
    tip.className = "float-tip";
    tip.setAttribute("role", "tooltip");
    tip.textContent = copy;
    document.body.appendChild(tip);
    positionTip(tip, anchor);
    _activeTip = tip;
    _activeAnchor = anchor;
  };

  anchor.addEventListener("mouseenter", show);
  anchor.addEventListener("focus", show);
  anchor.addEventListener("mouseleave", hideFloatTooltip);
  anchor.addEventListener("blur", hideFloatTooltip);
}

export function wireScanRankWhyTooltips(root = document) {
  const scope = root?.querySelectorAll ? root : document;
  scope.querySelectorAll(".scan-rank-why[data-rank-tip]").forEach((el) => {
    attachFloatTooltip(el, el.getAttribute("data-rank-tip") || "");
  });
}

if (typeof document !== "undefined") {
  document.addEventListener("scroll", hideFloatTooltip, true);
  window.addEventListener("resize", hideFloatTooltip);
}
