"""Round 9: the morning board — the opening view re-derived
from the record.

The board is a fold, not a record: these tests grade that it
composes the current findings (figures copied from the finding
artefacts, in the plan's order), calls out the stock risks,
shows the approval picture exactly as the queue folds it
(awaiting behind the guardian's gate, approved reading
"approved — not sent" with stale approvals marked, rejected
with their reasons, blocked with their rules); that it pins
the heads it folded; that it is never stored; and that a
tampered or forged board fails verification end to end.
"""

from __future__ import annotations

import copy
import json

import pytest

from smb_commerce import api as api_module
from smb_commerce import generator
from smb_commerce.board import BOARD_KIND
from smb_commerce.cli import main as cli_main
from smb_commerce.models import Artefact
from smb_commerce.plan import PLAN_PACK
from smb_commerce.service import CommerceService

from conftest import make_settings

APPROVER = "Maya Chen"


@pytest.fixture(scope="module")
def populated(service):
    """The record an operator opens onto: the three clean drafts
    screened, one planted bad draft blocked at the guardian, one
    extra clean draft planted and screened; then two approvals
    and one rejection recorded by a named human."""
    service.ledger.compute()
    plan = service.assemble_plan()
    drafts = service.draft_all()
    service.screen_all()
    genuine = drafts["winback_email_copy"]
    proposal_id = genuine.payload["source_bindings"]["proposal"]
    audit_id = genuine.payload["source_bindings"]["audit"]

    bad_payload = copy.deepcopy(genuine.payload)
    bad_payload["content"] = {"steps": [
        {"subject": "A treat, on us",
         "body": "Take 50% off your next order — results"
                 " guaranteed, unlike the shoddy throws at"
                 " BrightNest Living."},
        {"subject": "Still thinking it over?",
         "body": "Your favourites are waiting."},
    ]}
    bad = Artefact(
        artefact_id="DCE-plantedbad000000009",
        kind="draft_email_copy", payload=bad_payload,
        input_fingerprint="planted",
    )
    service.store.insert_artefact(bad)
    bad_verdict = service.screen_draft(bad)
    assert bad_verdict.payload["outcome"] == "block"

    extra_payload = copy.deepcopy(genuine.payload)
    extra_payload["content"] = {"steps": [
        {"subject": "A note from Hartwell Home Goods",
         "body": "Your favourites are waiting, whenever you are"
                 " ready to look again."},
        {"subject": "Still there?",
         "body": "Your favourites are waiting."},
    ]}
    extra = Artefact(
        artefact_id="DCE-plantedclean00000009",
        kind="draft_email_copy", payload=extra_payload,
        input_fingerprint="planted-clean",
    )
    service.store.insert_artefact(extra)
    service.screen_draft(extra)

    acts = {
        "plan_narrative": service.approve_draft(
            drafts["plan_narrative"].artefact_id, APPROVER),
        "winback_email_copy": service.approve_draft(
            drafts["winback_email_copy"].artefact_id, APPROVER),
    }
    rejection = service.reject_draft(
        extra.artefact_id, APPROVER, "timing",
        "Hold until the spring catalogue lands.")
    return {
        "plan": plan, "drafts": drafts, "bad": bad, "extra": extra,
        "acts": acts, "rejection": rejection,
    }


