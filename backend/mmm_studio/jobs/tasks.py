"""Procrastinate background tasks for `analysis`, `meridian`, and `interactive` queues."""

from __future__ import annotations

import importlib
import io
import logging
import os
import sys
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any

import pandas as pd
from procrastinate import JobContext

from mmm_studio.core.schema import DataProfile, DataRoleConfig, extract_role_config
from mmm_studio.db.models import ForecastRun, OptimisationRun, Run, ScenarioRun, StageProgress, utcnow
from mmm_studio.db.session import get_session_factory
from mmm_studio.errors import public_error
from mmm_studio.jobs.registry import default_forecast_keys, register_default_forecast
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
_PKG_DIR = Path(__file__).resolve().parent.parent
_RELOAD_PREFIXES = ("mmm_studio.charts", "mmm_studio.core", "mmm_studio.report", "mmm_studio.pipeline")
_RELOAD_PRIORITY = (
    "mmm_studio.charts.theme",
    "mmm_studio.charts.builders",
    "mmm_studio.core.model_settings",
    "mmm_studio.core.schema",
    "mmm_studio.core.metrics",
    "mmm_studio.core.holidays",
    "mmm_studio.core.features",
    "mmm_studio.core.tables",
    "mmm_studio.core.calendar",
    "mmm_studio.core.eda.stats",
    "mmm_studio.core.eda.target",
    "mmm_studio.core.eda",
    "mmm_studio.core.preparation",
    "mmm_studio.core.workbook",
    "mmm_studio.core.meridian.spec",
    "mmm_studio.core.meridian.forecast",
    "mmm_studio.report.sections",
    "mmm_studio.report.narrative",
    "mmm_studio.report.facts",
    "mmm_studio.pipeline.context",
)


def _latest_source_mtime() -> float:
    try:
        return max((p.stat().st_mtime for p in _PKG_DIR.rglob("*.py")), default=0.0)
    except OSError:
        return 0.0


_LOADED_MTIME: float = _latest_source_mtime()


def _refresh_code_if_changed() -> None:
    """Reload core/report/pipeline modules if source files changed on disk since worker startup."""
    global _LOADED_MTIME, DataProfile, DataRoleConfig, extract_role_config
    global RunContext, model_panel_key, STAGE_DEFINITIONS, run_analysis_stages, run_meridian_stages

    latest = _latest_source_mtime()
    if latest <= _LOADED_MTIME:
        return
    with _CACHE_LOCK:
        if latest <= _LOADED_MTIME:
            return
        _INTERACTIVE_CACHE.clear()
        loaded = [
            name
            for name, mod in list(sys.modules.items())
            if mod is not None and name.startswith(_RELOAD_PREFIXES) and getattr(mod, "__file__", None)
        ]
        order = {name: idx for idx, name in enumerate(_RELOAD_PRIORITY)}
        loaded.sort(key=lambda n: (order.get(n, len(_RELOAD_PRIORITY)), n.count("."), n))
        for name in loaded:
            mod = sys.modules.get(name)
            if mod is not None:
                try:
                    importlib.reload(mod)
                except Exception:
                    logger.debug("Could not hot-reload module %s", name, exc_info=True)
        from mmm_studio.core import schema as _schema
        from mmm_studio.pipeline import context as _ctx
        from mmm_studio.pipeline import stages as _stages

        DataProfile = _schema.DataProfile
        DataRoleConfig = _schema.DataRoleConfig
        extract_role_config = _schema.extract_role_config
        RunContext = _ctx.RunContext
        model_panel_key = _ctx.model_panel_key
        STAGE_DEFINITIONS = _stages.STAGE_DEFINITIONS
        run_analysis_stages = _stages.run_analysis_stages
        run_meridian_stages = _stages.run_meridian_stages
        _LOADED_MTIME = latest


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

    from mmm_studio.settings import get_settings

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
        spawn_meridian_child=_spawn_child(hb.job_context),
        fit_timeout_seconds=get_settings().meridian_fit_timeout_seconds,
    )


def _spawn_child(job_context: JobContext | None) -> bool:
    """Meridian fits run in a spawned child inside workers (memory isolation, cancellation, timeout)."""
    return job_context is not None and os.environ.get("MMM_SPAWN_MERIDIAN_CHILD", "true").lower() in ("1", "true", "yes")


