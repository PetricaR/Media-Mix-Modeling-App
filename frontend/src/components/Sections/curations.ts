import type { SectionCuration } from "./curation";

/**
 * What each page shows of its section (see `curation.ts`). Every page leads with its decision-relevant blocks; the
 * secondary material that repeats or deepens them sits under "More analysis". Each number has one home:
 *
 * - Naive baselines: Exploration › Readiness (the benchmark table keeps its naive row as the reference).
 * - Meridian accuracy: Fit & validation › Fit; the model comparison: › Benchmark; the plan-time back-test: › Back-test.
 * - Diagnostics (health checks, R-hat, selection, sensitivity, settings): Fit & validation › Diagnostics.
 * - The example plan and its scenarios: Forecast & scenarios (collapsed under the planner).
 * - The executive brief answers: Report › Answers to the brief; headline figures: Summary.
 */
export const CURATIONS: Record<string, SectionCuration> = {
  data_quality: {
    primary: ["kpis:0", "callout:0", "fig01_completeness", "quality_anomaly_inventory"],
  },

  explore_target: {
    primary: ["kpis:0", "target_timeseries", "target_seasonality", "target_stl", "events_lift", "markdown:0"],
  },
  explore_channels: {
    primary: ["kpis:0", "channel_share_growth", "channel_mix_monthly"],
    pairs: { channel_summary_table: "channel_share_growth", channel_mix_table: "channel_mix_monthly" },
  },
  explore_spend: {
    primary: ["kpis:0", "callout:0", "spend_vs_sales_share", "spend_vs_sales_time"],
    pairs: { spend_summary_table: "spend_vs_sales_share", spend_weekday_eur: "spend_weekday_index" },
  },
  explore_efficiency: {
    primary: ["kpis:0", "callout:0", "efficiency_kpis", "social_regime_change", "affiliate_commission"],
    pairs: { efficiency_quarterly: "efficiency_kpis", social_regime_table: "social_regime_change" },
  },
  explore_correlations: {
    primary: ["kpis:0", "callout:0", "lag_correlation", "corr_cost_and_sales_vs_total"],
    pairs: { corr_vif_table: "lag_correlation", corr_robustness_table: "corr_robustness" },
  },
  explore_catalog: {
    primary: ["kpis:0", "callout:0", "callout:1", "catalog_event_study", "catalog_halo", "catalog_natural_experiment"],
    pairs: { catalog_event_table: "catalog_event_study", catalog_editions_table: "catalog_sendouts" },
  },
  explore_readiness: {
    primary: ["kpis:0", "callout:0", "readiness_baselines", "readiness_channel_diagnostics", "readiness_implications_table"],
    pairs: { readiness_baselines_table: "readiness_baselines" },
  },

  preparation: {
    primary: [
      "kpis:0",
      "prep_cost_considerations",
      "prep_new_features",
      "prep_r2_ladder",
      "callout:0",
      "prep_meridian_roles",
      "callout:1",
    ],
    pairs: {
      prep_ladder_table: "prep_r2_ladder",
      prep_shift_scan_table: "prep_shift_scan",
      prep_channel_importance_table: "prep_channel_importance",
    },
  },

  // Fit & validation is four tabs over three sections; the model section is split between Fit and Diagnostics
  meridian_model_fit: {
    primary: ["kpis:0", "meridian_fit_full_period", "meridian_accuracy_table", "meridian_residuals"],
    omit: [
      "meridian_selection_table",
      "meridian_sensitivity_table",
      "meridian_health_checks_table",
      "meridian_rhat_table",
      "callout:0",
    ],
  },
  meridian_model_diagnostics: {
    primary: [
      "meridian_health_checks_table",
      "meridian_rhat_table",
      "meridian_selection_table",
      "meridian_sensitivity_table",
      "callout:0",
    ],
    rest: "omit",
  },
  benchmark: {
    primary: ["kpis:0", "bench_model_comparison", "bench_holdout_trajectory", "bench_walk_forward_cv", "callout:0"],
    pairs: { bench_holdout_table: "bench_model_comparison", bench_walk_forward_table: "bench_walk_forward_cv" },
  },
  forecast_backtest: {
    primary: ["forecast_accuracy_kpis", "forecast_backtest_q4", "markdown:0"],
    rest: "omit",
  },

  drivers_roi: {
    primary: [
      "kpis:0",
      "callout:0",
      "callout:1",
      "drivers_roi_chart",
      "drivers_weekly_decomp",
      "drivers_response_curves",
      "drivers_adstock_curves",
    ],
    pairs: { drivers_roi_table: "drivers_roi_chart" },
    tableFirst: ["drivers_roi_chart"],
  },

  // Under the forecast planner: the example plan and its scenarios (the back-test lives on Fit & validation)
  forecast_example: {
    primary: ["forecast_plan_kpis", "forecast_2025_01_chart", "forecast_scenarios_chart", "forecast_weekly_plan_table"],
    pairs: {
      forecast_weekly_summary_table: "forecast_2025_01_chart",
      forecast_scenarios_table: "forecast_scenarios_chart",
    },
    omit: ["forecast_accuracy_kpis", "forecast_backtest_q4", "markdown:0"],
    rest: "omit",
  },

  // The brief's KPIs repeat the Summary; its answers, roadmap and limits are the deliverable
  brief: {
    primary: ["brief_qa_summary_table", "brief_experiments_roadmap_table", "brief_gcp_mlops_table", "markdown:0"],
    omit: ["kpis:0"],
  },
};
