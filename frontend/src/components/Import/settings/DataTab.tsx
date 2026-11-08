import React from "react";
import { Box, Checkbox, FormControl, FormControlLabel, FormGroup, FormLabel, MenuItem, Radio, RadioGroup, TextField, Typography } from "@mui/material";
import type { CalendarControls, DataProfile, ResampleConfig, SourceFrequency } from "../../../api/types";
import { NUMERIC, PALETTE } from "../../../theme";
import { BACKTEST_COLORS } from "../../Forecast/figures";
import { DataProfileSummary, workbookSource } from "../DataProfileSummary";
import { formatDay } from "../format";
import { type TabProps, bound, patchRoleConfig, patchSettings } from "./draft";
import { Dot, Group, NumberField, SwitchField } from "./fields";
import { type DataSplit, splitWindows } from "./split";

const DEFAULT_FREQUENCIES: { value: SourceFrequency; label: string; help: string }[] = [
  { value: "auto", label: "Detect from the dates", help: "Use the spacing the dates have" },
  { value: "daily", label: "Daily", help: "One row per day" },
  { value: "weekly", label: "Weekly → days", help: "One row per week, split into its 7 days per column (finer rows are combined by week)" },
  { value: "monthly", label: "Monthly → days", help: "One row per calendar month, split into its days per column (finer rows are combined by month)" },
  { value: "sub_daily", label: "Several rows a day → days", help: "Rows that share a day (hours, regions, stores) are combined per column" },
];

const CALENDAR: { key: keyof CalendarControls; label: string; detail: string }[] = [
  { key: "weekdays", label: "Weekdays", detail: "One dummy per weekday, Monday as reference" },
  { key: "holidays", label: "Public holidays", detail: "The country's national holidays from Google Calendar" },
  { key: "christmas", label: "Christmas window", detail: "20–31 December, plus the 24 and 31 December half-days" },
  { key: "black_week", label: "Black Week", detail: "Black Friday to Cyber Monday" },
];

/** The split the run will use over its modelling period, read from the workbook profile. */
function dataSplit(profile: DataProfile | null | undefined, holdoutDays: number, dropTrailing: boolean, minTraining: number): DataSplit | null {
  const start = profile?.date_min || profile?.start_date;
  const end = dropTrailing ? profile?.modelling_end_date : profile?.date_max || profile?.end_date;
  return start && end ? splitWindows(start, end, holdoutDays, minTraining) : null;
}

/** Training → validation → test as a proportional bar with each window's dates, length and share of the data. */
const SplitPreview: React.FC<{ split: DataSplit; minTraining: number }> = ({ split, minTraining }) => {
  const share = (days: number) => `${Math.round((100 * days) / split.total)} %`;
  const rows = [
    { key: "training", name: "Training", colour: PALETTE.borderStrong, w: split.training },
    ...(split.validation ? [{ key: "validation", name: "Validation", colour: BACKTEST_COLORS.validation.line, w: split.validation }] : []),
    { key: "test", name: "Test (hold-out)", colour: BACKTEST_COLORS.test.line, w: split.test },
  ];
  // The longest test that still leaves a training year before an equally long validation window
  const longestWithValidation = Math.floor((split.total - minTraining) / 2);
  const summary = rows.map((r) => `${r.name} ${r.w.days} days (${share(r.w.days)})`).join(", ");
  return (
    <Box sx={{ mt: 2, maxWidth: 560 }}>
      <Box role="img" aria-label={`Split of the ${split.total} days: ${summary}`} sx={{ display: "flex", gap: "2px", height: 10, borderRadius: 0.5, overflow: "hidden" }}>
        {rows.map((r) => (
          <Box key={r.key} sx={{ flex: r.w.days, minWidth: 3, backgroundColor: r.colour }} />
        ))}
      </Box>
      <Box component="ul" sx={{ listStyle: "none", m: 0, mt: 1, p: 0, display: "grid", gap: 0.25 }}>
        {rows.map((r) => (
          <Box component="li" key={r.key} sx={{ display: "flex", flexWrap: "wrap", alignItems: "center", columnGap: 1, fontSize: "0.8125rem", color: PALETTE.inkSecondary, ...NUMERIC }}>
            <Dot colour={r.colour} />
            <Box component="span" sx={{ fontWeight: 600, color: PALETTE.inkPrimary, minWidth: 112 }}>
              {r.name}
            </Box>
            <span>
              {formatDay(r.w.start)} – {formatDay(r.w.end)} · {r.w.days} days · {share(r.w.days)}
            </span>
          </Box>
        ))}
      </Box>
      {!split.validation && (
        <Typography variant="body2" sx={{ mt: 1, color: PALETTE.inkSecondary }}>
          {longestWithValidation >= 14
            ? `No validation window: one as long as the test would leave less than a year of training before it. A hold-out of at most ${longestWithValidation} days gets one.`
            : `No validation window: that needs ${minTraining} training days plus two windows of at least 14 days.`}{" "}
          Without it the model-selection grid and the forward hold-out are skipped.
        </Typography>
      )}
    </Box>
  );
};

