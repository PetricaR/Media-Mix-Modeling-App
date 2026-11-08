import React from "react";
import Plotly from "plotly.js-cartesian-dist-min";
import createPlotlyComponent from "react-plotly.js/factory";
import { Box, useMediaQuery } from "@mui/material";
import { FONT_SANS, PALETTE } from "../../theme";
import { rethemeFigure } from "./legacyColors";

const Plot = createPlotlyComponent(Plotly);

interface PlotlyChartProps {
  data: Record<string, unknown>[];
  layout: Record<string, unknown>;
  height?: number;
  ariaLabel?: string;
  /** Change it to drop the viewer's zoom, pan and legend toggles (e.g. another forecast or history window). */
  viewKey?: string;
}

/**
 * Renders a backend Plotly figure. The card already shows the chart's title, so the figure's own title is dropped
 * (it collided with the legend) and the top margin is sized for the legend instead.
 *
 * Zoom must survive re-renders: polling (run 2 s, sections 4 s, scenarios 1 s) and the planner hand the chart new
 * figure objects, and Plotly redraws any new layout from scratch. A fixed `uirevision` tells Plotly to keep what the
 * viewer did; the layout is also deep-copied, because Plotly writes axis ranges into it and would otherwise change
 * the query cache, so each refetch of an unchanged section looked like a new figure.
 *
 * Runs stored before the Formare re-theme carry the old colours; `rethemeFigure` swaps them on the way in (and its
 * copy doubles as the deep copy of the layout).
 */
export const PlotlyChart: React.FC<PlotlyChartProps> = ({ data, layout, height = 420, ariaLabel, viewKey = "view" }) => {
  const narrow = useMediaQuery("(max-width: 600px)", { noSsr: true });
  const mergedLayout = React.useMemo(() => {
    const { title: _title, ...rest } = rethemeFigure(layout || {}) as Record<string, unknown>;
    const margin = { l: 56, r: 24, t: 24, b: 48, ...((rest.margin as Record<string, number>) || {}) };
    const legendCount = Array.isArray(data) ? data.filter((t) => t && t.showlegend !== false).length : 0;
    if (rest.showlegend !== false) margin.t = Math.max(margin.t, legendCount >= 7 ? 58 : 44);
    else margin.t = Math.min(margin.t, 28);
    if (narrow) {
      margin.l = Math.min(margin.l, 44);
      margin.r = Math.min(margin.r, 12);
    }
    const font = { ...((rest.font as Record<string, unknown>) || {}), family: FONT_SANS, color: PALETTE.inkPrimary };
    // Long category labels (horizontal bars) widen the left margin instead of being cut off at it
    const yAxes = Object.fromEntries(
      Object.keys({ yaxis: null, ...rest })
        .filter((k) => /^yaxis\d*$/.test(k))
        .map((k) => {
          const axis = (rest[k] as Record<string, unknown>) || {};
          return [k, { ...axis, automargin: axis.automargin ?? true }];
        })
    );
    return {
      ...rest,
      ...yAxes,
      autosize: true,
      height: (rest.height as number) || height,
      margin,
      font,
      hoverlabel: { font: { family: FONT_SANS, color: PALETTE.inkPrimary }, bgcolor: "#FFFFFF", bordercolor: PALETTE.borderSubtle },
      uirevision: viewKey,
    };
  }, [layout, height, narrow, viewKey]);
  const themedData = React.useMemo(() => rethemeFigure(data || []), [data]);

  // A new config object on every render made react-plotly redraw the chart on every parent render.
  const config = React.useMemo(
    () => ({
      responsive: true,
      displaylogo: false,
      modeBarButtonsToRemove: ["lasso2d", "select2d"] as Plotly.ModeBarDefaultButtons[],
      toImageButtonOptions: { format: "png" as const, filename: (ariaLabel || "mmm-studio-chart").slice(0, 60), scale: 2 },
    }),
    [ariaLabel]
  );

  return (
    <Box sx={{ width: "100%", minWidth: 0 }}>
      <Plot
        data={themedData as Plotly.Data[]}
        layout={mergedLayout as Partial<Plotly.Layout>}
        config={config}
        style={{ width: "100%", minHeight: mergedLayout.height }}
        useResizeHandler
      />
    </Box>
  );
};

export default PlotlyChart;
