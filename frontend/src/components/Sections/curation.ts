import type { Section, SectionBlock, TableBlock } from "../../api/types";

/**
 * How a page shows a stored section. The backend writes every analysis (sections are complete), the page
 * decides what is worth the reader's attention: a few primary blocks, the numbers behind a chart folded into that
 * chart's card, and everything else collapsed under "More analysis". Nothing is deleted: a block is only left out when
 * another page or tab is its one home (`omit`).
 *
 * Blocks are addressed by key: a chart's `chart_id`, a table's `table_id`, a KPI block's `id`, otherwise the type and
 * its position among blocks of that type (`kpis:0`, `callout:1`, `markdown:0`).
 */
export interface SectionCuration {
  /** Shown on the page, in this order. Keys that the section does not have are skipped. */
  primary?: string[];
  /** Table key → chart key: the table becomes the chart card's "Table" view instead of a card of its own. */
  pairs?: Record<string, string>;
  /** Chart keys whose card opens on the table view (the table says more than the chart). */
  tableFirst?: string[];
  /** Left out here: another page or tab is the one home of these numbers. */
  omit?: string[];
  /** Blocks that are neither primary nor omitted: collapsed under "More analysis" (default) or left out (another tab shows them). */
  rest?: "details" | "omit";
}

export interface CuratedBlock {
  key: string;
  block: SectionBlock;
  /** The table shown as this chart's "Table" view. */
  table?: TableBlock;
  tableFirst?: boolean;
}

export interface CuratedSection {
  primary: CuratedBlock[];
  details: CuratedBlock[];
}

/** The key of every block, in order (see `SectionCuration`). */
export function blockKeys(blocks: SectionBlock[]): string[] {
  const counts: Record<string, number> = {};
  const next = (type: string) => {
    const n = counts[type] ?? 0;
    counts[type] = n + 1;
    return `${type}:${n}`;
  };
  return blocks.map((b) => {
    switch (b.type) {
      case "chart":
        return b.chart_id || b.id || next("chart");
      case "table":
        return b.table_id || b.id || next("table");
      case "kpis":
        return b.id || next("kpis");
      default:
        return next(b.type);
    }
  });
}

/** Split a section into the blocks a page shows and the ones it collapses, with tables folded into their charts. */
export function curateSection(section: Section, curation?: SectionCuration | null): CuratedSection {
  const keys = blockKeys(section.blocks);
  if (!curation) return { primary: section.blocks.map((block, i) => ({ key: keys[i], block })), details: [] };

  const byKey = new Map<string, SectionBlock>();
  keys.forEach((k, i) => {
    if (!byKey.has(k)) byKey.set(k, section.blocks[i]);
  });
  const omit = new Set(curation.omit ?? []);
  const tableFirst = new Set(curation.tableFirst ?? []);

  // A pair only holds when both blocks exist with the right types; otherwise the table keeps its own card
  const tableOfChart = new Map<string, TableBlock>();
  const paired = new Set<string>();
  for (const [tableKey, chartKey] of Object.entries(curation.pairs ?? {})) {
    const table = byKey.get(tableKey);
    const chart = byKey.get(chartKey);
    if (table?.type === "table" && chart?.type === "chart" && !tableOfChart.has(chartKey) && !omit.has(chartKey)) {
      tableOfChart.set(chartKey, table);
      paired.add(tableKey);
    }
  }

  const item = (key: string): CuratedBlock => {
    const table = tableOfChart.get(key);
    return { key, block: byKey.get(key)!, ...(table ? { table, tableFirst: tableFirst.has(key) } : {}) };
  };
  const shown = (key: string) => byKey.has(key) && !paired.has(key) && !omit.has(key);

  const primaryKeys = curation.primary ? [...new Set(curation.primary.filter(shown))] : [...new Set(keys.filter(shown))];
  const primarySet = new Set(primaryKeys);
  const detailKeys = curation.rest === "omit" ? [] : [...new Set(keys.filter((k) => shown(k) && !primarySet.has(k)))];
  return { primary: primaryKeys.map(item), details: detailKeys.map(item) };
}

/** The first KPI of a section whose label matches, for pages that quote another page's headline number. */
export function findKpi(section: Section | null | undefined, label: RegExp) {
  for (const block of section?.blocks ?? []) {
    if (block.type !== "kpis") continue;
    const kpi = block.items.find((k) => label.test(k.label));
    if (kpi) return kpi;
  }
  return null;
}
