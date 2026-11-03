import React from "react";
import { Box, Checkbox, FormControl, FormControlLabel, FormGroup, FormLabel, Typography } from "@mui/material";
import type { CalendarControls, DataProfile } from "../../../api/types";
import { PALETTE } from "../../../theme";
import { DataProfileSummary, workbookSource } from "../DataProfileSummary";
import { formatDay } from "../format";
import { type TabProps, bound, patchRoleConfig, patchSettings } from "./draft";
import { Group, NumberField, SwitchField } from "./fields";

const CALENDAR: { key: keyof CalendarControls; label: string; detail: string }[] = [
  { key: "weekdays", label: "Weekdays", detail: "One dummy per weekday, Monday as reference" },
  { key: "holidays", label: "German public holidays", detail: "National holidays of every year in the data" },
  { key: "christmas", label: "Christmas window", detail: "20–31 December, plus the 24 and 31 December half-days" },
  { key: "black_week", label: "Black Week", detail: "Black Friday to Cyber Monday" },
];

const DAY_MS = 86_400_000;
const isoDay = (t: number) => new Date(t).toISOString().slice(0, 10);

/** The hold-out window the run will use: the last `days` days of the modelling period, read from the workbook profile. */
function holdoutWindow(profile: DataProfile | null | undefined, days: number, dropTrailing: boolean): string | null {
  const end = dropTrailing ? profile?.modelling_end_date : profile?.date_max || profile?.end_date;
  if (!end) return null;
  const endT = Date.parse(`${end.slice(0, 10)}T00:00:00Z`);
  if (!Number.isFinite(endT)) return null;
  return `${formatDay(isoDay(endT - (days - 1) * DAY_MS))} – ${formatDay(end)}`;
}

