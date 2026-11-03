import type { RunHistory, ScenarioChannelRow, ScenarioResult } from "../../api/types";
import { PALETTE } from "../../theme";
import { addDays, eurCompact, signedEur } from "./format";

/**
 * Colours of the forecast planner. The four lines that overlap in the forecast window (plan, current scenario and two
 * saved scenarios) were checked with the dataviz palette validator (lightness, chroma, CVD and normal-vision
 * separation on every pair, contrast on white), which is why at most two saved scenarios are drawn at once.
 */
export const PLANNER_COLORS = {
  actual: PALETTE.brandNavy,
  plan: "#0A63BD",
  scenario: "#A8822E",
  saved: ["#BE2D2F", "#1A9C8C"],
  lastYear: "#75808A",
  gain: "#0A63BD",
  loss: "#BE2D2F",
  total: PALETTE.brandNavy,
};

/** 52 weeks, so every forecast day is compared with the same weekday one year earlier. */
export const LAST_YEAR_SHIFT_DAYS = 364;

const GRID = "#ECE7DE";
const AXIS = "#D3D7DA";
const DAY_MS = 86_400_000;
const HALF_DAY_MS = DAY_MS / 2;

type Trace = Record<string, unknown>;

export interface PlannerLine {
  name: string;
  result: ScenarioResult;
  color: string;
  dash: "solid" | "dash" | "dot";
  /** Dimmed while a newer scenario is being evaluated */
  dimmed?: boolean;
  /** Show the scenario's spend in the spend panel and each channel's effect in the hover */
  primary?: boolean;
}

export interface PlannerFigureInput {
  history: RunHistory | null;
  plan: ScenarioResult;
  /** Days of history shown before the forecast; null shows all of it */
  historyDays: number | null;
  showLastYear: boolean;
  lines: PlannerLine[];
  channelNames: Record<string, string>;
  /** Phone width: taller figure with the legend under the plot */
  compact?: boolean;
}

const shiftMs = (iso: string, ms: number) => new Date(new Date(`${iso}T00:00:00Z`).getTime() + ms).toISOString();

