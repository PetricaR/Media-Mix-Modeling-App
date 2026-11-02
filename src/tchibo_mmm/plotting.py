"""Shared matplotlib style: one palette, fixed channel colors, recessive chrome."""

from pathlib import Path

import matplotlib as mpl
import matplotlib.pyplot as plt
from matplotlib.colors import LinearSegmentedColormap

from tchibo_mmm.data import PAID_CHANNELS, PROJECT_ROOT

FIG_DIR = PROJECT_ROOT / "reports" / "figures"

SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK_2 = "#52514e"
MUTED = "#898781"
GRID = "#e1e0d9"
AXIS = "#c3c2b7"

# Categorical slots, in validated order. Never cycle past these.
SLOTS = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"]
# Color follows the channel everywhere (Catalog is always orange).
CHANNEL_COLORS = dict(zip(PAID_CHANNELS, SLOTS))
# Total sales is not a channel: dark neutral, so it never borrows a channel's hue.
TOTAL_COLOR = "#33322f"

# Diverging red <-> gray <-> blue for correlations (negative red, positive blue).
DIVERGING = LinearSegmentedColormap.from_list(
    "tchibo_div", ["#a33433", "#e34948", "#f0efec", "#2a78d6", "#184f95"]
)
SEQUENTIAL = LinearSegmentedColormap.from_list("tchibo_seq", ["#cde2fb", "#3987e5", "#0d366b"])


def use_style() -> None:
    mpl.rcParams.update(
        {
            "figure.facecolor": SURFACE,
            "axes.facecolor": SURFACE,
            "savefig.facecolor": SURFACE,
            "figure.dpi": 110,
            "savefig.dpi": 200,
            "font.family": "sans-serif",
            "font.sans-serif": ["Helvetica Neue", "Arial", "DejaVu Sans"],
            "font.size": 10,
            "text.color": INK,
            "axes.labelcolor": INK_2,
            "axes.titlesize": 12,
            "axes.titleweight": "semibold",
            "axes.titlelocation": "left",
            "axes.edgecolor": AXIS,
            "axes.linewidth": 0.8,
            "axes.spines.top": False,
            "axes.spines.right": False,
            "axes.grid": True,
            "axes.grid.axis": "y",
            "axes.axisbelow": True,
            "grid.color": GRID,
            "grid.linewidth": 0.6,
            "grid.linestyle": "-",
            "xtick.color": MUTED,
            "ytick.color": MUTED,
            "xtick.labelcolor": INK_2,
            "ytick.labelcolor": INK_2,
            "lines.linewidth": 1.6,
            "legend.frameon": False,
            "axes.prop_cycle": mpl.cycler(color=SLOTS),
        }
    )


def thousands(ax, axis: str = "y", unit: str = "k", scale: float = 1e3) -> None:
    def label(v, _):
        text = f"{v / scale:,.1f}".rstrip("0").rstrip(".")
        return f"{text}{unit}"

    fmt = mpl.ticker.FuncFormatter(label)
    (ax.yaxis if axis == "y" else ax.xaxis).set_major_formatter(fmt)


def save(fig: plt.Figure, name: str) -> Path:
    FIG_DIR.mkdir(parents=True, exist_ok=True)
    path = FIG_DIR / f"{name}.png"
    fig.savefig(path, bbox_inches="tight")
    return path