def execute_analysis_job(
    run_id: str,
    skip_meridian: bool = False,
    job_context: JobContext | None = None,
    sqlalchemy_url: str | None = None,
) -> None:
    """Synchronous implementation of the `analysis` queue task."""
    _refresh_code_if_changed()
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
                if not skip_meridian:
                    # The Meridian job starts only now, so it never runs before (or alongside) the analysis stages
                    _defer_meridian_job(run_id, sqlalchemy_url=sqlalchemy_url)
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
                run_rec.error = public_error(exc)
                db.commit()
        raise
    finally:
        hb.stop()


def _defer_meridian_job(run_id: str, sqlalchemy_url: str | None = None) -> None:
    """Queue the run's Meridian job after its analysis job and record its id (a failure marks the run failed)."""
    from mmm_studio.jobs.app import defer_task

    SessionLocal = get_session_factory(sqlalchemy_url)
    try:
        job_id = defer_task(
            run_meridian_job,
            queue="meridian",
            lock="meridian_singleton",
            queueing_lock=f"meridian:{run_id}",
            run_id=run_id,
        )
    except Exception as exc:
        logger.exception("Could not queue the Meridian job of run %s", run_id)
        with SessionLocal() as db:
            run_rec = db.get(Run, run_id)
            if run_rec and run_rec.status != "cancelled":
                run_rec.status = "failed"
                run_rec.error = f"The Meridian fit could not be queued ({public_error(exc)})."
                db.commit()
        return
    with SessionLocal() as db:
        run_rec = db.get(Run, run_id)
        if run_rec:
            run_rec.meridian_job_id = job_id
            db.commit()


def execute_meridian_job(
    run_id: str,
    job_context: JobContext | None = None,
    sqlalchemy_url: str | None = None,
) -> None:
    """Synchronous implementation of the `meridian` queue task."""
    _refresh_code_if_changed()
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
        # The job is queued by the analysis job when it finishes, so the modelling panel must exist
        if model_panel_key(ctx.storage, run_id) is None:
            raise RuntimeError("The modelling panel is missing; the analysis stages must finish before the Meridian fit.")
        try:
            run_meridian_stages(ctx)
        finally:
            ctx.cleanup()
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
            "holdout_label": meridian_metrics.get("holdout_label"),
            "holdout_q3_2024": meridian_metrics.get("holdout_q3_2024"),
            "forward_label": meridian_metrics.get("forward_label"),
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
            register_default_forecast(db, ctx.storage, run_id)
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
                run_rec.error = public_error(exc)
                db.commit()
        raise
    finally:
        hb.stop()


def _read_parquet(storage: Any, key: str) -> pd.DataFrame:
    return pd.read_parquet(io.BytesIO(storage.read_bytes(key)))


def _first_existing(storage: Any, keys: list[str]) -> str | None:
    return next((k for k in keys if storage.exists(k)), None)


def _run_config(run_id: str, sqlalchemy_url: str | None = None) -> DataRoleConfig:
    """The run's configuration from the database: the single source every job reads (it is frozen once a run starts)."""
    SessionLocal = get_session_factory(sqlalchemy_url)
    with SessionLocal() as db:
        run_rec = db.get(Run, run_id)
        if run_rec is None:
            raise RuntimeError(f"Run {run_id} no longer exists.")
        return extract_role_config(run_rec.config or {})


def _forecast_level_sd(storage: Any, run_id: str) -> float:
    """The run's forecast level uncertainty (0 when the run predates it or had too few windows)."""
    from mmm_studio.core.meridian.forecast import forecast_level_sd

    key = f"runs/{run_id}/data/forecast_summary.json"
    return forecast_level_sd(storage.read_json(key)) if storage.exists(key) else 0.0


def _backtest_coverage(storage: Any, run_id: str) -> float | None:
    key = f"runs/{run_id}/data/forecast_summary.json"
    if not storage.exists(key):
        return None
    value = storage.read_json(key).get("backtest_coverage")
    return float(value) if value is not None else None


