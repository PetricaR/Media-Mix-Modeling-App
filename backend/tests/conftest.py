"""Shared pytest fixtures for MMM Studio backend tests."""

from __future__ import annotations

import os
import shutil
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pandas as pd
import pytest
from long_data import golden_dict, long_xlsx

from mmm_studio.core.schema import DataProfile, DataRoleConfig
from mmm_studio.core.tables import build_daily_wide_table
from mmm_studio.core.workbook import profile_workbook

TESTS_DIR = Path(__file__).resolve().parent

# Holidays come from Google Calendar; tests read a snapshot of the German calendar (fetched 2026-11-07, 2021–2031)
# from a temporary copy of the fixture cache, so they run offline and never write into the fixtures
_EXTERNAL_CACHE = Path(tempfile.mkdtemp(prefix="mmm_external_"))
shutil.copytree(TESTS_DIR / "fixtures" / "external", _EXTERNAL_CACHE, dirs_exist_ok=True)
os.environ["MMM_EXTERNAL_CACHE_DIR"] = str(_EXTERNAL_CACHE)


@pytest.fixture(scope="session")
def golden_data() -> dict[str, Any]:
    return golden_dict()


@pytest.fixture(scope="session")
def fixture_bytes() -> bytes:
    return long_xlsx()


@pytest.fixture(scope="session")
def fixture_xlsx_path(tmp_path_factory: pytest.TempPathFactory, fixture_bytes: bytes) -> Path:
    path = tmp_path_factory.mktemp("data") / "sample_data.xlsx"
    path.write_bytes(fixture_bytes)
    return path


@pytest.fixture(scope="session")
def profiled_sample(fixture_bytes: bytes) -> tuple[pd.DataFrame, DataProfile]:
    return profile_workbook(fixture_bytes, filename="sample_data.xlsx")


@pytest.fixture(scope="session")
def wide_full(profiled_sample: tuple[pd.DataFrame, DataProfile]) -> pd.DataFrame:
    df, profile = profiled_sample
    return build_daily_wide_table(df, config=profile.role_config, drop_2025=False)


@pytest.fixture(scope="session")
def wide_model(profiled_sample: tuple[pd.DataFrame, DataProfile]) -> pd.DataFrame:
    """The modelling panel: the full calendar (2025-01-01 included)."""
    df, profile = profiled_sample
    return build_daily_wide_table(df, config=profile.role_config, drop_2025=profile.role_config.drop_trailing_isolated_days)


@pytest.fixture(scope="session")
def wide_2023_2024(profiled_sample: tuple[pd.DataFrame, DataProfile]) -> pd.DataFrame:
    df, profile = profiled_sample
    return build_daily_wide_table(df, config=profile.role_config, drop_2025=True)


def _reference_config(role_config: DataRoleConfig) -> DataRoleConfig:
    """`role_config` with the inputs and validation the golden numbers were recorded with: no missing-row controls and
    conventional hold-outs (the app's defaults add the controls and score every window leak-free)."""
    out = role_config.model_copy(deep=True)
    out.model_settings.missing_row_controls = False
    out.model_settings.leak_free_validation = False
    return out


@pytest.fixture(scope="session")
def reference_config() -> Callable[[DataRoleConfig], DataRoleConfig]:
    return _reference_config
