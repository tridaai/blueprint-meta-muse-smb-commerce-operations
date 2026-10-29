"""The object-store port: S3-compatible, local by default.

Artefact payloads that outgrow the database (exports, report packs in
later rounds) go through this port. The default implementation is a
filesystem store under ``SMB_OBJECT_DIR``. Setting ``SMB_S3_ENDPOINT``
selects the S3-compatible adapter, which speaks the S3 HTTP shape to
any compatible endpoint (the compose stack runs SeaweedFS under the
service name ``minio``). The S3 adapter fails closed when its bucket
configuration is incomplete — it never falls back to local disk
silently.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Protocol


class ObjectStoreNotConfigured(RuntimeError):
    """The S3 adapter was selected without complete configuration."""


class ObjectStore(Protocol):
    def put(self, key: str, data: bytes) -> str:
        """Store ``data`` under ``key``; return its content hash."""
        ...

    def get(self, key: str) -> bytes:
        ...


class LocalObjectStore:
    def __init__(self, root: Path):
        self._root = root
        self._root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        safe = key.replace("/", "__")
        return self._root / safe

    def put(self, key: str, data: bytes) -> str:
        self._path(key).write_bytes(data)
        return hashlib.sha256(data).hexdigest()

    def get(self, key: str) -> bytes:
        return self._path(key).read_bytes()


class S3ObjectStore:
    """Minimal S3-compatible adapter over plain HTTPS (PUT/GET object).

    Unsigned requests are accepted by the local SeaweedFS default in
    the compose stack; a deployment against a signed endpoint supplies
    its gateway in front of this adapter. Construction validates the
    configuration so a half-configured deployment fails at wiring
    time, not mid-run.
    """

    def __init__(self, endpoint: str, bucket: str):
        if not endpoint or not bucket:
            raise ObjectStoreNotConfigured(
                "S3 object store selected but SMB_S3_ENDPOINT or "
                "SMB_S3_BUCKET is not set"
            )
        self._endpoint = endpoint.rstrip("/")
        self._bucket = bucket

    def _url(self, key: str) -> str:
        return f"{self._endpoint}/{self._bucket}/{key}"

    def put(self, key: str, data: bytes) -> str:
        import httpx

        response = httpx.put(self._url(key), content=data, timeout=30.0)
        response.raise_for_status()
        return hashlib.sha256(data).hexdigest()

    def get(self, key: str) -> bytes:
        import httpx

        response = httpx.get(self._url(key), timeout=30.0)
        response.raise_for_status()
        return response.content


def build_object_store(settings) -> ObjectStore:
    if settings.s3_endpoint:
        return S3ObjectStore(settings.s3_endpoint, settings.s3_bucket)
    return LocalObjectStore(settings.object_dir)
