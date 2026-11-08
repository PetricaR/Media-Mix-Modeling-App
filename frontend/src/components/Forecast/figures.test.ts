import { describe, expect, it } from "vitest";
import type { RunHistory, ScenarioResult } from "../../api/types";
import {
  buildDailyVariableStackFigure,
  buildShapleyWaterfallFigure,
  buildSpendVsContributionFigure,
  buildVariableContributionBarFigure,
  plannerFigure,
  resolveVariableContributions,
} from "./figures";

const SAMPLE_RESULT: ScenarioResult = {
  method: "Meridian posterior (fitted model, no refit)",
  horizon_days: 3,
  start_date: "2025-01-02",
  end_date: "2025-01-04",
  base_total_sales: 3_000_000,
  scenario_total_sales: 3_300_000,
  delta_sales: 300_000,
  delta_sales_pct: 10.0,
  base_total_spend: 500_000,
  scenario_total_spend: 560_000,
  delta_spend: 60_000,
  marginal_roas: 5.0,
  prediction_total_lo: 3_050_000,
  prediction_total_hi: 3_550_000,
  channel_summary: [
    {
      channel: "SEA",
      slug: "sea",
      role: "media",
      multiplier: 1.2,
      base_spend: 300_000,
      scenario_spend: 360_000,
      delta_spend: 60_000,
      base_contribution: 1_500_000,
      scenario_contribution: 1_800_000,
      contribution_lo: 1_650_000,
      contribution_hi: 1_950_000,
      share_of_forecast_pct: 54.55,
      base_roi: 5.0,
      scenario_roi: 5.0,
      delta_sales: 300_000,
      delta_sales_lo: 240_000,
      delta_sales_hi: 360_000,
      delta_roas: 5.0,
    },
    {
      channel: "Display",
      slug: "display",
      role: "media",
      multiplier: 1.0,
      base_spend: 150_000,
      scenario_spend: 150_000,
      delta_spend: 0,
      base_contribution: 300_000,
      scenario_contribution: 300_000,
      contribution_lo: 250_000,
      contribution_hi: 350_000,
      share_of_forecast_pct: 9.09,
      base_roi: 2.0,
      scenario_roi: 2.0,
      delta_sales: 0,
      delta_sales_lo: 0,
      delta_sales_hi: 0,
      delta_roas: null,
    },
    {
      channel: "Affiliate",
      slug: "affiliate",
      role: "control",
      multiplier: 1.0,
      base_spend: 50_000,
      scenario_spend: 50_000,
      delta_spend: 0,
      delta_sales: null,
    },
  ],
  daily: [
    {
      date: "2025-01-02",
      baseline_forecast: 1_000_000,
      scenario_forecast: 1_100_000,
      prediction_lo: 950_000,
      prediction_hi: 1_250_000,
      planned_spend: 180_000,
    },
    {
      date: "2025-01-03",
      baseline_forecast: 1_000_000,
      scenario_forecast: 1_100_000,
      prediction_lo: 950_000,
      prediction_hi: 1_250_000,
      planned_spend: 190_000,
    },
    {
      date: "2025-01-04",
      baseline_forecast: 1_000_000,
      scenario_forecast: 1_100_000,
      prediction_lo: 950_000,
      prediction_hi: 1_250_000,
      planned_spend: 190_000,
    },
  ],
};

