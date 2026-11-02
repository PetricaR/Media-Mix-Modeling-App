import React from "react";
import { Box, Button, Grid, Paper, Stack, Typography } from "@mui/material";
import ArrowForwardIcon from "@mui/icons-material/ArrowForward";
import { useNavigate } from "react-router-dom";
import { useSection } from "../api/hooks";
import { SectionRenderer } from "../components/Sections/SectionRenderer";
import { PALETTE } from "../theme";

export const OverviewPage: React.FC<{ runId: string | null }> = ({ runId }) => {
  const navigate = useNavigate();
  const { data: section, isLoading, error } = useSection(runId, "overview");

  const quickLinks = [
    {
      title: "Exploratory Analysis (7 Tabs)",
      desc: "Inspect target seasonality, 2023 structural break, Pearson/Spearman correlations, and catalog decay.",
      path: "/explore",
    },
    {
      title: "Meridian Bayesian MMM",
      desc: "Review posterior convergence (R-hat < 1.02), prior vs posterior shifts, and Q4/Q3 holdouts.",
      path: "/model",
    },
    {
      title: "Budget Optimiser",
      desc: "Simulate constrained budget reallocation across paid channels and inspect marginal ROI curves.",
      path: "/optimizer",
    },
    {
      title: "Forecast & Scenarios",
      desc: "Upload a custom budget plan CSV or run instant what-if multiplier scenarios.",
      path: "/forecast",
    },
  ];

  return (
    <Box>
      <SectionRenderer
        section={section}
        isLoading={isLoading}
        error={error as Error | null}
        emptyMessage="Select or start a run on the Import & Runs page to view the Executive Overview."
      />

      <Grid container spacing={2} sx={{ mt: 1 }}>
        {quickLinks.map((item) => (
          <Grid item xs={12} sm={6} md={3} key={item.path}>
            <Paper
              sx={{
                p: 2.5,
                height: "100%",
                display: "flex",
                flexDirection: "column",
                justifyContent: "space-between",
              }}
            >
              <Box>
                <Typography variant="h3" sx={{ mb: 1 }}>
                  {item.title}
                </Typography>
                <Typography variant="body2" sx={{ color: PALETTE.inkSecondary, mb: 2 }}>
                  {item.desc}
                </Typography>
              </Box>
              <Stack direction="row" justifyContent="flex-end">
                <Button
                  size="small"
                  endIcon={<ArrowForwardIcon fontSize="small" />}
                  onClick={() => navigate(item.path)}
                >
                  Explore
                </Button>
              </Stack>
            </Paper>
          </Grid>
        ))}
      </Grid>
    </Box>
  );
};
