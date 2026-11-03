"""Procrastinate background tasks for `analysis`, `meridian`, and `interactive` queues."""

from __future__ import annotations

import io
import logging
import os
import threading
import time
from collections import OrderedDict
from typing import Any

import pandas as pd
from procrastinate import JobContext

from mmm_studio.core.schema import DataProfile, DataRoleConfig, default_role_config, extract_role_config
from mmm_studio.db.models import ForecastRun, OptimisationRun, Run, ScenarioRun, StageProgress, utcnow
from mmm_studio.db.session import get_session_factory
from mmm_studio.pipeline.context import RunContext, model_panel_key
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


def ensure_stage_rows(
    run_id: str,
    skip_meridian: bool = False,
    sqlalchemy_url: str | None = None,
    reset: bool = False,
) -> None:
    """Create the StageProgress rows of a Run; with `reset=True` (a re-run) every stage goes back to pending."""
    SessionLocal = get_session_factory(sqlalchemy_url)
    with SessionLocal() as db:
        existing = {s.key: s for s in db.query(StageProgress).filter(StageProgress.run_id == run_id).all()}
        defined = {key for key, _, _ in STAGE_DEFINITIONS}
        for idx, (key, label, queue) in enumerate(STAGE_DEFINITIONS):
            status = "skipped" if (skip_meridian and queue == "meridian") else "pending"
            row = existing.get(key)
            if row is None:
                db.add(StageProgress(run_id=run_id, order_idx=idx, key=key, label=label, queue=queue, status=status))
            elif reset:
                row.order_idx, row.label, row.queue, row.status = idx, label, queue, status
                row.started_at = row.finished_at = None
                row.error = None
        if reset:
            for key, row in existing.items():
                if key not in defined:
                    db.delete(row)
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

    def on_stage_skipped(stage_key: str, reason: str) -> None:
        hb.touch()
        with SessionLocal() as sdb:
            st = (
                sdb.query(StageProgress)
                .filter(StageProgress.run_id == run_id, StageProgress.key == stage_key)
                .first()
            )
            if st:
                st.status = "skipped"
                st.finished_at = utcnow()
                st.error = reason
                sdb.commit()

    spawn_child = (
        hb.job_context is not None
        and os.environ.get("MMM_SPAWN_MERIDIAN_CHILD", "true").lower() in ("1", "true", "yes")
    )
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
        on_stage_skipped=on_stage_skipped,
        on_heartbeat=hb.touch,
        is_cancelled=lambda: hb.cancelled,
        spawn_meridian_child=spawn_child,
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

        with SessionLocal() as db:
            run_rec = db.get(Run, run_id)
            meridian_stages = (
                db.query(StageProgress)
                .filter(StageProgress.run_id == run_id, StageProgress.queue == "meridian")
                .all()
            )
            meridian_done = bool(meridian_stages) and all(
                st.status in ("succeeded", "failed", "skipped") for st in meridian_stages
            ) and any(st.status == "succeeded" for st in meridian_stages)
            if not meridian_done:
                ctx.save_json("meta/summary.json", summary)
            if run_rec and run_rec.status != "cancelled":
                if not meridian_done:
                    run_rec.summary = summary
                run_rec.heartbeat_at = utcnow()
                if skip_meridian or meridian_done:
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
        # The Meridian stages need the modelling panel written by `s01_ingest`. When both jobs run on separate workers,
        # wait for an active analysis job to produce it before falling back to running the analysis stages here.
        if model_panel_key(ctx.storage, run_id) is None:
            wait_deadline = time.monotonic() + 60.0
            while model_panel_key(ctx.storage, run_id) is None:
                with SessionLocal() as db:
                    prep_stage = (
                        db.query(StageProgress)
                        .filter(StageProgress.run_id == run_id, StageProgress.key == "s10_preparation")
                        .first()
                    )
                    any_running_analysis = (
                        db.query(StageProgress)
                        .filter(
                            StageProgress.run_id == run_id,
                            StageProgress.queue == "analysis",
                            StageProgress.status == "running",
                        )
                        .first()
                        is not None
                    )
                if not any_running_analysis and (prep_stage is None or prep_stage.status in ("pending", "failed")):
                    break
                if time.monotonic() >= wait_deadline:
                    break
                hb.touch()
                time.sleep(0.5)
            if model_panel_key(ctx.storage, run_id) is None:
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


def _read_parquet(storage: Any, key: str) -> pd.DataFrame:
    return pd.read_parquet(io.BytesIO(storage.read_bytes(key)))


def _first_existing(storage: Any, keys: list[str]) -> str | None:
    return next((k for k in keys if storage.exists(k)), None)


def default_forecast_keys(run_id: str) -> tuple[list[str], list[str]]:
    """Daily-forecast and plan keys of the run's example forecast (current names first, then pre-rename names)."""
    return (
        [f"runs/{run_id}/data/forecast_daily.parquet", f"runs/{run_id}/data/forecast_2025_01_daily.parquet"],
        [f"runs/{run_id}/data/forecast_plan.parquet", f"runs/{run_id}/data/forecast_2025_01_plan.parquet"],
    )


