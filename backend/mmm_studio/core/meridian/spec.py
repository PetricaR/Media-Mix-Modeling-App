"""Meridian input DataFrame builder, ModelSpec configuration, and binary persistence."""

from __future__ import annotations

import warnings
from collections.abc import Iterator
from contextlib import contextmanager
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

_MERIDIAN_WARNING_PATTERNS: tuple[str, ...] = (
    r"Hierarchical distribution parameters must be deterministically zero for national models\..*",
    r"In a nationally aggregated model, the `media_effects_dist` will be reset to `normal`\.",
    r"Revenue from the `kpi` data is used when `kpi_type`=`revenue`\..*",
    r"(The group )?trace( group)? is not defined in the InferenceData scheme",
    # Meridian's own analyzer/summarizer/optimizer pass geo arguments and `use_kpi=True` internally; on a national
    # revenue model both are reset to the only valid value, so the warnings carry no information for the app.
    r"The `\w+` argument is ignored in the national model\..*",
    r"Setting `use_kpi=True` has no effect when `kpi_type=REVENUE`.*",
    r"Effectiveness is not reported because it does not have a clear interpretation by time period\.",
    # `sample_prior(seed=…)` calls `backend.set_random_seed` before building its PRNG key; on JAX that call is a no-op
    # and the seed still reaches the sampler through `RNGHandler(seed)`, so fits stay reproducible.
    r"backend\.set_random_seed is a no-op in JAX\..*",
    # Altair, inside Meridian's HTML report charts: an all-null column (no rows in that chart) is encoded as nominal.
    r"I don't know how to infer vegalite type from 'empty'\..*",
)


def _install_meridian_warning_filters() -> None:
    """Register warning filters for known benign Meridian, ArviZ and Altair (report chart) UserWarnings."""
    for pattern in _MERIDIAN_WARNING_PATTERNS:
        warnings.filterwarnings("ignore", message=pattern, category=UserWarning)


class _TrimmedAKSData:
    """Lightweight view over `InputData` restricting `AKS` to the non-placeholder historical prefix."""

    def __init__(self, data: Any, n_active: int) -> None:
        self._data = data
        self.time = data.time[:n_active]
        self.geo = data.geo
        raw_kpi = np.asarray(data.scaled_centered_kpi, dtype=np.float64)
        n_geos = int(len(data.geo))
        y_2d = np.reshape(raw_kpi, (n_geos, len(data.time)))[:, :n_active]
        mean = np.mean(y_2d, axis=1, keepdims=True)
        std = np.std(y_2d, axis=1, keepdims=True)
        std = np.where(std > 1e-8, std, 1.0)
        self.scaled_centered_kpi = ((y_2d - mean) / std).astype(raw_kpi.dtype)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._data, name)


def _active_aks_time_steps(data: Any, min_internal_knots: int = 1) -> int:
    """Return the prefix length before any trailing constant-KPI placeholder tail (e.g. `extend_history`)."""
    n_times = int(len(data.time))
    n_geos = int(len(data.geo))
    if n_times <= 4:
        return n_times
    y_2d = np.reshape(np.asarray(data.scaled_centered_kpi, dtype=np.float64), (n_geos, n_times))
    tail_start = n_times
    while tail_start > 1 and np.allclose(y_2d[:, tail_start - 2], y_2d[:, -1], rtol=0.0, atol=1e-6):
        tail_start -= 1
    if n_times - tail_start < 1:
        return n_times
    n_active = tail_start - 1
    n_features = sum(
        len(feature) if feature is not None else 0
        for feature in (
            data.media_channel,
            data.rf_channel,
            data.organic_media_channel,
            data.organic_rf_channel,
            data.non_media_channel,
            data.control_variable,
        )
    )
    min_required = n_features + max(int(min_internal_knots), 1) + 4
    return n_active if n_active >= min_required else n_times


