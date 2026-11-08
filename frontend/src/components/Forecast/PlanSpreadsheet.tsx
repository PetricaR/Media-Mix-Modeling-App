import React, { useEffect, useMemo, useRef, useState } from "react";
import {
  Alert,
  Box,
  Button,
  Chip,
  CircularProgress,
  InputAdornment,
  Paper,
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
} from "@mui/material";
import DownloadIcon from "@mui/icons-material/Download";
import LockOutlinedIcon from "@mui/icons-material/LockOutlined";
import PlayArrowIcon from "@mui/icons-material/PlayArrow";
import RestartAltIcon from "@mui/icons-material/RestartAlt";
import RestoreIcon from "@mui/icons-material/Restore";
import { api } from "../../api/client";
import type { ForecastItem, ScenarioResult } from "../../api/types";
import { NUMERIC, PALETTE } from "../../theme";
import { resolveVariableContributions } from "./figures";
import { eur, eurCompact, parseEuroAmount, parsePctChange, shortDate, signedEur, signedPct } from "./format";

const DEFAULT_CHANNEL_LABELS: Record<string, string> = {
  sea: "SEA",
  catalog: "Catalog",
  display: "Display",
  display_network: "Display Network",
  social_media: "Social Media",
  psm: "PSM",
  affiliate: "Affiliate",
};

function resolveChannelLabel(slug: string, channelNames: Record<string, string>): string {
  const fromProps = channelNames[slug];
  if (fromProps && fromProps !== slug) return fromProps;
  if (DEFAULT_CHANNEL_LABELS[slug]) return DEFAULT_CHANNEL_LABELS[slug];
  return slug
    .split("_")
    .map((w) => (w.length <= 3 ? w.toUpperCase() : w.charAt(0).toUpperCase() + w.slice(1)))
    .join(" ");
}

export interface ParsedPlanTemplate {
  dates: string[];
  columns: string[];
  dailyBySlug: Record<string, number[]>;
}

export function parsePlanTemplateCsv(csvText: string): ParsedPlanTemplate {
  const lines = csvText
    .trim()
    .split(/\r?\n/)
    .filter((l) => l.trim().length > 0);
  if (lines.length < 2) {
    return { dates: [], columns: [], dailyBySlug: {} };
  }
  const header = lines[0].split(",").map((h) => h.trim());
  const dateIdx = header.findIndex((h) => h.toLowerCase() === "date");
  const columns = header.filter((_, idx) => idx !== dateIdx);
  const dates: string[] = [];
  const dailyBySlug: Record<string, number[]> = Object.fromEntries(columns.map((c) => [c, []]));

  for (let i = 1; i < lines.length; i++) {
    const cells = lines[i].split(",").map((c) => c.trim());
    if (cells.length < header.length) continue;
    dates.push(cells[dateIdx >= 0 ? dateIdx : 0]);
    header.forEach((col, idx) => {
      if (idx === dateIdx) return;
      const val = Number(cells[idx]);
      dailyBySlug[col].push(Number.isFinite(val) && val >= 0 ? val : 0);
    });
  }

  return { dates, columns, dailyBySlug };
}

export function buildSpreadsheetCsv(
  dates: string[],
  columns: string[],
  dailyBySlug: Record<string, number[]>,
  days: number
): string {
  const rowCount = Math.max(0, Math.min(days, dates.length));
  const header = ["date", ...columns].join(",");
  const rows: string[] = [];
  for (let i = 0; i < rowCount; i++) {
    const vals = columns.map((col) => {
      const v = dailyBySlug[col]?.[i] ?? 0;
      return Math.max(0, Number.isFinite(v) ? v : 0).toFixed(2);
    });
    rows.push([dates[i], ...vals].join(","));
  }
  return [header, ...rows].join("\n");
}

function parseCellNumber(raw: string): number | null {
  const trimmed = raw.trim();
  if (!trimmed) return 0;
  const euroParsed = parseEuroAmount(trimmed);
  if (euroParsed !== null) return euroParsed;
  const direct = Number(trimmed.replace(/,/g, ""));
  if (Number.isFinite(direct) && direct >= 0) return Math.round(direct * 100) / 100;
  return null;
}

const ISO_DATE_RE = /^\d{4}-\d{2}-\d{2}$/;

/**
 * Apply a pasted text block (TSV from Excel/Sheets or CSV) starting at `(startRow, startColIdx)`.
 * Automatically strips an optional header line and/or leading date column when pasting a full table.
 */
export function applySpreadsheetPaste(
  rawText: string,
  startRow: number,
  startColIdx: number,
  columns: string[],
  currentDaily: Record<string, number[]>,
  maxRows: number
): { nextDaily: Record<string, number[]>; cellsUpdated: number } {
  const rawLines = rawText
    .replace(/\r\n/g, "\n")
    .replace(/\r/g, "\n")
    .split("\n")
    .filter((l) => l.trim().length > 0);

  if (rawLines.length === 0 || columns.length === 0) {
    return { nextDaily: currentDaily, cellsUpdated: 0 };
  }

  const hasTabs = rawLines.some((l) => l.includes("\t"));
  const splitLine = (line: string): string[] => {
    if (hasTabs) return line.split("\t").map((c) => c.trim());
    const parts = line.split(",").map((c) => c.trim());
    if (parts.length > 1 && parseEuroAmount(line) === null) {
      return parts;
    }
    return [line.trim()];
  };

  let grid = rawLines.map(splitLine);

  if (grid.length > 1) {
    const firstRowNumericCount = grid[0].filter((c) => c !== "" && parseCellNumber(c) !== null && !ISO_DATE_RE.test(c)).length;
    if (firstRowNumericCount === 0) {
      grid = grid.slice(1);
    }
  }

  if (grid.length > 0 && grid[0].length > 1 && ISO_DATE_RE.test(grid[0][0])) {
    grid = grid.map((row) => row.slice(1));
  }

  const nextDaily: Record<string, number[]> = {};
  for (const col of columns) {
    nextDaily[col] = [...(currentDaily[col] ?? [])];
  }

  let cellsUpdated = 0;
  for (let r = 0; r < grid.length; r++) {
    const targetRow = startRow + r;
    if (targetRow >= maxRows) break;
    const rowCells = grid[r];
    for (let c = 0; c < rowCells.length; c++) {
      const targetColIdx = startColIdx + c;
      if (targetColIdx >= columns.length) break;
      const colSlug = columns[targetColIdx];
      const parsed = parseCellNumber(rowCells[c]);
      if (parsed !== null) {
        nextDaily[colSlug][targetRow] = Math.max(0, Math.round(parsed));
        cellsUpdated++;
      }
    }
  }

  return { nextDaily, cellsUpdated };
}

function cloneDaily(dailyBySlug: Record<string, number[]>, columns: string[], totalRows: number): Record<string, number[]> {
  const out: Record<string, number[]> = {};
  for (const col of columns) {
    const src = dailyBySlug[col] ?? [];
    out[col] = Array.from({ length: totalRows }, (_, i) => Math.round(src[i] ?? 0));
  }
  return out;
}

function mergePlanIntoTemplate(
  template: ParsedPlanTemplate,
  planDaily: Record<string, number[]> | undefined
): Record<string, number[]> {
  const base = cloneDaily(template.dailyBySlug, template.columns, template.dates.length);
  if (!planDaily) return base;
  for (const col of template.columns) {
    const planArr = planDaily[col];
    if (!planArr) continue;
    for (let i = 0; i < Math.min(planArr.length, template.dates.length); i++) {
      const v = planArr[i];
      if (Number.isFinite(v) && v >= 0) {
        base[col][i] = Math.round(v);
      }
    }
  }
  return base;
}