/** History before the plan, the plan's forecast with its 90 % prediction interval, scenario lines and daily spend. */
export function plannerFigure({ history, plan, historyDays, showLastYear, lines, channelNames, compact = false }: PlannerFigureInput) {
  const fcDates = plan.daily.map((r) => r.date);
  const fcStart = fcDates[0];
  const fcEnd = fcDates[fcDates.length - 1];
  const histStart = historyDays == null ? null : addDays(fcStart, -historyDays);

  const hIdx = history
    ? history.dates.flatMap((d, i) => ((histStart == null || d >= histStart) && d < fcStart ? [i] : []))
    : [];
  const hDates = hIdx.map((i) => history!.dates[i]);
  const hSpend = hIdx.map((i) => Object.values(history!.spend).reduce<number>((acc, arr) => acc + (arr[i] ?? 0), 0));
  const traces: Trace[] = [];

  if (history && hDates.length) {
    traces.push({
      type: "scatter",
      mode: "lines",
      name: "Actual sales",
      x: hDates,
      y: hIdx.map((i) => history.sales[i]),
      line: { color: PLANNER_COLORS.actual, width: 1.6 },
      hovertemplate: "€%{y:,.0f}<extra>Actual sales</extra>",
    });
  }

  if (history && showLastYear) {
    const salesByDate = new Map(history.dates.map((d, i) => [d, history.sales[i]]));
    const lyDates = [...hDates, ...fcDates];
    const lyY = lyDates.map((d) => salesByDate.get(addDays(d, -LAST_YEAR_SHIFT_DAYS)) ?? null);
    if (lyY.some((v) => v != null)) {
      traces.push({
        type: "scatter",
        mode: "lines",
        name: "Same weekday last year",
        x: lyDates,
        y: lyY,
        line: { color: PLANNER_COLORS.lastYear, width: 1.3, dash: "dot" },
        hovertemplate: "€%{y:,.0f}<extra>Same weekday last year</extra>",
      });
    }
  }

  traces.push(
    {
      type: "scatter",
      mode: "lines",
      name: "Upper 95 %",
      x: fcDates,
      y: plan.daily.map((r) => r.prediction_hi),
      line: { width: 0 },
      showlegend: false,
      legendgroup: "plan",
      hoverinfo: "skip",
    },
    {
      type: "scatter",
      mode: "lines",
      name: "Plan: 90 % prediction interval",
      x: fcDates,
      y: plan.daily.map((r) => r.prediction_lo),
      line: { width: 0 },
      fill: "tonexty",
      fillcolor: "rgba(10, 99, 189, 0.13)",
      legendgroup: "plan",
      hoverinfo: "skip",
    },
    {
      type: "scatter",
      mode: "lines",
      name: "Plan forecast",
      x: fcDates,
      y: plan.daily.map((r) => r.scenario_forecast),
      line: { color: PLANNER_COLORS.plan, width: 2.2 },
      legendgroup: "plan",
      customdata: plan.daily.map((r) => [r.prediction_lo, r.prediction_hi]),
      hovertemplate: "€%{y:,.0f} (90 %: €%{customdata[0]:,.0f} – €%{customdata[1]:,.0f})<extra>Plan forecast</extra>",
    }
  );

  for (const line of lines) {
    const rows = line.result.daily;
    const deltas = line.result.channel_daily_delta ?? {};
    const breakdown = rows.map((_, i) =>
      Object.entries(deltas)
        .filter(([, d]) => Math.abs(d[i] ?? 0) >= 1)
        .sort((a, b) => Math.abs(b[1][i]) - Math.abs(a[1][i]))
        .map(([slug, d]) => `${channelNames[slug] ?? slug} ${eurCompact(d[i], true)}`)
        .join(" · ")
    );
    traces.push({
      type: "scatter",
      mode: "lines",
      name: line.name,
      x: rows.map((r) => r.date),
      y: rows.map((r) => r.scenario_forecast),
      line: { color: line.color, width: line.primary ? 2.4 : 1.8, dash: line.dash },
      opacity: line.dimmed ? 0.4 : 1,
      customdata: rows.map((r, i) => [signedEur(r.scenario_forecast - r.baseline_forecast), breakdown[i] || "no channel effect"]),
      hovertemplate:
        line.primary
          ? "€%{y:,.0f} (%{customdata[0]} vs plan)<br><span style='font-size:11px'>%{customdata[1]}</span><extra>" + escapeName(line.name) + "</extra>"
          : "€%{y:,.0f} (%{customdata[0]} vs plan)<extra>" + escapeName(line.name) + "</extra>",
    });
  }

  // Spend panel: history, the plan and the current scenario, coloured like their sales lines
  const primary = lines.find((l) => l.primary);
  const barWidth = DAY_MS * 0.8;
  if (hDates.length) {
    traces.push({
      type: "bar",
      name: "Spend (history)",
      x: hDates,
      y: hSpend,
      yaxis: "y2",
      width: barWidth,
      marker: { color: "rgba(0, 66, 102, 0.26)" },
      hovertemplate: "€%{y:,.0f}<extra>Spend</extra>",
    });
  }
  traces.push({
    type: "bar",
    name: "Planned spend",
    x: fcDates,
    y: plan.daily.map((r) => r.planned_spend),
    yaxis: "y2",
    width: barWidth,
    marker: { color: "rgba(10, 99, 189, 0.32)" },
    hovertemplate: "€%{y:,.0f}<extra>Planned spend</extra>",
  });
  if (primary) {
    const rows = primary.result.daily;
    traces.push({
      type: "bar",
      name: "Scenario spend",
      x: rows.map((r) => r.date),
      y: rows.map((r) => r.planned_spend),
      yaxis: "y2",
      width: DAY_MS * 0.4,
      opacity: primary.dimmed ? 0.4 : 1,
      marker: { color: PLANNER_COLORS.scenario },
      hovertemplate: "€%{y:,.0f}<extra>Scenario spend</extra>",
    });
  }

  const catalog = plan.plan_daily?.catalog ?? [];
  const sendOuts = fcDates.filter((_, i) => (catalog[i] ?? 0) > 0);
  if (sendOuts.length) {
    // Legend entry for the dotted send-out lines (shapes have no legend of their own)
    traces.push({
      type: "scatter",
      mode: "lines",
      name: "Planned catalog send-out",
      x: [null],
      y: [null],
      line: { color: PALETTE.brandGoldLight, width: 1.5, dash: "dot" },
      hoverinfo: "skip",
    });
  }

  // A catalog send-out costs many days of digital spend, so the spend axis is cut above the busiest non-catalog
  // day; the cut bars keep their full value in the hover and, in the plan window, get a label.
  const nonCatalog = [
    ...hIdx.map((i, k) => hSpend[k] - (history!.spend.catalog?.[i] ?? 0)),
    ...plan.daily.map((r, i) => r.planned_spend - (catalog[i] ?? 0)),
    ...(primary ? primary.result.daily.map((r, i) => r.planned_spend - (primary.result.scenario_plan_daily?.catalog?.[i] ?? 0)) : []),
  ];
  const spendCap = sendOuts.length || hIdx.some((i) => (history!.spend.catalog?.[i] ?? 0) > 0) ? Math.max(...nonCatalog, 1) * 1.35 : null;
  // Labelled only while days are wide enough for the labels not to overlap (about half a year shown)
  const cutLabels = spendCap && hDates.length + fcDates.length <= 200
    ? plan.daily.flatMap((r) =>
        r.planned_spend > spendCap
          ? [{ x: r.date, xref: "x", y: 1, yref: "y2 domain", yanchor: "bottom", text: eurCompact(r.planned_spend), showarrow: false, font: { size: 10, color: PALETTE.inkSecondary } }]
          : []
      )
    : [];
  const boundary = shiftMs(fcStart, -HALF_DAY_MS);
  const shapes: Trace[] = [
    {
      type: "rect",
      xref: "x",
      yref: "paper",
      x0: boundary,
      x1: shiftMs(fcEnd, HALF_DAY_MS),
      y0: 0,
      y1: 1,
      fillcolor: "rgba(140, 114, 59, 0.07)",
      line: { width: 0 },
      layer: "below",
    },
    { type: "line", xref: "x", yref: "paper", x0: boundary, x1: boundary, y0: 0, y1: 1, line: { color: PALETTE.borderStrong, width: 1 } },
    ...sendOuts.map((d) => ({
      type: "line",
      xref: "x",
      yref: "y domain",
      x0: d,
      x1: d,
      y0: 0,
      y1: 1,
      line: { color: PALETTE.brandGoldLight, width: 1, dash: "dot" },
    })),
  ];
  const annotations: Trace[] = [
    ...(hDates.length
      ? [{ x: boundary, xref: "x", y: 1, yref: "paper", yanchor: "top", xanchor: "right", text: "History ", showarrow: false, font: { size: 11, color: PALETTE.inkSecondary } }]
      : []),
    { x: boundary, xref: "x", y: 1, yref: "paper", yanchor: "top", xanchor: "left", text: " Forecast", showarrow: false, font: { size: 11, color: PALETTE.inkSecondary } },
    // Panel titles instead of y-axis titles, which collide with the tick labels on a phone
    ...[
      ["y domain", "Daily sales"],
      ["y2 domain", "Daily spend"],
    ].map(([yref, text]) => ({ x: 0, xref: "paper", y: 1, yref, yanchor: "bottom", xanchor: "left", text: `<b>${text}</b>`, showarrow: false, font: { size: 11, color: PALETTE.inkSecondary } })),
    ...cutLabels,
  ];

  const axis = { gridcolor: GRID, linecolor: AXIS, zerolinecolor: GRID, tickprefix: "€", tickformat: "~s", fixedrange: true };
  const layout = {
    height: compact ? 680 : 520,
    hovermode: "x unified",
    barmode: "overlay",
    showlegend: true,
    legend: compact
      ? { orientation: "h", yanchor: "top", y: -0.08, xanchor: "left", x: 0, font: { size: 10 }, bgcolor: "rgba(255,255,255,0)" }
      : { orientation: "h", yanchor: "bottom", y: 1.02, xanchor: "left", x: 0, font: { size: 11 }, bgcolor: "rgba(255,255,255,0)" },
    margin: compact ? { l: 48, r: 8, t: 28, b: 190 } : { l: 60, r: 16, t: 72, b: 40 },
    paper_bgcolor: "#FFFFFF",
    plot_bgcolor: "#FFFFFF",
    xaxis: { type: "date", gridcolor: GRID, linecolor: AXIS, anchor: "y2", hoverformat: "%a %d %b %Y" },
    yaxis: { ...axis, domain: [0.36, 0.97] },
    yaxis2: {
      ...axis,
      domain: [0, 0.25],
      ...(spendCap ? { range: [0, spendCap] } : { rangemode: "tozero" }),
    },
    shapes,
    annotations,
  };
  return { data: traces, layout };
}

