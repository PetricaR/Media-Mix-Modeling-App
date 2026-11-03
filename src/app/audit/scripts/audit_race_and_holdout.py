"""(1) Job-ordering race: the Meridian job runs before the analysis job, then the analysis job runs. Do the final
overview/benchmark/brief survive? (2) Short dataset: does a 'hold-out' metric get reported without any hold-out?"""

from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

import pandas as pd

SP = Path(sys.argv[1])
DB = sys.argv[2]
STORE = SP / "race_storage"
shutil.rmtree(STORE, ignore_errors=True)
STORE.mkdir(parents=True)
os.environ.update(MMM_DATABASE_URL=DB, MMM_STORAGE_BACKEND="local", MMM_LOCAL_DATA_DIR=str(STORE),
                  MMM_WAKE_WORKERS="false", MMM_APP_ACCESS_CODE="", MMM_MAX_MERIDIAN_QUEUED="3")
REPO = Path("/Users/radanpetrica/02_PROFESSIONAL/Job_Opportunities/2026_Tchibo/Case-Study-Marketing-Data-Science-Tchibo")
XLSX = REPO / "src/app/backend/tests/fixtures/case_study.xlsx"

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

out: dict[str, object] = {}


def overview_model(run_id: str) -> str:
    sec = json.loads((STORE / f"runs/{run_id}/sections/overview.json").read_text())
    kpi = sec["blocks"][0]["items"][0]
    return f"{kpi['label']} = {kpi['value']} ({kpi['delta']})"


def bench_has_meridian(run_id: str) -> bool:
    sec = json.loads((STORE / f"runs/{run_id}/sections/benchmark.json").read_text())
    return any(r.get("model") == "Meridian MMM" for b in sec["blocks"] if b.get("type") == "table" for r in b.get("rows", []))


with TestClient(create_app()) as client:
    run_id = client.post("/api/v1/runs", files={"file": ("case.xlsx", XLSX.read_bytes())}).json()["id"]
    client.patch(f"/api/v1/runs/{run_id}", json={"profile": "smoke"})
    assert client.post(f"/api/v1/runs/{run_id}/start", json={"profile": "smoke", "skip_meridian": False}).status_code == 202

    from mmm_studio.jobs.tasks import execute_analysis_job, execute_meridian_job

    # Meridian worker picks its job up first (e.g. the analysis worker is cold-starting or busy)
    execute_meridian_job(run_id)
    out["after_meridian_job"] = {"status": client.get(f"/api/v1/runs/{run_id}").json()["status"],
                                 "overview_kpi": overview_model(run_id), "benchmark_has_meridian": bench_has_meridian(run_id)}
    # ...then the analysis job runs
    execute_analysis_job(run_id)
    out["after_analysis_job"] = {"status": client.get(f"/api/v1/runs/{run_id}").json()["status"],
                                 "overview_kpi": overview_model(run_id), "benchmark_has_meridian": bench_has_meridian(run_id)}
    print(json.dumps(out, indent=2), flush=True)

# (2) short dataset: 390 days, hold-out 200 days
from mmm_studio.core.meridian.analysis import build_meridian_model_section  # noqa: E402
from mmm_studio.core.meridian.fit import fit_meridian_isolated  # noqa: E402
from mmm_studio.core.meridian.spec import MeridianSpecConfig  # noqa: E402
from mmm_studio.core.model_settings import SMOKE_SAMPLING  # noqa: E402
from mmm_studio.core.tables import build_daily_wide_table  # noqa: E402
from mmm_studio.core.workbook import profile_workbook  # noqa: E402

df, prof = profile_workbook(XLSX.read_bytes(), filename="case_study.xlsx")
rc = prof.role_config
rc.holdout_days = 200
wide = build_daily_wide_table(df, config=rc).loc["2023-01-01":"2024-01-25"]
cfg = MeridianSpecConfig.from_role_config(rc, wide=wide)
mmm = fit_meridian_isolated(wide, cfg, SP / "short.binpb", sampling=SMOKE_SAMPLING, seed=0, spawn_child=False)
sec, metrics = build_meridian_model_section(mmm, wide, cfg)
out["short_dataset"] = {
    "days": len(wide),
    "holdout_days_setting": 200,
    "cfg_holdout": cfg.holdout,
    "reported_holdout_days": metrics["holdout_days"],
    "reported_holdout_label": metrics["holdout_label"],
    "holdout_mae_over_mean": round(metrics["q4_mae_over_mean"], 4),
    "train_mae_over_mean": round(metrics["train"]["MAE_over_mean"], 4),
    "first_kpi": f"{sec.blocks[0].items[0].label} = {sec.blocks[0].items[0].value} ({sec.blocks[0].items[0].delta})",
}
print(json.dumps(out["short_dataset"], indent=2))
(SP / "race_and_holdout.json").write_text(json.dumps(out, indent=2))
print("DONE")
