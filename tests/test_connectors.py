"""Round 11 grades: exchange contracts, conformance, quarantine.

The conformance suite runs connector doubles against the v2
contracts: a conforming double's batch is accepted; a deliberately
broken double's batch is quarantined WHOLE — no partial ingestion —
with the violation named on a recorded artefact. The dataset still
loads through the contracts, and everything downstream of the
facts comes out byte-identical to the v1 port's record.
"""

from __future__ import annotations

import copy

import pytest

from smb_commerce import generator
from smb_commerce.connectors import (
    CONTRACT_VERSION,
    CONTRACTS,
    ConnectorPort,
    contract_for_pair,
    validate_batch,
)
from smb_commerce.ingest import IngestPort
from smb_commerce.parity import run_core_walk, snapshot
from smb_commerce.service import CommerceService

from conftest import make_settings


def _envelope(contract_name: str, records: list[dict]) -> dict:
    contract = CONTRACTS[contract_name]
    return {
        "contract": contract.name,
        "contract_version": CONTRACT_VERSION,
        "source_system": contract.source_system,
        # Just after the fixture records below — inside every
        # contract's stated lag bound.
        "as_of": "2025-06-20",
        "records": records,
    }


def _expense_record(key: str = "expense:Probe Co:2025-06-14:10.00",
                    **overrides) -> dict:
    record = {
        "kind": "expense",
        "natural_key": key,
        "payload": {"vendor": "Probe Co", "date": "2025-06-14",
                    "category": "software", "amount": "10.00"},
        "source_system": "hartwell-books",
        "as_of": "2025-06-14",
    }
    record.update(overrides)
    return record


def _codes(report) -> set[str]:
    return {v.code for v in report.violations}


# -- the registry -----------------------------------------------------

def test_every_dataset_item_belongs_to_exactly_one_contract():
    for item in generator.build_fact_items():
        assert contract_for_pair(item["kind"], item["source_system"]) \
            is not None


def test_contracts_state_the_four_vendor_shapes():
    shapes = {c.shape for c in CONTRACTS.values()}
    assert {"shopify", "stripe", "quickbooks", "klaviyo"} <= shapes


# -- conforming doubles -------------------------------------------------

def test_conforming_batch_is_accepted(store):
    port = ConnectorPort(store, IngestPort(store))
    report = port.exchange(
        _envelope("books", [_expense_record()]))
    assert report.status == "accepted"
    assert report.accepted == 1
    assert store.count_facts() == 1


def test_generator_batches_all_conform(store):
    batches = generator.build_batches()
    assert {b["contract"] for b in batches} == set(CONTRACTS)
    for envelope in batches:
        assert validate_batch(envelope) == []


def test_dataset_loads_through_the_contracts(service):
    assert service.store.count_facts() == 3954
    assert service.dataset_counts() == generator.manifest_counts()


def test_downstream_is_byte_identical_to_the_v1_record(service, tmp_path):
    board_v2 = run_core_walk(service)

    v1_service = CommerceService(make_settings(tmp_path / "v1"))
    v1_service.ingest.ingest_many(generator.build_fact_items())
    board_v1 = run_core_walk(v1_service)

    snap_v2 = snapshot(service, board_v2).splitlines()
    snap_v1 = snapshot(v1_service, board_v1).splitlines()
    # The facts are the same set (id, hash, payload) — the batch
    # loader groups them by contract, so their insertion order
    # legitimately differs from the v1 port's interleaved order.
    assert sorted(l for l in snap_v2 if l.startswith("FACT ")) \
        == sorted(l for l in snap_v1 if l.startswith("FACT "))
    # Everything the facts produce — artefacts, model calls,
    # migrations, the board — is byte-identical, in order.
    assert [l for l in snap_v2 if not l.startswith("FACT ")] \
        == [l for l in snap_v1 if not l.startswith("FACT ")]


# -- replay ---------------------------------------------------------------

def test_replayed_batch_lands_nothing_twice(store):
    port = ConnectorPort(store, IngestPort(store))
    envelope = _envelope("books", [_expense_record()])
    first = port.exchange(envelope)
    second = port.exchange(envelope)
    assert first.accepted == 1
    assert (second.status, second.duplicates) == ("accepted", 1)
    assert store.count_facts() == 1


# -- broken doubles: quarantined whole, violation named -------------------

def test_missing_provenance_quarantines_the_whole_batch(store):
    port = ConnectorPort(store, IngestPort(store))
    good = _expense_record("expense:Probe Co:2025-06-14:10.00")
    bad = _expense_record("expense:Probe Co:2025-06-15:20.00")
    bad.pop("source_system")
    report = port.exchange(_envelope("books", [good, bad]))
    assert report.status == "quarantined"
    assert "missing_provenance" in _codes(report)
    # Whole-batch: even the conforming record never landed.
    assert store.count_facts() == 0
    artefact = report.quarantine
    assert artefact is not None and artefact.kind == "connector_quarantine"
    assert artefact.payload["record_count"] == 2
    assert artefact.payload["batch_hash"] == report.batch_hash
    assert port.verify(artefact) == []


def test_wrong_payload_type_quarantines(store):
    port = ConnectorPort(store, IngestPort(store))
    record = _expense_record()
    record["payload"] = dict(record["payload"], amount=10.00)  # not a string
    report = port.exchange(_envelope("books", [record]))
    assert report.status == "quarantined"
    assert "invalid_payload" in _codes(report)
    assert store.count_facts() == 0


