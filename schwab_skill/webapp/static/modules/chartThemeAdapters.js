import { YourThemeConfig } from "./YourThemeConfig.js";

/**
 * Ready-to-drop Recharts props for one-liner themed charts.
 * Override any field via `overrides`.
 *
 * Example:
 *   const theme = getRechartsProps();
 *   <Line stroke={theme.series.truth} />
 *
 * @param {Record<string, any>} [overrides]
 * @returns {{
 *   colors: Record<string, string>,
 *   series: Record<string, string>,
 *   grid: { stroke: string; strokeDasharray: string },
 *   axis: { stroke: string; tick: { fill: string; fontSize: number } },
 *   tooltip: {
 *     contentStyle: Record<string, string>;
 *     labelStyle: Record<string, string>;
 *     itemStyle: Record<string, string>;
 *   }
 * }}
 */
export function getRechartsProps(overrides = {}) {
  const theme = YourThemeConfig;
  const base = {
    colors: {
      bg: theme.palette.slate950,
      panel: theme.palette.cardGlass,
      text: theme.recharts.textColor,
      muted: theme.palette.muted,
      truth: theme.palette.truth,
      deception: theme.palette.deception,
      neutral: theme.palette.neutral,
      caution: theme.palette.caution,
    },
    series: {
      truth: theme.recharts.truthSeries,
      deception: theme.recharts.deceptionSeries,
      neutral: theme.recharts.neutralSeries,
      caution: theme.palette.caution,
    },
    grid: {
      stroke: theme.recharts.grid,
      strokeDasharray: "3 3",
    },
    axis: {
      stroke: theme.palette.muted,
      tick: {
        fill: theme.recharts.textColor,
        fontSize: 12,
      },
    },
    tooltip: {
      contentStyle: {
        backgroundColor: theme.recharts.tooltipBg,
        border: `1px solid ${theme.palette.cardBorder}`,
        borderRadius: "10px",
        color: theme.recharts.textColor,
      },
      labelStyle: {
        color: theme.recharts.textColor,
      },
      itemStyle: {
        color: theme.recharts.textColor,
      },
    },
  };
  return {
    ...base,
    ...overrides,
    colors: { ...base.colors, ...(overrides.colors || {}) },
    series: { ...base.series, ...(overrides.series || {}) },
    grid: { ...base.grid, ...(overrides.grid || {}) },
    axis: {
      ...base.axis,
      ...(overrides.axis || {}),
      tick: { ...base.axis.tick, ...(overrides.axis?.tick || {}) },
    },
    tooltip: {
      ...base.tooltip,
      ...(overrides.tooltip || {}),
      contentStyle: { ...base.tooltip.contentStyle, ...(overrides.tooltip?.contentStyle || {}) },
      labelStyle: { ...base.tooltip.labelStyle, ...(overrides.tooltip?.labelStyle || {}) },
      itemStyle: { ...base.tooltip.itemStyle, ...(overrides.tooltip?.itemStyle || {}) },
    },
  };
}

/**
 * Ready-to-drop Tremor class/color map bindings.
 * Use this to keep semantic tokens aligned with YourThemeConfig.
 *
 * @param {Record<string, any>} [overrides]
 * @returns {{
 *   colorMap: Record<string, string>,
 *   ui: {
 *     cardClassName: string;
 *     valueClassName: string;
 *     metricClassName: string;
 *     deltaPositiveColor: string;
 *     deltaNegativeColor: string;
 *   }
 * }}
 */
export function getTremorClassMap(overrides = {}) {
  const theme = YourThemeConfig;
  const base = {
    colorMap: {
      ...theme.tremor.colorMap,
      truth: "emerald",
      deception: "rose",
      neutral: "slate",
      caution: "amber",
    },
    ui: {
      cardClassName:
        "rounded-xl border border-slate-500/30 bg-slate-950/55 shadow-xl shadow-slate-950/40 backdrop-blur-md",
      valueClassName: "text-slate-100",
      metricClassName: "text-slate-300",
      deltaPositiveColor: "emerald",
      deltaNegativeColor: "rose",
    },
  };
  return {
    ...base,
    ...overrides,
    colorMap: { ...base.colorMap, ...(overrides.colorMap || {}) },
    ui: { ...base.ui, ...(overrides.ui || {}) },
  };
}

