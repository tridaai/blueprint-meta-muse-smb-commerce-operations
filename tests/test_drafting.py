"""Round 6 grades: drafting through the governed model port.

The offline profile composes from the figure sheet alone, so the
three drafts over the Hartwell record must verify clean: every
claim re-resolves against the artefact it cites, the narrative
walks the plan's priorities in the plan's order, and the winback
copy states only the proposal's own figures. A careless model
double — one that invents a figure in its claims, and one that
states a number its claims do not carry — must be refused whole,
with no draft stored as usable.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from smb_commerce import api as api_module
from smb_commerce.cli import main as cli_main
from smb_commerce.drafting import (
    AD_NOTE_KIND,
    EMAIL_COPY_KIND,
    NARRATIVE_KIND,
    DraftingService,
    DraftRefused,
    figure_tokens,
    resolve_field,
)
from smb_commerce.graph import build_graph
from smb_commerce.modelport import GovernedModelPort, ModelReply
from smb_commerce.models import Artefact
from smb_commerce.service import CommerceService
from conftest import make_settings


def _drafts(service):
    return service.draft_all()


def test_narrative_verifies_and_walks_priorities_in_order(service):
    drafts = _drafts(service)
    narrative = drafts["plan_narrative"]
    assert narrative.kind == NARRATIVE_KIND
    assert narrative.artefact_id.startswith("DRN-")
    p = narrative.payload
    assert p["status"] == "draft"
    assert service.drafting.verify(narrative) == []

    plan = service.store.get_artefact(p["source_bindings"]["plan"])
    prose = p["content"]["prose"]
    positions = [
        prose.index(entry["finding_key"])
        for entry in plan.payload["priorities"]
        if entry["rank"] is not None
    ]
    assert positions == sorted(positions)
    # The summary figures the narrative states are the ledger's.
    ledger = service.store.get_artefact(plan.payload["ledger_artefact_id"])
    for figure in plan.payload["summary"]["figures"]:
        assert figure["value"] in prose
        assert ledger.payload[figure["source_field"]] == figure["value"]


def test_narrative_claims_resolve_to_their_artefacts(service):
    narrative = _drafts(service)["plan_narrative"]
    claims = narrative.payload["claims"]
    # Six summary figures plus four exposures (the audit priority
    # is structural and quotes none).
    assert len(claims) == 10
    for claim in claims:
        artefact = service.store.get_artefact(claim["source_artefact_id"])
        resolved = resolve_field(artefact.payload, claim["source_field"])
        assert str(resolved) == claim["value"]


def test_winback_copy_steps_subjects_and_claims(service):
    copy = _drafts(service)["winback_email_copy"]
    assert copy.kind == EMAIL_COPY_KIND
    assert copy.artefact_id.startswith("DCE-")
    p = copy.payload
    assert p["status"] == "draft"
    assert service.drafting.verify(copy) == []
    steps = p["content"]["steps"]
    assert len(steps) == 2
    assert steps[0]["subject"] == "We miss you at Hartwell Home Goods"
    assert steps[1]["subject"] == (
        "Your Hartwell Home Goods favourites are still here"
    )
    assert "336 hours" in steps[1]["body"]
    proposal = service.store.get_artefact(p["source_bindings"]["proposal"])
    assert proposal.payload["flow_key"] == "winback"
    cited = {(c["source_artefact_id"], c["source_field"])
             for c in p["claims"]}
    assert (proposal.artefact_id, "steps") in cited
    assert (proposal.artefact_id, "step_delays_hours[1]") in cited


def test_ad_note_quotes_the_decay_finding_verbatim(service):
    note = _drafts(service)["ad_budget_note"]
    assert note.kind == AD_NOTE_KIND
    assert note.artefact_id.startswith("DAN-")
    p = note.payload
    assert p["status"] == "draft"
    assert service.drafting.verify(note) == []
    diagnosis = service.store.get_artefact(p["source_bindings"]["diagnosis"])
    finding = diagnosis.payload["findings"][0]
    prose = p["content"]["prose"]
    for field in ("first_week_roas", "latest_week_roas", "decay_factor",
                  "total_spend"):
        assert finding[field] in prose


def test_drafts_are_stamped_and_their_calls_reconcile(service):
    drafts = _drafts(service)
    for draft in drafts.values():
        p = draft.payload
        assert p["model_profile"] == "offline"
        assert p["tier"] == "standard"
        (record,) = [
            call for call in service.store.list_model_calls(p["run_id"])
            if call.call_id == p["model_call_id"]
        ]
        assert record.profile == p["model_profile"]
        assert record.tier == p["tier"]
        assert service.model.reconcile(p["run_id"]) == []


def test_redrafting_the_same_record_is_idempotent(service):
    first = _drafts(service)
    second = _drafts(service)
    assert first["plan_narrative"].artefact_id == (
        second["plan_narrative"].artefact_id
    )
    assert len(service.store.list_artefacts(NARRATIVE_KIND)) == 1


def test_draft_prose_states_no_unclaimed_figure(service):
    from decimal import Decimal

    for draft in _drafts(service).values():
        p = draft.payload
        claimed = {Decimal(c["value"]) for c in p["claims"]}
        content = p["content"]
        texts = [content.get("prose", "")]
        texts += [
            f"{step['subject']} {step['body']}"
            for step in content.get("steps", [])
        ]
        stated = [
            value for text in texts for _raw, value in figure_tokens(text)
        ]
        assert stated  # every draft states figures from its sheet
        for value in stated:
            assert value in claimed


# -- the careless model ------------------------------------------------------


def _sheet_claims(prompt: str) -> list[dict]:
    line = prompt.splitlines()[1]
    return json.loads(line[len("FIGURE-SHEET "):])["claims"]


class _InventedClaimPort:
    """A model double that returns the sheet's claims with the
    first figure invented — the classic careless draft."""

    profile_name = "offline"
    model_name = "careless-double"

    def __init__(self, tier: str = "standard"):
        self.tier = tier

    def complete(self, system: str, prompt: str) -> ModelReply:
        claims = _sheet_claims(prompt)
        claims[0] = dict(claims[0], value="999999.99")
        text = json.dumps(
            {"content": {"prose": "A fine quarter."}, "claims": claims}
        )
        return ModelReply(
            text=text, profile=self.profile_name, model=self.model_name,
            tier=self.tier, input_tokens=10, output_tokens=5,
        )


class _UnclaimedProsePort:
    """A model double whose claims are honest but whose prose
    states a number no claim carries."""

    profile_name = "offline"
    model_name = "careless-double"

    def __init__(self, tier: str = "standard"):
        self.tier = tier

    def complete(self, system: str, prompt: str) -> ModelReply:
        claims = _sheet_claims(prompt)
        text = json.dumps({
            "content": {"prose": "Revenue will reach 999999.99 soon."},
            "claims": claims,
        })
        return ModelReply(
            text=text, profile=self.profile_name, model=self.model_name,
            tier=self.tier, input_tokens=10, output_tokens=5,
        )


def _careless_service(service, inner) -> DraftingService:
    port = GovernedModelPort(inner, service.store)
    return DraftingService(service.store, port)


def test_careless_model_invented_claim_is_refused(service):
    plan = service.assemble_plan()
    before = len(service.store.list_artefacts(NARRATIVE_KIND))
    careless = _careless_service(service, _InventedClaimPort())
    with pytest.raises(DraftRefused) as refused:
        careless.draft_plan_narrative(plan)
    refusal = refused.value.refusal
    assert refusal.kind == "draft_refusal"
    assert refusal.artefact_id.startswith("DRR-")
    assert any(
        problem.startswith("draft_figure_mismatch")
        for problem in refusal.payload["problems"]
    )
    # No draft was stored as usable by the careless run.
    assert len(service.store.list_artefacts(NARRATIVE_KIND)) == before
    assert service.drafting.verify(refusal) == []


def test_careless_model_unclaimed_prose_number_is_refused(service):
    plan = service.assemble_plan()
    careless = _careless_service(service, _UnclaimedProsePort())
    with pytest.raises(DraftRefused) as refused:
        careless.draft_plan_narrative(plan)
    assert any(
        problem.startswith("prose_figure_unclaimed:999999.99")
        for problem in refused.value.refusal.payload["problems"]
    )


def test_tampered_stored_draft_fails_verify(service):
    narrative = _drafts(service)["plan_narrative"]
    payload = json.loads(json.dumps(narrative.payload))
    payload["claims"][0] = dict(payload["claims"][0], value="1.00")
    forged = Artefact(
        artefact_id="DRN-forged00000000000000", kind=NARRATIVE_KIND,
        payload=payload, input_fingerprint="forged",
    )
    service.store.insert_artefact(forged)
    problems = service.drafting.verify(forged)
    assert any(
        problem.startswith("draft_figure_mismatch") for problem in problems
    )


def test_draft_surfaces_api(tmp_path, monkeypatch):
    settings = make_settings(tmp_path)
    monkeypatch.setattr(api_module, "_service", None)
    monkeypatch.setattr(api_module, "load_settings", lambda: settings)
    client = TestClient(api_module.app)
    assert client.get("/drafts/plan-narrative").status_code == 404
    service = api_module.get_service()
    service.generate_dataset()
    drafts = service.draft_all()
    body = client.get("/drafts").json()
    assert body["count"] == 3
    narrative = client.get("/drafts/plan-narrative").json()
    assert narrative["artefact_id"] == drafts["plan_narrative"].artefact_id
    assert narrative["payload"]["status"] == "draft"
    assert client.get("/drafts/plan-narrative/verify").json()["verified"]
    copy = client.get("/drafts/winback-copy").json()
    subjects = [s["subject"] for s in copy["payload"]["content"]["steps"]]
    assert subjects[0] == "We miss you at Hartwell Home Goods"
    assert client.get("/drafts/winback-copy/verify").json()["verified"]
    assert client.get("/drafts/ad-budget-note/verify").json()["verified"]
    assert client.get("/drafts/refusals").json()["count"] == 0


def test_draft_surfaces_cli(tmp_path, monkeypatch, capsys):
    settings = make_settings(tmp_path)
    monkeypatch.setenv("SMB_SQLITE_PATH", str(settings.sqlite_path))
    assert cli_main(["generate"]) == 0
    assert cli_main(["draft"]) == 0
    out = capsys.readouterr().out
    assert "DRAFT plan_narrative" in out
    assert "status draft" in out
    assert "We miss you at Hartwell Home Goods" in out
    assert cli_main(["drafts-verify"]) == 0
    assert cli_main(["draft-refusals"]) == 0
    assert "0 draft refusals" in capsys.readouterr().out


def test_graph_drafts_and_verifies(tmp_path):
    service = CommerceService(make_settings(tmp_path))
    result = build_graph(service).invoke({})
    assert result["narrative_draft_id"].startswith("DRN-")
    assert result["email_copy_draft_id"].startswith("DCE-")
    assert result["ad_note_draft_id"].startswith("DAN-")
    assert result["verify_problems"] == []
