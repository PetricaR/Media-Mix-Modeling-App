"""Budget optimisation over Meridian Hill response curves and `BudgetOptimizer` wrapper."""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
from scipy.interpolate import PchipInterpolator
from scipy.optimize import minimize

from mmm_studio.charts.builders import bar_figure
from mmm_studio.charts.theme import SLATE, TEAL
from mmm_studio.core.meridian.spec import CHANNEL_DISPLAY_NAMES
from mmm_studio.report.narrative import fmt_eur
from mmm_studio.report.sections import (
    CalloutBlock,
    ChartBlock,
    KpiItem,
    KpisBlock,
    Section,
    TableBlock,
    TableColumn,
)


def run_meridian_budget_optimizer(mmm: Any) -> tuple[Any, pd.DataFrame]:
    """Run Meridian's native `BudgetOptimizer` and extract the channel allocation table."""
    from meridian.analysis import optimizer

    opt = optimizer.BudgetOptimizer(mmm).optimize()
    od = opt.optimized_data
    nd = opt.nonoptimized_data

    cur_spend = nd["spend"].to_series()
    opt_spend = od["spend"].to_series()
    cur_inc = nd["incremental_outcome"].sel(metric="mean").to_series() if "metric" in nd["incremental_outcome"].dims else nd["incremental_outcome"].to_series()
    opt_inc = od["incremental_outcome"].sel(metric="mean").to_series() if "metric" in od["incremental_outcome"].dims else od["incremental_outcome"].to_series()

    df = pd.DataFrame(
        {
            "current_spend": cur_spend,
            "optimised_spend": opt_spend,
            "change": opt_spend - cur_spend,
            "current_inc_sales": cur_inc,
            "optimised_inc_sales": opt_inc,
        }
    )
    df.index.name = "channel"
    return opt, df