def test_unknown_contract_version_quarantines(store):
    port = ConnectorPort(store, IngestPort(store))
    envelope = _envelope("books", [_expense_record()])
    envelope["contract_version"] = "1999.1.1"
    report = port.exchange(envelope)
    assert report.status == "quarantined"
    assert "unknown_contract_version" in _codes(report)
    assert store.count_facts() == 0


def test_unknown_contract_quarantines(store):
    port = ConnectorPort(store, IngestPort(store))
    envelope = _envelope("books", [_expense_record()])
    envelope["contract"] = "carrier-pigeon"
    report = port.exchange(envelope)
    assert report.status == "quarantined"
    assert "unknown_contract" in _codes(report)
    assert store.count_facts() == 0


def test_mixed_source_batch_quarantines(store):
    port = ConnectorPort(store, IngestPort(store))
    foreign = _expense_record("expense:Probe Co:2025-06-15:20.00")
    foreign["source_system"] = "hartwell-storefront"
    foreign["kind"] = "order"
    foreign["payload"] = {"order_id": "ORD-X", "sku": "HW-DIN-001",
                          "date": "2025-06-15", "qty": 1,
                          "unit_price": "189.00", "amount": "189.00"}
    report = port.exchange(
        _envelope("books", [_expense_record(), foreign]))
    assert report.status == "quarantined"
    assert "mixed_source_batch" in _codes(report)
    assert store.count_facts() == 0


def test_kind_outside_the_contract_quarantines(store):
    port = ConnectorPort(store, IngestPort(store))
    report = port.exchange(_envelope("books", [{
        "kind": "order",
        "natural_key": "order:HW-DIN-001:2025-06-14",
        "payload": {"order_id": "ORD-X", "sku": "HW-DIN-001",
                    "date": "2025-06-14", "qty": 1,
                    "unit_price": "189.00", "amount": "189.00"},
        "source_system": "hartwell-books",
        "as_of": "2025-06-14",
    }]))
    assert report.status == "quarantined"
    assert "kind_not_in_contract" in _codes(report)
    assert store.count_facts() == 0


def test_stale_as_of_quarantines_both_ways(store):
    port = ConnectorPort(store, IngestPort(store))
    envelope = _envelope("books", [_expense_record()])
    envelope["as_of"] = "2025-06-01"  # precedes its newest record
    report = port.exchange(envelope)
    assert report.status == "quarantined"
    assert "stale_as_of" in _codes(report)

    envelope = _envelope("books", [_expense_record()])
    envelope["as_of"] = "2026-06-01"  # lags beyond the stated 45 days
    report = port.exchange(envelope)
    assert report.status == "quarantined"
    assert "stale_as_of" in _codes(report)
    assert store.count_facts() == 0


def test_quarantined_batch_replay_records_one_artefact(store):
    port = ConnectorPort(store, IngestPort(store))
    envelope = _envelope("books", [_expense_record()])
    envelope["contract_version"] = "1999.1.1"
    first = port.exchange(envelope)
    second = port.exchange(copy.deepcopy(envelope))
    assert first.quarantine.artefact_id == second.quarantine.artefact_id
    assert len(port.quarantines()) == 1
    assert store.count_facts() == 0


def test_forged_quarantine_fails_verification(store):
    port = ConnectorPort(store, IngestPort(store))
    envelope = _envelope("books", [_expense_record()])
    envelope["contract_version"] = "1999.1.1"
    artefact = port.exchange(envelope).quarantine
    from smb_commerce.models import Artefact

    forged_payload = dict(artefact.payload, record_count=99)
    forged = Artefact(artefact_id=artefact.artefact_id,
                      kind=artefact.kind, payload=forged_payload,
                      input_fingerprint=artefact.input_fingerprint)
    assert port.verify(forged) != []


# -- surfaces ---------------------------------------------------------------

def test_quarantine_reads_on_the_service(service):
    broken = copy.deepcopy(generator.build_batches()[-1])
    broken["records"][0].pop("as_of")
    report = service.exchange_batch(broken)
    assert report.status == "quarantined"
    listed = service.quarantines()
    assert [q.artefact_id for q in listed] == [report.quarantine.artefact_id]
    assert service.verify_quarantine(report.quarantine) == []
    # The dataset itself is untouched by the refused batch.
    assert service.store.count_facts() == 3954


def test_connector_reads_on_the_api(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from smb_commerce import api as api_module

    settings = make_settings(tmp_path)
    monkeypatch.setattr(api_module, "_service", None)
    monkeypatch.setattr(api_module, "load_settings", lambda: settings)
    client = TestClient(api_module.app)

    registry = client.get("/connectors").json()
    assert registry["contract_version"] == CONTRACT_VERSION
    assert registry["count"] == len(CONTRACTS)

    envelope = _envelope("books", [_expense_record()])
    accepted = client.post("/connectors/exchange",
                           json={"envelope": envelope})
    assert accepted.status_code == 200
    assert accepted.json()["accepted"] == 1

    envelope["contract_version"] = "1999.1.1"
    refused = client.post("/connectors/exchange",
                          json={"envelope": envelope})
    assert refused.status_code == 422
    quarantine_id = refused.json()["detail"]["quarantine_artefact_id"]

    listed = client.get("/connectors/quarantines").json()
    assert listed["count"] == 1
    one = client.get(f"/connectors/quarantines/{quarantine_id}").json()
    assert one["payload"]["status"] == "quarantined"
    verify = client.get(
        f"/connectors/quarantines/{quarantine_id}/verify").json()
    assert verify["verified"] is True
    assert client.get("/connectors/quarantines/QAR-nope").status_code == 404
