/// <reference types="vite/client" />

declare module "plotly.js-cartesian-dist-min" {
  const Plotly: any;
  export default Plotly;
}

declare module "react-plotly.js/factory" {
  import React from "react";
  export default function createPlotlyComponent(plotly: any): React.ComponentType<any>;
}