def optimise_hill_response_curves(
    response_curves: dict[str, pd.DataFrame],
    current_spend: dict[str, float] | None = None,
    total_budget: float | None = None,
    min_pct: float = 0.5,
    max_pct: float = 1.5,
    channel_bounds: dict[str, tuple[float, float]] | None = None,
) -> dict[str, Any]:
    """Constrained non-linear budget optimiser over channel response curves (SLSQP).

    Supports custom `total_budget`, uniform `[min_pct, max_pct]` multipliers around each
    channel's historical spend, or per-channel `(min_eur, max_eur)` bounds.
    """
    channels = list(response_curves.keys())
    if not channels:
        return {"total_budget": 0.0, "current_inc_sales": 0.0, "optimised_inc_sales": 0.0, "lift_pct": 0.0, "allocations": []}

    cur_sp_map: dict[str, float] = {}
    interpolators: dict[str, PchipInterpolator] = {}
    lo_interpolators: dict[str, PchipInterpolator] = {}
    hi_interpolators: dict[str, PchipInterpolator] = {}

    for ch in channels:
        rc = response_curves[ch].sort_values("spend").drop_duplicates(subset=["spend"])
        sp_vals = rc["spend"].to_numpy(dtype=float)
        mean_vals = rc["mean"].to_numpy(dtype=float)
        lo_vals = rc["ci_lo"].to_numpy(dtype=float) if "ci_lo" in rc.columns else mean_vals * 0.85
        hi_vals = rc["ci_hi"].to_numpy(dtype=float) if "ci_hi" in rc.columns else mean_vals * 1.15

        if current_spend and ch in current_spend:
            cur_sp_map[ch] = float(current_spend[ch])
        elif "multiplier" in rc.columns:
            idx1 = int(np.argmin(np.abs(rc["multiplier"].to_numpy(dtype=float) - 1.0)))
            cur_sp_map[ch] = float(sp_vals[idx1])
        else:
            cur_sp_map[ch] = float(np.median(sp_vals))

        interpolators[ch] = PchipInterpolator(sp_vals, mean_vals, extrapolate=True)
        lo_interpolators[ch] = PchipInterpolator(sp_vals, lo_vals, extrapolate=True)
        hi_interpolators[ch] = PchipInterpolator(sp_vals, hi_vals, extrapolate=True)

    x0 = np.array([cur_sp_map[ch] for ch in channels], dtype=float)
    budget = float(total_budget) if total_budget is not None else float(x0.sum())

    bounds: list[tuple[float, float]] = []
    for ch in channels:
        c_sp = cur_sp_map[ch]
        if channel_bounds and ch in channel_bounds:
            b_lo, b_hi = channel_bounds[ch]
        else:
            b_lo = max(0.0, c_sp * min_pct)
            b_hi = c_sp * max_pct
        bounds.append((float(b_lo), float(max(b_lo, b_hi))))

    # Ensure budget is feasible within bounds
    sum_lo = sum(b[0] for b in bounds)
    sum_hi = sum(b[1] for b in bounds)
    if budget < sum_lo:
        scale = budget / max(sum_lo, 1.0)
        bounds = [(b[0] * scale, b[1]) for b in bounds]
    elif budget > sum_hi:
        scale = budget / max(sum_hi, 1.0)
        bounds = [(b[0], b[1] * scale) for b in bounds]

    # Feasible initial guess
    x_init = np.clip(x0 * (budget / max(x0.sum(), 1.0)), [b[0] for b in bounds], [b[1] for b in bounds])

    def neg_total_inc(x: np.ndarray) -> float:
        return -sum(float(interpolators[ch](x[i])) for i, ch in enumerate(channels))

    res = minimize(
        neg_total_inc,
        x_init,
        method="SLSQP",
        bounds=bounds,
        constraints=[{"type": "eq", "fun": lambda x: float(np.sum(x) - budget)}],
        options={"maxiter": 400, "ftol": 1e-3},
    )
    x_opt = np.clip(res.x if res.success else x_init, [b[0] for b in bounds], [b[1] for b in bounds])
    if x_opt.sum() > 0:
        x_opt = x_opt * (budget / x_opt.sum())

    cur_inc_total = sum(float(interpolators[ch](x0[i])) for i, ch in enumerate(channels))
    opt_inc_total = sum(float(interpolators[ch](x_opt[i])) for i, ch in enumerate(channels))
    lift_eur = opt_inc_total - cur_inc_total
    lift_pct = 100.0 * (opt_inc_total / max(cur_inc_total, 1.0) - 1.0)

    allocations = []
    for i, ch in enumerate(channels):
        c_sp = float(x0[i])
        o_sp = float(x_opt[i])
        c_inc = float(interpolators[ch](c_sp))
        o_inc = float(interpolators[ch](o_sp))
        o_lo = float(lo_interpolators[ch](o_sp))
        o_hi = float(hi_interpolators[ch](o_sp))
        allocations.append(
            {
                "channel": ch,
                "label": CHANNEL_DISPLAY_NAMES.get(ch, ch),
                "current_spend": round(c_sp, 0),
                "optimised_spend": round(o_sp, 0),
                "spend_delta": round(o_sp - c_sp, 0),
                "spend_delta_pct": round(100.0 * (o_sp / max(c_sp, 1.0) - 1.0), 1),
                "current_inc_sales": round(c_inc, 0),
                "optimised_inc_sales": round(o_inc, 0),
                "optimised_inc_lo": round(o_lo, 0),
                "optimised_inc_hi": round(o_hi, 0),
                "inc_sales_delta": round(o_inc - c_inc, 0),
                "optimised_roi": round(o_inc / max(o_sp, 1.0), 2),
            }
        )

    return {
        "total_budget": round(budget, 0),
        "historical_budget": round(float(x0.sum()), 0),
        "current_inc_sales": round(cur_inc_total, 0),
        "optimised_inc_sales": round(opt_inc_total, 0),
        "historical_incremental_revenue": round(cur_inc_total, 0),
        "optimised_incremental_revenue": round(opt_inc_total, 0),
        "lift_eur": round(lift_eur, 0),
        "lift_pct": round(lift_pct, 2),
        "uplift_eur": round(lift_eur, 0),
        "uplift_pct": round(lift_pct, 2),
        "min_pct": min_pct,
        "max_pct": max_pct,
        "allocations": allocations,
        "channels": allocations,
    }


