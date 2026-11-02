"""Meridian MMM: input data, model spec, fitting, prediction tables, persistence.

The model is a Bayesian regression of daily total sales on adstocked and
Hill-saturated channel spend plus calendar / seasonality / trend controls.
"""

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd
from meridian import backend, constants
from meridian.analysis import analyzer
from meridian.data import data_frame_input_data_builder as dfb
from meridian.model import model, prior_distribution, spec
from meridian.schema.serde import meridian_serde

from tchibo_mmm import features
from tchibo_mmm.data import PROJECT_ROOT

OUTPUT_DIR = PROJECT_ROOT / "outputs"
HOLDOUT_Q4_2024 = ("2024-10-01", "2025-01-01")


@dataclass
class MMMConfig:
    catalog_shift: int = features.DEFAULT_CATALOG_SHIFT
    # Channels entered as controls instead of media: they inform the forecast but get
    # no ROI. Affiliate cost is a commission on sales, i.e. it follows demand.
    as_controls: tuple[str, ...] = ("affiliate",)
    max_lag: int = 21
    adstock_decay: dict[str, str] = field(default_factory=lambda: {"catalog": "binomial"})
    holdout: tuple[str, str] | None = HOLDOUT_Q4_2024
    # ROI prior per channel as (mu, sigma) of a LogNormal; None = Meridian default LogNormal(0.2, 0.9).
    roi_prior: dict[str, tuple[float, float]] | None = None


def roles(table: pd.DataFrame, cfg: MMMConfig) -> tuple[list[str], list[str], list[str]]:
    """(spend columns, media channel names, control columns) for this config."""
    moved = [f"{c}_spend" for c in cfg.as_controls]
    spend = [s for s in features.spend_columns() if s not in moved]
    channels = [c for c in features.media_channels() if c not in cfg.as_controls]
    return spend, channels, features.control_columns(table) + moved


def build_input_data(table: pd.DataFrame, cfg: MMMConfig):
    spend, channels, controls = roles(table, cfg)
    return (
        dfb.DataFrameInputDataBuilder(kpi_type="revenue", default_kpi_column="total_sales")
        .with_kpi(table)
        .with_controls(table, control_cols=controls)
        # Spend is the media variable: the forecast has to run on a budget plan.
        .with_media(table, media_cols=spend, media_spend_cols=spend, media_channels=channels)
        .build()
    )


def build_model(input_data, cfg: MMMConfig) -> model.Meridian:
    channels = list(input_data.media_channel.values)
    kwargs = {}
    if cfg.roi_prior:
        default = (0.2, 0.9)
        # Meridian's JAX backend expects float64 parameters.
        mu = np.array([cfg.roi_prior.get(c, default)[0] for c in channels], dtype=np.float64)
        sigma = np.array([cfg.roi_prior.get(c, default)[1] for c in channels], dtype=np.float64)
        kwargs["prior"] = prior_distribution.PriorDistribution(
            roi_m=backend.tfd.LogNormal(mu, sigma, name=constants.ROI_M)
        )
    holdout = None
    if cfg.holdout:
        start, end = cfg.holdout
        holdout = spec.HoldoutSpec(spec=[spec.DateRange(start_date=start, end_date=end)])
    model_spec = spec.ModelSpec(
        max_lag=cfg.max_lag,
        adstock_decay_spec=cfg.adstock_decay,
        holdout=holdout,
        **kwargs,
    )
    return model.Meridian(input_data=input_data, model_spec=model_spec)


def fit(
    mmm: model.Meridian,
    n_chains: int = 4,
    n_adapt: int = 1000,
    n_burnin: int = 500,
    n_keep: int = 1000,
    n_prior: int = 500,
    seed: int = 0,
) -> model.Meridian:
    mmm.sample_prior(n_prior, seed=seed)
    mmm.sample_posterior(
        n_chains=n_chains, n_adapt=n_adapt, n_burnin=n_burnin, n_keep=n_keep, seed=seed
    )
    return mmm


def fit_table(mmm: model.Meridian, cfg: MMMConfig) -> pd.DataFrame:
    """Daily actual vs posterior expected sales (mean and 90 % interval), with a hold-out flag."""
    ds = analyzer.Analyzer(mmm).expected_vs_actual_data(aggregate_geos=True)
    df = ds.to_dataframe().reset_index()
    wide = df.pivot(index="time", columns="metric", values="expected")
    out = pd.DataFrame(
        {
            "actual": df.groupby("time")["actual"].first(),
            "expected": wide["mean"],
            "expected_lo": wide["ci_lo"],
            "expected_hi": wide["ci_hi"],
            "baseline": df[df.metric == "mean"].set_index("time")["baseline"],
        }
    )
    out.index = pd.to_datetime(out.index)
    out["holdout"] = False
    if cfg.holdout:
        out.loc[cfg.holdout[0] : cfg.holdout[1], "holdout"] = True
    return out


def roi_table(mmm: model.Meridian) -> pd.DataFrame:
    """Per channel: spend, incremental sales, contribution, ROI and marginal ROI.

    Posterior mean plus 90 % credible interval (`_lo` / `_hi` columns). Percentages
    are in percent, as Meridian reports them.
    """
    ds = analyzer.Analyzer(mmm).summary_metrics()
    cols = {}
    for var in [
        "spend",
        "pct_of_spend",
        "incremental_outcome",
        "pct_of_contribution",
        "roi",
        "mroi",
    ]:
        da = ds[var]
        if "distribution" in da.dims:
            da = da.sel(distribution=constants.POSTERIOR)
        if "metric" in da.dims:
            cols[var] = da.sel(metric="mean").to_series()
            cols[f"{var}_lo"] = da.sel(metric="ci_lo").to_series()
            cols[f"{var}_hi"] = da.sel(metric="ci_hi").to_series()
        else:
            cols[var] = da.to_series()
    return pd.DataFrame(cols)


def draws(values) -> np.ndarray:
    """Flatten (chain, draw, ...) posterior samples to (n_samples, ...)."""
    arr = np.asarray(values)
    return arr.reshape(-1, *arr.shape[2:])


def save(mmm: model.Meridian, name: str) -> Path:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUTPUT_DIR / f"{name}.binpb"
    meridian_serde.save_meridian(mmm, str(path))
    return path


def load(name: str) -> model.Meridian:
    return meridian_serde.load_meridian(str(OUTPUT_DIR / f"{name}.binpb"))
