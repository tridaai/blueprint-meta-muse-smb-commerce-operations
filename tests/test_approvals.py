"""Round 8: the approval queue — named acts, the derived queue,
staleness, and the refusal record.

Approval is an act, not a state: these tests grade that an act
binds the approver, the draft's content hash and the passing
verdict; that the attempts the brief names are refused with the
refusal recorded; that the queue is folded from the acts; that
an approval whose bound inputs move reads STALE with the moved
inputs named, never clean; and that approved everywhere reads
"approved — not sent".
"""

from __future__ import annotations

import json

import pytest

from smb_commerce import api as api_module
from smb_commerce import generator
from smb_commerce.approvals import (
    APPROVED_DISPLAY_STATUS,
    REJECTION_REASONS,
    ApprovalRefused,
)
from smb_commerce.cli import main as cli_main
from smb_commerce.guardian import draft_content_hash
from smb_commerce.models import Artefact
from smb_commerce.service import CommerceService

from conftest import make_settings

APPROVER = "Maya Chen"


@pytest.fixture(scope="module")
def screened(service):
    service.ledger.compute()
    drafts = service.draft_all()
    service.screen_all()
    return drafts


def test_clean_drafts_approve_and_leave_the_queue(service, screened):
    queue_before = service.approval_queue()
    assert {e["draft_artefact_id"] for e in queue_before["awaiting_approval"]} == {
        d.artefact_id for d in screened.values()}
    acts = {}
    for draft in screened.values():
        act = service.approve_draft(draft.artefact_id, APPROVER)
        acts[draft.artefact_id] = act
        p = act.payload
        assert p["approver"] == APPROVER
        assert p["draft_content_hash"] == draft_content_hash(draft)
        verdict = service.guardian.latest_verdict(draft.artefact_id)
        assert p["verdict_artefact_id"] == verdict.artefact_id
        assert p["verdict_pack_version"] == verdict.payload["pack_version"]
        assert p["status"] == "approved"
        assert p["display_status"] == APPROVED_DISPLAY_STATUS
        assert p["execution_status"] == "not_sent"
        assert service.approvals.verify(act) == []
    queue = service.approval_queue()
    assert queue["awaiting_approval"] == []
    assert {e["draft_artefact_id"] for e in queue["approved"]} == set(acts)
    for entry in queue["approved"]:
        assert entry["display_status"] == "approved — not sent"
        assert entry["execution_status"] == "not_sent"
        assert entry["stale"] is False
        assert entry["moved_inputs"] == []
    history = service.approval_history(
        screened["plan_narrative"].artefact_id)
    assert [h["kind"] for h in history] == ["approval_act"]


def test_double_approval_refused_and_recorded(tmp_path):
    svc = CommerceService(make_settings(tmp_path))
    svc.generate_dataset()
    draft = svc.draft_all()["ad_budget_note"]
    svc.screen_draft(draft)
    svc.approve_draft(draft.artefact_id, APPROVER)
    with pytest.raises(ApprovalRefused) as caught:
        svc.approve_draft(draft.artefact_id, "Other Person")
    refusal = caught.value.refusal
    assert refusal.payload["problems"] == ["already_approved"]
    assert refusal.payload["actor"] == "Other Person"
    assert len(svc.store.list_artefacts("approval_act")) == 1
    assert len(svc.approvals.approved()) == 1


def test_approval_without_a_verdict_refused(tmp_path):
    svc = CommerceService(make_settings(tmp_path))
    svc.generate_dataset()
    draft = svc.draft_all()["plan_narrative"]
    with pytest.raises(ApprovalRefused) as caught:
        svc.approve_draft(draft.artefact_id, APPROVER)
    assert caught.value.refusal.payload["problems"] == [
        "no_guardian_verdict"]
    assert svc.approvals.approved() == []
    assert svc.approval_history(draft.artefact_id)[0]["kind"] == (
        "approval_refusal")


def test_approval_of_a_blocked_draft_refused(tmp_path):
    svc = CommerceService(make_settings(tmp_path))
    svc.generate_dataset()
    svc.audit_email_flows()
    genuine = svc.draft_all()["winback_email_copy"]
    proposal_id = genuine.payload["source_bindings"]["proposal"]
    audit_id = genuine.payload["source_bindings"]["audit"]
    planted = Artefact(
        artefact_id="DCE-plantedbad000000008",
        kind="draft_email_copy",
        payload={
            "draft_kind": "winback_email_copy", "status": "draft",
            "source_bindings": {"proposal": proposal_id,
                                "audit": audit_id},
            "source_artefact_ids": sorted([proposal_id, audit_id]),
            "model_profile": "offline", "model": "planted-by-hand",
            "tier": "standard", "model_call_id": "CALL-planted",
            "run_id": "planted",
            "content": {"steps": [
                {"subject": "A treat, on us",
                 "body": "Take 50% off your next order — results"
                         " guaranteed."},
                {"subject": "Still there?",
                 "body": "Your favourites are waiting."},
            ]},
            "claims": genuine.payload["claims"], "supersedes": None,
        },
        input_fingerprint="planted",
    )
    svc.store.insert_artefact(planted)
    verdict = svc.screen_draft(planted)
    assert verdict.payload["outcome"] == "block"
    with pytest.raises(ApprovalRefused) as caught:
        svc.approve_draft(planted.artefact_id, APPROVER)
    problems = caught.value.refusal.payload["problems"]
    assert problems[0] == "guardian_verdict_not_passing"
    assert "blocking_rule:discount_beyond_cap" in problems
    assert svc.approvals.approved() == []


