import React, { useEffect, useId, useMemo, useRef, useState } from "react";
import {
  Box,
  Button,
  Checkbox,
  Chip,
  Divider,
  IconButton,
  InputAdornment,
  LinearProgress,
  Paper,
  Radio,
  RadioGroup,
  Slider,
  Stack,
  TextField,
  Tooltip,
  Typography,
  alpha,
} from "@mui/material";
import RestartAltIcon from "@mui/icons-material/RestartAlt";
import LockOutlinedIcon from "@mui/icons-material/LockOutlined";
import BookmarkAddOutlinedIcon from "@mui/icons-material/BookmarkAddOutlined";
import BalanceIcon from "@mui/icons-material/Balance";
import TuneIcon from "@mui/icons-material/Tune";
import type { ScenarioChannelRow } from "../../api/types";
import { NUMERIC, PALETTE } from "../../theme";
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
  splitMultipliersEqually,
  toDisplayEuro,
} from "./format";

export interface Preset {
  id: string;
  label: string;
  multipliers: Record<string, number>;
}

/** Presets built from the plan's own levers; notebook 05's digital and Display → SEA scenarios among them. */
export function buildPresets(levers: ScenarioChannelRow[]): Preset[] {
  const active = levers.filter((l) => l.base_spend > 0);
  const all = (m: number) => Object.fromEntries(active.map((l) => [l.slug, m]));
  const presets: Preset[] = [
    { id: "all-down", label: "All media −10 %", multipliers: all(0.9) },
    { id: "all-up", label: "All media +10 %", multipliers: all(1.1) },
  ];
  const digital = active.filter((l) => l.slug !== "catalog");
  if (digital.length && digital.length < active.length) {
    presets.push({ id: "digital-up", label: "Digital +10 %", multipliers: Object.fromEntries(digital.map((l) => [l.slug, 1.1])) });
  }
  const sea = active.find((l) => l.slug === "sea");
  const display = active.find((l) => l.slug === "display");
  if (sea && display) {
    // Budget-neutral: 20 % of Display's planned spend moves to SEA
    presets.push({
      id: "display-to-sea",
      label: "20 % of Display → SEA",
      multipliers: { display: 0.8, sea: 1 + (0.2 * display.base_spend) / sea.base_spend },
    });
  }
  if (active.some((l) => l.slug === "catalog")) {
    presets.push({ id: "no-catalog", label: "Skip catalog send-outs", multipliers: { catalog: 0 } });
  }
  return presets;
}

const sameMultipliers = (a: Record<string, number>, b: Record<string, number>) => {
  const keys = new Set([...Object.keys(a), ...Object.keys(b)]);
  return [...keys].every((k) => Math.abs((a[k] ?? 1) - (b[k] ?? 1)) < 1e-6);
};

const FIELDSET_SX = {
  border: `1px solid ${PALETTE.borderSubtle}`,
  borderRadius: 2,
  p: 1.5,
  m: 0,
  minWidth: 0,
  backgroundColor: "#FFFFFF",
} as const;

const LEGEND_SX = {
  px: 0.75,
  fontSize: "0.72rem",
  fontWeight: 800,
  textTransform: "uppercase",
  letterSpacing: "0.06em",
  color: PALETTE.brandGoldDark,
} as const;

/** Percentage change input (−100 % to +300 %) that validates on blur and resets error state on input. */
const PctInput: React.FC<{
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
      helperText={hasError ? "−100 to +300%" : undefined}
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
        style: { textAlign: "right", ...NUMERIC, fontWeight: 650, padding: "5px 2px 5px 8px", fontSize: "0.85rem" },
      }}
      InputProps={{ endAdornment: <InputAdornment position="end">%</InputAdornment> }}
      sx={{ width: 96, flexShrink: 0, "& .MuiInputBase-root": { backgroundColor: "#FFFFFF" } }}
    />
  );
};

/** Fixed euro amount input (accepts "2555073", "2,555,073", "500k", "2.5M") that validates on blur and clears error on input. */
const EuroInput: React.FC<{
  value: number;
  name: string;
  label: string;
  suffix?: string;
  disabled?: boolean;
  width?: number;
  onCommit: (euros: number) => void;
}> = ({ value, name, label, suffix, disabled, width = 124, onCommit }) => {
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
      helperText={hasError ? "e.g. 500k or 2.5M" : undefined}
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
        style: { textAlign: "right", ...NUMERIC, fontWeight: 650, padding: "5px 6px 5px 2px", fontSize: "0.85rem" },
      }}
      InputProps={{
        startAdornment: <InputAdornment position="start">€</InputAdornment>,
        endAdornment: suffix ? <InputAdornment position="end">{suffix}</InputAdornment> : undefined,
      }}
      sx={{ width: suffix ? width + 18 : width, flexShrink: 0, "& .MuiInputBase-root": { backgroundColor: "#FFFFFF" } }}
    />
  );
};

