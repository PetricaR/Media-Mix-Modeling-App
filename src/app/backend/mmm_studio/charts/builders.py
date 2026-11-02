"""Reusable Plotly JSON figure builders for MMM Studio."""

from __future__ import annotations

from typing import Any

import numpy as np

from mmm_studio.charts.theme import (
    COPPER,
    GRID_COLOR,
    INK_MUTED,
    NAVY,
    SLATE,
    STATUS_DANGER,
    STATUS_SUCCESS,
    STATUS_WARNING,
    TEAL,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
    base_layout,
    channel_color,
)


def _clean_list(values: Any) -> list[Any]:
    """Convert numpy/pandas arrays into JSON-clean Python lists (NaN -> None)."""
    out: list[Any] = []
    for v in values:
        if v is None:
            out.append(None)
        elif isinstance(v, (float, np.floating)):
            if np.isnan(v) or np.isinf(v):
                out.append(None)
            else:
                out.append(round(float(v), 6))
        elif isinstance(v, (int, np.integer)):
            out.append(int(v))
        else:
            out.append(str(v))
    return out


def _hex_to_rgba(hex_color: str, alpha: float = 0.16) -> str:
    h = hex_color.lstrip("#")
    if len(h) == 6:
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
        return f"rgba({r},{g},{b},{alpha})"
    return f"rgba(27,79,114,{alpha})"


