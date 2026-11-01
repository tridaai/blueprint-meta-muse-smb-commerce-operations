"""Round 17: the monthly review pack, graded.

Five things are graded here:

- the pack itself — folded whole from the record for a stated
  month (headline and detail from the head ledger, the ranked
  findings, the listing summary, the approval picture, the cost
  summary), pins on its face, verified by re-performance, and a
  tampered figure failing with the problem named;
- the export — one deterministic document whose provenance
  appendix re-resolves figure by figure and whose checksum
  manifest re-performs against the stored artefacts; tampered
  bytes and a forged manifest failing with the problems named;
- the publication gate — draft until the owner's named act,
  bound to the pack's content hash; the operator refused, a
  spoofed publisher refused, a forged act conferring nothing,
  a second publication refused, and staleness (a moved pin, a
  superseding pack) derived and named, never shown clean;
- content-gated reads — the bookkeeper and the viewer, who
  lack section permissions the pack carries, get absence
  indistinguishable from a missing pack, on the service layer,
  the generic artefact reads and the API alike;
- generation and export idempotence, and the month validation.
"""

from __future__ import annotations

import hashlib
import json
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
from smb_commerce.models import Artefact
from smb_commerce.reviewpacks import (
    EXPORT_KIND,
    PACK_KIND,
    PUBLICATION_KIND,
    PackPublicationRefused,
)
from smb_commerce.service import CommerceService
from smb_commerce.store import canonical_json

from conftest import make_settings

OWNER = Caller("Maya Chen", "owner")
OPERATOR = Caller("Omar Operator", "operator")
BOOKKEEPER = Caller("Bea Bookkeeper", "bookkeeper")
VIEWER = Caller("Vic Viewer", "viewer")

ABSENT_PACK = "RPK-absent000000000000"
ABSENT_EXPORT = "RPX-absent000000000000"

ALL_SECTIONS = ["headline", "detail", "findings", "stock_risks",
                "listing", "approvals", "costs"]


def test_pack_permissions_are_as_stated():
    assert ACCESS_PACK["packs:read"] == frozenset(
        {"owner", "operator", "bookkeeper", "viewer"})
    assert ACCESS_PACK["packs:generate"] == frozenset(
        {"owner", "operator"})
    # The named human act over the pack: the owner only, exactly
    # as approvals:act — the operator may not publish what their
    # own pipeline produced.
    assert ACCESS_PACK["packs:publish"] == frozenset({"owner"})


@pytest.fixture(scope="module")
def record(service):
    """The walked record the pack folds: images and the listing
    diagnosis included, one approval act on the record."""
    service.generate_product_images()
    service.ledger.compute()
    service.diagnose_listing_quality()
    service.diagnose_stockout()
    service.diagnose_ad_decay()
    service.diagnose_expenses()
    service.diagnose_refunds()
    service.audit_email_flows()
    service.assemble_plan()
    drafts = service.draft_all()
    service.screen_all()
    service.approve_draft(
        drafts["plan_narrative"].artefact_id, "Maya Chen")
    return service


def fresh_pack(service, month="2026-01"):
    """The pack for ``month`` over the record as it stands now —
    generation is idempotent per record state, so this is the
    verifying pack whatever earlier tests moved."""
    return service.generate_review_pack(month)


# -- the pack ---------------------------------------------------------


def test_pack_folds_the_record(service, record):
    pack = fresh_pack(service)
    payload = pack.payload
    assert pack.kind == PACK_KIND
    assert payload["month"] == "2026-01"
    assert payload["sections_included"] == ALL_SECTIONS
    assert "no per-month ledger" in payload["grain"]
    ledger = service.store.latest_artefact("ledger")
    assert payload["headline"]["ledger_artefact_id"] == (
        ledger.artefact_id)
    figures = {f["name"]: f["value"]
               for f in payload["headline"]["figures"]}
    assert figures["gross_revenue"] == ledger.payload["gross_revenue"]
    assert figures["net_revenue"] == ledger.payload["net_revenue"]
    assert payload["detail"]["skus"] == ledger.payload["skus"]
    assert payload["detail"]["ad_sets"] == ledger.payload["ad_sets"]
    board = service.board.derive()
    assert payload["findings"] == board.payload["findings"]
    assert payload["pins"]["ledger_head"] == ledger.artefact_id
    assert payload["pins"]["listing_head"] == (
        service.store.latest_artefact(
            "diagnosis_listing_quality").artefact_id)
    listing = service.store.latest_artefact("diagnosis_listing_quality")
    assert payload["listing"]["counts"]["findings"] == len(
        listing.payload["findings"])
    assert payload["approvals"]["counts"]["approved"] == 1
    assert payload["costs"]["total"]["calls"] > 0
    assert service.review_packs.verify(pack) == []


