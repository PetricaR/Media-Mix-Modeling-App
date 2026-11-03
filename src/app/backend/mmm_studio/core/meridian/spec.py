"""Meridian input DataFrame builder, ModelSpec configuration, and binary persistence."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from mmm_studio.core.features import calendar_controls, seasonality_controls
from mmm_studio.core.model_settings import (
    CALENDAR_GROUPS,
    DEFAULT_IN_HOME_SHIFT,
    MERIDIAN_ROI_PRIOR,
    default_adstock_shape,
)
from mmm_studio.core.schema import DataRoleConfig, slugify_channel

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
    """Configuration for building and fitting a Google Meridian model. Defaults are notebook 03's specification."""

    catalog_shift: int = 2
    as_controls: tuple[str, ...] = ("affiliate",)
    max_lag: int = 21
    adstock_decay: dict[str, str] = field(default_factory=lambda: {"catalog": "binomial"})
    holdout: tuple[str, str] | None = HOLDOUT_Q4_2024
    roi_prior: dict[str, tuple[float, float]] | None = None
    n_fourier: int = 2
    paid_channels: list[str] = field(default_factory=lambda: list(DEFAULT_PAID_CHANNELS))
    # Print-event channels, moved from send-out to in-home date by `catalog_shift`
    print_slugs: tuple[str, ...] = ("catalog",)
    calendar: tuple[str, ...] = CALENDAR_GROUPS
    trend: bool = True
    knots: int | None = None
    enable_aks: bool = False
    hill_before_adstock: bool = False
    media_prior_type: str = "roi"
    # Channels without Hill saturation (Meridian's `saturation_spec` "none"); every other channel uses Hill
    saturation: dict[str, str] = field(default_factory=dict)

    @property
    def paid_slugs(self) -> list[str]:
        """Canonical snake_case slugs for `self.paid_channels`."""
        return [slugify_channel(c) for c in self.paid_channels]

    @property
    def media_slugs(self) -> list[str]:
        """Paid channels that enter Meridian as media (the rest are controls)."""
        return [s for s in self.paid_slugs if s not in self.as_controls]

    @classmethod
    def from_role_config(
        cls,
        role_config: DataRoleConfig | None = None,
        index: pd.DatetimeIndex | None = None,
        wide: pd.DataFrame | None = None,
    ) -> MeridianSpecConfig:
        """Construct a `MeridianSpecConfig` from a run's `DataRoleConfig` (roles and model settings).

        `wide` (the modelling panel) is needed only for ROI priors centred on attributed ROAS; `index` defaults to its
        dates.
        """
        if role_config is None:
            return cls()
        if index is None and wide is not None:
            index = pd.DatetimeIndex(wide.index)
        ms = role_config.model_settings

        active_paid_or_ctrl = [
            ch
            for ch in role_config.channels
            if ch.enabled and ch.role in ("paid", "print_event", "commission", "digital_paid", "control")
        ]
        for ch in active_paid_or_ctrl:
            CHANNEL_DISPLAY_NAMES.setdefault(ch.slug, ch.name)

        paid_names = [ch.name for ch in active_paid_or_ctrl] or list(DEFAULT_PAID_CHANNELS)
        # Preserve canonical ordering when the channels match the default Tchibo set
        if set(paid_names) == set(DEFAULT_PAID_CHANNELS):
            paid_names = list(DEFAULT_PAID_CHANNELS)

        as_controls = tuple(
            ch.slug
            for ch in active_paid_or_ctrl
            if ch.role in ("commission", "control") or ch.slug == "affiliate"
        )
        adstock_decay: dict[str, str] = {}
        saturation: dict[str, str] = {}
        print_slugs: list[str] = []
        catalog_shift = DEFAULT_IN_HOME_SHIFT
        for ch in active_paid_or_ctrl:
            if ch.role in ("paid", "digital_paid", "print_event"):
                decay = ch.adstock_shape or default_adstock_shape(ch.role)
                if decay != "geometric":
                    adstock_decay[ch.slug] = decay
                if ch.saturation == "none":
                    saturation[ch.slug] = "none"
            if ch.role == "print_event":
                print_slugs.append(ch.slug)
                if ch.in_home_shift_days is not None:
                    catalog_shift = int(ch.in_home_shift_days)

        holdout: tuple[str, str] | None = HOLDOUT_Q4_2024
        if role_config.holdout_days <= 0:
            holdout = None
        elif index is not None and len(index) > role_config.holdout_days * 2:
            # Meridian's DateRange is inclusive on both ends: the hold-out is the last `holdout_days` days.
            idx = pd.DatetimeIndex(index)
            end_dt = idx.max()
            start_dt = end_dt - pd.Timedelta(days=int(role_config.holdout_days) - 1)
            holdout = (start_dt.strftime("%Y-%m-%d"), end_dt.strftime("%Y-%m-%d"))

        cfg = cls(
            catalog_shift=catalog_shift,
            as_controls=as_controls,
            max_lag=ms.max_lag,
            adstock_decay=adstock_decay,
            holdout=holdout,
            n_fourier=ms.n_fourier,
            paid_channels=paid_names,
            print_slugs=tuple(print_slugs),
            calendar=ms.calendar.groups(),
            trend=ms.trend,
            knots=ms.n_knots if ms.baseline == "knots" else None,
            enable_aks=ms.baseline == "auto",
            hill_before_adstock=ms.hill_before_adstock,
            media_prior_type=ms.media_prior_type,
            saturation=saturation,
        )
        if ms.media_prior_type == "roi":
            if ms.roi_prior == "attributed_roas" and wide is not None:
                cfg.roi_prior = attributed_roas_priors(wide, cfg.media_slugs, ms.roas_prior_sigma) or None
            elif ms.roi_prior == "custom" and ms.custom_roi_priors:
                cfg.roi_prior = {slug: (float(np.log(p.median)), float(p.sigma)) for slug, p in ms.custom_roi_priors.items()}
        return cfg