export const DataTab: React.FC<TabProps & { dataProfile?: DataProfile | null }> = ({ draft, setDraft, defaults, locked, dataProfile }) => {
  const rc = draft.roleConfig;
  const ms = rc.model_settings;
  const base = defaults.model_settings;
  const holdout = rc.holdout_days ?? defaults.holdout_days;
  const dropTrailing = Boolean(rc.drop_trailing_isolated_days);
  const trailing = dataProfile?.trailing_isolated_dates ?? [];
  const days = dataProfile?.calendar_days ?? dataProfile?.modelling_days;
  const limits = bound(defaults, "holdout_days");
  // Meridian needs more training days than hold-out days
  const maxHoldout = days ? Math.min(limits.max ?? 365, Math.floor((days - 1) / 2)) : limits.max;
  const window = holdoutWindow(dataProfile, holdout, dropTrailing);
  const calendarChanged = CALENDAR.some((c) => ms.calendar[c.key] !== base.calendar[c.key]);

  return (
    <>
      {dataProfile && (
        <Group first title="Workbook" description={workbookSource(dataProfile)}>
          <DataProfileSummary profile={dataProfile} channelCount={rc.channels.length} />
        </Group>
      )}
      <Group
        first={!dataProfile}
        title="Validation"
        description="The last days of the data are hidden from every model, so their error measures a genuine forecast."
        changed={holdout !== defaults.holdout_days || dropTrailing !== defaults.drop_trailing_isolated_days}
      >
        <Box sx={{ display: "flex", flexWrap: "wrap", gap: 3, alignItems: "flex-start" }}>
          <NumberField
            label="Hold-out length"
            unit="days"
            value={holdout}
            min={limits.min}
            max={maxHoldout}
            disabled={locked}
            onChange={(v) => patchRoleConfig(setDraft, { holdout_days: v })}
            helper={window ? `${window} · notebooks: ${defaults.holdout_days}` : `Notebooks: ${defaults.holdout_days} days`}
            width={300}
          />
          {trailing.length > 0 && (
            <Box sx={{ maxWidth: 420 }}>
              <SwitchField
                label={`Keep ${trailing.map((d) => formatDay(d)).join(", ")} in the models`}
                checked={!dropTrailing}
                disabled={locked}
                onChange={(keep) => patchRoleConfig(setDraft, { drop_trailing_isolated_days: !keep })}
                help="An isolated day after the last full year. The notebooks keep it in every model and leave it out of yearly and monthly comparisons only."
              />
            </Box>
          )}
        </Box>
      </Group>

      <Group title="Controls" description="Calendar and seasonality terms in both the OLS benchmark and Meridian. The data-preparation page keeps notebook 02's steps." changed={calendarChanged || ms.n_fourier !== base.n_fourier || ms.trend !== base.trend || ms.missing_row_controls !== base.missing_row_controls}>
        <FormControl component="fieldset" fullWidth disabled={locked} sx={{ m: 0, p: 0, border: 0, minWidth: 0, mb: 2.5 }}>
          <FormLabel component="legend" sx={{ fontWeight: 650, fontSize: "0.84rem", color: PALETTE.inkPrimary, mb: 0.5, "&.Mui-focused": { color: PALETTE.inkPrimary } }}>
            Calendar controls
          </FormLabel>
          <FormGroup sx={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(min(100%, 230px), 1fr))", columnGap: 2 }}>
            {CALENDAR.map((c) => (
              <FormControlLabel
                key={c.key}
                control={
                  <Checkbox
                    size="small"
                    checked={ms.calendar[c.key]}
                    onChange={(e) => patchSettings(setDraft, { calendar: { ...ms.calendar, [c.key]: e.target.checked } })}
                  />
                }
                label={
                  <Box component="span" sx={{ display: "block", py: 0.5 }}>
                    <Typography component="span" sx={{ display: "block", fontWeight: 600, fontSize: "0.88rem", color: PALETTE.inkPrimary }}>
                      {c.label}
                    </Typography>
                    <Typography component="span" variant="caption" sx={{ display: "block", color: PALETTE.inkSecondary }}>
                      {c.detail}
                    </Typography>
                  </Box>
                }
                sx={{ alignItems: "flex-start", "& .MuiCheckbox-root": { mt: 0.25 } }}
              />
            ))}
          </FormGroup>
        </FormControl>

        <Box sx={{ display: "flex", flexWrap: "wrap", gap: 3, alignItems: "flex-start" }}>
          <NumberField
            label="Yearly Fourier pairs"
            value={ms.n_fourier}
            {...bound(defaults, "n_fourier")}
            disabled={locked}
            onChange={(v) => patchSettings(setDraft, { n_fourier: v })}
            helper={`Sine/cosine pairs for the yearly cycle · notebooks: ${base.n_fourier}`}
            width={300}
          />
          <Box sx={{ maxWidth: 420 }}>
            <SwitchField
              label="Linear trend"
              checked={ms.trend}
              disabled={locked}
              onChange={(trend) => patchSettings(setDraft, { trend })}
              help="A straight line in years since the first day, for slow growth or decline."
            />
          </Box>
          <Box sx={{ maxWidth: 420 }}>
            <SwitchField
              label="Missing-row controls"
              checked={ms.missing_row_controls}
              disabled={locked}
              onChange={(missing_row_controls) => patchSettings(setDraft, { missing_row_controls })}
              help="A 0/1 control for each channel's days without a workbook row, so those days are not read as sales drops. Switch off to reproduce the notebooks exactly."
            />
          </Box>
        </Box>
      </Group>

      <Group title="Reproducibility" description="The random seed of every MCMC fit of this run. Another seed shows how much the results move with MCMC noise." changed={draft.seed !== defaults.seed}>
        <NumberField
          label="Random seed"
          value={draft.seed}
          {...bound(defaults, "seed")}
          disabled={locked}
          onChange={(seed) => setDraft((d) => ({ ...d, seed }))}
          helper={`Notebooks: ${defaults.seed}`}
          width={300}
        />
      </Group>
    </>
  );
};
