"""Round 14: subscriptions and digests, graded.

Four things are graded here:

- the subscription record — the fold (subscribe, supersession
  by unsubscribe, resubscribe), idempotent re-subscription,
  validation, and the write gates (only owner + operator write;
  only owner + operator read the record itself);
- per-role content — a digest carries exactly the sections the
  access pack allows its role (the owner all five, the
  bookkeeper headline + costs, the viewer none), its headline
  figures are the head ledger's, and its findings are the
  board's, in plan order;
- verification — every generated digest re-performs clean, and
  a tampered figure or a section smuggled into a role's digest
  fails with the problem named;
- generation and reads — generation is idempotent (a second
  run over an unmoved record inserts nothing), and digest reads
  are content-gated: the bookkeeper probing the owner digest
  gets byte-identical absence to probing a missing id, on the
  service layer, the generic artefact reads and the API.
"""

from __future__ import annotations

from copy import deepcopy

import pytest
from fastapi.testclient import TestClient

from smb_commerce import api as api_module
from smb_commerce.access import (
    ACCESS_PACK,
    ANONYMOUS,
    AccessRefused,
    Caller,
)
from smb_commerce.cli import main as cli_main
from smb_commerce.digests import DIGEST_KIND, SUBSCRIPTION_KIND
from smb_commerce.service import CommerceService

from conftest import make_settings

OWNER = Caller("Maya Chen", "owner")
OPERATOR = Caller("Omar Operator", "operator")
BOOKKEEPER = Caller("Bea Bookkeeper", "bookkeeper")
VIEWER = Caller("Vic Viewer", "viewer")

ABSENT = "DIG-absent000000000000"

ALL_SECTIONS = ["headline", "findings", "stock_risks",
                "approvals", "costs"]


def test_pack_gains_the_digest_permissions():
    assert ACCESS_PACK["digests:read"] == frozenset(
        {"owner", "operator", "bookkeeper", "viewer"})
    assert ACCESS_PACK["digests:generate"] == frozenset(
        {"owner", "operator"})
    assert ACCESS_PACK["subscriptions:read"] == frozenset(
        {"owner", "operator"})
    assert ACCESS_PACK["subscriptions:write"] == frozenset(
        {"owner", "operator"})


@pytest.fixture(scope="module")
def record(service):
    """The walked record plus the four roles subscribed daily
    (the operator also weekly) and both cadences generated."""
    service.ledger.compute()
    drafts = service.draft_all()
    service.screen_all()
    service.approve_draft(
        drafts["plan_narrative"].artefact_id, "Maya Chen")
    service.subscribe_digest("Maya Chen", "owner", "daily")
    service.subscribe_digest("Omar Operator", "operator", "daily")
    service.subscribe_digest("Bea Bookkeeper", "bookkeeper", "daily")
    service.subscribe_digest("Vic Viewer", "viewer", "daily")
    service.subscribe_digest("Omar Operator", "operator", "weekly")
    daily = service.generate_digests("daily")
    weekly = service.generate_digests("weekly")
    by_key = {
        (d.payload["role"], d.payload["cadence"]): d
        for d in service.store.list_artefacts(DIGEST_KIND)
    }
    return {"daily": daily, "weekly": weekly, "digests": by_key}


def _states(service, actor):
    return [s for s in service.digests.subscriptions()
            if s["actor"] == actor]


def test_subscription_fold_supersession_and_idempotence(service):
    artefacts_before = len(
        service.store.list_artefacts(SUBSCRIPTION_KIND))
    first = service.subscribe_digest("Temp One", "operator", "weekly")
    assert _states(service, "Temp One")[0]["active"] is True
    again = service.subscribe_digest("Temp One", "operator", "weekly")
    assert again.artefact_id == first.artefact_id
    assert len(service.store.list_artefacts(
        SUBSCRIPTION_KIND)) == artefacts_before + 1
    ended = service.unsubscribe_digest("Temp One", "operator", "weekly")
    assert ended is not None
    assert ended.payload["supersedes"] == first.artefact_id
    state = _states(service, "Temp One")[0]
    assert state["active"] is False
    assert state["artefact_id"] == ended.artefact_id
    assert service.unsubscribe_digest(
        "Temp One", "operator", "weekly") is None
    revived = service.subscribe_digest("Temp One", "operator", "weekly")
    assert revived.payload["supersedes"] == ended.artefact_id
    assert _states(service, "Temp One")[0]["active"] is True
    chain = [a for a in service.store.list_artefacts(SUBSCRIPTION_KIND)
             if a.payload["actor"] == "Temp One"]
    assert len(chain) == 3


def test_subscription_validation_and_gates(service):
    for actor, role, cadence in (
        ("", "owner", "daily"),
        ("Maya Chen", "superuser", "daily"),
        ("Maya Chen", "owner", "monthly"),
    ):
        with pytest.raises(ValueError):
            service.subscribe_digest(actor, role, cadence)
    for caller in (BOOKKEEPER, VIEWER, ANONYMOUS):
        with pytest.raises(AccessRefused):
            service.subscribe_digest(
                "New Person", "viewer", "daily", caller)
        with pytest.raises(AccessRefused):
            service.generate_digests("daily", caller=caller)
    assert service.read_subscriptions(BOOKKEEPER) == []
    assert service.read_subscriptions(VIEWER) == []
    assert service.read_subscriptions(ANONYMOUS) == []
    assert service.read_subscriptions(OWNER) != []