def test_pack_tampering_fails_with_names(service, record):
    pack = fresh_pack(service)
    tampered = deepcopy(pack)
    tampered.payload["headline"]["figures"][0]["value"] = "0.01"
    problems = service.review_packs.verify(tampered)
    assert any(p.startswith("pack_figure_mismatch:headline:")
               for p in problems)
    tampered = deepcopy(pack)
    sku = tampered.payload["detail"]["skus"][0]
    sku["gross_revenue"] = "0.01"
    problems = service.review_packs.verify(tampered)
    assert "pack_figure_mismatch:detail:skus" in problems
    tampered = deepcopy(pack)
    ranked = next(e for e in tampered.payload["findings"]
                  if e["rank"] is not None)
    field = next(iter(ranked["figures"]))
    ranked["figures"][field] = "tampered"
    problems = service.review_packs.verify(tampered)
    assert any(p.startswith("pack_figure_mismatch:")
               and p.endswith(f":{field}") for p in problems)


def test_generation_idempotent_and_month_validated(service, record):
    before = len(service.store.list_artefacts(PACK_KIND))
    pack = fresh_pack(service)
    again = service.generate_review_pack("2026-01")
    assert again.artefact_id == pack.artefact_id
    assert len(service.store.list_artefacts(PACK_KIND)) == before
    for bad in ("2026-13", "2026-1", "Jan 2026", "", "2026-01-04"):
        with pytest.raises(ValueError):
            service.generate_review_pack(bad)
    with pytest.raises(AccessRefused):
        service.generate_review_pack("2026-02", caller=BOOKKEEPER)
    with pytest.raises(AccessRefused):
        service.generate_review_pack("2026-02", caller=ANONYMOUS)


# -- the export ---------------------------------------------------------


def test_export_document_appendix_and_manifest(service, record):
    pack = fresh_pack(service)
    export = service.export_review_pack(pack.artefact_id)
    assert export.kind == EXPORT_KIND
    body = service.objects.get(export.payload["object_key"])
    assert hashlib.sha256(body).hexdigest() == (
        export.payload["body_checksum"])
    document = json.loads(body)
    assert document["pack_artefact_id"] == pack.artefact_id
    assert document["publication"]["status"] == "draft"
    for name in ALL_SECTIONS:
        assert document["sections"][name] == pack.payload[name]
    appendix = {e["figure"]: e
                for e in document["provenance_appendix"]}
    ledger = service.store.latest_artefact("ledger")
    gross = appendix["headline.gross_revenue"]
    assert gross["value"] == ledger.payload["gross_revenue"]
    assert gross["source_artefact_id"] == ledger.artefact_id
    assert gross["source_as_of"] == ledger.payload["as_of"]
    sku_entry = appendix["detail.skus.HW-THR-002.gross_revenue"]
    assert sku_entry["source_artefact_id"] == ledger.artefact_id
    assert appendix["costs.total.cost_usd"]["source"] == (
        "model_call_ledger")
    manifest = {e["artefact_id"] for e in document["checksum_manifest"]}
    assert pack.artefact_id in manifest
    assert ledger.artefact_id in manifest
    assert pack.payload["pins"]["plan_head"] in manifest
    assert service.review_packs.verify_export(export) == []
    # Re-exporting an unmoved pack in the same state is the
    # same export — nothing lands twice.
    assert service.export_review_pack(
        pack.artefact_id).artefact_id == export.artefact_id


