import React, { Suspense, useEffect, useMemo, useRef, useState } from "react";
import {
  Accordion,
  AccordionDetails,
  AccordionSummary,
  Alert,
  Box,
  Button,
  Checkbox,
  Chip,
  CircularProgress,
  FormControl,
  FormControlLabel,
  IconButton,
  InputLabel,
  LinearProgress,
  MenuItem,
  Paper,
  Select,
  Skeleton,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  ToggleButton,
  ToggleButtonGroup,
  Tooltip,
  Typography,
  useMediaQuery,
} from "@mui/material";
import DownloadIcon from "@mui/icons-material/Download";
import UploadFileIcon from "@mui/icons-material/UploadFile";
import TuneIcon from "@mui/icons-material/Tune";
import ExpandMoreIcon from "@mui/icons-material/ExpandMore";
import DeleteOutlineIcon from "@mui/icons-material/DeleteOutline";
import { useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import { useForecast, useForecasts, useRun, useRunHistory, useScenario, useScenarios, useSection } from "../api/hooks";
import type { RunHistory, ScenarioChannelRow, ScenarioDetail, ScenarioResult } from "../api/types";
import { ChannelEffects } from "../components/Forecast/ChannelEffects";
import { LAST_YEAR_SHIFT_DAYS, PLANNER_COLORS, plannerFigure, type PlannerLine } from "../components/Forecast/figures";
import { addDays, eur, eurCompact, multiplierLabel, shortDate, signedEur, signedPct, type BudgetInputMode, type SpendGranularity } from "../components/Forecast/format";
import { LeverPanel } from "../components/Forecast/LeverPanel";
import { PlanBuilderDialog } from "../components/Forecast/PlanBuilderDialog";
import { RUN_STATUS_LABEL, runStatusColor } from "../components/Import/format";
import { PageHeader } from "../components/Layout/PageHeader";
import { SectionRenderer } from "../components/Sections/SectionRenderer";
import { NUMERIC, PALETTE } from "../theme";

const LazyPlotlyChart = React.lazy(() => import("../components/Charts/PlotlyChart"));

const HISTORY_RANGES: { id: string; label: string; days: number | null }[] = [
  { id: "6w", label: "6 weeks", days: 42 },
  { id: "3m", label: "3 months", days: 91 },
  { id: "6m", label: "6 months", days: 182 },
  { id: "1y", label: "1 year", days: 365 },
  { id: "all", label: "All", days: null },
];

/** A scenario kept for comparison; its result is read back from the API by id. */
interface SavedScenario {
  id: string;
  name: string;
  multipliers: Record<string, number>;
  horizonDays: number;
  slot: number;
}

const MAX_SAVED = PLANNER_COLORS.saved.length;
const savedKey = (forecastId: string) => `mmm-studio:saved-scenarios:${forecastId}`;

function loadSaved(forecastId: string | null): SavedScenario[] {
  if (!forecastId) return [];
  try {
    const parsed: unknown = JSON.parse(window.localStorage.getItem(savedKey(forecastId)) ?? "[]");
    return Array.isArray(parsed)
      ? (parsed as SavedScenario[]).filter((s) => s && typeof s.id === "string" && typeof s.slot === "number").slice(0, MAX_SAVED)
      : [];
  } catch {
    return [];
  }
}

function storeSaved(forecastId: string, items: SavedScenario[]) {
  try {
    window.localStorage.setItem(savedKey(forecastId), JSON.stringify(items));
  } catch {
    // Storage can be unavailable (private mode, blocked site data): saving then lasts for this visit only
  }
}

/** Only the channels that move, rounded so 1.2 and 1.2000001 are one scenario. */
const changedEntries = (m: Record<string, number>) =>
  Object.entries(m)
    .filter(([, v]) => Math.abs(v - 1) > 1e-6)
    .map(([k, v]) => [k, Math.round(v * 1000) / 1000] as const)
    .sort(([a], [b]) => a.localeCompare(b));

const scenarioKey = (forecastId: string | null, m: Record<string, number>, horizon: number) => JSON.stringify([forecastId, changedEntries(m), horizon]);

const describe = (m: Record<string, number>, order: ScenarioChannelRow[]) => {
  const parts = order.filter((c) => Math.abs((m[c.slug] ?? 1) - 1) > 1e-6).map((c) => `${c.channel} ${multiplierLabel(m[c.slug])}`);
  if (!parts.length) return "As planned";
  return parts.length > 3 ? `${parts.slice(0, 3).join(", ")} +${parts.length - 3} more` : parts.join(", ");
};

const sumFirst = (values: number[], n: number) => values.slice(0, n).reduce((a, b) => a + b, 0);

/** Actual sales on the same weekdays one year before the first `n` forecast days; null when history does not cover them. */
function lastYearTotal(history: RunHistory | undefined, dates: string[]) {
  if (!history) return null;
  const byDate = new Map(history.dates.map((d, i) => [d, history.sales[i]]));
  let total = 0;
  for (const d of dates) {
    const v = byDate.get(addDays(d, -LAST_YEAR_SHIFT_DAYS));
    if (v == null) return null;
    total += v;
  }
  return total;
}

const Tile: React.FC<{ label: string; value: string; sub?: React.ReactNode; accent?: string; dim?: boolean }> = ({ label, value, sub, accent, dim }) => (
  <Box sx={{ pl: 1.5, borderLeft: `3px solid ${accent ?? PALETTE.borderSubtle}`, opacity: dim ? 0.55 : 1, transition: "opacity 0.2s", minWidth: 0 }}>
    <Typography variant="caption" component="p" sx={{ fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.04em", color: PALETTE.inkSecondary }}>
      {label}
    </Typography>
    <Typography sx={{ ...NUMERIC, fontWeight: 700, fontSize: "1.2rem", lineHeight: 1.35, letterSpacing: "-0.01em" }}>{value}</Typography>
    {sub && (
      <Typography variant="caption" component="p" sx={{ color: PALETTE.inkSecondary }}>
        {sub}
      </Typography>
    )}
  </Box>
);

interface DailyTableRow {
  date: string;
  actual: number | null;
  lastYear: number | null;
  plan: number | null;
  lo: number | null;
  hi: number | null;
  scenario: number | null;
  spend: number | null;
  scenarioSpend: number | null;
}

function dailyTableRows(history: RunHistory | undefined, plan: ScenarioResult, scenario: ScenarioResult | null, historyDays: number | null): DailyTableRow[] {
  const fcStart = plan.daily[0].date;
  const from = historyDays == null ? null : addDays(fcStart, -historyDays);
  const salesByDate = new Map((history?.dates ?? []).map((d, i) => [d, history!.sales[i]]));
  const lastYear = (d: string) => salesByDate.get(addDays(d, -LAST_YEAR_SHIFT_DAYS)) ?? null;
  const rows: DailyTableRow[] = [];
  history?.dates.forEach((d, i) => {
    if ((from == null || d >= from) && d < fcStart) {
      const spend = Object.values(history.spend).reduce<number>((acc, arr) => acc + (arr[i] ?? 0), 0);
      rows.push({ date: d, actual: history.sales[i], lastYear: lastYear(d), plan: null, lo: null, hi: null, scenario: null, spend, scenarioSpend: null });
    }
  });
  const scen = new Map((scenario?.daily ?? []).map((r) => [r.date, r]));
  for (const r of plan.daily) {
    const s = scen.get(r.date);
    rows.push({
      date: r.date,
      actual: null,
      lastYear: lastYear(r.date),
      plan: r.scenario_forecast,
      lo: r.prediction_lo,
      hi: r.prediction_hi,
      scenario: s ? s.scenario_forecast : null,
      spend: r.planned_spend,
      scenarioSpend: s ? s.planned_spend : null,
    });
  }
  return rows;
}

function downloadCsv(rows: DailyTableRow[], filename: string) {
  const header = "date,actual_sales,same_weekday_last_year,plan_forecast,plan_pi90_low,plan_pi90_high,scenario_forecast,spend,scenario_spend";
  const cell = (v: number | null) => (v == null ? "" : String(Math.round(v)));
  const body = rows.map((r) => [r.date, cell(r.actual), cell(r.lastYear), cell(r.plan), cell(r.lo), cell(r.hi), cell(r.scenario), cell(r.spend), cell(r.scenarioSpend)].join(","));
  const url = URL.createObjectURL(new Blob([[header, ...body].join("\n")], { type: "text/csv;charset=utf-8" }));
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

export const ForecastPage: React.FC<{ runId: string | null }> = ({ runId }) => {
  const queryClient = useQueryClient();
  const { data: section, isLoading, error } = useSection(runId, "forecast_scenarios");
  const { data: run } = useRun(runId);
  const { data: history } = useRunHistory(runId);
  const { data: forecastsData } = useForecasts(runId);
  const forecasts = useMemo(() => forecastsData?.forecasts ?? [], [forecastsData]);
  const planInput = useRef<HTMLInputElement>(null);

  const [forecastId, setForecastId] = useState<string | null>(null);
  const [multipliers, setMultipliers] = useState<Record<string, number>>({});
  const [horizonDays, setHorizonDays] = useState<number>(42);
  const [inputMode, setInputMode] = useState<BudgetInputMode>("pct");
  const [spendGranularity, setSpendGranularity] = useState<SpendGranularity>("period");
  const [builderOpen, setBuilderOpen] = useState(false);
  const [historyRange, setHistoryRange] = useState("3m");
  const [showLastYear, setShowLastYear] = useState(false);
  const [scenarioIds, setScenarioIds] = useState<Record<string, string>>({});
  const [shown, setShown] = useState<ScenarioDetail | null>(null);
  const [saved, setSaved] = useState<SavedScenario[]>([]);
  const [retryToken, setRetryToken] = useState(0);
  const [uploading, setUploading] = useState(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [announcement, setAnnouncement] = useState("");
  const pending = useRef(new Set<string>());
  const compact = useMediaQuery("(max-width: 600px)", { noSsr: true });

  // Default to the newest finished forecast (the example plan until a custom plan has been forecast)
  useEffect(() => {
    if (forecastId && forecasts.some((f) => f.id === forecastId)) return;
    const done = forecasts.find((f) => f.status === "succeeded");
    setForecastId(done?.id ?? forecasts[0]?.id ?? null);
  }, [forecasts, forecastId]);

  useEffect(() => {
    setMultipliers({});
    setShown(null);
    setSaved(loadSaved(forecastId));
  }, [forecastId, runId]);

  const { data: forecast } = useForecast(forecastId);
  const result = forecast?.result ?? null;
  const horizonMax = result?.horizon_days ?? 42;
  useEffect(() => setHorizonDays(horizonMax), [horizonMax]);

  const lockedSlugs = useMemo(() => {
    const channels = run?.config?.role_config?.channels ?? [];
    return new Set(
      channels.filter((c) => c.role === "commission" || c.role === "control").map((c) => c.slug ?? c.name)
    );
  }, [run]);
  const channelRows = useMemo(() => result?.channel_summary ?? [], [result]);
  const levers = useMemo(() => channelRows.filter((c) => !lockedSlugs.has(c.slug)), [channelRows, lockedSlugs]);
  const locked = useMemo(() => channelRows.filter((c) => lockedSlugs.has(c.slug)), [channelRows, lockedSlugs]);
  const channelNames = useMemo(() => {
    const fromRun = Object.fromEntries(
      (run?.config?.role_config?.channels ?? []).map((c) => [c.slug ?? c.name, c.name])
    );
    const fromResult = Object.fromEntries(channelRows.map((c) => [c.slug, c.channel]));
    return { ...fromRun, ...fromResult };
  }, [run, channelRows]);

  // --- What-if: every lever change is evaluated on its own, a moment after the last change --------------------
  const horizon = Math.min(horizonDays, horizonMax);
  const key = scenarioKey(forecastId, multipliers, horizon);
  const changed = changedEntries(multipliers).length > 0;
  const forecastReady = forecast?.status === "succeeded" && Boolean(result);

  useEffect(() => {
    if (!forecastId || !forecastReady || !changed || scenarioIds[key] || pending.current.has(key)) return;
    const body = { name: describe(multipliers, levers), multipliers: Object.fromEntries(changedEntries(multipliers)), horizon_days: horizon };
    const timer = window.setTimeout(() => {
      pending.current.add(key);
      setErrorMsg(null);
      api
        .createScenario(forecastId, body)
        .then((resp) => setScenarioIds((prev) => ({ ...prev, [key]: resp.id })))
        .catch((err) => setErrorMsg(err instanceof Error ? err.message : "The scenario could not be started."))
        .finally(() => pending.current.delete(key));
    }, 450);
    return () => window.clearTimeout(timer);
  }, [key, forecastId, forecastReady, changed, retryToken]); // multipliers, levers and horizon are all part of `key`

  const currentId = changed ? scenarioIds[key] ?? null : null;
  const { data: current } = useScenario(currentId);
  useEffect(() => {
    if (current?.status === "succeeded" && current.result) setShown(current);
  }, [current]);
  useEffect(() => {
    if (!changed) setShown(null);
  }, [changed]);

  const shownResult = changed ? shown?.result ?? null : null;
  const stale = changed && (!current || current.id !== shown?.id);
  const evaluating = changed && (!currentId || current?.status === "queued" || current?.status === "running");
  const failed = changed && current?.status === "failed";

  useEffect(() => {
    const r = shown?.result;
    if (!r) return;
    setAnnouncement(
      `Scenario updated: forecast sales ${eur(r.scenario_total_sales)}, ${signedEur(r.delta_sales)} (${signedPct(r.delta_sales_pct, 2)}) versus the plan over ${r.horizon_days} days.`
    );
  }, [shown?.id, shown?.result]);

  // --- Saved scenarios -------------------------------------------------------------------------------------------
  const savedQueries = useScenarios(saved.map((s) => s.id));
  const savedDetails = saved.map((s, i) => ({ entry: s, detail: savedQueries[i]?.data }));
  const persistSaved = (items: SavedScenario[]) => {
    setSaved(items);
    if (forecastId) storeSaved(forecastId, items);
  };
  const shownIsSaved = Boolean(shown && saved.some((s) => s.id === shown.id));
  const canSave = Boolean(shownResult && !stale && !shownIsSaved && saved.length < MAX_SAVED);
  const saveHint = !changed
    ? "Move a lever to build a scenario you can save and compare."
    : shownIsSaved
      ? "This scenario is saved."
      : saved.length >= MAX_SAVED
        ? `${MAX_SAVED} scenarios are saved: remove one to save another.`
        : undefined;
  const handleSave = () => {
    if (!shown || !canSave) return;
    const used = new Set(saved.map((s) => s.slot));
    const slot = [...Array(MAX_SAVED).keys()].find((i) => !used.has(i)) ?? 0;
    persistSaved([...saved, { id: shown.id, name: describe(multipliers, levers), multipliers: Object.fromEntries(changedEntries(multipliers)), horizonDays: horizon, slot }]);
  };
  const handleLoad = (s: SavedScenario) => {
    setScenarioIds((prev) => ({ ...prev, [scenarioKey(forecastId, s.multipliers, s.horizonDays)]: s.id }));
    setHorizonDays(s.horizonDays);
    setMultipliers(s.multipliers);
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

  // --- Chart and totals ------------------------------------------------------------------------------------------
  const historyDays = HISTORY_RANGES.find((r) => r.id === historyRange)?.days ?? null;
  const figure = useMemo(() => {
    if (!result) return null;
    const lines: PlannerLine[] = savedDetails
      .filter(({ detail }) => detail?.status === "succeeded" && detail.result && detail.id !== shown?.id)
      .map(({ entry, detail }) => ({ name: `Saved: ${entry.name}`, result: detail!.result!, color: PLANNER_COLORS.saved[entry.slot % MAX_SAVED], dash: "dash" as const }));
    if (shownResult) lines.push({ name: `Scenario: ${describe(multipliers, levers)}`, result: shownResult, color: PLANNER_COLORS.scenario, dash: "solid", dimmed: stale, primary: true });
    return plannerFigure({ history: history ?? null, plan: result, historyDays, showLastYear, lines, channelNames, compact });
    // useQueries returns a new array every render, so the saved results are tracked by their update times
  }, [result, history, historyDays, showLastYear, shownResult, stale, channelNames, compact, savedQueries.map((q) => q.dataUpdatedAt).join(","), saved]);

  const planDates = result ? result.daily.slice(0, horizon).map((r) => r.date) : [];
  const planSales = result ? sumFirst(result.daily.map((r) => r.scenario_forecast), horizon) : 0;
  const planSpend = result ? sumFirst(result.daily.map((r) => r.planned_spend), horizon) : 0;
  const lastYear = lastYearTotal(history, planDates);
  const tableRows = useMemo(() => (result ? dailyTableRows(history, result, shownResult, historyDays) : []), [history, result, shownResult, historyDays]);

  const scenarioBusy = forecast?.status === "queued" || forecast?.status === "running";

  return (
    <Box>
      <PageHeader
        title="Forecast planner"
        description="Set up your forecast period, total media budget, and channel allocations in either Percentage (%) or Fixed amount (€) mode. The fitted Meridian model evaluates scenario updates in place, or you can configure a custom plan up to 90 days."
        actions={
          runId ? (
            <>
              <Button
                size="small"
                variant="contained"
                color="secondary"
                startIcon={<TuneIcon fontSize="small" />}
                onClick={() => setBuilderOpen(true)}
              >
                Configure new plan
              </Button>
              <Button size="small" variant="outlined" startIcon={<DownloadIcon fontSize="small" />} href={`/api/v1/runs/${runId}/plan-template?days=42`}>
                Plan template (CSV)
              </Button>
              <Button
                size="small"
                variant="outlined"
                onClick={() => planInput.current?.click()}
                startIcon={uploading ? <CircularProgress size={14} color="inherit" /> : <UploadFileIcon fontSize="small" />}
                disabled={uploading}
              >
                {uploading ? "Uploading…" : "Upload CSV"}
              </Button>
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
            </>
          ) : undefined
        }
      />

      <Box component="section" aria-label="Budget plan" sx={{ mb: 3 }}>
        {errorMsg && (
          <Alert
            severity="error"
            sx={{ mb: 2 }}
            onClose={() => setErrorMsg(null)}
            action={
              changed && !currentId ? (
                <Button color="inherit" size="small" onClick={() => setRetryToken((t) => t + 1)}>
                  Retry
                </Button>
              ) : undefined
            }
          >
            {errorMsg}
          </Alert>
        )}

        {forecasts.length === 0 ? (
          <Alert
            severity="info"
            action={
              runId ? (
                <Button color="inherit" size="small" onClick={() => setBuilderOpen(true)}>
                  Configure new plan
                </Button>
              ) : undefined
            }
          >
            No forecast yet. The example-plan forecast appears when the Meridian stages of this run have finished; you can also
            configure or upload your own plan (the day after the history onwards, one cost column per paid channel).
          </Alert>
        ) : (
          <Box>
            <Stack direction={{ xs: "column", sm: "row" }} spacing={1.5} alignItems={{ xs: "stretch", sm: "center" }} flexWrap="wrap" useFlexGap>
              <FormControl size="small" sx={{ width: { xs: "100%", sm: 480 }, maxWidth: "100%" }}>
                <InputLabel id="forecast-select-label">Budget plan</InputLabel>
                <Select
                  labelId="forecast-select-label"
                  label="Budget plan"
                  value={forecastId ?? ""}
                  onChange={(e) => setForecastId(e.target.value as string)}
                  sx={{ backgroundColor: "#FFFFFF" }}
                >
                  {forecasts.map((f) => (
                    <MenuItem key={f.id} value={f.id}>
                      <Stack direction="row" spacing={1} alignItems="center" sx={{ width: "100%", minWidth: 0 }}>
                        <Box sx={{ flexGrow: 1, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{f.name}</Box>
                        <Chip size="small" label={RUN_STATUS_LABEL[f.status] ?? f.status} color={runStatusColor(f.status)} />
                      </Stack>
                    </MenuItem>
                  ))}
                </Select>
              </FormControl>
              {runId && (
                <Button
                  size="small"
                  variant="outlined"
                  startIcon={<TuneIcon fontSize="small" />}
                  onClick={() => setBuilderOpen(true)}
                  sx={{ backgroundColor: "#FFFFFF" }}
                >
                  New custom plan (1–90d)…
                </Button>
              )}
            </Stack>
            <Stack
              direction="row"
              spacing={1}
              alignItems="center"
              flexWrap="wrap"
              useFlexGap
              sx={{
                mt: 1.25,
                px: 1.5,
                py: 0.75,
                borderRadius: 1.5,
                backgroundColor: "#FFFFFF",
                border: `1px solid ${PALETTE.borderSubtle}`,
              }}
            >
              <Typography variant="caption" sx={{ fontWeight: 700, color: PALETTE.inkPrimary }}>
                Spend &amp; plan input granularity:
              </Typography>
              <Chip
                size="small"
                label="Daily × Channel · National · EUR (€)"
                sx={{ height: 20, fontSize: "0.7rem", fontWeight: 600 }}
              />
              <Typography variant="caption" sx={{ color: PALETTE.inkSecondary }}>
                1 row/day × {levers.length + locked.length || 7} paid channels ({levers.filter((c) => c.slug !== "catalog").length || 5} continuous daily digital · {levers.some((c) => c.slug === "catalog") ? "1 episodic Catalog send-out" : "Catalog"} · {locked.length || 1} commission control)
              </Typography>
            </Stack>
            {scenarioBusy && (
              <Box sx={{ mt: 2 }}>
                <Typography variant="body2" sx={{ mb: 1 }}>
                  Refitting Meridian on the history plus your plan window
                  {typeof forecast?.queue_position === "number" && forecast.queue_position > 0 ? ` (queue position ${forecast.queue_position})` : ""}…
                </Typography>
                <LinearProgress color="secondary" aria-label="Forecast refit in progress" />
              </Box>
            )}
            {forecast?.status === "failed" && (
              <Alert severity="error" sx={{ mt: 2 }}>
                {forecast.error || "The forecast failed."}
              </Alert>
            )}
          </Box>
        )}
      </Box>

      {result && figure && (
        <Box
          sx={{
            display: "grid",
            gap: 3,
            gridTemplateColumns: { xs: "minmax(0, 1fr)", md: "340px minmax(0, 1fr)", lg: "380px minmax(0, 1fr)" },
            alignItems: "start",
            mb: 4,
          }}
        >
          <Paper
            sx={{
              p: { xs: 2, md: 2.5 },
              position: { md: "sticky" },
              top: { md: 76 },
              maxHeight: { md: "calc(100vh - 92px)" },
              overflowY: { md: "auto" },
            }}
          >
            <LeverPanel
              levers={levers}
              locked={locked}
              multipliers={multipliers}
              onChange={setMultipliers}
              effects={shownResult ? Object.fromEntries(shownResult.channel_summary.map((r) => [r.slug, r])) : null}
              effectsStale={stale}
              evaluating={evaluating}
              horizonDays={horizon}
              horizonMax={horizonMax}
              startDate={result.start_date}
              planDaily={result.plan_daily}
              inputMode={inputMode}
              onInputModeChange={setInputMode}
              spendGranularity={spendGranularity}
              onSpendGranularityChange={setSpendGranularity}
              onHorizonChange={setHorizonDays}
              onSave={handleSave}
              canSave={canSave}
              saveHint={saveHint}
              onOpenPlanBuilder={runId ? () => setBuilderOpen(true) : undefined}
            />
          </Paper>

          <Stack spacing={3} sx={{ minWidth: 0 }}>
            <Paper component="section" aria-labelledby="forecast-chart-title" sx={{ p: { xs: 2, md: 2.5 } }}>
              <Typography id="forecast-chart-title" variant="h3" component="h2">
                {result.start_date && result.end_date
                  ? `Forecast ${shortDate(result.start_date)} – ${shortDate(result.end_date)}, with the sales that led into it`
                  : "Forecast, with the sales that led into it"}
              </Typography>

              <Box sx={{ display: "grid", gap: 2, gridTemplateColumns: "repeat(auto-fit, minmax(128px, 1fr))", mt: 2, mb: 2.5 }}>
                <Tile
                  label="Plan forecast"
                  value={eur(planSales)}
                  accent={PLANNER_COLORS.plan}
                  sub={
                    <>
                      {horizon} days · {eurCompact(planSales / Math.max(horizon, 1))}/day
                      {horizon === horizonMax && result.prediction_total_lo != null && result.prediction_total_hi != null && (
                        <>
                          <br />
                          <Box component="span" sx={{ whiteSpace: "nowrap" }}>
                            90 %: {eurCompact(result.prediction_total_lo)} to {eurCompact(result.prediction_total_hi)}
                          </Box>
                        </>
                      )}
                    </>
                  }
                />
                <Tile
                  label="Scenario"
                  accent={PLANNER_COLORS.scenario}
                  dim={stale}
                  value={shownResult ? eur(shownResult.scenario_total_sales) : "As planned"}
                  sub={
                    shownResult ? (
                      <>
                        {signedEur(shownResult.delta_sales)} ({signedPct(shownResult.delta_sales_pct, 2)})
                        {shownResult.delta_sales_lo != null && shownResult.delta_sales_hi != null && (
                          <>
                            <br />
                            <Box component="span" sx={{ whiteSpace: "nowrap" }}>
                              90 %: {eurCompact(shownResult.delta_sales_lo, true)} to {eurCompact(shownResult.delta_sales_hi, true)}
                            </Box>
                          </>
                        )}
                      </>
                    ) : evaluating
                        ? "Evaluating…"
                        : "Move a lever or pick a quick scenario"
                  }
                />
                <Tile
                  label="Spend"
                  dim={stale}
                  value={shownResult ? `${eurCompact(shownResult.base_total_spend)} → ${eurCompact(shownResult.scenario_total_spend)}` : eur(planSpend)}
                  sub={shownResult ? `${signedEur(shownResult.delta_spend)} vs plan` : "Planned over the same days"}
                />
                <Tile
                  label="Δ sales per Δ € spend"
                  dim={stale}
                  value={shownResult?.marginal_roas != null ? shownResult.marginal_roas.toFixed(2) : "–"}
                  sub={
                    shownResult
                      ? shownResult.marginal_roas != null
                        ? "Return on the euros added or removed"
                        : "Spend unchanged: budget moved between channels"
                      : "Shown once a scenario changes spend"
                  }
                />
                {lastYear != null && lastYear > 0 && (
                  <Tile
                    label="Same weekdays last year"
                    value={eur(lastYear)}
                    accent={PLANNER_COLORS.lastYear}
                    sub={`Plan forecast ${signedPct((100 * (planSales - lastYear)) / lastYear)} vs actual`}
                  />
                )}
              </Box>

              <Stack direction={{ xs: "column", sm: "row" }} spacing={1.5} alignItems={{ xs: "flex-start", sm: "center" }} justifyContent="space-between" flexWrap="wrap" useFlexGap>
                <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap" useFlexGap>
                  <Typography id="history-range-label" variant="caption" sx={{ fontWeight: 700, color: PALETTE.inkSecondary }}>
                    History shown
                  </Typography>
                  <ToggleButtonGroup
                    size="small"
                    exclusive
                    value={historyRange}
                    onChange={(_, v: string | null) => v && setHistoryRange(v)}
                    aria-labelledby="history-range-label"
                  >
                    {HISTORY_RANGES.map((r) => (
                      <ToggleButton key={r.id} value={r.id} sx={{ textTransform: "none", px: 1.25, py: 0.25, fontWeight: 600 }}>
                        {r.label}
                      </ToggleButton>
                    ))}
                  </ToggleButtonGroup>
                </Stack>
                <FormControlLabel
                  control={<Checkbox size="small" checked={showLastYear} onChange={(e) => setShowLastYear(e.target.checked)} />}
                  label={<Typography variant="body2">Same weekday last year</Typography>}
                />
              </Stack>

              <Box sx={{ mt: 1, position: "relative" }}>
                <Suspense fallback={<Skeleton variant="rounded" height={figure.layout.height} />}>
                  <LazyPlotlyChart data={figure.data} layout={figure.layout} height={figure.layout.height} ariaLabel="Actual sales, plan forecast and scenarios" />
                </Suspense>
              </Box>
              <Typography variant="caption" component="p" sx={{ color: PALETTE.inkSecondary }}>
                The shaded band is the plan's 90 % prediction interval, where an actual day is expected to land. Lower panel: daily
                spend, with the scenario's as narrow gold bars; catalog send-out days run off the top of the panel, and hovering
                shows their cost. Hover a day of the scenario to see each channel's share of its change.
              </Typography>
              {failed && (
                <Alert severity="error" sx={{ mt: 1.5 }}>
                  {current?.error || "The scenario failed."}
                </Alert>
              )}

              <Accordion disableGutters elevation={0} sx={{ mt: 2, border: `1px solid ${PALETTE.borderSubtle}`, borderRadius: 1.5, "&:before": { display: "none" } }}>
                <AccordionSummary expandIcon={<ExpandMoreIcon />} aria-controls="daily-table" id="daily-table-header">
                  <Typography variant="body2" sx={{ fontWeight: 650, color: PALETTE.inkPrimary }}>
                    Daily numbers behind the chart
                  </Typography>
                </AccordionSummary>
                <AccordionDetails id="daily-table">
                  <Button
                    size="small"
                    variant="outlined"
                    startIcon={<DownloadIcon fontSize="small" />}
                    onClick={() => downloadCsv(tableRows, `forecast_${result.start_date ?? "plan"}${shownResult ? "_scenario" : ""}.csv`)}
                    sx={{ mb: 1.5 }}
                  >
                    Download CSV
                  </Button>
                  <TableContainer role="region" aria-label="Daily sales, forecast and spend" tabIndex={0} sx={{ maxHeight: 420, border: `1px solid ${PALETTE.borderSubtle}`, borderRadius: 1.5 }}>
                    <Table size="small" stickyHeader>
                      <TableHead>
                        <TableRow>
                          <TableCell scope="col">Date</TableCell>
                          <TableCell scope="col" align="right">Actual sales</TableCell>
                          <TableCell scope="col" align="right">Same weekday last year</TableCell>
                          <TableCell scope="col" align="right">Plan forecast</TableCell>
                          <TableCell scope="col" align="right">90 % low</TableCell>
                          <TableCell scope="col" align="right">90 % high</TableCell>
                          {shownResult && <TableCell scope="col" align="right">Scenario</TableCell>}
                          <TableCell scope="col" align="right">Spend</TableCell>
                          {shownResult && <TableCell scope="col" align="right">Scenario spend</TableCell>}
                        </TableRow>
                      </TableHead>
                      <TableBody>
                        {tableRows.map((r) => {
                          const v = (x: number | null) => (x == null ? "–" : eur(x));
                          return (
                            <TableRow key={r.date} sx={r.plan != null ? { backgroundColor: "rgba(140, 114, 59, 0.05)" } : undefined}>
                              <TableCell component="th" scope="row" sx={{ whiteSpace: "nowrap" }}>
                                {shortDate(r.date)}
                              </TableCell>
                              {[r.actual, r.lastYear, r.plan, r.lo, r.hi].map((x, i) => (
                                <TableCell key={i} align="right" sx={NUMERIC}>
                                  {v(x)}
                                </TableCell>
                              ))}
                              {shownResult && <TableCell align="right" sx={NUMERIC}>{v(r.scenario)}</TableCell>}
                              <TableCell align="right" sx={NUMERIC}>{v(r.spend)}</TableCell>
                              {shownResult && <TableCell align="right" sx={NUMERIC}>{v(r.scenarioSpend)}</TableCell>}
                            </TableRow>
                          );
                        })}
                      </TableBody>
                    </Table>
                  </TableContainer>
                </AccordionDetails>
              </Accordion>
            </Paper>

            {shownResult && (
              <Paper component="section" aria-labelledby="effects-title" sx={{ p: { xs: 2, md: 2.5 } }}>
                <Typography id="effects-title" variant="h3" component="h2">
                  What each change does to forecast sales
                </Typography>
                <Typography variant="body2" sx={{ mt: 0.5, mb: 1.5 }}>
                  Each bar is one channel's own effect, with its 90 % credible interval; the last bar is the scenario as a whole.
                </Typography>
                <ChannelEffects result={shownResult} stale={stale} compact={compact} />
              </Paper>
            )}

            <Paper component="section" aria-labelledby="saved-title" sx={{ p: { xs: 2, md: 2.5 } }}>
              <Typography id="saved-title" variant="h3" component="h2">
                Saved scenarios
              </Typography>
              {saved.length === 0 ? (
                <Typography variant="body2" sx={{ mt: 0.5 }}>
                  Save up to {MAX_SAVED} scenarios to keep them on the chart as dashed lines while you try others.
                </Typography>
              ) : (
                <Stack spacing={1.25} sx={{ mt: 1.5 }}>
                  {savedDetails.map(({ entry, detail }) => {
                    const r = detail?.result;
                    return (
                      <Stack
                        key={entry.id}
                        direction={{ xs: "column", sm: "row" }}
                        spacing={{ xs: 0.5, sm: 2 }}
                        alignItems={{ xs: "flex-start", sm: "center" }}
                        sx={{ p: 1.25, border: `1px solid ${PALETTE.borderSubtle}`, borderRadius: 1.5 }}
                      >
                        <Stack direction="row" spacing={1.25} alignItems="center" sx={{ flexGrow: 1, minWidth: 0 }}>
                          <Box aria-hidden="true" sx={{ width: 22, flexShrink: 0, borderTop: `2.5px dashed ${PLANNER_COLORS.saved[entry.slot % MAX_SAVED]}` }} />
                          <Box sx={{ minWidth: 0 }}>
                            <Typography sx={{ fontWeight: 650, fontSize: "0.9rem" }}>{entry.name}</Typography>
                            <Typography variant="caption" sx={{ color: PALETTE.inkSecondary, ...NUMERIC }}>
                              {r
                                ? `${signedEur(r.delta_sales)} sales (${signedPct(r.delta_sales_pct, 2)}) · ${signedEur(r.delta_spend)} spend · ${r.horizon_days} days`
                                : detail?.status === "failed"
                                  ? "This scenario failed."
                                  : "Loading…"}
                            </Typography>
                          </Box>
                        </Stack>
                        <Stack direction="row" spacing={0.5} alignItems="center">
                          <Button size="small" onClick={() => handleLoad(entry)}>
                            Load
                          </Button>
                          <Tooltip title="Remove">
                            <IconButton size="small" aria-label={`Remove saved scenario ${entry.name}`} onClick={() => persistSaved(saved.filter((s) => s.id !== entry.id))}>
                              <DeleteOutlineIcon fontSize="small" />
                            </IconButton>
                          </Tooltip>
                        </Stack>
                      </Stack>
                    );
                  })}
                </Stack>
              )}
            </Paper>
          </Stack>
        </Box>
      )}

      <Box className="visually-hidden" role="status" aria-live="polite">
        {announcement}
      </Box>

      <SectionRenderer
        section={section}
        isLoading={isLoading}
        error={error as Error | null}
        headingLevel={2}
        toc={false}
        pendingMessage="The back-test, the example-plan forecast and its scenarios appear when the Meridian forecast stage has finished."
      />

      {runId && (
        <PlanBuilderDialog
          open={builderOpen}
          onClose={() => setBuilderOpen(false)}
          runId={runId}
          channelNames={channelNames}
          lockedSlugs={lockedSlugs}
          initialMode={inputMode}
          initialGranularity={spendGranularity}
          onPlanCreated={(newForecastId) => {
            void queryClient.invalidateQueries({ queryKey: ["forecasts", runId] });
            setForecastId(newForecastId);
          }}
        />
      )}
    </Box>
  );
};
