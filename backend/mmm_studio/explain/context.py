"""What Gemini is told about one chart or table, and which numbers in its answer the data does not contain.

The browser sends the item exactly as it is drawn (the Plotly figure with the viewer's variant and hidden traces, or
the table's displayed cells) plus the text around it. Long series are summarised (count, first/last, min/max, mean)
and sampled, so a 732-day chart costs about as much as a bar chart.
"""

from __future__ import annotations

import bisect
import math
import re
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

PROMPT_VERSION = "explain-v3"

MAX_POINTS = 60  # points listed per trace; longer traces are summarised and sampled
MAX_TRACES = 40
MAX_TABLE_ROWS = 160  # rows listed; longer tables show the first and last rows
MAX_CELL_CHARS = 120
MAX_NOTES_CHARS = 8_000
MAX_CONTEXT_CHARS = 48_000

# What the app's terms mean, so "How to read it" is right on every page. No numbers that describe a result.
GLOSSARY = """\
Terms used in the app:
- ROI: incremental gross sales per €1 of media spend over the period (incremental sales ÷ spend); above 1.0× the \
channel brings in more sales than it costs (gross sales, before product margin).
- Marginal ROI (mROI): the return on the next €1 added at the current spend level; lower than average ROI wherever \
diminishing returns have set in.
- Incremental sales / contribution: the sales the model attributes to a channel or control on top of the baseline.
- Baseline: the sales the model expects without paid media (level, time-varying knots/trend, seasonality, weekday, \
calendar and other non-media controls).
- Adstock / carry-over: spend keeps working on the following days; geometric adstock decays from day 0, while \
delayed adstock peaks a few days after the event before fading. For print drops, spend is first shifted to the \
in-home delivery date.
- Saturation (Hill) and response curve: each extra €1 adds less; the curve maps spend to incremental sales, showing \
where a channel still has headroom (steep slope) versus where it is saturated (flat slope).
- 90 % credible interval: under the Bayesian posterior, the true value lies between the lower and upper bounds with \
90 % probability; a narrow interval means well-identified, while a wide interval means high uncertainty.
- P(ROI < 1): the posterior probability that a channel returns less than €1 of sales per €1 spent.
- Mostly prior: the data narrowed the prior assumption little, so the estimate mainly reflects that prior assumption.
- Hold-out and validation: days hidden from the fit and predicted out-of-sample. MAE / mean is the average daily \
absolute error relative to average daily sales (lower is better). RMSE penalises large daily misses. R² is the \
share of daily sales variation the model reproduces (1.0 is perfect).
- R-hat: Gelman–Rubin MCMC convergence diagnostic; values close to 1.0 (below 1.2) mean the sampler converged.
- VIF (Variance Inflation Factor): measures collinearity between inputs; higher values mean stronger overlap.
- Upper bound (demand-following channel): for a click-billed channel whose daily cost rises automatically when \
customer demand rises, the model cannot separate causal lift from demand the channel merely follows; its ROI and \
contribution are the most they could be, and the default optimiser holds its spend fixed.
- Commission channel: a channel paid as a share of completed orders (such as Affiliate); it scales with sales and \
is entered as a control without a causal ROI.
- OLS benchmark: an Ordinary Least Squares regression ladder (OLS-0 raw spend → OLS-1 calendar/seasonality → OLS-2 \
adstock, saturation and in-home shift) used as a transparent yardstick alongside Meridian.
- Optimiser: moves a fixed total media budget between channels within guardrail bounds to maximise incremental \
sales along the Meridian response curves.
- Shapley / waterfall decomposition: splits a forecast or historical change into additive contributions of each \
driver.
"""