def _load_forecast_context(storage: Any, run_id: str, forecast_id: str) -> dict[str, Any]:
    """Load base daily forecast, plan, history, spec and (when available) the fitted forecast model of a forecast."""
    from mmm_studio.core.meridian.spec import MeridianSpecConfig, load_meridian_model

    if forecast_id.startswith("default_"):
        daily_keys, plan_keys = default_forecast_keys(run_id)
        model_key = f"runs/{run_id}/models/meridian_forecast.binpb"
    else:
        daily_keys = [f"runs/{run_id}/forecasts/{forecast_id}/daily.parquet"]
        plan_keys = [f"runs/{run_id}/forecasts/{forecast_id}/plan.parquet"]
        model_key = f"runs/{run_id}/forecasts/{forecast_id}/meridian_forecast.binpb"
    daily_key, plan_key = _first_existing(storage, daily_keys), _first_existing(storage, plan_keys)
    if daily_key is None or plan_key is None:
        raise RuntimeError("The forecast has no results yet; wait for it to finish before running scenarios.")
    panel_key = model_panel_key(storage, run_id)
    cfg_key = f"runs/{run_id}/meta/config.json"
    config_obj = extract_role_config(storage.read_json(cfg_key)) if storage.exists(cfg_key) else default_role_config()
    history = _read_parquet(storage, panel_key) if panel_key else None
    spec_cfg = MeridianSpecConfig.from_role_config(config_obj, index=history.index if history is not None else None)
    out: dict[str, Any] = {
        "base_daily": _read_parquet(storage, daily_key),
        "base_plan": _read_parquet(storage, plan_key),
        "history": history,
        "spec_cfg": spec_cfg,
        "config": config_obj,
        "mmm": None,
    }
    if history is not None and storage.exists(model_key):
        import tempfile
        from pathlib import Path

        tmp = Path(tempfile.mkdtemp(prefix=f"sc_{run_id}_")) / "meridian_forecast.binpb"
        tmp.write_bytes(storage.read_bytes(model_key))
        out["mmm"] = load_meridian_model(tmp)
    roi_key = f"runs/{run_id}/data/meridian_roi.parquet"
    if storage.exists(roi_key):
        roi = _read_parquet(storage, roi_key).drop(index="All Channels", errors="ignore")
        out["mroi"] = {str(c): float(v) for c, v in roi["mroi"].items()}
    return out


