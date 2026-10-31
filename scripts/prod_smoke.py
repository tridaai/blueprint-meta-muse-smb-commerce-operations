#!/usr/bin/env python3
"""Production smoke: the Postgres profile, end to end.

Stands the application up against the production-shaped store —
PostgreSQL 16 via ``DATABASE_URL`` — in a throwaway schema, and
checks the things a deployment must prove before it serves:

- the full migration set applies cleanly in the fresh schema, and
  a second boot against the same schema applies nothing (the
  runner is idempotent — it was not, on a SQL_ASCII database,
  before Round 10);
- ``/health`` and ``/readiness`` answer on the real app, served
  from the Postgres profile;
- the core walk (ingest -> ledger -> diagnoses -> plan -> drafts
  -> guardian -> approvals -> morning board, defined once in
  ``smb_commerce.parity``) runs on the Postgres profile;
- the same walk on a full SQLite store produces byte-identical
  artefacts — parity gap 0.

Usage: ``DATABASE_URL=postgresql://... uv run python
scripts/prod_smoke.py`` (``make smoke``). Exits non-zero on any
failure; the throwaway schema is dropped at the end either way.
"""

from __future__ import annotations

import os
import sys
import tempfile
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from smb_commerce.config import Settings, load_settings  # noqa: E402
from smb_commerce.parity import run_core_walk, snapshot  # noqa: E402
from smb_commerce.service import CommerceService  # noqa: E402


def _settings(tmp: Path, **overrides) -> Settings:
    base = load_settings()
    values = dict(
        database_url="",
        sqlite_path=tmp / "smoke.db",
        migrations_dir=base.migrations_dir,
        object_dir=tmp / "objects",
        s3_endpoint="",
        s3_bucket=base.s3_bucket,
        model_profile="offline",
        model_tier="standard",
        log_level="WARNING",
    )
    values.update(overrides)
    return Settings(**values)


def main() -> int:
    database_url = os.environ.get("DATABASE_URL", "")
    if not database_url:
        print("SMOKE FAIL — DATABASE_URL is not set; the production "
              "smoke runs against the Postgres profile only")
        return 1
    schema = f"smb_smoke_{uuid.uuid4().hex[:12]}"
    failures: list[str] = []
    stores_to_close: list = []

    def check(label: str, ok: bool, detail: str = "") -> None:
        print(f"  {'PASS' if ok else 'FAIL'}  {label}"
              + (f" — {detail}" if detail else ""))
        if not ok:
            failures.append(label)

    try:
        with tempfile.TemporaryDirectory(prefix="smb-smoke-") as tmp:
            tmp_path = Path(tmp)
            print(f"== Production smoke — Postgres profile, schema {schema} ==")
            service = CommerceService(
                _settings(tmp_path, database_url=database_url,
                          postgres_schema=schema)
            )
            stores_to_close.append(service.store)
            check("migrations apply on a fresh schema",
                  service.store.applied_migrations() == ["0001", "0002", "0003"],
                  f"applied {service.store.applied_migrations()}")

            second = CommerceService(
                _settings(tmp_path, database_url=database_url,
                          postgres_schema=schema)
            )
            stores_to_close.append(second.store)
            check("second boot is idempotent (no re-applied migrations)",
                  second.store.applied_migrations() == ["0001", "0002", "0003"]
                  and len(second.store.list_artefacts())
                  == len(service.store.list_artefacts()))

            # /health + /readiness on the real app, on this profile.
            os.environ["DATABASE_URL"] = database_url
            os.environ["SMB_POSTGRES_SCHEMA"] = schema
            import smb_commerce.api as api

            api._service = None
            from fastapi.testclient import TestClient

            with TestClient(api.app) as client:
                if api._service is not None:
                    stores_to_close.append(api._service.store)
                health = client.get("/health")
                check("/health answers 200", health.status_code == 200,
                      f"got {health.status_code}")
                readiness = client.get("/readiness")
                body = readiness.json()
                check("/readiness answers 200, database ok",
                      readiness.status_code == 200
                      and body.get("database") == "ok",
                      f"got {readiness.status_code} {body.get('database')}")
                check("/readiness reports the applied migrations",
                      body.get("migrations") == ["0001", "0002", "0003"],
                      f"got {body.get('migrations')}")

            board = run_core_walk(service)
            snap_pg = snapshot(service, board)
            check("core walk completes on Postgres",
                  service.store.count_facts() == 3954
                  and len(service.store.list_artefacts()) >= 17,
                  f"{service.store.count_facts()} facts, "
                  f"{len(service.store.list_artefacts())} artefacts")

            (tmp_path / "lite").mkdir(exist_ok=True)
            sqlite_service = CommerceService(_settings(tmp_path / "lite"))
            board_sqlite = run_core_walk(sqlite_service)
            snap_sqlite = snapshot(sqlite_service, board_sqlite)
            check("Postgres and SQLite artefacts are byte-identical "
                  "(parity gap 0)", snap_pg == snap_sqlite)
    finally:
        # Close before dropping: an open store connection sits
        # idle-in-transaction and its locks block the drop.
        for store in stores_to_close:
            try:
                store.close()
            except Exception:
                pass
        import psycopg

        with psycopg.connect(database_url, autocommit=True) as conn:
            conn.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')

    if failures:
        print(f"SMOKE FAIL — {len(failures)} check(s) failed")
        return 1
    print("SMOKE PASS — the Postgres profile serves end to end, "
          "parity gap 0 against SQLite")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