def _install_meridian_aks_guard() -> None:
    """Guard Meridian's `AKS` against constant forecast-tail KPI placeholders and zero/underflow `sigma0sq`."""
    from meridian import constants
    from meridian.model import knots as knots_mod
    from statsmodels.regression import linear_model

    if getattr(knots_mod.AKS, "_mmm_studio_guarded", False):
        return

    orig_aspline = knots_mod.AKS.aspline
    orig_aks = knots_mod.AKS.automatic_knot_selection

    def _safe_aspline(
        self: Any,
        x: np.ndarray,
        y: np.ndarray,
        knots: np.ndarray,
        penalty: np.ndarray,
        *args: Any,
        **kwargs: Any,
    ) -> dict[str, Any]:
        with np.errstate(divide="ignore", invalid="ignore", over="ignore"):
            out = orig_aspline(self, x, y, knots, penalty, *args, **kwargs)
        xmat = self._get_bspline_matrix(x, knots)
        sigma0sq = float(linear_model.OLS(y, xmat).fit().mse_resid)
        aic_arr = np.asarray(out[constants.AIC], dtype=np.float64)
        if not np.isfinite(sigma0sq) or sigma0sq <= 1e-8 or not np.any(np.isfinite(aic_arr)):
            y64 = np.asarray(y, dtype=np.float64)
            diff2_var = float(np.var(np.diff(y64, n=2)) / 6.0) if y64.size >= 3 else 0.0
            diff1_var = float(np.var(np.diff(y64)) / 2.0) if y64.size >= 2 else 0.0
            sigma0sq_safe = max(diff2_var, diff1_var, float(np.var(y64)) * 1e-4, 1e-6)
            nrow = max(int(y64.size), 1)
            aic_fixed: list[float] = []
            bic_fixed: list[float] = []
            for k_sel, m in zip(out[constants.KNOTS_SELECTED], out[constants.MODEL], strict=False):
                if m is None or k_sel is None:
                    aic_fixed.append(float("inf"))
                    bic_fixed.append(float("inf"))
                else:
                    dim_i = len(k_sel) + self._DEGREE + 1
                    resid = np.asarray(m.resid, dtype=np.float64)
                    two_loglik = float(np.sum(np.square(resid)) / sigma0sq_safe)
                    aic_fixed.append(2.0 * dim_i + two_loglik)
                    bic_fixed.append(float(np.log(nrow)) * dim_i + two_loglik)
            out[constants.AIC] = np.asarray(aic_fixed, dtype=np.float64)
            out[constants.BIC] = np.asarray(bic_fixed, dtype=np.float64)
        return out

    def _safe_automatic_knot_selection(
        self: Any,
        base_penalty: np.ndarray | None = None,
        min_internal_knots: int = 1,
        max_internal_knots: int | None = None,
        required_knots: Any = None,
        excluded_knots: Any = None,
        restrict_to_right_of_required_knots: bool = False,
    ) -> Any:
        n_active = _active_aks_time_steps(self._data, min_internal_knots=min_internal_knots)
        target = knots_mod.AKS(_TrimmedAKSData(self._data, n_active)) if n_active < len(self._data.time) else self
        try:
            return orig_aks(
                target,
                base_penalty=base_penalty,
                min_internal_knots=min_internal_knots,
                max_internal_knots=max_internal_knots,
                required_knots=required_knots,
                excluded_knots=excluded_knots,
                restrict_to_right_of_required_knots=restrict_to_right_of_required_knots,
            )
        except ValueError:
            end_knot = max(int(n_active) - 1, 1)
            fallback_knots = np.unique(np.array([0, end_knot], dtype=int))
            return knots_mod.AKSResult(knots=fallback_knots, model=None)  # type: ignore[arg-type]

    knots_mod.AKS.aspline = _safe_aspline  # type: ignore[method-assign]
    knots_mod.AKS.automatic_knot_selection = _safe_automatic_knot_selection  # type: ignore[method-assign]
    knots_mod.AKS._mmm_studio_guarded = True  # type: ignore[attr-defined]



@contextmanager
def suppress_meridian_warnings() -> Iterator[None]:
    """Suppress benign Meridian, ArviZ and Altair UserWarnings inside the active warnings context."""
    with warnings.catch_warnings():
        _install_meridian_warning_filters()
        yield


_install_meridian_warning_filters()

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
    """Display name of a channel slug for the current run, falling back to the default channel names, then the slug."""
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


