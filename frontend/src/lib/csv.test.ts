import { describe, expect, it } from "vitest";
import { escapeCsvCell } from "./csv";

describe("escapeCsvCell", () => {
  it("quotes values and doubles quotes", () => {
    expect(escapeCsvCell('say "hi"')).toBe('"say ""hi"""');
    expect(escapeCsvCell(null)).toBe('""');
  });

  it("neutralises spreadsheet formulas but keeps negative numbers", () => {
    expect(escapeCsvCell("=HYPERLINK(1)")).toBe(`"'=HYPERLINK(1)"`);
    expect(escapeCsvCell("@SUM(A1)")).toBe(`"'@SUM(A1)"`);
    expect(escapeCsvCell("-12.5")).toBe('"-12.5"');
  });
});