def attributed_roas_priors(wide: pd.DataFrame, slugs: list[str], sigma: float) -> dict[str, tuple[float, float]]:
    """LogNormal ROI priors centred on each channel's attributed ROAS (channel sales / cost), as notebook 03 §1."""
    priors: dict[str, tuple[float, float]] = {}
    for slug in slugs:
        cost, sales = f"cost_{slug}", f"sales_{slug}"
        if cost in wide and sales in wide and float(wide[cost].sum()) > 0:
            roas = float(wide[sales].sum()) / float(wide[cost].sum())
            if roas > 0:
                priors[slug] = (float(np.log(roas)), float(sigma))
    return priors


def build_meridian_table(
    wide: pd.DataFrame,
    catalog_shift: int = 2,
    n_fourier: int = 2,
    paid_channels: list[str] | None = None,
    *,
    print_slugs: tuple[str, ...] = ("catalog",),
    calendar: tuple[str, ...] = CALENDAR_GROUPS,
    trend: bool = True,
) -> pd.DataFrame:
    """Build the national daily Meridian input table from the wide daily panel."""
    channels = paid_channels or DEFAULT_PAID_CHANNELS
    idx = pd.DatetimeIndex(wide.index)
    table = pd.DataFrame({"time": idx.strftime("%Y-%m-%d"), "total_sales": wide["total_sales"]})
    for ch in channels:
        slug = slugify_channel(ch)
        col = f"cost_{slug}"
        spend = wide[col] if col in wide.columns else pd.Series(0.0, index=idx)
        if slug in print_slugs and catalog_shift > 0:
            spend = spend.shift(catalog_shift, fill_value=0.0)
        table[f"{slug}_spend"] = spend.astype(float)
    table = table.join(calendar_controls(idx, calendar)).join(seasonality_controls(idx, n_fourier, trend))
    return table.reset_index(drop=True)


def meridian_table(wide: pd.DataFrame, cfg: MeridianSpecConfig) -> pd.DataFrame:
    """`build_meridian_table` with every option taken from `cfg`."""
    return build_meridian_table(
        wide,
        catalog_shift=cfg.catalog_shift,
        n_fourier=cfg.n_fourier,
        paid_channels=cfg.paid_channels,
        print_slugs=cfg.print_slugs,
        calendar=cfg.calendar,
        trend=cfg.trend,
    )


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
    builder = dfb.DataFrameInputDataBuilder(kpi_type="revenue", default_kpi_column="total_sales")
    builder.currency_code = "EUR"  # Meridian's reports otherwise label the euro amounts with "$"
    builder = builder.with_kpi(table)
    if control_cols:  # every control can be switched off in the run settings
        builder = builder.with_controls(table, control_cols=control_cols)
    return builder.with_media(table, media_cols=spend_cols, media_spend_cols=spend_cols, media_channels=media_channels).build()


def build_meridian_model(input_data: Any, cfg: MeridianSpecConfig) -> Any:
    """Construct an unfitted `meridian.model.model.Meridian` instance."""
    from meridian import backend, constants
    from meridian.model import model, prior_distribution, spec

    channels = list(input_data.media_channel.values)
    kwargs: dict[str, Any] = {}
    if cfg.media_prior_type != "roi":
        kwargs["media_prior_type"] = cfg.media_prior_type
    elif cfg.roi_prior:
        default = MERIDIAN_ROI_PRIOR
        mu = np.array([cfg.roi_prior.get(c, default)[0] for c in channels], dtype=np.float64)
        sigma = np.array([cfg.roi_prior.get(c, default)[1] for c in channels], dtype=np.float64)
        kwargs["prior"] = prior_distribution.PriorDistribution(
            roi_m=backend.tfd.LogNormal(mu, sigma, name=constants.ROI_M)
        )
    if cfg.enable_aks:
        kwargs["enable_aks"] = True
    elif cfg.knots is not None:
        kwargs["knots"] = int(cfg.knots)
    if cfg.hill_before_adstock:
        kwargs["hill_before_adstock"] = True
    saturation = {k: v for k, v in cfg.saturation.items() if k in channels}
    if saturation:
        kwargs["saturation_spec"] = saturation
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
