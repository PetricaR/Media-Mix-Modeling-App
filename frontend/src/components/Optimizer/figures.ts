import type {
  OptimisationChannelRow,
  OptimisationResult,
  OptimiserChannelMeta,
  PlotlySpec,
  ResponseCurvePoint,
} from "../../api/types";
import { CHART, FONT_SANS, PALETTE, channelColor } from "../../theme";

// One colour per channel on every page: the optimiser uses the app-wide map (it had its own before)
export { channelColor };

function hexToRgba(hex: string, alpha: number): string {
  const clean = hex.replace("#", "");
  const r = parseInt(clean.slice(0, 2), 16) || 10;
  const g = parseInt(clean.slice(2, 4), 16) || 99;
  const b = parseInt(clean.slice(4, 6), 16) || 189;
  return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}

function baseAxis(title?: string): Record<string, unknown> {
  return {
    title: title ? { text: title, font: { size: 12, color: PALETTE.inkSecondary } } : undefined,
    gridcolor: CHART.grid,
    linecolor: CHART.axis,
    tickfont: { size: 11, color: PALETTE.inkSecondary },
    zerolinecolor: PALETTE.borderStrong,
  };
}

function getRowSpendCurrent(r: OptimisationChannelRow): number {
  return r.current_spend ?? r.historical_spend ?? 0;
}

function getRowIncCurrent(r: OptimisationChannelRow): number {
  return r.current_inc_sales ?? r.historical_incremental ?? r.current_incremental ?? 0;
}

function getRowIncOpt(r: OptimisationChannelRow): number {
  return r.optimised_inc_sales ?? r.optimised_incremental ?? r.expected_incremental_sales ?? 0;
}

function getRowDeltaSpend(r: OptimisationChannelRow): number {
  return r.spend_delta ?? r.delta_spend ?? r.optimised_spend - getRowSpendCurrent(r);
}

function getRowDeltaInc(r: OptimisationChannelRow): number {
  return r.inc_sales_delta ?? r.delta_incremental ?? getRowIncOpt(r) - getRowIncCurrent(r);
}

/**
 * Figure 18 · Panel 1: Change in optimized spend for each channel (Positive vs Negative bars).
 */
export function buildSpendDeltaFigure(rows: OptimisationChannelRow[]): PlotlySpec {
  const ordered = [...rows].sort((a, b) => getRowSpendCurrent(b) - getRowSpendCurrent(a));
  const labels = ordered.map((r) => r.label || r.channel);
  const deltasM = ordered.map((r) => Number((getRowDeltaSpend(r) / 1e6).toFixed(2)));
  const posValues = deltasM.map((v) => (v > 0 ? v : null));
  const negValues = deltasM.map((v) => (v < 0 ? v : null));
  const zeroValues = deltasM.map((v) => (v === 0 ? 0 : null));
  const posText = deltasM.map((v) => (v > 0 ? `+€${v.toFixed(1)}M` : ""));
  const negText = deltasM.map((v) => (v < 0 ? `−€${Math.abs(v).toFixed(1)}M` : ""));
  const zeroText = deltasM.map((v) => (v === 0 ? "€0.0M (fixed)" : ""));

  const minVal = Math.min(0, ...deltasM);
  const maxVal = Math.max(0, ...deltasM);
  const span = Math.max(maxVal - minVal, 4);

  const data: Record<string, unknown>[] = [
    {
      type: "bar",
      name: "Positive change (+)",
      x: labels,
      y: posValues,
      marker: { color: PALETTE.semanticGood },
      text: posText,
      textposition: "outside",
      cliponaxis: false,
      hovertemplate: "<b>%{x}</b><br>Spend change: +€%{y:.2f}M<extra></extra>",
    },
    {
      type: "bar",
      name: "Negative change (−)",
      x: labels,
      y: negValues,
      marker: { color: PALETTE.semanticBad },
      text: negText,
      textposition: "outside",
      cliponaxis: false,
      hovertemplate: "<b>%{x}</b><br>Spend change: €%{y:.2f}M<extra></extra>",
    },
  ];

  if (zeroValues.some((v) => v !== null)) {
    data.push({
      type: "bar",
      name: "Held fixed (0)",
      x: labels,
      y: zeroValues,
      marker: { color: PALETTE.inkMuted },
      text: zeroText,
      textposition: "outside",
      cliponaxis: false,
      hovertemplate: "<b>%{x}</b><br>Held at current spend (€0.00M)<extra></extra>",
    });
  }

  return {
    data,
    layout: {
      barmode: "overlay",
      height: 340,
      paper_bgcolor: PALETTE.bgCard,
      plot_bgcolor: PALETTE.bgCard,
      font: { family: FONT_SANS, color: PALETTE.inkPrimary, size: 12 },
      margin: { l: 56, r: 20, t: 44, b: 56 },
      xaxis: { ...baseAxis(), showgrid: false },
      yaxis: {
        ...baseAxis("Spend change (€M)"),
        range: [minVal - span * 0.24, maxVal + span * 0.24],
        zeroline: true,
        zerolinewidth: 1.5,
        zerolinecolor: PALETTE.inkSecondary,
      },
      legend: {
        orientation: "h",
        yanchor: "bottom",
        y: 1.02,
        xanchor: "left",
        x: 0,
        bgcolor: "rgba(255,255,255,0.88)",
      },
    },
  };
}