def test_approval_with_a_forged_verdict_refused(tmp_path):
    svc = CommerceService(make_settings(tmp_path))
    svc.generate_dataset()
    drafts = svc.draft_all()
    svc.screen_all()
    target = drafts["ad_budget_note"]
    other = drafts["plan_narrative"]
    pass_verdict = svc.guardian.latest_verdict(other.artefact_id)
    forged_payload = dict(pass_verdict.payload)
    forged_payload["draft_artefact_id"] = target.artefact_id
    forged = Artefact(
        artefact_id="GRV-forged00000000000008",
        kind="guardian_verdict", payload=forged_payload,
        input_fingerprint="forged",
    )
    svc.store.insert_artefact(forged)
    with pytest.raises(ApprovalRefused) as caught:
        svc.approve_draft(target.artefact_id, APPROVER)
    problems = caught.value.refusal.payload["problems"]
    assert "verdict_draft_hash_mismatch" in problems
    assert svc.approvals.approved() == []


def test_stale_approval_after_source_superseded(tmp_path):
    svc = CommerceService(make_settings(tmp_path))
    svc.generate_dataset()
    plan = svc.assemble_plan()
    narrative = svc.draft_plan_narrative()
    svc.screen_draft(narrative)
    act = svc.approve_draft(narrative.artefact_id, APPROVER)
    assert svc.approvals.approved()[0]["stale"] is False
    svc.ingest.ingest(
        kind="order", natural_key="order:EXTRA-8:2026-01-05",
        payload={"order_id": "EXTRA-8", "sku": generator.FAST_MOVER_SKU,
                 "date": "2026-01-05", "qty": 40,
                 "unit_price": "64.00", "amount": "2560.00"},
        source_system=generator.SOURCE_ORDERS, as_of="2026-01-05",
    )
    svc.ledger.compute()
    new_plan = svc.assemble_plan()
    assert new_plan.payload["supersedes"] == plan.artefact_id
    # The act itself is a true record and still verifies; the
    # approved surface must show it STALE with the moved inputs
    # named — never clean.
    assert svc.approvals.verify(act) == []
    entries = svc.approvals.approved()
    assert len(entries) == 1
    assert entries[0]["stale"] is True
    assert entries[0]["moved_inputs"]
    assert any("source" in moved or "ledger" in moved
               for moved in entries[0]["moved_inputs"])
    history = svc.approval_history(narrative.artefact_id)
    assert history[0]["stale"] is True


def test_rejection_leaves_queue_and_stays_on_record(tmp_path):
    svc = CommerceService(make_settings(tmp_path))
    svc.generate_dataset()
    draft = svc.draft_all()["winback_email_copy"]
    svc.screen_draft(draft)
    assert len(svc.approvals.awaiting()) == 1
    act = svc.reject_draft(
        draft.artefact_id, APPROVER, "timing",
        "Hold until the spring catalogue lands.")
    p = act.payload
    assert p["rejecter"] == APPROVER
    assert p["reason_code"] in REJECTION_REASONS
    assert p["note"] == "Hold until the spring catalogue lands."
    assert p["execution_status"] == "not_sent"
    assert svc.approvals.verify(act) == []
    queue = svc.approval_queue()
    assert queue["awaiting_approval"] == []
    assert queue["approved"] == []
    assert [e["draft_artefact_id"] for e in queue["rejected"]] == [
        draft.artefact_id]
    history = svc.approval_history(draft.artefact_id)
    assert [h["kind"] for h in history] == ["rejection_act"]
    # A rejected draft cannot then be approved, and the refusal
    # joins the history.
    with pytest.raises(ApprovalRefused) as caught:
        svc.approve_draft(draft.artefact_id, APPROVER)
    assert caught.value.refusal.payload["problems"] == [
        "already_rejected"]
    assert [h["kind"] for h in svc.approval_history(
        draft.artefact_id)] == ["rejection_act", "approval_refusal"]


def test_rejection_reason_must_be_in_the_vocabulary(tmp_path):
    svc = CommerceService(make_settings(tmp_path))
    svc.generate_dataset()
    draft = svc.draft_all()["plan_narrative"]
    svc.screen_draft(draft)
    with pytest.raises(ApprovalRefused) as caught:
        svc.reject_draft(draft.artefact_id, APPROVER, "bad_vibes")
    assert caught.value.refusal.payload["problems"] == [
        "rejection_reason_not_in_vocabulary:bad_vibes"]
    assert svc.approvals.rejected() == []


