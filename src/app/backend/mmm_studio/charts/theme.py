"""Chart colours and the Plotly layout template, aligned with the Tchibo design tokens used on tchibo.de.

Tchibo's corporate gold (#8C723B / #776132), interaction blue (#0A63BD), navy (#004266), coral (#BE2D2F) and warm
neutral surfaces. Channel colours are keyed by display name and slug so every chart shows a channel in one colour.
"""

from __future__ import annotations

from typing import Any

# Brand tokens (Tchibo design system, light theme)
GOLD = "#8C723B"
GOLD_DARK = "#776132"
GOLD_LIGHT = "#C0A468"
BLUE = "#0A63BD"
NAVY_BRAND = "#004266"
CORAL_BRAND = "#BE2D2F"
GREEN_BRAND = "#00753F"
ORANGE_BRAND = "#E39214"

# Semantic chart roles (names kept stable across the analytics modules)
NAVY = NAVY_BRAND
COPPER = GOLD
TEAL = BLUE
PURPLE = "#7A5195"
MINT = GREEN_BRAND
CORAL = CORAL_BRAND
OLIVE = "#719C6F"
SLATE = "#545C63"
INK_MUTED = "#75808A"
TEXT_PRIMARY = "#21292F"
TEXT_SECONDARY = "#545C63"
BG_CARD = "#FFFFFF"
BG_CANVAS = "#F9F6F1"
GRID_COLOR = "#ECE7DE"
AXIS_COLOR = "#D3D7DA"
HOLDOUT_SHADE = "rgba(140, 114, 59, 0.10)"
BASELINE_FILL = "#E4DBC8"

STATUS_SUCCESS = GREEN_BRAND
STATUS_WARNING = "#B7791F"
STATUS_DANGER = CORAL_BRAND

PAID_PALETTE: dict[str, str] = {
    "SEA": BLUE,
    "Catalog": GOLD,
    "Display": NAVY_BRAND,
    "Affiliate": CORAL_BRAND,
    "Display Network": GREEN_BRAND,
    "Social Media": ORANGE_BRAND,
    "PSM": PURPLE,
}

UNPAID_PALETTE: dict[str, str] = {
    "Direct Type-in": "#6C767F",
    "SEO": "#8B959D",
    "TV": "#A6A9AC",
    "Direct Referred": "#B2B8BD",
    "Owned": "#C3C7CB",
}

CHANNEL_COLORS: dict[str, str] = {
    **PAID_PALETTE,
    **UNPAID_PALETTE,
    # Slug aliases
    "sea": PAID_PALETTE["SEA"],
    "catalog": PAID_PALETTE["Catalog"],
    "display": PAID_PALETTE["Display"],
    "affiliate": PAID_PALETTE["Affiliate"],
    "display_network": PAID_PALETTE["Display Network"],
    "social_media": PAID_PALETTE["Social Media"],
    "psm": PAID_PALETTE["PSM"],
    "direct_type_in": UNPAID_PALETTE["Direct Type-in"],
    "seo": UNPAID_PALETTE["SEO"],
    "tv": UNPAID_PALETTE["TV"],
    "direct_referred": UNPAID_PALETTE["Direct Referred"],
    "owned": UNPAID_PALETTE["Owned"],
}

FALLBACK_PALETTE: list[str] = [
    BLUE,
    GOLD,
    NAVY_BRAND,
    CORAL_BRAND,
    GREEN_BRAND,
    ORANGE_BRAND,
    PURPLE,
    "#6C767F",
    OLIVE,
    GOLD_LIGHT,
]

# Sequential (gold) and diverging (coral ↔ warm grey ↔ blue) scales for heatmaps
SEQUENTIAL_SCALE: list[list[Any]] = [[0.0, "#F9F6F1"], [0.35, "#E4DBC8"], [0.7, "#C0A468"], [1.0, "#776132"]]
DIVERGING_SCALE: list[list[Any]] = [
    [0.0, "#8C2123"],
    [0.25, "#D0393B"],
    [0.45, "#F1EBDF"],
    [0.55, "#F1EBDF"],
    [0.75, "#3896F5"],
    [1.0, "#07427E"],
]


def channel_color(name: str, index: int = 0) -> str:
    """Return the canonical color for a channel display name or slug."""
    if name in CHANNEL_COLORS:
        return CHANNEL_COLORS[name]
    lower = name.lower().strip()
    if lower in CHANNEL_COLORS:
        return CHANNEL_COLORS[lower]
    return FALLBACK_PALETTE[index % len(FALLBACK_PALETTE)]


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
            "family": "Figtree, Inter, -apple-system, BlinkMacSystemFont, sans-serif",
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
