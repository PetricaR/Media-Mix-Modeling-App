import { describe, expect, it } from "vitest";
import { splitWindows } from "./split";

// The same cases as the backend's tests/core/test_time_series_split.py, which holds the pipeline's rule
describe("splitWindows", () => {
  it("gives the sample 2-year dataset a Q3 validation and Q4 test (75 / 12.5 / 12.5)", () => {
    expect(splitWindows("2023-01-01", "2025-01-01", 93)).toEqual({
      total: 732,
      training: { start: "2023-01-01", end: "2024-06-30", days: 547 },
      validation: { start: "2024-07-01", end: "2024-09-30", days: 92 },
      test: { start: "2024-10-01", end: "2025-01-01", days: 93 },
    });
  });

  it("keeps validation as long as the test whatever the calendar", () => {
    const s = splitWindows("2015-11-23", "2019-11-17", 93);
    expect(s?.validation).toEqual({ start: "2019-05-16", end: "2019-08-16", days: 93 });
    expect(splitWindows("2023-01-01", "2025-01-01", 60)?.validation).toEqual({ start: "2024-09-01", end: "2024-11-02", days: 63 });
  });

  it("drops validation rather than train it on less than a year", () => {
    const s = splitWindows("2022-01-01", "2023-01-28", 28);
    expect(s?.validation).toBeNull();
    expect(s?.training.days).toBe(365);
    expect(splitWindows("2022-01-01", "2023-03-26", 42)?.validation).toEqual({ start: "2023-01-01", end: "2023-02-12", days: 43 });
    expect(splitWindows("2022-01-01", "2023-03-26", 85)?.validation).toBeNull();
  });

  it("returns null for unusable input", () => {
    expect(splitWindows("", "2025-01-01", 93)).toBeNull();
    expect(splitWindows("2024-12-01", "2025-01-01", 93)).toBeNull();
  });
});
