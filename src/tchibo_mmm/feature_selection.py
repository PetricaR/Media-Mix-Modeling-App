"""Feature selection for Meridian's controls: which holiday and weather features earn a place?

Media channels are not selected: every paid channel needs an ROI for the budget question. Nor
are the structural controls of notebook 03 (weekday, holidays, Christmas, Black Week, yearly
Fourier, trend; Affiliate cost as a commission control). The candidates are the extra calendar,
Google-holiday and weather features. Two stages:

1. Screen with a fast proxy, notebook 04's OLS-2 (digital spend, catalog in-home adstock, the
   notebook-03 controls): greedy forward selection on walk-forward MAE / mean over 6-week folds
   that all end before the Q4-2024 hold-out, so the hold-out stays unseen.
2. Confirm with Meridian: the notebook-03 controls vs + the selected features vs + every
   candidate, with notebook 03's sampling and seed, scored once on the Q4-2024 hold-out. The
   weather is known only 16 days ahead, so the hold-out is also scored at plan time, with each
   weather feature at its seasonal normal.

    python -m tchibo_mmm.feature_selection             # screening only (seconds)
    python -m tchibo_mmm.feature_selection --meridian  # + Meridian fits (~3 min per control set)
"""

import argparse

import numpy as np
import pandas as pd
import statsmodels.api as sm

from tchibo_mmm import evaluate, features, holiday_features, weather_features
from tchibo_mmm.data import PAID_CHANNELS, PROJECT_ROOT, build_wide_table, load_raw, slug

OUTPUT_DIR = PROJECT_ROOT / "outputs"
HOLDOUT_START = pd.Timestamp("2024-10-01")  # same Q4-2024 hold-out as the MMM
FIRST_FOLD = pd.Timestamp("2024-01-15")  # as notebook 04's walk-forward
FOLD_DAYS = 42
# Notebook 03's final sampling.
FULL = {"n_chains": 4, "n_adapt": 1000, "n_burnin": 500, "n_keep": 1000, "n_prior": 500}

# The controls of the notebook-03 model (Affiliate cost joins them inside `mmm.roles`).
NOTEBOOK_CONTROLS = [
    "dow_tue",
    "dow_wed",
    "dow_thu",
    "dow_fri",
    "dow_sat",
    "dow_sun",
    "holiday",
    "christmas",
    "black_week",
    "sin_1",
    "cos_1",
    "sin_2",
    "cos_2",
    "trend",
]
# Candidate -> (group, known when a budget is planned weeks ahead). One feature per collinear
# cluster: rain mm stands for rain hours and the wet-day share; the temperature and sunshine
# anomalies stand for their raw values, which are mostly the season the Fourier terms carry.
# Google's public holidays and half-days are left out: they equal `holiday` and `half_day`.
CANDIDATES = {
    "half_day": ("calendar", True),
    "gcal_regional_share": ("google holidays", True),
    "gcal_eve": ("google holidays", True),
    "gcal_after": ("google holidays", True),
    "gcal_bridge_day": ("google holidays", True),
    "gcal_observance": ("google holidays", True),
    "gcal_advent": ("google holidays", True),
    "wx_temp_anomaly": ("weather", False),
    "wx_sunshine_anomaly": ("weather", False),
    "wx_precip_mm": ("weather", False),
    "wx_wind_max": ("weather", False),
    "wx_snow_cm": ("weather", False),
    "wx_hot_share": ("weather", False),
    "wx_frost_share": ("weather", False),
}


# --- inputs -----------------------------------------------------------------------------------


def candidate_features(index: pd.DatetimeIndex) -> pd.DataFrame:
    """Every candidate on `index`, from the cached Google holidays and Open-Meteo weather."""
    gcal = holiday_features.holiday_features(index, holiday_features.load_google_holidays())
    national = weather_features.national_weather(weather_features.load_weather())
    wx = weather_features.weather_features(index, national)
    half_day = ((index.month == 12) & index.day.isin([24, 31])).astype(float)
    out = gcal.join(wx).assign(half_day=half_day)[list(CANDIDATES)]
    if out.isna().any().any():
        raise ValueError(f"Candidates missing on {int(out.isna().any(axis=1).sum())} days")
    return out


