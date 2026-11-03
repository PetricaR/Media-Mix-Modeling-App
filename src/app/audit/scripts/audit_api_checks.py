"""API-level audit checks against an isolated scratch database and storage directory (never the dev or test DB)."""

from __future__ import annotations

import io
import json
import os
import shutil
import sys
from pathlib import Path

import pandas as pd

SP = Path(sys.argv[1])
DB = sys.argv[2]
STORE = SP / "api_storage"
shutil.rmtree(STORE, ignore_errors=True)
STORE.mkdir(parents=True)
os.environ.update(
    MMM_DATABASE_URL=DB,
    MMM_STORAGE_BACKEND="local",
    MMM_LOCAL_DATA_DIR=str(STORE),
    MMM_WAKE_WORKERS="false",
    MMM_APP_ACCESS_CODE="s3cret-code",
    MMM_MAX_MERIDIAN_QUEUED="3",
)
REPO = Path("/Users/radanpetrica/02_PROFESSIONAL/Job_Opportunities/2026_Tchibo/Case-Study-Marketing-Data-Science-Tchibo")
XLSX = REPO / "src/app/backend/tests/fixtures/case_study.xlsx"
FULL_RUN = REPO / ".app_data/runs/run_f1e8df83da"

import psycopg  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from mmm_studio.db.models import Base  # noqa: E402
from mmm_studio.db.session import get_engine, init_db  # noqa: E402
from mmm_studio.jobs.app import ensure_procrastinate_schema  # noqa: E402
from mmm_studio.main import create_app  # noqa: E402

init_db()
ensure_procrastinate_schema()
Base.metadata.drop_all(bind=get_engine())
Base.metadata.create_all(bind=get_engine())
with psycopg.connect(DB, autocommit=True) as conn:
    conn.execute("TRUNCATE TABLE procrastinate_events, procrastinate_jobs RESTART IDENTITY CASCADE")

AUTH = {"X-Access-Code": "s3cret-code"}
results: list[tuple[str, bool, str]] = []


def record(name: str, ok: bool, evidence: str) -> None:
    results.append((name, ok, evidence))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}\n        {evidence}", flush=True)


raw = pd.read_excel(XLSX)


def xlsx_bytes(df: pd.DataFrame) -> bytes:
    buf = io.BytesIO()
    df.to_excel(buf, index=False, sheet_name="use_case_data")
    return buf.getvalue()


