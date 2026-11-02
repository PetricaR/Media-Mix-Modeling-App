import React from "react";
import Plotly from "plotly.js-cartesian-dist-min";
import createPlotlyComponent from "react-plotly.js/factory";
import { Box } from "@mui/material";

const Plot = createPlotlyComponent(Plotly);

interface PlotlyChartProps {
  data: Record<string, unknown>[];
  layout: Record<string, unknown>;
  height?: number;
}

export const PlotlyChart: React.FC<PlotlyChartProps> = ({ data, layout, height = 420 }) => {
  const mergedLayout = React.useMemo(
    () => ({
      autosize: true,
      height: (layout?.height as number) || height,
      ...layout,
    }),
    [layout, height]
  );

  return (
    <Box sx={{ width: "100%", overflowX: "auto" }}>
      <Plot
        data={data as Plotly.Data[]}
        layout={mergedLayout as Partial<Plotly.Layout>}
        config={{
          responsive: true,
          displaylogo: false,
          modeBarButtonsToRemove: ["lasso2d", "select2d"],
        }}
        style={{ width: "100%", minHeight: mergedLayout.height }}
        useResizeHandler
      />
    </Box>
  );
};

export default PlotlyChart;
