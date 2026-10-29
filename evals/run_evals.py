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
  - plan_figure_mismatches ........ a growth-plan figure that does
                                   not re-resolve against the ledger
                                   artefact it cites, or a figure
                                   problem from the plan verifier
  - plan_citation_errors ......... a plan citation to a missing,
                                   refused or superseded artefact, a
                                   proposal citation that does not
                                   resolve, or a refused diagnosis
                                   that failed to block a plan
  - plan_ranking_errors .......... a plan priority out of the plan
                                   pack's stated order (family, then
                                   exposure descending, then key)
                                   against the hand-computed fixture
                                   order
  - plan_empty_state_omissions ... a diagnosis family with no
                                   findings whose explicit no-findings
                                   entry is absent from a plan, or
                                   does not name the pack version
                                   that judged it
  - draft_figure_mismatches_accepted
                                   a stored draft whose claims do
                                   not re-resolve against the
                                   artefacts they cite, a prose
                                   figure no claim carries, or a
                                   narrative out of the plan's
                                   priority order
  - draft_refused_leakages ....... a refused (careless-model) draft
                                   stored as usable, or a careless
                                   draft that was not refused at all
  - draft_source_binding_errors .. a draft bound to anything but
                                   the exact plan / proposal /
                                   diagnosis artefact it drafts from
  - draft_cost_reconciliation_errors
                                   a drafting call whose recorded
                                   cost the price table cannot
                                   re-perform
  - guardian_bypasses ............ a draft listed as awaiting
                                   approval without a passing,
                                   re-verified verdict, a clean
                                   draft missing from the list, or
                                   a blocked or unscreened draft
                                   on it
  - verdict_hash_mismatches_accepted
                                   a verdict forged onto another
                                   draft's hash that verify()
                                   accepts
  - guardian_hard_line_misses .... an adversarial fixture draft
                                   (spelled-out discount, word-form
                                   figure, competitor disparagement,
                                   guaranteed-results language) that
                                   the guardian does not block with
                                   the right rule id — each fixture
                                   first passes the Round 6 gate,
                                   proving the guardian is a second
                                   net and not a rerun
  - screening_cost_reconciliation_errors
                                   a screening call whose recorded
                                   cost the price table cannot
                                   re-perform, or a verdict whose
                                   stamped call does not reconcile
  - approvals_without_passing_verdict
                                   an approval recorded for a draft
                                   with no passing guardian verdict
                                   (blocked, unscreened, or already
                                   rejected), or an approve attempt
                                   on such a draft that was not
                                   refused with the refusal recorded
  - approvals_on_hash_mismatch ... an approval recorded against a
                                   verdict whose hash differs from
                                   the draft's current hash, a
                                   forged approval act with a
                                   mismatched hash that verify()
                                   accepts or that the approved
                                   list shows, or an approve
                                   attempt against the forged
                                   verdict that was not refused
  - double_approvals_accepted .... a second approval act recorded
                                   for a draft that already carries
                                   one
  - stale_approvals_shown_clean .. an approved entry whose bound
                                   inputs have moved (a new ledger,
                                   a superseded plan) that the
                                   approved list shows as not stale,
                                   or shows stale without naming a
                                   moved input
  - approved_shown_as_executed ... an approved draft or approval
                                   act shown anywhere as sent,
                                   executed or published, or without
                                   the exact display status
                                   "approved — not sent"