SYSTEM_INSTRUCTION = """\
You explain one chart or table of MMM Studio, a marketing-mix-modelling app (a Google Meridian Bayesian MMM with an \
OLS benchmark) built on a retailer's daily online sales and marketing spend. The reader is a marketing manager or \
data analyst who is looking at the item right now.

Be open, thorough and analytical in your explanation: unpack what the numbers actually mean in plain business and \
statistical terms, compare figures across channels or time periods, surface genuine insights, and finish with a \
clear conclusion — while staying 100 % grounded in the real data provided.

Follow this exact Markdown template (around 250–450 words, adapting depth to the item):

**[1–2 sentences in bold stating the main takeaway and headline figure(s) from this chart or table.]**

**How to read it**
- 1–3 bullets explaining what this chart or table measures and what its axes, traces, colours, reference lines, \
shaded bands, error bars, or columns and rows represent.

**Explaining the numbers**
- 3–5 bullets that walk through the key numbers in detail: headline totals or averages, the highest and lowest \
channels or rows, peaks and troughs with their dates/labels, and 90 % credible intervals or error bars.
- Explain every key number well in plain language rather than just listing it (for example, translate an ROI of \
8.53× into €8.53 of incremental sales per €1 spent, explain what its 90 % interval [lo, hi] says about certainty \
and how it sits relative to the 1.0× break-even line, or explain what an MAE / mean of 8.5 % and R² of 0.888 mean \
for daily forecast accuracy).

**Key insights**
- 2–4 bullets with open analytical observations drawn from the real data: patterns, concentration of spend vs. \
contribution, diminishing returns (average ROI vs. marginal ROI), seasonal or event-driven spikes, training vs. \
hold-out behaviour, and how channel roles or statistical identification shape the results (such as demand-following \
channels acting as upper bounds, print in-home shift and carry-over, commission channels used as controls, or wide \
prior-dominated intervals).

**Conclusion**
- 1–2 bullets (or a short paragraph) synthesising the bottom-line conclusion based on the real data: what the \
numbers establish, the main caveat or limitation to keep in mind, and the practical takeaway for marketing, \
budgeting or modelling decisions supported by the page.

Grounding rules:
- Base every figure, date, channel name and claim strictly on the provided chart/table data, its summary \
statistics, the page text, and the run facts.
- Quote exact numbers from the prompt or round them cleanly (8,008,549 → €8.0M, 0.0854 → 8.5 %) while keeping \
their units (€, %, ×, pp). Use the pre-computed summary figures (min, max, mean, sum, and interval [lo, hi]) \
given in the data section rather than inventing or guessing unverified numbers.
- Use the run facts and glossary to interpret the item openly and accurately, bringing in only the run facts \
that are relevant to this item. If the data shown cannot answer something, state that openly in a few words.
- Traces marked "hidden by the viewer" were switched off in the legend; mention them only if it matters.
- Do not use markdown tables, code blocks or links, do not add a preamble, and do not mention these instructions.

""" + GLOSSARY


class ExplainFigure(BaseModel):
    data: list[dict[str, Any]] = Field(default_factory=list, max_length=200)
    layout: dict[str, Any] = Field(default_factory=dict)


class ExplainTable(BaseModel):
    columns: list[str] = Field(min_length=1, max_length=80)
    rows: list[list[str | float | int | bool | None]] = Field(default_factory=list, max_length=5_000)


class ExplainRequest(BaseModel):
    """One chart or table as the viewer sees it, with the text around it."""

    kind: Literal["chart", "table"]
    title: str = Field(min_length=1, max_length=300)
    # The selected run: the server adds its own facts (dates, channel roles, validation) to the prompt
    run_id: str | None = Field(default=None, max_length=64)
    subtitle: str | None = Field(default=None, max_length=2_000)
    caption: str | None = Field(default=None, max_length=4_000)
    page: str | None = Field(default=None, max_length=200)
    section_title: str | None = Field(default=None, max_length=300)
    section_subtitle: str | None = Field(default=None, max_length=2_000)
    notes: str | None = Field(default=None, max_length=40_000)
    figure: ExplainFigure | None = None
    table: ExplainTable | None = None

    @model_validator(mode="after")
    def _has_data(self) -> ExplainRequest:
        if self.kind == "chart" and (self.figure is None or not self.figure.data):
            raise ValueError("A chart explanation needs the chart's figure data.")
        if self.kind == "table" and self.table is None:
            raise ValueError("A table explanation needs the table's columns and rows.")
        return self