/**
 * Figure 18 · Panel 2: Optimized spend allocation (% of spend allocated to each channel).
 */
export function buildSpendDonutFigure(
  rows: OptimisationChannelRow[],
  totalBudget: number
): PlotlySpec {
  const ordered = [...rows].sort((a, b) => b.optimised_spend - a.optimised_spend);
  const labels = ordered.map((r) => r.label || r.channel);
  const values = ordered.map((r) => Math.round(r.optimised_spend));
  const colors = ordered.map((r, i) => channelColor(r.channel, i));
  const curShares = ordered.map((r) => {
    const curTotal = rows.reduce((s, x) => s + getRowSpendCurrent(x), 0);
    return curTotal > 0 ? (100 * getRowSpendCurrent(r)) / curTotal : 0;
  });

  return {
    data: [
      {
        type: "pie",
        labels,
        values,
        hole: 0.58,
        sort: false,
        direction: "clockwise",
        marker: {
          colors,
          line: { color: PALETTE.bgCard, width: 2 },
        },
        textinfo: "label+percent",
        textposition: "outside",
        automargin: true,
        customdata: curShares.map((s, i) => [
          (values[i] / 1e6).toFixed(2),
          s.toFixed(1),
        ]),
        hovertemplate:
          "<b>%{label}</b><br>Optimised spend: €%{customdata[0]}M (%{percent})<br>Non-optimised share: %{customdata[1]}%<extra></extra>",
      },
    ],
    layout: {
      height: 340,
      showlegend: true,
      paper_bgcolor: PALETTE.bgCard,
      plot_bgcolor: PALETTE.bgCard,
      font: { family: FONT_SANS, color: PALETTE.inkPrimary, size: 12 },
      margin: { l: 24, r: 24, t: 44, b: 28 },
      legend: {
        orientation: "h",
        yanchor: "bottom",
        y: 1.02,
        xanchor: "left",
        x: 0,
      },
      annotations: [
        {
          text: `<b>€${(totalBudget / 1e6).toFixed(1)}M</b><br><span style="font-size:11px;color:${PALETTE.inkSecondary}">Total budget</span>`,
          x: 0.5,
          y: 0.5,
          showarrow: false,
          font: { family: FONT_SANS, size: 14, color: PALETTE.inkPrimary },
        },
      ],
    },
  };
}

/**
 * Figure 18 · Panel 3: Budget spend per channel (Non-optimized vs Optimized spend).
 */
export function buildBudgetComparisonFigure(rows: OptimisationChannelRow[]): PlotlySpec {
  const ordered = [...rows].sort((a, b) => getRowSpendCurrent(b) - getRowSpendCurrent(a));
  const labels = ordered.map((r) => r.label || r.channel);
  const curM = ordered.map((r) => Number((getRowSpendCurrent(r) / 1e6).toFixed(2)));
  const optM = ordered.map((r) => Number((r.optimised_spend / 1e6).toFixed(2)));

  return {
    data: [
      {
        type: "bar",
        name: "Non-optimised spend",
        x: labels,
        y: curM,
        marker: { color: PALETTE.inkMuted },
        hovertemplate: "<b>%{x}</b><br>Non-optimised spend: €%{y:.2f}M<extra></extra>",
      },
      {
        type: "bar",
        name: "Optimised spend",
        x: labels,
        y: optM,
        marker: { color: PALETTE.brandPrimary },
        hovertemplate: "<b>%{x}</b><br>Optimised spend: €%{y:.2f}M<extra></extra>",
      },
    ],
    layout: {
      barmode: "group",
      height: 340,
      paper_bgcolor: PALETTE.bgCard,
      plot_bgcolor: PALETTE.bgCard,
      font: { family: FONT_SANS, color: PALETTE.inkPrimary, size: 12 },
      margin: { l: 56, r: 20, t: 44, b: 56 },
      xaxis: { ...baseAxis(), showgrid: false },
      yaxis: baseAxis("Spend (€M)"),
      legend: {
        orientation: "h",
        yanchor: "bottom",
        y: 1.02,
        xanchor: "left",
        x: 0,
      },
    },
  };
}

