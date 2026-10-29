"""Email-flow audit pack (diagnosis pack III, 2026.10.3).

Every planted expectation below is settled from the generator's
stated constants and the pack's stated catalogue, not read back
from the code's output:

- The email platform defines five flows — welcome_series (3 steps),
  abandoned_cart (2), post_purchase (2), browse_abandonment (1),
  replenishment (1), all active — and the catalogue's expected step
  shapes restate exactly those counts, so every defined flow grades
  healthy and is named nowhere.
- The catalogue's sixth flow, winback, is the planted absence: the
  one missing finding, and the one draft proposal — trigger
  customer_lapsed, 2 steps, delays 0 then 336 hours.
"""

from __future__ import annotations

import hashlib
import json

import pytest
from fastapi.testclient import TestClient

from smb_commerce import api as api_module, generator
from smb_commerce.cli import main as cli_main
from smb_commerce.diagnosis import (
    DIAGNOSIS_PACK,
    DIAGNOSIS_PACK_V2,
    DIAGNOSIS_PACKS,
    DiagnosisRefused,
)
from smb_commerce.graph import build_graph
from smb_commerce.models import Artefact
from smb_commerce.service import CommerceService
from smb_commerce.store import canonical_json
from conftest import make_settings


def test_pack_iii_catalogue_is_stated_data():
    assert DIAGNOSIS_PACK["version"] == "2026.10.3"
    audit = DIAGNOSIS_PACK["email_flow_audit"]
    assert audit["max_step_delay_hours"] == "720"
    catalogue = {e["flow_key"]: e for e in audit["catalogue"]}
    assert set(catalogue) == {
        "welcome_series", "abandoned_cart", "post_purchase",
        "browse_abandonment", "replenishment", "winback",
    }
    # The expected shapes restate the generator's defined flows.
    defined = {key: steps for key, _name, steps in generator.EMAIL_FLOWS}
    for key, steps in defined.items():
        assert catalogue[key]["expected_steps"] == steps
    assert catalogue["winback"]["trigger"] == "customer_lapsed"
    assert catalogue["winback"]["expected_steps"] == 2
    assert catalogue["winback"]["step_delays_hours"] == ["0", "336"]


def test_prior_packs_are_kept_verbatim():
    assert set(DIAGNOSIS_PACKS) == {"2026.10.1", "2026.10.2", "2026.10.3"}
    v2 = DIAGNOSIS_PACKS["2026.10.2"]
    assert v2 == DIAGNOSIS_PACK_V2
    assert "email_flow_audit" not in v2
    for section in ("stockout", "ad_decay", "expense_anomaly",
                    "refund_anomaly"):
        assert v2[section] == DIAGNOSIS_PACK[section]


def _flow_facts_fingerprint(service) -> str:
    material = canonical_json(
        [f.model_dump() for f in service.store.list_facts("email_flow")])
    return hashlib.sha256(material.encode()).hexdigest()


def test_audit_names_only_the_planted_missing_winback(service):
    before = _flow_facts_fingerprint(service)
    artefact = service.audit_email_flows()
    assert artefact.kind == "diagnosis_email_flow_audit"
    assert artefact.payload["diagnosis"] == "email_flow_audit"
    assert artefact.payload["pack_version"] == "2026.10.3"
    assert artefact.payload["findings"] == [{
        "class": "missing_flow",
        "flow_key": "winback",
        "name": "Winback",
        "trigger": "customer_lapsed",
        "expected_steps": 2,
    }]
    # The defined-and-healthy flows are named nowhere in the audit.
    named = {f["flow_key"] for f in artefact.payload["findings"]}
    assert named == {"winback"}
    # The audit writes artefacts only: the defined flows are
    # byte-identical before and after it runs.
    assert _flow_facts_fingerprint(service) == before
    assert service.diagnosis.verify(artefact) == []


