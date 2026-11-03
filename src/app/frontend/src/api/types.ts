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

/** Run settings beyond the channel roles; every default is the notebooks' value (served by the API). */
export interface ModelSettings {
  calendar: CalendarControls;
  n_fourier: number;
  trend: boolean;
  max_lag: number;
  baseline: "constant" | "knots" | "auto";
  n_knots: number;
  hill_before_adstock: boolean;
  media_prior_type: MediaPriorType;
  roi_prior: "meridian" | "attributed_roas" | "custom";
  roas_prior_sigma: number;
  custom_roi_priors: Record<string, RoiPrior>;
  sampling: SamplingSettings;
  forward_holdout: boolean;
  /** null: the profile decides (the full profile runs the grid) */
  model_selection: boolean | null;
  selection_shifts: number[];
}

export interface DataRoleConfig {
  target_column: string;
  date_column: string;
  channels: ChannelConfig[];
  structural_break_year: number;
  allow_single_year: boolean;
  holdout_days?: number;
  drop_trailing_isolated_days?: boolean;
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
  profiles: Record<"full" | "quick" | "smoke", SamplingSettings>;
  limits: Record<string, Bounds>;
  meridian_roi_prior: RoiPrior;
  selection_shift_choices: number[];
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
  /** Settings that differ from the notebook defaults */
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

export interface ScenarioChannelRow {
  channel: string;
  slug: string;
  multiplier: number;
  base_spend: number;
  scenario_spend: number;
  delta_spend: number;
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
  /** 90 % credible interval of delta_sales (Meridian posterior only) */
  delta_sales_lo?: number | null;
  delta_sales_hi?: number | null;
  prediction_lo_total?: number;
  prediction_hi_total?: number;
  channel_summary: ScenarioChannelRow[];
  /** Daily spend per channel slug: the plan as submitted, and the scenario's plan */
  plan_daily?: Record<string, number[]>;
  scenario_plan_daily?: Record<string, number[]>;
  /** Each media channel's mean change in daily sales */
  channel_daily_delta?: Record<string, number[]>;
  figure?: PlotlySpec;
  daily: ScenarioDailyRow[];
}

export interface RunHistory {
  run_id: string;
  dates: string[];
  sales: (number | null)[];
  spend: Record<string, (number | null)[]>;
  channels: { slug: string; channel: string }[];
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

export interface OptimisationChannelRow {
  channel: string;
  label?: string;
  historical_spend?: number;
  current_spend?: number;
  optimised_spend: number;
  delta_spend: number;
  delta_pct: number;
  multiplier: number;
  min_bound: number;
  max_bound: number;
  historical_incremental?: number;
  current_incremental?: number;
  optimised_incremental: number;
  delta_incremental: number;
  historical_roi?: number;
  current_roi?: number;
  optimised_roi: number;
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
  result?: {
    total_budget: number;
    historical_spend_total?: number;
    historical_budget?: number;
    historical_incremental_revenue: number;
    optimised_incremental_revenue: number;
    uplift_eur: number;
    uplift_pct: number;
    include_catalog: boolean;
    channels?: OptimisationChannelRow[];
    allocations?: OptimisationChannelRow[];
  } | null;
}

export interface ArtifactItem {
  path: string;
  category: string;
  filename: string;
  url: string;
}
