import React, { useState } from "react";
import { Box, Paper, Tab, Tabs } from "@mui/material";
import { useSection } from "../api/hooks";
import { SectionRenderer } from "../components/Sections/SectionRenderer";

const EXPLORE_TABS = [
  { id: "explore_target", label: "Target & Seasonality" },
  { id: "explore_channels", label: "Channel Activity & Break" },
  { id: "explore_spend", label: "Spend & Mix" },
  { id: "explore_efficiency", label: "Efficiency Proxies" },
  { id: "explore_correlations", label: "Correlations & Lags" },
  { id: "explore_catalog", label: "Catalog Deep-Dive" },
  { id: "explore_readiness", label: "Model Readiness" },
];

export const ExplorePage: React.FC<{ runId: string | null }> = ({ runId }) => {
  const [tabIdx, setTabIdx] = useState(0);
  const activeTab = EXPLORE_TABS[tabIdx] || EXPLORE_TABS[0];
  const { data: section, isLoading, error } = useSection(runId, activeTab.id);

  return (
    <Box>
      <Paper sx={{ mb: 3, px: 1 }}>
        <Tabs
          value={tabIdx}
          onChange={(_, next) => setTabIdx(next)}
          variant="scrollable"
          scrollButtons="auto"
        >
          {EXPLORE_TABS.map((t) => (
            <Tab key={t.id} label={t.label} sx={{ textTransform: "none", fontWeight: 600 }} />
          ))}
        </Tabs>
      </Paper>

      <SectionRenderer section={section} isLoading={isLoading} error={error as Error | null} />
    </Box>
  );
};
