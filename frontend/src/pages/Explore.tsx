import React from "react";
import { Box } from "@mui/material";
import { useRun, useSection } from "../api/hooks";
import { PageHeader } from "../components/Layout/PageHeader";
import { PageTabs, panelId, tabId, usePageTab } from "../components/Layout/PageTabs";
import { SectionRenderer } from "../components/Sections/SectionRenderer";
import { CURATIONS } from "../components/Sections/curations";

const EXPLORE_TABS = [
  { id: "explore_target", slug: "target", label: "Sales" },
  { id: "explore_channels", slug: "channels", label: "Channels" },
  { id: "explore_spend", slug: "spend", label: "Spend" },
  { id: "explore_efficiency", slug: "efficiency", label: "Efficiency & regimes" },
  { id: "explore_correlations", slug: "correlations", label: "Correlations & lags" },
  { id: "explore_catalog", slug: "catalog", label: "Catalog" },
  { id: "explore_readiness", slug: "readiness", label: "MMM readiness" },
];

// The stage behind a topic that only some data has (sales per channel, funnel metrics, a print channel)
const OPTIONAL_STAGES: Record<string, string> = {
  explore_channels: "s04_channels",
  explore_efficiency: "s06_efficiency",
  explore_catalog: "s08_catalog",
};

export const ExplorePage: React.FC<{ runId: string | null }> = ({ runId }) => {
  const { data: run } = useRun(runId);
  // A topic whose stage was skipped (the data has nothing for it) is not offered
  const skipped = new Set((run?.stages ?? []).filter((s) => s.status === "skipped").map((s) => s.key));
  const tabs = EXPLORE_TABS.filter((t) => !OPTIONAL_STAGES[t.id] || !skipped.has(OPTIONAL_STAGES[t.id]));
  const [tab, setTab] = usePageTab(tabs);
  const { data: section, isLoading, error } = useSection(runId, tab.id);

  return (
    <Box>
      <PageHeader description="The data before any model: sales, channels, spend and efficiency, how spend and sales move together, a print channel's delayed effect, and what it all means for the MMM." />
      <PageTabs scope="explore" tabs={tabs} active={tab.slug} onChange={setTab} label="Exploration topics" />
      <Box id={panelId("explore")} role="tabpanel" aria-labelledby={tabId("explore", tab.slug)}>
        <SectionRenderer
          key={tab.id}
          section={section}
          isLoading={isLoading}
          error={error as Error | null}
          headingLevel={0}
          title={null}
          labelledBy={tabId("explore", tab.slug)}
          curation={CURATIONS[tab.id]}
        />
      </Box>
    </Box>
  );
};