def solve_constrained_optimisation(
    roi_df: pd.DataFrame,
    response_curves: dict[str, pd.DataFrame],
    total_budget: float | None = None,
    channel_bounds: dict[str, tuple[float, float]] | None = None,
    default_bounds: tuple[float, float] = (0.7, 1.3),
    include_catalog: bool = True,
) -> dict[str, Any]:
    """Wrapper over `optimise_hill_response_curves` supporting `include_catalog` and multiplier bounds."""
    media_rt = roi_df.drop(index="All Channels", errors="ignore")
    cur_spend_map = {str(ch): float(media_rt.loc[ch, "spend"]) for ch in media_rt.index if ch in response_curves}

    abs_bounds: dict[str, tuple[float, float]] = {}
    for ch, c_sp in cur_spend_map.items():
        if not include_catalog and ch == "catalog":
            abs_bounds[ch] = (c_sp, c_sp)
        elif channel_bounds and ch in channel_bounds:
            lo_m, hi_m = channel_bounds[ch]
            # Support both multiplier bounds (<= 10) and absolute EUR bounds
            if hi_m <= 10.0:
                abs_bounds[ch] = (c_sp * lo_m, c_sp * hi_m)
            else:
                abs_bounds[ch] = (lo_m, hi_m)
        else:
            abs_bounds[ch] = (c_sp * default_bounds[0], c_sp * default_bounds[1])

    return optimise_hill_response_curves(
        response_curves=response_curves,
        current_spend=cur_spend_map,
        total_budget=total_budget,
        min_pct=default_bounds[0],
        max_pct=default_bounds[1],
        channel_bounds=abs_bounds,
    )


