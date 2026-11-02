import React from "react";
import { Box, Paper, Tab, Tabs } from "@mui/material";
import { useSearchParams } from "react-router-dom";
import { useSection } from "../api/hooks";
import { SectionRenderer } from "../components/Sections/SectionRenderer";

const EXPLORE_TABS = [
  { id: "explore_target", slug: "target", label: "Target" },
  { id: "explore_channels", slug: "channels", label: "Channels" },
  { id: "explore_spend", slug: "spend", label: "Spend" },
  { id: "explore_efficiency", slug: "efficiency", label: "Efficiency & regimes" },
  { id: "explore_correlations", slug: "correlations", label: "Correlations & lags" },
  { id: "explore_catalog", slug: "catalog", label: "Catalog deep-dive" },
  { id: "explore_readiness", slug: "readiness", label: "MMM readiness" },
];

export const ExplorePage: React.FC<{ runId: string | null }> = ({ runId }) => {
  // The tab lives in the URL (?tab=catalog) so it survives reloads and can be shared
  const [params, setParams] = useSearchParams();
  const tabIdx = Math.max(0, EXPLORE_TABS.findIndex((t) => t.slug === params.get("tab")));
  const activeTab = EXPLORE_TABS[tabIdx];
  const { data: section, isLoading, error } = useSection(runId, activeTab.id);

  return (
    <Box>
      <Paper sx={{ mb: 3, px: 1 }}>
        <Tabs
          value={tabIdx}
          onChange={(_, next) => setParams({ tab: EXPLORE_TABS[next].slug }, { replace: true })}
          variant="scrollable"
          scrollButtons="auto"
          allowScrollButtonsMobile
          aria-label="Exploratory analysis topics"
        >
          {EXPLORE_TABS.map((t) => (
            <Tab key={t.id} label={t.label} id={`explore-tab-${t.slug}`} aria-controls="explore-panel" />
          ))}
        </Tabs>
      </Paper>
      <Box id="explore-panel" role="tabpanel" aria-labelledby={`explore-tab-${activeTab.slug}`}>
        <SectionRenderer section={section} isLoading={isLoading} error={error as Error | null} />
      </Box>
    </Box>
  );
};
