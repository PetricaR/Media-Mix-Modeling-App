/**
 * Figures are stored with each run's sections, so runs computed before the switch from the legacy tokens to the
 * Formare ones (2026-11-06) still carry the old colours. `rethemeFigure` swaps them at render time, so an old run looks
 * like a new one; a re-run stores the new colours itself. Only whole strings that are a colour are swapped (hex, or
 * rgb/rgba with the alpha kept); text that merely contains one is left alone.
 *
 * Each old colour maps to what its role in `charts/theme.py` is now; the old roles mostly kept their pairing (gold was
 * Catalog and the warm secondary series, both now orange; coral was Affiliate and "negative", both now red). The old
 * navy was also Display's colour: stored charts keep Display in navy until the run is re-run.
 */
const LEGACY_HEX: Record<string, string> = {
  "#8C723B": "#EB6834", // gold: Catalog and the warm secondary series
  "#776132": "#08348A", // dark gold: end of the sequential scale
  "#C0A468": "#4385E4", // light gold: sequential scale, fallback
  "#0A63BD": "#1957D2", // blue: SEA and the accent
  "#004266": "#1B315B", // navy: the dark series (and Display)
  "#BE2D2F": "#E34948", // coral: Affiliate and negative values
  "#00753F": "#008300", // green: Display Network, success
  "#E39214": "#EDA100", // orange: Social Media
  "#7A5195": "#E87BA4", // purple: PSM
  "#719C6F": "#1BAF7A", // olive (fallback only)
  "#545C63": "#5B6472", // slate
  "#75808A": "#717B89", // muted ink
  "#21292F": "#0F1828", // ink
  "#F9F6F1": "#F8FAFD", // canvas
  "#ECE7DE": "#E8EBF1", // grid
  "#D3D7DA": "#D9DFE6", // axis
  "#E4DBC8": "#D9DFE6", // baseline fill
  "#B7791F": "#9D6300", // warning
  "#F1EBDF": "#ECEFF2", // diverging midpoint
  "#8C2123": "#791A18",
  "#D0393B": "#C53732",
  "#3896F5": "#4C8EDF",
  "#07427E": "#0F3F9C",
  "#6C767F": "#5B6472", // unpaid channels, darkest to lightest
  "#8B959D": "#717B89",
  "#A6A9AC": "#8993A2",
  "#B2B8BD": "#9EA9B8",
  "#C3C7CB": "#B4BFCE",
};

/** Exact strings whose role differs from their colour's: the hold-out shade was gold, now a blue wash. */
const LEGACY_EXACT: Record<string, string> = {
  "rgba(140, 114, 59, 0.10)": "rgba(25, 87, 210, 0.07)",
};

const rgbOf = (hex: string) => [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16)).join(",");

const LEGACY_RGB = new Map(Object.entries(LEGACY_HEX).map(([from, to]) => [rgbOf(from), rgbOf(to).replace(/,/g, ", ")]));
// Legacy navy at low alpha (catalog interval bands) was written as an rgba literal
LEGACY_RGB.set("0,66,102", "27, 49, 91");

const HEX_RE = /^#[0-9a-f]{6}$/i;
const RGB_RE = /^rgba?\(\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})\s*(?:,\s*([\d.]+)\s*)?\)$/i;

/** The current colour for an old one; anything else comes back unchanged. */
export function swapLegacyColour(value: string): string {
  if (value in LEGACY_EXACT) return LEGACY_EXACT[value];
  if (HEX_RE.test(value)) return LEGACY_HEX[value.toUpperCase()] ?? value;
  const m = RGB_RE.exec(value);
  if (!m) return value;
  const rgb = LEGACY_RGB.get(`${m[1]},${m[2]},${m[3]}`);
  if (!rgb) return value;
  return m[4] === undefined ? `rgb(${rgb})` : `rgba(${rgb}, ${m[4]})`;
}

/** A copy of a Plotly figure part (data or layout) with every old colour swapped for its current one. */
export function rethemeFigure<T>(node: T): T {
  if (typeof node === "string") return swapLegacyColour(node) as T;
  if (Array.isArray(node)) return node.map((item) => rethemeFigure(item)) as T;
  if (node && typeof node === "object") {
    return Object.fromEntries(Object.entries(node as Record<string, unknown>).map(([k, v]) => [k, rethemeFigure(v)])) as T;
  }
  return node;
}
