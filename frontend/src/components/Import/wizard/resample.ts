import type {
  ColumnMapping,
  ColumnResample,
  ResampleConfig,
  ResampleOp,
  ResampleOptions,
  ResampleReport,
  TimeGrid,
  UploadInspection,
} from "../../../api/types";
import { CHART, FONT_SANS, PALETTE } from "../../../theme";
import { columnAssignment } from "./mapping";

/** What the app did before the step existed: detect the frequency, no added dates, every column on its default. */
export const DEFAULT_RESAMPLE: ResampleConfig = { frequency: "auto", period_label: "start", fill_missing_dates: false, columns: [] };

export const resampleOf = (m: ColumnMapping): ResampleConfig => m.resample ?? DEFAULT_RESAMPLE;

type Grid = "sub_daily" | "daily" | "weekly" | "monthly";

/** The frequency the rows are read at: the chosen one, else the server's resolved or detected one. */
export function gridFrequency(m: ColumnMapping, grid?: TimeGrid | null, report?: ResampleReport | null): Grid | null {
  const chosen = resampleOf(m).frequency;
  if (chosen !== "auto") return chosen;
  if (report) return report.grid.frequency;
  const d = grid?.detected;
  return d === "sub_daily" || d === "daily" || d === "weekly" || d === "monthly" ? d : null;
}

/** The operations that matter at this frequency: fill always, aggregate when several rows share a period, split for weeks/months. */
export function activeOps(freq: Grid | null, detected?: TimeGrid["detected"] | null, mergedRows?: number): ResampleOp[] {
  const ops: ResampleOp[] = [];
  const combines =
    freq === "sub_daily" ||
    Boolean(mergedRows && mergedRows > 0) ||
    ((freq === "weekly" || freq === "monthly") && (detected === "sub_daily" || detected === "daily" || (freq === "monthly" && detected === "weekly")));
  if (combines) ops.push("aggregate");
  if (freq === "weekly" || freq === "monthly") ops.push("disaggregate");
  ops.push("fill");
  return ops;
}

/** The mapping with table-level resampling settings changed. */
export function setResample(m: ColumnMapping, patch: Partial<Omit<ResampleConfig, "columns">>): ColumnMapping {
  return { ...m, resample: { ...resampleOf(m), ...patch } };
}

/** The mapping with one column's method set (`null` = back to the role's default); an entry without a method is dropped. */
export function setColumnMethod(m: ColumnMapping, column: string, op: ResampleOp, value: string | null, fillValue?: number | null): ColumnMapping {
  const r = resampleOf(m);
  const current: ColumnResample = r.columns.find((c) => c.column === column) ?? { column };
  const next: ColumnResample = { ...current, [op]: value || null };
  if (op === "fill") next.fill_value = value === "constant" ? fillValue ?? current.fill_value ?? 0 : null;
  const empty = !next.aggregate && !next.fill && !next.disaggregate;
  const others = r.columns.filter((c) => c.column !== column);
  const columns = empty ? others : r.columns.some((c) => c.column === column) ? r.columns.map((c) => (c.column === column ? next : c)) : [...r.columns, next];
  return { ...m, resample: { ...r, columns } };
}

/** The value a constant fill uses. */
export function setFillValue(m: ColumnMapping, column: string, value: number | null): ColumnMapping {
  const r = resampleOf(m);
  return { ...m, resample: { ...r, columns: r.columns.map((c) => (c.column === column ? { ...c, fill_value: value } : c)) } };
}

/** `next` (e.g. Gemini's mapping) keeping `prev`'s resampling for every column whose role did not change. */
export function carryResample(next: ColumnMapping, prev: ColumnMapping): ColumnMapping {
  const r = resampleOf(prev);
  const same = (col: string) => {
    const a = columnAssignment(prev, col);
    const b = columnAssignment(next, col);
    return a.role !== "ignore" && a.role === b.role && a.channel === b.channel;
  };
  const layoutChanged = prev.layout !== next.layout;
  return {
    ...next,
    resample: { ...r, fill_missing_dates: layoutChanged ? false : r.fill_missing_dates, columns: layoutChanged ? [] : r.columns.filter((c) => same(c.column)) },
  };
}

/** The label of a method value in the server's catalog. */
export function methodLabel(options: ResampleOptions | undefined, op: ResampleOp, value: string | null | undefined): string {
  if (!value) return "";
  return options?.[op].find((o) => o.value === value)?.label ?? value;
}

const nf = (n: number) => n.toLocaleString("en-GB");
/** An ISO date as the wizard writes dates ("3 Jan 2022"). */
export const day = (iso?: string | null) =>
  iso ? new Date(`${iso.slice(0, 10)}T00:00:00Z`).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric", timeZone: "UTC" }) : "—";

