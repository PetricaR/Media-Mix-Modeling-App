import React, { useEffect, useId, useMemo, useState } from "react";
import {
  Alert,
  Box,
  Button,
  Checkbox,
  Chip,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Divider,
  InputAdornment,
  Radio,
  RadioGroup,
  Slider,
  Stack,
  TextField,
  Typography,
  alpha,
} from "@mui/material";
import DownloadIcon from "@mui/icons-material/Download";
import PlayArrowIcon from "@mui/icons-material/PlayArrow";
import RestartAltIcon from "@mui/icons-material/RestartAlt";
import LockOutlinedIcon from "@mui/icons-material/LockOutlined";
import { NUMERIC, PALETTE } from "../../theme";
import {
  type BudgetInputMode,
  type SpendGranularity,
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
} from "./format";

interface ParsedTemplate {
  dates: string[];
  columns: string[];
  dailyBySlug: Record<string, number[]>;
  baseBySlug: Record<string, number>;
  defaultSendDates: string[];
  defaultSendCost: number;
}

function parseTemplateCsv(csvText: string): ParsedTemplate {
  const lines = csvText
    .trim()
    .split(/\r?\n/)
    .filter((l) => l.trim().length > 0);
  if (lines.length < 2) {
    return { dates: [], columns: [], dailyBySlug: {}, baseBySlug: {}, defaultSendDates: [], defaultSendCost: 0 };
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
      dailyBySlug[col].push(Number.isFinite(val) ? val : 0);
    });
  }

  const baseBySlug: Record<string, number> = {};
  for (const col of columns) {
    baseBySlug[col] = Math.round(dailyBySlug[col].reduce((a, b) => a + b, 0));
  }

  const catalogDaily = dailyBySlug.catalog ?? [];
  const defaultSendDates = dates.filter((_, idx) => (catalogDaily[idx] ?? 0) > 0);
  const sendCosts = catalogDaily.filter((v) => v > 0);
  const defaultSendCost = sendCosts.length > 0 ? Math.round(sendCosts.reduce((a, b) => a + b, 0) / sendCosts.length) : 1_300_000;

  return { dates, columns, dailyBySlug, baseBySlug, defaultSendDates, defaultSendCost };
}

function buildPlanCsvString(
  template: ParsedTemplate,
  multipliers: Record<string, number>,
  lockedSlugs: Set<string>,
  selectedSendDates: string[]
): string {
  const { dates, columns, dailyBySlug, baseBySlug, defaultSendCost } = template;
  const sendSet = new Set(selectedSendDates);
  const catalogMult = multipliers.catalog ?? 1;
  const targetCatalogTotal = (baseBySlug.catalog ?? defaultSendCost * selectedSendDates.length) * catalogMult;
  const perSendCost = selectedSendDates.length > 0 ? targetCatalogTotal / selectedSendDates.length : 0;

  const header = ["date", ...columns].join(",");
  const rows = dates.map((d, i) => {
    const vals = columns.map((col) => {
      if (lockedSlugs.has(col)) {
        return ((dailyBySlug[col]?.[i] ?? 0)).toFixed(2);
      }
      if (col === "catalog") {
        const v = sendSet.has(d) ? perSendCost : 0;
        return Math.max(0, v).toFixed(2);
      }
      const m = multipliers[col] ?? 1;
      const v = (dailyBySlug[col]?.[i] ?? 0) * m;
      return Math.max(0, v).toFixed(2);
    });
    return [d, ...vals].join(",");
  });
  return [header, ...rows].join("\n");
}

interface PlanBuilderDialogProps {
  open: boolean;
  onClose: () => void;
  runId: string;
  channelNames: Record<string, string>;
  lockedSlugs: Set<string>;
  initialMode: BudgetInputMode;
  initialGranularity?: SpendGranularity;
  onPlanCreated: (forecastId: string) => void;
}

