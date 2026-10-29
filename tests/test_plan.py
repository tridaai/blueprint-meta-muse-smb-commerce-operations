"""Round 5 grades: the growth plan artefact against the planted
truth, hand-computed from the plan pack's stated rule.

Family order first — stockout, ad decay, expense, refund, audit —
then exposure descending inside a family, then the finding key.
On the Hartwell record that is: the throw (280 trailing units),
Social (26,000.00 of spend), the PackShip duplicate (349.00), the
vase (9,568.00 refunded), and the missing winback flow (structural,
exposure 0). Plus the discipline: figures re-resolve against the
ledger, a refused diagnosis blocks the plan whole, a moved record
supersedes by link, and an empty family is named, never absent.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from smb_commerce import api as api_module
from smb_commerce import generator
from smb_commerce.cli import main as cli_main
from smb_commerce.diagnosis import DIAGNOSIS_PACK
from smb_commerce.graph import build_graph
from smb_commerce.models import Artefact
from smb_commerce.plan import PLAN_PACK, PlanRefused, _derive, _derive_route2
from smb_commerce.service import CommerceService
from smb_commerce.store import canonical_json
from conftest import make_settings


def _ranked(plan):
    return [e for e in plan.payload["priorities"] if e["rank"] is not None]


def _coverage(plan):
    return [e for e in plan.payload["priorities"] if e["rank"] is None]


def test_plan_on_dataset_matches_hand_computed_order(service):
    plan = service.assemble_plan()
    p = plan.payload
    assert plan.kind == "growth_plan"
    assert plan.artefact_id.startswith("PLN-")
    assert p["pack_version"] == PLAN_PACK["version"]
    assert p["diagnosis_pack_version"] == "2026.10.3"
    assert p["performs_actions"] is False
    assert p["supersedes"] is None

    ledger = service.store.get_artefact(p["ledger_artefact_id"])
    for figure in p["summary"]["figures"]:
        assert figure["source_artefact_id"] == ledger.artefact_id
        assert figure["value"] == ledger.payload[figure["source_field"]]

    ranked = _ranked(plan)
    assert [e["family"] for e in ranked] == [
        "stockout_risk", "ad_decay", "expense_anomaly",
        "refund_anomaly", "email_flow_audit",
    ]
    assert [e["rank"] for e in ranked] == [1, 2, 3, 4, 5]
    assert ranked[0]["finding_key"] == generator.FAST_MOVER_SKU
    assert ranked[0]["exposure"] == "280"
    assert ranked[0]["exposure_field"] == "trailing_units"
    assert ranked[1]["finding_key"] == generator.DECAY_SET
    assert ranked[1]["exposure"] == "26000.00"
    assert ranked[2]["finding_key"].startswith(
        f"{generator.DUPLICATE_VENDOR}|duplicate_charge|")
    assert ranked[2]["exposure"] == "349.00"
    assert ranked[3]["finding_key"] == "HW-VAS-004"
    assert ranked[3]["exposure"] == "9568.00"
    assert ranked[4]["finding_key"] == "winback|missing_flow"
    assert ranked[4]["exposure"] == "0"
    assert ranked[4]["exposure_field"] is None
    assert _coverage(plan) == []

    actions = p["actions"]
    assert [a["rank"] for a in actions] == [1, 2, 3, 4, 5]
    assert all(a["performed"] is False for a in actions)
    proposals = service.store.list_artefacts("draft_flow_proposal")
    assert len(proposals) == 1
    assert actions[4]["proposal_artefact_id"] == proposals[0].artefact_id
    assert all(
        a["proposal_artefact_id"] is None for a in actions[:4]
    )
    assert service.plan.verify(plan) == []


def test_plan_reassembly_is_idempotent(service):
    first = service.assemble_plan()
    second = service.assemble_plan()
    assert second.artefact_id == first.artefact_id
    assert len(service.store.list_artefacts("growth_plan")) == 1


def test_plan_supersedes_by_link_when_the_record_moves(service):
    first = service.assemble_plan()
    service.ingest.ingest(
        kind="expense", natural_key="expense:NewVendor:2026-01-02:10.00",
        payload={"vendor": "NewVendor", "date": "2026-01-02",
                 "category": "ops", "amount": "10.00"},
        source_system=generator.SOURCE_FINANCE, as_of="2026-01-02",
    )
    second = service.assemble_plan()
    assert second.artefact_id != first.artefact_id
    assert second.payload["supersedes"] == first.artefact_id
    assert first.payload["supersedes"] is None
    assert len(service.store.list_artefacts("growth_plan")) == 2
    assert service.plan.verify(second) == []


def test_refused_diagnosis_blocks_the_plan_wholesale(tmp_path):
    service = CommerceService(make_settings(tmp_path))
    service.generate_dataset()
    service.ledger.compute()
    target = next(
        f for f in service.store.list_facts("order")
        if f.payload["sku"] == generator.FAST_MOVER_SKU
        and f.payload["date"] >= "2025-12-08"
    )
    payload = dict(target.payload)
    payload["qty"] = int(payload["qty"]) + 1
    service.store._conn.execute(
        "UPDATE facts SET payload = ? WHERE fact_id = ?",
        (json.dumps(payload, sort_keys=True), target.fact_id),
    )
    service.store._conn.commit()
    with pytest.raises(PlanRefused) as caught:
        service.assemble_plan()
    refusal = caught.value.refusal
    assert refusal.kind == "plan_refusal"
    assert refusal.artefact_id.startswith("PRF-")
    assert refusal.payload["blocking_refusals"]
    assert all(
        problem.startswith("diagnosis_refused:")
        for problem in refusal.payload["problems"]
    )
    named = {
        problem.split(":")[1] for problem in refusal.payload["problems"]
    }
    assert named == set(PLAN_PACK["family_order"])
    for diagnosis_refusal_id in refusal.payload["blocking_refusals"]:
        assert diagnosis_refusal_id.startswith("DRF-")
        assert service.store.get_artefact(diagnosis_refusal_id) is not None
    assert service.store.list_artefacts("growth_plan") == []
    assert service.plan.verify(refusal) == []


def test_forged_plan_figure_fails_verification(service):
    plan = service.assemble_plan()
    forged_payload = json.loads(canonical_json(plan.payload))
    forged_payload["summary"]["figures"][0]["value"] = "1.00"
    forged = Artefact(
        artefact_id="PLN-forged00000000000000", kind="growth_plan",
        payload=forged_payload, input_fingerprint=plan.input_fingerprint,
    )
    service.store.insert_artefact(forged)
    problems = service.plan.verify(forged)
    assert "plan_figure_mismatch:gross_revenue" in problems


def test_forged_plan_ranking_fails_verification(service):
    plan = service.assemble_plan()
    forged_payload = json.loads(canonical_json(plan.payload))
    ranked = [e for e in forged_payload["priorities"] if e["rank"] is not None]
    ranked[0], ranked[1] = ranked[1], ranked[0]
    ranked[0]["rank"], ranked[1]["rank"] = 1, 2
    forged_payload["priorities"] = ranked
    forged = Artefact(
        artefact_id="PLN-forged00000000000001", kind="growth_plan",
        payload=forged_payload, input_fingerprint=plan.input_fingerprint,
    )
    service.store.insert_artefact(forged)
    problems = service.plan.verify(forged)
    assert "plan_priorities_mismatch" in problems


def test_plan_citing_a_superseded_diagnosis_set_fails(service):
    first = service.assemble_plan()
    old_stockout_id = next(
        e["diagnosis_artefact_id"] for e in first.payload["priorities"]
        if e["family"] == "stockout_risk"
    )
    service.ingest.ingest(
        kind="expense", natural_key="expense:OtherVendor:2026-01-03:20.00",
        payload={"vendor": "OtherVendor", "date": "2026-01-03",
                 "category": "ops", "amount": "20.00"},
        source_system=generator.SOURCE_FINANCE, as_of="2026-01-03",
    )
    second = service.assemble_plan()
    assert second.artefact_id != first.artefact_id
    forged_payload = json.loads(canonical_json(second.payload))
    for entry in forged_payload["priorities"]:
        if entry["family"] == "stockout_risk":
            entry["diagnosis_artefact_id"] = old_stockout_id
    for action in forged_payload["actions"]:
        if action["family"] == "stockout_risk":
            action["diagnosis_artefact_id"] = old_stockout_id
    forged = Artefact(
        artefact_id="PLN-forged00000000000002", kind="growth_plan",
        payload=forged_payload, input_fingerprint=second.input_fingerprint,
    )
    service.store.insert_artefact(forged)
    problems = service.plan.verify(forged)
    assert "citation_not_bound_to_ledger:stockout_risk" in problems


def _fixture_service(tmp_path) -> CommerceService:
    """Two stockout SKUs with different trailing units, every
    catalogue flow defined and healthy, and nothing else: three
    families judge empty, and the stockouts must order by exposure."""
    service = CommerceService(make_settings(tmp_path))
    items = [
        {"kind": "order", "natural_key": "order:HW-THR-002:2025-12-20",
         "payload": {"order_id": "F-1", "sku": "HW-THR-002",
                     "date": "2025-12-20", "qty": 280,
                     "unit_price": "64.00", "amount": "17920.00"},
         "source_system": generator.SOURCE_ORDERS, "as_of": "2025-12-20"},
        {"kind": "inventory", "natural_key": "inventory:HW-THR-002:2025-12-29",
         "payload": {"sku": "HW-THR-002", "week_start": "2025-12-29",
                     "on_hand": 210},
         "source_system": generator.SOURCE_INVENTORY, "as_of": "2025-12-29"},
        {"kind": "order", "natural_key": "order:HW-CAN-003:2025-12-20",
         "payload": {"order_id": "F-2", "sku": "HW-CAN-003",
                     "date": "2025-12-20", "qty": 140,
                     "unit_price": "38.00", "amount": "5320.00"},
         "source_system": generator.SOURCE_ORDERS, "as_of": "2025-12-20"},
        {"kind": "inventory", "natural_key": "inventory:HW-CAN-003:2025-12-29",
         "payload": {"sku": "HW-CAN-003", "week_start": "2025-12-29",
                     "on_hand": 100},
         "source_system": generator.SOURCE_INVENTORY, "as_of": "2025-12-29"},
    ]
    items += [
        {"kind": "email_flow", "natural_key": f"flow:{entry['flow_key']}",
         "payload": {"flow_key": entry["flow_key"], "name": entry["name"],
                     "status": "active",
                     "steps": entry["expected_steps"]},
         "source_system": generator.SOURCE_EMAIL, "as_of": "2026-01-04"}
        for entry in DIAGNOSIS_PACK["email_flow_audit"]["catalogue"]
    ]
    service.ingest.ingest_many(items)
    return service


def test_fixture_plan_orders_by_exposure_and_names_empty_families(tmp_path):
    service = _fixture_service(tmp_path)
    plan = service.assemble_plan()
    ranked = _ranked(plan)
    assert [(e["family"], e["finding_key"], e["exposure"]) for e in ranked] == [
        ("stockout_risk", "HW-THR-002", "280"),
        ("stockout_risk", "HW-CAN-003", "140"),
    ]
    coverage = _coverage(plan)
    assert [e["family"] for e in coverage] == [
        "ad_decay", "expense_anomaly", "refund_anomaly", "email_flow_audit",
    ]
    assert all(
        e["status"] == "no_findings"
        and e["judged_by_pack_version"] == "2026.10.3"
        for e in coverage
    )
    assert service.plan.verify(plan) == []


def test_ties_break_by_finding_key_in_both_routes():
    def fake_set(family, findings):
        return Artefact(
            artefact_id=f"FAKE-{family}", kind="diagnosis_stockout",
            payload={"diagnosis": family, "pack_version": "2026.10.3",
                     "findings": findings},
            input_fingerprint="x",
        )

    sets = {
        "stockout_risk": fake_set("stockout_risk", [
            {"sku": "HW-THR-002", "trailing_units": 100},
            {"sku": "HW-CAN-003", "trailing_units": 100},
        ]),
        "ad_decay": fake_set("ad_decay", []),
        "expense_anomaly": fake_set("expense_anomaly", []),
        "refund_anomaly": fake_set("refund_anomaly", []),
        "email_flow_audit": fake_set("email_flow_audit", []),
    }
    route1 = _derive(sets, {}, PLAN_PACK)
    route2 = _derive_route2(sets, {}, PLAN_PACK)
    assert route1 == route2
    keys = [e["finding_key"] for e in route1[0] if e["rank"] is not None]
    assert keys == ["HW-CAN-003", "HW-THR-002"]


def test_api_plan_reads(tmp_path, monkeypatch):
    settings = make_settings(tmp_path)
    monkeypatch.setattr(api_module, "_service", None)
    monkeypatch.setattr(api_module, "load_settings", lambda: settings)
    client = TestClient(api_module.app)
    assert client.get("/plan").status_code == 404
    service = api_module.get_service()
    service.generate_dataset()
    plan = service.assemble_plan()
    body = client.get("/plan").json()
    assert body["kind"] == "growth_plan"
    assert body["artefact_id"] == plan.artefact_id
    assert client.get("/plan/verify").json()["verified"] is True
    assert client.get("/plan/refusals").json()["count"] == 0


def test_cli_plan_commands(tmp_path, monkeypatch, capsys):
    settings = make_settings(tmp_path)
    monkeypatch.setenv("SMB_SQLITE_PATH", str(settings.sqlite_path))
    assert cli_main(["generate"]) == 0
    assert cli_main(["plan"]) == 0
    out = capsys.readouterr().out
    assert "PRIORITY 1 stockout_risk HW-THR-002" in out
    assert "PRIORITY 5 email_flow_audit winback|missing_flow" in out
    assert cli_main(["plan-verify"]) == 0
    assert cli_main(["plan-refusals"]) == 0
    assert "0 plan refusals" in capsys.readouterr().out


def test_graph_assembles_and_verifies_the_plan(tmp_path):
    service = CommerceService(make_settings(tmp_path))
    result = build_graph(service).invoke({})
    assert result["plan_artefact_id"].startswith("PLN-")
    assert result["plan_priorities"] == 5
    assert result["verify_problems"] == []
