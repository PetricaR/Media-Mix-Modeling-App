"""Gemini's recommended run settings: what it is told, which settings it reviews, and how its answer is checked.

Gemini reads the run's workbook profile and, once the run (or an earlier run on the same workbook) has results, the
analysis behind them: data quality, MMM readiness, correlations, the catalog study, preparation, the OLS benchmark,
Meridian's validation, the selection and sensitivity grids, convergence and the ROI identification. It reviews every
setting the form offers and answers in JSON with a summary and its best value for each active setting (the current
value when it should stay), each with a reason and a confidence.

Nothing it says is applied blindly. Each value is parsed by its setting's type and bounds, the changes are applied in
a fixed order (run mode, then channel roles, then the rest) to the current settings, and every step must pass the same
checks as a run start. A change that fails is returned as rejected with the reason, never adapted, and the coverage
says which active settings Gemini did not review. Applying the accepted changes is the user's choice, in the form;
nothing is stored or started here.
"""

from __future__ import annotations

import copy
import json
import math
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError

from mmm_studio.core.model_settings import (
    DEFAULT_HOLDOUT_DAYS,
    DEFAULT_IN_HOME_SHIFT,
    FULL_SAMPLING,
    MERIDIAN_ROI_PRIOR,
    MIN_TRAINING_DAYS,
    PRIOR_TYPE_LABELS,
    ROLE_LABELS,
    ModelSettings,
    default_adstock_shape,
    run_config_errors,
    settings_changes,
)
from mmm_studio.core.resample import FREQUENCIES
from mmm_studio.core.schema import DataRoleConfig
from mmm_studio.explain.context import GLOSSARY, ExplainTable, clean_text, describe_table, fmt_number

PROMPT_VERSION = "recommend-v3"
TABS: tuple[str, ...] = ("run", "data", "channels", "model", "priors", "sampling")
Goal = Literal["balanced", "accuracy", "speed"]

MAX_PROMPT_CHARS = 90_000
MAX_TABLE_ROWS = 30
# The result pages Gemini reads, most decisive first, with a character budget each
EVIDENCE_SECTIONS: tuple[tuple[str, int], ...] = (
    ("meridian_model", 9_000),
    ("drivers_roi", 6_000),
    ("benchmark", 5_000),
    ("forecast_scenarios", 2_500),
    ("preparation", 7_000),
    ("explore_readiness", 5_000),
    ("explore_catalog", 3_500),
    ("explore_correlations", 4_000),
    ("explore_efficiency", 3_500),
    ("data_quality", 5_000),
    ("explore_target", 3_000),
)
MEDIA_ROLES = ("paid", "digital_paid", "print_event")
OFFERED_ROLES = ("paid", "print_event", "commission", "unpaid", "ignored")
MERIDIAN_MEDIAN = math.exp(MERIDIAN_ROI_PRIOR[0])
MERIDIAN_SIGMA = MERIDIAN_ROI_PRIOR[1]
SAMPLING_COUNTS: dict[str, str] = {
    "n_chains": "MCMC chains (R-hat compares them)",
    "n_adapt": "adaptation draws per chain",
    "n_burnin": "burn-in draws per chain (discarded)",
    "n_keep": "kept draws per chain (used for every result)",
    "n_prior": "prior draws (prior vs posterior checks)",
}
PROFILE_LABELS = {
    "full": "Full (4 chains × 1,000 adapt + 500 burn-in + 1,000 draws, plus the selection grid; about 15–25 min)",
    "quick": "Quick (2 chains × 300 adapt + 200 burn-in + 200 draws, no selection grid; about 5–8 min)",
    "smoke": "Smoke test (2 chains × 50 draws; checks the pipeline, results not converged; about 4 min)",
    "custom": "Custom draws (the sampling.* counts below for the final, forecast and back-test fits; extra fits never above Quick)",
}

GOALS: dict[str, str] = {
    "balanced": (
        "Best overall: maximise out-of-sample accuracy and ROI identification first, then run-time performance. "
        "Every extra fit or extra draw must earn its time with evidence."
    ),
    "accuracy": (
        "Maximum accuracy: the most accurate, best-identified and best-validated model, whatever the run time. More "
        "draws, the selection grid and the forward hold-out are all fine when they help."
    ),
    "speed": (
        "Fastest useful run: the shortest run whose results can still be read and trusted (converged, hold-out "
        "validated). Prefer fewer draws and no extra fits unless the evidence shows they matter."
    ),
}

