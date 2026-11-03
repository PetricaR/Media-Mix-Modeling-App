import { describe, expect, it } from "vitest";
import { bandLabel } from "./figures";
import { eurCompact, multiplierLabel, signedPct } from "./format";

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