def test_export_tamper_and_forged_manifest_fail(service, record):
    pack = fresh_pack(service)
    export = service.export_review_pack(pack.artefact_id)
    key = export.payload["object_key"]
    genuine = service.objects.get(key)
    service.objects.put(key, genuine.replace(b'"month":"2026-01"',
                                             b'"month":"2026-02"', 1))
    problems = service.review_packs.verify_export(export)
    assert "export_checksum_mismatch" in problems
    assert "export_month_mismatch" in problems
    service.objects.put(key, genuine)
    assert service.review_packs.verify_export(export) == []
    # A forged export whose manifest claims a checksum the
    # stored ledger does not have: the body is internally
    # consistent, so only re-performance catches it.
    document = json.loads(genuine)
    ledger_id = pack.payload["headline"]["ledger_artefact_id"]
    for entry in document["checksum_manifest"]:
        if entry["artefact_id"] == ledger_id:
            entry["sha256"] = "0" * 64
    forged_body = (canonical_json(document) + "\n").encode()
    forged_key = f"review-pack-exports/forged/{pack.artefact_id}.json"
    service.objects.put(forged_key, forged_body)
    forged_payload = deepcopy(export.payload)
    forged_payload["object_key"] = forged_key
    forged_payload["body_checksum"] = hashlib.sha256(
        forged_body).hexdigest()
    forged_payload["referenced_artefacts"] = (
        document["checksum_manifest"])
    forged = Artefact(
        artefact_id="RPX-forged000000000001", kind=EXPORT_KIND,
        payload=forged_payload, input_fingerprint="forged")
    service.store.insert_artefact(forged)
    problems = service.review_packs.verify_export(forged)
    assert f"export_referenced_checksum_mismatch:{ledger_id}" in problems
    assert "export_manifest_stale" in problems


# -- the publication gate -------------------------------------------------


def test_publication_gate(service, record):
    pack = fresh_pack(service, "2026-03")
    status = service.review_pack_status(pack.artefact_id, OWNER)
    assert status["status"] == "draft"
    # The operator is refused, and the refusal is recorded.
    refusals_before = len(service.store.list_artefacts(
        "review_pack_publication_refusal"))
    with pytest.raises(PackPublicationRefused) as caught:
        service.publish_review_pack(
            pack.artefact_id, "Omar Operator", caller=OPERATOR)
    assert caught.value.refusal.payload["problems"] == [
        "actor_role_may_not_publish:operator"]
    assert len(service.store.list_artefacts(
        "review_pack_publication_refusal")) == refusals_before + 1
    # A publication naming someone else is refused too.
    with pytest.raises(PackPublicationRefused) as caught:
        service.publish_review_pack(
            pack.artefact_id, "Maya Chen",
            caller=Caller("Mallory", "owner"))
    assert caught.value.refusal.payload["problems"] == [
        "publisher_is_not_the_calling_actor"]
    assert service.review_pack_status(
        pack.artefact_id, OWNER)["status"] == "draft"
    # The owner's act publishes; a second act is refused.
    act = service.publish_review_pack(
        pack.artefact_id, "Maya Chen", caller=OWNER)
    assert act.kind == PUBLICATION_KIND
    assert service.review_packs.verify_publication(act) == []
    status = service.review_pack_status(pack.artefact_id, OWNER)
    assert status["status"] == "published"
    assert status["publisher"] == "Maya Chen"
    assert status["stale"] is False
    with pytest.raises(PackPublicationRefused) as caught:
        service.publish_review_pack(
            pack.artefact_id, "Maya Chen", caller=OWNER)
    assert "already_published" in (
        caught.value.refusal.payload["problems"])
    # The export rendered after publication says so, and verifies.
    export = service.export_review_pack(pack.artefact_id)
    assert export.payload["publication"]["status"] == "published"
    assert service.review_packs.verify_export(export) == []


