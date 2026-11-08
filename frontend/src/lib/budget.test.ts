import { describe, expect, it } from "vitest";
import { parseBudgetMillions } from "./budget";

describe("parseBudgetMillions", () => {
  it("reads € million with a dot or a comma", () => {
    expect(parseBudgetMillions("121.8")).toBe(121_800_000);
    expect(parseBudgetMillions("121,8")).toBe(121_800_000);
    expect(parseBudgetMillions("€ 121,8 Mio")).toBe(121_800_000);
    expect(parseBudgetMillions("50 M")).toBe(50_000_000);
  });

  it("is empty for an empty field", () => {
    expect(parseBudgetMillions("  ")).toBeNull();
  });

  it("rejects anything that is not one number", () => {
    for (const bad of ["121.800.000", "121,800,000", "1.5e8", "abc", "0", "-5", "12 k"]) {
      expect(Number.isNaN(parseBudgetMillions(bad))).toBe(true);
    }
  });
});
