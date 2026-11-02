import React, { useRef, useState } from "react";
import {
  Alert,
  Box,
  Button,
  Chip,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogContentText,
  DialogTitle,
  FormControl,
  FormControlLabel,
  FormHelperText,
  Grid,
  IconButton,
  InputLabel,
  MenuItem,
  Paper,
  Select,
  Stack,
  Step,
  StepLabel,
  Stepper,
  Switch,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  TextField,
  Tooltip,
  Typography,
} from "@mui/material";
import CloudUploadOutlinedIcon from "@mui/icons-material/CloudUploadOutlined";
import PlayArrowIcon from "@mui/icons-material/PlayArrow";
import ReplayIcon from "@mui/icons-material/Replay";
import CheckCircleOutlineIcon from "@mui/icons-material/CheckCircleOutline";
import ErrorOutlineIcon from "@mui/icons-material/ErrorOutline";
import RemoveCircleOutlineIcon from "@mui/icons-material/RemoveCircleOutline";
import DeleteOutlineIcon from "@mui/icons-material/DeleteOutline";
import StarOutlineIcon from "@mui/icons-material/StarOutline";
import StarIcon from "@mui/icons-material/Star";
import { useNavigate } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import { isRunActive, useRun, useRuns } from "../api/hooks";
import type { ChannelConfig, ChannelRole, DataRoleConfig, RunSummaryItem } from "../api/types";
import { FONT_MONO, PALETTE } from "../theme";

type Profile = "quick" | "full" | "smoke";

const PROFILES: Record<Profile, { label: string; detail: string }> = {
  full: {
    label: "Full — as in the notebooks",
    detail:
      "Final model 4 chains × (1,000 adapt + 500 burn-in + 1,000 draws); forecast and back-test refits at the same size; plus the 9-fit model-selection grid. About 15–25 minutes on a laptop CPU.",
  },
  quick: {
    label: "Quick",
    detail: "Every Meridian fit with 2 chains × (300 + 200 + 200 draws); no selection grid. About 5–8 minutes.",
  },
  smoke: {
    label: "Smoke test",
    detail: "2 chains × 50 draws: checks the pipeline end to end in ~4 minutes. Results are not converged.",
  },
};

const STATUS_LABEL: Record<string, string> = {
  draft: "Draft",
  queued: "Queued",
  running: "Running",
  succeeded: "Finished",
  failed: "Failed",
  cancelled: "Cancelled",
};

