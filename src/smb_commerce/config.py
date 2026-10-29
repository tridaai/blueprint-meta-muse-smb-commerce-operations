"""Runtime configuration, from the environment only.

Defaults are the local development profile: a SQLite fact store in
``./data`` and the offline model profile. Setting ``DATABASE_URL``
selects the Postgres profile — Postgres 16 is the system of record in
the production-shaped deployment. The model tier (Law 6) is read here
too: ``standard`` unless ``META_MUSE_TIER=contributor`` was set
explicitly.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    database_url: str
    sqlite_path: Path
    migrations_dir: Path
    object_dir: Path
    s3_endpoint: str
    s3_bucket: str
    model_profile: str
    model_tier: str
    log_level: str

    @property
    def use_postgres(self) -> bool:
        return bool(self.database_url)


def _resolve(env_value: str | None, *candidates: Path) -> Path:
    """First existing candidate wins; env override always wins."""
    if env_value:
        return Path(env_value)
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]


def load_settings() -> Settings:
    repo_root = Path(__file__).resolve().parents[2]
    tier = os.environ.get("META_MUSE_TIER", "standard").strip().lower()
    if tier not in ("standard", "contributor"):
        tier = "standard"
    return Settings(
        database_url=os.environ.get("DATABASE_URL", ""),
        sqlite_path=Path(os.environ.get("SMB_SQLITE_PATH", "./data/smb.db")),
        migrations_dir=_resolve(
            os.environ.get("SMB_MIGRATIONS_DIR"),
            Path("migrations"),
            repo_root / "migrations",
        ),
        object_dir=Path(os.environ.get("SMB_OBJECT_DIR", "./data/objects")),
        s3_endpoint=os.environ.get("SMB_S3_ENDPOINT", ""),
        s3_bucket=os.environ.get("SMB_S3_BUCKET", "smb-commerce"),
        model_profile=os.environ.get("SMB_MODEL_PROFILE", "offline"),
        model_tier=tier,
        log_level=os.environ.get("LOG_LEVEL", "INFO"),
    )
