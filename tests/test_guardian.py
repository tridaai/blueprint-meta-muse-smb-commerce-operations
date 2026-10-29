"""Round 7: the guardian screen — verdicts, the gate, and the
adversarial fixtures.

The fixture drafts are built two ways, on purpose. Some are
drafted through a model double whose prose the Round 6 gate
cannot refuse (word-form figures, a spelled-out discount) — the
guardian must catch what the digit scan cannot see. Others are
planted straight into the store, bypassing the drafting gate
entirely — the guardian is the second net for those too.
"""

from __future__ import annotations

import json

import pytest

from smb_commerce import api as api_module
from smb_commerce import generator
from smb_commerce import guardian as guardian_module
from smb_commerce.cli import main as cli_main
from smb_commerce.drafting import DraftingService
from smb_commerce.graph import build_graph
from smb_commerce.guardian import (
    CODE_RULE_IDS,
    GUARDIAN_PACK,
    JUDGEMENT_RULE_IDS,
    GuardianService,
    draft_content_hash,
)
from smb_commerce.modelport import GovernedModelPort, ModelReply
from smb_commerce.models import Artefact
from smb_commerce.service import CommerceService

from conftest import make_settings


@pytest.fixture(scope="module")
def drafts(service):
    service.ledger.compute()
    return service.draft_all()


def test_clean_drafts_pass_and_await_approval(service, drafts):
    verdicts = service.screen_all()
    assert set(verdicts) == {d.artefact_id for d in drafts.values()}
    for verdict in verdicts.values():
        assert verdict.payload["outcome"] == "pass"
        assert verdict.payload["blocking_rules"] == []
        assert service.guardian.verify(verdict) == []
    awaiting = service.awaiting_approval()
    assert {e["draft_artefact_id"] for e in awaiting} == set(verdicts)
    assert service.blocked_drafts() == []


def test_verdict_binds_draft_hash_pack_and_call(service, drafts):
    draft = drafts["plan_narrative"]
    verdict = service.guardian.latest_verdict(draft.artefact_id)
    p = verdict.payload
    assert p["draft_artefact_id"] == draft.artefact_id
    assert p["draft_content_hash"] == draft_content_hash(draft)
    assert p["pack_version"] == GUARDIAN_PACK["version"]
    rule_ids = {r["rule_id"] for r in p["rules"]}
    assert set(CODE_RULE_IDS) <= rule_ids
    assert set(JUDGEMENT_RULE_IDS) <= rule_ids
    calls = service.store.list_model_calls(p["screening_run_id"])
    assert [c.call_id for c in calls] == [p["screening_call_id"]]
    assert service.model.reconcile(p["screening_run_id"]) == []


def test_rescreening_is_idempotent(service, drafts):
    draft = drafts["ad_budget_note"]
    before = len(service.store.list_artefacts("guardian_verdict"))
    again = service.screen_draft(draft)
    assert again.artefact_id == (
        service.guardian.latest_verdict(draft.artefact_id).artefact_id
    )
    assert len(service.store.list_artefacts("guardian_verdict")) == before


def test_unscreened_drafts_are_never_awaiting(tmp_path):
    svc = CommerceService(make_settings(tmp_path))
    svc.generate_dataset()
    svc.draft_all()
    assert svc.awaiting_approval() == []
    assert svc.blocked_drafts() == []


# -- fixture drafts through model doubles -----------------------------------


class _ProsePort:
    """A model double that drafts one stated prose over the
    figure sheet's own claims — honest figures, dangerous words."""

    profile_name = "offline"
    tier = "standard"

    def __init__(self, model_name: str, prose: str):
        self.model_name = model_name
        self._prose = prose

    def complete(self, system, prompt):
        line = prompt.splitlines()[1]
        claims = json.loads(line[len("FIGURE-SHEET "):])["claims"]
        return ModelReply(
            text=json.dumps({
                "content": {"prose": self._prose},
                "claims": claims,
            }),
            profile=self.profile_name, model=self.model_name,
            tier=self.tier, input_tokens=10, output_tokens=5,
        )


