import React, { useEffect, useId, useState } from "react";
import { Alert, Box, Chip, FormControlLabel, InputAdornment, Radio, Switch, TextField, Typography, alpha } from "@mui/material";
import { PALETTE } from "../../../theme";

export const GUTTER = { xs: 2.5, sm: 3 };

export const ChangedTag: React.FC = () => (
  <Chip size="small" variant="outlined" color="primary" label="Changed" sx={{ height: 20, fontSize: "0.7rem" }} />
);

export const Dot: React.FC<{ colour: string; hollow?: boolean }> = ({ colour, hollow }) => (
  <Box
    component="span"
    aria-hidden="true"
    sx={{ flexShrink: 0, width: 9, height: 9, boxSizing: "border-box", borderRadius: "50%", border: `2px solid ${colour}`, backgroundColor: hollow ? "transparent" : colour }}
  />
);

/** One block of settings inside a tab, with a "Changed" tag when it differs from the notebook defaults. */
export const Group: React.FC<{
  title: string;
  description?: React.ReactNode;
  changed?: boolean;
  aside?: React.ReactNode;
  first?: boolean;
  children: React.ReactNode;
}> = ({ title, description, changed, aside, first, children }) => (
  <Box component="section" sx={{ px: GUTTER, py: 2.5, borderTop: first ? "none" : `1px solid ${PALETTE.borderSubtle}` }}>
    <Box sx={{ display: "flex", alignItems: "flex-end", justifyContent: "space-between", flexWrap: "wrap", gap: 1.5, mb: 1.75 }}>
      <Box sx={{ maxWidth: "72ch" }}>
        <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
          <Typography component="h3" sx={{ fontWeight: 700, fontSize: "0.95rem", color: PALETTE.inkPrimary }}>
            {title}
          </Typography>
          {changed && <ChangedTag />}
        </Box>
        {description && <Typography variant="body2">{description}</Typography>}
      </Box>
      {aside}
    </Box>
    {children}
  </Box>
);

/** Shown in the Meridian tabs when the run skips Meridian: the settings stay editable but are not used. */
export const NotUsedNote: React.FC<{ show: boolean }> = ({ show }) =>
  show ? (
    <Box sx={{ px: GUTTER, pt: 2.5 }}>
      <Alert severity="info">Meridian is switched off for this run, so these settings are not used.</Alert>
    </Box>
  ) : null;

const rangeText = (min: number | undefined, max: number | undefined, integer: boolean) => {
  const kind = integer ? "a whole number" : "a number";
  if (min !== undefined && max !== undefined) return `Enter ${kind} from ${min.toLocaleString("en-GB")} to ${max.toLocaleString("en-GB")}.`;
  if (min !== undefined) return `Enter ${kind} of at least ${min.toLocaleString("en-GB")}.`;
  return `Enter ${kind}.`;
};

/**
 * A number input that keeps what the user types, reports only valid values, and shows its error after the user
 * leaves the field or tries to start the run (the browser's constraint validation blocks an invalid submit).
 */
export const NumberField: React.FC<{
  label: string;
  value: number;
  onChange: (value: number) => void;
  min?: number;
  max?: number;
  integer?: boolean;
  unit?: string;
  helper?: string;
  disabled?: boolean;
  width?: number | string;
  inputLabel?: string;
}> = ({ label, value, onChange, min, max, integer = true, unit, helper, disabled, width = 180, inputLabel }) => {
  // Decimals are shown to 4 places; the exact value is kept until the user types
  const show = (v: number) => (integer ? String(v) : String(Number(v.toFixed(4))));
  const [text, setText] = useState(show(value));
  const [touched, setTouched] = useState(false);

  // Follow outside changes (reset to defaults) without overwriting what is being typed
  useEffect(() => {
    if (Number(text) !== Number(show(value)) || text.trim() === "") setText(show(value));
  }, [value]); // eslint-disable-line react-hooks/exhaustive-deps

  const parsed = text.trim() === "" ? NaN : Number(text);
  const valid = Number.isFinite(parsed) && (!integer || Number.isInteger(parsed)) && (min === undefined || parsed >= min) && (max === undefined || parsed <= max);
  const showError = touched && !valid;

  return (
    <TextField
      label={label}
      type="number"
      size="small"
      value={text}
      disabled={disabled}
      required
      error={showError}
      onChange={(e) => {
        setText(e.target.value);
        const n = Number(e.target.value);
        if (e.target.value.trim() !== "" && Number.isFinite(n) && (!integer || Number.isInteger(n)) && (min === undefined || n >= min) && (max === undefined || n <= max)) onChange(n);
      }}
      onBlur={() => setTouched(true)}
      onInvalid={() => setTouched(true)}
      helperText={showError ? rangeText(min, max, integer) : helper}
      inputProps={{ min, max, step: integer ? 1 : "any", inputMode: integer ? "numeric" : "decimal", "aria-label": inputLabel }}
      InputProps={unit ? { endAdornment: <InputAdornment position="end">{unit}</InputAdornment> } : undefined}
      sx={{ width, maxWidth: "100%", "& .MuiInputBase-root": { backgroundColor: "#FFFFFF" } }}
    />
  );
};

