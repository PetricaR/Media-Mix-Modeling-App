"""Procrastinate background tasks for `analysis`, `meridian`, and `interactive` queues."""

from __future__ import annotations

import io
import logging
import threading
import time
from collections import OrderedDict
from typing import Any

import pandas as pd
from procrastinate import JobContext

from mmm_studio.core.schema import DataProfile, DataRoleConfig
from mmm_studio.db.models import ForecastRun, OptimisationRun, Run, ScenarioRun, StageProgress, utcnow
from mmm_studio.db.session import get_session_factory
from mmm_studio.pipeline.context import RunContext
from mmm_studio.pipeline.stages import (
    STAGE_DEFINITIONS,
    run_analysis_stages,
    run_meridian_stages,
)
from mmm_studio.storage import get_storage

logger = logging.getLogger(__name__)

# Warm LRU cache (max 2 runs) on the interactive worker
_INTERACTIVE_CACHE: OrderedDict[str, dict[str, Any]] = OrderedDict()
_CACHE_LOCK = threading.Lock()


def _cache_get(key: str) -> dict[str, Any] | None:
    with _CACHE_LOCK:
        if key in _INTERACTIVE_CACHE:
            _INTERACTIVE_CACHE.move_to_end(key)
            return _INTERACTIVE_CACHE[key]
        return None


def _cache_put(key: str, value: dict[str, Any]) -> None:
    with _CACHE_LOCK:
        _INTERACTIVE_CACHE[key] = value
        _INTERACTIVE_CACHE.move_to_end(key)
        while len(_INTERACTIVE_CACHE) > 2:
            _INTERACTIVE_CACHE.popitem(last=False)


def ensure_stage_rows(run_id: str, skip_meridian: bool = False, sqlalchemy_url: str | None = None) -> None:
    """Create initial StageProgress rows for a Run if not already present."""
    SessionLocal = get_session_factory(sqlalchemy_url)
    with SessionLocal() as db:
        existing = {
            s.key for s in db.query(StageProgress).filter(StageProgress.run_id == run_id).all()
        }
        for idx, (key, label, queue) in enumerate(STAGE_DEFINITIONS):
            if key not in existing:
                status = "skipped" if (skip_meridian and queue == "meridian") else "pending"
                db.add(
                    StageProgress(
                        run_id=run_id,
                        order_idx=idx,
                        key=key,
                        label=label,
                        queue=queue,
                        status=status,
                    )
                )
        db.commit()


class _HeartbeatMonitor:
    """Background daemon thread that updates `heartbeat_at` and checks for job cancellation."""

    def __init__(
        self,
        model_cls: type[Run] | type[ForecastRun],
        record_id: str,
        job_context: JobContext | None = None,
        interval_sec: float = 5.0,
        sqlalchemy_url: str | None = None,
    ) -> None:
        self.model_cls = model_cls
        self.record_id = record_id
        self.job_context = job_context
        self.interval_sec = interval_sec
        self.sqlalchemy_url = sqlalchemy_url
        self._stop_event = threading.Event()
        self.cancelled = False
        self._thread = threading.Thread(target=self._loop, daemon=True)

    def start(self) -> None:
        self.touch()
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        self._thread.join(timeout=2.0)

    def touch(self) -> None:
        SessionLocal = get_session_factory(self.sqlalchemy_url)
        try:
            with SessionLocal() as db:
                rec = db.get(self.model_cls, self.record_id)
                if rec is not None:
                    if rec.status == "cancelled":
                        self.cancelled = True
                    else:
                        rec.heartbeat_at = utcnow()
                        db.commit()
        except Exception:
            pass
        if self.job_context is not None:
            try:
                if self.job_context.should_abort():
                    self.cancelled = True
            except Exception:
                pass

    def _loop(self) -> None:
        while not self._stop_event.wait(self.interval_sec):
            self.touch()


