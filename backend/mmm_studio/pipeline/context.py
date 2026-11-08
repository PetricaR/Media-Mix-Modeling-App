"""Execution context for MMM Studio pipeline runs."""

from __future__ import annotations

import io
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import pandas as pd

from mmm_studio.core.schema import DataProfile, DataRoleConfig
from mmm_studio.report.sections import Section
from mmm_studio.storage import ArtifactStorage

# Daily modelling panel (the full calendar). Runs created before the rename stored it as
# `wide_2023_2024`, so readers fall back to that key.
MODEL_PANEL_NAME = "wide_model"
LEGACY_MODEL_PANEL_NAME = "wide_2023_2024"


def model_panel_key(storage: ArtifactStorage, run_id: str) -> str | None:
    """Return the storage key of a run's modelling panel, or None when ingestion has not produced it yet."""
    for name in (MODEL_PANEL_NAME, LEGACY_MODEL_PANEL_NAME):
        key = f"runs/{run_id}/data/{name}.parquet"
        if storage.exists(key):
            return key
    return None


class StageSkipped(Exception):
    """Raised by a stage that does not apply to this run (e.g. the model-selection grid on a quick profile)."""


@dataclass
class RunContext:
    """Holds run state, storage handle, schema/config, and progress callbacks across stages."""

    run_id: str
    storage: ArtifactStorage
    profile: DataProfile | None = None
    config: DataRoleConfig | None = None
    run_profile: Literal["quick", "full", "smoke", "custom"] = "quick"
    seed: int = 0
    on_stage_start: Callable[[str], None] | None = None
    on_stage_complete: Callable[[str], None] | None = None
    on_stage_error: Callable[[str, str], None] | None = None
    on_stage_skipped: Callable[[str, str], None] | None = None
    on_heartbeat: Callable[[], None] | None = None
    is_cancelled: Callable[[], bool] | None = None
    spawn_meridian_child: bool = False
    # Wall-clock limit of one Meridian fit (spawned child only); a stuck fit fails the stage instead of the worker
    fit_timeout_seconds: int = 3600
    cache: dict[str, Any] = field(default_factory=dict)
    _tmp_root: Path | None = None

    @property
    def prefix(self) -> str:
        return f"runs/{self.run_id}"

    def key(self, rel_path: str) -> str:
        clean = rel_path.lstrip("/")
        return f"{self.prefix}/{clean}" if self.run_id else clean

    def check_cancelled(self) -> None:
        if self.is_cancelled is not None and self.is_cancelled():
            raise InterruptedError(f"Run {self.run_id} was cancelled.")

    def heartbeat(self) -> None:
        if self.on_heartbeat is not None:
            self.on_heartbeat()
        self.check_cancelled()

    def save_section(self, section: Section) -> str:
        """Persist a rendered Section JSON and update the section index."""
        sec_key = self.key(f"sections/{section.id}.json")
        payload = section.model_dump(mode="json")
        self.storage.write_json(sec_key, payload)

        idx_key = self.key("sections/_index.json")
        index_data: list[dict[str, Any]] = []
        if self.storage.exists(idx_key):
            try:
                index_data = self.storage.read_json(idx_key)
            except Exception:
                index_data = []
        entry = {
            "id": section.id,
            "title": section.title,
            "subtitle": section.subtitle,
            "n_blocks": len(section.blocks),
            "n_charts": sum(1 for b in section.blocks if getattr(b, "type", None) == "chart" or (isinstance(b, dict) and b.get("type") == "chart")),
            "n_tables": sum(1 for b in section.blocks if getattr(b, "type", None) == "table" or (isinstance(b, dict) and b.get("type") == "table")),
        }
        index_data = [e for e in index_data if e.get("id") != section.id]
        index_data.append(entry)
        self.storage.write_json(idx_key, index_data)
        return sec_key

    def load_section(self, section_id: str) -> dict[str, Any]:
        return self.storage.read_json(self.key(f"sections/{section_id}.json"))

    def save_dataframe(self, name: str, df: pd.DataFrame, also_csv: bool = True) -> str:
        """Save a DataFrame as Parquet (and optionally CSV) in storage."""
        buf = io.BytesIO()
        df.to_parquet(buf, index=True)
        pq_key = self.key(f"data/{name}.parquet")
        self.storage.write_bytes(pq_key, buf.getvalue(), content_type="application/octet-stream")
        if also_csv:
            csv_bytes = df.to_csv(index=True).encode("utf-8")
            self.storage.write_bytes(self.key(f"data/{name}.csv"), csv_bytes, content_type="text/csv; charset=utf-8")
        return pq_key

    def has_dataframe(self, name: str) -> bool:
        """Return True when the named DataFrame exists in storage."""
        if name == MODEL_PANEL_NAME:
            return model_panel_key(self.storage, self.run_id) is not None
        return self.storage.exists(self.key(f"data/{name}.parquet"))

    def load_dataframe(self, name: str) -> pd.DataFrame:
        if name == MODEL_PANEL_NAME:
            key = model_panel_key(self.storage, self.run_id) or self.key(f"data/{name}.parquet")
        else:
            key = self.key(f"data/{name}.parquet")
        return pd.read_parquet(io.BytesIO(self.storage.read_bytes(key)))

    def save_json(self, rel_path: str, payload: Any) -> str:
        return self.storage.write_json(self.key(rel_path), payload)

    def load_json(self, rel_path: str) -> Any:
        return self.storage.read_json(self.key(rel_path))

    def local_work_dir(self, subdir: str = "work") -> Path:
        """Return a local working directory for Meridian binary/HTML file output.

        With local storage it is the run's own folder; otherwise one temporary root per context, removed by `cleanup()`.
        """
        if hasattr(self.storage, "_resolve"):
            p = self.storage._resolve(self.key(subdir))
            p.mkdir(parents=True, exist_ok=True)
            return p
        import tempfile

        if self._tmp_root is None:
            self._tmp_root = Path(tempfile.mkdtemp(prefix=f"mmm_studio_{self.run_id}_"))
        d = self._tmp_root / subdir
        d.mkdir(parents=True, exist_ok=True)
        return d

    def cleanup(self) -> None:
        """Remove the temporary working root (models and reports are already in storage)."""
        import shutil

        if self._tmp_root is not None:
            shutil.rmtree(self._tmp_root, ignore_errors=True)
            self._tmp_root = None