/**
 * Figure 18 · Panel 4: Incremental outcome per channel (Grouped bars or Waterfall decomposition).
 */
export function buildOutcomeComparisonFigure(
  rows: OptimisationChannelRow[],
  mode: "grouped" | "waterfall",
  result: OptimisationResult
): PlotlySpec {
  const ordered = [...rows].sort((a, b) => getRowIncOpt(b) - getRowIncOpt(a));
  const labels = ordered.map((r) => r.label || r.channel);

  if (mode === "waterfall") {
    const curTotalM = Number(
      ((result.current_inc_sales ?? result.historical_incremental_revenue) / 1e6).toFixed(2)
    );
    const optTotalM = Number(
      ((result.optimised_inc_sales ?? result.optimised_incremental_revenue) / 1e6).toFixed(2)
    );
    const byDelta = [...rows].sort((a, b) => getRowDeltaInc(b) - getRowDeltaInc(a));
    const wfX = ["Non-optimised", ...byDelta.map((r) => r.label || r.channel), "Optimised"];
    const wfY = [
      curTotalM,
      ...byDelta.map((r) => Number((getRowDeltaInc(r) / 1e6).toFixed(2))),
      optTotalM,
    ];
    const wfMeasure = ["absolute", ...byDelta.map(() => "relative"), "total"];
    const wfText = [
      `€${curTotalM.toFixed(1)}M`,
      ...byDelta.map((r) => {
        const d = getRowDeltaInc(r) / 1e6;
        return `${d >= 0 ? "+" : "−"}€${Math.abs(d).toFixed(1)}M`;
      }),
      `€${optTotalM.toFixed(1)}M`,
    ];

    return {
      data: [
        {
          type: "waterfall",
          name: "Incremental outcome",
          x: wfX,
          y: wfY,
          measure: wfMeasure,
          text: wfText,
          textposition: "outside",
          cliponaxis: false,
          connector: { line: { color: PALETTE.borderStrong, width: 1, dash: "dot" } },
          increasing: { marker: { color: PALETTE.semanticGood } },
          decreasing: { marker: { color: PALETTE.semanticBad } },
          totals: { marker: { color: PALETTE.brandPrimary } },
          hovertemplate: "<b>%{x}</b><br>%{text}<extra></extra>",
        },
      ],
      layout: {
        showlegend: false,
        height: 340,
        paper_bgcolor: PALETTE.bgCard,
        plot_bgcolor: PALETTE.bgCard,
        font: { family: FONT_SANS, color: PALETTE.inkPrimary, size: 12 },
        margin: { l: 56, r: 20, t: 32, b: 64 },
        xaxis: { ...baseAxis(), showgrid: false },
        yaxis: baseAxis("Incremental outcome (€M)"),
      },
    };
  }

  const curM = ordered.map((r) => Number((getRowIncCurrent(r) / 1e6).toFixed(2)));
  const optM = ordered.map((r) => Number((getRowIncOpt(r) / 1e6).toFixed(2)));

  return {
    data: [
      {
        type: "bar",
        name: "Non-optimised outcome",
        x: labels,
        y: curM,
        marker: { color: PALETTE.inkMuted },
        hovertemplate: "<b>%{x}</b><br>Non-optimised outcome: €%{y:.2f}M<extra></extra>",
      },
      {
        type: "bar",
        name: "Optimised outcome",
        x: labels,
        y: optM,
        marker: { color: PALETTE.brandPrimary },
        hovertemplate: "<b>%{x}</b><br>Optimised outcome: €%{y:.2f}M<extra></extra>",
      },
    ],
    layout: {
      barmode: "group",
      height: 340,
      paper_bgcolor: PALETTE.bgCard,
      plot_bgcolor: PALETTE.bgCard,
      font: { family: FONT_SANS, color: PALETTE.inkPrimary, size: 12 },
      margin: { l: 56, r: 20, t: 44, b: 56 },
      xaxis: { ...baseAxis(), showgrid: false },
      yaxis: baseAxis("Incremental outcome (€M)"),
      legend: {
        orientation: "h",
        yanchor: "bottom",
        y: 1.02,
        xanchor: "left",
        x: 0,
      },
    },
  };
}

