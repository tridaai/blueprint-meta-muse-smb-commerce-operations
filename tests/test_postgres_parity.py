"""Round 10: the Postgres profile, graded against SQLite.

This leg runs only when ``SMB_TEST_DATABASE_URL`` names a database:
without it every test here skips cleanly and the default suite is
exactly the pre-Round-10 suite. With it, the core walk (ingest ->
ledger -> diagnoses -> plan -> drafts -> guardian -> approvals ->
morning board, as defined once in ``smb_commerce.parity``) must
produce byte-identical artefacts on PostgreSQL 16 and on SQLite
over the same record — parity gap 0.

Safety rails, in order: the leg refuses a database whose name does
not look like a test database, and it works inside its own
throwaway schema of that database, dropped when the leg finishes.
It never touches the database's other schemas or tables.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path
from urllib.parse import urlparse

import pytest

from smb_commerce.config import Settings, load_settings
from smb_commerce.parity import run_core_walk, snapshot
from smb_commerce.service import CommerceService
from smb_commerce.store import SqlStore

PG_URL = os.environ.get("SMB_TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not PG_URL, reason="SMB_TEST_DATABASE_URL not set"
)


def _guarded_database_name(url: str) -> str:
    name = urlparse(url).path.lstrip("/")
    if "test" not in name.lower():
        raise RuntimeError(
            "SMB_TEST_DATABASE_URL must name a test database "
            f"(got {name!r}); the parity leg refuses anything else"
        )
    return name


def _settings(tmp_path: Path, **overrides) -> Settings:
    base = load_settings()
    values = dict(
        database_url="",
        sqlite_path=tmp_path / "parity.db",
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


@pytest.fixture(scope="module")
def pg_service(tmp_path_factory):
    _guarded_database_name(PG_URL)
    schema = f"smb_parity_{uuid.uuid4().hex[:12]}"
    tmp = tmp_path_factory.mktemp("pg-parity")
    service = CommerceService(
        _settings(tmp, database_url=PG_URL, postgres_schema=schema)
    )
    yield service
    # Close first: an open store connection sits idle-in-transaction
    # and its locks would block the drop indefinitely.
    service.store.close()
    # The leg's own throwaway schema only — dropped by its creator.
    import psycopg

    with psycopg.connect(PG_URL, autocommit=True) as conn:
        conn.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')


def test_database_name_guard_refuses_non_test_databases():
    with pytest.raises(RuntimeError):
        _guarded_database_name("postgresql://u:p@h:5432/production")
    assert _guarded_database_name("postgresql://u:p@h:5432/app_test") == "app_test"


def test_postgres_migrations_apply_cleanly_and_rerun_idempotently(pg_service):
    store = pg_service.store
    assert store.dialect == "postgres"
    assert store.applied_migrations() == ["0001", "0002", "0003"]
    artefact_count = len(store.list_artefacts())
    # A second store over the same schema is a second boot: the
    # migration runner must apply nothing and change nothing. (On a
    # SQL_ASCII database this crashed before Round 10: the applied
    # versions came back as bytes and never matched the filenames.)
    again = SqlStore.postgres(
        PG_URL, pg_service.settings.migrations_dir,
        schema=pg_service.settings.postgres_schema,
    )
    assert again.applied_migrations() == ["0001", "0002", "0003"]
    assert len(again.list_artefacts()) == artefact_count
    again.close()


def test_postgres_text_reads_back_as_text(pg_service):
    store = pg_service.store
    pg_service.generate_dataset()
    fact = store.list_facts()[0]
    assert isinstance(fact.fact_id, str)
    assert isinstance(fact.kind, str)
    assert isinstance(fact.payload, dict)
    assert all(isinstance(v, str) for v in store.applied_migrations())


def test_core_walk_parity_gap_zero(pg_service, tmp_path):
    board_pg = run_core_walk(pg_service)
    sqlite_service = CommerceService(_settings(tmp_path))
    board_sqlite = run_core_walk(sqlite_service)

    snap_pg = snapshot(pg_service, board_pg)
    snap_sqlite = snapshot(sqlite_service, board_sqlite)

    # The snapshot must be the whole record, not a stub of it.
    assert pg_service.store.count_facts() == 3954
    assert len(pg_service.store.list_artefacts()) >= 17
    assert snap_pg == snap_sqlite
