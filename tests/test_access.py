"""Round 13: access control and existence hiding, graded.

Four things are graded here:

- the pack itself — every (permission, role) pair against the
  matrix stated literally in this module, so the pack cannot
  drift from its documentation unnoticed;
- resolution — anything unresolvable is anonymous and holds
  nothing, and the internal caller holds everything;
- the service layer — a forbidden read returns the absent form
  (None, the empty list, the empty summary), a forbidden action
  refuses before any lookup of its target, a role-gated approval
  records a refusal artefact and no act, and the per-role flows
  that should work still work;
- the surfaces — cross-role probing over real artefact,
  quarantine, fact and verdict ids returns byte-identical
  responses to probing absent ids, on the API and through the
  CLI's --actor/--role flags.
"""

from __future__ import annotations

from copy import deepcopy

import pytest
from fastapi.testclient import TestClient

from smb_commerce import api as api_module
from smb_commerce import generator
from smb_commerce.access import (
    ACCESS_PACK,
    ANONYMOUS,
    INTERNAL_CALLER,
    PERMISSIONS,
    ROLES,
    AccessRefused,
    Caller,
    resolve_caller,
)
from smb_commerce.approvals import ApprovalRefused
from smb_commerce.cli import main as cli_main
from smb_commerce.costs import empty_summary
from smb_commerce.service import CommerceService

from conftest import make_settings

OWNER = Caller("Maya Chen", "owner")
OPERATOR = Caller("Omar Operator", "operator")
BOOKKEEPER = Caller("Bea Bookkeeper", "bookkeeper")
VIEWER = Caller("Vic Viewer", "viewer")
CALLERS = (OWNER, OPERATOR, BOOKKEEPER, VIEWER)

ABSENT = "XXX-absent0000000000000"

# The matrix, stated literally — the pack must equal this.
EXPECTED = {
    "facts:read": {"owner", "operator", "bookkeeper"},
    "ledger:read": {"owner", "operator", "bookkeeper"},
    "diagnosis:read": {"owner", "operator", "bookkeeper"},
    "plan:read": {"owner", "operator"},
    "drafts:read": {"owner", "operator"},
    "guardian:read": {"owner", "operator"},
    "approvals:read": {"owner", "operator"},
    "board:read": {"owner", "operator"},
    "connectors:read": {"owner", "operator", "bookkeeper"},
    "costs:read": {"owner", "operator", "bookkeeper"},
    "config:read": {"owner", "operator", "bookkeeper", "viewer"},
    "ingest:write": {"owner", "operator"},
    "diagnose:run": {"owner", "operator"},
    "plan:run": {"owner", "operator"},
    "draft:run": {"owner", "operator"},
    "screen:run": {"owner", "operator"},
    "approvals:act": {"owner"},
    # Round 14: the digest permissions. digests:read is the kind
    # gate every staff role holds — a digest's content gate (the
    # permissions its sections required) does the real work.
    "digests:read": {"owner", "operator", "bookkeeper", "viewer"},
    "digests:generate": {"owner", "operator"},
    "subscriptions:read": {"owner", "operator"},
    "subscriptions:write": {"owner", "operator"},
    # Round 17: the review-pack permissions. packs:read is the
    # kind gate every staff role holds — a pack's content gate
    # (the permissions its sections required) does the real
    # work. Publication is the owner's named act, like
    # approvals:act.
    "packs:read": {"owner", "operator", "bookkeeper", "viewer"},
    "packs:generate": {"owner", "operator"},
    "packs:publish": {"owner"},
}


def test_pack_matrix_is_exactly_as_stated():
    assert set(ACCESS_PACK) == set(EXPECTED)
    assert set(PERMISSIONS) == set(EXPECTED)
    for permission, roles in EXPECTED.items():
        assert ACCESS_PACK[permission] == frozenset(roles)
        for role in ROLES:
            caller = Caller("Some One", role)
            assert caller.allows(permission) == (role in roles), (
                permission, role)