def line_figure(
    x: Any,
    series: list[dict[str, Any]],
    title: str | None = None,
    subtitle: str | None = None,
    xaxis_title: str | None = None,
    yaxis_title: str | None = None,
    height: int = 380,
    vlines: list[dict[str, Any]] | None = None,
    hlines: list[dict[str, Any]] | None = None,
    vrects: list[dict[str, Any]] | None = None,
    showlegend: bool = True,
) -> dict[str, Any]:
    """Build a multi-series line/band figure."""
    x_list = _clean_list(x)
    traces: list[dict[str, Any]] = []
    for idx, s in enumerate(series):
        color = s.get("color") or channel_color(str(s.get("name", "")), idx)
        sx = _clean_list(s["x"]) if "x" in s else x_list
        if "band_lower" in s and "band_upper" in s:
            traces.append(
                {
                    "type": "scatter",
                    "mode": "lines",
                    "name": f"{s.get('name', 'Series')} Upper",
                    "x": sx,
                    "y": _clean_list(s["band_upper"]),
                    "line": {"width": 0},
                    "showlegend": False,
                    "hoverinfo": "skip",
                }
            )
            traces.append(
                {
                    "type": "scatter",
                    "mode": "lines",
                    "name": f"{s.get('name', 'Series')} Interval",
                    "x": sx,
                    "y": _clean_list(s["band_lower"]),
                    "line": {"width": 0},
                    "fill": "tonexty",
                    "fillcolor": _hex_to_rgba(color, 0.16),
                    "showlegend": False,
                    "hoverinfo": "skip",
                }
            )

        trace: dict[str, Any] = {
            "type": s.get("type", "scatter"),
            "mode": s.get("mode", "lines"),
            "name": s.get("name", f"Series {idx + 1}"),
            "x": sx,
            "y": _clean_list(s["y"]),
            "line": {
                "color": color,
                "width": s.get("width", 2),
                "dash": s.get("dash", "solid"),
            },
        }
        if s.get("stackgroup"):
            trace["stackgroup"] = s["stackgroup"]
        if s.get("fill"):
            trace["fill"] = s["fill"]
            if s.get("fillcolor"):
                trace["fillcolor"] = s["fillcolor"]
        if s.get("marker"):
            trace["marker"] = s["marker"]
        if "opacity" in s:
            trace["opacity"] = s["opacity"]
        if "showlegend" in s:
            trace["showlegend"] = s["showlegend"]
        if "yaxis" in s:
            trace["yaxis"] = s["yaxis"]
        traces.append(trace)

    shapes: list[dict[str, Any]] = []
    annotations: list[dict[str, Any]] = []
    for vl in vlines or []:
        shapes.append(
            {
                "type": "line",
                "x0": vl["x"],
                "x1": vl["x"],
                "y0": 0,
                "y1": 1,
                "yref": "paper",
                "line": {
                    "color": vl.get("color", COPPER),
                    "width": vl.get("width", 1.2),
                    "dash": vl.get("dash", "dot"),
                },
            }
        )
        lbl = vl.get("label") or vl.get("annotation")
        if lbl:
            annotations.append(
                {
                    "x": vl["x"],
                    "y": 1.0,
                    "yref": "paper",
                    "text": lbl,
                    "showarrow": False,
                    "yanchor": "bottom",
                    "font": {"size": 10, "color": vl.get("color", COPPER)},
                }
            )
    for hl in hlines or []:
        shapes.append(
            {
                "type": "line",
                "x0": 0,
                "x1": 1,
                "xref": "paper",
                "y0": hl["y"],
                "y1": hl["y"],
                "line": {
                    "color": hl.get("color", SLATE),
                    "width": hl.get("width", 1.2),
                    "dash": hl.get("dash", "dash"),
                },
            }
        )
        lbl = hl.get("label") or hl.get("annotation")
        if lbl:
            annotations.append(
                {
                    "x": 0.99,
                    "xref": "paper",
                    "y": hl["y"],
                    "text": lbl,
                    "showarrow": False,
                    "xanchor": "right",
                    "yanchor": "bottom",
                    "font": {"size": 10, "color": hl.get("color", SLATE)},
                }
            )
    for vr in vrects or []:
        shapes.append(
            {
                "type": "rect",
                "x0": vr["x0"],
                "x1": vr["x1"],
                "y0": 0,
                "y1": 1,
                "yref": "paper",
                "fillcolor": vr.get("fillcolor") or vr.get("color") or "rgba(200,90,23,0.10)",
                "line": {"width": 0},
                "layer": "below",
            }
        )
        if vr.get("label"):
            annotations.append(
                {
                    "x": vr["x0"],
                    "y": 0.98,
                    "yref": "paper",
                    "text": vr["label"],
                    "showarrow": False,
                    "xanchor": "left",
                    "yanchor": "top",
                    "font": {"size": 10, "color": TEXT_SECONDARY},
                }
            )

    layout = base_layout(
        title=title,
        height=height,
        xaxis_title=xaxis_title,
        yaxis_title=yaxis_title,
        showlegend=showlegend,
    )
    if subtitle:
        layout["meta"] = {"subtitle": subtitle}
    if shapes:
        layout["shapes"] = shapes
    if annotations:
        layout["annotations"] = annotations
    return {"data": traces, "layout": layout}