/**
 * Linear interpolation along a channel's response curve points.
 */
function interpolateCurve(points: ResponseCurvePoint[], spendEur: number): {
  mean: number;
  ci_lo: number;
  ci_hi: number;
  mroi: number;
} {
  if (!points.length) return { mean: 0, ci_lo: 0, ci_hi: 0, mroi: 0 };
  const sorted = [...points].sort((a, b) => a.spend - b.spend);
  if (spendEur <= sorted[0].spend) {
    const p0 = sorted[0];
    const p1 = sorted[1] || p0;
    const mroi = p1.spend > p0.spend ? (p1.mean - p0.mean) / (p1.spend - p0.spend) : 0;
    return { mean: p0.mean, ci_lo: p0.ci_lo, ci_hi: p0.ci_hi, mroi };
  }
  const last = sorted[sorted.length - 1];
  if (spendEur >= last.spend) {
    const prev = sorted[sorted.length - 2] || last;
    const mroi = last.spend > prev.spend ? (last.mean - prev.mean) / (last.spend - prev.spend) : 0;
    return { mean: last.mean, ci_lo: last.ci_lo, ci_hi: last.ci_hi, mroi };
  }
  for (let i = 0; i < sorted.length - 1; i += 1) {
    const a = sorted[i];
    const b = sorted[i + 1];
    if (spendEur >= a.spend && spendEur <= b.spend) {
      const span = Math.max(b.spend - a.spend, 1);
      const t = (spendEur - a.spend) / span;
      return {
        mean: a.mean + t * (b.mean - a.mean),
        ci_lo: a.ci_lo + t * (b.ci_lo - a.ci_lo),
        ci_hi: a.ci_hi + t * (b.ci_hi - a.ci_hi),
        mroi: (b.mean - a.mean) / span,
      };
    }
  }
  return { mean: last.mean, ci_lo: last.ci_lo, ci_hi: last.ci_hi, mroi: 0 };
}

/**
 * Split a response curve into solid (inside [loEur, hiEur]) and dotted (outside [loEur, hiEur]) segments,
 * inserting exact boundary points at loEur and hiEur so the solid and dotted segments meet seamlessly.
 */
function splitCurveByBounds(
  points: ResponseCurvePoint[],
  loEur: number,
  hiEur: number
): {
  allSorted: ResponseCurvePoint[];
  outsideLow: { x: number[]; y: number[] };
  inside: { x: number[]; y: number[] };
  outsideHigh: { x: number[]; y: number[] };
} {
  const sorted = [...points].sort((a, b) => a.spend - b.spend);
  const loClamped = Math.max(0, loEur);
  const hiClamped = Math.max(loClamped, hiEur);
  const loPt = interpolateCurve(sorted, loClamped);
  const hiPt = interpolateCurve(sorted, hiClamped);

  const lowPts = sorted.filter((p) => p.spend < loClamped);
  const midPts = sorted.filter((p) => p.spend >= loClamped && p.spend <= hiClamped);
  const highPts = sorted.filter((p) => p.spend > hiClamped);

  const outsideLow = {
    x: [...lowPts.map((p) => p.spend / 1e6), loClamped / 1e6],
    y: [...lowPts.map((p) => p.mean / 1e6), loPt.mean / 1e6],
  };
  const inside = {
    x: [loClamped / 1e6, ...midPts.map((p) => p.spend / 1e6), hiClamped / 1e6],
    y: [loPt.mean / 1e6, ...midPts.map((p) => p.mean / 1e6), hiPt.mean / 1e6],
  };
  const outsideHigh = {
    x: [hiClamped / 1e6, ...highPts.map((p) => p.spend / 1e6)],
    y: [hiPt.mean / 1e6, ...highPts.map((p) => p.mean / 1e6)],
  };

  return { allSorted: sorted, outsideLow, inside, outsideHigh };
}

/**
 * Figure 19 · Single-channel response curve:
 * - 90% credible interval band
 * - Dotted curve outside constraint bounds, solid curve inside constraint bounds
 * - Shaded feasible spend constraint window
 * - Circle marker (●) at Current (Non-optimized) spend
 * - Square marker (■) at Optimized spend
 */