def test_resolution_fails_closed():
    for actor, role in ((None, None), ("", "owner"), ("X", None),
                        ("X", ""), ("X", "superuser"), (None, "owner")):
        caller = resolve_caller(actor, role)
        assert caller == ANONYMOUS
        assert not caller.identified
        for permission in PERMISSIONS:
            assert not caller.allows(permission)
    assert resolve_caller("  Maya Chen ", " Owner ") == OWNER
    for permission in PERMISSIONS:
        assert INTERNAL_CALLER.allows(permission)


@pytest.fixture(scope="module")
def record(service):
    """The walked record: dataset, ledger, drafts, verdicts, one
    approval act, one quarantined batch."""
    service.ledger.compute()
    drafts = service.draft_all()
    verdicts = service.screen_all()
    act = service.approve_draft(
        drafts["plan_narrative"].artefact_id, "Maya Chen")
    broken = deepcopy(generator.build_batches()[-1])
    broken["contract_version"] = "1999.1.1"
    report = service.exchange_batch(broken)
    assert report.status == "quarantined"
    return {
        "drafts": drafts,
        "verdicts": verdicts,
        "act": act,
        "quarantine": report.quarantine,
        "fact_id": service.store.list_facts()[0].fact_id,
    }


def test_forbidden_reads_return_the_absent_form(service, record):
    draft_id = record["drafts"]["winback_email_copy"].artefact_id
    act_id = record["act"].artefact_id
    verdict_id = record["verdicts"][draft_id].artefact_id
    for caller in (BOOKKEEPER, VIEWER, ANONYMOUS):
        assert service.read_artefact(draft_id, caller) is None
        assert service.read_artefact(act_id, caller) is None
        assert service.read_artefact(verdict_id, caller) is None
        assert service.read_artefacts("draft_plan_narrative", caller) == []
        assert service.approval_queue(caller) == {
            "awaiting_approval": [], "approved": [], "rejected": []}
        assert service.approval_history(draft_id, caller) == []
        assert service.cost_summary(caller)["total"]["calls"] == (
            0 if caller is not BOOKKEEPER else
            service.cost_summary(OWNER)["total"]["calls"])
    for caller in (VIEWER, ANONYMOUS):
        assert service.read_fact(record["fact_id"], caller) is None
        assert service.read_facts(caller=caller) == []
        assert service.read_latest_artefact("ledger", caller) is None
        assert service.dataset_counts(caller) == {}
        assert service.read_quarantine(
            record["quarantine"].artefact_id, caller) is None
        assert service.quarantines(caller) == []
        assert service.cost_summary(caller) == empty_summary()
        assert service.context_records(caller) == []
        assert service.read_model_calls(caller=caller) == []
        assert service.connector_registry(caller) == []
    # The absent form is the same value the allowed roles get for
    # a truly absent id — that is the whole point.
    assert service.read_artefact(ABSENT, OWNER) is None
    assert service.read_fact(ABSENT, OWNER) is None
    assert service.read_quarantine(ABSENT, OWNER) is None
    assert service.approval_history(ABSENT, OWNER) == []


def test_permitted_reads_still_read(service, record):
    draft_id = record["drafts"]["winback_email_copy"].artefact_id
    for caller in (OWNER, OPERATOR):
        assert service.read_artefact(draft_id, caller) is not None
        assert service.read_artefact(record["act"].artefact_id,
                                      caller) is not None
        queue = service.approval_queue(caller)
        assert len(queue["approved"]) == 1
        assert len(queue["awaiting_approval"]) == 2
    # The bookkeeper reads the money record, in full.
    assert service.read_fact(record["fact_id"], BOOKKEEPER) is not None
    assert service.read_latest_artefact(
        "ledger", BOOKKEEPER) is not None
    assert service.read_latest_artefact(
        "diagnosis_stockout", BOOKKEEPER) is not None
    assert len(service.quarantines(BOOKKEEPER)) == 1
    assert service.read_quarantine(
        record["quarantine"].artefact_id, BOOKKEEPER) is not None
    assert service.cost_summary(BOOKKEEPER)["total"]["calls"] > 0


