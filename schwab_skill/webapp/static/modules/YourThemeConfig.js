/**
 * Central chart + dashboard theme for the management audit surface.
 * Use this for Recharts/Tremor configs and custom visual components.
 */
export const AUDIT_THEME_CONFIG = {
  palette: {
    slate950: "#fffefb",
    slate900: "#f5f1e8",
    slate800: "#ebe5d6",
    text: "#1a1a1a",
    muted: "#5a5a5a",
    truth: "#2d5a4a",      // forest green
    deception: "#c94949",  // signal red
    caution: "#b0852a",    // muted gold
    neutral: "#6e6862",
    cardGlass: "rgba(255, 254, 251, 0.85)",
    cardBorder: "rgba(26, 26, 26, 0.12)",
  },
  chart: {
    gauge: {
      strong: "#2d5a4a",
      watch: "#b0852a",
      weak: "#c94949",
      track: "rgba(26, 26, 26, 0.12)",
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
    textColor: "#1a1a1a",
    grid: "rgba(26, 26, 26, 0.14)",
    truthSeries: "#2d5a4a",
    deceptionSeries: "#c94949",
    neutralSeries: "#6e6862",
    tooltipBg: "rgba(255, 254, 251, 0.96)",
  },
  lightweightCharts: {
    textColor: "#5a5a5a",
    grid: "rgba(26, 58, 46, 0.08)",
    scaleBorder: "rgba(26, 58, 46, 0.18)",
    upColor: "#2d5a4a",
    downColor: "#c94949",
    volumeUp: "rgba(45, 90, 74, 0.28)",
    volumeDown: "rgba(201, 73, 73, 0.28)",
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

