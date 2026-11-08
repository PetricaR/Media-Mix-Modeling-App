import React from "react";
import {
  Alert,
  Box,
  Button,
  Chip,
  CircularProgress,
  MenuItem,
  TextField,
  ToggleButton,
  ToggleButtonGroup,
  Typography,
} from "@mui/material";
import AutoAwesomeIcon from "@mui/icons-material/AutoAwesome";
import type { ColumnMapping, ColumnProfile, MappingRecommendation, MappingRole, UploadInspection } from "../../../api/types";
import { FONT_MONO, PALETTE } from "../../../theme";
import {
  LONG_ROLES,
  WIDE_ROLES,
  assignColumn,
  columnAssignment,
  describeColumn,
  renameChannel,
  renameControl,
  switchLayout,
} from "./mapping";

const ROLE_TONE: Partial<Record<MappingRole, string>> = {
  date: PALETTE.inkSecondary,
  target: PALETTE.semanticGood,
  spend: PALETTE.brandPrimary,
  costs: PALETTE.brandPrimary,
  sales: PALETTE.semanticGood,
  control: PALETTE.semanticWarn,
};

/** Gemini's mapping: what it found, what the server refused, and the button that applies it to the table. */
const GeminiMappingPanel: React.FC<{
  available: boolean;
  result: MappingRecommendation | null;
  loading: boolean;
  error: string | null;
  applied: boolean;
  onAsk: () => void;
  onApply: () => void;
  onSwitchSheet: (sheet: string) => void;
}> = ({ available, result, loading, error, applied, onAsk, onApply, onSwitchSheet }) => (
  <Box
    component="section"
    aria-labelledby="gemini-mapping-title"
    sx={{ border: `1px solid ${PALETTE.borderSubtle}`, borderRadius: 1, backgroundColor: PALETTE.bgAccentTint, p: { xs: 2, sm: 2.5 }, mb: 2.5 }}
  >
    <Box sx={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: 2, flexWrap: "wrap" }}>
      <Box sx={{ maxWidth: "70ch" }}>
        <Typography id="gemini-mapping-title" component="h3" sx={{ fontWeight: 700, display: "flex", alignItems: "center", gap: 0.75 }}>
          <AutoAwesomeIcon fontSize="small" sx={{ color: PALETTE.brandPrimary }} aria-hidden="true" /> Gemini data assistant
        </Typography>
        <Typography variant="body2">
          Gemini reads every column, the first rows and the other sheets, and proposes the date, the target, each channel, the controls
          and the country. Each item is checked against your data before you see it; nothing changes until you apply it.
        </Typography>
      </Box>
      <Button
        variant={result ? "outlined" : "contained"}
        color={result ? "secondary" : "primary"}
        onClick={onAsk}
        disabled={!available || loading}
        startIcon={loading ? <CircularProgress size={16} color="inherit" aria-hidden="true" /> : <AutoAwesomeIcon />}
      >
        {loading ? "Gemini is reading the data…" : result ? "Ask again" : "Map with Gemini"}
      </Button>
    </Box>
    {!available && (
      <Alert severity="info" sx={{ mt: 1.5 }}>
        Gemini is not switched on for this server (MMM_GEMINI_API_KEY, or MMM_GEMINI_VERTEX with a GCP project). Map the columns below
        yourself; the app's own guess is already filled in.
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
        {result.notes.length > 0 && (
          <Box component="ul" sx={{ m: 0, pl: 2.5, "& li": { fontSize: "0.85rem", color: PALETTE.inkSecondary } }}>
            {result.notes.map((n) => (
              <li key={n}>{n}</li>
            ))}
          </Box>
        )}
        {result.better_sheet && (
          <Alert
            severity="info"
            action={
              <Button color="inherit" size="small" onClick={() => onSwitchSheet(result.better_sheet!)}>
                Use “{result.better_sheet}”
              </Button>
            }
          >
            Gemini suggests the sheet “{result.better_sheet}”: {result.sheet_reason}
          </Alert>
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
        {result.unverified_numbers.length > 0 && (
          <Typography variant="caption" sx={{ color: PALETTE.semanticWarn }}>
            Numbers in Gemini's text that are not in your data: {result.unverified_numbers.join(", ")}.
          </Typography>
        )}
        <Box sx={{ display: "flex", alignItems: "center", gap: 1.5, flexWrap: "wrap" }}>
          <Button variant="contained" onClick={onApply} disabled={applied} startIcon={applied ? undefined : <AutoAwesomeIcon />}>
            {applied ? "Gemini's mapping is applied" : "Apply Gemini's mapping"}
          </Button>
          <Typography variant="caption" sx={{ color: PALETTE.inkMuted }}>
            {result.model} · {result.cached ? "cached answer" : "new answer"}
            {result.unreviewed.length > 0 ? ` · not placed: ${result.unreviewed.join(", ")}` : ""}
          </Typography>
        </Box>
      </Box>
    )}
  </Box>
);

const RoleSelect: React.FC<{ column: string; categories?: string[]; mapping: ColumnMapping; onChange: (m: ColumnMapping) => void }> = ({ column, categories, mapping, onChange }) => {
  const a = columnAssignment(mapping, column);
  const roles = mapping.layout === "wide" ? WIDE_ROLES : LONG_ROLES;
  const metric = mapping.layout === "wide" && (a.role === "impressions" || a.role === "clicks" || a.role === "sales");
  return (
    <Box sx={{ display: "flex", gap: 1, flexWrap: "wrap" }}>
      <TextField
        select
        size="small"
        label="Role"
        value={a.role}
        onChange={(e) => {
          const role = e.target.value as MappingRole;
          const first = mapping.wide_channels.find((c) => c.spend !== column)?.name;
          onChange(assignColumn(mapping, column, role, first, categories));
        }}
        sx={{ minWidth: 190, "& .MuiInputBase-root": { backgroundColor: "#FFFFFF" } }}
        inputProps={{ "aria-label": `Role of column ${column}` }}
      >
        {roles.map((r) => (
          <MenuItem key={r.value} value={r.value} disabled={mapping.layout === "wide" && ["impressions", "clicks", "sales"].includes(r.value) && !mapping.wide_channels.some((c) => c.spend !== column)}>
            {r.label}
          </MenuItem>
        ))}
      </TextField>
      {a.role === "spend" && (
        <TextField
          size="small"
          label="Channel name"
          value={a.channel ?? ""}
          onChange={(e) => onChange(renameChannel(mapping, a.channel ?? "", e.target.value))}
          sx={{ minWidth: 240, "& .MuiInputBase-root": { backgroundColor: "#FFFFFF" } }}
        />
      )}
      {a.role === "control" && (
        <TextField
          size="small"
          label="Control name"
          value={a.name ?? ""}
          onChange={(e) => onChange(renameControl(mapping, column, e.target.value))}
          sx={{ minWidth: 180, "& .MuiInputBase-root": { backgroundColor: "#FFFFFF" } }}
        />
      )}
      {metric && (
        <TextField
          select
          size="small"
          label="Of channel"
          value={a.channel ?? ""}
          onChange={(e) => onChange(assignColumn(assignColumn(mapping, column, "ignore"), column, a.role, e.target.value))}
          sx={{ minWidth: 180, "& .MuiInputBase-root": { backgroundColor: "#FFFFFF" } }}
        >
          {mapping.wide_channels.map((c) => (
            <MenuItem key={c.name} value={c.name}>
              {c.name}
            </MenuItem>
          ))}
        </TextField>
      )}
    </Box>
  );
};

export const ColumnsStep: React.FC<{
  inspection: UploadInspection;
  mapping: ColumnMapping;
  onChange: (m: ColumnMapping) => void;
  gemini: {
    available: boolean;
    result: MappingRecommendation | null;
    loading: boolean;
    error: string | null;
    applied: boolean;
    onAsk: () => void;
    onApply: () => void;
    onSwitchSheet: (sheet: string) => void;
  };
  errors: string[];
}> = ({ inspection, mapping, onChange, gemini, errors }) => {
  const notes = gemini.applied || gemini.result ? gemini.result?.column_notes ?? {} : {};
  const counts = {
    channels: mapping.layout === "wide" ? mapping.wide_channels.length : null,
    controls: mapping.controls.length,
  };
  return (
    <Box>
      <GeminiMappingPanel {...gemini} />

      <Box sx={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 2, flexWrap: "wrap", mb: 1.5 }}>
        <Box>
          <Typography component="h3" sx={{ fontWeight: 700 }}>
            Columns of “{inspection.format === "xlsx" ? inspection.selected_sheet : inspection.filename}”
          </Typography>
          <Typography variant="body2">
            {mapping.layout === "wide"
              ? `One row per day: pick the target, the channels' spend and the controls${counts.channels !== null ? ` (now ${counts.channels} channel${counts.channels === 1 ? "" : "s"}, ${counts.controls} control${counts.controls === 1 ? "" : "s"})` : ""}.`
              : "One row per date and channel: pick the channel, its sales and its costs."}
          </Typography>
        </Box>
        <ToggleButtonGroup
          size="small"
          exclusive
          value={mapping.layout}
          onChange={(_, v) => v && onChange(switchLayout(mapping, v))}
          aria-label="Table layout"
          sx={{ "& .MuiToggleButton-root": { textTransform: "none", fontWeight: 600 } }}
        >
          <ToggleButton value="wide">Wide · one row per day</ToggleButton>
          <ToggleButton value="long">Long · one row per date × channel</ToggleButton>
        </ToggleButtonGroup>
      </Box>

      {errors.length > 0 && (
        <Alert severity="error" sx={{ mb: 1.5 }}>
          {errors.length === 1 ? errors[0] : (
            <Box component="ul" sx={{ m: 0, pl: 2.5 }}>
              {errors.map((e) => (
                <li key={e}>{e}</li>
              ))}
            </Box>
          )}
        </Alert>
      )}

      <Box role="region" aria-label="Column mapping" tabIndex={0} sx={{ overflowX: "auto", border: `1px solid ${PALETTE.borderSubtle}`, borderRadius: 1 }}>
        <Box component="table" sx={{ width: "100%", borderCollapse: "collapse", "& th, & td": { textAlign: "left", verticalAlign: "top", px: 1.5, py: 1.1, borderBottom: `1px solid ${PALETTE.borderSubtle}` }, "& th": { fontSize: "0.78rem", fontWeight: 650, color: PALETTE.inkSecondary, backgroundColor: PALETTE.bgSubtle }, "@media (max-width: 599.95px)": { "& thead": { display: "none" }, "& tbody tr": { display: "block", borderBottom: `1px solid ${PALETTE.borderSubtle}`, py: 1 }, "& tbody th, & tbody td": { display: "block", borderBottom: "none", py: 0.5, textAlign: "left !important" } } }}>
          <thead>
            <tr>
              <th scope="col">Column</th>
              <th scope="col">Values</th>
              <th scope="col">Role in the model</th>
              {gemini.result && <th scope="col">Gemini</th>}
            </tr>
          </thead>
          <tbody>
            {inspection.columns.map((c: ColumnProfile) => {
              const a = columnAssignment(mapping, c.name);
              const note = notes[c.name];
              const agrees = note && (note.role === a.role || (note.role === "spend" && a.role === "spend"));
              return (
                <tr key={c.name}>
                  <th scope="row" style={{ background: "transparent", fontWeight: 600 }}>
                    <Box sx={{ display: "flex", alignItems: "center", gap: 1, flexWrap: "wrap" }}>
                      <Box component="span" sx={{ fontFamily: FONT_MONO, fontSize: "0.82rem", color: PALETTE.inkPrimary, overflowWrap: "anywhere" }}>
                        {c.name}
                      </Box>
                      <Chip size="small" label={c.kind} variant="outlined" sx={{ height: 20, fontSize: "0.7rem" }} />
                      {a.role !== "ignore" && (
                        <Box component="span" aria-hidden="true" sx={{ width: 8, height: 8, borderRadius: "50%", backgroundColor: ROLE_TONE[a.role] ?? PALETTE.inkMuted }} />
                      )}
                    </Box>
                  </th>
                  <td>
                    <Typography variant="caption" sx={{ color: PALETTE.inkSecondary }}>
                      {describeColumn(c)}
                    </Typography>
                  </td>
                  <td>
                    <RoleSelect column={c.name} categories={c.categories} mapping={mapping} onChange={onChange} />
                  </td>
                  {gemini.result && (
                    <td>
                      {note ? (
                        <Typography variant="caption" component="p" sx={{ color: agrees ? PALETTE.inkSecondary : PALETTE.semanticWarn, maxWidth: "42ch" }}>
                          {!agrees && <strong>Gemini: {note.role}{note.channel ? ` (${note.channel})` : ""}. </strong>}
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
        </Box>
      </Box>
    </Box>
  );
};
