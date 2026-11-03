/**
 * Live backtest monitor helpers. Pure functions — no DOM, no fetch.
 * Hub renders from these so the weekend campaign is readable in 100-run slices.
 */

export const CAMPAIGN_PREFIX = "wknd ";
export const PF_GATE = 1.2;
export const MONITOR_RUN_CAP = 100;

const PF_BUCKETS = [
  { id: "lt08", label: "< 0.80", min: Number.NEGATIVE_INFINITY, max: 0.8, tone: "bad" },
  { id: "lt10", label: "0.80–1.00", min: 0.8, max: 1.0, tone: "warn" },
  { id: "lt12", label: "1.00–1.20", min: 1.0, max: 1.2, tone: "neutral" },
  { id: "lt15", label: "1.20–1.50", min: 1.2, max: 1.5, tone: "ok" },
  { id: "gte15", label: "≥ 1.50", min: 1.5, max: Number.POSITIVE_INFINITY, tone: "ok" },
];

export function theoryName(run) {
  const spec = run && typeof run.spec === "object" && run.spec ? run.spec : {};
  return String(spec.theory_name || "").trim();
}

export function isCampaignRun(run) {
  return theoryName(run).startsWith(CAMPAIGN_PREFIX);
}

export function shortTheory(name) {
  return String(name || "")
    .replace(/^wknd\s+/i, "")
    .trim();
}

export function parseApiTime(iso) {
  const raw = String(iso || "").trim();
  if (!raw) return Number.NaN;
  if (/[zZ]$/.test(raw) || /[+-]\d{2}:\d{2}$/.test(raw)) return Date.parse(raw);
  return Date.parse(`${raw.replace(" ", "T")}Z`);
}

export function formatElapsed(ms) {
  const n = Number(ms);
  if (!Number.isFinite(n) || n < 0) return "—";
  const sec = Math.floor(n / 1000);
  if (sec < 60) return `${sec}s`;
  const min = Math.floor(sec / 60);
  if (min < 60) return `${min}m ${sec % 60}s`;
  const hr = Math.floor(min / 60);
  return `${hr}h ${min % 60}m`;
}

export function pfNumber(run) {
  const n = Number(run?.result_summary?.profit_factor_net);
  if (run?.result_summary?.profit_factor_net === "inf" || n === Infinity) return Infinity;
  return Number.isFinite(n) ? n : null;
}

export function pfTone(value) {
  if (value === Infinity) return "ok";
  const n = Number(value);
  if (!Number.isFinite(n)) return "neutral";
  if (n >= PF_GATE) return "ok";
  if (n >= 1) return "warn";
  return "bad";
}

export function pfBuckets(values) {
  const nums = (Array.isArray(values) ? values : []).filter((v) => Number.isFinite(v) || v === Infinity);
  return PF_BUCKETS.map((bucket) => {
    const count = nums.filter((v) => {
      if (v === Infinity) return bucket.id === "gte15";
      const n = Number(v);
      return n >= bucket.min && n < bucket.max;
    }).length;
    return { ...bucket, count };
  });
}

export function universeCounts(runs) {
  const counts = {};
  for (const run of Array.isArray(runs) ? runs : []) {
    const spec = run && typeof run.spec === "object" && run.spec ? run.spec : {};
    const key = String(spec.universe_preset || spec.universe_mode || "unknown");
    counts[key] = (counts[key] || 0) + 1;
  }
  return Object.entries(counts)
    .map(([id, count]) => ({ id, count }))
    .sort((a, b) => b.count - a.count || a.id.localeCompare(b.id));
}

export function campaignDeadline(now = new Date()) {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: "America/Chicago",
    weekday: "short",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hourCycle: "h23",
  }).formatToParts(now);
  const get = (type) => parts.find((p) => p.type === type)?.value || "";
  const weekday = get("weekday");
  const year = Number(get("year"));
  const month = Number(get("month"));
  const day = Number(get("day"));
  const daysAhead = { Sun: 0, Mon: 6, Tue: 5, Wed: 4, Thu: 3, Fri: 2, Sat: 1 }[weekday] ?? 0;
  const base = Date.UTC(year, month - 1, day);
  const sunday = new Date(base + daysAhead * 86400000);
  return {
    iso: `${sunday.getUTCFullYear()}-${String(sunday.getUTCMonth() + 1).padStart(2, "0")}-${String(sunday.getUTCDate()).padStart(2, "0")}T23:59:59-05:00`,
    label: "Sunday 11:59 PM CT",
  };
}

