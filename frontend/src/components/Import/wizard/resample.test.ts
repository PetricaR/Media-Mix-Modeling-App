import { describe, expect, it } from "vitest";
import type { ColumnMapping, ResampleReport } from "../../../api/types";
import { assignColumn } from "./mapping";
import { activeOps, carryResample, describeGrid, gridFrequency, previewFigure, resampleOf, resampleSummary, setColumnMethod, setResample, splitErrors } from "./resample";

const wide: ColumnMapping = {
  layout: "wide",
  date: "Date",
  channel: "channel",
  sales: "sales_by_channel",
  costs: "costs_by_channel",
  impressions: null,
  clicks: null,
  target: "Revenue",
  wide_channels: [{ name: "TV", spend: "TV Spend", impressions: null, clicks: null, sales: null }],
  controls: [{ column: "Covid", name: "Covid" }],
};

describe("resample & fill", () => {
  it("sets and clears a column's methods", () => {
    let m = setColumnMethod(wide, "Revenue", "fill", "seasonal");
    expect(resampleOf(m).columns).toEqual([{ column: "Revenue", fill: "seasonal", fill_value: null }]);
    m = setColumnMethod(m, "Revenue", "disaggregate", "smooth");
    expect(resampleOf(m).columns[0]).toMatchObject({ fill: "seasonal", disaggregate: "smooth" });
    m = setColumnMethod(m, "Covid", "fill", "constant", 1);
    expect(resampleOf(m).columns.find((c) => c.column === "Covid")).toMatchObject({ fill: "constant", fill_value: 1 });
    // Back to the defaults: the entry goes
    m = setColumnMethod(setColumnMethod(m, "Revenue", "fill", null), "Revenue", "disaggregate", null);
    expect(resampleOf(m).columns.map((c) => c.column)).toEqual(["Covid"]);
    expect(resampleOf(setResample(m, { fill_missing_dates: true })).fill_missing_dates).toBe(true);
  });

  it("drops a column's methods when its role changes, and keeps them across Gemini's mapping when it does not", () => {
    const m = setColumnMethod(setColumnMethod(wide, "Covid", "fill", "ffill"), "TV Spend", "fill", "spread");
    const asTarget = assignColumn(m, "Covid", "target");
    expect(resampleOf(asTarget).columns.map((c) => c.column)).toEqual(["TV Spend"]);
    const gemini: ColumnMapping = { ...wide, controls: [], resample: undefined };
    const carried = carryResample(gemini, m);
    expect(resampleOf(carried).columns.map((c) => c.column)).toEqual(["TV Spend"]);
  });

  it("knows which operations matter at a frequency", () => {
    expect(activeOps("daily")).toEqual(["fill"]);
    expect(activeOps("sub_daily")).toEqual(["aggregate", "fill"]);
    expect(activeOps("monthly")).toEqual(["disaggregate", "fill"]);
    expect(activeOps("weekly", "daily")).toEqual(["aggregate", "disaggregate", "fill"]);
    expect(gridFrequency(wide, { detected: "weekly", rows: 60, dates: 60 })).toBe("weekly");
    expect(gridFrequency(setResample(wide, { frequency: "sub_daily" }), { detected: "daily", rows: 1, dates: 1 })).toBe("sub_daily");
  });

  it("describes the dates and the result in words", () => {
    expect(describeGrid({ detected: "weekly", rows: 60, dates: 60, first: "2022-01-03", last: "2023-02-20", weekday: "Monday", missing_dates: 2 })).toBe(
      "Weekly · 60 rows on 60 dates (Mondays) · 3 Jan 2022 – 20 Feb 2023 · 2 weeks missing"
    );
    const report = {
      grid: { frequency: "weekly", days: 420, added: 2 },
      columns: [{ column: "Revenue", filled: 2, fill: "pchip" }],
      series: { dates: ["2022-01-01", "2022-01-02", "2022-01-03"], columns: [{ column: "Revenue", label: "the target", values: [1, null, 3], imputed: [2] }] },
    } as unknown as ResampleReport;
    expect(resampleSummary(report)).toBe("Weekly rows split into days (420 days); 2 missing weeks added; imputed: Revenue 2 (pchip)");
    const fig = previewFigure(report, "Revenue")!;
    expect(fig.data).toHaveLength(2);
    expect(fig.data[1]).toMatchObject({ x: ["2022-01-03"], y: [3], name: "Imputed (1 day)" });
    expect(previewFigure(report, "Nope")).toBeNull();
  });

  it("separates what the resampling can fix from the mapping's errors", () => {
    const res = { errors: ["Column not in the sheet: 'X'.", "The table skips 1 day"], prep_errors: ["The table skips 1 day"] } as never;
    expect(splitErrors(res)).toEqual({ mapping: ["Column not in the sheet: 'X'."], prep: ["The table skips 1 day"] });
  });
});