def _build_run_context(
    run_id: str,
    hb: _HeartbeatMonitor,
    sqlalchemy_url: str | None = None,
) -> RunContext:
    SessionLocal = get_session_factory(sqlalchemy_url)
    storage = get_storage()

    with SessionLocal() as db:
        run_rec = db.get(Run, run_id)
        if run_rec is None:
            raise ValueError(f"Run {run_id} not found.")
        run_profile = run_rec.profile or "quick"
        seed = int((run_rec.config or {}).get("seed", 0))
        profile_obj = DataProfile.model_validate(run_rec.schema_json) if run_rec.schema_json else None
        config_obj = (
            DataRoleConfig.model_validate(run_rec.config["role_config"])
            if run_rec.config and "role_config" in run_rec.config
            else (profile_obj.role_config if profile_obj else None)
        )

    def on_stage_start(stage_key: str) -> None:
        hb.touch()
        with SessionLocal() as sdb:
            st = (
                sdb.query(StageProgress)
                .filter(StageProgress.run_id == run_id, StageProgress.key == stage_key)
                .first()
            )
            if st:
                st.status = "running"
                st.started_at = utcnow()
                st.error = None
                sdb.commit()

    def on_stage_complete(stage_key: str) -> None:
        hb.touch()
        with SessionLocal() as sdb:
            st = (
                sdb.query(StageProgress)
                .filter(StageProgress.run_id == run_id, StageProgress.key == stage_key)
                .first()
            )
            if st:
                st.status = "succeeded"
                st.finished_at = utcnow()
                sdb.commit()

    def on_stage_error(stage_key: str, err_msg: str) -> None:
        hb.touch()
        with SessionLocal() as sdb:
            st = (
                sdb.query(StageProgress)
                .filter(StageProgress.run_id == run_id, StageProgress.key == stage_key)
                .first()
            )
            if st:
                st.status = "failed"
                st.finished_at = utcnow()
                st.error = err_msg
                sdb.commit()

    return RunContext(
        run_id=run_id,
        storage=storage,
        profile=profile_obj,
        config=config_obj,
        run_profile=run_profile,  # type: ignore[arg-type]
        seed=seed,
        on_stage_start=on_stage_start,
        on_stage_complete=on_stage_complete,
        on_stage_error=on_stage_error,
        on_heartbeat=hb.touch,
        is_cancelled=lambda: hb.cancelled,
    )


def execute_analysis_job(
    run_id: str,
    skip_meridian: bool = False,
    job_context: JobContext | None = None,
    sqlalchemy_url: str | None = None,
) -> None:
    """Synchronous implementation of the `analysis` queue task."""
    SessionLocal = get_session_factory(sqlalchemy_url)
    ensure_stage_rows(run_id, skip_meridian=skip_meridian, sqlalchemy_url=sqlalchemy_url)

    with SessionLocal() as db:
        run_rec = db.get(Run, run_id)
        if run_rec is None or run_rec.status == "cancelled":
            return
        run_rec.status = "running"
        run_rec.heartbeat_at = utcnow()
        db.commit()

    hb = _HeartbeatMonitor(Run, run_id, job_context=job_context, sqlalchemy_url=sqlalchemy_url)
    hb.start()
    t0 = time.monotonic()
    try:
        ctx = _build_run_context(run_id, hb, sqlalchemy_url=sqlalchemy_url)
        run_analysis_stages(ctx)

        elapsed = round(time.monotonic() - t0, 2)
        idx_key = ctx.key("sections/_index.json")
        sections = ctx.storage.read_json(idx_key) if ctx.storage.exists(idx_key) else []
        summary = {
            "run_id": run_id,
            "run_profile": ctx.run_profile,
            "skip_meridian": skip_meridian,
            "analysis_elapsed_seconds": elapsed,
            "n_sections": len(sections),
            "sections": [s["id"] for s in sections],
        }
        ctx.save_json("meta/summary.json", summary)

        with SessionLocal() as db:
            run_rec = db.get(Run, run_id)
            if run_rec and run_rec.status != "cancelled":
                run_rec.summary = summary
                run_rec.heartbeat_at = utcnow()
                if skip_meridian:
                    run_rec.status = "succeeded"
                    has_active = db.query(Run).filter(Run.is_active.is_(True)).first() is not None
                    if not has_active:
                        run_rec.is_active = True
                db.commit()
    except InterruptedError:
        with SessionLocal() as db:
            run_rec = db.get(Run, run_id)
            if run_rec:
                run_rec.status = "cancelled"
                db.commit()
    except Exception as exc:
        logger.exception("Analysis job failed for run %s", run_id)
        with SessionLocal() as db:
            run_rec = db.get(Run, run_id)
            if run_rec:
                run_rec.status = "failed"
                run_rec.error = str(exc)
                db.commit()
        raise
    finally:
        hb.stop()