def test_forged_approval_act_never_reads_as_approved(tmp_path):
    svc = CommerceService(make_settings(tmp_path))
    svc.generate_dataset()
    draft = svc.draft_all()["plan_narrative"]
    svc.screen_draft(draft)
    verdict = svc.guardian.latest_verdict(draft.artefact_id)
    forged = Artefact(
        artefact_id="APR-forged00000000000008",
        kind="approval_act",
        payload={
            "action": "approve", "draft_artefact_id": draft.artefact_id,
            "draft_kind": "plan_narrative",
            "draft_content_hash": "0" * 64,
            "verdict_artefact_id": verdict.artefact_id,
            "verdict_pack_version": verdict.payload["pack_version"],
            "approver": "Forger",
            "status": "approved",
            "display_status": "approved — not sent",
            "execution_status": "not_sent",
            "bound_inputs": {
                "draft_content_hash": "0" * 64,
                "verdict_artefact_id": verdict.artefact_id,
                "verdict_pack_version": verdict.payload["pack_version"],
                "source_artefact_ids": [],
                "source_heads": {},
                "ledger_artefact_id": None,
            },
        },
        input_fingerprint="forged",
    )
    svc.store.insert_artefact(forged)
    assert "approval_draft_hash_mismatch" in svc.approvals.verify(forged)
    assert svc.approvals.approved() == []


def test_approval_surfaces_api(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    settings = make_settings(tmp_path)
    monkeypatch.setattr(api_module, "_service", None)
    monkeypatch.setattr(api_module, "load_settings", lambda: settings)
    client = TestClient(api_module.app)
    svc = api_module.get_service()
    svc.generate_dataset()
    drafts = svc.draft_all()
    svc.screen_all()
    queue = client.get("/approvals/queue").json()
    assert queue["awaiting_count"] == 3
    assert queue["approved_count"] == 0
    draft_id = drafts["plan_narrative"].artefact_id
    approved = client.post("/approvals/approve", json={
        "draft_artefact_id": draft_id, "approver": APPROVER})
    assert approved.status_code == 200
    act_id = approved.json()["artefact_id"]
    assert approved.json()["payload"]["display_status"] == (
        "approved — not sent")
    again = client.post("/approvals/approve", json={
        "draft_artefact_id": draft_id, "approver": APPROVER})
    assert again.status_code == 422
    checked = client.get(f"/approvals/acts/{act_id}/verify").json()
    assert checked["verified"] is True
    body = client.get("/approvals/approved").json()
    assert body["count"] == 1
    assert body["approved"][0]["stale"] is False
    history = client.get(f"/approvals/history/{draft_id}").json()
    assert history["count"] == 2  # the act and the refused retry
    rejected = client.post("/approvals/reject", json={
        "draft_artefact_id": drafts["ad_budget_note"].artefact_id,
        "rejecter": APPROVER, "reason_code": "timing",
        "note": "Hold."})
    assert rejected.status_code == 200
    assert client.get("/approvals/rejected").json()["count"] == 1
    assert client.get("/approvals/refusals").json()["count"] == 1
    assert client.get(
        "/approvals/acts/APR-nope").status_code == 404


def test_approval_surfaces_cli(tmp_path, monkeypatch, capsys):
    settings = make_settings(tmp_path)
    monkeypatch.setenv("SMB_SQLITE_PATH", str(settings.sqlite_path))
    assert cli_main(["generate"]) == 0
    assert cli_main(["draft"]) == 0
    assert cli_main(["screen"]) == 0
    assert cli_main(["queue"]) == 0
    out = capsys.readouterr().out
    assert "3 awaiting, 0 approved — not sent, 0 rejected" in out
    svc = CommerceService(settings)
    draft = svc.store.latest_artefact("draft_plan_narrative")
    assert cli_main(["approve", "--draft", draft.artefact_id,
                     "--approver", APPROVER]) == 0
    out = capsys.readouterr().out
    assert "APPROVED plan_narrative" in out
    assert "approved — not sent" in out
    assert cli_main(["approve", "--draft", draft.artefact_id,
                     "--approver", APPROVER]) == 1
    assert cli_main(["approval-history", "--draft",
                     draft.artefact_id]) == 0
    out = capsys.readouterr().out
    assert "APPROVAL" in out and "REFUSAL" in out
    assert cli_main(["approvals-verify"]) == 0
    assert cli_main(["approval-refusals"]) == 0
    out = capsys.readouterr().out
    assert "1 approval refusals" in out
    assert cli_main(["queue"]) == 0
    out = capsys.readouterr().out
    assert "2 awaiting, 1 approved — not sent, 0 rejected" in out
    # JSON import guard: the module's json stays used after edits.
    assert json.dumps({"ok": True})
