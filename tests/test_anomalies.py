"""Diagnosis pack II: the expense and refund anomaly packs.

Every planted expectation below is hand-computed from the
generator's stated constants, not read back from the code's output:

- PackShip Co charges $349.00 on the 14th of every month and again
  on 2025-11-15 — the only same-vendor, same-amount pair inside the
  7-day window (monthly charges sit 28+ days apart).
- Every vendor charges one constant amount all year, so each
  vendor's median equals that amount and no charge clears 3x it:
  zero outliers.
- First charges over the $1,000.00 threshold: the Studio Lease at
  $2,400.00 (2025-01-01) and Northgate Logistics at $1,250.00
  (2025-01-03). CloudHost ($189.00) and PackShip ($349.00) stay
  under it.
- Refund rate is refund amounts over gross revenue. The Ceramic
  Vase refunds floor(40 x 500/10000) = 2 units a week — 104 units,
  $9,568.00 — against 2,080 units and $191,360.00 gross: exactly
  0.050000, the only SKU over the 0.040000 bar. Every SKU sells at
  least 1,144 units, over the 1,000 minimum, so none is unjudged.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from smb_commerce import api as api_module, generator
from smb_commerce.cli import main as cli_main
from smb_commerce.diagnosis import DIAGNOSIS_PACK, DiagnosisRefused
from smb_commerce.models import Artefact
from smb_commerce.service import CommerceService
from smb_commerce.store import canonical_json
from conftest import make_settings


def test_pack_ii_rules_are_stated_data():
    expenses = DIAGNOSIS_PACK["expense_anomaly"]
    assert expenses["duplicate_window_days"] == 7
    assert expenses["outlier_min_charges"] == 3
    assert expenses["outlier_multiple"] == "3.00"
    assert expenses["new_vendor_threshold"] == "1000.00"
    refunds = DIAGNOSIS_PACK["refund_anomaly"]
    assert refunds["max_refund_rate"] == "0.040000"
    assert refunds["min_units_sold"] == 1000


def _expense_fact_id(service, vendor, day):
    fact = next(
        f for f in service.store.list_facts("expense")
        if f.payload["vendor"] == vendor and f.payload["date"] == day
    )
    return fact.fact_id


def test_duplicate_finding_is_the_planted_packship_pair(service):
    artefact = service.diagnose_expenses()
    assert artefact.kind == "diagnosis_expense_anomaly"
    assert artefact.artefact_id.startswith("DEA-")
    payload = artefact.payload
    assert payload["pack_version"] == DIAGNOSIS_PACK["version"]
    ledger = service.store.latest_artefact("ledger")
    assert payload["ledger_artefact_id"] == ledger.artefact_id
    assert payload["as_of"] == "2026-01-04"
    assert payload["unjudged"] == []
    assert len(payload["findings"]) == 1
    finding = payload["findings"][0]
    assert finding["class"] == "duplicate_charge"
    assert finding["vendor"] == generator.DUPLICATE_VENDOR
    assert finding["amount"] == "349.00"
    assert finding["dates"] == ["2025-11-14", "2025-11-15"]
    assert finding["gap_days"] == 1
    assert finding["window_days"] == 7
    assert finding["fact_ids"] == [
        _expense_fact_id(service, "PackShip Co", "2025-11-14"),
        _expense_fact_id(service, "PackShip Co", "2025-11-15"),
    ]
    assert service.diagnosis.verify(artefact) == []


def test_no_outliers_and_no_other_vendors_named_as_anomalies(service):
    payload = service.diagnose_expenses().payload
    outliers = [f for f in payload["findings"]
                if f["class"] == "amount_outlier"]
    assert outliers == []
    named = {f["vendor"] for f in payload["findings"]}
    assert named == {"PackShip Co"}


def test_new_vendor_first_charges_are_informational_only(service):
    payload = service.diagnose_expenses().payload
    informational = payload["informational"]
    assert [(e["vendor"], e["amount"], e["date"]) for e in informational] == [
        ("Hartwell Studio Lease", "2400.00", "2025-01-01"),
        ("Northgate Logistics", "1250.00", "2025-01-03"),
    ]
    for entry in informational:
        assert entry["class"] == "new_vendor_first_charge"
        assert entry["threshold"] == "1000.00"
        assert entry["fact_id"] == _expense_fact_id(
            service, entry["vendor"], entry["date"])
    # Informational is a separate class: neither vendor is an anomaly.
    anomaly_vendors = {f["vendor"] for f in payload["findings"]}
    assert "Hartwell Studio Lease" not in anomaly_vendors
    assert "Northgate Logistics" not in anomaly_vendors


def test_refund_finding_is_the_planted_vase_rate(service):
    artefact = service.diagnose_refunds()
    assert artefact.kind == "diagnosis_refund_anomaly"
    assert artefact.artefact_id.startswith("DRA-")
    payload = artefact.payload
    assert payload["pack_version"] == DIAGNOSIS_PACK["version"]
    assert payload["unjudged"] == []
    assert len(payload["findings"]) == 1
    finding = payload["findings"][0]
    assert finding["sku"] == "HW-VAS-004"
    assert finding["gross_revenue"] == "191360.00"
    assert finding["refunds"] == "9568.00"
    assert finding["refund_rate"] == "0.050000"
    assert finding["units_sold"] == 2080
    assert finding["units_refunded"] == 104
    assert finding["max_refund_rate"] == "0.040000"
    assert service.diagnosis.verify(artefact) == []


def test_other_skus_are_never_named(service):
    payload = service.diagnose_refunds().payload
    text = canonical_json(payload)
    for spec in generator.SKUS:
        if spec.sku != "HW-VAS-004":
            assert spec.sku not in text


def test_anomaly_diagnoses_are_insert_only_and_idempotent(service):
    first = service.diagnose_expenses()
    assert service.diagnose_expenses().artefact_id == first.artefact_id
    assert len(service.store.list_artefacts("diagnosis_expense_anomaly")) == 1
    refunds = service.diagnose_refunds()
    assert service.diagnose_refunds().artefact_id == refunds.artefact_id
    assert len(service.store.list_artefacts("diagnosis_refund_anomaly")) == 1


def test_prior_pack_version_findings_still_verify(service):
    """A stockout set stamped 2026.10.1 re-performs under its own
    version's rules even though the current pack is 2026.10.2."""
    artefact = service.diagnose_stockout()
    payload = json.loads(canonical_json(artefact.payload))
    payload["pack_version"] = "2026.10.1"
    prior = Artefact(
        artefact_id="DST-priorversion00000000", kind="diagnosis_stockout",
        payload=payload, input_fingerprint=artefact.input_fingerprint,
    )
    service.store.insert_artefact(prior)
    assert service.diagnosis.verify(prior) == []


