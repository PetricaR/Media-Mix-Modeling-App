import React, { useEffect, useState } from "react";
import {
  Box,
  Button,
  Chip,
  FormControl,
  InputAdornment,
  InputLabel,
  LinearProgress,
  MenuItem,
  Select,
  Slider,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import RestartAltIcon from "@mui/icons-material/RestartAlt";
import LockOutlinedIcon from "@mui/icons-material/LockOutlined";
import BookmarkAddOutlinedIcon from "@mui/icons-material/BookmarkAddOutlined";
import type { ScenarioChannelRow } from "../../api/types";
import { FONT_MONO, PALETTE } from "../../theme";
import { eurCompact, signedPct } from "./format";

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

const PctInput: React.FC<{ value: number; label: string; disabled?: boolean; onCommit: (pct: number) => void }> = ({ value, label, disabled, onCommit }) => {
  const shown = String(Math.round(value * 10) / 10);
  const [draft, setDraft] = useState(shown);
  useEffect(() => setDraft(shown), [shown]);
  const commit = () => {
    const v = Number(draft.replace(",", ".").replace("−", "-"));
    if (draft.trim() !== "" && Number.isFinite(v)) onCommit(Math.max(-100, Math.min(100, v)));
    else setDraft(shown);
  };
  return (
    <TextField
      size="small"
      value={draft}
      disabled={disabled}
      onChange={(e) => setDraft(e.target.value)}
      onBlur={commit}
      onKeyDown={(e) => {
        if (e.key === "Enter") commit();
      }}
      inputProps={{ inputMode: "decimal", "aria-label": label, style: { textAlign: "right", fontFamily: FONT_MONO, padding: "5px 2px 5px 8px" } }}
      InputProps={{ endAdornment: <InputAdornment position="end">%</InputAdornment> }}
      sx={{ width: 88, flexShrink: 0 }}
    />
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
  onSave: () => void;
  canSave: boolean;
  saveHint?: string;
}

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
  onSave,
  canSave,
  saveHint,
}) => {
  const presets = buildPresets(levers);
  const changed = Object.values(multipliers).some((v) => Math.abs(v - 1) > 1e-9);
  const setOne = (slug: string, m: number) => onChange({ ...multipliers, [slug]: Math.round(m * 1000) / 1000 });

  return (
    <Box component="section" aria-labelledby="levers-title">
      <Stack direction="row" justifyContent="space-between" alignItems="center" spacing={1}>
        <Typography id="levers-title" variant="h3">
          Budget levers
        </Typography>
        <Button size="small" startIcon={<RestartAltIcon fontSize="small" />} onClick={() => onChange({})} disabled={!changed}>
          Back to plan
        </Button>
      </Stack>
      <Typography variant="body2" sx={{ mt: 0.5 }}>
        Scale a channel's budget over the whole plan. The forecast updates on its own a moment after you stop.
      </Typography>

      <Typography variant="caption" component="p" sx={{ mt: 2, mb: 0.75, fontWeight: 700, color: PALETTE.inkSecondary }}>
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

      <Stack spacing={2} sx={{ mt: 2.5 }}>
        {levers.map((ch) => {
          const m = multipliers[ch.slug] ?? 1;
          const pct = (m - 1) * 100;
          const noSpend = ch.base_spend <= 0;
          const eff = effects?.[ch.slug];
          const moved = Math.abs(m - 1) > 1e-9;
          return (
            <Box key={ch.slug} role="group" aria-labelledby={`lever-${ch.slug}`}>
              <Stack direction="row" justifyContent="space-between" alignItems="center" spacing={1}>
                <Box sx={{ minWidth: 0 }}>
                  <Typography id={`lever-${ch.slug}`} sx={{ fontWeight: 700, fontSize: "0.9rem", lineHeight: 1.3 }}>
                    {ch.channel}
                  </Typography>
                  <Typography variant="caption" sx={{ color: PALETTE.inkSecondary, fontFamily: FONT_MONO }}>
                    {noSpend ? "No spend in this plan" : moved ? `${eurCompact(ch.base_spend)} → ${eurCompact(ch.base_spend * m)}` : `${eurCompact(ch.base_spend)} planned`}
                  </Typography>
                </Box>
                <PctInput value={pct} label={`${ch.channel} budget change in percent`} disabled={noSpend} onCommit={(v) => setOne(ch.slug, 1 + v / 100)} />
              </Stack>
              <Slider
                size="small"
                color="secondary"
                min={-100}
                max={100}
                step={5}
                value={pct}
                disabled={noSpend}
                marks={[{ value: 0 }]}
                valueLabelDisplay="auto"
                valueLabelFormat={(v) => signedPct(v, 0)}
                getAriaValueText={(v) => `${signedPct(v, 0)} on the planned budget`}
                aria-labelledby={`lever-${ch.slug}`}
                onChange={(_, v) => setOne(ch.slug, 1 + (v as number) / 100)}
                sx={{ mt: 0.25, py: 1.25 }}
              />
              {moved && eff && eff.delta_sales != null && (
                <Typography
                  variant="caption"
                  component="p"
                  sx={{ mt: -0.5, color: PALETTE.inkSecondary, opacity: effectsStale ? 0.5 : 1, transition: "opacity 0.2s" }}
                >
                  Sales effect{" "}
                  <Box component="span" sx={{ fontFamily: FONT_MONO, fontWeight: 700, color: PALETTE.inkPrimary }}>
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
        {locked.map((ch) => (
          <Box key={ch.slug}>
            <Stack direction="row" spacing={0.75} alignItems="center" sx={{ color: PALETTE.inkMuted }}>
              <LockOutlinedIcon sx={{ fontSize: 15 }} aria-hidden="true" />
              <Typography variant="caption" sx={{ fontWeight: 700 }}>
                {ch.channel} stays as planned
              </Typography>
            </Stack>
            <Typography variant="caption" component="p" sx={{ color: PALETTE.inkMuted, mt: 0.25 }}>
              Its cost is a commission on sales, so the model uses it as a control, not as a budget lever.
            </Typography>
          </Box>
        ))}
      </Stack>

      <FormControl size="small" fullWidth sx={{ mt: 3 }}>
        <InputLabel id="horizon-label">Count totals over</InputLabel>
        <Select labelId="horizon-label" label="Count totals over" value={Math.min(horizonDays, horizonMax)} onChange={(e) => onHorizonChange(Number(e.target.value))}>
          {[7, 14, 28, 42, 56, 90]
            .filter((d) => d < horizonMax)
            .concat(horizonMax)
            .map((d) => (
              <MenuItem key={d} value={d}>
                {d === horizonMax ? `The whole plan (${d} days)` : `The first ${d} days`}
              </MenuItem>
            ))}
        </Select>
      </FormControl>

      <Box sx={{ mt: 2, minHeight: 8 }}>
        {evaluating && <LinearProgress color="secondary" aria-label="Evaluating the scenario on the fitted model" />}
      </Box>
      <Button fullWidth variant="outlined" startIcon={<BookmarkAddOutlinedIcon fontSize="small" />} onClick={onSave} disabled={!canSave} sx={{ mt: 1 }}>
        Save to compare
      </Button>
      {saveHint && (
        <Typography variant="caption" component="p" sx={{ mt: 0.75, color: PALETTE.inkMuted }}>
          {saveHint}
        </Typography>
      )}
    </Box>
  );
};
