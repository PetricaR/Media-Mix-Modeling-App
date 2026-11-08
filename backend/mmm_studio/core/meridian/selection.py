"""Forward hold-out validation, model-selection grid and prior/endogeneity sensitivity.

Every fit here is trained only on days before the window it is scored on. The selection grid and the sensitivity fits
are scored on a validation window that ends the day before the reported hold-out, so choosing a specification never
looks at the hold-out. With leak-free validation (`core/as_of.py`) a scored window is also forecast as a plan made the
day before it (`forecast_window`): its sales are not in the model's input at all (not even in Meridian's KPI scaling),
and the realised costs of channels that follow the outcome, the user's controls and the weather of the window are
replaced by what was knowable then.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import pandas as pd

from mmm_studio.core.meridian.fit import fit_meridian_isolated
from mmm_studio.core.meridian.spec import MeridianSpecConfig, channel_label
from mmm_studio.core.metrics import regression_metrics


def holdout_scores(mmm: Any) -> pd.DataFrame:
    """Meridian's `predictive_accuracy` pivoted to `evaluation_set × metric` (wMAPE = MAE / mean)."""
    from meridian.analysis import analyzer

    acc = analyzer.Analyzer(mmm).predictive_accuracy().to_dataframe().reset_index()
    if "geo_granularity" in acc:
        acc = acc[acc.geo_granularity == "national"]
    return acc.pivot(index="evaluation_set", columns="metric", values="value")


def window_config(
    wide_df: pd.DataFrame,
    cfg: MeridianSpecConfig,
    window: tuple[pd.Timestamp, pd.Timestamp, pd.Timestamp],
) -> tuple[pd.DataFrame, MeridianSpecConfig]:
    """The panel up to the end of `window` and `cfg` with `window` as its hold-out (priors from the days before it)."""
    _, test_start, test_end = window
    hist = wide_df.loc[:test_end]
    cfg_w = replace(cfg, holdout=(test_start.strftime("%Y-%m-%d"), test_end.strftime("%Y-%m-%d")))
    return hist, cfg_w.with_training_priors(hist)


def fit_forward_holdout(
    wide_df: pd.DataFrame,
    cfg: MeridianSpecConfig,
    window: tuple[pd.Timestamp, pd.Timestamp, pd.Timestamp],
    work_path: Path,
    sampling: dict[str, int],
    seed: int = 0,
    spawn_child: bool = False,
    should_cancel: Callable[[], bool] | None = None,
    on_heartbeat: Callable[[], None] | None = None,
    timeout_seconds: int | None = None,
) -> tuple[dict[str, float], pd.DataFrame]:
    """Refit on data up to the end of the forward window, holding the window out.

    Returns `(metrics, holdout_fit_table)`; nothing after the window is used, so the scores are a genuine forecast.
    """
    from mmm_studio.core.meridian.analysis import extract_fit_table

    hist, cfg_fwd = window_config(wide_df, cfg, window)
    mmm = fit_meridian_isolated(
        hist,
        cfg_fwd,
        work_path,
        sampling=sampling,
        seed=seed,
        spawn_child=spawn_child,
        should_cancel=should_cancel,
        on_heartbeat=on_heartbeat,
        **({"timeout_seconds": timeout_seconds} if timeout_seconds else {}),
    )
    fit = extract_fit_table(mmm, cfg_fwd)
    test = fit[fit["holdout"]]
    return regression_metrics(test["actual"], test["expected"]), test