def execute_meridian_job(
    run_id: str,
    job_context: JobContext | None = None,
    sqlalchemy_url: str | None = None,
) -> None:
    """Synchronous implementation of the `meridian` queue task."""
    SessionLocal = get_session_factory(sqlalchemy_url)
    with SessionLocal() as db:
        run_rec = db.get(Run, run_id)
        if run_rec is None or run_rec.status in ("cancelled", "failed"):
            return
        run_rec.status = "running"
        run_rec.heartbeat_at = utcnow()
        db.commit()

    hb = _HeartbeatMonitor(Run, run_id, job_context=job_context, sqlalchemy_url=sqlalchemy_url)
    hb.start()
    t0 = time.monotonic()
    try:
        ctx = _build_run_context(run_id, hb, sqlalchemy_url=sqlalchemy_url)
        # Ensure analysis stages completed first if wide_2023_2024 is not yet in storage
        if not ctx.storage.exists(ctx.key("data/wide_2023_2024.parquet")):
            run_analysis_stages(ctx)

        run_meridian_stages(ctx)
        elapsed = round(time.monotonic() - t0, 2)
        idx_key = ctx.key("sections/_index.json")
        sections = ctx.storage.read_json(idx_key) if ctx.storage.exists(idx_key) else []
        meridian_metrics = (
            ctx.load_json("meta/meridian_metrics.json")
            if ctx.storage.exists(ctx.key("meta/meridian_metrics.json"))
            else {}
        )
        summary = {
            "run_id": run_id,
            "run_profile": ctx.run_profile,
            "skip_meridian": False,
            "meridian_elapsed_seconds": elapsed,
            "n_sections": len(sections),
            "sections": [s["id"] for s in sections],
            "holdout_q4_2024": meridian_metrics.get("holdout_q4_2024"),
            "holdout_q3_2024": meridian_metrics.get("holdout_q3_2024"),
            "max_rhat": meridian_metrics.get("max_rhat"),
        }
        ctx.save_json("meta/summary.json", summary)

        with SessionLocal() as db:
            run_rec = db.get(Run, run_id)
            if run_rec and run_rec.status != "cancelled":
                run_rec.summary = summary
                run_rec.status = "succeeded"
                run_rec.heartbeat_at = utcnow()
                has_active = db.query(Run).filter(Run.is_active.is_(True)).first() is not None
                if not has_active:
                    run_rec.is_active = True
                db.commit()
    except InterruptedError:
        with SessionLocal() as db:
            run_rec = db.get(Run, run_id)
            if run_rec:
                run_rec.status = "cancelled"
                db.commit()
    except Exception as exc:
        logger.exception("Meridian job failed for run %s", run_id)
        with SessionLocal() as db:
            run_rec = db.get(Run, run_id)
            if run_rec:
                run_rec.status = "failed"
                run_rec.error = str(exc)
                db.commit()
        raise
    finally:
        hb.stop()