def _load_forecast_context(storage: Any, run_id: str, forecast_id: str, sqlalchemy_url: str | None = None) -> dict[str, Any]:
    """Load base daily forecast, plan, history, spec and (when available) the fitted forecast model of a forecast."""
    import tempfile
    from dataclasses import replace
    from pathlib import Path

    from mmm_studio.core.meridian.forecast import extend_history
    from mmm_studio.core.meridian.spec import MeridianSpecConfig, load_meridian_model

    default_model = f"runs/{run_id}/models/meridian_forecast.binpb"
    if forecast_id.startswith("default_"):
        daily_keys, plan_keys = default_forecast_keys(run_id)
        model_key = default_model
    else:
        daily_keys = [f"runs/{run_id}/forecasts/{forecast_id}/daily.parquet"]
        plan_keys = [f"runs/{run_id}/forecasts/{forecast_id}/plan.parquet"]
        own_model = f"runs/{run_id}/forecasts/{forecast_id}/meridian_forecast.binpb"
        model_key = own_model if storage.exists(own_model) else default_model
    daily_key, plan_key = _first_existing(storage, daily_keys), _first_existing(storage, plan_keys)
    if daily_key is None or plan_key is None:
        raise RuntimeError("The forecast has no results yet; wait for it to finish before running scenarios.")
    panel_key = model_panel_key(storage, run_id)
    if panel_key is None:
        raise RuntimeError("The run's modelling panel is missing; re-run the analysis first.")
    config_obj = _run_config(run_id, sqlalchemy_url)
    history = _read_parquet(storage, panel_key)
    spec_cfg = MeridianSpecConfig.from_role_config(config_obj, wide=history)
    base_daily = _read_parquet(storage, daily_key)
    base_plan = _read_parquet(storage, plan_key)
    model_plan = base_plan
    if model_key == default_model and not forecast_id.startswith("default_"):
        def_plan_key = _first_existing(storage, default_forecast_keys(run_id)[1])
        if def_plan_key is not None:
            def_plan = _read_parquet(storage, def_plan_key)
            if len(def_plan) >= len(base_plan) and pd.Timestamp(def_plan.index[0]) == pd.Timestamp(base_plan.index[0]):
                model_plan = def_plan.copy()
                for col in base_plan.columns:
                    if col in model_plan.columns:
                        model_plan.iloc[: len(base_plan), model_plan.columns.get_loc(col)] = base_plan[col].to_numpy(dtype=float)
            else:
                model_key = f"runs/{run_id}/forecasts/{forecast_id}/meridian_forecast.binpb"
    if storage.exists(model_key) and len(model_plan) > 0:
        start = model_plan.index[0].strftime("%Y-%m-%d")
        end = model_plan.index[-1].strftime("%Y-%m-%d")
        spec_cfg = replace(spec_cfg, holdout=(start, end)).with_training_priors(extend_history(history, model_plan))
    out: dict[str, Any] = {
        "base_daily": base_daily,
        "base_plan": model_plan,
        "history": history,
        "spec_cfg": spec_cfg,
        "config": config_obj,
        "coverage": _backtest_coverage(storage, run_id),
        "level_sd": _forecast_level_sd(storage, run_id),
        "mmm": None,
    }
    if storage.exists(model_key):
        # Meridian loads the whole model into memory, so the temporary file can go right away
        with tempfile.TemporaryDirectory(prefix=f"sc_{run_id}_") as tmp:
            path = Path(tmp) / "meridian_forecast.binpb"
            path.write_bytes(storage.read_bytes(model_key))
            out["mmm"] = load_meridian_model(path)
    roi_key = f"runs/{run_id}/data/meridian_roi.parquet"
    if storage.exists(roi_key):
        roi = _read_parquet(storage, roi_key).drop(index="All Channels", errors="ignore")
        out["mroi"] = {str(c): float(v) for c, v in roi["mroi"].items()}
        out["roi"] = {str(c): float(v) for c, v in roi["roi"].items()}
    return out


