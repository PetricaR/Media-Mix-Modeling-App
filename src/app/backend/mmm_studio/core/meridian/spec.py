"""Meridian input DataFrame builder, ModelSpec configuration, and binary persistence."""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from mmm_studio.core.features import calendar_controls, seasonality_controls
from mmm_studio.core.model_settings import (
    CALENDAR_GROUPS,
    DEFAULT_IN_HOME_SHIFT,
    MERIDIAN_ROI_PRIOR,
    MIN_TRAINING_DAYS,
    default_adstock_shape,
)
from mmm_studio.core.schema import DataRoleConfig, slugify_channel

DEFAULT_PAID_CHANNELS: list[str] = [
    "SEA",
    "Catalog",
    "Display",
    "Affiliate",
    "Display Network",
    "Social Media",
    "PSM",
]
DEFAULT_CHANNEL_NAMES: dict[str, str] = {slugify_channel(c): c for c in DEFAULT_PAID_CHANNELS}
# Daily correlation of a channel's cost with its own clicks above which the cost is billed per click and follows demand
DEMAND_FOLLOWING_CLICK_CORR = 0.9

# Display names of the run being processed. Set per run (and per request/job context), never merged across runs.
_RUN_CHANNEL_NAMES: ContextVar[dict[str, str] | None] = ContextVar("mmm_run_channel_names", default=None)


def set_channel_names(names: dict[str, str]) -> None:
    """Make `names` (slug → display name) the channel labels of the current run context."""
    _RUN_CHANNEL_NAMES.set(dict(names))


def channel_label(slug: str) -> str:
    """Display name of a channel slug for the current run, falling back to the case-study names, then the slug."""
    names = _RUN_CHANNEL_NAMES.get() or {}
    return names.get(slug) or DEFAULT_CHANNEL_NAMES.get(slug, slug)


def infer_demand_following(wide: pd.DataFrame, slug: str) -> bool:
    """True when a channel's daily cost tracks its own clicks (r ≥ 0.9): it is billed per click and follows demand."""
    cost, clicks = f"cost_{slug}", f"clicks_{slug}"
    if cost not in wide or clicks not in wide:
        return False
    c, k = wide[cost].astype(float), wide[clicks].astype(float)
    if c.std() == 0 or k.std() == 0:
        return False
    return bool(c.corr(k) >= DEMAND_FOLLOWING_CLICK_CORR)


def holdout_range(index: pd.DatetimeIndex, holdout_days: int) -> tuple[str, str]:
    """The last `holdout_days` days of `index` (inclusive on both ends, as Meridian's `DateRange`).

    Raises `ValueError` when fewer than `MIN_TRAINING_DAYS` days would be left for training: a hold-out is never moved
    or shrunk silently.
    """
    idx = pd.DatetimeIndex(index)
    if len(idx) == 0:
        raise ValueError("The modelling panel is empty.")
    days = int(holdout_days)
    training_days = len(idx) - days
    if training_days < MIN_TRAINING_DAYS:
        raise ValueError(
            f"A {days}-day hold-out leaves {training_days} training days; at least {MIN_TRAINING_DAYS} are needed "
            f"(the data has {len(idx)} days). Shorten the hold-out or upload a longer history."
        )
    end_dt = idx.max()
    start_dt = end_dt - pd.Timedelta(days=days - 1)
    return start_dt.strftime("%Y-%m-%d"), end_dt.strftime("%Y-%m-%d")