def execute_forecast_job(
    forecast_id: str,
    job_context: JobContext | None = None,
    sqlalchemy_url: str | None = None,
) -> None:
    """Execute a budget-plan forecast refit on the `meridian` queue."""
    from mmm_studio.core.meridian.fit import FAST_SAMPLING, SMOKE_SAMPLING
    from mmm_studio.core.meridian.forecast import (
        fast_forecast_from_multipliers,
        forecast_budget_plan,
        validate_plan,
    )
    from mmm_studio.core.meridian.spec import MeridianSpecConfig, save_meridian_model

    SessionLocal = get_session_factory(sqlalchemy_url)
    storage = get_storage()

    with SessionLocal() as db:
        fc = db.get(ForecastRun, forecast_id)
        if fc is None or fc.status == "cancelled":
            return
        run_id = fc.run_id
        plan_path = fc.plan_path
        run_rec = db.get(Run, run_id)
        run_profile = run_rec.profile if run_rec else "quick"
        fc.status = "running"
        fc.heartbeat_at = utcnow()
        db.commit()

    hb = _HeartbeatMonitor(ForecastRun, forecast_id, job_context=job_context, sqlalchemy_url=sqlalchemy_url)
    hb.start()
    try:
        raw_plan = storage.read_bytes(plan_path)
        plan_df = pd.read_csv(io.BytesIO(raw_plan))
        plan_clean = validate_plan(plan_df)

        wide_bytes = storage.read_bytes(f"runs/{run_id}/data/wide_2023_2024.parquet")
        wide_2023_2024 = pd.read_parquet(io.BytesIO(wide_bytes))

        # Check if user requested fast multiplier mode or full refit
        sampling = SMOKE_SAMPLING if run_profile == "smoke" else FAST_SAMPLING
        spec_cfg = MeridianSpecConfig()

        # If a default forecast already exists on the exact same date index, or if we refit:
        default_fc_key = f"runs/{run_id}/data/forecast_2025_01_daily.parquet"
        default_plan_key = f"runs/{run_id}/data/forecast_2025_01_plan.parquet"
        if run_profile == "smoke" and storage.exists(default_fc_key) and storage.exists(default_plan_key):
            base_daily = pd.read_parquet(io.BytesIO(storage.read_bytes(default_fc_key)))
            base_plan = pd.read_parquet(io.BytesIO(storage.read_bytes(default_plan_key)))
            if len(plan_clean) <= len(base_daily):
                res = fast_forecast_from_multipliers(
                    base_daily=base_daily,
                    base_plan=base_plan,
                    multipliers={},
                    horizon_days=len(plan_clean),
                )
                result_key = f"runs/{run_id}/forecasts/{forecast_id}/result.json"
                storage.write_json(result_key, res)
                with SessionLocal() as db:
                    fc = db.get(ForecastRun, forecast_id)
                    if fc:
                        fc.status = "succeeded"
                        fc.result_path = result_key
                        fc.summary = {
                            "horizon_days": res["horizon_days"],
                            "total_forecast_sales": res["scenario_total_sales"],
                            "total_planned_spend": res["scenario_total_spend"],
                        }
                        db.commit()
                return

        fc_daily, fc_mmm, _ = forecast_budget_plan(
            wide_2023_2024,
            plan_clean,
            cfg=spec_cfg,
            sampling=sampling,
            seed=0,
        )

        # Save daily parquet and model binary for subsequent fast scenarios on this forecast window
        buf = io.BytesIO()
        fc_daily.to_parquet(buf, index=True)
        storage.write_bytes(f"runs/{run_id}/forecasts/{forecast_id}/daily.parquet", buf.getvalue())

        pbuf = io.BytesIO()
        plan_clean.to_parquet(pbuf, index=True)
        storage.write_bytes(f"runs/{run_id}/forecasts/{forecast_id}/plan.parquet", pbuf.getvalue())

        import tempfile
        from pathlib import Path

        tmp_dir = Path(tempfile.mkdtemp(prefix=f"fc_{forecast_id}_"))
        bin_path = save_meridian_model(fc_mmm, tmp_dir / "meridian_forecast.binpb")
        storage.write_bytes(f"runs/{run_id}/forecasts/{forecast_id}/meridian_forecast.binpb", bin_path.read_bytes())

        res_payload = fast_forecast_from_multipliers(
            base_daily=fc_daily,
            base_plan=plan_clean,
            multipliers={},
            horizon_days=len(plan_clean),
        )
        result_key = f"runs/{run_id}/forecasts/{forecast_id}/result.json"
        storage.write_json(result_key, res_payload)

        with SessionLocal() as db:
            fc = db.get(ForecastRun, forecast_id)
            if fc:
                fc.status = "succeeded"
                fc.result_path = result_key
                fc.summary = {
                    "horizon_days": len(plan_clean),
                    "total_forecast_sales": float(fc_daily["forecast"].sum()),
                    "total_planned_spend": float(fc_daily["planned_spend"].sum()),
                    "credible_90_lo": float(fc_daily["credible_lo"].sum()),
                    "credible_90_hi": float(fc_daily["credible_hi"].sum()),
                }
                db.commit()
    except Exception as exc:
        logger.exception("Forecast job %s failed", forecast_id)
        with SessionLocal() as db:
            fc = db.get(ForecastRun, forecast_id)
            if fc:
                fc.status = "failed"
                fc.error = str(exc)
                db.commit()
        raise
    finally:
        hb.stop()