export const PlanBuilderDialog: React.FC<PlanBuilderDialogProps> = ({
  open,
  onClose,
  runId,
  channelNames,
  lockedSlugs,
  initialMode,
  initialGranularity = "period",
  onPlanCreated,
}) => {
  const [days, setDays] = useState<number>(42);
  const [mode, setMode] = useState<BudgetInputMode>(initialMode);
  const [granularity, setGranularity] = useState<SpendGranularity>(initialGranularity);
  const [planName, setPlanName] = useState<string>("");
  const [template, setTemplate] = useState<ParsedTemplate | null>(null);
  const [loadingTemplate, setLoadingTemplate] = useState(false);
  const [multipliers, setMultipliers] = useState<Record<string, number>>({});
  const [sendDates, setSendDates] = useState<string[]>([]);
  const [submitting, setSubmitting] = useState(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  useEffect(() => {
    if (open) {
      setMode(initialMode);
      setGranularity(initialGranularity);
    }
  }, [open, initialMode, initialGranularity]);

  useEffect(() => {
    if (!open || !runId) return;
    let cancelled = false;
    setLoadingTemplate(true);
    setErrorMsg(null);
    fetch(`/api/v1/runs/${encodeURIComponent(runId)}/plan-template?days=${days}`, { credentials: "include" })
      .then(async (res) => {
        if (!res.ok) throw new Error(`Could not load ${days}-day template (${res.status})`);
        return res.text();
      })
      .then((csvText) => {
        if (cancelled) return;
        const parsed = parseTemplateCsv(csvText);
        setTemplate(parsed);
        setSendDates(parsed.defaultSendDates);
        setMultipliers({});
      })
      .catch((err) => {
        if (!cancelled) setErrorMsg(err instanceof Error ? err.message : "Could not load plan template");
      })
      .finally(() => {
        if (!cancelled) setLoadingTemplate(false);
      });
    return () => {
      cancelled = true;
    };
  }, [open, runId, days]);

  const adjustableColumns = useMemo(
    () => (template?.columns ?? []).filter((c) => !lockedSlugs.has(c)),
    [template, lockedSlugs]
  );
  const lockedColumns = useMemo(
    () => (template?.columns ?? []).filter((c) => lockedSlugs.has(c)),
    [template, lockedSlugs]
  );

  // Thursdays in the selected forecast window for easy Catalog send-out selection
  const thursdays = useMemo(() => {
    if (!template) return [];
    return template.dates.filter((d) => new Date(`${d}T00:00:00Z`).getUTCDay() === 4);
  }, [template]);

  const baseMediaTotal = useMemo(() => {
    if (!template) return 0;
    return adjustableColumns.reduce((acc, c) => acc + (template.baseBySlug[c] ?? 0), 0);
  }, [template, adjustableColumns]);

  const scenarioMediaTotal = useMemo(() => {
    if (!template) return 0;
    return adjustableColumns.reduce((acc, c) => acc + (template.baseBySlug[c] ?? 0) * (multipliers[c] ?? 1), 0);
  }, [template, adjustableColumns, multipliers]);

  const deltaMedia = scenarioMediaTotal - baseMediaTotal;
  const deltaPct = baseMediaTotal > 0 ? (deltaMedia / baseMediaTotal) * 100 : 0;

  const setChannelMultiplier = (slug: string, m: number) => {
    const clamped = Math.max(0, Math.min(4, Math.round(m * 1000) / 1000));
    setMultipliers((prev) => ({ ...prev, [slug]: clamped }));
  };

  const handleToggleSendDate = (date: string) => {
    setSendDates((prev) => {
      const next = prev.includes(date) ? prev.filter((d) => d !== date) : [...prev, date].sort();
      if (template && (template.baseBySlug.catalog ?? 0) > 0) {
        const defaultCount = Math.max(1, template.defaultSendDates.length);
        setChannelMultiplier("catalog", next.length / defaultCount);
      }
      return next;
    });
  };

  const defaultName = useMemo(() => {
    if (!template || template.dates.length === 0) return `Custom plan (${days} days)`;
    const start = template.dates[0];
    const end = template.dates[template.dates.length - 1];
    const diffLabel = Math.abs(deltaPct) >= 0.5 ? ` · ${signedPct(deltaPct, 0)} budget` : "";
    return `Custom plan ${start} → ${end} (${days}d${diffLabel})`;
  }, [template, days, deltaPct]);

  const handleDownloadCsv = () => {
    if (!template) return;
    const csv = buildPlanCsvString(template, multipliers, lockedSlugs, sendDates);
    const url = URL.createObjectURL(new Blob([csv], { type: "text/csv;charset=utf-8" }));
    const a = document.createElement("a");
    a.href = url;
    a.download = `budget_plan_${days}d.csv`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const handleSubmitPlan = async () => {
    if (!template) return;
    setSubmitting(true);
    setErrorMsg(null);
    try {
      const csv = buildPlanCsvString(template, multipliers, lockedSlugs, sendDates);
      const file = new File([csv], `custom_plan_${days}d.csv`, { type: "text/csv" });
      const { api } = await import("../../api/client");
      const created = await api.uploadForecastPlan(runId, file, planName.trim() || defaultName);
      onPlanCreated(created.id);
      onClose();
    } catch (err) {
      setErrorMsg(err instanceof Error ? err.message : "Could not submit forecast plan");
    } finally {
      setSubmitting(false);
    }
  };

  const modePctId = useId();
  const modeEurId = useId();
  const granPeriodId = useId();
  const granDailyId = useId();

  return (
    <Dialog open={open} onClose={submitting ? undefined : onClose} maxWidth="md" fullWidth>
      <DialogTitle sx={{ pb: 1 }}>
        <Stack direction="row" justifyContent="space-between" alignItems="center">
          <Box>
            <Typography variant="h3" component="span">
              Configure a new forecast plan
            </Typography>
            <Typography variant="body2" sx={{ color: PALETTE.inkSecondary, mt: 0.25 }}>
              Set the forecast horizon (up to 90 days), choose percentage or fixed euro inputs (period total or daily rate), and configure channel budgets and catalog send-outs.
            </Typography>
          </Box>
          <Button size="small" startIcon={<RestartAltIcon fontSize="small" />} onClick={() => setMultipliers({})}>
            Reset
          </Button>
        </Stack>
      </DialogTitle>

      <DialogContent dividers>
        {errorMsg && (
          <Alert severity="error" sx={{ mb: 2 }} onClose={() => setErrorMsg(null)}>
            {errorMsg}
          </Alert>
        )}

        <Stack spacing={2.5}>
          {/* Plan Name & Forecast Period */}
          <Box
            sx={{
              display: "grid",
              gap: 2,
              gridTemplateColumns: { xs: "1fr", sm: "1fr 1fr" },
              alignItems: "start",
            }}
          >
            <Box component="fieldset" sx={{ border: `1px solid ${PALETTE.borderSubtle}`, borderRadius: 2, p: 1.5, m: 0 }}>
              <Typography component="legend" sx={{ px: 0.75, fontSize: "0.72rem", fontWeight: 800, textTransform: "uppercase", color: PALETTE.brandGoldDark }}>
                1. Forecast period (1–90 days)
              </Typography>
              <Stack direction="row" spacing={1} alignItems="center" justifyContent="space-between">
                <Typography variant="body2" sx={{ fontWeight: 650, ...NUMERIC }}>
                  {template && template.dates.length > 0
                    ? `${shortDate(template.dates[0])} – ${shortDate(template.dates[template.dates.length - 1])}`
                    : `${days} days`}
                </Typography>
                <TextField
                  name="builder-horizon-days"
                  size="small"
                  type="number"
                  value={days}
                  onChange={(e) => {
                    const v = Number(e.target.value);
                    if (Number.isFinite(v) && v >= 1 && v <= 90) setDays(Math.round(v));
                  }}
                  inputProps={{ min: 1, max: 90, "aria-label": "Plan duration in days", style: { textAlign: "right", ...NUMERIC, fontWeight: 700, width: 52 } }}
                  InputProps={{ endAdornment: <InputAdornment position="end">days</InputAdornment> }}
                />
              </Stack>
              <Stack direction="row" spacing={0.75} flexWrap="wrap" useFlexGap sx={{ mt: 1 }}>
                {[7, 14, 28, 42, 60, 90].map((d) => (
                  <Chip
                    key={d}
                    label={d === 42 ? "42d (6w default)" : `${d}d`}
                    size="small"
                    color={days === d ? "primary" : "default"}
                    variant={days === d ? "filled" : "outlined"}
                    onClick={() => setDays(d)}
                    sx={{ ...NUMERIC, fontWeight: days === d ? 700 : 500 }}
                  />
                ))}
              </Stack>
              <Typography variant="caption" component="p" sx={{ mt: 1, color: PALETTE.inkSecondary, lineHeight: 1.35 }}>
                Generates a daily plan CSV with <strong>{days} rows</strong> (1 row/day) × <strong>{template?.columns.length ?? 7} paid channels</strong> in EUR (€).
              </Typography>
            </Box>

            <Box component="fieldset" sx={{ border: `1px solid ${PALETTE.borderSubtle}`, borderRadius: 2, p: 1.5, m: 0 }}>
              <Typography component="legend" sx={{ px: 0.75, fontSize: "0.72rem", fontWeight: 800, textTransform: "uppercase", color: PALETTE.brandGoldDark }}>
                2. Budget input mode & granularity
              </Typography>
              <RadioGroup
                name="builder-budget-input-mode"
                value={mode}
                onChange={(e) => setMode(e.target.value as BudgetInputMode)}
                sx={{ display: "grid", gap: 1, gridTemplateColumns: "repeat(2, minmax(0, 1fr))" }}
              >
                <Box
                  component="label"
                  htmlFor={modePctId}
                  sx={{
                    display: "flex",
                    alignItems: "center",
                    gap: 0.75,
                    p: 1,
                    borderRadius: 1.5,
                    cursor: "pointer",
                    border: `${mode === "pct" ? 2 : 1}px solid ${mode === "pct" ? PALETTE.brandPrimary : PALETTE.borderSubtle}`,
                    backgroundColor: mode === "pct" ? alpha(PALETTE.brandPrimary, 0.06) : "#FFFFFF",
                  }}
                >
                  <Radio id={modePctId} value="pct" size="small" sx={{ p: 0.25, accentColor: PALETTE.brandPrimary }} />
                  <Box>
                    <Typography sx={{ fontWeight: 700, fontSize: "0.85rem" }}>Percentage (%)</Typography>
                    <Typography variant="caption" sx={{ color: PALETTE.inkSecondary }}>vs seasonal run-rate</Typography>
                  </Box>
                </Box>
                <Box
                  component="label"
                  htmlFor={modeEurId}
                  sx={{
                    display: "flex",
                    alignItems: "center",
                    gap: 0.75,
                    p: 1,
                    borderRadius: 1.5,
                    cursor: "pointer",
                    border: `${mode === "eur" ? 2 : 1}px solid ${mode === "eur" ? PALETTE.brandPrimary : PALETTE.borderSubtle}`,
                    backgroundColor: mode === "eur" ? alpha(PALETTE.brandPrimary, 0.06) : "#FFFFFF",
                  }}
                >
                  <Radio id={modeEurId} value="eur" size="small" sx={{ p: 0.25, accentColor: PALETTE.brandPrimary }} />
                  <Box>
                    <Typography sx={{ fontWeight: 700, fontSize: "0.85rem" }}>Fixed amount (€)</Typography>
                    <Typography variant="caption" sx={{ color: PALETTE.inkSecondary }}>Period € or €/day</Typography>
                  </Box>
                </Box>
              </RadioGroup>

              {mode === "eur" && (
                <Box sx={{ mt: 1.25, pt: 1.25, borderTop: `1px dashed ${PALETTE.borderSubtle}` }}>
                  <Typography variant="caption" sx={{ display: "block", mb: 0.5, fontWeight: 700, color: PALETTE.inkPrimary }}>
                    Euro input granularity
                  </Typography>
                  <RadioGroup
                    name="builder-spend-granularity"
                    value={granularity}
                    onChange={(e) => setGranularity(e.target.value as SpendGranularity)}
                    sx={{ display: "grid", gap: 1, gridTemplateColumns: "repeat(2, minmax(0, 1fr))" }}
                  >
                    <Box
                      component="label"
                      htmlFor={granPeriodId}
                      sx={{
                        display: "flex",
                        alignItems: "center",
                        gap: 0.75,
                        p: 0.85,
                        borderRadius: 1.5,
                        cursor: "pointer",
                        border: `${granularity === "period" ? 2 : 1}px solid ${granularity === "period" ? PALETTE.brandPrimary : PALETTE.borderSubtle}`,
                        backgroundColor: granularity === "period" ? alpha(PALETTE.brandPrimary, 0.06) : "#FFFFFF",
                      }}
                    >
                      <Radio id={granPeriodId} value="period" size="small" sx={{ p: 0.25, accentColor: PALETTE.brandPrimary }} />
                      <Box>
                        <Typography sx={{ fontWeight: 700, fontSize: "0.82rem" }}>Period total (€)</Typography>
                        <Typography variant="caption" sx={{ color: PALETTE.inkSecondary }}>Sum over {days}d</Typography>
                      </Box>
                    </Box>
                    <Box
                      component="label"
                      htmlFor={granDailyId}
                      sx={{
                        display: "flex",
                        alignItems: "center",
                        gap: 0.75,
                        p: 0.85,
                        borderRadius: 1.5,
                        cursor: "pointer",
                        border: `${granularity === "daily" ? 2 : 1}px solid ${granularity === "daily" ? PALETTE.brandPrimary : PALETTE.borderSubtle}`,
                        backgroundColor: granularity === "daily" ? alpha(PALETTE.brandPrimary, 0.06) : "#FFFFFF",
                      }}
                    >
                      <Radio id={granDailyId} value="daily" size="small" sx={{ p: 0.25, accentColor: PALETTE.brandPrimary }} />
                      <Box>
                        <Typography sx={{ fontWeight: 700, fontSize: "0.82rem" }}>Daily rate (€/d)</Typography>
                        <Typography variant="caption" sx={{ color: PALETTE.inkSecondary }}>Per day (× {days}d)</Typography>
                      </Box>
                    </Box>
                  </RadioGroup>
                </Box>
              )}
            </Box>
          </Box>

          <TextField
            name="builder-plan-name"
            label="Plan name"
            size="small"
            fullWidth
            placeholder={defaultName}
            value={planName}
            onChange={(e) => setPlanName(e.target.value)}
          />

          {loadingTemplate || !template ? (
            <Stack direction="row" spacing={1.5} alignItems="center" sx={{ py: 3, justifyContent: "center" }}>
              <CircularProgress size={20} color="secondary" />
              <Typography variant="body2">Building {days}-day baseline profile from historical data…</Typography>
            </Stack>
          ) : (
            <>
              {/* Total Budget Bar */}
              <Box
                component="fieldset"
                sx={{
                  border: `1px solid ${PALETTE.borderSubtle}`,
                  borderRadius: 2,
                  p: 1.5,
                  m: 0,
                  backgroundColor: alpha(PALETTE.brandGold, 0.05),
                }}
              >
                <Typography component="legend" sx={{ px: 0.75, fontSize: "0.72rem", fontWeight: 800, textTransform: "uppercase", color: PALETTE.brandGoldDark }}>
                  3. Total media budget ({days} days · {mode === "pct" ? "%" : granularity === "daily" ? "€/day" : "period €"})
                </Typography>
                <Stack direction={{ xs: "column", sm: "row" }} justifyContent="space-between" alignItems={{ xs: "flex-start", sm: "center" }} spacing={1.5}>
                  <Box>
                    <Typography sx={{ fontWeight: 700, fontSize: "0.95rem", ...NUMERIC }}>
                      {eur(scenarioMediaTotal)}{" "}
                      <Typography component="span" variant="body2" sx={{ color: PALETTE.inkSecondary, ...NUMERIC }}>
                        ({eurCompact(scenarioMediaTotal / Math.max(1, days))}/day · {signedEur(deltaMedia)} · {signedPct(deltaPct, 1)} vs {eurCompact(baseMediaTotal)} run-rate)
                      </Typography>
                    </Typography>
                  </Box>
                  {mode === "pct" ? (
                    <TextField
                      name="builder-total-pct"
                      label="Total change (%)"
                      size="small"
                      defaultValue={Math.round(deltaPct * 10) / 10}
                      key={`tot-pct-${Math.round(deltaPct * 10)}`}
                      onBlur={(e) => {
                        const p = parsePctChange(e.target.value, -100, 300);
                        if (p !== null) {
                          setMultipliers(scaleMultipliersToTotal(adjustableColumns, template.baseBySlug, multipliers, baseMediaTotal * (1 + p / 100)));
                        }
                      }}
                      InputProps={{ endAdornment: <InputAdornment position="end">%</InputAdornment> }}
                      sx={{ width: 140, backgroundColor: "#FFFFFF" }}
                    />
                  ) : (
                    <TextField
                      name="builder-total-eur"
                      label={granularity === "daily" ? "Daily budget (€/day)" : `Total budget (${days}d €)`}
                      size="small"
                      defaultValue={Math.round(toDisplayEuro(scenarioMediaTotal, granularity, days)).toLocaleString("en-GB")}
                      key={`tot-eur-${granularity}-${Math.round(toDisplayEuro(scenarioMediaTotal, granularity, days))}`}
                      onBlur={(e) => {
                        const parsed = parseEuroAmount(e.target.value);
                        if (parsed !== null) {
                          const targetPeriodTotal = fromDisplayEuro(parsed, granularity, days);
                          setMultipliers(scaleMultipliersToTotal(adjustableColumns, template.baseBySlug, multipliers, targetPeriodTotal));
                        }
                      }}
                      InputProps={{
                        startAdornment: <InputAdornment position="start">€</InputAdornment>,
                        endAdornment: granularity === "daily" ? <InputAdornment position="end">/d</InputAdornment> : undefined,
                      }}
                      sx={{ width: 180, backgroundColor: "#FFFFFF" }}
                    />
                  )}
                </Stack>
              </Box>

              {/* Channel Budgets */}
              <Box component="fieldset" sx={{ border: `1px solid ${PALETTE.borderSubtle}`, borderRadius: 2, p: 1.5, m: 0 }}>
                <Typography component="legend" sx={{ px: 0.75, fontSize: "0.72rem", fontWeight: 800, textTransform: "uppercase", color: PALETTE.brandGoldDark }}>
                  4. Channel budgets ({mode === "pct" ? "Percentage %" : granularity === "daily" ? "Daily rate €/day" : `Period total € (${days}d)`})
                </Typography>
                <Stack spacing={1.5} divider={<Divider flexItem sx={{ borderColor: PALETTE.borderSubtle }} />}>
                  {adjustableColumns.map((slug) => {
                    const base = template.baseBySlug[slug] ?? 0;
                    const m = multipliers[slug] ?? 1;
                    const pct = (m - 1) * 100;
                    const scenEur = Math.round(base * m);
                    const label = channelNames[slug] ?? slug;
                    const active = m > 0;
                    const isCatalog = slug === "catalog";
                    const dropCount = isCatalog ? sendDates.length : days;
                    const perDropOrDayLabel = isCatalog
                      ? dropCount > 0
                        ? `${eurCompact(scenEur / dropCount)}/drop (${dropCount} send-out${dropCount === 1 ? "" : "s"})`
                        : "0 send-outs selected"
                      : `${eurCompact(scenEur / Math.max(1, days))}/day`;
                    const shownInputEur = Math.round(toDisplayEuro(scenEur, granularity, days));
                    const sliderMaxEur = Math.max(100, Math.round(toDisplayEuro(base * 2, granularity, days)));
                    const sliderStepEur = Math.max(10, Math.round(toDisplayEuro(base / 40, granularity, days)));
                    return (
                      <Box key={slug}>
                        <Stack direction="row" spacing={1.5} alignItems="center" justifyContent="space-between">
                          <Stack direction="row" spacing={0.5} alignItems="center" sx={{ minWidth: 210 }}>
                            <Checkbox
                              size="small"
                              checked={active}
                              onChange={(e) => setChannelMultiplier(slug, e.target.checked ? 1 : 0)}
                              inputProps={{ "aria-label": `Include ${label} in plan` }}
                              sx={{ p: 0.5, accentColor: PALETTE.brandPrimary }}
                            />
                            <Box>
                              <Stack direction="row" spacing={0.75} alignItems="center">
                                <Typography sx={{ fontWeight: 700, fontSize: "0.88rem" }}>{label}</Typography>
                                <Typography
                                  component="span"
                                  variant="caption"
                                  sx={{
                                    px: 0.6,
                                    py: 0.05,
                                    borderRadius: 1,
                                    backgroundColor: isCatalog ? PALETTE.bgGoldTint : PALETTE.bgSubtle,
                                    color: isCatalog ? PALETTE.brandGoldDark : PALETTE.inkSecondary,
                                    fontSize: "0.66rem",
                                    fontWeight: 700,
                                    ...NUMERIC,
                                  }}
                                >
                                  {isCatalog ? `Send-out (${sendDates.length}d)` : `Daily (${days}d)`}
                                </Typography>
                              </Stack>
                              <Typography variant="caption" sx={{ color: PALETTE.inkSecondary, ...NUMERIC, display: "block" }}>
                                {eurCompact(scenEur)} total · {perDropOrDayLabel} ({signedPct(pct, 0)} vs {eurCompact(base)})
                              </Typography>
                            </Box>
                          </Stack>

                          <Box sx={{ flexGrow: 1, px: 1, display: { xs: "none", sm: "block" } }}>
                            {mode === "pct" ? (
                              <Slider
                                size="small"
                                color="secondary"
                                min={-100}
                                max={100}
                                step={5}
                                value={Math.max(-100, Math.min(100, pct))}
                                onChange={(_, v) => setChannelMultiplier(slug, 1 + (v as number) / 100)}
                              />
                            ) : (
                              <Slider
                                size="small"
                                color="secondary"
                                min={0}
                                max={sliderMaxEur}
                                step={sliderStepEur}
                                value={Math.max(0, Math.min(sliderMaxEur, shownInputEur))}
                                onChange={(_, v) => {
                                  const targetPeriod = fromDisplayEuro(v as number, granularity, days);
                                  if (base > 0) setChannelMultiplier(slug, targetPeriod / base);
                                }}
                              />
                            )}
                          </Box>

                          {mode === "pct" ? (
                            <TextField
                              name={`builder-pct-${slug}`}
                              size="small"
                              defaultValue={Math.round(pct * 10) / 10}
                              key={`pct-${slug}-${Math.round(pct * 10)}`}
                              onBlur={(e) => {
                                const p = parsePctChange(e.target.value, -100, 300);
                                if (p !== null) setChannelMultiplier(slug, 1 + p / 100);
                              }}
                              inputProps={{ "aria-label": `${label} percentage change`, style: { textAlign: "right", ...NUMERIC, fontWeight: 650 } }}
                              InputProps={{ endAdornment: <InputAdornment position="end">%</InputAdornment> }}
                              sx={{ width: 105 }}
                            />
                          ) : (
                            <TextField
                              name={`builder-eur-${slug}`}
                              size="small"
                              defaultValue={shownInputEur.toLocaleString("en-GB")}
                              key={`eur-${slug}-${granularity}-${shownInputEur}`}
                              onBlur={(e) => {
                                const parsed = parseEuroAmount(e.target.value);
                                if (parsed !== null && base > 0) {
                                  const targetPeriod = fromDisplayEuro(parsed, granularity, days);
                                  setChannelMultiplier(slug, targetPeriod / base);
                                }
                              }}
                              inputProps={{
                                "aria-label": `${label} ${granularity === "daily" ? "daily euro budget" : "period euro budget"}`,
                                style: { textAlign: "right", ...NUMERIC, fontWeight: 650 },
                              }}
                              InputProps={{
                                startAdornment: <InputAdornment position="start">€</InputAdornment>,
                                endAdornment: granularity === "daily" ? <InputAdornment position="end">/d</InputAdornment> : undefined,
                              }}
                              sx={{ width: granularity === "daily" ? 148 : 135 }}
                            />
                          )}
                        </Stack>

                        {slug === "catalog" && thursdays.length > 0 && (
                          <Box sx={{ pl: 4, mt: 0.75 }}>
                            <Typography variant="caption" sx={{ color: PALETTE.inkSecondary, fontWeight: 600, display: "block", mb: 0.5 }}>
                              Catalog send-out Thursdays ({sendDates.length} selected):
                            </Typography>
                            <Stack direction="row" spacing={0.5} flexWrap="wrap" useFlexGap>
                              {thursdays.map((thu) => {
                                const on = sendDates.includes(thu);
                                return (
                                  <Chip
                                    key={thu}
                                    label={shortDate(thu)}
                                    size="small"
                                    color={on ? "secondary" : "default"}
                                    variant={on ? "filled" : "outlined"}
                                    onClick={() => handleToggleSendDate(thu)}
                                    sx={{ height: 22, fontSize: "0.72rem", ...NUMERIC }}
                                  />
                                );
                              })}
                            </Stack>
                          </Box>
                        )}
                      </Box>
                    );
                  })}

                  {lockedColumns.map((slug) => (
                    <Stack key={slug} direction="row" justifyContent="space-between" alignItems="center" sx={{ color: PALETTE.inkMuted, py: 0.5 }}>
                      <Stack direction="row" spacing={0.75} alignItems="center">
                        <LockOutlinedIcon sx={{ fontSize: 15 }} />
                        <Typography variant="caption" sx={{ fontWeight: 700 }}>
                          {channelNames[slug] ?? slug} (control variable — estimated from run-rate)
                        </Typography>
                      </Stack>
                      <Typography variant="caption" sx={{ fontWeight: 700, ...NUMERIC }}>
                        {eurCompact(template.baseBySlug[slug] ?? 0)}
                      </Typography>
                    </Stack>
                  ))}
                </Stack>
              </Box>
            </>
          )}
        </Stack>
      </DialogContent>

      <DialogActions sx={{ px: 3, py: 1.5, justifyContent: "space-between" }}>
        <Button
          size="small"
          variant="outlined"
          startIcon={<DownloadIcon fontSize="small" />}
          onClick={handleDownloadCsv}
          disabled={!template || submitting}
        >
          Download CSV
        </Button>
        <Stack direction="row" spacing={1}>
          <Button onClick={onClose} disabled={submitting}>
            Cancel
          </Button>
          <Button
            variant="contained"
            color="secondary"
            startIcon={submitting ? <CircularProgress size={14} color="inherit" /> : <PlayArrowIcon fontSize="small" />}
            onClick={() => void handleSubmitPlan()}
            disabled={!template || submitting}
          >
            {submitting ? "Submitting…" : "Run forecast plan"}
          </Button>
        </Stack>
      </DialogActions>
    </Dialog>
  );
};
