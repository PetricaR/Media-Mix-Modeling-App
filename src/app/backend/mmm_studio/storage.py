"""Artifact storage abstraction supporting LocalStorage and GCSStorage."""

from __future__ import annotations

import json
import shutil
from abc import ABC, abstractmethod
from functools import lru_cache
from pathlib import Path
from typing import Any

from mmm_studio.settings import Settings, get_settings


class ArtifactStorage(ABC):
    """Abstract base class for reading and writing run artifacts."""

    @abstractmethod
    def write_bytes(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> str:
        """Write raw bytes to storage and return the storage key."""

    @abstractmethod
    def read_bytes(self, key: str) -> bytes:
        """Read raw bytes from storage."""

    @abstractmethod
    def exists(self, key: str) -> bool:
        """Return True if the artifact key exists."""

    @abstractmethod
    def list_keys(self, prefix: str = "") -> list[str]:
        """List all keys under the given prefix."""

    def delete_prefix(self, prefix: str) -> None:
        """Remove all keys under the given prefix."""

    def write_text(self, key: str, text: str, content_type: str = "text/plain; charset=utf-8") -> str:
        return self.write_bytes(key, text.encode("utf-8"), content_type=content_type)

    def read_text(self, key: str) -> str:
        return self.read_bytes(key).decode("utf-8")

    def write_json(self, key: str, payload: Any) -> str:
        raw = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        return self.write_bytes(key, raw, content_type="application/json")

    def read_json(self, key: str) -> Any:
        return json.loads(self.read_text(key))


# Type alias used by API dependency annotations
StorageBackend = ArtifactStorage


class LocalStorage(ArtifactStorage):
    """Local filesystem storage under a base directory."""

    def __init__(self, base_dir: str | Path) -> None:
        self.base_dir = Path(base_dir).resolve()
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def _resolve(self, key: str) -> Path:
        clean_key = key.lstrip("/")
        target = (self.base_dir / clean_key).resolve()
        if target != self.base_dir and not target.is_relative_to(self.base_dir):
            raise ValueError(f"Invalid storage key traversal: {key}")
        return target

    def write_bytes(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> str:
        import os
        import uuid

        path = self._resolve(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
        tmp_path.write_bytes(data)
        tmp_path.replace(path)
        return key.lstrip("/")

    def read_bytes(self, key: str) -> bytes:
        path = self._resolve(key)
        if not path.exists():
            raise FileNotFoundError(f"Artifact not found: {key}")
        return path.read_bytes()

    def exists(self, key: str) -> bool:
        return self._resolve(key).exists()

    def list_keys(self, prefix: str = "") -> list[str]:
        clean_prefix = prefix.lstrip("/")
        root = self._resolve(clean_prefix) if clean_prefix else self.base_dir
        if not root.exists():
            return []
        if root.is_file():
            return [str(root.relative_to(self.base_dir))]
        return sorted(
            str(p.relative_to(self.base_dir))
            for p in root.rglob("*")
            if p.is_file() and not p.name.endswith(".tmp")
        )

    def delete_prefix(self, prefix: str) -> None:
        clean_prefix = prefix.lstrip("/")
        if not clean_prefix:
            return
        target = self._resolve(clean_prefix)
        if target.exists():
            if target.is_dir():
                shutil.rmtree(target, ignore_errors=True)
            else:
                target.unlink(missing_ok=True)


class GCSStorage(ArtifactStorage):
    """Google Cloud Storage implementation for production Cloud Run."""

    def __init__(self, bucket_name: str, project_id: str | None = None) -> None:
        from google.cloud import storage

        if not bucket_name:
            raise ValueError("GCS_BUCKET must be configured when STORAGE_BACKEND='gcs'")
        self.client = storage.Client(project=project_id or None)
        self.bucket = self.client.bucket(bucket_name)

    def write_bytes(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> str:
        clean_key = key.lstrip("/")
        blob = self.bucket.blob(clean_key)
        blob.upload_from_string(data, content_type=content_type)
        return clean_key

    def read_bytes(self, key: str) -> bytes:
        clean_key = key.lstrip("/")
        blob = self.bucket.blob(clean_key)
        if not blob.exists():
            raise FileNotFoundError(f"GCS artifact not found: {clean_key}")
        return blob.download_as_bytes()

    def exists(self, key: str) -> bool:
        clean_key = key.lstrip("/")
        return self.bucket.blob(clean_key).exists()

    def list_keys(self, prefix: str = "") -> list[str]:
        clean_prefix = prefix.lstrip("/")
        blobs = self.client.list_blobs(self.bucket, prefix=clean_prefix)
        return sorted(b.name for b in blobs)

    def delete_prefix(self, prefix: str) -> None:
        clean_prefix = prefix.lstrip("/")
        if not clean_prefix:
            return
        for blob in self.client.list_blobs(self.bucket, prefix=clean_prefix):
            blob.delete()


@lru_cache(maxsize=1)
def get_storage(settings: Settings | None = None) -> ArtifactStorage:
    cfg = settings or get_settings()
    if cfg.storage_backend == "gcs":
        return GCSStorage(bucket_name=cfg.gcs_bucket, project_id=cfg.gcp_project_id)
    return LocalStorage(cfg.storage_local_dir)
