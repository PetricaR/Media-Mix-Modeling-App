"""A synthetic long MMM table (one row per day and channel) and expected metrics for the test suite."""

from __future__ import annotations

import copy
import functools
import io
from typing import Any

import numpy as np
import openpyxl
import pandas as pd


@functools.lru_cache(maxsize=1)
def _build_rows() -> list[tuple[Any, ...]]:
    rng = np.random.default_rng(20260510)
    dates = pd.date_range("2023-01-01", "2025-01-01", freq="D")
    n_days = len(dates)
    t = np.arange(n_days, dtype=float)
    doy = dates.dayofyear.to_numpy(dtype=float)
    dow = dates.dayofweek.to_numpy()
    year = dates.year.to_numpy()
    month = dates.month.to_numpy()

    thursdays = [d for d in dates if d.dayofweek == 3 and not (d.year == 2023 and d.month == 9) and d.year < 2025]
    chosen_idx = np.linspace(0, len(thursdays) - 1, 26, dtype=int)
    sendout_dates = {thursdays[i] for i in chosen_idx}
    sendout_dates.add(pd.Timestamp("2024-11-14"))
    while len(sendout_dates) > 26:
        for d in sorted(sendout_dates):
            if d != pd.Timestamp("2024-11-14"):
                sendout_dates.remove(d)
                break
    sendout_dates.add(pd.Timestamp("2023-05-10"))
    sendout_dates.add(pd.Timestamp("2024-03-29"))

    catalog_cost = np.zeros(n_days)
    catalog_impr = np.zeros(n_days)
    catalog_clicks = np.zeros(n_days)
    for i, d in enumerate(dates):
        if d in sendout_dates:
            yr_mult = 1.06 if d.year == 2024 else 1.0
            q4_mult = 1.12 if d.month in (10, 11) else 1.0
            copies = np.round(rng.normal(1_850_000, 140_000) * q4_mult)
            cpc_copy = (0.68 if d.year == 2023 else 0.74) * rng.normal(1.0, 0.02)
            catalog_impr[i] = max(1_200_000.0, copies)
            catalog_cost[i] = round(catalog_impr[i] * cpc_copy * yr_mult, 2)
            catalog_clicks[i] = round(catalog_impr[i] * rng.uniform(0.018, 0.024))

    irf_weights = np.array([0.02, 0.22, 0.45, 0.52, 0.34, 0.22, 0.14, 0.09, 0.05, 0.03, 0.01])
    cat_norm = catalog_cost / max(catalog_cost[catalog_cost > 0].mean(), 1.0)
    cat_pulse = np.convolve(cat_norm, irf_weights, mode="full")[:n_days]

    season = (
        1.0
        + 0.16 * np.cos(2 * np.pi * (doy - 325) / 365.25)
        + 0.08 * np.sin(4 * np.pi * doy / 365.25)
    )
    sep_dip = np.where((year == 2023) & (month == 9), 0.72, 1.0)
    black_week = np.where((month == 11) & (dates.day >= 22) & (dates.day <= 29), 1.35, 1.0)
    xmas_bump = np.where((month == 12) & (dates.day <= 20), 1.22, 1.0)
    post_xmas = np.where((month == 12) & (dates.day >= 24), 0.76, 1.0)
    trend_mult = 1.0 + 0.22 * (t / n_days)

    dow_demand = np.array([1.08, 1.03, 1.00, 0.99, 0.94, 0.91, 1.05])[dow]
    demand_index = season * sep_dip * black_week * xmas_bump * post_xmas * trend_mult * dow_demand
    demand_noise = rng.normal(1.0, 0.045, n_days)
    latent_demand = demand_index * demand_noise

    sea_dow = np.array([1.14, 1.05, 1.02, 1.00, 0.92, 0.86, 1.01])[dow]
    sea_spend = (36_000.0 * (0.45 + 0.55 * latent_demand) * sea_dow + 6_500.0 * cat_pulse) * rng.normal(1.0, 0.05, n_days)
    sea_clicks = (sea_spend / 0.34) * rng.normal(1.0, 0.025, n_days)
    sea_impr = sea_clicks * rng.normal(19.5, 1.2, n_days)

    psm_dow = np.array([1.06, 1.04, 1.01, 0.99, 0.95, 0.92, 1.03])[dow]
    psm_spend = (8_200.0 * (0.50 + 0.50 * latent_demand) * psm_dow + 1_400.0 * cat_pulse) * rng.normal(1.0, 0.06, n_days)
    psm_clicks = (psm_spend / 0.29) * rng.normal(1.0, 0.025, n_days)
    psm_impr = psm_clicks * rng.normal(14.0, 1.0, n_days)

    disp_dow = np.array([1.05, 1.04, 1.03, 1.02, 0.98, 0.93, 0.95])[dow]
    disp_spend = (22_000.0 * (0.55 + 0.45 * season * trend_mult) * disp_dow) * rng.normal(1.0, 0.09, n_days)
    disp_impr = (disp_spend / 0.00052) * rng.normal(1.0, 0.18, n_days)
    disp_clicks = (disp_impr * 0.0028 * (0.6 + 0.4 * latent_demand)) * rng.normal(1.0, 0.25, n_days)

    dnet_dow = np.array([1.03, 1.02, 1.02, 1.01, 0.99, 0.96, 0.97])[dow]
    dnet_spend = (9_500.0 * (0.60 + 0.40 * season * trend_mult) * dnet_dow) * rng.normal(1.0, 0.10, n_days)
    dnet_impr = (dnet_spend / 0.00065) * rng.normal(1.0, 0.18, n_days)
    dnet_clicks = (dnet_impr * 0.0031 * (0.6 + 0.4 * latent_demand)) * rng.normal(1.0, 0.25, n_days)

    soc_dow = np.array([1.75, 0.92, 0.88, 0.88, 0.85, 0.84, 0.88])[dow]
    soc_regime = np.where(dates >= pd.Timestamp("2024-08-01"), 2.55, 1.0)
    soc_spend = (7_800.0 * soc_regime * soc_dow * (0.75 + 0.25 * season)) * rng.normal(1.0, 0.08, n_days)
    soc_impr = (soc_spend / 0.0018) * rng.normal(1.0, 0.22, n_days)
    soc_clicks = (soc_impr * 0.0085 * (0.5 + 0.5 * latent_demand)) * rng.normal(1.0, 0.32, n_days)

    def adstock_geom(x: np.ndarray, decay: float) -> np.ndarray:
        out = np.zeros_like(x)
        for i in range(len(x)):
            out[i] = x[i] + (decay * out[i - 1] if i > 0 else 0.0)
        return out

    sea_ad = adstock_geom(sea_spend, 0.25)
    psm_ad = adstock_geom(psm_spend, 0.20)
    disp_ad = adstock_geom(disp_spend, 0.35)
    dnet_ad = adstock_geom(dnet_spend, 0.30)
    soc_ad = adstock_geom(soc_spend, 0.30)

    sales_sea = (115_000.0 * latent_demand + 2.35 * sea_ad + 42_000.0 * cat_pulse) * rng.normal(1.0, 0.04, n_days)
    sales_psm = (29_000.0 * latent_demand + 1.65 * psm_ad + 9_000.0 * cat_pulse) * rng.normal(1.0, 0.05, n_days)
    sales_disp = (58_000.0 * latent_demand + 0.95 * disp_ad + 16_000.0 * cat_pulse) * rng.normal(1.0, 0.05, n_days)
    sales_dnet = (26_000.0 * latent_demand + 1.10 * dnet_ad + 7_500.0 * cat_pulse) * rng.normal(1.0, 0.05, n_days)
    sales_soc = (
        24_000.0 * latent_demand * np.where(dates >= pd.Timestamp("2024-08-01"), 1.45, 1.0)
        + 0.75 * soc_ad
        + 14_000.0 * cat_pulse
    ) * rng.normal(1.0, 0.05, n_days)
    sales_cat = (18_000.0 * latent_demand + 165_000.0 * cat_pulse) * rng.normal(1.0, 0.06, n_days)

    aff_dow = np.array([0.92, 0.90, 0.91, 0.92, 0.88, 0.85, 1.62])[dow]
    sales_aff = (52_000.0 * latent_demand * aff_dow + 21_000.0 * cat_pulse) * rng.normal(1.0, 0.045, n_days)
    aff_rate = np.where(dates >= pd.Timestamp("2024-02-01"), 0.118, 0.062) * rng.normal(1.0, 0.03, n_days)
    aff_spend = sales_aff * aff_rate
    aff_clicks = (sales_aff / 18.5) * rng.normal(1.0, 0.05, n_days)
    aff_impr = aff_clicks * rng.normal(22.0, 1.5, n_days)

    sales_direct_typein = (96_000.0 * latent_demand + 48_000.0 * cat_pulse) * rng.normal(1.0, 0.045, n_days)
    sales_seo = (82_000.0 * latent_demand + 44_000.0 * cat_pulse) * rng.normal(1.0, 0.045, n_days)
    sales_owned = (64_000.0 * latent_demand + 24_000.0 * cat_pulse) * rng.normal(1.0, 0.05, n_days)
    sales_direct_ref = (34_000.0 * latent_demand + 11_000.0 * cat_pulse) * rng.normal(1.0, 0.05, n_days)
    sales_tv = (14_000.0 * latent_demand + 6_000.0 * cat_pulse) * rng.normal(1.0, 0.07, n_days)

    tv_missing_idx = set(rng.choice(np.arange(30, 520), size=38, replace=False).tolist())
    tv_available = [i for i in range(50, 500) if i not in tv_missing_idx]
    tv_low_idx = set(rng.choice(tv_available, size=8, replace=False).tolist())
    for idx_low in tv_low_idx:
        sales_tv[idx_low] = round(float(rng.uniform(0.15, 0.85)), 4)

    cat_non_send = [i for i in range(20, 500) if catalog_cost[i] == 0]
    cat_missing_idx = set(rng.choice(cat_non_send, size=4, replace=False).tolist())
    owned_missing_idx = {120, 310}

    rows: list[tuple[Any, ...]] = []
    channels_data = [
        ("Affiliate", sales_aff, aff_impr, aff_clicks, aff_spend, set()),
        ("Catalog", sales_cat, catalog_impr, catalog_clicks, catalog_cost, cat_missing_idx),
        ("Direct Referred", sales_direct_ref, None, None, None, set()),
        ("Direct Type-in", sales_direct_typein, None, None, None, set()),
        ("Display", sales_disp, disp_impr, disp_clicks, disp_spend, set()),
        ("Display Network", sales_dnet, dnet_impr, dnet_clicks, dnet_spend, set()),
        ("Owned", sales_owned, None, None, None, owned_missing_idx),
        ("PSM", sales_psm, psm_impr, psm_clicks, psm_spend, set()),
        ("SEA", sales_sea, sea_impr, sea_clicks, sea_spend, set()),
        ("SEO", sales_seo, None, None, None, set()),
        ("Social Media", sales_soc, soc_impr, soc_clicks, soc_spend, set()),
        ("TV", sales_tv, None, None, None, tv_missing_idx),
    ]

    for i, d in enumerate(dates):
        dt_val = d.to_pydatetime()
        for ch_name, s_arr, imp_arr, clk_arr, cst_arr, miss_set in channels_data:
            if i in miss_set:
                continue
            s_val = round(float(s_arr[i]), 4)
            if ch_name == "Catalog":
                imp_val = int(round(imp_arr[i])) if imp_arr[i] > 0 else 0
                clk_val = int(round(clk_arr[i])) if clk_arr[i] > 0 else 0
                cst_val = round(float(cst_arr[i]), 2) if cst_arr[i] > 0 else 0.0
            elif cst_arr is not None:
                imp_val = int(round(imp_arr[i]))
                clk_val = int(round(clk_arr[i]))
                cst_val = round(float(cst_arr[i]), 2)
            else:
                imp_val = None
                clk_val = None
                cst_val = None
            rows.append((dt_val, ch_name, s_val, imp_val, clk_val, cst_val))

    order = rng.permutation(len(rows))
    return [rows[idx] for idx in order]