def demand_following_slugs(role_config: DataRoleConfig, wide: pd.DataFrame | None = None) -> tuple[str, ...]:
    """Media channels whose cost follows demand (their ROI is an upper bound), in channel order.

    The run's setting decides; a channel without one is inferred from `wide`. This is the single definition used by the
    model specification, every narrative and the optimiser API.
    """
    out: list[str] = []
    for ch in role_config.channels:
        if not ch.enabled or ch.role not in ("paid", "digital_paid", "print_event"):
            continue
        following = ch.demand_following
        if following is None and wide is not None:
            following = infer_demand_following(wide, ch.slug)
        if following:
            out.append(ch.slug)
    return tuple(out)


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
    """Configuration for building and fitting a Google Meridian model. Defaults are the validated reference specification."""

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
    # How `roi_prior` was set, so refits on other training windows can recompute attributed-ROAS or total-contribution priors
    roi_prior_mode: str = "meridian"
    roas_prior_sigma: float = 0.5
    total_media_contribution_mean: float = 0.4
    total_media_contribution_sd: float = 0.2
    # Media channels whose cost follows demand: their ROI is an upper bound, and the optimiser reports them apart
    upper_bound_slugs: tuple[str, ...] = ()
    # Controls whose cost is a commission on their own sales (role "commission"); the selection grid tries them as media
    commission_slugs: tuple[str, ...] = ("affiliate",)
    # The user's own control variables (`ctrl_{slug}` columns of the panel) and their display names
    user_controls: tuple[str, ...] = ()
    control_names: dict[str, str] = field(default_factory=dict)
    # Country of the public-holiday control
    country: str = "DE"

    @property
    def paid_slugs(self) -> list[str]:
        """Canonical snake_case slugs for `self.paid_channels`."""
        return [slugify_channel(c) for c in self.paid_channels]

    @property
    def media_slugs(self) -> list[str]:
        """Paid channels that enter Meridian as media (the rest are controls)."""
        return [s for s in self.paid_slugs if s not in self.as_controls]

    @property
    def outcome_driven_slugs(self) -> tuple[str, ...]:
        """Channels whose cost follows the outcome (commission and demand-following channels): a leak-free scored
        window plans their cost from history instead of using the realised one (`core/as_of.py`)."""
        return tuple(dict.fromkeys([*self.commission_slugs, *self.upper_bound_slugs]))

    def for_all_days(self, wide: pd.DataFrame) -> MeridianSpecConfig:
        """This config without a hold-out, priors from every day of `wide`: the final model of a leak-free run, fitted
        once its test and validation windows are scored by refits on the days before them."""
        return replace(self, holdout=None).with_training_priors(wide)

    def shift_for(self, slug: str) -> int:
        """In-home shift (days) of a print-event channel; 0 for every other channel."""
        if slug not in self.print_slugs:
            return 0
        return int(self.print_shifts.get(slug, self.catalog_shift))

    def with_training_priors(self, wide: pd.DataFrame) -> MeridianSpecConfig:
        """This config with training-derived ROI priors recomputed on `wide`'s training days (outside `holdout`)."""
        if self.media_prior_type != "roi":
            return self
        if self.roi_prior_mode == "attributed_roas":
            priors = attributed_roas_priors(wide, self.media_slugs, self.roas_prior_sigma, holdout=self.holdout)
            return replace(self, roi_prior=priors or None)
        if self.roi_prior_mode == "total_media_contribution":
            priors = total_media_contribution_priors(
                wide,
                self.media_slugs,
                p_mean=self.total_media_contribution_mean,
                p_sd=self.total_media_contribution_sd,
                holdout=self.holdout,
            )
            return replace(self, roi_prior=priors or None)
        return self

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
        # Preserve canonical ordering when the channels match the default channel set
        if set(paid_names) == set(DEFAULT_PAID_CHANNELS):
            paid_names = list(DEFAULT_PAID_CHANNELS)

        as_controls = tuple(ch.slug for ch in active_paid_or_ctrl if ch.role in ("commission", "control"))
        adstock_decay: dict[str, str] = {}
        saturation: dict[str, str] = {}
        print_slugs: list[str] = []
        print_shifts: dict[str, int] = {}
        upper_bound = list(demand_following_slugs(role_config, wide))
        for ch in active_paid_or_ctrl:
            if ch.role in ("paid", "digital_paid", "print_event"):
                decay = ch.adstock_shape or default_adstock_shape(ch.role)
                if decay != "geometric":
                    adstock_decay[ch.slug] = decay
                if ch.saturation == "none":
                    saturation[ch.slug] = "none"
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
            total_media_contribution_mean=float(getattr(ms, "total_media_contribution_mean", 0.4)),
            total_media_contribution_sd=float(getattr(ms, "total_media_contribution_sd", 0.2)),
            upper_bound_slugs=tuple(upper_bound),
            commission_slugs=tuple(ch.slug for ch in active_paid_or_ctrl if ch.role == "commission"),
            # Legacy configs (and simple stand-ins) have no controls or country: fall back to defaults
            user_controls=tuple(k for k in getattr(role_config, "control_keys", []) if wide is None or k in wide.columns),
            control_names=role_config.control_names() if hasattr(role_config, "control_names") else {},
            country=getattr(role_config, "country", "DE"),
        )
        if ms.media_prior_type == "roi":
            if ms.roi_prior in ("attributed_roas", "total_media_contribution") and wide is not None:
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


