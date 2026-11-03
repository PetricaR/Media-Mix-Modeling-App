import type React from "react";
import type { AdstockShape, ChannelConfig, ChannelRole, DataRoleConfig, ModelSettings, Profile, RunDetail, SettingsDefaults } from "../../../api/types";

/** Everything the settings form edits before a run starts. */
export interface SettingsDraft {
  name: string;
  profile: Profile;
  includeMeridian: boolean;
  seed: number;
  roleConfig: DataRoleConfig & { model_settings: ModelSettings };
}

export type TabKey = "data" | "channels" | "model" | "priors" | "sampling";

export const TABS: { key: TabKey; label: string }[] = [
  { key: "data", label: "Data" },
  { key: "channels", label: "Channels" },
  { key: "model", label: "Model" },
  { key: "priors", label: "Priors" },
  { key: "sampling", label: "Sampling" },
];

export const isMedia = (role: ChannelRole) => role === "paid" || role === "digital_paid" || role === "print_event";
export const defaultShape = (role: ChannelRole): AdstockShape => (role === "print_event" ? "binomial" : "geometric");
export const inferredRole = (ch: ChannelConfig): ChannelRole => ch.inferred_role ?? ch.role;

/** Media channels that get an ROI (commission and control channels enter Meridian as controls). */
export const mediaChannels = (rc: DataRoleConfig): ChannelConfig[] => rc.channels.filter((c) => c.enabled !== false && isMedia(c.role));

export function initialDraft(run: RunDetail, defaults: SettingsDefaults): SettingsDraft {
  const rc = run.config?.role_config ?? run.schema?.role_config;
  const roleConfig = {
    ...(rc as DataRoleConfig),
    channels: rc?.channels ?? [],
    holdout_days: rc?.holdout_days ?? defaults.holdout_days,
    drop_trailing_isolated_days: rc?.drop_trailing_isolated_days ?? defaults.drop_trailing_isolated_days,
    model_settings: structuredClone(rc?.model_settings ?? defaults.model_settings),
  };
  return {
    name: run.name,
    profile: run.profile,
    includeMeridian: !run.config?.skip_meridian,
    seed: run.config?.seed ?? defaults.seed,
    roleConfig,
  };
}

/** The draft with every setting back at the notebook default; the name, profile and Meridian switch are kept. */
export function resetDraft(d: SettingsDraft, defaults: SettingsDefaults): SettingsDraft {
  return {
    ...d,
    seed: defaults.seed,
    roleConfig: {
      ...d.roleConfig,
      holdout_days: defaults.holdout_days,
      drop_trailing_isolated_days: defaults.drop_trailing_isolated_days,
      model_settings: structuredClone(defaults.model_settings),
      channels: d.roleConfig.channels.map((c) => withRole(c, inferredRole(c), defaults)),
    },
  };
}

/** A channel with a new role: its adstock shape follows the role's default again and a print event gets its shift. */
export function withRole(c: ChannelConfig, role: ChannelRole, defaults: SettingsDefaults): ChannelConfig {
  return {
    ...c,
    role,
    enabled: role !== "ignored",
    adstock_shape: null,
    saturation: "hill",
    in_home_shift_days: role === "print_event" ? defaults.in_home_shift_days : 0,
  };
}

export function channelChanged(c: ChannelConfig, defaults: SettingsDefaults): boolean {
  if (c.role !== inferredRole(c)) return true;
  if (!isMedia(c.role)) return false;
  if ((c.adstock_shape ?? defaultShape(c.role)) !== defaultShape(c.role)) return true;
  if ((c.saturation ?? "hill") !== "hill") return true;
  return c.role === "print_event" && (c.in_home_shift_days ?? defaults.in_home_shift_days) !== defaults.in_home_shift_days;
}

const sameSampling = (a: object, b: object) => JSON.stringify(a) === JSON.stringify(b);
const selectionOn = (ms: ModelSettings, profile: Profile) => ms.model_selection ?? profile === "full";