def _note_with_prose(tmp_path, model_name: str, prose: str):
    svc = CommerceService(make_settings(tmp_path))
    svc.generate_dataset()
    svc.diagnose_ad_decay()
    drafting = DraftingService(
        svc.store,
        GovernedModelPort(_ProsePort(model_name, prose), svc.store),
    )
    draft = drafting.draft_ad_note()
    return svc, draft


def test_spelled_out_discount_blocks(tmp_path):
    svc, draft = _note_with_prose(
        tmp_path, "wordy-discount",
        "Take forty percent off the decaying set's next flight,"
        " whenever you are ready.",
    )
    # The drafting gate cannot see a spelled-out figure: the
    # draft was stored as usable. The guardian is the second net.
    assert svc.drafting.verify(draft) == []
    verdict = svc.screen_draft(draft)
    assert verdict.payload["outcome"] == "block"
    assert "discount_beyond_cap" in verdict.payload["blocking_rules"]
    assert svc.guardian.verify(verdict) == []
    assert svc.awaiting_approval() == []
    blocked = svc.blocked_drafts()
    assert blocked[0]["draft_artefact_id"] == draft.artefact_id
    assert "discount_beyond_cap" in blocked[0]["blocking_rules"]


def test_word_form_figure_blocks(tmp_path):
    svc, draft = _note_with_prose(
        tmp_path, "wordy-figure",
        "The set reached nine hundred new shoppers this week.",
    )
    assert svc.drafting.verify(draft) == []
    verdict = svc.screen_draft(draft)
    assert "figure_not_in_claims" in verdict.payload["blocking_rules"]


def test_competitor_disparagement_blocks(tmp_path):
    svc, draft = _note_with_prose(
        tmp_path, "wordy-disparagement",
        "Unlike BrightNest Living, whose throws are shoddy and"
        " fall apart within a season, ours are made to hold.",
    )
    verdict = svc.screen_draft(draft)
    assert "competitor_disparagement" in (
        verdict.payload["blocking_rules"])


def test_guaranteed_results_language_blocks(tmp_path):
    svc, draft = _note_with_prose(
        tmp_path, "wordy-guarantee",
        "These results are guaranteed, every single week.",
    )
    verdict = svc.screen_draft(draft)
    assert "guaranteed_results_language" in (
        verdict.payload["blocking_rules"])


def test_pressure_tactics_block_via_the_screening_call(tmp_path):
    svc, draft = _note_with_prose(
        tmp_path, "wordy-pressure",
        "Act now: this is your last chance to hold the set's"
        " budget where it is.",
    )
    verdict = svc.screen_draft(draft)
    assert "tone_pressure" in verdict.payload["blocking_rules"]
    entry = next(
        r for r in verdict.payload["rules"]
        if r["rule_id"] == "tone_pressure"
    )
    assert entry["source"] == "model"


# -- fixture drafts planted straight into the store ---------------------------


def _plant_draft(svc, artefact_id: str, body: str, claims: list) -> Artefact:
    proposal = svc.store.list_artefacts("draft_flow_proposal")[0]
    audit = svc.store.latest_artefact("diagnosis_email_flow_audit")
    payload = {
        "draft_kind": "winback_email_copy",
        "status": "draft",
        "source_bindings": {"proposal": proposal.artefact_id,
                            "audit": audit.artefact_id},
        "source_artefact_ids": sorted(
            [proposal.artefact_id, audit.artefact_id]),
        "model_profile": "offline",
        "model": "planted-by-hand",
        "tier": "standard",
        "model_call_id": "CALL-planted",
        "run_id": "planted",
        "content": {"steps": [
            {"subject": "A note", "body": body},
            {"subject": "Still there?",
             "body": "Your favourites are waiting."},
        ]},
        "claims": claims,
        "supersedes": None,
    }
    planted = Artefact(
        artefact_id=artefact_id, kind="draft_email_copy",
        payload=payload, input_fingerprint="planted",
    )
    svc.store.insert_artefact(planted)
    return planted


