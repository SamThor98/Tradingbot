export type IntegrityPillar = {
  name: "Capital Discipline" | "Shareholder Alignment" | "Communication Transparency" | "Operational Execution" | string;
  score: number;
  note: string;
};

export type SayDoTimelineRow = {
  quarter: string;
  guidance: string;
  actual: string;
  kpi: string;
  target_value?: number;
  actual_value?: number;
  variance_pct?: number;
  status: "Beat" | "Miss" | "Mixed" | string;
  source: string;
};

export type DilutionHeatmapRow = {
  quarter: string;
  sbc_musd: number;
  sbc_pct_rev: number;
  net_income_musd: number;
  price_return_pct: number;
  correlation: number;
  note?: string;
};

export type ManagementRedFlag = {
  title: string;
  severity: "low" | "medium" | "high" | "critical" | string;
  evidence: string;
  quarter: string;
};

export type ManagementIntegrityScore = {
  source: string;
  ticker: string;
  generated_at: string;
  integrity_scorecard: {
    score: number;
    pillars: IntegrityPillar[];
  };
  say_do_timeline: SayDoTimelineRow[];
  dilution_sbc_heatmap: DilutionHeatmapRow[];
  red_flags: ManagementRedFlag[];
  diagnostics?: Record<string, number | string>;
};

export declare function calculateManagementIntegrityScore(
  ticker: string,
): Promise<{ ok: boolean; data?: ManagementIntegrityScore; error?: string }>;

