"""End-to-end FastAPI tests: upload -> confirm roles -> start -> sections -> scenarios -> optimiser -> auth & queue caps."""

from __future__ import annotations

import os
from pathlib import Path

import psycopg
import pytest
from fastapi.testclient import TestClient

from mmm_studio.api.deps import reset_rate_limiter
from mmm_studio.db.models import Base
from mmm_studio.db.session import get_engine, get_session_factory, init_db
from mmm_studio.jobs.app import ensure_procrastinate_schema, get_procrastinate_app
from mmm_studio.jobs.worker import run_worker_loop
from mmm_studio.main import create_app
from mmm_studio.settings import get_settings
from mmm_studio.storage import get_storage

TEST_DB_URL = os.environ.get("TEST_DATABASE_URL", "postgresql:///mmm_studio_test?host=/tmp&port=5433")
FIXTURE_XLSX = Path(__file__).parent / "fixtures" / "case_study.xlsx"


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
    with FIXTURE_XLSX.open("rb") as fh:
        r_create = client.post(
            "/api/v1/runs",
            files={"file": ("DataScienceMarketingUseCase.xlsx", fh, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
            data={"name": "Case Study Evaluation", "profile": "quick"},
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
        json={"name": "Confirmed Case Study Run", "profile": "smoke", "skip_meridian": True},
    )
    assert r_patch.status_code == 200
    assert r_patch.json()["name"] == "Confirmed Case Study Run"

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

    # Seed the artifacts a Meridian run writes (ROI table, response curves, example-plan forecast)
    _seed_meridian_artifacts(run_id)

    r_fcs = client.get(f"/api/v1/runs/{run_id}/forecasts")
    assert any(f["id"] == default_fc_id and f["status"] == "succeeded" for f in r_fcs.json()["forecasts"])
    r_fc = client.get(f"/api/v1/forecasts/{default_fc_id}")
    assert r_fc.json()["result"]["scenario_total_sales"] > 0

    r_sc = client.post(
        f"/api/v1/forecasts/{default_fc_id}/scenarios",
        json={"name": "SEA +20%", "multipliers": {"sea": 1.2, "affiliate": 2.0}, "horizon_days": 42},
    )
    assert r_sc.status_code == 202
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

    # 7. Interactive budget optimiser
    r_opt = client.post(
        f"/api/v1/runs/{run_id}/optimisations",
        json={"min_multiplier": 0.7, "max_multiplier": 1.3, "include_catalog": True},
    )
    assert r_opt.status_code == 202
    opt_id = r_opt.json()["id"]

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

    r_opts_list = client.get(f"/api/v1/runs/{run_id}/optimisations")
    assert r_opts_list.status_code == 200
    assert r_opts_list.json()["run_id"] == run_id
    assert len(r_opts_list.json()["optimisations"]) == 1

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

    # Enable APP_ACCESS_CODE
    monkeypatch.setenv("MMM_APP_ACCESS_CODE", "secret-studio-2026")
    get_settings.cache_clear()

    # Open read endpoints still return 200 without header
    assert client.get("/api/v1/runs").status_code == 200

    # Write endpoint without header -> 401
    with FIXTURE_XLSX.open("rb") as fh:
        r_no_auth = client.post(
            "/api/v1/runs",
            files={"file": ("case_study.xlsx", fh, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        )
    assert r_no_auth.status_code == 401

    # Write endpoint with wrong header -> 403
    with FIXTURE_XLSX.open("rb") as fh:
        r_bad_auth = client.post(
            "/api/v1/runs",
            headers={"X-Access-Code": "wrong-code"},
            files={"file": ("case_study.xlsx", fh, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        )
    assert r_bad_auth.status_code == 403

    # Test Meridian queue cap (max 3 queued jobs)
    headers = {"X-Access-Code": "secret-studio-2026"}
    run_ids = []
    for i in range(4):
        with FIXTURE_XLSX.open("rb") as fh:
            resp = client.post(
                "/api/v1/runs",
                headers=headers,
                files={"file": ("case_study.xlsx", fh, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
                data={"name": f"Run {i}"},
            )
        assert resp.status_code == 201
        run_ids.append(resp.json()["id"])

    for rid in run_ids[:3]:
        r_st = client.post(f"/api/v1/runs/{rid}/start", headers=headers, json={"skip_meridian": False})
        assert r_st.status_code == 202

    # 4th Meridian start should return 429 Too Many Requests
    r_fourth = client.post(f"/api/v1/runs/{run_ids[3]}/start", headers=headers, json={"skip_meridian": False})
    assert r_fourth.status_code == 429

    # Cancelling one frees a slot
    r_cancel = client.post(f"/api/v1/runs/{run_ids[0]}/cancel", headers=headers)
    assert r_cancel.status_code == 200
    assert r_cancel.json()["status"] == "cancelled"

    r_fourth_retry = client.post(f"/api/v1/runs/{run_ids[3]}/start", headers=headers, json={"skip_meridian": False})
    assert r_fourth_retry.status_code == 202

    # Rate limit on /api/v1/auth/check (5 attempts per minute)
    reset_rate_limiter()
    for _ in range(5):
        r_chk = client.post("/api/v1/auth/check", headers=headers)
        assert r_chk.status_code == 200
    r_limited = client.post("/api/v1/auth/check", headers=headers)
    assert r_limited.status_code == 429
