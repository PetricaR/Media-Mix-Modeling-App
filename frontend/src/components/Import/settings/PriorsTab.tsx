import React from "react";
import { Alert, Box, RadioGroup, Typography } from "@mui/material";
import type { DataProfile, MediaPriorType, ModelSettings, RoiPrior } from "../../../api/types";
import { PALETTE } from "../../../theme";
import { type TabProps, bound, mediaChannels, patchSettings } from "./draft";
import { CARD_GRID, GUTTER, Group, NotUsedNote, NumberField, RadioCard } from "./fields";

const PRIOR_TYPES: { value: MediaPriorType; title: string; badge?: string; detail: string }[] = [
  { value: "roi", title: "ROI", badge: "Default", detail: "A prior on each channel's return on spend, the quantity the brief asks about." },
  { value: "mroi", title: "Marginal ROI", detail: "A prior on the return of the next euro, at the current spend level." },
  { value: "contribution", title: "Contribution", detail: "A prior on the share of sales each channel drives." },
  { value: "coefficient", title: "Coefficient", detail: "A prior on the raw regression coefficients. Meridian advises against it: estimates get noisy." },
];

const fmt = (v: number) => v.toLocaleString("en-GB", { maximumFractionDigits: 2 });
/** The 90 % range of a LogNormal prior given by its median and σ. */
const range90 = (p: RoiPrior) => `${fmt(p.median * Math.exp(-1.645 * p.sigma))} – ${fmt(p.median * Math.exp(1.645 * p.sigma))}`;

