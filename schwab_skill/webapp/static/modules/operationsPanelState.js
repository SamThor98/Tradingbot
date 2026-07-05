const VALID_STATES = new Set(["success", "partial", "empty", "loading", "error"]);

/** Mirror operations status strips onto panel containers for CSS theming. */
export function setPanelState(elementId, stateName) {
  const el = document.getElementById(elementId);
  if (!el) return;
  el.dataset.state = VALID_STATES.has(stateName) ? stateName : "empty";
}

export function syncScanSectionState(stateName) {
  setPanelState("scanSection", stateName);
}

export function syncScanDetailPanelState(stateName) {
  setPanelState("scanDetailPanel", stateName);
}

export function syncScanDetailBriefState(stateName) {
  setPanelState("scanDetailBriefCard", stateName);
}

export function syncDecisionDashboardState(stateName) {
  setPanelState("decisionDashboardCard", stateName);
}

export function syncDecisionSignalEdgeState(stateName) {
  setPanelState("decisionSignalEdgeBoard", stateName);
}

export function syncPendingSectionState(stateName) {
  setPanelState("pendingSection", stateName);
}
