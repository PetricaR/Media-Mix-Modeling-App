import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: process.env.VITE_API_PROXY_TARGET || "http://localhost:8000",
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: "dist",
    sourcemap: false,
    chunkSizeWarningLimit: 1600,
    rollupOptions: {
      output: {
        // Plotly is large and only needed once a chart renders, so it gets its own lazily loaded chunk; the rest of
        // node_modules shares one vendor chunk (splitting MUI from React created a circular chunk dependency).
        manualChunks(id) {
          if (id.includes("plotly")) return "plotly";
          if (id.includes("node_modules")) return "vendor";
          return undefined;
        },
      },
    },
  },
});
