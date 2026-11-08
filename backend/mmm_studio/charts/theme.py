"""Chart colours and the Plotly layout template, aligned with the Formare AI design tokens from formare.dev.

formare.dev defines its tokens in OKLCH; the hex values here are those tokens in sRGB: navy-slate ink, cool "paper"
surfaces and one accent blue (#1957D2, the logo square). The frontend mirrors these in `frontend/src/theme.ts`
(`PALETTE`, `CHANNEL_COLORS`, `CHART`). Channel colours are keyed by display name and slug so every chart shows a
channel in one colour.
"""

from __future__ import annotations

from typing import Any

# Brand tokens (formare.dev)
ACCENT = "#1957D2"  # accent / cta, oklch(50% .2 262)
ACCENT_DARK = "#0643B5"  # accent-hover, oklch(43% .19 262)
NAVY_INK = "#1B315B"  # dark series, oklch(32% .08 262)

# Semantic chart roles (names kept stable across the analytics modules)
NAVY = NAVY_INK  # the main dark series (actual sales, totals)
TEAL = ACCENT  # the accent series (model fit, SEA)
COPPER = "#EB6834"  # Catalog's colour: the warm secondary series, print and send-out accents
CORAL = "#C53732"  # the negative pole (declines, losses)
MINT = "#008300"
PURPLE = "#6250D6"
OLIVE = "#1BAF7A"  # Display's aqua
SLATE = "#5B6472"  # ink-3 family, oklch(50% .025 258)
INK_MUTED = "#717B89"  # oklch(58% .025 258)
TEXT_PRIMARY = "#0F1828"  # ink, oklch(21% .035 262)
TEXT_SECONDARY = "#3A4353"  # ink-2, oklch(38% .03 262)
BG_CARD = "#FFFFFF"
BG_CANVAS = "#F8FAFD"  # paper, oklch(98.5% .004 250)
GRID_COLOR = "#E8EBF1"
AXIS_COLOR = "#D9DFE6"  # rule, oklch(90% .012 255)
HOLDOUT_SHADE = "rgba(25, 87, 210, 0.07)"
BASELINE_FILL = "#D9DFE6"

STATUS_SUCCESS = "#10703A"
STATUS_WARNING = "#9D6300"
STATUS_DANGER = "#B32322"  # danger, oklch(50% .18 27)

# SEA takes the Formare accent and Catalog the warm COPPER; the others are hues of the dataviz reference palette.
# Checked with its palette validator on white, neighbours in the canonical order: the six media channels pass (worst
# CVD delta E 9.2, normal vision >= 15.6); with Affiliate, Display <-> Affiliate sits in the 6-8 floor band (6.9),
# covered by legends, hover labels and axis labels. The reference violet is left out: under protanopia it is
# delta E 0.7 from the accent.
PAID_PALETTE: dict[str, str] = {
    "SEA": ACCENT,
    "Catalog": COPPER,
    "Display": OLIVE,
    "Affiliate": "#E34948",
    "Display Network": MINT,
    "Social Media": "#EDA100",
    "PSM": "#E87BA4",
}

# Unpaid channels: cool slate steps, darkest first
UNPAID_PALETTE: dict[str, str] = {
    "Direct Type-in": SLATE,
    "SEO": INK_MUTED,
    "TV": "#8993A2",
    "Direct Referred": "#9EA9B8",
    "Owned": "#B4BFCE",
}

# Common paid/digital/offline marketing channel names across workbooks so multi-channel charts never collapse to ACCENT
EXTENDED_CHANNEL_PALETTE: dict[str, str] = {
    "Instagram": ACCENT,
    "Google ADS": COPPER,
    "Google Ads": COPPER,
    "Paid Search": ACCENT,
    "OTT": OLIVE,
    "Connected TV": OLIVE,
    "TV": PURPLE,
    "Influencer": "#EDA100",
    "Youtube": "#E34948",
    "YouTube": "#E34948",
    "Video": "#E34948",
    "Newspaper": MINT,
    "Print": COPPER,
    "TikTok": "#E87BA4",
    "Facebook": "#4C8EDF",
    "Meta": "#4C8EDF",
    "LinkedIn": NAVY_INK,
    "Radio": "#9D6300",
    "OOH": "#008300",
    "Email": "#9D6300",
    "Podcast": "#E87BA4",
    "Programmatic": OLIVE,
}

