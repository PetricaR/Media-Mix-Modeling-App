import React, { useEffect, useId, useMemo, useState } from "react";
import {
  Alert,
  Box,
  Button,
  Chip,
  CircularProgress,
  Collapse,
  Divider,
  FormControl,
  FormControlLabel,
  IconButton,
  InputLabel,
  MenuItem,
  Paper,
  Radio,
  RadioGroup,
  Select,
  Skeleton,
  Slider,
  Stack,
  Switch,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableFooter,
  TableHead,
  TableRow,
  TextField,
  ToggleButton,
  ToggleButtonGroup,
  Tooltip,
  Typography,
} from "@mui/material";
import PlayArrowRoundedIcon from "@mui/icons-material/PlayArrowRounded";
import DownloadRoundedIcon from "@mui/icons-material/DownloadRounded";
import LockRoundedIcon from "@mui/icons-material/LockRounded";
import LockOpenRoundedIcon from "@mui/icons-material/LockOpenRounded";
import RestartAltRoundedIcon from "@mui/icons-material/RestartAltRounded";
import TuneRoundedIcon from "@mui/icons-material/TuneRounded";
import ExpandMoreRoundedIcon from "@mui/icons-material/ExpandMoreRounded";
import ExpandLessRoundedIcon from "@mui/icons-material/ExpandLessRounded";
import ScienceRoundedIcon from "@mui/icons-material/ScienceRounded";
import { useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import {
  isRunActive,
  useOptimisation,
  useOptimisations,
  useOptimiserContext,
  useRun,
  useSection,
} from "../api/hooks";
import type {
  BudgetConstraintMode,
  OptimisationChannelRow,
  OptimisationResult,
  OptimiserChannelMeta,
} from "../api/types";
import { PageHeader } from "../components/Layout/PageHeader";
import { ExplainButton, ExplainScope, useExplainPageNotes } from "../components/Explain/ExplainButton";
import { OnThisPage, type TocEntry } from "../components/Sections/OnThisPage";
import { KpiStrip, MoreAnalysis, SectionRenderer, useChartsDeferred } from "../components/Sections/SectionRenderer";
const LazyPlotlyChart = React.lazy(() => import("../components/Charts/PlotlyChart"));
/** A chart panel's title with its Gemini "Explain" button (it reads the chart drawn in the same panel). */
const ChartPanelTitle: React.FC<{ title: string; caption?: string; figure?: { data: unknown[]; layout: unknown } | null }> = ({
  title,
  caption,
  figure,
}) => (
  <Stack direction="row" justifyContent="space-between" alignItems="flex-start" spacing={1}>
    <Typography variant="h3">{title}</Typography>
    {figure && <ExplainButton kind="chart" title={title} caption={caption} figure={figure} />}
  </Stack>
);

function PlotlyChart(props: React.ComponentProps<typeof LazyPlotlyChart>) {
  // Inside a closed "More allocation charts" group the chart waits for the first open (Plotly cannot size it hidden)
  const deferred = useChartsDeferred();
  if (deferred) return <Box sx={{ height: props.height ?? 320, borderRadius: 1, bgcolor: PALETTE.bgSubtle }} aria-hidden="true" />;
  return (
    <React.Suspense fallback={<Skeleton variant="rounded" height={props.height ?? 320} />}>
      <LazyPlotlyChart {...props} />
    </React.Suspense>
  );
}
import { eur, eurCompact, signedEur, signedPct } from "../components/Forecast/format";
import {
  buildAllResponseCurvesGridFigure,
  buildBudgetComparisonFigure,
  buildMarginalRoiCurveFigure,
  buildOutcomeComparisonFigure,
  buildSingleResponseCurveFigure,
  buildSpendDeltaFigure,
  buildSpendDonutFigure,
  channelColor,
} from "../components/Optimizer/figures";
import { escapeCsvCell } from "../lib/csv";
import { NUMERIC, PALETTE } from "../theme";

const formatEur = (v: number) => eurCompact(v, false);
const formatEurSigned = (v: number) => eurCompact(v, true);
const formatPctSigned = (v: number) => signedPct(v, 1);

function downloadCsv(filename: string, rows: Record<string, unknown>[]) {
  if (!rows.length) return;
  const cols = Object.keys(rows[0]);
  const lines = [
    cols.map((c) => escapeCsvCell(c)).join(","),
    ...rows.map((r) => cols.map((c) => escapeCsvCell(r[c])).join(",")),
  ];
  const blob = new Blob([lines.join("\n")], { type: "text/csv;charset=utf-8;" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

type ActiveScenarioKey = "default_constrained" | "default_unconstrained" | string;

const RATIO_STEP_OPTIONS = [
  { value: 0, label: "0% (locked)" },
  { value: 0.1, label: "10%" },
  { value: 0.2, label: "20%" },
  { value: 0.3, label: "30%" },
  { value: 0.4, label: "40%" },
  { value: 0.5, label: "50%" },
  { value: 0.75, label: "75%" },
  { value: 1.0, label: "100%" },
];

/** "+1.3%" / "−0.4%": a signed percentage with one decimal. */
function signedPct1(v: number): string {
  return `${v >= 0 ? "+" : "−"}${Math.abs(v).toFixed(1)}%`;
}

function rowCurSpend(r: OptimisationChannelRow): number {
  return r.current_spend ?? r.historical_spend ?? 0;
}

function rowCurInc(r: OptimisationChannelRow): number {
  return r.current_inc_sales ?? r.historical_incremental ?? r.current_incremental ?? 0;
}

function rowOptInc(r: OptimisationChannelRow): number {
  return r.optimised_inc_sales ?? r.optimised_incremental ?? r.expected_incremental_sales ?? 0;
}

function rowDeltaSpend(r: OptimisationChannelRow): number {
  return r.spend_delta ?? r.delta_spend ?? r.optimised_spend - rowCurSpend(r);
}

function rowDeltaPct(r: OptimisationChannelRow): number {
  return r.spend_delta_pct ?? r.delta_pct ?? (rowCurSpend(r) > 0 ? (100 * rowDeltaSpend(r)) / rowCurSpend(r) : 0);
}

function rowDeltaInc(r: OptimisationChannelRow): number {
  return r.inc_sales_delta ?? r.delta_incremental ?? rowOptInc(r) - rowCurInc(r);
}

/** The page's own title and description, which Gemini's explanations of its charts and tables may use. */
const OPTIMIZER_EXPLAIN_SCOPE = {
  sectionTitle: "Budget Optimiser · Meridian Scenario Planner",
  sectionSubtitle:
    "Configure budget and channel constraints, compare counterfactual spend allocations, and inspect diminishing-returns response curves.",
};

export const OptimizerPage: React.FC<{ runId: string | null }> = (props) => (
  <ExplainScope value={OPTIMIZER_EXPLAIN_SCOPE}>
    <OptimizerPlanner {...props} />
  </ExplainScope>
);

const OptimizerPlanner: React.FC<{ runId: string | null }> = ({ runId: activeRunId }) => {
  const { data: run } = useRun(activeRunId);
  const { data: section, isLoading: secLoading } = useSection(activeRunId, "optimiser");
  const { data: ctx, isLoading: ctxLoading } = useOptimiserContext(activeRunId);
  const { data: savedList } = useOptimisations(activeRunId);
  const qc = useQueryClient();
  const formId = useId();

  // Active scenario displayed across all sections
  const [selectedScenarioKey, setSelectedScenarioKey] = useState<ActiveScenarioKey>("default_constrained");
  const customOptId =
    selectedScenarioKey !== "default_constrained" && selectedScenarioKey !== "default_unconstrained"
      ? selectedScenarioKey
      : null;
  const { data: customOpt } = useOptimisation(customOptId);

  // Setup configuration form state (Steps 1–4 of Meridian Scenario Planner)
  const [setupExpanded, setSetupExpanded] = useState<boolean>(false);
  const [scenarioName, setScenarioName] = useState<string>("Custom Scenario Plan");
  const [budgetMode, setBudgetMode] = useState<BudgetConstraintMode>("fixed");
  const [budgetInput, setBudgetInput] = useState<string>("");
  const [targetRoiInput, setTargetRoiInput] = useState<string>("");
  const [targetMroiInput, setTargetMroiInput] = useState<string>("1.00");
  const [fieldError, setFieldError] = useState<string | null>(null);

  const [globalShiftRatio, setGlobalShiftRatio] = useState<number>(0.3);
  const [includeCatalog, setIncludeCatalog] = useState<boolean>(true);
  const [holdDemandFollowingFixed, setHoldDemandFollowingFixed] = useState<boolean>(true);
  // Per-channel custom [lowerRatio, upperRatio] overrides (e.g. [0.2, 0.4] means -20% to +40%)
  const [channelRatioOverrides, setChannelRatioOverrides] = useState<Record<string, [number, number]>>({});
  const [lockedChannels, setLockedChannels] = useState<Record<string, boolean>>({});

  // Interactive chart controls
  const [outcomeChartMode, setOutcomeChartMode] = useState<"grouped" | "waterfall">("grouped");
  const [curveViewMode, setCurveViewMode] = useState<"single" | "grid">("single");
  const [selectedChannelSlug, setSelectedChannelSlug] = useState<string>("display");

  const [busy, setBusy] = useState(false);
  const [submitErr, setSubmitErr] = useState<string | null>(null);

  // Reset state when switching runs
  useEffect(() => {
    setSelectedScenarioKey("default_constrained");
    setChannelRatioOverrides({});
    setLockedChannels({});
    setFieldError(null);
    setSubmitErr(null);
  }, [activeRunId]);

  // Seed default target ROI and selected channel once context loads
  useEffect(() => {
    if (ctx) {
      if (!targetRoiInput) {
        setTargetRoiInput(ctx.historical_roi.toFixed(2));
      }
      if (ctx.channels.length > 0 && !ctx.channels.some((c) => c.channel === selectedChannelSlug)) {
        const firstMovable = ctx.channels.find((c) => !c.demand_following) || ctx.channels[0];
        setSelectedChannelSlug(firstMovable.channel);
      }
    }
  }, [ctx, selectedChannelSlug, targetRoiInput]);

  // Effective per-channel bounds in Step 4 configuration table
  const channelConfigRows = useMemo(() => {
    if (!ctx) return [];
    return ctx.channels.map((ch: OptimiserChannelMeta) => {
      const lockedByDemandGuardrail = holdDemandFollowingFixed && ch.demand_following;
      const lockedByPrintGuardrail = !includeCatalog && ch.is_print;
      const lockedManually = Boolean(lockedChannels[ch.channel]);
      const isLocked = lockedByDemandGuardrail || lockedByPrintGuardrail || lockedManually;

      const override = channelRatioOverrides[ch.channel];
      const downRatio = isLocked ? 0 : override ? override[0] : globalShiftRatio;
      const upRatio = isLocked ? 0 : override ? override[1] : globalShiftRatio;

      const minMult = Math.max(0, Number((1 - downRatio).toFixed(4)));
      const maxMult = Math.min(2.0, Number((1 + upRatio).toFixed(4)));
      const minSpendEur = ch.historical_spend * minMult;
      const maxSpendEur = Math.min(ch.curve_max_spend, ch.historical_spend * maxMult);

      let lockReason: string | null = null;
      if (lockedByDemandGuardrail) lockReason = "Demand-following guardrail";
      else if (lockedByPrintGuardrail) lockReason = "Print lead-time guardrail";
      else if (lockedManually) lockReason = "Locked in table";

      return {
        ...ch,
        isLocked,
        lockReason,
        downRatio,
        upRatio,
        minMult,
        maxMult,
        minSpendEur,
        maxSpendEur,
      };
    });
  }, [ctx, holdDemandFollowingFixed, includeCatalog, lockedChannels, channelRatioOverrides, globalShiftRatio]);

  const feasibleBudgetRange = useMemo(() => {
    const lo = channelConfigRows.reduce((s, r) => s + r.minSpendEur, 0);
    const hi = channelConfigRows.reduce((s, r) => s + r.maxSpendEur, 0);
    const hist = channelConfigRows.reduce((s, r) => s + r.historical_spend, 0);
    return { lo, hi, hist };
  }, [channelConfigRows]);

  // Validate numeric input on blur (per modern-web-guidance: validate on blur, clear immediately on input)
  const validateStep2Input = (mode: BudgetConstraintMode = budgetMode): string | null => {
    if (mode === "fixed" && budgetInput.trim() !== "") {
      const val = Number(budgetInput.replace(/,/g, ""));
      if (!Number.isFinite(val) || val <= 0) {
        return "Enter a positive total budget in euros (or leave blank to keep historical spend).";
      }
      if (ctx && (val < feasibleBudgetRange.lo - 1 || val > feasibleBudgetRange.hi + 1)) {
        return `Budget ${formatEur(val)} is outside the feasible channel bounds (${formatEur(feasibleBudgetRange.lo)} – ${formatEur(feasibleBudgetRange.hi)}). Change the budget or widen the channel limits in Step 3.`;
      }
    } else if (mode === "target_roi") {
      const val = Number(targetRoiInput);
      if (!Number.isFinite(val) || val <= 0 || val > 50) {
        return "Enter a target total ROI between 0.10× and 50.0×.";
      }
    } else if (mode === "target_mroi") {
      const val = Number(targetMroiInput);
      if (!Number.isFinite(val) || val <= 0 || val > 50) {
        return "Enter a target marginal ROI between 0.10× and 50.0×.";
      }
    }
    return null;
  };

  const handleRunOptimisation = async (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    if (!activeRunId || !ctx) return;

    const validationMessage = validateStep2Input(budgetMode);
    if (validationMessage) {
      setFieldError(validationMessage);
      return;
    }

    setBusy(true);
    setSubmitErr(null);
    setFieldError(null);
    try {
      const customBounds: Record<string, [number, number]> = {};
      channelConfigRows.forEach((r) => {
        customBounds[r.channel] = [r.minMult, r.maxMult];
      });

      const parsedBudget =
        budgetMode === "fixed" && budgetInput.trim() !== ""
          ? Number(budgetInput.replace(/,/g, ""))
          : null;
      const parsedTargetValue =
        budgetMode === "target_roi"
          ? Number(targetRoiInput || ctx.historical_roi)
          : budgetMode === "target_mroi"
          ? Number(targetMroiInput || 1.0)
          : null;

      const defaultName =
        budgetMode === "fixed"
          ? `Fixed Budget (${parsedBudget ? formatEur(parsedBudget) : "Historical"} · ±${Math.round(globalShiftRatio * 100)}%)`
          : budgetMode === "target_roi"
          ? `Flexible · Target ROI ≥ ${(parsedTargetValue ?? ctx.historical_roi).toFixed(2)}×`
          : `Flexible · Target mROI ≥ ${(parsedTargetValue ?? 1.0).toFixed(2)}×`;

      const res = await api.createOptimisation(activeRunId, {
        name: scenarioName.trim() || defaultName,
        budget_mode: budgetMode,
        total_budget: parsedBudget,
        target_value: parsedTargetValue,
        min_multiplier: Math.max(0, Number((1 - globalShiftRatio).toFixed(2))),
        max_multiplier: Math.min(2.0, Number((1 + globalShiftRatio).toFixed(2))),
        include_catalog: includeCatalog,
        include_demand_following: !holdDemandFollowingFixed,
        channel_bounds: customBounds,
      });
      setSelectedScenarioKey(res.id);
      await qc.invalidateQueries({ queryKey: ["optimisations", activeRunId] });
    } catch (err) {
      setSubmitErr(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  // Resolve the active scenario result to render across Sections 2, 3, 4, and 5
  const activeResult: OptimisationResult | null = useMemo(() => {
    if (!ctx) return null;
    if (selectedScenarioKey === "default_unconstrained") {
      return ctx.default_unconstrained;
    }
    if (selectedScenarioKey === "default_constrained") {
      return ctx.default_constrained;
    }
    if (customOpt?.result) {
      return customOpt.result;
    }
    const fromList = savedList?.optimisations.find((o) => o.id === selectedScenarioKey);
    // A queued, running or failed custom scenario has no numbers yet: never show another scenario's under its name
    return fromList?.result ?? null;
  }, [ctx, selectedScenarioKey, customOpt, savedList]);

  const labelOf = useMemo(() => {
    const map = new Map((ctx?.channels ?? []).map((c) => [c.channel, c.label || c.channel]));
    return (slug: string) => map.get(slug) ?? slug;
  }, [ctx]);
  const upperNames = (ctx?.upper_bound_channels ?? []).map(labelOf);
  const movableNames = (ctx?.channels ?? []).filter((c) => !(ctx?.upper_bound_channels ?? []).includes(c.channel)).map((c) => c.label || c.channel);
  const joinNames = (names: string[]) => (names.length <= 1 ? names.join("") : `${names.slice(0, -1).join(", ")} and ${names[names.length - 1]}`);
  const customStatus = customOpt?.status ?? savedList?.optimisations.find((o) => o.id === selectedScenarioKey)?.status;

  const activeRows: OptimisationChannelRow[] = useMemo(() => {
    if (!activeResult) return [];
    return activeResult.allocations || activeResult.channels || [];
  }, [activeResult]);

  const rowsBySlug = useMemo(() => {
    const map: Record<string, OptimisationChannelRow> = {};
    activeRows.forEach((r) => {
      map[r.channel] = r;
    });
    return map;
  }, [activeRows]);

  // Derived summary metrics for Section 2 (Optimization overview)
  const overviewMetrics = useMemo(() => {
    if (!activeResult) return null;
    const histBudget =
      activeResult.historical_budget ??
      activeResult.historical_spend_total ??
      activeRows.reduce((s, r) => s + rowCurSpend(r), 0);
    const optBudget =
      activeResult.total_budget ?? activeRows.reduce((s, r) => s + r.optimised_spend, 0);
    const budgetDelta = optBudget - histBudget;
    const budgetDeltaPct = histBudget > 0 ? (100 * budgetDelta) / histBudget : 0;

    const histInc =
      activeResult.current_inc_sales ??
      activeResult.historical_incremental_revenue ??
      activeRows.reduce((s, r) => s + rowCurInc(r), 0);
    const optInc =
      activeResult.optimised_inc_sales ??
      activeResult.optimised_incremental_revenue ??
      activeRows.reduce((s, r) => s + rowOptInc(r), 0);
    const incDelta = optInc - histInc;
    const incDeltaPct = histInc > 0 ? (100 * incDelta) / histInc : 0;

    const histRoi = activeResult.historical_roi ?? (histBudget > 0 ? histInc / histBudget : 0);
    const optRoi = activeResult.optimised_roi ?? (optBudget > 0 ? optInc / optBudget : 0);
    const roiDelta = optRoi - histRoi;

    let reallocationLabel = "Balanced allocation";
    if (activeRows.length > 0) {
      const donor = [...activeRows].sort((a, b) => rowDeltaSpend(a) - rowDeltaSpend(b))[0];
      const recipient = [...activeRows].sort((a, b) => rowDeltaSpend(b) - rowDeltaSpend(a))[0];
      if (rowDeltaSpend(donor) < -100 && rowDeltaSpend(recipient) > 100 && donor.channel !== recipient.channel) {
        reallocationLabel = `${donor.label || donor.channel} → ${recipient.label || recipient.channel}`;
      } else if (rowDeltaSpend(recipient) > 100) {
        reallocationLabel = `Scale up ${recipient.label || recipient.channel}`;
      } else if (rowDeltaSpend(donor) < -100) {
        reallocationLabel = `Scale down ${donor.label || donor.channel}`;
      }
    }

    return {
      histBudget,
      optBudget,
      budgetDelta,
      budgetDeltaPct,
      histInc,
      optInc,
      incDelta,
      incDeltaPct,
      histRoi,
      optRoi,
      roiDelta,
      reallocationLabel,
    };
  }, [activeResult, activeRows]);

  // The KPI tiles' figures, so every explanation on the page knows the scenario's headline result
  useExplainPageNotes(
    overviewMetrics
      ? [
          `Scenario shown: ${
            selectedScenarioKey === "default_constrained"
              ? "recommended (demand-following channels held fixed)"
              : selectedScenarioKey === "default_unconstrained"
              ? "unconstrained (every channel may move)"
              : "custom constraints"
          }`,
          `Optimised total budget ${formatEur(overviewMetrics.optBudget)} (non-optimised ${formatEur(overviewMetrics.histBudget)})`,
          `Optimised total ROI ${overviewMetrics.optRoi.toFixed(2)}× (non-optimised ${overviewMetrics.histRoi.toFixed(2)}×)`,
          `Optimised total outcome ${formatEur(overviewMetrics.optInc)}, ${formatEurSigned(overviewMetrics.incDelta)} (${formatPctSigned(overviewMetrics.incDeltaPct)}) vs ${formatEur(overviewMetrics.histInc)}`,
          `Primary re-allocation: ${overviewMetrics.reallocationLabel}`,
        ].join(". ") + "."
      : null
  );

  // Build Plotly figures for Sections 3 and 4
  const spendDeltaSpec = useMemo(
    () => (activeRows.length ? buildSpendDeltaFigure(activeRows) : null),
    [activeRows]
  );
  const spendDonutSpec = useMemo(
    () =>
      activeRows.length && overviewMetrics
        ? buildSpendDonutFigure(activeRows, overviewMetrics.optBudget)
        : null,
    [activeRows, overviewMetrics]
  );
  const budgetComparisonSpec = useMemo(
    () => (activeRows.length ? buildBudgetComparisonFigure(activeRows) : null),
    [activeRows]
  );
  const outcomeComparisonSpec = useMemo(
    () =>
      activeRows.length && activeResult
        ? buildOutcomeComparisonFigure(activeRows, outcomeChartMode, activeResult)
        : null,
    [activeRows, outcomeChartMode, activeResult]
  );

  const selectedChannelMeta = useMemo(
    () =>
      ctx?.channels.find((c) => c.channel === selectedChannelSlug) ||
      ctx?.channels[0] ||
      null,
    [ctx, selectedChannelSlug]
  );

  const singleCurveSpec = useMemo(() => {
    if (!ctx || !selectedChannelMeta) return null;
    const row = rowsBySlug[selectedChannelMeta.channel];
    const pts = ctx.response_curves[selectedChannelMeta.channel] || [];
    if (!row || !pts.length) return null;
    return buildSingleResponseCurveFigure(selectedChannelMeta, row, pts);
  }, [ctx, selectedChannelMeta, rowsBySlug]);

  const marginalRoiSpec = useMemo(() => {
    if (!ctx || !selectedChannelMeta) return null;
    const row = rowsBySlug[selectedChannelMeta.channel];
    const pts = ctx.response_curves[selectedChannelMeta.channel] || [];
    if (!row || !pts.length) return null;
    const targetMroi =
      activeResult?.budget_mode === "target_mroi" ? activeResult.target_value : null;
    return buildMarginalRoiCurveFigure(selectedChannelMeta, row, pts, targetMroi);
  }, [ctx, selectedChannelMeta, rowsBySlug, activeResult]);

  const allCurvesGridSpec = useMemo(() => {
    if (!ctx || !Object.keys(rowsBySlug).length) return null;
    return buildAllResponseCurvesGridFigure(ctx.channels, rowsBySlug, ctx.response_curves);
  }, [ctx, rowsBySlug]);

  const tocItems = useMemo<TocEntry[]>(
    () => [
      { id: "optimiser-setup", label: "Scenario settings" },
      { id: "optimiser-overview", label: "Result" },
      { id: "optimiser-allocation", label: "Where the budget moves" },
      { id: "optimiser-response-curves", label: "Response curves" },
      { id: "optimiser_lift_decomposition", label: "Unconstrained lift" },
    ],
    []
  );

  if (run?.summary?.skip_meridian) {
    return (
      <Box>
        <PageHeader description="Re-allocate the same media budget along each channel's fitted response curve." />
        <Alert severity="info">
          This run skipped the Meridian model fit, so posterior response curves are not available. Re-run with Meridian enabled to use the Scenario Planner.
        </Alert>
      </Box>
    );
  }

  const customJobRunning =
    customOpt && (customOpt.status === "queued" || customOpt.status === "running");

  return (
    <Box>
      <PageHeader
        description="Re-allocate the same media budget along each channel's fitted response curve: the recommended scenario first, your own constraints in the scenario settings."
        actions={
          ctx ? (
            <Stack direction="row" spacing={1.25} alignItems="center" flexWrap="wrap" useFlexGap>
              <FormControl size="small" sx={{ minWidth: 290 }}>
                <InputLabel id={`${formId}-scenario-select-label`}>Active scenario</InputLabel>
                <Select
                  labelId={`${formId}-scenario-select-label`}
                  label="Active scenario"
                  value={selectedScenarioKey}
                  onChange={(e) => setSelectedScenarioKey(e.target.value)}
                  sx={{ bgcolor: PALETTE.bgCard }}
                >
                  <MenuItem value="default_constrained">
                    Recommended: {upperNames.join(", ") || "Demand-following"} held fixed (±30%)
                  </MenuItem>
                  <MenuItem value="default_unconstrained">
                    Unconstrained: all channels ±30% (Meridian BudgetOptimizer)
                  </MenuItem>
                  {(savedList?.optimisations || []).map((opt) => (
                    <MenuItem key={opt.id} value={opt.id}>
                      {opt.name || opt.id} ({opt.status})
                    </MenuItem>
                  ))}
                </Select>
              </FormControl>
              <Button
                variant="outlined"
                size="small"
                startIcon={<TuneRoundedIcon />}
                onClick={() => {
                  setSetupExpanded(true);
                  document.getElementById("optimiser-setup")?.scrollIntoView({ behavior: "smooth" });
                }}
              >
                Configure scenario
              </Button>
            </Stack>
          ) : undefined
        }
      />

      <Box
        sx={{
          display: "grid",
          gridTemplateColumns: { xs: "minmax(0, 1fr)", xl: "minmax(0, 1fr) 216px" },
          gap: { xs: 2.5, xl: 3.5 },
          alignItems: "start",
        }}
      >
        <Box sx={{ minWidth: 0 }}>
          {/* =====================================================================
              SECTION 1: SETUP OPTIMIZATION CONFIGURATIONS (Steps 1-4, Figs 15-16)
             ===================================================================== */}
          <Paper
            id="optimiser-setup"
            data-anchor
            component="section"
            aria-labelledby={`${formId}-setup-heading`}
            sx={{ p: { xs: 2.25, md: 3 }, mb: 3, bgcolor: PALETTE.bgCard }}
          >
            <Stack
              direction={{ xs: "column", sm: "row" }}
              justifyContent="space-between"
              alignItems={{ xs: "flex-start", sm: "center" }}
              spacing={1.5}
            >
              <Box>
                <Stack direction="row" spacing={1} alignItems="center" sx={{ mb: 0.5 }}>
                  {ctx && (
                    <Typography variant="caption" color="text.secondary" sx={NUMERIC}>
                      {ctx.start_date} – {ctx.end_date} ({ctx.modelling_days} days)
                    </Typography>
                  )}
                </Stack>
                <Typography id={`${formId}-setup-heading`} variant="h2">
                  Scenario settings
                </Typography>
                <Typography variant="body2" color="text.secondary">
                  Define the analysis period, budget constraint mode (fixed budget, target total ROI, or target channel mROI), and per-channel spend limits before running the SLSQP response-curve solver.
                </Typography>
              </Box>
              <Button
                size="small"
                variant="text"
                onClick={() => setSetupExpanded((prev) => !prev)}
                endIcon={setupExpanded ? <ExpandLessRoundedIcon /> : <ExpandMoreRoundedIcon />}
                aria-expanded={setupExpanded}
                aria-controls={`${formId}-setup-form-collapse`}
              >
                {setupExpanded ? "Hide setup" : "Show setup"}
              </Button>
            </Stack>

            <Collapse in={setupExpanded} id={`${formId}-setup-form-collapse`}>
              <Divider sx={{ my: 2.5 }} />
              {ctxLoading && !ctx ? (
                <Stack spacing={1.5}>
                  <Skeleton variant="rounded" height={64} />
                  <Skeleton variant="rounded" height={180} />
                </Stack>
              ) : ctx ? (
                <Box component="form" onSubmit={handleRunOptimisation} noValidate>
                  <Stack spacing={3}>
                    {/* STEP 1: Date range & historical baseline */}
                    <Box
                      component="fieldset"
                      sx={{
                        border: `1px solid ${PALETTE.borderSubtle}`,
                        borderRadius: 1,
                        p: 2,
                        m: 0,
                        bgcolor: PALETTE.bgSubtle,
                      }}
                    >
                      <Typography
                        component="legend"
                        sx={{
                          px: 1,
                          fontWeight: 700,
                          fontSize: "0.82rem",
                          color: PALETTE.brandNavy,
                          textTransform: "uppercase",
                          letterSpacing: "0.04em",
                        }}
                      >
                        Step 1 · Select date range &amp; baseline
                      </Typography>
                      <Box
                        sx={{
                          display: "grid",
                          gridTemplateColumns: "repeat(auto-fit, minmax(min(100%, 200px), 1fr))",
                          gap: 2,
                        }}
                      >
                        <Box>
                          <Typography variant="caption" color="text.secondary" sx={{ fontWeight: 600 }}>
                            Modelling window
                          </Typography>
                          <Typography variant="body1" sx={{ fontWeight: 700, ...NUMERIC }}>
                            {ctx.start_date} → {ctx.end_date}
                          </Typography>
                          <Typography variant="caption" color="text.secondary">
                            {ctx.modelling_days} daily observations across {ctx.channels.length} media channels
                          </Typography>
                        </Box>
                        <Box>
                          <Typography variant="caption" color="text.secondary" sx={{ fontWeight: 600 }}>
                            Non-optimised total budget
                          </Typography>
                          <Typography variant="body1" sx={{ fontWeight: 700, ...NUMERIC }}>
                            {formatEur(ctx.historical_budget)}
                          </Typography>
                          <Typography variant="caption" color="text.secondary">
                            Historical total media spend
                          </Typography>
                        </Box>
                        <Box>
                          <Typography variant="caption" color="text.secondary" sx={{ fontWeight: 600 }}>
                            Non-optimised incremental outcome
                          </Typography>
                          <Typography variant="body1" sx={{ fontWeight: 700, ...NUMERIC }}>
                            {formatEur(ctx.historical_incremental)}
                          </Typography>
                          <Typography variant="caption" color="text.secondary">
                            Posterior mean media-attributed sales
                          </Typography>
                        </Box>
                        <Box>
                          <Typography variant="caption" color="text.secondary" sx={{ fontWeight: 600 }}>
                            Non-optimised total ROI
                          </Typography>
                          <Typography variant="body1" sx={{ fontWeight: 700, ...NUMERIC }}>
                            {ctx.historical_roi.toFixed(2)}×
                          </Typography>
                          <Typography variant="caption" color="text.secondary">
                            Incremental outcome ÷ total media spend
                          </Typography>
                        </Box>
                      </Box>
                    </Box>

                    {/* STEP 2 & STEP 3 side-by-side on desktop */}
                    <Box
                      sx={{
                        display: "grid",
                        gridTemplateColumns: { xs: "1fr", lg: "1.1fr 1fr" },
                        gap: 2.5,
                      }}
                    >
                      {/* STEP 2: Budget constraints (Fixed vs Flexible Target ROI / Target mROI) */}
                      <Box
                        component="fieldset"
                        sx={{
                          border: `1px solid ${PALETTE.borderSubtle}`,
                          borderRadius: 1,
                          p: 2.25,
                          m: 0,
                        }}
                      >
                        <Typography
                          component="legend"
                          sx={{
                            px: 1,
                            fontWeight: 700,
                            fontSize: "0.82rem",
                            color: PALETTE.brandNavy,
                            textTransform: "uppercase",
                            letterSpacing: "0.04em",
                          }}
                        >
                          Step 2 · Budget constraints
                        </Typography>

                        <RadioGroup
                          name="budget-constraint-mode"
                          value={budgetMode}
                          onChange={(e) => {
                            const nextMode = e.target.value as BudgetConstraintMode;
                            setBudgetMode(nextMode);
                            setFieldError(null);
                          }}
                        >
                          <FormControlLabel
                            value="fixed"
                            control={<Radio size="small" />}
                            sx={{ alignItems: "flex-start", mb: 1 }}
                            label={
                              <Box sx={{ pt: 0.25 }}>
                                <Typography variant="body2" sx={{ fontWeight: 700, color: PALETTE.inkPrimary }}>
                                  Fixed budget
                                </Typography>
                                <Typography variant="caption" color="text.secondary">
                                  Finds the optimal allocation across channels for a given total budget to maximise incremental sales.
                                </Typography>
                              </Box>
                            }
                          />
                          <FormControlLabel
                            value="target_roi"
                            control={<Radio size="small" />}
                            sx={{ alignItems: "flex-start", mb: 1 }}
                            label={
                              <Box sx={{ pt: 0.25 }}>
                                <Typography variant="body2" sx={{ fontWeight: 700, color: PALETTE.inkPrimary }}>
                                  Flexible · Target total ROI
                                </Typography>
                                <Typography variant="caption" color="text.secondary">
                                  Determines how much more you can spend while maintaining a target minimum ROI on the total budget.
                                </Typography>
                              </Box>
                            }
                          />
                          <FormControlLabel
                            value="target_mroi"
                            control={<Radio size="small" />}
                            sx={{ alignItems: "flex-start" }}
                            label={
                              <Box sx={{ pt: 0.25 }}>
                                <Typography variant="body2" sx={{ fontWeight: 700, color: PALETTE.inkPrimary }}>
                                  Flexible · Target channel mROI
                                </Typography>
                                <Typography variant="caption" color="text.secondary">
                                  Determines the maximum spend on each channel while achieving a minimum marginal ROI on every channel.
                                </Typography>
                              </Box>
                            }
                          />
                        </RadioGroup>

                        <Divider sx={{ my: 2 }} />

                        {budgetMode === "fixed" && (
                          <Box>
                            <TextField
                              id={`${formId}-fixed-budget-input`}
                              label="Total media budget (€)"
                              type="text"
                              inputMode="numeric"
                              size="small"
                              fullWidth
                              placeholder={`Historical: ${Math.round(ctx.historical_budget).toLocaleString("en-US")}`}
                              value={budgetInput}
                              onChange={(e) => {
                                setBudgetInput(e.target.value);
                                if (fieldError) setFieldError(null);
                              }}
                              onBlur={() => setFieldError(validateStep2Input("fixed"))}
                              error={Boolean(fieldError)}
                              helperText={
                                fieldError ||
                                `Feasible total budget given Step 4 bounds: ${formatEur(feasibleBudgetRange.lo)} – ${formatEur(feasibleBudgetRange.hi)} (leave blank for ${formatEur(ctx.historical_budget)}).`
                              }
                            />
                            <Stack direction="row" spacing={0.75} sx={{ mt: 1.25 }} flexWrap="wrap" useFlexGap>
                              {[
                                { label: `Historical (${formatEur(ctx.historical_budget)})`, val: "" },
                                {
                                  label: "−10% budget",
                                  val: String(Math.round(ctx.historical_budget * 0.9)),
                                },
                                {
                                  label: "+10% budget",
                                  val: String(Math.round(ctx.historical_budget * 1.1)),
                                },
                                {
                                  label: "+20% budget",
                                  val: String(Math.round(ctx.historical_budget * 1.2)),
                                },
                              ].map((preset) => (
                                <Chip
                                  key={preset.label}
                                  label={preset.label}
                                  size="small"
                                  variant={budgetInput === preset.val ? "filled" : "outlined"}
                                  color={budgetInput === preset.val ? "primary" : "default"}
                                  onClick={() => {
                                    setBudgetInput(preset.val);
                                    setFieldError(null);
                                  }}
                                />
                              ))}
                            </Stack>
                          </Box>
                        )}

                        {budgetMode === "target_roi" && (
                          <Box>
                            <TextField
                              id={`${formId}-target-roi-input`}
                              label="Target minimum total ROI (×)"
                              type="text"
                              inputMode="decimal"
                              size="small"
                              fullWidth
                              value={targetRoiInput}
                              onChange={(e) => {
                                setTargetRoiInput(e.target.value);
                                if (fieldError) setFieldError(null);
                              }}
                              onBlur={() => setFieldError(validateStep2Input("target_roi"))}
                              error={Boolean(fieldError)}
                              helperText={
                                fieldError ||
                                `Non-optimised total ROI is ${ctx.historical_roi.toFixed(2)}×. Lowering the target allows total spend to expand up to the channel upper bounds.`
                              }
                            />
                            <Stack direction="row" spacing={0.75} sx={{ mt: 1.25 }} flexWrap="wrap" useFlexGap>
                              {[
                                { label: `Historical (${ctx.historical_roi.toFixed(2)}×)`, val: ctx.historical_roi.toFixed(2) },
                                { label: "2.50×", val: "2.50" },
                                { label: "3.00×", val: "3.00" },
                                { label: "3.50×", val: "3.50" },
                              ].map((preset) => (
                                <Chip
                                  key={preset.label}
                                  label={preset.label}
                                  size="small"
                                  variant={targetRoiInput === preset.val ? "filled" : "outlined"}
                                  color={targetRoiInput === preset.val ? "primary" : "default"}
                                  onClick={() => {
                                    setTargetRoiInput(preset.val);
                                    setFieldError(null);
                                  }}
                                />
                              ))}
                            </Stack>
                          </Box>
                        )}

                        {budgetMode === "target_mroi" && (
                          <Box>
                            <TextField
                              id={`${formId}-target-mroi-input`}
                              label="Target minimum marginal ROI per channel (×)"
                              type="text"
                              inputMode="decimal"
                              size="small"
                              fullWidth
                              value={targetMroiInput}
                              onChange={(e) => {
                                setTargetMroiInput(e.target.value);
                                if (fieldError) setFieldError(null);
                              }}
                              onBlur={() => setFieldError(validateStep2Input("target_mroi"))}
                              error={Boolean(fieldError)}
                              helperText={
                                fieldError ||
                                "1.00× is marginal break-even (€1 of incremental sales for the last €1 spent). Channels below target stay at their lower bound."
                              }
                            />
                            <Stack direction="row" spacing={0.75} sx={{ mt: 1.25 }} flexWrap="wrap" useFlexGap>
                              {[
                                { label: "Break-even (1.00×)", val: "1.00" },
                                { label: "1.25×", val: "1.25" },
                                { label: "1.50×", val: "1.50" },
                                { label: "2.00×", val: "2.00" },
                              ].map((preset) => (
                                <Chip
                                  key={preset.label}
                                  label={preset.label}
                                  size="small"
                                  variant={targetMroiInput === preset.val ? "filled" : "outlined"}
                                  color={targetMroiInput === preset.val ? "primary" : "default"}
                                  onClick={() => {
                                    setTargetMroiInput(preset.val);
                                    setFieldError(null);
                                  }}
                                />
                              ))}
                            </Stack>
                          </Box>
                        )}
                      </Box>

                      {/* STEP 3: Channel spend constraints & guardrails */}
                      <Box
                        component="fieldset"
                        sx={{
                          border: `1px solid ${PALETTE.borderSubtle}`,
                          borderRadius: 1,
                          p: 2.25,
                          m: 0,
                        }}
                      >
                        <Typography
                          component="legend"
                          sx={{
                            px: 1,
                            fontWeight: 700,
                            fontSize: "0.82rem",
                            color: PALETTE.brandNavy,
                            textTransform: "uppercase",
                            letterSpacing: "0.04em",
                          }}
                        >
                          Step 3 · Channel spend constraints
                        </Typography>

                        <Typography variant="body2" sx={{ fontWeight: 650, mb: 0.5, ...NUMERIC }}>
                          Default spend shift bounds: −{Math.round(globalShiftRatio * 100)}% to +{Math.round(globalShiftRatio * 100)}% ({(1 - globalShiftRatio).toFixed(2)}× – {(1 + globalShiftRatio).toFixed(2)}× historical spend)
                        </Typography>
                        <Typography variant="caption" color="text.secondary" sx={{ display: "block", mb: 1.5 }}>
                          Sets the exploration space around each channel&apos;s historical spend. Individual channels can be overridden or locked in Step 4.
                        </Typography>

                        <Box sx={{ px: 1 }}>
                          <Slider
                            value={Math.round(globalShiftRatio * 100)}
                            min={10}
                            max={100}
                            step={5}
                            marks={[
                              { value: 10, label: "±10%" },
                              { value: 30, label: "±30%" },
                              { value: 50, label: "±50%" },
                              { value: 100, label: "±100%" },
                            ]}
                            valueLabelDisplay="auto"
                            valueLabelFormat={(v) => `±${v}%`}
                            aria-label="Default channel spend shift ratio"
                            onChange={(_, v) => {
                              setGlobalShiftRatio((v as number) / 100);
                              if (fieldError) setFieldError(null);
                            }}
                          />
                        </Box>

                        <Stack direction="row" spacing={0.75} sx={{ mt: 1, mb: 2 }} flexWrap="wrap" useFlexGap>
                          {[0.1, 0.2, 0.3, 0.5].map((ratio) => (
                            <Chip
                              key={ratio}
                              size="small"
                              label={`±${Math.round(ratio * 100)}%${ratio === 0.3 ? " (Meridian default)" : ""}`}
                              variant={Math.abs(globalShiftRatio - ratio) < 0.001 ? "filled" : "outlined"}
                              color={Math.abs(globalShiftRatio - ratio) < 0.001 ? "primary" : "default"}
                              onClick={() => {
                                setGlobalShiftRatio(ratio);
                                setFieldError(null);
                              }}
                            />
                          ))}
                          {(Object.keys(channelRatioOverrides).length > 0 ||
                            Object.keys(lockedChannels).length > 0) && (
                            <Chip
                              size="small"
                              icon={<RestartAltRoundedIcon />}
                              label="Reset per-channel overrides"
                              onClick={() => {
                                setChannelRatioOverrides({});
                                setLockedChannels({});
                              }}
                            />
                          )}
                        </Stack>

                        <Divider sx={{ my: 1.5 }} />

                        <Stack spacing={1}>
                          {ctx.upper_bound_channels.length > 0 && (
                            <FormControlLabel
                              control={
                                <Switch
                                  size="small"
                                  checked={holdDemandFollowingFixed}
                                  onChange={(_, checked) => setHoldDemandFollowingFixed(checked)}
                                />
                              }
                              label={
                                <Box>
                                  <Typography variant="body2" sx={{ fontWeight: 650, color: PALETTE.inkPrimary }}>
                                    Hold demand-following channels fixed ({upperNames.join(", ")})
                                  </Typography>
                                  <Typography variant="caption" color="text.secondary">
                                    Recommended: their CPC spend scales with consumer search/comparison demand, so observational ROI is an upper bound.
                                  </Typography>
                                </Box>
                              }
                            />
                          )}
                          {ctx.print_channels.length > 0 && (
                            <FormControlLabel
                              control={
                                <Switch
                                  size="small"
                                  checked={includeCatalog}
                                  onChange={(_, checked) => setIncludeCatalog(checked)}
                                />
                              }
                              label={
                                <Box>
                                  <Typography variant="body2" sx={{ fontWeight: 650, color: PALETTE.inkPrimary }}>
                                    Allow print event budget ({ctx.print_channels.map(labelOf).join(", ")}) to move
                                  </Typography>
                                  <Typography variant="caption" color="text.secondary">
                                    Turn off when print production contracts are already committed for the planning horizon.
                                  </Typography>
                                </Box>
                              }
                            />
                          )}
                        </Stack>
                      </Box>
                    </Box>

                    {/* STEP 4: Review configuration in the configuration table (Figure 16) */}
                    <Box
                      component="fieldset"
                      sx={{
                        border: `1px solid ${PALETTE.borderSubtle}`,
                        borderRadius: 1,
                        p: { xs: 1.5, md: 2.25 },
                        m: 0,
                      }}
                    >
                      <Typography
                        component="legend"
                        sx={{
                          px: 1,
                          fontWeight: 700,
                          fontSize: "0.82rem",
                          color: PALETTE.brandNavy,
                          textTransform: "uppercase",
                          letterSpacing: "0.04em",
                        }}
                      >
                        Step 4 · Review configuration in the configuration table
                      </Typography>

                      <Stack direction="row" justifyContent="flex-end" sx={{ mb: 1 }}>
                        <ExplainButton
                          kind="table"
                          title="Channel spend constraint configuration"
                          caption="Channel spend lower and upper bound ratios and euro limits for the optimisation scenario"
                          targetId={`${formId}-constraints-table`}
                        />
                      </Stack>
                      <TableContainer
                        id={`${formId}-constraints-table`}
                        tabIndex={0}
                        role="region"
                        aria-label="Channel spend constraint configuration table"
                        sx={{ overflowX: "auto" }}
                      >
                        <Table size="small">
                          <caption className="visually-hidden">
                            Channel spend lower and upper bound ratios and euro limits for the optimisation scenario
                          </caption>
                          <TableHead>
                            <TableRow>
                              <TableCell scope="col">Channels</TableCell>
                              <TableCell scope="col" align="right">
                                Non-optimised spend
                              </TableCell>
                              <TableCell scope="col" align="right">
                                Spend Lower Bound Ratio
                              </TableCell>
                              <TableCell scope="col" align="right">
                                Spend Upper Bound Ratio
                              </TableCell>
                              <TableCell scope="col" align="right">
                                Spend lower bound
                              </TableCell>
                              <TableCell scope="col" align="right">
                                Spend upper bound
                              </TableCell>
                            </TableRow>
                          </TableHead>
                          <TableBody>
                            {channelConfigRows.map((row, idx) => {
                              const guarded =
                                (holdDemandFollowingFixed && row.demand_following) ||
                                (!includeCatalog && row.is_print);
                              return (
                                <TableRow key={row.channel} hover>
                                  <TableCell component="th" scope="row">
                                    <Stack direction="row" spacing={1} alignItems="center">
                                      <Tooltip
                                        title={
                                          guarded
                                            ? `${row.lockReason} (toggle in Step 3)`
                                            : row.isLocked
                                            ? "Unlock channel bounds"
                                            : "Lock channel at historical spend"
                                        }
                                      >
                                        <span>
                                          <IconButton
                                            size="small"
                                            disabled={guarded}
                                            aria-label={`${row.isLocked ? "Unlock" : "Lock"} ${row.label} spend bounds`}
                                            aria-pressed={row.isLocked}
                                            onClick={() =>
                                              setLockedChannels((prev) => ({
                                                ...prev,
                                                [row.channel]: !prev[row.channel],
                                              }))
                                            }
                                          >
                                            {row.isLocked ? (
                                              <LockRoundedIcon fontSize="small" sx={{ color: PALETTE.brandPrimary }} />
                                            ) : (
                                              <LockOpenRoundedIcon fontSize="small" sx={{ color: PALETTE.inkMuted }} />
                                            )}
                                          </IconButton>
                                        </span>
                                      </Tooltip>
                                      <Box
                                        sx={{
                                          width: 10,
                                          height: 10,
                                          borderRadius: "50%",
                                          bgcolor: channelColor(row.channel, idx),
                                          flexShrink: 0,
                                        }}
                                      />
                                      <Typography variant="body2" sx={{ fontWeight: 650 }}>
                                        {row.label}
                                      </Typography>
                                      {row.demand_following && (
                                        <Chip
                                          size="small"
                                          label="Upper-bound ROI"
                                          sx={{
                                            height: 20,
                                            fontSize: "0.68rem",
                                            bgcolor: PALETTE.bgAccentTint,
                                            color: PALETTE.brandPrimary,
                                          }}
                                        />
                                      )}
                                      {row.is_print && (
                                        <Chip
                                          size="small"
                                          label="Print event"
                                          variant="outlined"
                                          sx={{ height: 20, fontSize: "0.68rem" }}
                                        />
                                      )}
                                    </Stack>
                                  </TableCell>
                                  <TableCell align="right" sx={NUMERIC}>
                                    {formatEur(row.historical_spend)}
                                  </TableCell>
                                  <TableCell align="right" sx={NUMERIC}>
                                    {row.isLocked ? (
                                      <Typography variant="body2" color="text.secondary" sx={NUMERIC}>
                                        0.00 (locked)
                                      </Typography>
                                    ) : (
                                      <Select
                                        size="small"
                                        variant="standard"
                                        disableUnderline
                                        aria-label={`${row.label} spend lower bound ratio`}
                                        value={row.downRatio}
                                        onChange={(e) => {
                                          const nextDown = Number(e.target.value);
                                          setChannelRatioOverrides((prev) => ({
                                            ...prev,
                                            [row.channel]: [nextDown, row.upRatio],
                                          }));
                                        }}
                                        sx={{ fontSize: "0.84rem", fontWeight: 600, ...NUMERIC }}
                                      >
                                        {RATIO_STEP_OPTIONS.map((opt) => (
                                          <MenuItem key={opt.value} value={opt.value}>
                                            {opt.value === 0 ? "0.00 (0%)" : `−${opt.value.toFixed(2)} (−${Math.round(opt.value * 100)}%)`}
                                          </MenuItem>
                                        ))}
                                      </Select>
                                    )}
                                  </TableCell>
                                  <TableCell align="right" sx={NUMERIC}>
                                    {row.isLocked ? (
                                      <Typography variant="body2" color="text.secondary" sx={NUMERIC}>
                                        0.00 (locked)
                                      </Typography>
                                    ) : (
                                      <Select
                                        size="small"
                                        variant="standard"
                                        disableUnderline
                                        aria-label={`${row.label} spend upper bound ratio`}
                                        value={row.upRatio}
                                        onChange={(e) => {
                                          const nextUp = Number(e.target.value);
                                          setChannelRatioOverrides((prev) => ({
                                            ...prev,
                                            [row.channel]: [row.downRatio, nextUp],
                                          }));
                                        }}
                                        sx={{ fontSize: "0.84rem", fontWeight: 600, ...NUMERIC }}
                                      >
                                        {RATIO_STEP_OPTIONS.map((opt) => (
                                          <MenuItem key={opt.value} value={opt.value}>
                                            {opt.value === 0 ? "0.00 (0%)" : `+${opt.value.toFixed(2)} (+${Math.round(opt.value * 100)}%)`}
                                          </MenuItem>
                                        ))}
                                      </Select>
                                    )}
                                  </TableCell>
                                  <TableCell align="right" sx={NUMERIC}>
                                    {formatEur(row.minSpendEur)}
                                  </TableCell>
                                  <TableCell align="right" sx={NUMERIC}>
                                    {formatEur(row.maxSpendEur)}
                                  </TableCell>
                                </TableRow>
                              );
                            })}
                          </TableBody>
                          <TableFooter>
                            <TableRow sx={{ bgcolor: PALETTE.bgSubtle }}>
                              <TableCell component="th" scope="row" sx={{ fontWeight: 700, color: PALETTE.inkPrimary }}>
                                Total feasible budget range
                              </TableCell>
                              <TableCell align="right" sx={{ fontWeight: 700, color: PALETTE.inkPrimary, ...NUMERIC }}>
                                {formatEur(feasibleBudgetRange.hist)}
                              </TableCell>
                              <TableCell align="right" sx={{ fontWeight: 600, color: PALETTE.inkSecondary, ...NUMERIC }}>
                                {feasibleBudgetRange.hist > 0
                                  ? `${(((feasibleBudgetRange.lo - feasibleBudgetRange.hist) / feasibleBudgetRange.hist) * 100).toFixed(1)}%`
                                  : "0%"}
                              </TableCell>
                              <TableCell align="right" sx={{ fontWeight: 600, color: PALETTE.inkSecondary, ...NUMERIC }}>
                                {feasibleBudgetRange.hist > 0
                                  ? `+${(((feasibleBudgetRange.hi - feasibleBudgetRange.hist) / feasibleBudgetRange.hist) * 100).toFixed(1)}%`
                                  : "0%"}
                              </TableCell>
                              <TableCell align="right" sx={{ fontWeight: 700, color: PALETTE.inkPrimary, ...NUMERIC }}>
                                {formatEur(feasibleBudgetRange.lo)}
                              </TableCell>
                              <TableCell align="right" sx={{ fontWeight: 700, color: PALETTE.inkPrimary, ...NUMERIC }}>
                                {formatEur(feasibleBudgetRange.hi)}
                              </TableCell>
                            </TableRow>
                          </TableFooter>
                        </Table>
                      </TableContainer>

                      {(submitErr || customOpt?.error) && (
                        <Alert severity="error" role="alert" sx={{ mt: 2 }}>
                          {submitErr || customOpt?.error}
                        </Alert>
                      )}

                      <Stack
                        direction={{ xs: "column", sm: "row" }}
                        justifyContent="space-between"
                        alignItems={{ xs: "stretch", sm: "center" }}
                        spacing={2}
                        sx={{ mt: 2.25 }}
                      >
                        <TextField
                          id={`${formId}-scenario-name`}
                          label="Scenario label"
                          size="small"
                          value={scenarioName}
                          onChange={(e) => setScenarioName(e.target.value)}
                          sx={{ minWidth: { sm: 280 } }}
                        />
                        <Stack direction="row" spacing={1.25} alignItems="center" justifyContent="flex-end">
                          {customJobRunning && (
                            <Stack direction="row" spacing={1} alignItems="center" role="status" aria-live="polite">
                              <CircularProgress size={16} />
                              <Typography variant="caption" color="text.secondary">
                                Solving SLSQP response-curve optimisation…
                              </Typography>
                            </Stack>
                          )}
                          <Button
                            type="submit"
                            variant="contained"
                            startIcon={<PlayArrowRoundedIcon />}
                            disabled={busy || Boolean(customJobRunning) || isRunActive(run)}
                          >
                            See optimisation result
                          </Button>
                        </Stack>
                      </Stack>
                    </Box>
                  </Stack>
                </Box>
              ) : null}
            </Collapse>
          </Paper>

          {/* =====================================================================
              SECTION 2: OPTIMIZATION OVERVIEW (Figure 17)
             ===================================================================== */}
          {ctx && !activeResult && customOptId ? (
            <Alert
              severity={customStatus === "failed" || customStatus === "cancelled" ? "error" : "info"}
              role="status"
              sx={{ mb: 3 }}
            >
              {customStatus === "failed" || customStatus === "cancelled"
                ? `This scenario did not finish${customOpt?.error ? `: ${customOpt.error}` : "."} Pick another scenario or change the constraints.`
                : "This scenario is still being solved; its results appear here when it finishes."}
            </Alert>
          ) : null}
          {ctx && activeResult && overviewMetrics ? (
            <>
              <Paper
                id="optimiser-overview"
                data-anchor
                component="section"
                aria-labelledby={`${formId}-overview-heading`}
                sx={{ p: { xs: 2.25, md: 3 }, mb: 3 }}
              >
                <Stack
                  direction={{ xs: "column", sm: "row" }}
                  justifyContent="space-between"
                  alignItems={{ xs: "flex-start", sm: "center" }}
                  spacing={1.5}
                  sx={{ mb: 2 }}
                >
                  <Box>
                    <Typography id={`${formId}-overview-heading`} variant="h2">
                      Result
                    </Typography>
                    <Typography variant="body2" color="text.secondary">
                      {activeResult.method || "Constrained response-curve optimisation"} · Comparing non-optimised baseline against the active scenario.
                    </Typography>
                  </Box>
                  <Button
                    size="small"
                    variant="outlined"
                    startIcon={<DownloadRoundedIcon />}
                    onClick={() =>
                      downloadCsv(
                        `meridian_scenario_${selectedScenarioKey}.csv`,
                        activeRows.map((r) => ({
                          Channel: r.label || r.channel,
                          "Non-optimised spend (EUR)": Math.round(rowCurSpend(r)),
                          "Optimised spend (EUR)": Math.round(r.optimised_spend),
                          "Spend change (EUR)": Math.round(rowDeltaSpend(r)),
                          "Spend change (%)": Number(rowDeltaPct(r).toFixed(1)),
                          "Non-optimised outcome (EUR)": Math.round(rowCurInc(r)),
                          "Optimised outcome (EUR)": Math.round(rowOptInc(r)),
                          "Outcome change (EUR)": Math.round(rowDeltaInc(r)),
                          "Non-optimised ROI": Number((r.historical_roi ?? 0).toFixed(2)),
                          "Optimised ROI": Number(r.optimised_roi.toFixed(2)),
                          "Marginal ROI": Number((r.mroi ?? r.optimised_roi).toFixed(2)),
                        }))
                      )
                    }
                  >
                    Download CSV
                  </Button>
                </Stack>

                {/* Methodological guardrail banner */}
                {ctx.upper_bound_channels.length > 0 && (
                  <Alert
                    severity={selectedScenarioKey === "default_unconstrained" ? "warning" : "info"}
                    icon={<ScienceRoundedIcon />}
                    sx={{ mb: 2.5 }}
                    action={
                      selectedScenarioKey === "default_constrained" ? (
                        <Button
                          color="inherit"
                          size="small"
                          onClick={() => setSelectedScenarioKey("default_unconstrained")}
                        >
                          View unconstrained (±30%)
                        </Button>
                      ) : (
                        <Button
                          color="inherit"
                          size="small"
                          onClick={() => setSelectedScenarioKey("default_constrained")}
                        >
                          View recommended ({upperNames.join(", ")} fixed)
                        </Button>
                      )
                    }
                  >
                    {selectedScenarioKey === "default_constrained" ? (
                      <>
                        <strong>Recommended headline ({upperNames.join(", ")} held at current spend):</strong>{" "}
                        {joinNames(upperNames)} {upperNames.length === 1 ? "has" : "have"} an upper-bound observational ROI because CPC spend follows consumer demand
                        {activeResult.upper_bound_lift_share != null && activeResult.unconstrained ? (
                          <>
                            {" "}and {upperNames.length === 1 ? "delivers" : "deliver"}{" "}
                            <strong>{Math.round(activeResult.upper_bound_lift_share * 100)}%</strong> of the unconstrained{" "}
                            {signedPct1(activeResult.unconstrained.lift_pct)} lift
                          </>
                        ) : null}
                        . Holding them fixed isolates the re-allocation across {joinNames(movableNames)}.
                      </>
                    ) : selectedScenarioKey === "default_unconstrained" ? (
                      <>
                        <strong>Unconstrained scenario (all {ctx.channels.length} channels ±30%):</strong>{" "}
                        {activeResult.upper_bound_lift_share != null ? (
                          <>
                            <strong>{Math.round(activeResult.upper_bound_lift_share * 100)}%</strong> of this{" "}
                          </>
                        ) : (
                          "Part of this "
                        )}
                        {signedPct1(overviewMetrics.incDeltaPct)} ({formatEurSigned(overviewMetrics.incDelta)}) lift comes from scaling {joinNames(upperNames)}. Validate their causal incrementality with a conversion-lift or geo holdout test before shifting budget.
                      </>
                    ) : (
                      <>
                        <strong>Custom scenario ({customOpt?.name || "User configuration"}):</strong>{" "}
                        Mode: <strong>{activeResult.budget_mode === "target_roi" ? `Flexible · Target ROI ≥ ${activeResult.target_value?.toFixed(2)}×` : activeResult.budget_mode === "target_mroi" ? `Flexible · Target mROI ≥ ${activeResult.target_value?.toFixed(2)}×` : "Fixed budget"}</strong>. The optimiser takes fitted response curves at face value within the configured channel limits.
                      </>
                    )}
                  </Alert>
                )}

                {/* The scenario's headline numbers, in the same strip as every other page */}
                <KpiStrip
                  label="Scenario result"
                  items={[
                    {
                      label: "Optimised total budget",
                      value: formatEur(overviewMetrics.optBudget),
                      delta:
                        Math.abs(overviewMetrics.budgetDelta) < 50_000
                          ? `Unchanged vs ${formatEur(overviewMetrics.histBudget)} non-optimised`
                          : `${formatEurSigned(overviewMetrics.budgetDelta)} (${formatPctSigned(overviewMetrics.budgetDeltaPct)}) from non-optimised`,
                    },
                    {
                      label: "Optimised total ROI",
                      value: `${overviewMetrics.optRoi.toFixed(2)}×`,
                      delta: `${overviewMetrics.roiDelta >= 0 ? "+" : ""}${overviewMetrics.roiDelta.toFixed(2)}× from non-optimised (${overviewMetrics.histRoi.toFixed(2)}×)`,
                      tone: overviewMetrics.roiDelta >= 0 ? "good" : "warn",
                    },
                    {
                      label: "Optimised total outcome",
                      value: formatEur(overviewMetrics.optInc),
                      delta: `${formatEurSigned(overviewMetrics.incDelta)} (${formatPctSigned(overviewMetrics.incDeltaPct)}) vs ${formatEur(overviewMetrics.histInc)}`,
                      tone: overviewMetrics.incDelta >= 0 ? "good" : "bad",
                    },
                    {
                      label: "Primary re-allocation",
                      value: overviewMetrics.reallocationLabel,
                      delta:
                        activeResult.unconstrained && selectedScenarioKey === "default_constrained"
                          ? `Unconstrained lift: ${signedPct1(activeResult.unconstrained.lift_pct)} (${formatEurSigned(activeResult.unconstrained.lift_eur)})`
                          : "Moves spend toward highest marginal return",
                    },
                  ]}
                />

                {/* Summary comparison table with Grand total row (Figure 17 bottom) */}
                <Stack direction="row" justifyContent="flex-end" sx={{ mb: 1 }}>
                  <ExplainButton
                    kind="table"
                    title="Optimisation overview: channel comparison"
                    caption="Channel-by-channel comparison of non-optimised vs optimised spend, incremental outcome, ROI, and marginal ROI"
                    targetId={`${formId}-overview-table`}
                  />
                </Stack>
                <TableContainer
                  id={`${formId}-overview-table`}
                  tabIndex={0}
                  role="region"
                  aria-label="Optimisation overview channel comparison table"
                  sx={{ overflowX: "auto" }}
                >
                  <Table size="small">
                    <caption className="visually-hidden">
                      Channel-by-channel comparison of non-optimised vs optimised spend, incremental outcome, ROI, and marginal ROI
                    </caption>
                    <TableHead>
                      <TableRow>
                        <TableCell scope="col">Media channel name</TableCell>
                        <TableCell scope="col" align="right">
                          Non-optimised spend
                        </TableCell>
                        <TableCell scope="col" align="right">
                          Optimised spend
                        </TableCell>
                        <TableCell scope="col" align="right">
                          Spend change
                        </TableCell>
                        <TableCell scope="col" align="right">
                          Non-optimised outcome
                        </TableCell>
                        <TableCell scope="col" align="right">
                          Optimised outcome
                        </TableCell>
                        <TableCell scope="col" align="right">
                          Outcome change
                        </TableCell>
                        <TableCell scope="col" align="right">
                          Non-optimised ROI
                        </TableCell>
                        <TableCell scope="col" align="right">
                          Optimised ROI
                        </TableCell>
                        <TableCell scope="col" align="right">
                          Marginal ROI
                        </TableCell>
                      </TableRow>
                    </TableHead>
                    <TableBody>
                      {[...activeRows]
                        .sort((a, b) => rowCurSpend(b) - rowCurSpend(a))
                        .map((r, idx) => {
                          const dSpend = rowDeltaSpend(r);
                          const dPct = rowDeltaPct(r);
                          const dInc = rowDeltaInc(r);
                          const curSp = rowCurSpend(r);
                          const curInc = rowCurInc(r);
                          const optInc = rowOptInc(r);
                          const histRoi = r.historical_roi ?? (curSp > 0 ? curInc / curSp : 0);
                          return (
                            <TableRow key={r.channel} hover>
                              <TableCell component="th" scope="row" sx={{ fontWeight: 650 }}>
                                <Stack direction="row" spacing={1} alignItems="center">
                                  <Box
                                    sx={{
                                      width: 10,
                                      height: 10,
                                      borderRadius: "50%",
                                      bgcolor: channelColor(r.channel, idx),
                                      flexShrink: 0,
                                    }}
                                  />
                                  <span>{r.label || r.channel}</span>
                                </Stack>
                              </TableCell>
                              <TableCell align="right" sx={NUMERIC}>
                                {formatEur(curSp)}
                              </TableCell>
                              <TableCell align="right" sx={{ fontWeight: 650, ...NUMERIC }}>
                                {formatEur(r.optimised_spend)}
                              </TableCell>
                              <TableCell
                                align="right"
                                sx={{
                                  fontWeight: 650,
                                  color:
                                    Math.abs(dSpend) < 1000
                                      ? PALETTE.inkSecondary
                                      : dSpend > 0
                                      ? PALETTE.semanticGood
                                      : PALETTE.semanticBad,
                                  ...NUMERIC,
                                }}
                              >
                                {Math.abs(dSpend) < 1000
                                  ? "€0 (0.0%)"
                                  : `${formatEurSigned(dSpend)} (${formatPctSigned(dPct)})`}
                              </TableCell>
                              <TableCell align="right" sx={NUMERIC}>
                                {formatEur(curInc)}
                              </TableCell>
                              <TableCell align="right" sx={{ fontWeight: 650, ...NUMERIC }}>
                                {formatEur(optInc)}
                              </TableCell>
                              <TableCell
                                align="right"
                                sx={{
                                  fontWeight: 650,
                                  color:
                                    Math.abs(dInc) < 1000
                                      ? PALETTE.inkSecondary
                                      : dInc > 0
                                      ? PALETTE.semanticGood
                                      : PALETTE.semanticBad,
                                  ...NUMERIC,
                                }}
                              >
                                {Math.abs(dInc) < 1000 ? "€0" : formatEurSigned(dInc)}
                              </TableCell>
                              <TableCell align="right" sx={NUMERIC}>
                                {histRoi.toFixed(2)}×
                              </TableCell>
                              <TableCell align="right" sx={{ fontWeight: 650, ...NUMERIC }}>
                                {r.optimised_roi.toFixed(2)}×
                              </TableCell>
                              <TableCell align="right" sx={NUMERIC}>
                                {(r.mroi ?? r.optimised_roi).toFixed(2)}×
                              </TableCell>
                            </TableRow>
                          );
                        })}
                    </TableBody>
                    <TableFooter>
                      <TableRow sx={{ bgcolor: PALETTE.bgSubtle }}>
                        <TableCell component="th" scope="row" sx={{ fontWeight: 700, color: PALETTE.inkPrimary }}>
                          Grand total
                        </TableCell>
                        <TableCell align="right" sx={{ fontWeight: 700, color: PALETTE.inkPrimary, ...NUMERIC }}>
                          {formatEur(overviewMetrics.histBudget)}
                        </TableCell>
                        <TableCell align="right" sx={{ fontWeight: 700, color: PALETTE.inkPrimary, ...NUMERIC }}>
                          {formatEur(overviewMetrics.optBudget)}
                        </TableCell>
                        <TableCell
                          align="right"
                          sx={{
                            fontWeight: 700,
                            color:
                              Math.abs(overviewMetrics.budgetDelta) < 50_000
                                ? PALETTE.inkPrimary
                                : overviewMetrics.budgetDelta > 0
                                ? PALETTE.semanticGood
                                : PALETTE.semanticBad,
                            ...NUMERIC,
                          }}
                        >
                          {Math.abs(overviewMetrics.budgetDelta) < 50_000
                            ? "€0 (0.0%)"
                            : `${formatEurSigned(overviewMetrics.budgetDelta)} (${formatPctSigned(overviewMetrics.budgetDeltaPct)})`}
                        </TableCell>
                        <TableCell align="right" sx={{ fontWeight: 700, color: PALETTE.inkPrimary, ...NUMERIC }}>
                          {formatEur(overviewMetrics.histInc)}
                        </TableCell>
                        <TableCell align="right" sx={{ fontWeight: 700, color: PALETTE.inkPrimary, ...NUMERIC }}>
                          {formatEur(overviewMetrics.optInc)}
                        </TableCell>
                        <TableCell
                          align="right"
                          sx={{
                            fontWeight: 700,
                            color:
                              overviewMetrics.incDelta >= 0 ? PALETTE.semanticGood : PALETTE.semanticBad,
                            ...NUMERIC,
                          }}
                        >
                          {formatEurSigned(overviewMetrics.incDelta)} ({formatPctSigned(overviewMetrics.incDeltaPct)})
                        </TableCell>
                        <TableCell align="right" sx={{ fontWeight: 700, color: PALETTE.inkPrimary, ...NUMERIC }}>
                          {overviewMetrics.histRoi.toFixed(2)}×
                        </TableCell>
                        <TableCell align="right" sx={{ fontWeight: 700, color: PALETTE.inkPrimary, ...NUMERIC }}>
                          {overviewMetrics.optRoi.toFixed(2)}×
                        </TableCell>
                        <TableCell align="right" sx={{ fontWeight: 700, color: PALETTE.inkSecondary, ...NUMERIC }}>
                          —
                        </TableCell>
                      </TableRow>
                    </TableFooter>
                  </Table>
                </TableContainer>
              </Paper>

              {/* =====================================================================
                  SECTION 3: OPTIMIZED BUDGET ALLOCATION (Figure 18)
                 ===================================================================== */}
              <Paper
                id="optimiser-allocation"
                data-anchor
                component="section"
                aria-labelledby={`${formId}-allocation-heading`}
                sx={{ p: { xs: 2.25, md: 3 }, mb: 3 }}
              >
                <Typography id={`${formId}-allocation-heading`} variant="h2">
                  Where the budget moves
                </Typography>
                <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
                  The spend change per channel and what it does to each channel's incremental sales.
                </Typography>

                {/* 2x2 Chart Grid (Figure 18) */}
                <Box
                  sx={{
                    display: "grid",
                    gridTemplateColumns: { xs: "minmax(0, 1fr)", lg: "repeat(2, minmax(0, 1fr))" },
                    gap: 2.5,
                  }}
                >
                  {/* Panel 1: Change in optimized spend for each channel */}
                  <Paper sx={{ p: 2, minWidth: 0 }}>
                    <ChartPanelTitle
                      title="Change in optimised spend for each channel"
                      caption="Optimised minus non-optimised spend (€M), colored by expansion (+) vs reduction (−)."
                      figure={spendDeltaSpec}
                    />
                    <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>
                      Optimised minus non-optimised spend (€M), colored by expansion (+) vs reduction (−).
                    </Typography>
                    {spendDeltaSpec && (
                      <PlotlyChart
                        data={spendDeltaSpec.data}
                        layout={spendDeltaSpec.layout}
                        height={340}
                        ariaLabel="Change in optimised spend for each channel"
                        viewKey={`delta-${selectedScenarioKey}`}
                      />
                    )}
                  </Paper>

                  {/* Panel 4: Incremental outcome per channel (with Grouped / Waterfall toggle) */}
                  <Paper sx={{ p: 2, minWidth: 0 }}>
                    <Stack
                      direction="row"
                      justifyContent="space-between"
                      alignItems="flex-start"
                      spacing={1}
                      sx={{ mb: 1 }}
                    >
                      <Box>
                        <Typography variant="h3">Incremental outcome per channel</Typography>
                        <Typography variant="body2" color="text.secondary">
                          {outcomeChartMode === "grouped"
                            ? "Non-optimised vs optimised incremental sales per channel (€M)."
                            : "Waterfall bridge from non-optimised to optimised incremental outcome (€M)."}
                        </Typography>
                      </Box>
                      <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap" justifyContent="flex-end" useFlexGap>
                        <ToggleButtonGroup
                          size="small"
                          exclusive
                          value={outcomeChartMode}
                          aria-label="Incremental outcome chart view"
                          onChange={(_, val) => {
                            if (val) setOutcomeChartMode(val);
                          }}
                        >
                          <ToggleButton value="grouped">By channel</ToggleButton>
                          <ToggleButton value="waterfall">Waterfall</ToggleButton>
                        </ToggleButtonGroup>
                        {outcomeComparisonSpec && (
                          <ExplainButton
                            kind="chart"
                            title={`Incremental outcome per channel (${outcomeChartMode === "grouped" ? "by channel" : "waterfall"})`}
                            caption={
                              outcomeChartMode === "grouped"
                                ? "Non-optimised vs optimised incremental sales per channel (€M)."
                                : "Waterfall bridge from non-optimised to optimised incremental outcome (€M)."
                            }
                            figure={outcomeComparisonSpec}
                          />
                        )}
                      </Stack>
                    </Stack>
                    {outcomeComparisonSpec && (
                      <PlotlyChart
                        data={outcomeComparisonSpec.data}
                        layout={outcomeComparisonSpec.layout}
                        height={340}
                        ariaLabel="Incremental outcome per channel chart"
                        viewKey={`outcome-${outcomeChartMode}-${selectedScenarioKey}`}
                      />
                    )}
                  </Paper>
                </Box>
                {/* The allocation as shares and as side-by-side bars repeats the result table: one click away */}
                <Box sx={{ mt: 2.5 }}>
                  <MoreAnalysis count={2} label="More allocation charts">
                    <Box sx={{ display: "grid", gridTemplateColumns: { xs: "minmax(0, 1fr)", lg: "repeat(2, minmax(0, 1fr))" }, gap: 2.5 }}>
                    {/* Panel 2: Optimized spend allocation donut */}
                    <Paper sx={{ p: 2, minWidth: 0 }}>
                      <ChartPanelTitle
                        title="Optimised spend allocation"
                        caption={`Percentage of total budget (${formatEur(overviewMetrics.optBudget)}) allocated to each channel.`}
                        figure={spendDonutSpec}
                      />
                      <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>
                        Percentage of total budget ({formatEur(overviewMetrics.optBudget)}) allocated to each channel.
                      </Typography>
                      {spendDonutSpec && (
                        <PlotlyChart
                          data={spendDonutSpec.data}
                          layout={spendDonutSpec.layout}
                          height={340}
                          ariaLabel="Optimised spend allocation donut chart"
                          viewKey={`donut-${selectedScenarioKey}`}
                        />
                      )}
                    </Paper>

                    {/* Panel 3: Budget spend per channel */}
                    <Paper sx={{ p: 2, minWidth: 0 }}>
                      <ChartPanelTitle
                        title="Budget spend per channel"
                        caption="Non-optimised historical spend vs optimised spend per channel (€M)."
                        figure={budgetComparisonSpec}
                      />
                      <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>
                        Non-optimised historical spend vs optimised spend per channel (€M).
                      </Typography>
                      {budgetComparisonSpec && (
                        <PlotlyChart
                          data={budgetComparisonSpec.data}
                          layout={budgetComparisonSpec.layout}
                          height={340}
                          ariaLabel="Budget spend per channel comparison chart"
                          viewKey={`budget-cmp-${selectedScenarioKey}`}
                        />
                      )}
                    </Paper>
                    </Box>
                  </MoreAnalysis>
                </Box>
              </Paper>

              {/* =====================================================================
                  SECTION 4: OPTIMIZATION WITH RESPONSE CURVES (Figure 19)
                 ===================================================================== */}
              <Paper
                id="optimiser-response-curves"
                data-anchor
                component="section"
                aria-labelledby={`${formId}-curves-heading`}
                sx={{ p: { xs: 2.25, md: 3 }, mb: 3 }}
              >
                <Stack
                  direction={{ xs: "column", md: "row" }}
                  justifyContent="space-between"
                  alignItems={{ xs: "flex-start", md: "center" }}
                  spacing={1.5}
                  sx={{ mb: 2 }}
                >
                  <Box>
                    <Typography id={`${formId}-curves-heading`} variant="h2">
                      Response curves
                    </Typography>
                    <Typography variant="body2" color="text.secondary">
                      Inspect how each channel&apos;s optimal spend sits along its posterior Hill saturation curve inside the configured constraint bounds.
                    </Typography>
                  </Box>
                  <Stack direction="row" spacing={1.25} alignItems="center" flexWrap="wrap" useFlexGap>
                    {curveViewMode === "single" && (
                      <FormControl size="small" sx={{ minWidth: 200 }}>
                        <InputLabel id={`${formId}-curve-channel-label`}>Media channel name</InputLabel>
                        <Select
                          labelId={`${formId}-curve-channel-label`}
                          label="Media channel name"
                          value={selectedChannelMeta?.channel || ""}
                          onChange={(e) => setSelectedChannelSlug(e.target.value)}
                        >
                          {ctx.channels.map((c) => (
                            <MenuItem key={c.channel} value={c.channel}>
                              {c.label}
                            </MenuItem>
                          ))}
                        </Select>
                      </FormControl>
                    )}
                    <ToggleButtonGroup
                      size="small"
                      exclusive
                      value={curveViewMode}
                      aria-label="Response curves display mode"
                      onChange={(_, val) => {
                        if (val) setCurveViewMode(val);
                      }}
                    >
                      <ToggleButton value="single">Single channel + mROI</ToggleButton>
                      <ToggleButton value="grid">All channels (6-panel)</ToggleButton>
                    </ToggleButtonGroup>
                  </Stack>
                </Stack>

                {/* Visual encoding key matching Meridian Scenario Planner Figure 19 */}
                <Stack
                  direction="row"
                  spacing={2}
                  alignItems="center"
                  flexWrap="wrap"
                  useFlexGap
                  sx={{
                    px: 2,
                    py: 1.25,
                    mb: 2,
                    borderRadius: 1,
                    bgcolor: PALETTE.bgSubtle,
                    border: `1px solid ${PALETTE.borderSubtle}`,
                  }}
                >
                  <Typography variant="caption" sx={{ fontWeight: 700, color: PALETTE.inkPrimary }}>
                    Curve encoding:
                  </Typography>
                  <Typography variant="caption" color="text.secondary">
                    <strong>━━ Solid line</strong> = spend inside constraint bounds
                  </Typography>
                  <Typography variant="caption" color="text.secondary">
                    <strong>┈┈ Dotted line</strong> = spend outside constraint bounds (0–200% historical)
                  </Typography>
                  <Typography variant="caption" color="text.secondary">
                    <strong>● Circle</strong> = current (non-optimised) spend level
                  </Typography>
                  <Typography variant="caption" color="text.secondary">
                    <strong>■ Square</strong> = optimised spend level
                  </Typography>
                  <Typography variant="caption" color="text.secondary" sx={{ ml: "auto" }}>
                    Note: Response curves are fitted on spend (R&amp;F data is not present in this workbook).
                  </Typography>
                </Stack>

                {curveViewMode === "single" ? (
                  <Box
                    sx={{
                      display: "grid",
                      gridTemplateColumns: { xs: "minmax(0, 1fr)", lg: "repeat(2, minmax(0, 1fr))" },
                      gap: 2.5,
                    }}
                  >
                    <Paper sx={{ p: 2, minWidth: 0 }}>
                      <ChartPanelTitle
                        title={`${selectedChannelMeta?.label ?? ""} · Response curve (Spend → Incremental outcome)`}
                        caption="Posterior mean and 90% credible interval across 0–200% of historical spend."
                        figure={singleCurveSpec}
                      />
                      <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>
                        Posterior mean and 90% credible interval across 0–200% of historical spend.
                      </Typography>
                      {singleCurveSpec && (
                        <PlotlyChart
                          data={singleCurveSpec.data}
                          layout={singleCurveSpec.layout}
                          height={380}
                          ariaLabel={`${selectedChannelMeta?.label} response curve`}
                          viewKey={`rc-single-${selectedChannelMeta?.channel}-${selectedScenarioKey}`}
                        />
                      )}
                    </Paper>
                    <Paper sx={{ p: 2, minWidth: 0 }}>
                      <ChartPanelTitle
                        title={`${selectedChannelMeta?.label ?? ""} · Marginal ROI curve (Diminishing returns)`}
                        caption="Slope of the response curve (€ of incremental sales per additional €1 of spend)."
                        figure={marginalRoiSpec}
                      />
                      <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>
                        Slope of the response curve (€ of incremental sales per additional €1 of spend).
                      </Typography>
                      {marginalRoiSpec && (
                        <PlotlyChart
                          data={marginalRoiSpec.data}
                          layout={marginalRoiSpec.layout}
                          height={380}
                          ariaLabel={`${selectedChannelMeta?.label} marginal ROI curve`}
                          viewKey={`mroi-single-${selectedChannelMeta?.channel}-${selectedScenarioKey}`}
                        />
                      )}
                    </Paper>
                  </Box>
                ) : (
                  <Paper sx={{ p: 2, minWidth: 0 }}>
                    <ChartPanelTitle
                      title="All media channels · Response curves & optimal allocations"
                      caption="Solid segments show the feasible constraint range; circles (●) mark non-optimised spend and squares (■) mark optimised spend."
                      figure={allCurvesGridSpec}
                    />
                    <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>
                      Solid segments show the feasible constraint range; circles (●) mark non-optimised spend and squares (■) mark optimised spend.
                    </Typography>
                    {allCurvesGridSpec && (
                      <PlotlyChart
                        data={allCurvesGridSpec.data}
                        layout={allCurvesGridSpec.layout}
                        height={(allCurvesGridSpec.layout.height as number) || 540}
                        ariaLabel="All media channels response curves grid"
                        viewKey={`rc-grid-${selectedScenarioKey}`}
                      />
                    )}
                  </Paper>
                )}
              </Paper>
            </>
          ) : null}

          {/* =====================================================================
              SECTION 5: UNCONSTRAINED LIFT DECOMPOSITION (from pipeline section)
             ===================================================================== */}
          {secLoading && !section ? (
            <Stack spacing={2}>
              <Skeleton variant="rounded" height={120} />
              <Skeleton variant="rounded" height={240} />
            </Stack>
          ) : section ? (
            <SectionRenderer
              section={
                ctx
                  ? {
                      ...section,
                      title: "Unconstrained lift",
                      subtitle:
                        "Share of the unconstrained lift (all channels ±30%) attributable to each channel before demand-following guardrails.",
                      blocks: section.blocks.filter(
                        (b) => b.type === "table" && (b.id === "optimiser_lift_decomposition" || b.table_id === "optimiser_lift_decomposition")
                      ),
                    }
                  : section
              }
              headingLevel={2}
              toc={false}
            />
          ) : (
            !ctx && (
              <Alert severity="info">
                Optimiser outputs will appear once the Meridian fit finishes.
              </Alert>
            )
          )}
        </Box>

        <OnThisPage entries={tocItems} />
      </Box>
    </Box>
  );
};

export default OptimizerPage;
