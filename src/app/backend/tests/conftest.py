"""Shared pytest fixtures for MMM Studio backend tests."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from mmm_studio.core.schema import DataProfile, DataRoleConfig
from mmm_studio.core.tables import build_daily_wide_table
from mmm_studio.core.workbook import profile_workbook

TESTS_DIR = Path(__file__).resolve().parent
FIXTURE_XLSX = TESTS_DIR / "fixtures" / "case_study.xlsx"
GOLDEN_JSON = TESTS_DIR / "golden" / "case_study.json"


@pytest.fixture(scope="session")
def golden_data() -> dict[str, Any]:
    return json.loads(GOLDEN_JSON.read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def fixture_xlsx_path() -> Path:
    return FIXTURE_XLSX


@pytest.fixture(scope="session")
def fixture_bytes() -> bytes:
    return FIXTURE_XLSX.read_bytes()


@pytest.fixture(scope="session")
def profiled_case_study(fixture_bytes: bytes) -> tuple[pd.DataFrame, DataProfile]:
    return profile_workbook(fixture_bytes, filename="case_study.xlsx")


@pytest.fixture(scope="session")
def wide_full(profiled_case_study: tuple[pd.DataFrame, DataProfile]) -> pd.DataFrame:
    df, profile = profiled_case_study
    return build_daily_wide_table(df, config=profile.role_config, drop_2025=False)


@pytest.fixture(scope="session")
def wide_model(profiled_case_study: tuple[pd.DataFrame, DataProfile]) -> pd.DataFrame:
    """The modelling panel: the full calendar, as notebooks 02–05 use it (2025-01-01 included)."""
    df, profile = profiled_case_study
    return build_daily_wide_table(df, config=profile.role_config, drop_2025=profile.role_config.drop_trailing_isolated_days)


@pytest.fixture(scope="session")
def wide_2023_2024(profiled_case_study: tuple[pd.DataFrame, DataProfile]) -> pd.DataFrame:
    df, profile = profiled_case_study
    return build_daily_wide_table(df, config=profile.role_config, drop_2025=True)


def _notebook_parity(role_config: DataRoleConfig) -> DataRoleConfig:
    """`role_config` with the notebooks' exact inputs: no missing-row controls (the app's default adds them)."""
    out = role_config.model_copy(deep=True)
    out.model_settings.missing_row_controls = False
    return out


@pytest.fixture(scope="session")
def notebook_parity() -> Callable[[DataRoleConfig], DataRoleConfig]:
    return _notebook_parity
