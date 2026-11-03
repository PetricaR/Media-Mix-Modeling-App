"""Registers the pipeline's example-plan forecast as a `ForecastRun`, so the API only ever reads it."""

from __future__ import annotations

import io
from typing import Any

import pandas as pd
from sqlalchemy.orm import Session

from mmm_studio.db.models import ForecastRun

DEFAULT_FORECAST_DAILY_KEYS = ("data/forecast_daily.parquet", "data/forecast_2025_01_daily.parquet")
DEFAULT_FORECAST_PLAN_KEYS = ("data/forecast_plan.parquet", "data/forecast_2025_01_plan.parquet")


def default_forecast_keys(run_id: str) -> tuple[list[str], list[str]]:
    """Daily-forecast and plan keys of the run's example forecast (current names first, then pre-rename names)."""
    return (
        [f"runs/{run_id}/{k}" for k in DEFAULT_FORECAST_DAILY_KEYS],
        [f"runs/{run_id}/{k}" for k in DEFAULT_FORECAST_PLAN_KEYS],
    )


def register_default_forecast(db: Session, storage: Any, run_id: str) -> ForecastRun | None:
    """Create (or refresh) the `default_{run_id}` ForecastRun from the pipeline's stage-s14 results.

    Called by the Meridian job when it finishes and by `mmm-studio migrate` for older runs; nothing is created before
    the forecast exists, so the page never shows a placeholder as a result.
    """
    from mmm_studio.core.meridian.forecast import scenario_payload

    default_id = f"default_{run_id}"
    fc = db.get(ForecastRun, default_id)
    daily_keys, plan_keys = default_forecast_keys(run_id)
    daily_key = next((k for k in daily_keys if storage.exists(k)), None)
    plan_key = next((k for k in plan_keys if storage.exists(k)), None)
    if daily_key is None or plan_key is None:
        if fc is not None:
            db.delete(fc)
            db.commit()
        return None

    base_daily = pd.read_parquet(io.BytesIO(storage.read_bytes(daily_key)))
    base_plan = pd.read_parquet(io.BytesIO(storage.read_bytes(plan_key)))
    summary_key = f"runs/{run_id}/data/forecast_summary.json"
    fc_summary = storage.read_json(summary_key) if storage.exists(summary_key) else {}
    total_pi = (
        (float(fc_summary["sales_total_lo"]), float(fc_summary["sales_total_hi"]))
        if fc_summary.get("sales_total_lo") is not None
        else None
    )
    res_payload = scenario_payload(
        base_daily,
        base_plan,
        base_daily,
        base_plan,
        "Meridian refit (pipeline example plan)",
        total_pi=total_pi,
        coverage=fc_summary.get("backtest_coverage"),
    )
    result_key = f"runs/{run_id}/forecasts/{default_id}/result.json"
    storage.write_json(result_key, res_payload)
    summary = {
        "source": "pipeline",
        "horizon_days": len(base_plan),
        "start_date": base_plan.index[0].strftime("%Y-%m-%d"),
        "end_date": base_plan.index[-1].strftime("%Y-%m-%d"),
        "total_forecast_sales": float(base_daily["forecast"].sum()),
        "total_planned_spend": float(base_plan.to_numpy().sum()),
        "prediction_total_lo": total_pi[0] if total_pi else None,
        "prediction_total_hi": total_pi[1] if total_pi else None,
    }
    name = f"Example plan {summary['start_date']} → {summary['end_date']} ({len(base_plan)} days)"
    if fc is None:
        fc = ForecastRun(id=default_id, run_id=run_id, name=name, status="succeeded", plan_path=plan_key, result_path=result_key, summary=summary)
        db.add(fc)
    else:
        fc.name, fc.status, fc.plan_path, fc.result_path, fc.summary, fc.error = name, "succeeded", plan_key, result_key, summary, None
    db.commit()
    db.refresh(fc)
    return fc
