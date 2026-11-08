import type { ExplainRequest } from "../../api/types";

/**
 * Builds what Gemini is shown for one chart or table: the figure as Plotly currently draws it (the viewer's variant
 * and legend toggles included) or the table's displayed cells. Only the keys that carry meaning are kept, so a resize
 * or a new `uirevision` does not change the content key and the cached explanation is reused.
 */

type Plain = string | number | boolean | null | Plain[] | { [key: string]: Plain };

const TRACE_KEYS = [
  "type", "name", "mode", "orientation", "visible", "fill", "xaxis", "yaxis", "x", "y", "z", "text",
  "labels", "values", "measure", "base", "error_x", "error_y",
];
const AXIS_KEYS = ["title", "type", "tickprefix", "ticksuffix", "tickformat", "overlaying", "side"];
const ERROR_KEYS = ["type", "array", "arrayminus", "symmetric", "visible"];

const LIMITS = { title: 300, subtitle: 2_000, caption: 4_000, notes: 12_000, traces: 200, columns: 80, rows: 5_000 };

const clip = (text: string | null | undefined, max: number) => {
  if (!text) return null;
  const t = text.replace(/\s+/g, " ").trim();
  return t.length > max ? `${t.slice(0, max - 1)}…` : t || null;
};

/** JSON-safe copy: typed arrays become arrays, dates ISO strings, non-finite numbers null, functions dropped. */
function toPlain(value: unknown, depth = 0): Plain {
  if (value == null || depth > 6) return null;
  if (typeof value === "number") return Number.isFinite(value) ? value : null;
  if (typeof value === "string" || typeof value === "boolean") return value;
  if (value instanceof Date) return value.toISOString();
  if (ArrayBuffer.isView(value)) return Array.from(value as unknown as ArrayLike<number>).map((v) => (Number.isFinite(v) ? v : null));
  if (Array.isArray(value)) return value.map((v) => toPlain(v, depth + 1));
  if (typeof value === "object") {
    const out: { [key: string]: Plain } = {};
    for (const [k, v] of Object.entries(value as Record<string, unknown>)) {
      if (typeof v !== "function" && !k.startsWith("_")) out[k] = toPlain(v, depth + 1);
    }
    return out;
  }
  return null;
}

function pick(source: unknown, keys: string[]): { [key: string]: Plain } {
  const out: { [key: string]: Plain } = {};
  if (!source || typeof source !== "object") return out;
  for (const k of keys) {
    const v = (source as Record<string, unknown>)[k];
    if (v !== undefined) out[k] = toPlain(v);
  }
  return out;
}

function tracePayload(trace: unknown): Record<string, unknown> {
  const out = pick(trace, TRACE_KEYS);
  for (const k of ["error_x", "error_y"]) {
    if (out[k]) out[k] = pick((trace as Record<string, unknown>)[k], ERROR_KEYS);
  }
  return out;
}

function layoutPayload(layout: unknown): Record<string, unknown> {
  const src = (layout && typeof layout === "object" ? layout : {}) as Record<string, unknown>;
  const out: Record<string, unknown> = {};
  if (src.title) out.title = toPlain(src.title);
  for (const k of ["barmode", "boxmode"]) if (src[k]) out[k] = src[k];
  for (const key of Object.keys(src).filter((k) => /^[xy]axis\d*$/.test(k))) out[key] = pick(src[key], AXIS_KEYS);
  if (Array.isArray(src.annotations)) out.annotations = src.annotations.slice(0, 30).map((a) => pick(a, ["text", "x", "y"]));
  if (Array.isArray(src.shapes)) out.shapes = src.shapes.slice(0, 30).map((s) => pick(s, ["type", "x0", "x1", "y0", "y1", "label"]));
  return out;
}

/** The figure Plotly is drawing inside `scope`, if it has rendered (it carries legend toggles and the active variant). */
function drawnFigure(scope: Element | null): { data: unknown[]; layout: unknown } | null {
  const gd = scope?.querySelector(".js-plotly-plot") as (HTMLElement & { data?: unknown[]; layout?: unknown }) | null;
  return gd && Array.isArray(gd.data) && gd.data.length ? { data: gd.data, layout: gd.layout } : null;
}

