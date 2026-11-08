import { describe, expect, it } from "vitest";
import type { ColumnMapping } from "../../../api/types";
import { assignColumn, cleanName, columnAssignment, describeColumn, missingParts, switchLayout } from "./mapping";

const wide: ColumnMapping = {
  layout: "wide",
  date: "Date",
  channel: "channel",
  sales: "sales_by_channel",
  costs: "costs_by_channel",
  impressions: null,
  clicks: null,
  target: "Revenue",
  wide_channels: [{ name: "TV", spend: "TV Spend", impressions: "TV Impressions", clicks: null, sales: null }],
  controls: [{ column: "Covid", name: "Covid" }],
};

describe("upload mapping", () => {
  it("names a channel after its spend column", () => {
    expect(cleanName("tv_spend")).toBe("TV");
    expect(cleanName("Google Search Brand")).toBe("Google Search Brand");
    expect(cleanName("radio_costs_eur")).toBe("Radio");
  });

  it("reads each column's role", () => {
    expect(columnAssignment(wide, "Date").role).toBe("date");
    expect(columnAssignment(wide, "TV Impressions")).toEqual({ role: "impressions", channel: "TV" });
    expect(columnAssignment(wide, "Covid")).toEqual({ role: "control", name: "Covid" });
    expect(columnAssignment(wide, "Sessions").role).toBe("ignore");
  });

  it("moves a column to a new role and releases what it held", () => {
    const m = assignColumn(wide, "TV Spend", "control");
    expect(m.wide_channels).toEqual([]); // the channel goes with its spend column, metrics included
    expect(m.controls.map((c) => c.column)).toEqual(["Covid", "TV Spend"]);
    const t = assignColumn(wide, "Orders", "target");
    expect(t.target).toBe("Orders");
    expect(columnAssignment(t, "Revenue").role).toBe("ignore");
    const s = assignColumn(wide, "Radio Spend", "spend");
    expect(s.wide_channels.map((c) => c.name)).toEqual(["TV", "Radio"]);
    const k = assignColumn(s, "Radio Clicks", "clicks", "Radio");
    expect(k.wide_channels[1].clicks).toBe("Radio Clicks");
  });

  it("says what is missing and switches layouts cleanly", () => {
    expect(missingParts(wide)).toEqual([]);
    const long = switchLayout(wide, "long");
    expect(long.controls).toEqual(wide.controls);
    expect(missingParts(long)).toEqual(["the channel column", "the channel sales column", "the channel costs column"]);
    expect(missingParts(assignColumn(wide, "Revenue", "ignore"))).toEqual(["the target column"]);
  });

  it("describes weekly dates and categorical controls", () => {
    expect(
      describeColumn({
        name: "date",
        kind: "date",
        non_null: 209,
        blank: 0,
        distinct: 209,
        min: "2022-01-03",
        max: "2025-12-29",
        distinct_dates: 209,
        step_days: 7,
        samples: ["2022-01-03"],
      })
    ).toBe("2022-01-03 → 2025-12-29 · 209 weeks (→ 1463 days)");
    expect(
      describeColumn({
        name: "sales_promotion",
        kind: "text",
        non_null: 209,
        blank: 0,
        distinct: 4,
        categories: ["Coupons", "BOGO", "Flash Sale"],
        baseline: "Normal",
        samples: ["Normal", "Coupons", "BOGO"],
      })
    ).toBe("4 distinct: Normal, Coupons, BOGO · 0/1 flags: Coupons, BOGO, Flash Sale (vs Normal)");
  });
});
