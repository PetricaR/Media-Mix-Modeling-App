export type ChannelRole =
  | "paid"
  | "print_event"
  | "commission"
  | "unpaid"
  | "ignored"
  | "digital_paid"
  | "control";

export type Profile = "full" | "quick" | "smoke" | "custom";
export type AdstockShape = "geometric" | "binomial";
export type MediaPriorType = "roi" | "mroi" | "contribution" | "coefficient";

export interface ChannelConfig {
  name: string;
  slug?: string;
  role: ChannelRole;
  enabled?: boolean;
  cost_column?: string | null;
  exposure_column?: string | null;
  exposure_type?: string | null;
  rationale?: string;
  total_cost?: number;
  cost_days?: number;
  in_home_shift_days?: number | null;
  /** null: the role's default shape (binomial for print events, geometric otherwise) */
  adstock_shape?: AdstockShape | null;
  saturation?: "hill" | "none";
  /** The role the app inferred from the workbook */
  inferred_role?: ChannelRole | null;
  /** Cost billed per click that follows demand: its ROI is an upper bound (null = infer from the data) */
  demand_following?: boolean | null;
  inferred_demand_following?: boolean | null;
}

export interface CalendarControls {
  weekdays: boolean;
  holidays: boolean;
  christmas: boolean;
  black_week: boolean;
}

export interface SamplingSettings {
  n_chains: number;
  n_adapt: number;
  n_burnin: number;
  n_keep: number;
  n_prior: number;
}

export interface RoiPrior {
  median: number;
  sigma: number;
}

/** Run settings beyond the channel roles; every default is served by the API. */
export interface ModelSettings {
  calendar: CalendarControls;
  n_fourier: number;
  trend: boolean;
  /** A 0/1 control per channel with missing workbook rows (off: missing channel-days count as 0 sales) */
  missing_row_controls: boolean;
  /** Temperature, sunshine and precipitation vs normal for the run's country (Open-Meteo) */
  weather_controls?: boolean;
  max_lag: number;
  baseline: "constant" | "knots" | "auto";
  n_knots: number;
  hill_before_adstock: boolean;
  media_prior_type: MediaPriorType;
  roi_prior: "meridian" | "attributed_roas" | "total_media_contribution" | "custom";
  roas_prior_sigma: number;
  total_media_contribution_mean?: number;
  total_media_contribution_sd?: number;
  custom_roi_priors: Record<string, RoiPrior>;
  sampling: SamplingSettings;
  forward_holdout: boolean;
  /** Score every window as a forecast from a plan made the day before it and fit the final model on every day (the
   * default); off scores conventional hold-outs. Missing on runs stored before 2026-11-08: treat as on. */
  leak_free_validation?: boolean;
  /** null: the profile decides (the full profile runs the grid) */
  model_selection: boolean | null;
  selection_shifts: number[];
}

/** One channel of a wide table (one row per day): its name and the columns holding its daily values. */
export interface WideChannel {
  name: string;
  spend: string;
  impressions?: string | null;
  clicks?: string | null;
  sales?: string | null;
}

/** A column of the upload used as a control variable (a Covid flag, a price index, or a categorical promotion column). */
export interface ControlColumn {
  column: string;
  name: string;
  categories?: string[];
}

export type Layout = "long" | "wide";

/** Where each MMM input comes from in the upload (long: one row per date and channel; wide: one row per day). */
export interface ColumnMapping {
  layout: Layout;
  date: string;
  channel: string;
  sales: string;
  costs: string;
  impressions?: string | null;
  clicks?: string | null;
  target?: string | null;
  wide_channels: WideChannel[];
  controls: ControlColumn[];
  /** How the rows reach the daily grid and how gaps are filled (the wizard's "Resample & fill" step). */
  resample?: ResampleConfig;
}

export type SourceFrequency = "auto" | "sub_daily" | "daily" | "weekly" | "monthly";
export type AggregateMethod = "sum" | "mean" | "median" | "min" | "max" | "first" | "last" | "mode";
export type FillMethod =
  | "none"
  | "zero"
  | "constant"
  | "baseline"
  | "ffill"
  | "bfill"
  | "nearest"
  | "linear"
  | "quadratic"
  | "cubic"
  | "pchip"
  | "akima"
  | "mean"
  | "median"
  | "mode"
  | "moving_average"
  | "seasonal"
  | "spread";
