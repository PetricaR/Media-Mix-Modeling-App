"""Evaluation metrics and naive forecasting baselines."""

from __future__ import annotations

import numpy as np
import pandas as pd


def regression_metrics(y_true: np.ndarray | pd.Series, y_pred: np.ndarray | pd.Series) -> dict[str, float]:
    """Compute original-scale accuracy metrics for daily sales predictions."""
    yt = np.asarray(y_true, dtype=float)
    yp = np.asarray(y_pred, dtype=float)
    mask = ~(np.isnan(yt) | np.isnan(yp))
    yt = yt[mask]
    yp = yp[mask]
    err = yp - yt
    abs_err = np.abs(err)

    mae = float(np.mean(abs_err))
    rmse = float(np.sqrt(np.mean(err**2)))
    mean_y = float(np.mean(yt))
    mape = float(np.mean(abs_err / np.maximum(np.abs(yt), 1.0)))
    wmape = float(np.sum(abs_err) / np.maximum(np.sum(np.abs(yt)), 1.0))
    mae_over_mean = float(mae / mean_y) if mean_y != 0 else np.nan
    ss_res = float(np.sum(err**2))
    ss_tot = float(np.sum((yt - mean_y) ** 2))
    r2 = float(1.0 - ss_res / ss_tot) if ss_tot > 0 else np.nan
    bias = float(np.mean(err))

    return {
        "MAE": mae,
        "RMSE": rmse,
        "MAPE": mape,
        "WMAPE": wmape,
        "MAE_over_mean": mae_over_mean,
        "R2": r2,
        "bias": bias,
    }


def in_sample_naive_baselines(y: pd.Series) -> pd.DataFrame:
    """Compute full-series naive baseline metrics as in 01b_eda_mmm_deep_dive."""
    idx = pd.DatetimeIndex(y.index)
    mean_y = float(y.mean())

    def _eval(name: str, pred: pd.Series) -> dict[str, float | str]:
        # Scored on the days the forecast exists (lags drop the first days), as `evaluate.metrics_table` does.
        valid = pred.dropna()
        return {"model": name, **regression_metrics(y.loc[valid.index], valid)}

    pred_mean = pd.Series(mean_y, index=idx)
    pred_dow = y.groupby(idx.dayofweek).transform("mean")
    pred_lag7 = y.shift(7)
    pred_lag1 = y.shift(1)

    return pd.DataFrame(
        [
            _eval("Naive: constant mean", pred_mean),
            _eval("Naive: weekday mean", pred_dow),
            _eval("Naive: lag-7 (last week)", pred_lag7),
            _eval("Naive: lag-1 (yesterday, 1-step)", pred_lag1),
        ]
    )


def naive_baselines(
    y_train: pd.Series,
    y_test: pd.Series,
    dates_train: pd.DatetimeIndex,
    dates_test: pd.DatetimeIndex,
) -> pd.DataFrame:
    """Compute holdout naive baseline metrics (constant mean, weekday mean, lag-7, lag-1)."""
    rows: list[dict[str, float | str]] = []

    pred_mean = np.full(len(y_test), float(y_train.mean()))
    rows.append({"model": "Naive: constant mean", **regression_metrics(y_test, pred_mean)})

    dow_means = y_train.groupby(dates_train.dayofweek).mean()
    pred_dow = dates_test.dayofweek.map(dow_means).to_numpy(dtype=float)
    rows.append({"model": "Naive: weekday mean", **regression_metrics(y_test, pred_dow)})

    full = pd.concat([y_train, y_test])
    pred_lag7 = full.shift(7).loc[y_test.index].to_numpy(dtype=float)
    rows.append({"model": "Naive: lag-7 (last week)", **regression_metrics(y_test, pred_lag7)})

    pred_lag1 = full.shift(1).loc[y_test.index].to_numpy(dtype=float)
    rows.append({"model": "Naive: lag-1 (yesterday, 1-step)", **regression_metrics(y_test, pred_lag1)})

    return pd.DataFrame(rows)