def execute_forecast_job(
    forecast_id: str,
    job_context: JobContext | None = None,
    sqlalchemy_url: str | None = None,
) -> None:
    """Refit Meridian on history + the uploaded plan window (`meridian` queue), as in notebook 05."""
    from mmm_studio.core.meridian.forecast import forecast_budget_plan, scenario_payload, validate_plan
    from mmm_studio.core.meridian.spec import MeridianSpecConfig, save_meridian_model
    from mmm_studio.pipeline.stages import _sampling_for, _sampling_label

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
        seed = int(((run_rec.config if run_rec else None) or {}).get("seed", 0))
        config_dict = (run_rec.config if run_rec else None) or {}
        fc.status = "running"
        fc.heartbeat_at = utcnow()
        db.commit()

    config_obj = extract_role_config(config_dict)
    hb = _HeartbeatMonitor(ForecastRun, forecast_id, job_context=job_context, sqlalchemy_url=sqlalchemy_url)
    hb.start()
    try:
        panel_key = model_panel_key(storage, run_id)
        if panel_key is None:
            raise RuntimeError("The run's modelling panel is missing; re-run the analysis first.")
        history = _read_parquet(storage, panel_key)
        spec_cfg = MeridianSpecConfig.from_role_config(config_obj, index=history.index)
        plan_df = pd.read_csv(io.BytesIO(storage.read_bytes(plan_path)))
        plan_clean = validate_plan(plan_df, last_history_date=pd.Timestamp(history.index.max()), paid_channels=spec_cfg.paid_slugs)

        sampling = _sampling_for(run_profile, "main")
        fc_daily, fc_mmm, _ = forecast_budget_plan(history, plan_clean, cfg=spec_cfg, sampling=sampling, seed=seed)
        if hb.cancelled:
            raise InterruptedError("Forecast cancelled.")

        base = f"runs/{run_id}/forecasts/{forecast_id}"
        for name, frame in (("daily", fc_daily), ("plan", plan_clean)):
            buf = io.BytesIO()
            frame.to_parquet(buf, index=True)
            storage.write_bytes(f"{base}/{name}.parquet", buf.getvalue())
        storage.write_bytes(f"{base}/forecast.csv", fc_daily.round(0).to_csv().encode("utf-8"), content_type="text/csv; charset=utf-8")

        import tempfile
        from pathlib import Path

        bin_path = save_meridian_model(fc_mmm, Path(tempfile.mkdtemp(prefix=f"fc_{forecast_id}_")) / "meridian_forecast.binpb")
        storage.write_bytes(f"{base}/meridian_forecast.binpb", bin_path.read_bytes())

        res_payload = scenario_payload(fc_daily, plan_clean, fc_daily, plan_clean, f"Meridian refit ({_sampling_label(sampling)})")
        result_key = f"{base}/result.json"
        storage.write_json(result_key, res_payload)

        with SessionLocal() as db:
            fc = db.get(ForecastRun, forecast_id)
            if fc:
                fc.status = "succeeded"
                fc.result_path = result_key
                fc.summary = {
                    "horizon_days": len(plan_clean),
                    "start_date": plan_clean.index[0].strftime("%Y-%m-%d"),
                    "end_date": plan_clean.index[-1].strftime("%Y-%m-%d"),
                    "total_forecast_sales": float(fc_daily["forecast"].sum()),
                    "total_planned_spend": float(fc_daily["planned_spend"].sum()),
                    "prediction_90_lo": float(fc_daily["prediction_lo"].sum()),
                    "prediction_90_hi": float(fc_daily["prediction_hi"].sum()),
                }
                db.commit()
    except InterruptedError:
        with SessionLocal() as db:
            fc = db.get(ForecastRun, forecast_id)
            if fc:
                fc.status = "cancelled"
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
    """Evaluate a what-if scenario with the fitted forecast model (warm LRU cache, `interactive` queue)."""
    from mmm_studio.core.meridian.forecast import (
        channel_effect_draws,
        fast_forecast_from_multipliers,
        model_scenario_from_multipliers,
    )

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
        cache_key = f"{run_id}:{forecast_id}:{inputs.get('cache_version', '')}"
        cached = _cache_get(cache_key)
        if cached is None:
            cached = _load_forecast_context(storage, run_id, forecast_id)
            _cache_put(cache_key, cached)

        multipliers = {str(k): float(v) for k, v in (inputs.get("multipliers") or {}).items()}
        for locked in (*cached["config"].commission_channels, *cached["spec_cfg"].as_controls):
            multipliers.pop(locked, None)
        horizon_days = int(inputs.get("horizon_days", len(cached["base_plan"])))

        if cached["mmm"] is not None:
            if cached.get("base_effects") is None:
                # The plan's per-channel draws are the same for every scenario of this forecast
                cached["base_effects"] = channel_effect_draws(
                    cached["mmm"], cached["history"], cached["base_plan"], cached["spec_cfg"]
                )
            res = model_scenario_from_multipliers(
                cached["mmm"],
                cached["history"],
                cached["base_daily"],
                cached["base_plan"],
                cached["spec_cfg"],
                multipliers=multipliers,
                horizon_days=horizon_days,
                base_effects=cached["base_effects"],
            )
        elif cached.get("mroi"):
            res = fast_forecast_from_multipliers(
                cached["base_daily"],
                cached["base_plan"],
                multipliers=multipliers,
                horizon_days=horizon_days,
                mroi_map=cached["mroi"],
                catalog_shift=cached["spec_cfg"].catalog_shift,
            )
        else:
            raise RuntimeError("Neither the fitted forecast model nor the run's marginal ROIs are available.")
        result_key = f"runs/{run_id}/scenarios/{scenario_id}/result.json"
        storage.write_json(result_key, res)

        with SessionLocal() as db:
            sc = db.get(ScenarioRun, scenario_id)
            if sc:
                sc.status = "succeeded"
                sc.result_path = result_key
                sc.summary = {
                    "method": res["method"],
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
    """Constrained budget optimisation along the run's posterior response curves (`interactive` queue)."""
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
        cache_key = f"opt:{run_id}:{inputs.get('cache_version', '')}"
        cached = _cache_get(cache_key)
        if cached is None:
            roi_key = f"runs/{run_id}/data/meridian_roi.parquet"
            rc_key = f"runs/{run_id}/data/response_curves.json"
            if not (storage.exists(roi_key) and storage.exists(rc_key)):
                raise RuntimeError("The optimiser needs the Meridian response curves; wait for the Meridian fit to finish.")
            roi_df = _read_parquet(storage, roi_key)
            response_curves = {ch: pd.DataFrame(rows) for ch, rows in storage.read_json(rc_key).items()}
            cached = {"roi_df": roi_df, "response_curves": response_curves}
            _cache_put(cache_key, cached)

        roi_df = cached["roi_df"]
        ch_rows = roi_df.drop(index="All Channels", errors="ignore")
        total_budget = float(inputs.get("total_budget") or float(ch_rows["spend"].sum()))
        bounds_raw = inputs.get("channel_bounds") or {}
        channel_bounds = {
            str(ch): (float(b[0]), float(b[1]))
            for ch, b in bounds_raw.items()
            if isinstance(b, (list, tuple)) and len(b) == 2
        }
        default_bounds = (float(inputs.get("min_multiplier", 0.7)), float(inputs.get("max_multiplier", 1.3)))
        res = solve_constrained_optimisation(
            roi_df=roi_df,
            response_curves=cached["response_curves"],
            total_budget=total_budget,
            channel_bounds=channel_bounds or None,
            default_bounds=default_bounds,
            include_catalog=bool(inputs.get("include_catalog", True)),
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
