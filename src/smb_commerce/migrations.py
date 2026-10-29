"""Numbered SQL migrations + the startup runner.

Migrations live in ``migrations/<dialect>/NNNN_name.sql`` and are
applied in filename order, each exactly once, recorded in the
``schema_migrations`` table inside the same database. The runner
speaks to a DB-API connection whose paramstyle the caller declares
(``qmark`` for SQLite, ``format`` for psycopg/Postgres).

House rule, learned the hard way in sibling blueprints: migration
files never carry a semicolon inside a comment, because the runner
splits statements on semicolons after dropping comment lines.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

_MIGRATION_RE = re.compile(r"^(\d{4})_.*\.sql$")


def _split_statements(sql: str) -> list[str]:
    lines = [ln for ln in sql.splitlines() if not ln.strip().startswith("--")]
    return [stmt.strip() for stmt in "\n".join(lines).split(";") if stmt.strip()]


def run_migrations(conn, migrations_dir: Path, dialect: str, paramstyle: str) -> list[str]:
    """Apply pending migrations for ``dialect``; return versions applied."""
    placeholder = "?" if paramstyle == "qmark" else "%s"
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        "version TEXT PRIMARY KEY, applied_at TEXT NOT NULL)"
    )
    rows = conn.execute("SELECT version FROM schema_migrations").fetchall()
    applied = {row["version"] if isinstance(row, dict) else row[0] for row in rows}
    now = datetime.now(timezone.utc).isoformat()
    done: list[str] = []
    dialect_dir = migrations_dir / dialect
    for path in sorted(dialect_dir.glob("*.sql")):
        match = _MIGRATION_RE.match(path.name)
        if not match:
            continue
        version = match.group(1)
        if version in applied:
            continue
        for statement in _split_statements(path.read_text(encoding="utf-8")):
            conn.execute(statement)
        conn.execute(
            f"INSERT INTO schema_migrations (version, applied_at) VALUES ({placeholder}, {placeholder})",
            (version, now),
        )
        done.append(version)
    conn.commit()
    return done