def _item(kind, key, payload, source, as_of):
    return {"kind": kind, "natural_key": key, "payload": payload,
            "source_system": source, "as_of": as_of}


def _expense(vendor, day, amount):
    return _item(
        "expense", f"expense:{vendor}:{day}:{amount}",
        {"vendor": vendor, "date": day, "category": "ops",
         "amount": amount},
        generator.SOURCE_FINANCE, day)


def test_outlier_unjudged_and_window_edges_on_fixtures(tmp_path):
    service = CommerceService(make_settings(tmp_path))
    items = [
        # PrintWorks: four $100.00 charges and one $950.00 charge.
        # Median is $100.00, so $950.00 clears 3x it — one outlier.
        _expense("PrintWorks", "2025-01-05", "100.00"),
        _expense("PrintWorks", "2025-02-05", "100.00"),
        _expense("PrintWorks", "2025-03-05", "100.00"),
        _expense("PrintWorks", "2025-04-05", "100.00"),
        _expense("PrintWorks", "2025-05-05", "950.00"),
        # TwinBill: the same amount 10 days apart — outside the
        # 7-day window, so no duplicate; two charges only, so the
        # vendor is unjudged for outliers, with the reason named.
        _expense("TwinBill", "2025-03-01", "75.00"),
        _expense("TwinBill", "2025-03-11", "75.00"),
        # CloseBill: the same amount 5 days apart — one duplicate.
        _expense("CloseBill", "2025-06-01", "60.00"),
        _expense("CloseBill", "2025-06-06", "60.00"),
        _expense("CloseBill", "2025-07-01", "60.00"),
    ]
    report = service.ingest.ingest_many(items)
    assert report.refused == 0
    payload = service.diagnose_expenses().payload
    outliers = [f for f in payload["findings"]
                if f["class"] == "amount_outlier"]
    assert len(outliers) == 1
    outlier = outliers[0]
    assert outlier["vendor"] == "PrintWorks"
    assert outlier["amount"] == "950.00"
    assert outlier["date"] == "2025-05-05"
    assert outlier["vendor_median"] == "100.00"
    assert outlier["multiple"] == "3.00"
    assert outlier["charges"] == 5
    assert outlier["fact_id"] == _expense_fact_id(
        service, "PrintWorks", "2025-05-05")
    duplicates = [f for f in payload["findings"]
                  if f["class"] == "duplicate_charge"]
    assert len(duplicates) == 1
    assert duplicates[0]["vendor"] == "CloseBill"
    assert duplicates[0]["dates"] == ["2025-06-01", "2025-06-06"]
    assert duplicates[0]["gap_days"] == 5
    unjudged = {e["vendor"]: e for e in payload["unjudged"]}
    assert set(unjudged) == {"TwinBill"}
    assert unjudged["TwinBill"]["reason"] == "insufficient_charges"
    assert unjudged["TwinBill"]["charges"] == 2
    # No first charge clears the threshold on this fixture.
    assert payload["informational"] == []
    artefact = service.store.latest_artefact("diagnosis_expense_anomaly")
    assert service.diagnosis.verify(artefact) == []