def test_board_composes_the_opening_view(service, populated):
    board = service.derive_board()
    p = board.payload
    assert board.kind == BOARD_KIND
    ledger = service.store.latest_artefact("ledger")
    assert p["ledger_artefact_id"] == ledger.artefact_id
    assert p["as_of"] == ledger.payload["as_of"]
    # Headline: the ledger's own figures, cited field by field.
    figures = p["headline"]["figures"]
    assert [f["name"] for f in figures] == PLAN_PACK["summary_figures"]
    for figure in figures:
        assert figure["source_artefact_id"] == ledger.artefact_id
        assert figure["value"] == ledger.payload[figure["source_field"]]
    # The approval picture, exactly as the queue folds it.
    counts = p["headline"]["counts"]
    assert counts == {
        "findings": 5, "coverage_families": 0, "stock_risks": 1,
        "awaiting_approval": 1, "approved": 2, "approved_stale": 0,
        "rejected": 1, "blocked": 1, "drafts_total": 5,
        "unscreened": 0,
    }
    queue = service.approval_queue()
    assert [e["draft_artefact_id"] for e in p["awaiting_approval"]] == [
        e["draft_artefact_id"] for e in queue["awaiting_approval"]]
    assert [e["draft_artefact_id"] for e in p["awaiting_approval"]] == [
        populated["drafts"]["ad_budget_note"].artefact_id]
    assert {e["draft_artefact_id"] for e in p["approved"]} == {
        d.artefact_id for d in (
            populated["drafts"]["plan_narrative"],
            populated["drafts"]["winback_email_copy"])}
    for entry in p["approved"]:
        assert entry["display_status"] == "approved — not sent"
        assert entry["execution_status"] == "not_sent"
        assert entry["stale"] is False
        assert entry["moved_inputs"] == []
        assert entry["approver"] == APPROVER
    assert [e["draft_artefact_id"] for e in p["rejected"]] == [
        populated["extra"].artefact_id]
    assert p["rejected"][0]["reason_code"] == "timing"
    assert [e["draft_artefact_id"] for e in p["blocked"]] == [
        populated["bad"].artefact_id]
    assert "discount_beyond_cap" in p["blocked"][0]["blocking_rules"]
    # The pins name the heads the board folded.
    pins = p["pins"]
    assert pins["ledger_head"] == ledger.artefact_id
    assert pins["plan_head"] == populated["plan"].artefact_id
    for entry in populated["plan"].payload["priorities"]:
        assert pins["diagnosis_heads"][entry["family"]] == (
            entry["diagnosis_artefact_id"])
    note = populated["drafts"]["ad_budget_note"]
    verdict = service.guardian.latest_verdict(note.artefact_id)
    assert pins["verdict_heads"][note.artefact_id] == verdict.artefact_id
    assert pins["approval_head"] == (
        populated["acts"]["winback_email_copy"].artefact_id)
    assert pins["rejection_head"] == populated["rejection"].artefact_id


def test_board_findings_carry_the_finding_figures(service, populated):
    board = service.derive_board()
    p = board.payload
    ranked = [e for e in p["findings"] if e["rank"] is not None]
    plan_ranked = [
        e for e in populated["plan"].payload["priorities"]
        if e["rank"] is not None
    ]
    assert [e["finding_key"] for e in ranked] == [
        e["finding_key"] for e in plan_ranked]
    for entry in ranked:
        finding_set = service.store.get_artefact(
            entry["diagnosis_artefact_id"])
        assert finding_set is not None
        match = next(
            f for f in finding_set.payload["findings"]
            if all(f.get(field) == value
                   for field, value in entry["figures"].items())
        )
        assert match is not None
        assert entry["exposure"] == next(
            e["exposure"] for e in plan_ranked
            if e["finding_key"] == entry["finding_key"])
    # The stock risk is the planted fast mover, with its figures.
    assert len(p["stock_risks"]) == 1
    risk = p["stock_risks"][0]
    assert risk["sku"] == generator.FAST_MOVER_SKU
    assert risk["on_hand"] == 210
    assert risk["cover_days"] == "21.00"
    assert risk["cover_out_date"] == "2026-01-25"
    assert risk["trailing_velocity_per_day"] == "10.00"


def test_board_is_rederived_and_never_stored(service, populated):
    first = service.derive_board()
    second = service.derive_board()
    assert first.artefact_id == second.artefact_id
    assert first.payload == second.payload
    assert service.morning_board() == first.payload
    assert service.store.list_artefacts(BOARD_KIND) == []


def test_board_verifies_clean(service, populated):
    board = service.derive_board()
    assert service.board.verify(board) == []


def test_tampered_board_fails_verification(service, populated):
    board = service.derive_board()

    def tampered_with(mutate):
        payload = copy.deepcopy(board.payload)
        mutate(payload)
        return Artefact(
            artefact_id=board.artefact_id, kind=BOARD_KIND,
            payload=payload, input_fingerprint="tampered")

    def change_headline(payload):
        payload["headline"]["figures"][0]["value"] = "1.00"

    problems = service.board.verify(tampered_with(change_headline))
    assert "board_figure_mismatch:headline:gross_revenue" in problems
    assert "board_headline_mismatch" in problems

    def change_cover(payload):
        for entry in payload["findings"]:
            if entry.get("family") == "stockout_risk":
                entry["figures"]["cover_days"] = "99.00"

    problems = service.board.verify(tampered_with(change_cover))
    assert (f"board_figure_mismatch:{generator.FAST_MOVER_SKU}"
            f":cover_days") in problems
    assert "board_section_mismatch:findings" in problems

    def drop_awaiting(payload):
        payload["awaiting_approval"] = []

    problems = service.board.verify(tampered_with(drop_awaiting))
    assert "board_section_mismatch:awaiting_approval" in problems

    def claim_sent(payload):
        payload["approved"][0]["display_status"] = "sent"

    problems = service.board.verify(tampered_with(claim_sent))
    assert "board_approved_display_status_wrong" in problems
    assert "board_section_mismatch:approved" in problems

    def move_pin(payload):
        payload["pins"]["ledger_head"] = "LED-forged"

    problems = service.board.verify(tampered_with(move_pin))
    assert "board_pin_mismatch:ledger_head" in problems

    def change_count(payload):
        payload["headline"]["counts"]["findings"] = 99

    problems = service.board.verify(tampered_with(change_count))
    assert "board_headline_mismatch" in problems