export type DisaggregateMethod = "even" | "smooth" | "first_day" | "repeat" | "linear";
export type ResampleOp = "aggregate" | "fill" | "disaggregate";
/** What a mapped column is to the resampling: an amount per day, a level, a 0/1 flag or a text category. */
export type ResampleKind = "flow" | "level" | "flag" | "category";

/** One mapped column's methods; a method left out keeps the role's default. */
export interface ColumnResample {
  column: string;
  aggregate?: AggregateMethod | null;
  fill?: FillMethod | null;
  fill_value?: number | null;
  disaggregate?: DisaggregateMethod | null;
}

export interface ResampleConfig {
  frequency: SourceFrequency;
  /** Weekly rows: the date is the first or the last day of its week. */
  period_label: "start" | "end";
  /** Add the days, weeks or months without a row and fill them like blank cells. */
  fill_missing_dates: boolean;
  columns: ColumnResample[];
}

export interface ResampleMethodOption {
  value: string;
  label: string;
  help: string;
  kinds?: ResampleKind[];
}

/** Every frequency and method the resampling offers (`core/resample.method_catalog`). */
export interface ResampleOptions {
  frequencies: ResampleMethodOption[];
  aggregate: ResampleMethodOption[];
  fill: ResampleMethodOption[];
  disaggregate: ResampleMethodOption[];
  max_blank_share: number;
}

/** How the dates of the upload are spaced (`core/resample.describe_timestamps`). */
export interface TimeGrid {
  detected: "sub_daily" | "daily" | "weekly" | "monthly" | "irregular" | "unknown";
  rows: number;
  dates: number;
  first?: string;
  last?: string;
  has_time?: boolean;
  rows_per_date?: number;
  repeated_dates?: number;
  step_days?: number | null;
  missing_dates?: number;
  missing_examples?: string[];
  longest_gap?: number;
  weekdays_present?: number[];
  weekday?: string;
  shifted?: number;
  anchor?: "start" | "end" | "mixed";
  repeated_months?: number;
}

/** A mapped column as the "Resample & fill" step offers it. */
export interface ResampleColumnChoice {
  column: string;
  role: string;
  label: string;
  kind: ResampleKind;
  blank: number;
  defaults: Record<ResampleOp, string>;
  allowed: Record<ResampleOp, string[]>;
}

/** What the resampling did to one mapped column. */
export interface ResampleColumnReport {
  column: string;
  role: string;
  label: string;
  kind: ResampleKind;
  aggregate: AggregateMethod;
  fill: FillMethod;
  fill_value: number | null;
  disaggregate: DisaggregateMethod;
  explicit: ResampleOp[];
  defaults: Record<ResampleOp, string>;
  allowed: Record<ResampleOp, string[]>;
  rows_merged: number;
  blank: number;
  added: number;
  filled: number;
  nearest_fallback: number;
  spread_values: number;
  periods: number;
  imputed_days: number;
}

export interface ResampleReport {
  grid: TimeGrid & {
    frequency: "sub_daily" | "daily" | "weekly" | "monthly";
    period_label: "start" | "end";
    periods: number;
    added: number;
    added_examples: string[];
    days: number;
    first_day: string | null;
    last_day: string | null;
    merged_rows: number;
  };
  columns: ResampleColumnReport[];
  /** The daily values for the preview chart; `imputed` holds the positions of imputed days. */
  series: { dates: string[]; columns: { column: string; label: string; values: (number | null)[]; imputed: number[] }[] };
}

/** `POST /uploads/resample-recommendation`: Gemini's frequency and per-column methods, every item checked. */
export interface ResampleRecommendation {
  summary: string;
  resample: ResampleConfig;
  column_notes: Record<string, { reason: string; confidence: "high" | "medium" | "low" }>;
  rejected: { item: string; reason: string }[];
  errors: string[];
  unreviewed: string[];
  frequency_reason: string;
  period_label_reason: string;
  missing_dates_reason: string;
  notes: string[];
  model: string;
  generated_at?: string | null;
  cached: boolean;
  unverified_numbers: string[];
}