/** Compact accessible radio option card for switching between input modes and spend granularities. */
const ModeRadioOption: React.FC<{
  value: string;
  selected: boolean;
  title: string;
  badge: string;
  subtitle: string;
}> = ({ value, selected, title, badge, subtitle }) => {
  const id = useId();
  return (
    <Box
      component="label"
      htmlFor={`${id}-radio`}
      sx={{
        display: "flex",
        alignItems: "flex-start",
        gap: 0.75,
        p: 1,
        borderRadius: 1.5,
        cursor: "pointer",
        border: `${selected ? 2 : 1}px solid ${selected ? PALETTE.brandPrimary : PALETTE.borderSubtle}`,
        m: selected ? 0 : "1px",
        backgroundColor: selected ? alpha(PALETTE.brandPrimary, 0.06) : "#FFFFFF",
        transition: "border-color 0.12s, background-color 0.12s",
        "&:hover": selected ? undefined : { borderColor: PALETTE.borderStrong },
        "&:has(input[type=radio]:focus-visible)": { outline: `3px solid ${PALETTE.brandPrimary}`, outlineOffset: 2 },
        "@media (prefers-reduced-motion: reduce)": { transition: "none" },
      }}
    >
      <Radio
        value={value}
        size="small"
        sx={{ p: 0.25, mt: 0.1, accentColor: PALETTE.brandPrimary }}
        inputProps={{
          id: `${id}-radio`,
          "aria-labelledby": `${id}-title`,
          "aria-describedby": `${id}-sub`,
        }}
      />
      <Box sx={{ minWidth: 0, flexGrow: 1 }}>
        <Stack direction="row" spacing={0.5} alignItems="center" justifyContent="space-between">
          <Typography id={`${id}-title`} component="span" sx={{ fontWeight: 700, fontSize: "0.84rem", color: PALETTE.inkPrimary, lineHeight: 1.25 }}>
            {title}
          </Typography>
          <Box
            component="span"
            sx={{
              px: 0.6,
              py: 0.1,
              borderRadius: 1,
              fontSize: "0.7rem",
              fontWeight: 800,
              color: selected ? PALETTE.brandPrimary : PALETTE.brandGoldDark,
              backgroundColor: selected ? alpha(PALETTE.brandPrimary, 0.12) : PALETTE.bgGoldTint,
              ...NUMERIC,
            }}
          >
            {badge}
          </Box>
        </Stack>
        <Typography id={`${id}-sub`} variant="caption" component="span" sx={{ display: "block", color: PALETTE.inkSecondary, fontSize: "0.72rem", lineHeight: 1.3, mt: 0.25 }}>
          {subtitle}
        </Typography>
      </Box>
    </Box>
  );
};

interface LeverPanelProps {
  levers: ScenarioChannelRow[];
  locked: ScenarioChannelRow[];
  multipliers: Record<string, number>;
  onChange: (next: Record<string, number>) => void;
  /** Each channel's own effect from the scenario on screen, keyed by slug */
  effects: Record<string, ScenarioChannelRow> | null;
  effectsStale: boolean;
  evaluating: boolean;
  horizonDays: number;
  horizonMax: number;
  onHorizonChange: (days: number) => void;
  startDate?: string | null;
  planDaily?: Record<string, number[]>;
  inputMode?: BudgetInputMode;
  onInputModeChange?: (mode: BudgetInputMode) => void;
  spendGranularity?: SpendGranularity;
  onSpendGranularityChange?: (granularity: SpendGranularity) => void;
  onOpenPlanBuilder?: () => void;
  onSave: () => void;
  canSave: boolean;
  saveHint?: string;
}

const MODE_STORAGE_KEY = "mmm-studio:forecast-input-mode";
const GRANULARITY_STORAGE_KEY = "mmm-studio:forecast-spend-granularity";