def test_role_gated_approval_records_a_refusal(service, record):
    draft_id = record["drafts"]["winback_email_copy"].artefact_id
    refusals_before = len(service.approvals.refusals())
    for caller, role in ((OPERATOR, "operator"), (VIEWER, "viewer"),
                         (BOOKKEEPER, "bookkeeper")):
        with pytest.raises(ApprovalRefused) as caught:
            service.approve_draft(draft_id, caller.actor, caller=caller)
        problems = caught.value.refusal.payload["problems"]
        assert problems == [f"actor_role_may_not_approve:{role}"]
        assert caught.value.refusal.payload["actor"] == caller.actor
    with pytest.raises(ApprovalRefused) as caught:
        service.approve_draft(draft_id, "Maya Chen", caller=ANONYMOUS)
    assert caught.value.refusal.payload["problems"] == [
        "actor_role_may_not_approve:none"]
    assert len(service.approvals.refusals()) == refusals_before + 4
    # No act was recorded by any of the refused attempts.
    assert service.approvals.approval_for(draft_id) is None
    # The gate fires before lookup: an absent draft gets the same
    # role refusal, not a draft problem.
    with pytest.raises(ApprovalRefused) as caught:
        service.approve_draft(ABSENT, "Omar Operator", caller=OPERATOR)
    assert caught.value.refusal.payload["problems"] == [
        "actor_role_may_not_approve:operator"]
    # And the owner can still approve the same draft afterwards.
    act = service.approve_draft(draft_id, "Maya Chen", caller=OWNER)
    assert act.payload["approver"] == "Maya Chen"


def test_forbidden_actions_refuse(service, record):
    envelope = generator.build_batches()[0]
    for caller in (BOOKKEEPER, VIEWER, ANONYMOUS):
        with pytest.raises(AccessRefused):
            service.generate_dataset(caller)
        with pytest.raises(AccessRefused):
            service.exchange_batch(envelope, caller)
        with pytest.raises(AccessRefused):
            service.diagnose_stockout(caller)
        with pytest.raises(AccessRefused):
            service.assemble_plan(caller)
        with pytest.raises(AccessRefused):
            service.draft_all(caller)
        with pytest.raises(AccessRefused):
            service.screen_all(caller)
        with pytest.raises(AccessRefused):
            service.derive_board(caller)
    with pytest.raises(AccessRefused):
        service.model_profile_report(ANONYMOUS)
    # Identified roles may read the configuration.
    assert service.model_profile_report(VIEWER)["profile"] == "offline"


def test_operator_walks_the_pipeline_but_cannot_approve(tmp_path):
    svc = CommerceService(make_settings(tmp_path))
    svc.generate_dataset(OPERATOR)
    svc.ledger.compute()
    svc.diagnose_stockout(OPERATOR)
    svc.assemble_plan(OPERATOR)
    drafts = svc.draft_all(OPERATOR)
    svc.screen_all(OPERATOR)
    board = svc.derive_board(OPERATOR)
    assert board.payload["headline"]["counts"]["awaiting_approval"] == 3
    draft_id = drafts["plan_narrative"].artefact_id
    with pytest.raises(ApprovalRefused):
        svc.approve_draft(draft_id, "Omar Operator", caller=OPERATOR)
    act = svc.approve_draft(draft_id, "Maya Chen", caller=OWNER)
    assert act.payload["display_status"] == "approved — not sent"


def _api_client(monkeypatch, service):
    monkeypatch.setattr(api_module, "_service", service)
    return TestClient(api_module.app)


def _headers(caller):
    return {"X-SMB-Actor": caller.actor, "X-SMB-Role": caller.role}


