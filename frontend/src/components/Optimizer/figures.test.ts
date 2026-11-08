import { describe, expect, it } from "vitest";
import {
  buildAllResponseCurvesGridFigure,
  buildBudgetComparisonFigure,
  buildMarginalRoiCurveFigure,
  buildOutcomeComparisonFigure,
  buildSingleResponseCurveFigure,
  buildSpendDeltaFigure,
  buildSpendDonutFigure,
} from "./figures";
import type {
  OptimisationChannelRow,
  OptimisationResult,
  OptimiserChannelMeta,
  ResponseCurvePoint,
} from "../../api/types";

const sampleRows: OptimisationChannelRow[] = [
  {
    channel: "display",
    label: "Display",
    current_spend: 29_800_000,
    optimised_spend: 20_900_000,
    delta_spend: -8_900_000,
    delta_pct: -29.9,
    multiplier: 0.701,
    min_bound: 0.7,
    max_bound: 1.3,
    min_bound_eur: 20_860_000,
    max_bound_eur: 38_740_000,
    current_inc_sales: 19_900_000,
    optimised_incremental: 16_700_000,
    delta_incremental: -3_200_000,
    historical_roi: 0.67,
    optimised_roi: 0.8,
    mroi: 0.36,
  },
  {
    channel: "catalog",
    label: "Catalog",
    current_spend: 38_600_000,
    optimised_spend: 49_000_000,
    delta_spend: 10_400_000,
    delta_pct: 26.9,
    multiplier: 1.269,
    min_bound: 0.7,
    max_bound: 1.3,
    min_bound_eur: 27_020_000,
    max_bound_eur: 50_180_000,
    current_inc_sales: 37_900_000,
    optimised_incremental: 45_800_000,
    delta_incremental: 7_900_000,
    historical_roi: 0.98,
    optimised_roi: 0.93,
    mroi: 0.76,
  },
  {
    channel: "sea",
    label: "SEA",
    current_spend: 38_500_000,
    optimised_spend: 38_500_000,
    delta_spend: 0,
    delta_pct: 0,
    multiplier: 1.0,
    min_bound: 1.0,
    max_bound: 1.0,
    min_bound_eur: 38_500_000,
    max_bound_eur: 38_500_000,
    current_inc_sales: 328_500_000,
    optimised_incremental: 328_500_000,
    delta_incremental: 0,
    historical_roi: 8.53,
    optimised_roi: 8.53,
    mroi: 4.12,
  },
];

const sampleResult: OptimisationResult = {
  total_budget: 108_400_000,
  historical_budget: 106_900_000,
  historical_incremental_revenue: 386_300_000,
  optimised_incremental_revenue: 391_000_000,
  uplift_eur: 4_700_000,
  uplift_pct: 1.22,
  allocations: sampleRows,
};

const sampleCurve: ResponseCurvePoint[] = [
  { multiplier: 0.0, spend: 0, mean: 0, ci_lo: 0, ci_hi: 0 },
  { multiplier: 0.5, spend: 19_300_000, mean: 21_000_000, ci_lo: 18_000_000, ci_hi: 24_000_000 },
  { multiplier: 1.0, spend: 38_600_000, mean: 37_900_000, ci_lo: 34_000_000, ci_hi: 41_800_000 },
  { multiplier: 1.5, spend: 57_900_000, mean: 51_500_000, ci_lo: 46_000_000, ci_hi: 57_000_000 },
  { multiplier: 2.0, spend: 77_200_000, mean: 62_000_000, ci_lo: 55_000_000, ci_hi: 69_000_000 },
];

const sampleMeta: OptimiserChannelMeta = {
  channel: "catalog",
  label: "Catalog",
  role: "print_event",
  demand_following: false,
  is_print: true,
  historical_spend: 38_600_000,
  historical_incremental: 37_900_000,
  historical_roi: 0.98,
  historical_mroi: 0.81,
  roi_lo: 0.91,
  roi_hi: 1.05,
  p_roi_below_1: 0.731,
  curve_max_spend: 77_200_000,
};

describe("Meridian Scenario Planner figures", () => {
  it("builds spend delta, donut, budget comparison, and outcome comparison specs", () => {
    const delta = buildSpendDeltaFigure(sampleRows);
    expect(delta.data.length).toBeGreaterThanOrEqual(2);

    const donut = buildSpendDonutFigure(sampleRows, sampleResult.total_budget);
    expect(donut.data[0].type).toBe("pie");
    expect(donut.data[0].hole).toBe(0.58);

    const budget = buildBudgetComparisonFigure(sampleRows);
    expect(budget.data).toHaveLength(2);

    const outcomeGrouped = buildOutcomeComparisonFigure(sampleRows, "grouped", sampleResult);
    expect(outcomeGrouped.data).toHaveLength(2);

    const outcomeWf = buildOutcomeComparisonFigure(sampleRows, "waterfall", sampleResult);
    expect(outcomeWf.data[0].type).toBe("waterfall");
  });

  it("builds single response curve, marginal ROI curve, and 6-panel grid with solid/dotted bounds and circle/square markers", () => {
    const rc = buildSingleResponseCurveFigure(sampleMeta, sampleRows[1], sampleCurve);
    // 90% CI + break-even + outsideLow + outsideHigh + inside + circle current + square optimised
    expect(rc.data).toHaveLength(7);

    const mroi = buildMarginalRoiCurveFigure(sampleMeta, sampleRows[1], sampleCurve, 1.2);
    expect(mroi.data).toHaveLength(6);

    const grid = buildAllResponseCurvesGridFigure(
      [sampleMeta],
      { catalog: sampleRows[1] },
      { catalog: sampleCurve }
    );
    expect(grid.data).toHaveLength(6);
  });
});
