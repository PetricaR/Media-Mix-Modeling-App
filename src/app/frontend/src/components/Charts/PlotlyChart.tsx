import React from "react";
import Plotly from "plotly.js-cartesian-dist-min";
import createPlotlyComponent from "react-plotly.js/factory";
import { Box, useMediaQuery } from "@mui/material";
import { FONT_SANS, PALETTE } from "../../theme";

const Plot = createPlotlyComponent(Plotly);

interface PlotlyChartProps {
  data: Record<string, unknown>[];
  layout: Record<string, unknown>;
  height?: number;
  ariaLabel?: string;
}

/**
 * Renders a backend Plotly figure. The card already shows the chart's title, so the figure's own title is dropped
 * (it collided with the legend) and the top margin is sized for the legend instead.
 */
export const PlotlyChart: React.FC<PlotlyChartProps> = ({ data, layout, height = 420, ariaLabel }) => {
  const narrow = useMediaQuery("(max-width: 600px)", { noSsr: true });
  const mergedLayout = React.useMemo(() => {
    const { title: _title, ...rest } = (layout || {}) as Record<string, unknown>;
    const margin = { l: 56, r: 24, t: 24, b: 48, ...((rest.margin as Record<string, number>) || {}) };
    if (rest.showlegend !== false) margin.t = Math.max(margin.t, 44);
    else margin.t = Math.min(margin.t, 28);
    if (narrow) {
      margin.l = Math.min(margin.l, 44);
      margin.r = Math.min(margin.r, 12);
    }
    const font = { ...((rest.font as Record<string, unknown>) || {}), family: FONT_SANS, color: PALETTE.inkPrimary };
    return {
      ...rest,
      autosize: true,
      height: (rest.height as number) || height,
      margin,
      font,
      hoverlabel: { font: { family: FONT_SANS }, bgcolor: "#FFFFFF", bordercolor: PALETTE.borderStrong },
    };
  }, [layout, height, narrow]);

  return (
    <Box sx={{ width: "100%", minWidth: 0 }}>
      <Plot
        data={data as Plotly.Data[]}
        layout={mergedLayout as Partial<Plotly.Layout>}
        config={{
          responsive: true,
          displaylogo: false,
          modeBarButtonsToRemove: ["lasso2d", "select2d"],
          toImageButtonOptions: { format: "png", filename: (ariaLabel || "mmm-studio-chart").slice(0, 60), scale: 2 },
        }}
        style={{ width: "100%", minHeight: mergedLayout.height }}
        useResizeHandler
      />
    </Box>
  );
};

export default PlotlyChart;
