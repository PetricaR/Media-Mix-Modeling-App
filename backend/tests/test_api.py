"""End-to-end FastAPI tests: upload -> confirm roles -> start -> sections -> scenarios -> optimiser -> auth & queue caps."""

from __future__ import annotations

import io
import os
from pathlib import Path

import psycopg
import pytest
from fastapi.testclient import TestClient
from long_data import long_xlsx

from mmm_studio.api.deps import reset_rate_limiter
from mmm_studio.db.models import Base
from mmm_studio.db.session import get_engine, get_session_factory, init_db
from mmm_studio.jobs.app import ensure_procrastinate_schema, get_procrastinate_app
from mmm_studio.jobs.worker import run_worker_loop
from mmm_studio.main import create_app
from mmm_studio.settings import get_settings
from mmm_studio.storage import get_storage

TEST_DB_URL = os.environ.get("TEST_DATABASE_URL", "postgresql:///mmm_studio_test?host=/tmp&port=5433")


@pytest.fixture()
def api_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MMM_DATABASE_URL", TEST_DB_URL)
    monkeypatch.setenv("MMM_STORAGE_BACKEND", "local")
    monkeypatch.setenv("MMM_LOCAL_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("MMM_WAKE_WORKERS", "false")
    monkeypatch.setenv("MMM_APP_ACCESS_CODE", "")
    monkeypatch.setenv("MMM_MAX_MERIDIAN_QUEUED", "3")

    get_settings.cache_clear()
    get_storage.cache_clear()
    get_engine.cache_clear()
    get_session_factory.cache_clear()
    get_procrastinate_app.cache_clear()
    reset_rate_limiter()

    init_db()
    ensure_procrastinate_schema()

    engine = get_engine()
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    with psycopg.connect(TEST_DB_URL, autocommit=True) as conn:
        conn.execute("TRUNCATE TABLE procrastinate_events, procrastinate_jobs RESTART IDENTITY CASCADE")

    app = create_app()
    with TestClient(app) as client:
        yield client, monkeypatch

    get_settings.cache_clear()
    get_storage.cache_clear()
    get_engine.cache_clear()
    get_session_factory.cache_clear()
    get_procrastinate_app.cache_clear()
    reset_rate_limiter()


def _seed_meridian_artifacts(run_id: str) -> None:
    """Write the files the Meridian stages produce, with simple curves, so the interactive jobs can be tested fast."""
    import io

    import numpy as np
    import pandas as pd

    from mmm_studio.core.meridian.forecast import build_example_plan

    storage = get_storage()
    wide = pd.read_parquet(io.BytesIO(storage.read_bytes(f"runs/{run_id}/data/wide_model.parquet")))
    channels = ["sea", "catalog", "display", "display_network", "social_media", "psm"]
    roi = pd.DataFrame(
        {
            "spend": [float(wide[f"cost_{c}"].sum()) for c in channels],
            "roi": [8.0, 1.0, 0.7, 1.2, 0.7, 2.5],
            "mroi": [4.5, 0.8, 0.2, 0.5, 0.3, 1.2],
            "pct_of_contribution": [55.0, 6.0, 3.0, 1.0, 1.0, 1.0],
        },
        index=pd.Index(channels, name="channel"),
    )
    curves = {}
    for c, r in roi.iterrows():
        mult = np.round(np.arange(0, 2.01, 0.05), 2)
        inc = r["roi"] * r["spend"] * (1 - np.exp(-mult)) / (1 - np.exp(-1))
        curves[c] = [{"multiplier": float(m), "spend": float(m * r["spend"]), "mean": float(v), "ci_lo": float(0.8 * v), "ci_hi": float(1.2 * v)} for m, v in zip(mult, inc)]
    plan = build_example_plan(wide, days=42)
    daily = pd.DataFrame(
        {
            "forecast": 1.0e6,
            "credible_lo": 0.95e6,
            "credible_hi": 1.05e6,
            "prediction_lo": 0.85e6,
            "prediction_hi": 1.15e6,
            "planned_spend": plan.sum(axis=1).to_numpy(),
        },
        index=plan.index,
    )
    for name, frame in (("meridian_roi", roi), ("forecast_daily", daily), ("forecast_plan", plan)):
        buf = io.BytesIO()
        frame.to_parquet(buf, index=True)
        storage.write_bytes(f"runs/{run_id}/data/{name}.parquet", buf.getvalue())
    storage.write_json(f"runs/{run_id}/data/response_curves.json", curves)


def test_full_api_lifecycle_and_interactive_endpoints(api_env) -> None:
    client, _ = api_env

    # 1. Health & auth check
    r_health = client.get("/api/v1/health")
    assert r_health.status_code == 200
    assert r_health.json()["status"] == "ok"

    r_auth_chk = client.post("/api/v1/auth/check")
    assert r_auth_chk.status_code == 200
    assert r_auth_chk.json()["ok"] is True
    assert r_auth_chk.json()["protected"] is False
    assert r_auth_chk.json()["required"] is False

    # 2. Upload workbook (creates draft run)
    r_create = client.post(
        "/api/v1/runs",
        files={"file": ("sample_data.xlsx", io.BytesIO(long_xlsx()), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        data={"name": "Sample Workbook Evaluation", "profile": "quick"},
    )
    assert r_create.status_code == 201, r_create.text
    run_data = r_create.json()
    run_id = run_data["id"]
    assert run_data["status"] == "draft"
    assert run_data["schema"]["sheet_names"] == ["data", "info"]
    assert run_data["schema"]["years_present"] == [2023, 2024, 2025]
    assert run_data["schema"]["duplicate_rows_count"] == 0
    assert run_data["schema"]["negative_values_count"] == 0
    assert len(run_data["config"]["role_config"]["channels"]) == 12

    # 3. Confirm roles via PATCH
    r_patch = client.patch(
        f"/api/v1/runs/{run_id}",
        json={"name": "Confirmed Sample Run", "profile": "smoke", "skip_meridian": True},
    )
    assert r_patch.status_code == 200
    assert r_patch.json()["name"] == "Confirmed Sample Run"

    # 4. Start run (skip_meridian=True for fast test)
    r_start = client.post(
        f"/api/v1/runs/{run_id}/start",
        json={"profile": "smoke", "skip_meridian": True},
    )
    assert r_start.status_code == 202
    assert r_start.json()["status"] == "queued"

    # Execute queued analysis job with worker in burst mode
    run_worker_loop(queue="analysis", burst=True)

    # 5. Poll run & sections
    r_get = client.get(f"/api/v1/runs/{run_id}")
    assert r_get.status_code == 200
    assert r_get.json()["status"] == "succeeded"

    r_secs = client.get(f"/api/v1/runs/{run_id}/sections")
    assert r_secs.status_code == 200
    secs_json = r_secs.json()
    assert secs_json["status"] == "succeeded"
    assert secs_json["run_status"] == "succeeded"
    sec_ids = {s["id"] for s in secs_json["sections"]}
    assert {"overview", "data_quality", "explore_correlations", "preparation", "benchmark", "brief"}.issubset(sec_ids)

    r_ov = client.get(f"/api/v1/runs/{run_id}/sections/overview")
    assert r_ov.status_code == 200
    ov = r_ov.json()
    assert len(ov["blocks"]) >= 3
    kpi_blocks = [b for b in ov["blocks"] if b["type"] == "kpis"]
    assert kpi_blocks
    assert all("tone" in item and "status" in item for item in kpi_blocks[0]["items"])
    assert any(b["type"] == "chart" for b in ov["blocks"])

    # 6. Plan template & interactive scenario
    r_tpl = client.get(f"/api/v1/runs/{run_id}/plan-template?days=42")
    assert r_tpl.status_code == 200
    assert "date,sea,catalog" in r_tpl.text

    # Without a Meridian fit there is no forecast and no optimiser: nothing may be invented
    default_fc_id = f"default_{run_id}"
    r_fcs = client.get(f"/api/v1/runs/{run_id}/forecasts")
    assert r_fcs.status_code == 200
    assert all(f["id"] != default_fc_id for f in r_fcs.json()["forecasts"])
    r_opt_early = client.post(f"/api/v1/runs/{run_id}/optimisations", json={})
    assert r_opt_early.status_code == 409

    # Seed the artifacts a Meridian run writes (ROI table, response curves, example-plan forecast); the Meridian job
    # registers the example forecast when it finishes (GET requests never write)
    _seed_meridian_artifacts(run_id)
    assert all(f["id"] != default_fc_id for f in client.get(f"/api/v1/runs/{run_id}/forecasts").json()["forecasts"])
    from mmm_studio.jobs.registry import register_default_forecast

    with get_session_factory()() as db:
        register_default_forecast(db, get_storage(), run_id)

    r_fcs = client.get(f"/api/v1/runs/{run_id}/forecasts")
    assert any(f["id"] == default_fc_id and f["status"] == "succeeded" for f in r_fcs.json()["forecasts"])
    r_fc = client.get(f"/api/v1/forecasts/{default_fc_id}")
    assert r_fc.json()["result"]["scenario_total_sales"] > 0
    assert len(r_fc.json()["result"]["plan_daily"]["sea"]) == 42

    # A result stored before the planner showed spend gets its daily plan from the plan file
    storage = get_storage()
    result_key = f"runs/{run_id}/forecasts/{default_fc_id}/result.json"
    old = storage.read_json(result_key)
    storage.write_json(result_key, {k: v for k, v in old.items() if k not in ("plan_daily", "scenario_plan_daily")})
    backfilled = client.get(f"/api/v1/forecasts/{default_fc_id}").json()["result"]
    assert backfilled["plan_daily"] == old["plan_daily"] == backfilled["scenario_plan_daily"]

    # The history shown before the forecast: every modelled day, actual sales and paid spend per channel
    r_hist = client.get(f"/api/v1/runs/{run_id}/history")
    assert r_hist.status_code == 200
    hist = r_hist.json()
    assert len(hist["dates"]) == len(hist["sales"]) == 732
    assert hist["dates"][-1] == "2025-01-01"
    assert {c["slug"] for c in hist["channels"]} == set(hist["spend"]) >= {"sea", "catalog", "affiliate"}
    assert all(len(v) == 732 for v in hist["spend"].values())
    assert client.get("/api/v1/runs/run_missing/history").status_code == 404
    # No forecast stage yet, so no validation or test window is shown
    assert hist["backtest"] == []

    # With the forecast stage's back-tests the history carries the rolling-origin split: validation and test, each
    # forecast like a plan from the days before it (the plan-time variant when a commission control exists)
    import pandas as pd

    wide = pd.read_parquet(io.BytesIO(storage.read_bytes(f"runs/{run_id}/data/wide_model.parquet")))
    sales = wide["total_sales"]

    def window_frame(start: str, end: str, factor: float) -> pd.DataFrame:
        actual = sales.loc[start:end]
        return pd.DataFrame(
            {"forecast": actual * factor, "prediction_lo": actual * 0.8, "prediction_hi": actual * 1.3, "actual": actual},
            index=pd.DatetimeIndex(actual.index, name="date"),
        )

    def seed(name: str, frame: pd.DataFrame) -> None:
        buf = io.BytesIO()
        frame.to_parquet(buf, index=True)
        storage.write_bytes(f"runs/{run_id}/data/{name}.parquet", buf.getvalue())

    seed("forecast_validation_daily", window_frame("2024-07-01", "2024-09-30", 0.95))
    seed("forecast_backtest_daily", window_frame("2024-10-01", "2025-01-01", 1.05))
    seed("forecast_backtest_plan_time_daily", window_frame("2024-10-01", "2025-01-01", 1.1))
    backtest = client.get(f"/api/v1/runs/{run_id}/history").json()["backtest"]
    assert [(w["id"], w["label"], w["start"], w["end"], w["train_start"], w["train_end"], len(w["dates"])) for w in backtest] == [
        ("validation", "Q3 2024", "2024-07-01", "2024-09-30", "2023-01-01", "2024-06-30", 92),
        ("test", "Q4 2024", "2024-10-01", "2025-01-01", "2023-01-01", "2024-09-30", 93),
    ]
    # Training, validation and test tile the 732 days
    assert (pd.Timestamp("2024-06-30") - pd.Timestamp("2023-01-01")).days + 1 + 92 + 93 == len(hist["dates"])
    assert [w["plan_time"] for w in backtest] == [False, True]
    assert backtest[0]["mae_over_mean"] == pytest.approx(0.05)
    assert backtest[1]["mae_over_mean"] == pytest.approx(0.10)  # the plan-time variant, not the realised-commission one
    assert backtest[1]["forecast"][0] == round(1.1 * float(sales.loc["2024-10-01"]), 0)
    assert backtest[1]["lo"][0] == round(0.8 * float(sales.loc["2024-10-01"]), 0)

    # A stored split that does not tile the data is an error, never redrawn
    seed("forecast_validation_daily", window_frame("2024-07-01", "2024-09-29", 0.95))
    r_gap = client.get(f"/api/v1/runs/{run_id}/history")
    assert r_gap.status_code == 409
    assert "the validation window does not end the day before the test window" in r_gap.json()["detail"]
    for name in ("forecast_validation_daily", "forecast_backtest_daily", "forecast_backtest_plan_time_daily"):
        storage.delete_prefix(f"runs/{run_id}/data/{name}.parquet")

    # An unknown channel or a negative multiplier is rejected instead of silently returning "no change"
    for bad in ({"Teletext": 1.2}, {"sea": -0.5}):
        r_bad = client.post(f"/api/v1/forecasts/{default_fc_id}/scenarios", json={"multipliers": bad})
        assert r_bad.status_code == 422, r_bad.text

    # Display names are accepted and stored as slugs
    r_sc = client.post(
        f"/api/v1/forecasts/{default_fc_id}/scenarios",
        json={"name": "SEA +20%", "multipliers": {"SEA": 1.2, "Affiliate": 2.0}, "horizon_days": 42},
    )
    assert r_sc.status_code == 202
    assert r_sc.json()["inputs"]["multipliers"] == {"sea": 1.2, "affiliate": 2.0}
    sc_id = r_sc.json()["id"]

    run_worker_loop(queue="interactive", burst=True)

    r_sc_done = client.get(f"/api/v1/scenarios/{sc_id}")
    assert r_sc_done.status_code == 200
    sc_payload = r_sc_done.json()
    assert sc_payload["status"] == "succeeded", sc_payload.get("error")
    result = sc_payload["result"]
    assert result["delta_sales"] > 0
    assert "delta_sales_pct" in result and result["channel_summary"]
    aff = next(r for r in result["channel_summary"] if r["slug"] == "affiliate")
    assert aff["delta_spend"] == 0  # Affiliate is a control: its multiplier is ignored

    # 7. Interactive budget optimiser & Meridian Scenario Planner context
    r_ctx = client.get(f"/api/v1/runs/{run_id}/optimiser-context")
    assert r_ctx.status_code == 200
    ctx_payload = r_ctx.json()
    assert ctx_payload["run_id"] == run_id
    assert len(ctx_payload["channels"]) == 6
    assert "default_constrained" in ctx_payload and "default_unconstrained" in ctx_payload
    assert set(ctx_payload["response_curves"].keys()) == {c["channel"] for c in ctx_payload["channels"]}

    r_opt = client.post(
        f"/api/v1/runs/{run_id}/optimisations",
        json={"min_multiplier": 0.7, "max_multiplier": 1.3, "include_catalog": True},
    )
    assert r_opt.status_code == 202
    opt_id = r_opt.json()["id"]

    # Flexible mode: Target channel mROI
    r_opt_mroi = client.post(
        f"/api/v1/runs/{run_id}/optimisations",
        json={
            "name": "Target mROI 1.2x",
            "budget_mode": "target_mroi",
            "target_value": 1.2,
            "min_multiplier": 0.5,
            "max_multiplier": 1.5,
            "include_demand_following": False,
        },
    )
    assert r_opt_mroi.status_code == 202
    opt_mroi_id = r_opt_mroi.json()["id"]

    # Flexible mode: Target total ROI (feasible vs infeasible)
    r_opt_roi_infeasible = client.post(
        f"/api/v1/runs/{run_id}/optimisations",
        json={
            "budget_mode": "target_roi",
            "target_value": 45.0,
            "min_multiplier": 0.7,
            "max_multiplier": 1.3,
        },
    )
    assert r_opt_roi_infeasible.status_code == 422

    r_opt_roi = client.post(
        f"/api/v1/runs/{run_id}/optimisations",
        json={
            "name": "Target ROI",
            "budget_mode": "target_roi",
            "target_value": max(0.5, ctx_payload["historical_roi"] * 0.9),
            "min_multiplier": 0.7,
            "max_multiplier": 1.3,
        },
    )
    assert r_opt_roi.status_code == 202
    opt_roi_id = r_opt_roi.json()["id"]

    run_worker_loop(queue="interactive", burst=True)

    r_opt_done = client.get(f"/api/v1/optimisations/{opt_id}")
    assert r_opt_done.status_code == 200
    opt_payload = r_opt_done.json()
    assert opt_payload["status"] == "succeeded", opt_payload.get("error")
    assert len(opt_payload["result"]["channels"]) == 6
    assert len(opt_payload["result"]["allocations"]) == 6
    first_alloc = opt_payload["result"]["allocations"][0]
    for key in ("channel_label", "current_spend", "optimal_spend", "delta_pct", "expected_incremental_sales", "roi", "mroi"):
        assert key in first_alloc
    assert abs(sum(a["optimal_spend"] for a in opt_payload["result"]["allocations"]) - opt_payload["result"]["total_budget"]) < 10

    r_mroi_done = client.get(f"/api/v1/optimisations/{opt_mroi_id}")
    assert r_mroi_done.status_code == 200
    assert r_mroi_done.json()["status"] == "succeeded", r_mroi_done.json().get("error")
    assert r_mroi_done.json()["result"]["budget_mode"] == "target_mroi"

    r_roi_done = client.get(f"/api/v1/optimisations/{opt_roi_id}")
    assert r_roi_done.status_code == 200
    assert r_roi_done.json()["status"] == "succeeded", r_roi_done.json().get("error")
    assert r_roi_done.json()["result"]["budget_mode"] == "target_roi"

    r_opts_list = client.get(f"/api/v1/runs/{run_id}/optimisations")
    assert r_opts_list.status_code == 200
    assert r_opts_list.json()["run_id"] == run_id
    assert len(r_opts_list.json()["optimisations"]) == 3

    # A finished run cannot be cancelled; a re-run resets its stages
    assert client.post(f"/api/v1/runs/{run_id}/cancel").status_code == 409

    # 8. Artifacts listing & streaming
    r_arts = client.get(f"/api/v1/runs/{run_id}/artifacts")
    assert r_arts.status_code == 200
    assert len(r_arts.json()["artifacts"]) > 0

    r_art_dl = client.get(f"/api/v1/runs/{run_id}/artifacts/meta/summary.json")
    assert r_art_dl.status_code == 200


def test_access_code_guard_rate_limiter_and_meridian_queue_cap(api_env) -> None:
    client, monkeypatch = api_env

    monkeypatch.setenv("MMM_APP_ACCESS_CODE", "secret-studio-2026")
    get_settings.cache_clear()

    # Every data route needs the code: reads included (runs, uploads, artifacts)
    assert client.get("/api/v1/health").status_code == 200
    assert client.get("/api/v1/health").json()["auth_required"] is True
    assert client.get("/api/v1/runs").status_code == 401
    assert client.get("/api/v1/auth/session").json() == {"auth_required": True, "authenticated": False}

    r_no_auth = client.post(
        "/api/v1/runs",
        files={"file": ("sample_data.xlsx", io.BytesIO(long_xlsx()), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert r_no_auth.status_code == 401

    r_bad_auth = client.post(
        "/api/v1/runs",
        headers={"X-Access-Code": "wrong-code"},
        files={"file": ("sample_data.xlsx", io.BytesIO(long_xlsx()), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert r_bad_auth.status_code == 403

    # Test Meridian queue cap (max 3 queued fits); the Meridian job is chained after the analysis job, so runs that
    # are still in their analysis stage count too
    headers = {"X-Access-Code": "secret-studio-2026"}
    run_ids = []
    for i in range(4):
        resp = client.post(
            "/api/v1/runs",
            headers=headers,
            files={"file": ("sample_data.xlsx", io.BytesIO(long_xlsx()), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
            data={"name": f"Run {i}"},
        )
        assert resp.status_code == 201
        run_ids.append(resp.json()["id"])

    # The uploaded workbook is private: no code, no download
    assert client.get(f"/api/v1/runs/{run_ids[0]}/artifacts/input/workbook.xlsx").status_code == 401
    assert client.get(f"/api/v1/runs/{run_ids[0]}/artifacts/input/workbook.xlsx", headers=headers).status_code == 200

    for rid in run_ids[:3]:
        r_st = client.post(f"/api/v1/runs/{rid}/start", headers=headers, json={"skip_meridian": False})
        assert r_st.status_code == 202

    r_fourth = client.post(f"/api/v1/runs/{run_ids[3]}/start", headers=headers, json={"skip_meridian": False})
    assert r_fourth.status_code == 429
    assert client.get(f"/api/v1/runs/{run_ids[3]}", headers=headers).json()["status"] == "draft"  # nothing changed

    r_cancel = client.post(f"/api/v1/runs/{run_ids[0]}/cancel", headers=headers)
    assert r_cancel.status_code == 200
    assert r_cancel.json()["status"] == "cancelled"

    r_fourth_retry = client.post(f"/api/v1/runs/{run_ids[3]}/start", headers=headers, json={"skip_meridian": False})
    assert r_fourth_retry.status_code == 202

    # Signing in sets an HttpOnly session cookie that authenticates reads (links and report frames send no headers)
    reset_rate_limiter()
    r_login = client.post("/api/v1/auth/login", json={"access_code": "secret-studio-2026"})
    assert r_login.status_code == 200
    assert "httponly" in r_login.headers["set-cookie"].lower() and "samesite=strict" in r_login.headers["set-cookie"].lower()
    assert client.get("/api/v1/runs").status_code == 200
    assert client.get("/api/v1/auth/session").json()["authenticated"] is True
    client.post("/api/v1/auth/logout")
    client.cookies.clear()
    assert client.get("/api/v1/runs").status_code == 401

    # Failed attempts are rate limited per client (5 a minute), on sign-in and on every guarded route
    for _ in range(5):
        assert client.post("/api/v1/auth/login", json={"access_code": "nope"}).status_code == 403
    assert client.post("/api/v1/auth/login", json={"access_code": "secret-studio-2026"}).status_code == 429
    assert client.get("/api/v1/runs", headers={"X-Access-Code": "nope"}).status_code == 429
    reset_rate_limiter()


def test_finished_runs_keep_their_settings_and_reruns_are_atomic(api_env) -> None:
    client, monkeypatch = api_env
    run = client.post(
        "/api/v1/runs",
        files={"file": ("sample.xlsx", io.BytesIO(long_xlsx()), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        data={"name": "Frozen"},
    ).json()
    run_id = run["id"]
    assert client.post(f"/api/v1/runs/{run_id}/start", json={"skip_meridian": True}).status_code == 202
    run_worker_loop(queue="analysis", burst=True)
    assert client.get(f"/api/v1/runs/{run_id}").json()["status"] == "succeeded"

    # Settings of a run with results cannot change without a re-run; the name can
    rc = client.get(f"/api/v1/runs/{run_id}").json()["config"]["role_config"]
    rc_changed = {**rc, "holdout_days": 60}
    assert client.patch(f"/api/v1/runs/{run_id}", json={"role_config": rc_changed}).status_code == 409
    assert client.patch(f"/api/v1/runs/{run_id}", json={"seed": 5}).status_code == 409
    assert client.patch(f"/api/v1/runs/{run_id}", json={"name": "Renamed"}).json()["name"] == "Renamed"

    # A refused re-run (queue full) changes nothing
    monkeypatch.setenv("MMM_MAX_MERIDIAN_QUEUED", "0")
    get_settings.cache_clear()
    r = client.post(f"/api/v1/runs/{run_id}/start", json={"skip_meridian": False, "role_config": rc_changed})
    assert r.status_code == 429
    after = client.get(f"/api/v1/runs/{run_id}").json()
    assert after["status"] == "succeeded" and after["config"]["role_config"]["holdout_days"] == 93
    monkeypatch.setenv("MMM_MAX_MERIDIAN_QUEUED", "3")
    get_settings.cache_clear()

    # Settings that cannot run are a 422 that says why
    rc_bad = {
        **rc,
        "model_settings": {**rc["model_settings"], "roi_prior": "custom", "custom_roi_priors": {"affiliate": {"median": 2.0, "sigma": 0.5}}},
    }
    r = client.post(f"/api/v1/runs/{run_id}/start", json={"skip_meridian": True, "role_config": rc_bad})
    assert r.status_code == 422 and "not media channels" in r.json()["detail"]

    # A re-run removes what the previous settings produced, including optimisations and custom forecasts
    _seed_meridian_artifacts(run_id)
    assert client.post(f"/api/v1/runs/{run_id}/optimisations", json={}).status_code == 202
    r = client.post(f"/api/v1/runs/{run_id}/start", json={"skip_meridian": True, "role_config": rc_changed})
    assert r.status_code == 202, r.text
    assert r.json()["config"]["role_config"]["holdout_days"] == 60
    assert client.get(f"/api/v1/runs/{run_id}/optimisations").json()["optimisations"] == []

    # A run whose previous job is still queued cannot be started twice (409, never a 500)
    client.post(f"/api/v1/runs/{run_id}/cancel")
    with psycopg.connect(TEST_DB_URL, autocommit=True) as conn:
        conn.execute(
            "UPDATE procrastinate_jobs SET status = 'todo', abort_requested = false "
            "WHERE id = (SELECT max(id) FROM procrastinate_jobs WHERE queueing_lock = %s)",
            (f"analysis:{run_id}",),
        )
    assert client.post(f"/api/v1/runs/{run_id}/start", json={"skip_meridian": True}).status_code == 409


def test_optimiser_requests_are_validated(api_env) -> None:
    client, _ = api_env
    run_id = client.post(
        "/api/v1/runs",
        files={"file": ("sample.xlsx", io.BytesIO(long_xlsx()), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    ).json()["id"]
    assert client.post(f"/api/v1/runs/{run_id}/start", json={"skip_meridian": True}).status_code == 202
    run_worker_loop(queue="analysis", burst=True)
    _seed_meridian_artifacts(run_id)

    def post(body: dict) -> object:
        return client.post(f"/api/v1/runs/{run_id}/optimisations", json=body)

    assert post({"channel_bounds": {"Display": [0.5, 0.5]}}).status_code == 202  # display names are accepted
    assert post({"channel_bounds": {"Teletext": [0.5, 1.0]}}).status_code == 422  # unknown channel
    assert post({"channel_bounds": {"affiliate": [0.5, 1.0]}}).status_code == 422  # a control, not media
    assert post({"channel_bounds": {"display": [0.9]}}).status_code == 422  # malformed
    assert post({"channel_bounds": {"display": [-5, 0.2]}}).status_code == 422  # negative
    assert post({"channel_bounds": {"display": [1.2, 0.8]}}).status_code == 422  # reversed
    assert post({"max_multiplier": 3.0}).status_code == 422  # beyond the response curves
    r = post({"total_budget": 50_000_000, "min_multiplier": 0.9, "max_multiplier": 1.1})
    assert r.status_code == 422 and "cannot be allocated" in r.json()["detail"]

    run_worker_loop(queue="interactive", burst=True)
    opt = client.get(f"/api/v1/runs/{run_id}/optimisations").json()["optimisations"][0]
    assert opt["status"] == "succeeded", opt.get("error")
    display = next(a for a in opt["result"]["allocations"] if a["channel"] == "display")
    assert display["multiplier"] == pytest.approx(0.5, abs=0.002)


def test_plans_with_wrong_columns_are_rejected(api_env) -> None:
    import pandas as pd

    client, _ = api_env
    run_id = client.post(
        "/api/v1/runs",
        files={"file": ("sample.xlsx", io.BytesIO(long_xlsx()), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    ).json()["id"]
    assert client.post(f"/api/v1/runs/{run_id}/start", json={"skip_meridian": True}).status_code == 202
    run_worker_loop(queue="analysis", burst=True)
    template = pd.read_csv(io.StringIO(client.get(f"/api/v1/runs/{run_id}/plan-template?days=14").text))
    bad = template.rename(columns={"catalog": "catalgo"})
    r = client.post(f"/api/v1/runs/{run_id}/forecasts", files={"file": ("plan.csv", bad.to_csv(index=False).encode())})
    assert r.status_code == 422 and "catalgo" in r.json()["detail"]


def test_run_settings_are_validated_and_reported(api_env) -> None:
    client, _ = api_env

    r_defaults = client.get("/api/v1/runs/settings/defaults")
    assert r_defaults.status_code == 200
    defaults = r_defaults.json()
    assert defaults["model_settings"]["max_lag"] == 21
    assert defaults["limits"]["sampling.n_chains"] == {"min": 1, "max": 4}

    r_create = client.post(
        "/api/v1/runs",
        files={"file": ("sample.xlsx", io.BytesIO(long_xlsx()), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        data={"name": "Settings", "profile": "full"},
    )
    run = r_create.json()
    assert run["settings_changes"] == []
    role_config = run["config"]["role_config"]
    assert {c["inferred_role"] for c in role_config["channels"]} >= {"paid", "print_event", "commission", "unpaid"}

    # Out-of-range values are rejected before anything is stored
    too_many = {**role_config, "model_settings": {**role_config["model_settings"], "sampling": {"n_chains": 9}}}
    assert client.patch(f"/api/v1/runs/{run['id']}", json={"role_config": too_many, "profile": "custom"}).status_code == 422
    assert client.patch(f"/api/v1/runs/{run['id']}", json={"seed": -1}).status_code == 422
    assert client.get(f"/api/v1/runs/{run['id']}").json()["profile"] == "full"

    changed = {
        **role_config,
        "holdout_days": 60,
        "model_settings": {**role_config["model_settings"], "max_lag": 14, "sampling": {"n_chains": 2, "n_keep": 300}},
    }
    r_patch = client.patch(f"/api/v1/runs/{run['id']}", json={"role_config": changed, "profile": "custom", "seed": 3})
    assert r_patch.status_code == 200, r_patch.text
    body = r_patch.json()
    assert body["profile"] == "custom"
    assert body["config"]["role_config"]["model_settings"]["max_lag"] == 14
    settings = {r["setting"] for r in body["settings_changes"]}
    assert {"Hold-out length", "Max lag", "Random seed", "Sampling draws"} <= settings


def test_upload_with_a_confirmed_wide_mapping_and_a_csv(api_env) -> None:
    import sys

    sys.path.insert(0, str(Path(__file__).parent))
    from wide_data import wide_csv, wide_xlsx

    from mmm_studio.core.schema import ChannelConfig, DataRoleConfig
    from mmm_studio.core.workbook import inspect_workbook

    client, _ = api_env
    preview = inspect_workbook(wide_xlsx(), "mmm.xlsx")
    rc = DataRoleConfig.model_validate(preview["preview"]["role_config"])
    rc.channels = [ChannelConfig(name="TV Ads", role="ignored")]
    rc.country = "AT"
    rc.model_settings.weather_controls = True
    r = client.post(
        "/api/v1/runs",
        files={"file": ("mmm.xlsx", wide_xlsx(), "application/octet-stream")},
        data={"name": "Wide", "profile": "smoke", "role_config": rc.model_dump_json()},
    )
    assert r.status_code == 201, r.text
    run = r.json()
    stored = run["config"]["role_config"]
    assert stored["columns"]["layout"] == "wide" and stored["country"] == "AT"
    assert stored["model_settings"]["weather_controls"] and stored["holdout_days"] == 35
    tv = next(c for c in stored["channels"] if c["name"] == "TV Ads")
    assert tv["role"] == "ignored" and tv["inferred_role"] == "paid" and tv["total_cost"] > 0
    assert run["schema"]["layout"] == "wide" and run["schema"]["control_names"] == ["Covid"]
    changes = {c["setting"] for c in run["settings_changes"]}
    assert {"Country (holidays, weather)", "Weather controls", "Role · TV Ads"} <= changes

    # A CSV upload without a mapping: the app's guess, stored under its own extension
    r = client.post("/api/v1/runs", files={"file": ("mmm.csv", wide_csv(";"), "text/csv")}, data={"name": "CSV"})
    assert r.status_code == 201, r.text
    assert client.get(f"/api/v1/runs/{r.json()['id']}/artifacts/input/workbook.csv").status_code == 200

    # A mapping the data cannot satisfy is a 422 with the reason
    rc.holdout_days = 120
    r = client.post(
        "/api/v1/runs",
        files={"file": ("mmm.xlsx", wide_xlsx(), "application/octet-stream")},
        data={"role_config": rc.model_dump_json()},
    )
    assert r.status_code == 422 and "training days" in r.json()["detail"]
