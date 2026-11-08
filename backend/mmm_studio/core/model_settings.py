"""Run settings a user can change before a run starts. Every default is the validated reference value.

Pure pydantic (no pandas, no Meridian), so the API can validate a request and serve the defaults cheaply. The limits
on the sampling counts are a guard rail for the public deployment: one Meridian fit must stay within the worker's
timeout.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, Field, field_validator

if TYPE_CHECKING:
    from mmm_studio.core.schema import DataRoleConfig

FULL_SAMPLING: dict[str, int] = {"n_chains": 4, "n_adapt": 1000, "n_burnin": 500, "n_keep": 1000, "n_prior": 500}
FAST_SAMPLING: dict[str, int] = {"n_chains": 2, "n_adapt": 300, "n_burnin": 200, "n_keep": 200, "n_prior": 200}
SMOKE_SAMPLING: dict[str, int] = {"n_chains": 2, "n_adapt": 50, "n_burnin": 50, "n_keep": 50, "n_prior": 50}
PROFILE_SAMPLING: dict[str, dict[str, int]] = {"full": FULL_SAMPLING, "quick": FAST_SAMPLING, "smoke": SMOKE_SAMPLING}
RUN_PROFILES: tuple[str, ...] = ("full", "quick", "smoke", "custom")

# Meridian's own default ROI prior (`PriorDistribution.roi_m`): LogNormal(0.2, 0.9), i.e. a median ROI of e^0.2
MERIDIAN_ROI_PRIOR: tuple[float, float] = (0.2, 0.9)
DEFAULT_IN_HOME_SHIFT = 2
DEFAULT_HOLDOUT_DAYS = 93
# A run needs at least one full seasonal cycle of training days before its hold-out, and so does every refit that is
# scored on the validation window before it
MIN_TRAINING_DAYS = 365
# The chronological split (`features.default_holdout_days`, `features.holdout_windows`): training, then validation and
# test of equal length, back to back at the end of the data. By default each window takes about an eighth of the days
# (75 / 12.5 / 12.5; e.g. 547 / 92 / 93 days on a 2-year panel), at most about a quarter (`DEFAULT_HOLDOUT_DAYS`: Meridian
# advises against long contiguous hold-outs at the end of the data, where its knots need nearby data) and at least four
# weeks; with less data than that the test takes every day beyond the training year and there is no validation window.
SPLIT_WINDOW_SHARE = 0.125
MIN_SPLIT_WINDOW_DAYS = 28
MIN_HOLDOUT_DAYS = 14
# A window's first day moves to the nearest first of a month within this many days, so windows read as months/quarters
SPLIT_SNAP_DAYS = 7
MAX_SEED = 2**31 - 1
SELECTION_SHIFT_CHOICES: tuple[int, ...] = tuple(range(8))
CALENDAR_GROUPS: tuple[str, ...] = ("weekdays", "holidays", "christmas", "black_week")
# Weather controls (Open-Meteo, the run's country): surprises vs the seasonal normal, 0 on future days
WEATHER_CONTROLS: dict[str, str] = {
    "wx_temp_anomaly": "Temperature vs normal (°C)",
    "wx_sunshine_anomaly": "Sunshine vs normal (h)",
    "wx_precip_anomaly": "Precipitation vs normal (mm)",
}


class CalendarControls(BaseModel):
    """Which calendar dummies enter the OLS benchmark and Meridian (weekdays, the country's public holidays from Google
    Calendar, 20–31 Dec with the 24 / 31 Dec half-days, Black Week)."""

    weekdays: bool = True
    holidays: bool = True
    christmas: bool = True
    black_week: bool = True

    def groups(self) -> tuple[str, ...]:
        return tuple(g for g in CALENDAR_GROUPS if getattr(self, g))


class SamplingSettings(BaseModel):
    """MCMC draws of the `custom` profile (per chain, except `n_prior`)."""

    n_chains: int = Field(FULL_SAMPLING["n_chains"], ge=1, le=4)
    n_adapt: int = Field(FULL_SAMPLING["n_adapt"], ge=10, le=2000)
    n_burnin: int = Field(FULL_SAMPLING["n_burnin"], ge=10, le=2000)
    n_keep: int = Field(FULL_SAMPLING["n_keep"], ge=10, le=2000)
    n_prior: int = Field(FULL_SAMPLING["n_prior"], ge=10, le=1000)


class RoiPrior(BaseModel):
    """A LogNormal ROI prior given by its median ROI (e^μ) and σ on the log scale."""

    median: float = Field(gt=0, le=100)
    sigma: float = Field(gt=0, le=3)


class ModelSettings(BaseModel):
    """Controls, Meridian specification, priors and fit options of a run."""

    calendar: CalendarControls = Field(default_factory=CalendarControls)
    n_fourier: int = Field(2, ge=0, le=6)
    trend: bool = True
    # A 0/1 control per channel with missing workbook rows: those days otherwise look like real sales drops. Off,
    # missing channel-days count as 0 sales with no control.
    missing_row_controls: bool = True
    # Weather surprises (temperature, sunshine, precipitation vs normal) for the run's country from Open-Meteo; off by
    # default
    weather_controls: bool = False
    max_lag: int = Field(21, ge=1, le=60)
    baseline: Literal["constant", "knots", "auto"] = "constant"
    n_knots: int = Field(8, ge=2, le=60)
    hill_before_adstock: bool = False
    media_prior_type: Literal["roi", "mroi", "contribution", "coefficient"] = "roi"
    roi_prior: Literal["meridian", "attributed_roas", "total_media_contribution", "custom"] = "meridian"
    roas_prior_sigma: float = Field(0.5, gt=0, le=3)
    total_media_contribution_mean: float = Field(0.4, gt=0.01, le=0.95)
    total_media_contribution_sd: float = Field(0.2, gt=0.01, le=0.5)
    custom_roi_priors: dict[str, RoiPrior] = Field(default_factory=dict)
    sampling: SamplingSettings = Field(default_factory=SamplingSettings)
    forward_holdout: bool = True
    # Leak-free validation (`core/as_of.py`). Every scored window (test, validation, selection grid, OLS hold-outs and
    # walk-forward folds) is forecast with only what was known the day before it: its sales unknown (not even in a
    # scaling), costs that follow the outcome (commission and demand-following channels) planned from history, the user's
    # controls at their last value, normal weather and no missing-row flags. Choices made from the data (shift scan,
    # feature ladder, weather normal) use only the days before the test window, and the final model is fitted on every
    # day once the windows are scored. Off: conventional hold-outs (realised costs and controls in the window, KPI
    # scaling over the held-out days, final model = test model).
    leak_free_validation: bool = True
    # None: the profile decides (the full profile runs the grid, the others skip it)
    model_selection: bool | None = None
    selection_shifts: list[int] = Field(default_factory=lambda: [1, 2, 3])

    @field_validator("selection_shifts")
    @classmethod
    def _check_shifts(cls, value: list[int]) -> list[int]:
        shifts = sorted(set(value))
        if not 1 <= len(shifts) <= 4:
            raise ValueError("choose between 1 and 4 catalog shifts for the selection grid")
        if any(k not in SELECTION_SHIFT_CHOICES for k in shifts):
            raise ValueError(f"catalog shifts must be between {SELECTION_SHIFT_CHOICES[0]} and {SELECTION_SHIFT_CHOICES[-1]} days")
        return shifts

    def selection_enabled(self, profile: str) -> bool:
        return self.model_selection if self.model_selection is not None else profile == "full"


def sampling_for(profile: str, purpose: str = "main", settings: ModelSettings | None = None) -> dict[str, int]:
    """Draws per profile: FULL for the final model and forecasts, FAST for selection and forward fits.

    The custom profile uses its own counts for the main fits and never more than FAST for the extra fits.
    """
    if profile == "smoke":
        return dict(SMOKE_SAMPLING)
    if profile == "custom":
        custom = (settings or ModelSettings()).sampling.model_dump()
        if purpose in ("selection", "forward"):
            return {k: min(v, FAST_SAMPLING[k]) for k, v in custom.items()}
        return custom
    if purpose in ("selection", "forward"):
        return dict(FAST_SAMPLING)
    return dict(FULL_SAMPLING if profile == "full" else FAST_SAMPLING)


def _bounds(model: type[BaseModel]) -> dict[str, dict[str, float]]:
    out: dict[str, dict[str, float]] = {}
    for name, info in model.model_fields.items():
        b: dict[str, float] = {}
        for m in info.metadata:
            for attr, key in (("ge", "min"), ("gt", "min_exclusive"), ("le", "max"), ("lt", "max_exclusive")):
                if getattr(m, attr, None) is not None:
                    b[key] = getattr(m, attr)
        if b:
            out[name] = b
    return out


def settings_defaults() -> dict[str, Any]:
    """Everything the settings form needs: defaults, limits and the fixed values it shows next to them."""
    from mmm_studio.core.resample import FREQUENCIES
    from mmm_studio.core.schema import COUNTRIES, DEFAULT_COUNTRY, ChannelConfig, DataRoleConfig

    return {
        "model_settings": ModelSettings().model_dump(mode="json"),
        "holdout_days": DEFAULT_HOLDOUT_DAYS,
        "drop_trailing_isolated_days": False,
        "seed": 0,
        "in_home_shift_days": DEFAULT_IN_HOME_SHIFT,
        "min_training_days": MIN_TRAINING_DAYS,
        "profiles": {k: dict(v) for k, v in PROFILE_SAMPLING.items()},
        "limits": {
            **_bounds(ModelSettings),
            **{f"sampling.{k}": v for k, v in _bounds(SamplingSettings).items()},
            **{f"roi_prior.{k}": v for k, v in _bounds(RoiPrior).items()},
            "holdout_days": _bounds(DataRoleConfig)["holdout_days"],
            "in_home_shift_days": _bounds(ChannelConfig)["in_home_shift_days"],
            "seed": {"min": 0, "max": MAX_SEED},
        },
        "meridian_roi_prior": {"median": math.exp(MERIDIAN_ROI_PRIOR[0]), "sigma": MERIDIAN_ROI_PRIOR[1]},
        "selection_shift_choices": list(SELECTION_SHIFT_CHOICES),
        "countries": [{"code": code, "name": name} for code, name in COUNTRIES.items()],
        "country": DEFAULT_COUNTRY,
        "weather_controls": dict(WEATHER_CONTROLS),
        "frequencies": [{"value": k, "label": v[0], "help": v[1]} for k, v in FREQUENCIES.items()],
    }


ROLE_LABELS = {
    "paid": "Paid media",
    "digital_paid": "Paid media",
    "print_event": "Print event",
    "commission": "Commission / control",
    "control": "Control",
    "unpaid": "Unpaid / organic",
    "ignored": "Ignored",
}
PRIOR_TYPE_LABELS = {"roi": "ROI", "mroi": "Marginal ROI", "contribution": "Contribution", "coefficient": "Coefficient"}


def default_adstock_shape(role: str) -> str:
    return "binomial" if role == "print_event" else "geometric"


def _on_off(flag: bool) -> str:
    return "On" if flag else "Off"


def _calendar_label(cal: CalendarControls) -> str:
    names = {"weekdays": "weekdays", "holidays": "holidays", "christmas": "Christmas", "black_week": "Black Week"}
    label = ", ".join(names[g] for g in cal.groups())
    return label[:1].upper() + label[1:] if label else "None"


def _baseline_label(ms: ModelSettings) -> str:
    if ms.baseline == "knots":
        return f"Time-varying, {ms.n_knots} knots"
    if ms.baseline == "auto":
        return "Time-varying, automatic knot selection"
    return "Constant (1 knot)"


def _sampling_label(counts: dict[str, int]) -> str:
    return (
        f"{counts['n_chains']} chains × ({counts['n_adapt']:,} adapt + {counts['n_burnin']:,} burn-in + {counts['n_keep']:,} draws), "
        f"{counts['n_prior']:,} prior draws"
    )


def settings_changes(
    role_config: DataRoleConfig, seed: int = 0, profile: str = "full", holdout_default: int | None = None
) -> list[dict[str, str]]:
    """The settings of a run that differ from the defaults, as `{setting, value, default}` rows.

    Roles are compared with the role the app inferred from the workbook. The sampling profile and the Meridian switch
    are run modes, not model changes, so they are not listed (except the custom profile's draw counts).
    """
    ms, base = role_config.model_settings, ModelSettings()
    rows: list[dict[str, str]] = []

    def add(setting: str, value: str, default: str) -> None:
        if value != default:
            rows.append({"setting": setting, "value": value, "default": default})

    # A short history has a shorter default hold-out (a year of training must remain): the data's default counts
    from mmm_studio.core.resample import FREQUENCIES

    freq = role_config.columns.resample.frequency if role_config.columns and role_config.columns.resample else "auto"
    add("Data frequency", FREQUENCIES.get(freq, (freq, ""))[0], FREQUENCIES["auto"][0])
    add("Hold-out length", f"{role_config.holdout_days} days", f"{holdout_default or DEFAULT_HOLDOUT_DAYS} days")
    add("Trailing isolated days", "Left out" if role_config.drop_trailing_isolated_days else "Kept", "Kept")
    add("Random seed", str(seed), "0")
    add("Country (holidays, weather)", role_config.country_name, "Germany")
    add("Calendar controls", _calendar_label(ms.calendar), _calendar_label(base.calendar))
    add("Yearly Fourier pairs", str(ms.n_fourier), str(base.n_fourier))
    add("Linear trend", _on_off(ms.trend), _on_off(base.trend))
    add("Missing-row controls", _on_off(ms.missing_row_controls), _on_off(base.missing_row_controls))
    add("Weather controls", _on_off(ms.weather_controls), _on_off(base.weather_controls))

    for ch in role_config.channels:
        inferred = ch.inferred_role or ch.role
        add(f"Role · {ch.name}", ROLE_LABELS.get(ch.role, ch.role), ROLE_LABELS.get(inferred, inferred))
        if not ch.enabled or ch.role not in ("paid", "digital_paid", "print_event"):
            continue
        default_shape = default_adstock_shape(ch.role)
        add(f"Adstock · {ch.name}", (ch.adstock_shape or default_shape).capitalize(), default_shape.capitalize())
        add(f"Saturation · {ch.name}", "None" if ch.saturation == "none" else "Hill", "Hill")
        if ch.demand_following is not None and ch.demand_following != bool(ch.inferred_demand_following):
            add(
                f"Demand-following cost · {ch.name}",
                _on_off(ch.demand_following),
                _on_off(bool(ch.inferred_demand_following)),
            )
        if ch.role == "print_event":
            shift = ch.in_home_shift_days if ch.in_home_shift_days is not None else DEFAULT_IN_HOME_SHIFT
            add(f"In-home shift · {ch.name}", f"{shift} days", f"{DEFAULT_IN_HOME_SHIFT} days")

    add("Max lag", f"{ms.max_lag} days", f"{base.max_lag} days")
    add("Baseline", _baseline_label(ms), _baseline_label(base))
    add("Hill before adstock", _on_off(ms.hill_before_adstock), _on_off(base.hill_before_adstock))
    add("Media prior type", PRIOR_TYPE_LABELS[ms.media_prior_type], PRIOR_TYPE_LABELS[base.media_prior_type])
    if ms.media_prior_type == "roi":
        meridian_median, meridian_sigma = math.exp(MERIDIAN_ROI_PRIOR[0]), MERIDIAN_ROI_PRIOR[1]
        default_prior = f"Meridian default, LogNormal({MERIDIAN_ROI_PRIOR[0]}, {meridian_sigma})"
        if ms.roi_prior == "attributed_roas":
            add("ROI prior", f"Centred on attributed ROAS, σ {ms.roas_prior_sigma:g}", default_prior)
        elif ms.roi_prior == "total_media_contribution":
            tmc_mean = float(getattr(ms, "total_media_contribution_mean", 0.4))
            tmc_sd = float(getattr(ms, "total_media_contribution_sd", 0.2))
            add(
                "ROI prior",
                f"Total media contribution (mean {100 * tmc_mean:.0f}%, SD {100 * tmc_sd:.0f}%)",
                default_prior,
            )
        elif ms.roi_prior == "custom" and ms.custom_roi_priors:
            names = {c.slug: c.name for c in role_config.channels}
            for slug, p in sorted(ms.custom_roi_priors.items()):
                add(
                    f"ROI prior · {names.get(slug, slug)}",
                    f"median {p.median:.2f}, σ {p.sigma:g}",
                    f"median {meridian_median:.2f}, σ {meridian_sigma:g}",
                )

    if profile == "custom":
        add("Sampling draws", _sampling_label(ms.sampling.model_dump()), _sampling_label(FULL_SAMPLING))
    add("Leak-free validation", _on_off(ms.leak_free_validation), _on_off(base.leak_free_validation))
    add("Forward hold-out refit", _on_off(ms.forward_holdout), _on_off(base.forward_holdout))
    add("Model-selection grid", _on_off(ms.selection_enabled(profile)), _on_off(base.selection_enabled(profile)))
    if ms.selection_enabled(profile):
        add("Selection catalog shifts", ", ".join(map(str, ms.selection_shifts)), ", ".join(map(str, base.selection_shifts)))
    return rows


def run_config_errors(role_config: DataRoleConfig, modelling_days: int | None) -> list[str]:
    """Problems that make a run's settings unusable, in plain words (empty when the run can start).

    `modelling_days` is the length of the modelling panel from the workbook profile.
    """
    errors: list[str] = []
    if modelling_days is not None:
        training_days = int(modelling_days) - int(role_config.holdout_days)
        if training_days < MIN_TRAINING_DAYS:
            errors.append(
                f"A {role_config.holdout_days}-day hold-out leaves {training_days} training days of {modelling_days}; at "
                f"least {MIN_TRAINING_DAYS} are needed. Shorten the hold-out or upload a longer history."
            )
    media = [c.slug for c in role_config.channels if c.enabled and c.role in ("paid", "digital_paid", "print_event")]
    if not media:
        errors.append("No channel is modelled as paid media or a print event; Meridian needs at least one media channel.")
    ms = role_config.model_settings
    if ms.media_prior_type == "roi" and ms.roi_prior == "attributed_roas" and not role_config.has_channel_sales:
        errors.append(
            "ROI priors centred on attributed ROAS need sales per channel, and this data has none; choose another ROI prior."
        )
    if ms.media_prior_type == "roi" and ms.roi_prior == "custom":
        unknown = sorted(set(ms.custom_roi_priors) - set(media))
        if unknown:
            errors.append(f"Custom ROI priors are set for {', '.join(unknown)}, which are not media channels of this run.")
    return errors
