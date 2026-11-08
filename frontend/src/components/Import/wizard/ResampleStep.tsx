import React, { useId, useMemo, useState } from "react";
import {
  Alert,
  Box,
  Button,
  Chip,
  CircularProgress,
  FormControlLabel,
  MenuItem,
  Radio,
  RadioGroup,
  TextField,
  Typography,
} from "@mui/material";
import AutoAwesomeIcon from "@mui/icons-material/AutoAwesome";
import type {
  ColumnMapping,
  ResampleColumnChoice,
  ResampleColumnReport,
  ResampleOp,
  ResampleOptions,
  ResampleRecommendation,
  UploadInspection,
} from "../../../api/types";
import { FONT_MONO, NUMERIC, PALETTE } from "../../../theme";
import { NumberField, SwitchField } from "../settings/fields";
import {
  activeOps,
  day,
  describeGrid,
  gridFrequency,
  methodLabel,
  previewFigure,
  resampleOf,
  setColumnMethod,
  setFillValue,
  setResample,
} from "./resample";

const LazyPlotlyChart = React.lazy(() => import("../../Charts/PlotlyChart"));

const OP_TITLE: Record<ResampleOp, string> = {
  aggregate: "Several rows a day →",
  disaggregate: "Week / month → days",
  fill: "Blanks and missing dates",
};
const KIND_LABEL = { flow: "amount", level: "level", flag: "0/1 flag", category: "category" } as const;
const nf = (n: number) => n.toLocaleString("en-GB");

export interface ResampleGemini {
  available: boolean;
  result: ResampleRecommendation | null;
  loading: boolean;
  error: string | null;
  applied: boolean;
  onAsk: () => void;
  onApply: () => void;
}