@functools.lru_cache(maxsize=1)
def long_xlsx() -> bytes:
    """Build the synthetic two-sheet (`data`, `info`) long-table `.xlsx` workbook in memory."""
    wb = openpyxl.Workbook()
    wb.properties.creator = "MMM Studio"
    wb.properties.title = "Sample Marketing Mix Dataset"
    ws_data = wb.active
    ws_data.title = "data"
    ws_data.append(["date", "channel", "sales_by_channel", "impressions", "clicks", "costs"])
    for r in _build_rows():
        ws_data.append(list(r))

    ws_info = wb.create_sheet("info")
    ws_info.append(["Column", "Explanations"])
    ws_info.append(["date", "Date of measurement for impressions, clicks, and sales"])
    ws_info.append(["channel", "Marketing channel name"])
    ws_info.append(["sales_by_channel", "Attributed revenue of the marketing channel (EUR)"])
    ws_info.append(["impressions", "Number of impressions of the marketing channel (for Catalog: print circulation)"])
    ws_info.append(["clicks", "Number of marketing channel clicks"])
    ws_info.append(["costs", "Daily marketing spend booked on the date of causation (EUR)"])

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def long_frame() -> pd.DataFrame:
    """Return a fresh DataFrame of the `data` sheet."""
    return pd.DataFrame(
        _build_rows(),
        columns=["date", "channel", "sales_by_channel", "impressions", "clicks", "costs"],
    )


