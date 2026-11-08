import React from "react";
import { Alert, Box } from "@mui/material";
import { useRun, useSection } from "../api/hooks";
import { PageHeader } from "../components/Layout/PageHeader";
import { PageTabs, panelId, tabId, usePageTab } from "../components/Layout/PageTabs";
import { SectionRenderer } from "../components/Sections/SectionRenderer";
import { CURATIONS } from "../components/Sections/curations";

/**
 * Fit & validation, as an MMM review reads it: how well the model fits and forecasts unseen quarters, how it compares
 * with simpler models, whether it forecasts from a budget plan alone, and whether the sampler and the specification
 * are sound. The model section is shared by the Fit and Diagnostics tabs.
 */
const TABS = [
  {
    slug: "fit",
    label: "Fit",
    section: "meridian_model",
    curation: "meridian_model_fit",
    subtitle: "Daily sales against the model on the training period and the hold-out it never saw, with the error on every window.",
    pending: "The fit appears when the main Bayesian fit has finished (a few minutes after the analysis stages).",
  },
  {
    slug: "benchmark",
    label: "Benchmark",
    section: "benchmark",
    curation: "benchmark",
    subtitle: undefined,
    pending: "The OLS benchmark appears when the analysis stages have finished; Meridian's row is added after its fit.",
  },
  {
    slug: "backtest",
    label: "Back-test from a plan",
    section: "forecast_scenarios",
    curation: "forecast_backtest",
    subtitle: "The forecast a planner would have made: Q4 2024 predicted from the budget plan alone, commission channels estimated instead of their realised cost.",
    pending: "The back-test appears when the Meridian forecast stage has finished.",
  },
  {
    slug: "diagnostics",
    label: "Diagnostics",
    section: "meridian_model",
    curation: "meridian_model_diagnostics",
    subtitle: "Convergence, Meridian's own quality checks, the model-selection grid and the prior sensitivity fits, and the settings this run used.",
    pending: "Diagnostics appear when the main Bayesian fit has finished.",
  },
];

export const ModelPage: React.FC<{ runId: string | null }> = ({ runId }) => {
  const [tab, setTab] = usePageTab(TABS);
  const { data: currentRun } = useRun(runId);
  const { data: section, isLoading, error } = useSection(runId, tab.section);
  const stage = (key: string) => currentRun?.stages?.find((s) => s.key === key);
  const fit = stage("s12_meridian_fit");
  const selection = stage("s16_model_selection");

  // Stage notices sit under the page title, above the results they are about
  const notices = (
    <>
      {fit?.status === "running" && (
        <Alert severity="info" sx={{ mb: 3 }}>
          Meridian's MCMC sampling is running on the background worker (main fit, then the forward hold-out refit). The
          results appear here automatically.
        </Alert>
      )}
      {selection?.status === "running" && tab.slug === "diagnostics" && (
        <Alert severity="info" sx={{ mb: 3 }}>
          The model-selection grid and prior sensitivity fits are running; their tables replace the note below when they finish.
        </Alert>
      )}
      {fit?.status === "skipped" && (
        <Alert severity="warning" sx={{ mb: 3 }}>
          This run skipped Meridian. Re-run it from the Run setup page with Meridian enabled to fit the Bayesian model.
        </Alert>
      )}
      {fit?.status === "failed" && (
        <Alert severity="error" sx={{ mb: 3 }}>
          The Meridian fit failed: {fit.error}
        </Alert>
      )}
    </>
  );

  return (
    <Box>
      <PageHeader description="A Bayesian regression (Google Meridian) of daily total sales on adstocked, saturated media spend and calendar controls, validated on quarters it never saw." />
      {notices}
      <PageTabs scope="model" tabs={TABS} active={tab.slug} onChange={setTab} label="Fit and validation views" />
      <Box id={panelId("model")} role="tabpanel" aria-labelledby={tabId("model", tab.slug)}>
        <SectionRenderer
          key={tab.slug}
          section={section}
          isLoading={isLoading}
          error={error as Error | null}
          headingLevel={0}
          title={null}
          subtitle={tab.subtitle}
          labelledBy={tabId("model", tab.slug)}
          curation={CURATIONS[tab.curation]}
          pendingMessage={tab.pending}
        />
      </Box>
    </Box>
  );
};
