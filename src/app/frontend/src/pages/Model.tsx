import React from "react";
import { Alert, Box } from "@mui/material";
import { useRun, useSection } from "../api/hooks";
import { SectionRenderer } from "../components/Sections/SectionRenderer";

export const ModelPage: React.FC<{ runId: string | null }> = ({ runId }) => {
  const { data: currentRun } = useRun(runId);
  const { data: section, isLoading, error } = useSection(runId, "meridian_model");
  const stage = (key: string) => currentRun?.stages?.find((s) => s.key === key);
  const fit = stage("s12_meridian_fit");
  const selection = stage("s16_model_selection");

  return (
    <Box>
      {fit?.status === "running" && (
        <Alert severity="info" sx={{ mb: 3 }}>
          Meridian's MCMC sampling is running on the background worker (main fit, then the forward hold-out refit). The
          results appear here automatically.
        </Alert>
      )}
      {selection?.status === "running" && (
        <Alert severity="info" sx={{ mb: 3 }}>
          The model-selection grid and prior sensitivity fits are running; their tables replace the note below when they finish.
        </Alert>
      )}
      {fit?.status === "skipped" && !section && (
        <Alert severity="warning" sx={{ mb: 3 }}>
          This run skipped Meridian. Re-run it from the Run setup page with Meridian enabled to fit the Bayesian model.
        </Alert>
      )}
      {fit?.status === "failed" && (
        <Alert severity="error" sx={{ mb: 3 }}>
          The Meridian fit failed: {fit.error}
        </Alert>
      )}
      <SectionRenderer
        section={section}
        isLoading={isLoading}
        error={error as Error | null}
        pendingMessage="The Meridian model page appears when the main Bayesian fit has finished (a few minutes after the analysis stages)."
      />
    </Box>
  );
};