export interface DataRoleConfig {
  target_column?: string;
  date_column?: string;
  sheet_name?: string | null;
  columns?: ColumnMapping;
  channels: ChannelConfig[];
  holdout_days?: number;
  drop_trailing_isolated_days?: boolean;
  /** ISO code of the market: its Google Calendar public holidays and Open-Meteo weather */
  country?: string;
  model_settings?: ModelSettings;
}

export interface Bounds {
  min?: number;
  max?: number;
  min_exclusive?: number;
  max_exclusive?: number;
}

export interface SettingsDefaults {
  model_settings: ModelSettings;
  holdout_days: number;
  drop_trailing_isolated_days: boolean;
  seed: number;
  in_home_shift_days: number;
  /** Training days a run needs before its hold-out */
  min_training_days?: number;
  profiles: Record<"full" | "quick" | "smoke", SamplingSettings>;
  limits: Record<string, Bounds>;
  meridian_roi_prior: RoiPrior;
  selection_shift_choices: number[];
  countries?: { code: string; name: string }[];
  country?: string;
  /** Weather control column → display name */
  weather_controls?: Record<string, string>;
  /** Frequency options for resampling onto the daily model grid */
  frequencies?: ResampleMethodOption[];
}

export interface SettingChange {
  setting: string;
  value: string;
  default: string;
}

export interface DataProfile {
  filename: string;
  sha256: string;
  sheet_names: string[];
  selected_sheet?: string | null;
  n_rows: number;
  n_columns: number;
  date_min: string;
  date_max: string;
  raw_row_count?: number;
  start_date?: string;
  end_date?: string;
  calendar_days?: number;
  modelling_days?: number;
  modelling_end_date?: string;
  trailing_isolated_dates?: string[];
  years_present: number[];
  missing_dates_count: number;
  duplicate_rows_count: number;
  negative_values_count: number;
  total_sales_sum: number;
  total_media_spend_sum: number;
  role_config: DataRoleConfig;
  warnings: string[];
  layout?: Layout;
  has_channel_sales?: boolean;
  control_names?: string[];
  default_holdout_days?: number;
  max_holdout_days?: number | null;
  /** Prior chance that media earn more than all sales under Meridian's default ROI prior (null: no media spend) */
  prior_media_over_100_prob?: number | null;
  /** The ROI prior a new run of this data starts with: total media contribution when the default prior is implausible */
  default_roi_prior?: ModelSettings["roi_prior"];
}

export type ColumnKind = "date" | "numeric" | "text" | "empty";
type Cell = string | number | boolean | null;

/** What the upload wizard knows about one column of the chosen sheet. */
export interface ColumnProfile {
  name: string;
  kind: ColumnKind;
  non_null: number;
  blank: number;
  distinct: number;
  min?: Cell;
  max?: Cell;
  mean?: number | null;
  sum?: number | null;
  zero_share?: number | null;
  negative?: number;
  binary?: boolean;
  distinct_dates?: number;
  step_days?: number | null;
  categories?: string[];
  baseline?: string | null;
  samples: Cell[];
}

/** `POST /uploads/inspect`: the upload before a run exists. */
export interface UploadInspection {
  filename: string;
  format: string;
  sheets: { name: string; rows: number; columns: number; has_date: boolean }[];
  selected_sheet: string;
  n_rows: number;
  columns: ColumnProfile[];
  sample_rows: Record<string, Cell>[];
  side_tables: { sheet: string; columns: string[]; rows: Record<string, Cell>[]; n_rows: number }[];
  mapping: ColumnMapping;
  suggested: boolean;
  errors: string[];
  /** The errors (a subset of `errors`) the "Resample & fill" step can fix: gaps, cadence, blanks, repeated dates. */
  prep_errors?: string[];
  time_grid?: TimeGrid | null;
  resample?: ResampleReport | null;
  /** Every mapped column's kind, blanks, default and allowed methods (known before the data can run). */
  resample_columns?: ResampleColumnChoice[];
  resample_options?: ResampleOptions;
  preview: DataProfile | null;
  min_days: number;
}

export type MappingRole = "date" | "target" | "spend" | "impressions" | "clicks" | "sales" | "control" | "ignore" | "channel" | "costs";

