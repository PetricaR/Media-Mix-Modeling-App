import { describe, expect, it } from "vitest";
import { bandLabel } from "./figures";
import {
  channelActiveDays,
  channelPeriodBaseSpend,
  eurCompact,
  fromDisplayEuro,
  multiplierLabel,
  parseEuroAmount,
  parsePctChange,
  scaleMultipliersToTotal,
  signedPct,
  toDisplayEuro,
} from "./format";
import { applySpreadsheetPaste, buildSpreadsheetCsv, parsePlanTemplateCsv } from "./PlanSpreadsheet";

describe("eurCompact", () => {
  it("switches to millions before showing €1000k", () => {
    expect(eurCompact(999_950)).toBe("€1.00M");
    expect(eurCompact(8_008_549)).toBe("€8.01M");
    expect(eurCompact(514_000)).toBe("€514k");
    expect(eurCompact(-950)).toBe("−€950");
  });

  it("shows a dash for missing values", () => {
    expect(eurCompact(Number.NaN)).toBe("—");
  });
});

describe("labels", () => {
  it("formats multipliers and percentages", () => {
    expect(multiplierLabel(1)).toBe("as planned");
    expect(multiplierLabel(1.2)).toBe("+20 %");
    expect(signedPct(-3.25, 1)).toBe("−3.3 %");
  });

  it("states the band's measured coverage when known", () => {
    expect(bandLabel(0.806)).toBe("Plan: 90 % interval (held 81 % in back-test)");
    expect(bandLabel(null)).toBe("Plan: 90 % prediction interval");
  });
});

describe("parseEuroAmount", () => {
  it("parses plain euro amounts, thousands separators, and k/M suffixes", () => {
    expect(parseEuroAmount("250000")).toBe(250_000);
    expect(parseEuroAmount("€250,000")).toBe(250_000);
    expect(parseEuroAmount("250.000")).toBe(250_000);
    expect(parseEuroAmount("125k")).toBe(125_000);
    expect(parseEuroAmount("1.5M")).toBe(1_500_000);
    expect(parseEuroAmount("0")).toBe(0);
    expect(parseEuroAmount("abc")).toBeNull();
  });
});

describe("parsePctChange", () => {
  it("parses signed percentages and clamps to bounds", () => {
    expect(parsePctChange("+20 %")).toBe(20);
    expect(parsePctChange("−15%")).toBe(-15);
    expect(parsePctChange("-120")).toBe(-100);
    expect(parsePctChange("250", -100, 100)).toBe(100);
    expect(parsePctChange("450")).toBe(300);
    expect(parsePctChange("")).toBeNull();
  });
});

describe("channelPeriodBaseSpend and budget scaling", () => {
  it("computes exact period spend from daily plan arrays", () => {
    expect(channelPeriodBaseSpend(420, [10, 20, 30, 40], 2, 4)).toBe(30);
    expect(channelPeriodBaseSpend(420, undefined, 21, 42)).toBe(210);
  });

  it("scales active channels proportionally to hit a target total budget", () => {
    const base = { sea: 100_000, display: 100_000, social_media: 50_000 };
    const current = { sea: 1, display: 1, social_media: 0 }; // social_media paused
    const next = scaleMultipliersToTotal(["sea", "display", "social_media"], base, current, 300_000);
    expect(next.sea).toBe(1.5);
    expect(next.display).toBe(1.5);
    expect(next.social_media).toBe(0);
  });

  it("converts between period total and daily spend granularity and counts active spend days", () => {
    expect(toDisplayEuro(420_000, "period", 42)).toBe(420_000);
    expect(toDisplayEuro(420_000, "daily", 42)).toBe(10_000);
    expect(fromDisplayEuro(10_000, "daily", 42)).toBe(420_000);
    expect(fromDisplayEuro(420_000, "period", 42)).toBe(420_000);
    expect(channelActiveDays([0, 0, 1299841.5, 0, 0, 1299841.5, 0], 7)).toBe(2);
    expect(channelActiveDays(undefined, 14)).toBe(14);
  });
});

describe("PlanSpreadsheet helpers", () => {
  it("parses template CSV and serializes a sliced spreadsheet CSV", () => {
    const csv = ["date,sea,catalog,display", "2025-01-02,60000,1300000,25000", "2025-01-03,61000,0,26000", "2025-01-04,59000,0,24000"].join("\n");
    const parsed = parsePlanTemplateCsv(csv);
    expect(parsed.dates).toEqual(["2025-01-02", "2025-01-03", "2025-01-04"]);
    expect(parsed.columns).toEqual(["sea", "catalog", "display"]);
    expect(parsed.dailyBySlug.sea).toEqual([60000, 61000, 59000]);

    const out = buildSpreadsheetCsv(parsed.dates, parsed.columns, parsed.dailyBySlug, 2);
    expect(out.split("\n")).toEqual([
      "date,sea,catalog,display",
      "2025-01-02,60000.00,1300000.00,25000.00",
      "2025-01-03,61000.00,0.00,26000.00",
    ]);
  });

  it("applies TSV/CSV multi-cell paste and strips optional header and date columns", () => {
    const columns = ["sea", "catalog", "display"];
    const current = {
      sea: [100, 100, 100],
      catalog: [0, 0, 0],
      display: [50, 50, 50],
    };

    const tsvWithHeaderAndDates = [
      "date\tSEA\tCatalog",
      "2025-01-02\t70k\t1.3M",
      "2025-01-03\t65,000\t0",
    ].join("\n");

    const { nextDaily, cellsUpdated } = applySpreadsheetPaste(tsvWithHeaderAndDates, 0, 0, columns, current, 3);
    expect(cellsUpdated).toBe(4);
    expect(nextDaily.sea).toEqual([70_000, 65_000, 100]);
    expect(nextDaily.catalog).toEqual([1_300_000, 0, 0]);
    expect(nextDaily.display).toEqual([50, 50, 50]);
  });
});

