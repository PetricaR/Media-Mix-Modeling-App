import { describe, expect, it } from "vitest";
import { clampText, contentKey, figurePayload } from "./payload";

const figure = {
  data: [
    {
      type: "bar",
      name: "ROI",
      x: new Float64Array([8.53, Number.NaN]),
      y: ["SEA", "Display"],
      marker: { color: ["#0A63BD", "#8C723B"] },
      hovertemplate: "%{y}: %{x}<extra></extra>",
      error_x: { type: "data", array: [1, 2], color: "#000", thickness: 1 },
    },
  ],
  layout: { height: 420, width: 913, uirevision: "view", xaxis: { title: { text: "ROI" }, ticksuffix: "×", range: [0, 10] } },
};

describe("figurePayload", () => {
  it("keeps the meaningful trace and axis keys and makes typed arrays plain", () => {
    const out = figurePayload(null, figure)!;
    expect(out.data[0]).toEqual({
      type: "bar",
      name: "ROI",
      x: [8.53, null],
      y: ["SEA", "Display"],
      error_x: { type: "data", array: [1, 2] },
    });
    expect(out.layout).toEqual({ xaxis: { title: { text: "ROI" }, ticksuffix: "×" } });
  });

  it("gives the same content key whatever the chart's size or view state", () => {
    const req = (layout: Record<string, unknown>) =>
      clampText({ kind: "chart", title: "ROI", figure: figurePayload(null, { ...figure, layout: { ...figure.layout, ...layout } })! });
    expect(contentKey(req({ width: 400, uirevision: "x" }))).toBe(contentKey(req({ width: 1200 })));
    expect(contentKey(req({ xaxis: { title: { text: "Other" } } }))).not.toBe(contentKey(req({})));
  });

  it("returns null for a chart without data", () => {
    expect(figurePayload(null, { data: [], layout: {} })).toBeNull();
  });
});

describe("clampText", () => {
  it("keeps texts within the API's limits", () => {
    const out = clampText({ kind: "table", title: "t".repeat(400), notes: "n".repeat(20_000), table: { columns: ["a"], rows: [] } });
    expect(out.title.length).toBe(300);
    expect(out.notes!.length).toBe(12_000);
  });
});
