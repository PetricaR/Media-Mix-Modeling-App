import { describe, expect, it } from "vitest";
import type { RecommendedChange, RunDetail, SettingsDefaults } from "../../../api/types";
import { applyRecommendation, changesByTab, channelChanged, initialDraft, mediaChannels, resetDraft, withRole } from "./draft";

const DEFAULTS: SettingsDefaults = {
  model_settings: {
    calendar: { weekdays: true, holidays: true, christmas: true, black_week: true },
    n_fourier: 3,
    trend: true,
    missing_row_controls: false,
    max_lag: 42,
    baseline: "constant",
    n_knots: 6,
    hill_before_adstock: true,
    media_prior_type: "roi",
    roi_prior: "meridian",
    roas_prior_sigma: 0.5,
    total_media_contribution_mean: 0.4,
    total_media_contribution_sd: 0.2,
    custom_roi_priors: {},
    sampling: { n_chains: 4, n_adapt: 500, n_burnin: 500, n_keep: 500, n_prior: 500 },
    forward_holdout: true,
    model_selection: null,
    selection_shifts: [0, 7, 14],
  },
  holdout_days: 92,
  drop_trailing_isolated_days: true,
  seed: 0,
  in_home_shift_days: 7,
  min_training_days: 365,
  profiles: {
    full: { n_chains: 4, n_adapt: 500, n_burnin: 500, n_keep: 500, n_prior: 500 },
    quick: { n_chains: 2, n_adapt: 200, n_burnin: 200, n_keep: 200, n_prior: 200 },
    smoke: { n_chains: 1, n_adapt: 20, n_burnin: 20, n_keep: 20, n_prior: 50 },
  },
  limits: {
    max_lag: { min: 7, max: 90 },
    roas_prior_sigma: { min_exclusive: 0, max: 3 },
  },
  meridian_roi_prior: { median: 1.22, sigma: 0.9 },
  selection_shift_choices: [0, 3, 7, 10, 14],
};

const SAMPLE_RUN: RunDetail = {
  id: "run_123",
  name: "Sample workbook",
  filename: "workbook.xlsx",
  sha256: "abc",
  profile: "quick",
  status: "draft",
  is_active: true,
  config: {
    seed: 0,
    skip_meridian: false,
    role_config: {
      target_column: "sales",
      date_column: "date",
      holdout_days: 92,
      drop_trailing_isolated_days: true,
      channels: [
        { name: "SEA", slug: "sea", role: "paid", inferred_role: "paid", enabled: true },
        { name: "Catalog", slug: "catalog", role: "print_event", inferred_role: "print_event", in_home_shift_days: 7, enabled: true },
        { name: "Affiliate", slug: "affiliate", role: "commission", inferred_role: "commission", enabled: true },
      ],
      model_settings: structuredClone(DEFAULTS.model_settings),
    },
  },
  schema: {
    filename: "workbook.xlsx",
    sha256: "abc",
    sheet_names: ["data"],
    n_rows: 7299,
    n_columns: 4,
    date_min: "2023-01-01",
    date_max: "2025-01-01",
    years_present: [2023, 2024, 2025],
    missing_dates_count: 0,
    duplicate_rows_count: 0,
    negative_values_count: 0,
    total_sales_sum: 2_310_000_000,
    total_media_spend_sum: 138_000_000,
    warnings: [],
    role_config: {
      target_column: "sales",
      date_column: "date",
      channels: [],
    },
  },
  stages: [],
};