/** `POST /uploads/recommendation`: Gemini's mapping, every item checked against the sheet. */
export interface MappingRecommendation {
  sheet: string;
  summary: string;
  mapping: ColumnMapping;
  channel_roles: { name: string; role: ChannelRole; reason: string; confidence: "high" | "medium" | "low" }[];
  column_notes: Record<string, { role: MappingRole; reason: string; channel?: string }>;
  rejected: { item: string; reason: string }[];
  errors: string[];
  unreviewed: string[];
  country: string | null;
  country_reason: string;
  better_sheet: string | null;
  sheet_reason: string;
  notes: string[];
  model: string;
  generated_at?: string | null;
  cached: boolean;
  unverified_numbers: string[];
}

export interface StageStatus {
  key: string;
  label: string;
  queue: string;
  status: "pending" | "running" | "succeeded" | "failed" | "skipped" | "cancelled";
  started_at?: string | null;
  finished_at?: string | null;
  error?: string | null;
}

/** `GET /health`. */
/** `GET /auth/session`. */
export interface AuthSession {
  auth_required: boolean;
  authenticated: boolean;
}

export interface HealthInfo {
  status: string;
  version: string;
  git_sha?: string | null;
  auth_required: boolean;
  max_meridian_queued?: number;
  /** Gemini explanations of charts and tables are configured on the server. */
  explain_available?: boolean;
  explain_model?: string | null;
}

/** One chart or table as the viewer sees it, with the text around it (`POST /explain`). */
export interface ExplainRequest {
  kind: "chart" | "table";
  title: string;
  subtitle?: string | null;
  caption?: string | null;
  /** The page's sidebar label. */
  page?: string | null;
  /** The selected run: the server adds its dates, channel roles and validation results. */
  run_id?: string | null;
  section_title?: string | null;
  section_subtitle?: string | null;
  /** Narrative, callouts and KPIs shown on the same page. */
  notes?: string | null;
  figure?: { data: Record<string, unknown>[]; layout: Record<string, unknown> };
  table?: { columns: string[]; rows: (string | number | boolean | null)[][] };
}

export interface ExplainResponse {
  /** Markdown. */
  explanation: string;
  model: string;
  generated_at: string;
  /** Numbers in the explanation that the chart, table or page text does not contain. */
  unverified_numbers: string[];
  cached: boolean;
}

/** What Gemini's settings recommendation optimises for. */
export type RecommendationGoal = "balanced" | "accuracy" | "speed";

/** A settings-form tab ("run" is the Meridian switch above the tabs). */
export type SettingTab = "run" | "data" | "channels" | "model" | "priors" | "sampling";

/** `POST /runs/{id}/recommendation`: the goal and the form's current (unsaved) settings. */
export interface RecommendationRequest {
  goal: RecommendationGoal;
  profile?: Profile;
  skip_meridian?: boolean;
  seed?: number;
  role_config?: DataRoleConfig;
}

/**
 * One checked change. `setting` is a key such as `max_lag`, `calendar.weekdays`, `sampling.n_keep`,
 * `channels.sea.role` or `custom_roi_priors.sea.median`.
 */
export interface RecommendedChange {
  setting: string;
  label: string;
  tab: SettingTab;
  value: boolean | number | string | number[];
  current: string;
  recommended: string;
  reason: string;
  confidence: "high" | "medium" | "low";
}

/** A setting Gemini reviewed and keeps at its current value (the tab note says why). */
export interface KeptSetting {
  setting: string;
  label: string;
  tab: SettingTab;
  current: string;
  reason: string;
  confidence: "high" | "medium" | "low";
}

/** A suggestion the server could not apply (unknown setting, invalid value, or one that breaks a run rule). */
export interface RejectedChange {
  setting: string;
  label: string;
  tab: SettingTab | null;
  value: string;
  reason: string;
  problem: string;
}

