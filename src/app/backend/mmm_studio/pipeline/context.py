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


@dataclass
class RunContext:
    """Holds run state, storage handle, schema/config, and progress callbacks across stages."""

    run_id: str
    storage: ArtifactStorage
    profile: DataProfile | None = None
    config: DataRoleConfig | None = None
    run_profile: Literal["quick", "full", "smoke"] = "quick"
    seed: int = 0
    on_stage_start: Callable[[str], None] | None = None
    on_stage_complete: Callable[[str], None] | None = None
    on_stage_error: Callable[[str, str], None] | None = None
    on_heartbeat: Callable[[], None] | None = None
    is_cancelled: Callable[[], bool] | None = None
    spawn_meridian_child: bool = False
    cache: dict[str, Any] = field(default_factory=dict)

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

    def load_dataframe(self, name: str) -> pd.DataFrame:
        raw = self.storage.read_bytes(self.key(f"data/{name}.parquet"))
        return pd.read_parquet(io.BytesIO(raw))

    def save_json(self, rel_path: str, payload: Any) -> str:
        return self.storage.write_json(self.key(rel_path), payload)

    def load_json(self, rel_path: str) -> Any:
        return self.storage.read_json(self.key(rel_path))

    def local_work_dir(self, subdir: str = "work") -> Path:
        """Return a local temporary working directory for Meridian binary/HTML file output."""
        if hasattr(self.storage, "_resolve"):
            p = self.storage._resolve(self.key(subdir))
            p.mkdir(parents=True, exist_ok=True)
            return p
        import tempfile

        d = Path(tempfile.mkdtemp(prefix=f"mmm_studio_{self.run_id}_"))
        return d