/** What differs from the notebook defaults, per tab (mirrors the API's `settings_changes`). */
export function changesByTab(d: SettingsDraft, defaults: SettingsDefaults): Record<TabKey, string[]> {
  const rc = d.roleConfig;
  const ms = rc.model_settings;
  const base = defaults.model_settings;
  const out: Record<TabKey, string[]> = { data: [], channels: [], model: [], priors: [], sampling: [] };
  const add = (tab: TabKey, label: string, changed: boolean) => {
    if (changed) out[tab].push(label);
  };

  add("data", "Hold-out length", rc.holdout_days !== defaults.holdout_days);
  add("data", "Trailing isolated days", Boolean(rc.drop_trailing_isolated_days) !== defaults.drop_trailing_isolated_days);
  add("data", "Random seed", d.seed !== defaults.seed);
  add("data", "Calendar controls", JSON.stringify(ms.calendar) !== JSON.stringify(base.calendar));
  add("data", "Yearly Fourier pairs", ms.n_fourier !== base.n_fourier);
  add("data", "Linear trend", ms.trend !== base.trend);

  for (const c of rc.channels) add("channels", c.name, channelChanged(c, defaults));

  add("model", "Max lag", ms.max_lag !== base.max_lag);
  add("model", "Baseline", ms.baseline !== base.baseline || (ms.baseline === "knots" && ms.n_knots !== base.n_knots));
  add("model", "Hill before adstock", ms.hill_before_adstock !== base.hill_before_adstock);

  add("priors", "Media prior type", ms.media_prior_type !== base.media_prior_type);
  if (ms.media_prior_type === "roi") {
    add("priors", "ROI prior centred on attributed ROAS", ms.roi_prior === "attributed_roas");
    if (ms.roi_prior === "custom") {
      const def = defaults.meridian_roi_prior;
      for (const c of mediaChannels(rc)) {
        const p = ms.custom_roi_priors[c.slug ?? ""];
        add("priors", `ROI prior · ${c.name}`, Boolean(p) && (p.median.toFixed(2) !== def.median.toFixed(2) || p.sigma !== def.sigma));
      }
    }
  }

  add("sampling", "Sampling draws", d.profile === "custom" && !sameSampling(ms.sampling, defaults.profiles.full));
  add("sampling", "Forward hold-out refit", ms.forward_holdout !== base.forward_holdout);
  add("sampling", "Model-selection grid", selectionOn(ms, d.profile) !== selectionOn(base, d.profile));
  add("sampling", "Selection catalog shifts", selectionOn(ms, d.profile) && ms.selection_shifts.join() !== base.selection_shifts.join());
  return out;
}

export interface TabProps {
  draft: SettingsDraft;
  setDraft: React.Dispatch<React.SetStateAction<SettingsDraft>>;
  defaults: SettingsDefaults;
  locked: boolean;
}

/** Merge a change into the draft's model settings. */
export const patchSettings = (setDraft: TabProps["setDraft"], patch: Partial<ModelSettings>) =>
  setDraft((d) => ({ ...d, roleConfig: { ...d.roleConfig, model_settings: { ...d.roleConfig.model_settings, ...patch } } }));

/** Merge a change into the draft's role config (hold-out, trailing days, channels). */
export const patchRoleConfig = (setDraft: TabProps["setDraft"], patch: Partial<DataRoleConfig>) =>
  setDraft((d) => ({ ...d, roleConfig: { ...d.roleConfig, ...patch } }));

/** Bounds from the API's limits table, e.g. `bound(defaults, "max_lag")`. */
export const bound = (defaults: SettingsDefaults, key: string) => {
  const b = defaults.limits[key] ?? {};
  // "Greater than 0" becomes a smallest accepted value the number inputs can enforce
  return { min: b.min ?? (b.min_exclusive !== undefined ? b.min_exclusive + 0.01 : undefined), max: b.max };
};
