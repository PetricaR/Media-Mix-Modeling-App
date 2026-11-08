"""The benign Meridian / ArviZ / Altair UserWarnings are filtered, and nothing else is."""

from __future__ import annotations

import warnings

import pytest

from mmm_studio.core.meridian.spec import suppress_meridian_warnings

# Verbatim texts as the libraries emit them (meridian/analysis/analyzer.py, altair/utils/core.py).
BENIGN = [
    "The `aggregate_geos` argument is ignored in the national model. It will be reset to `True`.",
    "The `geos_to_include` argument is ignored in the national model. It will be reset to `None`.",
    "Setting `use_kpi=True` has no effect when `kpi_type=REVENUE` since in this case, KPI is equal to revenue.",
    "Effectiveness is not reported because it does not have a clear interpretation by time period.",
    "I don't know how to infer vegalite type from 'empty'.  Defaulting to nominal.",
    "Hierarchical distribution parameters must be deterministically zero for national models. x",
    "The group trace is not defined in the InferenceData scheme",
    "backend.set_random_seed is a no-op in JAX. Randomness is managed statelessly via PRNGKeys passed to "
    "sampling methods.",
]

MEANINGFUL = [
    "I don't know how to infer vegalite type from 'mixed-integer'.  Defaulting to nominal.",
    "Setting `use_kpi=False` but `revenue_per_kpi` is not available.",
]


def _caught(message: str) -> list[str]:
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        with suppress_meridian_warnings():
            warnings.warn(message, UserWarning, stacklevel=1)
    return [str(w.message) for w in caught]


@pytest.mark.parametrize("message", BENIGN)
def test_benign_warning_is_suppressed(message: str) -> None:
    assert _caught(message) == []


@pytest.mark.parametrize("message", MEANINGFUL)
def test_other_warnings_still_surface(message: str) -> None:
    assert _caught(message) == [message]