def execute_forecast_job(
    forecast_id: str,
    job_context: JobContext | None = None,
    sqlalchemy_url: str | None = None,
) -> None:
    """Refit Meridian on history + the uploaded plan window (`meridian` queue)."""
    _refresh_code_if_changed()
    import tempfile
    from pathlib import Path

    from mmm_studio.core.meridian.forecast import (
        channel_effect_draws,
        control_effect_draws,
        forecast_budget_plan,
        scenario_payload,
        validate_plan,
    )
    from mmm_studio.core.meridian.spec import MeridianSpecConfig, save_meridian_model
    from mmm_studio.pipeline.stages import _sampling_for, _sampling_label
    from mmm_studio.settings import get_settings

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
        spec_cfg = MeridianSpecConfig.from_role_config(config_obj, wide=history)
        plan_df = pd.read_csv(io.BytesIO(storage.read_bytes(plan_path)))
        plan_clean = validate_plan(plan_df, last_history_date=pd.Timestamp(history.index.max()), paid_channels=spec_cfg.paid_slugs)

        sampling = _sampling_for(run_profile, "main", config_obj.model_settings)
        result = forecast_budget_plan(
            history,
            plan_clean,
            cfg=spec_cfg,
            sampling=sampling,
            seed=seed,
            spawn_child=_spawn_child(job_context),
            should_cancel=lambda: hb.cancelled,
            on_heartbeat=hb.touch,
            timeout_seconds=get_settings().meridian_fit_timeout_seconds,
            level_sd=_forecast_level_sd(storage, run_id),
        )
        fc_daily = result.daily
        if hb.cancelled:
            raise InterruptedError("Forecast cancelled.")

        base = f"runs/{run_id}/forecasts/{forecast_id}"
        for name, frame in (("daily", fc_daily), ("plan", plan_clean)):
            buf = io.BytesIO()
            frame.to_parquet(buf, index=True)
            storage.write_bytes(f"{base}/{name}.parquet", buf.getvalue())
        storage.write_bytes(f"{base}/forecast.csv", fc_daily.round(0).to_csv().encode("utf-8"), content_type="text/csv; charset=utf-8")

        with tempfile.TemporaryDirectory(prefix=f"fc_{forecast_id}_") as tmp:
            bin_path = save_meridian_model(result.mmm, Path(tmp) / "meridian_forecast.binpb")
            storage.write_bytes(f"{base}/meridian_forecast.binpb", bin_path.read_bytes())

        base_draws, effect_channels = channel_effect_draws(result.mmm, history, plan_clean, result.cfg)
        base_ctrl = control_effect_draws(result.mmm, history, plan_clean, result.cfg)
        res_payload = scenario_payload(
            fc_daily,
            plan_clean,
            fc_daily,
            plan_clean,
            f"Meridian refit ({_sampling_label(sampling)})",
            effect_channels=effect_channels,
            total_pi=(result.total_lo, result.total_hi),
            coverage=_backtest_coverage(storage, run_id),
            base_media_draws=base_draws,
            alt_media_draws=base_draws,
            base_control_draws=base_ctrl,
            alt_control_draws=base_ctrl,
        )
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
                    # 90 % prediction interval of the total (quantiles of the draws' sums)
                    "prediction_total_lo": result.total_lo,
                    "prediction_total_hi": result.total_hi,
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
                fc.error = public_error(exc)
                db.commit()
        raise
    finally:
        hb.stop()


