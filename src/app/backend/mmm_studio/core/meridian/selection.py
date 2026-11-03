"""Forward hold-out validation, model-selection grid and prior/endogeneity sensitivity (notebook 03 §1 and §5).

Every fit here is trained only on days before the window it is scored on. The selection grid and the sensitivity fits
are scored on a validation window that ends the day before the reported hold-out, so choosing a specification never
looks at the hold-out.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
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
    """Refit on data up to the end of the forward window, holding the window out (notebook 03 §5).

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


def _roi_and_share(mmm: Any) -> tuple[pd.Series, float]:
    from mmm_studio.core.meridian.analysis import extract_roi_table

    rt = extract_roi_table(mmm)
    share = float(rt.loc["All Channels", "pct_of_contribution"]) if "All Channels" in rt.index else float("nan")
    return rt.drop(index="All Channels", errors="ignore")["roi"], share


def key_channels(cfg: MeridianSpecConfig) -> list[str]:
    """Channels whose ROI the selection tables show: an upper-bound (or the first) media channel, a print event and a
    commission channel."""
    picks = [*(cfg.upper_bound_slugs[:1] or cfg.media_slugs[:1]), *cfg.print_slugs[:1], *cfg.commission_slugs[:1]]
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
) -> dict[str, Any]:
    """Fast fits of notebook 03 on the validation window: print-event shift k × commission channels as media/control,
    then prior and demand-following-channel sensitivity."""
    work_dir.mkdir(parents=True, exist_ok=True)
    hist, val_cfg = window_config(wide_df, base_cfg, validation_window)
    commission = tuple(base_cfg.commission_slugs)
    selection: list[dict[str, Any]] = []

    def _fit(cfg: MeridianSpecConfig, tag: str) -> Any:
        if on_progress:
            on_progress(tag)
        return fit_meridian_isolated(
            hist,
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
            sc = holdout_scores(mmm)
            roi, _ = _roi_and_share(mmm)
            selection.append(
                {
                    "catalog_shift": k,
                    "affiliate": role,
                    "test_mae_over_mean": float(sc.loc["Test", "wMAPE"]),
                    "train_mae_over_mean": float(sc.loc["Train", "wMAPE"]),
                    "test_r2": float(sc.loc["Test", "R_Squared"]),
                    "roi": {str(c): float(v) for c, v in roi.items()},
                }
            )

    # Sensitivity: informative priors centred on attributed ROAS, and the demand-following channels as controls
    final_name = "run's own prior (final)" if base_cfg.roi_prior else "default prior (final)"
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
        sc = holdout_scores(mmm)
        roi, share = _roi_and_share(mmm)
        sensitivity.append(
            {
                "variant": name,
                "test_mae_over_mean": float(sc.loc["Test", "wMAPE"]),
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
        "key_channels": key_channels(base_cfg),
        "commission_channels": list(commission),
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
            "affiliate": r["affiliate"] + (" (final)" if r["affiliate"] == "control" and r["catalog_shift"] == final_shift else ""),
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