export function buildSingleResponseCurveFigure(
  chMeta: OptimiserChannelMeta,
  row: OptimisationChannelRow,
  points: ResponseCurvePoint[]
): PlotlySpec {
  const color = channelColor(chMeta.channel);
  const curSpend = getRowSpendCurrent(row);
  const optSpend = row.optimised_spend;
  const loEur = row.min_bound_eur ?? curSpend * row.min_bound;
  const hiEur = row.max_bound_eur ?? curSpend * row.max_bound;

  const { allSorted, outsideLow, inside, outsideHigh } = splitCurveByBounds(points, loEur, hiEur);
  const xAllM = allSorted.map((p) => p.spend / 1e6);
  const yLoM = allSorted.map((p) => p.ci_lo / 1e6);
  const yHiM = allSorted.map((p) => p.ci_hi / 1e6);

  const curPt = interpolateCurve(allSorted, curSpend);
  const optPt = interpolateCurve(allSorted, optSpend);
  const curIncM = (getRowIncCurrent(row) || curPt.mean) / 1e6;
  const optIncM = (getRowIncOpt(row) || optPt.mean) / 1e6;
  const maxSpendM = Math.max(...xAllM, optSpend / 1e6, 1);
  const maxOutcomeM = Math.max(...yHiM, optIncM, 1);

  return {
    data: [
      {
        type: "scatter",
        mode: "lines",
        x: [...xAllM, ...[...xAllM].reverse()],
        y: [...yHiM, ...[...yLoM].reverse()],
        fill: "toself",
        fillcolor: hexToRgba(color, 0.13),
        line: { color: "rgba(0,0,0,0)", width: 0 },
        name: "90% credible interval",
        hoverinfo: "skip",
      },
      {
        type: "scatter",
        mode: "lines",
        x: [0, Math.min(maxSpendM, maxOutcomeM)],
        y: [0, Math.min(maxSpendM, maxOutcomeM)],
        line: { color: PALETTE.borderStrong, width: 1.2, dash: "dash" },
        name: "Break-even (ROI = 1.0×)",
        hoverinfo: "skip",
      },
      {
        type: "scatter",
        mode: "lines",
        x: outsideLow.x,
        y: outsideLow.y,
        line: { color, width: 2.2, dash: "dot" },
        name: "Outside constraint bounds",
        hovertemplate: `<b>${chMeta.label}</b> (outside bounds)<br>Spend: €%{x:.2f}M<br>Outcome: €%{y:.2f}M<extra></extra>`,
      },
      {
        type: "scatter",
        mode: "lines",
        x: outsideHigh.x,
        y: outsideHigh.y,
        line: { color, width: 2.2, dash: "dot" },
        name: "Outside constraint bounds (upper)",
        showlegend: false,
        hovertemplate: `<b>${chMeta.label}</b> (outside bounds)<br>Spend: €%{x:.2f}M<br>Outcome: €%{y:.2f}M<extra></extra>`,
      },
      {
        type: "scatter",
        mode: "lines",
        x: inside.x,
        y: inside.y,
        line: { color, width: 3.4 },
        name: "Inside constraint bounds",
        hovertemplate: `<b>${chMeta.label}</b> (feasible range)<br>Spend: €%{x:.2f}M<br>Outcome: €%{y:.2f}M<extra></extra>`,
      },
      {
        type: "scatter",
        mode: "markers",
        x: [curSpend / 1e6],
        y: [curIncM],
        marker: {
          symbol: "circle",
          size: 13,
          color: PALETTE.bgCard,
          line: { color: PALETTE.inkPrimary, width: 3 },
        },
        name: "Current spend level (●)",
        hovertemplate: `<b>${chMeta.label} · Current spend (●)</b><br>Spend: €%{x:.2f}M<br>Incremental outcome: €%{y:.2f}M<br>ROI: ${(row.historical_roi ?? chMeta.historical_roi).toFixed(2)}×<extra></extra>`,
      },
      {
        type: "scatter",
        mode: "markers",
        x: [optSpend / 1e6],
        y: [optIncM],
        marker: {
          symbol: "square",
          size: 14,
          color,
          line: { color: PALETTE.inkPrimary, width: 2 },
        },
        name: "Optimised spend level (■)",
        hovertemplate: `<b>${chMeta.label} · Optimised spend (■)</b><br>Spend: €%{x:.2f}M (${row.multiplier.toFixed(2)}×)<br>Incremental outcome: €%{y:.2f}M<br>Optimised ROI: ${row.optimised_roi.toFixed(2)}×<extra></extra>`,
      },
    ],
    layout: {
      height: 380,
      paper_bgcolor: PALETTE.bgCard,
      plot_bgcolor: PALETTE.bgCard,
      font: { family: FONT_SANS, color: PALETTE.inkPrimary, size: 12 },
      margin: { l: 56, r: 24, t: 48, b: 56 },
      xaxis: baseAxis("Channel spend (€M)"),
      yaxis: baseAxis("Incremental outcome (€M)"),
      shapes:
        Math.abs(hiEur - loEur) > 100
          ? [
              {
                type: "rect",
                xref: "x",
                yref: "paper",
                x0: loEur / 1e6,
                x1: hiEur / 1e6,
                y0: 0,
                y1: 1,
                fillcolor: hexToRgba(PALETTE.inkPrimary, 0.05),
                line: { color: hexToRgba(PALETTE.inkPrimary, 0.3), width: 1, dash: "dot" },
                layer: "below",
              },
            ]
          : [],
      legend: {
        orientation: "h",
        yanchor: "bottom",
        y: 1.02,
        xanchor: "left",
        x: 0,
      },
    },
  };
}

