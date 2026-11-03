import React, { useMemo, useState } from "react";
import {
  Alert,
  Box,
  Button,
  Chip,
  CircularProgress,
  FormControl,
  FormControlLabel,
  FormLabel,
  MenuItem,
  Radio,
  RadioGroup,
  Select,
  Switch,
  TextField,
  Typography,
  alpha,
} from "@mui/material";
import PlayArrowIcon from "@mui/icons-material/PlayArrow";
import ReplayIcon from "@mui/icons-material/Replay";
import UndoIcon from "@mui/icons-material/Undo";
import type { ChannelRole, DataRoleConfig, RunDetail } from "../../api/types";
import { FONT_MONO, PALETTE } from "../../theme";
import { PROFILES, PROFILE_ORDER, ROLE_OPTIONS, type Profile, roleOption } from "./format";

export interface RunSettingsValues {
  name: string;
  profile: Profile;
  skipMeridian: boolean;
  roleConfig: DataRoleConfig | undefined;
}

const ROLE_COLS = "minmax(110px, 0.9fr) minmax(0, 2.3fr) minmax(120px, 1fr)";
const GUTTER = { xs: 2.5, sm: 3 };

const Dot: React.FC<{ colour: string; hollow?: boolean }> = ({ colour, hollow }) => (
  <Box
    component="span"
    aria-hidden="true"
    sx={{ flexShrink: 0, width: 9, height: 9, boxSizing: "border-box", borderRadius: "50%", border: `2px solid ${colour}`, backgroundColor: hollow ? "transparent" : colour }}
  />
);

const Group: React.FC<{ title: string; description?: React.ReactNode; aside?: React.ReactNode; children: React.ReactNode }> = ({ title, description, aside, children }) => (
  <Box sx={{ px: GUTTER, py: 2.5, borderTop: `1px solid ${PALETTE.borderSubtle}` }}>
    <Box sx={{ display: "flex", alignItems: "flex-end", justifyContent: "space-between", flexWrap: "wrap", gap: 1.5, mb: 1.75 }}>
      <Box sx={{ maxWidth: "70ch" }}>
        <Typography component="h3" sx={{ fontWeight: 700, fontSize: "0.95rem", color: PALETTE.inkPrimary }}>
          {title}
        </Typography>
        {description && <Typography variant="body2">{description}</Typography>}
      </Box>
      {aside}
    </Box>
    {children}
  </Box>
);

/**
 * The settings a run starts with: name (drafts only), whether Meridian is fitted, its sampling depth and each channel's
 * role. The parent keys this form by run id, so its draft state starts fresh for every run.
 */