def test_winback_proposal_is_structure_only(service):
    audit = service.audit_email_flows()
    assert audit.payload["proposals"] == [
        {"flow_key": "winback", "proposal_for": "missing_flow"}]
    proposals = service.store.list_artefacts("draft_flow_proposal")
    assert len(proposals) == 1
    proposal = proposals[0]
    assert proposal.artefact_id.startswith("DFP-")
    assert proposal.payload == {
        "proposal_for": "missing_flow",
        "flow_key": "winback",
        "name": "Winback",
        "trigger": "customer_lapsed",
        "steps": 2,
        "step_delays_hours": ["0", "336"],
        "audit_artefact_id": audit.artefact_id,
        "pack_version": "2026.10.3",
        "status": "proposal",
        "changes_nothing": True,
    }
    assert service.diagnosis.verify(proposal) == []


def test_audit_is_insert_only_and_idempotent(service):
    first = service.audit_email_flows()
    assert service.audit_email_flows().artefact_id == first.artefact_id
    assert len(service.store.list_artefacts("diagnosis_email_flow_audit")) == 1
    assert len(service.store.list_artefacts("draft_flow_proposal")) == 1


def _flow_item(flow_key, name, status, steps, delays=None):
    payload = {"flow_key": flow_key, "name": name,
               "status": status, "steps": steps}
    if delays is not None:
        payload["step_delays_hours"] = delays
    return {"kind": "email_flow", "natural_key": f"flow:{flow_key}",
            "payload": payload,
            "source_system": generator.SOURCE_EMAIL, "as_of": "2026-01-04"}


def test_dormant_shortfall_and_delay_classes_on_a_fixture(tmp_path):
    service = CommerceService(make_settings(tmp_path))
    service.ingest.ingest_many([
        _flow_item("welcome_series", "Welcome Series", "active", 3),
        _flow_item("abandoned_cart", "Abandoned Cart", "active", 1),
        _flow_item("post_purchase", "Post-Purchase", "paused", 2),
        _flow_item("browse_abandonment", "Browse Abandonment", "active", 1,
                   delays=["900"]),
        _flow_item("replenishment", "Replenishment Reminder", "active", 1),
        _flow_item("loyalty_club", "Loyalty Club", "active", 1),
    ])
    service.ledger.compute()
    artefact = service.audit_email_flows()
    findings = {(f["flow_key"], f["class"])
                for f in artefact.payload["findings"]}
    assert findings == {
        ("abandoned_cart", "step_shortfall"),
        ("browse_abandonment", "delay_breach"),
        ("post_purchase", "dormant_flow"),
        ("winback", "missing_flow"),
    }
    by_key = {f["flow_key"]: f for f in artefact.payload["findings"]}
    assert by_key["post_purchase"]["status"] == "paused"
    assert by_key["abandoned_cart"]["steps"] == 1
    assert by_key["abandoned_cart"]["expected_steps"] == 2
    assert by_key["browse_abandonment"]["step_index"] == 0
    assert by_key["browse_abandonment"]["delay_hours"] == "900"
    assert by_key["browse_abandonment"]["max_step_delay_hours"] == "720"
    # A dormant flow and the missing flow each earn a proposal; the
    # shortfall and delay findings do not.
    assert artefact.payload["proposals"] == [
        {"flow_key": "post_purchase", "proposal_for": "dormant_flow"},
        {"flow_key": "winback", "proposal_for": "missing_flow"},
    ]
    proposals = {p.payload["flow_key"]: p
                 for p in service.store.list_artefacts("draft_flow_proposal")}
    assert set(proposals) == {"post_purchase", "winback"}
    assert proposals["post_purchase"].payload["proposal_for"] == "dormant_flow"
    assert service.diagnosis.verify(artefact) == []
    for proposal in proposals.values():
        assert service.diagnosis.verify(proposal) == []


def test_tampered_flow_fact_refuses_the_audit(tmp_path):
    service = CommerceService(make_settings(tmp_path))
    service.generate_dataset()
    service.ledger.compute()
    target = next(
        f for f in service.store.list_facts("email_flow")
        if f.payload["flow_key"] == "welcome_series"
    )
    payload = dict(target.payload)
    payload["status"] = "paused"
    service.store._conn.execute(
        "UPDATE facts SET payload = ? WHERE fact_id = ?",
        (json.dumps(payload, sort_keys=True), target.fact_id),
    )
    service.store._conn.commit()
    with pytest.raises(DiagnosisRefused) as caught:
        service.audit_email_flows()
    refusal = caught.value.refusal
    assert refusal.payload["diagnosis"] == "email_flow_audit"
    assert "ledger_not_current" in refusal.payload["problems"]
    assert service.store.list_artefacts("diagnosis_email_flow_audit") == []
    assert service.store.list_artefacts("draft_flow_proposal") == []