const escapeName = (s: string) => s.replace(/</g, "&lt;").replace(/>/g, "&gt;");

/** Each changed channel's own effect on forecast sales (90 % credible interval as whiskers), plus the total. */
export function effectsFigure(rows: ScenarioChannelRow[], total: { delta: number; lo?: number | null; hi?: number | null }, compact = false) {
  const label = (r: ScenarioChannelRow) =>
    `${r.channel} (${r.base_spend > 0 ? `${r.multiplier >= 1 ? "+" : "−"}${Math.abs(Math.round((r.multiplier - 1) * 100))} %` : "no plan spend"})`;
  const ordered = [...rows].sort((a, b) => Math.abs(b.delta_sales ?? 0) - Math.abs(a.delta_sales ?? 0));
  const bars = [
    ...ordered.map((r) => ({ y: label(r), x: r.delta_sales ?? 0, lo: r.delta_sales_lo, hi: r.delta_sales_hi, row: r })),
    { y: "All changes", x: total.delta, lo: total.lo, hi: total.hi, row: null },
  ];
  const hasCi = bars.every((b) => b.lo != null && b.hi != null);
  // Value labels sit past the end of each whisker, so the axis is padded on the side(s) that have bars
  const ends = bars.map((b) => (b.x >= 0 ? (hasCi ? b.hi! : b.x) : hasCi ? b.lo! : b.x));
  const minX = Math.min(0, ...ends);
  const maxX = Math.max(0, ...ends);
  const span = maxX - minX || 1;
  const pad = compact ? 0.5 : 0.24;
  const range = [minX < 0 ? minX - pad * span : -0.02 * span, maxX > 0 ? maxX + pad * span : 0.02 * span];
  const ci = (b: (typeof bars)[number]) =>
    hasCi
      ? { type: "data", symmetric: false, array: [b.hi! - b.x], arrayminus: [b.x - b.lo!], color: PALETTE.inkSecondary, thickness: 1.2, width: 4 }
      : undefined;
  const traces: Trace[] = bars.map((b) => ({
    type: "bar",
    orientation: "h",
    name: b.y,
    y: [b.y],
    x: [b.x],
    marker: { color: b.row ? (b.x >= 0 ? PLANNER_COLORS.gain : PLANNER_COLORS.loss) : PLANNER_COLORS.total },
    error_x: ci(b),
    customdata: [[signedEur(b.row ? b.row.delta_spend : 0), b.row?.delta_roas == null ? "–" : b.row.delta_roas.toFixed(2), hasCi ? `${signedEur(b.lo!)} to ${signedEur(b.hi!)}` : "–"]],
    hovertemplate: b.row
      ? "Δ sales €%{x:,.0f} (90 %: %{customdata[2]})<br>Δ spend %{customdata[0]}<br>Δ sales / Δ spend %{customdata[1]}<extra>%{y}</extra>"
      : "Δ sales €%{x:,.0f} (90 %: %{customdata[2]})<extra>All changes</extra>",
  }));
  const annotations = bars.map((b, i) => ({
    x: ends[i],
    y: b.y,
    xanchor: b.x >= 0 ? "left" : "right",
    xshift: b.x >= 0 ? 6 : -6,
    text: eurCompact(b.x, true),
    showarrow: false,
    font: { size: 12, color: PALETTE.inkPrimary },
  }));
  return {
    data: traces,
    layout: {
      height: 70 + 38 * bars.length,
      showlegend: false,
      hovermode: "closest",
      barmode: "overlay",
      bargap: 0.35,
      margin: { l: compact ? 96 : 150, r: 8, t: 12, b: 40 },
      paper_bgcolor: "#FFFFFF",
      plot_bgcolor: "#FFFFFF",
      annotations,
      xaxis: {
        range,
        gridcolor: GRID,
        linecolor: AXIS,
        zeroline: true,
        zerolinecolor: PALETTE.inkSecondary,
        zerolinewidth: 1,
        tickprefix: "€",
        tickformat: "~s",
        fixedrange: true,
        title: { text: "Change in forecast sales vs plan", font: { size: 12, color: PALETTE.inkSecondary } },
      },
      yaxis: { autorange: "reversed", categoryorder: "array", categoryarray: bars.map((b) => b.y), fixedrange: true, automargin: true },
    },
  };
}
