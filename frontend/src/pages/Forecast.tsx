import React, { Suspense, useEffect, useId, useMemo, useRef, useState } from "react";
import {
  Alert,
  Box,
  Button,
  Card,
  CardContent,
  Checkbox,
  Chip,
  Collapse,
  FormControl,
  FormControlLabel,
  IconButton,
  InputAdornment,
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
  TextField,
  ToggleButton,
  ToggleButtonGroup,
  Tooltip,
  Typography,
  alpha,
  useMediaQuery,
} from "@mui/material";
import DownloadIcon from "@mui/icons-material/Download";
import ExpandMoreIcon from "@mui/icons-material/ExpandMore";
import ExpandLessIcon from "@mui/icons-material/ExpandLess";
import TuneIcon from "@mui/icons-material/Tune";
import TableChartOutlinedIcon from "@mui/icons-material/TableChartOutlined";
import RestartAltIcon from "@mui/icons-material/RestartAlt";
import DeleteOutlineIcon from "@mui/icons-material/DeleteOutline";
import LockOutlinedIcon from "@mui/icons-material/LockOutlined";
import LockOpenOutlinedIcon from "@mui/icons-material/LockOpenOutlined";
import BookmarkAddOutlinedIcon from "@mui/icons-material/BookmarkAddOutlined";
import VisibilityOutlinedIcon from "@mui/icons-material/VisibilityOutlined";
import { useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import { useForecast, useForecasts, useRun, useRunHistory, useScenario, useSection } from "../api/hooks";
import type {
  ForecastItem,
  RunHistory,
  ScenarioDetail,
  ScenarioResult,
  VariableContributionRow,
} from "../api/types";
import {
  LAST_YEAR_SHIFT_DAYS,
  PLANNER_COLORS,
  type PlannerLine,
  buildDailyVariableStackFigure,
  buildShapleyWaterfallFigure,
  buildSpendVsContributionFigure,
  buildVariableContributionBarFigure,
  categoryBadgeMeta,
  plannerFigure,
  resolveVariableContributions,
  variableColor,
} from "../components/Forecast/figures";
import {
  type BudgetInputMode,
  type SpendGranularity,
  addDays,
  channelActiveDays,
  channelPeriodBaseSpend,
  eur,
  eurCompact,
  fromDisplayEuro,
  parseEuroAmount,
  parsePctChange,
  scaleMultipliersToTotal,
  shortDate,
  signedEur,
  signedPct,
  toDisplayEuro,
} from "../components/Forecast/format";
import { buildPresets } from "../components/Forecast/LeverPanel";
import { PlanSpreadsheet } from "../components/Forecast/PlanSpreadsheet";
import { RUN_STATUS_LABEL, runStatusColor } from "../components/Import/format";
import { OnThisPage, type TocEntry } from "../components/Sections/OnThisPage";
import { KpiStrip, MoreAnalysis, SectionRenderer } from "../components/Sections/SectionRenderer";
import { curateSection } from "../components/Sections/curation";
import { CURATIONS } from "../components/Sections/curations";
import { PageHeader } from "../components/Layout/PageHeader";
import { ExplainButton, ExplainScope, useExplainPageNotes } from "../components/Explain/ExplainButton";
import { NUMERIC, PALETTE } from "../theme";

const LazyPlotlyChart = React.lazy(() => import("../components/Charts/PlotlyChart"));

const InlinePctInput: React.FC<{
  value: number;
  name: string;
  label: string;
  disabled?: boolean;
  onCommit: (pct: number) => void;
}> = ({ value, name, label, disabled, onCommit }) => {
  const inputId = useId();
  const shown = String(Math.round(value * 10) / 10);
  const [draft, setDraft] = useState(shown);
  const [hasError, setHasError] = useState(false);

  useEffect(() => {
    setDraft(shown);
    setHasError(false);
  }, [shown]);

  const commit = () => {
    if (draft.trim() === "") {
      setDraft(shown);
      setHasError(false);
      return;
    }
    const parsed = parsePctChange(draft, -100, 300);
    if (parsed !== null) {
      setHasError(false);
      onCommit(parsed);
      setDraft(String(Math.round(parsed * 10) / 10));
    } else {
      setHasError(true);
    }
  };

  return (
    <TextField
      id={inputId}
      name={name}
      size="small"
      value={draft}
      disabled={disabled}
      error={hasError}
      onChange={(e) => {
        setDraft(e.target.value);
        if (hasError) setHasError(false);
      }}
      onBlur={commit}
      onKeyDown={(e) => {
        if (e.key === "Enter") {
          e.preventDefault();
          commit();
        } else if (e.key === "Escape") {
          setDraft(shown);
          setHasError(false);
        }
      }}
      inputProps={{
        inputMode: "decimal",
        "aria-label": label,
        style: { textAlign: "right", ...NUMERIC, fontWeight: 700, padding: "4px 2px 4px 6px", fontSize: "0.8rem" },
      }}
      InputProps={{ endAdornment: <InputAdornment position="end">%</InputAdornment> }}
      sx={{ width: 84, flexShrink: 0, "& .MuiInputBase-root": { backgroundColor: "#FFFFFF" } }}
    />
  );
};

const InlineEuroInput: React.FC<{
  value: number;
  name: string;
  label: string;
  suffix?: string;
  disabled?: boolean;
  onCommit: (euros: number) => void;
}> = ({ value, name, label, suffix, disabled, onCommit }) => {
  const inputId = useId();
  const shown = Math.max(0, Math.round(value)).toLocaleString("en-GB");
  const [draft, setDraft] = useState(shown);
  const [focused, setFocused] = useState(false);
  const [hasError, setHasError] = useState(false);

  useEffect(() => {
    if (!focused) {
      setDraft(shown);
      setHasError(false);
    }
  }, [shown, focused]);

  const commit = () => {
    setFocused(false);
    if (draft.trim() === "") {
      setDraft(shown);
      setHasError(false);
      return;
    }
    const parsed = parseEuroAmount(draft);
    if (parsed !== null) {
      setHasError(false);
      onCommit(parsed);
      setDraft(parsed.toLocaleString("en-GB"));
    } else {
      setHasError(true);
    }
  };

  return (
    <TextField
      id={inputId}
      name={name}
      size="small"
      value={draft}
      disabled={disabled}
      error={hasError}
      onFocus={() => {
        setFocused(true);
        setDraft(String(Math.max(0, Math.round(value))));
      }}
      onChange={(e) => {
        setDraft(e.target.value);
        if (hasError) setHasError(false);
      }}
      onBlur={commit}
      onKeyDown={(e) => {
        if (e.key === "Enter") {
          e.preventDefault();
          commit();
        } else if (e.key === "Escape") {
          setFocused(false);
          setDraft(shown);
          setHasError(false);
        }
      }}
      inputProps={{
        inputMode: "decimal",
        "aria-label": label,
        style: { textAlign: "right", ...NUMERIC, fontWeight: 700, padding: "4px 4px 4px 2px", fontSize: "0.8rem" },
      }}
      InputProps={{
        startAdornment: <InputAdornment position="start">€</InputAdornment>,
        endAdornment: suffix ? <InputAdornment position="end">{suffix}</InputAdornment> : undefined,
      }}
      sx={{ width: suffix ? 124 : 108, flexShrink: 0, "& .MuiInputBase-root": { backgroundColor: "#FFFFFF" } }}
    />
  );
};

/** `short` labels the buttons on a phone, where the full names would push the row past the screen edge. */
const HISTORY_RANGES: { id: string; label: string; short: string; days: number | null }[] = [
  { id: "6w", label: "6 weeks", short: "6w", days: 42 },
  { id: "3m", label: "3 months", short: "3m", days: 91 },
  { id: "6m", label: "6 months", short: "6m", days: 182 },
  { id: "1y", label: "1 year", short: "1y", days: 365 },
  { id: "all", label: "All", short: "All", days: null },
];

const TOC_ITEMS: TocEntry[] = [
  { id: "sec-setup", label: "1. Variable Attribution Matrix" },
  { id: "sec-trajectory", label: "2. Daily Forecast Trajectory" },
  { id: "sec-shapley", label: "3. Shapley & Input–Output Charts" },
  { id: "sec-example", label: "Example plan" },
];

/** Actual sales on the same weekdays one year before the first `n` forecast days; null when history does not cover them. */
function lastYearTotal(history: RunHistory | undefined, dates: string[]) {
  if (!history || dates.length === 0) return null;
  const byDate = new Map(history.dates.map((d, i) => [d, history.sales[i]]));
  let total = 0;
  for (const d of dates) {
    const v = byDate.get(addDays(d, -LAST_YEAR_SHIFT_DAYS));
    if (v == null) return null;
    total += v;
  }
  return total;
}

interface DailyTableRow {
  date: string;
  actual: number | null;
  lastYear: number | null;
  baselinePlan: number | null;
  scenarioForecast: number | null;
  lo: number | null;
  hi: number | null;
  spend: number | null;
  byVariable: Record<string, number | null>;
}

function dailyTableRows(
  history: RunHistory | undefined,
  plan: ScenarioResult,
  active: ScenarioResult,
  historyDays: number | null,
  variables: VariableContributionRow[] = []
): DailyTableRow[] {
  if (!active.daily.length) return [];
  const fcStart = active.daily[0].date;
  const from = historyDays == null ? null : addDays(fcStart, -historyDays);
  const salesByDate = new Map((history?.dates ?? []).map((d, i) => [d, history!.sales[i]]));
  const lastYear = (d: string) => salesByDate.get(addDays(d, -LAST_YEAR_SHIFT_DAYS)) ?? null;
  const rows: DailyTableRow[] = [];
  history?.dates.forEach((d, i) => {
    if ((from == null || d >= from) && d < fcStart) {
      const spend = Object.values(history.spend).reduce<number>((acc, arr) => acc + (arr[i] ?? 0), 0);
      rows.push({
        date: d,
        actual: history.sales[i],
        lastYear: lastYear(d),
        baselinePlan: null,
        scenarioForecast: null,
        lo: null,
        hi: null,
        spend,
        byVariable: {},
      });
    }
  });
  active.daily.forEach((r, idx) => {
    const baseRow = plan.daily[idx];
    const byVariable: Record<string, number | null> = {};
    for (const v of variables) {
      byVariable[v.key] = v.daily_scenario[idx] ?? null;
    }
    rows.push({
      date: r.date,
      actual: null,
      lastYear: lastYear(r.date),
      baselinePlan: baseRow ? baseRow.baseline_forecast : r.baseline_forecast,
      scenarioForecast: r.scenario_forecast,
      lo: r.prediction_lo,
      hi: r.prediction_hi,
      spend: r.planned_spend,
      byVariable,
    });
  });
  return rows;
}

function downloadDailyCsv(rows: DailyTableRow[], variables: VariableContributionRow[], filename: string) {
  const varHeaders = variables.map((v) => `forecast_${v.key}_eur`);
  const header = [
    "date",
    "actual_sales",
    "same_weekday_last_year",
    "baseline_plan_forecast",
    "scenario_forecast",
    "pi90_low",
    "pi90_high",
    "planned_spend",
    ...varHeaders,
  ].join(",");
  const cell = (v: number | null | undefined) => (v == null ? "" : String(Math.round(v)));
  const body = rows.map((r) =>
    [
      r.date,
      cell(r.actual),
      cell(r.lastYear),
      cell(r.baselinePlan),
      cell(r.scenarioForecast),
      cell(r.lo),
      cell(r.hi),
      cell(r.spend),
      ...variables.map((v) => cell(r.byVariable[v.key])),
    ].join(",")
  );
  const url = URL.createObjectURL(new Blob([[header, ...body].join("\n")], { type: "text/csv;charset-utf-8" }));
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

function downloadVariablesCsv(vars: VariableContributionRow[], filename: string) {
  const header = [
    "variable_key",
    "variable_label",
    "category",
    "multiplier",
    "base_spend_eur",
    "scenario_spend_eur",
    "delta_spend_eur",
    "base_contribution_eur",
    "scenario_contribution_eur",
    "scenario_ci90_lo_eur",
    "scenario_ci90_hi_eur",
    "shapley_delta_sales_eur",
    "share_of_forecast_pct",
    "roi",
    "mroi",
  ].join(",");
  const num = (v: number | null | undefined) => (v == null ? "" : String(v));
  const body = vars.map((v) =>
    [
      v.key,
      `"${v.label.replace(/"/g, '""')}"`,
      v.category,
      v.multiplier.toFixed(3),
      num(v.base_spend),
      num(v.scenario_spend),
      num(v.delta_spend),
      Math.round(v.base_contribution),
      Math.round(v.scenario_contribution),
      num(v.scenario_ci_lo),
      num(v.scenario_ci_hi),
      Math.round(v.delta_contribution),
      v.scenario_share_pct.toFixed(2),
      v.roi != null ? v.roi.toFixed(2) : "",
      v.mroi != null ? v.mroi.toFixed(2) : "",
    ].join(",")
  );
  const url = URL.createObjectURL(new Blob([[header, ...body].join("\n")], { type: "text/csv;charset=utf-8" }));
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

const sameMultipliers = (a: Record<string, number>, b: Record<string, number>) => {
  const keys = new Set([...Object.keys(a), ...Object.keys(b)]);
  return [...keys].every((k) => Math.abs((a[k] ?? 1) - (b[k] ?? 1)) < 1e-6);
};

const isVariableActiveInWindow = (v: VariableContributionRow) =>
  Math.abs(v.scenario_contribution) >= 100 ||
  Math.abs(v.base_contribution) >= 100 ||
  (v.scenario_spend ?? 0) >= 100 ||
  (v.base_spend ?? 0) >= 100;

/** The page's own title and description, which Gemini's explanations of its charts and tables may use. */
const FORECAST_EXPLAIN_SCOPE = {
  sectionTitle: "Forecast & scenarios",
  sectionSubtitle:
    "The daily sales forecast for a budget plan on the fitted Meridian model, each variable's additive contribution to it, and what-if changes to channel budgets or daily plans.",
};

export const ForecastPage: React.FC<{ runId: string | null }> = (props) => (
  <ExplainScope value={FORECAST_EXPLAIN_SCOPE}>
    <ForecastPlanner {...props} />
  </ExplainScope>
);

/**
 * The example plan and its scenarios as the pipeline computed them: secondary content, collapsed under the
 * planner (the plan-time back-test of the same section lives on Fit & validation).
 */
const ExamplePlan: React.FC<{ runId: string | null }> = ({ runId }) => {
  const { data: section } = useSection(runId, "forecast_scenarios");
  const count = useMemo(() => (section ? curateSection(section, CURATIONS.forecast_example).primary.length : 0), [section]);
  if (!section || count === 0) return null;
  return (
    <MoreAnalysis id="sec-example" count={count} label="Example plan and scenarios">
      <SectionRenderer section={section} headingLevel={0} subtitle={null} toc={false} curation={CURATIONS.forecast_example} />
    </MoreAnalysis>
  );
};

const ForecastPlanner: React.FC<{ runId: string | null }> = ({ runId }) => {
  const queryClient = useQueryClient();
  const { data: run } = useRun(runId);
  const { data: history, error: historyError } = useRunHistory(runId);
  const { data: forecastsData } = useForecasts(runId);
  const forecasts = useMemo(() => forecastsData?.forecasts ?? [], [forecastsData]);

  const [forecastId, setForecastId] = useState<string | null>(null);
  const pendingForecastIdRef = useRef<string | null>(null);

  // Interactive Setup & Scenario Planner state
  const [setupExpanded, setSetupExpanded] = useState(true);
  const [editorTab, setEditorTab] = useState<"levers" | "sales_cells" | "spreadsheet">("levers");
  const [multipliers, setMultipliers] = useState<Record<string, number>>({});
  const [spendOverrides, setSpendOverrides] = useState<Record<string, number>>({});
  const [horizonDays, setHorizonDays] = useState<number>(42);
  const [unlockControls, setUnlockControls] = useState<boolean>(false);
  const [inputMode, setInputMode] = useState<BudgetInputMode>("pct");
  const [spendGranularity, setSpendGranularity] = useState<SpendGranularity>("period");
  const [showZeroActivityVars, setShowZeroActivityVars] = useState<boolean>(false);

  // Scenario evaluation & comparison state
  const [activeScenarioId, setActiveScenarioId] = useState<string | null>(null);
  const [immediateScenarioResult, setImmediateScenarioResult] = useState<ScenarioResult | null>(null);
  const [submittingScenario, setSubmittingScenario] = useState(false);
  const [scenarioError, setScenarioError] = useState<string | null>(null);
  const [savedScenarios, setSavedScenarios] = useState<{ id: string; name: string; result: ScenarioResult }[]>([]);

  // Interactive chart & table filters
  const [selectedVariable, setSelectedVariable] = useState<string | null>(null);
  const [categoryFilter, setCategoryFilter] = useState<"all" | "media" | "baseline_controls">("all");
  const [waterfallMode, setWaterfallMode] = useState<"absolute" | "delta">("absolute");
  const [rightChartMode, setRightChartMode] = useState<"io" | "ranking">("io");
  const [trajectoryTab, setTrajectoryTab] = useState<"trajectory" | "stack" | "table">("trajectory");
  const [dailyStackMode, setDailyStackMode] = useState<"absolute" | "delta">("absolute");
  // "backtest" (validation + test + forecast) until the user picks a range; 3 months when the run has no back-test
  const [historyRange, setHistoryRange] = useState("backtest");
  const [showLastYear, setShowLastYear] = useState(false);
  const [showHistoryInTable, setShowHistoryInTable] = useState(false);

  const compact = useMediaQuery("(max-width: 600px)", { noSsr: true });

  useEffect(() => {
    pendingForecastIdRef.current = null;
    setForecastId(null);
    setMultipliers({});
    setSpendOverrides({});
    setActiveScenarioId(null);
    setImmediateScenarioResult(null);
    setSavedScenarios([]);
    setSelectedVariable(null);
  }, [runId]);

  // Default to the run's full 42-day baseline forecast if available, otherwise the newest finished forecast
  useEffect(() => {
    if (forecastId && (forecastId === pendingForecastIdRef.current || forecasts.some((f) => f.id === forecastId))) {
      if (forecasts.some((f) => f.id === forecastId)) {
        pendingForecastIdRef.current = null;
      }
      return;
    }
    const baselineDone = forecasts.find((f) => f.id.startsWith("default_") && f.status === "succeeded");
    const done = baselineDone ?? forecasts.find((f) => f.status === "succeeded");
    setForecastId(done?.id ?? forecasts[0]?.id ?? null);
  }, [forecasts, forecastId]);

  const { data: forecast } = useForecast(forecastId);
  const planResult = forecast?.result ?? null;
  const horizonMax = planResult?.horizon_days ?? 42;

  // Reset horizon and scenario state when switching baseline forecast plans
  useEffect(() => {
    if (planResult) {
      setHorizonDays(planResult.horizon_days);
      setMultipliers({});
      setSpendOverrides({});
      setActiveScenarioId(null);
      setImmediateScenarioResult(null);
      setScenarioError(null);
    }
  }, [forecastId, planResult?.horizon_days]);

  const { data: polledScenario } = useScenario(activeScenarioId);

  const lockedSlugs = useMemo(() => {
    const channels = run?.config?.role_config?.channels ?? [];
    const fromRun = channels
      .filter((c) => c.role === "commission" || c.role === "control")
      .map((c) => c.slug ?? c.name);
    if (fromRun.length > 0) return new Set(fromRun);
    return new Set(
      (planResult?.channel_summary ?? [])
        .filter((c) => c.role === "control" || c.slug === "affiliate")
        .map((c) => c.slug)
    );
  }, [run, planResult]);

  const channelRows = useMemo(() => planResult?.channel_summary ?? [], [planResult]);
  const levers = useMemo(() => channelRows.filter((c) => !lockedSlugs.has(c.slug)), [channelRows, lockedSlugs]);

  const channelNames = useMemo(() => {
    const fromRun = Object.fromEntries(
      (run?.config?.role_config?.channels ?? []).map((c) => [c.slug ?? c.name, c.name])
    );
    const fromResult = Object.fromEntries(channelRows.map((c) => [c.slug, c.channel]));
    return { ...fromRun, ...fromResult };
  }, [run, channelRows]);

  const periodBaseBySlug = useMemo(() => {
    const map: Record<string, number> = {};
    for (const ch of channelRows) {
      map[ch.slug] = channelPeriodBaseSpend(
        ch.base_spend,
        planResult?.plan_daily?.[ch.slug],
        horizonDays,
        horizonMax
      );
    }
    return map;
  }, [channelRows, planResult?.plan_daily, horizonDays, horizonMax]);

  const printSlugs = useMemo(
    () =>
      (run?.config?.role_config?.channels ?? [])
        .filter((c) => c.role === "print_event")
        .map((c) => c.slug ?? c.name),
    [run]
  );
  const presets = useMemo(
    () => buildPresets(levers, periodBaseBySlug, printSlugs.length ? printSlugs : undefined),
    [levers, periodBaseBySlug, printSlugs]
  );

  const hasMultiplierChanges = useMemo(
    () =>
      Object.entries(multipliers).some(([slug, m]) => {
        if (!unlockControls && lockedSlugs.has(slug)) return false;
        return Math.abs(m - 1) > 1e-6;
      }) ||
      Object.entries(spendOverrides).some(([slug, sp]) => {
        if (!unlockControls && lockedSlugs.has(slug)) return false;
        const base = periodBaseBySlug[slug] ?? 0;
        return Math.abs(sp - base) >= 0.5;
      }),
    [multipliers, spendOverrides, periodBaseBySlug, unlockControls, lockedSlugs]
  );

  const isBaselineSelected = !hasMultiplierChanges && horizonDays === horizonMax;

  // Debounced interactive scenario evaluation when multipliers, spend overrides, or horizon change
  useEffect(() => {
    if (!forecastId || !planResult || forecast?.status !== "succeeded") return;
    if (isBaselineSelected) {
      setActiveScenarioId(null);
      setImmediateScenarioResult(null);
      setScenarioError(null);
      setSubmittingScenario(false);
      return;
    }

    const timer = window.setTimeout(async () => {
      setSubmittingScenario(true);
      setScenarioError(null);
      try {
        const cleanMults: Record<string, number> = {};
        for (const [k, v] of Object.entries(multipliers)) {
          if (!unlockControls && lockedSlugs.has(k)) continue;
          if (Math.abs(v - 1) > 1e-6) cleanMults[k] = v;
        }
        const cleanOverrides: Record<string, number> = {};
        for (const [k, v] of Object.entries(spendOverrides)) {
          if (!unlockControls && lockedSlugs.has(k)) continue;
          const base = periodBaseBySlug[k] ?? 0;
          if (Math.abs(v - base) >= 0.5) cleanOverrides[k] = v;
        }
        const hasOverrides = Object.keys(cleanOverrides).length > 0;
        const presetMatch = !hasOverrides ? presets.find((p) => sameMultipliers(p.multipliers, cleanMults)) : undefined;
        const name = presetMatch
          ? presetMatch.label
          : Object.keys(cleanMults).length > 0 || hasOverrides
            ? `Custom scenario (${horizonDays}d)`
            : `Baseline plan (${horizonDays}d)`;

        const created: ScenarioDetail = await api.createScenario(forecastId, {
          name,
          multipliers: cleanMults,
          spend_overrides: cleanOverrides,
          horizon_days: horizonDays,
          unlock_controls: unlockControls,
        });
        queryClient.setQueryData(["scenario", created.id], created);
        if (created.status === "succeeded" && created.result) {
          setImmediateScenarioResult(created.result);
          setActiveScenarioId(created.id);
        } else {
          setActiveScenarioId(created.id);
        }
      } catch (err) {
        setScenarioError(err instanceof Error ? err.message : "Failed to evaluate scenario.");
      } finally {
        setSubmittingScenario(false);
      }
    }, 220);

    return () => window.clearTimeout(timer);
  }, [
    forecastId,
    planResult,
    forecast?.status,
    isBaselineSelected,
    multipliers,
    spendOverrides,
    periodBaseBySlug,
    horizonDays,
    unlockControls,
    lockedSlugs,
    presets,
    queryClient,
  ]);

  useEffect(() => {
    if (polledScenario?.status === "succeeded" && polledScenario.result) {
      setImmediateScenarioResult(polledScenario.result);
    } else if (polledScenario?.status === "failed") {
      setScenarioError(polledScenario.error || "Scenario evaluation failed.");
    }
  }, [polledScenario]);

  const evaluatingScenario =
    submittingScenario || polledScenario?.status === "queued" || polledScenario?.status === "running";

  const activeResult: ScenarioResult | null = useMemo(() => {
    if (!planResult) return null;
    if (isBaselineSelected) return planResult;
    return immediateScenarioResult ?? polledScenario?.result ?? planResult;
  }, [planResult, isBaselineSelected, immediateScenarioResult, polledScenario?.result]);

  // The plan and scenario totals, so every explanation on the page knows what is being compared
  useExplainPageNotes(
    activeResult
      ? `Plan window: ${activeResult.horizon_days} days${activeResult.start_date ? ` (${activeResult.start_date} to ${activeResult.end_date ?? "?"})` : ""}. ` +
          `Baseline plan forecast: ${eur(activeResult.base_total_sales)} on ${eur(activeResult.base_total_spend)} of planned media spend. ` +
          (isBaselineSelected
            ? "No scenario is applied (the baseline plan is shown)."
            : `Active scenario: ${eur(activeResult.scenario_total_sales)} on ${eur(activeResult.scenario_total_spend)} of media spend, ` +
              `${signedEur(activeResult.delta_sales)} (${signedPct(activeResult.delta_sales_pct, 2)}) vs the baseline plan.`)
      : null
  );

  const variableContributions = useMemo(
    () => (activeResult ? resolveVariableContributions(activeResult) : []),
    [activeResult]
  );

  // Order rows so interactive Paid Media levers are at the top, followed by Structural / Control drivers
  const orderedMediaVars = useMemo(
    () =>
      variableContributions
        .filter((v) => v.category === "media")
        .sort((a, b) => b.scenario_contribution - a.scenario_contribution),
    [variableContributions]
  );

  const orderedStructuralVars = useMemo(
    () =>
      variableContributions
        .filter((v) => v.category !== "media")
        .sort((a, b) => Math.abs(b.scenario_contribution) - Math.abs(a.scenario_contribution)),
    [variableContributions]
  );

  const controlChannelNames = useMemo(
    () =>
      orderedStructuralVars
        .filter((v) => v.category === "control_channel")
        .map((v) => v.label.replace(/ \(control[^)]*\)$/i, "")),
    [orderedStructuralVars]
  );

  const inactiveCount = useMemo(
    () =>
      variableContributions.filter(
        (v) =>
          v.category !== "media" &&
          !isVariableActiveInWindow(v) &&
          (spendOverrides[v.key] ?? 0) <= 0
      ).length,
    [variableContributions, spendOverrides]
  );

  const visibleMediaVars = useMemo(
    () => orderedMediaVars,
    [orderedMediaVars]
  );

  const visibleStructuralVars = useMemo(
    () =>
      showZeroActivityVars
        ? orderedStructuralVars
        : orderedStructuralVars.filter(
            (v) => isVariableActiveInWindow(v) || (spendOverrides[v.key] ?? 0) > 0
          ),
    [orderedStructuralVars, showZeroActivityVars, spendOverrides]
  );

  const mediaTotalSales = useMemo(
    () =>
      activeResult?.media_scenario_sales ??
      orderedMediaVars.reduce((acc, v) => acc + v.scenario_contribution, 0),
    [activeResult, orderedMediaVars]
  );
  const mediaBaseSales = useMemo(
    () =>
      activeResult?.media_base_sales ??
      orderedMediaVars.reduce((acc, v) => acc + v.base_contribution, 0),
    [activeResult, orderedMediaVars]
  );
  const mediaTotalSpend = useMemo(
    () =>
      activeResult?.media_scenario_spend ??
      orderedMediaVars.reduce((acc, v) => acc + (v.scenario_spend ?? 0), 0),
    [activeResult, orderedMediaVars]
  );
  const mediaRoi = mediaTotalSpend > 0 ? mediaTotalSales / mediaTotalSpend : null;

  const baselineControlsSales = useMemo(
    () =>
      activeResult?.baseline_and_controls_sales ??
      orderedStructuralVars.reduce((acc, v) => acc + v.scenario_contribution, 0),
    [activeResult, orderedStructuralVars]
  );

  // Figures
  // From a week before the first back-test window to the forecast, so validation, test and forecast share one view
  const backtestDays = useMemo(() => {
    const first = history?.backtest?.[0]?.start;
    const fcStart = planResult?.daily[0]?.date;
    if (!first || !fcStart) return null;
    return Math.round((Date.parse(`${fcStart}T00:00:00Z`) - Date.parse(`${first}T00:00:00Z`)) / 86_400_000) + 7;
  }, [history, planResult]);
  const historyRanges = useMemo(
    () =>
      backtestDays ? [{ id: "backtest", label: "Validation + test", short: "Val + test", days: backtestDays }, ...HISTORY_RANGES] : HISTORY_RANGES,
    [backtestDays]
  );
  const effectiveHistoryRange = historyRanges.some((r) => r.id === historyRange) ? historyRange : "3m";
  const historyDays = historyRanges.find((r) => r.id === effectiveHistoryRange)?.days ?? null;
  const hasBacktest = Boolean(history?.backtest?.length);
  const trajectoryCaption = useMemo(() => {
    const windows = history?.backtest ?? [];
    if (!windows.length) {
      return "Top panel: actual sales, the plan forecast with its 90% prediction interval and the active scenario. Bottom panel: daily spend.";
    }
    const commission = windows.some((w) => w.plan_time) ? " (a commission at its plannable level, not the realised one)" : "";
    return (
      "Top panel: actual sales; then every window forecast the way the plan is, by a model refit on all days before it, " +
      `from the spend that actually ran${commission}, with its 90% prediction interval; the active scenario. ` +
      "Stricter than the hold-out scores on Fit & validation, where the model saw the realised controls. Bottom panel: daily spend."
    );
  }, [history]);

  const plannerLines = useMemo<PlannerLine[]>(() => {
    if (!planResult || !activeResult) return [];
    const lines: PlannerLine[] = [];
    if (!isBaselineSelected && activeResult !== planResult) {
      lines.push({
        name: "Active scenario",
        result: activeResult,
        color: PLANNER_COLORS.scenario,
        dash: "solid",
        dimmed: evaluatingScenario,
        primary: true,
      });
    }
    savedScenarios.slice(0, 2).forEach((sc, idx) => {
      lines.push({
        name: sc.name,
        result: sc.result,
        color: PLANNER_COLORS.saved[idx % PLANNER_COLORS.saved.length],
        dash: "dash",
      });
    });
    return lines;
  }, [planResult, activeResult, isBaselineSelected, evaluatingScenario, savedScenarios]);

  const trajectoryFigure = useMemo(() => {
    if (!planResult) return null;
    const baseSlice: ScenarioResult =
      horizonDays < planResult.daily.length
        ? {
            ...planResult,
            horizon_days: horizonDays,
            daily: planResult.daily.slice(0, horizonDays),
          }
        : planResult;
    return plannerFigure({
      history: history ?? null,
      plan: baseSlice,
      historyDays,
      showLastYear,
      lines: plannerLines,
      channelNames,
      compact,
    });
  }, [planResult, horizonDays, history, historyDays, showLastYear, plannerLines, channelNames, compact]);

  const waterfallFigure = useMemo(
    () => (activeResult ? buildShapleyWaterfallFigure(activeResult, waterfallMode, selectedVariable) : null),
    [activeResult, waterfallMode, selectedVariable]
  );

  const contribBarFigure = useMemo(
    () =>
      variableContributions.length
        ? buildVariableContributionBarFigure(
            variableContributions,
            !isBaselineSelected && Math.abs(activeResult?.delta_sales ?? 0) >= 100,
            selectedVariable
          )
        : null,
    [variableContributions, isBaselineSelected, activeResult?.delta_sales, selectedVariable]
  );

  const spendVsContribFigure = useMemo(
    () => (variableContributions.length ? buildSpendVsContributionFigure(variableContributions, selectedVariable) : null),
    [variableContributions, selectedVariable]
  );

  const dailyStackFigure = useMemo(
    () => (activeResult ? buildDailyVariableStackFigure(activeResult, dailyStackMode, selectedVariable) : null),
    [activeResult, dailyStackMode, selectedVariable]
  );

  const planDates = activeResult ? activeResult.daily.map((r) => r.date) : [];
  // The whole dataset in time order: training, validation, test (each forecast from the days before it), then the plan
  const splitText = (() => {
    const windows = history?.backtest ?? [];
    if (!history?.dates.length || !windows.length || !planDates.length) return null;
    const days = (from: string, to: string) => Math.round((Date.parse(`${to}T00:00:00Z`) - Date.parse(`${from}T00:00:00Z`)) / 86_400_000) + 1;
    const first = windows[0];
    // Each window's share of the data (the forecast lies beyond it)
    const total = history.dates.length;
    const sized = (n: number) => `${n} days, ${Math.round((100 * n) / total)} %`;
    const parts = [
      `training ${shortDate(history.dates[0])} – ${shortDate(first.train_end)} (${sized(days(history.dates[0], first.train_end))})`,
      ...windows.map((w) => `${w.id} ${w.label} (${sized(w.dates.length)})`),
      `forecast ${shortDate(planDates[0])} – ${shortDate(planDates[planDates.length - 1])} (${planDates.length} days)`,
    ];
    return `Split of the ${history.dates.length} days of data: ${parts.join(" → ")}`;
  })();
  const lastYear = lastYearTotal(history, planDates);
  const dailyTableVariables = useMemo(
    () => [...visibleMediaVars, ...visibleStructuralVars],
    [visibleMediaVars, visibleStructuralVars]
  );
  const dailyRows = useMemo(
    () =>
      planResult && activeResult
        ? dailyTableRows(
            history,
            planResult,
            activeResult,
            showHistoryInTable ? historyDays : 0,
            dailyTableVariables
          )
        : [],
    [history, planResult, activeResult, showHistoryInTable, historyDays, dailyTableVariables]
  );

  const forecastBusy = forecast?.status === "queued" || forecast?.status === "running";
  const activePreset = presets.find((p) => sameMultipliers(p.multipliers, multipliers));

  const canSaveScenario =
    Boolean(activeResult) &&
    !isBaselineSelected &&
    !evaluatingScenario &&
    savedScenarios.length < 2;

  const handleSaveScenario = () => {
    if (!activeResult) return;
    const label =
      activePreset?.label ??
      `Scenario ${savedScenarios.length + 1} (${signedEur(activeResult.delta_spend)} spend)`;
    setSavedScenarios((prev) => [
      ...prev.slice(-1),
      { id: activeScenarioId ?? `saved_${Date.now()}`, name: label, result: activeResult },
    ]);
  };

  const setOneMultiplier = (slug: string, m: number) => {
    const clamped = Math.max(0, Math.min(4, Math.round(m * 1000) / 1000));
    setSpendOverrides((prev) => {
      if (!(slug in prev)) return prev;
      const next = { ...prev };
      delete next[slug];
      return next;
    });
    setMultipliers((prev) => ({ ...prev, [slug]: clamped }));
  };

  const setChannelSpend = (slug: string, rawPeriodSpend: number, baseSpend: number) => {
    const target = Math.max(0, Math.round(rawPeriodSpend));
    if (baseSpend > 0) {
      if (Math.abs(target - baseSpend) < 0.5) {
        setSpendOverrides((prev) => {
          const next = { ...prev };
          delete next[slug];
          return next;
        });
        setMultipliers((prev) => {
          const next = { ...prev };
          delete next[slug];
          return next;
        });
        return;
      }
      setSpendOverrides((prev) => ({ ...prev, [slug]: target }));
      setMultipliers((prev) => ({ ...prev, [slug]: Math.round((target / baseSpend) * 10000) / 10000 }));
    } else {
      setSpendOverrides((prev) => {
        const next = { ...prev };
        if (target <= 0) {
          delete next[slug];
        } else {
          next[slug] = target;
        }
        return next;
      });
    }
  };

  const resetSingleVariable = (slug: string) => {
    setSpendOverrides((prev) => {
      if (!(slug in prev)) return prev;
      const next = { ...prev };
      delete next[slug];
      return next;
    });
    setMultipliers((prev) => {
      if (!(slug in prev)) return prev;
      const next = { ...prev };
      delete next[slug];
      return next;
    });
  };

  const adjustSingleVariable = (
    slug: string,
    deltaMultiplier: number,
    baseSpend: number,
    fallbackRef: number
  ) => {
    if (baseSpend <= 0) {
      const currentSpend = spendOverrides[slug] ?? 0;
      const nextSpend = Math.max(0, Math.round(currentSpend + deltaMultiplier * fallbackRef));
      setChannelSpend(slug, nextSpend, 0);
      return;
    }
    if (spendOverrides[slug] !== undefined) {
      const nextSpend = Math.max(0, Math.round(spendOverrides[slug] + deltaMultiplier * baseSpend));
      setChannelSpend(slug, nextSpend, baseSpend);
      return;
    }
    const current = multipliers[slug] ?? 1;
    setOneMultiplier(slug, current + deltaMultiplier);
  };

  const effectiveHorizon = Math.max(1, Math.min(horizonDays, horizonMax));
  const perDaySuffix = spendGranularity === "daily" ? "/d" : "";
  const toDisplay = (periodSpend: number) => toDisplayEuro(periodSpend, spendGranularity, effectiveHorizon);

  const leverSlugs = useMemo(() => levers.map((l) => l.slug), [levers]);
  const baseMediaSpendPool = useMemo(
    () => levers.reduce((acc, ch) => acc + (periodBaseBySlug[ch.slug] ?? 0), 0),
    [levers, periodBaseBySlug]
  );
  const scenarioMediaSpendPool = useMemo(
    () =>
      levers.reduce((acc, ch) => {
        const base = periodBaseBySlug[ch.slug] ?? 0;
        const ov = spendOverrides[ch.slug];
        return acc + (ov !== undefined ? ov : base * (multipliers[ch.slug] ?? 1));
      }, 0),
    [levers, periodBaseBySlug, multipliers, spendOverrides]
  );
  const totalMediaDeltaPct =
    baseMediaSpendPool > 0 ? ((scenarioMediaSpendPool - baseMediaSpendPool) / baseMediaSpendPool) * 100 : 0;

  const handleTotalMediaPoolChange = (periodTarget: number) => {
    setSpendOverrides({});
    setMultipliers(scaleMultipliersToTotal(leverSlugs, periodBaseBySlug, multipliers, periodTarget));
  };

  const renderVariableRow = (v: VariableContributionRow) => {
    const badge = categoryBadgeMeta(v.category);
    const canEdit = v.editable || (v.category === "control_channel" && unlockControls);
    const isSelected = selectedVariable === v.key;
    const shareAbs = Math.min(100, Math.abs(v.scenario_share_pct));
    const barColor = variableColor(v.key, v.category);
    const baseSpend = periodBaseBySlug[v.key] ?? v.base_spend ?? 0;
    const noSpend = baseSpend <= 0;
    const fallbackPeriodRef =
      v.key === "catalog" || printSlugs.includes(v.key)
        ? 1_300_000
        : Math.max(100_000, Math.round((baseMediaSpendPool || 500_000) / Math.max(1, levers.length)));
    const overrideSpend = spendOverrides[v.key];
    const mult = multipliers[v.key] ?? v.multiplier ?? 1;
    const scenSpend = canEdit
      ? overrideSpend !== undefined
        ? overrideSpend
        : baseSpend * mult
      : (v.scenario_spend ?? null);
    const pct = baseSpend > 0 ? (((scenSpend ?? baseSpend) / baseSpend) - 1) * 100 : 0;
    const moved =
      overrideSpend !== undefined
        ? Math.abs(overrideSpend - baseSpend) >= 0.5
        : Math.abs(mult - 1) > 1e-4;
    const activeDays = planResult ? channelActiveDays(planResult.plan_daily?.[v.key], effectiveHorizon) : 0;
    const maxPeriodRef = Math.max(
      baseSpend * 2,
      noSpend ? fallbackPeriodRef * 2 : 0,
      scenSpend ?? 0
    );
    const displayMax = Math.max(100, Math.round(toDisplay(maxPeriodRef)));
    const spendDiff = scenSpend != null ? scenSpend - baseSpend : 0;
    const useEuroControl = inputMode === "eur" || noSpend;

    return (
      <TableRow
        key={v.key}
        hover
        selected={isSelected}
        onClick={() => setSelectedVariable(isSelected ? null : v.key)}
        sx={{
          cursor: "pointer",
          bgcolor: isSelected ? alpha(PALETTE.brandPrimary, 0.06) : undefined,
        }}
      >
        {/* 1. Variable & Role (Sticky first column per responsive-table guidance) */}
        <TableCell
          component="th"
          scope="row"
          sx={{
            fontWeight: 700,
            whiteSpace: "nowrap",
            position: "sticky",
            insetInlineStart: 0,
            zIndex: 1,
            bgcolor: isSelected ? PALETTE.bgAccentTint : "#FFFFFF",
          }}
        >
          <Stack direction="row" spacing={1} alignItems="center">
            <Box
              sx={{
                width: 9,
                height: 9,
                borderRadius: "50%",
                bgcolor: barColor,
                flexShrink: 0,
              }}
            />
            <Box>
              <Stack direction="row" spacing={0.75} alignItems="center">
                <Typography variant="body2" sx={{ fontWeight: 700, fontSize: "0.84rem", lineHeight: 1.2 }}>
                  {v.label}
                </Typography>
                <Box
                  component="span"
                  sx={{
                    px: 0.65,
                    py: 0.1,
                    fontSize: "0.65rem",
                    fontWeight: 700,
                    borderRadius: 1,
                    bgcolor: badge.bg,
                    color: badge.color,
                    border: `1px solid ${badge.border}`,
                    lineHeight: 1.3,
                  }}
                >
                  {badge.label}
                </Box>
              </Stack>
              {v.base_spend != null && activeDays > 0 && activeDays < effectiveHorizon && (
                <Typography variant="caption" sx={{ color: PALETTE.inkSecondary, fontSize: "0.68rem", display: "block" }}>
                  Active on {activeDays} of {effectiveHorizon} days
                </Typography>
              )}
            </Box>
          </Stack>
        </TableCell>

        {/* 2. Interactive Input Slider + Numeric Input */}
        <TableCell onClick={(e) => e.stopPropagation()} sx={{ py: 0.75, minWidth: 290 }}>
          {canEdit ? (
            <Stack direction="row" spacing={1} alignItems="center">
              <Button
                size="small"
                variant="outlined"
                aria-label={`Decrease ${v.label} by 10 percent`}
                disabled={noSpend ? (scenSpend ?? 0) <= 0 : (scenSpend ?? baseSpend) <= 0}
                onClick={() => adjustSingleVariable(v.key, -0.1, baseSpend, fallbackPeriodRef)}
                sx={{ minWidth: 32, px: 0.5, py: 0.15, fontSize: "0.7rem", ...NUMERIC }}
              >
                −10%
              </Button>

              <Box sx={{ flexGrow: 1, minWidth: 90, px: 0.5 }}>
                {!useEuroControl ? (
                  <Slider
                    size="small"
                    color="secondary"
                    min={-100}
                    max={100}
                    step={5}
                    value={Math.max(-100, Math.min(100, pct))}
                    marks={[{ value: 0 }]}
                    valueLabelDisplay="auto"
                    valueLabelFormat={(val) => signedPct(val, 0)}
                    aria-label={`${v.label} percentage change slider`}
                    onChange={(_, val) => setOneMultiplier(v.key, 1 + (val as number) / 100)}
                    sx={{ py: 0.5 }}
                  />
                ) : (
                  <Slider
                    size="small"
                    color="secondary"
                    min={0}
                    max={displayMax}
                    step={Math.max(10, Math.round(toDisplay((baseSpend > 0 ? baseSpend : fallbackPeriodRef) / 40)))}
                    value={Math.max(0, Math.min(displayMax, Math.round(toDisplay(scenSpend ?? 0))))}
                    marks={noSpend ? undefined : [{ value: Math.round(toDisplay(baseSpend)) }]}
                    valueLabelDisplay="auto"
                    valueLabelFormat={(val) => `${eurCompact(val as number)}${perDaySuffix}`}
                    aria-label={`${v.label} euro budget slider`}
                    onChange={(_, val) =>
                      setChannelSpend(
                        v.key,
                        fromDisplayEuro(val as number, spendGranularity, effectiveHorizon),
                        baseSpend
                      )
                    }
                    sx={{ py: 0.5 }}
                  />
                )}
              </Box>

              <Button
                size="small"
                variant="outlined"
                aria-label={`Increase ${v.label} by 10 percent`}
                onClick={() => adjustSingleVariable(v.key, 0.1, baseSpend, fallbackPeriodRef)}
                sx={{ minWidth: 32, px: 0.5, py: 0.15, fontSize: "0.7rem", ...NUMERIC }}
              >
                +10%
              </Button>

              {!useEuroControl ? (
                <InlinePctInput
                  value={pct}
                  name={`matrix-pct-${v.key}`}
                  label={`${v.label} budget change in percent`}
                  onCommit={(val) => setOneMultiplier(v.key, 1 + val / 100)}
                />
              ) : (
                <InlineEuroInput
                  value={toDisplay(scenSpend ?? 0)}
                  name={`matrix-eur-${v.key}`}
                  label={`${v.label} budget in euros`}
                  suffix={perDaySuffix || undefined}
                  onCommit={(entered) =>
                    setChannelSpend(
                      v.key,
                      fromDisplayEuro(entered, spendGranularity, effectiveHorizon),
                      baseSpend
                    )
                  }
                />
              )}

              {moved && (
                <Tooltip title={`Reset ${v.label} to planned spend`}>
                  <IconButton
                    size="small"
                    aria-label={`Reset ${v.label}`}
                    onClick={() => resetSingleVariable(v.key)}
                    sx={{ p: 0.25 }}
                  >
                    <RestartAltIcon sx={{ fontSize: 15 }} />
                  </IconButton>
                </Tooltip>
              )}
              {v.category === "control_channel" && unlockControls && (
                <Tooltip title="Lock control channel back to planned run-rate">
                  <IconButton
                    size="small"
                    aria-label={`Lock ${v.label}`}
                    onClick={() => {
                      resetSingleVariable(v.key);
                      setUnlockControls(false);
                    }}
                    sx={{ p: 0.25 }}
                  >
                    <LockOpenOutlinedIcon sx={{ fontSize: 15, color: PALETTE.brandPrimary }} />
                  </IconButton>
                </Tooltip>
              )}
            </Stack>
          ) : v.category === "control_channel" ? (
            <Stack direction="row" spacing={1} alignItems="center">
              <LockOutlinedIcon sx={{ fontSize: 14, color: PALETTE.inkMuted }} />
              <Typography variant="caption" sx={{ color: PALETTE.inkSecondary }}>
                Commission control (fixed)
              </Typography>
              <Button
                size="small"
                variant="outlined"
                startIcon={<LockOpenOutlinedIcon sx={{ fontSize: 13 }} />}
                onClick={() => setUnlockControls(true)}
                sx={{ textTransform: "none", fontSize: "0.7rem", py: 0.1, px: 0.75 }}
              >
                Unlock
              </Button>
            </Stack>
          ) : (
            <Typography variant="caption" sx={{ color: PALETTE.inkMuted }}>
              Structural / calendar feature (from dates)
            </Typography>
          )}
        </TableCell>

        {/* 3. Single Non-Redundant Spend Column (Shows Plan Delta ONLY when modified) */}
        <TableCell align="right" sx={{ ...NUMERIC, whiteSpace: "nowrap" }}>
          {scenSpend != null ? (
            <Box>
              <Box
                component="span"
                sx={{
                  display: "inline-block",
                  minWidth: 108,
                  px: 0.9,
                  py: 0.35,
                  borderRadius: 1,
                  border: `1px solid ${PALETTE.borderSubtle}`,
                  bgcolor: PALETTE.bgCanvas,
                  fontWeight: 700,
                  fontSize: "0.83rem",
                  textAlign: "right",
                  ...NUMERIC,
                }}
              >
                {eur(toDisplay(scenSpend))}{perDaySuffix}
              </Box>
              {Math.abs(spendDiff) >= 10 && (
                <Typography
                  variant="caption"
                  sx={{
                    ...NUMERIC,
                    color: spendDiff > 0 ? PALETTE.semanticGood : PALETTE.semanticBad,
                    display: "block",
                    fontWeight: 600,
                    mt: 0.25,
                  }}
                >
                  {signedEur(spendDiff)} vs {eur(toDisplay(baseSpend))} plan
                </Typography>
              )}
            </Box>
          ) : (
            <Typography variant="caption" sx={{ color: PALETTE.inkMuted }}>
              —
            </Typography>
          )}
        </TableCell>

        {/* 4. Shapley Forecast Contribution as an explicit Forecast Sales (€) Cell Value */}
        <TableCell align="right" sx={{ ...NUMERIC, whiteSpace: "nowrap" }}>
          <Box
            component="span"
            sx={{
              display: "inline-block",
              minWidth: 118,
              px: 0.9,
              py: 0.35,
              borderRadius: 1,
              border: `1px solid ${v.scenario_contribution < 0 ? alpha(PALETTE.semanticBad, 0.35) : alpha(PALETTE.semanticGood, 0.4)}`,
              bgcolor: v.scenario_contribution < 0 ? alpha(PALETTE.semanticBad, 0.05) : PALETTE.bgGoodTint,
              fontWeight: 800,
              fontSize: "0.84rem",
              textAlign: "right",
              color: v.scenario_contribution < 0 ? PALETTE.semanticBad : PALETTE.semanticGood,
              ...NUMERIC,
            }}
          >
            {v.scenario_contribution < 0 ? signedEur(v.scenario_contribution) : eur(v.scenario_contribution)}
          </Box>
          {v.scenario_ci_lo != null && v.scenario_ci_hi != null && (
            <Typography variant="caption" sx={{ ...NUMERIC, color: PALETTE.inkSecondary, display: "block", mt: 0.25 }}>
              90%: {v.scenario_ci_lo < 0 ? signedEur(v.scenario_ci_lo) : eur(v.scenario_ci_lo)} –{" "}
              {v.scenario_ci_hi < 0 ? signedEur(v.scenario_ci_hi) : eur(v.scenario_ci_hi)}
            </Typography>
          )}
        </TableCell>

        {/* 5. Share of Forecast + Scenario Δ Sales Badge (only shown when modified) */}
        <TableCell sx={{ minWidth: 165 }}>
          <Stack spacing={0.35}>
            <Stack direction="row" justifyContent="space-between" alignItems="center" spacing={1}>
              <Typography variant="caption" sx={{ ...NUMERIC, fontWeight: 700 }}>
                {v.scenario_share_pct.toFixed(1)}%
              </Typography>
              {Math.abs(v.delta_contribution) >= 10 && (
                <Typography
                  variant="caption"
                  sx={{
                    ...NUMERIC,
                    fontWeight: 750,
                    color: v.delta_contribution >= 0 ? PALETTE.semanticGood : PALETTE.semanticBad,
                  }}
                >
                  Δ {signedEur(v.delta_contribution)}
                </Typography>
              )}
            </Stack>
            <Box
              sx={{
                width: "100%",
                height: 6,
                borderRadius: 1,
                bgcolor: PALETTE.bgSubtle,
                overflow: "hidden",
              }}
            >
              <Box
                sx={{
                  width: `${shareAbs}%`,
                  height: "100%",
                  bgcolor: v.scenario_contribution < 0 ? PALETTE.semanticBad : barColor,
                  borderRadius: 1,
                }}
              />
            </Box>
          </Stack>
        </TableCell>

        {/* 6. Efficiency (ROI / mROI) */}
        <TableCell align="right" sx={{ ...NUMERIC, whiteSpace: "nowrap" }}>
          {v.roi != null && v.category === "media" ? (
            <Box>
              <Typography variant="body2" sx={{ ...NUMERIC, fontWeight: 700, fontSize: "0.82rem" }}>
                {v.roi.toFixed(2)}×
              </Typography>
              {v.mroi != null && (
                <Typography variant="caption" sx={{ ...NUMERIC, color: PALETTE.inkSecondary, display: "block" }}>
                  mROI {v.mroi.toFixed(2)}×
                </Typography>
              )}
            </Box>
          ) : (
            <Typography variant="caption" sx={{ color: PALETTE.inkMuted }}>
              —
            </Typography>
          )}
        </TableCell>
      </TableRow>
    );
  };

  return (
    <Box>
      <PageHeader
        description={FORECAST_EXPLAIN_SCOPE.sectionSubtitle}
        actions={
          <Stack direction="row" spacing={1.25} alignItems="center" flexWrap="wrap" useFlexGap sx={{ minWidth: 0, maxWidth: "100%" }}>
            {forecasts.length > 0 && (
              <FormControl size="small" sx={{ minWidth: { xs: 0, sm: 220 }, maxWidth: "100%" }}>
                <InputLabel id="header-forecast-plan-label">Baseline plan</InputLabel>
                <Select
                  labelId="header-forecast-plan-label"
                  label="Baseline plan"
                  value={forecastId ?? ""}
                  onChange={(e) => setForecastId(e.target.value as string)}
                  sx={{ bgcolor: "#FFFFFF", fontSize: "0.84rem" }}
                >
                  {forecasts.map((f) => (
                    <MenuItem key={f.id} value={f.id}>
                      <Stack direction="row" spacing={1} alignItems="center" sx={{ width: "100%", minWidth: 0 }}>
                        <Box sx={{ flexGrow: 1, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis" }}>
                          {f.name}
                        </Box>
                        <Chip
                          size="small"
                          label={RUN_STATUS_LABEL[f.status] ?? f.status}
                          color={runStatusColor(f.status)}
                          sx={{ height: 20, fontSize: "0.68rem" }}
                        />
                      </Stack>
                    </MenuItem>
                  ))}
                </Select>
              </FormControl>
            )}
            {presets.length > 0 && (
              <FormControl size="small" sx={{ minWidth: { xs: 0, sm: 195 }, maxWidth: "100%" }}>
                <InputLabel id="header-quick-scenario-label">Quick scenario</InputLabel>
                <Select
                  labelId="header-quick-scenario-label"
                  label="Quick scenario"
                  value={activePreset?.id ?? (!isBaselineSelected ? "custom" : "plan")}
                  onChange={(e) => {
                    const val = e.target.value as string;
                    if (val === "plan") {
                      setMultipliers({});
                      setSpendOverrides({});
                      setHorizonDays(horizonMax);
                    } else {
                      const picked = presets.find((p) => p.id === val);
                      if (picked) {
                        setSpendOverrides({});
                        setMultipliers(picked.multipliers);
                      }
                    }
                  }}
                  sx={{ bgcolor: "#FFFFFF", fontSize: "0.84rem" }}
                >
                  <MenuItem value="plan">Plan as submitted (Baseline)</MenuItem>
                  {!isBaselineSelected && !activePreset && (
                    <MenuItem value="custom" disabled>
                      Custom input edits ({horizonDays}d)
                    </MenuItem>
                  )}
                  {presets.map((p) => (
                    <MenuItem key={p.id} value={p.id}>
                      {p.label}
                    </MenuItem>
                  ))}
                </Select>
              </FormControl>
            )}
          </Stack>
        }
      />

      {forecasts.length === 0 && (
        <Alert severity="info" sx={{ mb: 3 }}>
          No forecast yet. The 42-day example-plan forecast appears automatically when the Meridian stages of this run finish;
          you can also enter or upload a daily budget plan below.
        </Alert>
      )}

      {forecastBusy && (
        <Paper sx={{ p: 2, mb: 3, border: `1px solid ${PALETTE.borderSubtle}` }}>
          <Typography variant="body2" sx={{ mb: 1, fontWeight: 600 }}>
            Refitting Meridian on history plus your submitted plan window
            {typeof forecast?.queue_position === "number" && forecast.queue_position > 0
              ? ` (queue position ${forecast.queue_position})`
              : ""}
            …
          </Typography>
          <LinearProgress color="secondary" aria-label="Forecast refit in progress" />
        </Paper>
      )}

      {forecast?.status === "failed" && (
        <Alert severity="error" sx={{ mb: 3 }}>
          {forecast.error || "The forecast failed."}
        </Alert>
      )}

      {scenarioError && (
        <Alert severity="warning" sx={{ mb: 2.5 }} onClose={() => setScenarioError(null)}>
          {scenarioError}
        </Alert>
      )}

      {/* Main Layout: Content + Sticky Right OnThisPage Rail */}
      <Box
        sx={{
          display: "grid",
          gridTemplateColumns: { xs: "minmax(0, 1fr)", xl: "minmax(0, 1fr) 216px" },
          gap: 3,
          alignItems: "start",
        }}
      >
        <Stack spacing={3} sx={{ minWidth: 0 }}>
          {/* =========================================================================================
              EXECUTIVE KPI STRIP (4 Non-Redundant Headline Cards)
             ========================================================================================= */}
          {activeResult && (
            <Box>
              {/* The same KPI strip as every other page; each tile is label, value and one context line */}
              <KpiStrip
                label="Forecast headline"
                items={[
                  {
                    label: isBaselineSelected ? `Forecast sales (${activeResult.horizon_days} days)` : `Scenario forecast (${activeResult.horizon_days} days)`,
                    value: eur(activeResult.scenario_total_sales),
                    delta: [
                      activeResult.prediction_total_lo != null && activeResult.prediction_total_hi != null
                        ? `90% PI ${eurCompact(activeResult.prediction_total_lo)} – ${eurCompact(activeResult.prediction_total_hi)}`
                        : null,
                      Math.abs(activeResult.delta_sales) >= 10
                        ? `${signedEur(activeResult.delta_sales)} (${signedPct(activeResult.delta_sales_pct, 2)}) vs baseline plan`
                        : lastYear != null && lastYear > 0
                          ? `${signedPct((100 * (activeResult.scenario_total_sales - lastYear)) / lastYear, 1)} vs same weekdays last year`
                          : `Ø ${eur(activeResult.scenario_total_sales / Math.max(1, activeResult.horizon_days))}/day`,
                    ]
                      .filter(Boolean)
                      .join(" · "),
                    tone: Math.abs(activeResult.delta_sales) >= 10 ? (activeResult.delta_sales >= 0 ? "good" : "bad") : "neutral",
                  },
                  {
                    label: "Paid media contribution",
                    value: eur(mediaTotalSales),
                    delta: [
                      `${((100 * mediaTotalSales) / Math.max(1, activeResult.scenario_total_sales)).toFixed(1)}% of the forecast`,
                      mediaRoi != null ? `ROI ${mediaRoi.toFixed(2)}×` : null,
                      Math.abs(mediaTotalSales - mediaBaseSales) >= 10 ? `Δ ${signedEur(mediaTotalSales - mediaBaseSales)}` : null,
                    ]
                      .filter(Boolean)
                      .join(" · "),
                  },
                  {
                    label: "Baseline, calendar & controls",
                    value: eur(baselineControlsSales),
                    delta: `${((100 * baselineControlsSales) / Math.max(1, activeResult.scenario_total_sales)).toFixed(1)}% of the forecast`,
                    help: ["Baseline, calendar, trend & season", ...controlChannelNames].join(" · "),
                  },
                  {
                    label: "Planned spend",
                    value: eur(activeResult.scenario_total_spend),
                    delta:
                      Math.abs(activeResult.delta_spend) >= 10
                        ? `Δ spend ${signedEur(activeResult.delta_spend)}${activeResult.marginal_roas != null ? ` · mROAS ${activeResult.marginal_roas.toFixed(2)}×` : ""}`
                        : `Paid media ${eurCompact(mediaTotalSpend)} · Ø ${eurCompact(activeResult.scenario_total_spend / Math.max(1, activeResult.horizon_days))}/day`,
                  },
                ]}
              />

              {savedScenarios.length > 0 && (
                <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap" useFlexGap sx={{ mt: 1.5 }}>
                  <Typography variant="caption" sx={{ fontWeight: 700, color: PALETTE.inkSecondary }}>
                    Saved comparisons:
                  </Typography>
                  {savedScenarios.map((sc, i) => (
                    <Chip
                      key={sc.id}
                      size="small"
                      label={`${sc.name}: ${eurCompact(sc.result.scenario_total_sales)} (${signedEur(sc.result.delta_sales)})`}
                      onDelete={() => setSavedScenarios((prev) => prev.filter((x) => x.id !== sc.id))}
                      deleteIcon={<DeleteOutlineIcon />}
                      sx={{
                        fontWeight: 700,
                        ...NUMERIC,
                        borderLeft: `4px solid ${PLANNER_COLORS.saved[i % PLANNER_COLORS.saved.length]}`,
                      }}
                    />
                  ))}
                </Stack>
              )}
            </Box>
          )}

          {/* =========================================================================================
              SECTION 1: LIVE VARIABLE INPUT -> FORECAST ATTRIBUTION MATRIX (Single Unified Table)
             ========================================================================================= */}
          <Card
            id="sec-setup"
            variant="outlined"
            sx={{
              borderRadius: 1,
              borderColor: PALETTE.borderSubtle,
              boxShadow: "0 1px 3px rgba(15,23,42,0.04)",
              scrollMarginTop: 84,
            }}
          >
            <Box
              sx={{
                px: 2.5,
                py: 1.75,
                bgcolor: PALETTE.bgCanvas,
                borderBottom: setupExpanded ? `1px solid ${PALETTE.borderSubtle}` : "none",
                display: "flex",
                alignItems: "center",
                justifyContent: "space-between",
                flexWrap: "wrap",
                gap: 1.5,
              }}
            >
              <Stack direction="row" spacing={1.25} alignItems="center" flexWrap="wrap" useFlexGap>
                <Chip
                  size="small"
                  label="1 · Live Variable Input → Forecast Attribution Matrix"
                  sx={{
                    bgcolor: PALETTE.brandNavy,
                    color: "#FFFFFF",
                    fontWeight: 700,
                    fontSize: "0.73rem",
                  }}
                />
                {planDates.length > 0 && (
                  <Typography variant="body2" sx={{ fontWeight: 650, color: PALETTE.inkPrimary, ...NUMERIC }}>
                    {shortDate(planDates[0])} – {shortDate(planDates[planDates.length - 1])} · {activeResult?.horizon_days ?? horizonDays} days
                  </Typography>
                )}
                {evaluatingScenario && (
                  <Chip
                    size="small"
                    color="secondary"
                    label="Updating posterior forecast…"
                    sx={{ fontSize: "0.7rem", height: 22, fontWeight: 700 }}
                  />
                )}
              </Stack>

              <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap" useFlexGap>
                {activeResult && (
                  <Button
                    size="small"
                    variant="outlined"
                    startIcon={<DownloadIcon fontSize="small" />}
                    onClick={() =>
                      downloadVariablesCsv(
                        variableContributions,
                        `forecast_variable_attributions_${activeResult.start_date ?? "plan"}.csv`
                      )
                    }
                    sx={{ textTransform: "none", fontWeight: 600 }}
                  >
                    Export CSV
                  </Button>
                )}
                <Button
                  size="small"
                  variant="outlined"
                  startIcon={<BookmarkAddOutlinedIcon fontSize="small" />}
                  onClick={handleSaveScenario}
                  disabled={!canSaveScenario}
                  sx={{ textTransform: "none", fontWeight: 600 }}
                >
                  Save to compare
                </Button>
                {!isBaselineSelected && (
                  <Button
                    size="small"
                    startIcon={<RestartAltIcon fontSize="small" />}
                    onClick={() => {
                      setMultipliers({});
                      setSpendOverrides({});
                      setHorizonDays(horizonMax);
                    }}
                    sx={{ textTransform: "none", fontWeight: 600 }}
                  >
                    Reset plan
                  </Button>
                )}
                <IconButton
                  size="small"
                  aria-label={setupExpanded ? "Collapse variable matrix" : "Expand variable matrix"}
                  onClick={() => setSetupExpanded((p) => !p)}
                >
                  {setupExpanded ? <ExpandLessIcon /> : <ExpandMoreIcon />}
                </IconButton>
              </Stack>
            </Box>

            <Collapse in={setupExpanded}>
              <CardContent sx={{ p: { xs: 2, md: 2.5 } }}>
                {/* Unified Toolbar: Mode toggles, Category Filter, Horizon slider */}
                <Stack
                  direction={{ xs: "column", lg: "row" }}
                  justifyContent="space-between"
                  alignItems={{ xs: "flex-start", lg: "center" }}
                  spacing={1.5}
                  sx={{ mb: 2 }}
                >
                  <Stack direction="row" spacing={1.25} alignItems="center" flexWrap="wrap" useFlexGap>
                    <ToggleButtonGroup
                      size="small"
                      exclusive
                      value={editorTab}
                      onChange={(_, v: "levers" | "sales_cells" | "spreadsheet" | null) => v && setEditorTab(v)}
                      aria-label="Input editing mode"
                    >
                      <ToggleButton value="levers" sx={{ textTransform: "none", px: 1.4, fontWeight: 700 }}>
                        <TuneIcon sx={{ fontSize: 16, mr: 0.75 }} />
                        Variable Attribution Matrix
                      </ToggleButton>
                      <ToggleButton value="sales_cells" sx={{ textTransform: "none", px: 1.4, fontWeight: 700 }}>
                        <TableChartOutlinedIcon sx={{ fontSize: 16, mr: 0.75 }} />
                        Daily Forecast Sales Cells (€)
                      </ToggleButton>
                      <ToggleButton value="spreadsheet" sx={{ textTransform: "none", px: 1.4, fontWeight: 700 }}>
                        <TableChartOutlinedIcon sx={{ fontSize: 16, mr: 0.75 }} />
                        Daily Budget Spend Cells (€)
                      </ToggleButton>
                    </ToggleButtonGroup>

                    {editorTab === "levers" && (
                      <>
                        <ToggleButtonGroup
                          size="small"
                          exclusive
                          value={categoryFilter}
                          onChange={(_, v: "all" | "media" | "baseline_controls" | null) => v && setCategoryFilter(v)}
                          aria-label="Filter variables by category"
                        >
                          <ToggleButton value="all" sx={{ textTransform: "none", px: 1.15, py: 0.25, fontWeight: 600 }}>
                            All ({visibleMediaVars.length + visibleStructuralVars.length})
                          </ToggleButton>
                          <ToggleButton value="media" sx={{ textTransform: "none", px: 1.15, py: 0.25, fontWeight: 600 }}>
                            Paid Media ({visibleMediaVars.length})
                          </ToggleButton>
                          <ToggleButton value="baseline_controls" sx={{ textTransform: "none", px: 1.15, py: 0.25, fontWeight: 600 }}>
                            Baseline & Controls ({visibleStructuralVars.length})
                          </ToggleButton>
                        </ToggleButtonGroup>

                        <ToggleButtonGroup
                          size="small"
                          exclusive
                          value={inputMode}
                          onChange={(_, v: BudgetInputMode | null) => v && setInputMode(v)}
                          aria-label="Enter budgets as"
                        >
                          <ToggleButton value="pct" sx={{ textTransform: "none", px: 1.15, py: 0.25, fontWeight: 600 }}>
                            % change
                          </ToggleButton>
                          <ToggleButton value="eur" sx={{ textTransform: "none", px: 1.15, py: 0.25, fontWeight: 600 }}>
                            € amount
                          </ToggleButton>
                        </ToggleButtonGroup>

                        {inputMode === "eur" && (
                          <ToggleButtonGroup
                            size="small"
                            exclusive
                            value={spendGranularity}
                            onChange={(_, v: SpendGranularity | null) => v && setSpendGranularity(v)}
                            aria-label="Euro amounts per"
                          >
                            <ToggleButton value="period" sx={{ textTransform: "none", px: 1.15, py: 0.25, fontWeight: 600 }}>
                              Period total
                            </ToggleButton>
                            <ToggleButton value="daily" sx={{ textTransform: "none", px: 1.15, py: 0.25, fontWeight: 600 }}>
                              Per day
                            </ToggleButton>
                          </ToggleButtonGroup>
                        )}
                      </>
                    )}
                    {editorTab !== "levers" && (
                      <ExplainButton
                        kind="table"
                        title={editorTab === "sales_cells" ? "Daily forecast sales cells (€)" : "Daily budget spend cells (€)"}
                      />
                    )}
                  </Stack>

                  {editorTab === "levers" && (
                    <Stack direction="row" spacing={1.5} alignItems="center" flexWrap="wrap" useFlexGap>
                      {inactiveCount > 0 && (
                        <Button
                          size="small"
                          variant="text"
                          startIcon={<VisibilityOutlinedIcon sx={{ fontSize: 15 }} />}
                          onClick={() => setShowZeroActivityVars((p) => !p)}
                          sx={{ textTransform: "none", fontSize: "0.75rem", color: PALETTE.inkSecondary }}
                        >
                          {showZeroActivityVars ? `Hide ${inactiveCount} €0 variables` : `Show ${inactiveCount} €0 variables`}
                        </Button>
                      )}
                      <Stack direction="row" spacing={1.25} alignItems="center" sx={{ minWidth: 210 }}>
                        <Typography variant="caption" sx={{ fontWeight: 700, color: PALETTE.inkSecondary, whiteSpace: "nowrap" }}>
                          Horizon: {effectiveHorizon} / {horizonMax}d
                        </Typography>
                        {horizonMax > 1 && (
                          <Slider
                            size="small"
                            color="secondary"
                            min={1}
                            max={horizonMax}
                            step={1}
                            value={effectiveHorizon}
                            valueLabelDisplay="auto"
                            valueLabelFormat={(v) => `${v}d`}
                            aria-label="Forecast horizon in days"
                            onChange={(_, v) => setHorizonDays(v as number)}
                            sx={{ width: 105 }}
                          />
                        )}
                      </Stack>
                      {selectedVariable && (
                        <Chip
                          size="small"
                          color="primary"
                          label={`Filtered: ${variableContributions.find((v) => v.key === selectedVariable)?.label ?? selectedVariable}`}
                          onDelete={() => setSelectedVariable(null)}
                        />
                      )}
                      {planResult && activeResult && (
                        <ExplainButton
                          kind="table"
                          title="Live variable input → forecast attribution matrix"
                          subtitle={
                            planDates.length > 0
                              ? `${shortDate(planDates[0])} – ${shortDate(planDates[planDates.length - 1])} · ${activeResult.horizon_days ?? horizonDays} days`
                              : null
                          }
                        />
                      )}
                    </Stack>
                  )}
                </Stack>

                {evaluatingScenario && (
                  <LinearProgress
                    color="secondary"
                    aria-label="Evaluating scenario on fitted Meridian model"
                    sx={{ mb: 1.5, borderRadius: 1 }}
                  />
                )}

                {editorTab === "levers" && planResult && activeResult ? (
                  <TableContainer
                    role="region"
                    aria-label="Live Variable Input to Forecast Attribution Matrix"
                    tabIndex={0}
                    sx={{
                      border: `1px solid ${PALETTE.borderSubtle}`,
                      borderRadius: 1,
                      bgcolor: "#FFFFFF",
                      overflow: "auto",
                      scrollbarGutter: "stable",
                      overscrollBehavior: "contain",
                    }}
                  >
                    <Table size="small" stickyHeader>
                      <TableHead>
                        <TableRow>
                          <TableCell
                            scope="col"
                            sx={{
                              fontWeight: 700,
                              bgcolor: PALETTE.bgCanvas,
                              position: "sticky",
                              insetInlineStart: 0,
                              zIndex: 3,
                            }}
                          >
                            Variable & Role
                          </TableCell>
                          <TableCell scope="col" sx={{ fontWeight: 700, bgcolor: PALETTE.bgCanvas, minWidth: 290 }}>
                            Interactive Input ({inputMode === "pct" ? "% Change vs Plan" : `€ ${spendGranularity === "daily" ? "Per Day" : "Period Total"}`})
                          </TableCell>
                          <TableCell scope="col" align="right" sx={{ fontWeight: 700, bgcolor: PALETTE.bgCanvas }}>
                            Input Spend (€)
                          </TableCell>
                          <TableCell scope="col" align="right" sx={{ fontWeight: 700, bgcolor: PALETTE.bgCanvas }}>
                            Forecast Sales (€)
                          </TableCell>
                          <TableCell scope="col" sx={{ fontWeight: 700, bgcolor: PALETTE.bgCanvas, minWidth: 165 }}>
                            Share of Forecast
                          </TableCell>
                          <TableCell scope="col" align="right" sx={{ fontWeight: 700, bgcolor: PALETTE.bgCanvas }}>
                            Efficiency (ROI)
                          </TableCell>
                        </TableRow>
                      </TableHead>
                      <TableBody>
                        {/* Group 1: Paid Media Levers (Interactive Inputs at Top) */}
                        {categoryFilter !== "baseline_controls" && (
                          <>
                            {visibleMediaVars.map(renderVariableRow)}

                            {/* Total Paid Media Budget Proportional Scaling Row */}
                            <TableRow
                              sx={{
                                bgcolor: alpha(PALETTE.brandPrimary, 0.07),
                                "& td, & th": { borderTop: `1.5px solid ${PALETTE.borderSubtle}`, borderBottom: `1.5px solid ${PALETTE.borderSubtle}` },
                              }}
                            >
                              <TableCell
                                component="th"
                                scope="row"
                                sx={{
                                  fontWeight: 800,
                                  position: "sticky",
                                  insetInlineStart: 0,
                                  zIndex: 1,
                                  bgcolor: PALETTE.bgAccentTint,
                                }}
                              >
                                <Stack direction="row" spacing={1} alignItems="center">
                                  <Typography variant="body2" sx={{ fontWeight: 800, fontSize: "0.84rem" }}>
                                    Total Paid Media Pool
                                  </Typography>
                                  <Chip
                                    size="small"
                                    label="Scale all proportionally"
                                    sx={{ height: 19, fontSize: "0.65rem", fontWeight: 700 }}
                                  />
                                </Stack>
                              </TableCell>
                              <TableCell onClick={(e) => e.stopPropagation()}>
                                <Stack direction="row" spacing={1.25} alignItems="center">
                                  <Box sx={{ flexGrow: 1, minWidth: 110, px: 0.5 }}>
                                    {inputMode === "pct" ? (
                                      <Slider
                                        size="small"
                                        color="secondary"
                                        min={-100}
                                        max={100}
                                        step={5}
                                        value={Math.max(-100, Math.min(100, Math.round(totalMediaDeltaPct)))}
                                        disabled={baseMediaSpendPool <= 0}
                                        marks={[{ value: 0 }]}
                                        valueLabelDisplay="auto"
                                        valueLabelFormat={(val) => signedPct(val, 0)}
                                        aria-label="Total paid media budget change in percent"
                                        onChange={(_, val) =>
                                          handleTotalMediaPoolChange(Math.max(0, baseMediaSpendPool * (1 + (val as number) / 100)))
                                        }
                                        sx={{ py: 0.5 }}
                                      />
                                    ) : (
                                      <Slider
                                        size="small"
                                        color="secondary"
                                        min={0}
                                        max={Math.max(100, Math.round(toDisplay(baseMediaSpendPool * 2)))}
                                        step={Math.max(10, Math.round(toDisplay(baseMediaSpendPool / 40)))}
                                        value={Math.max(
                                          0,
                                          Math.min(
                                            Math.round(toDisplay(baseMediaSpendPool * 2)),
                                            Math.round(toDisplay(scenarioMediaSpendPool))
                                          )
                                        )}
                                        disabled={baseMediaSpendPool <= 0}
                                        marks={baseMediaSpendPool > 0 ? [{ value: Math.round(toDisplay(baseMediaSpendPool)) }] : undefined}
                                        valueLabelDisplay="auto"
                                        valueLabelFormat={(val) => `${eurCompact(val as number)}${perDaySuffix}`}
                                        aria-label="Total paid media budget in euros"
                                        onChange={(_, val) =>
                                          handleTotalMediaPoolChange(fromDisplayEuro(val as number, spendGranularity, effectiveHorizon))
                                        }
                                        sx={{ py: 0.5 }}
                                      />
                                    )}
                                  </Box>
                                  {inputMode === "pct" ? (
                                    <InlinePctInput
                                      value={totalMediaDeltaPct}
                                      name="total-media-pct"
                                      label="Total paid media budget change in percent"
                                      disabled={baseMediaSpendPool <= 0}
                                      onCommit={(pct) => handleTotalMediaPoolChange(Math.max(0, baseMediaSpendPool * (1 + pct / 100)))}
                                    />
                                  ) : (
                                    <InlineEuroInput
                                      value={toDisplay(scenarioMediaSpendPool)}
                                      name="total-media-eur"
                                      label="Total paid media budget in euros"
                                      suffix={perDaySuffix || undefined}
                                      disabled={baseMediaSpendPool <= 0}
                                      onCommit={(entered) =>
                                        handleTotalMediaPoolChange(fromDisplayEuro(entered, spendGranularity, effectiveHorizon))
                                      }
                                    />
                                  )}
                                </Stack>
                              </TableCell>
                              <TableCell align="right" sx={{ ...NUMERIC, fontWeight: 800, whiteSpace: "nowrap" }}>
                                <Box
                                  component="span"
                                  sx={{
                                    display: "inline-block",
                                    minWidth: 108,
                                    px: 0.9,
                                    py: 0.35,
                                    borderRadius: 1,
                                    border: `1px solid ${alpha(PALETTE.brandPrimary, 0.5)}`,
                                    bgcolor: "#FFFFFF",
                                    fontWeight: 800,
                                    fontSize: "0.84rem",
                                    textAlign: "right",
                                    ...NUMERIC,
                                  }}
                                >
                                  {eur(toDisplay(scenarioMediaSpendPool))}{perDaySuffix}
                                </Box>
                              </TableCell>
                              <TableCell align="right" sx={{ ...NUMERIC, fontWeight: 800, whiteSpace: "nowrap" }}>
                                <Box
                                  component="span"
                                  sx={{
                                    display: "inline-block",
                                    minWidth: 118,
                                    px: 0.9,
                                    py: 0.35,
                                    borderRadius: 1,
                                    border: `1px solid ${alpha(PALETTE.semanticGood, 0.5)}`,
                                    bgcolor: "#FFFFFF",
                                    color: PALETTE.semanticGood,
                                    fontWeight: 800,
                                    fontSize: "0.84rem",
                                    textAlign: "right",
                                    ...NUMERIC,
                                  }}
                                >
                                  {eur(mediaTotalSales)}
                                </Box>
                              </TableCell>
                              <TableCell sx={{ ...NUMERIC, fontWeight: 750 }}>
                                <Stack direction="row" justifyContent="space-between" alignItems="center">
                                  <span>{((100 * mediaTotalSales) / Math.max(1, activeResult.scenario_total_sales)).toFixed(1)}%</span>
                                  {Math.abs(mediaTotalSales - mediaBaseSales) >= 10 && (
                                    <Typography
                                      variant="caption"
                                      sx={{
                                        ...NUMERIC,
                                        fontWeight: 800,
                                        color: mediaTotalSales - mediaBaseSales >= 0 ? PALETTE.semanticGood : PALETTE.semanticBad,
                                      }}
                                    >
                                      Δ {signedEur(mediaTotalSales - mediaBaseSales)}
                                    </Typography>
                                  )}
                                </Stack>
                              </TableCell>
                              <TableCell align="right" sx={{ ...NUMERIC, fontWeight: 800 }}>
                                {mediaRoi != null ? `${mediaRoi.toFixed(2)}×` : "—"}
                              </TableCell>
                            </TableRow>
                          </>
                        )}

                        {/* Group 2: Structural Baseline, Seasonality, Calendar & Control Drivers */}
                        {categoryFilter !== "media" && visibleStructuralVars.map(renderVariableRow)}

                        {/* Grand Total Forecast Row (100% Additive) */}
                        <TableRow
                          sx={{
                            bgcolor: PALETTE.bgCanvas,
                            "& td, & th": { fontWeight: 800, borderTop: `2px solid ${PALETTE.brandNavy}` },
                          }}
                        >
                          <TableCell
                            component="th"
                            scope="row"
                            sx={{
                              position: "sticky",
                              insetInlineStart: 0,
                              zIndex: 1,
                              bgcolor: PALETTE.bgCanvas,
                            }}
                          >
                            Total Forecast ({activeResult.horizon_days} days · 100% Additive)
                          </TableCell>
                          <TableCell sx={{ color: PALETTE.inkSecondary, fontWeight: 600 }}>
                            {activeResult.prediction_total_lo != null && activeResult.prediction_total_hi != null
                              ? `90% Prediction Interval: ${eur(activeResult.prediction_total_lo)} – ${eur(activeResult.prediction_total_hi)}`
                              : "All variables sum to 100% of expected sales"}
                          </TableCell>
                          <TableCell align="right" sx={{ ...NUMERIC, whiteSpace: "nowrap" }}>
                            <Box
                              component="span"
                              sx={{
                                display: "inline-block",
                                minWidth: 108,
                                px: 0.9,
                                py: 0.35,
                                borderRadius: 1,
                                border: `1px solid ${PALETTE.borderStrong}`,
                                bgcolor: "#FFFFFF",
                                fontWeight: 800,
                                fontSize: "0.84rem",
                                textAlign: "right",
                                ...NUMERIC,
                              }}
                            >
                              {eur(activeResult.scenario_total_spend)}
                            </Box>
                            {Math.abs(activeResult.delta_spend) >= 10 ? ` (${signedEur(activeResult.delta_spend)})` : ""}
                          </TableCell>
                          <TableCell align="right" sx={{ ...NUMERIC, whiteSpace: "nowrap" }}>
                            <Box
                              component="span"
                              sx={{
                                display: "inline-block",
                                minWidth: 118,
                                px: 0.9,
                                py: 0.35,
                                borderRadius: 1,
                                border: `1.5px solid ${PALETTE.semanticGood}`,
                                bgcolor: PALETTE.bgGoodTint,
                                color: PALETTE.semanticGood,
                                fontWeight: 800,
                                fontSize: "0.85rem",
                                textAlign: "right",
                                ...NUMERIC,
                              }}
                            >
                              {eur(activeResult.scenario_total_sales)}
                            </Box>
                          </TableCell>
                          <TableCell sx={NUMERIC}>
                            <Stack direction="row" justifyContent="space-between" alignItems="center">
                              <span>100.0%</span>
                              {Math.abs(activeResult.delta_sales) >= 10 && (
                                <Typography
                                  variant="caption"
                                  sx={{
                                    ...NUMERIC,
                                    fontWeight: 800,
                                    color: activeResult.delta_sales >= 0 ? PALETTE.semanticGood : PALETTE.semanticBad,
                                  }}
                                >
                                  Δ {signedEur(activeResult.delta_sales)}
                                </Typography>
                              )}
                            </Stack>
                          </TableCell>
                          <TableCell align="right" sx={NUMERIC}>
                            {activeResult.marginal_roas != null && Math.abs(activeResult.delta_spend) >= 10
                              ? `mROAS ${activeResult.marginal_roas.toFixed(2)}×`
                              : mediaRoi != null
                                ? `${mediaRoi.toFixed(2)}× media`
                                : "—"}
                          </TableCell>
                        </TableRow>
                      </TableBody>
                    </Table>
                  </TableContainer>
                ) : (
                  runId && (
                    <PlanSpreadsheet
                      runId={runId}
                      activeForecastId={forecastId}
                      activeResult={activeResult ?? planResult}
                      forecastBusy={forecastBusy}
                      channelNames={channelNames}
                      lockedSlugs={lockedSlugs}
                      cellMode={editorTab === "sales_cells" ? "sales" : "spend"}
                      onCellModeChange={(mode) => {
                        if (mode === "sales") setEditorTab("sales_cells");
                        else if (mode === "spend") setEditorTab("spreadsheet");
                      }}
                      onPlanCreated={(created) => {
                        pendingForecastIdRef.current = created.id;
                        queryClient.setQueryData<{ run_id: string; forecasts: ForecastItem[] }>(
                          ["forecasts", runId],
                          (old) => {
                            const prev = old?.forecasts ?? [];
                            if (prev.some((f) => f.id === created.id)) return old;
                            return { run_id: runId, forecasts: [created, ...prev] };
                          }
                        );
                        queryClient.setQueryData(["forecast", created.id], created);
                        setForecastId(created.id);
                        void queryClient.invalidateQueries({ queryKey: ["forecasts", runId] });
                        void queryClient.invalidateQueries({ queryKey: ["forecast", created.id] });
                      }}
                    />
                  )
                )}
              </CardContent>
            </Collapse>
          </Card>

          {/* =========================================================================================
              SECTION 2: DAILY FORECAST TRAJECTORY & TIME-SERIES STACK (Single Tabbed Card)
             ========================================================================================= */}
          {activeResult && trajectoryFigure && dailyStackFigure && (
            <Box id="sec-trajectory" sx={{ scrollMarginTop: 84 }}>
              <Stack
                direction={{ xs: "column", sm: "row" }}
                justifyContent="space-between"
                alignItems={{ xs: "flex-start", sm: "center" }}
                spacing={1}
                sx={{ mb: 1.5 }}
              >
                <Box>
                  <Typography variant="h3" component="h2">
                    2. Daily Forecast Trajectory & Time-Series Decomposition
                  </Typography>
                  <Typography variant="body2" sx={{ color: PALETTE.inkSecondary }}>
                    Switch between the historical + 90% PI forecast trajectory, the day-by-day stacked variable attribution, and the daily CSV data table.
                  </Typography>
                </Box>
                <ToggleButtonGroup
                  size="small"
                  exclusive
                  value={trajectoryTab}
                  onChange={(_, v: "trajectory" | "stack" | "table" | null) => v && setTrajectoryTab(v)}
                  aria-label="Daily time-series view mode"
                >
                  <ToggleButton value="trajectory" sx={{ textTransform: "none", px: 1.25, py: 0.3, fontWeight: 700 }}>
                    Forecast & History (90% PI)
                  </ToggleButton>
                  <ToggleButton value="stack" sx={{ textTransform: "none", px: 1.25, py: 0.3, fontWeight: 700 }}>
                    Daily Variable Stack
                  </ToggleButton>
                  <ToggleButton value="table" sx={{ textTransform: "none", px: 1.25, py: 0.3, fontWeight: 700 }}>
                    Daily Table ({dailyRows.length}d)
                  </ToggleButton>
                </ToggleButtonGroup>
              </Stack>

              <Paper variant="outlined" sx={{ p: { xs: 2, md: 2.5 }, borderRadius: 1, borderColor: PALETTE.borderSubtle }}>
                {trajectoryTab === "trajectory" && (
                  <>
                    {historyError && (
                      <Alert severity="error" sx={{ mb: 1.5 }}>
                        History, validation and test could not be loaded: {historyError instanceof Error ? historyError.message : String(historyError)}
                      </Alert>
                    )}
                    <Stack
                      direction={{ xs: "column", sm: "row" }}
                      spacing={1.5}
                      alignItems={{ xs: "flex-start", sm: "center" }}
                      justifyContent="space-between"
                      flexWrap="wrap"
                      useFlexGap
                      sx={{ mb: 1.5 }}
                    >
                      <Box>
                        <Typography variant="subtitle1" component="h3" sx={{ fontWeight: 750 }}>
                          Historical Sales, Forecast Trajectory & Daily Spend ({shortDate(planDates[0])} – {shortDate(planDates[planDates.length - 1])})
                        </Typography>
                        <Typography variant="caption" sx={{ color: PALETTE.inkSecondary }}>
                          {trajectoryCaption}
                        </Typography>
                        {splitText && (
                          <Typography variant="caption" component="p" sx={{ color: PALETTE.inkPrimary, fontWeight: 600, mt: 0.5 }}>
                            {splitText}
                          </Typography>
                        )}
                      </Box>
                      <Stack direction="row" spacing={1.5} alignItems="center" flexWrap="wrap" useFlexGap>
                        <Stack direction="row" spacing={1} alignItems="center">
                          <Typography id="history-range-label" variant="caption" sx={{ fontWeight: 700, color: PALETTE.inkSecondary }}>
                            History:
                          </Typography>
                          <ToggleButtonGroup
                            size="small"
                            exclusive
                            value={effectiveHistoryRange}
                            onChange={(_, v: string | null) => v && setHistoryRange(v)}
                            aria-labelledby="history-range-label"
                          >
                            {historyRanges.map((r) => (
                              <ToggleButton
                                key={r.id}
                                value={r.id}
                                aria-label={r.label}
                                sx={{ textTransform: "none", px: compact ? 0.9 : 1.1, py: 0.2, fontWeight: 600, whiteSpace: "nowrap" }}
                              >
                                {compact ? r.short : r.label}
                              </ToggleButton>
                            ))}
                          </ToggleButtonGroup>
                        </Stack>
                        <FormControlLabel
                          control={<Checkbox size="small" checked={showLastYear} onChange={(e) => setShowLastYear(e.target.checked)} />}
                          label={<Typography variant="body2">Same weekday last year</Typography>}
                        />
                        <ExplainButton
                          kind="chart"
                          title={`Historical sales, forecast trajectory & daily spend (${shortDate(planDates[0])} – ${shortDate(planDates[planDates.length - 1])})`}
                          caption={splitText ? `${trajectoryCaption} ${splitText}.` : trajectoryCaption}
                          figure={trajectoryFigure}
                        />
                      </Stack>
                    </Stack>

                    <Suspense fallback={<Skeleton variant="rounded" height={Number(trajectoryFigure.layout.height) || 440} />}>
                      <LazyPlotlyChart
                        data={trajectoryFigure.data}
                        layout={trajectoryFigure.layout}
                        height={Number(trajectoryFigure.layout.height) || 440}
                        ariaLabel={hasBacktest ? "Actual sales, validation and test forecasts, plan forecast, scenario trajectory and daily spend" : "Actual sales, plan forecast, scenario trajectory and daily spend"}
                        viewKey={`${forecastId ?? ""}:${effectiveHistoryRange}:${horizonDays}:${showLastYear}`}
                      />
                    </Suspense>
                  </>
                )}

                {trajectoryTab === "stack" && (
                  <>
                    <Stack
                      direction={{ xs: "column", sm: "row" }}
                      spacing={1.5}
                      alignItems={{ xs: "flex-start", sm: "center" }}
                      justifyContent="space-between"
                      flexWrap="wrap"
                      useFlexGap
                      sx={{ mb: 1.5 }}
                    >
                      <Box>
                        <Typography variant="subtitle1" component="h3" sx={{ fontWeight: 750 }}>
                          Daily Stacked Variable Attribution Over the Forecast Horizon
                        </Typography>
                        <Typography variant="caption" sx={{ color: PALETTE.inkSecondary }}>
                          Day-by-day decomposition of the forecast into Baseline, Seasonality, Weekday Profile, Affiliate Control, and each Paid Media channel (including Catalog adstock decay).
                        </Typography>
                      </Box>
                      <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap" useFlexGap>
                        <ToggleButtonGroup
                          size="small"
                          exclusive
                          value={dailyStackMode}
                          onChange={(_, v: "absolute" | "delta" | null) => v && setDailyStackMode(v)}
                          aria-label="Daily variable attribution mode"
                        >
                          <ToggleButton value="absolute" sx={{ textTransform: "none", px: 1.25, py: 0.25, fontWeight: 600 }}>
                            Daily Total Stack
                          </ToggleButton>
                          <ToggleButton value="delta" sx={{ textTransform: "none", px: 1.25, py: 0.25, fontWeight: 600 }}>
                            Daily Scenario Δ
                          </ToggleButton>
                        </ToggleButtonGroup>
                        <ExplainButton
                          kind="chart"
                          title={dailyStackMode === "absolute" ? "Daily stacked variable attribution (total)" : "Daily stacked variable attribution (scenario Δ)"}
                          caption="Day-by-day decomposition of the forecast into Baseline, Seasonality, Weekday Profile, Affiliate Control, and each Paid Media channel (including Catalog adstock decay)."
                          figure={dailyStackFigure}
                        />
                      </Stack>
                    </Stack>

                    <Suspense fallback={<Skeleton variant="rounded" height={400} />}>
                      <LazyPlotlyChart
                        data={dailyStackFigure.data}
                        layout={dailyStackFigure.layout}
                        height={400}
                        ariaLabel="Daily stacked variable attribution across the forecast window"
                        viewKey={`stack:${forecastId}:${dailyStackMode}:${horizonDays}:${selectedVariable ?? ""}`}
                      />
                    </Suspense>
                  </>
                )}

                {trajectoryTab === "table" && (
                  <>
                    <Stack
                      direction={{ xs: "column", md: "row" }}
                      justifyContent="space-between"
                      alignItems={{ xs: "flex-start", md: "center" }}
                      spacing={1.5}
                      sx={{ mb: 1.5 }}
                    >
                      <Box>
                        <Typography variant="subtitle1" component="h3" sx={{ fontWeight: 750 }}>
                          Daily Forecast &amp; Individual Channel Sales Cells ({dailyRows.length} rows)
                        </Typography>
                        <Typography variant="caption" sx={{ color: PALETTE.inkSecondary }}>
                          Each cell shows the exact Euro forecast sales contribution for that day and channel, summing across columns to the Daily Scenario Forecast.
                        </Typography>
                      </Box>
                      <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap" useFlexGap>
                        <ToggleButtonGroup
                          size="small"
                          exclusive
                          value={showHistoryInTable ? "all" : "forecast"}
                          onChange={(_, v: "forecast" | "all" | null) => v && setShowHistoryInTable(v === "all")}
                          aria-label="Daily table row scope"
                        >
                          <ToggleButton value="forecast" sx={{ textTransform: "none", px: 1.15, py: 0.25, fontWeight: 700 }}>
                            Forecast Window ({activeResult.horizon_days}d)
                          </ToggleButton>
                          <ToggleButton value="all" sx={{ textTransform: "none", px: 1.15, py: 0.25, fontWeight: 600 }}>
                            + Historical Rows
                          </ToggleButton>
                        </ToggleButtonGroup>
                        <ExplainButton
                          kind="table"
                          title={`Daily forecast & individual channel sales cells (${dailyRows.length} rows)`}
                          caption="Each cell shows the exact Euro forecast sales contribution for that day and channel, summing across columns to the Daily Scenario Forecast."
                        />
                        <Button
                          size="small"
                          variant="outlined"
                          startIcon={<DownloadIcon fontSize="small" />}
                          onClick={() =>
                            downloadDailyCsv(
                              dailyRows,
                              dailyTableVariables,
                              `forecast_daily_${activeResult.start_date ?? "plan"}.csv`
                            )
                          }
                          sx={{ textTransform: "none", fontWeight: 600 }}
                        >
                          Download Daily CSV
                        </Button>
                      </Stack>
                    </Stack>
                    <TableContainer
                      role="region"
                      aria-label="Daily sales, baseline forecast, scenario forecast, spend and individual channel forecast values"
                      tabIndex={0}
                      sx={{
                        maxHeight: 500,
                        border: `1px solid ${PALETTE.borderSubtle}`,
                        borderRadius: 1,
                        overflow: "auto",
                        scrollbarGutter: "stable",
                        overscrollBehavior: "contain",
                      }}
                    >
                      <Table size="small" stickyHeader>
                        <TableHead>
                          <TableRow>
                            <TableCell
                              scope="col"
                              sx={{
                                position: "sticky",
                                insetInlineStart: 0,
                                zIndex: 3,
                                bgcolor: PALETTE.bgCanvas,
                                whiteSpace: "nowrap",
                                fontWeight: 800,
                                borderRight: `1px solid ${PALETTE.borderSubtle}`,
                              }}
                            >
                              Date
                            </TableCell>
                            <TableCell
                              scope="col"
                              align="right"
                              sx={{
                                whiteSpace: "nowrap",
                                fontWeight: 800,
                                bgcolor: PALETTE.bgGoodTint,
                                color: PALETTE.semanticGood,
                                borderRight: `1px solid ${PALETTE.borderSubtle}`,
                              }}
                            >
                              Total Forecast (€)
                            </TableCell>
                            {dailyTableVariables.map((v) => (
                              <TableCell
                                key={v.key}
                                scope="col"
                                align="right"
                                sx={{
                                  whiteSpace: "nowrap",
                                  fontWeight: 700,
                                  bgcolor: v.category === "media" ? PALETTE.bgCanvas : PALETTE.bgAccentTint,
                                  borderRight: `1px solid ${PALETTE.borderSubtle}`,
                                }}
                              >
                                {v.label} (€)
                              </TableCell>
                            ))}
                            <TableCell scope="col" align="right" sx={{ whiteSpace: "nowrap", fontWeight: 700, bgcolor: PALETTE.bgCanvas }}>
                              Planned Spend (€)
                            </TableCell>
                            <TableCell scope="col" align="right" sx={{ whiteSpace: "nowrap", bgcolor: PALETTE.bgCanvas }}>
                              90% Low
                            </TableCell>
                            <TableCell scope="col" align="right" sx={{ whiteSpace: "nowrap", bgcolor: PALETTE.bgCanvas }}>
                              90% High
                            </TableCell>
                            <TableCell scope="col" align="right" sx={{ whiteSpace: "nowrap", bgcolor: PALETTE.bgCanvas }}>
                              Baseline Plan
                            </TableCell>
                            {showHistoryInTable && (
                              <>
                                <TableCell scope="col" align="right" sx={{ whiteSpace: "nowrap", bgcolor: PALETTE.bgCanvas }}>
                                  Actual Sales
                                </TableCell>
                                <TableCell scope="col" align="right" sx={{ whiteSpace: "nowrap", bgcolor: PALETTE.bgCanvas }}>
                                  Same Weekday LY
                                </TableCell>
                              </>
                            )}
                          </TableRow>
                        </TableHead>
                        <TableBody>
                          {/* Top Summary Row: Period Total per Channel as Cell Values */}
                          <TableRow
                            sx={{
                              bgcolor: PALETTE.bgGoodTint,
                              "& td, & th": {
                                fontWeight: 800,
                                borderBottom: `2px solid ${PALETTE.borderStrong}`,
                                whiteSpace: "nowrap",
                              },
                            }}
                          >
                            <TableCell
                              component="th"
                              scope="row"
                              sx={{
                                position: "sticky",
                                insetInlineStart: 0,
                                zIndex: 2,
                                bgcolor: PALETTE.bgGoodTint,
                                color: PALETTE.semanticGood,
                                borderRight: `1px solid ${PALETTE.borderSubtle}`,
                              }}
                            >
                              Period Total (Σ {activeResult.horizon_days}d)
                            </TableCell>
                            <TableCell
                              align="right"
                              sx={{
                                ...NUMERIC,
                                color: PALETTE.semanticGood,
                                bgcolor: alpha(PALETTE.semanticGood, 0.12),
                                borderRight: `1px solid ${PALETTE.borderSubtle}`,
                              }}
                            >
                              {eur(activeResult.scenario_total_sales)}
                            </TableCell>
                            {dailyTableVariables.map((v) => (
                              <TableCell
                                key={`top-sum-${v.key}`}
                                align="right"
                                sx={{
                                  ...NUMERIC,
                                  color: v.scenario_contribution < 0 ? PALETTE.semanticBad : PALETTE.semanticGood,
                                  borderRight: `1px solid ${PALETTE.borderSubtle}`,
                                }}
                              >
                                {v.scenario_contribution < 0 ? signedEur(v.scenario_contribution) : eur(v.scenario_contribution)}
                              </TableCell>
                            ))}
                            <TableCell align="right" sx={NUMERIC}>{eur(activeResult.scenario_total_spend)}</TableCell>
                            <TableCell align="right" sx={NUMERIC}>
                              {activeResult.prediction_total_lo != null ? eur(activeResult.prediction_total_lo) : "–"}
                            </TableCell>
                            <TableCell align="right" sx={NUMERIC}>
                              {activeResult.prediction_total_hi != null ? eur(activeResult.prediction_total_hi) : "–"}
                            </TableCell>
                            <TableCell align="right" sx={NUMERIC}>{eur(activeResult.base_total_sales)}</TableCell>
                            {showHistoryInTable && (
                              <>
                                <TableCell align="right" sx={NUMERIC}>–</TableCell>
                                <TableCell align="right" sx={NUMERIC}>{lastYear != null ? eur(lastYear) : "–"}</TableCell>
                              </>
                            )}
                          </TableRow>

                          {/* Daily Rows */}
                          {dailyRows.map((r) => {
                            const fmt = (x: number | null | undefined) =>
                              x == null ? "–" : x < 0 ? signedEur(x) : eur(x);
                            return (
                              <TableRow
                                key={r.date}
                                hover
                                sx={r.scenarioForecast != null ? { backgroundColor: "#FFFFFF" } : { backgroundColor: PALETTE.bgCanvas }}
                              >
                                <TableCell
                                  component="th"
                                  scope="row"
                                  sx={{
                                    whiteSpace: "nowrap",
                                    position: "sticky",
                                    insetInlineStart: 0,
                                    zIndex: 1,
                                    fontWeight: 600,
                                    bgcolor: r.scenarioForecast != null ? "#FFFFFF" : PALETTE.bgCanvas,
                                    borderRight: `1px solid ${PALETTE.borderSubtle}`,
                                    ...NUMERIC,
                                  }}
                                >
                                  {shortDate(r.date)}
                                </TableCell>
                                <TableCell
                                  align="right"
                                  sx={{
                                    ...NUMERIC,
                                    whiteSpace: "nowrap",
                                    fontWeight: 800,
                                    color: PALETTE.semanticGood,
                                    bgcolor: r.scenarioForecast != null ? alpha(PALETTE.semanticGood, 0.06) : undefined,
                                    borderRight: `1px solid ${PALETTE.borderSubtle}`,
                                  }}
                                >
                                  {fmt(r.scenarioForecast)}
                                </TableCell>
                                {dailyTableVariables.map((v) => {
                                  const val = r.byVariable[v.key];
                                  return (
                                    <TableCell
                                      key={v.key}
                                      align="right"
                                      sx={{
                                        ...NUMERIC,
                                        whiteSpace: "nowrap",
                                        fontWeight: val != null && Math.abs(val) >= 1 ? 650 : 400,
                                        color: val != null && val < 0 ? PALETTE.semanticBad : val != null && val > 0 ? PALETTE.inkPrimary : PALETTE.inkMuted,
                                        borderRight: `1px solid ${PALETTE.borderSubtle}`,
                                      }}
                                    >
                                      {fmt(val)}
                                    </TableCell>
                                  );
                                })}
                                <TableCell align="right" sx={{ ...NUMERIC, whiteSpace: "nowrap", fontWeight: 600 }}>
                                  {fmt(r.spend)}
                                </TableCell>
                                <TableCell align="right" sx={{ ...NUMERIC, whiteSpace: "nowrap", color: PALETTE.inkSecondary }}>
                                  {fmt(r.lo)}
                                </TableCell>
                                <TableCell align="right" sx={{ ...NUMERIC, whiteSpace: "nowrap", color: PALETTE.inkSecondary }}>
                                  {fmt(r.hi)}
                                </TableCell>
                                <TableCell align="right" sx={{ ...NUMERIC, whiteSpace: "nowrap" }}>
                                  {fmt(r.baselinePlan)}
                                </TableCell>
                                {showHistoryInTable && (
                                  <>
                                    <TableCell align="right" sx={{ ...NUMERIC, whiteSpace: "nowrap" }}>
                                      {fmt(r.actual)}
                                    </TableCell>
                                    <TableCell align="right" sx={{ ...NUMERIC, whiteSpace: "nowrap" }}>
                                      {fmt(r.lastYear)}
                                    </TableCell>
                                  </>
                                )}
                              </TableRow>
                            );
                          })}

                          {/* Bottom Summary Row */}
                          <TableRow
                            sx={{
                              bgcolor: PALETTE.bgCanvas,
                              "& td, & th": { fontWeight: 800, borderTop: `2px solid ${PALETTE.brandNavy}`, whiteSpace: "nowrap" },
                            }}
                          >
                            <TableCell
                              component="th"
                              scope="row"
                              sx={{
                                position: "sticky",
                                insetInlineStart: 0,
                                zIndex: 1,
                                bgcolor: PALETTE.bgCanvas,
                                borderRight: `1px solid ${PALETTE.borderSubtle}`,
                              }}
                            >
                              Forecast Total ({activeResult.horizon_days}d)
                            </TableCell>
                            <TableCell
                              align="right"
                              sx={{
                                ...NUMERIC,
                                color: PALETTE.semanticGood,
                                bgcolor: alpha(PALETTE.semanticGood, 0.08),
                                borderRight: `1px solid ${PALETTE.borderSubtle}`,
                              }}
                            >
                              {eur(activeResult.scenario_total_sales)}
                            </TableCell>
                            {dailyTableVariables.map((v) => (
                              <TableCell
                                key={v.key}
                                align="right"
                                sx={{
                                  ...NUMERIC,
                                  color: v.scenario_contribution < 0 ? PALETTE.semanticBad : PALETTE.semanticGood,
                                  borderRight: `1px solid ${PALETTE.borderSubtle}`,
                                }}
                              >
                                {v.scenario_contribution < 0 ? signedEur(v.scenario_contribution) : eur(v.scenario_contribution)}
                              </TableCell>
                            ))}
                            <TableCell align="right" sx={NUMERIC}>{eur(activeResult.scenario_total_spend)}</TableCell>
                            <TableCell align="right" sx={NUMERIC}>
                              {activeResult.prediction_total_lo != null ? eur(activeResult.prediction_total_lo) : "–"}
                            </TableCell>
                            <TableCell align="right" sx={NUMERIC}>
                              {activeResult.prediction_total_hi != null ? eur(activeResult.prediction_total_hi) : "–"}
                            </TableCell>
                            <TableCell align="right" sx={NUMERIC}>{eur(activeResult.base_total_sales)}</TableCell>
                            {showHistoryInTable && (
                              <>
                                <TableCell align="right" sx={NUMERIC}>–</TableCell>
                                <TableCell align="right" sx={NUMERIC}>{lastYear != null ? eur(lastYear) : "–"}</TableCell>
                              </>
                            )}
                          </TableRow>
                        </TableBody>
                      </Table>
                    </TableContainer>
                  </>
                )}
              </Paper>
            </Box>
          )}

          {/* =========================================================================================
              SECTION 3: SHAPLEY ATTRIBUTION & INPUT–OUTPUT CHARTS (2 Side-by-Side Complementary Panels)
             ========================================================================================= */}
          {activeResult && (
            <Box id="sec-shapley" sx={{ scrollMarginTop: 84, mb: 4 }}>
              <Stack
                direction={{ xs: "column", sm: "row" }}
                justifyContent="space-between"
                alignItems={{ xs: "flex-start", sm: "center" }}
                spacing={1}
                sx={{ mb: 1.5 }}
              >
                <Box>
                  <Typography variant="h3" component="h2">
                    3. Shapley Variable Attribution & Input–Output Diagnostics
                  </Typography>
                  <Typography variant="body2" sx={{ color: PALETTE.inkSecondary }}>
                    Left: additive Shapley waterfall showing how each feature builds the forecast (or shifts the scenario). Right: channel input spend vs. output sales and 90% credible intervals.
                  </Typography>
                </Box>
                {selectedVariable && (
                  <Button size="small" variant="outlined" onClick={() => setSelectedVariable(null)} sx={{ textTransform: "none" }}>
                    Clear variable highlight
                  </Button>
                )}
              </Stack>

              <Box
                sx={{
                  display: "grid",
                  gridTemplateColumns: { xs: "minmax(0, 1fr)", lg: "repeat(2, minmax(0, 1fr))" },
                  gap: 2.5,
                }}
              >
                {/* Left Panel: Shapley Waterfall Decomposition */}
                <Paper variant="outlined" sx={{ p: 2.25, borderRadius: 1, borderColor: PALETTE.borderSubtle }}>
                  <Stack direction="row" justifyContent="space-between" alignItems="flex-start" spacing={1} sx={{ mb: 1 }}>
                    <Box>
                      <Typography variant="subtitle1" component="h3" sx={{ fontWeight: 750, lineHeight: 1.25 }}>
                        Shapley Waterfall Decomposition
                      </Typography>
                      <Typography variant="caption" sx={{ color: PALETTE.inkSecondary }}>
                        {waterfallMode === "absolute"
                          ? "How Baseline Demand + Calendar/Controls + Paid Media sum to 100% of the forecast"
                          : "How each modified input shifts forecast sales from the Baseline Plan to the Active Scenario"}
                      </Typography>
                    </Box>
                    <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap" justifyContent="flex-end" useFlexGap>
                      <ToggleButtonGroup
                        size="small"
                        exclusive
                        value={waterfallMode}
                        onChange={(_, v: "absolute" | "delta" | null) => v && setWaterfallMode(v)}
                        aria-label="Waterfall decomposition mode"
                      >
                        <ToggleButton value="absolute" sx={{ textTransform: "none", px: 1, py: 0.2, fontSize: "0.75rem", fontWeight: 600 }}>
                          Total Build-Up
                        </ToggleButton>
                        <ToggleButton value="delta" sx={{ textTransform: "none", px: 1, py: 0.2, fontSize: "0.75rem", fontWeight: 600 }}>
                          Scenario Δ Bridge
                        </ToggleButton>
                      </ToggleButtonGroup>
                      {waterfallFigure && (
                        <ExplainButton
                          kind="chart"
                          title={waterfallMode === "absolute" ? "Shapley waterfall decomposition (total build-up)" : "Shapley waterfall decomposition (scenario Δ bridge)"}
                          caption={
                            waterfallMode === "absolute"
                              ? "How Baseline Demand + Calendar/Controls + Paid Media sum to 100% of the forecast"
                              : "How each modified input shifts forecast sales from the Baseline Plan to the Active Scenario"
                          }
                          figure={waterfallFigure}
                        />
                      )}
                    </Stack>
                  </Stack>
                  {waterfallFigure && (
                    <Suspense fallback={<Skeleton variant="rounded" height={360} />}>
                      <LazyPlotlyChart
                        data={waterfallFigure.data}
                        layout={waterfallFigure.layout}
                        height={360}
                        ariaLabel="Shapley waterfall decomposition of forecast sales"
                        viewKey={`wf:${forecastId}:${waterfallMode}:${selectedVariable ?? ""}`}
                      />
                    </Suspense>
                  )}
                </Paper>

                {/* Right Panel: Input Spend vs. Output Sales OR Feature Attribution Ranking (with 90% CIs) */}
                <Paper variant="outlined" sx={{ p: 2.25, borderRadius: 1, borderColor: PALETTE.borderSubtle }}>
                  <Stack direction="row" justifyContent="space-between" alignItems="flex-start" spacing={1} sx={{ mb: 1 }}>
                    <Box>
                      <Typography variant="subtitle1" component="h3" sx={{ fontWeight: 750, lineHeight: 1.25 }}>
                        {rightChartMode === "io"
                          ? "Input Spend vs. Output Sales Contribution (ROI)"
                          : "Feature Attribution Ranking (90% Credible Intervals)"}
                      </Typography>
                      <Typography variant="caption" sx={{ color: PALETTE.inkSecondary }}>
                        {rightChartMode === "io"
                          ? "Direct input–output comparison across active spend channels with ROI multipliers"
                          : `Posterior mean contribution and 5%–95% credible whiskers across active features (${activeResult.horizon_days}d)`}
                      </Typography>
                    </Box>
                    <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap" justifyContent="flex-end" useFlexGap>
                      <ToggleButtonGroup
                        size="small"
                        exclusive
                        value={rightChartMode}
                        onChange={(_, v: "io" | "ranking" | null) => v && setRightChartMode(v)}
                        aria-label="Right diagnostic chart mode"
                      >
                        <ToggleButton value="io" sx={{ textTransform: "none", px: 1, py: 0.2, fontSize: "0.75rem", fontWeight: 600 }}>
                          Spend vs. Output
                        </ToggleButton>
                        <ToggleButton value="ranking" sx={{ textTransform: "none", px: 1, py: 0.2, fontSize: "0.75rem", fontWeight: 600 }}>
                          90% CI Ranking
                        </ToggleButton>
                      </ToggleButtonGroup>
                      {(rightChartMode === "io" ? spendVsContribFigure : contribBarFigure) && (
                        <ExplainButton
                          kind="chart"
                          title={
                            rightChartMode === "io"
                              ? "Input spend vs. output sales contribution (ROI)"
                              : "Feature attribution ranking (90% credible intervals)"
                          }
                          caption={
                            rightChartMode === "io"
                              ? "Direct input–output comparison across active spend channels with ROI multipliers"
                              : `Posterior mean contribution and 5%–95% credible whiskers across active features (${activeResult.horizon_days}d)`
                          }
                          figure={rightChartMode === "io" ? spendVsContribFigure : contribBarFigure}
                        />
                      )}
                    </Stack>
                  </Stack>

                  {rightChartMode === "io" && spendVsContribFigure && (
                    <Suspense fallback={<Skeleton variant="rounded" height={360} />}>
                      <LazyPlotlyChart
                        data={spendVsContribFigure.data}
                        layout={spendVsContribFigure.layout}
                        height={360}
                        ariaLabel="Input spend versus output forecast sales contribution per channel"
                        viewKey={`io:${forecastId}:${selectedVariable ?? ""}`}
                      />
                    </Suspense>
                  )}

                  {rightChartMode === "ranking" && contribBarFigure && (
                    <Suspense fallback={<Skeleton variant="rounded" height={360} />}>
                      <LazyPlotlyChart
                        data={contribBarFigure.data}
                        layout={contribBarFigure.layout}
                        height={360}
                        ariaLabel="Variable attribution ranking with 90 percent credible intervals"
                        viewKey={`bar:${forecastId}:${selectedVariable ?? ""}`}
                      />
                    </Suspense>
                  )}
                </Paper>
              </Box>
            </Box>
          )}
          <ExamplePlan runId={runId} />
        </Stack>

        {/* Sticky Right Table of Contents Rail */}
        <OnThisPage entries={TOC_ITEMS} />
      </Box>
    </Box>
  );
};
