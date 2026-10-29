"""Shared fixtures: a throwaway store and a generated service.

Generation happens once per module that asks for it; the dataset is
deterministic, so every test grades against the same planted truth.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from smb_commerce.config import Settings, load_settings
from smb_commerce.service import CommerceService
from smb_commerce.store import SqlStore


def make_settings(tmp_path: Path, **overrides) -> Settings:
    base = load_settings()
    values = dict(
        database_url="",
        sqlite_path=tmp_path / "test.db",
        migrations_dir=base.migrations_dir,
        object_dir=tmp_path / "objects",
        s3_endpoint="",
        s3_bucket=base.s3_bucket,
        model_profile="offline",
        model_tier="standard",
        log_level="WARNING",
    )
    values.update(overrides)
    return Settings(**values)


@pytest.fixture()
def store(tmp_path) -> SqlStore:
    settings = make_settings(tmp_path)
    return SqlStore.sqlite(settings.sqlite_path, settings.migrations_dir)


@pytest.fixture(scope="module")
def service(tmp_path_factory) -> CommerceService:
    tmp = tmp_path_factory.mktemp("dataset")
    svc = CommerceService(make_settings(tmp))
    svc.generate_dataset()
    return svc