SYSTEM_INSTRUCTION = f"""\
You are the modelling adviser of MMM Studio, a marketing-mix-modelling app: a Google Meridian Bayesian MMM (one \
national model on daily data) with an OLS benchmark, fitted on a retailer's daily online sales and marketing spend. \
You choose the best value of every run setting for the user's goal, using only the evidence in the prompt.

How to decide:
- Review every setting listed as active, run-wide and per channel, and give your best value for each one: the \
current value when it should stay, a new value when it should change. Settings listed as inactive (a switch they \
depend on is off) need an entry only when your answer switches that dependency on.
- Change a setting when evidence in the prompt supports it (a validation score, a selection or sensitivity row, an \
identification flag, a convergence figure, a data-quality finding, a correlation, a shift scan) or, for run-time \
settings, when the goal does. Name that evidence with its numbers. Without evidence, keep the current value: the \
defaults are the validated reference settings.
- Judge models out of sample: forward hold-out (validation window) MAE / mean and the selection and sensitivity rows \
(lower is better; the app's target is at most 15 %), R², convergence (R-hat below 1.2) and identified ROIs (narrow \
intervals, not "mostly prior"). Never prefer a setting because it improves the training fit alone.
- The hold-out (test window) score is the final check of the chosen settings. Quote it, but never change a setting to \
improve it: a test score used to choose settings stops being an honest estimate of forecast accuracy.
- Keep leak_free_validation on. Off, every score reads the window's realised commission and click costs, its controls \
and its sales (in Meridian's KPI scaling), so scores look better while the model is not; switch it off only to \
reproduce conventional hold-out scores, and never compare scores across the two modes.
- Selection and sensitivity rows are fast fits trained before the validation window and scored on it: they compare \
settings with each other, not with the final model's hold-out score.
- The results were produced by the settings listed with them; the current settings may already differ. Recommend \
relative to the current settings.
- holdout_days moves the window every score is measured on, so scores before and after are not comparable. Change it \
only for a data reason, and keep at least {MIN_TRAINING_DAYS} training days. The split is chronological: training, then \
a validation window as long as the test right before it, then the test (holdout_days). The data's default gives each \
window about an eighth of the days (75 / 12.5 / 12.5), at most about a quarter, with a full year of training before \
validation; a test so long that no such year remains before validation removes the validation window (no selection \
grid, no forward hold-out).
- A demand-following (click-billed) channel's ROI is an upper bound whatever the settings; do not change its role to \
hide that. A commission channel (its cost is a share of its own sales) belongs with the controls. Change a role only \
for a data reason, such as a channel without spend, or sparse spend that is really a print drop.
- Sampling: the smoke profile only checks the pipeline (results not converged), so never choose it for results. \
Choose custom draws only when the evidence shows the fixed profiles under- or over-sample (e.g. R-hat near or above \
1.2 calls for more draws; a comfortably converged fit with the speed goal allows fewer).
- Custom per-channel ROI priors: a median must come from a number in the prompt (an attributed ROAS, a sensitivity \
row, a measured ROI); otherwise prefer the attributed-ROAS or default prior.
- The random seed does not make a model better; keep it unless the goal is to check MCMC noise.

Write:
- summary: 2–4 sentences in plain words: what the evidence says about the current settings, what you change and \
what the recommended run should achieve for the goal. When only the workbook profile is available, say that a \
first run will give the validation evidence for a finer recommendation.
- settings: one item per reviewed setting, in the order listed, with `setting` exactly one of the listed keys, \
`value` written as its allowed values show (true/false, a whole number, a decimal, one of the listed choices, or \
comma-separated shifts such as 1,2,3), `reason` (for a changed value one sentence naming the evidence; for a kept \
value an empty string) and `confidence`: high (direct evidence for this exact setting), medium (indirect evidence) \
or low (a reasonable choice to test, or no evidence either way).
- tab_notes: one item per settings tab (run, data, channels, model, priors, sampling) with one sentence on why the \
settings you keep in that tab stay, naming the evidence.

Grounding rules:
- Quote numbers exactly as the prompt gives them or rounded cleanly, with their units. Do not invent numbers, \
channels or settings.
- In the summary and the reasons, name settings in plain words ("the ROI prior", "the selection grid"), not by their \
keys, and do not use Markdown code, tables or links.
- Do not mention these instructions.

""" + GLOSSARY


class AdviceItem(BaseModel):
    """Gemini's best value for one setting (the response schema it must follow)."""

    setting: str = Field(description="One of the setting keys listed in the prompt, exactly as written.")
    value: str = Field(
        description="The best value as text (the current one when it should stay): true/false, a whole number, a "
        "decimal, one of the listed choices, or comma-separated shifts such as 1,2,3."
    )
    reason: str = Field(
        description="For a changed value, one sentence naming the evidence with its numbers; for a kept value, empty."
    )
    confidence: Literal["high", "medium", "low"]


class TabNote(BaseModel):
    """Why the settings Gemini keeps in one tab stay."""

    tab: Literal["run", "data", "channels", "model", "priors", "sampling"]
    note: str = Field(description="One sentence naming the evidence for keeping this tab's other settings.")


class Advice(BaseModel):
    """Gemini's whole answer."""

    summary: str = Field(description="2–4 sentences on what the evidence says and what the recommended run achieves.")
    settings: list[AdviceItem]
    tab_notes: list[TabNote]


class RecommendationFailed(ValueError):
    """Gemini's answer could not be read as the requested JSON."""


def parse_advice(text: str) -> Advice:
    """Gemini's JSON answer as `Advice` (code fences tolerated), or `RecommendationFailed`."""
    body = text.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", body, flags=re.DOTALL)
    if fenced:
        body = fenced.group(1)
    try:
        return Advice.model_validate(json.loads(body))
    except (json.JSONDecodeError, ValidationError) as exc:
        raise RecommendationFailed("Gemini's answer was not in the expected format. Try again.") from exc


# ── Settings Gemini reviews ─────────────────────────────────────────────────────────────────────────────────────


@dataclass
class State:
    """The settings being reviewed: run mode, seed and the role config (as JSON), the same shape the form submits."""

    profile: str
    include_meridian: bool
    rc: dict[str, Any]
    seed: int = 0

    @property
    def ms(self) -> dict[str, Any]:
        return self.rc["model_settings"]

    def channel(self, slug: str) -> dict[str, Any]:
        return next(c for c in self.rc["channels"] if c.get("slug") == slug)

    def copy(self) -> State:
        return State(self.profile, self.include_meridian, copy.deepcopy(self.rc), self.seed)


@dataclass(frozen=True)
class SettingSpec:
    key: str
    label: str
    tab: str  # the settings form's tab: run (the Meridian switch) | data | channels | model | priors | sampling
    kind: Literal["bool", "int", "float", "choice", "shifts"]
    help: str
    get: Callable[[State], Any]
    set: Callable[[State, Any], None]
    choices: dict[str, str] = field(default_factory=dict)  # value -> label
    bounds: dict[str, float] = field(default_factory=dict)
    unit: str = ""
    # Why the setting does nothing in this state (e.g. knots without a time-varying baseline), else None
    requires: Callable[[State], str | None] | None = None
    meridian_only: bool = False