/**
 * Companion chart to Figure 19: Marginal ROI (dOutcome / dSpend) curve along the channel's spend range.
 */
export function buildMarginalRoiCurveFigure(
  chMeta: OptimiserChannelMeta,
  row: OptimisationChannelRow,
  points: ResponseCurvePoint[],
  targetMroi?: number | null
): PlotlySpec {
  const color = channelColor(chMeta.channel);
  const sorted = [...points].sort((a, b) => a.spend - b.spend);
  const curSpend = getRowSpendCurrent(row);
  const optSpend = row.optimised_spend;
  const loEur = row.min_bound_eur ?? curSpend * row.min_bound;
  const hiEur = row.max_bound_eur ?? curSpend * row.max_bound;

  const mroiPts = sorted.map((p, i) => {
    const prev = sorted[Math.max(0, i - 1)];
    const next = sorted[Math.min(sorted.length - 1, i + 1)];
    const span = Math.max(next.spend - prev.spend, 1);
    return {
      spendM: p.spend / 1e6,
      spend: p.spend,
      mroi: (next.mean - prev.mean) / span,
    };
  });

  const insidePts = mroiPts.filter((p) => p.spend >= loEur - 1 && p.spend <= hiEur + 1);
  // Meridian's own marginal ROI at the current spend (the Drivers table's), not a 5 % secant of the curve
  const curMroi = chMeta.historical_mroi ?? interpolateCurve(sorted, curSpend).mroi;
  const optMroi = row.mroi ?? interpolateCurve(sorted, optSpend).mroi;
  const maxSpendM = Math.max(...mroiPts.map((p) => p.spendM), 1);

  const data: Record<string, unknown>[] = [
    {
      type: "scatter",
      mode: "lines",
      x: mroiPts.map((p) => p.spendM),
      y: mroiPts.map((p) => Number(p.mroi.toFixed(3))),
      line: { color, width: 2.0, dash: "dot" },
      name: "Marginal ROI (full curve)",
      hovertemplate: `<b>${chMeta.label}</b><br>Spend: €%{x:.2f}M<br>Marginal ROI: %{y:.2f}×<extra></extra>`,
    },
    {
      type: "scatter",
      mode: "lines",
      x: insidePts.map((p) => p.spendM),
      y: insidePts.map((p) => Number(p.mroi.toFixed(3))),
      line: { color, width: 3.4 },
      name: "Inside constraint bounds",
      hovertemplate: `<b>${chMeta.label}</b> (inside bounds)<br>Spend: €%{x:.2f}M<br>Marginal ROI: %{y:.2f}×<extra></extra>`,
    },
    {
      type: "scatter",
      mode: "lines",
      x: [0, maxSpendM],
      y: [1, 1],
      line: { color: PALETTE.semanticBad, width: 1.3, dash: "dash" },
      name: "Break-even mROI (1.0×)",
      hoverinfo: "skip",
    },
    {
      type: "scatter",
      mode: "markers",
      x: [curSpend / 1e6],
      y: [Number(curMroi.toFixed(2))],
      marker: {
        symbol: "circle",
        size: 12,
        color: PALETTE.bgCard,
        line: { color: PALETTE.inkPrimary, width: 3 },
      },
      name: "Current mROI (●)",
      hovertemplate: `<b>${chMeta.label} · Current (●)</b><br>Spend: €%{x:.2f}M<br>mROI: %{y:.2f}×<extra></extra>`,
    },
    {
      type: "scatter",
      mode: "markers",
      x: [optSpend / 1e6],
      y: [Number(optMroi.toFixed(2))],
      marker: {
        symbol: "square",
        size: 13,
        color,
        line: { color: PALETTE.inkPrimary, width: 2 },
      },
      name: "Optimised mROI (■)",
      hovertemplate: `<b>${chMeta.label} · Optimised (■)</b><br>Spend: €%{x:.2f}M<br>mROI: %{y:.2f}×<extra></extra>`,
    },
  ];

  if (targetMroi && targetMroi > 0) {
    data.push({
      type: "scatter",
      mode: "lines",
      x: [0, maxSpendM],
      y: [targetMroi, targetMroi],
      line: { color: CHART.navy, width: 1.8, dash: "dashdot" },
      name: `Target mROI (${targetMroi.toFixed(2)}×)`,
    });
  }

  return {
    data,
    layout: {
      height: 380,
      paper_bgcolor: PALETTE.bgCard,
      plot_bgcolor: PALETTE.bgCard,
      font: { family: FONT_SANS, color: PALETTE.inkPrimary, size: 12 },
      margin: { l: 56, r: 24, t: 48, b: 56 },
      xaxis: baseAxis("Channel spend (€M)"),
      yaxis: baseAxis("Marginal ROI (€ outcome per additional €1)"),
      legend: {
        orientation: "h",
        yanchor: "bottom",
        y: 1.02,
        xanchor: "left",
        x: 0,
      },
    },
  };
}