describe("RunSettingsForm draft helpers", () => {
  it("reports zero changes on the defaults and tracks changes per tab", () => {
    const draft = initialDraft(SAMPLE_RUN, DEFAULTS);
    expect(mediaChannels(draft.roleConfig).map((c) => c.slug)).toEqual(["sea", "catalog"]);

    const initialChanges = changesByTab(draft, DEFAULTS);
    expect(Object.values(initialChanges).flat()).toEqual([]);

    draft.roleConfig.model_settings.roi_prior = "total_media_contribution";
    draft.roleConfig.model_settings.baseline = "auto";
    draft.roleConfig.holdout_days = 60;
    const updatedChanges = changesByTab(draft, DEFAULTS);
    expect(updatedChanges.data).toContain("Hold-out length");
    expect(updatedChanges.model).toContain("Baseline");
    expect(updatedChanges.priors).toContain("Total media contribution ROI prior");

    const reset = resetDraft(draft, DEFAULTS);
    expect(Object.values(changesByTab(reset, DEFAULTS)).flat()).toEqual([]);
  });

  it("detects channel role and in-home shift overrides", () => {
    const cat = SAMPLE_RUN.config!.role_config!.channels[1];
    expect(channelChanged(cat, DEFAULTS)).toBe(false);
    expect(channelChanged({ ...cat, in_home_shift_days: 14 }, DEFAULTS)).toBe(true);
    const asPaid = withRole(cat, "paid", DEFAULTS);
    expect(asPaid.in_home_shift_days).toBe(0);
    expect(channelChanged(asPaid, DEFAULTS)).toBe(true);
  });

  it("applies Gemini's recommended changes in order and counts them as changes", () => {
    const draft = initialDraft(SAMPLE_RUN, DEFAULTS);
    const change = (setting: string, value: RecommendedChange["value"], tab: RecommendedChange["tab"]): RecommendedChange => ({
      setting,
      value,
      tab,
      label: setting,
      current: "",
      recommended: "",
      reason: "",
      confidence: "medium",
    });
    const next = applyRecommendation(
      draft,
      [
        change("channels.catalog.role", "paid", "channels"),
        change("channels.catalog.saturation", "none", "channels"),
        change("channels.sea.adstock_shape", "geometric", "channels"),
        change("include_meridian", true, "run"),
        change("profile", "full", "sampling"),
        change("resample.frequency", "weekly", "data"),
        change("holdout_days", 60, "data"),
        change("calendar.black_week", false, "data"),
        change("roi_prior", "attributed_roas", "priors"),
        change("selection_shifts", [1, 2], "sampling"),
      ],
      DEFAULTS
    );
    const catalog = next.roleConfig.channels.find((c) => c.slug === "catalog")!;
    // The role resets the shift (as the role picker does); the later saturation change still applies
    expect(catalog).toMatchObject({ role: "paid", in_home_shift_days: 0, saturation: "none", adstock_shape: null });
    // A recommended default shape is stored as null, like the form does
    expect(next.roleConfig.channels.find((c) => c.slug === "sea")!.adstock_shape).toBeNull();
    expect(next.profile).toBe("full");
    expect(next.roleConfig.holdout_days).toBe(60);
    expect(next.roleConfig.model_settings.calendar).toEqual({ weekdays: true, holidays: true, christmas: true, black_week: false });
    expect(next.roleConfig.model_settings.roi_prior).toBe("attributed_roas");
    expect(next.roleConfig.model_settings.selection_shifts).toEqual([1, 2]);
    // The original draft is untouched
    expect(draft.roleConfig.model_settings.calendar.black_week).toBe(true);
    expect(draft.roleConfig.channels[1].role).toBe("print_event");

    const changes = changesByTab(next, DEFAULTS);
    expect(next.roleConfig.columns?.resample?.frequency).toBe("weekly");
    expect(changes.data).toEqual(expect.arrayContaining(["Data frequency", "Hold-out length", "Calendar controls"]));
    expect(changes.channels).toContain("Catalog");
    expect(changes.priors).toContain("ROI prior centred on attributed ROAS");
  });

  it("applies seed, custom draws and custom ROI priors, and drops a prior when its channel stops being media", () => {
    const draft = initialDraft(SAMPLE_RUN, DEFAULTS);
    const change = (setting: string, value: RecommendedChange["value"]): RecommendedChange => ({
      setting,
      value,
      tab: "priors",
      label: setting,
      current: "",
      recommended: "",
      reason: "",
      confidence: "low",
    });
    const next = applyRecommendation(
      draft,
      [
        change("profile", "custom"),
        change("sampling.n_keep", 1500),
        change("seed", 7),
        change("roi_prior", "custom"),
        change("custom_roi_priors.sea.median", 6.7),
      ],
      DEFAULTS
    );
    const ms = next.roleConfig.model_settings;
    expect(next.seed).toBe(7);
    expect(ms.sampling).toEqual({ ...DEFAULTS.model_settings.sampling, n_keep: 1500 });
    // The custom preset starts every media channel at Meridian's default, as the Priors tab does
    expect(ms.custom_roi_priors).toEqual({ sea: { median: 6.7, sigma: 0.9 }, catalog: { median: 1.22, sigma: 0.9 } });
    const changes = changesByTab(next, DEFAULTS);
    expect(changes.sampling).toContain("Sampling draws");
    expect(changes.priors).toContain("ROI prior · SEA");

    const unpaid = applyRecommendation(next, [change("channels.catalog.role", "unpaid")], DEFAULTS);
    expect(Object.keys(unpaid.roleConfig.model_settings.custom_roi_priors)).toEqual(["sea"]);
  });
});