export interface SettingsRecommendation {
  summary: string;
  /** In application order (run mode, channel roles, the rest); every one passed the run-start checks. */
  changes: RecommendedChange[];
  kept: KeptSetting[];
  rejected: RejectedChange[];
  /** Active settings (those that affect the run) Gemini reviewed, and the labels of any it skipped. */
  coverage: { reviewed: number; active: number; missing: string[] };
  /** Per tab: why the settings Gemini keeps there stay. */
  tab_notes: Partial<Record<SettingTab, string>>;
  goal: RecommendationGoal;
  /** Whose results Gemini read: this run's, an earlier run's on the same workbook, or only the workbook profile. */
  evidence: { kind: "this_run" | "same_workbook" | "profile_only"; run_id: string | null; run_name: string | null; sections: string[] };
  model: string;
  generated_at: string | null;
  cached: boolean;
  /** Numbers in the summary, reasons or notes that the data, results and settings sent to Gemini do not contain. */
  unverified_numbers: string[];
}

export interface RunSummaryItem {
  id: string;
  name: string;
  filename: string;
  sha256: string;
  profile: Profile;
  status: "draft" | "queued" | "running" | "succeeded" | "failed" | "cancelled";
  is_active: boolean;
  created_at?: string | null;
  updated_at?: string | null;
  heartbeat_at?: string | null;
  error?: string | null;
  summary?: Record<string, unknown> | null;
  config?: {
    role_config?: DataRoleConfig;
    skip_meridian?: boolean;
    seed?: number;
  } | null;
  /** Settings that differ from the defaults */
  settings_changes?: SettingChange[];
}

export interface RunDetail extends RunSummaryItem {
  schema?: DataProfile | null;
  queue_position?:
    | number
    | null
    | {
        analysis?: number | null;
        meridian?: number | null;
      };
  stages: StageStatus[];
}

export interface KpiCard {
  label: string;
  value: string;
  delta?: string | null;
  tone?: "neutral" | "good" | "warn" | "bad" | "info" | "success" | "danger";
  status?: "neutral" | "good" | "warn" | "bad" | "info" | "success" | "danger" | null;
  help?: string | null;
  note?: string | null;
}

export interface KpisBlock {
  type: "kpis";
  id?: string | null;
  items: KpiCard[];
}

export interface CalloutBlock {
  type: "callout";
  tone: "info" | "good" | "warn" | "bad" | "success" | "danger";
  title: string;
  body: string;
}

export interface MarkdownBlock {
  type: "markdown";
  title?: string | null;
  body: string;
}

export interface PlotlySpec {
  data: Record<string, unknown>[];
  layout: Record<string, unknown>;
  variants?: Record<string, { data: Record<string, unknown>[]; layout: Record<string, unknown> }>;
}

export interface ChartBlock {
  type: "chart";
  id?: string;
  chart_id: string;
  title: string;
  subtitle?: string | null;
  caption?: string | null;
  note?: string | null;
  height?: number;
  figure: PlotlySpec;
}

export interface TableColumn {
  key: string;
  label: string;
  align?: "left" | "right" | "center";
  format?: string | null;
}

export interface TableBlock {
  type: "table";
  id?: string;
  table_id: string;
  title: string;
  subtitle?: string | null;
  caption?: string | null;
  note?: string | null;
  columns: TableColumn[];
  rows: Record<string, unknown>[];
}

export type SectionBlock = KpisBlock | CalloutBlock | MarkdownBlock | ChartBlock | TableBlock;

export interface Section {
  id: string;
  title: string;
  subtitle?: string | null;
  blocks: SectionBlock[];
  metadata?: Record<string, unknown>;
}

export interface SectionIndexItem {
  id: string;
  title: string;
  subtitle?: string | null;
  n_blocks?: number;
}

export interface ForecastItem {
  id: string;
  run_id: string;
  name: string;
  status: string;
  plan_path?: string | null;
  result_path?: string | null;
  created_at?: string | null;
  error?: string | null;
  summary?: Record<string, unknown> | null;
  queue_position?: number | null;
  result?: ScenarioResult | null;
}

export type VariableContributionCategory =
  | "media"
  | "control_channel"
  | "seasonality"
  | "calendar"
  | "trend"
  | "baseline";