def execute_scenario_job(
    scenario_id: str,
    sqlalchemy_url: str | None = None,
) -> None:
    """Evaluate a what-if scenario with the fitted forecast model (warm LRU cache, `interactive` queue)."""
    _refresh_code_if_changed()
    from mmm_studio.core.meridian.forecast import (
        channel_effect_draws,
        control_effect_draws,
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
            cached = _load_forecast_context(storage, run_id, forecast_id, sqlalchemy_url)
            _cache_put(cache_key, cached)

        unlock_controls = bool(inputs.get("unlock_controls", False))
        multipliers = {str(k): float(v) for k, v in (inputs.get("multipliers") or {}).items()}
        spend_overrides = {str(k): float(v) for k, v in (inputs.get("spend_overrides") or {}).items()}
        if not unlock_controls:
            for locked in (*cached["config"].commission_channels, *cached["spec_cfg"].as_controls):
                multipliers.pop(locked, None)
                spend_overrides.pop(locked, None)
        horizon_days = min(int(inputs.get("horizon_days", len(cached["base_daily"]))), len(cached["base_daily"]))

        if cached["mmm"] is not None:
            if cached.get("base_effects") is None:
                # The plan's per-channel draws are the same for every scenario of this forecast
                cached["base_effects"] = channel_effect_draws(
                    cached["mmm"], cached["history"], cached["base_plan"], cached["spec_cfg"]
                )
            if cached.get("base_controls") is None:
                cached["base_controls"] = control_effect_draws(
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
                base_controls=cached["base_controls"],
                coverage=cached.get("coverage"),
                unlock_controls=unlock_controls,
                level_sd=cached.get("level_sd", 0.0),
                spend_overrides=spend_overrides,
            )
        elif cached.get("mroi"):
            res = fast_forecast_from_multipliers(
                cached["base_daily"],
                cached["base_plan"],
                multipliers=multipliers,
                horizon_days=horizon_days,
                mroi_map=cached["mroi"],
                roi_map=cached.get("roi"),
                catalog_shift=cached["spec_cfg"].catalog_shift,
                print_slugs=cached["spec_cfg"].print_slugs,
                spend_overrides=spend_overrides,
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
                sc.error = public_error(exc)
                db.commit()
        raise


def execute_optimiser_job(
    optimisation_id: str,
    sqlalchemy_url: str | None = None,
) -> None:
    """Constrained budget optimisation along the run's posterior response curves (`interactive` queue).

    The API has already validated the request (channels, bounds, feasibility); an `OptimisationError` here still fails
    the optimisation with its reason instead of returning a relaxed allocation.
    """
    _refresh_code_if_changed()
    from mmm_studio.core.meridian.optimise import OptimisationError, solve_constrained_optimisation

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
            cached = load_optimiser_inputs(storage, run_id)
            _cache_put(cache_key, cached)

        roi_df = cached["roi_df"]
        ch_rows = roi_df.drop(index="All Channels", errors="ignore")
        budget_mode = str(inputs.get("budget_mode") or "fixed")
        target_value_raw = inputs.get("target_value")
        target_value = float(target_value_raw) if target_value_raw is not None else None
        total_budget = float(inputs.get("total_budget") or float(ch_rows["spend"].sum()))
        channel_bounds = {str(ch): (float(b[0]), float(b[1])) for ch, b in (inputs.get("channel_bounds") or {}).items()}
        default_bounds = (float(inputs.get("min_multiplier", 0.7)), float(inputs.get("max_multiplier", 1.3)))
        res = solve_constrained_optimisation(
            roi_df=roi_df,
            response_curves=cached["response_curves"],
            total_budget=total_budget,
            channel_bounds=channel_bounds or None,
            default_bounds=default_bounds,
            include_catalog=bool(inputs.get("include_catalog", True)),
            print_slugs=tuple(inputs.get("print_slugs") or ("catalog",)),
            budget_mode=budget_mode,  # type: ignore[arg-type]
            target_value=target_value,
            include_demand_following=bool(inputs.get("include_demand_following", True)),
            upper_bound_slugs=tuple(inputs.get("upper_bound_slugs") or ()),
        )
        result_key = f"runs/{run_id}/optimisations/{optimisation_id}/result.json"
        storage.write_json(result_key, res)

        with SessionLocal() as db:
            opt = db.get(OptimisationRun, optimisation_id)
            if opt:
                opt.status = "succeeded"
                opt.result_path = result_key
                opt.summary = {
                    "budget_mode": res.get("budget_mode", "fixed"),
                    "target_value": res.get("target_value"),
                    "total_budget": res["total_budget"],
                    "historical_incremental_revenue": res["historical_incremental_revenue"],
                    "optimised_incremental_revenue": res["optimised_incremental_revenue"],
                    "historical_roi": res.get("historical_roi"),
                    "optimised_roi": res.get("optimised_roi"),
                    "uplift_eur": res["uplift_eur"],
                    "uplift_pct": res["uplift_pct"],
                }
                db.commit()
    except OptimisationError as exc:
        with SessionLocal() as db:
            opt = db.get(OptimisationRun, optimisation_id)
            if opt:
                opt.status = "failed"
                opt.error = str(exc)
                db.commit()
    except Exception as exc:
        logger.exception("Optimiser job %s failed", optimisation_id)
        with SessionLocal() as db:
            opt = db.get(OptimisationRun, optimisation_id)
            if opt:
                opt.status = "failed"
                opt.error = public_error(exc)
                db.commit()
        raise


def load_optimiser_inputs(storage: Any, run_id: str) -> dict[str, Any]:
    """The run's ROI table and response curves (raises when the Meridian stages have not produced them)."""
    roi_key = f"runs/{run_id}/data/meridian_roi.parquet"
    rc_key = f"runs/{run_id}/data/response_curves.json"
    if not (storage.exists(roi_key) and storage.exists(rc_key)):
        raise RuntimeError("The optimiser needs the Meridian response curves; wait for the Meridian fit to finish.")
    roi_df = _read_parquet(storage, roi_key)
    response_curves = {ch: pd.DataFrame(rows) for ch, rows in storage.read_json(rc_key).items()}
    return {"roi_df": roi_df, "response_curves": response_curves}


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
