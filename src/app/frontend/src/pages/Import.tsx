import React, { useEffect, useRef, useState } from "react";
import { Alert, Box, Button, Collapse, Dialog, DialogActions, DialogContent, DialogContentText, DialogTitle, Paper, Skeleton, Stack, Typography } from "@mui/material";
import ExpandMoreIcon from "@mui/icons-material/ExpandMore";
import { useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import { isRunActive, useRun, useRuns } from "../api/hooks";
import type { RunDetail, RunSummaryItem } from "../api/types";
import { DataProfileCard } from "../components/Import/DataProfileCard";
import { PipelineProgress } from "../components/Import/PipelineProgress";
import { RunHeader } from "../components/Import/RunHeader";
import { RunList } from "../components/Import/RunList";
import { RunOutcome } from "../components/Import/RunOutcome";
import { RunSettingsForm, type RunSettingsValues } from "../components/Import/RunSettingsForm";
import { UploadDropzone } from "../components/Import/UploadDropzone";
import { PROFILES, prefersReducedMotion } from "../components/Import/format";
import { PALETTE } from "../theme";

const DEFAULT_RUN_NAME = "Tchibo case study";

const HOW_IT_WORKS = [
  { title: "Upload", text: "The app profiles the workbook and infers how each channel enters the model." },
  { title: "Review", text: "Check the channel roles and choose how thoroughly Meridian samples." },
  { title: "Run and explore", text: "The pipeline runs on the server; every page fills in as its stage finishes." },
];

type Confirm = { kind: "delete" } | { kind: "cancel" } | { kind: "rerun"; values: RunSettingsValues } | null;

/** "Tchibo case study", then "Tchibo case study (2)", … so new drafts are told apart in the run list. */
function nextRunName(runs: RunSummaryItem[]): string {
  const names = new Set(runs.map((r) => r.name));
  if (!names.has(DEFAULT_RUN_NAME)) return DEFAULT_RUN_NAME;
  let n = 2;
  while (names.has(`${DEFAULT_RUN_NAME} (${n})`)) n += 1;
  return `${DEFAULT_RUN_NAME} (${n})`;
}

const errorText = (err: unknown, fallback: string) => (err instanceof Error && err.message ? err.message : fallback);

/** Settings of a run that has already started, folded away behind a disclosure (APG pattern: a button inside a heading). */
const SettingsDisclosure: React.FC<{ run: RunDetail; open: boolean; onToggle: () => void; onOpened: () => void; children: React.ReactNode }> = ({
  run,
  open,
  onToggle,
  onOpened,
  children,
}) => {
  const locked = isRunActive(run);
  const summary = run.config?.skip_meridian ? "EDA and OLS only" : `${PROFILES[run.profile]?.label ?? run.profile} sampling`;
  return (
    <Paper component="section" aria-labelledby="settings-toggle" sx={{ scrollMarginTop: 84 }} id="run-settings">
      <Typography component="h2" sx={{ m: 0 }}>
        <Box
          component="button"
          type="button"
          id="settings-toggle"
          aria-expanded={open}
          aria-controls="settings-panel"
          onClick={onToggle}
          sx={{
            all: "unset",
            boxSizing: "border-box",
            width: "100%",
            display: "flex",
            alignItems: "center",
            gap: 2,
            px: { xs: 2.5, sm: 3 },
            py: 2,
            cursor: "pointer",
            borderRadius: "10px",
            "&:hover": { backgroundColor: PALETTE.bgCanvas },
            "&:focus-visible": { outline: `3px solid ${PALETTE.brandPrimary}`, outlineOffset: "-3px" },
          }}
        >
          <Box component="span" sx={{ flexGrow: 1, minWidth: 0 }}>
            <Box component="span" sx={{ display: "block", fontWeight: 650, fontSize: "1.0625rem", color: PALETTE.inkPrimary }}>
              {locked ? "Run settings" : "Settings and re-run"}
            </Box>
            <Box component="span" sx={{ display: "block", fontSize: "0.84rem", color: PALETTE.inkSecondary }}>
              {summary} · {run.config?.role_config?.channels?.length ?? 0} channels{locked ? " · locked while the run is active" : ""}
            </Box>
          </Box>
          <ExpandMoreIcon aria-hidden="true" sx={{ color: PALETTE.inkSecondary, transform: open ? "rotate(180deg)" : "none", transition: "transform 0.2s" }} />
        </Box>
      </Typography>
      <Collapse in={open} id="settings-panel" onEntered={onOpened} sx={{ position: "relative" }}>
        <Box sx={{ pt: 1 }}>{children}</Box>
      </Collapse>
    </Paper>
  );
};

export const ImportPage: React.FC<{
  selectedRunId: string | null;
  onSelectRunId: (id: string | null) => void;
}> = ({ selectedRunId, onSelectRunId }) => {
  const queryClient = useQueryClient();
  const { data: runsData, isLoading: runsLoading } = useRuns();
  const runs = runsData?.runs ?? [];
  const { data: currentRun, isLoading: runLoading } = useRun(selectedRunId);

  const [uploadingName, setUploadingName] = useState<string | null>(null);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [formError, setFormError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [confirm, setConfirm] = useState<Confirm>(null);
  // MUI's dialog focus trap lands on the dialog container, so the safe choice is focused once the dialog has opened
  const safeChoice = useRef<HTMLButtonElement>(null);
  // The dialog keeps showing the last request while it animates closed
  const lastConfirm = useRef<Confirm>(null);
  if (confirm) lastConfirm.current = confirm;
  const dialog = confirm ?? lastConfirm.current;
  const [settingsOpen, setSettingsOpen] = useState(false);

  // After an upload or a start, focus and reveal the run's heading once it has rendered
  const headingRef = useRef<HTMLHeadingElement>(null);
  const revealRun = useRef(false);
  useEffect(() => {
    if (!revealRun.current || !currentRun || !headingRef.current) return;
    revealRun.current = false;
    headingRef.current.focus({ preventScroll: true });
    headingRef.current.closest("section")?.scrollIntoView({ behavior: prefersReducedMotion() ? "auto" : "smooth", block: "start" });
  }, [currentRun]);

  useEffect(() => {
    setFormError(null);
    setActionError(null);
    setSettingsOpen(false);
  }, [selectedRunId]);

  const refreshRun = (id: string) =>
    Promise.all([queryClient.invalidateQueries({ queryKey: ["run", id] }), queryClient.invalidateQueries({ queryKey: ["runs"] })]);

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
      revealRun.current = true;
      onSelectRunId(created.id);
    } catch (err) {
      setUploadError(errorText(err, "The upload failed."));
    } finally {
      setUploadingName(null);
    }
  };

  const startRun = async (run: RunDetail, values: RunSettingsValues) => {
    setFormError(null);
    setSubmitting(true);
    try {
      await api.updateRunConfig(run.id, {
        name: run.status === "draft" ? values.name || run.name : undefined,
        profile: values.profile,
        role_config: values.roleConfig,
        skip_meridian: values.skipMeridian,
      });
      await api.startRun(run.id, { profile: values.profile, skip_meridian: values.skipMeridian, seed: run.config?.seed ?? 0 });
      queryClient.removeQueries({ queryKey: ["section", run.id] });
      // Set before the refetch: the refreshed run is the render that moves focus to the heading
      revealRun.current = true;
      setSettingsOpen(false);
      await refreshRun(run.id);
    } catch (err) {
      setFormError(errorText(err, "The run could not be started."));
    } finally {
      setSubmitting(false);
    }
  };

  const handleSubmit = (values: RunSettingsValues) => {
    if (!currentRun) return;
    if (currentRun.status === "draft") void startRun(currentRun, values);
    else setConfirm({ kind: "rerun", values });
  };

  const handleConfirm = async () => {
    const c = confirm;
    const run = currentRun;
    setConfirm(null);
    if (!c || !run) return;
    setActionError(null);
    try {
      if (c.kind === "rerun") {
        await startRun(run, c.values);
      } else if (c.kind === "cancel") {
        await api.cancelRun(run.id);
        await refreshRun(run.id);
      } else {
        await api.deleteRun(run.id);
        onSelectRunId(null);
        await queryClient.invalidateQueries({ queryKey: ["runs"] });
      }
    } catch (err) {
      setActionError(errorText(err, c.kind === "delete" ? "The run could not be deleted." : "The run could not be cancelled."));
    }
  };

  const handleMakeDefault = async () => {
    if (!currentRun) return;
    try {
      await api.activateRun(currentRun.id);
      await refreshRun(currentRun.id);
    } catch (err) {
      setActionError(errorText(err, "The default run could not be changed."));
    }
  };

  // "Review settings and re-run" scrolls to the settings once the panel has expanded (before that the page is too short)
  const scrollToSettings = useRef(false);
  const revealSettings = () =>
    document.getElementById("run-settings")?.scrollIntoView({ behavior: prefersReducedMotion() ? "auto" : "smooth", block: "start" });
  const openSettings = () => {
    if (settingsOpen) revealSettings();
    else {
      scrollToSettings.current = true;
      setSettingsOpen(true);
    }
  };
  const handleSettingsOpened = () => {
    if (!scrollToSettings.current) return;
    scrollToSettings.current = false;
    revealSettings();
  };

  const noRuns = !runsLoading && runs.length === 0;
  const active = isRunActive(currentRun);
  const channelCount = currentRun?.config?.role_config?.channels?.length ?? currentRun?.schema?.role_config?.channels?.length ?? 0;

  const settingsForm = currentRun && (
    <RunSettingsForm
      key={currentRun.id}
      run={currentRun}
      mode={currentRun.status === "draft" ? "draft" : "rerun"}
      locked={active}
      submitting={submitting}
      error={formError}
      onClearError={() => setFormError(null)}
      onSubmit={handleSubmit}
    />
  );

  let detail: React.ReactNode;
  if (!currentRun) {
    detail =
      selectedRunId && runLoading ? (
        <Paper sx={{ p: 3 }}>
          <Skeleton width="30%" />
          <Skeleton width="55%" height={36} />
          <Skeleton width="70%" />
        </Paper>
      ) : (
        <Paper sx={{ p: 4, textAlign: "center", borderStyle: "dashed", backgroundColor: PALETTE.bgSubtle }}>
          <Typography variant="h3" component="h2">
            No run selected
          </Typography>
          <Typography variant="body2" sx={{ mt: 0.5 }}>
            Pick a run from the list, or upload a workbook to create one.
          </Typography>
        </Paper>
      );
  } else {
    detail = (
      <>
        <RunHeader ref={headingRef} run={currentRun} onMakeDefault={handleMakeDefault} onDelete={() => setConfirm({ kind: "delete" })} />
        {actionError && (
          <Alert severity="error" onClose={() => setActionError(null)}>
            {actionError}
          </Alert>
        )}
        <RunOutcome run={currentRun} onRerun={openSettings} />
        {currentRun.stages.length > 0 && <PipelineProgress run={currentRun} onCancel={() => setConfirm({ kind: "cancel" })} />}
        {currentRun.status === "draft" ? (
          <>
            {currentRun.schema && <DataProfileCard profile={currentRun.schema} channelCount={channelCount} />}
            <Paper component="section" aria-labelledby="review-heading">
              <Box sx={{ px: { xs: 2.5, sm: 3 }, pt: 2.5, pb: 2 }}>
                <Typography id="review-heading" variant="h3" component="h2">
                  Review and start
                </Typography>
                <Typography variant="body2">The app inferred these settings from the workbook. Adjust anything that looks wrong, then start the run.</Typography>
              </Box>
              {settingsForm}
            </Paper>
          </>
        ) : (
          <>
            {currentRun.schema && <DataProfileCard profile={currentRun.schema} channelCount={channelCount} />}
            <SettingsDisclosure run={currentRun} open={settingsOpen} onToggle={() => setSettingsOpen((v) => !v)} onOpened={handleSettingsOpened}>
              {settingsForm}
            </SettingsDisclosure>
          </>
        )}
      </>
    );
  }

  return (
    <Box>
      <Box sx={{ mb: 3, maxWidth: "75ch" }}>
        <Typography variant="h1" sx={{ mb: 0.5 }}>
          Import & runs
        </Typography>
        <Typography variant="body1" sx={{ color: PALETTE.inkSecondary }}>
          Upload the case-study workbook, check how each channel enters the model and start the pipeline: data audit, EDA,
          preparation, the OLS benchmark, the Google Meridian MMM, the optimiser and the forecast. Each run keeps its own
          workbook, settings and results.
        </Typography>
      </Box>

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
        <Box
          sx={{
            display: "grid",
            gap: 3,
            alignItems: "start",
            gridTemplateColumns: { xs: "minmax(0, 1fr)", lg: "300px minmax(0, 1fr)", xl: "340px minmax(0, 1fr)" },
          }}
        >
          <Stack spacing={3} sx={{ minWidth: 0 }}>
            <UploadDropzone variant="compact" uploadingName={uploadingName} error={uploadError} onClearError={() => setUploadError(null)} onFile={handleFile} />
            <RunList runs={runs} loading={runsLoading} selectedRunId={selectedRunId} onSelect={onSelectRunId} />
          </Stack>
          <Stack spacing={3} sx={{ minWidth: 0 }}>
            {detail}
          </Stack>
        </Box>
      )}

      <Dialog
        open={confirm !== null}
        onClose={() => setConfirm(null)}
        maxWidth="xs"
        fullWidth
        aria-labelledby="confirm-title"
        TransitionProps={{ onEntered: () => safeChoice.current?.focus() }}
      >
        {dialog?.kind === "delete" && (
          <>
            <DialogTitle id="confirm-title">Delete “{currentRun?.name}”?</DialogTitle>
            <DialogContent>
              <DialogContentText>This removes the run, its uploaded workbook and every result, model and report it produced. It cannot be undone.</DialogContentText>
            </DialogContent>
            <DialogActions>
              <Button ref={safeChoice} onClick={() => setConfirm(null)}>
                Keep it
              </Button>
              <Button color="error" variant="contained" onClick={handleConfirm}>
                Delete run
              </Button>
            </DialogActions>
          </>
        )}
        {dialog?.kind === "rerun" && (
          <>
            <DialogTitle id="confirm-title">Re-run “{currentRun?.name}”?</DialogTitle>
            <DialogContent>
              <DialogContentText>
                The current results (sections, models, reports and the example forecast) are replaced by the new run. The uploaded workbook is kept.
              </DialogContentText>
            </DialogContent>
            <DialogActions>
              <Button ref={safeChoice} onClick={() => setConfirm(null)}>
                Keep the results
              </Button>
              <Button variant="contained" onClick={handleConfirm}>
                Re-run
              </Button>
            </DialogActions>
          </>
        )}
        {dialog?.kind === "cancel" && (
          <>
            <DialogTitle id="confirm-title">Cancel this run?</DialogTitle>
            <DialogContent>
              <DialogContentText>
                The workers stop “{currentRun?.name}” after the current step. Results already computed stay available, and the run can be started again here.
              </DialogContentText>
            </DialogContent>
            <DialogActions>
              <Button ref={safeChoice} onClick={() => setConfirm(null)}>
                Keep running
              </Button>
              <Button color="warning" variant="contained" onClick={handleConfirm}>
                Cancel run
              </Button>
            </DialogActions>
          </>
        )}
      </Dialog>
    </Box>
  );
};
