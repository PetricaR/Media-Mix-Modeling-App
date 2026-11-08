"""End-to-end fast pipeline and CLI test (no database, no Meridian MCMC fit required)."""

from __future__ import annotations

import json
from pathlib import Path

from mmm_studio.cli import main as cli_main
from mmm_studio.report.sections import Section


def test_cli_fast_pipeline_generates_all_analysis_sections(
    fixture_xlsx_path: Path,
    golden_data: dict,
    tmp_path: Path,
) -> None:
    rc = cli_main(
        [
            "run",
            "--input",
            str(fixture_xlsx_path),
            "--out",
            str(tmp_path),
            "--run-id",
            "test_fast",
            "--skip-meridian",
        ]
    )
    assert rc == 0

    run_dir = tmp_path / "runs" / "test_fast"
    summary_path = run_dir / "meta" / "summary.json"
    assert summary_path.exists()
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    assert summary["skip_meridian"] is True

    expected_fast_sections = {
        "overview",
        "data_quality",
        "explore_target",
        "explore_channels",
        "explore_spend",
        "explore_efficiency",
        "explore_correlations",
        "explore_catalog",
        "explore_readiness",
        "preparation",
        "benchmark",
        "reports",
        "brief",
    }
    assert expected_fast_sections.issubset(set(summary["sections"]))

    total_charts = 0
    for sec_id in expected_fast_sections:
        sec_file = run_dir / "sections" / f"{sec_id}.json"
        assert sec_file.exists(), f"Missing section file: {sec_id}"
        sec_obj = Section.model_validate_json(sec_file.read_text(encoding="utf-8"))
        assert len(sec_obj.blocks) >= 2
        total_charts += sum(1 for b in sec_obj.blocks if b.type == "chart")

    # Fast EDA + Preparation + Benchmark + Overview should have >= 45 charts
    assert total_charts >= 45

    # Check data artifacts
    assert (run_dir / "data" / "wide_model.csv").exists()
    assert (run_dir / "data" / "meridian_input.csv").exists()

    # Check profile matches golden
    profile = json.loads((run_dir / "meta" / "profile.json").read_text(encoding="utf-8"))
    assert profile["raw_row_count"] == golden_data["raw_row_count"]
    assert profile["calendar_days"] == golden_data["calendar_days"]
    assert profile["modelling_days"] == golden_data["calendar_days"]  # the isolated 2025-01-01 stays in


def test_run_context_has_dataframe(tmp_path: Path) -> None:
    import pandas as pd

    from mmm_studio.pipeline.context import MODEL_PANEL_NAME, RunContext
    from mmm_studio.storage import LocalStorage

    storage = LocalStorage(tmp_path)
    ctx = RunContext(run_id="r_test", storage=storage)
    assert not ctx.has_dataframe("meridian_roi")
    assert not ctx.has_dataframe(MODEL_PANEL_NAME)

    df = pd.DataFrame({"mroi": [2.5, 1.1]}, index=["SEA", "Display"])
    ctx.save_dataframe("meridian_roi", df, also_csv=False)
    assert ctx.has_dataframe("meridian_roi")
    assert ctx.load_dataframe("meridian_roi").loc["SEA", "mroi"] == 2.5

    ctx.save_dataframe(MODEL_PANEL_NAME, df, also_csv=False)
    assert ctx.has_dataframe(MODEL_PANEL_NAME)