def inactive_reason(spec: SettingSpec, state: State) -> str | None:
    """Why `spec` has no effect in `state` (None when it is active)."""
    if spec.meridian_only and not state.include_meridian:
        return "applies only when the Meridian model is fitted"
    return spec.requires(state) if spec.requires else None


def _ms_spec(key: str, label: str, tab: str, kind: Any, help: str, limits: dict[str, Any], **kw: Any) -> SettingSpec:
    def setter(s: State, v: Any) -> None:
        s.ms[key] = v

    kw.setdefault("meridian_only", tab in ("model", "priors", "sampling"))
    return SettingSpec(key, label, tab, kind, help, lambda s: s.ms[key], setter, bounds=limits.get(key, {}), **kw)


def _calendar_spec(group: str, label: str) -> SettingSpec:
    def setter(s: State, v: Any) -> None:
        s.ms["calendar"][group] = v

    return SettingSpec(
        f"calendar.{group}",
        f"Calendar controls · {label}",
        "data",
        "bool",
        f"0/1 controls for {label} in Meridian and the OLS benchmark.",
        lambda s: s.ms["calendar"][group],
        setter,
    )


def _sampling_spec(count: str, help: str, limits: dict[str, Any]) -> SettingSpec:
    def setter(s: State, v: Any) -> None:
        s.ms["sampling"][count] = v

    return SettingSpec(
        f"sampling.{count}",
        f"Custom draws · {count.removeprefix('n_')}",
        "sampling",
        "int",
        f"Custom profile: {help}.",
        lambda s: s.ms["sampling"][count],
        setter,
        bounds=limits.get(f"sampling.{count}", {}),
        requires=lambda s: None if s.profile == "custom" else "used only with the custom sampling profile",
        meridian_only=True,
    )


def _roi_prior_in_use(s: State) -> str | None:
    return None if s.ms["media_prior_type"] == "roi" else "ROI priors apply only when the media prior type is ROI"


def _prior_option(option: str, name: str) -> Callable[[State], str | None]:
    def check(s: State) -> str | None:
        return _roi_prior_in_use(s) or (None if s.ms["roi_prior"] == option else f"used only with the {name} ROI prior")

    return check


def _selection_on(s: State) -> bool:
    flag = s.ms.get("model_selection")
    return bool(flag) if flag is not None else s.profile == "full"


def _set_roi_prior(s: State, v: Any) -> None:
    """Custom priors start from Meridian's default for every media channel, as the form's preset does."""
    s.ms["roi_prior"] = v
    if v == "custom":
        priors = s.ms.setdefault("custom_roi_priors", {})
        for ch in s.rc["channels"]:
            if ch.get("enabled", True) and ch.get("role") in MEDIA_ROLES:
                priors.setdefault(ch["slug"], {"median": MERIDIAN_MEDIAN, "sigma": MERIDIAN_SIGMA})


def _put(target: str) -> Callable[[State, Any], None]:
    def put(s: State, v: Any) -> None:
        if target in ("profile", "include_meridian", "seed"):
            setattr(s, target, v)
        elif target == "model_selection":
            s.ms["model_selection"] = v
        else:
            s.rc[target] = v

    return put


def _get_frequency(s: State) -> str:
    return ((s.rc.get("columns") or {}).get("resample") or {}).get("frequency") or "auto"


def _set_frequency(s: State, v: Any) -> None:
    cols = s.rc.setdefault("columns", {})
    res = cols.setdefault("resample", {"frequency": "auto", "period_label": "start", "fill_missing_dates": False, "columns": []})
    res["frequency"] = v


