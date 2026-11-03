import React, { useEffect, useId, useMemo, useState } from "react";
import {
  Box,
  Button,
  Divider,
  IconButton,
  InputAdornment,
  LinearProgress,
  MenuItem,
  Slider,
  Stack,
  TextField,
  ToggleButton,
  ToggleButtonGroup,
  Tooltip,
  Typography,
  alpha,
} from "@mui/material";
import RestartAltIcon from "@mui/icons-material/RestartAlt";
import LockOutlinedIcon from "@mui/icons-material/LockOutlined";
import BookmarkAddOutlinedIcon from "@mui/icons-material/BookmarkAddOutlined";
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
  toDisplayEuro,
} from "./format";

export interface Preset {
  id: string;
  label: string;
  multipliers: Record<string, number>;
}

/** Presets built from the plan's own levers over the chosen period; notebook 05's digital and Display → SEA scenarios among them. */
export function buildPresets(levers: ScenarioChannelRow[], periodBase: Record<string, number>): Preset[] {
  const base = (l: ScenarioChannelRow) => periodBase[l.slug] ?? l.base_spend;
  const active = levers.filter((l) => base(l) > 0);
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
    // Budget-neutral over the chosen period: 20 % of Display's planned spend moves to SEA
    presets.push({
      id: "display-to-sea",
      label: "20 % of Display → SEA",
      multipliers: { display: 0.8, sea: 1 + (0.2 * base(display)) / base(sea) },
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

const TOGGLE_SX = { textTransform: "none", px: 1.25, py: 0.25, fontWeight: 600 } as const;

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

interface LeverPanelProps {
  levers: ScenarioChannelRow[];
  locked: ScenarioChannelRow[];
  multipliers: Record<string, number>;
  onChange: (next: Record<string, number>) => void;
  evaluating: boolean;
  horizonDays: number;
  horizonMax: number;
  onHorizonChange: (days: number) => void;
  startDate?: string | null;
  planDaily?: Record<string, number[]>;
  inputMode: BudgetInputMode;
  onInputModeChange: (mode: BudgetInputMode) => void;
  spendGranularity: SpendGranularity;
  onSpendGranularityChange: (granularity: SpendGranularity) => void;
  onSave: () => void;
  canSave: boolean;
  saveHint?: string;
}

export const LeverPanel: React.FC<LeverPanelProps> = ({
  levers,
  locked,
  multipliers,
  onChange,
  evaluating,
  horizonDays,
  horizonMax,
  onHorizonChange,
  startDate,
  planDaily,
  inputMode: mode,
  onInputModeChange,
  spendGranularity: granularity,
  onSpendGranularityChange,
  onSave,
  canSave,
  saveHint,
}) => {
  const effectiveHorizon = Math.max(1, Math.min(horizonDays, horizonMax));
  const [daysDraft, setDaysDraft] = useState(String(effectiveHorizon));
  useEffect(() => setDaysDraft(String(effectiveHorizon)), [effectiveHorizon]);

  const periodEndDate = useMemo(() => (startDate ? addDays(startDate, effectiveHorizon - 1) : null), [startDate, effectiveHorizon]);

  // Each channel's planned spend over the chosen forecast period (`effectiveHorizon` days)
  const periodBaseBySlug = useMemo(() => {
    const map: Record<string, number> = {};
    for (const ch of [...levers, ...locked]) map[ch.slug] = channelPeriodBaseSpend(ch.base_spend, planDaily?.[ch.slug], effectiveHorizon, horizonMax);
    return map;
  }, [levers, locked, planDaily, effectiveHorizon, horizonMax]);

  const leverSlugs = useMemo(() => levers.map((l) => l.slug), [levers]);
  const baseMediaSpend = levers.reduce((acc, ch) => acc + (periodBaseBySlug[ch.slug] ?? 0), 0);
  const scenarioMediaSpend = levers.reduce((acc, ch) => acc + (periodBaseBySlug[ch.slug] ?? 0) * (multipliers[ch.slug] ?? 1), 0);
  const totalDeltaSpend = scenarioMediaSpend - baseMediaSpend;
  const totalDeltaPct = baseMediaSpend > 0 ? (totalDeltaSpend / baseMediaSpend) * 100 : 0;
  const perDay = granularity === "daily" ? "/d" : "";
  const display = (periodSpend: number) => toDisplayEuro(periodSpend, granularity, effectiveHorizon);

  const presets = useMemo(() => buildPresets(levers, periodBaseBySlug), [levers, periodBaseBySlug]);
  const changed = Object.values(multipliers).some((v) => Math.abs(v - 1) > 1e-9);
  const activePreset = presets.find((p) => sameMultipliers(p.multipliers, multipliers));

  const setOne = (slug: string, m: number) => {
    const clamped = Math.max(0, Math.min(4, Math.round(m * 1000) / 1000));
    onChange({ ...multipliers, [slug]: clamped });
  };

  const handleTotalChange = (periodTarget: number) => onChange(scaleMultipliersToTotal(leverSlugs, periodBaseBySlug, multipliers, periodTarget));

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

      <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap sx={{ mt: 1.5 }}>
        <ToggleButtonGroup
          size="small"
          exclusive
          value={mode}
          onChange={(_, v: BudgetInputMode | null) => v && onInputModeChange(v)}
          aria-label="Enter budgets as"
        >
          <ToggleButton value="pct" sx={TOGGLE_SX}>
            % change
          </ToggleButton>
          <ToggleButton value="eur" sx={TOGGLE_SX}>
            € amount
          </ToggleButton>
        </ToggleButtonGroup>
        {mode === "eur" && (
          <ToggleButtonGroup
            size="small"
            exclusive
            value={granularity}
            onChange={(_, v: SpendGranularity | null) => v && onSpendGranularityChange(v)}
            aria-label="Euro amounts per"
          >
            <ToggleButton value="period" sx={TOGGLE_SX}>
              Period total
            </ToggleButton>
            <ToggleButton value="daily" sx={TOGGLE_SX}>
              Per day
            </ToggleButton>
          </ToggleButtonGroup>
        )}
      </Stack>

      {/* --- Forecast period --- */}
      <Box component="fieldset" sx={{ ...FIELDSET_SX, mt: 2 }}>
        <Typography component="legend" sx={LEGEND_SX}>
          Forecast period
        </Typography>
        <Stack direction="row" justifyContent="space-between" alignItems="center" spacing={1}>
          <Typography variant="body2" sx={{ ...NUMERIC, fontWeight: 650, color: PALETTE.inkPrimary }}>
            {startDate && periodEndDate ? `${shortDate(startDate)} – ${shortDate(periodEndDate)}` : `First ${effectiveHorizon} of ${horizonMax} planned days`}
          </Typography>
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
            getAriaValueText={(v) => `${v} forecast days`}
            aria-label="Forecast period in days"
            onChange={(_, v) => onHorizonChange(v as number)}
            sx={{ mt: 0.5, py: 1 }}
          />
        )}
      </Box>

      {/* --- Quick scenarios --- */}
      {presets.length > 0 && (
        <TextField
          select
          fullWidth
          size="small"
          label="Quick scenario"
          value={activePreset?.id ?? (changed ? "custom" : "plan")}
          onChange={(e) => {
            const picked = presets.find((p) => p.id === e.target.value);
            onChange(picked ? picked.multipliers : {});
          }}
          sx={{ mt: 2, "& .MuiInputBase-root": { backgroundColor: "#FFFFFF" } }}
        >
          <MenuItem value="plan">As planned</MenuItem>
          {changed && !activePreset && (
            <MenuItem value="custom" disabled>
              Your own changes
            </MenuItem>
          )}
          {presets.map((p) => (
            <MenuItem key={p.id} value={p.id}>
              {p.label}
            </MenuItem>
          ))}
        </TextField>
      )}

      {/* --- Channels --- */}
      <Box component="fieldset" sx={{ ...FIELDSET_SX, mt: 2 }}>
        <Typography component="legend" sx={LEGEND_SX}>
          Channels
        </Typography>
        <Stack spacing={1.75} divider={<Divider flexItem sx={{ borderColor: PALETTE.borderSubtle }} />}>
          {levers.map((ch) => {
            const m = multipliers[ch.slug] ?? 1;
            const pct = (m - 1) * 100;
            const base = periodBaseBySlug[ch.slug] ?? 0;
            const noSpend = base <= 0;
            const moved = Math.abs(m - 1) > 1e-9;
            const scenarioEur = base * m;
            const activeDays = channelActiveDays(planDaily?.[ch.slug], effectiveHorizon);
            const days = !noSpend && activeDays < effectiveHorizon ? ` · on ${activeDays} of ${effectiveHorizon} days` : "";
            const caption = noSpend
              ? `No spend planned in ${effectiveHorizon === horizonMax ? "this plan" : `the first ${effectiveHorizon} days`}`
              : mode === "pct"
                ? (moved ? `${eurCompact(base)} → ${eurCompact(scenarioEur)} (${signedEur(scenarioEur - base)})` : `${eurCompact(base)} planned`) + days
                : `Plan ${eurCompact(display(base))}${perDay}${moved ? ` (${signedPct(pct, 1)})` : ""}${days}`;
            const displayMax = Math.max(100, Math.round(display(base * 2)));

            return (
              <Box key={ch.slug} role="group" aria-labelledby={`lever-${ch.slug}`}>
                <Stack direction="row" justifyContent="space-between" alignItems="flex-start" spacing={1}>
                  <Box sx={{ minWidth: 0, flexGrow: 1 }}>
                    <Stack direction="row" spacing={0.5} alignItems="center">
                      <Typography id={`lever-${ch.slug}`} sx={{ fontWeight: 700, fontSize: "0.88rem", lineHeight: 1.3 }}>
                        {ch.channel}
                      </Typography>
                      {moved && (
                        <Tooltip title={`Reset ${ch.channel} to planned spend`}>
                          <IconButton size="small" aria-label={`Reset ${ch.channel} to planned spend`} onClick={() => setOne(ch.slug, 1)} sx={{ p: 0.2 }}>
                            <RestartAltIcon sx={{ fontSize: 14 }} />
                          </IconButton>
                        </Tooltip>
                      )}
                    </Stack>
                    <Typography variant="caption" sx={{ color: PALETTE.inkSecondary, ...NUMERIC, display: "block", mt: 0.15 }}>
                      {caption}
                    </Typography>
                  </Box>
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
                      value={display(scenarioEur)}
                      name={`channel-eur-${ch.slug}`}
                      label={`${ch.channel} ${granularity === "daily" ? "daily budget in euros per day" : "period budget in euros"}`}
                      suffix={perDay || undefined}
                      disabled={noSpend}
                      width={112}
                      onCommit={(entered) => setOne(ch.slug, fromDisplayEuro(entered, granularity, effectiveHorizon) / base)}
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
                    max={displayMax}
                    step={Math.max(10, Math.round(display(base / 40)))}
                    value={Math.max(0, Math.min(displayMax, Math.round(display(scenarioEur))))}
                    disabled={noSpend}
                    marks={noSpend ? undefined : [{ value: Math.round(display(base)) }]}
                    valueLabelDisplay="auto"
                    valueLabelFormat={(v) => `${eurCompact(v as number)}${perDay}`}
                    getAriaValueText={(v) => `${eur(v as number)}${granularity === "daily" ? " per day" : ""} for ${ch.channel}`}
                    aria-labelledby={`lever-${ch.slug}`}
                    onChange={(_, v) => setOne(ch.slug, fromDisplayEuro(v as number, granularity, effectiveHorizon) / base)}
                    sx={{ mt: 0.25, py: 1 }}
                  />
                )}
              </Box>
            );
          })}

          {locked.map((ch) => (
            <Box key={ch.slug}>
              <Stack direction="row" justifyContent="space-between" alignItems="center" spacing={1} sx={{ color: PALETTE.inkMuted }}>
                <Stack direction="row" spacing={0.75} alignItems="center">
                  <LockOutlinedIcon sx={{ fontSize: 15 }} aria-hidden="true" />
                  <Typography variant="caption" sx={{ fontWeight: 700 }}>
                    {ch.channel}
                  </Typography>
                </Stack>
                <Typography variant="caption" sx={{ fontWeight: 700, ...NUMERIC }}>
                  {eurCompact(periodBaseBySlug[ch.slug] ?? ch.base_spend)} planned
                </Typography>
              </Stack>
              <Typography variant="caption" component="p" sx={{ color: PALETTE.inkMuted, mt: 0.25 }}>
                Kept as planned: the model uses it as a control, not as a budget lever.
              </Typography>
            </Box>
          ))}
        </Stack>
      </Box>

      {/* --- Total media budget --- */}
      <Box component="fieldset" sx={{ ...FIELDSET_SX, mt: 2, backgroundColor: alpha(PALETTE.brandGold, 0.04) }}>
        <Typography component="legend" sx={LEGEND_SX}>
          Total media budget
        </Typography>
        <Stack direction="row" justifyContent="space-between" alignItems="center" spacing={1}>
          <Typography id="total-budget-label" variant="caption" sx={{ color: PALETTE.inkSecondary, ...NUMERIC, minWidth: 0 }}>
            {mode === "pct"
              ? `${eurCompact(baseMediaSpend)} planned → ${eurCompact(scenarioMediaSpend)}`
              : `Plan ${eurCompact(display(baseMediaSpend))}${perDay} (${signedPct(totalDeltaPct, 1)})`}
          </Typography>
          {mode === "pct" ? (
            <PctInput
              value={totalDeltaPct}
              name="total-budget-pct"
              label="Total media budget change in percent"
              disabled={baseMediaSpend <= 0}
              onCommit={(pct) => handleTotalChange(Math.max(0, baseMediaSpend * (1 + pct / 100)))}
            />
          ) : (
            <EuroInput
              value={display(scenarioMediaSpend)}
              name="total-budget-eur"
              label={granularity === "daily" ? "Daily media budget in euros per day" : "Total media budget in euros"}
              suffix={perDay || undefined}
              disabled={baseMediaSpend <= 0}
              onCommit={(entered) => handleTotalChange(fromDisplayEuro(entered, granularity, effectiveHorizon))}
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
            onChange={(_, v) => handleTotalChange(Math.max(0, baseMediaSpend * (1 + (v as number) / 100)))}
            sx={{ mt: 0.25, py: 1 }}
          />
        ) : (
          <Slider
            size="small"
            color="secondary"
            min={0}
            max={Math.max(100, Math.round(display(baseMediaSpend * 2)))}
            step={Math.max(10, Math.round(display(baseMediaSpend / 40)))}
            value={Math.max(0, Math.min(Math.round(display(baseMediaSpend * 2)), Math.round(display(scenarioMediaSpend))))}
            disabled={baseMediaSpend <= 0}
            marks={baseMediaSpend > 0 ? [{ value: Math.round(display(baseMediaSpend)) }] : undefined}
            valueLabelDisplay="auto"
            valueLabelFormat={(v) => `${eurCompact(v as number)}${perDay}`}
            getAriaValueText={(v) => `${eur(v as number)}${granularity === "daily" ? " per day" : ""} total media budget`}
            aria-labelledby="total-budget-label"
            onChange={(_, v) => handleTotalChange(fromDisplayEuro(v as number, granularity, effectiveHorizon))}
            sx={{ mt: 0.25, py: 1 }}
          />
        )}
      </Box>

      <Box sx={{ mt: 2, minHeight: 8 }}>
        {evaluating && <LinearProgress color="secondary" aria-label="Evaluating the scenario on the fitted model" />}
      </Box>
      <Button fullWidth variant="outlined" startIcon={<BookmarkAddOutlinedIcon fontSize="small" />} onClick={onSave} disabled={!canSave} sx={{ mt: 0.5 }}>
        Save scenario to compare
      </Button>
      {saveHint && (
        <Typography variant="caption" component="p" sx={{ mt: 0.75, color: PALETTE.inkSecondary, textAlign: "center" }}>
          {saveHint}
        </Typography>
      )}
    </Box>
  );
};
