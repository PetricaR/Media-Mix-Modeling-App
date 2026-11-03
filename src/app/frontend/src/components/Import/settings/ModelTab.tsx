import React from "react";
import { Alert, Box, RadioGroup } from "@mui/material";
import type { ModelSettings } from "../../../api/types";
import { type TabProps, bound, patchSettings } from "./draft";
import { CARD_GRID, GUTTER, Group, NotUsedNote, NumberField, RadioCard, SwitchField } from "./fields";

export const ModelTab: React.FC<TabProps> = ({ draft, setDraft, defaults, locked }) => {
  const ms = draft.roleConfig.model_settings;
  const base = defaults.model_settings;
  const off = locked || !draft.includeMeridian;
  const baselineChanged = ms.baseline !== base.baseline || (ms.baseline === "knots" && ms.n_knots !== base.n_knots);

  return (
    <>
      <NotUsedNote show={!draft.includeMeridian} />
      <Group first title="Carry-over" description="How many days a day's spend can keep driving sales (the adstock window). Meridian's own default is 8 days." changed={ms.max_lag !== base.max_lag}>
        <NumberField
          label="Max lag"
          unit="days"
          value={ms.max_lag}
          {...bound(defaults, "max_lag")}
          disabled={off}
          onChange={(max_lag) => patchSettings(setDraft, { max_lag })}
          helper={`Notebooks: ${base.max_lag} days`}
          width={300}
        />
      </Group>

      <Group
        title="Baseline"
        description="Sales that the media channels and controls do not explain. A time-varying baseline can absorb slow shifts in demand."
        changed={baselineChanged}
      >
        <RadioGroup
          name="baseline"
          value={ms.baseline}
          onChange={(e) => patchSettings(setDraft, { baseline: e.target.value as ModelSettings["baseline"] })}
          sx={CARD_GRID}
        >
          <RadioCard value="constant" selected={ms.baseline === "constant"} disabled={off} title="Constant" badge="As in the notebooks" detail="One intercept for the whole period (1 knot); trend and seasonality come from the controls." />
          <RadioCard value="knots" selected={ms.baseline === "knots"} disabled={off} title="Time-varying" detail="A baseline that bends at evenly spaced knots over the modelling period.">
            {ms.baseline === "knots" && (
              <Box sx={{ mt: 1.5 }}>
                <NumberField
                  label="Knots"
                  value={ms.n_knots}
                  {...bound(defaults, "n_knots")}
                  disabled={off}
                  onChange={(n_knots) => patchSettings(setDraft, { n_knots })}
                  width="100%"
                />
              </Box>
            )}
          </RadioCard>
          <RadioCard
            value="auto"
            selected={ms.baseline === "auto"}
            disabled={off}
            title="Automatic knots"
            detail="Meridian's automatic knot selection. It reads the whole sales series, hold-out included, to place the knots, and on daily data it can pick many knots, which slows the fit."
          />
        </RadioGroup>
      </Group>

      <Group title="Order of the media transforms" changed={ms.hill_before_adstock !== base.hill_before_adstock}>
        <SwitchField
          label="Saturate before carry-over (Hill before adstock)"
          checked={ms.hill_before_adstock}
          disabled={off}
          onChange={(hill_before_adstock) => patchSettings(setDraft, { hill_before_adstock })}
          help="Off, as in the notebooks: spend is carried over first and the total saturates. On: each day's spend saturates before it is carried over."
        />
      </Group>

      <Box sx={{ px: GUTTER, pb: 2.5 }}>
        <Alert severity="info" variant="outlined" sx={{ backgroundColor: "#FFFFFF" }}>
          Not offered here: the distribution of media effects and the geo options (baseline geo, one σ per geo, population
          scaling). The model is national, and Meridian fixes them for a national model.
        </Alert>
      </Box>
    </>
  );
};