export const DataTab: React.FC<TabProps & { dataProfile?: DataProfile | null }> = ({ draft, setDraft, defaults, locked, dataProfile }) => {
  const rc = draft.roleConfig;
  const ms = rc.model_settings;
  const base = defaults.model_settings;
  const holdout = rc.holdout_days ?? defaults.holdout_days;
  const dropTrailing = Boolean(rc.drop_trailing_isolated_days);
  const trailing = dataProfile?.trailing_isolated_dates ?? [];
  const days = dataProfile?.calendar_days ?? dataProfile?.modelling_days;
  const limits = bound(defaults, "holdout_days");
  // A full year of training days must remain before the hold-out (the profile knows how long that leaves)
  const maxHoldout = dataProfile?.max_holdout_days ?? (days ? Math.min(limits.max ?? 365, Math.floor((days - 1) / 2)) : limits.max);
  const holdoutDefault = dataProfile?.default_holdout_days ?? defaults.holdout_days;
  const countries = defaults.countries ?? [{ code: "DE", name: "Germany" }];
  const country = rc.country ?? defaults.country ?? "DE";
  const countryName = countries.find((c) => c.code === country)?.name ?? country;
  const weather = Boolean(ms.weather_controls);
  const minTraining = defaults.min_training_days ?? 365;
  const split = dataSplit(dataProfile, holdout, dropTrailing, minTraining);
  const calendarChanged = CALENDAR.some((c) => ms.calendar[c.key] !== base.calendar[c.key]);
  const frequencies = (defaults.frequencies as typeof DEFAULT_FREQUENCIES | undefined) ?? DEFAULT_FREQUENCIES;
  const resample = rc.columns?.resample ?? { frequency: "auto" as SourceFrequency, period_label: "start" as const, fill_missing_dates: false, columns: [] };
  const frequency = resample.frequency ?? "auto";
  const freqHelp = frequencies.find((f) => f.value === frequency)?.help ?? "Default: Detect from the dates";
  const isWide = rc.columns?.layout === "wide";
  const patchResample = (patch: Partial<ResampleConfig>) => {
    const cols = rc.columns ?? {
      layout: "long" as const,
      date: rc.date_column ?? "date",
      channel: "channel",
      sales: rc.target_column ?? "sales_by_channel",
      costs: "costs",
      wide_channels: [],
      controls: [],
    };
    patchRoleConfig(setDraft, { columns: { ...cols, resample: { ...resample, ...patch } } });
  };

  return (
    <>
      {dataProfile && (
        <Group first title="Workbook" description={workbookSource(dataProfile)}>
          <DataProfileSummary profile={dataProfile} channelCount={rc.channels.length} />
        </Group>
      )}
      <Group
        first={!dataProfile}
        title="Frequency & resampling"
        description="Choose how the uploaded dates are read and resampled onto the model's daily grid (or ask Gemini in the settings advisor above). Choosing weekly or monthly combines finer rows by week or month and splits each period into days."
        changed={frequency !== "auto" || Boolean(resample.fill_missing_dates)}
      >
        <Box sx={{ display: "flex", flexWrap: "wrap", gap: 3, alignItems: "flex-start" }}>
          <TextField
            select
            label="Data frequency"
            size="small"
            value={frequency}
            disabled={locked}
            onChange={(e) => patchResample({ frequency: e.target.value as SourceFrequency })}
            sx={{ width: 300, maxWidth: "100%", "& .MuiInputBase-root": { backgroundColor: "#FFFFFF" } }}
            helperText={freqHelp}
          >
            {frequencies.map((f) => (
              <MenuItem key={f.value} value={f.value}>
                {f.label}
              </MenuItem>
            ))}
          </TextField>
          {frequency === "weekly" && (
            <Box>
              <Typography id="run-period-label-title" sx={{ fontWeight: 650, fontSize: "0.84rem", color: PALETTE.inkPrimary }}>
                Each weekly date is the week's
              </Typography>
              <RadioGroup
                row
                aria-labelledby="run-period-label-title"
                value={resample.period_label ?? "start"}
                onChange={(e) => patchResample({ period_label: e.target.value as "start" | "end" })}
              >
                <FormControlLabel value="start" disabled={locked} control={<Radio size="small" />} label="first day" />
                <FormControlLabel value="end" disabled={locked} control={<Radio size="small" />} label="last day (week ending)" />
              </RadioGroup>
            </Box>
          )}
          {isWide && (
            <Box sx={{ maxWidth: 420 }}>
              <SwitchField
                label="Fill missing dates"
                checked={Boolean(resample.fill_missing_dates)}
                disabled={locked}
                onChange={(fill_missing_dates) => patchResample({ fill_missing_dates })}
                help="Add missing days, weeks or months and fill them with each column's fill method."
              />
            </Box>
          )}
        </Box>
      </Group>
      <Group
        title="Train / validation / test split"
        description="The models train on the earliest days; a validation window (model selection, forward hold-out) and an equally long test window at the end are hidden from them, so their errors measure genuine forecasts. Default: each about an eighth of the data, at most about a quarter, with a full year of training before validation."
        changed={holdout !== holdoutDefault || dropTrailing !== defaults.drop_trailing_isolated_days}
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
            helper={`The test window. Default for this data: ${holdoutDefault} days`}
            width={300}
          />
          {trailing.length > 0 && (
            <Box sx={{ maxWidth: 420 }}>
              <SwitchField
                label={`Keep ${trailing.map((d) => formatDay(d)).join(", ")} in the models`}
                checked={!dropTrailing}
                disabled={locked}
                onChange={(keep) => patchRoleConfig(setDraft, { drop_trailing_isolated_days: !keep })}
                help="An isolated day after the last full year. By default it stays in every model and is left out of yearly and monthly comparisons only."
              />
            </Box>
          )}
        </Box>
        {split && <SplitPreview split={split} minTraining={minTraining} />}
      </Group>

      <Group
        title="Country"
        description="The market the data comes from: its public holidays (Google Calendar) feed the holiday control, and its weather (Open-Meteo) the optional weather controls."
        changed={country !== (defaults.country ?? "DE") || weather}
      >
        <Box sx={{ display: "flex", flexWrap: "wrap", gap: 3, alignItems: "flex-start" }}>
          <TextField
            select
            label="Country"
            size="small"
            value={country}
            disabled={locked}
            onChange={(e) => patchRoleConfig(setDraft, { country: e.target.value })}
            sx={{ width: 300, maxWidth: "100%", "& .MuiInputBase-root": { backgroundColor: "#FFFFFF" } }}
            helperText="Default: Germany"
          >
            {countries.map((c) => (
              <MenuItem key={c.code} value={c.code}>
                {c.name}
              </MenuItem>
            ))}
          </TextField>
          <Box sx={{ maxWidth: 460 }}>
            <SwitchField
              label="Weather controls"
              checked={weather}
              disabled={locked}
              onChange={(weather_controls) => patchSettings(setDraft, { weather_controls })}
              help={`Temperature, sunshine and precipitation vs their seasonal normal in ${countryName}, population-weighted (Open-Meteo). A forecast assumes normal weather. Off by default.`}
            />
          </Box>
        </Box>
      </Group>

      <Group title="Controls" description="Calendar and seasonality terms in both the OLS benchmark and Meridian. The data-preparation page keeps its fixed steps." changed={calendarChanged || ms.n_fourier !== base.n_fourier || ms.trend !== base.trend || ms.missing_row_controls !== base.missing_row_controls}>
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
                      {c.key === "holidays" ? `${c.label} (${countryName})` : c.label}
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
            helper={`Sine/cosine pairs for the yearly cycle · default: ${base.n_fourier}`}
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
              help="A 0/1 control for each channel's days without a workbook row, so those days are not read as sales drops. Off, missing channel-days count as 0 sales."
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
          helper={`Default: ${defaults.seed}`}
          width={300}
        />
      </Group>
    </>
  );
};