/** A labelled switch with its explanation underneath, wired with aria-describedby. */
export const SwitchField: React.FC<{
  label: string;
  checked: boolean;
  onChange: (checked: boolean) => void;
  help?: React.ReactNode;
  disabled?: boolean;
}> = ({ label, checked, onChange, help, disabled }) => {
  const helpId = useId();
  return (
    <Box>
      <FormControlLabel
        control={<Switch checked={checked} onChange={(e) => onChange(e.target.checked)} disabled={disabled} inputProps={{ "aria-describedby": help ? helpId : undefined }} />}
        label={<Typography sx={{ fontWeight: 650, fontSize: "0.9rem", color: PALETTE.inkPrimary }}>{label}</Typography>}
        sx={{ ml: -1 }}
      />
      {help && (
        <Typography id={helpId} variant="caption" component="p" sx={{ color: PALETTE.inkSecondary, maxWidth: "72ch", mt: -0.25 }}>
          {help}
        </Typography>
      )}
    </Box>
  );
};

/** A radio choice drawn as a card; extra controls for the choice can sit inside it. */
export const RadioCard: React.FC<{
  value: string;
  selected: boolean;
  disabled?: boolean;
  title: string;
  meta?: string;
  badge?: string;
  detail?: React.ReactNode;
  children?: React.ReactNode;
}> = ({ value, selected, disabled, title, meta, badge, detail, children }) => {
  const id = useId();
  return (
    <Box
      sx={{
        display: "grid",
        gridTemplateColumns: "auto minmax(0, 1fr)",
        columnGap: 1,
        alignItems: "start",
        p: 1.5,
        pl: 1,
        borderRadius: 2,
        opacity: disabled ? 0.6 : 1,
        border: `${selected ? 2 : 1}px solid ${selected ? PALETTE.brandPrimary : PALETTE.borderSubtle}`,
        m: selected ? 0 : "1px",
        backgroundColor: selected ? alpha(PALETTE.brandPrimary, 0.05) : "#FFFFFF",
        transition: "border-color 0.12s, background-color 0.12s",
        "&:hover": disabled || selected ? undefined : { borderColor: PALETTE.borderStrong },
        "&:has(input[type=radio]:focus-visible)": { outline: `3px solid ${PALETTE.brandPrimary}`, outlineOffset: 2 },
        "@media (prefers-reduced-motion: reduce)": { transition: "none" },
      }}
    >
      <Radio value={value} size="small" disabled={disabled} sx={{ p: 0.5, mt: -0.25 }} inputProps={{ id: `${id}-input`, "aria-labelledby": `${id}-label`, "aria-describedby": detail ? `${id}-detail` : undefined }} />
      <Box sx={{ minWidth: 0 }}>
        <Box component="label" htmlFor={`${id}-input`} sx={{ display: "flex", alignItems: "baseline", flexWrap: "wrap", columnGap: 1, cursor: disabled ? "default" : "pointer" }}>
          <Typography id={`${id}-label`} component="span" sx={{ fontWeight: 700, fontSize: "0.9rem", color: PALETTE.inkPrimary }}>
            {title}
          </Typography>
          {meta && (
            <Typography component="span" variant="caption" sx={{ color: PALETTE.inkSecondary, fontWeight: 600 }}>
              {meta}
            </Typography>
          )}
        </Box>
        {badge && (
          <Box component="span" sx={{ display: "inline-block", mt: 0.5, px: 0.75, borderRadius: 1, fontSize: "0.7rem", fontWeight: 700, color: PALETTE.brandGoldDark, backgroundColor: PALETTE.bgGoldTint }}>
            {badge}
          </Box>
        )}
        {detail && (
          <Typography id={`${id}-detail`} component="p" variant="caption" sx={{ mt: 0.5, color: PALETTE.inkSecondary }}>
            {detail}
          </Typography>
        )}
        {children}
      </Box>
    </Box>
  );
};

export const CARD_GRID = { display: "grid", gap: 1.25, gridTemplateColumns: "repeat(auto-fit, minmax(min(100%, 200px), 1fr))" } as const;
