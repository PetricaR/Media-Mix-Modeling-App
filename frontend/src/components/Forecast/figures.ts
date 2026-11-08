import type {
  BacktestWindow,
  PlotlySpec,
  RunHistory,
  ScenarioChannelRow,
  ScenarioResult,
  VariableContributionCategory,
  VariableContributionRow,
} from "../../api/types";
import { CHART, PALETTE, channelColor } from "../../theme";
import { addDays, eurCompact, signedEur, signedPct } from "./format";

/**
 * Colours of the forecast planner. The four lines that overlap in the forecast window (plan, current scenario and two
 * saved scenarios) were checked with the dataviz palette validator (lightness, chroma, CVD and normal-vision
 * separation on every pair, contrast on white), which is why at most two saved scenarios are drawn at once.
 */
export const PLANNER_COLORS = {
  actual: CHART.navy,
  plan: PALETTE.brandPrimary,
  scenario: "#008300",
  saved: ["#E87BA4", "#EDA100"],
  lastYear: CHART.muted,
  gain: PALETTE.brandPrimary,
  loss: PALETTE.semanticBad,
  total: CHART.navy,
};

/**
 * The forecasts of the validation and test windows. They never share days with the forecast-window lines, only with
 * actual sales and last year; that pair passes the validator on its own (CVD ΔE 28, normal 28, ≥ 3:1). Each window is
 * also shaded and labelled, so identity never rests on colour alone. `band` is the 90 % interval's fill.
 */
export const BACKTEST_COLORS: Record<BacktestWindow["id"], { line: string; shade: string; band: string; name: string }> = {
  validation: { line: "#8E44C9", shade: "rgba(142, 68, 201, 0.05)", band: "rgba(142, 68, 201, 0.16)", name: "Validation" },
  test: { line: "#C25A00", shade: "rgba(194, 90, 0, 0.05)", band: "rgba(194, 90, 0, 0.16)", name: "Test" },
};

/** 52 weeks, so every forecast day is compared with the same weekday one year earlier. */
export const LAST_YEAR_SHIFT_DAYS = 364;

const GRID = CHART.grid;
const AXIS = CHART.axis;
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

