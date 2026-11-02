"""Forward hold-out validation, model-selection grid and prior/endogeneity sensitivity (notebook 03 §1 and §5)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from mmm_studio.core.meridian.fit import fit_meridian_isolated
from mmm_studio.core.meridian.spec import CHANNEL_DISPLAY_NAMES, MeridianSpecConfig
from mmm_studio.core.metrics import regression_metrics


def holdout_scores(mmm: Any) -> pd.DataFrame:
    """Meridian's `predictive_accuracy` pivoted to `evaluation_set × metric` (wMAPE = MAE / mean)."""
    from meridian.analysis import analyzer

    acc = analyzer.Analyzer(mmm).predictive_accuracy().to_dataframe().reset_index()
    if "geo_granularity" in acc:
        acc = acc[acc.geo_granularity == "national"]
    return acc.pivot(index="evaluation_set", columns="metric", values="value")


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
) -> tuple[dict[str, float], pd.DataFrame]:
    """Refit on data up to the end of the forward window, holding the window out (notebook 03 §5).

    Returns `(metrics, holdout_fit_table)`; nothing after the window is used, so the scores are a genuine forecast.
    """
    from mmm_studio.core.meridian.analysis import extract_fit_table

    _, test_start, test_end = window
    cfg_fwd = replace(cfg, holdout=(test_start.strftime("%Y-%m-%d"), test_end.strftime("%Y-%m-%d")))
    hist = wide_df.loc[:test_end]
    mmm = fit_meridian_isolated(
        hist,
        cfg_fwd,
        work_path,
        sampling=sampling,
        seed=seed,
        spawn_child=spawn_child,
        should_cancel=should_cancel,
        on_heartbeat=on_heartbeat,
    )
    fit = extract_fit_table(mmm, cfg_fwd)
    test = fit[fit["holdout"]]
    return regression_metrics(test["actual"], test["expected"]), test


def _roi_and_share(mmm: Any) -> tuple[pd.Series, float]:
    from mmm_studio.core.meridian.analysis import extract_roi_table

    rt = extract_roi_table(mmm)
    share = float(rt.loc["All Channels", "pct_of_contribution"]) if "All Channels" in rt.index else float("nan")
    return rt.drop(index="All Channels", errors="ignore")["roi"], share


def run_model_selection(
    wide_df: pd.DataFrame,
    base_cfg: MeridianSpecConfig,
    work_dir: Path,
    sampling: dict[str, int],
    seed: int = 0,
    spawn_child: bool = False,
    shifts: tuple[int, ...] = (1, 2, 3),
    should_cancel: Callable[[], bool] | None = None,
    on_heartbeat: Callable[[], None] | None = None,
    on_progress: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Fast fits of notebook 03: catalog shift k × Affiliate as media/control, then three prior/endogeneity variants."""
    work_dir.mkdir(parents=True, exist_ok=True)
    commission = [c for c in base_cfg.as_controls]
    selection: list[dict[str, Any]] = []

    def _fit(cfg: MeridianSpecConfig, tag: str) -> Any:
        if on_progress:
            on_progress(tag)
        return fit_meridian_isolated(
            wide_df,
            cfg,
            work_dir / f"{tag}.binpb",
            sampling=sampling,
            seed=seed,
            spawn_child=spawn_child,
            should_cancel=should_cancel,
            on_heartbeat=on_heartbeat,
        )

    for k in shifts:
        for role in ("media", "control"):
            cfg = replace(base_cfg, catalog_shift=k, as_controls=tuple(commission) if role == "control" else ())
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

    # Sensitivity: informative priors centred on attributed ROAS, and SEA as a control.
    roas: dict[str, float] = {}
    for slug in base_cfg.paid_slugs:
        cost, sales = f"cost_{slug}", f"sales_{slug}"
        if cost in wide_df and sales in wide_df and float(wide_df[cost].sum()) > 0:
            roas[slug] = float(wide_df[sales].sum()) / float(wide_df[cost].sum())
    variants: dict[str, MeridianSpecConfig] = {"default prior (final)": base_cfg}
    if roas:
        variants["attribution prior σ = 0.5"] = replace(
            base_cfg, roi_prior={c: (float(np.log(r)), 0.5) for c, r in roas.items() if r > 0}
        )
    if "sea" in base_cfg.paid_slugs:
        variants["SEA as control"] = replace(base_cfg, as_controls=tuple(dict.fromkeys([*commission, "sea"])))
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
    return {"selection": selection, "sensitivity": sensitivity, "sampling": sampling}


def selection_table_rows(result: dict[str, Any], final_shift: int) -> list[dict[str, Any]]:
    """Rows for the selection-grid table; the final configuration is marked."""
    rows = []
    for r in result.get("selection", []):
        roi = r["roi"]
        rows.append(
            {
                "catalog_shift": r["catalog_shift"],
                "affiliate": r["affiliate"] + (" (final)" if r["affiliate"] == "control" and r["catalog_shift"] == final_shift else ""),
                "test_mae_pct": f"{100 * r['test_mae_over_mean']:.1f}%",
                "train_mae_pct": f"{100 * r['train_mae_over_mean']:.1f}%",
                "test_r2": f"{r['test_r2']:.2f}",
                "roi_sea": f"{roi['sea']:.2f}" if "sea" in roi else "–",
                "roi_catalog": f"{roi['catalog']:.2f}" if "catalog" in roi else "–",
                "roi_affiliate": f"{roi['affiliate']:.2f}" if "affiliate" in roi else "– (control)",
            }
        )
    return rows


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


def channel_label(slug: str) -> str:
    return CHANNEL_DISPLAY_NAMES.get(slug, slug)