def test_api_probing_is_byte_identical(record, service, monkeypatch):
    client = _api_client(monkeypatch, service)
    act_id = record["act"].artefact_id
    quarantine_id = record["quarantine"].artefact_id
    verdict_id = record["verdicts"][
        record["drafts"]["winback_email_copy"].artefact_id].artefact_id
    probes = (
        "/approvals/acts/{}",
        "/approvals/acts/{}/verify",
        "/connectors/quarantines/{}",
        "/connectors/quarantines/{}/verify",
        "/guardian/verdicts/{}/verify",
        "/facts/{}",
    )
    real_ids = (act_id, act_id, quarantine_id, quarantine_id,
                verdict_id, record["fact_id"])
    for caller in (VIEWER, ANONYMOUS):
        headers = _headers(caller) if caller.identified else {}
        for template, real in zip(probes, real_ids):
            real_response = client.get(template.format(real),
                                       headers=headers)
            absent_response = client.get(template.format(ABSENT),
                                         headers=headers)
            assert real_response.status_code == 404, template
            assert absent_response.status_code == 404, template
            assert real_response.content == absent_response.content, (
                template)
    # The bookkeeper is forbidden the approval and guardian
    # classes, and only those, among these probes.
    for template, real in zip(probes[:2], real_ids[:2]):
        real_response = client.get(template.format(real),
                                   headers=_headers(BOOKKEEPER))
        absent_response = client.get(template.format(ABSENT),
                                     headers=_headers(BOOKKEEPER))
        assert real_response.status_code == 404
        assert real_response.content == absent_response.content
    verdict_probe = client.get(
        f"/guardian/verdicts/{verdict_id}/verify",
        headers=_headers(BOOKKEEPER))
    assert verdict_probe.status_code == 404
    # Sanity: the same ids are really there for the owner.
    for template, real in zip(probes, real_ids):
        assert client.get(template.format(real),
                          headers=_headers(OWNER)).status_code == 200


def test_api_summaries_do_not_leak(record, service, monkeypatch):
    client = _api_client(monkeypatch, service)
    viewer = _headers(VIEWER)
    assert client.get("/drafts", headers=viewer).json()["count"] == 0
    queue = client.get("/approvals/queue", headers=viewer).json()
    assert queue["awaiting_count"] == 0
    assert queue["approved_count"] == 0
    assert queue["rejected_count"] == 0
    costs = client.get("/costs", headers=viewer).json()
    assert costs["total"]["calls"] == 0
    assert costs["total"]["cost_usd"] == "0.000000"
    assert client.get("/contexts", headers=viewer).json()["count"] == 0
    assert client.get(
        "/model-calls", headers=viewer).json()["count"] == 0
    board = client.get("/board", headers=_headers(BOOKKEEPER))
    assert board.status_code == 404
    assert board.json() == {"detail": "no board yet"}
    anonymous_board = client.get("/board")
    assert anonymous_board.status_code == 404
    assert anonymous_board.content == board.content
    # The owner sees the real summaries.
    owner = _headers(OWNER)
    assert client.get("/drafts", headers=owner).json()["count"] >= 3
    assert client.get(
        "/approvals/queue", headers=owner).json()["approved_count"] >= 1
    assert client.get("/board", headers=owner).status_code == 200
    # Configuration answers any identified role, and no anonymous.
    assert client.get(
        "/model-profile", headers=viewer).status_code == 200
    assert client.get("/model-profile").status_code == 403
    assert client.get("/health").status_code == 200


