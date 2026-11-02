"""Meridian input DataFrame builder, ModelSpec configuration, and binary persistence."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import holidays
import numpy as np
import pandas as pd

from mmm_studio.core.schema import slugify_channel

HOLDOUT_Q4_2024: tuple[str, str] = ("2024-10-01", "2025-01-01")
DEFAULT_PAID_CHANNELS: list[str] = [
    "SEA",
    "Catalog",
    "Display",
    "Affiliate",
    "Display Network",
    "Social Media",
    "PSM",
]
CHANNEL_DISPLAY_NAMES: dict[str, str] = {slugify_channel(c): c for c in DEFAULT_PAID_CHANNELS}


@dataclass
class MeridianSpecConfig:
    """Configuration for building and fitting a Google Meridian model."""

    catalog_shift: int = 2
    as_controls: tuple[str, ...] = ("affiliate",)
    max_lag: int = 21
    adstock_decay: dict[str, str] = field(default_factory=lambda: {"catalog": "binomial"})
    holdout: tuple[str, str] | None = HOLDOUT_Q4_2024
    roi_prior: dict[str, tuple[float, float]] | None = None
    n_fourier: int = 2
    paid_channels: list[str] = field(default_factory=lambda: list(DEFAULT_PAID_CHANNELS))


def _black_friday(year: int) -> pd.Timestamp:
    nov1 = pd.Timestamp(year=year, month=11, day=1)
    first_thu = nov1 + pd.Timedelta(days=(3 - nov1.dayofweek) % 7)
    return first_thu + pd.Timedelta(days=22)


def _calendar_controls(index: pd.DatetimeIndex) -> pd.DataFrame:
    out = pd.DataFrame(index=index)
    for d, name in enumerate(["mon", "tue", "wed", "thu", "fri", "sat", "sun"]):
        if d:
            out[f"dow_{name}"] = (index.dayofweek == d).astype(float)
    de_holidays = holidays.Germany(years=sorted(set(index.year)))
    out["holiday"] = index.map(lambda d: d in de_holidays).astype(float)
    out["christmas"] = ((index.month == 12) & (index.day >= 20)).astype(float)
    bw = np.zeros(len(index), dtype=bool)
    for year in sorted(set(index.year)):
        bf = _black_friday(year)
        bw |= (index >= bf) & (index <= bf + pd.Timedelta(days=3))
    out["black_week"] = bw.astype(float)
    return out


def _seasonality_controls(index: pd.DatetimeIndex, n_fourier: int = 2) -> pd.DataFrame:
    out = pd.DataFrame(index=index)
    t = index.dayofyear / 365.25
    for k in range(1, n_fourier + 1):
        out[f"sin_{k}"] = np.sin(2 * np.pi * k * t)
        out[f"cos_{k}"] = np.cos(2 * np.pi * k * t)
    out["trend"] = (index - index[0]).days / 365.25
    return out


def build_meridian_table(
    wide: pd.DataFrame,
    catalog_shift: int = 2,
    n_fourier: int = 2,
    paid_channels: list[str] | None = None,
) -> pd.DataFrame:
    """Build the national daily Meridian input table from the wide daily panel."""
    channels = paid_channels or DEFAULT_PAID_CHANNELS
    idx = pd.DatetimeIndex(wide.index)
    table = pd.DataFrame({"time": idx.strftime("%Y-%m-%d"), "total_sales": wide["total_sales"]})
    for ch in channels:
        slug = slugify_channel(ch)
        col = f"cost_{slug}"
        spend = wide[col] if col in wide.columns else pd.Series(0.0, index=idx)
        if slug == "catalog" and catalog_shift > 0:
            spend = spend.shift(catalog_shift, fill_value=0.0)
        table[f"{slug}_spend"] = spend.astype(float)
    table = table.join(_calendar_controls(idx)).join(_seasonality_controls(idx, n_fourier))
    return table.reset_index(drop=True)


def meridian_roles(
    table: pd.DataFrame,
    cfg: MeridianSpecConfig,
) -> tuple[list[str], list[str], list[str]]:
    """Return `(spend_cols, media_channel_slugs, control_cols)` for `cfg`."""
    all_slugs = [slugify_channel(c) for c in cfg.paid_channels]
    all_spend_cols = [f"{s}_spend" for s in all_slugs]
    moved_spend = [f"{c}_spend" for c in cfg.as_controls]
    spend_cols = [s for s in all_spend_cols if s not in moved_spend and s in table.columns]
    media_channels = [s for s in all_slugs if s not in cfg.as_controls and f"{s}_spend" in table.columns]
    fixed = {"time", "total_sales", *all_spend_cols}
    base_controls = [c for c in table.columns if c not in fixed]
    controls = base_controls + [c for c in moved_spend if c in table.columns]
    return spend_cols, media_channels, controls


def build_input_data(table: pd.DataFrame, cfg: MeridianSpecConfig) -> Any:
    """Build Meridian InputData from the prepared table."""
    from meridian.data import data_frame_input_data_builder as dfb

    spend_cols, media_channels, control_cols = meridian_roles(table, cfg)
    return (
        dfb.DataFrameInputDataBuilder(kpi_type="revenue", default_kpi_column="total_sales")
        .with_kpi(table)
        .with_controls(table, control_cols=control_cols)
        .with_media(table, media_cols=spend_cols, media_spend_cols=spend_cols, media_channels=media_channels)
        .build()
    )


def build_meridian_model(input_data: Any, cfg: MeridianSpecConfig) -> Any:
    """Construct an unfitted `meridian.model.model.Meridian` instance."""
    from meridian import backend, constants
    from meridian.model import model, prior_distribution, spec

    channels = list(input_data.media_channel.values)
    kwargs: dict[str, Any] = {}
    if cfg.roi_prior:
        default = (0.2, 0.9)
        mu = np.array([cfg.roi_prior.get(c, default)[0] for c in channels], dtype=np.float64)
        sigma = np.array([cfg.roi_prior.get(c, default)[1] for c in channels], dtype=np.float64)
        kwargs["prior"] = prior_distribution.PriorDistribution(
            roi_m=backend.tfd.LogNormal(mu, sigma, name=constants.ROI_M)
        )
    holdout = None
    if cfg.holdout:
        start, end = cfg.holdout
        # Clamp holdout end date to available time coordinates in input_data
        avail_times = list(input_data.time.values)
        if avail_times:
            end_clamped = min(end, avail_times[-1])
            start_clamped = max(start, avail_times[0])
            if start_clamped <= end_clamped:
                holdout = spec.HoldoutSpec(
                    spec=[spec.DateRange(start_date=start_clamped, end_date=end_clamped)]
                )
    adstock_decay = {k: v for k, v in cfg.adstock_decay.items() if k in channels}
    model_spec = spec.ModelSpec(
        max_lag=cfg.max_lag,
        adstock_decay_spec=adstock_decay or "geometric",
        holdout=holdout,
        **kwargs,
    )
    return model.Meridian(input_data=input_data, model_spec=model_spec)


def save_meridian_model(mmm: Any, path: Path | str) -> Path:
    """Serialize a fitted Meridian model to `.binpb`."""
    from meridian.schema.serde import meridian_serde

    out_path = Path(path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    meridian_serde.save_meridian(mmm, str(out_path))
    return out_path


def load_meridian_model(path: Path | str) -> Any:
    """Deserialize a fitted Meridian model from `.binpb`."""
    from meridian.schema.serde import meridian_serde

    return meridian_serde.load_meridian(str(path))
