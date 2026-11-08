"""A synthetic wide MMM table (one row per day, a revenue column, spend per channel, a Covid flag) for the tests."""

from __future__ import annotations

import io

import numpy as np
import pandas as pd

CHANNELS = ("Google Search Brand", "TV Ads", "Facebook Product Catalog Sales")


def wide_frame(days: int = 400, start: str = "2022-01-01", seed: int = 7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    dates = pd.date_range(start, periods=days, freq="D")
    t = np.arange(days)
    season = 1.0 + 0.2 * np.sin(2 * np.pi * t / 365.25)
    spend = {
        CHANNELS[0]: 400 * season + rng.normal(0, 40, days),
        CHANNELS[1]: np.clip(1400 + rng.normal(0, 500, days), 50, None),
        CHANNELS[2]: 600 * season + rng.normal(0, 60, days),
    }
    covid = (t < 30).astype(int)
    revenue = 5000 * season + 1.5 * spend[CHANNELS[0]] + 0.4 * spend[CHANNELS[1]] - 800 * covid + rng.normal(0, 300, days)
    frame = pd.DataFrame({"Date": dates, "Revenue": revenue, **{k: np.round(v, 2) for k, v in spend.items()}, "Covid": covid})
    frame["Sessions"] = (revenue / 3).round()  # an outcome: must never become an input
    return frame


def to_xlsx(sheets: dict[str, pd.DataFrame]) -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        for name, frame in sheets.items():
            frame.to_excel(writer, sheet_name=name, index=False)
    return buf.getvalue()


def wide_xlsx(days: int = 400) -> bytes:
    """The wide table plus a short side table of channel notes, like the user's own workbook."""
    notes = pd.DataFrame({"Channels": list(CHANNELS), "delayed effect": [0, 3, 0], "Attributions": [330000.0, None, 200000.0]})
    return to_xlsx({"short": wide_frame(300), "data": wide_frame(days), "notes": notes})


def wide_csv(sep: str = ";", days: int = 400) -> bytes:
    frame = wide_frame(days)
    frame["Date"] = frame["Date"].dt.strftime("%d.%m.%Y")
    return frame.to_csv(sep=sep, index=False).encode("utf-8")