# ── Formatting ──────────────────────────────────────────────────────────────────────────────────────────────────

_TAGS = re.compile(r"<[^>]+>")
_SPACES = re.compile(r"\s+")


def clean_text(value: Any, limit: int = 300) -> str:
    """Plain one-line text: Plotly's HTML tags (<b>, <br>) become spaces."""
    text = _SPACES.sub(" ", _TAGS.sub(" ", str(value))).strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) else None


def fmt_number(value: float) -> str:
    """Integers with thousands separators from 1,000 up, four significant digits below."""
    if value == 0:
        return "0"
    if abs(value) >= 1_000:
        return f"{value:,.0f}"
    return f"{value:.4g}"


def fmt_value(value: Any) -> str:
    num = _number(value)
    if num is not None:
        return fmt_number(num)
    if value is None or value == "":
        return "–"
    text = clean_text(value, 60)
    return text[:-9] if text.endswith("T00:00:00") else text


def _axis_title(axis: Any) -> str:
    if not isinstance(axis, dict):
        return ""
    title = axis.get("title")
    if isinstance(title, dict):
        title = title.get("text")
    return clean_text(title) if title else ""


# ── Figures ─────────────────────────────────────────────────────────────────────────────────────────────────────


def _layout_lines(layout: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    title = layout.get("title")
    if isinstance(title, dict):
        title = title.get("text")
    if title:
        lines.append(f"Figure title: {clean_text(title)}")
    for key in sorted(k for k in layout if re.fullmatch(r"[xy]axis\d*", k)):
        axis = layout[key]
        if not isinstance(axis, dict):
            continue
        bits = []
        if (name := _axis_title(axis)):
            bits.append(f'"{name}"')
        for attr in ("type", "tickprefix", "ticksuffix", "tickformat"):
            if axis.get(attr):
                bits.append(f"{attr} {clean_text(axis[attr], 40)}")
        if axis.get("overlaying"):
            bits.append(f"drawn over {axis['overlaying']}, {axis.get('side', 'right')} side")
        if bits:
            lines.append(f"Axis {key}: " + ", ".join(bits))
    for attr in ("barmode", "boxmode"):
        if layout.get(attr):
            lines.append(f"{attr}: {layout[attr]}")
    notes = [clean_text(a.get("text"), 160) for a in layout.get("annotations") or [] if isinstance(a, dict) and a.get("text")]
    if notes:
        lines.append("Annotations: " + "; ".join(f'"{n}"' for n in notes[:20]))
    markers = []
    for shape in layout.get("shapes") or []:
        if not isinstance(shape, dict):
            continue
        label = shape.get("label", {}).get("text") if isinstance(shape.get("label"), dict) else None
        x0, x1, y0, y1 = (shape.get(k) for k in ("x0", "x1", "y0", "y1"))
        if shape.get("type") == "line" and x0 is not None and x0 == x1:
            markers.append(f"vertical line at x = {fmt_value(x0)}" + (f' ("{clean_text(label)}")' if label else ""))
        elif shape.get("type") == "line" and y0 is not None and y0 == y1:
            markers.append(f"horizontal line at y = {fmt_value(y0)}" + (f' ("{clean_text(label)}")' if label else ""))
        elif shape.get("type") == "rect" and x0 is not None and x1 is not None:
            markers.append(f"shaded x range {fmt_value(x0)} to {fmt_value(x1)}" + (f' ("{clean_text(label)}")' if label else ""))
    if markers:
        lines.append("Shapes: " + "; ".join(markers[:12]))
    return lines


def _as_list(value: Any) -> list[Any] | None:
    return list(value) if isinstance(value, (list, tuple)) else None


def _sample_indices(values: list[float | None]) -> list[int]:
    """Evenly spaced indices plus the first, last, smallest and largest point."""
    n = len(values)
    step = max(1, math.ceil(n / MAX_POINTS))
    picked = set(range(0, n, step)) | {n - 1}
    finite = [(v, i) for i, v in enumerate(values) if v is not None]
    if finite:
        picked |= {min(finite)[1], max(finite)[1]}
    return sorted(picked)


def _error_bars(trace: dict[str, Any], key: str, n: int) -> tuple[list[Any] | None, list[Any] | None]:
    bars = trace.get(key)
    if not isinstance(bars, dict) or bars.get("visible") is False:
        return None, None
    plus = _as_list(bars.get("array"))
    minus = _as_list(bars.get("arrayminus"))
    if plus is not None and len(plus) != n:
        plus = None
    if minus is not None and len(minus) != n:
        minus = None
    if plus is not None and minus is None and bars.get("symmetric", True) is not False:
        minus = plus
    return plus, minus


def _trace_lines(index: int, trace: dict[str, Any]) -> list[str]:
    visible = trace.get("visible", True)
    if visible is False:
        return []
    kind = str(trace.get("type") or "scatter")
    name = clean_text(trace.get("name") or f"trace {index + 1}", 120)
    traits = [kind]
    if trace.get("mode"):
        traits.append(str(trace["mode"]))
    horizontal = trace.get("orientation") == "h"
    if horizontal:
        traits.append("horizontal")
    if trace.get("fill") and trace.get("fill") != "none":
        traits.append(f"fill {trace['fill']}")
    for axis_key in ("xaxis", "yaxis"):
        if trace.get(axis_key) and trace[axis_key] not in ("x", "y"):
            traits.append(f"on {trace[axis_key]}")
    if visible == "legendonly":
        traits.append("hidden by the viewer")
    head = f'Trace "{name}" ({", ".join(traits)})'

    if kind in ("heatmap", "contour"):
        return [head, *_matrix_lines(trace)]
    if kind == "pie":
        labels, values = _as_list(trace.get("labels")) or [], _as_list(trace.get("values")) or []
        pie_nums = [_number(v) for v in values[:MAX_POINTS]]
        pie_total = sum(v for v in pie_nums if v is not None and v > 0)
        slices = []
        for lbl, v, num in zip(labels[:MAX_POINTS], values[:MAX_POINTS], pie_nums):
            share = f" ({num / pie_total * 100:.1f} %)" if pie_total > 0 and num is not None and num >= 0 else ""
            slices.append(f"{fmt_value(lbl)}: {fmt_value(v)}{share}")
        pairs = "; ".join(slices)
        summary = f" (total {fmt_number(pie_total)})" if pie_total > 0 else ""
        return [head, f"  slices{summary}: {pairs}"] if pairs else [head]

    xs, ys = _as_list(trace.get("x")), _as_list(trace.get("y"))
    cats, vals = (ys, xs) if horizontal else (xs, ys)
    if vals is None:  # e.g. a histogram of x only
        vals, cats = cats, None
    if not vals:
        return [head]
    n = len(vals)
    nums = [_number(v) for v in vals]
    if cats is None or len(cats) != n:
        cats = list(range(n))
    texts = _as_list(trace.get("text"))
    texts = texts if texts is not None and len(texts) == n else None
    measures = _as_list(trace.get("measure"))
    measures = measures if measures is not None and len(measures) == n else None
    plus, minus = _error_bars(trace, "error_x" if horizontal else "error_y", n)

    lines = [head]
    finite = [(v, i) for i, v in enumerate(nums) if v is not None]
    if n > MAX_POINTS and finite:
        lo, hi = min(finite), max(finite)
        total = sum(v for v, _ in finite)
        mean = total / len(finite)
        lines.append(
            f"  {n} points from {fmt_value(cats[0])} to {fmt_value(cats[-1])}: first {fmt_value(vals[0])}, "
            f"last {fmt_value(vals[-1])}, min {fmt_number(lo[0])} at {fmt_value(cats[lo[1]])}, "
            f"max {fmt_number(hi[0])} at {fmt_value(cats[hi[1]])}, mean {fmt_number(mean)}, sum {fmt_number(total)}"
        )
        indices = _sample_indices(nums)
        lines.append(f"  sampled points ({len(indices)} of {n}):")
    else:
        if len(finite) >= 2:
            lo, hi = min(finite), max(finite)
            total = sum(v for v, _ in finite)
            mean = total / len(finite)
            lines.append(
                f"  {len(finite)} points: min {fmt_number(lo[0])} at {fmt_value(cats[lo[1]])}, "
                f"max {fmt_number(hi[0])} at {fmt_value(cats[hi[1]])}, mean {fmt_number(mean)}, sum {fmt_number(total)}"
            )
        indices = list(range(n))
    points = []
    for i in indices:
        point = f"{fmt_value(cats[i])}: {fmt_value(vals[i])}"
        p_val = _number(plus[i]) if plus is not None else None
        m_val = _number(minus[i]) if minus is not None else None
        if plus is not None and minus is not None and p_val is not None and m_val is not None:
            point += f" (−{fmt_value(minus[i])} / +{fmt_value(plus[i])})"
            if nums[i] is not None:
                point += f" [{fmt_number(nums[i] - m_val)}, {fmt_number(nums[i] + p_val)}]"
        if measures is not None and measures[i] not in (None, "relative"):
            point += f" [{measures[i]}]"
        if texts is not None and texts[i] not in (None, "") and fmt_value(texts[i]) != fmt_value(vals[i]):
            point += f' "{clean_text(texts[i], 60)}"'
        points.append(point)
    lines.append("  " + "; ".join(points))
    return lines


def _matrix_lines(trace: dict[str, Any]) -> list[str]:
    z = _as_list(trace.get("z")) or []
    rows = [_as_list(r) or [] for r in z][:30]
    xs = _as_list(trace.get("x")) or []
    ys = _as_list(trace.get("y")) or []
    if not rows:
        return []
    width = min(max(len(r) for r in rows), 30)
    header = [fmt_value(xs[j]) if j < len(xs) else str(j) for j in range(width)]
    lines = ["  matrix (rows = y, columns = x): | " + " | ".join(header) + " |"]
    for i, row in enumerate(rows):
        label = fmt_value(ys[i]) if i < len(ys) else str(i)
        lines.append(f"  {label} | " + " | ".join(fmt_value(v) for v in row[:width]) + " |")
    if len(z) > len(rows) or any(len(_as_list(r) or []) > width for r in z):
        lines.append("  (matrix truncated to 30 × 30)")
    return lines


def describe_figure(figure: ExplainFigure) -> str:
    lines = _layout_lines(figure.layout)
    traces = figure.data[:MAX_TRACES]
    for i, trace in enumerate(traces):
        if isinstance(trace, dict):
            lines.extend(_trace_lines(i, trace))
    if len(figure.data) > len(traces):
        lines.append(f"({len(figure.data) - len(traces)} more traces not listed)")
    return "\n".join(lines)


# ── Tables ──────────────────────────────────────────────────────────────────────────────────────────────────────


def _cell(value: Any) -> str:
    text = fmt_value(value) if _number(value) is not None else clean_text("–" if value in (None, "") else value, MAX_CELL_CHARS)
    return text.replace("|", "/")


def describe_table(table: ExplainTable) -> str:
    header = "| " + " | ".join(clean_text(c, 80).replace("|", "/") for c in table.columns) + " |"
    rows = table.rows
    lines = [f"{len(rows)} rows × {len(table.columns)} columns", header]
    if len(rows) > MAX_TABLE_ROWS:
        head, tail = rows[: MAX_TABLE_ROWS - 40], rows[-40:]
        lines += ["| " + " | ".join(_cell(v) for v in r) + " |" for r in head]
        lines.append(f"| … {len(rows) - len(head) - len(tail)} rows not listed … |")
        lines += ["| " + " | ".join(_cell(v) for v in r) + " |" for r in tail]
    else:
        lines += ["| " + " | ".join(_cell(v) for v in r) + " |" for r in rows]
    return "\n".join(lines)


# ── Prompt ──────────────────────────────────────────────────────────────────────────────────────────────────────


def build_prompt(req: ExplainRequest, run_facts: str | None = None) -> str:
    """The user turn: the run's facts, where the item sits, its own text, the page text and the data."""
    parts: list[str] = []
    if run_facts:
        parts.append("About this run (from its settings and results):\n" + run_facts)
    where = " · ".join(clean_text(p, 200) for p in (req.page, req.section_title) if p)
    if where:
        parts.append(f"Page: {where}")
    if req.section_subtitle:
        parts.append(f"Page description: {clean_text(req.section_subtitle, 2_000)}")
    item = [f'{req.kind.capitalize()}: "{clean_text(req.title)}"']
    if req.subtitle:
        item.append(f"Subtitle: {clean_text(req.subtitle, 2_000)}")
    if req.caption:
        item.append(f"Caption: {clean_text(req.caption, 4_000)}")
    parts.append("\n".join(item))
    if req.notes and req.notes.strip():
        notes = req.notes.strip()
        if len(notes) > MAX_NOTES_CHARS:
            notes = notes[: MAX_NOTES_CHARS - 1] + "…"
        parts.append("Text shown elsewhere on the page:\n" + notes)
    data = describe_figure(req.figure) if req.kind == "chart" and req.figure else describe_table(req.table)  # type: ignore[arg-type]
    parts.append("Data:\n" + data)
    text = "\n\n".join(parts)
    if len(text) > MAX_CONTEXT_CHARS:
        text = text[: MAX_CONTEXT_CHARS - 40] + "\n… (data truncated)"
    return text


# ── Grounding check ─────────────────────────────────────────────────────────────────────────────────────────────

_NUMBER = re.compile(
    r"(?<![\w.,])"  # not part of a word ("Q4", "s01"), a decimal or a thousands group
    r"(?P<cur>[€$£])?\s?"
    r"(?P<num>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)"
    r"(?:\s?(?P<suf>bn|billion|million|thousand|[kKmMB])(?![\w]))?"
    r"(?:\s?(?P<unit>%|pp|×|x(?![\w])))?"
)
_SCALE = {"k": 1e3, "K": 1e3, "thousand": 1e3, "m": 1e6, "M": 1e6, "million": 1e6, "B": 1e9, "bn": 1e9, "billion": 1e9}


def _tokens(text: str) -> list[tuple[str, float, float, bool]]:
    """(as written, absolute value, half a unit of the last shown digit, has a unit) for every number in `text`."""
    out = []
    for m in _NUMBER.finditer(text):
        raw = m.group("num")
        scale = _SCALE.get(m.group("suf") or "", 1.0)
        decimals = len(raw.split(".", 1)[1]) if "." in raw else 0
        value = float(raw.replace(",", "")) * scale
        half_step = 0.5 * 10 ** (-decimals) * scale
        has_unit = bool(m.group("cur") or m.group("suf") or m.group("unit"))
        out.append((m.group(0).strip(), value, half_step, has_unit))
    return out


def unverified_numbers(answer: str, context: str, limit: int = 6) -> list[str]:
    """Numbers in `answer` that no number in `context` rounds to (also as a share ↔ percent).

    Small bare integers (up to 12: counts, list positions, months) are not checked. A number matches when it is
    within rounding of a context number or within 1 % of it.
    """
    known = sorted({v for _, v, _, _ in _tokens(context)})

    def near(target: float, window: float) -> bool:
        i = bisect.bisect_left(known, target - window)
        return i < len(known) and known[i] <= target + window

    missing: list[str] = []
    for written, value, half_step, has_unit in _tokens(answer):
        if not has_unit and value <= 12 and value == int(value):
            continue
        window = max(half_step, 0.01 * value) + 1e-9
        # The data may hold a share (0.085) where the answer says 8.5 %, or the reverse
        if near(value, window) or near(value / 100, window / 100) or near(value * 100, window * 100):
            continue
        if written not in missing:
            missing.append(written)
        if len(missing) >= limit:
            break
    return missing
