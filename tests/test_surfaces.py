"""API reads, the LangGraph loop, and migration hygiene."""

from __future__ import annotations

import re
from pathlib import Path

from fastapi.testclient import TestClient

from smb_commerce import api as api_module
from smb_commerce.graph import build_graph
from conftest import make_settings


def test_api_health_readiness_and_reads(tmp_path, monkeypatch):
    settings = make_settings(tmp_path)
    monkeypatch.setenv("SMB_SQLITE_PATH", str(settings.sqlite_path))
    monkeypatch.setattr(api_module, "_service", None)
    monkeypatch.setattr(
        api_module, "load_settings",
        lambda: settings,
    )
    client = TestClient(api_module.app)
    assert client.get("/health").json() == {"status": "ok"}
    readiness = client.get("/readiness")
    assert readiness.status_code == 200
    assert readiness.json()["ready"] is True
    assert readiness.headers["X-Request-Id"]

    service = api_module.get_service()
    service.generate_dataset()
    service.ledger.compute()
    facts = client.get("/facts", params={"kind": "email_flow"}).json()
    assert facts["count"] == 5
    ledger = client.get("/ledger").json()
    assert ledger["kind"] == "ledger"
    verify = client.get("/ledger/verify").json()
    assert verify["verified"] is True
    assert client.get("/facts/nope").status_code == 404
    assert client.get("/model-calls").json()["count"] == 0


def test_graph_runs_the_loop(tmp_path):
    from smb_commerce.service import CommerceService

    service = CommerceService(make_settings(tmp_path))
    graph = build_graph(service)
    result = graph.invoke({})
    assert result["facts_refused"] == 0
    assert result["facts_accepted"] > 3000
    assert result["ledger_artefact_id"].startswith("LED-")
    assert result["verify_problems"] == []


def test_migration_comments_carry_no_semicolons():
    root = Path(__file__).resolve().parents[1] / "migrations"
    files = sorted(root.rglob("*.sql"))
    assert len(files) == 6  # 3 migrations x 2 dialects
    for path in files:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("--"):
                assert ";" not in line, f"semicolon in comment: {path}"


def test_migrations_apply_in_both_dialect_file_sets(store):
    assert store.applied_migrations() == ["0001", "0002", "0003"]
    # Dialect files must name the same versions in the same order.
    root = Path(__file__).resolve().parents[1] / "migrations"
    names = {
        d: sorted(p.name for p in (root / d).glob("*.sql"))
        for d in ("sqlite", "postgres")
    }
    assert names["sqlite"] == names["postgres"]
    assert all(re.match(r"^\d{4}_", n) for n in names["sqlite"])
