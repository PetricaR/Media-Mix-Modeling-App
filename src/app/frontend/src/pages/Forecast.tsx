import React, { Suspense, useEffect, useMemo, useRef, useState } from "react";
import {
  Alert,
  Box,
  Button,
  Chip,
  CircularProgress,
  FormControl,
  Grid,
  InputLabel,
  LinearProgress,
  MenuItem,
  Paper,
  Select,
  Skeleton,
  Slider,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Typography,
} from "@mui/material";
import DownloadIcon from "@mui/icons-material/Download";
import UploadFileIcon from "@mui/icons-material/UploadFile";
import PlayArrowIcon from "@mui/icons-material/PlayArrow";
import RestartAltIcon from "@mui/icons-material/RestartAlt";
import LockOutlinedIcon from "@mui/icons-material/LockOutlined";
import { useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import { useForecast, useForecasts, useRun, useScenario, useSection } from "../api/hooks";
import type { ScenarioResult } from "../api/types";
import { SectionRenderer } from "../components/Sections/SectionRenderer";
import { FONT_MONO, PALETTE } from "../theme";

const LazyPlotlyChart = React.lazy(() => import("../components/Charts/PlotlyChart"));

const eur = (v: number) => `€${Math.round(v).toLocaleString("en-GB")}`;
const signedEur = (v: number) => `${v >= 0 ? "+" : "−"}€${Math.abs(Math.round(v)).toLocaleString("en-GB")}`;

const Money: React.FC<{ label: string; value: string; sub?: string }> = ({ label, value, sub }) => (
  <Box>
    <Typography variant="caption" sx={{ fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.04em", color: PALETTE.inkSecondary }}>
      {label}
    </Typography>
    <Typography sx={{ fontFamily: FONT_MONO, fontWeight: 600, fontSize: "1.15rem" }}>{value}</Typography>
    {sub && (
      <Typography variant="caption" sx={{ color: PALETTE.inkSecondary }}>
        {sub}
      </Typography>
    )}
  </Box>
);

const ResultChart: React.FC<{ result: ScenarioResult; title: string }> = ({ result, title }) =>
  result.figure ? (
    <Suspense fallback={<Skeleton variant="rounded" height={340} />}>
      <LazyPlotlyChart data={result.figure.data} layout={result.figure.layout} height={340} ariaLabel={title} />
    </Suspense>
  ) : null;

export const ForecastPage: React.FC<{ runId: string | null }> = ({ runId }) => {
  const queryClient = useQueryClient();
  const { data: section, isLoading, error } = useSection(runId, "forecast_scenarios");
  const { data: run } = useRun(runId);
  const { data: forecastsData } = useForecasts(runId);
  const forecasts = useMemo(() => forecastsData?.forecasts ?? [], [forecastsData]);
  const planInput = useRef<HTMLInputElement>(null);

  const [forecastId, setForecastId] = useState<string | null>(null);
  const [multipliers, setMultipliers] = useState<Record<string, number>>({});
  const [horizonDays, setHorizonDays] = useState<number>(42);
  const [activeScenarioId, setActiveScenarioId] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  // Default to the newest finished forecast (the example plan until a custom plan has been forecast)
  useEffect(() => {
    if (forecastId && forecasts.some((f) => f.id === forecastId)) return;
    const done = forecasts.find((f) => f.status === "succeeded");
    setForecastId(done?.id ?? forecasts[0]?.id ?? null);
  }, [forecasts, forecastId]);

  useEffect(() => {
    setActiveScenarioId(null);
    setMultipliers({});
  }, [forecastId, runId]);

  const { data: forecast } = useForecast(forecastId);
  const { data: scenario } = useScenario(activeScenarioId);
  const result = forecast?.result ?? null;
  const horizonMax = result?.horizon_days ?? 42;

  useEffect(() => setHorizonDays(horizonMax), [horizonMax]);

  const lockedSlugs = useMemo(() => {
    const channels = run?.config?.role_config?.channels ?? [];
    return new Set(
      channels.filter((c) => c.role === "commission" || c.role === "control" || c.slug === "affiliate" || c.name === "Affiliate").map((c) => c.slug ?? c.name)
    );
  }, [run]);

  const levers = (result?.channel_summary ?? []).filter((c) => !lockedSlugs.has(c.slug));
  const locked = (result?.channel_summary ?? []).filter((c) => lockedSlugs.has(c.slug));

  const handleEvaluateScenario = async () => {
    if (!forecastId) return;
    setSubmitting(true);
    setErrorMsg(null);
    try {
      const resp = await api.createScenario(forecastId, {
        name: `What-if over ${horizonDays} days`,
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

  const handleUploadPlanCsv = async (file: File) => {
    if (!runId) return;
    setUploading(true);
    setErrorMsg(null);
    try {
      const created = await api.uploadForecastPlan(runId, file, file.name.replace(/\.csv$/i, ""));
      await queryClient.invalidateQueries({ queryKey: ["forecasts", runId] });
      setForecastId(created.id);
    } catch (err) {
      setErrorMsg(err instanceof Error ? err.message : "Plan upload failed");
    } finally {
      setUploading(false);
      if (planInput.current) planInput.current.value = "";
    }
  };

  const scenarioBusy = submitting || scenario?.status === "queued" || scenario?.status === "running";
  const forecastBusy = forecast?.status === "queued" || forecast?.status === "running";
  const changed = Object.values(multipliers).some((v) => Math.abs(v - 1) > 1e-9);

  return (
    <Box>
      <Paper component="section" aria-labelledby="planner-title" sx={{ p: { xs: 2, md: 3 }, mb: 4, borderTop: `3px solid ${PALETTE.brandGold}` }}>
        <Stack direction={{ xs: "column", md: "row" }} justifyContent="space-between" alignItems={{ xs: "flex-start", md: "flex-start" }} spacing={2} sx={{ mb: 2.5 }}>
          <Box sx={{ maxWidth: "70ch" }}>
            <Typography id="planner-title" variant="h2">
              Budget plan forecast and what-if scenarios
            </Typography>
            <Typography variant="body2" sx={{ mt: 0.5 }}>
              Pick a forecast (the example plan, or upload your own daily plan as CSV), then scale channel budgets to see the
              effect on forecast sales. Scenarios reuse the fitted model, so they need no refit.
            </Typography>
          </Box>
          {runId && (
            <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap>
              <Button size="small" variant="outlined" startIcon={<DownloadIcon fontSize="small" />} href={`/api/v1/runs/${runId}/plan-template?days=42`}>
                Plan template (CSV)
              </Button>
              <Button size="small" variant="contained" color="secondary" component="label" startIcon={uploading ? <CircularProgress size={14} color="inherit" /> : <UploadFileIcon fontSize="small" />} disabled={uploading}>
                {uploading ? "Uploading…" : "Upload a plan"}
                <input
                  ref={planInput}
                  type="file"
                  hidden
                  accept=".csv,text/csv"
                  onChange={(e) => {
                    const f = e.target.files?.[0];
                    if (f) void handleUploadPlanCsv(f);
                  }}
                />
              </Button>
            </Stack>
          )}
        </Stack>

        {errorMsg && (
          <Alert severity="error" sx={{ mb: 2 }} onClose={() => setErrorMsg(null)}>
            {errorMsg}
          </Alert>
        )}

        {forecasts.length === 0 ? (
          <Alert severity="info">
            No forecast yet. The example-plan forecast appears when the Meridian stages of this run have finished; you can
            also upload your own plan (the day after the history onwards, one cost column per paid channel).
          </Alert>
        ) : (
          <>
            <FormControl size="small" sx={{ width: { xs: "100%", sm: 420 }, maxWidth: "100%", mb: 2 }}>
              <InputLabel id="forecast-select-label">Forecast</InputLabel>
              <Select labelId="forecast-select-label" label="Forecast" value={forecastId ?? ""} onChange={(e) => setForecastId(e.target.value as string)}>
                {forecasts.map((f) => (
                  <MenuItem key={f.id} value={f.id}>
                    <Stack direction="row" spacing={1} alignItems="center" sx={{ width: "100%", minWidth: 0 }}>
                      <Box sx={{ flexGrow: 1, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{f.name}</Box>
                      <Chip size="small" label={f.status} color={f.status === "succeeded" ? "success" : f.status === "failed" ? "error" : "info"} />
                    </Stack>
                  </MenuItem>
                ))}
              </Select>
            </FormControl>

            {forecastBusy && (
              <Box sx={{ mb: 2 }}>
                <Typography variant="body2" sx={{ mb: 1 }}>
                  Refitting Meridian on the history plus your plan window
                  {typeof forecast?.queue_position === "number" && forecast.queue_position > 0 ? ` (queue position ${forecast.queue_position})` : ""}…
                </Typography>
                <LinearProgress color="secondary" aria-label="Forecast refit in progress" />
              </Box>
            )}
            {forecast?.status === "failed" && (
              <Alert severity="error" sx={{ mb: 2 }}>
                {forecast.error || "The forecast failed."}
              </Alert>
            )}

            {result && (
              <>
                <Box sx={{ display: "grid", gap: 2, gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))", mb: 2 }}>
                  <Money label="Forecast sales" value={eur(result.scenario_total_sales)} sub={`${result.horizon_days} days · ${result.start_date} → ${result.end_date}`} />
                  <Money label="Planned spend" value={eur(result.scenario_total_spend)} />
                  {result.prediction_lo_total !== undefined && result.prediction_hi_total !== undefined && (
                    <Money label="Sum of daily 90 % bounds" value={`${eur(result.prediction_lo_total)} – ${eur(result.prediction_hi_total)}`} sub="Conservative (bounds add up)" />
                  )}
                </Box>
                <ResultChart result={result} title={forecast?.name ?? "Forecast"} />
              </>
            )}
          </>
        )}

        {result && (
          <Box sx={{ mt: 3, pt: 2.5, borderTop: `1px solid ${PALETTE.borderSubtle}` }}>
            <Stack direction={{ xs: "column", sm: "row" }} justifyContent="space-between" alignItems={{ xs: "flex-start", sm: "center" }} spacing={1} sx={{ mb: 2 }}>
              <Typography variant="h3" component="h3">
                What-if: scale channel budgets
              </Typography>
              <Button size="small" startIcon={<RestartAltIcon fontSize="small" />} onClick={() => setMultipliers({})} disabled={!changed}>
                Reset to plan
              </Button>
            </Stack>
            <Grid container spacing={2.5} alignItems="flex-start">
              {levers.map((ch) => {
                const val = multipliers[ch.slug] ?? 1.0;
                return (
                  <Grid item xs={12} sm={6} md={4} key={ch.slug}>
                    <Stack direction="row" justifyContent="space-between">
                      <Typography id={`m-${ch.slug}`} variant="caption" sx={{ fontWeight: 700 }}>
                        {ch.channel}
                      </Typography>
                      <Typography variant="caption" sx={{ fontFamily: FONT_MONO, fontWeight: 600 }}>
                        {val.toFixed(2)}× · {eur(ch.base_spend * val)}
                      </Typography>
                    </Stack>
                    <Slider
                      size="small"
                      color="secondary"
                      min={0}
                      max={2}
                      step={0.05}
                      value={val}
                      aria-labelledby={`m-${ch.slug}`}
                      getAriaValueText={(v) => `${v.toFixed(2)} times the planned budget`}
                      marks={[{ value: 1 }]}
                      onChange={(_, v) => setMultipliers((prev) => ({ ...prev, [ch.slug]: v as number }))}
                    />
                  </Grid>
                );
              })}
              {locked.map((ch) => (
                <Grid item xs={12} sm={6} md={4} key={ch.slug}>
                  <Stack direction="row" spacing={0.75} alignItems="center" sx={{ color: PALETTE.inkMuted }}>
                    <LockOutlinedIcon sx={{ fontSize: 15 }} aria-hidden="true" />
                    <Typography variant="caption" sx={{ fontWeight: 700 }}>
                      {ch.channel}: stays as planned
                    </Typography>
                  </Stack>
                  <Typography variant="caption" sx={{ color: PALETTE.inkMuted, display: "block", mt: 0.5 }}>
                    Its cost is a commission on sales, so it enters the model as a control, not as a budget lever.
                  </Typography>
                </Grid>
              ))}
              <Grid item xs={12} sm={6} md={4}>
                <FormControl size="small" fullWidth>
                  <InputLabel id="horizon-label">Horizon</InputLabel>
                  <Select labelId="horizon-label" label="Horizon" value={Math.min(horizonDays, horizonMax)} onChange={(e) => setHorizonDays(Number(e.target.value))}>
                    {[7, 14, 28, 42, 56, 90]
                      .filter((d) => d < horizonMax)
                      .concat(horizonMax)
                      .map((d) => (
                        <MenuItem key={d} value={d}>
                          First {d} days{d === horizonMax ? " (whole plan)" : ""}
                        </MenuItem>
                      ))}
                  </Select>
                </FormControl>
              </Grid>
              <Grid item xs={12} sm={6} md={4}>
                <Button
                  variant="contained"
                  fullWidth
                  startIcon={scenarioBusy ? <CircularProgress size={16} color="inherit" /> : <PlayArrowIcon />}
                  disabled={!runId || scenarioBusy || forecast?.status !== "succeeded"}
                  onClick={handleEvaluateScenario}
                >
                  {scenarioBusy ? "Evaluating…" : "Evaluate scenario"}
                </Button>
              </Grid>
            </Grid>

            <Box aria-live="polite">
              {scenario?.status === "failed" && (
                <Alert severity="error" sx={{ mt: 2 }}>
                  {scenario.error || "The scenario failed."}
                </Alert>
              )}
              {scenario?.result && scenario.status === "succeeded" && (
                <Box sx={{ mt: 3 }}>
                  <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap sx={{ mb: 2 }}>
                    <Chip color="primary" label={`Scenario sales ${eur(scenario.result.scenario_total_sales)}`} />
                    <Chip
                      color={scenario.result.delta_sales >= 0 ? "success" : "warning"}
                      label={`Δ sales ${signedEur(scenario.result.delta_sales)} (${scenario.result.delta_sales_pct >= 0 ? "+" : ""}${scenario.result.delta_sales_pct.toFixed(2)}%)`}
                    />
                    <Chip variant="outlined" label={`Δ spend ${signedEur(scenario.result.delta_spend)}`} />
                    {scenario.result.marginal_roas != null && <Chip color="info" label={`Δ sales / Δ spend ${scenario.result.marginal_roas.toFixed(2)}`} />}
                  </Stack>
                  {scenario.result.method && (
                    <Typography variant="caption" sx={{ display: "block", mb: 1, color: PALETTE.inkSecondary }}>
                      Method: {scenario.result.method}
                    </Typography>
                  )}
                  <ResultChart result={scenario.result} title="Scenario forecast" />
                  <TableContainer role="region" aria-label="Scenario spend by channel" tabIndex={0} sx={{ mt: 2, border: `1px solid ${PALETTE.borderSubtle}`, borderRadius: 1.5 }}>
                    <Table size="small">
                      <TableHead>
                        <TableRow>
                          <TableCell scope="col">Channel</TableCell>
                          <TableCell scope="col" align="right">
                            Multiplier
                          </TableCell>
                          <TableCell scope="col" align="right">
                            Plan spend
                          </TableCell>
                          <TableCell scope="col" align="right">
                            Scenario spend
                          </TableCell>
                          <TableCell scope="col" align="right">
                            Δ spend
                          </TableCell>
                        </TableRow>
                      </TableHead>
                      <TableBody>
                        {scenario.result.channel_summary.map((row) => (
                          <TableRow key={row.slug} hover>
                            <TableCell component="th" scope="row" sx={{ fontWeight: 600 }}>
                              {row.channel}
                            </TableCell>
                            <TableCell align="right" sx={{ fontFamily: FONT_MONO }}>
                              {row.multiplier.toFixed(2)}×
                            </TableCell>
                            <TableCell align="right" sx={{ fontFamily: FONT_MONO }}>
                              {eur(row.base_spend)}
                            </TableCell>
                            <TableCell align="right" sx={{ fontFamily: FONT_MONO }}>
                              {eur(row.scenario_spend)}
                            </TableCell>
                            <TableCell align="right" sx={{ fontFamily: FONT_MONO }}>
                              {signedEur(row.delta_spend)}
                            </TableCell>
                          </TableRow>
                        ))}
                      </TableBody>
                    </Table>
                  </TableContainer>
                </Box>
              )}
            </Box>
          </Box>
        )}
      </Paper>

      <SectionRenderer
        section={section}
        isLoading={isLoading}
        error={error as Error | null}
        pendingMessage="The back-test, the example-plan forecast and its scenarios appear when the Meridian forecast stage has finished."
      />
    </Box>
  );
};