export const ImportPage: React.FC<{
  selectedRunId: string | null;
  onSelectRunId: (id: string | null) => void;
}> = ({ selectedRunId, onSelectRunId }) => {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { data: runsData } = useRuns();
  const { data: currentRun } = useRun(selectedRunId);
  const fileInput = useRef<HTMLInputElement>(null);

  const [runName, setRunName] = useState("Tchibo case study");
  const [profile, setProfile] = useState<Profile>("full");
  const [skipMeridian, setSkipMeridian] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [starting, setStarting] = useState(false);
  const [dragOver, setDragOver] = useState(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [roleConfigDraft, setRoleConfigDraft] = useState<DataRoleConfig | null>(null);
  const [toDelete, setToDelete] = useState<RunSummaryItem | null>(null);

  React.useEffect(() => {
    setRoleConfigDraft(currentRun?.config?.role_config ?? currentRun?.schema?.role_config ?? null);
    if (currentRun?.profile) setProfile(currentRun.profile);
    if (currentRun?.config) setSkipMeridian(Boolean(currentRun.config.skip_meridian));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentRun?.id]);

  const uploadFile = async (file: File) => {
    if (!file.name.toLowerCase().endsWith(".xlsx")) {
      setErrorMsg("Please choose an Excel workbook (.xlsx).");
      return;
    }
    setUploading(true);
    setErrorMsg(null);
    try {
      const created = await api.uploadRun(file, runName || file.name, profile);
      onSelectRunId(created.id);
      setRoleConfigDraft(created.config?.role_config ?? created.schema?.role_config ?? null);
      queryClient.invalidateQueries({ queryKey: ["runs"] });
    } catch (err) {
      setErrorMsg(err instanceof Error ? err.message : "Upload failed");
    } finally {
      setUploading(false);
      if (fileInput.current) fileInput.current.value = "";
    }
  };

  const handleRoleChange = (chIdx: number, newRole: ChannelRole) => {
    if (!roleConfigDraft) return;
    const channels: ChannelConfig[] = roleConfigDraft.channels.map((c, idx) => (idx === chIdx ? { ...c, role: newRole } : c));
    setRoleConfigDraft({ ...roleConfigDraft, channels });
  };

  const handleConfirmAndStart = async () => {
    if (!selectedRunId) return;
    setErrorMsg(null);
    setStarting(true);
    try {
      await api.updateRunConfig(selectedRunId, {
        name: currentRun?.status === "draft" ? runName || currentRun?.name : undefined,
        profile,
        role_config: roleConfigDraft || undefined,
        skip_meridian: skipMeridian,
      });
      await api.startRun(selectedRunId, { profile, skip_meridian: skipMeridian, seed: currentRun?.config?.seed ?? 0 });
      queryClient.invalidateQueries({ queryKey: ["run", selectedRunId] });
      queryClient.invalidateQueries({ queryKey: ["runs"] });
      queryClient.removeQueries({ queryKey: ["section", selectedRunId] });
    } catch (err) {
      setErrorMsg(err instanceof Error ? err.message : "Failed to start the run");
    } finally {
      setStarting(false);
    }
  };

  const handleActivate = async (id: string) => {
    await api.activateRun(id);
    onSelectRunId(id);
    queryClient.invalidateQueries({ queryKey: ["runs"] });
  };

  const handleDelete = async () => {
    if (!toDelete) return;
    try {
      await api.deleteRun(toDelete.id);
      if (selectedRunId === toDelete.id) onSelectRunId(null);
      queryClient.invalidateQueries({ queryKey: ["runs"] });
    } catch (err) {
      setErrorMsg(err instanceof Error ? err.message : "Delete failed");
    } finally {
      setToDelete(null);
    }
  };

  const activeStep = !currentRun ? 0 : currentRun.status === "draft" ? 1 : 2;
  const busy = isRunActive(currentRun);
  const schema = currentRun?.schema;

  return (
    <Box>
      <Typography variant="h1" sx={{ mb: 0.5 }}>
        Import the workbook and start a run
      </Typography>
      <Typography variant="body1" sx={{ color: PALETTE.inkSecondary, mb: 3, maxWidth: "75ch" }}>
        Upload the case-study workbook (<code>DataScienceMarketingUseCase.xlsx</code>), check the channel roles the app
        inferred, and start the pipeline: data audit, EDA, preparation, the OLS benchmark, the Google Meridian MMM, the
        optimiser and the forecast.
      </Typography>

      {errorMsg && (
        <Alert severity="error" sx={{ mb: 3 }} onClose={() => setErrorMsg(null)}>
          {errorMsg}
        </Alert>
      )}

      <Stepper activeStep={activeStep} alternativeLabel sx={{ mb: 4 }}>
        <Step>
          <StepLabel>Upload the workbook</StepLabel>
        </Step>
        <Step>
          <StepLabel>Confirm roles and sampling</StepLabel>
        </Step>
        <Step>
          <StepLabel>Pipeline progress</StepLabel>
        </Step>
      </Stepper>

      <Grid container spacing={3}>
        <Grid item xs={12} md={5}>
          <Paper sx={{ p: 3, mb: 3 }}>
            <Typography variant="h3" component="h2" sx={{ mb: 2 }}>
              New upload
            </Typography>
            <Stack spacing={2}>
              <TextField size="small" label="Run name" value={runName} onChange={(e) => setRunName(e.target.value)} fullWidth />
              <Box
                component="label"
                htmlFor="workbook-input"
                onDragOver={(e) => {
                  e.preventDefault();
                  setDragOver(true);
                }}
                onDragLeave={() => setDragOver(false)}
                onDrop={(e) => {
                  e.preventDefault();
                  setDragOver(false);
                  const file = e.dataTransfer.files?.[0];
                  if (file) void uploadFile(file);
                }}
                sx={{
                  display: "flex",
                  flexDirection: "column",
                  alignItems: "center",
                  gap: 1,
                  py: 4,
                  px: 2,
                  textAlign: "center",
                  cursor: uploading ? "progress" : "pointer",
                  borderRadius: 2,
                  border: `2px dashed ${dragOver ? PALETTE.brandGold : PALETTE.borderStrong}`,
                  backgroundColor: dragOver ? PALETTE.bgGoldTint : PALETTE.bgSubtle,
                  transition: "background-color 0.15s, border-color 0.15s",
                  "&:has(input:focus-visible)": { outline: `3px solid ${PALETTE.brandPrimary}`, outlineOffset: 2 },
                }}
              >
                {uploading ? <CircularProgress size={28} color="secondary" /> : <CloudUploadOutlinedIcon sx={{ fontSize: 34, color: PALETTE.brandGold }} />}
                <Typography sx={{ fontWeight: 700 }}>{uploading ? "Profiling the workbook…" : "Drop the .xlsx here or browse"}</Typography>
                <Typography variant="caption" sx={{ color: PALETTE.inkSecondary }}>
                  Long format: date, channel, sales, costs (+ impressions, clicks). Max 10 MB.
                </Typography>
                <input
                  id="workbook-input"
                  ref={fileInput}
                  type="file"
                  accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                  className="visually-hidden"
                  disabled={uploading}
                  onChange={(e) => {
                    const file = e.target.files?.[0];
                    if (file) void uploadFile(file);
                  }}
                />
              </Box>
            </Stack>
          </Paper>

          <Paper sx={{ p: 3 }}>
            <Typography variant="h3" component="h2" sx={{ mb: 1.5 }}>
              Runs ({runsData?.runs?.length || 0})
            </Typography>
            <TableContainer role="region" aria-label="Runs" tabIndex={0}>
              <Table size="small">
                <TableHead>
                  <TableRow>
                    <TableCell scope="col">Run</TableCell>
                    <TableCell scope="col">Status</TableCell>
                    <TableCell scope="col" align="right">
                      Actions
                    </TableCell>
                  </TableRow>
                </TableHead>
                <TableBody>
                  {(runsData?.runs || []).map((r) => (
                    <TableRow key={r.id} selected={r.id === selectedRunId} hover>
                      <TableCell>
                        <Button
                          variant="text"
                          size="small"
                          onClick={() => onSelectRunId(r.id)}
                          aria-pressed={r.id === selectedRunId}
                          sx={{ p: 0, minHeight: 24, borderRadius: 1, justifyContent: "flex-start", textAlign: "left", color: PALETTE.inkPrimary }}
                        >
                          <Box>
                            <Typography variant="body2" sx={{ fontWeight: 700, color: PALETTE.inkPrimary }}>
                              {r.name}
                            </Typography>
                            <Typography variant="caption" sx={{ color: PALETTE.inkMuted }}>
                              {r.profile} · {r.created_at ? new Date(r.created_at).toLocaleString("en-GB", { dateStyle: "medium", timeStyle: "short" }) : r.id}
                            </Typography>
                          </Box>
                        </Button>
                      </TableCell>
                      <TableCell>
                        <Chip size="small" label={STATUS_LABEL[r.status] ?? r.status} color={r.status === "succeeded" ? "success" : r.status === "failed" ? "error" : isRunActive(r) ? "info" : "default"} />
                      </TableCell>
                      <TableCell align="right" sx={{ whiteSpace: "nowrap" }}>
                        <Tooltip title={r.is_active ? "Default run" : "Make this the default run"}>
                          <IconButton size="small" aria-label={r.is_active ? `${r.name} is the default run` : `Make ${r.name} the default run`} onClick={() => handleActivate(r.id)}>
                            {r.is_active ? <StarIcon fontSize="small" sx={{ color: PALETTE.brandGold }} /> : <StarOutlineIcon fontSize="small" />}
                          </IconButton>
                        </Tooltip>
                        <Tooltip title="Delete run">
                          <IconButton size="small" color="error" aria-label={`Delete ${r.name}`} onClick={() => setToDelete(r)}>
                            <DeleteOutlineIcon fontSize="small" />
                          </IconButton>
                        </Tooltip>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </TableContainer>
          </Paper>
        </Grid>

        <Grid item xs={12} md={7}>
          {!currentRun && (
            <Paper sx={{ p: 4, textAlign: "center", borderStyle: "dashed", backgroundColor: PALETTE.bgSubtle }}>
              <Typography variant="h3" component="h2">
                No run selected
              </Typography>
              <Typography variant="body2" sx={{ mt: 1 }}>
                Upload the workbook to create a draft run, then confirm the channel roles here.
              </Typography>
            </Paper>
          )}

          {schema && roleConfigDraft && (
            <Paper sx={{ p: 3, mb: 3 }}>
              <Typography variant="h3" component="h2">
                {currentRun.status === "draft" ? "Confirm data roles" : "Run settings"} — {currentRun.name}
              </Typography>
              <Typography variant="body2" sx={{ mb: 2 }}>
                {(schema.n_rows || schema.raw_row_count || 0).toLocaleString("en-GB")} rows · {schema.date_min || schema.start_date} →{" "}
                {schema.date_max || schema.end_date}
                {schema.calendar_days ? ` (${schema.calendar_days} days)` : ""} · sheets: {(schema.sheet_names || []).join(", ")}
              </Typography>

              {schema.warnings?.map((w, idx) => (
                <Alert key={idx} severity="info" sx={{ mb: 1.5 }}>
                  {w}
                </Alert>
              ))}

              <Grid container spacing={2} sx={{ mb: 2.5, mt: 0 }}>
                <Grid item xs={12} sm={7}>
                  <FormControl fullWidth size="small">
                    <InputLabel id="profile-label">Sampling profile</InputLabel>
                    <Select labelId="profile-label" label="Sampling profile" value={profile} onChange={(e) => setProfile(e.target.value as Profile)} disabled={busy}>
                      {(Object.keys(PROFILES) as Profile[]).map((p) => (
                        <MenuItem key={p} value={p}>
                          {PROFILES[p].label}
                        </MenuItem>
                      ))}
                    </Select>
                    <FormHelperText>{PROFILES[profile].detail}</FormHelperText>
                  </FormControl>
                </Grid>
                <Grid item xs={12} sm={5}>
                  <FormControlLabel
                    control={<Switch checked={skipMeridian} onChange={(e) => setSkipMeridian(e.target.checked)} disabled={busy} />}
                    label="EDA and OLS only (skip Meridian)"
                  />
                </Grid>
              </Grid>

              <TableContainer sx={{ maxHeight: 380, mb: 2.5, border: `1px solid ${PALETTE.borderSubtle}`, borderRadius: 1.5 }} role="region" aria-label="Channel roles" tabIndex={0}>
                <Table size="small" stickyHeader>
                  <TableHead>
                    <TableRow>
                      <TableCell scope="col">Channel</TableCell>
                      <TableCell scope="col">Role</TableCell>
                      <TableCell scope="col">Cost column</TableCell>
                    </TableRow>
                  </TableHead>
                  <TableBody>
                    {roleConfigDraft.channels.map((ch, idx) => (
                      <TableRow key={ch.name}>
                        <TableCell component="th" scope="row" sx={{ fontWeight: 700 }}>
                          {ch.name}
                        </TableCell>
                        <TableCell>
                          <Select
                            size="small"
                            value={ch.role}
                            disabled={busy}
                            inputProps={{ "aria-label": `Role of ${ch.name}` }}
                            onChange={(e) => handleRoleChange(idx, e.target.value as ChannelRole)}
                            sx={{ fontSize: "0.82rem", minWidth: 160 }}
                          >
                            <MenuItem value="paid">Paid media (gets an ROI)</MenuItem>
                            <MenuItem value="print_event">Print event (catalog)</MenuItem>
                            <MenuItem value="commission">Commission / control</MenuItem>
                            <MenuItem value="unpaid">Unpaid / organic</MenuItem>
                            <MenuItem value="ignored">Ignored</MenuItem>
                          </Select>
                          {/* The rationale sits under its role: a separate column was squeezed to one word per line */}
                          {ch.rationale && (
                            <Typography variant="caption" component="p" sx={{ color: PALETTE.inkSecondary, mt: 0.75, maxWidth: 420 }}>
                              {ch.rationale}
                            </Typography>
                          )}
                        </TableCell>
                        <TableCell sx={{ fontFamily: FONT_MONO, fontSize: "0.78rem", verticalAlign: "top", pt: 1.75 }}>{ch.cost_column || "—"}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </TableContainer>

              <Stack direction={{ xs: "column", sm: "row" }} spacing={1.5} justifyContent="flex-end">
                {currentRun.status === "succeeded" && (
                  <Button variant="outlined" onClick={() => navigate("/overview")}>
                    Open the overview
                  </Button>
                )}
                <Button
                  variant="contained"
                  startIcon={starting ? <CircularProgress size={16} color="inherit" /> : currentRun.status === "draft" ? <PlayArrowIcon /> : <ReplayIcon />}
                  onClick={handleConfirmAndStart}
                  disabled={busy || starting}
                >
                  {currentRun.status === "draft" ? "Confirm roles and start" : "Re-run with these settings"}
                </Button>
              </Stack>
              {currentRun.status !== "draft" && !busy && (
                <Typography variant="caption" sx={{ display: "block", textAlign: "right", mt: 1, color: PALETTE.inkMuted }}>
                  A re-run replaces this run's results; the uploaded workbook is kept.
                </Typography>
              )}
            </Paper>
          )}

          {currentRun?.stages && currentRun.stages.length > 0 && (
            <Paper sx={{ p: 3 }}>
              <Typography variant="h3" component="h2" sx={{ mb: 2 }}>
                Pipeline stages · {STATUS_LABEL[currentRun.status] ?? currentRun.status}
              </Typography>
              {currentRun.error && (
                <Alert severity="error" sx={{ mb: 2 }}>
                  {currentRun.error}
                </Alert>
              )}
              <Box component="ol" sx={{ listStyle: "none", p: 0, m: 0, display: "flex", flexDirection: "column", gap: 0.75 }}>
                {currentRun.stages.map((st) => (
                  <Box
                    component="li"
                    key={st.key}
                    sx={{ display: "flex", alignItems: "flex-start", gap: 1.25, py: 0.75, px: 1.25, borderRadius: 1.5, backgroundColor: st.status === "running" ? PALETTE.bgGoldTint : "transparent" }}
                  >
                    <Box sx={{ mt: 0.25, width: 20, display: "flex", justifyContent: "center" }} aria-hidden="true">
                      {st.status === "succeeded" && <CheckCircleOutlineIcon fontSize="small" color="success" />}
                      {st.status === "running" && <CircularProgress size={16} color="secondary" />}
                      {st.status === "failed" && <ErrorOutlineIcon fontSize="small" color="error" />}
                      {(st.status === "skipped" || st.status === "cancelled") && <RemoveCircleOutlineIcon fontSize="small" sx={{ color: PALETTE.inkMuted }} />}
                      {st.status === "pending" && <Box sx={{ width: 10, height: 10, mt: 0.5, borderRadius: "50%", border: `2px solid ${PALETTE.borderStrong}` }} />}
                    </Box>
                    <Box sx={{ flexGrow: 1, minWidth: 0 }}>
                      <Typography variant="body2" sx={{ fontWeight: 600, color: PALETTE.inkPrimary }}>
                        {st.label}
                      </Typography>
                      {st.error && (
                        <Typography variant="caption" sx={{ color: st.status === "failed" ? PALETTE.semanticBad : PALETTE.inkMuted, display: "block", overflowWrap: "anywhere" }}>
                          {st.error}
                        </Typography>
                      )}
                    </Box>
                    <Chip size="small" variant="outlined" label={st.queue} sx={{ display: { xs: "none", sm: "inline-flex" } }} />
                    <Typography variant="caption" sx={{ fontWeight: 700, minWidth: 74, textAlign: "right", color: PALETTE.inkSecondary }}>
                      {st.status}
                    </Typography>
                  </Box>
                ))}
              </Box>
            </Paper>
          )}
        </Grid>
      </Grid>

      <Dialog open={Boolean(toDelete)} onClose={() => setToDelete(null)} maxWidth="xs" fullWidth>
        <DialogTitle>Delete “{toDelete?.name}”?</DialogTitle>
        <DialogContent>
          <DialogContentText>
            This removes the run, its uploaded workbook and every result, model and report it produced. It cannot be undone.
          </DialogContentText>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setToDelete(null)} autoFocus>
            Keep it
          </Button>
          <Button color="error" variant="contained" onClick={handleDelete}>
            Delete run
          </Button>
        </DialogActions>
      </Dialog>
    </Box>
  );
};
