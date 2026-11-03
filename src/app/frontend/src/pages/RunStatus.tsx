import React, { useEffect, useState } from "react";
import { Alert, Box, Button, Stack } from "@mui/material";
import { useQueryClient } from "@tanstack/react-query";
import { Link as RouterLink, useNavigate } from "react-router-dom";
import { api } from "../api/client";
import { useRun } from "../api/hooks";
import { ConfirmDialog } from "../components/Import/ConfirmDialog";
import { PipelineProgress } from "../components/Import/PipelineProgress";
import { RunHeader } from "../components/Import/RunHeader";
import { RunOutcome } from "../components/Import/RunOutcome";
import { NoRunSelected, RunPageHeader } from "../components/Import/RunPage";

/** Where the selected run stands: its lifecycle, outcome and the pipeline's stages per queue. */
export const RunStatusPage: React.FC<{ runId: string | null }> = ({ runId }) => {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const { data: run, isLoading } = useRun(runId);
  const [confirmCancel, setConfirmCancel] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);

  useEffect(() => setActionError(null), [runId]);

  const handleCancel = async () => {
    setConfirmCancel(false);
    if (!run) return;
    setActionError(null);
    try {
      await api.cancelRun(run.id);
    } catch (err) {
      setActionError(err instanceof Error && err.message ? err.message : "The run could not be cancelled.");
    }
    await Promise.all([queryClient.invalidateQueries({ queryKey: ["run", run.id] }), queryClient.invalidateQueries({ queryKey: ["runs"] })]);
  };

  return (
    <Box>
      <RunPageHeader title="Run status">
        Follow the pipeline of the selected run: the analysis and Meridian queues run in parallel, and every page of the studio
        fills in as its stage finishes.
      </RunPageHeader>

      {!run ? (
        <NoRunSelected loading={Boolean(runId) && isLoading} />
      ) : (
        <Stack spacing={3}>
          <RunHeader run={run} />
          {actionError && (
            <Alert severity="error" onClose={() => setActionError(null)}>
              {actionError}
            </Alert>
          )}
          {run.status === "draft" && (
            <Alert
              severity="info"
              action={
                <Button color="inherit" size="small" component={RouterLink} to="/setup">
                  Open run setup
                </Button>
              }
            >
              This run has not started yet. Review its settings and start it on the run setup page.
            </Alert>
          )}
          <RunOutcome run={run} onRerun={() => navigate("/setup")} />
          {run.stages.length > 0 && <PipelineProgress run={run} onCancel={() => setConfirmCancel(true)} />}
        </Stack>
      )}

      <ConfirmDialog
        request={
          confirmCancel && run
            ? {
                title: "Cancel this run?",
                body: `The workers stop “${run.name}” after the current step. Results already computed stay available, and the run can be started again from the run setup page.`,
                safeLabel: "Keep running",
                confirmLabel: "Cancel run",
                tone: "warning",
              }
            : null
        }
        onCancel={() => setConfirmCancel(false)}
        onConfirm={handleCancel}
      />
    </Box>
  );
};