function formatRowDate(iso: string): { weekday: string; dayMonth: string; isThursday: boolean; isWeekend: boolean } {
  const d = new Date(`${iso}T00:00:00Z`);
  const day = d.getUTCDay();
  const weekday = d.toLocaleDateString("en-GB", { weekday: "short", timeZone: "UTC" });
  const dayMonth = d.toLocaleDateString("en-GB", { day: "numeric", month: "short", timeZone: "UTC" });
  return { weekday, dayMonth, isThursday: day === 4, isWeekend: day === 0 || day === 6 };
}

const HORIZON_PRESETS = [7, 14, 28, 42, 60, 90] as const;

export type SpreadsheetCellMode = "sales" | "spend" | "both";

export interface PlanSpreadsheetProps {
  runId: string;
  activeForecastId: string | null;
  activeResult: ScenarioResult | null;
  forecastBusy?: boolean;
  channelNames: Record<string, string>;
  lockedSlugs: Set<string>;
  cellMode?: SpreadsheetCellMode;
  onCellModeChange?: (mode: SpreadsheetCellMode) => void;
  onPlanCreated: (forecast: ForecastItem) => void;
}

export const PlanSpreadsheet: React.FC<PlanSpreadsheetProps> = ({
  runId,
  activeForecastId,
  activeResult,
  forecastBusy = false,
  channelNames,
  lockedSlugs,
  cellMode: controlledCellMode,
  onCellModeChange,
  onPlanCreated,
}) => {
  const [internalCellMode, setInternalCellMode] = useState<SpreadsheetCellMode>(controlledCellMode ?? "sales");
  useEffect(() => {
    if (controlledCellMode) setInternalCellMode(controlledCellMode);
  }, [controlledCellMode]);
  const cellMode = controlledCellMode ?? internalCellMode;
  const handleCellModeChange = (next: SpreadsheetCellMode) => {
    setInternalCellMode(next);
    onCellModeChange?.(next);
  };

  const [template, setTemplate] = useState<ParsedPlanTemplate | null>(null);
  const [loadingTemplate, setLoadingTemplate] = useState(false);
  const [days, setDays] = useState<number>(42);
  const [daysDraft, setDaysDraft] = useState<string>("42");
  const [dailyBySlug, setDailyBySlug] = useState<Record<string, number[]>>({});
  const [planName, setPlanName] = useState<string>("");
  const [dirty, setDirty] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [pasteNotice, setPasteNotice] = useState<string | null>(null);

  const [editingCell, setEditingCell] = useState<{ key: string; text: string } | null>(null);
  const cellRefs = useRef<Map<string, HTMLInputElement>>(new Map());
  const hydratedKeyRef = useRef<string | null>(null);
  const submittedIdsRef = useRef<Set<string>>(new Set());

  const applyHorizonDays = (nextDays: number, markDirty: boolean) => {
    const clamped = Math.max(1, Math.min(90, Math.round(nextDays)));
    setDays(clamped);
    setDaysDraft(String(clamped));
    if (markDirty) setDirty(true);
    return clamped;
  };

  const commitDaysDraft = (): number => {
    const trimmed = daysDraft.trim();
    if (!trimmed) {
      setDaysDraft(String(days));
      return days;
    }
    const parsed = Number(trimmed);
    if (!Number.isFinite(parsed)) {
      setDaysDraft(String(days));
      return days;
    }
    const clamped = Math.max(1, Math.min(90, Math.round(parsed)));
    if (clamped !== days) {
      setDays(clamped);
      setDirty(true);
    }
    setDaysDraft(String(clamped));
    return clamped;
  };

  useEffect(() => {
    if (!runId) return;
    let cancelled = false;
    hydratedKeyRef.current = null;
    submittedIdsRef.current.clear();
    setLoadingTemplate(true);
    setErrorMsg(null);
    fetch(`/api/v1/runs/${encodeURIComponent(runId)}/plan-template?days=90`, { credentials: "include" })
      .then(async (res) => {
        if (!res.ok) throw new Error(`Could not load baseline plan template (${res.status})`);
        return res.text();
      })
      .then((csvText) => {
        if (cancelled) return;
        const parsed = parsePlanTemplateCsv(csvText);
        setTemplate(parsed);
      })
      .catch((err) => {
        if (!cancelled) setErrorMsg(err instanceof Error ? err.message : "Could not load baseline plan template");
      })
      .finally(() => {
        if (!cancelled) setLoadingTemplate(false);
      });
    return () => {
      cancelled = true;
    };
  }, [runId]);

  useEffect(() => {
    if (!template) return;

    // Never overwrite user state when switching to a forecast just submitted from this spreadsheet
    if (activeForecastId && submittedIdsRef.current.has(activeForecastId)) {
      hydratedKeyRef.current = `${runId}:${activeForecastId}:result`;
      return;
    }

    if (activeForecastId && activeResult && activeResult.plan_daily) {
      const targetKey = `${runId}:${activeForecastId}:result`;
      if (hydratedKeyRef.current === targetKey) return;
      hydratedKeyRef.current = targetKey;
      if (!dirty) {
        const nextDays = Math.max(1, Math.min(90, activeResult.horizon_days || 42));
        setDays(nextDays);
        setDaysDraft(String(nextDays));
        setDailyBySlug(mergePlanIntoTemplate(template, activeResult.plan_daily));
      }
      return;
    }

    if (hydratedKeyRef.current === null) {
      hydratedKeyRef.current = `${runId}:template`;
      setDailyBySlug(cloneDaily(template.dailyBySlug, template.columns, template.dates.length));
    }
  }, [template, runId, activeForecastId, activeResult, dirty]);

  const columns = template?.columns ?? [];
  const visibleDates = useMemo(() => (template?.dates ?? []).slice(0, days), [template, days]);

  const channelTotals = useMemo(() => {
    const out: Record<string, number> = {};
    for (const col of columns) {
      const arr = dailyBySlug[col] ?? [];
      let sum = 0;
      for (let i = 0; i < days; i++) sum += arr[i] ?? 0;
      out[col] = sum;
    }
    return out;
  }, [columns, dailyBySlug, days]);

  const baselineTotals = useMemo(() => {
    const out: Record<string, number> = {};
    if (!template) return out;
    for (const col of columns) {
      const arr = template.dailyBySlug[col] ?? [];
      let sum = 0;
      for (let i = 0; i < days; i++) sum += arr[i] ?? 0;
      out[col] = sum;
    }
    return out;
  }, [template, columns, days]);

  const rowTotals = useMemo(() => {
    return Array.from({ length: days }, (_, rowIdx) =>
      columns.reduce((acc, col) => acc + (dailyBySlug[col]?.[rowIdx] ?? 0), 0)
    );
  }, [columns, dailyBySlug, days]);

  const grandTotal = useMemo(() => Object.values(channelTotals).reduce((a, b) => a + b, 0), [channelTotals]);
  const baselineGrandTotal = useMemo(() => Object.values(baselineTotals).reduce((a, b) => a + b, 0), [baselineTotals]);
  const grandDeltaPct = baselineGrandTotal > 0 ? ((grandTotal - baselineGrandTotal) / baselineGrandTotal) * 100 : 0;

  // Individual channel & structural Forecast Sales (EUR) cell values from the fitted Meridian posterior decomposition
  const variableContributions = useMemo(
    () => (activeResult ? resolveVariableContributions(activeResult) : []),
    [activeResult]
  );

  const varBySlug = useMemo(() => {
    const map: Record<string, (typeof variableContributions)[number]> = {};
    for (const v of variableContributions) map[v.key] = v;
    return map;
  }, [variableContributions]);

  const dailySalesBySlug = useMemo(() => {
    const out: Record<string, number[]> = {};
    for (const col of columns) {
      const v = varBySlug[col];
      const baseSpend = baselineTotals[col] ?? 0;
      const curSpend = channelTotals[col] ?? 0;
      const scale = dirty && baseSpend > 0 && !lockedSlugs.has(col) ? curSpend / baseSpend : 1;
      out[col] = Array.from({ length: days }, (_, i) => {
        const raw = v?.daily_scenario?.[i] ?? 0;
        return Math.round(raw * scale);
      });
    }
    return out;
  }, [columns, varBySlug, baselineTotals, channelTotals, dirty, lockedSlugs, days]);

  const structuralDailySales = useMemo(() => {
    const colSet = new Set(columns);
    const structVars = variableContributions.filter((v) => !colSet.has(v.key));
    return Array.from({ length: days }, (_, i) =>
      Math.round(structVars.reduce((acc, v) => acc + (v.daily_scenario?.[i] ?? 0), 0))
    );
  }, [variableContributions, columns, days]);

  const channelSalesTotals = useMemo(() => {
    const out: Record<string, number> = {};
    for (const col of columns) {
      const arr = dailySalesBySlug[col] ?? [];
      out[col] = arr.reduce((a, b) => a + b, 0);
    }
    return out;
  }, [columns, dailySalesBySlug]);

  const structuralTotalSales = useMemo(
    () => structuralDailySales.reduce((a, b) => a + b, 0),
    [structuralDailySales]
  );

  const rowSalesTotals = useMemo(() => {
    return Array.from({ length: days }, (_, rowIdx) => {
      if (!dirty && activeResult?.daily?.[rowIdx]?.scenario_forecast != null) {
        return Math.round(activeResult.daily[rowIdx].scenario_forecast);
      }
      const chSum = columns.reduce((acc, col) => acc + (dailySalesBySlug[col]?.[rowIdx] ?? 0), 0);
      return chSum + (structuralDailySales[rowIdx] ?? 0);
    });
  }, [days, dirty, activeResult, columns, dailySalesBySlug, structuralDailySales]);

  const grandSalesTotal = useMemo(
    () => rowSalesTotals.reduce((a, b) => a + b, 0),
    [rowSalesTotals]
  );
  const showStructuralCol = cellMode === "sales" || cellMode === "both";

  const defaultPlanName = useMemo(() => {
    if (visibleDates.length === 0) return `Budget plan (${days}d)`;
    const start = visibleDates[0];
    const end = visibleDates[visibleDates.length - 1];
    const diffLabel = Math.abs(grandDeltaPct) >= 0.5 ? ` · ${signedPct(grandDeltaPct, 0)} vs baseline` : "";
    return `Plan ${start} → ${end} (${days}d${diffLabel})`;
  }, [visibleDates, days, grandDeltaPct]);

  const updateSingleCell = (col: string, rowIdx: number, raw: string) => {
    const parsed = parseCellNumber(raw);
    if (parsed === null) return;
    const rounded = Math.max(0, Math.round(parsed));
    setDailyBySlug((prev) => {
      const prevCol = prev[col] ?? [];
      if (prevCol[rowIdx] === rounded) return prev;
      const nextCol = [...prevCol];
      nextCol[rowIdx] = rounded;
      setDirty(true);
      return { ...prev, [col]: nextCol };
    });
  };

  const updateChannelTotal = (col: string, raw: string) => {
    const trimmed = raw.trim();
    if (!trimmed) return;
    const currentSum = channelTotals[col] ?? 0;
    const baseSum = baselineTotals[col] ?? 0;
    let targetTotal: number | null = null;

    if (trimmed.endsWith("%") || trimmed.startsWith("+") || trimmed.startsWith("-") || trimmed.startsWith("−")) {
      const pct = parsePctChange(trimmed, -100, 500);
      if (pct !== null) {
        const ref = baseSum > 0 ? baseSum : currentSum;
        targetTotal = Math.max(0, ref * (1 + pct / 100));
      }
    } else {
      targetTotal = parseCellNumber(trimmed);
    }

    if (targetTotal === null || targetTotal < 0) return;

    setDailyBySlug((prev) => {
      const prevArr = [...(prev[col] ?? [])];
      const baseArr = template?.dailyBySlug[col] ?? [];
      if (targetTotal === 0) {
        for (let i = 0; i < days; i++) prevArr[i] = 0;
      } else if (currentSum > 0) {
        const ratio = targetTotal / currentSum;
        for (let i = 0; i < days; i++) prevArr[i] = Math.round((prevArr[i] ?? 0) * ratio);
      } else if (baseSum > 0) {
        const ratio = targetTotal / baseSum;
        for (let i = 0; i < days; i++) prevArr[i] = Math.round((baseArr[i] ?? 0) * ratio);
      } else {
        const perDay = Math.round(targetTotal / Math.max(1, days));
        for (let i = 0; i < days; i++) prevArr[i] = perDay;
      }
      setDirty(true);
      return { ...prev, [col]: prevArr };
    });
  };

  const handleCellPaste = (e: React.ClipboardEvent<HTMLInputElement>, startRow: number, startColIdx: number) => {
    const text = e.clipboardData.getData("text/plain");
    if (!text) return;
    const isMultiCell = text.includes("\t") || text.includes("\n") || text.includes("\r");
    if (!isMultiCell) return;

    e.preventDefault();
    setEditingCell(null);
    const { nextDaily, cellsUpdated } = applySpreadsheetPaste(
      text,
      startRow,
      startColIdx,
      columns,
      dailyBySlug,
      days
    );
    if (cellsUpdated > 0) {
      setDailyBySlug(nextDaily);
      setDirty(true);
      setPasteNotice(`Pasted ${cellsUpdated} cell${cellsUpdated === 1 ? "" : "s"} into the budget table.`);
      window.setTimeout(() => setPasteNotice(null), 4000);
    }
  };

  const focusCell = (rowIdx: number, colIdx: number) => {
    const clampedRow = Math.max(0, Math.min(days - 1, rowIdx));
    const clampedCol = Math.max(0, Math.min(columns.length - 1, colIdx));
    const key = `${clampedRow}:${columns[clampedCol]}`;
    const el = cellRefs.current.get(key);
    if (el) {
      el.focus();
      el.select();
    }
  };

  const handleResetToBaseline = () => {
    if (!template) return;
    setDailyBySlug(cloneDaily(template.dailyBySlug, template.columns, template.dates.length));
    setDirty(true);
    setEditingCell(null);
  };

  const handleResetToCurrentPlan = () => {
    if (!template) return;
    if (activeResult?.plan_daily) {
      applyHorizonDays(activeResult.horizon_days || 42, false);
      setDailyBySlug(mergePlanIntoTemplate(template, activeResult.plan_daily));
    } else {
      setDailyBySlug(cloneDaily(template.dailyBySlug, template.columns, template.dates.length));
    }
    setDirty(false);
    setEditingCell(null);
  };

  const handleDownloadCsv = () => {
    if (!template) return;
    const effectiveDays = commitDaysDraft();
    if (cellMode === "sales") {
      const header = ["date", ...columns.map((c) => `${c}_forecast_sales_eur`), "baseline_calendar_sales_eur", "total_forecast_sales_eur"].join(",");
      const rows = Array.from({ length: Math.min(effectiveDays, visibleDates.length) }, (_, i) =>
        [
          visibleDates[i],
          ...columns.map((c) => String(Math.round(dailySalesBySlug[c]?.[i] ?? 0))),
          String(Math.round(structuralDailySales[i] ?? 0)),
          String(Math.round(rowSalesTotals[i] ?? 0)),
        ].join(",")
      );
      const csv = [header, ...rows].join("\n");
      const url = URL.createObjectURL(new Blob([csv], { type: "text/csv;charset=utf-8" }));
      const a = document.createElement("a");
      a.href = url;
      a.download = `forecast_sales_by_channel_${effectiveDays}d.csv`;
      a.click();
      URL.revokeObjectURL(url);
      return;
    }
    const csv = buildSpreadsheetCsv(template.dates, columns, dailyBySlug, effectiveDays);
    const url = URL.createObjectURL(new Blob([csv], { type: "text/csv;charset=utf-8" }));
    const a = document.createElement("a");
    a.href = url;
    a.download = `budget_plan_${effectiveDays}d.csv`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const handleRunPlan = async () => {
    if (!template) return;
    const effectiveDays = commitDaysDraft();
    setSubmitting(true);
    setErrorMsg(null);
    try {
      const csv = buildSpreadsheetCsv(template.dates, columns, dailyBySlug, effectiveDays);
      const file = new File([csv], `budget_plan_${effectiveDays}d.csv`, { type: "text/csv" });
      const datesSlice = template.dates.slice(0, effectiveDays);
      const fallbackName =
        datesSlice.length > 0
          ? `Plan ${datesSlice[0]} → ${datesSlice[datesSlice.length - 1]} (${effectiveDays}d${
              Math.abs(grandDeltaPct) >= 0.5 ? ` · ${signedPct(grandDeltaPct, 0)} vs baseline` : ""
            })`
          : `Budget plan (${effectiveDays}d)`;
      const created = await api.uploadForecastPlan(runId, file, planName.trim() || fallbackName);
      submittedIdsRef.current.add(created.id);
      hydratedKeyRef.current = `${runId}:${created.id}:result`;
      setDirty(false);
      setPlanName("");
      onPlanCreated(created);
    } catch (err) {
      setErrorMsg(err instanceof Error ? err.message : "Could not submit forecast plan");
    } finally {
      setSubmitting(false);
    }
  };

  const busy = submitting || forecastBusy;
  const dateColWidth = 114;
  const totalColWidth = 118;
  const totalDynamicCols = Math.max(1, columns.length + (showStructuralCol ? 1 : 0));

  return (
    <Paper component="section" aria-labelledby="plan-spreadsheet-title" sx={{ p: { xs: 2, md: 2.5 }, mb: 3 }}>
      <Stack
        direction={{ xs: "column", md: "row" }}
        spacing={2}
        justifyContent="space-between"
        alignItems={{ xs: "flex-start", md: "center" }}
        sx={{ mb: 2 }}
      >
        <Box>
          <Stack direction="row" spacing={1.25} alignItems="center" flexWrap="wrap" useFlexGap>
            <Typography id="plan-spreadsheet-title" variant="h3" component="h2">
              {cellMode === "sales"
                ? "Daily Forecast Sales by Channel (EUR Cells)"
                : cellMode === "both"
                  ? "Daily Forecast Sales & Budget Spend by Channel (EUR Cells)"
                  : "Daily Channel Budget Plan (EUR Cells)"}
            </Typography>
            <ToggleButtonGroup
              size="small"
              exclusive
              value={cellMode}
              onChange={(_, v: SpreadsheetCellMode | null) => v && handleCellModeChange(v)}
              aria-label="Spreadsheet cell value mode"
            >
              <ToggleButton value="sales" sx={{ textTransform: "none", px: 1.25, py: 0.25, fontWeight: 700 }}>
                Forecast Sales Cells (€)
              </ToggleButton>
              <ToggleButton value="spend" sx={{ textTransform: "none", px: 1.25, py: 0.25, fontWeight: 700 }}>
                Budget Spend Cells (€)
              </ToggleButton>
              <ToggleButton value="both" sx={{ textTransform: "none", px: 1.25, py: 0.25, fontWeight: 700 }}>
                Both (Sales + Spend)
              </ToggleButton>
            </ToggleButtonGroup>
            {dirty && <Chip size="small" color="warning" label="Unsaved edits" sx={{ fontWeight: 700 }} />}
          </Stack>
          <Typography variant="body2" sx={{ color: PALETTE.inkSecondary, mt: 0.35 }}>
            {cellMode === "sales"
              ? "Each cell displays the exact forecasted daily sales contribution (EUR) for that individual channel, plus Baseline & Calendar and Total Daily Forecast Sales."
              : "Edit daily channel budgets directly, type a new channel total or % in the summary row, or paste cells from Excel / Google Sheets."}
          </Typography>
        </Box>

        <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap alignItems="center">
          {dirty && (
            <Button
              size="small"
              variant="text"
              startIcon={<RestoreIcon fontSize="small" />}
              onClick={handleResetToCurrentPlan}
              disabled={busy}
            >
              Revert edits
            </Button>
          )}
          <Button
            size="small"
            variant="outlined"
            startIcon={<RestartAltIcon fontSize="small" />}
            onClick={handleResetToBaseline}
            disabled={!template || busy}
          >
            Reset to baseline
          </Button>
          <Button
            size="small"
            variant="outlined"
            startIcon={<DownloadIcon fontSize="small" />}
            onClick={handleDownloadCsv}
            disabled={!template || busy}
          >
            Export CSV
          </Button>
          <Button
            size="small"
            variant="contained"
            color="secondary"
            startIcon={busy ? <CircularProgress size={14} color="inherit" /> : <PlayArrowIcon fontSize="small" />}
            onClick={() => void handleRunPlan()}
            disabled={!template || busy}
          >
            {busy ? `Running forecast (${days}d)…` : "Run forecast plan"}
          </Button>
        </Stack>
      </Stack>

      {errorMsg && (
        <Alert severity="error" sx={{ mb: 2 }} onClose={() => setErrorMsg(null)}>
          {errorMsg}
        </Alert>
      )}

      {pasteNotice && (
        <Alert severity="success" sx={{ mb: 2 }} onClose={() => setPasteNotice(null)}>
          {pasteNotice}
        </Alert>
      )}

      {/* Horizon & Plan Name Bar */}
      <Stack
        direction={{ xs: "column", lg: "row" }}
        spacing={1.5}
        alignItems={{ xs: "stretch", lg: "center" }}
        justifyContent="space-between"
        sx={{
          p: 1.5,
          mb: 2,
          borderRadius: 1,
          backgroundColor: PALETTE.bgSubtle,
          border: `1px solid ${PALETTE.borderSubtle}`,
        }}
      >
        <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap" useFlexGap>
          <Typography variant="caption" sx={{ fontWeight: 800, textTransform: "uppercase", color: PALETTE.brandPrimary }}>
            Horizon:
          </Typography>
          {HORIZON_PRESETS.map((preset) => (
            <Chip
              key={preset}
              label={`${preset}d`}
              size="small"
              color={days === preset ? "primary" : "default"}
              variant={days === preset ? "filled" : "outlined"}
              onClick={() => applyHorizonDays(preset, true)}
              sx={{ ...NUMERIC, fontWeight: days === preset ? 700 : 500, backgroundColor: days === preset ? undefined : "#FFFFFF" }}
            />
          ))}
          <TextField
            name="spreadsheet-horizon-days"
            size="small"
            type="number"
            value={daysDraft}
            onChange={(e) => {
              const raw = e.target.value;
              setDaysDraft(raw);
              const trimmed = raw.trim();
              if (trimmed !== "") {
                const v = Number(trimmed);
                if (Number.isFinite(v) && v >= 1 && v <= 90) {
                  setDays(Math.round(v));
                  setDirty(true);
                }
              }
            }}
            onBlur={() => {
              commitDaysDraft();
            }}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                commitDaysDraft();
              }
            }}
            inputProps={{
              min: 1,
              max: 90,
              "aria-label": "Forecast horizon in days",
              style: { textAlign: "right", ...NUMERIC, fontWeight: 700, width: 42, padding: "4px 6px" },
            }}
            InputProps={{ endAdornment: <InputAdornment position="end">d</InputAdornment> }}
            sx={{ backgroundColor: "#FFFFFF" }}
          />
          {visibleDates.length > 0 && (
            <Typography variant="body2" sx={{ color: PALETTE.inkSecondary, ...NUMERIC, fontWeight: 600 }}>
              {shortDate(visibleDates[0])} – {shortDate(visibleDates[visibleDates.length - 1])}
            </Typography>
          )}
        </Stack>

        <TextField
          name="spreadsheet-plan-name"
          size="small"
          label="Plan name"
          placeholder={defaultPlanName}
          value={planName}
          onChange={(e) => setPlanName(e.target.value)}
          sx={{ minWidth: { xs: "100%", lg: 300 }, backgroundColor: "#FFFFFF" }}
        />
      </Stack>

      {loadingTemplate || !template ? (
        <Stack direction="row" spacing={1.5} alignItems="center" sx={{ py: 4, justifyContent: "center" }}>
          <CircularProgress size={20} color="secondary" />
          <Typography variant="body2">Loading daily budget template…</Typography>
        </Stack>
      ) : (
        <TableContainer
          role="region"
          aria-label="Editable daily channel budget spreadsheet"
          sx={{
            maxHeight: 520,
            border: `1px solid ${PALETTE.borderSubtle}`,
            borderRadius: 1,
            backgroundColor: "#FFFFFF",
            overflowX: "auto",
          }}
        >
          <Table
            size="small"
            stickyHeader
            sx={{
              tableLayout: "fixed",
              width: "100%",
              minWidth: showStructuralCol ? 880 : 780,
              borderCollapse: "separate",
              borderSpacing: 0,
            }}
          >
            <colgroup>
              <col style={{ width: `${dateColWidth}px` }} />
              {columns.map((col) => (
                <col
                  key={col}
                  style={{
                    width: `calc((100% - ${dateColWidth + totalColWidth}px) / ${totalDynamicCols})`,
                  }}
                />
              ))}
              {showStructuralCol && (
                <col
                  style={{
                    width: `calc((100% - ${dateColWidth + totalColWidth}px) / ${totalDynamicCols})`,
                  }}
                />
              )}
              <col style={{ width: `${totalColWidth}px` }} />
            </colgroup>

            <TableHead>
              <TableRow>
                <TableCell
                  scope="col"
                  sx={{
                    position: "sticky",
                    left: 0,
                    zIndex: 4,
                    backgroundColor: PALETTE.bgSubtle,
                    borderBottom: `2px solid ${PALETTE.borderStrong}`,
                    borderRight: `1px solid ${PALETTE.borderSubtle}`,
                    px: 1,
                    py: 1,
                    verticalAlign: "middle",
                    fontWeight: 700,
                    fontSize: "0.74rem",
                  }}
                >
                  Date ({days}d)
                </TableCell>
                {columns.map((col) => {
                  const isControl = lockedSlugs.has(col);
                  const label = resolveChannelLabel(col, channelNames);
                  return (
                    <TableCell
                      key={col}
                      scope="col"
                      align="right"
                      sx={{
                        backgroundColor: PALETTE.bgSubtle,
                        borderBottom: `2px solid ${PALETTE.borderStrong}`,
                        borderRight: `1px solid ${PALETTE.borderSubtle}`,
                        px: 0.75,
                        py: 1,
                        verticalAlign: "middle",
                        fontWeight: 700,
                        fontSize: "0.73rem",
                        lineHeight: 1.2,
                      }}
                    >
                      <Box sx={{ display: "inline-flex", alignItems: "center", justifyContent: "flex-end", gap: 0.4, textAlign: "right" }}>
                        {isControl && (
                          <Tooltip title="Control channel in the model (included in plan table)">
                            <LockOutlinedIcon sx={{ fontSize: 13, color: PALETTE.inkSecondary, flexShrink: 0 }} />
                          </Tooltip>
                        )}
                        <span>{label}</span>
                      </Box>
                      <Typography
                        variant="caption"
                        sx={{
                          display: "block",
                          fontSize: "0.62rem",
                          fontWeight: 600,
                          color: cellMode === "sales" ? PALETTE.semanticGood : PALETTE.inkSecondary,
                          lineHeight: 1.1,
                          mt: 0.15,
                        }}
                      >
                        {cellMode === "sales" ? "Forecast Sales (€)" : cellMode === "both" ? "Spend + Sales (€)" : "Budget Spend (€)"}
                      </Typography>
                    </TableCell>
                  );
                })}
                {showStructuralCol && (
                  <TableCell
                    scope="col"
                    align="right"
                    sx={{
                      backgroundColor: alpha(PALETTE.brandPrimary, 0.08),
                      borderBottom: `2px solid ${PALETTE.borderStrong}`,
                      borderRight: `1px solid ${PALETTE.borderSubtle}`,
                      px: 0.75,
                      py: 1,
                      verticalAlign: "middle",
                      fontWeight: 700,
                      fontSize: "0.73rem",
                      lineHeight: 1.2,
                    }}
                  >
                    <Tooltip title="Combined daily sales contribution from baseline demand, annual seasonality, weekday profile, long-term trend and holidays">
                      <span>Baseline &amp; Calendar</span>
                    </Tooltip>
                    <Typography
                      variant="caption"
                      sx={{
                        display: "block",
                        fontSize: "0.62rem",
                        fontWeight: 600,
                        color: PALETTE.brandPrimary,
                        lineHeight: 1.1,
                        mt: 0.15,
                      }}
                    >
                      Structural Sales (€)
                    </Typography>
                  </TableCell>
                )}
                <TableCell
                  scope="col"
                  align="right"
                  sx={{
                    backgroundColor: cellMode === "sales" ? alpha(PALETTE.semanticGood, 0.1) : PALETTE.bgAccentTint,
                    borderBottom: `2px solid ${PALETTE.borderStrong}`,
                    px: 1,
                    py: 1,
                    verticalAlign: "middle",
                    fontWeight: 800,
                    fontSize: "0.74rem",
                    lineHeight: 1.2,
                  }}
                >
                  {cellMode === "sales" ? "Daily Forecast (€)" : cellMode === "both" ? "Daily Total (€)" : "Daily Spend (€)"}
                </TableCell>
              </TableRow>
            </TableHead>

            <TableBody>
              {/* Summary Row 1: Period Forecast Sales per channel (always shown so user sees exact channel forecast values as cells) */}
              <TableRow sx={{ backgroundColor: alpha(PALETTE.semanticGood, 0.06) }}>
                <TableCell
                  component="th"
                  scope="row"
                  sx={{
                    position: "sticky",
                    left: 0,
                    zIndex: 2,
                    backgroundColor: PALETTE.bgGoodTint,
                    borderBottom: cellMode === "sales" ? `2px solid ${PALETTE.borderStrong}` : `1px solid ${PALETTE.borderSubtle}`,
                    borderRight: `1px solid ${PALETTE.borderSubtle}`,
                    px: 1,
                    py: 0.75,
                  }}
                >
                  <Typography sx={{ fontWeight: 800, fontSize: "0.76rem", color: PALETTE.semanticGood, lineHeight: 1.2 }}>
                    Forecast Sales (Σ)
                  </Typography>
                  <Typography variant="caption" sx={{ color: PALETTE.inkSecondary, fontSize: "0.65rem", display: "block", lineHeight: 1.2, mt: 0.2 }}>
                    {days}d channel sales (€)
                  </Typography>
                </TableCell>
                {columns.map((col) => {
                  const salesTotal = channelSalesTotals[col] ?? 0;
                  const spendTotal = channelTotals[col] ?? 0;
                  const sharePct = grandSalesTotal > 0 ? (salesTotal / grandSalesTotal) * 100 : 0;
                  const roiVal = spendTotal > 0 ? salesTotal / spendTotal : null;
                  const label = resolveChannelLabel(col, channelNames);
                  return (
                    <TableCell
                      key={`sales-total-${col}`}
                      align="right"
                      sx={{
                        backgroundColor: alpha(PALETTE.semanticGood, 0.06),
                        borderBottom: cellMode === "sales" ? `2px solid ${PALETTE.borderStrong}` : `1px solid ${PALETTE.borderSubtle}`,
                        borderRight: `1px solid ${PALETTE.borderSubtle}`,
                        px: 0.5,
                        py: 0.5,
                        verticalAlign: "top",
                      }}
                    >
                      <Box
                        component="input"
                        type="text"
                        readOnly
                        aria-label={`${label} period forecast sales in euros`}
                        value={`€${Math.round(salesTotal).toLocaleString("en-GB")}`}
                        onFocus={(e: React.FocusEvent<HTMLInputElement>) => e.currentTarget.select()}
                        sx={{
                          boxSizing: "border-box",
                          minWidth: 0,
                          width: "100%",
                          border: `1px solid ${alpha(PALETTE.semanticGood, 0.45)}`,
                          borderRadius: 1,
                          px: 0.6,
                          py: 0.3,
                          textAlign: "right",
                          fontWeight: 800,
                          fontSize: "0.78rem",
                          backgroundColor: "#FFFFFF",
                          color: PALETTE.semanticGood,
                          cursor: "default",
                          ...NUMERIC,
                        }}
                      />
                      <Typography
                        variant="caption"
                        sx={{
                          display: "block",
                          mt: 0.25,
                          fontSize: "0.65rem",
                          lineHeight: 1.15,
                          whiteSpace: "nowrap",
                          overflow: "hidden",
                          textOverflow: "ellipsis",
                          color: PALETTE.inkSecondary,
                          fontWeight: 600,
                          ...NUMERIC,
                        }}
                      >
                        {sharePct.toFixed(1)}% share{roiVal !== null ? ` · ROI ${roiVal.toFixed(2)}×` : ""}
                      </Typography>
                    </TableCell>
                  );
                })}
                {showStructuralCol && (
                  <TableCell
                    align="right"
                    sx={{
                      backgroundColor: alpha(PALETTE.semanticGood, 0.06),
                      borderBottom: cellMode === "sales" ? `2px solid ${PALETTE.borderStrong}` : `1px solid ${PALETTE.borderSubtle}`,
                      borderRight: `1px solid ${PALETTE.borderSubtle}`,
                      px: 0.5,
                      py: 0.5,
                      verticalAlign: "top",
                    }}
                  >
                    <Box
                      component="input"
                      type="text"
                      readOnly
                      aria-label="Baseline and calendar period forecast sales in euros"
                      value={`€${Math.round(structuralTotalSales).toLocaleString("en-GB")}`}
                      onFocus={(e: React.FocusEvent<HTMLInputElement>) => e.currentTarget.select()}
                      sx={{
                        boxSizing: "border-box",
                        minWidth: 0,
                        width: "100%",
                        border: `1px solid ${alpha(PALETTE.brandPrimary, 0.55)}`,
                        borderRadius: 1,
                        px: 0.6,
                        py: 0.3,
                        textAlign: "right",
                        fontWeight: 800,
                        fontSize: "0.78rem",
                        backgroundColor: "#FFFFFF",
                        color: PALETTE.brandPrimary,
                        cursor: "default",
                        ...NUMERIC,
                      }}
                    />
                    <Typography
                      variant="caption"
                      sx={{
                        display: "block",
                        mt: 0.25,
                        fontSize: "0.65rem",
                        lineHeight: 1.15,
                        whiteSpace: "nowrap",
                        color: PALETTE.inkSecondary,
                        fontWeight: 600,
                        ...NUMERIC,
                      }}
                    >
                      {(grandSalesTotal > 0 ? (structuralTotalSales / grandSalesTotal) * 100 : 0).toFixed(1)}% share · Structural
                    </Typography>
                  </TableCell>
                )}
                <TableCell
                  align="right"
                  sx={{
                    backgroundColor: alpha(PALETTE.semanticGood, 0.1),
                    borderBottom: cellMode === "sales" ? `2px solid ${PALETTE.borderStrong}` : `1px solid ${PALETTE.borderSubtle}`,
                    px: 0.75,
                    py: 0.6,
                    verticalAlign: "middle",
                    ...NUMERIC,
                  }}
                >
                  <Typography sx={{ fontWeight: 800, fontSize: "0.82rem", color: PALETTE.semanticGood, lineHeight: 1.2, whiteSpace: "nowrap", ...NUMERIC }}>
                    {eur(grandSalesTotal)}
                  </Typography>
                  <Typography
                    variant="caption"
                    sx={{
                      color: PALETTE.inkSecondary,
                      fontSize: "0.65rem",
                      display: "block",
                      lineHeight: 1.2,
                      mt: 0.2,
                      whiteSpace: "nowrap",
                      ...NUMERIC,
                    }}
                  >
                    {eurCompact(grandSalesTotal / Math.max(1, days))}/d · 100%
                  </Typography>
                </TableCell>
              </TableRow>

              {/* Summary Row 2: Editable Period Spend Total per channel (shown in Spend and Both modes) */}
              {cellMode !== "sales" && (
                <TableRow sx={{ backgroundColor: PALETTE.bgAccentTint }}>
                  <TableCell
                    component="th"
                    scope="row"
                    sx={{
                      position: "sticky",
                      left: 0,
                      zIndex: 2,
                      backgroundColor: PALETTE.bgAccentTint,
                      borderBottom: `2px solid ${PALETTE.borderStrong}`,
                      borderRight: `1px solid ${PALETTE.borderSubtle}`,
                      px: 1,
                      py: 0.75,
                    }}
                  >
                    <Typography sx={{ fontWeight: 800, fontSize: "0.76rem", color: PALETTE.inkPrimary, lineHeight: 1.2 }}>
                      Budget Spend (Σ)
                    </Typography>
                    <Typography variant="caption" sx={{ color: PALETTE.inkSecondary, fontSize: "0.65rem", display: "block", lineHeight: 1.2, mt: 0.2 }}>
                      Type € or ±%
                    </Typography>
                  </TableCell>
                  {columns.map((col) => {
                    const total = channelTotals[col] ?? 0;
                    const base = baselineTotals[col] ?? 0;
                    const pct = base > 0 ? ((total - base) / base) * 100 : 0;
                    const cellKey = `total:${col}`;
                    const isEditing = editingCell?.key === cellKey;
                    const label = resolveChannelLabel(col, channelNames);
                    return (
                      <TableCell
                        key={col}
                        align="right"
                        sx={{
                          backgroundColor: PALETTE.bgAccentTint,
                          borderBottom: `2px solid ${PALETTE.borderStrong}`,
                          borderRight: `1px solid ${PALETTE.borderSubtle}`,
                          px: 0.5,
                          py: 0.5,
                          verticalAlign: "top",
                        }}
                      >
                        <Box
                          component="input"
                          type="text"
                          inputMode="decimal"
                          aria-label={`${label} period total in euros`}
                          value={isEditing ? editingCell.text : Math.round(total).toLocaleString("en-GB")}
                          onFocus={(e: React.FocusEvent<HTMLInputElement>) => {
                            setEditingCell({ key: cellKey, text: String(Math.round(total)) });
                            e.currentTarget.select();
                          }}
                          onChange={(e: React.ChangeEvent<HTMLInputElement>) =>
                            setEditingCell({ key: cellKey, text: e.target.value })
                          }
                          onBlur={() => {
                            if (editingCell?.key === cellKey) {
                              updateChannelTotal(col, editingCell.text);
                              setEditingCell(null);
                            }
                          }}
                          onKeyDown={(e: React.KeyboardEvent<HTMLInputElement>) => {
                            if (e.key === "Enter") {
                              e.currentTarget.blur();
                            } else if (e.key === "Escape") {
                              setEditingCell(null);
                              e.currentTarget.blur();
                            }
                          }}
                          sx={{
                            boxSizing: "border-box",
                            minWidth: 0,
                            width: "100%",
                            border: `1px solid ${alpha(PALETTE.brandPrimary, 0.5)}`,
                            borderRadius: 1,
                            px: 0.6,
                            py: 0.3,
                            textAlign: "right",
                            fontWeight: 700,
                            fontSize: "0.78rem",
                            backgroundColor: "#FFFFFF",
                            color: PALETTE.inkPrimary,
                            ...NUMERIC,
                          }}
                        />
                        <Typography
                          variant="caption"
                          sx={{
                            display: "block",
                            mt: 0.25,
                            fontSize: "0.66rem",
                            lineHeight: 1.15,
                            whiteSpace: "nowrap",
                            overflow: "hidden",
                            textOverflow: "ellipsis",
                            color: Math.abs(pct) >= 0.5 ? PALETTE.brandPrimaryDark : PALETTE.inkSecondary,
                            fontWeight: Math.abs(pct) >= 0.5 ? 700 : 500,
                            ...NUMERIC,
                          }}
                        >
                          {eurCompact(total / Math.max(1, days))}/d · {signedPct(pct, 0)}
                        </Typography>
                      </TableCell>
                    );
                  })}
                  {showStructuralCol && (
                    <TableCell
                      align="right"
                      sx={{
                        backgroundColor: PALETTE.bgAccentTint,
                        borderBottom: `2px solid ${PALETTE.borderStrong}`,
                        borderRight: `1px solid ${PALETTE.borderSubtle}`,
                        px: 0.75,
                        py: 0.5,
                        verticalAlign: "middle",
                        color: PALETTE.inkSecondary,
                        fontSize: "0.72rem",
                        ...NUMERIC,
                      }}
                    >
                      Non-spend
                    </TableCell>
                  )}
                  <TableCell
                    align="right"
                    sx={{
                      backgroundColor: PALETTE.bgAccentTint,
                      borderBottom: `2px solid ${PALETTE.borderStrong}`,
                      px: 0.75,
                      py: 0.6,
                      verticalAlign: "middle",
                      ...NUMERIC,
                    }}
                  >
                    <Typography sx={{ fontWeight: 800, fontSize: "0.82rem", lineHeight: 1.2, whiteSpace: "nowrap", ...NUMERIC }}>
                      {eur(grandTotal)}
                    </Typography>
                    <Typography
                      variant="caption"
                      sx={{
                        color: PALETTE.inkSecondary,
                        fontSize: "0.66rem",
                        display: "block",
                        lineHeight: 1.2,
                        mt: 0.2,
                        whiteSpace: "nowrap",
                        ...NUMERIC,
                      }}
                    >
                      {eurCompact(grandTotal / Math.max(1, days))}/d · {signedPct(grandDeltaPct, 0)}
                    </Typography>
                  </TableCell>
                </TableRow>
              )}

              {/* Daily Rows */}
              {visibleDates.map((dateStr, rowIdx) => {
                const { weekday, dayMonth, isThursday, isWeekend } = formatRowDate(dateStr);
                const rowBg = isWeekend ? alpha(PALETTE.bgSubtle, 0.65) : "#FFFFFF";
                const dayTotalSales = rowSalesTotals[rowIdx] ?? 0;
                const dayTotalSpend = rowTotals[rowIdx] ?? 0;
                const structDaySales = structuralDailySales[rowIdx] ?? 0;
                return (
                  <TableRow key={dateStr} hover>
                    <TableCell
                      component="th"
                      scope="row"
                      sx={{
                        position: "sticky",
                        left: 0,
                        zIndex: 1,
                        backgroundColor: isWeekend ? PALETTE.bgSubtle : "#FFFFFF",
                        borderRight: `1px solid ${PALETTE.borderSubtle}`,
                        whiteSpace: "nowrap",
                        px: 0.75,
                        py: 0.4,
                      }}
                    >
                      <Stack direction="row" spacing={0.6} alignItems="center">
                        <Typography
                          component="span"
                          variant="caption"
                          sx={{
                            minWidth: 26,
                            px: 0.4,
                            py: 0.05,
                            borderRadius: 0.75,
                            textAlign: "center",
                            fontWeight: 700,
                            fontSize: "0.66rem",
                            backgroundColor: isThursday ? PALETTE.bgAccentTint : PALETTE.bgSubtle,
                            color: isThursday ? PALETTE.brandPrimary : PALETTE.inkSecondary,
                          }}
                        >
                          {weekday}
                        </Typography>
                        <Typography sx={{ fontSize: "0.78rem", fontWeight: 600, ...NUMERIC }}>{dayMonth}</Typography>
                      </Stack>
                    </TableCell>

                    {columns.map((col, colIdx) => {
                      const val = dailyBySlug[col]?.[rowIdx] ?? 0;
                      const salesVal = dailySalesBySlug[col]?.[rowIdx] ?? 0;
                      const cellKey = `${rowIdx}:${col}`;
                      const isEditing = editingCell?.key === cellKey;
                      const label = resolveChannelLabel(col, channelNames);

                      if (cellMode === "sales") {
                        return (
                          <TableCell
                            key={col}
                            align="right"
                            sx={{
                              p: 0,
                              backgroundColor: rowBg,
                              borderRight: `1px solid ${PALETTE.borderSubtle}`,
                            }}
                          >
                            <Box
                              component="input"
                              type="text"
                              readOnly
                              aria-label={`${label} forecast sales on ${dateStr}`}
                              value={Math.round(salesVal).toLocaleString("en-GB")}
                              onFocus={(e: React.FocusEvent<HTMLInputElement>) => e.currentTarget.select()}
                              sx={{
                                boxSizing: "border-box",
                                minWidth: 0,
                                width: "100%",
                                border: "none",
                                px: 0.75,
                                py: 0.55,
                                textAlign: "right",
                                fontSize: "0.8rem",
                                fontWeight: Math.abs(salesVal) > 0.5 ? 700 : 400,
                                color: salesVal > 0 ? PALETTE.semanticGood : salesVal < 0 ? PALETTE.semanticBad : PALETTE.inkMuted,
                                backgroundColor: "transparent",
                                cursor: "default",
                                ...NUMERIC,
                                "&:focus": {
                                  backgroundColor: alpha(PALETTE.semanticGood, 0.08),
                                  outline: `1px solid ${alpha(PALETTE.semanticGood, 0.4)}`,
                                },
                              }}
                            />
                          </TableCell>
                        );
                      }

                      return (
                        <TableCell
                          key={col}
                          align="right"
                          sx={{
                            p: 0,
                            backgroundColor: rowBg,
                            borderRight: `1px solid ${PALETTE.borderSubtle}`,
                          }}
                        >
                          <Box
                            component="input"
                            ref={(el: HTMLInputElement | null) => {
                              if (el) cellRefs.current.set(cellKey, el);
                              else cellRefs.current.delete(cellKey);
                            }}
                            type="text"
                            inputMode="decimal"
                            aria-label={`${label} spend on ${dateStr}`}
                            value={isEditing ? editingCell.text : Math.round(val).toLocaleString("en-GB")}
                            onFocus={(e: React.FocusEvent<HTMLInputElement>) => {
                              setEditingCell({ key: cellKey, text: String(Math.round(val)) });
                              e.currentTarget.select();
                            }}
                            onChange={(e: React.ChangeEvent<HTMLInputElement>) =>
                              setEditingCell({ key: cellKey, text: e.target.value })
                            }
                            onBlur={() => {
                              if (editingCell?.key === cellKey) {
                                updateSingleCell(col, rowIdx, editingCell.text);
                                setEditingCell(null);
                              }
                            }}
                            onPaste={(e: React.ClipboardEvent<HTMLInputElement>) => handleCellPaste(e, rowIdx, colIdx)}
                            onKeyDown={(e: React.KeyboardEvent<HTMLInputElement>) => {
                              if (e.key === "Enter" || e.key === "ArrowDown") {
                                e.preventDefault();
                                if (editingCell?.key === cellKey) {
                                  updateSingleCell(col, rowIdx, editingCell.text);
                                  setEditingCell(null);
                                }
                                focusCell(rowIdx + 1, colIdx);
                              } else if (e.key === "ArrowUp") {
                                e.preventDefault();
                                if (editingCell?.key === cellKey) {
                                  updateSingleCell(col, rowIdx, editingCell.text);
                                  setEditingCell(null);
                                }
                                focusCell(rowIdx - 1, colIdx);
                              } else if (e.key === "Escape") {
                                setEditingCell(null);
                                e.currentTarget.blur();
                              }
                            }}
                            sx={{
                              boxSizing: "border-box",
                              minWidth: 0,
                              width: "100%",
                              border: "none",
                              px: 0.75,
                              py: cellMode === "both" ? 0.35 : 0.55,
                              textAlign: "right",
                              fontSize: "0.8rem",
                              fontWeight: val > 0 ? 600 : 400,
                              color: val > 0 ? PALETTE.inkPrimary : PALETTE.inkMuted,
                              backgroundColor: "transparent",
                              ...NUMERIC,
                              "&:focus": {
                                backgroundColor: alpha(PALETTE.brandPrimary, 0.08),
                                fontWeight: 700,
                              },
                            }}
                          />
                          {cellMode === "both" && (
                            <Box
                              sx={{
                                px: 0.75,
                                py: 0.2,
                                borderTop: `1px dashed ${alpha(PALETTE.semanticGood, 0.22)}`,
                                backgroundColor: alpha(PALETTE.semanticGood, 0.04),
                                textAlign: "right",
                                fontSize: "0.72rem",
                                fontWeight: 700,
                                color: PALETTE.semanticGood,
                                ...NUMERIC,
                              }}
                            >
                              Sales: €{Math.round(salesVal).toLocaleString("en-GB")}
                            </Box>
                          )}
                        </TableCell>
                      );
                    })}

                    {showStructuralCol && (
                      <TableCell
                        align="right"
                        sx={{
                          p: 0,
                          backgroundColor: alpha(PALETTE.brandPrimary, 0.04),
                          borderRight: `1px solid ${PALETTE.borderSubtle}`,
                        }}
                      >
                        <Box
                          component="input"
                          type="text"
                          readOnly
                          aria-label={`Baseline and calendar forecast sales on ${dateStr}`}
                          value={Math.round(structDaySales).toLocaleString("en-GB")}
                          onFocus={(e: React.FocusEvent<HTMLInputElement>) => e.currentTarget.select()}
                          sx={{
                            boxSizing: "border-box",
                            minWidth: 0,
                            width: "100%",
                            border: "none",
                            px: 0.75,
                            py: 0.55,
                            textAlign: "right",
                            fontSize: "0.8rem",
                            fontWeight: 600,
                            color: PALETTE.brandPrimary,
                            backgroundColor: "transparent",
                            cursor: "default",
                            ...NUMERIC,
                          }}
                        />
                      </TableCell>
                    )}

                    <TableCell
                      align="right"
                      sx={{
                        backgroundColor: cellMode === "sales" ? alpha(PALETTE.semanticGood, 0.08) : alpha(PALETTE.bgAccentTint, 0.45),
                        fontWeight: 700,
                        fontSize: "0.78rem",
                        whiteSpace: "nowrap",
                        px: 0.75,
                        py: 0.4,
                        color: cellMode === "sales" ? PALETTE.semanticGood : PALETTE.inkPrimary,
                        ...NUMERIC,
                      }}
                    >
                      {cellMode === "sales" ? (
                        eur(dayTotalSales)
                      ) : cellMode === "both" ? (
                        <Box>
                          <Typography sx={{ fontSize: "0.76rem", fontWeight: 700, lineHeight: 1.2, ...NUMERIC }}>
                            {eur(dayTotalSpend)}
                          </Typography>
                          <Typography sx={{ fontSize: "0.7rem", fontWeight: 700, color: PALETTE.semanticGood, lineHeight: 1.2, ...NUMERIC }}>
                            {eur(dayTotalSales)}
                          </Typography>
                        </Box>
                      ) : (
                        eur(dayTotalSpend)
                      )}
                    </TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        </TableContainer>
      )}
    </Paper>
  );
};
