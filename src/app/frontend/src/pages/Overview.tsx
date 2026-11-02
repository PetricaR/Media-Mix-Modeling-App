import React from "react";
import { Box, Button, Paper, Typography } from "@mui/material";
import ArrowForwardIcon from "@mui/icons-material/ArrowForward";
import { Link as RouterLink } from "react-router-dom";
import { useSection } from "../api/hooks";
import { SectionRenderer } from "../components/Sections/SectionRenderer";
import { PALETTE } from "../theme";

const QUICK_LINKS = [
  {
    title: "Exploratory analysis",
    desc: "The target, channel mix, spend, efficiency, correlations and the catalog's delayed effect and halo.",
    path: "/explore",
  },
  {
    title: "Meridian model",
    desc: "Specification, convergence (R-hat), the Q4 and Q3 hold-outs, residuals and model selection.",
    path: "/model",
  },
  {
    title: "Budget optimiser",
    desc: "Re-allocate the same budget along the response curves, with your own per-channel limits.",
    path: "/optimizer",
  },
  {
    title: "Forecast & scenarios",
    desc: "Daily forecast for a budget plan with 90 % prediction intervals, and what-if scenarios.",
    path: "/forecast",
  },
];

export const OverviewPage: React.FC<{ runId: string | null }> = ({ runId }) => {
  const { data: section, isLoading, error } = useSection(runId, "overview");

  return (
    <Box>
      <SectionRenderer
        section={section}
        isLoading={isLoading}
        error={error as Error | null}
        emptyMessage="Upload the case-study workbook on the Import & runs page to start. The overview fills in as the pipeline runs."
      />

      <Box component="nav" aria-label="Explore further" sx={{ display: "grid", gap: 2, gridTemplateColumns: "repeat(auto-fit, minmax(min(100%, 240px), 1fr))", mt: 1 }}>
        {QUICK_LINKS.map((item) => (
          <Paper key={item.path} sx={{ p: 2.5, display: "flex", flexDirection: "column", gap: 1, borderTop: `3px solid ${PALETTE.brandGoldLight}` }}>
            <Typography variant="h3" component="h2">
              {item.title}
            </Typography>
            <Typography variant="body2" sx={{ flexGrow: 1 }}>
              {item.desc}
            </Typography>
            <Box>
              <Button size="small" component={RouterLink} to={item.path} endIcon={<ArrowForwardIcon fontSize="small" />} aria-label={`Open ${item.title}`}>
                Open
              </Button>
            </Box>
          </Paper>
        ))}
      </Box>
    </Box>
  );
};
