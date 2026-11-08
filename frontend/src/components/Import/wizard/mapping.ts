import type { ChannelRole, ColumnMapping, ColumnProfile, Layout, MappingRole } from "../../../api/types";

/** The role one column of the upload plays in the mapping, and the channel or control name that goes with it. */
export interface Assignment {
  role: MappingRole;
  channel?: string;
  name?: string;
}

export const WIDE_ROLES: { value: MappingRole; label: string }[] = [
  { value: "date", label: "Date" },
  { value: "target", label: "Target (sales / revenue)" },
  { value: "spend", label: "Channel spend" },
  { value: "impressions", label: "Impressions of a channel" },
  { value: "clicks", label: "Clicks of a channel" },
  { value: "sales", label: "Attributed sales of a channel" },
  { value: "control", label: "Control" },
  { value: "ignore", label: "Leave out" },
];

export const LONG_ROLES: { value: MappingRole; label: string }[] = [
  { value: "date", label: "Date" },
  { value: "channel", label: "Channel name" },
  { value: "sales", label: "Channel sales" },
  { value: "costs", label: "Channel costs" },
  { value: "impressions", label: "Impressions" },
  { value: "clicks", label: "Clicks" },
  { value: "control", label: "Control" },
  { value: "ignore", label: "Leave out" },
];

/** Channel roles the wizard offers (the settings form offers the same, plus the transforms). */
export const CHANNEL_ROLES: { value: ChannelRole; label: string; help: string }[] = [
  { value: "paid", label: "Paid media", help: "Continuous paid media: gets an ROI and a response curve" },
  { value: "print_event", label: "Print event", help: "An episodic print drop (catalogue, mailing): shifted to its in-home date" },
  { value: "commission", label: "Commission", help: "Cost that is a share of its own sales (affiliate): a control, no ROI" },
  { value: "unpaid", label: "Organic", help: "Sales without cost (SEO, direct): part of the baseline" },
  { value: "ignored", label: "Leave out", help: "Not modelled" },
];

const SPEND_WORDS = new Set(["spend", "spends", "cost", "costs", "kosten", "budget", "investment", "eur", "usd", "gbp", "media"]);

/** A channel's display name from its spend column, as the server's guess makes it ("tv_spend" → "TV"). */
export function cleanName(column: string): string {
  const words = column.trim().split(/[\s_\-./]+/).filter(Boolean);
  let kept = words.filter((w) => !SPEND_WORDS.has(w.toLowerCase()));
  if (!kept.length) kept = words;
  if (kept.every((w) => w === w.toLowerCase())) kept = kept.map((w) => (w.length <= 3 ? w.toUpperCase() : w[0].toUpperCase() + w.slice(1)));
  return kept.join(" ").slice(0, 80);
}

export const slug = (name: string) => name.trim().toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "") || "channel";

export function columnAssignment(m: ColumnMapping, column: string): Assignment {
  if (m.date === column) return { role: "date" };
  if (m.layout === "long") {
    if (m.channel === column) return { role: "channel" };
    if (m.sales === column) return { role: "sales" };
    if (m.costs === column) return { role: "costs" };
    if (m.impressions === column) return { role: "impressions" };
    if (m.clicks === column) return { role: "clicks" };
  } else {
    if (m.target === column) return { role: "target" };
    for (const ch of m.wide_channels) {
      if (ch.spend === column) return { role: "spend", channel: ch.name };
      if (ch.impressions === column) return { role: "impressions", channel: ch.name };
      if (ch.clicks === column) return { role: "clicks", channel: ch.name };
      if (ch.sales === column) return { role: "sales", channel: ch.name };
    }
  }
  const control = m.controls.find((c) => c.column === column);
  if (control) return { role: "control", name: control.name };
  return { role: "ignore" };
}

/**
 * `m` without any use of `column` (a channel loses its metric columns with its spend column). The column's resampling
 * goes too: its methods were chosen for its old role (a spend's "spread" means nothing for a control).
 */
function release(m: ColumnMapping, column: string): ColumnMapping {
  const out: ColumnMapping = { ...m, wide_channels: [...m.wide_channels], controls: m.controls.filter((c) => c.column !== column) };
  if (m.resample) out.resample = { ...m.resample, columns: m.resample.columns.filter((c) => c.column !== column) };
  if (out.date === column) out.date = "";
  if (out.layout === "long") {
    if (out.channel === column) out.channel = "";
    if (out.sales === column) out.sales = "";
    if (out.costs === column) out.costs = "";
    if (out.impressions === column) out.impressions = null;
    if (out.clicks === column) out.clicks = null;
  } else {
    if (out.target === column) out.target = null;
    out.wide_channels = out.wide_channels
      .filter((ch) => ch.spend !== column)
      .map((ch) => ({
        ...ch,
        impressions: ch.impressions === column ? null : ch.impressions,
        clicks: ch.clicks === column ? null : ch.clicks,
        sales: ch.sales === column ? null : ch.sales,
      }));
  }
  return out;
}