@dataclass
class MeridianSpecConfig:
    """Configuration for building and fitting a Google Meridian model. Defaults are notebook 03's specification."""

    catalog_shift: int = 2
    as_controls: tuple[str, ...] = ("affiliate",)
    max_lag: int = 21
    adstock_decay: dict[str, str] = field(default_factory=lambda: {"catalog": "binomial"})
    holdout: tuple[str, str] | None = None
    roi_prior: dict[str, tuple[float, float]] | None = None
    n_fourier: int = 2
    paid_channels: list[str] = field(default_factory=lambda: list(DEFAULT_PAID_CHANNELS))
    # Print-event channels, moved from send-out to in-home date by their own shift (`print_shifts`, default
    # `catalog_shift`)
    print_slugs: tuple[str, ...] = ("catalog",)
    print_shifts: dict[str, int] = field(default_factory=dict)
    calendar: tuple[str, ...] = CALENDAR_GROUPS
    trend: bool = True
    knots: int | None = None
    enable_aks: bool = False
    hill_before_adstock: bool = False
    media_prior_type: str = "roi"
    # Channels without Hill saturation (Meridian's `saturation_spec` "none"); every other channel uses Hill
    saturation: dict[str, str] = field(default_factory=dict)
    # Channels whose missing workbook rows get a 0/1 control (`missing_{slug}` in the panel)
    missing_controls: tuple[str, ...] = ()
    # How `roi_prior` was set, so refits on other training windows can recompute attributed-ROAS priors
    roi_prior_mode: str = "meridian"
    roas_prior_sigma: float = 0.5
    # Media channels whose cost follows demand: their ROI is an upper bound, and the optimiser reports them apart
    upper_bound_slugs: tuple[str, ...] = ()
    # Controls whose cost is a commission on their own sales (role "commission"); the selection grid tries them as media
    commission_slugs: tuple[str, ...] = ("affiliate",)

    @property
    def paid_slugs(self) -> list[str]:
        """Canonical snake_case slugs for `self.paid_channels`."""
        return [slugify_channel(c) for c in self.paid_channels]

    @property
    def media_slugs(self) -> list[str]:
        """Paid channels that enter Meridian as media (the rest are controls)."""
        return [s for s in self.paid_slugs if s not in self.as_controls]

    def shift_for(self, slug: str) -> int:
        """In-home shift (days) of a print-event channel; 0 for every other channel."""
        if slug not in self.print_slugs:
            return 0
        return int(self.print_shifts.get(slug, self.catalog_shift))

    def with_training_priors(self, wide: pd.DataFrame) -> MeridianSpecConfig:
        """This config with attributed-ROAS priors recomputed on `wide`'s training days (outside `holdout`)."""
        if self.roi_prior_mode != "attributed_roas" or self.media_prior_type != "roi":
            return self
        priors = attributed_roas_priors(wide, self.media_slugs, self.roas_prior_sigma, holdout=self.holdout)
        return replace(self, roi_prior=priors or None)

    @classmethod
    def from_role_config(
        cls,
        role_config: DataRoleConfig,
        index: pd.DatetimeIndex | None = None,
        wide: pd.DataFrame | None = None,
    ) -> MeridianSpecConfig:
        """Construct a `MeridianSpecConfig` from a run's `DataRoleConfig` (roles and model settings).

        The hold-out is the last `holdout_days` days of the panel (`index`, or `wide`'s dates); one of them is required.
        `wide` (the modelling panel) is also used for ROI priors centred on attributed ROAS, the missing-row controls
        and the demand-following inference. Invalid settings raise `ValueError`; nothing is replaced silently.
        """
        if index is None and wide is not None:
            index = pd.DatetimeIndex(wide.index)
        if index is None:
            raise ValueError("The Meridian specification needs the modelling panel's dates.")
        ms = role_config.model_settings

        active_paid_or_ctrl = [
            ch
            for ch in role_config.channels
            if ch.enabled and ch.role in ("paid", "print_event", "commission", "digital_paid", "control")
        ]
        if not active_paid_or_ctrl:
            raise ValueError("No channel with costs is enabled; mark at least one channel as paid media.")
        set_channel_names({ch.slug: ch.name for ch in role_config.channels})

        paid_names = [ch.name for ch in active_paid_or_ctrl]
        # Preserve canonical ordering when the channels match the default Tchibo set
        if set(paid_names) == set(DEFAULT_PAID_CHANNELS):
            paid_names = list(DEFAULT_PAID_CHANNELS)

        as_controls = tuple(ch.slug for ch in active_paid_or_ctrl if ch.role in ("commission", "control"))
        adstock_decay: dict[str, str] = {}
        saturation: dict[str, str] = {}
        print_slugs: list[str] = []
        print_shifts: dict[str, int] = {}
        upper_bound: list[str] = []
        for ch in active_paid_or_ctrl:
            if ch.role in ("paid", "digital_paid", "print_event"):
                decay = ch.adstock_shape or default_adstock_shape(ch.role)
                if decay != "geometric":
                    adstock_decay[ch.slug] = decay
                if ch.saturation == "none":
                    saturation[ch.slug] = "none"
                following = ch.demand_following
                if following is None and wide is not None:
                    following = infer_demand_following(wide, ch.slug)
                if following:
                    upper_bound.append(ch.slug)
            if ch.role == "print_event":
                print_slugs.append(ch.slug)
                print_shifts[ch.slug] = int(ch.in_home_shift_days if ch.in_home_shift_days is not None else DEFAULT_IN_HOME_SHIFT)
        catalog_shift = print_shifts[print_slugs[0]] if print_slugs else DEFAULT_IN_HOME_SHIFT

        holdout = holdout_range(pd.DatetimeIndex(index), role_config.holdout_days)
        missing: tuple[str, ...] = ()
        if ms.missing_row_controls and wide is not None:
            missing = tuple(
                ch.slug
                for ch in role_config.channels
                if ch.enabled and ch.role != "ignored" and f"missing_{ch.slug}" in wide and float(wide[f"missing_{ch.slug}"].sum()) > 0
            )

        cfg = cls(
            catalog_shift=catalog_shift,
            as_controls=as_controls,
            max_lag=ms.max_lag,
            adstock_decay=adstock_decay,
            holdout=holdout,
            n_fourier=ms.n_fourier,
            paid_channels=paid_names,
            print_slugs=tuple(print_slugs),
            print_shifts=print_shifts,
            calendar=ms.calendar.groups(),
            trend=ms.trend,
            knots=ms.n_knots if ms.baseline == "knots" else None,
            enable_aks=ms.baseline == "auto",
            hill_before_adstock=ms.hill_before_adstock,
            media_prior_type=ms.media_prior_type,
            saturation=saturation,
            missing_controls=missing,
            roi_prior_mode=ms.roi_prior if ms.media_prior_type == "roi" else "meridian",
            roas_prior_sigma=ms.roas_prior_sigma,
            upper_bound_slugs=tuple(upper_bound),
            commission_slugs=tuple(ch.slug for ch in active_paid_or_ctrl if ch.role == "commission"),
        )
        if ms.media_prior_type == "roi":
            if ms.roi_prior == "attributed_roas" and wide is not None:
                cfg = cfg.with_training_priors(wide)
            elif ms.roi_prior == "custom" and ms.custom_roi_priors:
                unknown = sorted(set(ms.custom_roi_priors) - set(cfg.media_slugs))
                if unknown:
                    raise ValueError(
                        f"Custom ROI priors for {', '.join(unknown)}: only media channels get an ROI prior "
                        f"({', '.join(cfg.media_slugs)})."
                    )
                cfg.roi_prior = {slug: (float(np.log(p.median)), float(p.sigma)) for slug, p in ms.custom_roi_priors.items()}
        return cfg