def bar_figure(
    categories: Any,
    series: list[dict[str, Any]],
    title: str | None = None,
    subtitle: str | None = None,
    xaxis_title: str | None = None,
    yaxis_title: str | None = None,
    orientation: str = "v",
    barmode: str = "group",
    height: int = 380,
    hlines: list[dict[str, Any]] | None = None,
    vlines: list[dict[str, Any]] | None = None,
    showlegend: bool = True,
) -> dict[str, Any]:
    """Build a vertical or horizontal bar chart."""
    cats = _clean_list(categories)
    traces: list[dict[str, Any]] = []
    for idx, s in enumerate(series):
        vals = _clean_list(s["values"])
        colors = s.get("colors") or s.get("color") or channel_color(str(s.get("name", "")), idx)
        trace: dict[str, Any] = {
            "type": "bar",
            "name": s.get("name", f"Series {idx + 1}"),
            "orientation": orientation,
            "marker": {"color": colors},
        }
        if orientation == "h":
            trace["y"] = cats
            trace["x"] = vals
        else:
            trace["x"] = cats
            trace["y"] = vals
        if "error_y" in s:
            if orientation == "h":
                trace["error_x"] = s["error_y"]
            else:
                trace["error_y"] = s["error_y"]
        if "error_x" in s:
            trace["error_x"] = s["error_x"]
        if "text" in s:
            trace["text"] = _clean_list(s["text"])
            trace["textposition"] = s.get("textposition", "outside")
        traces.append(trace)

    layout = base_layout(
        title=title,
        height=height,
        xaxis_title=xaxis_title,
        yaxis_title=yaxis_title,
        barmode=barmode,
        showlegend=showlegend,
        hovermode="closest" if orientation == "h" else "x unified",
    )
    if subtitle:
        layout["meta"] = {"subtitle": subtitle}
    if orientation == "h":
        layout["margin"] = {"l": 140, "r": 32, "t": 44 if title else 24, "b": 48}

    shapes: list[dict[str, Any]] = []
    for hl in hlines or []:
        shapes.append(
            {
                "type": "line",
                "x0": 0,
                "x1": 1,
                "xref": "paper",
                "y0": hl["y"],
                "y1": hl["y"],
                "line": {
                    "color": hl.get("color", SLATE),
                    "width": hl.get("width", 1.5),
                    "dash": hl.get("dash", "dash"),
                },
            }
        )
    for vl in vlines or []:
        shapes.append(
            {
                "type": "line",
                "x0": vl["x"],
                "x1": vl["x"],
                "y0": 0,
                "y1": 1,
                "yref": "paper",
                "line": {
                    "color": vl.get("color", SLATE),
                    "width": vl.get("width", 1.5),
                    "dash": vl.get("dash", "dash"),
                },
            }
        )
    if shapes:
        layout["shapes"] = shapes
    return {"data": traces, "layout": layout}


def heatmap_figure(
    x: Any,
    y: Any,
    z: Any,
    title: str | None = None,
    subtitle: str | None = None,
    xaxis_title: str | None = None,
    yaxis_title: str | None = None,
    colorscale: Any = "RdBu_r",
    zmin: float | None = None,
    zmax: float | None = None,
    show_values: bool = True,
    value_fmt: str = ".2f",
    height: int = 420,
) -> dict[str, Any]:
    """Build a matrix / correlation / availability heatmap figure."""
    x_list = _clean_list(x)
    y_list = _clean_list(y)
    z_arr = np.asarray(z, dtype=float)
    z_list = [_clean_list(row) for row in z_arr]

    trace: dict[str, Any] = {
        "type": "heatmap",
        "x": x_list,
        "y": y_list,
        "z": z_list,
        "colorscale": colorscale,
        "hoverongaps": False,
    }
    if zmin is not None:
        trace["zmin"] = zmin
    if zmax is not None:
        trace["zmax"] = zmax

    if show_values and z_arr.size <= 225:
        text_grid = []
        for row in z_arr:
            text_row = []
            for val in row:
                if np.isnan(val):
                    text_row.append("")
                else:
                    text_row.append(format(float(val), value_fmt))
            text_grid.append(text_row)
        trace["text"] = text_grid
        trace["texttemplate"] = "%{text}"
        trace["textfont"] = {"size": 10}

    layout = base_layout(
        title=title,
        height=height,
        xaxis_title=xaxis_title,
        yaxis_title=yaxis_title,
        showlegend=False,
        hovermode="closest",
        margin={"l": 140, "r": 32, "t": 44 if title else 24, "b": 64},
    )
    if subtitle:
        layout["meta"] = {"subtitle": subtitle}
    return {"data": [trace], "layout": layout}


