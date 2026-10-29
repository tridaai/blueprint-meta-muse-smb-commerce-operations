"""Diagnosis packs I: the planted findings, the no-data and unjudged
discipline, and the refusal of a finding set that does not
re-perform."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from smb_commerce import api as api_module, generator
from smb_commerce.cli import main as cli_main
from smb_commerce.diagnosis import DIAGNOSIS_PACK, DiagnosisRefused
from smb_commerce.graph import build_graph
from smb_commerce.models import Artefact
from smb_commerce.service import CommerceService
from smb_commerce.store import canonical_json
from conftest import make_settings


def test_pack_is_versioned_data_with_stated_thresholds():
    assert DIAGNOSIS_PACK["version"] == "2026.10.3"
    stockout = DIAGNOSIS_PACK["stockout"]
    assert stockout["velocity_window_days"] == 28
    assert stockout["max_cover_days"] == "24.00"
    decay = DIAGNOSIS_PACK["ad_decay"]
    assert decay["min_total_spend"] == "2600.00"
    assert decay["min_weeks"] == 8
    assert decay["decay_flag_max_factor"] == "0.5000"
    assert decay["steady_band_min_factor"] == "0.9000"


def test_prior_pack_version_is_kept_verbatim():
    from smb_commerce.diagnosis import DIAGNOSIS_PACKS

    prior = DIAGNOSIS_PACKS["2026.10.1"]
    assert prior["stockout"] == DIAGNOSIS_PACK["stockout"]
    assert prior["ad_decay"] == DIAGNOSIS_PACK["ad_decay"]
    assert "expense_anomaly" not in prior
    assert "refund_anomaly" not in prior


def test_stockout_finding_is_the_planted_fast_mover(service):
    artefact = service.diagnose_stockout()
    assert artefact.kind == "diagnosis_stockout"
    assert artefact.artefact_id.startswith("DST-")
    payload = artefact.payload
    assert payload["pack_version"] == DIAGNOSIS_PACK["version"]
    ledger = service.store.latest_artefact("ledger")
    assert payload["ledger_artefact_id"] == ledger.artefact_id
    assert payload["as_of"] == "2026-01-04"
    assert payload["no_data"] == []
    assert len(payload["findings"]) == 1
    finding = payload["findings"][0]
    assert finding["sku"] == generator.FAST_MOVER_SKU
    assert finding["on_hand"] == 210
    assert finding["cover_days"] == "21.00"
    assert finding["cover_out_date"] == "2026-01-25"
    assert finding["trailing_units"] == 280
    assert finding["trailing_velocity_per_day"] == "10.00"
    assert finding["velocity_window_days"] == 28
    assert service.diagnosis.verify(artefact) == []


def test_adequate_skus_are_never_named(service):
    payload = service.diagnose_stockout().payload
    text = canonical_json(payload)
    for spec in generator.SKUS:
        if spec.sku != generator.FAST_MOVER_SKU:
            assert spec.sku not in text


def test_decay_finding_is_the_planted_social_set(service):
    artefact = service.diagnose_ad_decay()
    assert artefact.kind == "diagnosis_ad_decay"
    assert artefact.artefact_id.startswith("DAD-")
    payload = artefact.payload
    assert payload["pack_version"] == DIAGNOSIS_PACK["version"]
    assert payload["unjudged"] == []
    assert len(payload["findings"]) == 1
    finding = payload["findings"][0]
    assert finding["ad_set"] == generator.DECAY_SET
    assert finding["first_week"] == "2025-01-06"
    assert finding["first_week_roas"] == "4.0000"
    assert finding["latest_week"] == "2025-12-29"
    assert finding["latest_week_roas"] == "0.1704"
    assert finding["decay_factor"] == "0.0426"
    assert finding["weeks"] == 52
    assert finding["total_spend"] == "26000.00"
    assert service.diagnosis.verify(artefact) == []


def test_steady_ad_sets_are_never_named(service):
    text = canonical_json(service.diagnose_ad_decay().payload)
    for ad_set in generator.STEADY_AD_REVENUE:
        assert ad_set not in text


def test_diagnoses_are_insert_only_and_idempotent(service):
    first = service.diagnose_stockout()
    second = service.diagnose_stockout()
    assert first.artefact_id == second.artefact_id
    assert len(service.store.list_artefacts("diagnosis_stockout")) == 1
    decay = service.diagnose_ad_decay()
    assert service.diagnose_ad_decay().artefact_id == decay.artefact_id
    assert len(service.store.list_artefacts("diagnosis_ad_decay")) == 1


def _item(kind, key, payload, source, as_of):
    return {"kind": kind, "natural_key": key, "payload": payload,
            "source_system": source, "as_of": as_of}


def test_no_data_skus_read_no_data_never_risk(tmp_path):
    service = CommerceService(make_settings(tmp_path))
    items = [
        # HW-CAN-003: stock and healthy sales — adequate, never named.
        _item("inventory", "inventory:HW-CAN-003:2025-12-29",
              {"sku": "HW-CAN-003", "week_start": "2025-12-29", "on_hand": 340},
              generator.SOURCE_INVENTORY, "2026-01-04"),
        _item("order", "order:HW-CAN-003:2025-12-20",
              {"order_id": "ORD-003-1", "sku": "HW-CAN-003",
               "date": "2025-12-20", "qty": 340, "unit_price": "38.00",
               "amount": "12920.00"},
              generator.SOURCE_ORDERS, "2025-12-20"),
        # HW-VAS-004: a position but no sales in the window.
        _item("inventory", "inventory:HW-VAS-004:2025-12-29",
              {"sku": "HW-VAS-004", "week_start": "2025-12-29", "on_hand": 100},
              generator.SOURCE_INVENTORY, "2026-01-04"),
        # HW-TOW-005: sales but no inventory position at all.
        _item("order", "order:HW-TOW-005:2025-12-20",
              {"order_id": "ORD-005-1", "sku": "HW-TOW-005",
               "date": "2025-12-20", "qty": 10, "unit_price": "56.00",
               "amount": "560.00"},
              generator.SOURCE_ORDERS, "2025-12-20"),
    ]
    report = service.ingest.ingest_many(items)
    assert report.refused == 0
    payload = service.diagnose_stockout().payload
    assert payload["findings"] == []
    reasons = {entry["sku"]: entry["reason"] for entry in payload["no_data"]}
    assert reasons == {
        "HW-DIN-001": "no_inventory_position",
        "HW-THR-002": "no_inventory_position",
        "HW-VAS-004": "no_sales_in_window",
        "HW-TOW-005": "no_inventory_position",
        "HW-MIR-006": "no_inventory_position",
    }
    assert "HW-CAN-003" not in canonical_json(payload)


def test_unjudged_ad_sets_are_named_with_reasons(tmp_path):
    service = CommerceService(make_settings(tmp_path))
    items = []
    # AD-TINY: a full year of history but spend far below the bar.
    for week in range(generator.WEEKS):
        monday = generator.week_start(week)
        items.append(_item(
            "ad_daily", f"ad:AD-TINY:{monday.isoformat()}",
            {"ad_set": "AD-TINY", "date": monday.isoformat(),
             "spend": "10.00", "attributed_revenue": "5.00"},
            generator.SOURCE_ADS, monday.isoformat()))
    # AD-NEW: spend clears the bar but the history is too short.
    for week in range(4):
        monday = generator.week_start(week)
        items.append(_item(
            "ad_daily", f"ad:AD-NEW:{monday.isoformat()}",
            {"ad_set": "AD-NEW", "date": monday.isoformat(),
             "spend": "1000.00", "attributed_revenue": "900.00"},
            generator.SOURCE_ADS, monday.isoformat()))
    report = service.ingest.ingest_many(items)
    assert report.refused == 0
    payload = service.diagnose_ad_decay().payload
    assert payload["findings"] == []
    reasons = {entry["ad_set"]: entry["reason"] for entry in payload["unjudged"]}
    assert reasons == {"AD-TINY": "below_min_spend",
                       "AD-NEW": "insufficient_history"}


def test_tampered_fact_refuses_the_finding_set_wholesale(tmp_path):
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
    with pytest.raises(DiagnosisRefused) as caught:
        service.diagnose_stockout()
    refusal = caught.value.refusal
    assert refusal.kind == "diagnosis_refusal"
    assert refusal.artefact_id.startswith("DRF-")
    assert "ledger_not_current" in refusal.payload["problems"]
    assert service.store.list_artefacts("diagnosis_stockout") == []
    stored = service.store.list_artefacts("diagnosis_refusal")
    assert [r.artefact_id for r in stored] == [refusal.artefact_id]


def test_forged_finding_set_fails_verification(service):
    artefact = service.diagnose_stockout()
    forged_payload = json.loads(canonical_json(artefact.payload))
    forged_payload["findings"][0]["cover_days"] = "99.00"
    forged = Artefact(
        artefact_id="DST-forged00000000000000", kind="diagnosis_stockout",
        payload=forged_payload,
        input_fingerprint=artefact.input_fingerprint,
    )
    service.store.insert_artefact(forged)
    problems = service.diagnosis.verify(forged)
    assert "stockout_finding_mismatch" in problems


def test_api_diagnosis_reads(tmp_path, monkeypatch):
    settings = make_settings(tmp_path)
    monkeypatch.setattr(api_module, "_service", None)
    monkeypatch.setattr(api_module, "load_settings", lambda: settings)
    client = TestClient(api_module.app)
    assert client.get("/diagnosis/stockout").status_code == 404
    service = api_module.get_service()
    service.generate_dataset()
    service.ledger.compute()
    service.diagnose_stockout()
    service.diagnose_ad_decay()
    stockout = client.get("/diagnosis/stockout").json()
    assert stockout["kind"] == "diagnosis_stockout"
    assert stockout["payload"]["findings"][0]["sku"] == generator.FAST_MOVER_SKU
    verify = client.get("/diagnosis/stockout/verify").json()
    assert verify["verified"] is True
    decay = client.get("/diagnosis/ad-decay").json()
    assert decay["payload"]["findings"][0]["ad_set"] == generator.DECAY_SET
    assert client.get("/diagnosis/ad-decay/verify").json()["verified"] is True
    assert client.get("/diagnosis/refusals").json()["count"] == 0


def test_cli_diagnose_and_verify(tmp_path, monkeypatch, capsys):
    settings = make_settings(tmp_path)
    monkeypatch.setenv("SMB_SQLITE_PATH", str(settings.sqlite_path))
    assert cli_main(["generate"]) == 0
    assert cli_main(["diagnose"]) == 0
    out = capsys.readouterr().out
    assert "STOCKOUT HW-THR-002" in out
    assert "cover 21.00 days" in out
    assert "DECAY AD-SOCIAL" in out
    assert "4.0000" in out and "0.1704" in out
    assert cli_main(["diagnose-verify"]) == 0
    assert cli_main(["refusals"]) == 0
    assert "0 refusals" in capsys.readouterr().out


def test_graph_diagnoses_and_verifies(tmp_path):
    service = CommerceService(make_settings(tmp_path))
    result = build_graph(service).invoke({})
    assert result["stockout_artefact_id"].startswith("DST-")
    assert result["stockout_findings"] == 1
    assert result["ad_decay_artefact_id"].startswith("DAD-")
    assert result["ad_decay_findings"] == 1
    assert result["verify_problems"] == []