def test_planted_digit_discount_blocks(tmp_path):
    svc = CommerceService(make_settings(tmp_path))
    svc.generate_dataset()
    svc.audit_email_flows()
    genuine = svc.draft_all()["winback_email_copy"]
    planted = _plant_draft(
        svc, "DCE-planted00000000000001",
        "Take 50% off your next order, on us.",
        genuine.payload["claims"],
    )
    verdict = svc.screen_draft(planted)
    assert "discount_beyond_cap" in verdict.payload["blocking_rules"]
    assert "figure_not_in_claims" in verdict.payload["blocking_rules"]


def test_planted_unresolvable_claim_blocks(tmp_path):
    svc = CommerceService(make_settings(tmp_path))
    svc.generate_dataset()
    svc.audit_email_flows()
    planted = _plant_draft(
        svc, "DCE-planted00000000000002",
        "Your favourites are waiting.",
        [{"value": "12", "source_artefact_id": "PLN-missing",
          "source_field": "summary"}],
    )
    verdict = svc.screen_draft(planted)
    assert "claim_unresolvable" in verdict.payload["blocking_rules"]


# -- forgery -------------------------------------------------------------------


def test_forged_verdict_fails_verify_and_never_opens_the_gate(tmp_path):
    svc, draft = _note_with_prose(
        tmp_path, "wordy-guarantee-2",
        "These results are guaranteed, every single week.",
    )
    blocked_verdict = svc.screen_draft(draft)
    assert blocked_verdict.payload["outcome"] == "block"
    # A genuine pass verdict from another draft, rebound to this
    # one: the hash no longer matches the draft it names.
    svc.generate_dataset()
    clean = svc.draft_all()["plan_narrative"]
    pass_verdict = svc.screen_draft(clean)
    forged_payload = dict(pass_verdict.payload)
    forged_payload["draft_artefact_id"] = draft.artefact_id
    forged = Artefact(
        artefact_id="GRV-forged00000000000001",
        kind="guardian_verdict", payload=forged_payload,
        input_fingerprint="forged",
    )
    svc.store.insert_artefact(forged)
    problems = svc.guardian.verify(forged)
    assert "verdict_draft_hash_mismatch" in problems
    awaiting_ids = {e["draft_artefact_id"] for e in svc.awaiting_approval()}
    assert draft.artefact_id not in awaiting_ids


def test_flipped_rule_outcome_fails_verify(service, drafts):
    verdict = service.guardian.latest_verdict(
        drafts["plan_narrative"].artefact_id)
    payload = json.loads(json.dumps(verdict.payload))
    for rule in payload["rules"]:
        if rule["rule_id"] == "discount_beyond_cap":
            rule["outcome"] = "block"
    payload["blocking_rules"] = ["discount_beyond_cap"]
    payload["outcome"] = "block"
    forged = Artefact(
        artefact_id="GRV-forged00000000000002",
        kind="guardian_verdict", payload=payload,
        input_fingerprint="forged",
    )
    service.store.insert_artefact(forged)
    problems = service.guardian.verify(forged)
    assert "verdict_rule_mismatch:discount_beyond_cap" in problems


# -- re-screening under a changed pack ------------------------------------------