def execute_scenario_job(
    scenario_id: str,
    sqlalchemy_url: str | None = None,
) -> None:
    """Evaluate a what-if scenario on the `interactive` queue using the warm LRU cache."""
    from mmm_studio.core.meridian.forecast import build_example_plan, fast_forecast_from_multipliers

    SessionLocal = get_session_factory(sqlalchemy_url)
    storage = get_storage()

    with SessionLocal() as db:
        sc = db.get(ScenarioRun, scenario_id)
        if sc is None:
            return
        run_id = sc.run_id
        forecast_id = sc.forecast_id
        inputs = sc.inputs or {}
        sc.status = "running"
        db.commit()

    try:
        cache_key = f"{run_id}:{forecast_id}"
        cached = _cache_get(cache_key)
        if cached is None:
            fc_daily_key = f"runs/{run_id}/forecasts/{forecast_id}/daily.parquet"
            fc_plan_key = f"runs/{run_id}/forecasts/{forecast_id}/plan.parquet"
            if storage.exists(fc_daily_key) and storage.exists(fc_plan_key):
                base_daily = pd.read_parquet(io.BytesIO(storage.read_bytes(fc_daily_key)))
                base_plan = pd.read_parquet(io.BytesIO(storage.read_bytes(fc_plan_key)))
            elif storage.exists(f"runs/{run_id}/data/forecast_2025_01_daily.parquet"):
                base_daily = pd.read_parquet(
                    io.BytesIO(storage.read_bytes(f"runs/{run_id}/data/forecast_2025_01_daily.parquet"))
                )
                base_plan = pd.read_parquet(
                    io.BytesIO(storage.read_bytes(f"runs/{run_id}/data/forecast_2025_01_plan.parquet"))
                )
            else:
                wide_df = pd.read_parquet(
                    io.BytesIO(storage.read_bytes(f"runs/{run_id}/data/wide_2023_2024.parquet"))
                )
                base_plan = build_example_plan(wide_df, days=42)
                mean_sales = float(wide_df["total_sales"].mean())
                base_daily = pd.DataFrame(
                    {
                        "forecast": mean_sales,
                        "credible_lo": mean_sales * 0.92,
                        "credible_hi": mean_sales * 1.08,
                        "prediction_lo": mean_sales * 0.85,
                        "prediction_hi": mean_sales * 1.15,
                        "planned_spend": base_plan.sum(axis=1).to_numpy(),
                    },
                    index=base_plan.index,
                )
            cached = {"base_daily": base_daily, "base_plan": base_plan}
            _cache_put(cache_key, cached)

        multipliers = {str(k): float(v) for k, v in (inputs.get("multipliers") or {}).items()}
        # Commission channels (affiliate) are locked at 1.0 because they are controls
        multipliers.pop("affiliate", None)
        horizon_days = int(inputs.get("horizon_days", len(cached["base_plan"])))

        res = fast_forecast_from_multipliers(
            base_daily=cached["base_daily"],
            base_plan=cached["base_plan"],
            multipliers=multipliers,
            horizon_days=horizon_days,
        )
        result_key = f"runs/{run_id}/scenarios/{scenario_id}/result.json"
        storage.write_json(result_key, res)

        with SessionLocal() as db:
            sc = db.get(ScenarioRun, scenario_id)
            if sc:
                sc.status = "succeeded"
                sc.result_path = result_key
                sc.summary = {
                    "base_total_sales": res["base_total_sales"],
                    "scenario_total_sales": res["scenario_total_sales"],
                    "delta_sales": res["delta_sales"],
                    "delta_spend": res["delta_spend"],
                    "marginal_roas": res["marginal_roas"],
                }
                db.commit()
    except Exception as exc:
        logger.exception("Scenario job %s failed", scenario_id)
        with SessionLocal() as db:
            sc = db.get(ScenarioRun, scenario_id)
            if sc:
                sc.status = "failed"
                sc.error = str(exc)
                db.commit()
        raise


