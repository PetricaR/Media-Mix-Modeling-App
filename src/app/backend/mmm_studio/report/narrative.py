"""Templated narrative generators populated from computed statistics (no hardcoded dataset numbers)."""

from __future__ import annotations

from typing import Any


def fmt_eur(val: float, decimals: int = 0) -> str:
    """Format a euro value with thousands separators or compact k/M units."""
    abs_v = abs(val)
    if abs_v >= 1_000_000:
        return f"€{val / 1_000_000:,.2f}M"
    if abs_v >= 10_000 and decimals == 0:
        return f"€{val / 1_000:,.1f}k"
    return f"€{val:,.{decimals}f}"


def fmt_pct(val: float, decimals: int = 1, signed: bool = False) -> str:
    """Format a percentage value."""
    prefix = "+" if (signed and val > 0) else ""
    return f"{prefix}{val:.{decimals}f}%"


def dataframe_to_table_rows(
    records: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Sanitize a list of row dicts for JSON serialization."""
    import math

    cleaned: list[dict[str, Any]] = []
    for row in records:
        c_row: dict[str, Any] = {}
        for k, v in row.items():
            if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
                c_row[k] = None
            else:
                c_row[k] = v
        cleaned.append(c_row)
    return cleaned