def global_specs(limits: dict[str, Any]) -> list[SettingSpec]:
    """Every run-wide setting, in the order changes are applied (channel roles go after the first group)."""
    return [
        SettingSpec(
            "include_meridian",
            "Fit the Google Meridian MMM",
            "run",
            "bool",
            "Off: the run covers only the data audit, EDA, preparation and the OLS benchmark (no ROI, optimiser or forecast).",
            lambda s: s.include_meridian,
            _put("include_meridian"),
        ),
        SettingSpec(
            "profile",
            "Sampling profile",
            "sampling",
            "choice",
            "How thoroughly Meridian samples; more draws take longer and converge more reliably.",
            lambda s: s.profile,
            _put("profile"),
            choices=dict(PROFILE_LABELS),
            meridian_only=True,
        ),
        *(_sampling_spec(k, h, limits) for k, h in SAMPLING_COUNTS.items()),
        SettingSpec(
            "resample.frequency",
            "Data frequency",
            "data",
            "choice",
            "How the uploaded dates are read and resampled onto the daily model grid (weekly and monthly combine finer rows by period and split each period into days).",
            _get_frequency,
            _set_frequency,
            choices={k: v[0] for k, v in FREQUENCIES.items()},
        ),
        SettingSpec(
            "holdout_days",
            "Hold-out length",
            "data",
            "int",
            f"The test window: the last days, hidden from the fit and scored out of sample; the validation window is "
            f"as long and sits right before it (at least {MIN_TRAINING_DAYS} training days must remain before each).",
            lambda s: s.rc["holdout_days"],
            _put("holdout_days"),
            bounds=limits.get("holdout_days", {}),
            unit="days",
        ),
        SettingSpec(
            "drop_trailing_isolated_days",
            "Trailing isolated days",
            "data",
            "bool",
            "true leaves out trailing dates separated from the rest of the history (see the workbook profile).",
            lambda s: s.rc["drop_trailing_isolated_days"],
            _put("drop_trailing_isolated_days"),
            choices={"true": "Left out", "false": "Kept"},
        ),
        SettingSpec(
            "seed",
            "Random seed",
            "data",
            "int",
            "Seed of every MCMC fit; another seed shows how much results move with MCMC noise.",
            lambda s: s.seed,
            _put("seed"),
            bounds=limits.get("seed", {}),
        ),
        _calendar_spec("weekdays", "weekdays"),
        _calendar_spec("holidays", "German holidays"),
        _calendar_spec("christmas", "Christmas (20–31 Dec, 24/31 Dec half-days)"),
        _calendar_spec("black_week", "Black Week"),
        _ms_spec("n_fourier", "Yearly Fourier pairs", "data", "int", "Sine/cosine pairs for yearly seasonality.", limits),
        _ms_spec("trend", "Linear trend", "data", "bool", "A linear trend control.", limits),
        _ms_spec(
            "missing_row_controls",
            "Missing-row controls",
            "data",
            "bool",
            "A 0/1 control per channel with missing workbook rows, so those days are not read as sales drops.",
            limits,
        ),
        _ms_spec(
            "weather_controls",
            "Weather controls",
            "data",
            "bool",
            "Temperature, sunshine and precipitation vs their seasonal normal for the run's country (Open-Meteo); a forecast assumes normal weather.",
            limits,
        ),
        _ms_spec("max_lag", "Max lag", "model", "int", "Days a day's spend can keep driving sales (adstock window).", limits, unit="days"),
        _ms_spec(
            "baseline",
            "Baseline",
            "model",
            "choice",
            "The sales the media and controls do not explain: constant, or time-varying with knots.",
            limits,
            choices={
                "constant": "Constant (1 knot)",
                "knots": "Time-varying (n_knots knots)",
                "auto": "Automatic knots (reads the whole series, hold-out included; slow on daily data)",
            },
        ),
        _ms_spec(
            "n_knots",
            "Baseline knots",
            "model",
            "int",
            "Knots of the time-varying baseline.",
            limits,
            requires=lambda s: None if s.ms["baseline"] == "knots" else "used only with the time-varying (knots) baseline",
        ),
        _ms_spec(
            "hill_before_adstock",
            "Hill before adstock",
            "model",
            "bool",
            "true saturates each day's spend before carry-over; false carries over first.",
            limits,
        ),
        _ms_spec(
            "media_prior_type",
            "Media prior type",
            "priors",
            "choice",
            "What the media prior is placed on (Meridian advises against coefficient priors).",
            limits,
            choices={k: PRIOR_TYPE_LABELS[k] for k in ("roi", "mroi", "contribution", "coefficient")},
        ),
        SettingSpec(
            "roi_prior",
            "ROI prior",
            "priors",
            "choice",
            "Where the ROI prior is centred.",
            lambda s: s.ms["roi_prior"],
            _set_roi_prior,
            choices={
                "meridian": "Meridian default, LogNormal(0.2, 0.9)",
                "attributed_roas": "Centred on each channel's attributed ROAS (training days only)",
                "total_media_contribution": "From a total media contribution share",
                "custom": "Custom median and σ per media channel (custom_roi_priors.*)",
            },
            requires=_roi_prior_in_use,
            meridian_only=True,
        ),
        _ms_spec(
            "roas_prior_sigma",
            "ROAS prior σ",
            "priors",
            "float",
            "Width (σ on the log scale) of the ROAS-centred prior.",
            limits,
            requires=_prior_option("attributed_roas", "attributed-ROAS"),
        ),
        _ms_spec(
            "total_media_contribution_mean",
            "Total media contribution mean",
            "priors",
            "float",
            "Expected share of sales driven by media (a decimal share).",
            limits,
            requires=_prior_option("total_media_contribution", "total media contribution"),
        ),
        _ms_spec(
            "total_media_contribution_sd",
            "Total media contribution SD",
            "priors",
            "float",
            "Uncertainty of that share (a decimal share).",
            limits,
            requires=_prior_option("total_media_contribution", "total media contribution"),
        ),
        _ms_spec(
            "leak_free_validation",
            "Leak-free validation",
            "model",
            "bool",
            "Score every window as a forecast from a plan made the day before it (sales, outcome-driven costs, controls "
            "and weather of the window unseen) and fit the final model on every day; off scores conventional "
            "hold-outs that see the window's realised costs.",
            limits,
            meridian_only=False,  # the OLS benchmark and the data-preparation scans follow it too
        ),
        _ms_spec(
            "forward_holdout",
            "Forward hold-out refit",
            "sampling",
            "bool",
            "An extra fit trained only before the quarter preceding the hold-out and scored on it.",
            limits,
        ),
        SettingSpec(
            "model_selection",
            "Model-selection grid",
            "sampling",
            "bool",
            "Fast fits comparing catalog shifts × commission channels as media or control, plus prior sensitivity.",
            _selection_on,
            _put("model_selection"),
            meridian_only=True,
        ),
        _ms_spec(
            "selection_shifts",
            "Selection catalog shifts",
            "sampling",
            "shifts",
            "Print in-home shifts (days) the selection grid compares.",
            limits,
            requires=lambda s: None if _selection_on(s) else "used only when the model-selection grid runs",
        ),
    ]


N_RUN_MODE = 2 + len(SAMPLING_COUNTS)  # include_meridian, profile and the custom counts come before channel roles


def _channel_getter(slug: str, key: str, default: Callable[[dict[str, Any]], Any]) -> Callable[[State], Any]:
    def get(s: State) -> Any:
        value = s.channel(slug).get(key)
        return default(s.channel(slug)) if value is None else value

    return get


def _channel_setter(slug: str, key: str) -> Callable[[State, Any], None]:
    def put(s: State, v: Any) -> None:
        s.channel(slug)[key] = v

    return put


def _set_role(slug: str) -> Callable[[State, Any], None]:
    """A new role resets the channel's transforms to the role's defaults, as the form's role picker does."""

    def put(s: State, role: Any) -> None:
        s.channel(slug).update(
            role=role,
            enabled=role != "ignored",
            adstock_shape=None,
            saturation="hill",
            in_home_shift_days=DEFAULT_IN_HOME_SHIFT if role == "print_event" else 0,
        )
        if role not in MEDIA_ROLES:
            s.ms.get("custom_roi_priors", {}).pop(slug, None)  # a prior for a non-media channel blocks the run

    return put