/**
 * Figure 19 · All-channels 6-panel small-multiples response curve grid.
 */
export function buildAllResponseCurvesGridFigure(
  channels: OptimiserChannelMeta[],
  rowsBySlug: Record<string, OptimisationChannelRow>,
  curvesBySlug: Record<string, ResponseCurvePoint[]>
): PlotlySpec {
  const n = channels.length;
  const cols = Math.min(3, Math.max(1, n));
  const gridRows = Math.ceil(n / cols);
  const hGap = 0.07;
  const vGap = 0.14;
  const cellW = (1 - hGap * (cols - 1)) / cols;
  const cellH = (1 - vGap * (gridRows - 1)) / gridRows;

  const data: Record<string, unknown>[] = [];
  const layout: Record<string, unknown> = {
    height: gridRows * 270,
    paper_bgcolor: PALETTE.bgCard,
    plot_bgcolor: PALETTE.bgCard,
    font: { family: FONT_SANS, color: PALETTE.inkPrimary, size: 11 },
    margin: { l: 52, r: 20, t: 54, b: 48 },
    legend: {
      orientation: "h",
      yanchor: "bottom",
      y: 1.04,
      xanchor: "left",
      x: 0,
    },
    annotations: [] as Record<string, unknown>[],
  };

  channels.forEach((ch, idx) => {
    const r = Math.floor(idx / cols);
    const c = idx % cols;
    const axSuffix = idx === 0 ? "" : String(idx + 1);
    const xref = `x${axSuffix}`;
    const yref = `y${axSuffix}`;
    const x0 = c * (cellW + hGap);
    const x1 = x0 + cellW;
    const y1 = 1 - r * (cellH + vGap);
    const y0 = y1 - cellH;

    const pts = curvesBySlug[ch.channel] || [];
    const row = rowsBySlug[ch.channel];
    if (!row || !pts.length) return;

    const color = channelColor(ch.channel, idx);
    const curSpend = getRowSpendCurrent(row);
    const optSpend = row.optimised_spend;
    const loEur = row.min_bound_eur ?? curSpend * row.min_bound;
    const hiEur = row.max_bound_eur ?? curSpend * row.max_bound;
    const { allSorted, outsideLow, inside, outsideHigh } = splitCurveByBounds(pts, loEur, hiEur);

    const xAllM = allSorted.map((p) => p.spend / 1e6);
    const yLoM = allSorted.map((p) => p.ci_lo / 1e6);
    const yHiM = allSorted.map((p) => p.ci_hi / 1e6);
    const curIncM = getRowIncCurrent(row) / 1e6;
    const optIncM = getRowIncOpt(row) / 1e6;

    data.push(
      {
        type: "scatter",
        mode: "lines",
        xaxis: xref,
        yaxis: yref,
        x: [...xAllM, ...[...xAllM].reverse()],
        y: [...yHiM, ...[...yLoM].reverse()],
        fill: "toself",
        fillcolor: hexToRgba(color, 0.12),
        line: { color: "rgba(0,0,0,0)", width: 0 },
        name: "90% CI",
        showlegend: idx === 0,
        hoverinfo: "skip",
      },
      {
        type: "scatter",
        mode: "lines",
        xaxis: xref,
        yaxis: yref,
        x: outsideLow.x,
        y: outsideLow.y,
        line: { color, width: 1.8, dash: "dot" },
        name: "Outside bounds",
        showlegend: idx === 0,
        hovertemplate: `<b>${ch.label}</b><br>Spend: €%{x:.2f}M<br>Outcome: €%{y:.2f}M<extra></extra>`,
      },
      {
        type: "scatter",
        mode: "lines",
        xaxis: xref,
        yaxis: yref,
        x: outsideHigh.x,
        y: outsideHigh.y,
        line: { color, width: 1.8, dash: "dot" },
        showlegend: false,
        hovertemplate: `<b>${ch.label}</b><br>Spend: €%{x:.2f}M<br>Outcome: €%{y:.2f}M<extra></extra>`,
      },
      {
        type: "scatter",
        mode: "lines",
        xaxis: xref,
        yaxis: yref,
        x: inside.x,
        y: inside.y,
        line: { color, width: 3.0 },
        name: "Inside bounds",
        showlegend: idx === 0,
        hovertemplate: `<b>${ch.label}</b> (feasible)<br>Spend: €%{x:.2f}M<br>Outcome: €%{y:.2f}M<extra></extra>`,
      },
      {
        type: "scatter",
        mode: "markers",
        xaxis: xref,
        yaxis: yref,
        x: [curSpend / 1e6],
        y: [curIncM],
        marker: {
          symbol: "circle",
          size: 10,
          color: PALETTE.bgCard,
          line: { color: PALETTE.inkPrimary, width: 2.5 },
        },
        name: "Current spend (●)",
        showlegend: idx === 0,
        hovertemplate: `<b>${ch.label} · Current (●)</b><br>Spend: €%{x:.2f}M<br>Outcome: €%{y:.2f}M<extra></extra>`,
      },
      {
        type: "scatter",
        mode: "markers",
        xaxis: xref,
        yaxis: yref,
        x: [optSpend / 1e6],
        y: [optIncM],
        marker: {
          symbol: "square",
          size: 11,
          color,
          line: { color: PALETTE.inkPrimary, width: 1.8 },
        },
        name: "Optimised spend (■)",
        showlegend: idx === 0,
        hovertemplate: `<b>${ch.label} · Optimised (■)</b><br>Spend: €%{x:.2f}M<br>Outcome: €%{y:.2f}M<extra></extra>`,
      }
    );

    layout[`xaxis${axSuffix}`] = {
      ...baseAxis(r === gridRows - 1 ? "Spend (€M)" : undefined),
      domain: [Number(x0.toFixed(4)), Number(x1.toFixed(4))],
      anchor: yref,
    };
    layout[`yaxis${axSuffix}`] = {
      ...baseAxis(c === 0 ? "Outcome (€M)" : undefined),
      domain: [Number(y0.toFixed(4)), Number(y1.toFixed(4))],
      anchor: xref,
    };

    const deltaPct = row.spend_delta_pct ?? row.delta_pct ?? 0;
    const badge =
      Math.abs(deltaPct) < 0.05
        ? "held fixed"
        : `${deltaPct >= 0 ? "+" : ""}${deltaPct.toFixed(0)}% spend`;
    (layout.annotations as Record<string, unknown>[]).push({
      text: `<b>${ch.label}</b> · <span style="color:${PALETTE.inkSecondary}">${badge} (mROI ${(row.mroi ?? row.optimised_roi).toFixed(2)}×)</span>`,
      xref: "paper",
      yref: "paper",
      x: Number(((x0 + x1) / 2).toFixed(4)),
      y: Number((y1 + 0.015).toFixed(4)),
      xanchor: "center",
      yanchor: "bottom",
      showarrow: false,
      font: { family: FONT_SANS, size: 12, color: PALETTE.inkPrimary },
    });
  });

  return { data, layout };
}
