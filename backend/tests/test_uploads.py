"""The upload wizard's routes: inspection before a run exists, and Gemini's mapping checked item by item."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from wide_data import CHANNELS, wide_xlsx

from mmm_studio.api import explain as explain_api
from mmm_studio.api import uploads as uploads_api
from mmm_studio.api.deps import reset_rate_limiter
from mmm_studio.api.router import api_router
from mmm_studio.core.schema import ColumnMapping, DataRoleConfig
from mmm_studio.settings import get_settings
from mmm_studio.storage import get_storage

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    def make(gemini: bool = True) -> TestClient:
        env = {
            "MMM_STORAGE_BACKEND": "local",
            "MMM_LOCAL_DATA_DIR": str(tmp_path),
            "MMM_APP_ACCESS_CODE": "",
            "MMM_GEMINI_API_KEY": "test-key" if gemini else "",
            "MMM_GEMINI_VERTEX": "false",
            "MMM_EXPLAIN_PER_MINUTE": "20",
        }
        for name, value in env.items():
            monkeypatch.setenv(name, value)
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        get_settings.cache_clear()
        get_storage.cache_clear()
        app = FastAPI()
        app.include_router(api_router)
        return TestClient(app)

    reset_rate_limiter()
    explain_api.reset_explain_limiter()
    yield make
    get_settings.cache_clear()
    get_storage.cache_clear()
    explain_api.reset_explain_limiter()


def _files(data: bytes, name: str = "mmm.xlsx") -> dict:
    return {"file": (name, data, XLSX)}


def test_inspect_suggests_a_mapping_and_previews_the_run(client) -> None:
    c = client()
    r = c.post("/api/v1/uploads/inspect", files=_files(wide_xlsx()))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["selected_sheet"] == "data" and body["suggested"] and body["errors"] == []
    assert body["mapping"]["layout"] == "wide" and body["mapping"]["target"] == "Revenue"
    assert body["preview"]["role_config"]["holdout_days"] == 35
    # The user's mapping is checked, and its problems are listed rather than raised
    rc = DataRoleConfig(columns=ColumnMapping(layout="wide", date="Date", target="Sessions"), sheet_name="data")
    r = c.post("/api/v1/uploads/inspect", files=_files(wide_xlsx()), data={"role_config": rc.model_dump_json()})
    assert r.status_code == 200 and any("at least one column" in e for e in r.json()["errors"])
    assert c.post("/api/v1/uploads/inspect", files=_files(b"x", "notes.txt")).status_code == 400
    assert c.post("/api/v1/uploads/inspect", files=_files(wide_xlsx()), data={"role_config": "{"}).status_code == 422


ANSWER = {
    "summary": "A daily wide table: Revenue is the target and three channels report spend.",
    "layout": "wide",
    "date_column": "Date",
    "date_reason": "One row per day.",
    "target_column": "Revenue",
    "target_reason": "The daily revenue.",
    "channel_column": "",
    "sales_column": "",
    "cost_column": "",
    "impressions_column": "",
    "clicks_column": "",
    "channels": [
        {"name": "Brand search", "spend_column": CHANNELS[0], "impressions_column": "", "clicks_column": "", "sales_column": "", "role": "paid", "reason": "Search spend.", "confidence": "high"},
        {"name": "TV", "spend_column": CHANNELS[1], "impressions_column": "", "clicks_column": "", "sales_column": "", "role": "paid", "reason": "TV spend; the notes sheet says its effect is delayed by 3 days.", "confidence": "high"},
        {"name": "Catalog ads", "spend_column": CHANNELS[2], "impressions_column": "", "clicks_column": "", "sales_column": "", "role": "paid", "reason": "Digital catalogue ads.", "confidence": "medium"},
        {"name": "Radio", "spend_column": "Radio Spend", "impressions_column": "", "clicks_column": "", "sales_column": "", "role": "paid", "reason": "Radio.", "confidence": "low"},
    ],
    "controls": [{"column": "Covid", "name": "Covid lockdown", "reason": "A 0/1 flag on 30 days."}],
    "ignored": [{"column": "Sessions", "reason": "An outcome of demand."}],
    "country": "AT",
    "country_reason": "Austrian channel names.",
    "better_sheet": "short",
    "sheet_reason": "Fewer days.",
    "notes": ["The notes sheet lists attributions for two channels."],
}


def test_geminis_mapping_is_checked_item_by_item_and_cached(client, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def fake(_system: str, prompt: str, _settings, _schema) -> str:
        calls.append(prompt)
        return json.dumps(ANSWER)

    monkeypatch.setattr(uploads_api, "generate_json", fake)
    c = client()
    r = c.post("/api/v1/uploads/recommendation", files=_files(wide_xlsx()))
    assert r.status_code == 200, r.text
    body = r.json()
    assert "Side table on sheet 'notes'" in calls[0] and "'Covid'" in calls[0]
    m = body["mapping"]
    assert m["target"] == "Revenue" and [c["name"] for c in m["wide_channels"]] == ["Brand search", "TV", "Catalog ads"]
    assert m["controls"] == [{"column": "Covid", "name": "Covid lockdown"}]
    assert [x["item"] for x in body["rejected"]] == ["Radio spend: 'Radio Spend'"]  # no such column
    assert body["errors"] == [] and body["unreviewed"] == []
    assert body["country"] == "AT" and body["better_sheet"] == "short"
    assert body["column_notes"]["Sessions"]["role"] == "ignore"
    # The answer is cached by content: asking again does not call Gemini
    assert c.post("/api/v1/uploads/recommendation", files=_files(wide_xlsx())).json()["cached"] and len(calls) == 1


def test_mapping_recommendation_without_gemini_is_a_503(client) -> None:
    c = client(gemini=False)
    assert c.post("/api/v1/uploads/recommendation", files=_files(wide_xlsx())).status_code == 503


def test_review_mapping_accepts_categorical_and_partially_blank_controls_and_omits_rejected_from_unreviewed() -> None:
    import numpy as np
    import pandas as pd

    from mmm_studio.explain.mapping import MappingAdvice, review_mapping

    n = 60
    df = pd.DataFrame(
        {
            "date": pd.date_range("2024-01-01", periods=n, freq="7D"),
            "sales": np.linspace(1000.0, 2000.0, n),
            "tv_spend": np.linspace(100.0, 300.0, n),
            "competitor_spend": np.linspace(50.0, 80.0, n),
            "sales_promotion": ["Normal", "Coupons", "BOGO", "Flash Sale"] * (n // 4),
            "mostly_blank_ctrl": [1.0 if i < 5 else np.nan for i in range(n)],
        }
    )
    df.loc[[1, 5], "competitor_spend"] = np.nan
    advice = MappingAdvice(
        summary="Weekly wide dataset.",
        layout="wide",
        date_column="date",
        date_reason="Weekly Mondays.",
        target_column="sales",
        target_reason="Weekly sales.",
        channel_column="",
        sales_column="",
        cost_column="",
        impressions_column="",
        clicks_column="",
        channels=[
            {
                "name": "TV",
                "spend_column": "tv_spend",
                "impressions_column": "",
                "clicks_column": "",
                "sales_column": "",
                "role": "paid",
                "reason": "TV spend.",
                "confidence": "high",
            }
        ],
        controls=[
            {"column": "competitor_spend", "name": "Competitor spend", "reason": "Competitor activity."},
            {"column": "sales_promotion", "name": "Sales promotion", "reason": "Promotion type."},
            {"column": "mostly_blank_ctrl", "name": "Mostly blank", "reason": "Too many blanks."},
        ],
        ignored=[],
        country="DE",
        country_reason="Germany.",
        better_sheet="",
        sheet_reason="",
        notes=[],
    )
    rev = review_mapping(advice, df, ["csv"], "csv", [])
    # A mostly blank control is mapped: the "Resample & fill" step decides how to fill it
    assert [c.column for c in rev.mapping.controls] == ["competitor_spend", "sales_promotion", "mostly_blank_ctrl"]
    promo = next(c for c in rev.mapping.controls if c.column == "sales_promotion")
    assert promo.categories == ["Coupons", "BOGO", "Flash Sale"]
    assert rev.rejected == []
    assert rev.unreviewed == []
    assert rev.errors == []


def _column(column: str, fill: str = "", reason: str = "Fits.", **kw) -> dict:
    return {"column": column, "aggregate": "default", "fill": fill or "default", "fill_value": 0, "disaggregate": "default", "reason": reason, "confidence": "high", **kw}


RESAMPLE_ANSWER = {
    "summary": "A daily table with one missing day and a few blank revenue cells.",
    "frequency": "daily",
    "frequency_reason": "One row per day.",
    "period_label": "start",
    "period_label_reason": "",
    "fill_missing_dates": True,
    "missing_dates_reason": "Only 1 day is missing.",
    "columns": [
        _column("Revenue", "seasonal", "Revenue repeats a weekly pattern (lag 7)."),
        _column(CHANNELS[0], "zero", "A blank is no spend."),  # the default: kept, not stored
        _column("Covid", "linear", "Interpolate."),  # not allowed for a 0/1 flag
        _column("Sessions", "zero", "Not mapped."),
    ],
    "notes": ["Revenue is blank on 3 rows."],
}


def _gappy_csv() -> bytes:
    from wide_data import wide_frame

    frame = wide_frame(420).drop(index=[40]).reset_index(drop=True)
    frame.loc[[100, 101, 102], "Revenue"] = None
    return frame.to_csv(index=False).encode("utf-8")


def test_geminis_resampling_is_checked_item_by_item_and_cached(client, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def fake(_system: str, prompt: str, _settings, _schema) -> str:
        calls.append(prompt)
        return json.dumps(RESAMPLE_ANSWER)

    monkeypatch.setattr(uploads_api, "generate_json", fake)
    c = client()
    data = _gappy_csv()
    files = {"file": ("gappy.csv", data, "text/csv")}
    first = c.post("/api/v1/uploads/inspect", files=files).json()
    assert first["prep_errors"] and "skips 1 day" in first["prep_errors"][0]
    rc = DataRoleConfig.model_validate({"columns": first["mapping"]})
    body = {"role_config": rc.model_dump_json()}
    r = c.post("/api/v1/uploads/resample-recommendation", files=files, data=body)
    assert r.status_code == 200, r.text
    out = r.json()
    assert "detected frequency: daily" in calls[0] and "1 missing days" in calls[0] and "'Revenue'" in calls[0]
    assert out["resample"]["fill_missing_dates"] is True and out["resample"]["frequency"] == "daily"
    assert [(e["column"], e["fill"]) for e in out["resample"]["columns"]] == [("Revenue", "seasonal")]
    assert {x["item"] for x in out["rejected"]} == {"'Covid' fill “Linear interpolation”", "'Sessions'"}
    assert out["errors"] == [] and set(out["unreviewed"]) == {CHANNELS[1], CHANNELS[2]}
    assert out["column_notes"]["Revenue"]["reason"].startswith("Revenue repeats")
    # Applying it makes the upload usable
    applied = DataRoleConfig.model_validate({"columns": {**first["mapping"], "resample": out["resample"]}})
    checked = c.post("/api/v1/uploads/inspect", files=files, data={"role_config": applied.model_dump_json()}).json()
    assert checked["errors"] == [] and checked["resample"]["grid"]["added"] == 1
    # Cached by content; the mapping is required, and a broken mapping is refused before Gemini is asked
    assert c.post("/api/v1/uploads/resample-recommendation", files=files, data=body).json()["cached"] and len(calls) == 1
    assert c.post("/api/v1/uploads/resample-recommendation", files=files).status_code == 422
    broken = rc.model_copy(update={"columns": rc.columns.model_copy(update={"target": "Umsatz"})})
    r = c.post("/api/v1/uploads/resample-recommendation", files=files, data={"role_config": broken.model_dump_json()})
    assert r.status_code == 422 and "Fix the mapping first" in r.json()["detail"]
    assert client(gemini=False).post("/api/v1/uploads/resample-recommendation", files=files, data=body).status_code == 503