def _needs_role(slug: str, roles: tuple[str, ...], what: str) -> Callable[[State], str | None]:
    def check(s: State) -> str | None:
        return None if s.channel(slug).get("role") in roles else f"applies only to {what}"

    return check


def _prior_getter(slug: str, part: str) -> Callable[[State], Any]:
    default = MERIDIAN_MEDIAN if part == "median" else MERIDIAN_SIGMA

    def get(s: State) -> Any:
        return float((s.ms.get("custom_roi_priors") or {}).get(slug, {}).get(part, default))

    return get


def _prior_setter(slug: str, part: str) -> Callable[[State, Any], None]:
    def put(s: State, v: Any) -> None:
        priors = s.ms.setdefault("custom_roi_priors", {})
        current = priors.get(slug) or {"median": MERIDIAN_MEDIAN, "sigma": MERIDIAN_SIGMA}
        priors[slug] = {**current, part: v}

    return put


def _custom_prior_check(slug: str) -> Callable[[State], str | None]:
    media = _needs_role(slug, MEDIA_ROLES, "media channels")
    custom = _prior_option("custom", "custom")

    def check(s: State) -> str | None:
        return media(s) or custom(s)

    return check


def channel_specs(channels: list[dict[str, Any]], limits: dict[str, Any]) -> tuple[list[SettingSpec], list[SettingSpec]]:
    """(role settings, other per-channel settings); roles are applied right after the run mode."""
    roles: list[SettingSpec] = []
    others: list[SettingSpec] = []
    for ch in channels:
        slug, name = ch["slug"], ch["name"]
        roles.append(
            SettingSpec(
                f"channels.{slug}.role",
                f"Role · {name}",
                "channels",
                "choice",
                "How the channel enters the model.",
                lambda s, slug=slug: s.channel(slug)["role"],
                _set_role(slug),
                choices={r: ROLE_LABELS[r] for r in OFFERED_ROLES},
            )
        )
        others += [
            SettingSpec(
                f"channels.{slug}.adstock_shape",
                f"Adstock · {name}",
                "channels",
                "choice",
                "Carry-over shape: geometric decays from day 0, binomial peaks later.",
                _channel_getter(slug, "adstock_shape", lambda c: default_adstock_shape(c["role"])),
                _channel_setter(slug, "adstock_shape"),
                choices={"geometric": "Geometric", "binomial": "Binomial (delayed peak)"},
                requires=_needs_role(slug, MEDIA_ROLES, "media channels"),
                meridian_only=True,
            ),
            SettingSpec(
                f"channels.{slug}.saturation",
                f"Saturation · {name}",
                "channels",
                "choice",
                "Diminishing returns (Hill) or none.",
                _channel_getter(slug, "saturation", lambda _c: "hill"),
                _channel_setter(slug, "saturation"),
                choices={"hill": "Hill", "none": "None"},
                requires=_needs_role(slug, MEDIA_ROLES, "media channels"),
                meridian_only=True,
            ),
            SettingSpec(
                f"channels.{slug}.in_home_shift_days",
                f"In-home shift · {name}",
                "channels",
                "int",
                "Days from the send-out (cost) date to the in-home date (Meridian and the OLS benchmark).",
                _channel_getter(slug, "in_home_shift_days", lambda _c: DEFAULT_IN_HOME_SHIFT),
                _channel_setter(slug, "in_home_shift_days"),
                bounds=limits.get("in_home_shift_days", {}),
                unit="days",
                requires=_needs_role(slug, ("print_event",), "print events"),
            ),
            SettingSpec(
                f"channels.{slug}.demand_following",
                f"Demand-following cost · {name}",
                "channels",
                "bool",
                "Cost billed per click that follows demand: its ROI is reported as an upper bound and the default "
                "optimiser holds it fixed.",
                _channel_getter(slug, "demand_following", lambda c: bool(c.get("inferred_demand_following"))),
                _channel_setter(slug, "demand_following"),
                requires=_needs_role(slug, ("paid", "digital_paid"), "paid media channels"),
                meridian_only=True,
            ),
            *(
                SettingSpec(
                    f"custom_roi_priors.{slug}.{part}",
                    f"ROI prior · {name} · {'median' if part == 'median' else 'σ'}",
                    "priors",
                    "float",
                    "Custom ROI prior: the median ROI (e^μ)" if part == "median" else "Custom ROI prior: σ on the log scale",
                    _prior_getter(slug, part),
                    _prior_setter(slug, part),
                    bounds=limits.get(f"roi_prior.{part}", {}),
                    requires=_custom_prior_check(slug),
                    meridian_only=True,
                )
                for part in ("median", "sigma")
            ),
        ]
    return roles, others


def all_specs(state: State, limits: dict[str, Any]) -> dict[str, SettingSpec]:
    """Every setting by key, in the order changes are applied: run mode, channel roles, the rest."""
    specs = global_specs(limits)
    roles, others = channel_specs(state.rc["channels"], limits)
    ordered = specs[:N_RUN_MODE] + roles + specs[N_RUN_MODE:] + others
    return {s.key: s for s in ordered}


# ── Values ──────────────────────────────────────────────────────────────────────────────────────────────────────

_TRUE = {"true", "on", "yes", "1"}
_FALSE = {"false", "off", "no", "0"}


def _bounds_text(b: dict[str, float]) -> str:
    lo = f"from {fmt_number(b['min'])}" if "min" in b else f"above {fmt_number(b['min_exclusive'])}" if "min_exclusive" in b else ""
    hi = f"up to {fmt_number(b['max'])}" if "max" in b else f"below {fmt_number(b['max_exclusive'])}" if "max_exclusive" in b else ""
    return " ".join(p for p in (lo, hi) if p)