export const PriorsTab: React.FC<TabProps & { dataProfile?: DataProfile | null }> = ({ draft, setDraft, defaults, locked, dataProfile }) => {
  // Meridian's default ROI prior would let media earn more than all sales (a negative baseline) on this data
  const overHundred = dataProfile?.default_roi_prior === "total_media_contribution" ? dataProfile.prior_media_over_100_prob : null;
  const ms = draft.roleConfig.model_settings;
  const base = defaults.model_settings;
  const off = locked || !draft.includeMeridian;
  const def = defaults.meridian_roi_prior;
  const media = mediaChannels(draft.roleConfig);
  const sigma = bound(defaults, "roas_prior_sigma");
  const tmcMeanBound = bound(defaults, "total_media_contribution_mean");
  const tmcSdBound = bound(defaults, "total_media_contribution_sd");
  const priorBounds = { median: bound(defaults, "roi_prior.median"), sigma: bound(defaults, "roi_prior.sigma") };

  const setPreset = (roi_prior: ModelSettings["roi_prior"]) => {
    // Custom priors start from Meridian's default for every media channel
    const custom = { ...ms.custom_roi_priors };
    if (roi_prior === "custom") for (const c of media) custom[c.slug ?? c.name] ??= { ...def };
    patchSettings(setDraft, { roi_prior, custom_roi_priors: custom });
  };
  const setChannelPrior = (slug: string, patch: Partial<RoiPrior>) =>
    patchSettings(setDraft, { custom_roi_priors: { ...ms.custom_roi_priors, [slug]: { ...(ms.custom_roi_priors[slug] ?? def), ...patch } } });

  return (
    <>
      <NotUsedNote show={!draft.includeMeridian} />
      <Group first title="Prior type for paid media" description="What the media priors are placed on. The ROI results are reported whatever the type." changed={ms.media_prior_type !== base.media_prior_type}>
        <RadioGroup name="media_prior_type" value={ms.media_prior_type} onChange={(e) => patchSettings(setDraft, { media_prior_type: e.target.value as MediaPriorType })} sx={CARD_GRID}>
          {PRIOR_TYPES.map((t) => (
            <RadioCard key={t.value} value={t.value} selected={ms.media_prior_type === t.value} disabled={off} title={t.title} badge={t.badge} detail={t.detail} />
          ))}
        </RadioGroup>
      </Group>

      {ms.media_prior_type !== "roi" ? (
        <Box sx={{ px: GUTTER, pb: 2.5 }}>
          <Alert severity="info" variant="outlined" sx={{ backgroundColor: "#FFFFFF" }}>
            Meridian uses its own default priors for this prior type. Per-channel priors are available for ROI priors.
          </Alert>
        </Box>
      ) : (
        <Group
          title="ROI priors"
          description="Where each channel's ROI is expected before the data is seen. Informative priors help channels whose spend moves together."
          changed={ms.roi_prior !== base.roi_prior}
        >
          {overHundred != null && (
            <Alert severity={ms.roi_prior === "meridian" ? "warning" : "info"} variant="outlined" sx={{ mb: 1.5, backgroundColor: "#FFFFFF" }}>
              Media spend is large next to sales in this data: under the Meridian default, the prior gives a{" "}
              {Math.round(100 * overHundred)} % chance that media earn more than all sales, which forces a negative baseline. Runs of
              this data start with the total media contribution prior.
            </Alert>
          )}
          <RadioGroup name="roi_prior" value={ms.roi_prior} onChange={(e) => setPreset(e.target.value as ModelSettings["roi_prior"])} sx={CARD_GRID}>
            <RadioCard
              value="meridian"
              selected={ms.roi_prior === "meridian"}
              disabled={off}
              title="Meridian default"
              badge="Default"
              detail={`LogNormal(0.2, 0.9) for every channel: median ROI ${fmt(def.median)}, 90 % of the prior between ${range90(def)}.`}
            />
            <RadioCard
              value="attributed_roas"
              selected={ms.roi_prior === "attributed_roas"}
              disabled={off}
              title="Centred on attributed ROAS"
              detail="Each channel's median is its channel-attributed sales / cost from the workbook, as in the prior sensitivity check."
            >
              {ms.roi_prior === "attributed_roas" && (
                <Box sx={{ mt: 1.5 }}>
                  <NumberField label="σ (log scale)" value={ms.roas_prior_sigma} integer={false} {...sigma} disabled={off} onChange={(roas_prior_sigma) => patchSettings(setDraft, { roas_prior_sigma })} width="100%" />
                </Box>
              )}
            </RadioCard>
            <RadioCard
              value="total_media_contribution"
              selected={ms.roi_prior === "total_media_contribution"}
              disabled={off}
              title="Total media contribution"
              badge="Meridian baseline guardrail"
              detail="Calibrates a common LogNormal ROI prior so total media contribution has a chosen prior mean and SD of total sales, preventing negative baseline."
            >
              {ms.roi_prior === "total_media_contribution" && (
                <Box sx={{ mt: 1.5, display: "grid", gap: 1.25 }}>
                  <NumberField
                    label="Prior mean media share"
                    inputLabel="Prior mean total media contribution share"
                    value={ms.total_media_contribution_mean ?? 0.4}
                    integer={false}
                    {...tmcMeanBound}
                    disabled={off}
                    onChange={(total_media_contribution_mean) => patchSettings(setDraft, { total_media_contribution_mean })}
                    width="100%"
                  />
                  <NumberField
                    label="Prior SD media share"
                    inputLabel="Prior SD total media contribution share"
                    value={ms.total_media_contribution_sd ?? 0.2}
                    integer={false}
                    {...tmcSdBound}
                    disabled={off}
                    onChange={(total_media_contribution_sd) => patchSettings(setDraft, { total_media_contribution_sd })}
                    width="100%"
                  />
                </Box>
              )}
            </RadioCard>
            <RadioCard value="custom" selected={ms.roi_prior === "custom"} disabled={off} title="Custom per channel" detail="Set a median ROI and σ for each media channel, for example from a lift test." />
          </RadioGroup>

          {ms.roi_prior === "custom" && (
            <Box sx={{ mt: 2, border: `1px solid ${PALETTE.borderSubtle}`, borderRadius: 1, overflow: "hidden" }}>
              {media.map((c, idx) => {
                const slug = c.slug ?? c.name;
                const p = ms.custom_roi_priors[slug] ?? def;
                return (
                  <Box
                    key={slug}
                    role="group"
                    aria-label={`ROI prior of ${c.name}`}
                    sx={{
                      display: "grid",
                      gridTemplateColumns: { xs: "minmax(0, 1fr)", sm: "minmax(120px, 1fr) 150px 150px minmax(140px, 1fr)" },
                      columnGap: 2,
                      rowGap: 1,
                      alignItems: "center",
                      px: 2,
                      py: 1.25,
                      borderTop: idx ? `1px solid ${PALETTE.borderSubtle}` : "none",
                    }}
                  >
                    <Typography sx={{ fontWeight: 700, fontSize: "0.88rem", color: PALETTE.inkPrimary }}>{c.name}</Typography>
                    <NumberField label="Median ROI" inputLabel={`Median ROI prior of ${c.name}`} value={p.median} integer={false} {...priorBounds.median} disabled={off} onChange={(median) => setChannelPrior(slug, { median })} width="100%" />
                    <NumberField label="σ" inputLabel={`σ of the ROI prior of ${c.name}`} value={p.sigma} integer={false} {...priorBounds.sigma} disabled={off} onChange={(s) => setChannelPrior(slug, { sigma: s })} width="100%" />
                    <Typography variant="caption" sx={{ color: PALETTE.inkSecondary }}>
                      90 % of the prior: {range90(p)}
                    </Typography>
                  </Box>
                );
              })}
            </Box>
          )}
        </Group>
      )}
    </>
  );
};
