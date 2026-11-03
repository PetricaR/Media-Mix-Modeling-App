"""Budget optimisation over Meridian Hill response curves and `BudgetOptimizer` wrapper.

Constraints are never rewritten: an infeasible budget, a failed solve or a result outside its bounds raises
`OptimisationError` with the reason, so a recommendation always respects every limit the user set.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import numpy as np
import pandas as pd

from mmm_studio.charts.builders import bar_figure
from mmm_studio.charts.theme import SLATE, channel_color
from mmm_studio.core.meridian.spec import channel_label
from mmm_studio.report.narrative import fmt_eur, fmt_eur_signed
from mmm_studio.report.sections import (
    CalloutBlock,
    ChartBlock,
    KpiItem,
    KpisBlock,
    Section,
    TableBlock,
    TableColumn,
)

# Meridian's own fixed-budget default: every channel may move ±30 % from its historical spend
NATIVE_SPEND_CONSTRAINT = 0.3
# Response curves are computed for 0–200 % of historical spend; bounds beyond that would extrapolate
MAX_CURVE_MULTIPLIER = 2.0
# Share of the optimiser's lift above which channels with an upper-bound ROI make the headline conditional
UPPER_BOUND_LIFT_SHARE = 0.5


class OptimisationError(ValueError):
    """The optimisation cannot be solved as requested (infeasible budget, invalid bounds or solver failure)."""


class OptimisationInfeasible(OptimisationError):
    """The total budget lies outside the range the channel bounds allow."""

    def __init__(self, budget: float, lo: float, hi: float) -> None:
        self.budget, self.lo, self.hi = budget, lo, hi
        super().__init__(
            f"A total budget of {fmt_eur(budget)} cannot be allocated within the channel limits: they allow between "
            f"{fmt_eur(lo)} and {fmt_eur(hi)}. Change the budget or widen the limits."
        )


def _media_table(roi_df: pd.DataFrame) -> pd.DataFrame:
    media_rt = roi_df.copy()
    if "channel" in media_rt.columns and media_rt.index.name != "channel":
        media_rt = media_rt.set_index("channel")
    return media_rt.drop(index="All Channels", errors="ignore")


def run_meridian_budget_optimizer(mmm: Any, fixed_channels: Sequence[str] = ()) -> tuple[Any, pd.DataFrame]:
    """Run Meridian's native `BudgetOptimizer` (same budget, ±30 %) and extract the channel allocation table.

    Channels in `fixed_channels` keep their historical spend (constraint 0 on both sides).
    """
    from meridian.analysis import optimizer

    kwargs: dict[str, Any] = {}
    if fixed_channels:
        channels = list(mmm.input_data.media_channel.values)
        limits = [0.0 if ch in fixed_channels else NATIVE_SPEND_CONSTRAINT for ch in channels]
        kwargs = {"spend_constraint_lower": limits, "spend_constraint_upper": limits}
    opt = optimizer.BudgetOptimizer(mmm).optimize(**kwargs)
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


def _curve_bounds(
    channels: list[str],
    current: dict[str, float],
    curve_max: dict[str, float],
    min_pct: float,
    max_pct: float,
    channel_bounds: dict[str, tuple[float, float]] | None,
) -> list[tuple[float, float]]:
    bounds: list[tuple[float, float]] = []
    for ch in channels:
        c_sp = current[ch]
        lo, hi = channel_bounds[ch] if channel_bounds and ch in channel_bounds else (c_sp * min_pct, c_sp * max_pct)
        lo, hi = float(lo), float(hi)
        if lo < 0 or hi < lo:
            raise OptimisationError(f"Invalid limits for {channel_label(ch)}: {fmt_eur(lo)} to {fmt_eur(hi)}.")
        if hi > curve_max[ch] * (1 + 1e-9) + 1.0:
            raise OptimisationError(
                f"The upper limit for {channel_label(ch)} ({fmt_eur(hi)}) is beyond its response curve "
                f"({fmt_eur(curve_max[ch])}, {MAX_CURVE_MULTIPLIER:.0f}× its spend); the model has no evidence there."
            )
        bounds.append((lo, hi))
    return bounds


def optimise_hill_response_curves(
    response_curves: dict[str, pd.DataFrame],
    current_spend: dict[str, float] | None = None,
    total_budget: float | None = None,
    min_pct: float = 0.5,
    max_pct: float = 1.5,
    channel_bounds: dict[str, tuple[float, float]] | None = None,
) -> dict[str, Any]:
    """Constrained non-linear budget optimiser over channel response curves (SLSQP).

    Supports a custom `total_budget`, uniform `[min_pct, max_pct]` multipliers around each channel's historical
    spend, or per-channel `(min_eur, max_eur)` bounds. Raises `OptimisationError` instead of relaxing anything.
    """
    channels = list(response_curves.keys())
    if not channels:
        raise OptimisationError("There are no response curves to optimise.")

    from scipy.interpolate import PchipInterpolator
    from scipy.optimize import minimize

    cur_sp_map: dict[str, float] = {}
    curve_max: dict[str, float] = {}
    interpolators: dict[str, PchipInterpolator] = {}
    lo_interpolators: dict[str, PchipInterpolator] = {}
    hi_interpolators: dict[str, PchipInterpolator] = {}

    for ch in channels:
        rc = response_curves[ch].sort_values("spend").drop_duplicates(subset=["spend"])
        sp_vals = rc["spend"].to_numpy(dtype=float)
        mean_vals = rc["mean"].to_numpy(dtype=float)
        lo_vals = rc["ci_lo"].to_numpy(dtype=float) if "ci_lo" in rc.columns else mean_vals
        hi_vals = rc["ci_hi"].to_numpy(dtype=float) if "ci_hi" in rc.columns else mean_vals

        if current_spend and ch in current_spend:
            cur_sp_map[ch] = float(current_spend[ch])
        elif "multiplier" in rc.columns:
            idx1 = int(np.argmin(np.abs(rc["multiplier"].to_numpy(dtype=float) - 1.0)))
            cur_sp_map[ch] = float(sp_vals[idx1])
        else:
            cur_sp_map[ch] = float(np.median(sp_vals))
        curve_max[ch] = float(sp_vals[-1])
        # No extrapolation: bounds are checked against the curve's range above
        interpolators[ch] = PchipInterpolator(sp_vals, mean_vals, extrapolate=False)
        lo_interpolators[ch] = PchipInterpolator(sp_vals, lo_vals, extrapolate=False)
        hi_interpolators[ch] = PchipInterpolator(sp_vals, hi_vals, extrapolate=False)

    def curve(ch: str, x: float, which: dict[str, PchipInterpolator] = interpolators) -> float:
        return float(which[ch](min(max(x, 0.0), curve_max[ch])))

    x0 = np.array([cur_sp_map[ch] for ch in channels], dtype=float)
    budget = float(total_budget) if total_budget is not None else float(x0.sum())
    bounds = _curve_bounds(channels, cur_sp_map, curve_max, min_pct, max_pct, channel_bounds)
    lo_arr = np.array([b[0] for b in bounds], dtype=float)
    hi_arr = np.array([b[1] for b in bounds], dtype=float)
    tol = max(1.0, 1e-9 * budget)
    if budget < lo_arr.sum() - tol or budget > hi_arr.sum() + tol:
        raise OptimisationInfeasible(budget, float(lo_arr.sum()), float(hi_arr.sum()))

    # Feasible start: the same share of each channel's room between its bounds
    slack = hi_arr - lo_arr
    share = float(np.clip((budget - lo_arr.sum()) / slack.sum(), 0.0, 1.0)) if slack.sum() > 0 else 0.0
    x_init = lo_arr + share * slack

    # Solve in € millions with analytic gradients: on raw euros (~1e7–1e8) SLSQP's finite-difference step is lost in
    # floating-point noise, the gradient comes out as zero and the optimiser returns the starting allocation.
    scale = 1e6
    derivs = {ch: interpolators[ch].derivative() for ch in channels}

    def neg_total_inc(u: np.ndarray) -> float:
        return -sum(curve(ch, u[i] * scale) for i, ch in enumerate(channels)) / scale

    def neg_total_inc_grad(u: np.ndarray) -> np.ndarray:
        return -np.array([float(derivs[ch](min(max(u[i] * scale, 0.0), curve_max[ch]))) for i, ch in enumerate(channels)])

    res = minimize(
        neg_total_inc,
        x_init / scale,
        jac=neg_total_inc_grad,
        method="SLSQP",
        bounds=[(b[0] / scale, b[1] / scale) for b in bounds],
        constraints=[{"type": "eq", "fun": lambda u: float(np.sum(u) - budget / scale), "jac": lambda u: np.ones_like(u)}],
        options={"maxiter": 500, "ftol": 1e-9},
    )
    if not res.success:
        raise OptimisationError(f"The optimiser did not converge ({res.message}); no allocation is reported.")
    x_opt = np.asarray(res.x, dtype=float) * scale
    # SLSQP satisfies bounds up to its tolerance; snap the tiny excursions and check the result honestly
    x_opt = np.clip(x_opt, lo_arr, hi_arr)
    if abs(float(x_opt.sum()) - budget) > max(tol, 1e-6 * budget):
        raise OptimisationError(
            f"The optimiser's allocation sums to {fmt_eur(float(x_opt.sum()))} instead of {fmt_eur(budget)}; no allocation is reported."
        )

    cur_inc_total = sum(curve(ch, x0[i]) for i, ch in enumerate(channels))
    opt_inc_total = sum(curve(ch, x_opt[i]) for i, ch in enumerate(channels))
    lift_eur = opt_inc_total - cur_inc_total
    lift_pct = 100.0 * (opt_inc_total / max(cur_inc_total, 1.0) - 1.0)

    allocations = []
    for i, ch in enumerate(channels):
        c_sp = float(x0[i])
        o_sp = float(x_opt[i])
        c_inc = curve(ch, c_sp)
        o_inc = curve(ch, o_sp)
        delta_sp = round(o_sp - c_sp, 0)
        delta_pct = round(100.0 * (o_sp / max(c_sp, 1.0) - 1.0), 1)
        eps = max(o_sp * 0.01, 100.0)
        lo_x, hi_x = max(0.0, o_sp - eps), min(curve_max[ch], o_sp + eps)
        mroi_val = (curve(ch, hi_x) - curve(ch, lo_x)) / max(hi_x - lo_x, 1.0)
        opt_roi = round(o_inc / max(o_sp, 1.0), 2)
        allocations.append(
            {
                "channel": ch,
                "label": channel_label(ch),
                "channel_label": channel_label(ch),
                "current_spend": round(c_sp, 0),
                "historical_spend": round(c_sp, 0),
                "optimised_spend": round(o_sp, 0),
                "optimal_spend": round(o_sp, 0),
                "spend_delta": delta_sp,
                "delta_spend": delta_sp,
                "spend_delta_pct": delta_pct,
                "delta_pct": delta_pct,
                "multiplier": round(o_sp / max(c_sp, 1.0), 3),
                "min_bound": round(bounds[i][0] / max(c_sp, 1.0), 2),
                "max_bound": round(bounds[i][1] / max(c_sp, 1.0), 2),
                "current_inc_sales": round(c_inc, 0),
                "historical_incremental": round(c_inc, 0),
                "optimised_inc_sales": round(o_inc, 0),
                "optimised_incremental": round(o_inc, 0),
                "expected_incremental_sales": round(o_inc, 0),
                "optimised_inc_lo": round(curve(ch, o_sp, lo_interpolators), 0),
                "optimised_inc_hi": round(curve(ch, o_sp, hi_interpolators), 0),
                "inc_sales_delta": round(o_inc - c_inc, 0),
                "delta_incremental": round(o_inc - c_inc, 0),
                "historical_roi": round(c_inc / max(c_sp, 1.0), 2),
                "optimised_roi": opt_roi,
                "roi": opt_roi,
                "mroi": round(mroi_val, 2),
            }
        )

    return {
        "feasible": True,
        "total_budget": round(budget, 0),
        "historical_budget": round(float(x0.sum()), 0),
        "historical_spend_total": round(float(x0.sum()), 0),
        # Incremental (media-driven) sales only, not total sales
        "current_inc_sales": round(cur_inc_total, 0),
        "optimised_inc_sales": round(opt_inc_total, 0),
        "historical_incremental_revenue": round(cur_inc_total, 0),
        "optimised_incremental_revenue": round(opt_inc_total, 0),
        "lift_eur": round(lift_eur, 0),
        "lift_pct": round(lift_pct, 2),
        "uplift_eur": round(lift_eur, 0),
        "uplift_pct": round(lift_pct, 2),
        "expected_uplift_sales": round(lift_eur, 0),
        "expected_uplift_pct": round(lift_pct, 2),
        "include_catalog": True,
        "min_pct": min_pct,
        "max_pct": max_pct,
        "allocations": allocations,
        "channels": allocations,
    }


def constraint_bounds(
    roi_df: pd.DataFrame,
    response_curves: dict[str, pd.DataFrame],
    channel_bounds: dict[str, tuple[float, float]] | None = None,
    default_bounds: tuple[float, float] = (0.7, 1.3),
    fixed_channels: Sequence[str] = (),
) -> tuple[dict[str, float], dict[str, tuple[float, float]]]:
    """`(current_spend, absolute_bounds)` per media channel: multipliers of historical spend → euros.

    `channel_bounds` are always multipliers `(min_mult, max_mult)`; `fixed_channels` keep their spend exactly.
    """
    media_rt = _media_table(roi_df)
    current = {str(ch): float(media_rt.loc[ch, "spend"]) for ch in media_rt.index if ch in response_curves}
    abs_bounds: dict[str, tuple[float, float]] = {}
    for ch, c_sp in current.items():
        if ch in fixed_channels:
            abs_bounds[ch] = (c_sp, c_sp)
        elif channel_bounds and ch in channel_bounds:
            lo_m, hi_m = channel_bounds[ch]
            abs_bounds[ch] = (c_sp * float(lo_m), c_sp * float(hi_m))
        else:
            abs_bounds[ch] = (c_sp * default_bounds[0], c_sp * default_bounds[1])
    return current, abs_bounds


def feasible_budget_range(
    roi_df: pd.DataFrame,
    response_curves: dict[str, pd.DataFrame],
    channel_bounds: dict[str, tuple[float, float]] | None = None,
    default_bounds: tuple[float, float] = (0.7, 1.3),
    fixed_channels: Sequence[str] = (),
) -> tuple[float, float]:
    """The smallest and largest total budget the channel limits allow."""
    _, abs_bounds = constraint_bounds(roi_df, response_curves, channel_bounds, default_bounds, fixed_channels)
    return sum(b[0] for b in abs_bounds.values()), sum(b[1] for b in abs_bounds.values())


def solve_constrained_optimisation(
    roi_df: pd.DataFrame,
    response_curves: dict[str, pd.DataFrame],
    total_budget: float | None = None,
    channel_bounds: dict[str, tuple[float, float]] | None = None,
    default_bounds: tuple[float, float] = (0.7, 1.3),
    include_catalog: bool = True,
    print_slugs: Sequence[str] = ("catalog",),
) -> dict[str, Any]:
    """Wrapper over `optimise_hill_response_curves` with multiplier bounds; `include_catalog=False` fixes the
    print-event channels at their historical spend."""
    fixed = () if include_catalog else tuple(print_slugs)
    current, abs_bounds = constraint_bounds(roi_df, response_curves, channel_bounds, default_bounds, fixed)
    res = optimise_hill_response_curves(
        response_curves={ch: response_curves[ch] for ch in current},
        current_spend=current,
        total_budget=total_budget,
        min_pct=default_bounds[0],
        max_pct=default_bounds[1],
        channel_bounds=abs_bounds,
    )
    res["include_catalog"] = include_catalog
    res["fixed_channels"] = list(fixed)
    return res


def _native_allocations(opt_alloc_df: pd.DataFrame, fixed: Sequence[str] = ()) -> list[dict[str, Any]]:
    allocs = []
    for ch, r in opt_alloc_df.iterrows():
        if ch == "All Channels":
            continue
        c_sp = float(r["current_spend"])
        o_sp = float(r["optimised_spend"])
        c_inc = float(r["current_inc_sales"])
        o_inc = float(r["optimised_inc_sales"])
        bound = 0.0 if ch in fixed else NATIVE_SPEND_CONSTRAINT
        allocs.append(
            {
                "channel": str(ch),
                "label": channel_label(str(ch)),
                "current_spend": round(c_sp, 0),
                "historical_spend": round(c_sp, 0),
                "optimised_spend": round(o_sp, 0),
                "spend_delta": round(o_sp - c_sp, 0),
                "delta_spend": round(o_sp - c_sp, 0),
                "spend_delta_pct": round(100.0 * (o_sp / max(c_sp, 1.0) - 1.0), 1),
                "delta_pct": round(100.0 * (o_sp / max(c_sp, 1.0) - 1.0), 1),
                "multiplier": round(o_sp / max(c_sp, 1.0), 3),
                "min_bound": round(1 - bound, 2),
                "max_bound": round(1 + bound, 2),
                "current_inc_sales": round(c_inc, 0),
                "historical_incremental": round(c_inc, 0),
                "optimised_inc_sales": round(o_inc, 0),
                "optimised_incremental": round(o_inc, 0),
                "inc_sales_delta": round(o_inc - c_inc, 0),
                "delta_incremental": round(o_inc - c_inc, 0),
                "historical_roi": round(c_inc / max(c_sp, 1.0), 2),
                "optimised_roi": round(o_inc / max(o_sp, 1.0), 2),
            }
        )
    return allocs


def _summary(allocs: list[dict[str, Any]], method: str) -> dict[str, Any]:
    cur = sum(a["current_inc_sales"] for a in allocs)
    opt = sum(a["optimised_inc_sales"] for a in allocs)
    return {
        "feasible": True,
        "method": method,
        "allocations": allocs,
        "channels": allocs,
        "total_budget": round(sum(a["optimised_spend"] for a in allocs), 0),
        "historical_budget": round(sum(a["current_spend"] for a in allocs), 0),
        "historical_spend_total": round(sum(a["current_spend"] for a in allocs), 0),
        "current_inc_sales": round(cur, 0),
        "optimised_inc_sales": round(opt, 0),
        "historical_incremental_revenue": round(cur, 0),
        "optimised_incremental_revenue": round(opt, 0),
        "lift_eur": round(opt - cur, 0),
        "lift_pct": round(100.0 * (opt / max(cur, 1.0) - 1.0), 2),
        "uplift_eur": round(opt - cur, 0),
        "uplift_pct": round(100.0 * (opt / max(cur, 1.0) - 1.0), 2),
    }


def lift_share(res: dict[str, Any], channels: Sequence[str]) -> float:
    """Share of the total lift that comes from `channels` (can exceed 1 when the other moves lose sales)."""
    lift = float(res["lift_eur"])
    if abs(lift) < 1.0:
        return 0.0
    return sum(float(a["inc_sales_delta"]) for a in res["allocations"] if a["channel"] in channels) / lift


def build_optimiser_section(
    roi_df: pd.DataFrame,
    response_curves: dict[str, pd.DataFrame],
    opt_alloc_df: pd.DataFrame | None = None,
    native_error: str | None = None,
    fixed_alloc_df: pd.DataFrame | None = None,
    upper_bound: Sequence[str] = (),
) -> tuple[Section, dict[str, Any]]:
    """Build the `optimiser` section (`03_budget_optimisation`) and return `(section, default_opt_result)`.

    The numbers come from Meridian's own `BudgetOptimizer` (same total budget, ±30 %, notebook 03 §8). When channels
    have an upper-bound ROI (`upper_bound`, cost follows demand), the headline is the variant that keeps them at their
    historical spend (`fixed_alloc_df`); the unconstrained result is shown next to it with the share of its lift that
    those channels deliver. If Meridian's optimiser fails, the response-curve optimiser is used and the section says so.
    """
    media_rt = _media_table(roi_df)
    upper = [c for c in upper_bound if c in media_rt.index]
    if opt_alloc_df is not None and len(opt_alloc_df) > 0:
        free = _summary(_native_allocations(opt_alloc_df), "Meridian BudgetOptimizer (same total budget, ±30 %)")
    else:
        current, abs_bounds = constraint_bounds(roi_df, response_curves)
        free = optimise_hill_response_curves(
            {ch: response_curves[ch] for ch in current}, current_spend=current, channel_bounds=abs_bounds
        )
        free["method"] = "Response-curve optimiser (±30 % per channel)"
    fixed_res = None
    if upper and fixed_alloc_df is not None and len(fixed_alloc_df) > 0:
        fixed_res = _summary(
            _native_allocations(fixed_alloc_df, fixed=upper),
            f"Meridian BudgetOptimizer, {', '.join(channel_label(c) for c in upper)} held at current spend",
        )
    upper_share = lift_share(free, upper) if upper else 0.0
    conditional = bool(upper) and upper_share > UPPER_BOUND_LIFT_SHARE
    head = fixed_res if fixed_res is not None else free
    upper_names = ", ".join(channel_label(c) for c in upper)

    allocs_sorted = sorted(head["allocations"], key=lambda a: a["current_spend"])
    labels = [a["label"] for a in allocs_sorted]
    fig_opt = bar_figure(
        categories=labels,
        series=[
            {"name": "Current spend (€M)", "values": [round(a["current_spend"] / 1e6, 2) for a in allocs_sorted], "color": SLATE},
            {
                "name": "Optimised spend (€M)",
                "values": [round(a["optimised_spend"] / 1e6, 2) for a in allocs_sorted],
                "colors": [channel_color(lbl) for lbl in labels],
            },
        ],
        barmode="group",
        orientation="h",
        xaxis_title="Spend over the modelling window (€M)",
    )

    reallocation_vector = "Balanced allocation"
    if head["allocations"]:
        donor = min(head["allocations"], key=lambda a: a["spend_delta"])
        recipient = max(head["allocations"], key=lambda a: a["spend_delta"])
        if donor["spend_delta"] < 0 and recipient["spend_delta"] > 0 and donor["channel"] != recipient["channel"]:
            reallocation_vector = f"{donor['label']} → {recipient['label']}"

    def _sign(pct: float) -> str:
        return "+" if pct >= 0 else ""

    kpis = [
        KpiItem(label="Total media budget", value=fmt_eur(head["total_budget"]), delta="Unchanged: re-allocation only", status="neutral"),
        KpiItem(
            label="Incremental sales, optimised" + (f" ({upper_names} fixed)" if fixed_res else ""),
            value=fmt_eur(head["optimised_inc_sales"]),
            delta=f"{_sign(head['lift_pct'])}{head['lift_pct']:.1f}% ({fmt_eur_signed(head['lift_eur'])}) vs {fmt_eur(head['current_inc_sales'])} now",
            status="success" if head["lift_pct"] >= 0 and not (head is free and conditional) else "warn",
        ),
    ]
    if fixed_res is not None:
        kpis.append(
            KpiItem(
                label=f"Unconstrained, incl. {upper_names}",
                value=f"{_sign(free['lift_pct'])}{free['lift_pct']:.1f}%",
                delta=f"{100 * upper_share:.0f}% of this lift comes from {upper_names} (upper-bound ROI)",
                status="warn" if conditional else "neutral",
            )
        )
    kpis.append(KpiItem(label="Main re-allocation", value=reallocation_vector, delta=head["method"], status="neutral"))

    lift_rows = []
    for a in sorted(free["allocations"], key=lambda x: x["inc_sales_delta"], reverse=True):
        share = a["inc_sales_delta"] / free["lift_eur"] if abs(free["lift_eur"]) >= 1.0 else 0.0
        lift_rows.append(
            {
                "channel": a["label"] + (" (upper bound)" if a["channel"] in upper else ""),
                "delta_spend": f"{fmt_eur_signed(a['spend_delta'])} ({a['spend_delta_pct']:+.1f}%)",
                "delta_inc": fmt_eur_signed(a["inc_sales_delta"]),
                "share": f"{100 * share:.0f}%",
            }
        )

    callout_body = (
        f"{head['method']}: budget moves along each channel's response curve toward the highest marginal return. "
        "The model's ROIs are taken at face value, so an experiment should confirm a channel's lift before budget moves."
    )
    if upper:
        callout_body += (
            f" {upper_names} {'has' if len(upper) == 1 else 'have'} an upper-bound ROI (cost follows demand) and "
            f"deliver{'s' if len(upper) == 1 else ''} {100 * upper_share:.0f}% of the unconstrained lift"
            + (", so that headline is conditional on an unproven effect; the figures above keep them at current spend." if fixed_res else ".")
        )
    if native_error:
        callout_body += f"\n\nMeridian's BudgetOptimizer was not available for this run ({native_error})."
    callout_body += "\n\nMeridian's optimiser works on spend rounded to its own precision, so current spend can differ slightly from the ROI table."

    table_rows = [
        {
            "channel": a["label"],
            "cur_spend": fmt_eur(a["current_spend"]),
            "opt_spend": fmt_eur(a["optimised_spend"]),
            "delta_spend": f"{fmt_eur_signed(a['spend_delta'])} ({a['spend_delta_pct']:+.1f}%)",
            "cur_inc": fmt_eur(a["current_inc_sales"]),
            "opt_inc": fmt_eur(a["optimised_inc_sales"]),
            "opt_roi": f"{a['optimised_roi']:.2f}×",
        }
        for a in sorted(head["allocations"], key=lambda x: x["current_spend"], reverse=True)
    ]

    blocks: list[Any] = [
        KpisBlock(items=kpis),
        CalloutBlock(
            tone="warn" if conditional else "info",
            title="The optimiser takes the model's ROIs at face value",
            body=callout_body,
        ),
        ChartBlock(
            id="optimiser_allocation_chart",
            title="Same budget, re-allocated: current vs optimised spend by channel",
            subtitle=f"Total budget {fmt_eur(head['total_budget'])}; incremental sales {_sign(head['lift_pct'])}{head['lift_pct']:.1f}% ({head['method']}).",
            figure=fig_opt,
        ),
        TableBlock(
            id="optimiser_allocation_table",
            title="Budget optimisation by channel",
            subtitle="Current vs optimised spend and expected incremental sales over the modelling window",
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
        TableBlock(
            id="optimiser_lift_decomposition",
            title="Where the unconstrained lift comes from",
            subtitle=f"Change in incremental sales per channel, {free['method']}; shares above 100 % mean the other moves lose sales.",
            columns=[
                TableColumn(key="channel", label="Channel"),
                TableColumn(key="delta_spend", label="Spend change", align="right"),
                TableColumn(key="delta_inc", label="Δ incremental sales", align="right"),
                TableColumn(key="share", label="Share of lift", align="right"),
            ],
            rows=lift_rows,
        ),
    ]

    result = dict(head)
    result["unconstrained"] = {k: free[k] for k in ("lift_eur", "lift_pct", "method")}
    result["upper_bound_channels"] = upper
    result["upper_bound_lift_share"] = round(upper_share, 3)
    result["conditional"] = conditional
    sec = Section(
        id="optimiser",
        title="Budget optimiser",
        subtitle="Same total budget, re-allocated along the fitted response curves (notebook 03 §8).",
        blocks=blocks,
    )
    return sec, result
