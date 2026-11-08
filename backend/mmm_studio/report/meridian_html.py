"""Polish Google Meridian's standalone HTML reports so they render correctly inside MMM Studio.

Meridian 2.1 writes the summary, health card and optimisation reports with defects that show in any browser:

- the CSS and the Vega-Lite configs ask for Roboto and Google Sans but never load them, so tables, insight text and
  every chart fall back to a serif face;
- charts are fixed at 300-400 px wide inside ~900 px cards, and the 3-column response-curve grid of the optimisation
  report is clipped below ~1,100 px;
- every chart picks channel colours from Vega's default scheme by position, so a channel changes colour from chart to
  chart (Catalog is orange in one, blue in the next);
- the health card's control-correlation chart squeezes ~90 rows into 300 px, so its labels overprint;
- models without an `InputData.currency_code` label euro amounts with "$", the time-period chip shows the day after
  the last date (Meridian adds one interval to the end), and the ROI stat prints float noise (3.3000000000000003).

`polish_meridian_report` fixes these in the HTML itself, without touching Meridian: a stylesheet and a small script
that wraps `vegaEmbed` (fonts, channel colours from `charts/theme.py`, container-width and wrapped layouts) plus a
few text fixes. It is idempotent: polished reports carry `POLISH_MARKER` and are returned unchanged. A report polished
with an earlier theme (an older marker version) keeps its text fixes and only gets the current head block.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from datetime import datetime, timedelta

from mmm_studio.charts.theme import ACCENT, BG_CANVAS, CHANNEL_COLORS, FALLBACK_PALETTE, TEXT_PRIMARY

# Version 1 carried the legacy theme (Figtree, gold tints); version 2 the Formare one (IBM Plex Sans, cool tints)
POLISH_MARKER = '<meta name="mmm-studio-report-polish" content="2" />'
_ANY_POLISH_MARKER_RE = re.compile(r'<meta name="mmm-studio-report-polish" content="\d+" />')

_FONT_STACK = '"IBM Plex Sans", -apple-system, "Segoe UI", sans-serif'
_FONT_HREF = "https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600;700&display=swap"
# Meridian draws the baseline in amber (#F9AB00), too close to the app's yellow and orange channels, so the baseline
# becomes a neutral grey in every chart.
_MERIDIAN_BASELINE = "#F9AB00"
_BASELINE_COLOUR = "#8993A2"

_SCRIPT_RE = re.compile(r"(<script\b[^>]*>.*?</script>)", re.S | re.I)
_PERIOD_RE = re.compile(r"(Time period:\s*)([A-Z][a-z]{2} \d{1,2}, \d{4})(\s*-\s*)([A-Z][a-z]{2} \d{1,2}, \d{4})")
_STAT_RE = re.compile(r"(<(stat|delta)\b[^>]*>)(.*?)(</\2>)", re.S)
_FLOAT_NOISE_RE = re.compile(r"\d+\.\d{8,}")

_CSS = f"""
body {{ background: {BG_CANVAS}; margin: 0 0 32px; color: {TEXT_PRIMARY}; }}
body *:not(.material-icons-outlined) {{ font-family: {_FONT_STACK} !important; }}
.header {{ margin: 24px 24px 0; column-gap: 16px; grid-template-columns: 40px 1fr; }}
.logo {{ width: 40px; height: 40px; }}
.title {{ font-size: 28px; line-height: 36px; font-weight: 600; letter-spacing: -0.02em; color: {TEXT_PRIMARY}; }}
chips {{ padding: 16px 24px 0; }}
chips > chip {{ background: #E5F1FF; color: {TEXT_PRIMARY}; }}
cards {{ margin: 24px 24px 0; row-gap: 24px; }}
card {{ border-radius: 8px; }}
card > card-title {{ padding: 20px 32px; font-size: 22px; line-height: 32px; font-weight: 600; letter-spacing: -0.02em; color: {TEXT_PRIMARY}; }}
card > card-insights {{ background: #EFF3F8; }}
a {{ color: {ACCENT}; }}
charts {{ padding: 32px 40px; gap: 40px; }}
charts chart {{ flex: 1 1 100%; }}
charts chart > chart-description {{ max-width: 640px; }}
card errors, card warnings, card infos {{ margin-top: -12px; margin-left: -40px; margin-right: -40px; }}
"""

_JS = """
(() => {
  "use strict";
  const embed = window.vegaEmbed;
  if (typeof embed !== "function" || embed.mmmStudio) return;
  const FONT = __FONT__;
  const COLOURS = __COLOURS__;
  const FALLBACK = __FALLBACK__;
  const MERIDIAN_BASELINE = __MERIDIAN_BASELINE__;
  const FONT_KEYS = new Set(["font", "labelFont", "titleFont", "subtitleFont"]);
  const MULTI_VIEW = ["facet", "repeat", "concat", "hconcat", "vconcat"];
  const ROW_STEP = 14;

  const fontsReady = Promise.race([
    document.fonts && document.fonts.load ? document.fonts.load('12px "IBM Plex Sans"') : Promise.resolve(),
    new Promise((resolve) => setTimeout(resolve, 1500)),
  ]).catch(() => undefined);

  const label = (value) => String(value).split(" (")[0];
  const known = (value) => label(value) in COLOURS || label(value).toLowerCase() in COLOURS;
  const colourFor = (value, i) => COLOURS[label(value)] || COLOURS[label(value).toLowerCase()] || FALLBACK[i % FALLBACK.length];

  // Distinct values of a field across the spec's named datasets (Altair puts all data there)
  const distinct = (spec, field) => {
    const seen = new Set();
    for (const rows of Object.values(spec.datasets || {})) {
      for (const row of rows) if (row && row[field] != null) seen.add(row[field]);
    }
    return [...seen];
  };

  // One colour per channel in every chart, matching the app's own charts
  const recolour = (encoding, spec) => {
    for (const key of ["color", "fill", "stroke"]) {
      const def = encoding[key];
      if (!def || def.type !== "nominal" || !def.field || def.scale === null) continue;
      const scale = def.scale || {};
      if (scale.range || scale.scheme) continue;
      const domain = scale.domain || distinct(spec, def.field).sort();
      if (!domain.some(known)) continue;
      def.scale = { ...scale, domain, range: domain.map(colourFor) };
    }
  };

  // Objects and arrays alike: swap fonts and Meridian's baseline amber, recolour channels, note marks and y fields
  const walk = (node, spec, info) => {
    if (!node || typeof node !== "object") return;
    for (const key of Object.keys(node)) {
      const value = node[key];
      if (key === "mark") info.marks.add(typeof value === "string" ? value : value.type);
      if (key === "encoding" && value && typeof value === "object") {
        recolour(value, spec);
        if (value.y && value.y.type === "nominal" && value.y.field) info.nominalY.add(value.y.field);
      }
      if (typeof value === "string") {
        if (FONT_KEYS.has(key)) node[key] = FONT;
        else if (value.toUpperCase() === MERIDIAN_BASELINE) node[key] = COLOURS.baseline;
      } else if (key !== "datasets" && key !== "data") walk(value, spec, info);
    }
  };

  const polish = (spec) => {
    const info = { marks: new Set(), nominalY: new Set() };
    walk(spec, spec, info);
    const config = spec.config || {};
    spec.config = {
      ...config,
      font: FONT,
      axisQuantitative: { labelSeparation: 6, ...(config.axisQuantitative || {}) },
      axisTemporal: { labelSeparation: 6, ...(config.axisTemporal || {}) },
    };
    if (spec.facet && spec.columns) spec.config.legend = { ...(config.legend || {}), orient: "bottom" };
    const single = !MULTI_VIEW.some((key) => key in spec);
    if (single && !info.marks.has("arc")) {
      spec.width = "container";
      spec.autosize = { type: "fit-x", contains: "padding" };
    }
    if (single && typeof spec.height === "number") {
      const rows = Math.max(0, ...[...info.nominalY].map((field) => distinct(spec, field).length));
      if (rows * ROW_STEP > spec.height) spec.height = { step: ROW_STEP };
    }
    return spec;
  };

  // Wrapped facets (the optimised response curves) get as many columns as fit and cells that fill them
  const facetLayout = (node, spec) => {
    if (!spec.facet || !spec.columns || !spec.spec) return null;
    const maxColumns = spec.columns;
    return () => {
      const available = Math.max(320, node.clientWidth - 16);
      const columns = Math.max(1, Math.min(maxColumns, Math.floor(available / 340)));
      return { columns, width: Math.max(180, Math.floor(available / columns) - 110) };
    };
  };

  const polished = (el, spec, opts) => {
    const node = typeof el === "string" ? document.querySelector(el) : el;
    polish(spec);
    const layout = node && facetLayout(node, spec);
    let current = null;
    const render = () => {
      if (layout) {
        const next = layout();
        if (current && next.columns === spec.columns && Math.abs(next.width - spec.spec.width) < 12) return current;
        spec.columns = next.columns;
        spec.spec.width = next.width;
      }
      const previous = current;
      current = fontsReady.then(() => {
        if (previous) previous.then((result) => result.finalize()).catch(() => undefined);
        return embed(node || el, spec, { renderer: "svg", ...(opts || {}) });
      });
      return current;
    };
    if (layout) {
      let timer = null;
      window.addEventListener("resize", () => {
        clearTimeout(timer);
        timer = setTimeout(render, 200);
      });
    }
    return render();
  };
  window.vegaEmbed = Object.assign(polished, embed, { mmmStudio: true });
})();
"""


def _channel_colours() -> dict[str, str]:
    return {**CHANNEL_COLORS, "baseline": _BASELINE_COLOUR}


def _head_injection() -> str:
    js = (
        _JS.replace("__FONT__", json.dumps(_FONT_STACK))
        .replace("__COLOURS__", json.dumps(_channel_colours()))
        .replace("__FALLBACK__", json.dumps(FALLBACK_PALETTE))
        .replace("__MERIDIAN_BASELINE__", json.dumps(_MERIDIAN_BASELINE))
    )
    return (
        f"    {POLISH_MARKER}\n"
        '    <base target="_blank" />\n'
        f'    <link href="{_FONT_HREF}" rel="stylesheet" />\n'
        f"    <style>{_CSS}</style>\n"
        f"    <script>{js}</script>\n"
    )


def _outside_scripts(html: str, fix: Callable[[str], str]) -> str:
    """Apply `fix` to the HTML between `<script>` blocks only."""
    parts = _SCRIPT_RE.split(html)
    return "".join(part if i % 2 else fix(part) for i, part in enumerate(parts))


def _fix_period(html: str, interval_days: int) -> str:
    """Show the last modelled date: Meridian prints the period end plus one interval."""

    def repl(m: re.Match[str]) -> str:
        end = datetime.strptime(m.group(4), "%b %d, %Y") - timedelta(days=interval_days)
        return f"{m.group(1)}{m.group(2)} – {end:%b} {end.day}, {end:%Y}"

    return _PERIOD_RE.sub(repl, html)


def _fix_currency(html: str, currency: str) -> str:
    """Swap Meridian's default "$" for the model currency in chart axis labels and in the insight text."""
    if currency == "$":
        return html
    escaped = "".join(f"\\u{ord(ch):04x}" for ch in currency)
    html = html.replace("\\u0027$\\u0027", f"\\u0027{escaped}\\u0027").replace("'$' +", f"'{escaped}' +")
    return _outside_scripts(html, lambda part: re.sub(r"\$(?=\d)", currency, part))


def _fix_float_noise(html: str) -> str:
    """Round float artefacts such as 3.3000000000000003 in the headline stats."""

    def clean(m: re.Match[str]) -> str:
        inner = _FLOAT_NOISE_RE.sub(lambda n: f"{round(float(n.group(0)), 4):g}", m.group(3))
        return f"{m.group(1)}{inner}{m.group(4)}"

    return _STAT_RE.sub(clean, html)


def is_polished(html: str) -> bool:
    return POLISH_MARKER in html


def polish_meridian_report(html: str, *, currency: str = "€", interval_days: int = 1) -> str:
    """Return `html` (a Meridian summary, health card or optimisation report) with the fixes above applied.

    `interval_days` is the model's time step: Meridian adds it to the period end shown in the header chip.
    """
    if is_polished(html):
        return html
    older = _ANY_POLISH_MARKER_RE.search(html)
    if older:
        # Polished with an earlier theme: the text fixes are in place (applying them again would move the period end
        # once more), so only the injected head block, which runs from the marker to </head>, is replaced
        head_end = html.lower().find("</head>", older.start())
        return html if head_end < 0 else html[: older.start()] + _head_injection().lstrip(" ") + html[head_end:]
    html = _fix_period(html, interval_days)
    html = _fix_currency(html, currency)
    html = _fix_float_noise(html)
    head_end = html.lower().find("</head>")
    if head_end < 0:
        return html
    return html[:head_end] + _head_injection() + html[head_end:]