def test_forged_audit_and_proposal_fail_verification(service):
    audit = service.audit_email_flows()
    forged_payload = json.loads(canonical_json(audit.payload))
    forged_payload["findings"] = []
    forged = Artefact(
        artefact_id="DEF-forged00000000000000",
        kind="diagnosis_email_flow_audit",
        payload=forged_payload,
        input_fingerprint=audit.input_fingerprint,
    )
    service.store.insert_artefact(forged)
    assert "email_audit_finding_mismatch" in service.diagnosis.verify(forged)

    proposal = service.store.list_artefacts("draft_flow_proposal")[0]
    forged_payload = json.loads(canonical_json(proposal.payload))
    forged_payload["steps"] = 5
    forged_proposal = Artefact(
        artefact_id="DFP-forged00000000000000",
        kind="draft_flow_proposal",
        payload=forged_payload,
        input_fingerprint=proposal.input_fingerprint,
    )
    service.store.insert_artefact(forged_proposal)
    assert "email_audit_proposal_mismatch:winback" in (
        service.diagnosis.verify(forged_proposal))


def test_audit_stamped_with_a_prior_pack_fails_verification(service):
    """An audit set stamped 2026.10.2 — a pack with no email section —
    names the missing section rather than re-performing."""
    audit = service.audit_email_flows()
    payload = json.loads(canonical_json(audit.payload))
    payload["pack_version"] = "2026.10.2"
    prior = Artefact(
        artefact_id="DEF-priorversion00000000",
        kind="diagnosis_email_flow_audit",
        payload=payload, input_fingerprint=audit.input_fingerprint,
    )
    service.store.insert_artefact(prior)
    assert "pack_section_missing:email_flow_audit" in (
        service.diagnosis.verify(prior))


def test_api_email_audit_reads(tmp_path, monkeypatch):
    settings = make_settings(tmp_path)
    monkeypatch.setattr(api_module, "_service", None)
    monkeypatch.setattr(api_module, "load_settings", lambda: settings)
    client = TestClient(api_module.app)
    assert client.get("/diagnosis/email-flow-audit").status_code == 404
    service = api_module.get_service()
    service.generate_dataset()
    service.ledger.compute()
    service.audit_email_flows()
    audit = client.get("/diagnosis/email-flow-audit").json()
    assert audit["kind"] == "diagnosis_email_flow_audit"
    assert audit["payload"]["findings"][0]["flow_key"] == "winback"
    verify = client.get("/diagnosis/email-flow-audit/verify").json()
    assert verify["verified"] is True
    proposals = client.get("/diagnosis/flow-proposals").json()
    assert proposals["count"] == 1
    assert proposals["proposals"][0]["payload"]["flow_key"] == "winback"


def test_cli_diagnose_covers_the_audit(tmp_path, monkeypatch, capsys):
    settings = make_settings(tmp_path)
    monkeypatch.setenv("SMB_SQLITE_PATH", str(settings.sqlite_path))
    assert cli_main(["generate"]) == 0
    assert cli_main(["diagnose"]) == 0
    out = capsys.readouterr().out
    assert "MISSING winback Winback" in out
    assert "PROPOSAL winback for missing_flow" in out
    assert cli_main(["diagnose-verify"]) == 0


def test_graph_runs_the_audit_and_verifies(tmp_path):
    service = CommerceService(make_settings(tmp_path))
    result = build_graph(service).invoke({})
    assert result["email_audit_artefact_id"].startswith("DEF-")
    assert result["email_audit_findings"] == 1
    assert result["expense_findings"] == 1
    assert result["refund_findings"] == 1
    assert result["verify_problems"] == []