def test_digest_content_follows_the_pack(service, record):
    digests = record["digests"]
    owner = digests[("owner", "daily")]
    assert owner.payload["sections_included"] == ALL_SECTIONS
    ledger = service.store.latest_artefact("ledger")
    for figure in owner.payload["headline"]["figures"]:
        assert figure["source_artefact_id"] == ledger.artefact_id
        assert figure["value"] == ledger.payload[figure["source_field"]]
    assert owner.payload["headline"]["ledger_artefact_id"] == (
        ledger.artefact_id)
    board = service.derive_board().payload
    assert owner.payload["findings"] == board["findings"]
    assert owner.payload["stock_risks"] == board["stock_risks"]
    assert owner.payload["approvals"]["counts"]["approved"] >= 1
    assert owner.payload["costs"]["total"]["calls"] > 0
    assert owner.payload["period"] == {
        "start": "2026-01-04", "end": "2026-01-04"}
    assert owner.payload["as_of"] == "2026-01-04"

    bookkeeper = digests[("bookkeeper", "daily")]
    assert bookkeeper.payload["sections_included"] == [
        "headline", "costs"]
    for section in ("findings", "stock_risks", "approvals"):
        assert section not in bookkeeper.payload

    viewer = digests[("viewer", "daily")]
    assert viewer.payload["sections_included"] == []
    for section in ALL_SECTIONS:
        assert section not in viewer.payload

    weekly = digests[("operator", "weekly")]
    assert weekly.payload["sections_included"] == ALL_SECTIONS
    assert weekly.payload["period"] == {
        "start": "2025-12-29", "end": "2026-01-04"}


def test_generated_digests_verify_and_tampering_fails(service, record):
    for digest in record["digests"].values():
        assert service.digests.verify(digest) == []
    owner = record["digests"][("owner", "daily")]
    tampered = deepcopy(owner)
    tampered.payload["headline"]["figures"][0]["value"] = "0.01"
    problems = service.digests.verify(tampered)
    assert any(p.startswith("digest_figure_mismatch:headline:")
               for p in problems)
    viewer = record["digests"][("viewer", "daily")]
    smuggled = deepcopy(viewer)
    smuggled.payload["approvals"] = {"counts": {"approved": 99}}
    problems = service.digests.verify(smuggled)
    assert "digest_section_not_permitted:approvals" in problems
    assert "digest_section_mismatch:approvals" in problems


def test_generation_is_idempotent(service, record):
    before = len(service.store.list_artefacts(DIGEST_KIND))
    report = service.generate_digests("daily")
    assert len(service.store.list_artefacts(DIGEST_KIND)) == before
    assert report["generated"]
    assert all(e["status"] == "existing" for e in report["generated"])
    assert {e["role"] for e in report["generated"]} == {
        "owner", "operator", "bookkeeper", "viewer"}


def test_digest_reads_are_content_gated(service, record):
    digests = record["digests"]
    owner_id = digests[("owner", "daily")].artefact_id
    bookkeeper_id = digests[("bookkeeper", "daily")].artefact_id
    viewer_id = digests[("viewer", "daily")].artefact_id
    # The owner reads every digest; absence answers identically.
    for digest in digests.values():
        read = service.read_digest(digest.artefact_id, OWNER)
        assert read is not None
        assert read.artefact_id == digest.artefact_id
    assert service.read_digest(ABSENT, OWNER) is None
    # The bookkeeper cannot read what carries sections the pack
    # denies them — and the answer is the absent form.
    assert service.read_digest(owner_id, BOOKKEEPER) is None
    assert service.read_digest(ABSENT, BOOKKEEPER) is None
    assert service.read_digest(bookkeeper_id, BOOKKEEPER) is not None
    # The viewer's digest carries nothing, so any identified
    # staff caller may read it; the viewer reads nothing else.
    assert service.read_digest(viewer_id, BOOKKEEPER) is not None
    viewer_reads = service.read_digests(caller=VIEWER)
    assert [d.artefact_id for d in viewer_reads] == [viewer_id]
    bookkeeper_reads = {
        d.artefact_id for d in service.read_digests(caller=BOOKKEEPER)}
    assert bookkeeper_reads == {bookkeeper_id, viewer_id}
    assert service.read_digests(caller=ANONYMOUS) == []
    assert service.read_digest(owner_id, ANONYMOUS) is None
    # The generic artefact reads apply the same content gate.
    assert service.read_artefact(owner_id, BOOKKEEPER) is None
    assert service.read_artefact(owner_id, OWNER) is not None
    generic = {a.artefact_id
               for a in service.read_artefacts(DIGEST_KIND, BOOKKEEPER)}
    assert generic == {bookkeeper_id, viewer_id}


def _api_client(monkeypatch, service):
    monkeypatch.setattr(api_module, "_service", service)
    return TestClient(api_module.app)


