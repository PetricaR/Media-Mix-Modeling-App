import React from "react";
import { Box, Checkbox, FormControl, FormControlLabel, FormGroup, FormHelperText, FormLabel, RadioGroup } from "@mui/material";
import type { Profile, SamplingSettings } from "../../../api/types";
import { PALETTE } from "../../../theme";
import { PROFILES, PROFILE_ORDER } from "../format";
import { type TabProps, bound, patchSettings } from "./draft";
import { CARD_GRID, Group, NotUsedNote, NumberField, RadioCard, SwitchField } from "./fields";

const COUNTS: { key: keyof SamplingSettings; label: string; help: string }[] = [
  { key: "n_chains", label: "Chains", help: "Independent MCMC chains; R-hat compares them" },
  { key: "n_adapt", label: "Adaptation draws", help: "Per chain, to tune the sampler" },
  { key: "n_burnin", label: "Burn-in draws", help: "Per chain, discarded" },
  { key: "n_keep", label: "Kept draws", help: "Per chain, used for every result" },
  { key: "n_prior", label: "Prior draws", help: "For the prior vs posterior checks" },
];
const MAX_SHIFTS = 4;

export const SamplingTab: React.FC<TabProps> = ({ draft, setDraft, defaults, locked }) => {
  const ms = draft.roleConfig.model_settings;
  const base = defaults.model_settings;
  const off = locked || !draft.includeMeridian;
  const profileDefault = draft.profile === "full";
  const selectionOn = ms.model_selection ?? profileDefault;
  const shifts = ms.selection_shifts;
  const extraFits = shifts.length * 2 + (ms.media_prior_type === "roi" ? 3 : 2);

  const toggleShift = (k: number, on: boolean) => {
    const next = on ? [...shifts, k] : shifts.filter((s) => s !== k);
    patchSettings(setDraft, { selection_shifts: next.sort((a, b) => a - b) });
  };

  return (
    <>
      <NotUsedNote show={!draft.includeMeridian} />
      <Group first title="Sampling profile" description="How thoroughly Meridian samples: more draws take longer and converge more reliably." changed={draft.profile === "custom" && JSON.stringify(ms.sampling) !== JSON.stringify(defaults.profiles.full)}>
        <RadioGroup name="profile" value={draft.profile} onChange={(e) => setDraft((d) => ({ ...d, profile: e.target.value as Profile }))} sx={CARD_GRID}>
          {PROFILE_ORDER.map((p) => (
            <RadioCard key={p} value={p} selected={draft.profile === p} disabled={off} title={PROFILES[p].label} meta={PROFILES[p].duration} badge={PROFILES[p].badge} detail={PROFILES[p].detail} />
          ))}
        </RadioGroup>

        {draft.profile === "custom" && (
          <Box component="fieldset" sx={{ m: 0, mt: 2, p: 2, border: `1px solid ${PALETTE.borderSubtle}`, borderRadius: 2, minWidth: 0 }}>
            <Box component="legend" sx={{ px: 0.5, fontWeight: 650, fontSize: "0.84rem", color: PALETTE.inkPrimary }}>
              Custom draws (the API caps them so one fit stays within the worker's time limit)
            </Box>
            <Box sx={{ display: "grid", gap: 2, gridTemplateColumns: "repeat(auto-fill, minmax(min(100%, 170px), 1fr))" }}>
              {COUNTS.map((c) => (
                <NumberField
                  key={c.key}
                  label={c.label}
                  value={ms.sampling[c.key]}
                  {...bound(defaults, `sampling.${c.key}`)}
                  disabled={off}
                  onChange={(v) => patchSettings(setDraft, { sampling: { ...ms.sampling, [c.key]: v } })}
                  helper={`${c.help}. Full: ${defaults.profiles.full[c.key].toLocaleString("en-GB")}.`}
                  width="100%"
                />
              ))}
            </Box>
          </Box>
        )}
      </Group>

      <Group title="Extra fits" description="Checks that run after the final model. Each is a Meridian fit with at most the Quick profile's draws." changed={ms.forward_holdout !== base.forward_holdout || selectionOn !== profileDefault || (selectionOn && shifts.join() !== base.selection_shifts.join())}>
        <Box sx={{ display: "grid", gap: 2.5 }}>
          <SwitchField
            label="Forward hold-out refit"
            checked={ms.forward_holdout}
            disabled={off}
            onChange={(forward_holdout) => patchSettings(setDraft, { forward_holdout })}
            help="Refits on data up to the quarter before the hold-out and forecasts that quarter: a second genuine forecast test."
          />
          <Box>
            <SwitchField
              label="Model-selection grid and prior sensitivity"
              checked={selectionOn}
              disabled={off}
              onChange={(on) => patchSettings(setDraft, { model_selection: on === profileDefault ? null : on })}
              help={`${extraFits} extra fits: each catalog shift below × Affiliate as media or control, then the prior and SEA sensitivity variants. On by default for the full profile.`}
            />
            {selectionOn && (
              <FormControl component="fieldset" disabled={off} sx={{ m: 0, mt: 1.5, p: 0, border: 0, minWidth: 0 }}>
                <FormLabel component="legend" sx={{ fontWeight: 650, fontSize: "0.84rem", color: PALETTE.inkPrimary, "&.Mui-focused": { color: PALETTE.inkPrimary } }}>
                  Catalog shifts to compare (days)
                </FormLabel>
                <FormGroup row>
                  {defaults.selection_shift_choices.map((k) => {
                    const checked = shifts.includes(k);
                    return (
                      <FormControlLabel
                        key={k}
                        label={String(k)}
                        control={
                          <Checkbox
                            size="small"
                            checked={checked}
                            // At least one and at most four shifts, as the API requires
                            disabled={off || (checked ? shifts.length === 1 : shifts.length >= MAX_SHIFTS)}
                            onChange={(e) => toggleShift(k, e.target.checked)}
                          />
                        }
                      />
                    );
                  })}
                </FormGroup>
                <FormHelperText sx={{ mx: 0 }}>Pick 1 to {MAX_SHIFTS}. Notebooks: {base.selection_shifts.join(", ")}.</FormHelperText>
              </FormControl>
            )}
          </Box>
        </Box>
      </Group>
    </>
  );
};