def _check_bounds(value: float, b: dict[str, float]) -> None:
    ok = (
        ("min" not in b or value >= b["min"])
        and ("min_exclusive" not in b or value > b["min_exclusive"])
        and ("max" not in b or value <= b["max"])
        and ("max_exclusive" not in b or value < b["max_exclusive"])
    )
    if not ok:
        raise ValueError(f"must be {_bounds_text(b)}")


def parse_value(spec: SettingSpec, raw: Any) -> Any:
    """The typed value of `raw` for `spec`, or a ValueError saying what is allowed."""
    text = str(raw).strip().strip("\"'").strip()
    low = text.lower()
    if spec.kind == "bool":
        if low in _TRUE:
            return True
        if low in _FALSE:
            return False
        raise ValueError("must be true or false")
    if spec.kind in ("int", "float"):
        match = re.fullmatch(r"(-?\d+(?:\.\d+)?)\s*(?:days?|d)?", low.replace(",", ""))
        if not match:
            raise ValueError("must be a number")
        number = float(match.group(1))
        if spec.kind == "int":
            if number != int(number):
                raise ValueError("must be a whole number")
            number = int(number)
        _check_bounds(number, spec.bounds)
        return number
    if spec.kind == "choice":
        key = re.sub(r"[\s-]+", "_", low)
        if key not in spec.choices:
            raise ValueError(f"must be one of {', '.join(spec.choices)}")
        return key
    shifts = [int(n) for n in re.findall(r"-?\d+", text)]
    try:
        return ModelSettings(selection_shifts=shifts).selection_shifts
    except ValidationError as exc:
        raise ValueError(_first_error(exc)) from exc


def display(spec: SettingSpec, value: Any) -> str:
    """A value as the form shows it."""
    if spec.kind == "bool":
        if spec.choices:
            return spec.choices["true" if value else "false"]
        return "On" if value else "Off"
    if spec.kind == "choice":
        label = spec.choices.get(value) or ROLE_LABELS.get(value) or str(value)
        return label.split(" (")[0] if spec.key == "profile" else label
    if spec.kind == "shifts":
        return ", ".join(str(v) for v in value) + " days"
    number = fmt_number(float(value)) if spec.kind == "float" else (f"{value:,}" if spec.key.startswith("sampling.") else str(value))
    return f"{number} {spec.unit}".strip()


def _same(a: Any, b: Any) -> bool:
    if isinstance(a, float) or isinstance(b, float):
        return isinstance(a, (int, float)) and isinstance(b, (int, float)) and math.isclose(a, b, rel_tol=1e-9)
    return a == b


def _first_error(exc: ValidationError) -> str:
    errors = exc.errors()
    msg = str(errors[0].get("msg", "invalid value")) if errors else "invalid value"
    return msg.removeprefix("Value error, ")


# ── Review ──────────────────────────────────────────────────────────────────────────────────────────────────────


@dataclass
class Review:
    """Gemini's answer checked against the settings: what to change, what it keeps, what failed, what it skipped."""

    changes: list[dict[str, Any]]
    kept: list[dict[str, Any]]
    rejected: list[dict[str, Any]]
    coverage: dict[str, Any]
    tab_notes: dict[str, str]


def review(advice: Advice, state: State, modelling_days: int | None, limits: dict[str, Any]) -> Review:
    """Check every item of `advice` against `state`. Changes carry the typed value the form applies, in order."""
    specs = all_specs(state, limits)
    order = {k: i for i, k in enumerate(specs)}
    changes: list[dict[str, Any]] = []
    kept: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    seen: set[str] = set()
    base_errors = set(run_config_errors(DataRoleConfig.model_validate(state.rc), modelling_days))

    ranked = sorted(enumerate(advice.settings), key=lambda p: (order.get(p[1].setting.strip(), len(order)), p[0]))
    for _, item in ranked:
        key = item.setting.strip()
        spec = specs.get(key)
        out = {
            "setting": key,
            "label": spec.label if spec else key,
            "tab": spec.tab if spec else None,
            "value": item.value,
            "reason": clean_text(item.reason, 600),
            "confidence": item.confidence,
        }
        if spec is None:
            rejected.append({**out, "problem": "This is not a setting the form offers."})
            continue
        if key in seen:
            rejected.append({**out, "problem": "Recommended twice; the first value is kept."})
            continue
        seen.add(key)
        try:
            value = parse_value(spec, item.value)
        except ValueError as exc:
            rejected.append({**out, "problem": f"The value {clean_text(item.value, 40)!r} {exc}."})
            continue
        current = spec.get(state)
        if _same(current, value):
            kept.append({**out, "value": value, "current": display(spec, current)})
            continue
        if problem := inactive_reason(spec, state):
            rejected.append({**out, "problem": f"Not applied: it {problem}."})
            continue
        trial = state.copy()
        spec.set(trial, value)
        try:
            rc = DataRoleConfig.model_validate(trial.rc)
        except ValidationError as exc:
            rejected.append({**out, "problem": f"Not applied: {_first_error(exc)}."})
            continue
        new_errors = [e for e in run_config_errors(rc, modelling_days) if e not in base_errors]
        if new_errors:
            rejected.append({**out, "problem": "Not applied: " + " ".join(new_errors)})
            continue
        trial.rc = rc.model_dump(mode="json")
        state = trial
        changes.append({**out, "value": value, "current": display(spec, current), "recommended": display(spec, value)})

    # Coverage: the settings that matter in the recommended state (its channel roles may differ from the start)
    final = all_specs(state, limits)
    active = [s for s in final.values() if inactive_reason(s, state) is None]
    missing = [s.label for s in active if s.key not in seen]
    coverage = {"reviewed": len(active) - len(missing), "active": len(active), "missing": missing}
    notes = {n.tab: clean_text(n.note, 600) for n in advice.tab_notes if n.tab in TABS and n.note.strip()}
    return Review(changes, kept, rejected, coverage, notes)