def test_api_approval_and_exchange_gates(tmp_path, monkeypatch):
    settings = make_settings(tmp_path)
    monkeypatch.setattr(api_module, "_service", None)
    monkeypatch.setattr(api_module, "load_settings", lambda: settings)
    client = TestClient(api_module.app)
    svc = api_module.get_service()
    svc.generate_dataset()
    svc.ledger.compute()
    drafts = svc.draft_all()
    svc.screen_all()
    draft_id = drafts["plan_narrative"].artefact_id
    # The operator's attempt is refused with the role problem,
    # identically for a real and an absent draft.
    for target in (draft_id, ABSENT):
        refused = client.post(
            "/approvals/approve", headers=_headers(OPERATOR),
            json={"draft_artefact_id": target,
                  "approver": "Omar Operator"})
        assert refused.status_code == 422
        assert refused.json()["detail"]["problems"] == [
            "actor_role_may_not_approve:operator"]
    # No identity at all: refused the same way, role none.
    refused = client.post(
        "/approvals/approve",
        json={"draft_artefact_id": draft_id, "approver": "Maya Chen"})
    assert refused.status_code == 422
    assert refused.json()["detail"]["problems"] == [
        "actor_role_may_not_approve:none"]
    # The owner approves.
    approved = client.post(
        "/approvals/approve", headers=_headers(OWNER),
        json={"draft_artefact_id": draft_id, "approver": "Maya Chen"})
    assert approved.status_code == 200
    # Exchange: the bookkeeper and the anonymous caller are
    # refused outright; the operator's replay lands as duplicates.
    envelope = {"envelope": generator.build_batches()[0]}
    assert client.post("/connectors/exchange", json=envelope,
                       headers=_headers(BOOKKEEPER)).status_code == 403
    assert client.post(
        "/connectors/exchange", json=envelope).status_code == 403
    replay = client.post("/connectors/exchange", json=envelope,
                         headers=_headers(OPERATOR))
    assert replay.status_code == 200
    assert replay.json()["status"] == "accepted"


def test_cli_roles(tmp_path, monkeypatch, capsys):
    settings = make_settings(tmp_path)
    monkeypatch.setenv("SMB_SQLITE_PATH", str(settings.sqlite_path))
    assert cli_main(["generate"]) == 0
    assert cli_main(["diagnose"]) == 0
    assert cli_main(["draft"]) == 0
    assert cli_main(["screen"]) == 0
    capsys.readouterr()
    # The viewer's reads are the absent form: no drafts, an empty
    # queue, no facts.
    assert cli_main(["drafts", "--actor", "Vic Viewer",
                     "--role", "viewer"]) == 0
    assert "0 drafts" in capsys.readouterr().out
    assert cli_main(["queue", "--actor", "Vic Viewer",
                     "--role", "viewer"]) == 0
    assert ("0 awaiting, 0 approved — not sent, 0 rejected"
            in capsys.readouterr().out)
    assert cli_main(["facts", "--actor", "Vic Viewer",
                     "--role", "viewer"]) == 0
    assert "0 facts" in capsys.readouterr().out
    # The bookkeeper reads the money record.
    assert cli_main(["ledger", "--actor", "Bea Bookkeeper",
                     "--role", "bookkeeper"]) == 0
    assert "gross revenue" in capsys.readouterr().out
    # A forbidden action refuses.
    assert cli_main(["diagnose", "--actor", "Bea Bookkeeper",
                     "--role", "bookkeeper"]) == 1
    assert "role_not_permitted:diagnose:run" in capsys.readouterr().out
    # Approval through the CLI: the operator is refused with the
    # role problem recorded; the owner approves.
    svc = CommerceService(settings)
    draft = svc.store.latest_artefact("draft_plan_narrative")
    assert cli_main(["approve", "--draft", draft.artefact_id,
                     "--approver", "Omar Operator",
                     "--role", "operator"]) == 1
    assert ("actor_role_may_not_approve:operator"
            in capsys.readouterr().out)
    assert cli_main(["approve", "--draft", draft.artefact_id,
                     "--approver", "Maya Chen",
                     "--role", "owner"]) == 0
    assert "APPROVED plan_narrative" in capsys.readouterr().out
    # Approval history for a forbidden role is the absent form:
    # it prints nothing, exactly as for an absent draft.
    assert cli_main(["approval-history", "--draft", draft.artefact_id,
                     "--actor", "Vic Viewer", "--role", "viewer"]) == 0
    assert capsys.readouterr().out == ""