/** Gemini's preparation plan: its frequency and per-column methods, what the server refused, and the apply button. */
const GeminiResamplePanel: React.FC<ResampleGemini & { options?: ResampleOptions }> = ({ available, result, loading, error, applied, onAsk, onApply, options }) => {
  const freq = result ? options?.frequencies.find((f) => f.value === result.resample.frequency)?.label ?? result.resample.frequency : "";
  return (
    <Box
      component="section"
      aria-labelledby="gemini-resample-title"
      sx={{ border: `1px solid ${PALETTE.borderSubtle}`, borderRadius: 1, backgroundColor: PALETTE.bgAccentTint, p: { xs: 2, sm: 2.5 }, mb: 2.5 }}
    >
      <Box sx={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: 2, flexWrap: "wrap" }}>
        <Box sx={{ maxWidth: "70ch" }}>
          <Typography id="gemini-resample-title" component="h3" sx={{ fontWeight: 700, display: "flex", alignItems: "center", gap: 0.75 }}>
            <AutoAwesomeIcon fontSize="small" sx={{ color: PALETTE.brandPrimary }} aria-hidden="true" /> Gemini data preparation
          </Typography>
          <Typography variant="body2">
            Gemini reads how your dates are spaced and, for every mapped column, its gaps, zeros, steps and weekly pattern, then proposes the
            frequency and the best way to combine, split and fill each column. Every choice is checked against your data; nothing changes until
            you apply it.
          </Typography>
        </Box>
        <Button
          variant={result ? "outlined" : "contained"}
          color={result ? "secondary" : "primary"}
          onClick={onAsk}
          disabled={!available || loading}
          startIcon={loading ? <CircularProgress size={16} color="inherit" aria-hidden="true" /> : <AutoAwesomeIcon />}
        >
          {loading ? "Gemini is reading the columns…" : result ? "Ask again" : "Propose with Gemini"}
        </Button>
      </Box>
      {!available && (
        <Alert severity="info" sx={{ mt: 1.5 }}>
          Gemini is not switched on for this server. Choose the methods below yourself; every column starts on its default.
        </Alert>
      )}
      {error && (
        <Alert severity="error" sx={{ mt: 1.5 }}>
          {error}
        </Alert>
      )}
      {result && (
        <Box sx={{ mt: 1.75, display: "grid", gap: 1.25 }} aria-live="polite">
          <Typography variant="body2" sx={{ color: PALETTE.inkPrimary }}>
            {result.summary}
          </Typography>
          <Box component="dl" sx={{ m: 0, display: "grid", gridTemplateColumns: "max-content minmax(0,1fr)", columnGap: 1.5, rowGap: 0.5, "& dt": { fontWeight: 650, fontSize: "0.85rem" }, "& dd": { m: 0, fontSize: "0.85rem", color: PALETTE.inkSecondary } }}>
            <dt>Frequency</dt>
            <dd>
              {freq}
              {result.resample.frequency === "weekly" ? ` (dates mark the week's ${result.resample.period_label === "end" ? "last" : "first"} day)` : ""}: {result.frequency_reason}
              {result.period_label_reason ? ` ${result.period_label_reason}` : ""}
            </dd>
            <dt>Missing dates</dt>
            <dd>
              {result.resample.fill_missing_dates ? "Fill them" : "Leave them"}: {result.missing_dates_reason}
            </dd>
          </Box>
          {result.notes.length > 0 && (
            <Box component="ul" sx={{ m: 0, pl: 2.5, "& li": { fontSize: "0.85rem", color: PALETTE.inkSecondary } }}>
              {result.notes.map((n) => (
                <li key={n}>{n}</li>
              ))}
            </Box>
          )}
          {result.rejected.length > 0 && (
            <Alert severity="warning">
              Not applied, because the data does not allow it:
              <Box component="ul" sx={{ m: 0, pl: 2.5 }}>
                {result.rejected.map((r) => (
                  <li key={r.item}>
                    {r.item}: {r.reason}
                  </li>
                ))}
              </Box>
            </Alert>
          )}
          {result.errors.length > 0 && (
            <Alert severity="warning">
              With Gemini's plan the data still cannot run: {result.errors.join(" ")}
            </Alert>
          )}
          {result.unverified_numbers.length > 0 && (
            <Typography variant="caption" sx={{ color: PALETTE.semanticWarn }}>
              Numbers in Gemini's text that are not in the facts it was given: {result.unverified_numbers.join(", ")}.
            </Typography>
          )}
          <Box sx={{ display: "flex", alignItems: "center", gap: 1.5, flexWrap: "wrap" }}>
            <Button variant="contained" onClick={onApply} disabled={applied} startIcon={applied ? undefined : <AutoAwesomeIcon />}>
              {applied ? "Gemini's plan is applied" : "Apply Gemini's plan"}
            </Button>
            <Typography variant="caption" sx={{ color: PALETTE.inkMuted }}>
              {result.model} · {result.cached ? "cached answer" : "new answer"}
              {result.unreviewed.length > 0 ? ` · kept on their defaults: ${result.unreviewed.join(", ")}` : ""}
            </Typography>
          </Box>
        </Box>
      )}
    </Box>
  );
};

