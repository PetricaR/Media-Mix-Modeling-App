"""Unit and golden tests for workbook ingestion and profiling."""

from __future__ import annotations

from typing import Any

import pandas as pd
import pytest

from mmm_studio.core.schema import DataProfile
from mmm_studio.core.workbook import WorkbookValidationError, validate_upload_bytes


def test_profile_matches_golden(
    profiled_case_study: tuple[pd.DataFrame, DataProfile],
    golden_data: dict[str, Any],
) -> None:
    df, profile = profiled_case_study
    assert len(df) == golden_data["raw_row_count"]
    assert profile.raw_row_count == golden_data["raw_row_count"]
    assert profile.calendar_days == golden_data["calendar_days"]
    assert profile.start_date == golden_data["start_date"]
    assert profile.end_date == golden_data["end_date"]
    # The notebooks keep the isolated 2025-01-01 in every model; it only leaves yearly and monthly views
    assert profile.trailing_isolated_dates == ["2025-01-01"]
    assert profile.role_config.drop_trailing_isolated_days is False
    assert profile.role_config.holdout_days == 93
    assert profile.modelling_end_date == "2025-01-01"
    assert profile.modelling_days == golden_data["calendar_days"]

    rc = profile.role_config
    assert len(rc.active_channels) == golden_data["channel_count"]
    assert sorted(rc.paid_channels) == sorted(golden_data["paid_channels"])
    assert sorted(rc.unpaid_channels) == sorted(golden_data["unpaid_channels"])
    assert rc.print_event_channels == ["Catalog"]

    assert profile.missing_channel_days["TV"] == golden_data["missing_channel_days"]["TV"]
    assert profile.missing_channel_days["Catalog"] == golden_data["missing_channel_days"]["Catalog"]
    assert profile.missing_channel_days["Owned"] == golden_data["missing_channel_days"]["Owned"]
    assert sum(profile.missing_channel_days.values()) == golden_data["missing_channel_days"]["total"]
    assert profile.tv_low_sales_days == golden_data["tv_low_sales_days"]
    assert profile.catalog_sendout_days == golden_data["catalog_sendout_days"]


def test_upload_validation_rejects_invalid() -> None:
    with pytest.raises(WorkbookValidationError):
        validate_upload_bytes("empty.xlsx", b"")
    with pytest.raises(WorkbookValidationError):
        validate_upload_bytes("malicious.exe", b"hello")
    with pytest.raises(WorkbookValidationError):
        validate_upload_bytes("corrupt.xlsx", b"not-a-zip")
