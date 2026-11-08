"""Unit and golden tests for workbook ingestion and profiling."""

from __future__ import annotations

from typing import Any

import pandas as pd
import pytest

from mmm_studio.core.schema import DataProfile
from mmm_studio.core.workbook import WorkbookValidationError, validate_upload_bytes


def test_profile_matches_golden(
    profiled_sample: tuple[pd.DataFrame, DataProfile],
    golden_data: dict[str, Any],
) -> None:
    df, profile = profiled_sample
    assert len(df) == golden_data["raw_row_count"]
    assert profile.raw_row_count == golden_data["raw_row_count"]
    assert profile.calendar_days == golden_data["calendar_days"]
    assert profile.start_date == golden_data["start_date"]
    assert profile.end_date == golden_data["end_date"]
    # The isolated 2025-01-01 stays in every model; it only leaves yearly and monthly views
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


def test_affiliate_role_shows_what_the_model_uses() -> None:
    """Affiliate is a commission channel and a Meridian control; a stored "paid" role (older runs) reads as commission."""
    from mmm_studio.core.meridian.spec import MeridianSpecConfig
    from mmm_studio.core.schema import extract_role_config

    cfg = extract_role_config({"role_config": {"channels": [{"name": "Affiliate", "role": "paid"}, {"name": "SEA", "role": "paid"}]}})
    roles = {c.name: c.role for c in cfg.channels}
    assert roles == {"Affiliate": "commission", "SEA": "paid"}
    index = pd.date_range("2023-01-01", periods=500, freq="D")
    assert MeridianSpecConfig.from_role_config(cfg, index=index).as_controls == ("affiliate",)

    # A user who deliberately models the commission channel as media is followed (and told why that is risky)
    cfg.channels[0].role = "paid"
    changed = extract_role_config({"role_config": cfg.model_dump(mode="json")})
    assert changed.channels[0].role == "paid"
    assert "biased upwards" in (changed.channels[0].rationale or "")
    assert MeridianSpecConfig.from_role_config(changed, index=index).as_controls == ()
