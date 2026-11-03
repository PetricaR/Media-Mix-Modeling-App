"""Run settings a user can change before a run starts. Every default is the value notebooks 01–05 use.

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
# A run needs at least one full seasonal cycle of training days before its hold-out
MIN_TRAINING_DAYS = 365
MAX_SEED = 2**31 - 1
SELECTION_SHIFT_CHOICES: tuple[int, ...] = tuple(range(8))
CALENDAR_GROUPS: tuple[str, ...] = ("weekdays", "holidays", "christmas", "black_week")


class CalendarControls(BaseModel):
    """Which calendar dummies enter the OLS benchmark and Meridian (weekdays, German holidays, 20–31 Dec with the
    24 / 31 Dec half-days, Black Week)."""

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
    # A 0/1 control per channel with missing workbook rows: those days otherwise look like real sales drops. The
    # notebooks have no such control; switch it off to reproduce them exactly.
    missing_row_controls: bool = True
    max_lag: int = Field(21, ge=1, le=60)
    baseline: Literal["constant", "knots", "auto"] = "constant"
    n_knots: int = Field(8, ge=2, le=60)
    hill_before_adstock: bool = False
    media_prior_type: Literal["roi", "mroi", "contribution", "coefficient"] = "roi"
    roi_prior: Literal["meridian", "attributed_roas", "custom"] = "meridian"
    roas_prior_sigma: float = Field(0.5, gt=0, le=3)
    custom_roi_priors: dict[str, RoiPrior] = Field(default_factory=dict)
    sampling: SamplingSettings = Field(default_factory=SamplingSettings)
    forward_holdout: bool = True
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
    """Draws per profile. As in the notebooks: FULL for the final model and forecasts, FAST for selection and forward fits.

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
    from mmm_studio.core.schema import ChannelConfig, DataRoleConfig

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


def settings_changes(role_config: DataRoleConfig, seed: int = 0, profile: str = "full") -> list[dict[str, str]]:
    """The settings of a run that differ from the notebook defaults, as `{setting, value, default}` rows.

    Roles are compared with the role the app inferred from the workbook. The sampling profile and the Meridian switch
    are run modes, not model changes, so they are not listed (except the custom profile's draw counts).
    """
    ms, base = role_config.model_settings, ModelSettings()
    rows: list[dict[str, str]] = []

    def add(setting: str, value: str, default: str) -> None:
        if value != default:
            rows.append({"setting": setting, "value": value, "default": default})

    add("Hold-out length", f"{role_config.holdout_days} days", f"{DEFAULT_HOLDOUT_DAYS} days")
    add("Trailing isolated days", "Left out" if role_config.drop_trailing_isolated_days else "Kept", "Kept")
    add("Random seed", str(seed), "0")
    add("Calendar controls", _calendar_label(ms.calendar), _calendar_label(base.calendar))
    add("Yearly Fourier pairs", str(ms.n_fourier), str(base.n_fourier))
    add("Linear trend", _on_off(ms.trend), _on_off(base.trend))
    add("Missing-row controls", _on_off(ms.missing_row_controls), _on_off(base.missing_row_controls))

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
    if ms.media_prior_type == "roi" and ms.roi_prior == "custom":
        unknown = sorted(set(ms.custom_roi_priors) - set(media))
        if unknown:
            errors.append(f"Custom ROI priors are set for {', '.join(unknown)}, which are not media channels of this run.")
    return errors