# ── Prompt ──────────────────────────────────────────────────────────────────────────────────────────────────────


def _allowed(spec: SettingSpec) -> str:
    if spec.kind == "bool":
        return "true or false"
    if spec.kind == "choice":
        return "; ".join(f"{k} ({v})" for k, v in spec.choices.items())
    if spec.kind == "shifts":
        return "1 to 4 different whole numbers from 0 to 7, comma-separated"
    kind = "a whole number" if spec.kind == "int" else "a decimal"
    b = _bounds_text(spec.bounds)
    return f"{kind} {b}".strip()


def _default_state(state: State) -> State:
    """The defaults for the run-wide settings (channel defaults are listed in the channel table)."""
    default = state.copy()
    default.profile, default.include_meridian, default.seed = "full", True, 0
    _set_frequency(default, "auto")
    default.rc["holdout_days"] = DEFAULT_HOLDOUT_DAYS
    default.rc["drop_trailing_isolated_days"] = False
    default.rc["model_settings"] = ModelSettings().model_dump(mode="json")
    default.rc["model_settings"]["sampling"] = dict(FULL_SAMPLING)
    return default


def _status(spec: SettingSpec, state: State) -> str:
    reason = inactive_reason(spec, state)
    return "active" if reason is None else f"inactive: {reason}"


def settings_text(state: State, limits: dict[str, Any]) -> str:
    """Every setting: key, status, meaning, allowed values, current value and default."""
    default = _default_state(state)
    lines = ["Run-wide settings (key (tab) [status]: meaning | allowed values | current | default):"]
    for spec in global_specs(limits):
        lines.append(
            f"- {spec.key} ({spec.tab}) [{_status(spec, state)}]: {spec.help} | {_allowed(spec)} | current "
            f"{display(spec, spec.get(state))} | default {display(spec, spec.get(default))}"
        )
    lines.append("")
    lines.append(
        "Per-channel settings, key channels.<slug>.<field> (tab channels) and custom_roi_priors.<slug>.median / .sigma "
        "(tab priors):"
    )
    channels = state.rc["channels"]
    roles, others = channel_specs(channels[:1], limits) if channels else ([], [])
    for spec in roles + others:
        generic = re.sub(r"^(channels|custom_roi_priors)\.[^.]+\.", r"\1.<slug>.", spec.key)
        lines.append(f"- {generic}: {spec.help} | {_allowed(spec)}")
    lines.append(
        "  Active per channel: role always; adstock_shape and saturation for media channels (paid, print_event) when "
        "Meridian is on; in_home_shift_days for print events; demand_following for paid channels when Meridian is on; "
        "custom_roi_priors for media channels when the ROI prior is custom. A new role resets the channel's adstock, "
        "saturation and shift to the role's defaults (binomial for print events, geometric otherwise; Hill; the default "
        f"in-home shift of {DEFAULT_IN_HOME_SHIFT} days). Default custom prior: median {fmt_number(MERIDIAN_MEDIAN)}, "
        f"σ {fmt_number(MERIDIAN_SIGMA)}."
    )
    lines.append("")
    header = (
        "| Channel | slug | role now | inferred role | adstock now | saturation now | in-home shift now | "
        "demand-following now / inferred | custom ROI prior now | spend days | total spend € | channel sales € | "
        "clicks | impressions |"
    )
    lines += ["Channels:", header]
    custom = state.ms.get("roi_prior") == "custom" and state.ms.get("media_prior_type") == "roi"
    priors = state.ms.get("custom_roi_priors") or {}
    for ch in channels:
        role = ch.get("role")
        media = role in MEDIA_ROLES
        prior = priors.get(ch.get("slug")) or {}
        prior_text = (
            f"median {fmt_number(float(prior.get('median', MERIDIAN_MEDIAN)))}, σ {fmt_number(float(prior.get('sigma', MERIDIAN_SIGMA)))}"
            if media and custom
            else "–"
        )
        cells = (
            ch.get("name"),
            ch.get("slug"),
            role,
            ch.get("inferred_role") or role,
            (ch.get("adstock_shape") or default_adstock_shape(role)) if media else "–",
            (ch.get("saturation") or "hill") if media else "–",
            ch.get("in_home_shift_days") if role == "print_event" else "–",
            f"{_yn(ch.get('demand_following'))} / {_yn(ch.get('inferred_demand_following'))}",
            prior_text,
            ch.get("cost_days", 0),
            fmt_number(float(ch.get("total_cost") or 0)),
            fmt_number(float(ch.get("total_sales") or 0)),
            _yn(ch.get("has_clicks")),
            _yn(ch.get("has_impressions")),
        )
        lines.append("| " + " | ".join(str(v) for v in cells) + " |")
        if ch.get("rationale"):
            lines.append(f"  note on {ch.get('name')}: {clean_text(ch['rationale'], 240)}")
    return "\n".join(lines)


def _yn(value: Any) -> str:
    return "–" if value is None else ("yes" if value else "no")


