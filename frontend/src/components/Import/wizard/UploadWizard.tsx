import React, { useEffect, useMemo, useRef, useState } from "react";
import {
  Alert,
  Box,
  Button,
  Chip,
  CircularProgress,
  FormControlLabel,
  MenuItem,
  Paper,
  Radio,
  RadioGroup,
  Step,
  StepLabel,
  Stepper,
  TextField,
  Typography,
} from "@mui/material";
import { useHealth, useMappingRecommendation, useResampleRecommendation, useSettingsDefaults, useUploadInspection } from "../../../api/hooks";
import type {
  ChannelRole,
  ColumnMapping,
  DataRoleConfig,
  MappingRecommendation,
  ModelSettings,
  ResampleRecommendation,
  UploadInspection,
} from "../../../api/types";
import { FONT_MONO, NUMERIC, PALETTE } from "../../../theme";
import { NumberField, SwitchField } from "../settings/fields";
import { ChannelsStep } from "./ChannelsStep";
import { ColumnsStep } from "./ColumnsStep";
import { missingParts } from "./mapping";
import { ResampleStep } from "./ResampleStep";
import { carryResample, resampleSummary, splitErrors } from "./resample";

const STEPS = ["File", "Columns", "Resample & fill", "Channels", "Check & create"];
const RESAMPLE_STEP = 2;
const CHANNELS_STEP = 3;
const CREATE_STEP = 4;
/** The mapping apart from its resampling: Gemini's resampling plan belongs to one mapping. */
const mappingKey = (m: ColumnMapping | null) => (m ? JSON.stringify({ ...m, resample: undefined }) : "");
const errorText = (err: unknown, fallback: string) => (err instanceof Error && err.message ? err.message : fallback);
const fmtDay = (iso?: string) => (iso ? new Date(`${iso.slice(0, 10)}T00:00:00Z`).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric", timeZone: "UTC" }) : "—");

/** The side tables of other sheets (e.g. channel notes), shown so the user knows what Gemini reads too. */
const SideTables: React.FC<{ inspection: UploadInspection }> = ({ inspection }) =>
  inspection.side_tables.length ? (
    <Box component="details" sx={{ mt: 2, "& summary": { cursor: "pointer", fontWeight: 650, fontSize: "0.9rem" } }}>
      <summary>Small sheets ({inspection.side_tables.map((t) => t.sheet).join(", ")}): read as notes, not as daily data</summary>
      {inspection.side_tables.map((t) => (
        <Box key={t.sheet} role="region" aria-label={`Sheet ${t.sheet}`} tabIndex={0} sx={{ mt: 1.5, overflowX: "auto" }}>
          <Typography variant="caption" sx={{ fontWeight: 650 }}>
            {t.sheet} · {t.n_rows} rows
          </Typography>
          <Box component="table" sx={{ borderCollapse: "collapse", fontSize: "0.8rem", "& th, & td": { px: 1, py: 0.5, borderBottom: `1px solid ${PALETTE.borderSubtle}`, textAlign: "left" } }}>
            <thead>
              <tr>
                {t.columns.map((c) => (
                  <th key={c} scope="col">
                    {c}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {t.rows.map((r, i) => (
                <tr key={i}>
                  {t.columns.map((c) => (
                    <td key={c}>{r[c] === null || r[c] === undefined ? "" : String(r[c])}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </Box>
        </Box>
      ))}
    </Box>
  ) : null;

/**
 * The upload wizard: File (sheet) → Columns (the mapping, with the Gemini data assistant) → Resample & fill (how the
 * dates reach the daily grid and how each column's gaps are filled, with Gemini's plan) → Channels (roles) → Check &
 * create (country for holidays and weather, hold-out, run name). Nothing is stored until "Create draft run"; every step
 * re-checks the mapping on the server, and the resampling step re-checks every change (debounced) for its preview.
 */
export const UploadWizard: React.FC<{
  file: File;
  defaultName: string;
  onCancel: () => void;
  onCreate: (roleConfig: DataRoleConfig, name: string) => Promise<void>;
}> = ({ file, defaultName, onCancel, onCreate }) => {
  const { data: health } = useHealth();
  const { data: defaults } = useSettingsDefaults();
  const inspect = useUploadInspection();
  const recheck = useUploadInspection();
  const recommend = useMappingRecommendation();
  const recommendPrep = useResampleRecommendation();

  const [step, setStep] = useState(0);
  const [inspection, setInspection] = useState<UploadInspection | null>(null);
  const [checked, setChecked] = useState<UploadInspection | null>(null);
  const [mapping, setMapping] = useState<ColumnMapping | null>(null);
  const [errors, setErrors] = useState<string[]>([]);
  const [gemini, setGemini] = useState<MappingRecommendation | null>(null);
  const [geminiApplied, setGeminiApplied] = useState(false);
  const [geminiError, setGeminiError] = useState<string | null>(null);
  const [roles, setRoles] = useState<Record<string, ChannelRole>>({});
  const [country, setCountry] = useState("DE");
  const [weather, setWeather] = useState(false);
  const [holdout, setHoldout] = useState<number | null>(null);
  const [name, setName] = useState(defaultName);
  const [fatal, setFatal] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  // "Resample & fill": the errors it can fix, the debounced re-check, and Gemini's plan (for the mapping it was asked for)
  const [prepErrors, setPrepErrors] = useState<string[]>([]);
  const [checkedFor, setCheckedFor] = useState("");
  const [prepChecking, setPrepChecking] = useState(false);
  const [geminiPrep, setGeminiPrep] = useState<{ key: string; result: ResampleRecommendation } | null>(null);
  const [geminiPrepApplied, setGeminiPrepApplied] = useState(false);
  const [geminiPrepError, setGeminiPrepError] = useState<string | null>(null);
  const recheckSeq = useRef(0);
  const heading = useRef<HTMLHeadingElement>(null);

  const load = async (sheet?: string) => {
    setFatal(null);
    setErrors([]);
    setGemini(null);
    setGeminiApplied(false);
    setGeminiPrep(null);
    setGeminiPrepApplied(false);
    setPrepErrors([]);
    try {
      const res = await inspect.mutateAsync({ file, sheet });
      setInspection(res);
      setMapping(res.mapping);
      setChecked(null);
    } catch (err) {
      setFatal(errorText(err, "The file could not be read."));
    }
  };

  useEffect(() => {
    void load();
  }, [file]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    heading.current?.focus();
  }, [step]);

  const roleConfig = (m: ColumnMapping): DataRoleConfig => ({ sheet_name: inspection?.selected_sheet, columns: m, channels: [] });

  // Columns → Resample & fill: the server checks the mapping; what only the resampling can fix (gaps, cadence, blanks,
  // repeated dates) moves on to that step, everything else stays here
  const checkMapping = async () => {
    if (!mapping || !inspection) return;
    const missing = missingParts(mapping);
    if (missing.length) {
      setErrors([`Still needed: ${missing.join(", ")}.`]);
      return;
    }
    try {
      const res = await inspect.mutateAsync({ file, sheet: inspection.selected_sheet, roleConfig: roleConfig(mapping) });
      const split = splitErrors(res);
      if (split.mapping.length) {
        setErrors(split.mapping);
        return;
      }
      setErrors([]);
      setChecked(res);
      setCheckedFor(JSON.stringify(mapping));
      setPrepErrors(split.prep);
      setStep(RESAMPLE_STEP);
    } catch (err) {
      setErrors([errorText(err, "The mapping could not be checked.")]);
    }
  };

  // Every change in "Resample & fill" is checked again (debounced), so its table, errors and preview follow
  const mappingJson = mapping ? JSON.stringify(mapping) : "";
  useEffect(() => {
    if (step !== RESAMPLE_STEP || !mapping || !inspection || mappingJson === checkedFor) return;
    const seq = ++recheckSeq.current;
    setPrepChecking(true);
    const timer = window.setTimeout(async () => {
      try {
        const res = await recheck.mutateAsync({ file, sheet: inspection.selected_sheet, roleConfig: roleConfig(mapping) });
        if (seq !== recheckSeq.current) return;
        setChecked(res);
        setCheckedFor(mappingJson);
        const split = splitErrors(res);
        setPrepErrors([...split.prep, ...split.mapping]);
      } catch (err) {
        if (seq === recheckSeq.current) setPrepErrors([errorText(err, "The data could not be checked.")]);
      } finally {
        if (seq === recheckSeq.current) setPrepChecking(false);
      }
    }, 600);
    return () => window.clearTimeout(timer);
  }, [step, mappingJson]); // eslint-disable-line react-hooks/exhaustive-deps

  // Resample & fill → Channels: the profile a run would start with gives the channels and the hold-out
  const toChannels = async () => {
    if (!mapping || !inspection) return;
    recheckSeq.current += 1; // a pending re-check must not overwrite this one
    setPrepChecking(false);
    try {
      const res = await inspect.mutateAsync({ file, sheet: inspection.selected_sheet, roleConfig: roleConfig(mapping) });
      setChecked(res);
      setCheckedFor(JSON.stringify(mapping));
      if (res.errors.length || !res.preview) {
        const split = splitErrors(res);
        setPrepErrors(res.errors.length ? [...split.prep, ...split.mapping] : ["The data could not be checked."]);
        return;
      }
      setPrepErrors([]);
      const g = geminiApplied ? new Map((gemini?.channel_roles ?? []).map((c) => [c.name, c.role])) : new Map<string, ChannelRole>();
      setRoles(Object.fromEntries(res.preview.role_config.channels.map((c) => [c.name, g.get(c.name) ?? c.role])));
      setHoldout(res.preview.default_holdout_days ?? res.preview.role_config.holdout_days ?? 93);
      setStep(CHANNELS_STEP);
    } catch (err) {
      setPrepErrors([errorText(err, "The data could not be checked.")]);
    }
  };

  const askGeminiPrep = async () => {
    if (!inspection || !mapping) return;
    setGeminiPrepError(null);
    try {
      const res = await recommendPrep.mutateAsync({ file, sheet: inspection.selected_sheet, roleConfig: roleConfig(mapping) });
      setGeminiPrep({ key: mappingKey(mapping), result: res });
      setGeminiPrepApplied(false);
    } catch (err) {
      setGeminiPrepError(errorText(err, "Gemini could not propose a preparation."));
    }
  };

  const applyGeminiPrep = () => {
    if (!geminiPrep || !mapping) return;
    setMapping({ ...mapping, resample: geminiPrep.result.resample });
    setGeminiPrepApplied(true);
  };
  // A plan made for another mapping (the user went back and changed a column's role) is not shown
  const prepAdvice = geminiPrep && mapping && geminiPrep.key === mappingKey(mapping) ? geminiPrep.result : null;

  const askGemini = async () => {
    if (!inspection) return;
    setGeminiError(null);
    try {
      const res = await recommend.mutateAsync({ file, sheet: inspection.selected_sheet });
      setGemini(res);
      setGeminiApplied(false);
    } catch (err) {
      setGeminiError(errorText(err, "Gemini could not map the data."));
    }
  };

  const applyGemini = () => {
    if (!gemini || !mapping) return;
    // The columns that keep their role keep their resampling
    setMapping(carryResample(gemini.mapping, mapping));
    if (gemini.country) setCountry(gemini.country);
    setGeminiApplied(true);
    setErrors(gemini.errors);
  };

  const preview = checked?.preview ?? null;
  const maxHoldout = preview?.max_holdout_days ?? 365;
  const holdoutOk = holdout !== null && holdout >= 14 && holdout <= maxHoldout;
  const countries = defaults?.countries ?? [{ code: "DE", name: "Germany" }];
  const countryName = countries.find((c) => c.code === country)?.name ?? country;

  const create = async () => {
    if (!checked || !mapping || !preview || holdout === null) return;
    setCreating(true);
    setFatal(null);
    // The wizard picks no ROI prior: the server chooses it from the data's media share with the final channel roles
    const { roi_prior: _serverPicks, ...modelSettings } = defaults?.model_settings ?? preview.role_config.model_settings!;
    try {
      await onCreate(
        {
          sheet_name: checked.selected_sheet,
          columns: mapping,
          channels: preview.role_config.channels.map((c) => ({ name: c.name, role: roles[c.name] ?? c.role })),
          holdout_days: holdout,
          country,
          model_settings: { ...modelSettings, weather_controls: weather } as ModelSettings,
        },
        name.trim() || defaultName
      );
    } catch (err) {
      setFatal(errorText(err, "The run could not be created."));
      setCreating(false);
    }
  };

  const busy = inspect.isPending;
  const summary = useMemo(() => {
    if (!preview || !mapping) return [];
    const rc = preview.role_config;
    const media = rc.channels.filter((c) => ["paid", "digital_paid", "print_event"].includes(roles[c.name] ?? c.role));
    return [
      ["Data", `${checked?.format === "xlsx" ? checked.selected_sheet : checked?.filename} · ${mapping.layout === "wide" ? "one row per day" : "one row per date and channel"}`],
      ["Period", `${fmtDay(preview.start_date)} – ${fmtDay(preview.end_date)} (${preview.calendar_days} days)`],
      ["Resampling", resampleSummary(checked?.resample, checked?.resample_options)],
      ["Target", mapping.layout === "wide" ? mapping.target ?? "—" : `sum of ${mapping.sales} over the channels`],
      ["Media channels", `${media.length} of ${rc.channels.length}: ${media.map((c) => c.name).join(", ")}`],
      ["Your controls", preview.control_names?.length ? preview.control_names.join(", ") : "none"],
      ["Holidays", `Public holidays of ${countryName}, from Google Calendar`],
    ] as [string, string][];
  }, [preview, mapping, roles, checked, countryName]);

  return (
    <Paper component="section" aria-labelledby="wizard-title" sx={{ p: { xs: 2, sm: 3 } }}>
      <Box sx={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", gap: 2, flexWrap: "wrap", mb: 2 }}>
        <Box sx={{ minWidth: 0 }}>
          <Typography id="wizard-title" variant="h3" component="h2">
            New run from <Box component="span" sx={{ fontFamily: FONT_MONO, fontSize: "0.95em", overflowWrap: "anywhere" }}>{file.name}</Box>
          </Typography>
          <Typography variant="body2">Map your data onto the model in five steps. Nothing is saved until you create the draft.</Typography>
        </Box>
        <Button variant="text" color="secondary" onClick={onCancel} disabled={creating}>
          Cancel
        </Button>
      </Box>

      <Stepper activeStep={step} alternativeLabel sx={{ mb: 3, "& .MuiStepLabel-label": { fontSize: "0.85rem" } }}>
        {STEPS.map((s) => (
          <Step key={s}>
            <StepLabel>{s}</StepLabel>
          </Step>
        ))}
      </Stepper>

      <Typography ref={heading} tabIndex={-1} component="h3" className="visually-hidden">
        Step {step + 1} of {STEPS.length}: {STEPS[step]}
      </Typography>

      {fatal && (
        <Alert severity="error" sx={{ mb: 2 }}>
          {fatal}
        </Alert>
      )}
      {!inspection && !fatal && (
        <Box sx={{ display: "flex", alignItems: "center", gap: 1.5, py: 4 }} aria-live="polite">
          <CircularProgress size={22} color="secondary" aria-hidden="true" /> Reading {file.name}…
        </Box>
      )}

      {inspection && mapping && step === 0 && (
        <Box>
          <Typography variant="body2" sx={{ mb: 1.5 }}>
            {inspection.format.toUpperCase()} · {inspection.sheets.length} sheet{inspection.sheets.length > 1 ? "s" : ""}. The app picked the
            sheet with dates and the most rows. The model needs at least {inspection.min_days} days on its daily grid; hourly, weekly or
            monthly rows are brought onto it in step 3.
          </Typography>
          {inspection.sheets.length > 1 && (
            <RadioGroup
              aria-label="Sheet with the daily data"
              value={inspection.selected_sheet}
              onChange={(e) => void load(e.target.value)}
              sx={{ gap: 0.5 }}
            >
              {inspection.sheets.map((s) => (
                <FormControlLabel
                  key={s.name}
                  value={s.name}
                  disabled={busy}
                  control={<Radio size="small" />}
                  label={
                    <Typography variant="body2">
                      <strong>{s.name}</strong> · <Box component="span" sx={NUMERIC}>{s.rows.toLocaleString("en-GB")} rows × {s.columns} columns</Box>
                      {s.has_date ? " · has dates" : " · no date column"}
                    </Typography>
                  }
                />
              ))}
            </RadioGroup>
          )}
          <Alert severity={inspection.errors.length ? "warning" : "success"} sx={{ mt: 2 }}>
            {inspection.errors.length
              ? splitErrors(inspection).mapping.length
                ? `The app's guess needs your help on the next step: ${splitErrors(inspection).mapping[0]}`
                : `The columns map, and step 3 (Resample & fill) needs your choice: ${inspection.errors[0]}`
              : `Read as a ${inspection.mapping.layout} table: ${
                  inspection.mapping.layout === "wide"
                    ? `target “${inspection.mapping.target}”, ${inspection.mapping.wide_channels.length} channel${inspection.mapping.wide_channels.length === 1 ? "" : "s"}, ${inspection.mapping.controls.length} control${inspection.mapping.controls.length === 1 ? "" : "s"}`
                    : `${inspection.preview?.role_config.channels.length ?? 0} channels in “${inspection.mapping.channel}”`
                }${inspection.preview ? `, ${inspection.preview.calendar_days} days` : ""}.`}
          </Alert>
          <SideTables inspection={inspection} />
        </Box>
      )}

      {inspection && mapping && step === 1 && (
        <ColumnsStep
          inspection={inspection}
          mapping={mapping}
          onChange={(m) => {
            setMapping(m);
            setErrors([]);
          }}
          errors={errors}
          gemini={{
            available: Boolean(health?.explain_available),
            result: gemini,
            loading: recommend.isPending,
            error: geminiError,
            applied: geminiApplied,
            onAsk: () => void askGemini(),
            onApply: applyGemini,
            onSwitchSheet: (s) => {
              setStep(0);
              void load(s);
            },
          }}
        />
      )}

      {checked && mapping && step === RESAMPLE_STEP && (
        <ResampleStep
          inspection={checked}
          mapping={mapping}
          onChange={(m) => {
            setMapping(m);
            setGeminiPrepApplied(false);
          }}
          checking={prepChecking}
          errors={prepErrors}
          gemini={{
            available: Boolean(health?.explain_available),
            result: prepAdvice,
            loading: recommendPrep.isPending,
            error: geminiPrepError,
            applied: geminiPrepApplied,
            onAsk: () => void askGeminiPrep(),
            onApply: applyGeminiPrep,
          }}
        />
      )}

      {preview && step === CHANNELS_STEP && (
        <ChannelsStep preview={preview} roles={roles} onChange={(n, r) => setRoles((prev) => ({ ...prev, [n]: r }))} gemini={geminiApplied ? gemini : null} />
      )}

      {preview && step === CREATE_STEP && (
        <Box sx={{ display: "grid", gap: 3, gridTemplateColumns: { xs: "minmax(0,1fr)", md: "minmax(0,1fr) minmax(0,1fr)" }, alignItems: "start" }}>
          <Box component="dl" sx={{ m: 0, display: "grid", gridTemplateColumns: "max-content minmax(0,1fr)", columnGap: 2, rowGap: 1, "& dt": { fontWeight: 650, color: PALETTE.inkSecondary, fontSize: "0.85rem" }, "& dd": { m: 0, fontSize: "0.9rem", overflowWrap: "anywhere" } }}>
            {summary.map(([k, v]) => (
              <React.Fragment key={k}>
                <dt>{k}</dt>
                <dd>{v}</dd>
              </React.Fragment>
            ))}
          </Box>
          <Box sx={{ display: "grid", gap: 2.25 }}>
            <TextField label="Run name" size="small" value={name} onChange={(e) => setName(e.target.value)} sx={{ "& .MuiInputBase-root": { backgroundColor: "#FFFFFF" } }} />
            <Box>
              <TextField
                select
                label="Country (holidays and weather)"
                size="small"
                value={country}
                onChange={(e) => setCountry(e.target.value)}
                fullWidth
                helperText="Its public holidays come from Google Calendar; its weather, when switched on, from Open-Meteo."
                sx={{ "& .MuiInputBase-root": { backgroundColor: "#FFFFFF" } }}
              >
                {countries.map((c) => (
                  <MenuItem key={c.code} value={c.code}>
                    {c.name}
                  </MenuItem>
                ))}
              </TextField>
              {gemini?.country && (
                <Chip
                  size="small"
                  sx={{ mt: 1 }}
                  label={gemini.country === country ? `Gemini agrees: ${gemini.country_reason}` : `Gemini suggests ${countries.find((c) => c.code === gemini.country)?.name ?? gemini.country}: ${gemini.country_reason}`}
                  onClick={gemini.country === country ? undefined : () => setCountry(gemini.country!)}
                  variant="outlined"
                />
              )}
            </Box>
            <SwitchField
              label="Weather controls"
              checked={weather}
              onChange={setWeather}
              help={`Temperature, sunshine and precipitation vs their seasonal normal in ${countryName} (Open-Meteo, population-weighted). A forecast assumes normal weather.`}
            />
            <NumberField
              label="Hold-out length"
              unit="days"
              value={holdout ?? 14}
              min={14}
              max={maxHoldout}
              onChange={setHoldout}
              helper={`The test window: the last days, hidden from every model to measure a genuine forecast; an equally long validation window sits before it. The default is about an eighth of the data, at most about a quarter. At most ${maxHoldout}: a year of training must remain.`}
              width={220}
            />
          </Box>
          {preview.warnings.length > 0 && (
            <Alert severity="info" sx={{ gridColumn: "1 / -1" }}>
              <Box component="ul" sx={{ m: 0, pl: 2.5 }}>
                {preview.warnings.map((w) => (
                  <li key={w}>{w}</li>
                ))}
              </Box>
            </Alert>
          )}
        </Box>
      )}

      {inspection && (
        <Box sx={{ display: "flex", justifyContent: "space-between", gap: 1.5, mt: 3, pt: 2, borderTop: `1px solid ${PALETTE.borderSubtle}`, flexWrap: "wrap" }}>
          <Button variant="outlined" color="secondary" disabled={step === 0 || creating || busy} onClick={() => setStep((s) => Math.max(0, s - 1))}>
            Back
          </Button>
          {step === 0 && (
            <Button variant="contained" disabled={busy} onClick={() => setStep(1)}>
              Next: columns
            </Button>
          )}
          {step === 1 && (
            <Button variant="contained" disabled={busy} onClick={() => void checkMapping()} startIcon={busy ? <CircularProgress size={16} color="inherit" aria-hidden="true" /> : undefined}>
              {busy ? "Checking the mapping…" : "Next: resample & fill"}
            </Button>
          )}
          {step === RESAMPLE_STEP && (
            <Button variant="contained" disabled={busy} onClick={() => void toChannels()} startIcon={busy ? <CircularProgress size={16} color="inherit" aria-hidden="true" /> : undefined}>
              {busy ? "Checking the data…" : "Next: channels"}
            </Button>
          )}
          {step === CHANNELS_STEP && (
            <Button variant="contained" onClick={() => setStep(CREATE_STEP)}>
              Next: check & create
            </Button>
          )}
          {step === CREATE_STEP && (
            <Button variant="contained" disabled={creating || !holdoutOk} onClick={() => void create()} startIcon={creating ? <CircularProgress size={16} color="inherit" aria-hidden="true" /> : undefined}>
              {creating ? "Creating…" : "Create draft run"}
            </Button>
          )}
        </Box>
      )}
    </Paper>
  );
};