@dataclass
class WindowScore:
    """A past window forecast as a plan made the day before it (`forecast_window`), scored on its actual sales."""

    # forecast (posterior mean), credible_lo/hi, prediction_lo/hi, planned_spend and actual, one row per window day
    daily: pd.DataFrame
    metrics: dict[str, float]
    total_pi: tuple[float, float]
    ar1: float
    cfg: MeridianSpecConfig
    mmm: Any

    def holdout_table(self) -> pd.DataFrame:
        """The window as a fit table (`actual`, `expected`, `expected_lo`, `expected_hi`, `prediction_lo/hi`)."""
        d = self.daily
        return pd.DataFrame(
            {
                "actual": d["actual"],
                "expected": d["forecast"],
                "expected_lo": d["credible_lo"],
                "expected_hi": d["credible_hi"],
                "prediction_lo": d["prediction_lo"],
                "prediction_hi": d["prediction_hi"],
            },
            index=d.index,
        )


def forecast_window(
    wide_df: pd.DataFrame,
    cfg: MeridianSpecConfig,
    window: tuple[pd.Timestamp, pd.Timestamp, pd.Timestamp],
    sampling: dict[str, int],
    seed: int = 0,
    *,
    outcome_driven: tuple[str, ...] = (),
    level_sd: float = 0.0,
    spawn_child: bool = False,
    should_cancel: Callable[[], bool] | None = None,
    on_heartbeat: Callable[[], None] | None = None,
    timeout_seconds: int | None = None,
) -> WindowScore:
    """Forecast `window = (train_end, start, end)` exactly as a budget plan made on `train_end`, and score it.

    The model is refit on the days up to `train_end` (priors from those days) with the window appended as a plan
    (`as_of.window_plan`): the window's actual spend, except the `outcome_driven` channels, planned from history. Its
    sales are the training mean in the input, so nothing from the window reaches the fit; they are only used to score.
    """
    from mmm_studio.core.as_of import window_plan
    from mmm_studio.core.meridian.forecast import forecast_budget_plan

    plan_cols = [c for c in cfg.paid_slugs if f"cost_{c}" in wide_df.columns]
    history, plan = window_plan(wide_df, window, plan_cols, outcome_driven)
    fc = forecast_budget_plan(
        history,
        plan,
        cfg=cfg,
        sampling=sampling,
        seed=seed,
        level_sd=level_sd,
        spawn_child=spawn_child,
        should_cancel=should_cancel,
        on_heartbeat=on_heartbeat,
        timeout_seconds=timeout_seconds,
    )
    daily = fc.daily.copy()
    daily["actual"] = wide_df.loc[window[1] : window[2], "total_sales"].to_numpy()
    return WindowScore(
        daily=daily,
        metrics=regression_metrics(daily["actual"].to_numpy(), daily["forecast"].to_numpy()),
        total_pi=(fc.total_lo, fc.total_hi),
        ar1=fc.ar1,
        cfg=fc.cfg,
        mmm=fc.mmm,
    )


def _roi_and_share(mmm: Any) -> tuple[pd.Series, float]:
    from mmm_studio.core.meridian.analysis import extract_roi_table

    rt = extract_roi_table(mmm)
    share = float(rt.loc["All Channels", "pct_of_contribution"]) if "All Channels" in rt.index else float("nan")
    return rt.drop(index="All Channels", errors="ignore")["roi"], share


def key_channels(cfg: MeridianSpecConfig, wide: pd.DataFrame | None = None) -> list[str]:
    """Channels whose ROI the selection tables show: the largest upper-bound (or largest) media channel by spend, a print
    event and a commission channel."""

    def spend(slug: str) -> float:
        col = f"cost_{slug}"
        return float(wide[col].sum()) if wide is not None and col in wide else 0.0

    lead = sorted(cfg.upper_bound_slugs, key=spend, reverse=True)[:1] or sorted(
        (s for s in cfg.media_slugs if s not in cfg.print_slugs), key=spend, reverse=True
    )[:1]
    picks = [*lead, *cfg.print_slugs[:1], *cfg.commission_slugs[:1]]
    return list(dict.fromkeys(picks))