def proxy_inputs(wide: pd.DataFrame) -> pd.DataFrame:
    """Notebook 04's OLS-2 inputs: digital spend, catalog in-home adstock, notebook-03 controls."""
    idx = wide.index
    digital = [f"cost_{slug(c)}" for c in PAID_CHANNELS if c != "Catalog"]
    shifted = features.shift_spend(wide["cost_catalog"], features.DEFAULT_CATALOG_SHIFT)
    adstock = np.convolve(shifted, features.geometric_decay_weights(0.8, 21))[: len(idx)]
    controls = features.calendar_controls(idx).join(features.seasonality_controls(idx))
    return wide[digital].assign(catalog_inhome_adstock=adstock).join(controls[NOTEBOOK_CONTROLS])


# --- screening --------------------------------------------------------------------------------


def fold_starts(first: pd.Timestamp = FIRST_FOLD, end: pd.Timestamp = HOLDOUT_START):
    """Starts of the 6-week walk-forward folds that end before `end`."""
    return pd.date_range(first, end - pd.Timedelta(days=FOLD_DAYS), freq=f"{FOLD_DAYS}D")


def fold_errors(y: pd.Series, X: pd.DataFrame, starts: pd.DatetimeIndex) -> np.ndarray:
    """MAE / mean per fold; each fold is fitted on every day before it (expanding window)."""
    Xc = sm.add_constant(X, has_constant="add")
    errors = []
    for s in starts:
        train = y.index < s
        test = (y.index >= s) & (y.index < s + pd.Timedelta(days=FOLD_DAYS))
        pred = sm.OLS(y[train], Xc[train]).fit().predict(Xc[test])
        errors.append(evaluate.metrics(y[test], pred)["MAE/mean"])
    return np.array(errors)


def forward_selection(
    y: pd.Series,
    base: pd.DataFrame,
    cands: pd.DataFrame,
    starts: pd.DatetimeIndex,
    min_gain: float = 0.001,
) -> tuple[list[str], pd.DataFrame, pd.DataFrame]:
    """Greedy forward selection on walk-forward error.

    Each step tries every remaining candidate; the best one joins only if it cuts the mean fold
    error by at least `min_gain` and lowers it in most folds. Returns the chosen features, one
    row per step, and one row per candidate added alone to `base` (the first step).
    """
    current = fold_errors(y, base, starts)
    chosen, alone = [], None
    steps = [{"added": "notebook-03 controls", "cv_mae_over_mean": current.mean()}]
    while len(chosen) < cands.shape[1]:
        trials = {
            c: fold_errors(y, base.join(cands[[*chosen, c]]), starts)
            for c in cands
            if c not in chosen
        }
        rows = pd.DataFrame(
            {
                "cv_mae_over_mean": {c: e.mean() for c, e in trials.items()},
                "gain": {c: current.mean() - e.mean() for c, e in trials.items()},
                "folds_improved": {c: int((e < current).sum()) for c, e in trials.items()},
            }
        )
        alone = rows if alone is None else alone
        best = rows["cv_mae_over_mean"].idxmin()
        if rows.loc[best, "gain"] < min_gain or rows.loc[best, "folds_improved"] <= len(starts) / 2:
            break
        chosen.append(best)
        current = trials[best]
        steps.append({"added": best, **rows.loc[best].to_dict()})
    return chosen, pd.DataFrame(steps).rename_axis("step"), alone


# --- Meridian check ---------------------------------------------------------------------------


def plan_time_controls(table: pd.DataFrame, cols: list[str], start: pd.Timestamp) -> pd.DataFrame:
    """`table` with each of `cols` replaced from `start` on by its seasonal normal (yearly
    Fourier fit on the days before `start`): all a planner knows weeks ahead."""
    out = table.copy()
    idx = pd.DatetimeIndex(table["time"])
    season = sm.add_constant(features.seasonality_controls(idx, 3).drop(columns="trend"))
    season.index = table.index
    train = idx < start
    for col in cols:
        normal = sm.OLS(table.loc[train, col], season[train]).fit()
        out.loc[~train, col] = normal.predict(season[~train])
    return out