function uniqueName(m: ColumnMapping, wanted: string, column: string): string {
  const taken = new Set(m.wide_channels.map((c) => slug(c.name)));
  return taken.has(slug(wanted)) ? column.slice(0, 80) : wanted;
}

/**
 * The mapping with `column` given `role`. The column's previous use is released first, and a role that only one column
 * can hold (date, target, channel sales …) releases the column that held it. A metric role needs the `channel` it
 * belongs to.
 */
export function assignColumn(m: ColumnMapping, column: string, role: MappingRole, channel?: string, categories?: string[]): ColumnMapping {
  const current = columnAssignment(m, column);
  if (current.role === role && current.channel === channel) return m;
  let out = release(m, column);
  switch (role) {
    case "date":
      if (out.date) out = release(out, out.date);
      return { ...out, date: column };
    case "target":
      if (out.target) out = release(out, out.target);
      return { ...out, target: column };
    case "channel":
    case "costs":
      return { ...out, [role]: column };
    case "spend":
      return { ...out, wide_channels: [...out.wide_channels, { name: uniqueName(out, cleanName(column), column), spend: column, impressions: null, clicks: null, sales: null }] };
    case "impressions":
    case "clicks":
    case "sales":
      if (out.layout === "long") return { ...out, [role]: column };
      if (!channel) return out;
      return { ...out, wide_channels: out.wide_channels.map((ch) => (ch.name === channel ? { ...ch, [role]: column } : ch)) };
    case "control":
      return { ...out, controls: [...out.controls, categories?.length ? { column, name: cleanName(column), categories } : { column, name: cleanName(column) }] };
    default:
      return out;
  }
}

export function renameChannel(m: ColumnMapping, from: string, to: string): ColumnMapping {
  return { ...m, wide_channels: m.wide_channels.map((ch) => (ch.name === from ? { ...ch, name: to } : ch)) };
}

export function renameControl(m: ColumnMapping, column: string, name: string): ColumnMapping {
  return { ...m, controls: m.controls.map((c) => (c.column === column ? { ...c, name } : c)) };
}

/** A fresh mapping in the other layout; the date and the controls are kept, everything else is chosen again. */
export function switchLayout(m: ColumnMapping, layout: Layout): ColumnMapping {
  if (m.layout === layout) return m;
  return { layout, date: m.date, channel: "", sales: "", costs: "", impressions: null, clicks: null, target: null, wide_channels: [], controls: m.controls };
}

/** What is still missing before the server can check the mapping (the server's own check is the authority). */
export function missingParts(m: ColumnMapping): string[] {
  const out: string[] = [];
  if (!m.date) out.push("a date column");
  if (m.layout === "wide") {
    if (!m.target) out.push("the target column");
    if (!m.wide_channels.length) out.push("at least one channel's spend column");
    const names = m.wide_channels.map((c) => slug(c.name));
    if (new Set(names).size !== names.length) out.push("a unique name for every channel");
    if (m.wide_channels.some((c) => !c.name.trim())) out.push("a name for every channel");
  } else {
    if (!m.channel) out.push("the channel column");
    if (!m.sales) out.push("the channel sales column");
    if (!m.costs) out.push("the channel costs column");
  }
  if (m.controls.some((c) => !c.name.trim())) out.push("a name for every control");
  return out;
}

/** A one-line description of a column's values for the mapping table. */
export function describeColumn(c: ColumnProfile): string {
  const num = (v: unknown) => (typeof v === "number" ? v.toLocaleString("en-GB", { maximumFractionDigits: Math.abs(v) < 10 ? 2 : 0 }) : String(v ?? ""));
  const parts: string[] = [];
  if (c.kind === "numeric") {
    parts.push(c.binary ? "0/1 flag" : `${num(c.min)} – ${num(c.max)}`);
    if (typeof c.zero_share === "number" && c.zero_share > 0 && !c.binary) parts.push(`zero on ${Math.round(100 * c.zero_share)}%`);
    if (c.negative) parts.push(`${c.negative} negative`);
  } else if (c.kind === "date") {
    const nDates = c.distinct_dates ?? c.distinct;
    if (c.step_days === 7) {
      parts.push(`${c.min} → ${c.max}`, `${nDates} weeks (→ ${nDates * 7} days)`);
    } else if (typeof c.step_days === "number" && c.step_days >= 28 && c.step_days <= 31) {
      parts.push(`${c.min} → ${c.max}`, `${nDates} months (split into days)`);
    } else {
      parts.push(`${c.min} → ${c.max}`, `${nDates} days`);
    }
  } else if (c.kind === "text") {
    parts.push(`${c.distinct} distinct: ${c.samples.slice(0, 3).join(", ")}`);
    if (c.categories?.length) {
      parts.push(`0/1 flags: ${c.categories.join(", ")}${c.baseline ? ` (vs ${c.baseline})` : ""}`);
    }
  } else {
    parts.push("empty");
  }
  if (c.blank) parts.push(`${c.blank} blank`);
  return parts.join(" · ");
}
