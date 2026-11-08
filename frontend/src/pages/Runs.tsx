import React, { useState } from "react";
import { Alert, Box, Stack, Typography } from "@mui/material";
import { useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { api } from "../api/client";
import { useRuns } from "../api/hooks";
import type { DataRoleConfig, RunSummaryItem } from "../api/types";
import { ConfirmDialog, type ConfirmRequest } from "../components/Import/ConfirmDialog";
import { RunTable } from "../components/Import/RunTable";
import { UploadDropzone } from "../components/Import/UploadDropzone";
import { UploadWizard } from "../components/Import/wizard/UploadWizard";
import { PageHeader } from "../components/Layout/PageHeader";
import { PALETTE } from "../theme";

const SAMPLE_FILE = /^sample[_-]?data/i;
const UPLOAD_TYPES = /\.(xlsx|csv)$/i;

const HOW_IT_WORKS = [
  { title: "Upload and map", text: "Any daily marketing table: the wizard (with the Gemini data assistant) maps the date, target, channels and controls." },
  { title: "Set up", text: "Check the channel roles and, if you want, change the model, priors and sampling." },
  { title: "Run and explore", text: "The pipeline runs on the server; every page fills in as its stage finishes." },
];

/** A name from the file ("Sample workbook" for the sample workbook), then "(2)", … so drafts are told apart. */
function nextRunName(runs: RunSummaryItem[], file: File): string {
  const stem = file.name.replace(/\.[^.]+$/, "").replace(/[_-]+/g, " ").trim();
  const base = SAMPLE_FILE.test(file.name) ? "Sample workbook" : stem.charAt(0).toUpperCase() + stem.slice(1) || "New run";
  const names = new Set(runs.map((r) => r.name));
  if (!names.has(base)) return base;
  let n = 2;
  while (names.has(`${base} (${n})`)) n += 1;
  return `${base} (${n})`;
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

  const [uploadError, setUploadError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [toDelete, setToDelete] = useState<RunSummaryItem | null>(null);
  const [wizardFile, setWizardFile] = useState<File | null>(null);

  // A dropped file opens the mapping wizard; the run is created only at its last step
  const handleFile = (file: File) => {
    if (!UPLOAD_TYPES.test(file.name)) {
      setUploadError(`“${file.name}” is not an Excel workbook or a CSV file. Choose an .xlsx or .csv file.`);
      return;
    }
    setUploadError(null);
    setWizardFile(file);
  };

  const handleCreate = async (roleConfig: DataRoleConfig, name: string) => {
    if (!wizardFile) return;
    const created = await api.uploadRun(wizardFile, name, "full", roleConfig);
    // Wait for the refreshed run list first: the shell resets a selection that is not in the list yet
    await queryClient.invalidateQueries({ queryKey: ["runs"] });
    setWizardFile(null);
    onSelectRunId(created.id);
    navigate("/setup", { state: { focusHeading: true } });
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
      <PageHeader description="Each run keeps its own data, settings and results. Upload a marketing table (the sample workbook or your own; hourly, daily, weekly or monthly), map and resample it in the wizard, set the run up, then follow the pipeline on the run status page." />

      {actionError && (
        <Alert severity="error" onClose={() => setActionError(null)} sx={{ mb: 2 }}>
          {actionError}
        </Alert>
      )}

      {wizardFile && (
        <Box sx={{ mb: 3 }}>
          <UploadWizard key={`${wizardFile.name}-${wizardFile.lastModified}`} file={wizardFile} defaultName={nextRunName(runs, wizardFile)} onCancel={() => setWizardFile(null)} onCreate={handleCreate} />
        </Box>
      )}

      {wizardFile && noRuns ? null : noRuns ? (
        <Box sx={{ maxWidth: 880, mx: "auto" }}>
          <UploadDropzone variant="hero" uploadingName={null} error={uploadError} onClearError={() => setUploadError(null)} onFile={handleFile} />
          <Box component="section" aria-labelledby="how-heading" sx={{ mt: 4 }}>
            <Typography id="how-heading" variant="h3" component="h2" sx={{ mb: 1.5 }}>
              How it works
            </Typography>
            <Box component="ol" sx={{ listStyle: "none", m: 0, p: 0, display: "grid", gap: 2, gridTemplateColumns: "repeat(auto-fit, minmax(min(100%, 220px), 1fr))" }}>
              {HOW_IT_WORKS.map((step, i) => (
                <Box component="li" key={step.title} sx={{ display: "grid", gridTemplateColumns: "32px minmax(0, 1fr)", columnGap: 1.5 }}>
                  <Box
                    aria-hidden="true"
                    sx={{ width: 32, height: 32, borderRadius: "50%", display: "grid", placeItems: "center", fontWeight: 800, color: PALETTE.brandPrimary, backgroundColor: PALETTE.bgAccentTint }}
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
            <UploadDropzone variant="compact" uploadingName={null} error={uploadError} onClearError={() => setUploadError(null)} onFile={handleFile} disabled={Boolean(wizardFile)} />
          </Stack>
          <RunTable runs={runs} loading={isLoading} selectedRunId={selectedRunId} onSelect={onSelectRunId} onMakeDefault={handleMakeDefault} onDelete={setToDelete} />
        </Box>
      )}

      <ConfirmDialog request={confirm} onCancel={() => setToDelete(null)} onConfirm={handleDelete} />
    </Box>
  );
};