/** One method choice of one column: "Default (…)" first, then every method the column allows, each with its help. */
const MethodSelect: React.FC<{
  op: ResampleOp;
  choice: ResampleColumnChoice;
  value: string | null | undefined;
  options?: ResampleOptions;
  onChange: (value: string | null) => void;
}> = ({ op, choice, value, options, onChange }) => {
  const defaultLabel = methodLabel(options, op, choice.defaults[op]);
  const allowed = choice.allowed[op];
  return (
    <TextField
      select
      size="small"
      label={OP_TITLE[op]}
      value={value ?? ""}
      onChange={(e) => onChange(e.target.value || null)}
      disabled={allowed.length <= 1}
      InputLabelProps={{ shrink: true }}
      SelectProps={{
        displayEmpty: true,
        renderValue: (v) => (v ? methodLabel(options, op, String(v)) : `Default · ${defaultLabel}`),
        MenuProps: { PaperProps: { sx: { maxWidth: 420 } } },
        // Every row's select has the same visible label: name the combobox after its column instead
        SelectDisplayProps: { "aria-labelledby": undefined, "aria-label": `${OP_TITLE[op]} for ${choice.column}` } as React.HTMLAttributes<HTMLDivElement>,
      }}
      sx={{ minWidth: 200, width: "100%", "& .MuiInputBase-root": { backgroundColor: "#FFFFFF" } }}
    >
      <MenuItem value="">
        <Box>
          <Typography sx={{ fontSize: "0.88rem", fontWeight: 600 }}>Default · {defaultLabel}</Typography>
          <Typography variant="caption" sx={{ color: PALETTE.inkSecondary, whiteSpace: "normal" }}>
            What MMM Studio does with {choice.label} unless you choose otherwise
          </Typography>
        </Box>
      </MenuItem>
      {allowed.map((v) => {
        const o = options?.[op].find((x) => x.value === v);
        return (
          <MenuItem key={v} value={v}>
            <Box>
              <Typography sx={{ fontSize: "0.88rem", fontWeight: 600 }}>{o?.label ?? v}</Typography>
              {o?.help && (
                <Typography variant="caption" sx={{ color: PALETTE.inkSecondary, whiteSpace: "normal" }}>
                  {o.help}
                </Typography>
              )}
            </Box>
          </MenuItem>
        );
      })}
    </TextField>
  );
};

const resultText = (r: ResampleColumnReport | undefined, unit: string): string => {
  if (!r) return "—";
  const bits: string[] = [];
  if (r.rows_merged) bits.push(`${nf(r.rows_merged)} rows combined`);
  if (r.filled) bits.push(`${nf(r.filled)} ${unit}${r.filled === 1 ? "" : "s"} filled`);
  if (r.nearest_fallback) bits.push(`${nf(r.nearest_fallback)} at the edges took the nearest value`);
  if (r.spread_values) bits.push(`${nf(r.spread_values)} values spread`);
  if (r.fill === "none" && (r.blank || r.added)) bits.push(`${nf(r.blank + r.added)} left blank`);
  return bits.length ? bits.join(" · ") : "Nothing to fill";
};

/**
 * The wizard's "Resample & fill" step: how the dates are read (and whether missing dates are added), and per mapped
 * column how several rows of a day are combined, how a week or a month is split into days and how blanks are filled.
 * Every change is re-checked on the server (debounced in the wizard); the preview chart shows the daily series the
 * model will read, with the imputed days marked.
 */