def run_model_selection(
    wide_df: pd.DataFrame,
    base_cfg: MeridianSpecConfig,
    work_dir: Path,
    sampling: dict[str, int],
    validation_window: tuple[pd.Timestamp, pd.Timestamp, pd.Timestamp],
    validation_label: str,
    seed: int = 0,
    spawn_child: bool = False,
    shifts: tuple[int, ...] = (1, 2, 3),
    should_cancel: Callable[[], bool] | None = None,
    on_heartbeat: Callable[[], None] | None = None,
    on_progress: Callable[[str], None] | None = None,
    timeout_seconds: int | None = None,
    leak_free: bool = False,
) -> dict[str, Any]:
    """Fast fits on the validation window: print-event shift k × commission channels as media/control,
    then prior and demand-following-channel sensitivity.

    `leak_free`: every variant sees the validation window as a plan made the day before it (`as_of.as_of_panel`; the
    base spec's commission and demand-following costs planned from history in every variant, so a variant that makes a
    commission channel media is not handed its realised cost) and is scored on the actual sales outside the model.
    """
    from mmm_studio.core.as_of import as_of_panel
    from mmm_studio.core.meridian.analysis import extract_fit_table

    work_dir.mkdir(parents=True, exist_ok=True)
    hist, val_cfg = window_config(wide_df, base_cfg, validation_window)
    commission = tuple(base_cfg.commission_slugs)
    outcome_driven = base_cfg.outcome_driven_slugs if leak_free else ()
    _, val_start, val_end = validation_window
    plan_cols = [c for c in base_cfg.paid_slugs if f"cost_{c}" in wide_df.columns]
    data = as_of_panel(wide_df, validation_window, plan_cols, outcome_driven) if leak_free else hist
    val_actual = wide_df.loc[val_start:val_end, "total_sales"]
    selection: list[dict[str, Any]] = []

    def _scores(mmm: Any, cfg: MeridianSpecConfig) -> tuple[float, float, float]:
        """`(validation MAE / mean, train MAE / mean, validation R²)`."""
        if not leak_free:
            sc = holdout_scores(mmm)
            return float(sc.loc["Test", "wMAPE"]), float(sc.loc["Train", "wMAPE"]), float(sc.loc["Test", "R_Squared"])
        fit = extract_fit_table(mmm, cfg)
        train = fit[~fit["holdout"]]
        val = regression_metrics(val_actual.to_numpy(), fit["expected"].reindex(val_actual.index).to_numpy())
        tr = regression_metrics(train["actual"].to_numpy(), train["expected"].to_numpy())
        return val["MAE_over_mean"], tr["MAE_over_mean"], val["R2"]

    def _fit(cfg: MeridianSpecConfig, tag: str) -> Any:
        if on_progress:
            on_progress(tag)
        return fit_meridian_isolated(
            data,
            cfg,
            work_dir / f"{tag}.binpb",
            sampling=sampling,
            seed=seed,
            spawn_child=spawn_child,
            should_cancel=should_cancel,
            on_heartbeat=on_heartbeat,
            **({"timeout_seconds": timeout_seconds} if timeout_seconds else {}),
        )

    roles = ("media", "control") if commission else ("control",)
    for k in shifts:
        for role in roles:
            as_controls = val_cfg.as_controls if role == "control" else tuple(c for c in val_cfg.as_controls if c not in commission)
            cfg = replace(
                val_cfg,
                catalog_shift=k,
                print_shifts={s: k for s in val_cfg.print_slugs},
                as_controls=as_controls,
            ).with_training_priors(hist)
            mmm = _fit(cfg, f"sel_k{k}_{role}")
            val_mae, train_mae, val_r2 = _scores(mmm, cfg)
            roi, _ = _roi_and_share(mmm)
            selection.append(
                {
                    "catalog_shift": k,
                    "affiliate": role,
                    "test_mae_over_mean": val_mae,
                    "train_mae_over_mean": train_mae,
                    "test_r2": val_r2,
                    "roi": {str(c): float(v) for c, v in roi.items()},
                }
            )

    # Sensitivity: informative priors centred on attributed ROAS, and the demand-following channels as controls
    final_name = "run's own prior (final spec)" if base_cfg.roi_prior else "default prior (final spec)"
    variants: dict[str, MeridianSpecConfig] = {final_name: val_cfg}
    if base_cfg.media_prior_type == "roi":
        roas_cfg = replace(val_cfg, roi_prior_mode="attributed_roas", roas_prior_sigma=0.5).with_training_priors(hist)
        if roas_cfg.roi_prior:
            variants["attribution prior σ = 0.5"] = roas_cfg
    upper = [c for c in base_cfg.upper_bound_slugs if c in base_cfg.media_slugs]
    if upper and len(upper) < len(base_cfg.media_slugs):
        names = ", ".join(channel_label(c) for c in upper)
        variants[f"{names} as control"] = replace(
            val_cfg,
            as_controls=tuple(dict.fromkeys([*val_cfg.as_controls, *upper])),
            upper_bound_slugs=(),
        ).with_training_priors(hist)
    sensitivity: list[dict[str, Any]] = []
    for i, (name, cfg) in enumerate(variants.items()):
        mmm = _fit(cfg, f"sens_{i}")
        val_mae, _, _ = _scores(mmm, cfg)
        roi, share = _roi_and_share(mmm)
        sensitivity.append(
            {
                "variant": name,
                "test_mae_over_mean": val_mae,
                "roi": {str(c): float(v) for c, v in roi.items()},
                "media_share": share / 100.0,
            }
        )
    return {
        "selection": selection,
        "sensitivity": sensitivity,
        "sampling": sampling,
        "validation_label": validation_label,
        "validation_window": [validation_window[1].strftime("%Y-%m-%d"), validation_window[2].strftime("%Y-%m-%d")],
        "key_channels": key_channels(base_cfg, wide_df),
        "commission_channels": list(commission),
        "leak_free": leak_free,
        "estimated_costs": list(outcome_driven),
    }


