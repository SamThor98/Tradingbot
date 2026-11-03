/**
 * Central chart + dashboard theme for the management audit surface.
 * Use this for Recharts/Tremor configs and custom visual components.
 */
export const AUDIT_THEME_CONFIG = {
  palette: {
    slate950: "#ffffff",
    slate900: "#e9ebef",
    slate800: "#f6f7f9",
    text: "#111418",
    muted: "#5e6772",
    truth: "#14352c",
    deception: "#9f2d2d",
    caution: "#8a5c1f",
    neutral: "#5e6772",
    cardGlass: "#ffffff",
    cardBorder: "#d5d8de",
  },
  chart: {
    gauge: {
      strong: "#14352c",
      watch: "#8a5c1f",
      weak: "#9f2d2d",
      track: "#e4e7ec",
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
    textColor: "#111418",
    grid: "#e4e7ec",
    truthSeries: "#14352c",
    deceptionSeries: "#9f2d2d",
    neutralSeries: "#5e6772",
    tooltipBg: "#ffffff",
  },
  lightweightCharts: {
    textColor: "#5e6772",
    grid: "#e4e7ec",
    scaleBorder: "#d5d8de",
    upColor: "#14352c",
    downColor: "#9f2d2d",
    volumeUp: "rgba(20, 53, 44, 0.28)",
    volumeDown: "rgba(159, 45, 45, 0.28)",
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

