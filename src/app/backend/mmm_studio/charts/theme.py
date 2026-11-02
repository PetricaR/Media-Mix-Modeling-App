"""Consistent color palette and Plotly layout template matching PLAN.md §8.1."""

from __future__ import annotations

from typing import Any

NAVY = "#1B4F72"
COPPER = "#C85A17"
TEAL = "#2E86AB"
PURPLE = "#7B2D8E"
MINT = "#48A9A6"
CORAL = "#E07A5F"
OLIVE = "#5C8001"
SLATE = "#586069"
INK_MUTED = "#586069"
TEXT_PRIMARY = "#1B1F23"
TEXT_SECONDARY = "#586069"
BG_CARD = "#FFFFFF"
BG_CANVAS = "#F8F9FA"
GRID_COLOR = "#EAEEF2"

STATUS_SUCCESS = "#1E7E34"
STATUS_WARNING = "#D97706"
STATUS_DANGER = "#C0392B"

PAID_PALETTE: dict[str, str] = {
    "SEA": "#1B4F72",
    "Catalog": "#C85A17",
    "Display": "#2E86AB",
    "Affiliate": "#7B2D8E",
    "Display Network": "#48A9A6",
    "Social Media": "#E07A5F",
    "PSM": "#5C8001",
}

UNPAID_PALETTE: dict[str, str] = {
    "Direct Type-in": "#6C757D",
    "SEO": "#8D99AE",
    "TV": "#95A5A6",
    "Direct Referred": "#ADB5BD",
    "Owned": "#B8C4CE",
}

CHANNEL_COLORS: dict[str, str] = {
    **PAID_PALETTE,
    **UNPAID_PALETTE,
    # Slug aliases
    "sea": "#1B4F72",
    "catalog": "#C85A17",
    "display": "#2E86AB",
    "affiliate": "#7B2D8E",
    "display_network": "#48A9A6",
    "social_media": "#E07A5F",
    "psm": "#5C8001",
    "direct_type_in": "#6C757D",
    "seo": "#8D99AE",
    "tv": "#95A5A6",
    "direct_referred": "#ADB5BD",
    "owned": "#B8C4CE",
}

FALLBACK_PALETTE: list[str] = [
    "#1B4F72",
    "#C85A17",
    "#2E86AB",
    "#7B2D8E",
    "#48A9A6",
    "#E07A5F",
    "#5C8001",
    "#6C757D",
    "#8D99AE",
    "#95A5A6",
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
            "family": "Inter, -apple-system, BlinkMacSystemFont, sans-serif",
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
            "bgcolor": "rgba(255,255,255,0.85)",
            "font": {"size": 11},
        },
        "xaxis": {
            "gridcolor": GRID_COLOR,
            "linecolor": "#D0D7DE",
            "zerolinecolor": GRID_COLOR,
            "title": {"text": xaxis_title or "", "font": {"size": 12, "color": TEXT_SECONDARY}},
        },
        "yaxis": {
            "gridcolor": GRID_COLOR,
            "linecolor": "#D0D7DE",
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