GOLDEN_DATA: dict[str, Any] = {
    "raw_row_count": 8740,
    "calendar_days": 732,
    "start_date": "2023-01-01",
    "end_date": "2025-01-01",
    "channel_count": 12,
    "paid_channel_count": 7,
    "unpaid_channel_count": 5,
    "paid_channels": [
        "Affiliate",
        "Catalog",
        "Display",
        "Display Network",
        "PSM",
        "SEA",
        "Social Media",
    ],
    "unpaid_channels": [
        "Direct Referred",
        "Direct Type-in",
        "Owned",
        "SEO",
        "TV",
    ],
    "missing_channel_days": {
        "TV": 38,
        "Catalog": 4,
        "Owned": 2,
        "total": 44,
    },
    "tv_low_sales_days": 8,
    "catalog_sendout_days": 28,
    "catalog_sendout_weekdays": {
        "Thursday": 26,
        "Wednesday": 1,
        "Friday": 1,
    },
    "target_summary": {
        "mean_daily_sales": 919991.6,
        "std_daily_sales": 182361.1,
        "min_daily_sales": 572849.2,
        "max_daily_sales": 1620386.7,
    },
    "yoy_growth": {
        "sales_growth_2024_vs_2023_pct": 14.5,
        "spend_growth_2024_vs_2023_pct": 20.8,
        "paid_share_attributed_sales_pct": 63.8,
    },
    "same_day_pearson_corr_cost_vs_total_sales": {
        "Display": 0.44,
        "Display Network": 0.41,
        "PSM": 0.85,
        "SEA": 0.85,
        "Social Media": 0.59,
        "Catalog": -0.06,
        "Affiliate": 0.67,
    },
    "catalog_lag_3_corr": 0.28,
    "naive_baselines_mae_over_mean": {
        "constant_mean": 0.157,
        "weekday_mean": 0.149,
        "lag_7": 0.1,
        "lag_1": 0.081,
    },
    "digital_vif_range": {
        "min": 1.29,
        "max": 3.89,
    },
    "halo_uplift_7d_pct": {
        "total_sales": 12.4,
        "SEO": 11.5,
        "Social Media": 12.0,
        "Direct Type-in": 11.0,
        "Affiliate": 8.5,
        "SEA": 6.2,
    },
    "ols_in_sample_adj_r2_ladder": {
        "raw_costs": 0.83,
        "plus_calendar": 0.91,
        "plus_seasonality_trend": 0.92,
        "catalog_in_home_adstock": 0.95,
    },
    "ols_benchmark_mae_over_mean": {
        "q4_2024": {
            "Naive weekday mean": 0.24,
            "OLS-1 raw costs": 0.071,
            "OLS-2 Meridian inputs": 0.037,
        },
        "q3_2024": {
            "Naive weekday mean": 0.149,
            "OLS-1 raw costs": 0.136,
            "OLS-2 Meridian inputs": 0.045,
        },
        "walk_forward_mean": {
            "Naive weekday mean": 0.155,
            "OLS-1 raw costs": 0.078,
            "OLS-2 Meridian inputs": 0.058,
        },
    },
    "preparation": {
        "importance_ols_r2": 0.948,
        "best_catalog_shift_k": [1, 2],
        "max_digital_vif": 3.89,
    },
    "forecast_reference": {
        "plan_start": "2025-01-02",
        "plan_days": 42,
        "plan_total_spend": 7776891,
    },
}


def golden_dict() -> dict[str, Any]:
    return copy.deepcopy(GOLDEN_DATA)