def test_forged_publication_act_confers_nothing(service, record):
    pack = fresh_pack(service, "2025-12")
    forged = Artefact(
        artefact_id="RPP-forged000000000001", kind=PUBLICATION_KIND,
        payload={
            "action": "publish",
            "pack_artefact_id": pack.artefact_id,
            "month": pack.payload["month"],
            "pack_content_hash": "0" * 64,
            "publisher": "Mallory",
            "status": "published",
            "display_status": "published",
            "bound_pins": pack.payload["pins"],
        },
        input_fingerprint="forged",
    )
    service.store.insert_artefact(forged)
    problems = service.review_packs.verify_publication(forged)
    assert "publication_pack_hash_mismatch" in problems
    status = service.review_pack_status(pack.artefact_id, OWNER)
    assert status["status"] == "draft"
    assert status["publication_artefact_id"] is None


def test_publication_staleness_is_derived(service, record):
    pack = fresh_pack(service, "2025-11")
    service.publish_review_pack(pack.artefact_id, "Maya Chen",
                                caller=OWNER)
    # Moving a pinned head — a further approval act — stales
    # the publication, naming the pin; the act itself still
    # verifies (staleness is a derivation, not a defect).
    awaiting = service.approvals_awaiting()
    service.approve_draft(
        awaiting[0]["draft_artefact_id"], "Maya Chen")
    status = service.review_pack_status(pack.artefact_id, OWNER)
    assert status["status"] == "published"
    assert status["stale"] is True
    assert "pin_moved:approval_head" in status["moved_inputs"]
    act = service.review_packs.publication_for(pack.artefact_id)
    assert service.review_packs.verify_publication(act) == []
    # A fresh pack for the same month supersedes the published
    # one, and the staleness names the supersession too.
    newer = service.generate_review_pack("2025-11")
    assert newer.artefact_id != pack.artefact_id
    status = service.review_pack_status(pack.artefact_id, OWNER)
    assert any(m.startswith("pack_superseded:")
               for m in status["moved_inputs"])


# -- content-gated reads ---------------------------------------------------


def test_reads_are_content_gated(service, record):
    pack = fresh_pack(service)
    export = service.export_review_pack(pack.artefact_id)
    for caller in (OWNER, OPERATOR):
        assert service.read_review_pack(
            pack.artefact_id, caller) is not None
        assert service.read_review_pack_export(
            export.artefact_id, caller) is not None
    # The bookkeeper holds the ledger and costs permissions but
    # not plan:read or approvals:read — the pack, its export and
    # its verify all answer as absent, byte-identical in shape
    # to a missing id. The viewer likewise; anonymous too.
    for caller in (BOOKKEEPER, VIEWER, ANONYMOUS):
        assert service.read_review_pack(pack.artefact_id, caller) is None
        assert service.read_review_pack(ABSENT_PACK, caller) is None
        assert service.read_review_pack_export(
            export.artefact_id, caller) is None
        assert service.verify_review_pack(
            pack.artefact_id, caller) is None
        assert service.review_pack_status(
            pack.artefact_id, caller) is None
        assert service.read_review_packs(caller) == []
        assert service.read_artefact(pack.artefact_id, caller) is None
        assert service.read_artefacts(PACK_KIND, caller) == []
    assert service.read_artefact(pack.artefact_id, OWNER) is not None


@pytest.fixture()
def client(service, record, monkeypatch):
    monkeypatch.setattr(api_module, "_service", service)
    return TestClient(api_module.app)


def _headers(caller):
    return {"X-SMB-Actor": caller.actor, "X-SMB-Role": caller.role}


def test_api_pack_probing_is_byte_identical(service, client):
    pack_id = service.generate_review_pack("2026-06").artefact_id
    for caller in (BOOKKEEPER, VIEWER):
        headers = _headers(caller)
        real = client.get(f"/review-packs/{pack_id}", headers=headers)
        absent = client.get(
            f"/review-packs/{ABSENT_PACK}", headers=headers)
        assert real.status_code == absent.status_code == 404
        assert real.content == absent.content
        status_real = client.get(
            f"/review-packs/{pack_id}/status", headers=headers)
        assert status_real.status_code == 404
    assert client.get(
        f"/review-packs/{pack_id}",
        headers=_headers(OWNER)).status_code == 200
    verified = client.get(
        f"/review-packs/{pack_id}/verify", headers=_headers(OWNER))
    assert verified.json() == {
        "artefact_id": pack_id, "verified": True, "problems": []}


