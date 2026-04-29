/**
 * Central chart + dashboard theme for the management audit surface.
 * Use this for Recharts/Tremor configs and custom visual components.
 */
export const AUDIT_THEME_CONFIG = {
  palette: {
    slate950: "#020617",
    slate900: "#0f172a",
    slate800: "#1e293b",
    text: "#e2e8f0",
    muted: "#94a3b8",
    truth: "#10b981",      // Emerald-500
    deception: "#f43f5e",  // Rose-500
    caution: "#f59e0b",
    neutral: "#64748b",
    cardGlass: "rgba(15, 23, 42, 0.58)",
    cardBorder: "rgba(148, 163, 184, 0.24)",
  },
  chart: {
    gauge: {
      strong: "#10b981",
      watch: "#f59e0b",
      weak: "#f43f5e",
      track: "rgba(71, 85, 105, 0.38)",
    },
    heatmap: {
      high: "heat-high", // deception/failure pressure
      mid: "heat-mid",
      low: "heat-low",   // truth/execution
      na: "heat-na",
      thresholds: {
        sbcMusd: { mid: 250, high: 350 },
        sbcPctRevenue: { mid: 6, high: 9 },
        netIncomeRisk: { mid: -500, high: -150 },
        priceReturnRisk: { mid: -5, high: 0 },
        correlationRisk: { mid: 0.2, high: 0.45 },
      },
    },
  },
  recharts: {
    textColor: "#e2e8f0",
    grid: "rgba(148, 163, 184, 0.22)",
    truthSeries: "#10b981",
    deceptionSeries: "#f43f5e",
    neutralSeries: "#64748b",
    tooltipBg: "rgba(2, 6, 23, 0.92)",
  },
  tremor: {
    colorMap: {
      truth: "emerald",
      deception: "rose",
      neutral: "slate",
      caution: "amber",
    },
  },
};

export const YourThemeConfig = AUDIT_THEME_CONFIG;