def test_rescreen_under_a_changed_pack(tmp_path, monkeypatch):
    svc, draft = _note_with_prose(
        tmp_path, "wordy-discount-2",
        "Half off the decaying set's next flight, whenever you"
        " are ready.",
    )
    first = svc.screen_draft(draft)
    assert first.payload["outcome"] == "block"
    assert first.payload["blocking_rules"] == ["discount_beyond_cap"]
    pack_v2 = dict(GUARDIAN_PACK, version="2026.10.2",
                   max_discount_percent="50")
    monkeypatch.setitem(guardian_module.GUARDIAN_PACKS,
                        "2026.10.2", pack_v2)
    guardian_v2 = GuardianService(svc.store, svc.model, pack=pack_v2)
    second = guardian_v2.screen(draft)
    assert second.payload["outcome"] == "pass"
    assert second.payload["pack_version"] == "2026.10.2"
    assert second.payload["supersedes"] == first.artefact_id
    # The old verdict stays in the record, still a block under
    # its own pack — and it still re-verifies under that pack.
    stored = svc.store.get_artefact(first.artefact_id)
    assert stored.payload["outcome"] == "block"
    assert svc.guardian.verify(stored) == []
    assert guardian_v2.verify(second) == []


# -- superseded sources ------------------------------------------------------------


def test_draft_over_a_superseded_plan_blocks(tmp_path):
    svc = CommerceService(make_settings(tmp_path))
    svc.generate_dataset()
    plan = svc.assemble_plan()
    narrative = svc.draft_plan_narrative()
    first = svc.screen_draft(narrative)
    assert first.payload["outcome"] == "pass"
    svc.ingest.ingest(
        kind="order", natural_key="order:EXTRA-1:2026-01-05",
        payload={"order_id": "EXTRA-1", "sku": generator.FAST_MOVER_SKU,
                 "date": "2026-01-05", "qty": 40,
                 "unit_price": "64.00", "amount": "2560.00"},
        source_system=generator.SOURCE_ORDERS, as_of="2026-01-05",
    )
    svc.ledger.compute()
    new_plan = svc.assemble_plan()
    assert new_plan.artefact_id != plan.artefact_id
    assert new_plan.payload["supersedes"] == plan.artefact_id
    second = svc.screen_draft(narrative)
    assert second.payload["outcome"] == "block"
    assert "source_superseded" in second.payload["blocking_rules"]
    assert svc.awaiting_approval() == []


# -- surfaces ------------------------------------------------------------------------


def test_guardian_surfaces_api(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    settings = make_settings(tmp_path)
    monkeypatch.setattr(api_module, "_service", None)
    monkeypatch.setattr(api_module, "load_settings", lambda: settings)
    client = TestClient(api_module.app)
    svc = api_module.get_service()
    svc.generate_dataset()
    svc.draft_all()
    svc.screen_all()
    body = client.get("/guardian/verdicts").json()
    assert body["count"] == 3
    awaiting = client.get("/guardian/awaiting-approval").json()
    assert awaiting["count"] == 3
    assert client.get("/guardian/blocked").json()["count"] == 0
    verdict_id = body["verdicts"][0]["artefact_id"]
    checked = client.get(f"/guardian/verdicts/{verdict_id}/verify").json()
    assert checked["verified"] is True
    assert client.get(
        "/guardian/verdicts/GRV-nope/verify").status_code == 404


def test_guardian_surfaces_cli(tmp_path, monkeypatch, capsys):
    settings = make_settings(tmp_path)
    monkeypatch.setenv("SMB_SQLITE_PATH", str(settings.sqlite_path))
    assert cli_main(["generate"]) == 0
    assert cli_main(["draft"]) == 0
    assert cli_main(["screen"]) == 0
    out = capsys.readouterr().out
    assert "VERDICT" in out and "outcome pass" in out
    assert cli_main(["verdicts-verify"]) == 0
    assert cli_main(["awaiting-approval"]) == 0
    out = capsys.readouterr().out
    assert "3 awaiting approval" in out


def test_graph_screens_and_verifies(tmp_path):
    svc = CommerceService(make_settings(tmp_path))
    result = build_graph(svc).invoke({})
    assert result["narrative_verdict_id"].startswith("GRV-")
    assert result["email_copy_verdict_id"].startswith("GRV-")
    assert result["ad_note_verdict_id"].startswith("GRV-")
    assert result["guardian_blocked"] == 0
    assert result["awaiting_approval"] == 3
    assert result["verify_problems"] == []
