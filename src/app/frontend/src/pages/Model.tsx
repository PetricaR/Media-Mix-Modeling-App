import React from "react";
import { Alert, Box } from "@mui/material";
import { useRun, useSection } from "../api/hooks";
import { SectionRenderer } from "../components/Sections/SectionRenderer";

export const ModelPage: React.FC<{ runId: string | null }> = ({ runId }) => {
  const { data: currentRun } = useRun(runId);
  const { data: section, isLoading, error } = useSection(runId, "meridian_model");

  const meridianStage = currentRun?.stages?.find((s) => s.key === "s11_meridian_fit");

  return (
    <Box>
      {meridianStage?.status === "running" && (
        <Alert severity="info" sx={{ mb: 3 }}>
          Google Meridian Bayesian MCMC sampling is currently running on the background worker.
          Results will populate automatically when chains finish.
        </Alert>
      )}
      {meridianStage?.status === "skipped" && !section && (
        <Alert severity="warning" sx={{ mb: 3 }}>
          Meridian MCMC was skipped for this run (`skip_meridian=true`). Re-run with Quick or Full
          sampling on the Import & Runs page to fit the Bayesian MMM.
        </Alert>
      )}
      <SectionRenderer section={section} isLoading={isLoading} error={error as Error | null} />
    </Box>
  );
};