def test_refund_unjudged_skus_are_named_with_reasons(tmp_path):
    service = CommerceService(make_settings(tmp_path))
    items = [
        _item("order", "order:HW-DIN-001:2025-12-20",
              {"order_id": "ORD-001-1", "sku": "HW-DIN-001",
               "date": "2025-12-20", "qty": 10, "unit_price": "189.00",
               "amount": "1890.00"},
              generator.SOURCE_ORDERS, "2025-12-20"),
        _item("refund", "refund:HW-DIN-001:2025-12-15",
              {"sku": "HW-DIN-001", "week_start": "2025-12-15",
               "qty": 5, "amount": "945.00"},
              generator.SOURCE_ORDERS, "2025-12-21"),
    ]
    report = service.ingest.ingest_many(items)
    assert report.refused == 0
    payload = service.diagnose_refunds().payload
    # A 50% refund rate over 10 units is not judged: volume first.
    assert payload["findings"] == []
    reasons = {e["sku"]: e["reason"] for e in payload["unjudged"]}
    assert reasons == {spec.sku: "below_min_volume"
                       for spec in generator.SKUS}
    din = next(e for e in payload["unjudged"] if e["sku"] == "HW-DIN-001")
    assert din["units_sold"] == 10


def test_tampered_expense_fact_refuses_the_finding_set(tmp_path):
    service = CommerceService(make_settings(tmp_path))
    service.generate_dataset()
    service.ledger.compute()
    target = next(
        f for f in service.store.list_facts("expense")
        if f.payload["vendor"] == "PackShip Co"
        and f.payload["date"] == "2025-11-15"
    )
    payload = dict(target.payload)
    payload["amount"] = "350.00"
    service.store._conn.execute(
        "UPDATE facts SET payload = ? WHERE fact_id = ?",
        (json.dumps(payload, sort_keys=True), target.fact_id),
    )
    service.store._conn.commit()
    with pytest.raises(DiagnosisRefused) as caught:
        service.diagnose_expenses()
    refusal = caught.value.refusal
    assert refusal.kind == "diagnosis_refusal"
    assert refusal.payload["diagnosis"] == "expense_anomaly"
    assert "ledger_not_current" in refusal.payload["problems"]
    assert service.store.list_artefacts("diagnosis_expense_anomaly") == []


def test_forged_anomaly_sets_fail_verification(service):
    expenses = service.diagnose_expenses()
    forged_payload = json.loads(canonical_json(expenses.payload))
    forged_payload["findings"] = []
    forged = Artefact(
        artefact_id="DEA-forged00000000000000",
        kind="diagnosis_expense_anomaly",
        payload=forged_payload,
        input_fingerprint=expenses.input_fingerprint,
    )
    service.store.insert_artefact(forged)
    assert "expense_finding_mismatch" in service.diagnosis.verify(forged)

    refunds = service.diagnose_refunds()
    forged_payload = json.loads(canonical_json(refunds.payload))
    forged_payload["findings"][0]["refund_rate"] = "0.010000"
    forged = Artefact(
        artefact_id="DRA-forged00000000000000",
        kind="diagnosis_refund_anomaly",
        payload=forged_payload,
        input_fingerprint=refunds.input_fingerprint,
    )
    service.store.insert_artefact(forged)
    assert "refund_finding_mismatch" in service.diagnosis.verify(forged)


def test_api_anomaly_reads(tmp_path, monkeypatch):
    settings = make_settings(tmp_path)
    monkeypatch.setattr(api_module, "_service", None)
    monkeypatch.setattr(api_module, "load_settings", lambda: settings)
    client = TestClient(api_module.app)
    assert client.get("/diagnosis/expense-anomalies").status_code == 404
    service = api_module.get_service()
    service.generate_dataset()
    service.ledger.compute()
    service.diagnose_expenses()
    service.diagnose_refunds()
    expenses = client.get("/diagnosis/expense-anomalies").json()
    assert expenses["kind"] == "diagnosis_expense_anomaly"
    assert expenses["payload"]["findings"][0]["vendor"] == "PackShip Co"
    verify = client.get("/diagnosis/expense-anomalies/verify").json()
    assert verify["verified"] is True
    refunds = client.get("/diagnosis/refund-anomalies").json()
    assert refunds["payload"]["findings"][0]["sku"] == "HW-VAS-004"
    assert client.get("/diagnosis/refund-anomalies/verify").json()["verified"] is True


def test_cli_diagnose_covers_the_anomaly_packs(tmp_path, monkeypatch, capsys):
    settings = make_settings(tmp_path)
    monkeypatch.setenv("SMB_SQLITE_PATH", str(settings.sqlite_path))
    assert cli_main(["generate"]) == 0
    assert cli_main(["diagnose"]) == 0
    out = capsys.readouterr().out
    assert "DUPLICATE PackShip Co 349.00 on 2025-11-14 + 2025-11-15" in out
    assert "NEW-VENDOR Hartwell Studio Lease" in out
    assert "NEW-VENDOR Northgate Logistics" in out
    assert "REFUND HW-VAS-004" in out
    assert "rate 0.050000" in out
    assert cli_main(["diagnose-verify"]) == 0
    verify_out = capsys.readouterr().out
    assert verify_out.count('"verified": true') == 5
