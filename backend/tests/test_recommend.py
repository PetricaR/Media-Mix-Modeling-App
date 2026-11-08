"""Gemini settings recommendations: the review of its answer, the prompt, and the route (Gemini mocked, no DB)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from mmm_studio.api import explain as explain_api
from mmm_studio.api import recommend as recommend_api
from mmm_studio.api.deps import reset_rate_limiter
from mmm_studio.api.router import api_router
from mmm_studio.core.model_settings import settings_defaults
from mmm_studio.core.schema import default_role_config
from mmm_studio.db.models import Run
from mmm_studio.explain.recommend import (
    Advice,
    AdviceItem,
    RecommendationFailed,
    State,
    build_prompt,
    parse_advice,
    review,
    section_text,
)
from mmm_studio.settings import get_settings
from mmm_studio.storage import get_storage

LIMITS = settings_defaults()["limits"]


def _state(profile: str = "full") -> State:
    return State(profile=profile, include_meridian=True, rc=default_role_config().model_dump(mode="json"))


def _advice(*changes: tuple[str, str]) -> Advice:
    return Advice(
        summary="Keep the model; the hold-out MAE / mean is 8.5 %.",
        settings=[AdviceItem(setting=k, value=v, reason=f"because of {k}", confidence="medium") for k, v in changes],
        tab_notes=[],
    )


def _check(advice: Advice, state: State, days: int | None = 732) -> tuple[list[dict], list[dict]]:
    result = review(advice, state, days, LIMITS)
    return result.changes, result.rejected


# ── Gemini's answer ─────────────────────────────────────────────────────────────────────────────────────────────


def test_parse_advice_reads_json_with_or_without_fences() -> None:
    body = {
        "summary": "ok",
        "settings": [{"setting": "max_lag", "value": "28", "reason": "r", "confidence": "low"}],
        "tab_notes": [{"tab": "model", "note": "n"}],
    }
    assert parse_advice(json.dumps(body)).settings[0].setting == "max_lag"
    assert parse_advice("```json\n" + json.dumps(body) + "\n```").summary == "ok"
    with pytest.raises(RecommendationFailed):
        parse_advice("Here are my recommendations: lower the max lag.")


# ── Review ──────────────────────────────────────────────────────────────────────────────────────────────────────


def test_valid_changes_are_typed_ordered_and_labelled() -> None:
    accepted, rejected = _check(
        _advice(
            ("roi_prior", "attributed_roas"),
            ("max_lag", "28 days"),
            ("calendar.black_week", "false"),
            ("channels.display.role", "unpaid"),
            ("seed", "7"),
            ("profile", "quick"),
        ),
        _state(),
    )
    assert rejected == []
    # Run mode first, then channel roles, then everything else in the form's order
    assert [c["setting"] for c in accepted] == [
        "profile",
        "channels.display.role",
        "seed",
        "calendar.black_week",
        "max_lag",
        "roi_prior",
    ]
    by_key = {c["setting"]: c for c in accepted}
    assert by_key["max_lag"]["value"] == 28 and by_key["max_lag"]["current"] == "21 days"
    assert by_key["max_lag"]["recommended"] == "28 days" and by_key["max_lag"]["tab"] == "model"
    assert by_key["calendar.black_week"]["value"] is False and by_key["seed"]["value"] == 7
    assert by_key["channels.display.role"]["recommended"] == "Unpaid / organic"
    assert by_key["profile"]["current"] == "Full" and by_key["profile"]["recommended"] == "Quick"


def test_selection_shifts_need_the_grid_and_the_quick_profile_switches_it_off() -> None:
    # The quick profile turns the grid off, so its shifts would do nothing
    accepted, rejected = _check(_advice(("profile", "quick"), ("selection_shifts", "1,3")), _state())
    assert [c["setting"] for c in accepted] == ["profile"]
    assert rejected[0]["setting"] == "selection_shifts" and "selection grid" in rejected[0]["problem"]
    # Switching the grid on in the same answer makes them apply
    accepted, rejected = _check(_advice(("profile", "quick"), ("model_selection", "true"), ("selection_shifts", "1,3")), _state())
    assert rejected == [] and accepted[-1]["value"] == [1, 3]


def test_custom_draws_and_custom_roi_priors_apply_only_with_their_switch() -> None:
    accepted, rejected = _check(
        _advice(("sampling.n_keep", "1500"), ("custom_roi_priors.sea.median", "6.7"), ("channels.seo.role", "paid")),
        _state(),
    )
    assert accepted[0]["setting"] == "channels.seo.role"
    problems = {r["setting"]: r["problem"] for r in rejected}
    assert "custom sampling profile" in problems["sampling.n_keep"]
    assert "custom ROI prior" in problems["custom_roi_priors.sea.median"]

    accepted, rejected = _check(
        _advice(
            ("custom_roi_priors.sea.median", "6.7"),
            ("custom_roi_priors.sea.sigma", "0.4"),
            ("roi_prior", "custom"),
            ("sampling.n_keep", "1500"),
            ("profile", "custom"),
        ),
        _state(),
    )
    assert rejected == []
    assert [c["setting"] for c in accepted] == [
        "profile",
        "sampling.n_keep",
        "roi_prior",
        "custom_roi_priors.sea.median",
        "custom_roi_priors.sea.sigma",
    ]
    assert accepted[1]["recommended"] == "1,500" and accepted[1]["current"] == "1,000"
    assert accepted[3]["current"] == "1.221" and accepted[3]["value"] == 6.7


def test_a_media_channel_with_a_custom_prior_can_become_unpaid() -> None:
    state = _state()
    state.ms["roi_prior"] = "custom"
    state.ms["custom_roi_priors"] = {"display": {"median": 1.0, "sigma": 0.5}}
    accepted, rejected = _check(_advice(("channels.display.role", "unpaid")), state)
    assert rejected == [] and accepted[0]["setting"] == "channels.display.role"


def test_invalid_suggestions_are_rejected_with_the_reason_never_adapted() -> None:
    accepted, rejected = _check(
        _advice(
            ("media_effects_dist", "normal"),  # not offered
            ("max_lag", "99"),  # above the limit
            ("baseline", "spline"),  # not a choice
            ("trend", "maybe"),
            ("profile", "turbo"),
            ("n_knots", "12"),  # needs the knots baseline
            ("roas_prior_sigma", "0.4"),  # needs the attributed-ROAS prior
            ("channels.sea.in_home_shift_days", "3"),  # print events only
            ("channels.tiktok.role", "paid"),  # no such channel
            ("trend", "false"),  # second value for the same setting (the first was invalid)
        ),
        _state(),
    )
    assert accepted == []
    problems: dict[str, str] = {}
    for r in rejected:
        problems.setdefault(r["setting"], r["problem"])  # the first problem per setting
    assert "not a setting" in problems["media_effects_dist"] and "not a setting" in problems["channels.tiktok.role"]
    assert "up to 60" in problems["max_lag"]
    assert "one of constant, knots, auto" in problems["baseline"]
    assert "true or false" in problems["trend"]
    assert "one of full, quick, smoke, custom" in problems["profile"]
    assert "knots" in problems["n_knots"]
    assert "attributed-ROAS" in problems["roas_prior_sigma"]
    assert "print events" in problems["channels.sea.in_home_shift_days"]
    assert [r["problem"] for r in rejected if r["setting"] == "trend"][1] == "Recommended twice; the first value is kept."


def test_meridian_settings_do_nothing_when_meridian_is_switched_off() -> None:
    accepted, rejected = _check(_advice(("include_meridian", "false"), ("max_lag", "28"), ("trend", "false")), _state())
    assert [c["setting"] for c in accepted] == ["include_meridian", "trend"]
    assert "Meridian model is fitted" in rejected[0]["problem"]


def test_dependent_settings_apply_when_their_switch_comes_in_the_same_answer() -> None:
    accepted, rejected = _check(
        _advice(
            ("n_knots", "12"),
            ("baseline", "knots"),
            ("channels.display.in_home_shift_days", "3"),
            ("channels.display.role", "print_event"),
        ),
        _state(),
    )
    assert rejected == []
    assert [c["setting"] for c in accepted] == [
        "channels.display.role",
        "baseline",
        "n_knots",
        "channels.display.in_home_shift_days",
    ]


def test_run_rules_reject_a_change_that_would_stop_the_run_from_starting() -> None:
    # A 500-day panel with a 200-day hold-out leaves 300 training days < 365 (93 leaves 407)
    accepted, rejected = _check(_advice(("holdout_days", "200")), _state(), 500)
    assert accepted == [] and "training days" in rejected[0]["problem"]

    media = [c["slug"] for c in _state().rc["channels"] if c["role"] in ("paid", "print_event")]
    accepted, rejected = _check(_advice(*[(f"channels.{s}.role", "unpaid") for s in media]), _state())
    assert len(accepted) == len(media) - 1
    assert "at least one media channel" in rejected[0]["problem"]


def test_kept_values_and_coverage_of_the_active_settings() -> None:
    result = review(_advice(("max_lag", "21"), ("trend", "on"), ("channels.catalog.adstock_shape", "binomial")), _state(), 732, LIMITS)
    assert result.changes == [] and result.rejected == []
    assert [k["setting"] for k in result.kept] == ["trend", "max_lag", "channels.catalog.adstock_shape"]
    assert result.kept[1]["current"] == "21 days" and result.kept[1]["tab"] == "model"
    # Active on the defaults: Meridian switch, profile, 21 data/model/prior/sampling settings (12 + 4 + 2 + 3: data
    # frequency and weather controls in data, leak-free validation the 4th model setting), 12 roles, adstock + saturation for
    # 6 media channels, the catalog shift and demand-following for 5 paid channels
    cov = result.coverage
    assert cov["reviewed"] == 3 and cov["active"] == 2 + 21 + 12 + 12 + 1 + 5
    assert "Max lag" not in cov["missing"] and "Role · SEA" in cov["missing"] and "Baseline knots" not in cov["missing"]


# ── Prompt ──────────────────────────────────────────────────────────────────────────────────────────────────────

SECTION = {
    "id": "meridian_model",
    "title": "Meridian MMM",
    "subtitle": "Validated on the hold-out.",
    "blocks": [
        {"type": "kpis", "items": [{"label": "Hold-out MAE / mean", "value": "8.5%"}]},
        {"type": "callout", "tone": "warn", "title": "Mostly prior", "body": "PSM, Display"},
        {"type": "chart", "id": "c", "title": "Fit", "figure": {"data": [{"y": [123456789]}]}, "caption": "Bias €69.5k"},
        {
            "type": "table",
            "id": "t",
            "title": "Sensitivity",
            "columns": [{"key": "variant", "label": "Variant"}, {"key": "mae", "label": "Validation MAE / mean"}],
            "rows": [{"variant": f"v{i}", "mae": f"{i}.0%"} for i in range(40)],
        },
    ],
}


def test_section_text_keeps_figures_and_drops_chart_data() -> None:
    text = section_text(SECTION, 10_000)
    assert "## Meridian MMM — Validated on the hold-out." in text
    assert "KPIs: Hold-out MAE / mean: 8.5%" in text
    assert "Note — Mostly prior: PSM, Display" in text
    assert "Chart: Fit — Bias €69.5k" in text and "123456789" not in text
    assert "| Variant | Validation MAE / mean |" in text and "| v29 | 29.0% |" in text
    assert "| v30 |" not in text and "(10 more rows not listed)" in text


def test_prompt_lists_goal_data_settings_and_says_when_there_are_no_results() -> None:
    profile = {"date_min": "2023-01-01", "date_max": "2025-01-01", "modelling_days": 732, "modelling_end_date": "2025-01-01"}
    prompt = build_prompt("speed", _state(), LIMITS, profile, None)
    assert prompt.startswith("Goal for the next run: Fastest useful run")
    assert "with the current 93-day hold-out 639 training days remain" in prompt
    assert "Results: none yet" in prompt
    for key in ("- max_lag (model) [active]:", "- n_knots (model) [inactive: used only with", "- seed (data) [active]:", "channels.<slug>.role", "custom_roi_priors.<slug>.median", "| SEA | sea | paid |"):
        assert key in prompt
    assert "- sampling.n_keep (sampling) [inactive: used only with the custom sampling profile]" in prompt


# ── Route ───────────────────────────────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def rec_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    runs: dict[str, Run] = {}
    others: list[Run] = []

    class FakeSession:
        def get(self, _model: Any, run_id: str) -> Run | None:
            return runs.get(run_id)

        def close(self) -> None:
            pass

    def fake_db():
        yield FakeSession()

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
        monkeypatch.setattr(recommend_api, "get_db", fake_db)
        monkeypatch.setattr(recommend_api, "same_workbook_runs", lambda _db, _run: list(others))
        app = FastAPI()
        app.include_router(api_router)
        return TestClient(app)

    reset_rate_limiter()
    explain_api.reset_explain_limiter()
    yield make, runs, others
    get_settings.cache_clear()
    get_storage.cache_clear()
    explain_api.reset_explain_limiter()


def _run(run_id: str, status: str = "draft", name: str = "Draft") -> Run:
    return Run(
        id=run_id,
        name=name,
        sha256="same-file",
        profile="full",
        status=status,
        config={"role_config": default_role_config().model_dump(mode="json"), "seed": 0, "skip_meridian": False},
        schema_json={"date_min": "2023-01-01", "date_max": "2025-01-01", "modelling_days": 732},
        summary=None,
    )


ANSWER = json.dumps(
    {
        "summary": "The attribution prior lowered the validation MAE / mean to 9.8 %.",
        "tab_notes": [{"tab": "model", "note": "The 21-day carry-over stays."}],
        "settings": [
            {"setting": "roi_prior", "value": "attributed_roas", "reason": "9.8 % vs 11.1 % in the sensitivity grid.", "confidence": "high"},
            {"setting": "max_lag", "value": "200", "reason": "Longer carry-over.", "confidence": "low"},
            {"setting": "trend", "value": "true", "reason": "", "confidence": "high"},
        ],
    }
)


def test_recommendation_reads_an_earlier_runs_results_checks_changes_and_caches(rec_client, monkeypatch: pytest.MonkeyPatch) -> None:
    make, runs, others = rec_client
    prompts: list[str] = []

    def fake(system: str, prompt: str, settings, schema) -> str:
        prompts.append(prompt)
        return ANSWER

    monkeypatch.setattr(recommend_api, "generate_json", fake)
    client = make()
    storage = get_storage()
    runs["run_draft"] = _run("run_draft")
    done = _run("run_done", status="succeeded", name="First full run")
    others.append(done)
    storage.write_json("runs/run_done/sections/_index.json", [])
    storage.write_json("runs/run_done/sections/meridian_model.json", {**SECTION, "blocks": SECTION["blocks"][:2]})
    storage.write_json(
        "runs/run_done/sections/drivers_roi.json",
        {"id": "drivers_roi", "title": "Drivers", "blocks": [{"type": "kpis", "items": [{"label": "Sensitivity", "value": "9.8% vs 11.1%"}]}]},
    )

    resp = client.post("/api/v1/runs/run_draft/recommendation", json={"goal": "accuracy"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["cached"] is False and body["goal"] == "accuracy" and body["model"] == "gemini-4-argon"
    assert body["evidence"] == {"kind": "same_workbook", "run_id": "run_done", "run_name": "First full run", "sections": ["meridian_model", "drivers_roi"]}
    assert [c["setting"] for c in body["changes"]] == ["roi_prior"]
    assert body["changes"][0]["value"] == "attributed_roas" and body["changes"][0]["tab"] == "priors"
    assert body["rejected"][0]["setting"] == "max_lag" and "up to 60" in body["rejected"][0]["problem"]
    assert body["unverified_numbers"] == []  # 9.8 % and 11.1 % are in the results
    assert [k["setting"] for k in body["kept"]] == ["trend"] and body["tab_notes"] == {"model": "The 21-day carry-over stays."}
    assert body["coverage"]["reviewed"] == 3 and "Role · SEA" in body["coverage"]["missing"]
    assert 'Results of an earlier run "First full run" on the same workbook' in prompts[0]
    assert "Goal for the next run: Maximum accuracy" in prompts[0]

    again = client.post("/api/v1/runs/run_draft/recommendation", json={"goal": "accuracy"})
    assert again.json()["cached"] is True and len(prompts) == 1

    # The form's unsaved settings are what Gemini reviews: the prior is already set there, so nothing is left to change
    rc = default_role_config().model_dump(mode="json")
    rc["model_settings"]["roi_prior"] = "attributed_roas"
    edited = client.post("/api/v1/runs/run_draft/recommendation", json={"goal": "accuracy", "role_config": rc, "profile": "quick"})
    assert edited.status_code == 200 and edited.json()["changes"] == [] and len(prompts) == 2
    assert "- roi_prior (priors) [active]: Where the ROI prior is centred. | " in prompts[1] and "current Centred on each channel" in prompts[1]


def test_recommendation_without_results_uses_the_profile_only(rec_client, monkeypatch: pytest.MonkeyPatch) -> None:
    make, runs, _others = rec_client
    prompts: list[str] = []
    monkeypatch.setattr(recommend_api, "generate_json", lambda _s, prompt, _st, _sc: prompts.append(prompt) or ANSWER)
    client = make()
    runs["run_draft"] = _run("run_draft")
    body = client.post("/api/v1/runs/run_draft/recommendation").json()
    assert body["evidence"]["kind"] == "profile_only" and body["goal"] == "balanced"
    assert "Results: none yet" in prompts[0]
    assert body["unverified_numbers"] == ["9.8 %", "11.1 %"]  # not in a profile-only prompt


def test_recommendation_refuses_unknown_active_and_unconfigured(rec_client, monkeypatch: pytest.MonkeyPatch) -> None:
    make, runs, _others = rec_client
    monkeypatch.setattr(recommend_api, "generate_json", lambda *_a: ANSWER)
    client = make()
    runs["run_busy"] = _run("run_busy", status="running")
    assert client.post("/api/v1/runs/run_missing/recommendation").status_code == 404
    assert client.post("/api/v1/runs/run_busy/recommendation").status_code == 409
    assert make(MMM_GEMINI_API_KEY="").post("/api/v1/runs/run_busy/recommendation").status_code == 503


def test_unreadable_gemini_answer_is_a_502(rec_client, monkeypatch: pytest.MonkeyPatch) -> None:
    make, runs, _others = rec_client
    monkeypatch.setattr(recommend_api, "generate_json", lambda *_a: "Lower the max lag.")
    client = make()
    runs["run_draft"] = _run("run_draft")
    resp = client.post("/api/v1/runs/run_draft/recommendation")
    assert resp.status_code == 502 and "expected format" in resp.json()["detail"]
