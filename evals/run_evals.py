#!/usr/bin/env python3
"""Eval gate v1 for Blueprint #22 — SMB Commerce Operations Agent.

Runs against a throwaway store (no disk state shared with anything,
no network, no keys) and grades the foundation guarantees and the
Round 2 diagnosis packs against the planted truth in the generator's
stated constants.

Zero-tolerance counters:
  - provenance_refusal_missed .... a fact without source or as-of was
                                   accepted at the ingest port
  - fact_count_mismatch .......... stored counts != generator manifest
  - provenance_missing ........... a stored fact lacks provenance
  - ledger_reperformance_mismatch  the verifier's second route disagrees
                                   with the stored ledger artefact
  - planted_cover_mismatch ....... the fast mover's cover is not the
                                   planted 21.0 days out on 2026-01-25
  - planted_decay_mismatch ....... a week of the decaying ad set whose
                                   revenue is not 2000 x 0.94^week
  - duplicate_charge_missing ..... the planted PackShip duplicate is
                                   not present as two adjacent facts
  - winback_state_wrong .......... the winback flow is defined, or one
                                   of the five standard flows is not
  - cost_ledger_reconciliation_errors
                                   a recorded model-call cost that the
                                   price table cannot re-perform
  - tier_stamp_missing ........... a call record without a tier stamp
  - stockout_false_positives ..... a SKU named at risk that the
                                   planted truth does not name
  - stockout_false_negatives ..... the planted fast mover not named
  - ad_decay_false_positives ..... an ad set named decaying that the
                                   planted truth does not name
  - ad_decay_false_negatives ..... the planted decaying set not named
  - finding_reperformance_mismatches
                                   a diagnosis verifier problem, or a
                                   planted figure in a finding that
                                   does not match the stated constants
  - duplicate_false_positives .... a duplicate-charge finding the
                                   planted truth does not name
  - duplicate_false_negatives .... the planted PackShip pair not
                                   named as a duplicate
  - outlier_false_positives ...... an amount-outlier finding the
                                   truth (dataset or fixture) does
                                   not name
  - outlier_false_negatives ...... the fixture's planted outlier not
                                   named
  - refund_anomaly_false_positives
                                   a SKU named over the refund bar
                                   that the planted truth does not name
  - refund_anomaly_false_negatives
                                   the planted Ceramic Vase rate not
                                   named
  - anomaly_reperformance_mismatches
                                   a pack II verifier problem, or a
                                   planted pack II figure that does
                                   not match the hand-computed values
  - email_missing_false_positives  a flow named missing that the
                                   truth (dataset or fixture) defines
  - email_missing_false_negatives  the planted missing flow (winback
                                   on the dataset) not named missing
  - email_dormant_false_positives  a flow named dormant that the
                                   truth does not show as not active
  - email_dormant_false_negatives  the fixture's paused flow not
                                   named dormant
  - draft_proposal_mismatches      a draft flow proposal whose
                                   structure differs from the pack
                                   catalogue's stated shape for that
                                   flow, or a proposal verifier
                                   problem
  - audit_reperformance_mismatches an email-audit verifier problem
  - flow_definition_mutations      an email_flow fact whose stored
                                   form changed across an audit run

Prints one line per counter and a final GATE: PASS or GATE: FAIL.
Exit code 0 on PASS, 1 on FAIL.
"""

from __future__ import annotations

import hashlib
import sys
import tempfile
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from smb_commerce import generator
from smb_commerce.config import Settings, load_settings
from smb_commerce.diagnosis import DIAGNOSIS_PACK
from smb_commerce.ingest import IngestPort
from smb_commerce.service import CommerceService
from smb_commerce.store import SqlStore, canonical_json