describe("Forecast Shapley & Input-Output figures", () => {
  it("resolves fallback variable contributions summing to 100% of forecast sales", () => {
    const vars = resolveVariableContributions(SAMPLE_RESULT);
    expect(vars.length).toBeGreaterThanOrEqual(4);
    const totalScenario = vars.reduce((acc, v) => acc + v.scenario_contribution, 0);
    expect(totalScenario).toBe(SAMPLE_RESULT.scenario_total_sales);
  });

  it("builds absolute and delta Shapley waterfall figures", () => {
    const absFig = buildShapleyWaterfallFigure(SAMPLE_RESULT, "absolute", "sea");
    expect(absFig.data.length).toBeGreaterThanOrEqual(1);
    expect(absFig.data[0].type).toBe("waterfall");

    const deltaFig = buildShapleyWaterfallFigure(SAMPLE_RESULT, "delta");
    expect(deltaFig.data[0].type).toBe("waterfall");
    expect(deltaFig.data[0].x as string[]).toContain("SEA");
    expect(deltaFig.data[0].y as number[]).toContain(300_000);
  });

  it("builds horizontal variable attribution bar figure with 90% CIs", () => {
    const vars = resolveVariableContributions(SAMPLE_RESULT);
    const fig = buildVariableContributionBarFigure(vars, true, "sea");
    expect(fig.data).toHaveLength(2);
    expect(fig.layout.barmode).toBe("group");
  });

  it("builds input spend vs output contribution figure", () => {
    const vars = resolveVariableContributions(SAMPLE_RESULT);
    const fig = buildSpendVsContributionFigure(vars);
    expect(fig.data.length).toBeGreaterThanOrEqual(2);
    expect(fig.layout.barmode).toBe("group");
  });

  it("builds daily stacked variable attribution figure in absolute and delta modes", () => {
    const absStack = buildDailyVariableStackFigure(SAMPLE_RESULT, "absolute");
    expect(absStack.data.length).toBeGreaterThanOrEqual(3);
    expect(absStack.layout.barmode).toBe("relative");

    const deltaStack = buildDailyVariableStackFigure(SAMPLE_RESULT, "delta");
    expect(deltaStack.data.length).toBeGreaterThanOrEqual(1);
  });
});

describe("Forecast trajectory with the validation and test windows", () => {
  const dates = ["2024-12-24", "2024-12-25", "2024-12-26", "2024-12-27", "2024-12-28", "2024-12-29", "2024-12-30", "2024-12-31", "2025-01-01"];
  const window = (id: "validation" | "test", from: number, to: number, forecast: number[]) => ({
    id,
    label: `${dates[from]} – ${dates[to]}`,
    start: dates[from],
    end: dates[to],
    train_start: dates[0],
    train_end: dates[from - 1],
    plan_time: true,
    dates: dates.slice(from, to + 1),
    forecast,
    lo: forecast.map((v) => v - 20),
    hi: forecast.map((v) => v + 20),
    mae_over_mean: id === "validation" ? 0.0667 : 0.02,
  });
  const history: RunHistory = {
    run_id: "run_x",
    dates,
    sales: [100, 100, 100, 100, 100, 100, 100, 100, 200],
    spend: {},
    channels: [],
    backtest: [window("validation", 2, 4, [90, 110, 100]), window("test", 5, 8, [100, 100, 100, 210])],
  };
  const build = (historyDays: number | null) =>
    plannerFigure({ history, plan: SAMPLE_RESULT, historyDays, showLastYear: false, lines: [], channelNames: {} });
  const trace = (fig: ReturnType<typeof build>, name: string) => fig.data.find((t) => String(t.name).startsWith(name));
  const labels = (fig: ReturnType<typeof build>) => (fig.layout.annotations as { text: string }[]).map((a) => a.text.trim());

  it("reads Training → Validation → Test → Forecast, each window with its forecast, interval and error vs actual", () => {
    const fig = build(null);
    expect(trace(fig, "Validation forecast")?.x).toEqual(dates.slice(2, 5));
    const test = trace(fig, "Test forecast");
    expect(test?.y).toEqual([100, 100, 100, 210]);
    expect((test?.customdata as unknown[][]).map((c) => c[0])).toEqual(["+0.0 % vs actual", "+0.0 % vs actual", "+0.0 % vs actual", "+5.0 % vs actual"]);
    expect(trace(fig, "Test 90 % interval")?.y).toEqual([80, 80, 80, 190]);
    expect(trace(fig, "Test 90 % interval")?.fill).toBe("tonexty");
    expect(labels(fig)).toEqual(
      expect.arrayContaining(["Training", "Validation · MAE / mean 6.7 %", "Test · MAE / mean 2.0 %", "Forecast"])
    );
    // The test window's own label replaces "History", which would sit inside it
    expect(labels(fig)).not.toContain("History");
  });

  it("clips the windows to the history shown and keeps headroom for the region names", () => {
    const fig = build(2);
    expect(trace(fig, "Validation forecast")).toBeUndefined();
    expect(trace(fig, "Test forecast")?.x).toEqual(["2024-12-31", "2025-01-01"]);
    expect(labels(fig)).not.toContain("Training");
    const [lo, hi] = (fig.layout.yaxis as { range: [number, number] }).range;
    expect(hi).toBeGreaterThan(1_250_000);
    expect(lo).toBeLessThan(100);
  });
});