with TestClient(create_app()) as client:
    r = client.post("/api/v1/runs", files={"file": ("case.xlsx", XLSX.read_bytes())}, data={"name": "audit"}, headers=AUTH)
    assert r.status_code == 201, r.text
    run_id = r.json()["id"]

    # SEC-1: with an access code configured, reads need no code
    anon = {
        "GET /runs": client.get("/api/v1/runs").status_code,
        "GET /runs/{id}": client.get(f"/api/v1/runs/{run_id}").status_code,
        "GET uploaded workbook": client.get(f"/api/v1/runs/{run_id}/artifacts/input/workbook.xlsx").status_code,
        "GET profile.json": client.get(f"/api/v1/runs/{run_id}/artifacts/meta/profile.json").status_code,
    }
    wb = client.get(f"/api/v1/runs/{run_id}/artifacts/input/workbook.xlsx")
    record("SEC-1 reads require the access code when one is configured", all(v in (401, 403) for v in anon.values()),
           f"without X-Access-Code: {anon}; workbook bytes returned={len(wb.content)}")

    # DQ-1/2 via the API: duplicated rows and negative costs are accepted without a warning
    dup = pd.concat([raw, raw.iloc[:50]], ignore_index=True)
    r_dup = client.post("/api/v1/runs", files={"file": ("dup.xlsx", xlsx_bytes(dup))}, headers=AUTH)
    neg = raw.copy()
    neg.loc[neg.index[neg["costs"].fillna(0) > 0][:5], "costs"] *= -1
    r_neg = client.post("/api/v1/runs", files={"file": ("neg.xlsx", xlsx_bytes(neg))}, headers=AUTH)
    record("DQ-API upload with duplicates / negative costs is rejected or warned",
           r_dup.status_code != 201 or any("dup" in w.lower() for w in r_dup.json()["schema"]["warnings"]),
           f"duplicates -> {r_dup.status_code}, warnings={r_dup.json()['schema'].get('warnings') if r_dup.status_code == 201 else None}; "
           f"negatives -> {r_neg.status_code}, negative_values_count="
           f"{r_neg.json()['schema'].get('negative_values_count') if r_neg.status_code == 201 else None}")

    # Run the analysis stages synchronously (skip Meridian) so the run is 'succeeded'
    r = client.post(f"/api/v1/runs/{run_id}/start", json={"skip_meridian": True, "seed": 0}, headers=AUTH)
    assert r.status_code == 202, r.text
    from mmm_studio.jobs.tasks import execute_analysis_job

    execute_analysis_job(run_id, skip_meridian=True)
    # The job ran in-process, not through a worker: mark its queue row done as a worker would
    with psycopg.connect(DB, autocommit=True) as conn:
        conn.execute("UPDATE procrastinate_jobs SET status = 'succeeded' WHERE status = 'todo'")
    run = client.get(f"/api/v1/runs/{run_id}").json()
    assert run["status"] == "succeeded", run["status"]
    bench_before = client.get(f"/api/v1/runs/{run_id}/sections/benchmark").json()

    # STATE-1: settings of a finished run can be changed without re-running; the run then reports settings its
    # results were not computed with
    rc = run["config"]["role_config"]
    rc["holdout_days"] = 60
    rc["model_settings"]["n_fourier"] = 4
    r = client.patch(f"/api/v1/runs/{run_id}", json={"role_config": rc, "seed": 99}, headers=AUTH)
    after = client.get(f"/api/v1/runs/{run_id}").json()
    bench_after = client.get(f"/api/v1/runs/{run_id}/sections/benchmark").json()
    stored_cfg = json.loads((STORE / f"runs/{run_id}/meta/config.json").read_text())
    record("STATE-1 a finished run's settings cannot change without a re-run", r.status_code in (409, 422),
           f"PATCH on a succeeded run -> {r.status_code}; status still {after['status']}; settings_changes now "
           f"{[c['setting'] + '=' + c['value'] for c in after['settings_changes']]}; results unchanged="
           f"{bench_before == bench_after}; meta/config.json holdout_days={stored_cfg['holdout_days']} vs DB "
           f"{after['config']['role_config']['holdout_days']}")

    # STATE-2: the UI's re-run = PATCH then start; when start is refused (queue full) the PATCH has already landed
    os.environ["MMM_MAX_MERIDIAN_QUEUED"] = "0"
    from mmm_studio.settings import get_settings

    get_settings.cache_clear()
    rc2 = after["config"]["role_config"]
    rc2["model_settings"]["trend"] = False
    p = client.patch(f"/api/v1/runs/{run_id}", json={"role_config": rc2, "skip_meridian": False}, headers=AUTH)
    s = client.post(f"/api/v1/runs/{run_id}/start", json={"skip_meridian": False, "seed": 0}, headers=AUTH)
    after2 = client.get(f"/api/v1/runs/{run_id}").json()
    record("STATE-2 a refused re-run leaves the run's settings unchanged", s.status_code == 202 or after2["config"]["role_config"]["model_settings"]["trend"],
           f"PATCH {p.status_code} then start {s.status_code} ({s.json().get('detail', '')[:60]}...); run status={after2['status']}, "
           f"stored trend={after2['config']['role_config']['model_settings']['trend']}, skip_meridian={after2['config']['skip_meridian']}")
    os.environ["MMM_MAX_MERIDIAN_QUEUED"] = "3"
    get_settings.cache_clear()

    # OPT-API: give the run the full run's Meridian response curves and ROI table, then run optimisations directly
    for name in ("response_curves.json", "meridian_roi.parquet"):
        shutil.copyfile(FULL_RUN / "data" / name, STORE / f"runs/{run_id}/data/{name}")
    from mmm_studio.jobs.tasks import execute_optimiser_job

    def optimise(body: dict) -> dict:
        resp = client.post(f"/api/v1/runs/{run_id}/optimisations", json=body, headers=AUTH)
        if resp.status_code != 202:
            return {"http": resp.status_code, "detail": resp.json().get("detail")}
        oid = resp.json()["id"]
        try:
            execute_optimiser_job(oid)
        except Exception as exc:  # noqa: BLE001
            return {"error": str(exc)}
        return client.get(f"/api/v1/optimisations/{oid}").json()

    o1 = optimise({"channel_bounds": {"Display": [0.5, 0.5]}})
    d1 = next((a for a in o1.get("result", {}).get("allocations", []) if a["channel"] == "display"), None)
    record("OPT-API-1 a display-name key in channel_bounds is applied or rejected",
           o1.get("http") == 422 or (d1 is not None and abs(d1["multiplier"] - 0.5) < 0.01),
           f"channel_bounds={{'Display': [0.5, 0.5]}} -> display multiplier {d1['multiplier'] if d1 else None} (bound ignored silently)")
    o2 = optimise({"channel_bounds": {"display": [0.9]}})
    record("OPT-API-2 malformed channel_bounds are rejected", o2.get("http") == 422,
           f"channel_bounds={{'display': [0.9]}} -> HTTP {o2.get('http', 202)}, status {o2.get('status')}")
    o3 = optimise({"channel_bounds": {"display": [-5, 0.2]}})
    d3 = next((a for a in o3.get("result", {}).get("allocations", []) if a["channel"] == "display"), None)
    record("OPT-API-3 negative bounds are rejected", o3.get("http") == 422,
           f"channel_bounds={{'display': [-5, 0.2]}} -> HTTP {o3.get('http', 202)}, display spend {d3['optimised_spend'] if d3 else None}")
    o4 = optimise({"min_multiplier": 0.9, "max_multiplier": 1.1, "total_budget": 50_000_000})
    res4 = o4.get("result", {})
    outside = [f"{a['channel']} x{a['multiplier']}" for a in res4.get("allocations", []) if not 0.9 - 1e-3 <= a["multiplier"] <= 1.1 + 1e-3]
    record("OPT-API-4 an infeasible budget is reported, not silently re-bounded", bool(o4.get("http") == 422 or not outside),
           f"budget EUR 50M vs EUR 121.8M history with bounds [0.9, 1.1] -> status {o4.get('status')}, allocations outside bounds {outside}")

    # FC-API: a plan CSV with a misspelt channel column is accepted (that channel is forecast at EUR 0)
    wide = pd.read_parquet(STORE / f"runs/{run_id}/data/wide_model.parquet")
    start = wide.index.max() + pd.Timedelta(days=1)
    plan = pd.DataFrame({"date": pd.date_range(start, periods=14).strftime("%Y-%m-%d"), "sea": 50_000, "catalgo": 0, "display": 40_000})
    r = client.post(f"/api/v1/runs/{run_id}/forecasts", files={"file": ("plan.csv", plan.to_csv(index=False).encode())}, headers=AUTH)
    record("FC-API a plan with unknown or missing channel columns is rejected", r.status_code == 422,
           f"plan columns {list(plan.columns)} -> HTTP {r.status_code} ({r.json().get('status') or r.json().get('detail')})")

    # Re-run leaves old optimisation results / custom forecasts in place
    n_opts = len(client.get(f"/api/v1/runs/{run_id}/optimisations").json()["optimisations"])
    n_fc = len(client.get(f"/api/v1/runs/{run_id}/forecasts").json()["forecasts"])
    r = client.post(f"/api/v1/runs/{run_id}/start", json={"skip_meridian": True, "seed": 0}, headers=AUTH)
    n_opts2 = len(client.get(f"/api/v1/runs/{run_id}/optimisations").json()["optimisations"])
    n_fc2 = len(client.get(f"/api/v1/runs/{run_id}/forecasts").json()["forecasts"])
    record("STATE-3 a re-run clears results computed from the previous model", n_opts2 == 0 and n_fc2 == 0,
           f"re-run start -> {r.status_code}; optimisations before/after {n_opts}/{n_opts2}; custom forecasts before/after {n_fc}/{n_fc2}")

print("\nSUMMARY")
for n, ok, _ in results:
    print(("PASS " if ok else "FAIL ") + n)
