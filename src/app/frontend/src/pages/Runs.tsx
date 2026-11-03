import React, { useState } from "react";
import { Alert, Box, Stack, Typography } from "@mui/material";
import { useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { api } from "../api/client";
import { useRuns } from "../api/hooks";
import type { RunSummaryItem } from "../api/types";
import { ConfirmDialog, type ConfirmRequest } from "../components/Import/ConfirmDialog";
import { RunTable } from "../components/Import/RunTable";
import { UploadDropzone } from "../components/Import/UploadDropzone";
import { PALETTE } from "../theme";

const DEFAULT_RUN_NAME = "Tchibo case study";

const HOW_IT_WORKS = [
  { title: "Upload", text: "The app profiles the workbook and infers how each channel enters the model." },
  { title: "Set up", text: "Check the channel roles and, if you want, change the model, priors and sampling." },
  { title: "Run and explore", text: "The pipeline runs on the server; every page fills in as its stage finishes." },
];

/** "Tchibo case study", then "Tchibo case study (2)", … so new drafts are told apart in the run list. */
function nextRunName(runs: RunSummaryItem[]): string {
  const names = new Set(runs.map((r) => r.name));
  if (!names.has(DEFAULT_RUN_NAME)) return DEFAULT_RUN_NAME;
  let n = 2;
  while (names.has(`${DEFAULT_RUN_NAME} (${n})`)) n += 1;
  return `${DEFAULT_RUN_NAME} (${n})`;
}

const errorText = (err: unknown, fallback: string) => (err instanceof Error && err.message ? err.message : fallback);

/** The run library: upload a workbook to create a draft, open, make default or delete a run. */
export const RunsPage: React.FC<{
  selectedRunId: string | null;
  onSelectRunId: (id: string | null) => void;
}> = ({ selectedRunId, onSelectRunId }) => {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const { data: runsData, isLoading } = useRuns();
  const runs = runsData?.runs ?? [];

  const [uploadingName, setUploadingName] = useState<string | null>(null);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [toDelete, setToDelete] = useState<RunSummaryItem | null>(null);

  const handleFile = async (file: File) => {
    if (!file.name.toLowerCase().endsWith(".xlsx")) {
      setUploadError(`“${file.name}” is not an Excel workbook. Choose an .xlsx file.`);
      return;
    }
    setUploadError(null);
    setUploadingName(file.name);
    try {
      const created = await api.uploadRun(file, nextRunName(runs), "full");
      // Wait for the refreshed run list first: the shell resets a selection that is not in the list yet
      await queryClient.invalidateQueries({ queryKey: ["runs"] });
      onSelectRunId(created.id);
      navigate("/setup", { state: { focusHeading: true } });
    } catch (err) {
      setUploadError(errorText(err, "The upload failed."));
    } finally {
      setUploadingName(null);
    }
  };

  const handleMakeDefault = async (run: RunSummaryItem) => {
    setActionError(null);
    try {
      await api.activateRun(run.id);
      await queryClient.invalidateQueries({ queryKey: ["runs"] });
      await queryClient.invalidateQueries({ queryKey: ["run", run.id] });
    } catch (err) {
      setActionError(errorText(err, "The default run could not be changed."));
    }
  };

  const handleDelete = async () => {
    const run = toDelete;
    setToDelete(null);
    if (!run) return;
    setActionError(null);
    try {
      await api.deleteRun(run.id);
      if (run.id === selectedRunId) onSelectRunId(null);
      await queryClient.invalidateQueries({ queryKey: ["runs"] });
    } catch (err) {
      setActionError(errorText(err, "The run could not be deleted."));
    }
  };

  const confirm: ConfirmRequest | null = toDelete && {
    title: `Delete “${toDelete.name}”?`,
    body: "This removes the run, its uploaded workbook and every result, model and report it produced. It cannot be undone.",
    safeLabel: "Keep it",
    confirmLabel: "Delete run",
    tone: "error",
  };

  const noRuns = !isLoading && runs.length === 0;

  return (
    <Box>
      <Box sx={{ mb: 3, maxWidth: "75ch" }}>
        <Typography variant="h1" sx={{ mb: 0.5 }}>
          Runs
        </Typography>
        <Typography variant="body1" sx={{ color: PALETTE.inkSecondary }}>
          Each run keeps its own workbook, settings and results. Upload the case-study workbook to create a draft, set it up,
          then follow the pipeline on the run status page.
        </Typography>
      </Box>

      {actionError && (
        <Alert severity="error" onClose={() => setActionError(null)} sx={{ mb: 2 }}>
          {actionError}
        </Alert>
      )}

      {noRuns ? (
        <Box sx={{ maxWidth: 880, mx: "auto" }}>
          <UploadDropzone variant="hero" uploadingName={uploadingName} error={uploadError} onClearError={() => setUploadError(null)} onFile={handleFile} />
          <Box component="section" aria-labelledby="how-heading" sx={{ mt: 4 }}>
            <Typography id="how-heading" variant="h3" component="h2" sx={{ mb: 1.5 }}>
              How it works
            </Typography>
            <Box component="ol" sx={{ listStyle: "none", m: 0, p: 0, display: "grid", gap: 2, gridTemplateColumns: "repeat(auto-fit, minmax(min(100%, 220px), 1fr))" }}>
              {HOW_IT_WORKS.map((step, i) => (
                <Box component="li" key={step.title} sx={{ display: "grid", gridTemplateColumns: "32px minmax(0, 1fr)", columnGap: 1.5 }}>
                  <Box
                    aria-hidden="true"
                    sx={{ width: 32, height: 32, borderRadius: "50%", display: "grid", placeItems: "center", fontWeight: 800, color: PALETTE.brandGoldDark, backgroundColor: PALETTE.bgGoldTint }}
                  >
                    {i + 1}
                  </Box>
                  <Box>
                    <Typography component="h3" sx={{ fontWeight: 700, color: PALETTE.inkPrimary }}>
                      {step.title}
                    </Typography>
                    <Typography variant="body2">{step.text}</Typography>
                  </Box>
                </Box>
              ))}
            </Box>
          </Box>
        </Box>
      ) : (
        <Box sx={{ display: "grid", gap: 3, alignItems: "start", gridTemplateColumns: { xs: "minmax(0, 1fr)", lg: "300px minmax(0, 1fr)", xl: "340px minmax(0, 1fr)" } }}>
          <Stack spacing={3} sx={{ minWidth: 0 }}>
            <UploadDropzone variant="compact" uploadingName={uploadingName} error={uploadError} onClearError={() => setUploadError(null)} onFile={handleFile} />
          </Stack>
          <RunTable runs={runs} loading={isLoading} selectedRunId={selectedRunId} onSelect={onSelectRunId} onMakeDefault={handleMakeDefault} onDelete={setToDelete} />
        </Box>
      )}

      <ConfirmDialog request={confirm} onCancel={() => setToDelete(null)} onConfirm={handleDelete} />
    </Box>
  );
};