/** Legend label of the plan's band: nominally 90 %, with the coverage it reached on the back-test when known. */
export function bandLabel(coverage: number | null | undefined): string {
  return coverage != null ? `Plan: 90 % interval (held ${Math.round(100 * coverage)} % in back-test)` : "Plan: 90 % prediction interval";
}

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

  // Validation and test: each window forecast like a plan by a model refit on the days before it, with its 90 %
  // prediction interval (the plan's method), clipped to the history shown
  const actualByDate = new Map((history?.dates ?? []).map((d, i) => [d, history!.sales[i]]));
  const backtest = (history?.backtest ?? []).flatMap((w) => {
    const keep = w.dates.flatMap((d, i) => ((histStart == null || d >= histStart) && d < fcStart ? [i] : []));
    const pick = (arr: (number | null)[]) => keep.map((i) => arr[i]);
    return keep.length ? [{ window: w, dates: keep.map((i) => w.dates[i]), forecast: pick(w.forecast), lo: pick(w.lo), hi: pick(w.hi) }] : [];
  });
  for (const { window: w, dates, forecast, lo, hi } of backtest) {
    const style = BACKTEST_COLORS[w.id];
    const band = { type: "scatter", mode: "lines", x: dates, line: { width: 0 }, legendgroup: w.id, showlegend: false, hoverinfo: "skip" };
    traces.push(
      { ...band, name: `${style.name} upper 90 %`, y: hi },
      { ...band, name: `${style.name} 90 % interval`, y: lo, fill: "tonexty", fillcolor: style.band },
      {
        type: "scatter",
        mode: "lines",
        name: `${style.name} forecast (${w.label})`,
        legendgroup: w.id,
        x: dates,
        y: forecast,
        line: { color: style.line, width: 1.8 },
        customdata: dates.map((d, i) => {
          const actual = actualByDate.get(d);
          const f = forecast[i];
          return [actual && f != null ? `${signedPct((100 * (f - actual)) / actual)} vs actual` : "no actual", lo[i], hi[i]];
        }),
        hovertemplate: `€%{y:,.0f} (%{customdata[0]}; 90 %: €%{customdata[1]:,.0f} – €%{customdata[2]:,.0f})<extra>${style.name} forecast</extra>`,
      }
    );
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
      name: bandLabel(plan.backtest_coverage),
      x: fcDates,
      y: plan.daily.map((r) => r.prediction_lo),
      line: { width: 0 },
      fill: "tonexty",
      fillcolor: "rgba(25, 87, 210, 0.13)",
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
      marker: { color: "rgba(27, 49, 91, 0.26)" },
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
    marker: { color: "rgba(25, 87, 210, 0.32)" },
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
      line: { color: channelColor("Catalog"), width: 1.5, dash: "dot" },
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
      fillcolor: "rgba(25, 87, 210, 0.05)",
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
      line: { color: channelColor("Catalog"), width: 1, dash: "dot" },
    })),
  ];
  // Each back-test window is shaded and named at its top-left; the accuracy joins the name while the window is wide
  // enough for it (a share of the days shown, as the plot's pixel width is not known here)
  const shownDays = hDates.length + fcDates.length;
  const [full, short] = compact ? [0.6, 0.22] : [0.2, 0.08];
  const windowLabels: Trace[] = [];
  // The days before the first window only ever trained a model, so the whole timeline reads Training → Validation →
  // Test → Forecast
  const firstWindowStart = history?.backtest?.[0]?.start;
  const trainingDays = firstWindowStart ? hDates.filter((d) => d < firstWindowStart).length : 0;
  if (trainingDays && trainingDays / shownDays >= short) {
    windowLabels.push({ x: shiftMs(hDates[0], -HALF_DAY_MS), xref: "x", ...REGION_LABEL, xanchor: "left", text: " Training" });
  }
  for (const { window: w, dates } of backtest) {
    const style = BACKTEST_COLORS[w.id];
    const start = shiftMs(dates[0], -HALF_DAY_MS);
    shapes.unshift(
      { type: "rect", xref: "x", yref: "paper", x0: start, x1: shiftMs(dates[dates.length - 1], HALF_DAY_MS), y0: 0, y1: 1, fillcolor: style.shade, line: { width: 0 }, layer: "below" },
      { type: "line", xref: "x", yref: "paper", x0: start, x1: start, y0: 0, y1: 1, line: { color: PALETTE.borderStrong, width: 1, dash: "dot" } }
    );
    const share = dates.length / shownDays;
    if (share < short) continue;
    const accuracy = w.mae_over_mean != null && share >= full ? ` · MAE / mean ${(100 * w.mae_over_mean).toFixed(1)} %` : "";
    windowLabels.push({ x: start, xref: "x", ...REGION_LABEL, xanchor: "left", text: ` ${style.name}${accuracy}` });
  }
  const annotations: Trace[] = [
    // "History" would sit inside the test window, whose own label already says what those days are
    ...(hDates.length && !backtest.length
      ? [{ x: boundary, xref: "x", ...REGION_LABEL, xanchor: "right", text: "History " }]
      : []),
    ...windowLabels,
    { x: boundary, xref: "x", ...REGION_LABEL, xanchor: "left", text: " Forecast" },
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
    yaxis: { ...axis, domain: [0.36, 0.97], ...salesRangeWithHeadroom(traces) },
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

/** Region names (History, Validation, Test, Forecast) sit in a band at the top of the sales panel, below its title. */
const REGION_LABEL = { y: 1, yref: "y domain", yanchor: "top", showarrow: false, font: { size: 11, color: PALETTE.inkSecondary } };

/** The sales axis range with room above the highest line for the region names, so no line runs under them. */
function salesRangeWithHeadroom(traces: Trace[]): { range: [number, number] } | Record<string, never> {
  const ys = traces
    .filter((t) => t.type === "scatter" && t.yaxis == null)
    .flatMap((t) => (t.y as (number | null)[]).filter((v): v is number => v != null && Number.isFinite(v)));
  if (!ys.length) return {};
  const lo = Math.min(...ys);
  const hi = Math.max(...ys);
  const span = hi - lo || Math.abs(hi) || 1;
  return { range: [lo - 0.04 * span, hi + 0.14 * span] };
}

const escapeName = (s: string) => s.replace(/</g, "&lt;").replace(/>/g, "&gt;");

/** Each changed channel's own effect on forecast sales (90 % credible interval as whiskers), plus the total. */
export function effectsFigure(rows: ScenarioChannelRow[], total: { delta: number; lo?: number | null; hi?: number | null }, compact = false) {
  const label = (r: ScenarioChannelRow) =>
    `${r.channel} (${
      r.base_spend > 0
        ? `${r.multiplier >= 1 ? "+" : "−"}${Math.abs(Math.round((r.multiplier - 1) * 100))} %`
        : r.scenario_spend > 0
          ? `+${eurCompact(r.scenario_spend)}`
          : "no plan spend"
    })`;
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

/** Channels keep their app-wide colour; the non-media variables are navy and slate steps. */
export const VARIABLE_COLORS: Record<string, string> = {
  sea: channelColor("sea"),
  catalog: channelColor("catalog"),
  display: channelColor("display"),
  psm: channelColor("psm"),
  display_network: channelColor("display_network"),
  social_media: channelColor("social_media"),
  affiliate: channelColor("affiliate"),
  seasonality: "#5B6472",
  weekdays: "#717B89",
  holidays: "#8993A2",
  trend: "#9EA9B8",
  baseline: CHART.navy,
};

export function variableColor(key: string, category?: VariableContributionCategory): string {
  const norm = key.trim().toLowerCase().replace(/[\s-]+/g, "_");
  if (VARIABLE_COLORS[norm]) return VARIABLE_COLORS[norm];
  if (category === "media") return PALETTE.brandPrimary;
  if (category === "control_channel") return channelColor("affiliate");
  if (category === "seasonality") return VARIABLE_COLORS.seasonality;
  if (category === "calendar") return VARIABLE_COLORS.weekdays;
  if (category === "trend") return VARIABLE_COLORS.trend;
  return VARIABLE_COLORS.baseline;
}

export function categoryBadgeMeta(category: VariableContributionCategory): {
  label: string;
  bg: string;
  color: string;
  border: string;
} {
  switch (category) {
    case "media":
      return { label: "Paid Media", bg: PALETTE.bgAccentTint, color: PALETTE.brandPrimaryDark, border: "#B9D0F5" };
    case "control_channel":
      return { label: "Control (Commission)", bg: PALETTE.bgSubtle, color: PALETTE.inkSecondary, border: PALETTE.borderSubtle };
    case "seasonality":
      return { label: "Seasonality", bg: PALETTE.bgSubtle, color: PALETTE.inkSecondary, border: PALETTE.borderSubtle };
    case "calendar":
      return { label: "Calendar / DOW", bg: PALETTE.bgSubtle, color: PALETTE.inkSecondary, border: PALETTE.borderSubtle };
    case "trend":
      return { label: "Trend", bg: PALETTE.bgSubtle, color: PALETTE.inkSecondary, border: PALETTE.borderSubtle };
    case "baseline":
    default:
      return { label: "Baseline", bg: PALETTE.bgCanvas, color: PALETTE.inkPrimary, border: PALETTE.borderSubtle };
  }
}

/**
 * Guarantee a complete additive variable decomposition array even if an older payload omits `variable_contributions`.
 */
export function resolveVariableContributions(result: ScenarioResult): VariableContributionRow[] {
  if (result.variable_contributions && result.variable_contributions.length > 0) {
    return result.variable_contributions;
  }
  const nDays = result.daily.length || result.horizon_days || 1;
  const baseSales = result.base_total_sales || 1;
  const altSales = result.scenario_total_sales || 1;
  const mediaRows: VariableContributionRow[] = [];
  const controlRows: VariableContributionRow[] = [];
  let sumBase = 0;
  let sumAlt = 0;
  const missingContribution: string[] = [];

  for (const ch of result.channel_summary) {
    const isCtrl = ch.role === "control";
    // Results stored before the decomposition existed carry no contribution: none is assumed (the baseline row says so)
    const bContrib = ch.base_contribution ?? 0;
    if (ch.base_contribution == null && !isCtrl) missingContribution.push(ch.channel);
    const dContrib = ch.delta_sales ?? 0;
    const sContrib = ch.scenario_contribution ?? bContrib + dContrib;
    sumBase += bContrib;
    sumAlt += sContrib;
    const dailyDelta = result.channel_daily_delta?.[ch.slug] ?? Array(nDays).fill(dContrib / nDays);
    const dailyBase = Array(nDays).fill(bContrib / nDays);
    const dailyScen = dailyBase.map((v, i) => v + (dailyDelta[i] ?? 0));

    const row: VariableContributionRow = {
      key: ch.slug,
      label: isCtrl ? `${ch.channel} (control)` : ch.channel,
      category: isCtrl ? "control_channel" : "media",
      editable: !isCtrl,
      multiplier: ch.multiplier,
      base_spend: ch.base_spend,
      scenario_spend: ch.scenario_spend,
      delta_spend: ch.delta_spend,
      base_contribution: Math.round(bContrib),
      base_ci_lo: null,
      base_ci_hi: null,
      scenario_contribution: Math.round(sContrib),
      scenario_ci_lo: ch.contribution_lo ?? null,
      scenario_ci_hi: ch.contribution_hi ?? null,
      delta_contribution: Math.round(dContrib),
      delta_ci_lo: ch.delta_sales_lo ?? null,
      delta_ci_hi: ch.delta_sales_hi ?? null,
      base_share_pct: Number(((100 * bContrib) / baseSales).toFixed(2)),
      scenario_share_pct: Number(((100 * sContrib) / altSales).toFixed(2)),
      base_roi: !isCtrl && ch.base_spend > 0 ? Number((bContrib / ch.base_spend).toFixed(2)) : null,
      roi: !isCtrl && ch.scenario_spend > 0 ? Number((sContrib / ch.scenario_spend).toFixed(2)) : null,
      mroi: !isCtrl ? (ch.delta_roas ?? null) : null,
      daily_base: dailyBase,
      daily_scenario: dailyScen,
      daily_delta: dailyDelta,
    };
    if (isCtrl) controlRows.push(row);
    else mediaRows.push(row);
  }

  mediaRows.sort((a, b) => b.base_contribution - a.base_contribution);
  const bBase = Math.max(0, result.base_total_sales - sumBase);
  const sBase = Math.max(0, result.scenario_total_sales - sumAlt);
  const baselineRow: VariableContributionRow = {
    key: "baseline",
    label: missingContribution.length
      ? `Baseline + ${missingContribution.join(", ")} (no estimate in this result)`
      : "Baseline demand (controls at reference)",
    category: "baseline",
    editable: false,
    multiplier: 1,
    base_spend: null,
    scenario_spend: null,
    delta_spend: null,
    base_contribution: Math.round(bBase),
    base_ci_lo: null,
    base_ci_hi: null,
    scenario_contribution: Math.round(sBase),
    scenario_ci_lo: null,
    scenario_ci_hi: null,
    delta_contribution: Math.round(sBase - bBase),
    delta_ci_lo: null,
    delta_ci_hi: null,
    base_share_pct: Number(((100 * bBase) / baseSales).toFixed(2)),
    scenario_share_pct: Number(((100 * sBase) / altSales).toFixed(2)),
    base_roi: null,
    roi: null,
    mroi: null,
    daily_base: result.daily.map((d) => Math.max(0, d.baseline_forecast - sumBase / nDays)),
    daily_scenario: result.daily.map((d) => Math.max(0, d.scenario_forecast - sumAlt / nDays)),
    daily_delta: Array(nDays).fill(0),
  };

  return [baselineRow, ...controlRows, ...mediaRows];
}

/**
 * Panel 1: Shapley Waterfall Decomposition of Forecast Sales.
 * - `mode === "absolute"`: Shows how Baseline Intercept + Controls/Seasonality/Calendar + each Paid Media channel
 *   additively build up to 100 % of the Scenario Forecast (without a redundant Total Forecast bar).
 * - `mode === "delta"`: Shows each modified variable's Shapley Δ contribution.
 */
export function buildShapleyWaterfallFigure(
  result: ScenarioResult,
  mode: "absolute" | "delta" = "absolute",
  highlightKey?: string | null
): PlotlySpec {
  const vars = resolveVariableContributions(result);

  const shortLabel = (v: VariableContributionRow) =>
    v.key === "baseline"
      ? "Baseline"
      : v.label
          .replace("Annual Seasonality (Fourier)", "Seasonality")
          .replace("Weekday Profile", "Weekdays")
          .replace(/^Long-Term Trend.*/, "Trend")
          .replace(/ \(control[^)]*\)$/i, " (ctrl)")
          .replace("Holidays & Events", "Holidays");

  if (mode === "delta") {
    const changed = vars
      .filter((v) => Math.abs(v.delta_contribution) >= 50 || Math.abs(v.delta_spend ?? 0) >= 50)
      .sort((a, b) => b.delta_contribution - a.delta_contribution);

    const deltaVars =
      changed.length > 0
        ? changed
        : vars.filter((v) => v.category === "media" && ((v.scenario_spend ?? 0) >= 100 || Math.abs(v.scenario_contribution) >= 100));

    const xLabels = deltaVars.map(shortLabel);
    const measures = deltaVars.map(() => "relative");
    const yValues = deltaVars.map((v) => v.delta_contribution);
    const textLabels = deltaVars.map((v) => eurCompact(v.delta_contribution, true));

    let running = 0;
    const cumBounds = deltaVars.map((v) => {
      running += v.delta_contribution;
      return running;
    });
    const maxDelta = Math.max(1000, 0, ...cumBounds, ...yValues);
    const minDelta = Math.min(0, ...cumBounds, ...yValues);
    const span = maxDelta - minDelta || 1000;

    return {
      data: [
        {
          type: "waterfall",
          orientation: "v",
          x: xLabels,
          y: yValues,
          measure: measures,
          text: textLabels,
          textposition: "outside",
          cliponaxis: false,
          textfont: { size: 11, color: PALETTE.inkPrimary },
          connector: { line: { color: CHART.axis, width: 1, dash: "dot" } },
          increasing: { marker: { color: PLANNER_COLORS.gain, line: { color: PALETTE.brandPrimaryDark, width: 1 } } },
          decreasing: { marker: { color: PLANNER_COLORS.loss, line: { color: "#791A18", width: 1 } } },
          customdata: deltaVars.map((v) => [
            v.label,
            eurCompact(v.delta_contribution, true),
            eurCompact(v.delta_spend ?? 0, true),
            v.mroi != null ? `${v.mroi.toFixed(2)}×` : "—",
          ]),
          hovertemplate:
            "<b>%{customdata[0]}</b><br>" +
            "Δ Forecast Sales: <b>%{customdata[1]}</b><br>" +
            "Δ Input Spend: %{customdata[2]} · mROI: %{customdata[3]}<extra></extra>",
        },
      ],
      layout: {
        height: 360,
        margin: { l: 58, r: 20, t: 32, b: 86 },
        paper_bgcolor: "#FFFFFF",
        plot_bgcolor: "#FFFFFF",
        showlegend: false,
        xaxis: {
          tickangle: -25,
          tickfont: { size: 11, color: PALETTE.inkPrimary },
          gridcolor: "rgba(0,0,0,0)",
          linecolor: AXIS,
        },
        yaxis: {
          title: { text: "Scenario Δ Forecast Sales (€)", font: { size: 11, color: PALETTE.inkSecondary } },
          tickprefix: "€",
          tickformat: "~s",
          range: [minDelta < 0 ? minDelta - 0.22 * span : -0.05 * span, maxDelta + 0.24 * span],
          gridcolor: GRID,
          linecolor: AXIS,
          zeroline: true,
          zerolinecolor: PALETTE.inkSecondary,
        },
      },
    };
  }

  // Absolute Shapley waterfall decomposition: order Baseline -> Controls (descending) -> Paid Media (descending)
  const baselineVars = vars.filter((v) => v.category === "baseline");
  const structuralVars = vars
    .filter(
      (v) =>
        v.category !== "baseline" &&
        v.category !== "media" &&
        (Math.abs(v.scenario_contribution) >= 100 || Math.abs(v.base_contribution) >= 100 || (v.scenario_spend ?? 0) >= 100)
    )
    .sort((a, b) => b.scenario_contribution - a.scenario_contribution);
  const mediaVars = vars
    .filter(
      (v) =>
        v.category === "media" &&
        (Math.abs(v.scenario_contribution) >= 100 || Math.abs(v.base_contribution) >= 100 || (v.scenario_spend ?? 0) >= 100)
    )
    .sort((a, b) => b.scenario_contribution - a.scenario_contribution);

  const activeVars = [...baselineVars, ...structuralVars, ...mediaVars];

  const xLabels = activeVars.map(shortLabel);
  const measures: string[] = activeVars.map((_, idx) => (idx === 0 ? "absolute" : "relative"));
  const yValues = activeVars.map((v) => v.scenario_contribution);
  const textLabels = activeVars.map(
    (v) =>
      `${eurCompact(v.scenario_contribution, v.scenario_contribution < 0)} (${v.scenario_share_pct >= 0 ? "+" : ""}${v.scenario_share_pct.toFixed(1)}%)`
  );

  let runningScenario = 0;
  let runningTotalPct = 0;
  const customRows = activeVars.map((v) => {
    runningScenario += v.scenario_contribution;
    runningTotalPct += v.scenario_share_pct;
    return [
      v.label,
      eurCompact(v.scenario_contribution, v.scenario_contribution < 0),
      v.scenario_share_pct.toFixed(1),
      eurCompact(runningScenario),
      runningTotalPct.toFixed(1),
      eurCompact(v.base_contribution, v.base_contribution < 0),
      eurCompact(v.delta_contribution, true),
    ];
  });

  const traces: Trace[] = [
    {
      type: "waterfall",
      name: "Scenario decomposition",
      orientation: "v",
      x: xLabels,
      y: yValues,
      measure: measures,
      text: textLabels,
      textposition: "outside",
      cliponaxis: false,
      textfont: { size: 10, color: PALETTE.inkPrimary },
      connector: { line: { color: CHART.axis, width: 1, dash: "dot" } },
      increasing: { marker: { color: PLANNER_COLORS.gain, line: { color: PALETTE.brandPrimaryDark, width: 1 } } },
      decreasing: { marker: { color: PLANNER_COLORS.loss, line: { color: "#791A18", width: 1 } } },
      totals: { marker: { color: PALETTE.brandNavy, line: { color: PALETTE.brandNavy, width: 1 } } },
      customdata: customRows,
      hovertemplate:
        "<b>%{customdata[0]}</b><br>" +
        "Contribution: <b>%{customdata[1]}</b> (%{customdata[2]}% of forecast)<br>" +
        "Cumulative build-up: <b>%{customdata[3]}</b> (%{customdata[4]}%)<br>" +
        "Plan baseline: %{customdata[5]} · Δ vs plan: %{customdata[6]}<extra></extra>",
    },
  ];

  // If scenario differs from baseline plan, overlay cumulative baseline step markers
  let cumBase = 0;
  const baseCum = activeVars.map((v) => {
    cumBase += v.base_contribution;
    return Math.round(cumBase);
  });
  if (Math.abs(result.delta_sales) >= 500) {
    traces.push({
      type: "scatter",
      mode: "lines+markers",
      name: "Plan baseline path",
      x: xLabels,
      y: baseCum,
      line: { color: CHART.muted, width: 1.5, dash: "dot" },
      marker: { size: 6, color: CHART.muted, symbol: "diamond" },
      customdata: baseCum.map((c) => eurCompact(c)),
      hovertemplate: "<b>%{x}</b><br>Cumulative plan baseline: <b>%{customdata}</b><extra></extra>",
    });
  }

  let cumScen = 0;
  const scenCumSteps = activeVars.map((v) => {
    cumScen += v.scenario_contribution;
    return cumScen;
  });
  const maxCum = Math.max(result.base_total_sales, result.scenario_total_sales, ...scenCumSteps, 1);
  const minCum = Math.min(0, ...scenCumSteps, ...activeVars.map((v) => v.scenario_contribution));

  return {
    data: traces,
    layout: {
      height: 360,
      margin: { l: 58, r: 20, t: 32, b: 88 },
      paper_bgcolor: "#FFFFFF",
      plot_bgcolor: "#FFFFFF",
      showlegend: traces.length > 1,
      legend: { orientation: "h", x: 0, y: 1.11, font: { size: 11 } },
      xaxis: {
        tickangle: -28,
        tickfont: {
          size: 11,
          color: PALETTE.inkPrimary,
        },
        gridcolor: "rgba(0,0,0,0)",
        linecolor: AXIS,
      },
      yaxis: {
        title: { text: "Cumulative Forecast Sales (€)", font: { size: 11, color: PALETTE.inkSecondary } },
        tickprefix: "€",
        tickformat: "~s",
        range: [minCum < 0 ? minCum * 1.25 : 0, maxCum * 1.18],
        gridcolor: GRID,
        linecolor: AXIS,
        zeroline: true,
        zerolinecolor: AXIS,
      },
      ...(highlightKey
        ? {
            annotations: activeVars
              .filter((v) => v.key === highlightKey)
              .map((v) => ({
                x: shortLabel(v),
                y: maxCum * 1.11,
                text: `Selected: ${v.label} (${eurCompact(v.scenario_contribution)})`,
                showarrow: false,
                font: { size: 11, color: PALETTE.brandPrimary },
              })),
          }
        : {}),
    },
  };
}

/**
 * Panel 2: Horizontal SHAP-style Variable Attribution Bar Chart with 90 % Credible Intervals.
 */
export function buildVariableContributionBarFigure(
  vars: VariableContributionRow[],
  showScenarioComparison = false,
  highlightKey?: string | null
): PlotlySpec {
  const filtered = vars
    .filter(
      (v) =>
        v.category === "baseline" ||
        Math.abs(v.scenario_contribution) >= 100 ||
        Math.abs(v.base_contribution) >= 100 ||
        (v.scenario_spend ?? 0) >= 100
    )
    .sort((a, b) => a.scenario_contribution - b.scenario_contribution); // bottom-to-top so largest is at top

  const yLabels = filtered.map((v) => v.label.replace(" (Fourier)", ""));
  const scenVals = filtered.map((v) => v.scenario_contribution);
  const baseVals = filtered.map((v) => v.base_contribution);

  const errPlus = filtered.map((v) =>
    v.scenario_ci_hi != null ? Math.max(0, v.scenario_ci_hi - v.scenario_contribution) : 0
  );
  const errMinus = filtered.map((v) =>
    v.scenario_ci_lo != null ? Math.max(0, v.scenario_contribution - v.scenario_ci_lo) : 0
  );
  const hasAnyCi = filtered.some((v) => v.scenario_ci_lo != null && v.scenario_ci_hi != null);

  const colors = filtered.map((v) => {
    const baseCol = variableColor(v.key, v.category);
    if (highlightKey && v.key !== highlightKey) return `${baseCol}55`;
    return baseCol;
  });

  const traces: Trace[] = [];
  if (showScenarioComparison) {
    traces.push({
      type: "bar",
      orientation: "h",
      name: "Plan baseline",
      y: yLabels,
      x: baseVals,
      marker: { color: CHART.axis, line: { color: "#9EA9B8", width: 1 } },
      customdata: filtered.map((v) => eurCompact(v.base_contribution, v.base_contribution < 0)),
      hovertemplate: "<b>%{y}</b><br>Baseline contribution: <b>%{customdata}</b><extra></extra>",
    });
  }

  traces.push({
    type: "bar",
    orientation: "h",
    name: showScenarioComparison ? "Active scenario" : "Forecast contribution (90% CI)",
    y: yLabels,
    x: scenVals,
    marker: { color: colors },
    ...(hasAnyCi
      ? {
          error_x: {
            type: "data",
            symmetric: false,
            array: errPlus,
            arrayminus: errMinus,
            color: PALETTE.inkSecondary,
            thickness: 1.3,
            width: 4,
          },
        }
      : {}),
    customdata: filtered.map((v) => [
      eurCompact(v.scenario_contribution, v.scenario_contribution < 0),
      v.scenario_share_pct.toFixed(1),
      v.scenario_ci_lo != null && v.scenario_ci_hi != null
        ? `${eurCompact(v.scenario_ci_lo)} to ${eurCompact(v.scenario_ci_hi)}`
        : "deterministic / point",
      v.roi != null && v.category === "media" ? `${v.roi.toFixed(2)}×` : "n/a",
    ]),
    hovertemplate:
      "<b>%{y}</b><br>" +
      "Forecast contribution: <b>%{customdata[0]}</b> (%{customdata[1]}% share)<br>" +
      "90% credible interval: %{customdata[2]}<br>" +
      "Channel ROI: %{customdata[3]}<extra></extra>",
  });

  const rightEnds = filtered.map((_, i) =>
    scenVals[i] >= 0 ? Math.max(scenVals[i] + errPlus[i], baseVals[i]) : Math.min(scenVals[i] - errMinus[i], baseVals[i])
  );
  const maxRight = Math.max(1000, ...rightEnds);
  const minLeft = Math.min(0, ...rightEnds);
  const span = maxRight - minLeft || 1000;

  const annotations = filtered.map((v, i) => ({
    x: scenVals[i] >= 0 ? scenVals[i] + errPlus[i] : scenVals[i] - errMinus[i],
    y: yLabels[i],
    xanchor: scenVals[i] >= 0 ? "left" : "right",
    xshift: scenVals[i] >= 0 ? 6 : -6,
    text: `${eurCompact(v.scenario_contribution, v.scenario_contribution < 0)} (${v.scenario_share_pct.toFixed(1)}%)`,
    showarrow: false,
    font: { size: 10.5, color: PALETTE.inkPrimary },
  }));

  return {
    data: traces,
    layout: {
      height: 360,
      barmode: showScenarioComparison ? "group" : "overlay",
      bargap: 0.28,
      margin: { l: 175, r: 28, t: 28, b: 48 },
      paper_bgcolor: "#FFFFFF",
      plot_bgcolor: "#FFFFFF",
      showlegend: showScenarioComparison,
      legend: { orientation: "h", x: 0, y: 1.1, font: { size: 11 } },
      annotations,
      xaxis: {
        title: { text: "Attributed Forecast Sales (€)", font: { size: 11, color: PALETTE.inkSecondary } },
        range: [minLeft < 0 ? minLeft - 0.24 * span : 0, maxRight + 0.34 * span],
        tickprefix: "€",
        tickformat: "~s",
        gridcolor: GRID,
        linecolor: AXIS,
        zeroline: true,
        zerolinecolor: PALETTE.inkSecondary,
      },
      yaxis: {
        tickfont: { size: 11, color: PALETTE.inkPrimary },
        automargin: true,
      },
    },
  };
}

/**
 * Panel 3: Direct Input Spend vs. Output Forecast Sales Contribution across active Paid Media channels (with ROI & 90% CIs).
 */
export function buildSpendVsContributionFigure(
  vars: VariableContributionRow[],
  highlightKey?: string | null
): PlotlySpec {
  // Strictly active Paid Media channels (excludes non-causal controls like Affiliate and €0 inactive channels)
  const spendVars = vars
    .filter(
      (v) =>
        v.category === "media" &&
        ((v.scenario_spend ?? 0) >= 100 ||
          (v.base_spend ?? 0) >= 100 ||
          Math.abs(v.scenario_contribution) >= 100)
    )
    .sort((a, b) => a.scenario_contribution - b.scenario_contribution); // bottom-to-top so largest channel sits at top

  const yLabels = spendVars.map((v) => v.label);
  const spendVals = spendVars.map((v) => v.scenario_spend ?? 0);
  const baseContribVals = spendVars.map((v) => v.base_contribution);
  const scenContribVals = spendVars.map((v) => v.scenario_contribution);
  const hasScenarioDiff = spendVars.some((v) => Math.abs(v.delta_contribution) >= 50 || Math.abs(v.delta_spend ?? 0) >= 50);

  const dim = (hex: string, key: string) => (highlightKey && key !== highlightKey ? `${hex}55` : hex);

  const traces: Trace[] = [
    {
      type: "bar",
      orientation: "h",
      name: "Input Spend",
      y: yLabels,
      x: spendVals,
      marker: {
        color: spendVars.map((v) => dim(CHART.muted, v.key)),
        line: { color: PALETTE.inkSecondary, width: 1 },
      },
      text: spendVars.map((v) => `Spend: ${eurCompact(v.scenario_spend ?? 0)}`),
      textposition: "outside",
      cliponaxis: false,
      textfont: { size: 10, color: PALETTE.inkSecondary },
      customdata: spendVars.map((v) => [
        eurCompact(v.scenario_spend ?? 0),
        eurCompact(v.base_spend ?? 0),
        eurCompact(v.delta_spend ?? 0, true),
      ]),
      hovertemplate:
        "<b>%{y}</b><br>" +
        "Input Spend: <b>%{customdata[0]}</b><br>" +
        "Plan Spend: %{customdata[1]} (Δ %{customdata[2]})<extra></extra>",
    },
  ];

  if (hasScenarioDiff) {
    traces.push({
      type: "bar",
      orientation: "h",
      name: "Baseline Output Sales",
      y: yLabels,
      x: baseContribVals,
      marker: {
        color: spendVars.map((v) => dim("#90BAF1", v.key)),
        line: { color: "#4385E4", width: 1 },
      },
      customdata: spendVars.map((v) => [
        eurCompact(v.base_contribution),
        v.base_roi != null ? `${v.base_roi.toFixed(2)}×` : "—",
      ]),
      hovertemplate:
        "<b>%{y}</b><br>" +
        "Baseline Sales Contribution: <b>%{customdata[0]}</b> (ROI %{customdata[1]})<extra></extra>",
    });
  }

  traces.push({
    type: "bar",
    orientation: "h",
    name: hasScenarioDiff ? "Scenario Output Sales" : "Output Sales Contribution",
    y: yLabels,
    x: scenContribVals,
    marker: {
      color: spendVars.map((v) => dim(variableColor(v.key, v.category), v.key)),
      line: { color: PALETTE.brandPrimaryDark, width: 1 },
    },
    text: spendVars.map((v) =>
      v.roi != null
        ? `${eurCompact(v.scenario_contribution)} · ROI ${v.roi.toFixed(2)}×`
        : eurCompact(v.scenario_contribution)
    ),
    textposition: "outside",
    cliponaxis: false,
    textfont: { size: 10.5, color: PALETTE.inkPrimary },
    customdata: spendVars.map((v) => [
      eurCompact(v.scenario_contribution),
      eurCompact(v.scenario_spend ?? 0),
      v.roi != null ? `${v.roi.toFixed(2)}×` : "—",
      v.scenario_ci_lo != null && v.scenario_ci_hi != null
        ? `${eurCompact(v.scenario_ci_lo)} to ${eurCompact(v.scenario_ci_hi)}`
        : "point estimate",
      eurCompact(v.delta_spend ?? 0, true),
      eurCompact(v.delta_contribution, true),
      v.mroi != null ? `${v.mroi.toFixed(2)}×` : "—",
    ]),
    hovertemplate:
      "<b>%{y}</b><br>" +
      "Output Sales Contribution: <b>%{customdata[0]}</b> (90% CI: %{customdata[3]})<br>" +
      "Input Spend: <b>%{customdata[1]}</b> · ROI: <b>%{customdata[2]}</b><br>" +
      "Δ Spend: %{customdata[4]} · Δ Sales: %{customdata[5]} · mROI: %{customdata[6]}<extra></extra>",
  });

  const maxX = Math.max(
    1000,
    ...spendVars.map((_, i) => Math.max(spendVals[i], baseContribVals[i], scenContribVals[i]))
  );

  return {
    data: traces,
    layout: {
      height: 360,
      barmode: "group",
      bargap: 0.24,
      bargroupgap: 0.1,
      margin: { l: 130, r: 32, t: 36, b: 48 },
      paper_bgcolor: "#FFFFFF",
      plot_bgcolor: "#FFFFFF",
      showlegend: true,
      legend: { orientation: "h", x: 0, y: 1.12, font: { size: 11 } },
      xaxis: {
        title: { text: "Input Spend vs. Output Sales Contribution (€)", font: { size: 11, color: PALETTE.inkSecondary } },
        range: [0, maxX * 1.38],
        tickprefix: "€",
        tickformat: "~s",
        gridcolor: GRID,
        linecolor: AXIS,
        zeroline: true,
        zerolinecolor: AXIS,
      },
      yaxis: {
        tickfont: { size: 11, color: PALETTE.inkPrimary },
        automargin: true,
      },
    },
  };
}

/**
 * Panel 4 / Section 4: Daily Stacked Variable Attribution across the Forecast Window.
 * - `mode === "absolute"`: Shows each day's forecast decomposed into Baseline & Controls + each Paid Media channel.
 * - `mode === "delta"`: Shows each day's scenario change decomposed by channel (`daily_delta`).
 */
export function buildDailyVariableStackFigure(
  result: ScenarioResult,
  mode: "absolute" | "delta" = "absolute",
  highlightKey?: string | null
): PlotlySpec {
  const vars = resolveVariableContributions(result);
  const dates = result.daily.map((d) => d.date);

  if (mode === "delta") {
    const changed = vars.filter((v) => v.daily_delta.some((d) => Math.abs(d) >= 10));
    const traces: Trace[] = changed.map((v) => {
      const col = variableColor(v.key, v.category);
      return {
        type: "bar",
        name: v.label,
        x: dates,
        y: v.daily_delta,
        marker: { color: highlightKey && v.key !== highlightKey ? `${col}44` : col },
        hovertemplate: `<b>${escapeName(v.label)}</b>: €%{y:,.0f}/day<extra></extra>`,
      };
    });

    traces.push({
      type: "scatter",
      mode: "lines",
      name: "Net Δ Forecast Sales",
      x: dates,
      y: result.daily.map((d) => d.scenario_forecast - d.baseline_forecast),
      line: { color: PALETTE.brandNavy, width: 2.2 },
      hovertemplate: "<b>Net Δ Sales</b>: €%{y:,.0f}/day<extra></extra>",
    });

    return {
      data: traces,
      layout: {
        height: 390,
        barmode: "relative",
        hovermode: "x unified",
        margin: { l: 60, r: 20, t: 40, b: 44 },
        paper_bgcolor: "#FFFFFF",
        plot_bgcolor: "#FFFFFF",
        showlegend: true,
        legend: { orientation: "h", x: 0, y: 1.12, font: { size: 11 } },
        xaxis: { type: "date", gridcolor: GRID, linecolor: AXIS, hoverformat: "%a %d %b %Y" },
        yaxis: {
          title: { text: "Daily Δ Forecast Sales (€)", font: { size: 11, color: PALETTE.inkSecondary } },
          tickprefix: "€",
          tickformat: "~s",
          gridcolor: GRID,
          linecolor: AXIS,
          zeroline: true,
          zerolinecolor: PALETTE.inkSecondary,
        },
      },
    };
  }

  // Group non-media structural baseline & calendar variables into "Baseline + Seasonality + Calendar" and "Affiliate (Control)"
  const nonMedia = vars.filter((v) => v.category !== "media" && v.category !== "control_channel");
  const controlChannels = vars.filter((v) => v.category === "control_channel");
  const mediaVars = vars
    .filter((v) => v.category === "media")
    .sort((a, b) => b.scenario_contribution - a.scenario_contribution);

  const structuralDaily = dates.map((_, t) =>
    nonMedia.reduce((acc, v) => acc + (v.daily_scenario[t] ?? 0), 0)
  );

  const traces: Trace[] = [
    {
      type: "bar",
      name: "Baseline, Seasonality & Weekday",
      x: dates,
      y: structuralDaily,
      marker: {
        color:
          highlightKey && !nonMedia.some((v) => v.key === highlightKey)
            ? "#94A3B844"
            : "#9EA9B8",
      },
      hovertemplate: "<b>Baseline, Seasonality & Weekday</b>: €%{y:,.0f}<extra></extra>",
    },
    ...controlChannels.map((v) => {
      const col = variableColor(v.key, v.category);
      return {
        type: "bar",
        name: v.label,
        x: dates,
        y: v.daily_scenario,
        marker: { color: highlightKey && v.key !== highlightKey ? `${col}44` : col },
        hovertemplate: `<b>${escapeName(v.label)}</b>: €%{y:,.0f}<extra></extra>`,
      };
    }),
    ...mediaVars.map((v) => {
      const col = variableColor(v.key, v.category);
      return {
        type: "bar",
        name: v.label,
        x: dates,
        y: v.daily_scenario,
        marker: { color: highlightKey && v.key !== highlightKey ? `${col}44` : col },
        hovertemplate: `<b>${escapeName(v.label)}</b>: €%{y:,.0f}<extra></extra>`,
      };
    }),
    {
      type: "scatter",
      mode: "lines",
      name: "Total Scenario Forecast",
      x: dates,
      y: result.daily.map((d) => d.scenario_forecast),
      line: { color: PALETTE.brandNavy, width: 2.2 },
      hovertemplate: "<b>Total Scenario Forecast</b>: €%{y:,.0f}<extra></extra>",
    },
  ];

  if (Math.abs(result.delta_sales) >= 500) {
    traces.push({
      type: "scatter",
      mode: "lines",
      name: "Plan Baseline Forecast",
      x: dates,
      y: result.daily.map((d) => d.baseline_forecast),
      line: { color: PALETTE.inkSecondary, width: 1.6, dash: "dot" },
      hovertemplate: "<b>Plan Baseline</b>: €%{y:,.0f}<extra></extra>",
    });
  }

  return {
    data: traces,
    layout: {
      height: 400,
      barmode: "relative",
      hovermode: "x unified",
      margin: { l: 60, r: 20, t: 48, b: 44 },
      paper_bgcolor: "#FFFFFF",
      plot_bgcolor: "#FFFFFF",
      showlegend: true,
      legend: { orientation: "h", x: 0, y: 1.14, font: { size: 11 } },
      xaxis: { type: "date", gridcolor: GRID, linecolor: AXIS, hoverformat: "%a %d %b %Y" },
      yaxis: {
        title: { text: "Daily Forecast Sales Attribution (€)", font: { size: 11, color: PALETTE.inkSecondary } },
        tickprefix: "€",
        tickformat: "~s",
        gridcolor: GRID,
        linecolor: AXIS,
        zeroline: true,
        zerolinecolor: AXIS,
      },
    },
  };
}
