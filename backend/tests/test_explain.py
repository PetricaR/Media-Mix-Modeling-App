"""Gemini explanations: what the model is told, the number check, and the `/explain` route (Gemini mocked, no DB)."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from mmm_studio.api import explain as explain_api
from mmm_studio.api.deps import reset_rate_limiter
from mmm_studio.api.router import api_router
from mmm_studio.explain.context import (
    MAX_POINTS,
    SYSTEM_INSTRUCTION,
    ExplainFigure,
    ExplainRequest,
    ExplainTable,
    build_prompt,
    describe_figure,
    describe_table,
    unverified_numbers,
)
from mmm_studio.settings import get_settings
from mmm_studio.storage import get_storage

# ── Context ─────────────────────────────────────────────────────────────────────────────────────────────────────


def test_system_instruction_defines_template_sections_and_grounding() -> None:
    for section in ("**How to read it**", "**Explaining the numbers**", "**Key insights**", "**Conclusion**"):
        assert section in SYSTEM_INSTRUCTION


def test_bar_chart_lists_every_point_with_axes_and_intervals() -> None:
    fig = ExplainFigure(
        data=[
            {
                "type": "bar",
                "orientation": "h",
                "name": "ROI",
                "x": [8.53, 0.96],
                "y": ["SEA", "Catalog"],
                "error_x": {"type": "data", "array": [1.2, 0.3], "arrayminus": [2.0, 0.4]},
            },
            {"type": "scatter", "name": "Off", "x": [1], "y": [2], "visible": False},
            {"type": "scatter", "name": "Prior", "x": [1, 2], "y": [3, 4], "visible": "legendonly"},
        ],
        layout={"xaxis": {"title": {"text": "<b>ROI</b>"}, "ticksuffix": "×"}, "shapes": [{"type": "line", "x0": 1, "x1": 1, "y0": 0, "y1": 1}]},
    )
    text = describe_figure(fig)
    assert 'Axis xaxis: "ROI", ticksuffix ×' in text
    assert "SEA: 8.53 (−2 / +1.2) [6.53, 9.73]" in text and "Catalog: 0.96 (−0.4 / +0.3) [0.56, 1.26]" in text
    assert "2 points: min 0.96 at Catalog, max 8.53 at SEA" in text
    assert "vertical line at x = 1" in text
    assert '"Off"' not in text  # switched off entirely
    assert '"Prior" (scatter, hidden by the viewer)' in text


def test_long_series_is_summarised_and_sampled_with_extremes() -> None:
    days = [f"2024-01-{(i % 28) + 1:02d}T00:00:00" for i in range(732)]
    values = [100_000.0 + i for i in range(732)]
    values[400] = 2_000_000.0  # the peak must survive sampling
    text = describe_figure(ExplainFigure(data=[{"type": "scatter", "name": "Sales", "x": days, "y": values}]))
    assert "732 points" in text and "max 2,000,000" in text and "mean" in text
    sampled = text.split("sampled points", 1)[1]
    assert "2,000,000" in sampled and "T00:00:00" not in text
    assert sampled.count(";") < MAX_POINTS + 5


def test_table_keeps_displayed_cells_and_truncates_long_tables() -> None:
    table = ExplainTable(columns=["Channel", "ROI"], rows=[[f"C{i}", f"{i}.5×"] for i in range(300)])
    text = describe_table(table)
    assert text.startswith("300 rows × 2 columns")
    assert "| C0 | 0.5× |" in text and "| C299 | 299.5× |" in text
    assert "rows not listed" in text


def test_prompt_holds_page_text_title_caption_and_data() -> None:
    req = ExplainRequest(
        kind="table",
        title="ROI by channel",
        caption="90 % credible intervals",
        page="Drivers",
        section_title="ROI & contribution",
        notes="Hold-out MAE/mean 8.5 %",
        table=ExplainTable(columns=["Channel", "ROI"], rows=[["SEA", 8.53]]),
    )
    prompt = build_prompt(req)
    for part in ("Page: Drivers · ROI & contribution", 'Table: "ROI by channel"', "Caption: 90 % credible", "Hold-out MAE/mean 8.5 %", "| SEA | 8.53 |"):
        assert part in prompt


def test_chart_without_figure_is_rejected() -> None:
    with pytest.raises(ValueError):
        ExplainRequest(kind="chart", title="x")


# ── Number check ────────────────────────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("answer", "context"),
    [
        ("Sales reach €8.0M.", "Total | 8,008,549"),
        ("The hold-out error is 8.5 %.", "MAE/mean 0.0854"),  # share in the data, percent in the answer
        ("ROI of 8.53× for SEA", "SEA: 8.53"),
        ("about €121.8M on media", "Budget €121.8M"),
        ("down −6.3 % from plan", "back-test −6.3 %"),
        ("Q4 2024 shows 3 peaks", "2024-10-01: 5"),  # Q4 and small counts are not checked
    ],
)
def test_numbers_found_in_the_data_pass(answer: str, context: str) -> None:
    assert unverified_numbers(answer, context) == []


def test_invented_numbers_are_reported() -> None:
    answer = "SEA returns 12.4× and lifts sales by €3.2M, while Display adds 45 %."
    assert unverified_numbers(answer, "SEA: 8.53; Display: 0.4; total 8,008,549") == ["12.4×", "€3.2M", "45 %"]


# ── Route ───────────────────────────────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def explain_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    def make(**env: str) -> TestClient:
        base = {
            "MMM_STORAGE_BACKEND": "local",
            "MMM_LOCAL_DATA_DIR": str(tmp_path),
            "MMM_APP_ACCESS_CODE": "",
            "MMM_GEMINI_API_KEY": "test-key",
            "MMM_GEMINI_VERTEX": "false",
            "MMM_EXPLAIN_PER_MINUTE": "20",
        }
        for name, value in {**base, **env}.items():
            monkeypatch.setenv(name, value)
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        get_settings.cache_clear()
        get_storage.cache_clear()
        app = FastAPI()
        app.include_router(api_router)
        return TestClient(app)  # no lifespan: the route needs no database

    reset_rate_limiter()
    explain_api.reset_explain_limiter()
    yield make
    get_settings.cache_clear()
    get_storage.cache_clear()
    explain_api.reset_explain_limiter()


TABLE_REQUEST = {
    "kind": "table",
    "title": "ROI by channel",
    "table": {"columns": ["Channel", "ROI"], "rows": [["SEA", "8.53×"], ["Catalog", "0.96×"]]},
}


def test_explain_calls_gemini_once_then_serves_the_cache(explain_client, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def fake(system: str, prompt: str, settings) -> str:
        calls.append(prompt)
        return "**SEA earns 8.53× per euro.**\n- Catalog returns 9.9×."

    monkeypatch.setattr(explain_api, "generate_explanation", fake)
    client = explain_client()
    first = client.post("/api/v1/explain", json=TABLE_REQUEST)
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["cached"] is False and body["model"] == "gemini-4-argon"
    assert body["unverified_numbers"] == ["9.9×"]
    assert '| SEA | 8.53× |' in calls[0]

    second = client.post("/api/v1/explain", json=TABLE_REQUEST)
    assert second.json()["cached"] is True and second.json()["explanation"] == body["explanation"]
    assert len(calls) == 1


def test_explain_is_off_without_gemini_and_health_says_so(explain_client) -> None:
    client = explain_client(MMM_GEMINI_API_KEY="")
    assert client.get("/api/v1/health").json()["explain_available"] is False
    assert client.post("/api/v1/explain", json=TABLE_REQUEST).status_code == 503


def test_explain_needs_the_access_code(explain_client) -> None:
    client = explain_client(MMM_APP_ACCESS_CODE="a-long-enough-access-code")
    assert client.post("/api/v1/explain", json=TABLE_REQUEST).status_code == 401


def test_gemini_failures_are_502_with_the_reason(explain_client, monkeypatch: pytest.MonkeyPatch) -> None:
    from mmm_studio.explain.gemini import ExplainFailed

    def fail(*_args) -> str:
        raise ExplainFailed("The Gemini quota is used up for now. Try again in a minute.")

    monkeypatch.setattr(explain_api, "generate_explanation", fail)
    resp = explain_client().post("/api/v1/explain", json=TABLE_REQUEST)
    assert resp.status_code == 502 and "quota" in resp.json()["detail"]


def test_new_explanations_are_rate_limited_per_client(explain_client, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(explain_api, "generate_explanation", lambda *_a: "ok")
    client = explain_client(MMM_EXPLAIN_PER_MINUTE="2")
    codes = [
        client.post("/api/v1/explain", json={**TABLE_REQUEST, "title": f"Table {i}"}).status_code for i in range(3)
    ]
    assert codes == [200, 200, 429]
    # A cached answer does not count against the limit
    assert client.post("/api/v1/explain", json={**TABLE_REQUEST, "title": "Table 0"}).status_code == 200


# ── Run facts ───────────────────────────────────────────────────────────────────────────────────────────────────


def _sample_run(storage) -> object:
    from mmm_studio.core.schema import default_role_config
    from mmm_studio.db.models import Run

    rc = default_role_config()
    run = Run(
        id="run_facts_test",
        name="Facts",
        config={"role_config": rc.model_dump(mode="json")},
        schema_json={"date_min": "2023-01-01", "modelling_end_date": "2025-01-01", "modelling_days": 732},
        summary={
            "holdout_q4_2024": {"MAE_over_mean": 0.0854, "R2": 0.8877},
            "holdout_label": "Q4 2024",
            "holdout_q3_2024": {"MAE_over_mean": 0.1105},
            "forward_label": "Q3 2024",
            "max_rhat": 1.0027,
        },
    )
    storage.write_json("runs/run_facts_test/data/default_optimisation.json", {"upper_bound_channels": ["psm", "sea"]})
    return run


def test_run_facts_come_from_the_runs_settings_and_results(tmp_path: Path) -> None:
    from mmm_studio.explain.run_facts import run_facts
    from mmm_studio.storage import LocalStorage

    storage = LocalStorage(tmp_path)
    facts = run_facts(_sample_run(storage), storage)
    assert "Daily data from 2023-01-01 to 2025-01-01 (732 days)" in facts
    assert "Demand-following channels: SEA, PSM." in facts and "upper bound" in facts
    assert "Catalog, modelled from the in-home date (2 days after" in facts
    assert "Commission channel(s) used as controls, without an ROI: Affiliate." in facts
    assert "the last 93 days (Q4 2024)" in facts and "MAE / mean is 8.5 % and R² 0.888" in facts
    assert "Forward hold-out (Q3 2024)" in facts and "11.1 %" in facts and "R-hat 1.003" in facts


def test_explain_adds_the_run_facts_and_rejects_unknown_runs(explain_client, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    prompts: list[str] = []
    monkeypatch.setattr(explain_api, "generate_explanation", lambda _s, prompt, _st: prompts.append(prompt) or "**ok** 8.5 %")
    client = explain_client()
    run = _sample_run(get_storage())

    class FakeSession:
        def get(self, _model, run_id):
            return run if run_id == run.id else None

        def close(self) -> None:
            pass

    def fake_db():
        yield FakeSession()

    monkeypatch.setattr(explain_api, "get_db", fake_db)
    resp = client.post("/api/v1/explain", json={**TABLE_REQUEST, "run_id": run.id})
    assert resp.status_code == 200, resp.text
    assert prompts[0].startswith("About this run") and "Demand-following channels: SEA, PSM." in prompts[0]
    assert resp.json()["unverified_numbers"] == []  # 8.5 % is a run fact
    assert client.post("/api/v1/explain", json={**TABLE_REQUEST, "run_id": "run_missing"}).status_code == 404