def small_multiples_figure(
    panels: list[dict[str, Any]],
    ncols: int = 3,
    height_per_row: int = 210,
    title: str | None = None,
    subtitle: str | None = None,
) -> dict[str, Any]:
    """Build a grid of subplot panels (e.g. 12 channels or 7 paid response curves)."""
    n = max(len(panels), 1)
    nrows = int(np.ceil(n / ncols))
    total_height = max(nrows * height_per_row + 60, 320)

    h_gap = 0.06 if ncols > 1 else 0.0
    v_gap = max(0.08 / max(nrows - 1, 1), 0.04) if nrows > 1 else 0.0
    col_w = (1.0 - h_gap * (ncols - 1)) / ncols
    row_h = (1.0 - v_gap * (nrows - 1)) / nrows

    traces: list[dict[str, Any]] = []
    layout = base_layout(title=title, height=total_height, showlegend=False, hovermode="closest")
    if subtitle:
        layout["meta"] = {"subtitle": subtitle}
    annotations: list[dict[str, Any]] = []

    for idx, panel in enumerate(panels):
        r = idx // ncols
        c = idx % ncols
        axis_num = idx + 1
        ax_suffix = "" if axis_num == 1 else str(axis_num)
        xref = f"x{ax_suffix}"
        yref = f"y{ax_suffix}"

        x0 = round(c * (col_w + h_gap), 4)
        x1 = round(x0 + col_w, 4)
        y1 = round(1.0 - r * (row_h + v_gap), 4)
        y0 = round(max(y1 - row_h, 0.0), 4)

        layout[f"xaxis{ax_suffix}"] = {
            "domain": [x0, x1],
            "anchor": yref,
            "gridcolor": GRID_COLOR,
            "linecolor": "#D0D7DE",
            "tickfont": {"size": 10},
            "title": {"text": panel.get("xaxis_title", ""), "font": {"size": 10}},
        }
        layout[f"yaxis{ax_suffix}"] = {
            "domain": [y0, y1],
            "anchor": xref,
            "gridcolor": GRID_COLOR,
            "linecolor": "#D0D7DE",
            "tickfont": {"size": 10},
            "title": {"text": panel.get("yaxis_title", ""), "font": {"size": 10}},
        }

        annotations.append(
            {
                "x": (x0 + x1) / 2.0,
                "y": y1 + 0.008,
                "xref": "paper",
                "yref": "paper",
                "text": f"<b>{panel.get('title', f'Panel {axis_num}')}</b>",
                "showarrow": False,
                "xanchor": "center",
                "yanchor": "bottom",
                "font": {"size": 11, "color": TEXT_PRIMARY},
            }
        )

        p_traces = panel.get("traces")
        if not p_traces:
            p_traces = [
                {
                    "type": panel.get("type", "scatter"),
                    "mode": panel.get("mode", "lines"),
                    "name": panel.get("title", f"Panel {axis_num}"),
                    "x": _clean_list(panel.get("x", [])),
                    "y": _clean_list(panel.get("y", [])),
                    "line": {"color": panel.get("color", channel_color(panel.get("title", ""), idx)), "width": 1.6},
                }
            ]
        for pt in p_traces:
            t_copy = dict(pt)
            kind = t_copy.pop("kind", None)
            color = t_copy.pop("color", None)
            if kind == "bar":
                t_copy["type"] = "bar"
                if color:
                    t_copy["marker"] = {"color": color}
            else:
                t_copy.setdefault("type", "scatter")
                t_copy.setdefault("mode", "lines")
                if color and "line" not in t_copy:
                    t_copy["line"] = {"color": color, "width": 1.8}
            t_copy["xaxis"] = xref
            t_copy["yaxis"] = yref
            if "x" not in t_copy and "x" in panel:
                t_copy["x"] = _clean_list(panel["x"])
            elif "x" in t_copy:
                t_copy["x"] = _clean_list(t_copy["x"])
            if "y" in t_copy:
                t_copy["y"] = _clean_list(t_copy["y"])
            traces.append(t_copy)

    layout["annotations"] = annotations
    layout["margin"] = {"l": 52, "r": 20, "t": 54 if title else 36, "b": 40}
    return {"data": traces, "layout": layout}


__all__ = [
    "COPPER",
    "INK_MUTED",
    "NAVY",
    "SLATE",
    "STATUS_DANGER",
    "STATUS_SUCCESS",
    "STATUS_WARNING",
    "TEAL",
    "bar_figure",
    "base_layout",
    "channel_color",
    "heatmap_figure",
    "line_figure",
    "small_multiples_figure",
]
