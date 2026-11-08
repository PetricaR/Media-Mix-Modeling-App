"""Resampling onto the daily grid and gap filling, column by column (`core/resample.py`)."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from wide_data import CHANNELS, wide_frame

from mmm_studio.core.mapping import canonicalise_report, suggest_mapping
from mmm_studio.core.resample import (
    FILLS,
    PreparationError,
    describe_dates,
    disaggregate_level,
    disaggregate_series,
    fill_series,
    resample_errors,
)
from mmm_studio.core.schema import ColumnMapping, ColumnResample, ResampleConfig, WideChannel
from mmm_studio.core.tables import build_daily_wide_table
from mmm_studio.core.workbook import inspect_workbook, profile_workbook


def _with(mapping: ColumnMapping, **config) -> ColumnMapping:
    cols = [ColumnResample(**c) for c in config.pop("columns", [])]
    return mapping.model_copy(update={"resample": ResampleConfig(columns=cols, **config)})


def _csv(frame: pd.DataFrame) -> bytes:
    return frame.to_csv(index=False).encode("utf-8")


# --------------------------------------------------------------------------------------------------------------------
# Dates
# --------------------------------------------------------------------------------------------------------------------


def test_the_dates_frequency_is_detected() -> None:
    daily = describe_dates(pd.Series(pd.date_range("2024-01-01", periods=30)).drop(index=[3, 4]))
    assert daily["detected"] == "daily" and daily["missing_dates"] == 2 and daily["longest_gap"] == 2
    weekly = describe_dates(pd.Series(pd.date_range("2024-01-07", periods=20, freq="7D")))
    assert weekly["detected"] == "weekly" and weekly["weekday"] == "Sunday" and weekly["missing_dates"] == 0
    month_ends = describe_dates(pd.Series(pd.date_range("2023-01-31", periods=14, freq="ME")).drop(index=[5]))
    assert month_ends["detected"] == "monthly" and month_ends["anchor"] == "end" and month_ends["missing_dates"] == 1
    hourly = describe_dates(pd.Series(pd.date_range("2024-01-01", periods=72, freq="h")))
    assert hourly["detected"] == "sub_daily" and hourly["rows_per_date"] == 24 and hourly["has_time"]
    assert describe_dates(pd.Series(pd.date_range("2024-01-01", periods=10, freq="3D")))["detected"] == "irregular"


# --------------------------------------------------------------------------------------------------------------------
# One series
# --------------------------------------------------------------------------------------------------------------------


def test_every_fill_method_fills_only_the_blanks() -> None:
    s = pd.Series([np.nan, 2.0, np.nan, 4.0, 5.0, np.nan, np.nan, 8.0, 9.0, 10.0, np.nan])
    observed = s.notna()
    weekdays = np.arange(len(s)) % 7
    for method, (_, _, kinds) in FILLS.items():
        if "flow" not in kinds or method == "none":
            continue
        out, fallback = fill_series(s, method, 7.5, weekdays=weekdays)
        assert out.notna().all(), method
        if method != "spread":  # spread shares a value with the blanks after it
            assert np.allclose(out[observed], s[observed]), method
        assert fallback >= 0
    linear, fallback = fill_series(s, "linear")
    assert linear.tolist() == [2.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0, 10.0] and fallback == 2  # edges: nearest
    assert fill_series(s, "ffill")[0].tolist()[:3] == [2.0, 2.0, 2.0]
    assert fill_series(s, "bfill")[0].tolist()[-2:] == [10.0, 10.0]
    # Nearest: the closer value, the earlier one on a tie
    assert fill_series(pd.Series([1.0, np.nan, 3.0, np.nan, np.nan, 6.0]), "nearest")[0].tolist() == [1.0, 1.0, 3.0, 3.0, 6.0, 6.0]
    assert fill_series(s, "zero")[0][~observed].eq(0).all() and fill_series(s, "constant", 7.5)[0][~observed].eq(7.5).all()
    assert fill_series(s, "mean")[0][2] == pytest.approx(s.mean()) and fill_series(s, "median")[0][2] == s.median()
    # Spread: a value followed by blank rows is shared with them, so the total is kept
    booked = pd.Series([70.0, np.nan, np.nan, np.nan, np.nan, np.nan, np.nan, 14.0, np.nan])
    spread, _ = fill_series(booked, "spread")
    assert spread.tolist() == [10.0] * 7 + [7.0, 7.0] and spread.sum() == booked.sum()


def test_same_weekday_keeps_the_weekly_pattern() -> None:
    pattern = np.tile([100.0, 100, 100, 100, 100, 300, 50], 6)
    s = pd.Series(pattern.copy())
    s[[12, 19, 26]] = np.nan  # three Saturdays (index 5 mod 7)
    weekdays = np.arange(len(s)) % 7
    seasonal, _ = fill_series(s, "seasonal", weekdays=weekdays)
    assert np.allclose(seasonal, pattern)
    linear, _ = fill_series(s, "linear")
    assert not np.allclose(linear, pattern)  # a straight line misses the Saturday peak


def test_categories_fill_with_text_methods() -> None:
    s = pd.Series(["Promo", None, "Promo", None, "Normal"], dtype=object)
    assert fill_series(s, "ffill")[0].tolist() == ["Promo", "Promo", "Promo", "Promo", "Normal"]
    assert fill_series(s, "mode")[0].tolist()[1] == "Promo"
    assert fill_series(s, "baseline")[0].isna().sum() == 2  # blank = the baseline category: every flag off


def test_disaggregation_keeps_totals_or_levels() -> None:
    weeks = pd.Series([70.0, 140.0, 210.0, 0.0, 70.0])
    lengths = np.full(5, 7)
    even = disaggregate_series(weeks, lengths, "even")
    assert even[:7].tolist() == [10.0] * 7
    smooth = disaggregate_series(weeks, lengths, "smooth")
    sums = smooth.reshape(5, 7).sum(axis=1)
    assert np.allclose(sums, weeks) and (smooth >= -1e-9).all() and smooth[0] != smooth[6]
    first = disaggregate_series(weeks, lengths, "first_day")
    assert first[0] == 70.0 and first[1:7].sum() == 0 and first.sum() == weeks.sum()
    level = pd.Series([10.0, 20.0, 30.0])
    assert disaggregate_level(level, np.full(3, 7), "repeat").tolist()[:8] == [10.0] * 7 + [20.0]
    line = disaggregate_level(level, np.full(3, 7), "linear")
    assert line[3] == 10.0 and line[10] == 20.0 and line[0] == 10.0 and 10 < line[6] < 20


# --------------------------------------------------------------------------------------------------------------------
# Whole tables
# --------------------------------------------------------------------------------------------------------------------


def test_missing_days_fail_unless_filled_and_every_fill_is_reported() -> None:
    frame = wide_frame(420).drop(index=[50, 51, 52]).reset_index(drop=True)
    frame.loc[[100, 101], "Revenue"] = np.nan
    m = suggest_mapping(frame)
    assert any("skips 3 days" in e and "Fill missing dates" in e for e in resample_errors(frame, m))
    filled = _with(m, fill_missing_dates=True, columns=[{"column": "Revenue", "fill": "seasonal"}, {"column": CHANNELS[1], "fill": "pchip"}])
    out, warnings, prep = canonicalise_report(frame, filled)
    assert len(prep.daily) == 420 and prep.daily.notna().all().all()
    rev = next(c for c in prep.columns if c.column == "Revenue")
    assert (rev.blank, rev.added, rev.filled, rev.fill, rev.explicit) == (2, 3, 5, "seasonal", ["fill"])
    tv = next(c for c in prep.columns if c.column == CHANNELS[1])
    assert tv.filled == 3 and tv.fill == "pchip"
    assert any("3 missing days were added" in w for w in warnings)
    assert any("interpolated between the same weekdays" in w for w in warnings)
    assert any("TV Ads: 3 filled by PCHIP interpolation" in w for w in warnings)
    assert prep.imputed["Revenue"].sum() == 5
    # The canonical table carries the imputed values
    assert out["date"].nunique() == 420 and out["kpi_total"].notna().all()


def test_hourly_rows_are_combined_per_column() -> None:
    days = wide_frame(400)
    rows = []
    for hour in (8, 13, 19):
        part = days.copy()
        part["Date"] = part["Date"] + pd.Timedelta(hours=hour)
        for col in ("Revenue", *CHANNELS):
            part[col] = part[col] / 3
        rows.append(part)
    hourly = pd.concat(rows).sort_values("Date").reset_index(drop=True)
    hourly.loc[hourly.index[5], "Covid"] = 0  # a flag on in only some of a day's rows: max keeps the day on
    m = suggest_mapping(hourly)
    assert m.layout == "wide" and m.target == "Revenue"
    out, warnings, prep = canonicalise_report(hourly, m)
    assert prep.grid.frequency == "sub_daily" and len(prep.daily) == 400
    assert np.allclose(prep.daily["Revenue"].to_numpy(), days["Revenue"].to_numpy())
    assert prep.daily["Covid"].sum() == days["Covid"].sum()
    assert any("800 rows that share a day were combined" in w for w in warnings)
    # Rows that share a date without a time of day are only combined when asked to
    regions = pd.concat([days, days.assign(Revenue=days["Revenue"] + 1)]).reset_index(drop=True)
    with pytest.raises(PreparationError, match="share a date"):
        canonicalise_report(regions, suggest_mapping(days))
    _, _, prep = canonicalise_report(regions, _with(suggest_mapping(days), frequency="sub_daily"))
    assert np.allclose(prep.daily["Revenue"].to_numpy(), 2 * days["Revenue"].to_numpy() + 1)


def test_monthly_rows_become_days_and_keep_their_totals() -> None:
    months = pd.date_range("2022-01-01", periods=15, freq="MS")
    frame = pd.DataFrame(
        {
            "Month": months,
            "Revenue": np.linspace(300_000, 450_000, 15),
            "TV spend": np.linspace(30_000, 45_000, 15),
            "Price index": np.linspace(100.0, 114.0, 15),
        }
    )
    m = ColumnMapping(
        layout="wide",
        date="Month",
        target="Revenue",
        wide_channels=[WideChannel(name="TV", spend="TV spend")],
        controls=[{"column": "Price index", "name": "Price"}],
    )
    m = _with(m, columns=[{"column": "Revenue", "disaggregate": "smooth"}, {"column": "Price index", "disaggregate": "linear"}])
    df, prof = profile_workbook(_csv(frame), filename="monthly.csv", role_config=_role_config(m))
    assert prof.calendar_days == 455 and prof.start_date == "2022-01-01" and prof.end_date == "2023-03-31"
    wide = build_daily_wide_table(df, config=prof.role_config)
    assert wide["total_sales"].sum() == pytest.approx(frame["Revenue"].sum())
    assert wide["cost_tv"].iloc[:31].nunique() == 1  # spread evenly over January
    assert wide["ctrl_price"].is_monotonic_increasing  # a line through the months' middles
    assert any("Monthly data (15 months" in w and "455 daily rows" in w for w in prof.warnings)


def test_weekly_dates_can_mark_the_end_of_their_week() -> None:
    weekly = wide_frame(60 * 7).iloc[::7].reset_index(drop=True)
    m = _with(suggest_mapping(weekly), period_label="end")
    _, _, prep = canonicalise_report(weekly, m)
    assert prep.daily.index[0] == weekly["Date"].iloc[0] - pd.Timedelta(days=6)
    assert prep.daily.index[-1] == weekly["Date"].iloc[-1]


def test_a_monthly_control_in_a_daily_table_is_forward_filled_when_asked() -> None:
    frame = wide_frame(400)
    frame["CPI"] = np.where(frame["Date"].dt.day == 1, 100 + frame["Date"].dt.month, np.nan)
    m = suggest_mapping(frame)
    assert "CPI" in [c.column for c in m.controls]  # named like a known driver: a control, however blank
    errors = resample_errors(frame, m)
    assert any("'CPI'" in e and "blank on" in e for e in errors)  # too blank for the default (linear, ≤ 20 %)
    out, warnings, prep = canonicalise_report(frame, _with(m, columns=[{"column": "CPI", "fill": "ffill"}]))
    cpi = prep.daily["CPI"]
    assert cpi.notna().all() and cpi.iloc[0:31].eq(101).all() and cpi.iloc[31] == 102
    assert any("Most of 'CPI' is imputed" in w for w in warnings)


def test_impossible_choices_fail_with_the_reason() -> None:
    frame = wide_frame(400)
    m = suggest_mapping(frame)
    errs = resample_errors(frame, _with(m, columns=[{"column": "Covid", "fill": "spread"}]))
    assert any("Spread over the blanks after it" in e and "cannot be used" in e for e in errs)
    assert any("enter the value" in e for e in resample_errors(frame, _with(m, columns=[{"column": "Revenue", "fill": "constant"}])))
    assert any("0 or 1" in e for e in resample_errors(frame, _with(m, columns=[{"column": "Covid", "fill": "constant", "fill_value": 2}])))
    assert any("does not use" in e for e in resample_errors(frame, _with(m, columns=[{"column": "Sessions", "fill": "zero"}])))
    weekly = frame.iloc[::7].reset_index(drop=True)
    assert any("needs daily data" in e for e in resample_errors(weekly, _with(suggest_mapping(weekly), columns=[{"column": "Revenue", "fill": "seasonal"}])))
    assert any("No two rows share a date" in e for e in resample_errors(frame, _with(m, frequency="sub_daily")))
    # Choosing weekly or monthly on a daily table combines rows by period and splits them into days while keeping totals
    _, w_warnings, w_prep = canonicalise_report(frame, _with(m, frequency="weekly"))
    assert w_prep.grid.frequency == "weekly" and len(w_prep.daily) == len(frame)
    assert w_prep.daily["Revenue"].sum() == pytest.approx(frame["Revenue"].sum())
    assert any("combined into one row per week" in w for w in w_warnings)
    _, m_warnings, m_prep = canonicalise_report(frame, _with(m, frequency="monthly"))
    assert m_prep.grid.frequency == "monthly" and m_prep.daily["Revenue"].sum() == pytest.approx(frame["Revenue"].sum())
    assert any("combined into one row per month" in w for w in m_warnings)
    # Shifted weekly dates fail on auto, and align when weekly is explicitly chosen
    shifted_weekly = weekly.copy()
    shifted_weekly.loc[3, "Date"] = shifted_weekly.loc[3, "Date"] + pd.Timedelta(days=1)
    assert any("skip or shift weeks" in e for e in resample_errors(shifted_weekly, suggest_mapping(shifted_weekly)))
    assert resample_errors(shifted_weekly, _with(suggest_mapping(shifted_weekly), frequency="weekly")) == []
    # A cubic spline that dips below zero for an amount that never does is refused, with the way out
    spiky = frame.copy()
    spiky[CHANNELS[1]] = 0.0
    spiky.loc[100, CHANNELS[1]] = 5000.0
    spiky.loc[[102, 103], CHANNELS[1]] = np.nan
    with pytest.raises(PreparationError, match="PCHIP"):
        canonicalise_report(spiky, _with(m, columns=[{"column": CHANNELS[1], "fill": "cubic"}]))


def test_a_long_weekly_table_is_split_per_channel() -> None:
    weeks = pd.date_range("2023-01-02", periods=56, freq="7D")
    frame = pd.concat(
        [
            pd.DataFrame({"date": weeks, "channel": ch, "sales_by_channel": 700.0 * (i + 1), "costs": 70.0 * (i + 1)})
            for i, ch in enumerate(("Search", "Social"))
        ],
        ignore_index=True,
    )
    m = ColumnMapping(layout="long", date="date", channel="channel", sales="sales_by_channel", costs="costs", impressions=None, clicks=None)
    out, warnings, prep = canonicalise_report(frame, _with(m, columns=[{"column": "costs", "disaggregate": "first_day"}]))
    assert len(out) == 2 * 56 * 7 and out["date"].nunique() == 56 * 7
    search = out[out["channel"] == "Search"]
    assert search["sales_by_channel"].iloc[:7].tolist() == [100.0] * 7
    assert search["costs_by_channel"].iloc[:7].tolist() == [70.0] + [0.0] * 6
    assert any("Weekly data (56 weeks" in w for w in warnings)
    assert any("Fill missing dates" in e for e in resample_errors(frame, _with(m, fill_missing_dates=True)))
    assert any("missing-row controls" in e for e in resample_errors(frame, _with(m, columns=[{"column": "costs", "fill": "zero"}])))


def test_the_inspection_reports_the_dates_the_resampling_and_its_errors() -> None:
    frame = wide_frame(420).drop(index=[30]).reset_index(drop=True)
    data = _csv(frame)
    first = inspect_workbook(data, "gappy.csv")
    assert first["time_grid"]["detected"] == "daily" and first["time_grid"]["missing_dates"] == 1
    assert first["prep_errors"] and first["prep_errors"][0] in first["errors"] and first["preview"] is None
    assert {o["value"] for o in first["resample_options"]["fill"]} == set(FILLS)
    # The choices per column are known before the data can run, so the step can fix what blocks it
    covid = next(c for c in first["resample_columns"] if c["column"] == "Covid")
    assert covid["kind"] == "flag" and covid["defaults"]["fill"] == "zero" and "linear" not in covid["allowed"]["fill"]
    m = _with(suggest_mapping(frame), fill_missing_dates=True)
    fixed = inspect_workbook(data, "gappy.csv", role_config=_role_config(m))
    assert fixed["errors"] == [] and fixed["preview"]["calendar_days"] == 420
    res = fixed["resample"]
    assert res["grid"]["added"] == 1 and res["grid"]["days"] == 420
    series = {s["column"]: s for s in res["series"]["columns"]}
    assert len(res["series"]["dates"]) == 420 and series["Revenue"]["imputed"] == [30]
    assert {c["column"]: c["fill"] for c in res["columns"]}["Revenue"] == "linear"


def _role_config(mapping: ColumnMapping):
    from mmm_studio.core.schema import DataRoleConfig

    return DataRoleConfig(columns=mapping)


def test_a_resampled_upload_runs_the_analysis_pipeline(tmp_path) -> None:
    """Weekly rows with a missing week and a sparse control, resampled in the wizard, run through every analysis stage."""
    from mmm_studio.core.schema import DataRoleConfig
    from mmm_studio.pipeline.context import RunContext
    from mmm_studio.pipeline.stages import run_full_pipeline
    from mmm_studio.storage import LocalStorage

    weekly = wide_frame(80 * 7, start="2022-01-03").iloc[::7].reset_index(drop=True).drop(index=[12]).reset_index(drop=True)
    weekly.loc[[5, 6], "Revenue"] = np.nan
    weekly["Price index"] = np.where(np.arange(len(weekly)) % 4 == 0, np.linspace(100, 110, len(weekly)), np.nan)
    m = suggest_mapping(weekly)
    assert "Price index" in [c.column for c in m.controls]  # however sparse, a price is a control
    m = _with(
        m,
        fill_missing_dates=True,
        columns=[
            {"column": "Revenue", "fill": "pchip", "disaggregate": "smooth"},
            {"column": "Price index", "fill": "ffill", "disaggregate": "linear"},
        ],
    )
    raw = _csv(weekly)
    _, prof = profile_workbook(raw, filename="weekly.csv", role_config=DataRoleConfig(columns=m))
    assert prof.calendar_days == 80 * 7
    errors: list[str] = []
    ctx = RunContext(
        run_id="resampled",
        storage=LocalStorage(tmp_path),
        run_profile="smoke",
        on_stage_error=lambda s, e: errors.append(f"{s}: {e}"),
        on_stage_skipped=lambda s, why: None,
    )
    ctx.config = prof.role_config
    ctx.cache.update(workbook_bytes=raw, workbook_filename="weekly.csv")
    summary = run_full_pipeline(ctx, skip_meridian=True)
    assert errors == []
    assert {"benchmark", "preparation", "explore_target"} <= set(summary["sections"])
    wide = ctx.load_dataframe("wide_model")
    assert len(wide) == 80 * 7 and wide["total_sales"].notna().all() and wide["ctrl_price_index"].notna().all()
