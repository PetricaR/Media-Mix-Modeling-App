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
