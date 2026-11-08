"""Any dataset: the mapping onto the data contract, wide tables, controls, Google holidays and Open-Meteo weather."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from wide_data import CHANNELS, wide_csv, wide_frame, wide_xlsx

from mmm_studio.core import holidays as gcal
from mmm_studio.core import weather as wx
from mmm_studio.core.holidays import (
    GOOGLE_CALENDAR_NAMES,
    HolidayCalendarError,
    parse_ical,
    public_holiday_flags,
)
from mmm_studio.core.mapping import (
    TARGET_COLUMN,
    WorkbookValidationError,
    canonicalise,
    mapping_errors,
    suggest_mapping,
)
from mmm_studio.core.model_settings import run_config_errors
from mmm_studio.core.resample import resample_errors
from mmm_studio.core.schema import (
    COUNTRIES,
    ChannelConfig,
    ColumnMapping,
    ColumnResample,
    ControlColumn,
    DataRoleConfig,
    ResampleConfig,
    WideChannel,
)
from mmm_studio.core.tables import build_daily_wide_table
from mmm_studio.core.workbook import (
    apply_channel_choices,
    default_holdout_days,
    inspect_workbook,
    profile_workbook,
)


def test_the_suggested_mapping_of_a_wide_table() -> None:
    m = suggest_mapping(wide_frame())
    assert m.layout == "wide" and m.date == "Date" and m.target == "Revenue"
    assert [c.spend for c in m.wide_channels] == list(CHANNELS)
    assert [c.column for c in m.controls] == ["Covid"]  # a 0/1 column is a control
    # An outcome is never an input, and a "catalog sales" ad format is a channel, not the target
    assert "Sessions" not in {c for ch in m.wide_channels for c in (ch.spend, ch.impressions, ch.clicks, ch.sales)}
    assert mapping_errors(wide_frame(), m) == []


def test_the_sample_workbook_still_maps_as_a_long_table(fixture_bytes: bytes) -> None:
    _, prof = profile_workbook(fixture_bytes, filename="sample_data.xlsx")
    cols = prof.role_config.columns
    assert (cols.layout, cols.date, cols.channel, cols.sales, cols.costs) == ("long", "date", "channel", "sales_by_channel", "costs")
    assert (cols.impressions, cols.clicks) == ("impressions", "clicks")
    assert prof.layout == "long" and prof.has_channel_sales and prof.default_holdout_days == 93
    roles = {c.name: c.role for c in prof.role_config.channels}
    assert roles["Catalog"] == "print_event" and roles["Affiliate"] == "commission"


def test_a_wide_table_becomes_the_daily_panel_with_its_target_and_controls() -> None:
    frame = wide_frame()
    df, prof = profile_workbook(wide_xlsx(), filename="mmm.xlsx")
    rc = prof.role_config
    # The longest sheet with dates is read; the short sheet and the notes are not
    assert prof.selected_sheet == "data" and prof.calendar_days == 400 and prof.layout == "wide"
    assert prof.default_holdout_days == 400 - 365 == rc.holdout_days and prof.max_holdout_days == 35
    assert not prof.has_channel_sales and prof.control_names == ["Covid"]
    roles = {c.name: c.role for c in rc.channels}
    assert roles == dict.fromkeys(CHANNELS, "paid")  # spend on every day: no print drop despite "Catalog"
    wide = build_daily_wide_table(df, config=rc)
    assert np.allclose(wide["total_sales"].to_numpy(), frame["Revenue"].to_numpy())
    assert wide["ctrl_covid"].sum() == 30 and TARGET_COLUMN not in wide.columns
    assert np.allclose(wide["cost_tv_ads"].to_numpy(), frame["TV Ads"].to_numpy())
    assert prof.total_sales_sum == pytest.approx(float(frame["Revenue"].sum()), rel=1e-6)


def test_a_semicolon_csv_with_day_first_dates_reads_like_the_workbook() -> None:
    _, prof = profile_workbook(wide_csv(";"), filename="mmm.csv")
    assert prof.layout == "wide" and prof.calendar_days == 400 and prof.start_date == "2022-01-01"
    assert prof.role_config.columns.target == "Revenue"


def test_mapping_errors_are_plain_words() -> None:
    frame = wide_frame()
    base = suggest_mapping(frame)
    missing = base.model_copy(update={"target": "Umsatz"})
    assert any("not in the sheet" in e and "Umsatz" in e for e in mapping_errors(frame, missing))
    twice = base.model_copy(update={"controls": [ControlColumn(column="Revenue")]})
    assert any("used twice" in e for e in mapping_errors(frame, twice))
    texty = frame.assign(Notes="n/a")
    bad = base.model_copy(update={"wide_channels": [*base.wide_channels, WideChannel(name="Notes", spend="Notes")]})
    assert any("not numbers" in e for e in mapping_errors(texty, bad))
    bad_ctrl = base.model_copy(update={"controls": [ControlColumn(column="Notes", name="Notes")]})
    assert any("2 to 12 categories" in e for e in mapping_errors(texty, bad_ctrl))
    blank = frame.copy()
    blank.loc[:99, "Revenue"] = np.nan
    # Blank cells are the resampling's business: too many for the default fill, fine with a chosen method
    assert mapping_errors(blank, base) == []
    assert any("blank on 100 rows (25%)" in e and "Resample & fill" in e for e in resample_errors(blank, base))
    chosen = base.model_copy(update={"resample": ResampleConfig(columns=[ColumnResample(column="Revenue", fill="seasonal")])})
    assert resample_errors(blank, chosen) == []
    with pytest.raises(WorkbookValidationError, match="Choose the column with the target"):
        canonicalise(frame, base.model_copy(update={"target": None}))


def test_gaps_and_non_daily_tables_fail_loudly() -> None:
    frame = wide_frame()
    with pytest.raises(WorkbookValidationError, match="skips 1 day"):
        canonicalise(frame.drop(index=50), suggest_mapping(frame))
    weekly_gapped = frame.iloc[::7].drop(index=70).reset_index(drop=True)
    with pytest.raises(WorkbookValidationError, match="skip or shift weeks"):
        canonicalise(weekly_gapped, suggest_mapping(weekly_gapped))
    monthly = frame.iloc[::30].reset_index(drop=True)
    with pytest.raises(WorkbookValidationError, match="monthly"):
        canonicalise(monthly, suggest_mapping(monthly))
    repeated = pd.concat([frame, frame.iloc[[3]].assign(Revenue=1.0)], ignore_index=True)
    with pytest.raises(WorkbookValidationError, match="share a date"):
        canonicalise(repeated, suggest_mapping(frame))


def test_weekly_csv_with_bom_categorical_control_and_blanks_disaggregates_to_daily() -> None:
    weekly = wide_frame(60 * 7).iloc[::7].reset_index(drop=True)
    promos = np.array(["Normal", "Coupons", "BOGO", "Flash Sale"])
    weekly["sales_promotion"] = promos[np.arange(len(weekly)) % len(promos)]
    weekly["competitor_spend"] = np.linspace(1000.0, 2000.0, len(weekly))
    weekly.loc[[2, 10], "Revenue"] = np.nan
    weekly.loc[[3, 15, 25], "competitor_spend"] = np.nan
    csv_bytes = ("\ufeff" + weekly.to_csv(index=False)).encode("utf-8")

    df, prof = profile_workbook(csv_bytes, filename="mmm_dataset.csv")
    assert prof.calendar_days == 60 * 7
    assert prof.role_config.columns.date == "Date"
    assert prof.control_names == ["Covid", "Sales Promotion", "Competitor"]
    assert any("linearly interpolated" in w for w in prof.warnings)
    assert any("one-hot encoded" in w and "sales_promotion" in w for w in prof.warnings)
    assert any("disaggregated into 420 daily rows" in w for w in prof.warnings)
    wide = build_daily_wide_table(df, config=prof.role_config)
    assert len(wide) == 60 * 7
    assert {
        "ctrl_covid",
        "ctrl_sales_promotion_coupons",
        "ctrl_sales_promotion_bogo",
        "ctrl_sales_promotion_flash_sale",
        "ctrl_competitor",
    } <= set(wide.columns)
    assert np.isclose(wide["cost_tv_ads"].sum(), weekly["TV Ads"].sum())


def test_channel_choices_apply_by_name_and_attributed_priors_need_channel_sales() -> None:
    _, prof = profile_workbook(wide_xlsx(), filename="mmm.xlsx")
    rc = prof.role_config
    chosen = apply_channel_choices(rc.channels, [ChannelConfig(name="TV Ads", role="ignored")])
    tv = next(c for c in chosen if c.name == "TV Ads")
    assert tv.role == "ignored" and tv.inferred_role == "paid" and not tv.enabled
    assert tv.total_cost > 0  # statistics stay the measured ones
    with pytest.raises(WorkbookValidationError, match="not in the data"):
        apply_channel_choices(rc.channels, [ChannelConfig(name="Radio", role="paid")])
    rc.model_settings.roi_prior = "attributed_roas"
    assert any("sales per channel" in e for e in run_config_errors(rc, prof.modelling_days))
    assert default_holdout_days(pd.date_range("2023-01-01", "2025-01-01")) == 93
    assert default_holdout_days(pd.date_range("2022-01-01", periods=380)) == 15


def test_inspection_reports_sheets_side_tables_and_the_preview() -> None:
    out = inspect_workbook(wide_xlsx(), "mmm.xlsx")
    assert out["selected_sheet"] == "data" and out["suggested"] and out["errors"] == []
    assert [s["name"] for s in out["sheets"]] == ["short", "data", "notes"]
    assert [t["sheet"] for t in out["side_tables"]] == ["notes"]  # small sheets only; "short" is a 300-row table
    assert out["preview"]["default_holdout_days"] == 35
    cols = {c["name"]: c for c in out["columns"]}
    assert cols["Covid"]["binary"] and cols["Date"]["kind"] == "date"
    # A mapping that cannot be applied is reported, not raised
    bad = DataRoleConfig(columns=ColumnMapping(layout="wide", date="Date", target="Umsatz"))
    out = inspect_workbook(wide_xlsx(), "mmm.xlsx", sheet="data", role_config=bad)
    assert out["preview"] is None and any("Umsatz" in e for e in out["errors"])


# ── Google Calendar holidays ────────────────────────────────────────────────────────────────────────────────


ICS = (
    "BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nDTSTART;VALUE=DATE:20240501\r\nSUMMARY:Labour Day\r\nDESCRIPTION:Public holiday\r\n"
    "UID:20240501_abc@google.com\r\nEND:VEVENT\r\nBEGIN:VEVENT\r\nDTSTART;VALUE=DATE:20240106\r\nSUMMARY:Epiphany (regional holiday)\r\n"
    "DESCRIPTION:Public holiday in Bavaria\\, Saxony-Anhalt\r\nUID:20240106_def@google.com\r\nEND:VEVENT\r\nBEGIN:VEVENT\r\n"
    "DTSTART;VALUE=DATE:20241224\r\nSUMMARY:Christmas Eve\r\nDESCRIPTION:Public holiday\\nHalf-day holiday\\, half-day\r\n"
    "UID:20241224_ghi@google.com\r\nEND:VEVENT\r\nBEGIN:VEVENT\r\nDTSTART;VALUE=DATE:20240214\r\nSUMMARY:Valentine's Day\r\n"
    "DESCRIPTION:Observance\\nTo hide observances\\, go to Google Calendar Settings\r\nUID:20240214_jkl@google.com\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"
)


def test_google_calendar_events_are_classified_by_their_description() -> None:
    kinds = {r["name"]: (r["kind"], r["regions"]) for r in parse_ical(ICS)}
    assert kinds == {
        "Labour Day": ("public", ""),
        "Epiphany": ("regional", "Bavaria, Saxony-Anhalt"),
        "Christmas Eve": ("half_day", ""),
        "Valentine's Day": ("observance", ""),
    }


def test_the_holiday_control_is_googles_german_public_holidays() -> None:
    import holidays

    idx = pd.date_range("2023-01-01", "2024-12-31", freq="D")
    flags = public_holiday_flags(idx, "DE")
    assert set(idx[flags == 1].date) == set(holidays.Germany(years=[2023, 2024]))  # Germany's national holidays


def test_dates_outside_the_google_calendar_use_python_holidays_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    import holidays

    snapshot = gcal.google_holidays("DE")  # a refresh fetches the same calendar again: no network in tests
    monkeypatch.setattr(gcal, "fetch_google_holidays", lambda country: snapshot)
    idx = pd.date_range("2019-12-25", "2020-01-05")
    flags = public_holiday_flags(idx, "DE")
    assert set(idx[flags == 1].date) == {
        d for d in holidays.Germany(years=[2019, 2020]) if pd.Timestamp("2019-12-25").date() <= d <= pd.Timestamp("2020-01-05").date()
    }


def test_dates_outside_the_google_calendar_fail_loudly(monkeypatch: pytest.MonkeyPatch) -> None:
    snapshot = gcal.google_holidays("DE")
    monkeypatch.setattr(gcal, "fetch_google_holidays", lambda country: snapshot)
    monkeypatch.setattr(gcal, "_fallback_holiday_events", lambda country, years: pd.DataFrame(columns=gcal.EVENT_COLUMNS))
    with pytest.raises(HolidayCalendarError, match="covers 2021–2031"):
        public_holiday_flags(pd.date_range("2019-12-25", "2020-01-05"), "DE")


def test_holiday_control_disabled_skips_holiday_lookup(monkeypatch: pytest.MonkeyPatch) -> None:
    from mmm_studio.core.calendar import add_calendar_and_adstock_features
    from mmm_studio.core.eda.stats import event_lifts
    from mmm_studio.core.preparation import _calendar
    from mmm_studio.core.schema import DataRoleConfig

    def _boom(*args: object, **kwargs: object) -> object:
        raise AssertionError("holiday calendar should not be queried when holidays=False")

    monkeypatch.setattr(gcal, "holiday_events", _boom)
    monkeypatch.setattr(gcal, "public_holiday_flags", _boom)

    idx = pd.date_range("2017-01-01", "2017-12-31", freq="D")
    panel = pd.DataFrame(
        {"total_sales": 1000.0, "cost_tv": 50.0, "total_cost": 50.0, "digital_cost": 50.0},
        index=idx,
    )
    cfg = DataRoleConfig(country="US")
    cfg.model_settings.calendar.holidays = False

    enriched = add_calendar_and_adstock_features(panel, config=cfg)
    assert int(enriched["is_de_holiday"].sum()) == 0
    ev = event_lifts(panel["total_sales"], None, "US", include_holidays=False)
    assert "Black Friday" in set(ev["event"])
    cal = _calendar(panel, cfg)
    assert "holiday" not in cal.columns


def test_every_country_has_a_google_calendar_and_weather_locations() -> None:
    assert set(COUNTRIES) == set(GOOGLE_CALENDAR_NAMES) == set(wx.LOCATIONS)


# ── Open-Meteo weather ─────────────────────────────────────────────────────────────────────────────────────


def _fake_open_meteo(url: str, country: str, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    days = pd.date_range(start, end, freq="D")
    doy = days.dayofyear.to_numpy()
    frames = []
    for i, (name, *_rest) in enumerate(wx.LOCATIONS[country]):
        frames.append(
            pd.DataFrame(
                {
                    "date": days,
                    "place": name,
                    "temperature_2m_mean": 10 - 9 * np.cos(2 * np.pi * doy / 365.25) + i + np.sin(np.arange(len(days))),
                    "sunshine_duration": 3600 * (6 - 3 * np.cos(2 * np.pi * doy / 365.25)),
                    "precipitation_sum": np.where(np.arange(len(days)) % 5 == 0, 6.0, 0.5),
                }
            )
        )
    return pd.concat(frames, ignore_index=True)


def test_weather_controls_are_population_weighted_surprises(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("MMM_EXTERNAL_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(wx, "_fetch", _fake_open_meteo)
    idx = pd.date_range("2022-01-01", periods=400, freq="D")
    out = wx.weather_controls("AT", idx)
    assert list(out.columns) == ["wx_temp_anomaly", "wx_sunshine_anomaly", "wx_precip_anomaly"]
    assert out.notna().all().all() and abs(out.mean()).max() < 0.05  # surprises around the seasonal normal
    assert out["wx_sunshine_anomaly"].abs().max() < 0.2  # a pure seasonal cycle leaves almost no surprise
    assert (tmp_path / "open_meteo_at_20220101_20230204.csv").exists()


def test_a_wide_upload_runs_the_analysis_pipeline_with_weather(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from mmm_studio.pipeline.context import RunContext
    from mmm_studio.pipeline.stages import run_full_pipeline
    from mmm_studio.storage import LocalStorage

    monkeypatch.setattr(wx, "_fetch", _fake_open_meteo)
    raw = wide_xlsx()
    _, prof = profile_workbook(raw, filename="mmm.xlsx")
    rc = prof.role_config
    rc.model_settings.weather_controls = True
    errors: list[str] = []
    skipped: list[str] = []
    ctx = RunContext(
        run_id="wide",
        storage=LocalStorage(tmp_path),
        run_profile="smoke",
        on_stage_error=lambda s, e: errors.append(f"{s}: {e}"),
        on_stage_skipped=lambda s, why: skipped.append(s),
    )
    ctx.config = rc
    ctx.cache.update(workbook_bytes=raw, workbook_filename="mmm.xlsx")
    summary = run_full_pipeline(ctx, skip_meridian=True)
    assert errors == []
    assert skipped == ["s04_channels", "s06_efficiency", "s08_catalog"]  # no channel sales, funnel metrics or print drop
    assert {"benchmark", "preparation", "explore_target", "explore_spend"} <= set(summary["sections"])
    wide = ctx.load_dataframe("wide_model")
    assert {"ctrl_covid", "wx_temp_anomaly"} <= set(wide.columns)
    roles = ctx.load_json("sections/preparation.json")
    controls = next(b for b in roles["blocks"] if b.get("id") == "prep_meridian_roles")["rows"][-1]["columns"]
    assert "ctrl_covid" in controls and "wx_precip_anomaly" in controls