def test_api_generate_publish_and_export_gates(client):
    generated = client.post(
        "/review-packs/generate", json={"month": "2026-04"},
        headers=_headers(OPERATOR))
    assert generated.status_code == 200
    pack_id = generated.json()["artefact_id"]
    refused = client.post(
        "/review-packs/generate", json={"month": "2026-05"},
        headers=_headers(BOOKKEEPER))
    assert refused.status_code == 403
    bad_month = client.post(
        "/review-packs/generate", json={"month": "2026-13"},
        headers=_headers(OWNER))
    assert bad_month.status_code == 422
    # Publication by the operator: 422 with the recorded
    # refusal's problems, exactly like an approval refusal.
    refused = client.post(
        f"/review-packs/{pack_id}/publish",
        json={"publisher": "Omar Operator"},
        headers=_headers(OPERATOR))
    assert refused.status_code == 422
    assert refused.json()["detail"]["problems"] == [
        "actor_role_may_not_publish:operator"]
    published = client.post(
        f"/review-packs/{pack_id}/publish",
        json={"publisher": "Maya Chen"}, headers=_headers(OWNER))
    assert published.status_code == 200
    status = client.get(
        f"/review-packs/{pack_id}/status", headers=_headers(OWNER))
    assert status.json()["status"] == "published"
    exported = client.post(
        f"/review-packs/{pack_id}/export", headers=_headers(OWNER))
    assert exported.status_code == 200
    export_id = exported.json()["artefact_id"]
    verified = client.get(
        f"/review-pack-exports/{export_id}/verify",
        headers=_headers(OWNER))
    assert verified.json()["verified"] is True
    hidden = client.get(
        f"/review-pack-exports/{export_id}",
        headers=_headers(VIEWER))
    absent = client.get(
        f"/review-pack-exports/{ABSENT_EXPORT}",
        headers=_headers(VIEWER))
    assert hidden.status_code == absent.status_code == 404
    assert hidden.content == absent.content


def test_cli_review_pack_round_trip(tmp_path, monkeypatch, capsys):
    settings = make_settings(tmp_path)
    svc = CommerceService(settings)
    svc.generate_dataset()
    svc.generate_product_images()
    svc.ledger.compute()
    svc.diagnose_listing_quality()
    svc.diagnose_stockout()
    svc.diagnose_ad_decay()
    svc.diagnose_expenses()
    svc.diagnose_refunds()
    svc.audit_email_flows()
    svc.assemble_plan()
    svc.draft_all()
    svc.screen_all()
    monkeypatch.setenv("SMB_SQLITE_PATH", str(settings.sqlite_path))
    monkeypatch.setenv("SMB_OBJECT_DIR", str(settings.object_dir))
    assert cli_main(["review-pack-generate", "--month", "2026-01"]) == 0
    out = capsys.readouterr().out
    pack_id = out.split()[0]
    assert pack_id.startswith("RPK-")
    assert cli_main(["review-pack-verify", "--pack", pack_id]) == 0
    assert cli_main(["review-pack-export", "--pack", pack_id]) == 0
    assert cli_main(["review-pack-publish", "--pack", pack_id,
                     "--publisher", "Maya Chen"]) == 0
    assert cli_main(["review-pack-status", "--pack", pack_id]) == 0
    status = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert status["status"] == "published"
    # As the bookkeeper the pack list is empty and the pack's
    # status answers as absent.
    assert cli_main(["review-packs", "--actor", "Bea Bookkeeper",
                     "--role", "bookkeeper"]) == 0
    out = capsys.readouterr().out
    assert "0 review packs" in out
    assert cli_main(["review-pack-status", "--pack", pack_id,
                     "--actor", "Bea Bookkeeper",
                     "--role", "bookkeeper"]) == 1