Prints one line per counter and a final GATE: PASS or GATE: FAIL.
Exit code 0 on PASS, 1 on FAIL.
"""

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from smb_commerce import generator
from smb_commerce.approvals import ApprovalRefused
from smb_commerce.config import Settings, load_settings
from smb_commerce.diagnosis import DIAGNOSIS_PACK
from smb_commerce.drafting import DraftingService, DraftRefused
from smb_commerce.ingest import IngestPort
from smb_commerce.modelport import GovernedModelPort, ModelReply
from smb_commerce.models import Artefact
from smb_commerce.plan import PlanRefused
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
        "plan_figure_mismatches", "plan_citation_errors",
        "plan_ranking_errors", "plan_empty_state_omissions",
        "draft_figure_mismatches_accepted", "draft_refused_leakages",
        "draft_source_binding_errors", "draft_cost_reconciliation_errors",
        "guardian_bypasses", "verdict_hash_mismatches_accepted",
        "guardian_hard_line_misses",
        "screening_cost_reconciliation_errors",
        "approvals_without_passing_verdict",
        "approvals_on_hash_mismatch", "double_approvals_accepted",
        "stale_approvals_shown_clean", "approved_shown_as_executed",
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

        # Growth plan (Round 5): the plan over the dataset must
        # verify clean, its summary figures must re-resolve against
        # the ledger it cites, and its priority order must equal the
        # hand-computed order from the plan pack's stated rule —
        # family order (stockout, decay, expense, refund, audit),
        # then exposure descending, then key: the throw at 280
        # trailing units, Social at 500.00 x 52 weeks of spend, the
        # PackShip duplicate at 349.00, the vase at 9,568.00
        # refunded, winback structural at 0.
        def grade_plan_problems(svc, plan):
            for problem in svc.plan.verify(plan):
                if problem.startswith("plan_figure_mismatch"):
                    counters["plan_figure_mismatches"] += 1
                elif (problem.startswith("citation")
                        or problem.startswith("plan_citation_error")):
                    counters["plan_citation_errors"] += 1
                else:
                    counters["plan_figure_mismatches"] += 1

        plan = service.assemble_plan()
        grade_plan_problems(service, plan)
        ledger = service.store.get_artefact(plan.payload["ledger_artefact_id"])
        for figure in plan.payload["summary"]["figures"]:
            if (figure["source_artefact_id"] != ledger.artefact_id
                    or figure["value"]
                    != ledger.payload[figure["source_field"]]):
                counters["plan_figure_mismatches"] += 1
        ranked = [e for e in plan.payload["priorities"]
                  if e["rank"] is not None]
        expected_ranking = [
            ("stockout_risk", generator.FAST_MOVER_SKU, "280"),
            ("ad_decay", generator.DECAY_SET, "26000.00"),
            ("expense_anomaly", None, "349.00"),
            ("refund_anomaly", "HW-VAS-004", "9568.00"),
            ("email_flow_audit", "winback|missing_flow", "0"),
        ]
        if len(ranked) != len(expected_ranking):
            counters["plan_ranking_errors"] += 1
        for entry, (family, key, exposure) in zip(ranked, expected_ranking):
            if entry["family"] != family or entry["exposure"] != exposure:
                counters["plan_ranking_errors"] += 1
            elif key is not None and entry["finding_key"] != key:
                counters["plan_ranking_errors"] += 1
            elif key is None and not entry["finding_key"].startswith(
                f"{generator.DUPLICATE_VENDOR}|duplicate_charge|"
            ):
                counters["plan_ranking_errors"] += 1
        dataset_proposals = service.store.list_artefacts(
            "draft_flow_proposal")
        winback_action = next(
            a for a in plan.payload["actions"]
            if a["family"] == "email_flow_audit"
        )
        if (len(dataset_proposals) != 1
                or winback_action["proposal_artefact_id"]
                != dataset_proposals[0].artefact_id):
            counters["plan_citation_errors"] += 1
        if [e for e in plan.payload["priorities"] if e["rank"] is None]:
            counters["plan_empty_state_omissions"] += 1

        # Empty-state and exposure grading needs a record where
        # families judge empty: a fixture with two stockout SKUs
        # (280 and 140 trailing units) and every catalogue flow
        # defined and healthy. Ad decay, expenses, refunds and the
        # audit must each contribute a named no-findings entry.
        plan_fixture_settings = Settings(
            database_url="", sqlite_path=tmp_path / "plan-fixture.db",
            migrations_dir=base.migrations_dir,
            object_dir=tmp_path / "plan-fixture-objects",
            s3_endpoint="", s3_bucket=base.s3_bucket,
            model_profile="offline", model_tier="standard",
            log_level="WARNING",
        )
        plan_fixture = CommerceService(plan_fixture_settings)
        plan_fixture_items = [
            {"kind": "order", "natural_key": "order:HW-THR-002:2025-12-20",
             "payload": {"order_id": "F-1", "sku": "HW-THR-002",
                         "date": "2025-12-20", "qty": 280,
                         "unit_price": "64.00", "amount": "17920.00"},
             "source_system": generator.SOURCE_ORDERS,
             "as_of": "2025-12-20"},
            {"kind": "inventory",
             "natural_key": "inventory:HW-THR-002:2025-12-29",
             "payload": {"sku": "HW-THR-002", "week_start": "2025-12-29",
                         "on_hand": 210},
             "source_system": generator.SOURCE_INVENTORY,
             "as_of": "2025-12-29"},
            {"kind": "order", "natural_key": "order:HW-CAN-003:2025-12-20",
             "payload": {"order_id": "F-2", "sku": "HW-CAN-003",
                         "date": "2025-12-20", "qty": 140,
                         "unit_price": "38.00", "amount": "5320.00"},
             "source_system": generator.SOURCE_ORDERS,
             "as_of": "2025-12-20"},
            {"kind": "inventory",
             "natural_key": "inventory:HW-CAN-003:2025-12-29",
             "payload": {"sku": "HW-CAN-003", "week_start": "2025-12-29",
                         "on_hand": 100},
             "source_system": generator.SOURCE_INVENTORY,
             "as_of": "2025-12-29"},
        ]
        plan_fixture_items += [
            {"kind": "email_flow", "natural_key": f"flow:{entry['flow_key']}",
             "payload": {"flow_key": entry["flow_key"],
                         "name": entry["name"], "status": "active",
                         "steps": entry["expected_steps"]},
             "source_system": generator.SOURCE_EMAIL, "as_of": "2026-01-04"}
            for entry in DIAGNOSIS_PACK["email_flow_audit"]["catalogue"]
        ]
        plan_fixture.ingest.ingest_many(plan_fixture_items)
        fixture_plan = plan_fixture.assemble_plan()
        grade_plan_problems(plan_fixture, fixture_plan)
        fixture_ranked = [e for e in fixture_plan.payload["priorities"]
                          if e["rank"] is not None]
        expected_fixture = [
            ("stockout_risk", "HW-THR-002", "280"),
            ("stockout_risk", "HW-CAN-003", "140"),
        ]
        if len(fixture_ranked) != len(expected_fixture):
            counters["plan_ranking_errors"] += 1
        for entry, (family, key, exposure) in zip(
            fixture_ranked, expected_fixture
        ):
            if (entry["family"] != family or entry["finding_key"] != key
                    or entry["exposure"] != exposure):
                counters["plan_ranking_errors"] += 1
        fixture_coverage = {
            e["family"]: e for e in fixture_plan.payload["priorities"]
            if e["rank"] is None
        }
        for family in ("ad_decay", "expense_anomaly", "refund_anomaly",
                       "email_flow_audit"):
            entry = fixture_coverage.get(family)
            if (entry is None or entry["status"] != "no_findings"
                    or entry["judged_by_pack_version"]
                    != DIAGNOSIS_PACK["version"]):
                counters["plan_empty_state_omissions"] += 1

        # A refused diagnosis must block the plan whole: tamper
        # with a fixture fact behind the store's back and the
        # assembler must raise PlanRefused naming the diagnosis
        # refusal, storing no plan.
        plan_fixture.ledger.compute()
        target = next(
            f for f in plan_fixture.store.list_facts("order")
            if f.payload["sku"] == "HW-THR-002"
        )
        tampered = dict(target.payload)
        tampered["qty"] = int(tampered["qty"]) + 1
        plan_fixture.store._conn.execute(
            "UPDATE facts SET payload = ? WHERE fact_id = ?",
            (canonical_json(tampered), target.fact_id),
        )
        plan_fixture.store._conn.commit()
        try:
            plan_fixture.assemble_plan()
            counters["plan_citation_errors"] += 1
        except PlanRefused as refused_error:
            refusal = refused_error.refusal
            if not refusal.payload["blocking_refusals"] or not all(
                problem.startswith("diagnosis_refused:")
                for problem in refusal.payload["problems"]
            ):
                counters["plan_citation_errors"] += 1
        if plan_fixture.store.list_artefacts("growth_plan") != [
            fixture_plan
        ]:
            counters["plan_citation_errors"] += 1

        # Drafting (Round 6): the offline drafts over the verified
        # plan must verify clean, bind to the exact artefacts they
        # draft from, walk the plan's priorities in order, and
        # reconcile in the cost ledger. A careless model double
        # that invents a figure must be refused, with nothing
        # stored as usable.
        drafts = service.draft_all()
        for draft in drafts.values():
            problems = service.drafting.verify(draft)
            counters["draft_figure_mismatches_accepted"] += len(problems)
            if draft.payload["status"] != "draft":
                counters["draft_source_binding_errors"] += 1
            counters["draft_cost_reconciliation_errors"] += len(
                service.model.reconcile(draft.payload["run_id"]))
        narrative = drafts["plan_narrative"]
        if narrative.payload["source_bindings"].get("plan") != (
            plan.artefact_id
        ):
            counters["draft_source_binding_errors"] += 1
        prose = narrative.payload["content"]["prose"]
        positions = [
            prose.index(entry["finding_key"])
            for entry in plan.payload["priorities"]
            if entry["rank"] is not None
        ]
        if positions != sorted(positions):
            counters["draft_figure_mismatches_accepted"] += 1
        copy_draft = drafts["winback_email_copy"]
        if copy_draft.payload["source_bindings"].get("proposal") != (
            dataset_proposals[0].artefact_id
        ):
            counters["draft_source_binding_errors"] += 1
        steps = copy_draft.payload["content"]["steps"]
        if (len(steps) != 2
                or steps[0]["subject"]
                != "We miss you at Hartwell Home Goods"
                or "336 hours" not in steps[1]["body"]):
            counters["draft_figure_mismatches_accepted"] += 1
        note = drafts["ad_budget_note"]
        if note.payload["source_bindings"].get("diagnosis") != (
            decay.artefact_id
        ):
            counters["draft_source_binding_errors"] += 1

        class CarelessPort:
            profile_name = "offline"
            model_name = "careless-double"
            tier = "standard"

            def complete(self, system, prompt):
                line = prompt.splitlines()[1]
                claims = json.loads(
                    line[len("FIGURE-SHEET "):])["claims"]
                claims[0] = dict(claims[0], value="999999.99")
                return ModelReply(
                    text=json.dumps({
                        "content": {"prose": "A fine quarter."},
                        "claims": claims,
                    }),
                    profile=self.profile_name, model=self.model_name,
                    tier=self.tier, input_tokens=10, output_tokens=5,
                )

        careless = DraftingService(
            service.store,
            GovernedModelPort(CarelessPort(), service.store),
        )
        narratives_before = len(
            service.store.list_artefacts("draft_plan_narrative"))
        try:
            careless.draft_plan_narrative(plan)
            counters["draft_refused_leakages"] += 1
        except DraftRefused as refused_error:
            if not any(
                problem.startswith("draft_figure_mismatch")
                for problem in refused_error.refusal.payload["problems"]
            ):
                counters["draft_refused_leakages"] += 1
        if len(service.store.list_artefacts(
            "draft_plan_narrative"
        )) != narratives_before:
            counters["draft_refused_leakages"] += 1

        # Guardian screen (Round 7): the three clean drafts must
        # pass, verify and be listed as awaiting approval. The
        # adversarial fixtures are drafted through doubles whose
        # prose the Round 6 gate cannot refuse — word-form
        # figures and a spelled-out discount — so each fixture
        # first proves it passed drafting, then must block with
        # the right rule id.
        verdicts = service.screen_all()
        for verdict in verdicts.values():
            if verdict.payload["outcome"] != "pass":
                counters["guardian_bypasses"] += 1
            counters["guardian_bypasses"] += len(
                service.guardian.verify(verdict))
            counters["screening_cost_reconciliation_errors"] += len(
                service.model.reconcile(
                    verdict.payload["screening_run_id"]))
        if {e["draft_artefact_id"]
                for e in service.awaiting_approval()} != set(verdicts):
            counters["guardian_bypasses"] += 1
        if service.blocked_drafts():
            counters["guardian_bypasses"] += 1

        class ProsePort:
            profile_name = "offline"
            tier = "standard"

            def __init__(self, model_name, prose):
                self.model_name = model_name
                self._prose = prose

            def complete(self, system, prompt):
                line = prompt.splitlines()[1]
                claims = json.loads(
                    line[len("FIGURE-SHEET "):])["claims"]
                return ModelReply(
                    text=json.dumps({
                        "content": {"prose": self._prose},
                        "claims": claims,
                    }),
                    profile=self.profile_name, model=self.model_name,
                    tier=self.tier, input_tokens=10, output_tokens=5,
                )

        guardian_fixtures = (
            ("eval-discount",
             "Take forty percent off the decaying set's next"
             " flight, whenever you are ready.",
             "discount_beyond_cap"),
            ("eval-word-figure",
             "The set reached nine hundred new shoppers this week.",
             "figure_not_in_claims"),
            ("eval-disparagement",
             "Unlike BrightNest Living, whose throws are shoddy"
             " and fall apart within a season, ours are made to"
             " hold.",
             "competitor_disparagement"),
            ("eval-guarantee",
             "These results are guaranteed, every single week.",
             "guaranteed_results_language"),
        )
        fixture_drafts = {}
        for model_name, prose, rule_id in guardian_fixtures:
            fixture_drafting = DraftingService(
                service.store,
                GovernedModelPort(
                    ProsePort(model_name, prose), service.store),
            )
            fixture_draft = fixture_drafting.draft_ad_note()
            fixture_drafts[rule_id] = fixture_draft
            if service.drafting.verify(fixture_draft):
                counters["guardian_hard_line_misses"] += 1
            fixture_verdict = service.screen_draft(fixture_draft)
            if rule_id not in fixture_verdict.payload["blocking_rules"]:
                counters["guardian_hard_line_misses"] += 1
            counters["screening_cost_reconciliation_errors"] += len(
                service.model.reconcile(
                    fixture_verdict.payload["screening_run_id"]))
        if {e["draft_artefact_id"]
                for e in service.awaiting_approval()} != set(verdicts):
            counters["guardian_bypasses"] += 1

        # An unscreened draft — clean prose, never screened — is
        # never awaiting approval.
        unscreened = DraftingService(
            service.store,
            GovernedModelPort(
                ProsePort("eval-unscreened",
                          "A quiet note on the decaying set, nothing"
                          " more."),
                service.store),
        ).draft_ad_note()
        if unscreened.artefact_id in {
            e["draft_artefact_id"] for e in service.awaiting_approval()
        }:
            counters["guardian_bypasses"] += 1

        # A forged verdict — a genuine pass rebound to a blocked
        # draft, so its hash names the wrong draft — must fail
        # verify() and must never open the gate.
        pass_verdict = verdicts[drafts["plan_narrative"].artefact_id]
        forged_payload = dict(pass_verdict.payload)
        forged_payload["draft_artefact_id"] = (
            fixture_drafts["guaranteed_results_language"].artefact_id)
        forged = Artefact(
            artefact_id="GRV-forged0000000000000e",
            kind="guardian_verdict", payload=forged_payload,
            input_fingerprint="forged",
        )
        service.store.insert_artefact(forged)
        forged_problems = service.guardian.verify(forged)
        if not any(
            problem == "verdict_draft_hash_mismatch"
            for problem in forged_problems
        ):
            counters["verdict_hash_mismatches_accepted"] += 1
        if fixture_drafts["guaranteed_results_language"].artefact_id in {
            e["draft_artefact_id"] for e in service.awaiting_approval()
        }:
            counters["guardian_bypasses"] += 1

        # Approval queue (Round 8): the three clean drafts are
        # approved by a named approver; every approved entry must
        # read "approved — not sent" and start out not stale.
        approval_acts = {}
        for draft in drafts.values():
            act = service.approve_draft(draft.artefact_id, "Maya Chen")
            approval_acts[draft.artefact_id] = act
            counters["approved_shown_as_executed"] += len(
                service.approvals.verify(act))
        approved_entries = {
            e["draft_artefact_id"]: e
            for e in service.approvals.approved()
        }
        if set(approved_entries) != set(approval_acts):
            counters["approved_shown_as_executed"] += 1
        for entry in approved_entries.values():
            if (entry["display_status"] != "approved — not sent"
                    or entry["execution_status"] != "not_sent"):
                counters["approved_shown_as_executed"] += 1
            if entry["stale"]:
                counters["stale_approvals_shown_clean"] += 1
        if service.approvals.awaiting():
            counters["approvals_without_passing_verdict"] += 1

        def _expect_refusal(draft_id, problems_expected, counter):
            refusals_before = len(service.approvals.refusals())
            try:
                service.approve_draft(draft_id, "Maya Chen")
                counters[counter] += 1
                return
            except ApprovalRefused as refused_error:
                problems = refused_error.refusal.payload["problems"]
                if not any(p in problems for p in problems_expected):
                    counters[counter] += 1
            if len(service.approvals.refusals()) <= refusals_before:
                counters[counter] += 1

        # A blocked draft (genuine blocking verdict) cannot be
        # approved; an unscreened draft cannot be approved.
        _expect_refusal(
            fixture_drafts["discount_beyond_cap"].artefact_id,
            ["guardian_verdict_not_passing"],
            "approvals_without_passing_verdict")
        _expect_refusal(
            unscreened.artefact_id, ["no_guardian_verdict"],
            "approvals_without_passing_verdict")
        # The guaranteed-results fixture's latest verdict is the
        # forged one from the guardian section: its hash names
        # another draft, so approval must refuse on the hash.
        _expect_refusal(
            fixture_drafts["guaranteed_results_language"].artefact_id,
            ["verdict_draft_hash_mismatch"],
            "approvals_on_hash_mismatch")
        # No double-approval: the narrative already carries one.
        _expect_refusal(
            drafts["plan_narrative"].artefact_id, ["already_approved"],
            "double_approvals_accepted")
        narrative_acts = [
            a for a in service.store.list_artefacts("approval_act")
            if a.payload["draft_artefact_id"]
            == drafts["plan_narrative"].artefact_id
        ]
        if len(narrative_acts) != 1:
            counters["double_approvals_accepted"] += 1

        # A forged approval act — bound to a hash that is not the
        # draft's — must fail verify() and never read as approved.
        forged_approval = Artefact(
            artefact_id="APR-forged0000000000000e",
            kind="approval_act",
            payload={
                "action": "approve",
                "draft_artefact_id": drafts["plan_narrative"].artefact_id,
                "draft_kind": "plan_narrative",
                "draft_content_hash": "0" * 64,
                "verdict_artefact_id": (
                    approval_acts[drafts["plan_narrative"].artefact_id]
                    .payload["verdict_artefact_id"]),
                "verdict_pack_version": "2026.10.1",
                "approver": "Forger", "status": "approved",
                "display_status": "approved — not sent",
                "execution_status": "not_sent",
                "bound_inputs": {
                    "draft_content_hash": "0" * 64,
                    "verdict_artefact_id": (
                        approval_acts[drafts["plan_narrative"].artefact_id]
                        .payload["verdict_artefact_id"]),
                    "verdict_pack_version": "2026.10.1",
                    "source_artefact_ids": [], "source_heads": {},
                    "ledger_artefact_id": None,
                },
            },
            input_fingerprint="forged",
        )
        service.store.insert_artefact(forged_approval)
        if "approval_draft_hash_mismatch" not in service.approvals.verify(
                forged_approval):
            counters["approvals_on_hash_mismatch"] += 1
        if any(e["approval_artefact_id"] == forged_approval.artefact_id
               for e in service.approvals.approved()):
            counters["approvals_on_hash_mismatch"] += 1

        # Rejection: a fresh clean draft, screened and passing,
        # is rejected with a stated reason; it leaves the queue,
        # stays on the record, and cannot then be approved.
        reject_draft = DraftingService(
            service.store,
            GovernedModelPort(
                ProsePort("eval-rejection",
                          "A quiet note on the decaying set, nothing"
                          " more."),
                service.store),
        ).draft_ad_note()
        service.screen_draft(reject_draft)
        service.reject_draft(
            reject_draft.artefact_id, "Maya Chen", "timing",
            "Hold until the spring catalogue lands.")
        if reject_draft.artefact_id not in {
            e["draft_artefact_id"] for e in service.approvals.rejected()
        } or reject_draft.artefact_id in {
            e["draft_artefact_id"] for e in service.approvals.awaiting()
        }:
            counters["approvals_without_passing_verdict"] += 1
        _expect_refusal(
            reject_draft.artefact_id, ["already_rejected"],
            "approvals_without_passing_verdict")

        # Staleness: move the record under the approved
        # narrative — a new ledger and a plan that supersedes
        # the one it drafts from — and the approved entry must
        # read STALE with the moved inputs named, never clean.
        service.ingest.ingest(
            kind="order", natural_key="order:EVAL-R8:2026-01-05",
            payload={"order_id": "EVAL-R8",
                     "sku": generator.FAST_MOVER_SKU,
                     "date": "2026-01-05", "qty": 40,
                     "unit_price": "64.00", "amount": "2560.00"},
            source_system=generator.SOURCE_ORDERS, as_of="2026-01-05",
        )
        service.ledger.compute()
        service.assemble_plan()
        stale_entry = {
            e["draft_artefact_id"]: e
            for e in service.approvals.approved()
        }[drafts["plan_narrative"].artefact_id]
        if not stale_entry["stale"] or not stale_entry["moved_inputs"]:
            counters["stale_approvals_shown_clean"] += 1
        if stale_entry["display_status"] != "approved — not sent":
            counters["approved_shown_as_executed"] += 1

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