def profile_text(profile: dict[str, Any], holdout_days: int) -> str:
    """The workbook profile: period, length, gaps and warnings."""
    lines: list[str] = []
    start = profile.get("date_min") or profile.get("start_date")
    end = profile.get("date_max") or profile.get("end_date")
    if start and end:
        lines.append(f"Workbook {profile.get('filename', '')}: daily rows from {start} to {end} ({profile.get('calendar_days', '?')} calendar days).")
    days = profile.get("modelling_days")
    if days:
        lines.append(
            f"Modelling panel: {days} days ending {profile.get('modelling_end_date', '?')}; with the current {holdout_days}-day "
            f"hold-out {int(days) - int(holdout_days)} training days remain (at least {MIN_TRAINING_DAYS} are needed)."
        )
        if profile.get("default_holdout_days"):
            lines.append(f"The data's default hold-out (the split rule above) is {profile['default_holdout_days']} days.")
    isolated = profile.get("trailing_isolated_dates") or []
    if isolated:
        lines.append(f"Trailing isolated dates (separated from the rest of the history): {', '.join(map(str, isolated))}.")
    if profile.get("years_present"):
        lines.append(f"Years present: {', '.join(map(str, profile['years_present']))}.")
    lines.append(
        f"Rows: {profile.get('raw_row_count', profile.get('n_rows', '?'))}; exact duplicate rows dropped: "
        f"{profile.get('duplicate_rows_count', 0)}."
    )
    missing = profile.get("missing_channel_days") or {}
    if missing:
        lines.append("Missing channel-days (no workbook row): " + ", ".join(f"{k} {v}" for k, v in missing.items()) + ".")
    for key, label in (("total_sales_sum", "Total sales"), ("total_media_spend_sum", "Total cost (all channels)")):
        if isinstance(profile.get(key), (int, float)):
            lines.append(f"{label}: €{fmt_number(float(profile[key]))}.")
    if profile.get("catalog_sendout_days"):
        lines.append(f"Catalog send-out days: {profile['catalog_sendout_days']}.")
    for warning in profile.get("warnings") or []:
        lines.append(f"Warning: {clean_text(warning, 300)}")
    return "\n".join(f"- {line}" for line in lines)


def _cell(value: Any) -> str | float | int | bool | None:
    if isinstance(value, (dict, list)):
        return clean_text(json.dumps(value, ensure_ascii=False), 80)
    return value


def section_text(section: dict[str, Any], limit: int) -> str:
    """A result page as text: KPIs, notes, narrative, tables (first rows) and chart captions; no chart data."""
    head = f"## {clean_text(section.get('title') or section.get('id') or 'Section', 200)}"
    if section.get("subtitle"):
        head += f" — {clean_text(section['subtitle'], 400)}"
    lines = [head]
    for block in section.get("blocks") or []:
        kind = block.get("type")
        if kind == "kpis":
            items = [
                f"{clean_text(i.get('label'), 120)}: {clean_text(i.get('value'), 60)}"
                + (f" ({clean_text(i['delta'], 60)})" if i.get("delta") else "")
                for i in block.get("items") or []
            ]
            if items:
                lines.append("KPIs: " + "; ".join(items))
        elif kind == "callout":
            lines.append(f"Note — {clean_text(block.get('title'), 200)}: {clean_text(block.get('body'), 900)}")
        elif kind == "markdown":
            body = re.sub(r"```.*?```", " ", block.get("body") or "", flags=re.DOTALL)
            title = f"{clean_text(block['title'], 120)}: " if block.get("title") else ""
            lines.append(title + clean_text(body, 1_200))
        elif kind == "table":
            columns = block.get("columns") or []
            rows = block.get("rows") or []
            if not columns:
                continue
            shown = rows[:MAX_TABLE_ROWS]
            table = ExplainTable(
                columns=[clean_text(c.get("label") or c.get("key"), 80) for c in columns],
                rows=[[_cell(r.get(c.get("key"))) for c in columns] for r in shown],
            )
            lines.append(f"Table: {clean_text(block.get('title'), 200)}")
            if block.get("caption"):
                lines.append(f"Caption: {clean_text(block['caption'], 500)}")
            lines.append(describe_table(table).split("\n", 1)[1])
            if len(rows) > len(shown):
                lines.append(f"({len(rows) - len(shown)} more rows not listed)")
        elif kind == "chart":
            caption = f" — {clean_text(block['caption'], 400)}" if block.get("caption") else ""
            lines.append(f"Chart: {clean_text(block.get('title'), 200)}{caption}")
    text = "\n".join(lines)
    return text if len(text) <= limit else text[: limit - 30] + "\n… (page truncated)"


def evidence_settings_text(config: dict[str, Any], profile: str) -> str:
    """The settings that produced the results, as a difference from the defaults."""
    from mmm_studio.core.schema import extract_role_config

    rows = settings_changes(extract_role_config(config), seed=int(config.get("seed", 0) or 0), profile=profile)
    mode = f"{PROFILE_LABELS.get(profile, profile).split(' (')[0]} sampling profile"
    if config.get("skip_meridian"):
        mode = "Meridian skipped (EDA and OLS benchmark only)"
    if not rows:
        return f"{mode}; every other setting at its default."
    changes = "; ".join(f"{r['setting']} = {r['value']} (default {r['default']})" for r in rows)
    return f"{mode}; defaults except: {changes}."


def build_prompt(goal: str, state: State, limits: dict[str, Any], profile: dict[str, Any], results: str | None) -> str:
    """The user turn: goal, data, results (when any) and every setting with its current value."""
    parts = [
        f"Goal for the next run: {GOALS[goal]}",
        "Data (workbook profile):\n" + profile_text(profile, int(state.rc.get("holdout_days") or DEFAULT_HOLDOUT_DAYS)),
        results
        or "Results: none yet. Neither this run nor an earlier run on the same workbook has finished its analysis, so "
        "only the workbook profile is available.",
        "Current settings of the next run (Meridian "
        + ("on" if state.include_meridian else "off")
        + "):\n"
        + settings_text(state, limits),
    ]
    text = "\n\n".join(parts)
    if len(text) > MAX_PROMPT_CHARS:
        # The settings must survive: shorten the results instead
        overflow = len(text) - MAX_PROMPT_CHARS + 40
        if results and len(results) > overflow:
            parts[2] = results[: len(results) - overflow] + "\n… (results truncated)"
            text = "\n\n".join(parts)
    return text