def test_forged_board_and_wrong_kind_fail_verification(
        service, populated):
    board = service.derive_board()
    forged_payload = copy.deepcopy(board.payload)
    forged_payload["findings"] = []
    forged_payload["stock_risks"] = []
    forged = Artefact(
        artefact_id="BRD-forged0000000000009", kind=BOARD_KIND,
        payload=forged_payload, input_fingerprint="forged")
    problems = service.board.verify(forged)
    assert "board_section_mismatch:findings" in problems
    assert "board_section_mismatch:stock_risks" in problems
    plan = service.store.latest_artefact("growth_plan")
    assert service.board.verify(plan) == ["not_a_board:growth_plan"]


def test_board_marks_a_stale_approval_and_the_old_board_fails(
        tmp_path):
    svc = CommerceService(make_settings(tmp_path))
    svc.generate_dataset()
    narrative = svc.draft_all()["plan_narrative"]
    svc.screen_all()
    svc.approve_draft(narrative.artefact_id, APPROVER)
    board = svc.derive_board()
    assert board.payload["approved"][0]["stale"] is False
    assert svc.board.verify(board) == []
    # Move the record under the approval: a new ledger and a
    # plan that supersedes the one the narrative drafts from.
    svc.ingest.ingest(
        kind="order", natural_key="order:EXTRA-9:2026-01-05",
        payload={"order_id": "EXTRA-9", "sku": generator.FAST_MOVER_SKU,
                 "date": "2026-01-05", "qty": 40,
                 "unit_price": "64.00", "amount": "2560.00"},
        source_system=generator.SOURCE_ORDERS, as_of="2026-01-05",
    )
    svc.ledger.compute()
    svc.assemble_plan()
    moved = svc.derive_board()
    entry = next(
        e for e in moved.payload["approved"]
        if e["draft_artefact_id"] == narrative.artefact_id)
    assert entry["stale"] is True
    assert entry["moved_inputs"]
    assert entry["display_status"] == "approved — not sent"
    assert svc.board.verify(moved) == []
    # The board folded before the move no longer re-derives.
    problems = svc.board.verify(board)
    assert problems
    assert any(p.startswith("board_pin_mismatch") for p in problems)


def test_board_surfaces_api(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    settings = make_settings(tmp_path)
    monkeypatch.setattr(api_module, "_service", None)
    monkeypatch.setattr(api_module, "load_settings", lambda: settings)
    client = TestClient(api_module.app)
    assert client.get("/board").status_code == 404
    svc = api_module.get_service()
    svc.generate_dataset()
    svc.draft_all()
    svc.screen_all()
    body = client.get("/board").json()
    assert body["kind"] == BOARD_KIND
    assert body["payload"]["headline"]["counts"][
        "awaiting_approval"] == 3
    assert body["payload"]["stock_risks"][0]["sku"] == (
        generator.FAST_MOVER_SKU)
    checked = client.get("/board/verify").json()
    assert checked["verified"] is True
    assert checked["problems"] == []


def test_board_surfaces_cli(tmp_path, monkeypatch, capsys):
    settings = make_settings(tmp_path)
    monkeypatch.setenv("SMB_SQLITE_PATH", str(settings.sqlite_path))
    assert cli_main(["generate"]) == 0
    assert cli_main(["draft"]) == 0
    assert cli_main(["screen"]) == 0
    assert cli_main(["board"]) == 0
    out = capsys.readouterr().out
    assert "BOARD" in out
    assert "3 awaiting approval" in out
    assert "STOCK RISK" in out
    assert generator.FAST_MOVER_SKU in out
    assert cli_main(["board-verify"]) == 0
    out = capsys.readouterr().out
    assert '"verified": true' in out
    assert json.dumps({"ok": True})