export interface VariableContributionRow {
  key: string;
  label: string;
  category: VariableContributionCategory;
  editable: boolean;
  multiplier: number;
  base_spend?: number | null;
  scenario_spend?: number | null;
  delta_spend?: number | null;
  base_contribution: number;
  base_ci_lo?: number | null;
  base_ci_hi?: number | null;
  scenario_contribution: number;
  scenario_ci_lo?: number | null;
  scenario_ci_hi?: number | null;
  delta_contribution: number;
  delta_ci_lo?: number | null;
  delta_ci_hi?: number | null;
  base_share_pct: number;
  scenario_share_pct: number;
  base_roi?: number | null;
  roi?: number | null;
  mroi?: number | null;
  daily_base: number[];
  daily_scenario: number[];
  daily_delta: number[];
}

export interface ScenarioChannelRow {
  channel: string;
  slug: string;
  role?: "media" | "control";
  multiplier: number;
  base_spend: number;
  scenario_spend: number;
  delta_spend: number;
  base_contribution?: number | null;
  scenario_contribution?: number | null;
  contribution_lo?: number | null;
  contribution_hi?: number | null;
  share_of_forecast_pct?: number | null;
  base_roi?: number | null;
  scenario_roi?: number | null;
  /** The channel's own change in forecast sales; null for a control channel (no sales effect of its own) */
  delta_sales?: number | null;
  delta_sales_lo?: number | null;
  delta_sales_hi?: number | null;
  delta_roas?: number | null;
}

export interface ScenarioDailyRow {
  date: string;
  baseline_forecast: number;
  scenario_forecast: number;
  prediction_lo: number;
  prediction_hi: number;
  planned_spend: number;
}

export interface ScenarioResult {
  method?: string;
  horizon_days: number;
  start_date?: string | null;
  end_date?: string | null;
  base_total_sales: number;
  scenario_total_sales: number;
  delta_sales: number;
  delta_sales_pct: number;
  base_total_spend: number;
  scenario_total_spend: number;
  delta_spend: number;
  marginal_roas?: number | null;
  media_base_sales?: number | null;
  media_scenario_sales?: number | null;
  media_share_pct?: number | null;
  baseline_and_controls_sales?: number | null;
  baseline_and_controls_share_pct?: number | null;
  media_base_spend?: number | null;
  media_scenario_spend?: number | null;
  base_media_roi?: number | null;
  media_roi?: number | null;
  /** 90 % credible interval of delta_sales (Meridian posterior only) */
  delta_sales_lo?: number | null;
  delta_sales_hi?: number | null;
  /** 90 % prediction interval of the total over the shown days (quantiles of the draws' sums) */
  prediction_total_lo?: number | null;
  prediction_total_hi?: number | null;
  /** Sums of the daily 5 % / 95 % bounds (much wider than an interval of the total) */
  sum_daily_prediction_lo?: number;
  sum_daily_prediction_hi?: number;
  /** Share of back-test days inside their daily interval (the band's measured coverage) */
  backtest_coverage?: number | null;
  channel_summary: ScenarioChannelRow[];
  /** Additive (Shapley-style) decomposition across all variables (baseline, trend, seasonality, calendar, controls, paid media) */
  variable_contributions?: VariableContributionRow[];
  /** Daily spend per channel slug: the plan as submitted, and the scenario's plan */
  plan_daily?: Record<string, number[]>;
  scenario_plan_daily?: Record<string, number[]>;
  /** Each media channel's mean change in daily sales */
  channel_daily_delta?: Record<string, number[]>;
  figure?: PlotlySpec;
  daily: ScenarioDailyRow[];
}

/**
 * A past window forecast exactly like a budget plan (rolling origin): by a model refit on every day before it
 * (`train_start`..`train_end`), with the window's sales unknown and its actual spend as the plan. `validation` is the
 * quarter before the test window; `test` is the last `holdout_days` days of the data.
 */
export interface BacktestWindow {
  id: "validation" | "test";
  /** "Q4 2024", or a date range when the window is not a whole quarter */
  label: string;
  start: string;
  end: string;
  train_start: string;
  train_end: string;
  /** A commission control was set to its plannable level (the realised one leaks the sales it was paid on) */
  plan_time: boolean;
  dates: string[];
  forecast: (number | null)[];
  /** 90 % prediction interval, the same method as the plan's band */
  lo: (number | null)[];
  hi: (number | null)[];
  /** MAE / mean of the forecast over the window */
  mae_over_mean: number | null;
}