export const RunSettingsForm: React.FC<{
  run: RunDetail;
  mode: "draft" | "rerun";
  locked: boolean;
  submitting: boolean;
  error: string | null;
  onClearError: () => void;
  onSubmit: (values: RunSettingsValues) => void;
}> = ({ run, mode, locked, submitting, error, onClearError, onSubmit }) => {
  const saved = run.config?.role_config ?? run.schema?.role_config ?? null;
  const [roles, setRoles] = useState<DataRoleConfig | null>(saved);
  const [profile, setProfile] = useState<Profile>(run.profile);
  const [includeMeridian, setIncludeMeridian] = useState(!run.config?.skip_meridian);
  const [name, setName] = useState(run.name);

  const savedRoles = useMemo(() => new Map((saved?.channels ?? []).map((c) => [c.name, c.role])), [saved]);
  const channels = roles?.channels ?? [];
  const changedCount = channels.filter((c) => savedRoles.has(c.name) && savedRoles.get(c.name) !== c.role).length;
  const counts = ROLE_OPTIONS.map((o) => ({ ...o, n: channels.filter((c) => c.role === o.value).length })).filter((o) => o.n > 0);

  const setRole = (idx: number, role: ChannelRole) =>
    setRoles((rc) => (rc ? { ...rc, channels: rc.channels.map((c, i) => (i === idx ? { ...c, role } : c)) } : rc));

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    onSubmit({ name: name.trim(), profile, skipMeridian: !includeMeridian, roleConfig: roles ?? undefined });
  };

  const plan = includeMeridian ? `${PROFILES[profile].label} sampling · ${PROFILES[profile].duration}` : "EDA and OLS benchmark only";
  const paid = channels.filter((c) => c.role === "paid").length;

  return (
    <Box component="form" onSubmit={submit} aria-label={mode === "draft" ? "Run settings" : "Re-run settings"}>
      {mode === "draft" && (
        <Box sx={{ px: GUTTER, pb: 2.5 }}>
          <TextField
            label="Run name"
            value={name}
            onChange={(e) => setName(e.target.value)}
            required
            fullWidth
            size="small"
            autoComplete="off"
            disabled={locked}
            helperText="Shown in the run switcher and the run list."
            inputProps={{ maxLength: 120 }}
            sx={{ maxWidth: 480 }}
          />
        </Box>
      )}

      <Group title="Model" description="Choose whether to fit the Google Meridian MMM and how thoroughly it samples.">
        <FormControlLabel
          control={<Switch checked={includeMeridian} onChange={(e) => setIncludeMeridian(e.target.checked)} disabled={locked} inputProps={{ "aria-describedby": "meridian-help" }} />}
          label={<Typography sx={{ fontWeight: 650, fontSize: "0.9rem", color: PALETTE.inkPrimary }}>Fit the Google Meridian MMM</Typography>}
          sx={{ ml: -1 }}
        />
        <Typography id="meridian-help" variant="caption" component="p" sx={{ color: PALETTE.inkSecondary, mb: 2, maxWidth: "70ch" }}>
          {includeMeridian
            ? "The Bayesian model behind drivers & ROI, the budget optimiser and the forecast."
            : "Off: the run covers the data audit, EDA, preparation and the OLS benchmark; the model, drivers, optimiser and forecast pages stay empty."}
        </Typography>

        <FormControl component="fieldset" disabled={locked || !includeMeridian} sx={{ width: "100%", m: 0, p: 0, border: 0, minWidth: 0 }}>
          <FormLabel component="legend" sx={{ fontWeight: 650, fontSize: "0.84rem", color: PALETTE.inkPrimary, mb: 1, "&.Mui-focused": { color: PALETTE.inkPrimary } }}>
            Sampling profile{!includeMeridian && <Typography component="span" variant="caption" sx={{ ml: 1, color: PALETTE.inkMuted }}>not used without Meridian</Typography>}
          </FormLabel>
          <RadioGroup
            name="profile"
            value={profile}
            onChange={(e) => setProfile(e.target.value as Profile)}
            sx={{ display: "grid", gap: 1.25, gridTemplateColumns: "repeat(auto-fit, minmax(min(100%, 190px), 1fr))" }}
          >
            {PROFILE_ORDER.map((p) => {
              const selected = profile === p;
              const off = locked || !includeMeridian;
              return (
                <Box
                  component="label"
                  key={p}
                  sx={{
                    display: "grid",
                    gridTemplateColumns: "auto minmax(0, 1fr)",
                    columnGap: 1,
                    alignItems: "start",
                    p: 1.5,
                    pl: 1,
                    borderRadius: 2,
                    cursor: off ? "default" : "pointer",
                    opacity: off ? 0.6 : 1,
                    border: `${selected ? 2 : 1}px solid ${selected ? PALETTE.brandPrimary : PALETTE.borderSubtle}`,
                    m: selected ? 0 : "1px",
                    backgroundColor: selected ? alpha(PALETTE.brandPrimary, 0.05) : "#FFFFFF",
                    transition: "border-color 0.12s, background-color 0.12s",
                    "&:hover": off || selected ? undefined : { borderColor: PALETTE.borderStrong },
                    "&:has(input:focus-visible)": { outline: `3px solid ${PALETTE.brandPrimary}`, outlineOffset: 2 },
                  }}
                >
                  <Radio value={p} size="small" sx={{ p: 0.5, mt: -0.25 }} inputProps={{ "aria-labelledby": `profile-${p}-label`, "aria-describedby": `profile-${p}-detail` }} />
                  <Box component="span" sx={{ display: "block", minWidth: 0 }}>
                    <Box component="span" sx={{ display: "flex", alignItems: "baseline", flexWrap: "wrap", columnGap: 1 }}>
                      <Typography id={`profile-${p}-label`} component="span" sx={{ fontWeight: 700, fontSize: "0.9rem", color: PALETTE.inkPrimary }}>
                        {PROFILES[p].label}
                      </Typography>
                      <Typography component="span" variant="caption" sx={{ color: PALETTE.inkSecondary, fontWeight: 600 }}>
                        {PROFILES[p].duration}
                      </Typography>
                    </Box>
                    {PROFILES[p].badge && (
                      <Box component="span" sx={{ display: "inline-block", mt: 0.5, px: 0.75, borderRadius: 1, fontSize: "0.7rem", fontWeight: 700, color: PALETTE.brandGoldDark, backgroundColor: PALETTE.bgGoldTint }}>
                        {PROFILES[p].badge}
                      </Box>
                    )}
                    <Typography id={`profile-${p}-detail`} component="span" variant="caption" sx={{ display: "block", mt: 0.5, color: PALETTE.inkSecondary }}>
                      {PROFILES[p].detail}
                    </Typography>
                  </Box>
                </Box>
              );
            })}
          </RadioGroup>
        </FormControl>
      </Group>

      {roles && (
        <Group
          title="Channel roles"
          description="Inferred from the data; the note under each role says why. Change a role only if you know the channel works differently."
          aside={
            changedCount > 0 && (
              <Button size="small" startIcon={<UndoIcon />} onClick={() => setRoles(saved)} disabled={locked}>
                Undo {changedCount} change{changedCount === 1 ? "" : "s"}
              </Button>
            )
          }
        >
          <Box component="ul" aria-label="Channels per role" sx={{ listStyle: "none", p: 0, m: 0, mb: 1.5, display: "flex", flexWrap: "wrap", gap: 0.75 }}>
            {counts.map((o) => (
              <Box
                component="li"
                key={o.value}
                sx={{ display: "inline-flex", alignItems: "center", gap: 0.75, px: 1.25, py: 0.375, borderRadius: 999, border: `1px solid ${PALETTE.borderSubtle}`, fontSize: "0.78rem", fontWeight: 600, color: PALETTE.inkPrimary }}
              >
                <Dot colour={o.colour} hollow={o.hollow} />
                {o.n} {o.short.toLowerCase()}
              </Box>
            ))}
          </Box>

          <Box sx={{ border: `1px solid ${PALETTE.borderSubtle}`, borderRadius: 2, overflow: "hidden" }}>
            <Box
              aria-hidden="true"
              sx={{
                display: { xs: "none", sm: "grid" },
                gridTemplateColumns: ROLE_COLS,
                columnGap: 2,
                px: 2,
                py: 0.875,
                backgroundColor: PALETTE.bgSubtle,
                fontSize: "0.72rem",
                fontWeight: 700,
                textTransform: "uppercase",
                letterSpacing: "0.04em",
                color: PALETTE.inkPrimary,
              }}
            >
              <span>Channel</span>
              <span>Role in the model</span>
              <span>Spend column</span>
            </Box>
            {channels.map((ch, idx) => {
              const changed = savedRoles.has(ch.name) && savedRoles.get(ch.name) !== ch.role;
              const options = ROLE_OPTIONS.some((o) => o.value === ch.role) ? ROLE_OPTIONS : [...ROLE_OPTIONS, roleOption(ch.role)];
              const noteId = `role-note-${idx}`;
              return (
                <Box
                  key={ch.name}
                  sx={{
                    display: "grid",
                    gridTemplateColumns: { xs: "minmax(0, 1fr)", sm: ROLE_COLS },
                    columnGap: 2,
                    rowGap: 0.75,
                    alignItems: "start",
                    px: 2,
                    py: 1.5,
                    borderTop: { xs: idx ? `1px solid ${PALETTE.borderSubtle}` : "none", sm: `1px solid ${PALETTE.borderSubtle}` },
                    backgroundColor: changed ? alpha(PALETTE.brandPrimary, 0.04) : "transparent",
                  }}
                >
                  <Box sx={{ display: "flex", alignItems: "center", flexWrap: "wrap", gap: 0.75, pt: { sm: 0.875 } }}>
                    <Typography sx={{ fontWeight: 700, fontSize: "0.88rem", color: PALETTE.inkPrimary }}>{ch.name}</Typography>
                    {changed && <Chip size="small" variant="outlined" color="primary" label="Changed" sx={{ height: 20, fontSize: "0.7rem" }} />}
                  </Box>
                  <Box sx={{ minWidth: 0 }}>
                    <Select
                      size="small"
                      fullWidth
                      value={ch.role}
                      disabled={locked}
                      onChange={(e) => setRole(idx, e.target.value as ChannelRole)}
                      inputProps={{ "aria-label": `Role of ${ch.name}`, "aria-describedby": ch.rationale ? noteId : undefined }}
                      renderValue={(v) => {
                        const o = roleOption(v as ChannelRole);
                        return (
                          <Box component="span" sx={{ display: "inline-flex", alignItems: "center", gap: 1 }}>
                            <Dot colour={o.colour} hollow={o.hollow} />
                            {o.label}
                          </Box>
                        );
                      }}
                      sx={{ fontSize: "0.84rem", backgroundColor: "#FFFFFF", maxWidth: 320 }}
                    >
                      {options.map((o) => (
                        <MenuItem key={o.value} value={o.value} sx={{ gap: 1.25, fontSize: "0.86rem" }}>
                          <Dot colour={o.colour} hollow={o.hollow} />
                          {o.label}
                        </MenuItem>
                      ))}
                    </Select>
                    {ch.rationale && (
                      <Typography id={noteId} variant="caption" component="p" sx={{ mt: 0.625, color: PALETTE.inkSecondary }}>
                        {ch.rationale}
                      </Typography>
                    )}
                  </Box>
                  <Typography sx={{ fontFamily: FONT_MONO, fontSize: "0.76rem", color: PALETTE.inkSecondary, pt: { sm: 1 }, overflowWrap: "anywhere" }}>
                    <span className="visually-hidden">Spend column: </span>
                    <Box component="span" aria-hidden="true" sx={{ display: { sm: "none" }, fontFamily: "inherit", color: PALETTE.inkMuted }}>
                      spend ·{" "}
                    </Box>
                    {ch.cost_column || "—"}
                  </Typography>
                </Box>
              );
            })}
          </Box>
        </Group>
      )}

      {error && (
        <Box sx={{ px: GUTTER, pb: 2 }}>
          <Alert severity="error" onClose={onClearError}>
            {error}
          </Alert>
        </Box>
      )}

      {/* Sticky so the start button stays in reach while the channel list is reviewed */}
      <Box
        sx={{
          position: "sticky",
          bottom: 0,
          zIndex: 3,
          display: "flex",
          alignItems: "center",
          flexWrap: "wrap",
          gap: 1.5,
          px: GUTTER,
          py: 1.75,
          borderTop: `1px solid ${PALETTE.borderSubtle}`,
          backgroundColor: alpha("#FFFFFF", 0.96),
          backdropFilter: "blur(6px)",
          borderBottomLeftRadius: 10,
          borderBottomRightRadius: 10,
          boxShadow: "0 -6px 16px rgba(33, 41, 47, 0.05)",
        }}
      >
        <Box sx={{ flexGrow: 1, minWidth: 200 }}>
          <Typography variant="body2" sx={{ fontWeight: 650, color: PALETTE.inkPrimary }}>
            {plan}
            {roles ? ` · ${paid} paid media channel${paid === 1 ? "" : "s"}` : ""}
          </Typography>
          <Typography variant="caption" sx={{ color: PALETTE.inkMuted }}>
            {locked
              ? "Settings are locked while the run is active."
              : mode === "rerun"
              ? "A re-run replaces this run's results; the uploaded workbook is kept."
              : "Progress shows on this page once the run starts."}
          </Typography>
        </Box>
        <Button
          type="submit"
          variant="contained"
          disabled={locked || submitting}
          startIcon={submitting ? <CircularProgress size={16} color="inherit" /> : mode === "draft" ? <PlayArrowIcon /> : <ReplayIcon />}
          sx={{ minHeight: 44, px: 3 }}
        >
          {mode === "draft" ? "Start run" : "Re-run with these settings"}
        </Button>
      </Box>
    </Box>
  );
};