export function summarizeMonitor(runs, nowMs = Date.now()) {
  const list = Array.isArray(runs) ? runs : [];
  const campaign = list.filter(isCampaignRun);
  const running = list.find((r) => r && r.status === "running") || null;
  const queued = list.filter((r) => r && r.status === "queued");
  const byStatus = (rows) => {
    const out = { success: 0, failed: 0, running: 0, queued: 0, cancelled: 0, other: 0 };
    for (const row of rows) {
      const status = String(row?.status || "");
      if (status in out) out[status] += 1;
      else out.other += 1;
    }
    return out;
  };
  const finished = campaign.filter((r) => r.status === "success" && r.has_result);
  const pfs = finished.map(pfNumber).filter((v) => v !== null);
  const finite = pfs.filter((v) => Number.isFinite(v));
  const meanPf = finite.length ? finite.reduce((a, b) => a + b, 0) / finite.length : null;
  const aboveGate = finite.filter((v) => v >= PF_GATE).length;
  let throughputPerHour = null;
  if (finished.length >= 2) {
    const times = finished
      .map((r) => parseApiTime(r.created_at))
      .filter((t) => Number.isFinite(t))
      .sort((a, b) => a - b);
    if (times.length >= 2) {
      const hours = (times[times.length - 1] - times[0]) / 3600000;
      if (hours > 0.15) throughputPerHour = finished.length / hours;
    }
  }
  const runningCreated = running ? parseApiTime(running.created_at) : Number.NaN;
  return {
    sampleSize: list.length,
    campaign: {
      total: campaign.length,
      status: byStatus(campaign),
      meanPf,
      aboveGate,
      finished: finished.length,
      pfs,
    },
    allStatus: byStatus(list),
    running,
    queued,
    elapsedMs: Number.isFinite(runningCreated) ? Math.max(0, nowMs - runningCreated) : null,
    throughputPerHour,
    universes: universeCounts(campaign.length ? campaign : list),
    buckets: pfBuckets(pfs),
    recentFinished: finished.slice(0, 6),
    deadline: campaignDeadline(new Date(nowMs)),
  };
}

export function filterQueueRows(runs, filter) {
  const list = Array.isArray(runs) ? runs : [];
  if (filter === "live") return list.filter((r) => r && (r.status === "running" || r.status === "queued"));
  if (filter === "campaign") return list.filter(isCampaignRun);
  return list;
}

export function defaultQueueFilter(runs) {
  const live = filterQueueRows(runs, "live");
  return live.length ? "live" : "all";
}

export const TAPE_LIMIT = 5;

function tradeNet(row) {
  const n = Number(row?.net_return_pct);
  return Number.isFinite(n) ? n : null;
}

function tapeKey(row) {
  return [row?.ticker, row?.entry_date, row?.exit_date, row?.net_return_pct, row?.runId || ""].join("|");
}

export function splitTape(rows, limit = TAPE_LIMIT) {
  const cap = Math.max(1, Number(limit) || TAPE_LIMIT);
  const list = (Array.isArray(rows) ? rows : [])
    .map((row, index) => ({ row, index, net: tradeNet(row) }))
    .filter((item) => item.net != null);
  list.sort((a, b) => b.net - a.net || a.index - b.index);
  const best = list.slice(0, cap).map((item) => item.row);
  const shown = new Set(best);
  const worst =
    list.length > cap
      ? list
          .slice(-cap)
          .reverse()
          .map((item) => item.row)
          .filter((row) => !shown.has(row))
      : [];
  return { best, worst, closed: list.length };
}

function ranksRows(ranks) {
  if (!ranks || typeof ranks !== "object") return [];
  const best = Array.isArray(ranks.best) ? ranks.best : [];
  const worst = Array.isArray(ranks.worst) ? ranks.worst : [];
  return [...best, ...worst];
}

export function finishedTradeTape(runs, limit = TAPE_LIMIT) {
  const pool = [];
  const seen = new Set();
  let sources = 0;
  for (const run of Array.isArray(runs) ? runs : []) {
    if (!run || run.status !== "success" || !(run.has_result || run.result_summary)) continue;
    const rows = ranksRows(run.result_summary?.trade_ranks);
    if (!rows.length) continue;
    sources += 1;
    const label = shortTheory(theoryName(run)) || theoryName(run) || "Finished run";
    for (const row of rows) {
      const next = { ...row, runId: run.id || "", runLabel: label };
      const key = tapeKey(next);
      if (seen.has(key)) continue;
      seen.add(key);
      pool.push(next);
    }
  }
  return { ...splitTape(pool, limit), sources };
}

export function inProgressTape(run) {
  const progress = run?.progress;
  if (!progress || typeof progress !== "object") return null;
  const split = splitTape(ranksRows(progress));
  const closed = Number(progress.closed);
  const count = Number.isFinite(closed) ? closed : split.closed;
  if (count <= 0 && !split.best.length && !split.worst.length) return null;
  return { closed: count, best: split.best, worst: split.worst };
}

export function latestFinishedRun(runs) {
  const list = (Array.isArray(runs) ? runs : []).filter(
    (run) => run && run.status === "success" && (run.has_result || run.result_summary),
  );
  list.sort((a, b) => (parseApiTime(b.created_at) || 0) - (parseApiTime(a.created_at) || 0));
  return list[0] || null;
}