def execute_optimiser_job(
    optimisation_id: str,
    sqlalchemy_url: str | None = None,
) -> None:
    """Evaluate a constrained budget optimisation on the `interactive` queue."""
    from mmm_studio.core.meridian.optimise import solve_constrained_optimisation

    SessionLocal = get_session_factory(sqlalchemy_url)
    storage = get_storage()

    with SessionLocal() as db:
        opt = db.get(OptimisationRun, optimisation_id)
        if opt is None:
            return
        run_id = opt.run_id
        inputs = opt.inputs or {}
        opt.status = "running"
        db.commit()

    try:
        cache_key = f"opt:{run_id}"
        cached = _cache_get(cache_key)
        if cached is None:
            roi_key = f"runs/{run_id}/data/meridian_roi.parquet"
            rc_key = f"runs/{run_id}/data/response_curves.json"
            if storage.exists(roi_key) and storage.exists(rc_key):
                roi_df = pd.read_parquet(io.BytesIO(storage.read_bytes(roi_key)))
                rc_raw = storage.read_json(rc_key)
                response_curves = {ch: pd.DataFrame(rows) for ch, rows in rc_raw.items()}
            else:
                # Fallback reference curves if Meridian hasn't finished yet
                wide_df = pd.read_parquet(
                    io.BytesIO(storage.read_bytes(f"runs/{run_id}/data/wide_2023_2024.parquet"))
                )
                channels = ["sea", "catalog", "display", "psm", "display_network", "social_media"]
                ref_rois = {"sea": 8.04, "social_media": 1.85, "psm": 1.42, "catalog": 1.01, "display_network": 0.65, "display": 0.42}
                rows = []
                response_curves = {}
                for ch in channels:
                    sp = float(wide_df[f"cost_{ch}"].sum()) if f"cost_{ch}" in wide_df.columns else 1_000_000.0
                    roi_val = ref_rois.get(ch, 1.2)
                    inc = sp * roi_val
                    rows.append(
                        {
                            "channel": ch,
                            "spend": sp,
                            "incremental": inc,
                            "roi": roi_val,
                            "roi_lo": roi_val * 0.7,
                            "roi_hi": roi_val * 1.3,
                            "mroi": roi_val * 0.65,
                            "mroi_lo": roi_val * 0.4,
                            "mroi_hi": roi_val * 0.9,
                            "pct_of_contribution": 10.0,
                        }
                    )
                    mults = [round(x * 0.05, 2) for x in range(41)]
                    response_curves[ch] = pd.DataFrame(
                        {
                            "multiplier": mults,
                            "spend": [m * sp for m in mults],
                            "mean": [inc * (m ** (0.85 if ch == "catalog" else 0.62)) for m in mults],
                            "ci_lo": [inc * 0.75 * (m ** 0.62) for m in mults],
                            "ci_hi": [inc * 1.25 * (m ** 0.62) for m in mults],
                        }
                    )
                roi_df = pd.DataFrame(rows).set_index("channel")
            cached = {"roi_df": roi_df, "response_curves": response_curves}
            _cache_put(cache_key, cached)

        roi_df = cached["roi_df"]
        response_curves = cached["response_curves"]
        ch_rows = roi_df.drop(index="All Channels", errors="ignore")
        default_budget = float(ch_rows["spend"].sum())

        total_budget = float(inputs.get("total_budget") or default_budget)
        bounds_raw = inputs.get("channel_bounds") or {}
        channel_bounds = {
            str(ch): (float(b[0]), float(b[1]))
            for ch, b in bounds_raw.items()
            if isinstance(b, (list, tuple)) and len(b) == 2
        }
        default_bounds = (
            float(inputs.get("min_multiplier", 0.7)),
            float(inputs.get("max_multiplier", 1.3)),
        )
        include_catalog = bool(inputs.get("include_catalog", True))

        res = solve_constrained_optimisation(
            roi_df=roi_df,
            response_curves=response_curves,
            total_budget=total_budget,
            channel_bounds=channel_bounds or None,
            default_bounds=default_bounds,
            include_catalog=include_catalog,
        )
        result_key = f"runs/{run_id}/optimisations/{optimisation_id}/result.json"
        storage.write_json(result_key, res)

        with SessionLocal() as db:
            opt = db.get(OptimisationRun, optimisation_id)
            if opt:
                opt.status = "succeeded"
                opt.result_path = result_key
                opt.summary = {
                    "total_budget": res["total_budget"],
                    "historical_incremental_revenue": res["historical_incremental_revenue"],
                    "optimised_incremental_revenue": res["optimised_incremental_revenue"],
                    "uplift_eur": res["uplift_eur"],
                    "uplift_pct": res["uplift_pct"],
                }
                db.commit()
    except Exception as exc:
        logger.exception("Optimiser job %s failed", optimisation_id)
        with SessionLocal() as db:
            opt = db.get(OptimisationRun, optimisation_id)
            if opt:
                opt.status = "failed"
                opt.error = str(exc)
                db.commit()
        raise