def meridian_check(
    wide: pd.DataFrame, cands: pd.DataFrame, specs: dict[str, list[str]], sampling: dict = FULL
) -> pd.DataFrame:
    """One Meridian fit per control set (notebook 03's config, sampling and seed).

    Scores the Q4-2024 hold-out with the actual weather and at plan time, and reports the max
    R-hat and each channel's ROI, which a control should not move without a reason.
    """
    from meridian.analysis import analyzer
    from meridian.analysis.tensors import DataTensors

    from tchibo_mmm import mmm as M  # TensorFlow + JAX: import only when fitting

    cfg = M.MMMConfig()
    table = features.build_meridian_table(wide, catalog_shift=cfg.catalog_shift)
    extra = [c for c in features.control_columns(table) if c not in NOTEBOOK_CONTROLS]
    table = table.drop(columns=extra)
    start = pd.Timestamp(cfg.holdout[0])
    hold = pd.DatetimeIndex(table["time"]) >= start
    actual = table.loc[hold, "total_sales"]
    rows = []
    for name, cols in specs.items():
        t = table.assign(**{c: cands[c].to_numpy() for c in cols})
        mmm = M.fit(M.build_model(M.build_input_data(t, cfg), cfg), **sampling)
        an = analyzer.Analyzer(mmm)
        expected = M.draws(an.expected_outcome(aggregate_times=False)).mean(axis=0)
        weather = [c for c in cols if not CANDIDATES[c][1]]
        order = [str(c) for c in mmm.input_data.controls.coords["control_variable"].values]
        planned = plan_time_controls(t, weather, start)[order].to_numpy(dtype=float)[np.newaxis]
        plan = M.draws(
            an.expected_outcome(new_data=DataTensors(controls=planned), aggregate_times=False)
        ).mean(axis=0)
        held, held_plan = evaluate.metrics(actual, expected[hold]), evaluate.metrics(actual, plan[hold])
        roi = M.roi_table(mmm)["roi"].drop(index="All Channels")
        rows.append(
            {
                "spec": name,
                "extra_controls": len(cols),
                "holdout_mae_over_mean": held["MAE/mean"],
                "holdout_plan_time_mae_over_mean": held_plan["MAE/mean"],
                "holdout_r2": held["R2"],
                "holdout_bias": held["bias"],
                "max_rhat": float(an.rhat_summary()["max_r_hat"].max()),
                **{f"roi_{c}": r for c, r in roi.items()},
            }
        )
    return pd.DataFrame(rows).set_index("spec")


# --- CLI --------------------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Select Meridian controls among the holiday and weather candidates."
    )
    parser.add_argument(
        "--meridian", action="store_true", help="also fit Meridian per control set (~3 min each)"
    )
    parser.add_argument(
        "--min-gain", type=float, default=0.001, help="min cut in mean fold MAE / mean to join"
    )
    args = parser.parse_args()

    wide = build_wide_table(load_raw())
    y = wide["total_sales"]
    cands = candidate_features(wide.index)
    starts = fold_starts()
    chosen, steps, alone = forward_selection(y, proxy_inputs(wide), cands, starts, args.min_gain)
    meta = pd.DataFrame(CANDIDATES, index=["group", "known_ahead"]).T
    candidates = meta.join(alone).assign(selected=lambda d: d.index.isin(chosen))
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    candidates.to_csv(OUTPUT_DIR / "feature_selection_candidates.csv")
    steps.to_csv(OUTPUT_DIR / "feature_selection_steps.csv")

    print(
        f"{len(CANDIDATES)} candidates, {len(starts)} walk-forward folds of {FOLD_DAYS} days "
        f"({starts[0].date()} .. {(starts[-1] + pd.Timedelta(days=FOLD_DAYS - 1)).date()})"
    )
    with pd.option_context("display.width", 200, "display.float_format", "{:,.4f}".format):
        print("\nEach candidate added alone (OLS-2 proxy)\n", candidates)
        print("\nForward selection\n", steps)

    if args.meridian:
        weather = [c for c, (group, _) in CANDIDATES.items() if group == "weather"]
        specs = {
            "notebook-03 controls": [],
            "+ selected": chosen,
            "+ all weather": weather,
            "+ all candidates": list(cands),
        }
        if not chosen:
            del specs["+ selected"]
        check = meridian_check(wide, cands, specs)
        check.to_csv(OUTPUT_DIR / "feature_selection_meridian.csv")
        with pd.option_context("display.width", 250, "display.float_format", "{:,.4f}".format):
            print("\nMeridian on the Q4-2024 hold-out\n", check.T)


if __name__ == "__main__":
    main()