def attributed_roas_priors(
    wide: pd.DataFrame,
    slugs: list[str],
    sigma: float,
    holdout: tuple[str, str] | None = None,
) -> dict[str, tuple[float, float]]:
    """LogNormal ROI priors centred on each channel's attributed ROAS (channel sales / cost) over the training days.

    Days inside `holdout` are left out, so a hold-out never informs the priors of the model scored on it.
    """
    train = wide
    if holdout is not None and len(wide.index) > 0:
        idx = pd.DatetimeIndex(wide.index)
        mask = (idx >= pd.Timestamp(holdout[0])) & (idx <= pd.Timestamp(holdout[1]))
        train = wide.loc[~mask]
    priors: dict[str, tuple[float, float]] = {}
    for slug in slugs:
        cost, sales = f"cost_{slug}", f"sales_{slug}"
        if cost in train and sales in train and float(train[cost].sum()) > 0:
            roas = float(train[sales].sum()) / float(train[cost].sum())
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
    print_shifts: dict[str, int] | None = None,
    calendar: tuple[str, ...] = CALENDAR_GROUPS,
    trend: bool = True,
    missing_controls: tuple[str, ...] = (),
    holdout: tuple[str, str] | None = None,
) -> pd.DataFrame:
    """Build the national daily Meridian input table from the wide daily panel.

    A `missing_{slug}` control enters only when it flags at least one training day (outside `holdout`): a control that
    is constant on the training days would be identified by its prior alone.
    """
    channels = paid_channels or DEFAULT_PAID_CHANNELS
    shifts = print_shifts or {}
    idx = pd.DatetimeIndex(wide.index)
    table = pd.DataFrame({"time": idx.strftime("%Y-%m-%d"), "total_sales": wide["total_sales"]})
    for ch in channels:
        slug = slugify_channel(ch)
        col = f"cost_{slug}"
        spend = wide[col] if col in wide.columns else pd.Series(0.0, index=idx)
        shift = int(shifts.get(slug, catalog_shift)) if slug in print_slugs else 0
        if shift > 0:
            spend = spend.shift(shift, fill_value=0.0)
        table[f"{slug}_spend"] = spend.astype(float)
    table = table.join(calendar_controls(idx, calendar)).join(seasonality_controls(idx, n_fourier, trend))
    if missing_controls:
        train = np.ones(len(idx), dtype=bool)
        if holdout is not None:
            train = ~((idx >= pd.Timestamp(holdout[0])) & (idx <= pd.Timestamp(holdout[1])))
        for slug in missing_controls:
            col = f"missing_{slug}"
            if col in wide.columns and float(wide.loc[train, col].sum()) > 0:
                table[col] = wide[col].fillna(0.0).astype(float)
    return table.reset_index(drop=True)


def meridian_table(wide: pd.DataFrame, cfg: MeridianSpecConfig) -> pd.DataFrame:
    """`build_meridian_table` with every option taken from `cfg`."""
    return build_meridian_table(
        wide,
        catalog_shift=cfg.catalog_shift,
        n_fourier=cfg.n_fourier,
        paid_channels=cfg.paid_channels,
        print_slugs=cfg.print_slugs,
        print_shifts=cfg.print_shifts,
        calendar=cfg.calendar,
        trend=cfg.trend,
        missing_controls=cfg.missing_controls,
        holdout=cfg.holdout,
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
        times = set(str(t) for t in input_data.time.values)
        if start not in times or end not in times:
            raise ValueError(f"The hold-out {start} .. {end} is outside the modelled dates; refusing to fit without it.")
        holdout = spec.HoldoutSpec(spec=[spec.DateRange(start_date=start, end_date=end)])
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
