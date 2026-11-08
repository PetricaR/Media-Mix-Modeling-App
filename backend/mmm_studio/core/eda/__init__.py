"""Exploratory Data Analysis (EDA) modules."""

from mmm_studio.core.eda.catalog import build_explore_catalog_section, compute_halo_uplift_7d
from mmm_studio.core.eda.channels import build_explore_channels_section
from mmm_studio.core.eda.correlations import build_explore_correlations_section, compute_vif_table
from mmm_studio.core.eda.efficiency import build_explore_efficiency_section
from mmm_studio.core.eda.quality import build_data_quality_section
from mmm_studio.core.eda.readiness import (
    build_explore_readiness_section,
    compute_meridian_pre_modeling_checks,
)
from mmm_studio.core.eda.spend import build_explore_spend_section
from mmm_studio.core.eda.target import build_explore_target_section

__all__ = [
    "build_data_quality_section",
    "build_explore_target_section",
    "build_explore_channels_section",
    "build_explore_spend_section",
    "build_explore_efficiency_section",
    "build_explore_correlations_section",
    "compute_vif_table",
    "build_explore_catalog_section",
    "compute_halo_uplift_7d",
    "build_explore_readiness_section",
    "compute_meridian_pre_modeling_checks",
]
