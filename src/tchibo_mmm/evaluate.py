"""Forecast metrics. The brief's KPI is MAE / mean(actual), which equals Meridian's wMAPE."""

import numpy as np
import pandas as pd

TARGET_MAE_RATIO = 0.15


def metrics(actual, predicted) -> dict[str, float]:
    y = np.asarray(actual, dtype=float)
    p = np.asarray(predicted, dtype=float)
    err = y - p
    mae = np.mean(np.abs(err))
    return {
        "MAE": mae,
        "MAE/mean": mae / np.mean(y),
        "RMSE": np.sqrt(np.mean(err**2)),
        "R2": 1 - np.sum(err**2) / np.sum((y - y.mean()) ** 2),
        "MAPE": np.mean(np.abs(err) / y),
        "bias": np.mean(p - y),
    }


def metrics_table(actual: pd.Series, predictions: dict[str, pd.Series]) -> pd.DataFrame:
    """One row per model, aligned on the index of each prediction (NaNs dropped)."""
    rows = {}
    for name, pred in predictions.items():
        pred = pred.dropna()
        rows[name] = metrics(actual.loc[pred.index], pred)
    table = pd.DataFrame(rows).T
    table["passes_15pct"] = table["MAE/mean"] <= TARGET_MAE_RATIO
    return table


def naive_baselines(y: pd.Series) -> dict[str, pd.Series]:
    """Simple reference forecasts over the full period."""
    return {
        "constant mean": pd.Series(y.mean(), index=y.index),
        "weekday mean": y.groupby(y.index.dayofweek).transform("mean"),
        "last week (lag 7)": y.shift(7),
        "yesterday (lag 1)": y.shift(1),
    }
