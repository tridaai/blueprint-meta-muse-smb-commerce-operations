"""The fact store behind the store port.

One SQL implementation serves both dialects: SQLite is the local
default, ``DATABASE_URL`` switches to PostgreSQL 16. Statements are
written with ``?`` placeholders and translated for psycopg. Payloads
are JSON text in both dialects (never JSONB), so a figure reads back
byte-identically on either profile.

The store is insert-only by construction: there is no update or delete
method for facts, artefacts or call records. Supersession happens by
inserting a new artefact that links to the one it replaces (Law 4).
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable

from .migrations import run_migrations
from .models import Artefact, Fact, ModelCallRecord


def canonical_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


class SqlStore:
    def __init__(self, conn, dialect: str, paramstyle: str):
        self._conn = conn
        self.dialect = dialect
        self._paramstyle = paramstyle
        self._batching = False

    @contextmanager
    def batch(self):
        """One transaction for a run of inserts (the ingest port's bulk
        loads). Reads and writes inside the block see the same
        connection, so duplicate/conflict checks behave exactly as
        they do one insert at a time; the commit lands once, at exit,
        and an exception rolls the whole batch back."""
        self._batching = True
        try:
            yield self
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise
        finally:
            self._batching = False

    def _commit(self) -> None:
        if not self._batching:
            self._conn.commit()

    # -- wiring ---------------------------------------------------------
    @classmethod
    def sqlite(cls, path: Path, migrations_dir: Path) -> "SqlStore":
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        store = cls(conn, "sqlite", "qmark")
        run_migrations(conn, migrations_dir, "sqlite", "qmark")
        return store

    @classmethod
    def postgres(cls, database_url: str, migrations_dir: Path) -> "SqlStore":
        import psycopg
        from psycopg.rows import dict_row

        conn = psycopg.connect(database_url, row_factory=dict_row)
        store = cls(conn, "postgres", "format")
        run_migrations(conn, migrations_dir, "postgres", "format")
        return store

    def _run(self, sql: str, params: Iterable[Any] = ()):
        if self._paramstyle == "format":
            sql = sql.replace("?", "%s")
        return self._conn.execute(sql, tuple(params))

    def _one(self, sql: str, params: Iterable[Any] = ()):
        row = self._run(sql, params).fetchone()
        if row is None:
            return None
        return dict(row)

    def _all(self, sql: str, params: Iterable[Any] = ()) -> list[dict]:
        return [dict(r) for r in self._run(sql, params).fetchall()]

    # -- facts ----------------------------------------------------------
    def get_fact_by_key(self, kind: str, natural_key: str) -> Fact | None:
        row = self._one(
            "SELECT * FROM facts WHERE kind = ? AND natural_key = ?",
            (kind, natural_key),
        )
        return self._fact(row) if row else None

    def get_fact(self, fact_id: str) -> Fact | None:
        row = self._one("SELECT * FROM facts WHERE fact_id = ?", (fact_id,))
        return self._fact(row) if row else None

    def insert_fact(self, fact: Fact) -> None:
        self._run(
            "INSERT INTO facts (fact_id, kind, natural_key, payload,"
            " source_system, as_of, content_hash)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                fact.fact_id,
                fact.kind,
                fact.natural_key,
                canonical_json(fact.payload),
                fact.source_system,
                fact.as_of,
                fact.content_hash,
            ),
        )
        self._commit()

    def list_facts(self, kind: str | None = None) -> list[Fact]:
        if kind:
            rows = self._all(
                "SELECT * FROM facts WHERE kind = ? ORDER BY seq", (kind,)
            )
        else:
            rows = self._all("SELECT * FROM facts ORDER BY seq")
        return [self._fact(r) for r in rows]

    def count_facts(self, kind: str | None = None) -> int:
        if kind:
            row = self._one("SELECT COUNT(*) AS n FROM facts WHERE kind = ?", (kind,))
        else:
            row = self._one("SELECT COUNT(*) AS n FROM facts")
        return int(row["n"])

    @staticmethod
    def _fact(row: dict) -> Fact:
        return Fact(
            fact_id=row["fact_id"],
            kind=row["kind"],
            natural_key=row["natural_key"],
            payload=json.loads(row["payload"]),
            source_system=row["source_system"],
            as_of=row["as_of"],
            content_hash=row["content_hash"],
        )

    # -- artefacts --------------------------------------------------------
    def insert_artefact(self, artefact: Artefact) -> None:
        self._run(
            "INSERT INTO artefacts (artefact_id, kind, payload, input_fingerprint)"
            " VALUES (?, ?, ?, ?)",
            (
                artefact.artefact_id,
                artefact.kind,
                canonical_json(artefact.payload),
                artefact.input_fingerprint,
            ),
        )
        self._commit()

    def get_artefact(self, artefact_id: str) -> Artefact | None:
        row = self._one(
            "SELECT * FROM artefacts WHERE artefact_id = ?", (artefact_id,)
        )
        return self._artefact(row) if row else None

    def list_artefacts(self, kind: str | None = None) -> list[Artefact]:
        if kind:
            rows = self._all(
                "SELECT * FROM artefacts WHERE kind = ? ORDER BY seq", (kind,)
            )
        else:
            rows = self._all("SELECT * FROM artefacts ORDER BY seq")
        return [self._artefact(r) for r in rows]

    def latest_artefact(self, kind: str) -> Artefact | None:
        rows = self.list_artefacts(kind)
        return rows[-1] if rows else None

    @staticmethod
    def _artefact(row: dict) -> Artefact:
        return Artefact(
            artefact_id=row["artefact_id"],
            kind=row["kind"],
            payload=json.loads(row["payload"]),
            input_fingerprint=row["input_fingerprint"],
        )

    # -- model calls (the cost ledger) ------------------------------------
    def insert_model_call(self, record: ModelCallRecord) -> None:
        self._run(
            "INSERT INTO model_calls (call_id, run_id, profile, model, tier,"
            " input_tokens, output_tokens, cost_usd)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                record.call_id,
                record.run_id,
                record.profile,
                record.model,
                record.tier,
                record.input_tokens,
                record.output_tokens,
                record.cost_usd,
            ),
        )
        self._commit()

    def list_model_calls(self, run_id: str | None = None) -> list[ModelCallRecord]:
        if run_id:
            rows = self._all(
                "SELECT * FROM model_calls WHERE run_id = ? ORDER BY seq", (run_id,)
            )
        else:
            rows = self._all("SELECT * FROM model_calls ORDER BY seq")
        return [
            ModelCallRecord(
                call_id=r["call_id"],
                run_id=r["run_id"],
                profile=r["profile"],
                model=r["model"],
                tier=r["tier"],
                input_tokens=int(r["input_tokens"]),
                output_tokens=int(r["output_tokens"]),
                cost_usd=r["cost_usd"],
            )
            for r in rows
        ]

    # -- health -------------------------------------------------------------
    def ping(self) -> bool:
        try:
            self._one("SELECT 1 AS ok")
            return True
        except Exception:
            return False

    def applied_migrations(self) -> list[str]:
        rows = self._all("SELECT version FROM schema_migrations ORDER BY version")
        return [r["version"] for r in rows]