export interface RunHistory {
  run_id: string;
  dates: string[];
  sales: (number | null)[];
  spend: Record<string, (number | null)[]>;
  channels: { slug: string; channel: string }[];
  /** Validation then test, as far as the run produced them; absent from older API responses */
  backtest?: BacktestWindow[];
}

export interface ScenarioDetail {
  id: string;
  run_id: string;
  forecast_id: string;
  name: string;
  status: string;
  inputs?: Record<string, unknown> | null;
  summary?: Record<string, unknown> | null;
  error?: string | null;
  created_at?: string | null;
  result?: ScenarioResult | null;
}

export type BudgetConstraintMode = "fixed" | "target_roi" | "target_mroi";

export interface OptimisationChannelRow {
  channel: string;
  label?: string;
  channel_label?: string;
  historical_spend?: number;
  current_spend?: number;
  optimised_spend: number;
  optimal_spend?: number;
  spend_delta?: number;
  delta_spend: number;
  spend_delta_pct?: number;
  delta_pct: number;
  multiplier: number;
  min_bound: number;
  max_bound: number;
  lower_bound_ratio?: number;
  upper_bound_ratio?: number;
  min_bound_eur?: number;
  max_bound_eur?: number;
  spend_share_current?: number;
  spend_share_optimised?: number;
  historical_incremental?: number;
  current_incremental?: number;
  current_inc_sales?: number;
  optimised_incremental: number;
  optimised_inc_sales?: number;
  expected_incremental_sales?: number;
  optimised_inc_lo?: number;
  optimised_inc_hi?: number;
  inc_sales_delta?: number;
  delta_incremental: number;
  inc_share_current?: number;
  inc_share_optimised?: number;
  historical_roi?: number;
  current_roi?: number;
  optimised_roi: number;
  roi?: number;
  mroi?: number;
}

export interface OptimisationResult {
  feasible?: boolean;
  budget_mode?: BudgetConstraintMode;
  target_value?: number | null;
  method?: string;
  total_budget: number;
  historical_spend_total?: number;
  historical_budget?: number;
  budget_delta?: number;
  budget_delta_pct?: number;
  current_inc_sales?: number;
  optimised_inc_sales?: number;
  historical_incremental_revenue: number;
  optimised_incremental_revenue: number;
  historical_roi?: number;
  optimised_roi?: number;
  roi_delta?: number;
  lift_eur?: number;
  lift_pct?: number;
  uplift_eur: number;
  uplift_pct: number;
  include_catalog?: boolean;
  include_demand_following?: boolean;
  fixed_channels?: string[];
  upper_bound_channels?: string[];
  upper_bound_lift_share?: number;
  conditional?: boolean;
  unconstrained?: {
    lift_eur: number;
    lift_pct: number;
    method: string;
  };
  channels?: OptimisationChannelRow[];
  allocations?: OptimisationChannelRow[];
}

export interface OptimisationDetail {
  id: string;
  run_id: string;
  name?: string;
  status: string;
  inputs?: Record<string, unknown> | null;
  summary?: Record<string, unknown> | null;
  error?: string | null;
  created_at?: string | null;
  result?: OptimisationResult | null;
}

export interface OptimiserChannelMeta {
  channel: string;
  label: string;
  role: string;
  demand_following: boolean;
  is_print: boolean;
  historical_spend: number;
  historical_incremental: number;
  historical_roi: number;
  historical_mroi: number;
  roi_lo: number;
  roi_hi: number;
  p_roi_below_1?: number | null;
  curve_max_spend: number;
}

export interface ResponseCurvePoint {
  multiplier: number;
  spend: number;
  mean: number;
  ci_lo: number;
  ci_hi: number;
}

export interface OptimiserContext {
  run_id: string;
  start_date: string;
  end_date: string;
  modelling_days: number;
  historical_budget: number;
  historical_incremental: number;
  historical_roi: number;
  upper_bound_channels: string[];
  print_channels: string[];
  channels: OptimiserChannelMeta[];
  response_curves: Record<string, ResponseCurvePoint[]>;
  default_constrained: OptimisationResult;
  default_unconstrained: OptimisationResult;
}

export interface ArtifactItem {
  path: string;
  category: string;
  filename: string;
  url: string;
}
