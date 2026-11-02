"""Shared pytest fixtures for MMM Studio backend tests."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from mmm_studio.core.schema import DataProfile
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
def wide_2023_2024(profiled_case_study: tuple[pd.DataFrame, DataProfile]) -> pd.DataFrame:
    df, profile = profiled_case_study
    return build_daily_wide_table(df, config=profile.role_config, drop_2025=True)