export function figurePayload(scope: Element | null, fallback?: { data: unknown[]; layout: unknown } | null): ExplainRequest["figure"] | null {
  const fig = drawnFigure(scope) ?? fallback;
  if (!fig || !fig.data?.length) return null;
  return { data: fig.data.slice(0, LIMITS.traces).map(tracePayload), layout: layoutPayload(fig.layout) };
}

/** Not the cell's value: action buttons ("+10%", "Unlock") and slider tick and thumb labels. */
const NOT_DATA = "button, [role='button'], .MuiSlider-markLabel, .MuiSlider-valueLabel";

/**
 * A cell as an assistive technology reads it: its text (screen-reader-only text included, `aria-hidden` decoration such
 * as slider tick labels left out) and the value of any input in it.
 */
function cellText(cell: Element): string {
  const parts: string[] = [];
  const walk = (node: Node) => {
    if (node.nodeType === Node.TEXT_NODE) {
      const t = node.textContent?.trim();
      if (t) parts.push(t);
    } else if (node instanceof HTMLInputElement) {
      if (node.type === "checkbox" || node.type === "radio") parts.push(node.checked ? "on" : "off");
      else if (node.type !== "hidden" && node.value) parts.push(node.value);
    } else if (node instanceof HTMLSelectElement || node instanceof HTMLTextAreaElement) {
      if (node.value) parts.push(node.value);
    } else if (node instanceof Element && node.tagName.toLowerCase() !== "svg" && node.getAttribute("aria-hidden") !== "true" && !node.matches(NOT_DATA)) {
      node.childNodes.forEach(walk);
    }
  };
  walk(cell);
  return parts.join(" ").replace(/[\u200b\u200c\u200d\ufeff]/g, "").replace(/\s+/g, " ").trim();
}

/** The displayed header and rows of the first table inside `scope`. */
export function tableFromElement(scope: Element | null): ExplainRequest["table"] | null {
  const table = scope?.querySelector("table");
  if (!table) return null;
  const headRows = table.tHead?.rows;
  const headerRow = headRows && headRows.length ? headRows[headRows.length - 1] : table.rows[0];
  if (!headerRow) return null;
  const columns = Array.from(headerRow.cells).map((c, i) => cellText(c) || `Column ${i + 1}`).slice(0, LIMITS.columns);
  const bodyRows = table.tBodies.length ? Array.from(table.tBodies).flatMap((b) => Array.from(b.rows)) : Array.from(table.rows).slice(1);
  const rows = bodyRows.slice(0, LIMITS.rows).map((r) => Array.from(r.cells).slice(0, LIMITS.columns).map((c) => cellText(c) || null));
  return { columns, rows };
}

export function clampTable(table: NonNullable<ExplainRequest["table"]>): NonNullable<ExplainRequest["table"]> {
  return {
    columns: table.columns.slice(0, LIMITS.columns).map((c) => clip(c, 200) ?? ""),
    rows: table.rows.slice(0, LIMITS.rows).map((r) => r.slice(0, LIMITS.columns)),
  };
}

export function clampText(req: ExplainRequest): ExplainRequest {
  return {
    ...req,
    title: clip(req.title, LIMITS.title) ?? "Untitled",
    subtitle: clip(req.subtitle, LIMITS.subtitle),
    caption: clip(req.caption, LIMITS.caption),
    page: clip(req.page, 200),
    section_title: clip(req.section_title, LIMITS.title),
    section_subtitle: clip(req.section_subtitle, LIMITS.subtitle),
    notes: req.notes ? (req.notes.length > LIMITS.notes ? `${req.notes.slice(0, LIMITS.notes - 1)}…` : req.notes) : null,
  };
}

/** A short content key (FNV-1a over the JSON), so the same content reuses its cached explanation. */
export function contentKey(payload: ExplainRequest): string {
  const text = JSON.stringify(payload);
  let h1 = 0x811c9dc5;
  let h2 = 0x01000193;
  for (let i = 0; i < text.length; i++) {
    const c = text.charCodeAt(i);
    h1 = Math.imul(h1 ^ c, 0x01000193);
    h2 = Math.imul(h2 ^ c, 0x5bd1e995);
  }
  return `${(h1 >>> 0).toString(16)}${(h2 >>> 0).toString(16)}-${text.length}`;
}
