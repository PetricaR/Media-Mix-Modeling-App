import React, { useState } from "react";
import {
  Alert,
  Box,
  Button,
  Chip,
  CircularProgress,
  FormControl,
  FormControlLabel,
  Grid,
  IconButton,
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
import CheckCircleOutlineIcon from "@mui/icons-material/CheckCircleOutline";
import DeleteOutlineIcon from "@mui/icons-material/DeleteOutline";
import StarOutlineIcon from "@mui/icons-material/StarOutline";
import StarIcon from "@mui/icons-material/Star";
import { useNavigate } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import { useRun, useRuns } from "../api/hooks";
import type { ChannelConfig, ChannelRole, DataRoleConfig } from "../api/types";
import { PALETTE } from "../theme";

export const ImportPage: React.FC<{
  selectedRunId: string | null;
  onSelectRunId: (id: string | null) => void;
}> = ({ selectedRunId, onSelectRunId }) => {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { data: runsData } = useRuns();
  const { data: currentRun } = useRun(selectedRunId);

  const [runName, setRunName] = useState("Case Study Evaluation");
  const [profile, setProfile] = useState<"quick" | "full" | "smoke">("quick");
  const [skipMeridian, setSkipMeridian] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [roleConfigDraft, setRoleConfigDraft] = useState<DataRoleConfig | null>(null);

  React.useEffect(() => {
    if (currentRun?.config?.role_config) {
      setRoleConfigDraft(currentRun.config.role_config);
    } else if (currentRun?.schema?.role_config) {
      setRoleConfigDraft(currentRun.schema.role_config);
    }
    if (currentRun?.profile) {
      setProfile(currentRun.profile);
    }
  }, [currentRun?.id]);

  const handleFileChange = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    setUploading(true);
    setErrorMsg(null);
    try {
      const created = await api.uploadRun(file, runName || file.name, profile);
      onSelectRunId(created.id);
      if (created.config?.role_config) {
        setRoleConfigDraft(created.config.role_config);
      } else if (created.schema?.role_config) {
        setRoleConfigDraft(created.schema.role_config);
      }
      queryClient.invalidateQueries({ queryKey: ["runs"] });
    } catch (err) {
      setErrorMsg(err instanceof Error ? err.message : "Upload failed");
    } finally {
      setUploading(false);
    }
  };

  const handleRoleChange = (chIdx: number, newRole: ChannelRole) => {
    if (!roleConfigDraft) return;
    const updatedChannels: ChannelConfig[] = roleConfigDraft.channels.map((c, idx) =>
      idx === chIdx ? { ...c, role: newRole } : c
    );
    setRoleConfigDraft({ ...roleConfigDraft, channels: updatedChannels });
  };

  const handleConfirmAndStart = async () => {
    if (!selectedRunId) return;
    setErrorMsg(null);
    try {
      await api.updateRunConfig(selectedRunId, {
        name: runName || currentRun?.name,
        profile,
        role_config: roleConfigDraft || undefined,
        skip_meridian: skipMeridian,
      });
      await api.startRun(selectedRunId, {
        profile,
        skip_meridian: skipMeridian,
      });
      queryClient.invalidateQueries({ queryKey: ["run", selectedRunId] });
      queryClient.invalidateQueries({ queryKey: ["runs"] });
    } catch (err) {
      setErrorMsg(err instanceof Error ? err.message : "Failed to start run");
    }
  };

  const handleActivate = async (id: string) => {
    await api.activateRun(id);
    onSelectRunId(id);
    queryClient.invalidateQueries({ queryKey: ["runs"] });
  };

  const handleDelete = async (id: string) => {
    await api.deleteRun(id);
    if (selectedRunId === id) {
      onSelectRunId(null);
    }
    queryClient.invalidateQueries({ queryKey: ["runs"] });
  };

  const activeStep = !currentRun ? 0 : currentRun.status === "draft" ? 1 : 2;

  return (
    <Box>
      <Typography variant="h1" sx={{ mb: 0.5 }}>
        Import Workbook & Configure Run
      </Typography>
      <Typography variant="body1" sx={{ color: PALETTE.inkSecondary, mb: 3 }}>
        Upload the raw Excel workbook (`DataScienceMarketingUseCase.xlsx`), review inferred channel
        roles and structural break settings, and launch the automated analysis and Bayesian MMM
        pipeline.
      </Typography>

      {errorMsg && (
        <Alert severity="error" sx={{ mb: 3 }} onClose={() => setErrorMsg(null)}>
          {errorMsg}
        </Alert>
      )}

      <Stepper activeStep={activeStep} sx={{ mb: 4 }}>
        <Step>
          <StepLabel>1. Upload Raw `.xlsx` Workbook</StepLabel>
        </Step>
        <Step>
          <StepLabel>2. Confirm Data Roles & Sampling Profile</StepLabel>
        </Step>
        <Step>
          <StepLabel>3. Pipeline Execution & Stage Progress</StepLabel>
        </Step>
      </Stepper>

      <Grid container spacing={3}>
        <Grid item xs={12} md={5}>
          <Paper sx={{ p: 3, mb: 3 }}>
            <Typography variant="h3" sx={{ mb: 2 }}>
              New Workbook Upload
            </Typography>
            <Stack spacing={2}>
              <TextField
                size="small"
                label="Run Name"
                value={runName}
                onChange={(e) => setRunName(e.target.value)}
                fullWidth
              />
              <Button
                component="label"
                variant="outlined"
                startIcon={
                  uploading ? <CircularProgress size={18} /> : <CloudUploadOutlinedIcon />
                }
                disabled={uploading}
                sx={{
                  py: 3,
                  borderStyle: "dashed",
                  borderWidth: 2,
                  backgroundColor: PALETTE.bgSubtle,
                }}
              >
                {uploading ? "Profiling Workbook…" : "Select or Drop .xlsx File"}
                <input type="file" hidden accept=".xlsx,.xls" onChange={handleFileChange} />
              </Button>
            </Stack>
          </Paper>

          <Paper sx={{ p: 3 }}>
            <Typography variant="h3" sx={{ mb: 1.5 }}>
              Existing Runs ({runsData?.runs?.length || 0})
            </Typography>
            <TableContainer>
              <Table size="small">
                <TableHead>
                  <TableRow>
                    <TableCell>Run</TableCell>
                    <TableCell>Profile</TableCell>
                    <TableCell>Status</TableCell>
                    <TableCell align="right">Actions</TableCell>
                  </TableRow>
                </TableHead>
                <TableBody>
                  {(runsData?.runs || []).map((r) => (
                    <TableRow
                      key={r.id}
                      selected={r.id === selectedRunId}
                      hover
                      onClick={() => onSelectRunId(r.id)}
                      sx={{ cursor: "pointer" }}
                    >
                      <TableCell>
                        <Typography variant="body2" sx={{ fontWeight: 600 }}>
                          {r.name}
                        </Typography>
                        <Typography variant="caption" color="text.secondary">
                          {r.id}
                        </Typography>
                      </TableCell>
                      <TableCell>{r.profile}</TableCell>
                      <TableCell>
                        <Chip size="small" label={r.status} />
                      </TableCell>
                      <TableCell align="right" onClick={(e) => e.stopPropagation()}>
                        <Tooltip title="Set as Default Active Run">
                          <IconButton size="small" onClick={() => handleActivate(r.id)}>
                            {r.is_active ? (
                              <StarIcon fontSize="small" color="primary" />
                            ) : (
                              <StarOutlineIcon fontSize="small" />
                            )}
                          </IconButton>
                        </Tooltip>
                        <Tooltip title="Delete Run">
                          <IconButton size="small" color="error" onClick={() => handleDelete(r.id)}>
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
          {currentRun?.schema && roleConfigDraft && (
            <Paper sx={{ p: 3, mb: 3 }}>
              <Stack
                direction="row"
                justifyContent="space-between"
                alignItems="center"
                sx={{ mb: 2 }}
              >
                <Box>
                  <Typography variant="h3">
                    Confirm Data Roles — {currentRun.name}
                  </Typography>
                  <Typography variant="body2">
                    {currentRun.schema.n_rows.toLocaleString()} rows ·{" "}
                    {currentRun.schema.date_min} to {currentRun.schema.date_max} · Sheets:{" "}
                    {currentRun.schema.sheet_names.join(", ")}
                  </Typography>
                </Box>
                <Chip
                  size="small"
                  color="info"
                  label={`Break Year: ${roleConfigDraft.structural_break_year}+`}
                />
              </Stack>

              {currentRun.schema.warnings?.map((w, idx) => (
                <Alert key={idx} severity="warning" sx={{ mb: 2 }}>
                  {w}
                </Alert>
              ))}

              <Grid container spacing={2} sx={{ mb: 2.5 }}>
                <Grid item xs={12} sm={6}>
                  <FormControl fullWidth size="small">
                    <Typography variant="caption" sx={{ mb: 0.5, fontWeight: 600 }}>
                      MCMC Sampling Profile
                    </Typography>
                    <Select
                      value={profile}
                      onChange={(e) => setProfile(e.target.value as "quick" | "full" | "smoke")}
                    >
                      <MenuItem value="quick">
                        Quick (4 chains × 400 warmup + 400 draws, ~5–8 min CPU)
                      </MenuItem>
                      <MenuItem value="full">
                        Full (4 chains × 1000 warmup + 1000 draws, ~15–22 min CPU)
                      </MenuItem>
                      <MenuItem value="smoke">
                        Smoke (2 chains × 50 warmup + 50 draws, ~45 sec verification)
                      </MenuItem>
                    </Select>
                  </FormControl>
                </Grid>
                <Grid item xs={12} sm={6} sx={{ display: "flex", alignItems: "flex-end" }}>
                  <FormControlLabel
                    control={
                      <Switch
                        checked={skipMeridian}
                        onChange={(e) => setSkipMeridian(e.target.checked)}
                      />
                    }
                    label="Fast EDA & OLS Only (Skip Meridian MCMC)"
                  />
                </Grid>
              </Grid>

              <TableContainer sx={{ maxHeight: 360, mb: 2.5 }}>
                <Table size="small" stickyHeader>
                  <TableHead>
                    <TableRow>
                      <TableCell>Channel</TableCell>
                      <TableCell>Role Assignment</TableCell>
                      <TableCell>Cost Column</TableCell>
                      <TableCell>Exposure Column</TableCell>
                      <TableCell>Rationale</TableCell>
                    </TableRow>
                  </TableHead>
                  <TableBody>
                    {roleConfigDraft.channels.map((ch, idx) => (
                      <TableRow key={ch.name}>
                        <TableCell sx={{ fontWeight: 600 }}>{ch.name}</TableCell>
                        <TableCell>
                          <Select
                            size="small"
                            value={ch.role}
                            onChange={(e) =>
                              handleRoleChange(idx, e.target.value as ChannelRole)
                            }
                            sx={{ fontSize: "0.8rem", minWidth: 130 }}
                          >
                            <MenuItem value="paid">Paid Media (ROI)</MenuItem>
                            <MenuItem value="print_event">Print Event (Catalog)</MenuItem>
                            <MenuItem value="unpaid">Unpaid / Control</MenuItem>
                            <MenuItem value="ignored">Ignored (Inactive)</MenuItem>
                          </Select>
                        </TableCell>
                        <TableCell sx={{ fontFamily: '"JetBrains Mono", monospace' }}>
                          {ch.cost_column || "—"}
                        </TableCell>
                        <TableCell sx={{ fontFamily: '"JetBrains Mono", monospace' }}>
                          {ch.exposure_column || "—"}
                        </TableCell>
                        <TableCell>
                          <Typography variant="caption" color="text.secondary">
                            {ch.rationale || "—"}
                          </Typography>
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </TableContainer>

              <Stack direction="row" spacing={2} justifyContent="flex-end">
                {currentRun.status === "succeeded" && (
                  <Button variant="outlined" onClick={() => navigate("/overview")}>
                    Open Executive Overview
                  </Button>
                )}
                <Button
                  variant="contained"
                  startIcon={<PlayArrowIcon />}
                  onClick={handleConfirmAndStart}
                  disabled={currentRun.status === "running" || currentRun.status === "queued"}
                >
                  {currentRun.status === "draft" ? "Confirm Roles & Start Run" : "Re-Run Pipeline"}
                </Button>
              </Stack>
            </Paper>
          )}

          {currentRun?.stages && currentRun.stages.length > 0 && (
            <Paper sx={{ p: 3 }}>
              <Typography variant="h3" sx={{ mb: 2 }}>
                Pipeline Stage Progress ({currentRun.status.toUpperCase()})
              </Typography>
              <TableContainer>
                <Table size="small">
                  <TableHead>
                    <TableRow>
                      <TableCell>Stage</TableCell>
                      <TableCell>Queue</TableCell>
                      <TableCell>Status</TableCell>
                    </TableRow>
                  </TableHead>
                  <TableBody>
                    {currentRun.stages.map((st) => (
                      <TableRow key={st.key}>
                        <TableCell>
                          <Typography variant="body2" sx={{ fontWeight: 500 }}>
                            {st.label}
                          </Typography>
                          {st.error && (
                            <Typography variant="caption" color="error">
                              {st.error}
                            </Typography>
                          )}
                        </TableCell>
                        <TableCell>
                          <Chip size="small" variant="outlined" label={st.queue} />
                        </TableCell>
                        <TableCell>
                          <Stack direction="row" alignItems="center" spacing={1}>
                            {st.status === "succeeded" && (
                              <CheckCircleOutlineIcon fontSize="small" color="success" />
                            )}
                            {st.status === "running" && <CircularProgress size={14} />}
                            <Typography variant="caption" sx={{ fontWeight: 600 }}>
                              {st.status.toUpperCase()}
                            </Typography>
                          </Stack>
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </TableContainer>
            </Paper>
          )}
        </Grid>
      </Grid>
    </Box>
  );
};