/** One line about the dates as uploaded ("Weekly · 60 dates on Mondays · 3 Jan 2022 – 20 Feb 2023 · 2 weeks missing"). */
export function describeGrid(g: TimeGrid | null | undefined): string {
  if (!g || g.detected === "unknown") return "No dates could be read.";
  const what = { sub_daily: "Several rows a day", daily: "Daily", weekly: "Weekly", monthly: "Monthly", irregular: `Every ${g.step_days} days` }[g.detected];
  const parts = [what, `${nf(g.rows)} rows on ${nf(g.dates)} date${g.dates === 1 ? "" : "s"}`, `${day(g.first)} – ${day(g.last)}`];
  if (g.detected === "weekly" && g.weekday) parts[1] += ` (${g.weekday}s)`;
  if (g.detected === "monthly" && g.anchor && g.anchor !== "mixed") parts[1] += ` (month ${g.anchor}s)`;
  if (g.has_time && g.detected === "sub_daily") parts.push(`up to ${g.rows_per_date} rows a day`);
  else if (g.repeated_dates) parts.push(`${nf(g.repeated_dates)} dates with several rows`);
  const unit = g.detected === "weekly" ? "week" : g.detected === "monthly" ? "month" : "day";
  if (g.missing_dates) parts.push(`${nf(g.missing_dates)} ${unit}${g.missing_dates === 1 ? "" : "s"} missing`);
  if (g.shifted) parts.push(`${g.shifted} shifted week${g.shifted === 1 ? "" : "s"}`);
  if (g.weekdays_present && g.weekdays_present.length < 7) parts.push("no rows on some weekdays");
  return parts.join(" · ");
}

/** The Check & create line: what the resampling did, in one sentence. */
export function resampleSummary(report: ResampleReport | null | undefined, options?: ResampleOptions): string {
  if (!report) return "—";
  const g = report.grid;
  const from = { sub_daily: "Several rows a day combined into days", daily: "Daily", weekly: "Weekly rows split into days", monthly: "Monthly rows split into days" }[g.frequency];
  const parts = [`${from} (${nf(g.days)} days)`];
  if (g.added) parts.push(`${g.added} missing ${g.frequency === "weekly" ? "week" : g.frequency === "monthly" ? "month" : "day"}${g.added === 1 ? "" : "s"} added`);
  const filled = report.columns.filter((c) => c.filled > 0);
  if (filled.length) parts.push(`imputed: ${filled.map((c) => `${c.column} ${nf(c.filled)} (${methodLabel(options, "fill", c.fill).toLowerCase()})`).join(", ")}`);
  else parts.push("nothing imputed");
  return parts.join("; ");
}

/** The errors the "Resample & fill" step can fix, and the rest (the mapping's). */
export function splitErrors(res: UploadInspection): { mapping: string[]; prep: string[] } {
  const prep = res.prep_errors ?? [];
  return { mapping: res.errors.filter((e) => !prep.includes(e)), prep };
}

/**
 * The preview of one column on the daily grid: the prepared values as a line and the imputed days as markers on it
 * (accent blue line, copper markers with a white ring: validated pair, CVD ΔE 28.9; the marker shape is the second cue).
 */
export function previewFigure(report: ResampleReport, column: string): { data: Record<string, unknown>[]; layout: Record<string, unknown> } | null {
  const s = report.series.columns.find((c) => c.column === column);
  if (!s) return null;
  const dates = report.series.dates;
  const imputedDates = s.imputed.map((i) => dates[i]);
  const imputedValues = s.imputed.map((i) => s.values[i]);
  const data: Record<string, unknown>[] = [
    {
      type: "scatter",
      mode: "lines",
      name: "Daily value",
      x: dates,
      y: s.values,
      line: { color: PALETTE.brandPrimary, width: 2 },
      connectgaps: false,
      hovertemplate: "%{x|%a %d %b %Y}: %{y:,.4~g}<extra></extra>",
    },
  ];
  if (imputedDates.length) {
    data.push({
      type: "scatter",
      mode: "markers",
      name: `Imputed (${nf(imputedDates.length)} day${imputedDates.length === 1 ? "" : "s"})`,
      x: imputedDates,
      y: imputedValues,
      marker: { color: "#EB6834", size: 8, symbol: "circle", line: { color: "#FFFFFF", width: 2 } },
      hovertemplate: "%{x|%a %d %b %Y}: %{y:,.4~g} (imputed)<extra></extra>",
    });
  }
  const values = s.values.filter((v): v is number => v !== null);
  const min = values.length ? Math.min(...values) : 0;
  const max = values.length ? Math.max(...values) : 1;
  return {
    data,
    layout: {
      font: { family: FONT_SANS, size: 12, color: PALETTE.inkPrimary },
      paper_bgcolor: "#FFFFFF",
      plot_bgcolor: "#FFFFFF",
      showlegend: true,
      legend: { orientation: "h", yanchor: "bottom", y: 1.02, xanchor: "left", x: 0, font: { size: 11 } },
      hovermode: "closest",
      margin: { l: 64, r: 16, t: 40, b: 40 },
      xaxis: { type: "date", gridcolor: CHART.grid, linecolor: CHART.axis, tickfont: { color: PALETTE.inkSecondary } },
      yaxis: {
        gridcolor: CHART.grid,
        linecolor: CHART.axis,
        zerolinecolor: CHART.axis,
        tickfont: { color: PALETTE.inkSecondary },
        tickformat: max - min >= 1000 ? ",.0f" : undefined,
        hoverformat: ",.4~g",
      },
    },
  };
}

