"""The chronological train / validation / test split (2026-11-08).

Validation and test are equally long and back to back at the end of the data; by default each takes about an eighth of
the days (75 / 12.5 / 12.5), at most about a quarter and at least four weeks, and every fit keeps a full year of
training days before the window it is scored on. Before, the validation window was "the calendar quarter before the
test" (47 days next to a 93-day test on a 4-year dataset) and its refits could train on half a year.
"""

from __future__ import annotations

import pandas as pd
import pytest

from mmm_studio.core.features import default_holdout_days, holdout_windows, quarter_label
from mmm_studio.core.model_settings import MIN_TRAINING_DAYS


def _split(index: pd.DatetimeIndex, holdout_days: int | None = None) -> dict[str, tuple[str, str, int]]:
    """{window: (first day, last day, days)} with training up to the first window."""
    h = default_holdout_days(index) if holdout_days is None else holdout_days
    windows = holdout_windows(index, h)
    out = {}
    for name, (_, start, end) in windows.items():
        out[name] = (f"{start:%Y-%m-%d}", f"{end:%Y-%m-%d}", (end - start).days + 1)
    first = windows.get("forward", windows["primary"])[1]
    out["training"] = (f"{index[0]:%Y-%m-%d}", f"{first - pd.Timedelta(days=1):%Y-%m-%d}", (first - index[0]).days)
    return out


def test_the_sample_workbook_keeps_its_q3_validation_and_q4_test() -> None:
    idx = pd.date_range("2023-01-01", "2025-01-01")
    assert default_holdout_days(idx) == 93
    split = _split(idx)
    assert split == {
        "training": ("2023-01-01", "2024-06-30", 547),
        "forward": ("2024-07-01", "2024-09-30", 92),
        "primary": ("2024-10-01", "2025-01-01", 93),
    }
    # 75 / 12.5 / 12.5
    assert [round(split[k][2] / len(idx), 3) for k in ("training", "forward", "primary")] == [0.747, 0.126, 0.127]
    assert quarter_label("2024-10-01", "2025-01-01") == "Q4 2024" and quarter_label("2024-07-01", "2024-09-30") == "Q3 2024"


def test_validation_is_as_long_as_the_test_whatever_the_calendar() -> None:
    # dt_simulated_weekly (run_a0acfc6e62): a 93-day test used to sit next to a 47-day "quarter" (1 Jul – 16 Aug)
    idx = pd.date_range(end="2019-11-17", periods=1456)
    split = _split(idx)
    assert split["primary"] == ("2019-08-17", "2019-11-17", 93)
    assert split["forward"] == ("2019-05-16", "2019-08-16", 93)
    # A user's own hold-out length: validation follows it, starting on the nearest first of a month within a week
    assert _split(pd.date_range("2023-01-01", "2025-01-01"), 60)["forward"] == ("2024-09-01", "2024-11-02", 63)


def test_each_window_is_about_an_eighth_of_the_data_and_at_most_a_quarter() -> None:
    for days in (500, 600, 732):
        idx = pd.date_range("2022-01-01", periods=days)
        split = _split(idx)
        for name in ("forward", "primary"):
            assert abs(split[name][2] - days / 8) <= 7, (days, name, split[name])
        assert split["forward"][1] < split["primary"][0]
    # From about two years on, an eighth would exceed a quarter: the windows stay about a quarter long
    for days in (900, 1463, 2000, 3650):
        split = _split(pd.date_range("2015-01-01", periods=days))
        assert 86 <= split["primary"][2] <= 100 and 86 <= split["forward"][2] <= 100, (days, split)


def test_every_fit_keeps_a_full_training_year_before_its_window() -> None:
    for days in range(379, 760, 7):
        idx = pd.date_range("2022-01-01", periods=days)
        h = default_holdout_days(idx)
        assert 14 <= h <= days - MIN_TRAINING_DAYS
        windows = holdout_windows(idx, h)
        if "forward" in windows:
            _, val_start, val_end = windows["forward"]
            assert (val_start - idx[0]).days >= MIN_TRAINING_DAYS
            assert (val_end - val_start).days + 1 >= 28
            assert val_end + pd.Timedelta(days=1) == windows["primary"][1]


@pytest.mark.parametrize(
    ("days", "end", "test_days"),
    [
        (393, "2023-01-28", 28),  # the user's 13-month file: every day beyond the training year is the test
        (379, None, 14),  # the shortest data a run accepts
        (420, None, 55),
    ],
)
def test_too_little_data_for_two_four_week_windows_keeps_only_the_test(days: int, end: str | None, test_days: int) -> None:
    idx = pd.date_range(end=end, periods=days) if end else pd.date_range("2022-01-01", periods=days)
    assert default_holdout_days(idx) == test_days == days - MIN_TRAINING_DAYS
    assert "forward" not in holdout_windows(idx, test_days)


def test_a_validation_window_never_trains_on_less_than_a_year() -> None:
    # 450 days: two 42-day windows after the training year (the old rule took 85 test days and a validation window
    # trained on 273 days)
    idx = pd.date_range("2022-01-01", periods=450)
    split = _split(idx)
    assert split["primary"][2] == 42 and split["forward"] == ("2023-01-01", "2023-02-12", 43)
    assert split["training"][2] == MIN_TRAINING_DAYS
    # A longer test than the data allows for both windows drops validation rather than shortening its training
    assert "forward" not in holdout_windows(idx, 85)


def test_a_long_window_from_a_quarter_start_is_not_labelled_as_that_quarter() -> None:
    assert quarter_label("2025-10-01", "2026-01-04") == "Q4 2025"  # a quarter plus a few trailing days
    assert quarter_label("2024-10-01", "2025-09-30") == "2024-10-01 – 2025-09-30"