def total_media_contribution_priors(
    wide: pd.DataFrame,
    slugs: list[str],
    p_mean: float = 0.4,
    p_sd: float = 0.2,
    holdout: tuple[str, str] | None = None,
) -> dict[str, tuple[float, float]]:
    """Meridian's total media contribution ROI prior (`_get_total_media_contribution_prior`) over the training days.

    Assigns a common LogNormal(mu, sigma) ROI prior across media channels so total media contribution has prior mean
    `p_mean` (default 40 % of total KPI) and prior SD `p_sd` (default 20 %), mitigating negative baseline.
    """
    train = wide
    if holdout is not None and len(wide.index) > 0:
        idx = pd.DatetimeIndex(wide.index)
        mask = (idx >= pd.Timestamp(holdout[0])) & (idx <= pd.Timestamp(holdout[1]))
        train = wide.loc[~mask]
    kpi = float(train["total_sales"].sum()) if "total_sales" in train else 0.0
    spends = np.array([float(train[f"cost_{s}"].sum()) for s in slugs if f"cost_{s}" in train], dtype=np.float64)
    total_spend = float(np.sum(spends))
    l2_spend = float(np.sqrt(np.sum(np.power(spends, 2))))
    if kpi <= 0 or total_spend <= 0 or l2_spend <= 0:
        return {}
    roi_mean = float(p_mean) * kpi / total_spend
    roi_sd = float(p_sd) * kpi / l2_spend
    sigma = float(np.sqrt(np.log((roi_sd / roi_mean) ** 2 + 1.0)))
    mu = float(np.log(roi_mean) - 0.5 * sigma**2)
    return {slug: (mu, sigma) for slug in slugs}


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
    user_controls: tuple[str, ...] = (),
    country: str = "DE",
) -> pd.DataFrame:
    """Build the national daily Meridian input table from the wide daily panel.

    The user's controls (`ctrl_*` columns of `wide`) enter as they are; a forecast's future rows carry their last
    observed value (`forecast.extend_history`).

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
    table = table.join(calendar_controls(idx, calendar, country)).join(seasonality_controls(idx, n_fourier, trend))
    for key in user_controls:
        if key not in wide.columns:
            raise ValueError(f"The control column '{key}' is missing from the daily panel.")
        table[key] = wide[key].astype(float)
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
        user_controls=cfg.user_controls,
        country=cfg.country,
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

    _install_meridian_aks_guard()
    spend_cols, media_channels, control_cols = meridian_roles(table, cfg)
    with suppress_meridian_warnings():
        builder = dfb.DataFrameInputDataBuilder(kpi_type="revenue", default_kpi_column="total_sales")
        builder.currency_code = "EUR"  # Meridian's reports otherwise label the euro amounts with "$"
        builder = builder.with_kpi(table)
        if control_cols:  # every control can be switched off in the run settings
            builder = builder.with_controls(table, control_cols=control_cols)
        return builder.with_media(
            table,
            media_cols=spend_cols,
            media_spend_cols=spend_cols,
            media_channels=media_channels,
        ).build()


def _national_hierarchical_priors() -> dict[str, Any]:
    """Deterministic-zero hierarchical geo scale priors required by Meridian for single-geo (national) models."""
    from meridian import backend, constants

    zero = backend.to_tensor(0.0, dtype=backend.float_dtype)
    return {
        name: backend.tfd.Deterministic(loc=zero, name=name)
        for name in (
            constants.TAU_G_EXCL_BASELINE,
            constants.ETA_M,
            constants.ETA_RF,
            constants.ETA_OM,
            constants.ETA_ORF,
            constants.XI_C,
            constants.XI_N,
        )
    }


def build_meridian_model(input_data: Any, cfg: MeridianSpecConfig) -> Any:
    """Construct an unfitted `meridian.model.model.Meridian` instance."""
    from meridian import backend, constants
    from meridian.model import model, prior_distribution, spec

    _install_meridian_aks_guard()
    channels = list(input_data.media_channel.values)
    is_national = int(input_data.geo.size) == 1
    prior_kwargs: dict[str, Any] = _national_hierarchical_priors() if is_national else {}
    kwargs: dict[str, Any] = {"media_effects_dist": "normal"} if is_national else {}
    if cfg.media_prior_type != "roi":
        kwargs["media_prior_type"] = cfg.media_prior_type
    elif cfg.roi_prior:
        default = MERIDIAN_ROI_PRIOR
        mu = np.array([cfg.roi_prior.get(c, default)[0] for c in channels], dtype=np.float64)
        sigma = np.array([cfg.roi_prior.get(c, default)[1] for c in channels], dtype=np.float64)
        prior_kwargs["roi_m"] = backend.tfd.LogNormal(mu, sigma, name=constants.ROI_M)
    if prior_kwargs:
        kwargs["prior"] = prior_distribution.PriorDistribution(**prior_kwargs)
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
    with suppress_meridian_warnings():
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

    _install_meridian_aks_guard()
    out_path = Path(path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with suppress_meridian_warnings():
        meridian_serde.save_meridian(mmm, str(out_path))
    return out_path


def load_meridian_model(path: Path | str) -> Any:
    """Deserialize a fitted Meridian model from `.binpb`."""
    from meridian.schema.serde import meridian_serde

    _install_meridian_aks_guard()
    with suppress_meridian_warnings():
        mmm = meridian_serde.load_meridian(str(path))
        _ = mmm.prior_broadcast
        return mmm