export const ResampleStep: React.FC<{
  inspection: UploadInspection;
  mapping: ColumnMapping;
  onChange: (m: ColumnMapping) => void;
  checking: boolean;
  errors: string[];
  gemini: ResampleGemini;
}> = ({ inspection, mapping, onChange, checking, errors, gemini }) => {
  const r = resampleOf(mapping);
  const options = inspection.resample_options;
  const report = inspection.resample ?? null;
  const grid = inspection.time_grid ?? null;
  const freq = gridFrequency(mapping, grid, report);
  const ops = activeOps(freq, grid?.detected, report?.grid.merged_rows);
  const choices = inspection.resample_columns ?? [];
  const reports = useMemo(() => new Map((report?.columns ?? []).map((c) => [c.column, c])), [report]);
  const proposed = useMemo(() => new Map((gemini.result?.resample.columns ?? []).map((c) => [c.column, c])), [gemini.result]);
  const unit = freq === "weekly" ? "week" : freq === "monthly" ? "month" : "day";
  const long = mapping.layout === "long";
  const captionId = useId();
  const statusId = useId();

  const detectedLabel = options?.frequencies.find((f) => f.value === grid?.detected)?.label ?? grid?.detected ?? "unknown";
  const previewable = report?.series.columns ?? [];
  const [previewColumn, setPreviewColumn] = useState<string>("");
  const shown =
    previewable.find((s) => s.column === previewColumn)?.column ??
    previewable.find((s) => s.imputed.length > 0)?.column ??
    previewable[0]?.column ??
    "";
  const figure = report && shown ? previewFigure(report, shown) : null;
  const shownReport = reports.get(shown);

  return (
    <Box>
      <GeminiResamplePanel {...gemini} options={options} />

      <Box component="section" aria-labelledby="resample-dates-title" sx={{ border: `1px solid ${PALETTE.borderSubtle}`, borderRadius: 1, p: { xs: 2, sm: 2.5 }, mb: 2.5, backgroundColor: "#FFFFFF" }}>
        <Typography id="resample-dates-title" component="h3" sx={{ fontWeight: 700 }}>
          Dates
        </Typography>
        <Typography variant="body2" sx={{ mb: 1.75, ...NUMERIC }}>
          {describeGrid(grid)}. MMM Studio models one row per day: finer rows are combined and coarser rows split into days, column by column.
        </Typography>
        <Box sx={{ display: "grid", gap: 2, gridTemplateColumns: { xs: "minmax(0,1fr)", md: "minmax(0,280px) minmax(0,1fr)" }, alignItems: "start" }}>
          <TextField
            select
            size="small"
            label="Read the dates as"
            value={r.frequency}
            onChange={(e) => onChange(setResample(mapping, { frequency: e.target.value as typeof r.frequency }))}
            helperText={options?.frequencies.find((f) => f.value === r.frequency)?.help}
            sx={{ "& .MuiInputBase-root": { backgroundColor: "#FFFFFF" } }}
          >
            {(options?.frequencies ?? [{ value: "auto", label: "Detect from the dates", help: "" }]).map((f) => (
              <MenuItem key={f.value} value={f.value}>
                {f.value === "auto" ? `Detect (${detectedLabel})` : f.label}
              </MenuItem>
            ))}
          </TextField>
          <Box sx={{ display: "grid", gap: 1.5 }}>
            {freq === "weekly" && (
              <Box>
                <Typography id="period-label-title" sx={{ fontWeight: 650, fontSize: "0.9rem" }}>
                  Each weekly date is the week's
                </Typography>
                <RadioGroup
                  row
                  aria-labelledby="period-label-title"
                  value={r.period_label}
                  onChange={(e) => onChange(setResample(mapping, { period_label: e.target.value as "start" | "end" }))}
                >
                  <FormControlLabel value="start" control={<Radio size="small" />} label="first day" />
                  <FormControlLabel value="end" control={<Radio size="small" />} label="last day (week ending)" />
                </RadioGroup>
              </Box>
            )}
            <SwitchField
              label="Fill missing dates"
              checked={r.fill_missing_dates}
              disabled={long}
              onChange={(v) => onChange(setResample(mapping, { fill_missing_dates: v }))}
              help={
                long
                  ? "Only for a wide table. In a long table a missing channel-day stays missing and the model flags it with missing-row controls."
                  : `${grid?.missing_dates ? `${nf(grid.missing_dates)} ${unit}${grid.missing_dates === 1 ? " is" : "s are"} missing. ` : "No dates are missing. "}When on, each missing ${unit} gets a row whose cells are filled like blanks, with each column's method below; when off, a gap stops the run.`
              }
            />
          </Box>
        </Box>
        {report && (
          <Typography variant="body2" sx={{ mt: 1.5, color: PALETTE.inkSecondary, ...NUMERIC }}>
            Model grid: <strong>{nf(report.grid.days)} days</strong>, {day(report.grid.first_day)} – {day(report.grid.last_day)}
            {report.grid.added ? ` · ${nf(report.grid.added)} ${unit}${report.grid.added === 1 ? "" : "s"} added` : ""}
            {report.grid.merged_rows ? ` · ${nf(report.grid.merged_rows)} rows combined into ${unit}s` : ""}
          </Typography>
        )}
      </Box>

      <Typography id={statusId} variant="caption" component="p" aria-live="polite" sx={{ color: PALETTE.inkMuted, minHeight: "1.4em", mb: 0.5, display: "flex", alignItems: "center", gap: 1 }}>
        {checking ? (
          <>
            <CircularProgress size={12} color="inherit" aria-hidden="true" /> Checking the data with your choices…
          </>
        ) : errors.length ? (
          "The data cannot reach the daily grid yet."
        ) : report ? (
          `Checked: ${nf(report.grid.days)} days, ${nf(report.columns.reduce((a, c) => a + c.filled, 0))} values imputed.`
        ) : (
          ""
        )}
      </Typography>

      {errors.length > 0 && (
        <Alert severity="error" sx={{ mb: 1.5 }}>
          {errors.length === 1 ? (
            errors[0]
          ) : (
            <Box component="ul" sx={{ m: 0, pl: 2.5 }}>
              {errors.map((e) => (
                <li key={e}>{e}</li>
              ))}
            </Box>
          )}
        </Alert>
      )}

      <Box
        role="region"
        aria-labelledby={captionId}
        tabIndex={0}
        sx={{
          containerType: "inline-size",
          overflowX: "auto",
          border: `1px solid ${PALETTE.borderSubtle}`,
          borderRadius: 1,
          "& table": { width: "100%", borderCollapse: "collapse" },
          "& caption": { textAlign: "left", px: 1.5, py: 1.25, fontWeight: 700, color: PALETTE.inkPrimary, captionSide: "top" },
          "& th, & td": { textAlign: "left", verticalAlign: "top", px: 1.5, py: 1.1, borderBottom: `1px solid ${PALETTE.borderSubtle}` },
          "& thead th": { fontSize: "0.78rem", fontWeight: 650, color: PALETTE.inkSecondary, backgroundColor: PALETTE.bgSubtle },
          "@container (max-width: 699.95px)": {
            "& thead": { display: "none" },
            "& tbody tr": { display: "block", borderBottom: `1px solid ${PALETTE.borderSubtle}`, py: 1 },
            "& tbody th, & tbody td": { display: "block", borderBottom: "none", py: 0.6 },
            "& td[data-label]::before": { content: "attr(data-label) ': '", fontWeight: 650, color: PALETTE.inkSecondary, fontSize: "0.78rem" },
          },
        }}
      >
        <table>
          <caption id={captionId}>How each column reaches the daily grid</caption>
          <thead>
            <tr>
              <th scope="col">Column</th>
              <th scope="col">Gaps</th>
              {ops.map((op) => (
                <th key={op} scope="col">
                  {OP_TITLE[op]}
                </th>
              ))}
              <th scope="col">Result</th>
              {gemini.result && <th scope="col">Gemini</th>}
            </tr>
          </thead>
          <tbody>
            {choices.map((c) => {
              const entry = r.columns.find((e) => e.column === c.column);
              const rep = reports.get(c.column);
              const note = gemini.result?.column_notes[c.column];
              const prop = proposed.get(c.column);
              const differs = gemini.result && !gemini.applied ? ops.filter((op) => (prop?.[op] ?? null) !== (entry?.[op] ?? null)) : [];
              return (
                <tr key={c.column}>
                  <th scope="row" style={{ fontWeight: 600 }}>
                    <Box component="span" sx={{ fontFamily: FONT_MONO, fontSize: "0.82rem", color: PALETTE.inkPrimary, overflowWrap: "anywhere", display: "block" }}>
                      {c.column}
                    </Box>
                    <Box sx={{ display: "flex", gap: 0.75, mt: 0.5, flexWrap: "wrap" }}>
                      <Chip size="small" variant="outlined" label={c.label} sx={{ height: 20, fontSize: "0.7rem" }} />
                      <Chip size="small" variant="outlined" label={KIND_LABEL[c.kind]} sx={{ height: 20, fontSize: "0.7rem" }} />
                    </Box>
                  </th>
                  <td data-label="Gaps">
                    <Typography variant="caption" sx={{ color: PALETTE.inkSecondary, ...NUMERIC }}>
                      {c.blank ? `${nf(c.blank)} blank` : "no blanks"}
                      {rep?.added ? ` · ${nf(rep.added)} added ${unit}${rep.added === 1 ? "" : "s"}` : ""}
                    </Typography>
                  </td>
                  {ops.map((op) => (
                    <td key={op}>
                      <MethodSelect
                        op={op}
                        choice={c}
                        value={entry?.[op] ?? null}
                        options={options}
                        onChange={(v) => onChange(setColumnMethod(mapping, c.column, op, v))}
                      />
                      {op === "fill" && entry?.fill === "constant" && (
                        <Box sx={{ mt: 1 }}>
                          <NumberField
                            label="Fill value"
                            value={entry.fill_value ?? 0}
                            integer={false}
                            min={c.kind === "flag" ? 0 : undefined}
                            max={c.kind === "flag" ? 1 : undefined}
                            onChange={(v) => onChange(setFillValue(mapping, c.column, v))}
                            inputLabel={`Fill value for ${c.column}`}
                            width="100%"
                          />
                        </Box>
                      )}
                    </td>
                  ))}
                  <td data-label="Result">
                    <Typography variant="caption" sx={{ color: PALETTE.inkSecondary, ...NUMERIC }}>
                      {resultText(rep, unit)}
                    </Typography>
                  </td>
                  {gemini.result && (
                    <td data-label="Gemini">
                      {note ? (
                        <Typography variant="caption" component="p" sx={{ color: differs.length ? PALETTE.semanticWarn : PALETTE.inkSecondary, maxWidth: "42ch" }}>
                          {differs.length > 0 && (
                            <strong>
                              Gemini:{" "}
                              {differs
                                .map((op) => (prop?.[op] ? methodLabel(options, op, prop[op]) : `default (${methodLabel(options, op, c.defaults[op])})`))
                                .join(", ")}
                              .{" "}
                            </strong>
                          )}
                          {note.reason}
                        </Typography>
                      ) : (
                        <Typography variant="caption" sx={{ color: PALETTE.inkMuted }}>
                          —
                        </Typography>
                      )}
                    </td>
                  )}
                </tr>
              );
            })}
          </tbody>
        </table>
      </Box>

      {report && previewable.length > 0 && (
        <Box component="section" aria-labelledby="resample-preview-title" sx={{ mt: 2.5, border: `1px solid ${PALETTE.borderSubtle}`, borderRadius: 1, p: { xs: 2, sm: 2.5 }, backgroundColor: "#FFFFFF" }}>
          <Box sx={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: 2, flexWrap: "wrap", mb: 1 }}>
            <Box sx={{ maxWidth: "62ch" }}>
              <Typography id="resample-preview-title" component="h3" sx={{ fontWeight: 700 }}>
                Preview on the daily grid
              </Typography>
              <Typography variant="body2">
                The daily values the model will read{shownReport?.filled ? `; the ${nf(shownReport.imputed_days)} imputed days are marked` : ""}. Change a
                method above and the preview follows.
              </Typography>
            </Box>
            <TextField
              select
              size="small"
              label="Column"
              value={shown}
              onChange={(e) => setPreviewColumn(e.target.value)}
              sx={{ minWidth: 220, maxWidth: "100%", "& .MuiInputBase-root": { backgroundColor: "#FFFFFF" } }}
            >
              {previewable.map((s) => (
                <MenuItem key={s.column} value={s.column}>
                  {s.column}
                  {s.imputed.length ? ` · ${nf(s.imputed.length)} imputed` : ""}
                </MenuItem>
              ))}
            </TextField>
          </Box>
          {figure && (
            <React.Suspense fallback={<Box sx={{ height: 300 }} aria-hidden="true" />}>
              <LazyPlotlyChart
                data={figure.data}
                layout={figure.layout}
                height={300}
                viewKey={shown}
                ariaLabel={`Daily values of ${shown} after resampling, ${shownReport?.imputed_days ?? 0} imputed days marked`}
              />
            </React.Suspense>
          )}
        </Box>
      )}
    </Box>
  );
};
