import React from "react";
import { Box, Paper, Typography } from "@mui/material";
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
        emptyMessage="Upload the case-study workbook on the Runs page to start. The overview fills in as the pipeline runs."
      />

      <Box component="nav" aria-label="Explore further" sx={{ display: "grid", gap: 2, gridTemplateColumns: "repeat(auto-fit, minmax(min(100%, 240px), 1fr))", mt: 1 }}>
        {QUICK_LINKS.map((item) => (
          <Paper
            key={item.path}
            sx={{
              // The whole card is clickable through its title link (one link per card, so screen readers hear it once)
              position: "relative",
              p: 2.5,
              display: "flex",
              flexDirection: "column",
              gap: 1,
              borderTop: `3px solid ${PALETTE.brandGoldLight}`,
              transition: "border-color 0.15s, box-shadow 0.15s",
              "&:hover, &:focus-within": { borderColor: PALETTE.borderStrong, boxShadow: "0 6px 18px rgba(33, 41, 47, 0.08)" },
              "&:hover .quick-link-arrow, &:focus-within .quick-link-arrow": { transform: "translateX(3px)" },
              "@media (prefers-reduced-motion: reduce)": { transition: "none", "& .quick-link-arrow": { transition: "none" } },
            }}
          >
            <Typography variant="h3" component="h2">
              <Box
                component={RouterLink}
                to={item.path}
                sx={{
                  color: "inherit",
                  textDecoration: "none",
                  "&::after": { content: '""', position: "absolute", inset: 0, borderRadius: "inherit" },
                  "&:focus-visible": { outline: "none" },
                  "&:focus-visible::after": { outline: `3px solid ${PALETTE.brandPrimary}`, outlineOffset: 2 },
                }}
              >
                {item.title}
              </Box>
            </Typography>
            <Typography variant="body2" sx={{ flexGrow: 1 }}>
              {item.desc}
            </Typography>
            <Box aria-hidden="true" sx={{ display: "flex", alignItems: "center", gap: 0.5, color: PALETTE.brandPrimary, fontWeight: 650, fontSize: "0.86rem" }}>
              Open
              <ArrowForwardIcon className="quick-link-arrow" fontSize="small" sx={{ transition: "transform 0.15s" }} />
            </Box>
          </Paper>
        ))}
      </Box>
    </Box>
  );
};
