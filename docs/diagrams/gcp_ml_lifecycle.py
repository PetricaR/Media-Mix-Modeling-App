"""Draw the Google Cloud ML lifecycle architecture for the Tchibo Meridian MMM as a standalone SVG.

Standard library only, so it runs anywhere:

    python docs/diagrams/gcp_ml_lifecycle.py            # writes reports/figures/gcp_ml_lifecycle.svg

The layout follows Google's MLOps reference: data → features → continuous training → registry → serving →
monitoring, with CI/CD for the pipeline on top. Components marked "live" already run in the project today
(MMM Studio on Cloud Run, see src/app/deploy/deploy.sh).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from html import escape
from pathlib import Path

OUT = Path(__file__).resolve().parents[2] / "reports" / "figures" / "gcp_ml_lifecycle.svg"

WIDTH, HEIGHT = 1800, 1130
FONT = "Inter, 'Helvetica Neue', Arial, sans-serif"

# Neutral surfaces, one tint per lifecycle stage, one accent for the promotion gate.
BG, INK, INK_2, LINE = "#FBFAF7", "#1F2933", "#52606D", "#9AA5B1"
LIVE = "#1E8E3E"
GATE_FILL, GATE_LINE = "#FFF4DC", "#C98A00"
STAGES = {
    "sources": ("#F1EFEA", "#B8B2A7"),
    "ingest": ("#EAF2FB", "#7FA7D6"),
    "features": ("#EAF6F1", "#76B79C"),
    "training": ("#F2EEFA", "#9C88CF"),
    "serving": ("#FDEFEA", "#D99577"),
    "consume": ("#F4F1E6", "#B9A36A"),
    "cicd": ("#EEF1F4", "#8B99A8"),
    "monitor": ("#FBEEF0", "#CF8796"),
}

TITLE_SIZE, SUB_SIZE, LINE_H = 15, 12.5, 16


@dataclass
class Box:
    title: str
    lines: list[str] = field(default_factory=list)
    live: bool = False
    gate: bool = False

    @property
    def height(self) -> int:
        return 34 + LINE_H * len(self.lines)


def text(x: float, y: float, s: str, size: float, weight: int = 400, color: str = INK, anchor: str = "start") -> str:
    return (
        f'<text x="{x:.1f}" y="{y:.1f}" font-family="{FONT}" font-size="{size}" font-weight="{weight}" '
        f'fill="{color}" text-anchor="{anchor}">{escape(s)}</text>'
    )


def draw_box(b: Box, x: float, y: float, w: float, fill: str, stroke: str) -> str:
    if b.gate:
        fill, stroke = GATE_FILL, GATE_LINE
    parts = [
        f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{b.height}" rx="8" fill="#FFFFFF" '
        f'stroke="{stroke}" stroke-width="{2 if b.gate else 1.2}"/>',
        f'<rect x="{x:.1f}" y="{y:.1f}" width="5" height="{b.height}" rx="2" fill="{stroke}"/>',
        text(x + 16, y + 22, b.title, TITLE_SIZE, 600),
    ]
    if b.gate:
        parts[0] = parts[0].replace('fill="#FFFFFF"', f'fill="{fill}"')
    for i, line in enumerate(b.lines):
        parts.append(text(x + 16, y + 42 + i * LINE_H, line, SUB_SIZE, 400, INK_2))
    if b.live:
        bx = x + w - 52
        parts.append(f'<rect x="{bx:.1f}" y="{y + 9:.1f}" width="42" height="18" rx="9" fill="{LIVE}"/>')
        parts.append(text(bx + 21, y + 22, "live", 11, 600, "#FFFFFF", "middle"))
    return "\n".join(parts)


def group(x: float, y: float, w: float, h: float, stage: str, heading: str, sub: str | None = None) -> str:
    fill, stroke = STAGES[stage]
    out = [
        f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" rx="14" fill="{fill}" stroke="{stroke}" '
        f'stroke-width="1.2"/>',
        text(x + 14, y + 26, heading, 16, 700),
    ]
    if sub:
        out.append(text(x + 14, y + 45, sub, 12.5, 500, INK_2))
    return "\n".join(out)


def column(x: float, y: float, w: float, stage: str, boxes: list[Box], top: float, gap: float = 12) -> tuple[str, float]:
    """Stack `boxes` inside a column; returns the SVG and the y below the last box."""
    fill, stroke = STAGES[stage]
    parts, cy = [], y + top
    for b in boxes:
        parts.append(draw_box(b, x + 12, cy, w - 24, fill, stroke))
        cy += b.height + gap
    return "\n".join(parts), cy


def row(x: float, y: float, w: float, stage: str, boxes: list[Box], arrows: bool = True) -> tuple[str, list[float]]:
    """Lay `boxes` side by side with arrows between them; returns the SVG and each box's centre x."""
    fill, stroke = STAGES[stage]
    gap = 34
    bw = (w - 24 - gap * (len(boxes) - 1)) / len(boxes)
    h = max(b.height for b in boxes)
    parts, centres = [], []
    for i, b in enumerate(boxes):
        bx = x + 12 + i * (bw + gap)
        b_h = b.height
        b.lines += [""] * ((h - b_h) // LINE_H)  # equal heights
        parts.append(draw_box(b, bx, y, bw, fill, stroke))
        centres.append(bx + bw / 2)
        if arrows and i < len(boxes) - 1:
            parts.append(arrow(bx + bw + 4, y + h / 2, bx + bw + gap - 4, y + h / 2))
    return "\n".join(parts), centres


def arrow(x1: float, y1: float, x2: float, y2: float, color: str = LINE, width: float = 2.2, dash: str | None = None,
          label: str | None = None, label_dx: float = 8, label_dy: float = 0) -> str:
    d = f' stroke-dasharray="{dash}"' if dash else ""
    out = (
        f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" stroke="{color}" stroke-width="{width}"{d} '
        f'marker-end="url(#head)"/>'
    )
    if label:
        out += "\n" + text((x1 + x2) / 2 + label_dx, (y1 + y2) / 2 + label_dy, label, 12, 500, INK_2)
    return out


def path_arrow(points: list[tuple[float, float]], color: str = LINE, dash: str | None = None) -> str:
    d = " ".join(("M" if i == 0 else "L") + f"{x:.1f},{y:.1f}" for i, (x, y) in enumerate(points))
    dash_attr = f' stroke-dasharray="{dash}"' if dash else ""
    return (
        f'<path d="{d}" fill="none" stroke="{color}" stroke-width="2.2"{dash_attr} stroke-linejoin="round" '
        f'marker-end="url(#head)"/>'
    )


def build() -> str:
    svg = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{HEIGHT}" viewBox="0 0 {WIDTH} {HEIGHT}" '
        f'role="img" aria-labelledby="title desc">',
        '<title id="title">Google Cloud ML lifecycle for the Tchibo Meridian MMM</title>',
        '<desc id="desc">Sources flow into Cloud Storage and BigQuery, are validated and turned into features, '
        "trained weekly by a Vertex AI pipeline that fits Meridian, evaluated against the 15 % MAE / mean gate, "
        "registered, served as batch forecasts and an interactive scenario service, consumed in MMM Studio and "
        "Looker Studio, and monitored against actual sales to trigger retraining. Cloud Build deploys the "
        "pipeline itself.</desc>",
        "<defs>",
        f'<marker id="head" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" markerHeight="7" orient="auto">'
        f'<path d="M0,0 L10,5 L0,10 z" fill="{LINE}"/></marker>',
        "</defs>",
        f'<rect width="{WIDTH}" height="{HEIGHT}" fill="{BG}"/>',
        text(30, 44, "Google Cloud ML lifecycle · Tchibo daily sales forecast with Google Meridian", 26, 700),
        text(
            30,
            72,
            "Weekly continuous training (MLOps level 1) with CI/CD for the pipeline (level 2) · "
            "region europe-west3 · target: MAE / mean ≤ 15 %",
            15,
            500,
            INK_2,
        ),
    ]

    # --- CI/CD band (level 2) -------------------------------------------------------------------------------
    cicd_y, cicd_h = 96, 132
    svg.append(group(30, cicd_y, 1740, cicd_h, "cicd", "CI/CD for the pipeline", "MLOps level 2"))
    cicd_boxes = [
        Box("GitHub", ["Pull request: Python,", "SQL, pipeline spec"]),
        Box("Cloud Build", ["ruff, golden parity tests,", "build images, compile KFP"], live=True),
        Box("Artifact Registry", ["Worker image +", "pipeline templates"], live=True),
        Box("Staging pipeline run", ["Smoke sampling on the", "fixture workbook"]),
        Box("Promote to production", ["Tag the template; the next", "scheduled run picks it up"]),
    ]
    part, cicd_centres = row(250, cicd_y + 34, 1510, "cicd", cicd_boxes)
    svg.append(part)

    # --- main lifecycle columns -----------------------------------------------------------------------------
    top, bottom = 262, 820
    cols = {
        "sources": (30, 230),
        "ingest": (292, 250),
        "features": (574, 250),
        "training": (856, 320),
        "serving": (1208, 280),
        "consume": (1520, 250),
    }
    headings = {
        "sources": ("1 · Sources", "Data the business owns"),
        "ingest": ("2 · Ingest & store", "Single source of truth"),
        "features": ("3 · Validate & features", "Data quality, feature tables"),
        "training": ("4 · Continuous training", "Vertex AI Pipelines · weekly"),
        "serving": ("5 · Registry & serving", "Batch forecasts + what-if"),
        "consume": ("6 · Consume & decide", "Marketing steering"),
    }
    for stage, (x, w) in cols.items():
        svg.append(group(x, top, w, bottom - top, stage, *headings[stage]))

    content_top = 60
    part, _ = column(*cols["sources"][:1], top, cols["sources"][1], "sources", [
        Box("Shop & ERP orders", ["Daily sales by channel"]),
        Box("Ad platforms & print", ["Daily spend, clicks,", "impressions, catalog cost"]),
        Box("Budget plans", ["Planned daily spend per", "channel (CSV / Sheets)"]),
        Box("Calendar & weather", ["Google Calendar holidays,", "Open-Meteo forecasts"]),
        Box("Lift & geo experiments", ["Measured incremental ROI", "→ calibrated ROI priors"]),
    ], content_top)
    svg.append(part)

    part, _ = column(cols["ingest"][0], top, cols["ingest"][1], "ingest", [
        Box("Cloud Storage", ["Landing zone: workbooks,", "plans, model artifacts"], live=True),
        Box("Cloud Scheduler + Pub/Sub", ["Daily loads and", "pipeline triggers"]),
        Box("BigQuery", ["raw → staging → marts;", "actual sales for monitoring"]),
        Box("Dataform", ["SQL: daily wide table,", "one row per day"]),
    ], content_top)
    svg.append(part)

    part, _ = column(cols["features"][0], top, cols["features"][1], "features", [
        Box("Dataplex data quality", ["Gaps, duplicates, negative", "costs, freshness"]),
        Box("Feature tables", ["BigQuery / Vertex AI", "Feature Store: catalog", "in-home shift, calendar,",
                               "Fourier, trend, Affiliate"]),
        Box("Experimentation", ["Vertex AI Workbench /", "Colab Enterprise:", "notebooks 01–05"]),
        Box("Experiments + ML Metadata", ["Params, metrics and", "lineage of every run"]),
    ], content_top)
    svg.append(part)

    # Training pipeline: a vertical chain of steps.
    tx, tw = cols["training"]
    steps = [
        Box("1  Validate inputs", ["≥ 365 training days, regime", "breaks, every channel present"]),
        Box("2  Build Meridian table", ["KPI, 6 media spend columns,", "controls incl. Affiliate"]),
        Box("3  Fit Meridian · Custom Job", ["MMM Studio worker image,", "4 chains × 1,000 draws, CPU"]),
        Box("4  Evaluate", ["Hold-out + plan-time back-test,", "R-hat, ROI plausibility"]),
        Box("5  Promotion gate", ["MAE / mean ≤ 15 %, R-hat < 1.2", "else keep champion + alert"], gate=True),
        Box("6  Register challenger", ["Model, metrics, model card"]),
    ]
    fill, stroke = STAGES["training"]
    cy = top + content_top
    step_gap = 16
    for i, s in enumerate(steps):
        svg.append(draw_box(s, tx + 12, cy, tw - 24, fill, stroke))
        if i < len(steps) - 1:
            svg.append(arrow(tx + tw / 2, cy + s.height + 1, tx + tw / 2, cy + s.height + step_gap - 1, width=1.8))
        cy += s.height + step_gap

    sx, sw = cols["serving"]
    part, s_end = column(sx, top, sw, "serving", [
        Box("Vertex AI Model Registry", ["Versions with aliases:", "champion · challenger"]),
        Box("Batch forecast pipeline", ["Refit on history + plan", "window → daily forecast,", "90 % intervals"]),
        Box("BigQuery forecast tables", ["Forecasts, ROI, response", "curves, run metadata"]),
        Box("Scenario & optimiser API", ["Cloud Run + Cloud Run Jobs", "(MMM Studio workers)"], live=True),
    ], content_top)
    svg.append(part)

    part, _ = column(cols["consume"][0], top, cols["consume"][1], "consume", [
        Box("MMM Studio", ["Planner, ROI, optimiser,", "Meridian HTML reports"], live=True),
        Box("Looker Studio", ["Dashboards; Meridian", "Scenario Planner"]),
        Box("Gemini on Vertex AI", ["Weekly narrative from", "computed numbers only"]),
        Box("Marketing decisions", ["Next 2–6 weeks' budget", "plan → back to sources"]),
    ], content_top)
    svg.append(part)

    # Arrows between the column groups.
    order = list(cols)
    mid = (top + bottom) / 2 - 40
    for a, b in zip(order, order[1:]):
        xa = cols[a][0] + cols[a][1]
        svg.append(arrow(xa + 3, mid, cols[b][0] - 3, mid, width=3))

    # CI/CD deploys the pipeline template and image.
    deploy_x = tx + tw / 2
    svg.append(arrow(deploy_x, cicd_y + cicd_h, deploy_x, top - 2, dash="6 5"))
    svg.append(text(deploy_x + 10, top - 12, "pipeline template + worker image", 12, 500, INK_2))

    # --- monitoring band ------------------------------------------------------------------------------------
    mon_y, mon_h = 850, 148
    svg.append(group(30, mon_y, 1740, mon_h, "monitor", "Monitor & retrain", "Closes the loop"))
    fill, stroke = STAGES["monitor"]
    by = mon_y + 40

    def under(col: str, b: Box) -> tuple[float, float]:
        x, w = cols[col]
        b.lines += [""] * ((66 - b.height) // LINE_H)
        svg.append(draw_box(b, x + 12, by, w - 24, fill, stroke))
        return x + 12, x + w - 12

    under("ingest", Box("Cloud Monitoring & Logging", ["Pipeline runs, job failures,", "data freshness"]))
    drift_l, drift_r = under("features", Box("Input drift", ["Spend mix, CPC / CPM drift,", "regime breaks, new channels"]))
    trig_l, trig_r = under("training", Box("Retraining triggers", ["Weekly schedule · alert ·", "new lift test → Eventarc"]))
    acc_l, acc_r = under("serving", Box("Forecast accuracy vs actuals", ["Rolling 4-week MAE / mean:", "warn 12 %, breach 15 %"]))
    alert_l, _ = under("consume", Box("Alert policies", ["Email / chat to the data", "team; marketing notified"]))
    mid_y = by + 33
    svg.append(arrow(drift_r + 4, mid_y, trig_l - 4, mid_y))
    svg.append(arrow(acc_l - 4, mid_y, trig_r + 4, mid_y))
    svg.append(arrow(acc_r + 4, mid_y, alert_l - 4, mid_y))
    # forecasts are scored against actual sales; triggers start the training pipeline
    svg.append(arrow(sx + sw / 2, bottom + 2, sx + sw / 2, by - 3, dash="6 5"))
    svg.append(text(sx + sw / 2 + 10, bottom + 19, "forecasts vs actual sales", 12, 500, INK_2))
    svg.append(arrow(tx + tw / 2, by - 2, tx + tw / 2, bottom + 3, width=2.6))
    svg.append(text(tx + tw / 2 + 10, bottom + 19, "retrain", 12, 600, INK_2))

    # --- governance bar and legend --------------------------------------------------------------------------
    gov_y = 1014
    svg.append(f'<rect x="30" y="{gov_y}" width="1740" height="44" rx="10" fill="#E9ECEF" stroke="#ADB5BD"/>')
    svg.append(text(46, gov_y + 28, "Security & governance (every stage):", 14, 700))
    svg.append(text(330, gov_y + 28, "one service account per stage (least privilege) · Secret Manager · "
                    "VPC Service Controls · CMEK · Dataplex lineage · Cloud Audit Logs · EU data residency",
                    14, 400, INK_2))

    leg_y = 1090
    svg.append(f'<rect x="30" y="{leg_y - 13}" width="42" height="18" rx="9" fill="{LIVE}"/>')
    svg.append(text(51, leg_y + 1, "live", 11, 600, "#FFFFFF", "middle"))
    svg.append(text(82, leg_y + 1, "already running in the project today (MMM Studio on Cloud Run: Cloud Storage, "
                    "Artifact Registry, Cloud Build, Cloud Run service + jobs)", 13, 400, INK_2))
    svg.append(f'<rect x="1080" y="{leg_y - 13}" width="18" height="18" rx="4" fill="{GATE_FILL}" '
               f'stroke="{GATE_LINE}" stroke-width="2"/>')
    svg.append(text(1108, leg_y + 1, "quality gate: a model is promoted only if it meets the brief's 15 % target",
                    13, 400, INK_2))
    svg.append("</svg>")
    return "\n".join(svg)


if __name__ == "__main__":
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(build(), encoding="utf-8")
    print(f"wrote {OUT}")