def run_analysis_job(context: JobContext, run_id: str, skip_meridian: bool = False) -> None:
    execute_analysis_job(run_id=run_id, skip_meridian=skip_meridian, job_context=context)


def run_meridian_job(context: JobContext, run_id: str) -> None:
    execute_meridian_job(run_id=run_id, job_context=context)


def run_forecast_job(context: JobContext, forecast_id: str) -> None:
    execute_forecast_job(forecast_id=forecast_id, job_context=context)


def run_scenario_job(scenario_id: str) -> None:
    execute_scenario_job(scenario_id=scenario_id)


def run_optimiser_job(optimisation_id: str) -> None:
    execute_optimiser_job(optimisation_id=optimisation_id)


run_analysis_job.name = "mmm_studio.jobs.tasks.run_analysis_job"  # type: ignore[attr-defined]
run_meridian_job.name = "mmm_studio.jobs.tasks.run_meridian_job"  # type: ignore[attr-defined]
run_forecast_job.name = "mmm_studio.jobs.tasks.run_forecast_job"  # type: ignore[attr-defined]
run_scenario_job.name = "mmm_studio.jobs.tasks.run_scenario_job"  # type: ignore[attr-defined]
run_optimiser_job.name = "mmm_studio.jobs.tasks.run_optimiser_job"  # type: ignore[attr-defined]


def register_tasks(proc_app: Any) -> None:
    """Register all 5 MMM Studio tasks on a Procrastinate App instance."""
    if "mmm_studio.jobs.tasks.run_analysis_job" not in proc_app.tasks:
        proc_app.task(name="mmm_studio.jobs.tasks.run_analysis_job", queue="analysis", pass_context=True)(
            run_analysis_job
        )
    if "mmm_studio.jobs.tasks.run_meridian_job" not in proc_app.tasks:
        proc_app.task(name="mmm_studio.jobs.tasks.run_meridian_job", queue="meridian", pass_context=True)(
            run_meridian_job
        )
    if "mmm_studio.jobs.tasks.run_forecast_job" not in proc_app.tasks:
        proc_app.task(name="mmm_studio.jobs.tasks.run_forecast_job", queue="meridian", pass_context=True)(
            run_forecast_job
        )
    if "mmm_studio.jobs.tasks.run_scenario_job" not in proc_app.tasks:
        proc_app.task(name="mmm_studio.jobs.tasks.run_scenario_job", queue="interactive")(
            run_scenario_job
        )
    if "mmm_studio.jobs.tasks.run_optimiser_job" not in proc_app.tasks:
        proc_app.task(name="mmm_studio.jobs.tasks.run_optimiser_job", queue="interactive")(
            run_optimiser_job
        )