CHANNEL_COLORS: dict[str, str] = {
    **PAID_PALETTE,
    **UNPAID_PALETTE,
    **EXTENDED_CHANNEL_PALETTE,
}
for _ch_name, _ch_col in list(CHANNEL_COLORS.items()):
    CHANNEL_COLORS["_".join(_ch_name.lower().strip().replace("-", " ").split())] = _ch_col

FALLBACK_PALETTE: list[str] = [
    ACCENT,
    COPPER,
    OLIVE,
    "#E34948",
    MINT,
    "#EDA100",
    "#E87BA4",
    PURPLE,
    SLATE,
    NAVY_INK,
    "#9EA9B8",
]

# Sequential (one blue hue, light to dark) and diverging (red <-> cool grey <-> blue) scales for heatmaps
SEQUENTIAL_SCALE: list[list[Any]] = [[0.0, "#F8FAFD"], [0.35, "#CDE0F9"], [0.7, "#4385E4"], [1.0, "#08348A"]]
DIVERGING_SCALE: list[list[Any]] = [
    [0.0, "#791A18"],
    [0.25, "#C53732"],
    [0.45, "#ECEFF2"],
    [0.55, "#ECEFF2"],
    [0.75, "#4C8EDF"],
    [1.0, "#0F3F9C"],
]

_DYNAMIC_CHANNEL_COLORS: dict[str, str] = {}
_USED_FALLBACK_COLORS: set[str] = set()


def channel_color(name: str, index: int = 0) -> str:
    """Return the canonical color for a channel display name or slug."""
    if name in CHANNEL_COLORS:
        return CHANNEL_COLORS[name]
    slug = "_".join(name.lower().strip().replace("-", " ").split())
    if slug in CHANNEL_COLORS:
        return CHANNEL_COLORS[slug]
    if index > 0:
        return FALLBACK_PALETTE[index % len(FALLBACK_PALETTE)]
    if slug not in _DYNAMIC_CHANNEL_COLORS:
        avail = [c for c in FALLBACK_PALETTE if c not in _USED_FALLBACK_COLORS]
        chosen = avail[0] if avail else FALLBACK_PALETTE[len(_DYNAMIC_CHANNEL_COLORS) % len(FALLBACK_PALETTE)]
        _DYNAMIC_CHANNEL_COLORS[slug] = chosen
        _USED_FALLBACK_COLORS.add(chosen)
    return _DYNAMIC_CHANNEL_COLORS[slug]


def base_layout(
    title: str | None = None,
    height: int = 380,
    xaxis_title: str | None = None,
    yaxis_title: str | None = None,
    showlegend: bool = True,
    barmode: str | None = None,
    margin: dict[str, int] | None = None,
    **extra: Any,
) -> dict[str, Any]:
    """Build a clean, publication-grade Plotly layout dictionary."""
    layout: dict[str, Any] = {
        "font": {
            "family": '"IBM Plex Sans Variable", "IBM Plex Sans", -apple-system, "Segoe UI", sans-serif',
            "size": 12,
            "color": TEXT_PRIMARY,
        },
        "paper_bgcolor": BG_CARD,
        "plot_bgcolor": BG_CARD,
        "height": height,
        "autosize": True,
        "margin": margin or {"l": 56, "r": 24, "t": 44 if title else 24, "b": 48},
        "showlegend": showlegend,
        "legend": {
            "orientation": "h",
            "yanchor": "bottom",
            "y": 1.02,
            "xanchor": "left",
            "x": 0,
            "bgcolor": "rgba(255,255,255,0)",
            "font": {"size": 11},
        },
        "xaxis": {
            "gridcolor": GRID_COLOR,
            "linecolor": AXIS_COLOR,
            "zerolinecolor": GRID_COLOR,
            "title": {"text": xaxis_title or "", "font": {"size": 12, "color": TEXT_SECONDARY}},
        },
        "yaxis": {
            "gridcolor": GRID_COLOR,
            "linecolor": AXIS_COLOR,
            "zerolinecolor": GRID_COLOR,
            "title": {"text": yaxis_title or "", "font": {"size": 12, "color": TEXT_SECONDARY}},
        },
        "hovermode": "x unified",
    }
    if title:
        layout["title"] = {
            "text": title,
            "x": 0.0,
            "xanchor": "left",
            "font": {"size": 14, "color": TEXT_PRIMARY, "weight": 600},
        }
    if barmode:
        layout["barmode"] = barmode
    layout.update(extra)
    return layout