def _headers(caller):
    return {"X-SMB-Actor": caller.actor, "X-SMB-Role": caller.role}


def test_api_digest_probing_is_byte_identical(service, record,
                                              monkeypatch):
    client = _api_client(monkeypatch, service)
    owner_id = record["digests"][("owner", "daily")].artefact_id
    for caller in (BOOKKEEPER, VIEWER, ANONYMOUS):
        headers = _headers(caller) if caller.identified else {}
        for template in ("/digests/{}", "/digests/{}/verify"):
            real = client.get(template.format(owner_id),
                              headers=headers)
            absent = client.get(template.format(ABSENT),
                                headers=headers)
            assert real.status_code == 404, template
            assert absent.status_code == 404, template
            assert real.content == absent.content, template
    assert client.get(f"/digests/{owner_id}",
                      headers=_headers(OWNER)).status_code == 200
    verified = client.get(f"/digests/{owner_id}/verify",
                          headers=_headers(OWNER))
    assert verified.status_code == 200
    assert verified.json()["verified"] is True
    listing = client.get("/digests", headers=_headers(BOOKKEEPER))
    listed_ids = {d["artefact_id"] for d in listing.json()["digests"]}
    assert owner_id not in listed_ids
    assert listing.json()["count"] == len(listed_ids) == 2


def test_api_subscription_and_generation_gates(service, record,
                                               monkeypatch):
    client = _api_client(monkeypatch, service)
    body = {"actor": "New Person", "role": "viewer",
            "cadence": "weekly"}
    refused = client.post("/subscriptions", json=body,
                          headers=_headers(VIEWER))
    assert refused.status_code == 403
    created = client.post("/subscriptions", json=body,
                          headers=_headers(OWNER))
    assert created.status_code == 200
    assert created.json()["active"] is True
    invalid = client.post(
        "/subscriptions",
        json={"actor": "X", "role": "owner", "cadence": "monthly"},
        headers=_headers(OWNER))
    assert invalid.status_code == 422
    ended = client.post("/subscriptions/unsubscribe", json=body,
                        headers=_headers(OWNER))
    assert ended.status_code == 200
    assert ended.json()["active"] is False
    missing = client.post("/subscriptions/unsubscribe", json=body,
                          headers=_headers(OWNER))
    assert missing.status_code == 404
    forbidden = client.post("/digests/generate",
                            json={"cadence": "daily"},
                            headers=_headers(BOOKKEEPER))
    assert forbidden.status_code == 403
    generated = client.post("/digests/generate",
                            json={"cadence": "daily"},
                            headers=_headers(OPERATOR))
    assert generated.status_code == 200
    assert all(e["status"] == "existing"
               for e in generated.json()["generated"])
    states = client.get("/subscriptions", headers=_headers(OWNER))
    assert states.json()["count"] >= 5
    hidden = client.get("/subscriptions", headers=_headers(BOOKKEEPER))
    assert hidden.json() == {"count": 0, "subscriptions": []}


def test_cli_subscriptions_and_digests(tmp_path, monkeypatch, capsys):
    settings = make_settings(tmp_path)
    svc = CommerceService(settings)
    svc.generate_dataset()
    svc.ledger.compute()
    svc.draft_all()
    svc.screen_all()
    monkeypatch.setenv("SMB_SQLITE_PATH", str(settings.sqlite_path))
    assert cli_main(["subscribe", "--subscriber", "Maya Chen",
                     "--subscriber-role", "owner",
                     "--cadence", "daily"]) == 0
    assert cli_main(["subscribe", "--subscriber", "Bea Bookkeeper",
                     "--subscriber-role", "bookkeeper",
                     "--cadence", "daily"]) == 0
    assert cli_main(["digest-generate", "--cadence", "daily"]) == 0
    out = capsys.readouterr().out
    assert "2 digests" in out
    assert cli_main(["digests"]) == 0
    out = capsys.readouterr().out
    assert "headline, findings, stock_risks, approvals, costs" in out
    assert "headline, costs" in out
    # As the bookkeeper the owner digest is absent from the list,
    # and a verify of its id answers as no digest at all.
    owner_digest = next(
        d for d in svc.store.list_artefacts(DIGEST_KIND)
        if d.payload["role"] == "owner")
    assert cli_main(["digests", "--actor", "Bea Bookkeeper",
                     "--role", "bookkeeper"]) == 0
    out = capsys.readouterr().out
    assert owner_digest.artefact_id not in out
    assert cli_main(["digest-verify", "--digest",
                     owner_digest.artefact_id,
                     "--actor", "Bea Bookkeeper",
                     "--role", "bookkeeper"]) == 1
    assert cli_main(["digest-verify", "--digest",
                     owner_digest.artefact_id]) == 0
    assert cli_main(["subscriptions", "--actor", "Bea Bookkeeper",
                     "--role", "bookkeeper"]) == 0
    out = capsys.readouterr().out
    assert "0 subscriptions" in out
    assert cli_main(["unsubscribe", "--subscriber", "Maya Chen",
                     "--subscriber-role", "owner",
                     "--cadence", "daily"]) == 0
