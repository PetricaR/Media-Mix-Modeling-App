import { describe, expect, it } from "vitest";
import type { Section, SectionBlock } from "../../api/types";
import { blockKeys, curateSection, findKpi } from "./curation";

const chart = (id: string): SectionBlock => ({ type: "chart", chart_id: id, title: id, figure: { data: [], layout: {} } });
const table = (id: string): SectionBlock => ({ type: "table", table_id: id, title: id, columns: [], rows: [] });
const section = (blocks: SectionBlock[]): Section => ({ id: "s", title: "S", blocks });

const blocks: SectionBlock[] = [
  { type: "kpis", items: [{ label: "Q4 2024 hold-out MAE / mean", value: "8.5%" }] },
  { type: "callout", tone: "info", title: "A", body: "a" },
  chart("c1"),
  table("t1"),
  chart("c2"),
  table("t2"),
  { type: "markdown", title: "M", body: "m" },
  { type: "callout", tone: "warn", title: "B", body: "b" },
];

const keysOf = (items: { key: string }[]) => items.map((i) => i.key);

describe("blockKeys", () => {
  it("uses chart and table ids, and type + position for the rest", () => {
    expect(blockKeys(blocks)).toEqual(["kpis:0", "callout:0", "c1", "t1", "c2", "t2", "markdown:0", "callout:1"]);
  });
});

describe("curateSection", () => {
  it("shows every block without a curation", () => {
    const { primary, details } = curateSection(section(blocks));
    expect(primary).toHaveLength(blocks.length);
    expect(details).toHaveLength(0);
  });

  it("orders primary blocks as listed and collapses the rest in section order", () => {
    const { primary, details } = curateSection(section(blocks), { primary: ["c2", "kpis:0", "missing"] });
    expect(keysOf(primary)).toEqual(["c2", "kpis:0"]);
    expect(keysOf(details)).toEqual(["callout:0", "c1", "t1", "t2", "markdown:0", "callout:1"]);
  });

  it("folds a paired table into its chart wherever the chart goes", () => {
    const { primary, details } = curateSection(section(blocks), {
      primary: ["c1", "t1"],
      pairs: { t1: "c1", t2: "c2" },
      tableFirst: ["c2"],
    });
    expect(keysOf(primary)).toEqual(["c1"]);
    expect(primary[0].table?.type === "table" && primary[0].table.table_id).toBe("t1");
    expect(primary[0].tableFirst).toBe(false);
    const c2 = details.find((d) => d.key === "c2");
    expect(c2?.table?.table_id).toBe("t2");
    expect(c2?.tableFirst).toBe(true);
    expect(keysOf(details)).not.toContain("t1");
    expect(keysOf(details)).not.toContain("t2");
  });

  it("keeps a table on its own when its chart is missing or omitted", () => {
    const { primary } = curateSection(section(blocks), { primary: ["t1", "t2"], pairs: { t1: "nope", t2: "c2" }, omit: ["c2"], rest: "omit" });
    expect(keysOf(primary)).toEqual(["t1", "t2"]);
    expect(primary.every((p) => !p.table)).toBe(true);
  });

  it("leaves out omitted blocks and, with rest: omit, everything unlisted", () => {
    const { primary, details } = curateSection(section(blocks), { primary: ["c1", "callout:1"], omit: ["callout:1"], rest: "omit" });
    expect(keysOf(primary)).toEqual(["c1"]);
    expect(details).toHaveLength(0);
  });
});

describe("findKpi", () => {
  it("finds a KPI by label", () => {
    expect(findKpi(section(blocks), /hold-out MAE/i)?.value).toBe("8.5%");
    expect(findKpi(section(blocks), /nothing/)).toBeNull();
    expect(findKpi(null, /x/)).toBeNull();
  });
});