def selection_table_rows(result: dict[str, Any], final_shift: int) -> tuple[list[dict[str, Any]], list[str]]:
    """Rows and ROI channel order of the selection-grid table; the final configuration is marked."""
    channels = list(result.get("key_channels") or ["sea", "catalog", "affiliate"])
    commission = set(result.get("commission_channels") or ["affiliate"])
    rows = []
    for r in result.get("selection", []):
        roi = r["roi"]
        row: dict[str, Any] = {
            "catalog_shift": r["catalog_shift"],
            "affiliate": r["affiliate"] + (" (final spec)" if r["affiliate"] == "control" and r["catalog_shift"] == final_shift else ""),
            "test_mae_pct": f"{100 * r['test_mae_over_mean']:.1f}%",
            "train_mae_pct": f"{100 * r['train_mae_over_mean']:.1f}%",
            "test_r2": f"{r['test_r2']:.2f}",
        }
        for c in channels:
            row[f"roi_{c}"] = f"{roi[c]:.2f}" if c in roi else ("– (control)" if c in commission else "–")
        rows.append(row)
    return rows, channels


def sensitivity_table_rows(result: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    """Rows and ROI channel order for the prior / endogeneity sensitivity table."""
    channels: list[str] = []
    for r in result.get("sensitivity", []):
        for c in r["roi"]:
            if c not in channels:
                channels.append(c)
    rows = []
    for r in result.get("sensitivity", []):
        row: dict[str, Any] = {
            "variant": r["variant"],
            "test_mae_pct": f"{100 * r['test_mae_over_mean']:.1f}%",
            "media_share": f"{100 * r['media_share']:.0f}%" if r["media_share"] == r["media_share"] else "–",
        }
        for c in channels:
            row[f"roi_{c}"] = f"{r['roi'][c]:.2f}" if c in r["roi"] else "– (control)"
        rows.append(row)
    return rows, channels