def main() -> int:
    counters: dict[str, int] = {name: 0 for name in (
        "provenance_refusal_missed", "fact_count_mismatch",
        "provenance_missing", "ledger_reperformance_mismatch",
        "planted_cover_mismatch", "planted_decay_mismatch",
        "duplicate_charge_missing", "winback_state_wrong",
        "cost_ledger_reconciliation_errors", "tier_stamp_missing",
        "stockout_false_positives", "stockout_false_negatives",
        "ad_decay_false_positives", "ad_decay_false_negatives",
        "finding_reperformance_mismatches",
        "duplicate_false_positives", "duplicate_false_negatives",
        "outlier_false_positives", "outlier_false_negatives",
        "refund_anomaly_false_positives", "refund_anomaly_false_negatives",
        "anomaly_reperformance_mismatches",
        "email_missing_false_positives", "email_missing_false_negatives",
        "email_dormant_false_positives", "email_dormant_false_negatives",
        "draft_proposal_mismatches", "audit_reperformance_mismatches",
        "flow_definition_mutations",
    )}
    with tempfile.TemporaryDirectory(prefix="smb-commerce-evals-") as tmp:
        tmp_path = Path(tmp)
        base = load_settings()
        settings = Settings(
            database_url="", sqlite_path=tmp_path / "evals.db",
            migrations_dir=base.migrations_dir, object_dir=tmp_path / "objects",
            s3_endpoint="", s3_bucket=base.s3_bucket,
            model_profile="offline", model_tier="standard", log_level="WARNING",
        )
        service = CommerceService(settings)

        # Provenance enforcement at the port (Law 3).
        probe_store = SqlStore.sqlite(tmp_path / "probe.db", base.migrations_dir)
        probe = IngestPort(probe_store)
        for kwargs in (
            {"source_system": None, "as_of": "2026-01-04"},
            {"source_system": "", "as_of": "2026-01-04"},
            {"source_system": "hartwell-books", "as_of": None},
            {"source_system": "hartwell-books", "as_of": "  "},
        ):
            outcome = probe.ingest(
                kind="expense", natural_key="probe:1",
                payload={"vendor": "Probe", "date": "2026-01-01",
                         "category": "software", "amount": "1.00"},
                **kwargs,
            )
            if outcome.status != "refused" or outcome.reason != "missing_provenance":
                counters["provenance_refusal_missed"] += 1

        # Dataset and manifest.
        service.generate_dataset()
        stored = service.dataset_counts()
        manifest = generator.manifest_counts()
        if stored != manifest:
            counters["fact_count_mismatch"] += 1
        for fact in service.store.list_facts():
            if not fact.source_system or not fact.as_of:
                counters["provenance_missing"] += 1

        # Ledger re-performance (Law 1 + Law 4).
        artefact = service.ledger.compute()
        problems = service.ledger.verify(artefact)
        counters["ledger_reperformance_mismatch"] += len(problems)

        # Planted cover: THR ends at 210 on hand, 280 units in the
        # trailing 28 days, so 21.0 cover days and out on 2026-01-25.
        skus = {row["sku"]: row for row in artefact.payload["skus"]}
        thr = skus[generator.FAST_MOVER_SKU]
        if not (thr["on_hand"] == 210 and thr["cover_days"] == "21.00"
                and thr["cover_out_date"] == "2026-01-25"):
            counters["planted_cover_mismatch"] += 1

        # Planted decay: every week of AD-SOCIAL re-performs from the
        # stated factor, and weekly spend stays 500.00.
        ad_sets = {row["ad_set"]: row for row in artefact.payload["ad_sets"]}
        social = ad_sets[generator.DECAY_SET]
        if len(social["weekly"]) != generator.WEEKS:
            counters["planted_decay_mismatch"] += 1
        for index, week in enumerate(social["weekly"]):
            expected = (
                generator.DECAY_BASE_REVENUE * (generator.DECAY_FACTOR ** index)
            ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            monday = generator.START + timedelta(days=index * 7)
            if (week["week_start"] != monday.isoformat()
                    or Decimal(week["attributed_revenue"]) != expected
                    or Decimal(week["spend"]) != Decimal("500.00")):
                counters["planted_decay_mismatch"] += 1

        # Planted duplicate charge: 2025-11-14 and 2025-11-15, adjacent.
        dup_dates = sorted(
            f.payload["date"] for f in service.store.list_facts("expense")
            if f.payload["vendor"] == generator.DUPLICATE_VENDOR
            and Decimal(f.payload["amount"]) == generator.DUPLICATE_AMOUNT
            and f.payload["date"] in ("2025-11-14", "2025-11-15")
        )
        if dup_dates != ["2025-11-14", "2025-11-15"]:
            counters["duplicate_charge_missing"] += 1

        # Planted missing flow: winback absent, the five present.
        defined = {f.payload["flow_key"]
                   for f in service.store.list_facts("email_flow")}
        expected_flows = {key for key, _name, _steps in generator.EMAIL_FLOWS}
        if generator.MISSING_FLOW in defined or defined != expected_flows:
            counters["winback_state_wrong"] += 1

        # Diagnosis packs I: the named sets must equal the planted
        # truth exactly, and every finding must re-perform.
        stockout = service.diagnose_stockout()
        named_stockout = {f["sku"] for f in stockout.payload["findings"]}
        expected_stockout = {generator.FAST_MOVER_SKU}
        counters["stockout_false_positives"] += len(
            named_stockout - expected_stockout)
        counters["stockout_false_negatives"] += len(
            expected_stockout - named_stockout)
        counters["finding_reperformance_mismatches"] += len(
            service.diagnosis.verify(stockout))
        if named_stockout == expected_stockout:
            finding = stockout.payload["findings"][0]
            if not (finding["cover_days"] == "21.00"
                    and finding["cover_out_date"] == "2026-01-25"
                    and finding["on_hand"] == 210
                    and finding["trailing_velocity_per_day"] == "10.00"):
                counters["finding_reperformance_mismatches"] += 1
        decay = service.diagnose_ad_decay()
        named_decay = {f["ad_set"] for f in decay.payload["findings"]}
        expected_decay = {generator.DECAY_SET}
        counters["ad_decay_false_positives"] += len(named_decay - expected_decay)
        counters["ad_decay_false_negatives"] += len(expected_decay - named_decay)
        counters["finding_reperformance_mismatches"] += len(
            service.diagnosis.verify(decay))
        if named_decay == expected_decay:
            finding = decay.payload["findings"][0]
            if not (finding["first_week_roas"] == "4.0000"
                    and finding["latest_week_roas"] == "0.1704"
                    and finding["decay_factor"] == "0.0426"):
                counters["finding_reperformance_mismatches"] += 1

        # Diagnosis pack II: the named anomalies must equal the
        # hand-computed truth exactly. Expenses: one duplicate pair
        # (PackShip Co $349.00, 2025-11-14 + 2025-11-15), no outliers
        # (every vendor's median equals its constant charge), two
        # informational first charges over $1,000.00. Refunds: only
        # the Ceramic Vase — $9,568.00 over $191,360.00 is 0.050000,
        # over the 0.040000 bar.
        expenses = service.diagnose_expenses()
        counters["anomaly_reperformance_mismatches"] += len(
            service.diagnosis.verify(expenses))
        duplicates = {
            (f["vendor"], f["amount"], tuple(f["dates"]))
            for f in expenses.payload["findings"]
            if f["class"] == "duplicate_charge"
        }
        expected_duplicates = {
            (generator.DUPLICATE_VENDOR, "349.00",
             ("2025-11-14", "2025-11-15")),
        }
        counters["duplicate_false_positives"] += len(
            duplicates - expected_duplicates)
        counters["duplicate_false_negatives"] += len(
            expected_duplicates - duplicates)
        dataset_outliers = {
            (f["vendor"], f["amount"])
            for f in expenses.payload["findings"]
            if f["class"] == "amount_outlier"
        }
        counters["outlier_false_positives"] += len(dataset_outliers)
        informational = {
            (e["vendor"], e["amount"])
            for e in expenses.payload["informational"]
        }
        if informational != {("Hartwell Studio Lease", "2400.00"),
                             ("Northgate Logistics", "1250.00")}:
            counters["anomaly_reperformance_mismatches"] += 1
        if expenses.payload["unjudged"]:
            counters["anomaly_reperformance_mismatches"] += 1
        refund_anomalies = service.diagnose_refunds()
        counters["anomaly_reperformance_mismatches"] += len(
            service.diagnosis.verify(refund_anomalies))
        named_refunds = {f["sku"] for f in refund_anomalies.payload["findings"]}
        expected_refunds = {"HW-VAS-004"}
        counters["refund_anomaly_false_positives"] += len(
            named_refunds - expected_refunds)
        counters["refund_anomaly_false_negatives"] += len(
            expected_refunds - named_refunds)
        if named_refunds == expected_refunds:
            finding = refund_anomalies.payload["findings"][0]
            if not (finding["refund_rate"] == "0.050000"
                    and finding["refunds"] == "9568.00"
                    and finding["gross_revenue"] == "191360.00"
                    and finding["units_sold"] == 2080
                    and finding["units_refunded"] == 104):
                counters["anomaly_reperformance_mismatches"] += 1
        if refund_anomalies.payload["unjudged"]:
            counters["anomaly_reperformance_mismatches"] += 1

        # Outlier grading needs a planted outlier, which the dataset
        # deliberately lacks: a fixture store where PrintWorks bills
        # $100.00 four times and $950.00 once — median $100.00, so
        # $950.00 is the one outlier the pack must name.
        fixture_settings = Settings(
            database_url="", sqlite_path=tmp_path / "fixture.db",
            migrations_dir=base.migrations_dir,
            object_dir=tmp_path / "fixture-objects",
            s3_endpoint="", s3_bucket=base.s3_bucket,
            model_profile="offline", model_tier="standard",
            log_level="WARNING",
        )
        fixture = CommerceService(fixture_settings)
        fixture_items = [
            {"kind": "expense",
             "natural_key": f"expense:PrintWorks:{day}:{amount}",
             "payload": {"vendor": "PrintWorks", "date": day,
                         "category": "ops", "amount": amount},
             "source_system": generator.SOURCE_FINANCE, "as_of": day}
            for day, amount in (
                ("2025-01-05", "100.00"), ("2025-02-05", "100.00"),
                ("2025-03-05", "100.00"), ("2025-04-05", "100.00"),
                ("2025-05-05", "950.00"),
            )
        ]
        fixture.ingest.ingest_many(fixture_items)
        fixture.ledger.compute()
        fixture_expenses = fixture.diagnose_expenses()
        counters["anomaly_reperformance_mismatches"] += len(
            fixture.diagnosis.verify(fixture_expenses))
        fixture_outliers = {
            (f["vendor"], f["amount"])
            for f in fixture_expenses.payload["findings"]
            if f["class"] == "amount_outlier"
        }
        expected_outliers = {("PrintWorks", "950.00")}
        counters["outlier_false_positives"] += len(
            fixture_outliers - expected_outliers)
        counters["outlier_false_negatives"] += len(
            expected_outliers - fixture_outliers)

        # Email-flow audit (pack III): on the dataset the winback
        # flow is the one missing finding, nothing is dormant, and
        # the one draft proposal must restate the catalogue's stated
        # shape for winback exactly — trigger customer_lapsed, 2
        # steps, delays 0 then 336 hours. The audit must leave the
        # defined flows byte-identical.
        def flow_fingerprint(svc) -> str:
            material = canonical_json(
                [f.model_dump() for f in svc.store.list_facts("email_flow")])
            return hashlib.sha256(material.encode()).hexdigest()

        def grade_audit(svc, audit, expected_missing, expected_dormant):
            counters["audit_reperformance_mismatches"] += len(
                svc.diagnosis.verify(audit))
            missing_named = {f["flow_key"]
                             for f in audit.payload["findings"]
                             if f["class"] == "missing_flow"}
            dormant_named = {f["flow_key"]
                             for f in audit.payload["findings"]
                             if f["class"] == "dormant_flow"}
            counters["email_missing_false_positives"] += len(
                missing_named - expected_missing)
            counters["email_missing_false_negatives"] += len(
                expected_missing - missing_named)
            counters["email_dormant_false_positives"] += len(
                dormant_named - expected_dormant)
            counters["email_dormant_false_negatives"] += len(
                expected_dormant - dormant_named)
            catalogue = {e["flow_key"]: e for e in
                         DIAGNOSIS_PACK["email_flow_audit"]["catalogue"]}
            proposals = {p.payload["flow_key"]: p for p in
                         svc.store.list_artefacts("draft_flow_proposal")}
            expected_keys = expected_missing | expected_dormant
            if set(proposals) != expected_keys:
                counters["draft_proposal_mismatches"] += 1
            for key in expected_keys & set(proposals):
                entry = catalogue[key]
                payload = proposals[key].payload
                if not (payload["steps"] == entry["expected_steps"]
                        and payload["step_delays_hours"]
                        == entry["step_delays_hours"]
                        and payload["trigger"] == entry["trigger"]
                        and payload["status"] == "proposal"
                        and payload["changes_nothing"] is True
                        and payload["audit_artefact_id"]
                        == audit.artefact_id):
                    counters["draft_proposal_mismatches"] += 1
                counters["draft_proposal_mismatches"] += len(
                    svc.diagnosis.verify(proposals[key]))

        flows_before = flow_fingerprint(service)
        dataset_audit = service.audit_email_flows()
        if flow_fingerprint(service) != flows_before:
            counters["flow_definition_mutations"] += 1
        grade_audit(service, dataset_audit, {generator.MISSING_FLOW}, set())

        # Dormant grading needs a defined-but-paused flow, which the
        # dataset deliberately lacks: a fixture store where
        # post_purchase is defined with status paused, the other
        # catalogue flows are active at their expected shapes, and
        # winback is still absent.
        audit_fixture_settings = Settings(
            database_url="", sqlite_path=tmp_path / "audit-fixture.db",
            migrations_dir=base.migrations_dir,
            object_dir=tmp_path / "audit-fixture-objects",
            s3_endpoint="", s3_bucket=base.s3_bucket,
            model_profile="offline", model_tier="standard",
            log_level="WARNING",
        )
        audit_fixture = CommerceService(audit_fixture_settings)
        audit_fixture.ingest.ingest_many([
            {"kind": "email_flow", "natural_key": f"flow:{key}",
             "payload": {"flow_key": key, "name": name,
                         "status": status, "steps": steps},
             "source_system": generator.SOURCE_EMAIL, "as_of": "2026-01-04"}
            for key, name, status, steps in (
                ("welcome_series", "Welcome Series", "active", 3),
                ("abandoned_cart", "Abandoned Cart", "active", 2),
                ("post_purchase", "Post-Purchase", "paused", 2),
                ("browse_abandonment", "Browse Abandonment", "active", 1),
                ("replenishment", "Replenishment Reminder", "active", 1),
            )
        ])
        audit_fixture.ledger.compute()
        fixture_flows_before = flow_fingerprint(audit_fixture)
        fixture_audit = audit_fixture.audit_email_flows()
        if flow_fingerprint(audit_fixture) != fixture_flows_before:
            counters["flow_definition_mutations"] += 1
        grade_audit(audit_fixture, fixture_audit,
                    {generator.MISSING_FLOW}, {"post_purchase"})

        # Cost ledger: an offline call and a stubbed hosted call, each
        # re-performed against the price table, each tier-stamped.
        service.draft("eval-run", "system", "Summarise the week.")
        problems = service.model.reconcile("eval-run")
        counters["cost_ledger_reconciliation_errors"] += len(problems)
        for call in service.store.list_model_calls():
            if call.tier not in ("standard", "contributor"):
                counters["tier_stamp_missing"] += 1

    failed = False
    for name, value in counters.items():
        print(f"  {name}: {value} (required 0)")
        if value != 0:
            failed = True
    print("GATE: FAIL" if failed else "GATE: PASS")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
