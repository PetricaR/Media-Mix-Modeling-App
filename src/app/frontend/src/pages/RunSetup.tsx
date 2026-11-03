import React, { useEffect, useState } from "react";
import { Alert, Box, Button, Chip, Paper, Stack, Typography } from "@mui/material";
import { useQueryClient } from "@tanstack/react-query";
import { Link as RouterLink, useNavigate } from "react-router-dom";
import { api } from "../api/client";
import { isRunActive, useRun } from "../api/hooks";
import type { RunDetail } from "../api/types";
import { ConfirmDialog } from "../components/Import/ConfirmDialog";
import { NoRunSelected, RunPageHeader } from "../components/Import/RunPage";
import { RunSettingsForm, type RunSettingsValues } from "../components/Import/RunSettingsForm";
import { RUN_STATUS_LABEL, runStatusColor } from "../components/Import/format";
import { PALETTE } from "../theme";

const errorText = (err: unknown, fallback: string) => (err instanceof Error && err.message ? err.message : fallback);

const INTRO: Record<string, string> = {
  draft: "The app inferred these settings from the workbook. Adjust anything that looks wrong, then start the run.",
  queued: "The run is waiting for a worker. Its settings are locked until it finishes or is cancelled.",
  running: "The run is in progress. Its settings are locked until it finishes or is cancelled.",
  succeeded: "The settings this run used. Change them and re-run to replace its results.",
  failed: "The settings this run used. Adjust them if needed and run it again; the uploaded workbook is kept.",
  cancelled: "The settings this run used. Start it again to complete the remaining stages.",
};

/** Review a draft's settings and start it, or change a finished run's settings and re-run it. */
export const RunSetupPage: React.FC<{ runId: string | null }> = ({ runId }) => {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const { data: run, isLoading } = useRun(runId);
  const [submitting, setSubmitting] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [pendingRerun, setPendingRerun] = useState<RunSettingsValues | null>(null);

  useEffect(() => setFormError(null), [runId]);

  const startRun = async (r: RunDetail, values: RunSettingsValues) => {
    setFormError(null);
    setSubmitting(true);
    try {
      const name = values.name.trim();
      if (r.status === "draft" && name && name !== r.name) await api.updateRunConfig(r.id, { name });
      // One request: the server checks everything first, so a refused start (queue full, invalid settings) leaves the
      // run's settings and results exactly as they were
      await api.startRun(r.id, {
        profile: values.profile,
        role_config: values.roleConfig,
        skip_meridian: values.skipMeridian,
        seed: values.seed,
      });
      queryClient.removeQueries({ queryKey: ["section", r.id] });
      await Promise.all([queryClient.invalidateQueries({ queryKey: ["run", r.id] }), queryClient.invalidateQueries({ queryKey: ["runs"] })]);
      navigate("/status", { state: { focusHeading: true } });
    } catch (err) {
      setFormError(errorText(err, "The run could not be started."));
    } finally {
      setSubmitting(false);
    }
  };

  const handleSubmit = (values: RunSettingsValues) => {
    if (!run) return;
    if (run.status === "draft") void startRun(run, values);
    else setPendingRerun(values);
  };

  const confirmRerun = () => {
    const values = pendingRerun;
    setPendingRerun(null);
    if (run && values) void startRun(run, values);
  };

  const active = isRunActive(run);

  return (
    <Box>
      <RunPageHeader title="Run setup">{run ? INTRO[run.status] : "Every setting a run starts with: data, channels, the Meridian model, priors and sampling."}</RunPageHeader>

      {!run ? (
        <NoRunSelected loading={Boolean(runId) && isLoading} />
      ) : (
        <Stack spacing={3}>
          {active && (
            <Alert
              severity="info"
              action={
                <Button color="inherit" size="small" component={RouterLink} to="/status">
                  Open run status
                </Button>
              }
            >
              “{run.name}” is {run.status === "queued" ? "queued" : "running"}; settings can change again once it has finished or been cancelled.
            </Alert>
          )}

          <Paper component="section" aria-labelledby="settings-heading">
            <Box sx={{ px: { xs: 2.5, sm: 3 }, pt: 2.5, pb: 2, display: "flex", alignItems: "center", flexWrap: "wrap", gap: 1.25 }}>
              <Typography id="settings-heading" variant="h3" component="h2">
                {run.status === "draft" ? "Review and start" : "Settings"}
              </Typography>
              <Chip size="small" color={runStatusColor(run.status)} label={RUN_STATUS_LABEL[run.status] ?? run.status} />
              <Typography variant="body2" component="span" sx={{ color: PALETTE.inkSecondary, overflowWrap: "anywhere" }}>
                {run.name}
              </Typography>
            </Box>
            <RunSettingsForm
              key={run.id}
              run={run}
              dataProfile={run.schema}
              mode={run.status === "draft" ? "draft" : "rerun"}
              locked={active}
              submitting={submitting}
              error={formError}
              onClearError={() => setFormError(null)}
              onSubmit={handleSubmit}
            />
          </Paper>
        </Stack>
      )}

      <ConfirmDialog
        request={
          pendingRerun && run
            ? {
                title: `Re-run “${run.name}”?`,
                body: "The current results (sections, models, reports and the example forecast) are replaced by the new run. The uploaded workbook is kept.",
                safeLabel: "Keep the results",
                confirmLabel: "Re-run",
              }
            : null
        }
        onCancel={() => setPendingRerun(null)}
        onConfirm={confirmRerun}
      />
    </Box>
  );
};