export const LeverPanel: React.FC<LeverPanelProps> = ({
  levers,
  locked,
  multipliers,
  onChange,
  effects,
  effectsStale,
  evaluating,
  horizonDays,
  horizonMax,
  onHorizonChange,
  startDate,
  planDaily,
  inputMode: controlledMode,
  onInputModeChange,
  spendGranularity: controlledGranularity,
  onSpendGranularityChange,
  onOpenPlanBuilder,
  onSave,
  canSave,
  saveHint,
}) => {
  const [internalMode, setInternalMode] = useState<BudgetInputMode>(() => {
    try {
      const saved = window.localStorage.getItem(MODE_STORAGE_KEY);
      return saved === "eur" || saved === "pct" ? saved : "pct";
    } catch {
      return "pct";
    }
  });

  const [internalGranularity, setInternalGranularity] = useState<SpendGranularity>(() => {
    try {
      const saved = window.localStorage.getItem(GRANULARITY_STORAGE_KEY);
      return saved === "daily" || saved === "period" ? saved : "period";
    } catch {
      return "period";
    }
  });

  const mode = controlledMode ?? internalMode;
  const granularity = controlledGranularity ?? internalGranularity;

  const handleModeChange = (next: BudgetInputMode) => {
    setInternalMode(next);
    onInputModeChange?.(next);
    try {
      window.localStorage.setItem(MODE_STORAGE_KEY, next);
    } catch {
      // Storage unavailable
    }
  };

  const handleGranularityChange = (next: SpendGranularity) => {
    setInternalGranularity(next);
    onSpendGranularityChange?.(next);
    try {
      window.localStorage.setItem(GRANULARITY_STORAGE_KEY, next);
    } catch {
      // Storage unavailable
    }
  };

  // Remember each channel's last non-zero multiplier so toggling a channel off and back on restores it
  const lastActiveMultipliers = useRef<Record<string, number>>({});
  useEffect(() => {
    for (const [slug, m] of Object.entries(multipliers)) {
      if (m > 0) lastActiveMultipliers.current[slug] = m;
    }
  }, [multipliers]);

  const effectiveHorizon = Math.max(1, Math.min(horizonDays, horizonMax));
  const [daysDraft, setDaysDraft] = useState(String(effectiveHorizon));
  useEffect(() => setDaysDraft(String(effectiveHorizon)), [effectiveHorizon]);

  const periodEndDate = useMemo(() => (startDate ? addDays(startDate, effectiveHorizon - 1) : null), [startDate, effectiveHorizon]);

  // Exact base spend for each channel over the selected forecast period (`effectiveHorizon` days)
  const periodBaseBySlug = useMemo(() => {
    const map: Record<string, number> = {};
    for (const ch of [...levers, ...locked]) {
      const daily = planDaily?.[ch.slug];
      const periodSum = channelPeriodBaseSpend(ch.base_spend, daily, effectiveHorizon, horizonMax);
      // If a lumpy channel (e.g. Catalog) has 0 spend in the sub-window but >0 in the plan, keep periodSum for display
      // and fallback to ch.base_spend only when computing multipliers if needed
      map[ch.slug] = periodSum;
    }
    return map;
  }, [levers, locked, planDaily, effectiveHorizon, horizonMax]);

  const leverSlugs = useMemo(() => levers.map((l) => l.slug), [levers]);

  const baseMediaSpend = useMemo(
    () => levers.reduce((acc, ch) => acc + (periodBaseBySlug[ch.slug] ?? 0), 0),
    [levers, periodBaseBySlug]
  );

  const scenarioMediaSpend = useMemo(
    () => levers.reduce((acc, ch) => acc + (periodBaseBySlug[ch.slug] ?? 0) * (multipliers[ch.slug] ?? 1), 0),
    [levers, periodBaseBySlug, multipliers]
  );

  const lockedPeriodSpend = useMemo(
    () => locked.reduce((acc, ch) => acc + (periodBaseBySlug[ch.slug] ?? 0), 0),
    [locked, periodBaseBySlug]
  );

  const totalDeltaSpend = scenarioMediaSpend - baseMediaSpend;
  const totalDeltaPct = baseMediaSpend > 0 ? (totalDeltaSpend / baseMediaSpend) * 100 : 0;

  const spendableLevers = useMemo(() => levers.filter((ch) => ch.base_spend > 0), [levers]);
  const activeLeversCount = useMemo(
    () => spendableLevers.filter((ch) => (multipliers[ch.slug] ?? 1) > 0).length,
    [spendableLevers, multipliers]
  );

  const presets = useMemo(() => buildPresets(levers), [levers]);
  const changed = Object.values(multipliers).some((v) => Math.abs(v - 1) > 1e-9);

  const setOne = (slug: string, m: number) => {
    const clamped = Math.max(0, Math.min(4, Math.round(m * 1000) / 1000));
    onChange({ ...multipliers, [slug]: clamped });
  };

  const handleToggleChannel = (slug: string, checked: boolean) => {
    if (!checked) {
      const current = multipliers[slug] ?? 1;
      if (current > 0) lastActiveMultipliers.current[slug] = current;
      setOne(slug, 0);
    } else {
      const restored = lastActiveMultipliers.current[slug] ?? 1;
      setOne(slug, restored > 0 ? restored : 1);
    }
  };

  const handleTotalPctChange = (pct: number) => {
    const target = Math.max(0, baseMediaSpend * (1 + pct / 100));
    onChange(scaleMultipliersToTotal(leverSlugs, periodBaseBySlug, multipliers, target));
  };

  const handleTotalEuroChange = (euros: number) => {
    onChange(scaleMultipliersToTotal(leverSlugs, periodBaseBySlug, multipliers, euros));
  };

  const handleProportionalAllocation = () => {
    // Set all active channels to the same multiplier so total scenario spend stays at `scenarioMediaSpend`
    const activeSlugs = spendableLevers.filter((ch) => (multipliers[ch.slug] ?? 1) > 0).map((ch) => ch.slug);
    const activeBase = activeSlugs.reduce((acc, s) => acc + (periodBaseBySlug[s] ?? 0), 0);
    if (activeBase <= 0) return;
    const uniformMultiplier = Math.round((scenarioMediaSpend / activeBase) * 1000) / 1000;
    const next: Record<string, number> = { ...multipliers };
    for (const s of activeSlugs) next[s] = uniformMultiplier;
    onChange(next);
  };

  const handleEqualAllocation = () => {
    onChange(splitMultipliersEqually(leverSlugs, periodBaseBySlug, multipliers, scenarioMediaSpend));
  };

  const periodPresets = useMemo(() => {
    const candidates = [
      { days: 7, label: "1w (7d)" },
      { days: 14, label: "2w (14d)" },
      { days: 28, label: "4w (28d)" },
      { days: 42, label: "6w (42d)" },
      { days: 56, label: "8w (56d)" },
      { days: 90, label: "90d" },
    ].filter((p) => p.days < horizonMax);
    candidates.push({
      days: horizonMax,
      label: horizonMax === 42 ? "Full (42d)" : `Full (${horizonMax}d)`,
    });
    return candidates;
  }, [horizonMax]);

  const commitDaysDraft = () => {
    const n = Number(daysDraft.trim());
    if (Number.isFinite(n) && n >= 1) {
      const clamped = Math.max(1, Math.min(horizonMax, Math.round(n)));
      onHorizonChange(clamped);
      setDaysDraft(String(clamped));
    } else {
      setDaysDraft(String(effectiveHorizon));
    }
  };

  return (
    <Box component="section" aria-labelledby="levers-title">
      <Stack direction="row" justifyContent="space-between" alignItems="center" spacing={1}>
        <Typography id="levers-title" variant="h3" component="h2">
          Forecast setup
        </Typography>
        <Button size="small" startIcon={<RestartAltIcon fontSize="small" />} onClick={() => onChange({})} disabled={!changed}>
          Back to plan
        </Button>
      </Stack>
      <Typography variant="body2" sx={{ mt: 0.5, color: PALETTE.inkSecondary }}>
        Set the forecast period, choose percentage or fixed euro inputs, and adjust total or per-channel budgets.
      </Typography>

      {/* --- Budget Input Mode & Spend Granularity --- */}
      <Box component="fieldset" aria-describedby="spend-granularity-note" sx={{ ...FIELDSET_SX, mt: 2, backgroundColor: PALETTE.bgCanvas }}>
        <Typography component="legend" sx={LEGEND_SX}>
          Input mode & spend granularity
        </Typography>
        <RadioGroup
          name="budget-input-mode"
          value={mode}
          onChange={(e) => handleModeChange(e.target.value as BudgetInputMode)}
          sx={{ display: "grid", gap: 1, gridTemplateColumns: "repeat(2, minmax(0, 1fr))" }}
        >
          <ModeRadioOption
            value="pct"
            selected={mode === "pct"}
            title="Percentage"
            badge="%"
            subtitle="Change vs planned spend"
          />
          <ModeRadioOption
            value="eur"
            selected={mode === "eur"}
            title="Fixed amount"
            badge="€"
            subtitle="Exact € budget input"
          />
        </RadioGroup>

        {mode === "eur" && (
          <Box sx={{ mt: 1.25, pt: 1.25, borderTop: `1px dashed ${PALETTE.borderSubtle}` }}>
            <Typography
              id="spend-granularity-group-label"
              variant="caption"
              component="p"
              sx={{ mb: 0.75, fontWeight: 700, color: PALETTE.inkPrimary, display: "flex", justifyContent: "space-between", alignItems: "center" }}
            >
              <span>Euro input granularity</span>
              <Box component="span" sx={{ color: PALETTE.inkSecondary, fontWeight: 600, ...NUMERIC }}>
                {granularity === "daily" ? `€/day × ${effectiveHorizon}d` : `${effectiveHorizon}-day total`}
              </Box>
            </Typography>
            <RadioGroup
              aria-labelledby="spend-granularity-group-label"
              name="spend-granularity"
              value={granularity}
              onChange={(e) => handleGranularityChange(e.target.value as SpendGranularity)}
              sx={{ display: "grid", gap: 1, gridTemplateColumns: "repeat(2, minmax(0, 1fr))" }}
            >
              <ModeRadioOption
                value="period"
                selected={granularity === "period"}
                title="Period total"
                badge="€"
                subtitle={`Full ${effectiveHorizon}-day sum`}
              />
              <ModeRadioOption
                value="daily"
                selected={granularity === "daily"}
                title="Daily rate"
                badge="€/d"
                subtitle={`Per day (× ${effectiveHorizon}d)`}
              />
            </RadioGroup>
          </Box>
        )}

        <Typography
          id="spend-granularity-note"
          variant="caption"
          component="p"
          sx={{ mt: 1, color: PALETTE.inkSecondary, lineHeight: 1.35, fontSize: "0.72rem" }}
        >
          Model granularity: <strong>Daily × Channel (National, EUR)</strong>. Digital channels scale their daily weekday profile; Catalog scales send-out days.
        </Typography>
      </Box>

      {/* --- 1. Forecast Period Setup --- */}
      <Box component="fieldset" sx={{ ...FIELDSET_SX, mt: 2 }}>
        <Typography component="legend" sx={LEGEND_SX}>
          1. Forecast period
        </Typography>
        <Stack direction="row" justifyContent="space-between" alignItems="center" spacing={1}>
          <Box sx={{ minWidth: 0 }}>
            <Typography sx={{ fontWeight: 700, fontSize: "0.88rem", color: PALETTE.inkPrimary }}>
              Horizon window
            </Typography>
            <Typography variant="caption" sx={{ color: PALETTE.inkSecondary, ...NUMERIC, display: "block" }}>
              {startDate && periodEndDate
                ? `${shortDate(startDate)} – ${shortDate(periodEndDate)}`
                : `First ${effectiveHorizon} of ${horizonMax} planned days`}
            </Typography>
          </Box>
          <TextField
            name="forecast-horizon-days"
            size="small"
            value={daysDraft}
            onChange={(e) => setDaysDraft(e.target.value)}
            onBlur={commitDaysDraft}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                commitDaysDraft();
              }
            }}
            inputProps={{
              inputMode: "numeric",
              min: 1,
              max: horizonMax,
              "aria-label": "Forecast period in days",
              style: { textAlign: "right", ...NUMERIC, fontWeight: 700, padding: "5px 4px 5px 8px", fontSize: "0.85rem" },
            }}
            InputProps={{ endAdornment: <InputAdornment position="end">days</InputAdornment> }}
            sx={{ width: 98, flexShrink: 0 }}
          />
        </Stack>

        <Slider
          size="small"
          color="secondary"
          min={1}
          max={horizonMax}
          step={1}
          value={effectiveHorizon}
          valueLabelDisplay="auto"
          valueLabelFormat={(v) => `${v}d`}
          getAriaValueText={(v) => `${v} forecast days`}
          aria-label="Forecast period in days"
          onChange={(_, v) => onHorizonChange(v as number)}
          sx={{ mt: 0.5, py: 1, accentColor: PALETTE.brandPrimary }}
        />

        <Stack direction="row" flexWrap="wrap" useFlexGap spacing={0.5} sx={{ mt: 0.25 }}>
          {periodPresets.map((p) => {
            const active = effectiveHorizon === p.days;
            return (
              <Chip
                key={p.days}
                label={p.label}
                size="small"
                variant={active ? "filled" : "outlined"}
                color={active ? "primary" : "default"}
                aria-pressed={active}
                onClick={() => onHorizonChange(p.days)}
                sx={{ height: 22, fontSize: "0.73rem", fontWeight: active ? 700 : 500, ...NUMERIC }}
              />
            );
          })}
          {onOpenPlanBuilder && (
            <Chip
              icon={<TuneIcon sx={{ fontSize: "14px !important" }} />}
              label="Custom plan (up to 90d)…"
              size="small"
              variant="outlined"
              color="secondary"
              onClick={onOpenPlanBuilder}
              sx={{ height: 22, fontSize: "0.73rem", fontWeight: 600 }}
            />
          )}
        </Stack>
      </Box>

      {/* --- 2. Total Media Budget Setup --- */}
      <Box component="fieldset" sx={{ ...FIELDSET_SX, mt: 2, backgroundColor: alpha(PALETTE.brandGold, 0.04) }}>
        <Typography component="legend" sx={LEGEND_SX}>
          2. Total media budget ({effectiveHorizon}d · {mode === "pct" ? "%" : granularity === "daily" ? "€/day" : "period €"})
        </Typography>
        <Stack direction="row" justifyContent="space-between" alignItems="flex-start" spacing={1}>
          <Box sx={{ minWidth: 0 }}>
            <Typography id="total-budget-label" sx={{ fontWeight: 700, fontSize: "0.88rem", color: PALETTE.inkPrimary }}>
              {mode === "pct"
                ? "Budget change vs plan"
                : granularity === "daily"
                  ? "Daily media budget"
                  : `Period media budget (${effectiveHorizon}d)`}
            </Typography>
            <Typography variant="caption" sx={{ color: PALETTE.inkSecondary, ...NUMERIC, display: "block" }}>
              {mode === "pct"
                ? `${eurCompact(baseMediaSpend)} → ${eurCompact(scenarioMediaSpend)} (${eurCompact(scenarioMediaSpend / effectiveHorizon)}/d · ${signedEur(totalDeltaSpend)})`
                : granularity === "daily"
                  ? `Total ${eurCompact(scenarioMediaSpend)} over ${effectiveHorizon}d · Plan ${eurCompact(baseMediaSpend / effectiveHorizon)}/d (${signedPct(totalDeltaPct, 1)})`
                  : `${eurCompact(scenarioMediaSpend / effectiveHorizon)}/d · Plan ${eurCompact(baseMediaSpend)} (${signedPct(totalDeltaPct, 1)})`}
            </Typography>
          </Box>
          {mode === "pct" ? (
            <PctInput
              value={totalDeltaPct}
              name="total-budget-pct"
              label="Total media budget change in percent"
              disabled={baseMediaSpend <= 0}
              onCommit={handleTotalPctChange}
            />
          ) : (
            <EuroInput
              value={toDisplayEuro(scenarioMediaSpend, granularity, effectiveHorizon)}
              name="total-budget-eur"
              label={granularity === "daily" ? "Daily media budget in euros per day" : "Total media budget in euros"}
              suffix={granularity === "daily" ? "/d" : undefined}
              disabled={baseMediaSpend <= 0}
              onCommit={(entered) => handleTotalEuroChange(fromDisplayEuro(entered, granularity, effectiveHorizon))}
            />
          )}
        </Stack>

        {mode === "pct" ? (
          <Slider
            size="small"
            color="secondary"
            min={-100}
            max={100}
            step={5}
            value={Math.max(-100, Math.min(100, Math.round(totalDeltaPct)))}
            disabled={baseMediaSpend <= 0}
            marks={[{ value: 0 }]}
            valueLabelDisplay="auto"
            valueLabelFormat={(v) => signedPct(v, 0)}
            getAriaValueText={(v) => `${signedPct(v, 0)} total media budget`}
            aria-labelledby="total-budget-label"
            onChange={(_, v) => handleTotalPctChange(v as number)}
            sx={{ mt: 0.25, py: 1 }}
          />
        ) : (
          <Slider
            size="small"
            color="secondary"
            min={0}
            max={Math.max(100, Math.round(toDisplayEuro(baseMediaSpend * 2, granularity, effectiveHorizon)))}
            step={Math.max(10, Math.round(toDisplayEuro(baseMediaSpend / 40, granularity, effectiveHorizon)))}
            value={Math.max(
              0,
              Math.min(
                Math.round(toDisplayEuro(baseMediaSpend * 2, granularity, effectiveHorizon)),
                Math.round(toDisplayEuro(scenarioMediaSpend, granularity, effectiveHorizon))
              )
            )}
            disabled={baseMediaSpend <= 0}
            marks={baseMediaSpend > 0 ? [{ value: Math.round(toDisplayEuro(baseMediaSpend, granularity, effectiveHorizon)) }] : undefined}
            valueLabelDisplay="auto"
            valueLabelFormat={(v) => `${eurCompact(v as number)}${granularity === "daily" ? "/d" : ""}`}
            getAriaValueText={(v) => `${eur(v as number)}${granularity === "daily" ? " per day" : ""} total media budget`}
            aria-labelledby="total-budget-label"
            onChange={(_, v) => handleTotalEuroChange(fromDisplayEuro(v as number, granularity, effectiveHorizon))}
            sx={{ mt: 0.25, py: 1 }}
          />
        )}

        <Stack direction="row" justifyContent="space-between" alignItems="center" flexWrap="wrap" useFlexGap spacing={0.75} sx={{ mt: 0.25 }}>
          <Typography variant="caption" sx={{ color: PALETTE.inkSecondary, ...NUMERIC }}>
            {activeLeversCount}/{spendableLevers.length} channels active
            {lockedPeriodSpend > 0 ? ` · +${eurCompact(lockedPeriodSpend)} control` : ""}
          </Typography>
          <Stack direction="row" spacing={0.5}>
            <Tooltip title="Distribute current total budget across active channels in proportion to their baseline plan">
              <span>
                <Button
                  size="small"
                  variant="text"
                  onClick={handleProportionalAllocation}
                  disabled={scenarioMediaSpend <= 0 || activeLeversCount <= 1}
                  sx={{ fontSize: "0.72rem", py: 0.2, px: 0.75, minWidth: 0 }}
                >
                  Plan mix
                </Button>
              </span>
            </Tooltip>
            <Tooltip title="Split current total media budget equally across all active channels">
              <span>
                <Button
                  size="small"
                  variant="text"
                  startIcon={<BalanceIcon sx={{ fontSize: "13px !important" }} />}
                  onClick={handleEqualAllocation}
                  disabled={scenarioMediaSpend <= 0 || activeLeversCount <= 1}
                  sx={{ fontSize: "0.72rem", py: 0.2, px: 0.75, minWidth: 0 }}
                >
                  Equal split
                </Button>
              </span>
            </Tooltip>
          </Stack>
        </Stack>
      </Box>

      {/* --- Quick Scenarios --- */}
      <Box sx={{ mt: 2 }}>
        <Typography variant="caption" component="p" sx={{ mb: 0.75, fontWeight: 700, color: PALETTE.inkSecondary }}>
          Quick scenarios
        </Typography>
        <Stack direction="row" flexWrap="wrap" useFlexGap spacing={0.75}>
          {presets.map((p) => {
            const on = sameMultipliers(p.multipliers, multipliers);
            return (
              <Chip
                key={p.id}
                label={p.label}
                size="small"
                variant={on ? "filled" : "outlined"}
                color={on ? "secondary" : "default"}
                aria-pressed={on}
                onClick={() => onChange(on ? {} : p.multipliers)}
              />
            );
          })}
        </Stack>
      </Box>

      {/* --- 3. Channels Setup --- */}
      <Box component="fieldset" sx={{ ...FIELDSET_SX, mt: 2 }}>
        <Typography component="legend" sx={LEGEND_SX}>
          3. Channels ({mode === "pct" ? "% change" : granularity === "daily" ? "Fixed €/day" : `Fixed € · ${effectiveHorizon}d`})
        </Typography>
        <Stack spacing={1.75} divider={<Divider flexItem sx={{ borderColor: PALETTE.borderSubtle }} />}>
          {levers.map((ch) => {
            const m = multipliers[ch.slug] ?? 1;
            const pct = (m - 1) * 100;
            const periodBase = periodBaseBySlug[ch.slug] ?? ch.base_spend;
            // Fallback base when a lumpy channel (Catalog) has €0 in the sub-horizon but >0 in the full plan
            const referenceBase = periodBase > 0 ? periodBase : ch.base_spend;
            const noSpend = ch.base_spend <= 0;
            const isPaused = !noSpend && m <= 1e-9;
            const eff = effects?.[ch.slug];
            const moved = Math.abs(m - 1) > 1e-9;
            const scenarioEur = Math.round(referenceBase * m);
            const deltaEur = scenarioEur - referenceBase;
            const sharePct = scenarioMediaSpend > 0 && periodBase > 0 ? Math.round(((periodBase * m) / scenarioMediaSpend) * 100) : 0;
            const activeDays = channelActiveDays(planDaily?.[ch.slug], effectiveHorizon);
            const isEpisodic = ch.slug === "catalog" || (activeDays > 0 && activeDays < effectiveHorizon);
            const dailyAvgEur = scenarioEur / effectiveHorizon;
            const perDropEur = activeDays > 0 ? scenarioEur / activeDays : scenarioEur;
            const displayValueEur = toDisplayEuro(scenarioEur, granularity, effectiveHorizon);
            const displayMaxEur = Math.max(100, Math.round(toDisplayEuro(referenceBase * 2, granularity, effectiveHorizon)));
            const displayStepEur = Math.max(10, Math.round(toDisplayEuro(referenceBase / 40, granularity, effectiveHorizon)));
            const displayMarkEur = Math.round(toDisplayEuro(referenceBase, granularity, effectiveHorizon));

            return (
              <Box key={ch.slug} role="group" aria-labelledby={`lever-${ch.slug}`} sx={{ opacity: isPaused ? 0.72 : 1 }}>
                <Stack direction="row" justifyContent="space-between" alignItems="flex-start" spacing={1}>
                  <Stack direction="row" spacing={0.5} alignItems="flex-start" sx={{ minWidth: 0, flexGrow: 1 }}>
                    <Checkbox
                      size="small"
                      name={`channel-active-${ch.slug}`}
                      checked={!noSpend && !isPaused}
                      disabled={noSpend}
                      onChange={(e) => handleToggleChannel(ch.slug, e.target.checked)}
                      inputProps={{ "aria-label": `Include ${ch.channel} in forecast` }}
                      sx={{ p: 0.25, mt: 0.1, accentColor: PALETTE.brandPrimary }}
                    />
                    <Box sx={{ minWidth: 0, flexGrow: 1 }}>
                      <Stack direction="row" spacing={0.6} alignItems="center" flexWrap="wrap">
                        <Typography id={`lever-${ch.slug}`} sx={{ fontWeight: 700, fontSize: "0.88rem", lineHeight: 1.3 }}>
                          {ch.channel}
                        </Typography>
                        {!noSpend && (
                          <Tooltip
                            title={
                              isEpisodic
                                ? `Episodic send-out channel: spend is booked on ${activeDays} send-out day${activeDays === 1 ? "" : "s"} in this ${effectiveHorizon}-day window (€0 on other days).`
                                : `Continuous daily channel: spend is distributed across all ${effectiveHorizon} days following its weekday profile.`
                            }
                          >
                            <Typography
                              component="span"
                              variant="caption"
                              sx={{
                                px: 0.6,
                                py: 0.05,
                                borderRadius: 1,
                                backgroundColor: isEpisodic ? PALETTE.bgGoldTint : PALETTE.bgSubtle,
                                color: isEpisodic ? PALETTE.brandGoldDark : PALETTE.inkSecondary,
                                fontSize: "0.66rem",
                                fontWeight: 700,
                                ...NUMERIC,
                              }}
                            >
                              {isEpisodic ? `Send-out (${activeDays}d)` : "Daily"}
                            </Typography>
                          </Tooltip>
                        )}
                        {!noSpend && !isPaused && periodBase > 0 && (
                          <Typography
                            component="span"
                            variant="caption"
                            sx={{
                              px: 0.6,
                              py: 0.05,
                              borderRadius: 1,
                              backgroundColor: PALETTE.bgSubtle,
                              color: PALETTE.inkSecondary,
                              fontSize: "0.68rem",
                              fontWeight: 700,
                              ...NUMERIC,
                            }}
                          >
                            {sharePct}% share
                          </Typography>
                        )}
                        {moved && (
                          <Tooltip title={`Reset ${ch.channel} to planned spend`}>
                            <IconButton
                              size="small"
                              aria-label={`Reset ${ch.channel} to planned spend`}
                              onClick={() => setOne(ch.slug, 1)}
                              sx={{ p: 0.2 }}
                            >
                              <RestartAltIcon sx={{ fontSize: 14 }} />
                            </IconButton>
                          </Tooltip>
                        )}
                      </Stack>
                      <Typography variant="caption" sx={{ color: PALETTE.inkSecondary, ...NUMERIC, display: "block", mt: 0.15 }}>
                        {noSpend
                          ? "No spend in this plan"
                          : periodBase === 0 && ch.base_spend > 0
                            ? `No send-outs in first ${effectiveHorizon}d (${eurCompact(ch.base_spend)} over ${horizonMax}d)`
                            : isPaused
                              ? `Paused (€0 · planned ${eurCompact(referenceBase)})`
                              : mode === "pct"
                                ? moved
                                  ? `${eurCompact(referenceBase)} → ${eurCompact(scenarioEur)} (${isEpisodic && activeDays > 0 ? `${eurCompact(perDropEur)}/drop` : `${eurCompact(dailyAvgEur)}/d`} · ${signedEur(deltaEur)})`
                                  : `${eurCompact(referenceBase)} planned (${isEpisodic && activeDays > 0 ? `${eurCompact(perDropEur)}/drop` : `${eurCompact(dailyAvgEur)}/d`})`
                                : granularity === "daily"
                                  ? `Total ${eurCompact(scenarioEur)} (${isEpisodic && activeDays > 0 ? `${eurCompact(perDropEur)}/drop` : `Plan ${eurCompact(referenceBase / effectiveHorizon)}/d`} · ${signedPct(pct, 1)})`
                                  : `${isEpisodic && activeDays > 0 ? `${eurCompact(perDropEur)}/drop (${eurCompact(dailyAvgEur)}/d avg)` : `${eurCompact(dailyAvgEur)}/d`} · Plan ${eurCompact(referenceBase)} (${signedPct(pct, 1)})`}
                      </Typography>
                    </Box>
                  </Stack>

                  {mode === "pct" ? (
                    <PctInput
                      value={pct}
                      name={`channel-pct-${ch.slug}`}
                      label={`${ch.channel} budget change in percent`}
                      disabled={noSpend}
                      onCommit={(v) => setOne(ch.slug, 1 + v / 100)}
                    />
                  ) : (
                    <EuroInput
                      value={displayValueEur}
                      name={`channel-eur-${ch.slug}`}
                      label={`${ch.channel} ${granularity === "daily" ? "daily budget in euros per day" : "period budget in euros"}`}
                      suffix={granularity === "daily" ? "/d" : undefined}
                      disabled={noSpend}
                      width={112}
                      onCommit={(entered) => {
                        const periodTarget = fromDisplayEuro(entered, granularity, effectiveHorizon);
                        if (referenceBase > 0) setOne(ch.slug, periodTarget / referenceBase);
                      }}
                    />
                  )}
                </Stack>

                {mode === "pct" ? (
                  <Slider
                    size="small"
                    color="secondary"
                    min={-100}
                    max={100}
                    step={5}
                    value={Math.max(-100, Math.min(100, pct))}
                    disabled={noSpend}
                    marks={[{ value: 0 }]}
                    valueLabelDisplay="auto"
                    valueLabelFormat={(v) => signedPct(v, 0)}
                    getAriaValueText={(v) => `${signedPct(v, 0)} on the planned budget`}
                    aria-labelledby={`lever-${ch.slug}`}
                    onChange={(_, v) => setOne(ch.slug, 1 + (v as number) / 100)}
                    sx={{ mt: 0.25, py: 1 }}
                  />
                ) : (
                  <Slider
                    size="small"
                    color="secondary"
                    min={0}
                    max={displayMaxEur}
                    step={displayStepEur}
                    value={Math.max(0, Math.min(displayMaxEur, Math.round(displayValueEur)))}
                    disabled={noSpend}
                    marks={referenceBase > 0 ? [{ value: displayMarkEur }] : undefined}
                    valueLabelDisplay="auto"
                    valueLabelFormat={(v) => `${eurCompact(v as number)}${granularity === "daily" ? "/d" : ""}`}
                    getAriaValueText={(v) => `${eur(v as number)}${granularity === "daily" ? " per day" : ""} for ${ch.channel}`}
                    aria-labelledby={`lever-${ch.slug}`}
                    onChange={(_, v) => {
                      const periodTarget = fromDisplayEuro(v as number, granularity, effectiveHorizon);
                      if (referenceBase > 0) setOne(ch.slug, periodTarget / referenceBase);
                    }}
                    sx={{ mt: 0.25, py: 1 }}
                  />
                )}

                {moved && eff && eff.delta_sales != null && (
                  <Typography
                    variant="caption"
                    component="p"
                    sx={{ mt: -0.25, color: PALETTE.inkSecondary, opacity: effectsStale ? 0.5 : 1, transition: "opacity 0.2s" }}
                  >
                    Sales effect{" "}
                    <Box component="span" sx={{ ...NUMERIC, fontWeight: 700, color: PALETTE.inkPrimary }}>
                      {eurCompact(eff.delta_sales, true)}
                    </Box>
                    {eff.delta_sales_lo != null && eff.delta_sales_hi != null && (
                      <>
                        {" "}
                        <Box component="span" sx={{ whiteSpace: "nowrap" }}>
                          (90 %: {eurCompact(eff.delta_sales_lo, true)} to {eurCompact(eff.delta_sales_hi, true)})
                        </Box>
                      </>
                    )}
                  </Typography>
                )}
              </Box>
            );
          })}

          {locked.map((ch) => {
            const lockedPeriodBase = periodBaseBySlug[ch.slug] ?? ch.base_spend;
            return (
              <Box key={ch.slug}>
                <Stack direction="row" justifyContent="space-between" alignItems="center" spacing={1}>
                  <Stack direction="row" spacing={0.75} alignItems="center" sx={{ color: PALETTE.inkMuted }}>
                    <LockOutlinedIcon sx={{ fontSize: 15 }} aria-hidden="true" />
                    <Typography variant="caption" sx={{ fontWeight: 700 }}>
                      {ch.channel} (control)
                    </Typography>
                  </Stack>
                  <Typography variant="caption" sx={{ color: PALETTE.inkMuted, fontWeight: 700, ...NUMERIC }}>
                    {eurCompact(lockedPeriodBase)} planned
                  </Typography>
                </Stack>
                <Typography variant="caption" component="p" sx={{ color: PALETTE.inkMuted, mt: 0.25 }}>
                  Its cost is a commission on sales, so the model uses it as a control, not as a budget lever.
                </Typography>
              </Box>
            );
          })}
        </Stack>
      </Box>

      {/* --- Progress & Save Scenario --- */}
      <Box sx={{ mt: 2, minHeight: 8 }}>
        {evaluating && <LinearProgress color="secondary" aria-label="Evaluating the scenario on the fitted model" />}
      </Box>
      <Paper elevation={0} sx={{ p: 1.25, mt: 0.5, backgroundColor: PALETTE.bgCanvas, border: `1px solid ${PALETTE.borderSubtle}`, borderRadius: 1.5 }}>
        <Button fullWidth variant="outlined" startIcon={<BookmarkAddOutlinedIcon fontSize="small" />} onClick={onSave} disabled={!canSave}>
          Save scenario to compare
        </Button>
        {saveHint && (
          <Typography variant="caption" component="p" sx={{ mt: 0.75, color: PALETTE.inkSecondary, textAlign: "center" }}>
            {saveHint}
          </Typography>
        )}
      </Paper>
    </Box>
  );
};
