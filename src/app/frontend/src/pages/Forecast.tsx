import React, { useState } from "react";
import {
  Alert,
  Box,
  Button,
  Chip,
  CircularProgress,
  FormControl,
  Grid,
  MenuItem,
  Paper,
  Select,
  Slider,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Tooltip,
  Typography,
} from "@mui/material";
import DownloadIcon from "@mui/icons-material/Download";
import UploadFileIcon from "@mui/icons-material/UploadFile";
import PlayArrowIcon from "@mui/icons-material/PlayArrow";
import LockOutlinedIcon from "@mui/icons-material/LockOutlined";
import { api } from "../api/client";
import { useForecasts, useScenario, useSection } from "../api/hooks";
import { SectionRenderer } from "../components/Sections/SectionRenderer";
import { PALETTE } from "../theme";
import { useQueryClient } from "@tanstack/react-query";

const PAID_SLIDER_CHANNELS = [
  { key: "sea", label: "SEA (Paid Search)" },
  { key: "catalog", label: "Catalog (Print Drop)" },
  { key: "social_media", label: "Social Media" },
  { key: "psm", label: "Price Search Engines (PSM)" },
  { key: "display_network", label: "Display Network" },
  { key: "display", label: "Display Banners" },
];

export const ForecastPage: React.FC<{ runId: string | null }> = ({ runId }) => {
  const queryClient = useQueryClient();
  const { data: section, isLoading, error } = useSection(runId, "forecast_scenarios");
  const { data: forecastsData } = useForecasts(runId);

  const [multipliers, setMultipliers] = useState<Record<string, number>>({
    sea: 1.2,
    catalog: 1.0,
    social_media: 1.15,
    psm: 1.0,
    display_network: 0.85,
    display: 0.7,
  });
  const [horizonDays, setHorizonDays] = useState<number>(42);
  const [activeScenarioId, setActiveScenarioId] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  const { data: scenarioDetail } = useScenario(activeScenarioId);

  const defaultForecastId =
    forecastsData?.forecasts?.[0]?.id || (runId ? `default_${runId}` : null);

  const handleEvaluateScenario = async () => {
    if (!defaultForecastId) return;
    setSubmitting(true);
    setErrorMsg(null);
    try {
      const resp = await api.createScenario(defaultForecastId, {
        name: `Interactive ${horizonDays}d What-If`,
        multipliers,
        horizon_days: horizonDays,
      });
      setActiveScenarioId(resp.id);
    } catch (err) {
      setErrorMsg(err instanceof Error ? err.message : "Scenario evaluation failed");
    } finally {
      setSubmitting(false);
    }
  };

  const handleUploadPlanCsv = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file || !runId) return;
    setSubmitting(true);
    setErrorMsg(null);
    try {
      await api.uploadForecastPlan(runId, file, file.name);
      queryClient.invalidateQueries({ queryKey: ["forecasts", runId] });
    } catch (err) {
      setErrorMsg(err instanceof Error ? err.message : "Plan CSV upload failed");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Box>
      <Paper sx={{ p: 3, mb: 4, borderTop: `3px solid ${PALETTE.brandPrimary}` }}>
        <Stack
          direction={{ xs: "column", md: "row" }}
          justifyContent="space-between"
          alignItems={{ xs: "flex-start", md: "center" }}
          spacing={2}
          sx={{ mb: 2.5 }}
        >
          <Box>
            <Typography variant="h2">
              Interactive Budget Plan & What-If Scenario Workbench
            </Typography>
            <Typography variant="body2">
              Adjust channel spend multipliers to evaluate 2025 out-of-sample revenue response in
              sub-second time, or upload a custom daily budget plan CSV.
            </Typography>
          </Box>
          {runId && (
            <Stack direction="row" spacing={1.5}>
              <Button
                size="small"
                variant="outlined"
                startIcon={<DownloadIcon fontSize="small" />}
                href={`/api/v1/runs/${runId}/plan-template?days=42`}
                target="_blank"
              >
                42-Day Plan CSV Template
              </Button>
              <Button
                size="small"
                variant="outlined"
                component="label"
                startIcon={<UploadFileIcon fontSize="small" />}
              >
                Upload Custom Plan CSV
                <input type="file" hidden accept=".csv" onChange={handleUploadPlanCsv} />
              </Button>
            </Stack>
          )}
        </Stack>

        {errorMsg && (
          <Alert severity="error" sx={{ mb: 2 }} onClose={() => setErrorMsg(null)}>
            {errorMsg}
          </Alert>
        )}

        <Grid container spacing={2.5} alignItems="center">
          {PAID_SLIDER_CHANNELS.map((ch) => (
            <Grid item xs={12} sm={6} md={4} key={ch.key}>
              <Stack direction="row" justifyContent="space-between">
                <Typography variant="caption" sx={{ fontWeight: 600 }}>
                  {ch.label}
                </Typography>
                <Typography
                  variant="caption"
                  sx={{ fontFamily: '"JetBrains Mono", monospace', fontWeight: 600 }}
                >
                  {(multipliers[ch.key] ?? 1.0).toFixed(2)}×
                </Typography>
              </Stack>
              <Slider
                size="small"
                min={0.0}
                max={2.0}
                step={0.05}
                value={multipliers[ch.key] ?? 1.0}
                onChange={(_, val) =>
                  setMultipliers((prev) => ({ ...prev, [ch.key]: val as number }))
                }
              />
            </Grid>
          ))}

          <Grid item xs={12} sm={6} md={4}>
            <Tooltip title="Affiliate cost is a downstream sales commission (~6.5% per order) modeled as a control variable (`non_media_treatments`) rather than an actionable spend lever.">
              <Box sx={{ opacity: 0.65 }}>
                <Stack direction="row" justifyContent="space-between" alignItems="center">
                  <Stack direction="row" spacing={0.5} alignItems="center">
                    <LockOutlinedIcon sx={{ fontSize: 14 }} />
                    <Typography variant="caption" sx={{ fontWeight: 600 }}>
                      Affiliate (Commission Control)
                    </Typography>
                  </Stack>
                  <Typography
                    variant="caption"
                    sx={{ fontFamily: '"JetBrains Mono", monospace', fontWeight: 600 }}
                  >
                    1.00× (Locked)
                  </Typography>
                </Stack>
                <Slider size="small" value={1.0} min={0} max={2} disabled />
              </Box>
            </Tooltip>
          </Grid>

          <Grid item xs={12} sm={6} md={4}>
            <FormControl size="small" fullWidth>
              <Typography variant="caption" sx={{ fontWeight: 600, mb: 0.5 }}>
                Forecast Horizon
              </Typography>
              <Select
                value={horizonDays}
                onChange={(e) => setHorizonDays(Number(e.target.value))}
              >
                <MenuItem value={14}>14 Days (2 Weeks)</MenuItem>
                <MenuItem value={28}>28 Days (4 Weeks)</MenuItem>
                <MenuItem value={42}>42 Days (6 Weeks — Default Jan–Feb 2025)</MenuItem>
              </Select>
            </FormControl>
          </Grid>

          <Grid item xs={12} sm={12} md={4}>
            <Button
              variant="contained"
              fullWidth
              startIcon={
                submitting ||
                scenarioDetail?.status === "running" ||
                scenarioDetail?.status === "queued" ? (
                  <CircularProgress size={16} color="inherit" />
                ) : (
                  <PlayArrowIcon />
                )
              }
              disabled={!runId || submitting}
              onClick={handleEvaluateScenario}
              sx={{ mt: { md: 2 } }}
            >
              Evaluate What-If Scenario
            </Button>
          </Grid>
        </Grid>

        {scenarioDetail?.result && (
          <Box sx={{ mt: 3, pt: 2.5, borderTop: `1px solid ${PALETTE.borderSubtle}` }}>
            <Stack direction="row" spacing={1.5} flexWrap="wrap" sx={{ mb: 2 }}>
              <Chip
                size="small"
                color="primary"
                label={`Scenario Sales: €${scenarioDetail.result.scenario_total_sales.toLocaleString(
                  undefined,
                  { maximumFractionDigits: 0 }
                )}`}
              />
              <Chip
                size="small"
                color={scenarioDetail.result.delta_sales >= 0 ? "success" : "warning"}
                label={`Sales Δ: €${scenarioDetail.result.delta_sales.toLocaleString(undefined, {
                  maximumFractionDigits: 0,
                })} (${scenarioDetail.result.delta_sales_pct >= 0 ? "+" : ""}${scenarioDetail.result.delta_sales_pct.toFixed(
                  2
                )}%)`}
              />
              <Chip
                size="small"
                variant="outlined"
                label={`Spend Δ: €${scenarioDetail.result.delta_spend.toLocaleString(undefined, {
                  maximumFractionDigits: 0,
                })}`}
              />
              {scenarioDetail.result.marginal_roas != null && (
                <Chip
                  size="small"
                  color="info"
                  label={`Marginal ROAS: ${scenarioDetail.result.marginal_roas.toFixed(2)}×`}
                />
              )}
            </Stack>

            <TableContainer>
              <Table size="small">
                <TableHead>
                  <TableRow>
                    <TableCell>Channel</TableCell>
                    <TableCell align="right">Multiplier</TableCell>
                    <TableCell align="right">Baseline Plan Spend (€)</TableCell>
                    <TableCell align="right">Scenario Spend (€)</TableCell>
                    <TableCell align="right">Spend Δ (€)</TableCell>
                    <TableCell align="right">Incremental Sales Δ (€)</TableCell>
                  </TableRow>
                </TableHead>
                <TableBody>
                  {scenarioDetail.result.channel_summary.map((row, idx) => (
                    <TableRow key={idx} hover>
                      <TableCell sx={{ fontWeight: 600 }}>{String(row.channel)}</TableCell>
                      <TableCell align="right">{Number(row.multiplier).toFixed(2)}×</TableCell>
                      <TableCell align="right">
                        {Number(row.base_spend).toLocaleString(undefined, {
                          maximumFractionDigits: 0,
                        })}
                      </TableCell>
                      <TableCell align="right">
                        {Number(row.scenario_spend).toLocaleString(undefined, {
                          maximumFractionDigits: 0,
                        })}
                      </TableCell>
                      <TableCell align="right">
                        {Number(row.delta_spend).toLocaleString(undefined, {
                          maximumFractionDigits: 0,
                        })}
                      </TableCell>
                      <TableCell align="right">
                        {Number(row.delta_sales).toLocaleString(undefined, {
                          maximumFractionDigits: 0,
                        })}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </TableContainer>
          </Box>
        )}
      </Paper>

      <SectionRenderer section={section} isLoading={isLoading} error={error as Error | null} />
    </Box>
  );
};