def build_optimiser_section(
    roi_df: pd.DataFrame,
    response_curves: dict[str, pd.DataFrame],
    opt_alloc_df: pd.DataFrame | None = None,
) -> tuple[Section, dict[str, Any]]:
    """Build the `optimiser` section (`03_budget_optimisation`) and return `(section, default_opt_result)`."""
    media_rt = roi_df.drop(index="All Channels", errors="ignore")
    cur_spend_map = {str(ch): float(media_rt.loc[ch, "spend"]) for ch in media_rt.index if ch in response_curves}
    opt_res = optimise_hill_response_curves(
        response_curves=response_curves,
        current_spend=cur_spend_map,
        min_pct=0.70,
        max_pct=1.30,
    )

    if opt_alloc_df is not None and len(opt_alloc_df) > 0:
        # Override default table with Meridian native BudgetOptimizer output if provided
        allocs = []
        for ch, r in opt_alloc_df.iterrows():
            if ch == "All Channels":
                continue
            c_sp = float(r["current_spend"])
            o_sp = float(r["optimised_spend"])
            c_inc = float(r["current_inc_sales"])
            o_inc = float(r["optimised_inc_sales"])
            allocs.append(
                {
                    "channel": str(ch),
                    "label": CHANNEL_DISPLAY_NAMES.get(str(ch), str(ch)),
                    "current_spend": round(c_sp, 0),
                    "optimised_spend": round(o_sp, 0),
                    "spend_delta": round(o_sp - c_sp, 0),
                    "spend_delta_pct": round(100.0 * (o_sp / max(c_sp, 1.0) - 1.0), 1),
                    "current_inc_sales": round(c_inc, 0),
                    "optimised_inc_sales": round(o_inc, 0),
                    "inc_sales_delta": round(o_inc - c_inc, 0),
                    "optimised_roi": round(o_inc / max(o_sp, 1.0), 2),
                }
            )
        if allocs:
            opt_res["allocations"] = allocs
            opt_res["total_budget"] = round(sum(a["optimised_spend"] for a in allocs), 0)
            opt_res["current_inc_sales"] = round(sum(a["current_inc_sales"] for a in allocs), 0)
            opt_res["optimised_inc_sales"] = round(sum(a["optimised_inc_sales"] for a in allocs), 0)
            opt_res["lift_eur"] = round(opt_res["optimised_inc_sales"] - opt_res["current_inc_sales"], 0)
            opt_res["lift_pct"] = round(
                100.0 * (opt_res["optimised_inc_sales"] / max(opt_res["current_inc_sales"], 1.0) - 1.0), 2
            )

    allocs_sorted = sorted(opt_res["allocations"], key=lambda a: a["current_spend"])
    labels = [a["label"] for a in allocs_sorted]
    cur_m = [round(a["current_spend"] / 1e6, 2) for a in allocs_sorted]
    opt_m = [round(a["optimised_spend"] / 1e6, 2) for a in allocs_sorted]

    fig_opt = bar_figure(
        categories=labels,
        series=[
            {"name": "Historical Spend (€M)", "values": cur_m, "color": SLATE},
            {"name": "Optimised Spend (€M)", "values": opt_m, "color": TEAL},
        ],
        barmode="group",
        orientation="h",
        title="Same Total Budget Re-Allocated: Historical vs Optimised Channel Spend (€M)",
        subtitle=f"Total 2-year media budget = {fmt_eur(opt_res['total_budget'])}; expected incremental sales lift = +{opt_res['lift_pct']:.1f}% ({fmt_eur(opt_res['lift_eur'])})",
        xaxis_title="2-Year Channel Spend (€ Millions)",
    )

    table_rows = [
        {
            "channel": a["label"],
            "cur_spend": fmt_eur(a["current_spend"]),
            "opt_spend": fmt_eur(a["optimised_spend"]),
            "delta_spend": f"{'+' if a['spend_delta'] >= 0 else ''}{fmt_eur(a['spend_delta'])} ({a['spend_delta_pct']:+.1f}%)",
            "cur_inc": fmt_eur(a["current_inc_sales"]),
            "opt_inc": fmt_eur(a["optimised_inc_sales"]),
            "opt_roi": f"{a['optimised_roi']:.2f}x",
        }
        for a in sorted(opt_res["allocations"], key=lambda x: x["current_spend"], reverse=True)
    ]

    blocks = [
        KpisBlock(
            items=[
                KpiItem(
                    label="Total Optimised Media Budget",
                    value=fmt_eur(opt_res["total_budget"]),
                    delta="Budget-neutral reallocation",
                    status="neutral",
                ),
                KpiItem(
                    label="Historical Incremental Sales",
                    value=fmt_eur(opt_res["current_inc_sales"]),
                    delta="Current allocation baseline",
                    status="neutral",
                ),
                KpiItem(
                    label="Optimised Incremental Sales",
                    value=fmt_eur(opt_res["optimised_inc_sales"]),
                    delta=f"+{opt_res['lift_pct']:.1f}% (+{fmt_eur(opt_res['lift_eur'])})",
                    status="success",
                ),
                KpiItem(
                    label="Primary Reallocation Vector",
                    value="Display → SEA",
                    delta="Subject to SEA geo-lift check",
                    status="warn",
                ),
            ]
        ),
        CalloutBlock(
            tone="warn",
            title="Decision Guardrail Before Moving Budget Into SEA",
            body=(
                "The response-curve optimiser takes posterior ROIs at face value and shifts budget from saturated Display into SEA. "
                "Because SEA spend follows organic search demand, run a geo or time-based hold-out test on SEA before executing large budget shifts. "
                "Use the interactive controls above to impose tighter channel guardrails (e.g. ±15% max shift)."
            ),
        ),
        ChartBlock(
            id="optimiser_allocation_chart",
            title="Budget Re-Allocation: Current vs Optimised Spend by Channel (€M)",
            subtitle="Constrained Hill response-curve optimisation at constant total budget",
            figure=fig_opt,
        ),
        TableBlock(
            id="optimiser_allocation_table",
            title="Channel-Level Budget Optimisation Table (2023–2024 Window)",
            subtitle="Comparison of current vs optimised spend and expected incremental revenue",
            columns=[
                TableColumn(key="channel", label="Channel"),
                TableColumn(key="cur_spend", label="Current Spend", align="right"),
                TableColumn(key="opt_spend", label="Optimised Spend", align="right"),
                TableColumn(key="delta_spend", label="Spend Change", align="right"),
                TableColumn(key="cur_inc", label="Current Inc. Sales", align="right"),
                TableColumn(key="opt_inc", label="Optimised Inc. Sales", align="right"),
                TableColumn(key="opt_roi", label="Optimised ROI", align="right"),
            ],
            rows=table_rows,
        ),
    ]

    sec = Section(
        id="optimiser",
        title="Budget Optimisation & Reallocation Simulator",
        subtitle="Constrained non-linear allocation across channel Hill saturation curves",
        blocks=blocks,
    )
    return sec, opt_res
