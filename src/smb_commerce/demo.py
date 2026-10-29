"""The demo: generate Hartwell Home Goods, compute the ledger, and
walk the operating picture — including the planted patterns, shown in
the computed figures themselves.

Runs against a throwaway store in a temp directory: no disk state in
the repo, no network, no keys. The model call at the end uses the
offline profile and lands in the cost ledger like any other call.
"""

from __future__ import annotations

import tempfile
from decimal import Decimal
from pathlib import Path

from . import generator
from .config import Settings, load_settings
from .service import CommerceService


def build_demo_service(tmp: Path) -> CommerceService:
    base = load_settings()
    settings = Settings(
        database_url="",
        sqlite_path=tmp / "demo.db",
        migrations_dir=base.migrations_dir,
        object_dir=tmp / "objects",
        s3_endpoint="",
        s3_bucket=base.s3_bucket,
        model_profile="offline",
        model_tier=base.model_tier,
        log_level=base.log_level,
    )
    return CommerceService(settings)


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="smb-commerce-demo-") as tmp:
        service = build_demo_service(Path(tmp))
        report = service.generate_dataset()
        counts = service.dataset_counts()
        print("== Hartwell Home Goods — dataset ==")
        print(f"period {generator.START.isoformat()} .. {generator.END.isoformat()}"
              f"  facts accepted {report.accepted}  refused {report.refused}")
        for kind in sorted(counts):
            print(f"  {kind:<12} {counts[kind]}")

        artefact = service.ledger.compute()
        problems = service.ledger.verify(artefact)
        p = artefact.payload
        print("\n== Operating picture (computed in code, Decimal) ==")
        print(f"  gross revenue  {p['gross_revenue']:>12}")
        print(f"  refunds        {p['refunds_total']:>12}  rate {p['refund_rate']}")
        print(f"  net revenue    {p['net_revenue']:>12}")
        print(f"  COGS           {p['cogs']:>12}")
        print(f"  gross margin   {p['gross_margin']:>12}")
        print(f"  ledger {artefact.artefact_id}  verified {not problems}")

        print("\n== Inventory cover by SKU ==")
        for row in p["skus"]:
            print(f"  {row['sku']} {row['name']:<30} on hand {row['on_hand']:>5}"
                  f"  cover {row['cover_days']:>6} days  out {row['cover_out_date']}")
        fastest = min(p["skus"], key=lambda r: Decimal(r["cover_days"]))
        print(f"  -> tightest cover: {fastest['sku']} runs out "
              f"{fastest['cover_out_date']} ({fastest['cover_days']} days)")

        print("\n== Ad sets ==")
        for row in p["ad_sets"]:
            first, last = row["weekly"][0], row["weekly"][-1]
            print(f"  {row['ad_set']:<10} spend {row['spend']:>9}"
                  f"  revenue {row['attributed_revenue']:>10}  ROAS {row['roas']}")
            print(f"             week 1 ROAS {first['roas']} ->"
                  f" week {len(row['weekly'])} ROAS {last['roas']}")

        print("\n== Diagnosis packs I — findings from the ledger ==")
        stockout = service.diagnose_stockout()
        sp = stockout.payload
        print(f"  stockout {stockout.artefact_id}  pack {sp['pack_version']}"
              f"  verified {not service.diagnosis.verify(stockout)}")
        for finding in sp["findings"]:
            print(f"  STOCKOUT {finding['sku']} {finding['name']}"
                  f"  on hand {finding['on_hand']}"
                  f"  cover {finding['cover_days']} days"
                  f"  out {finding['cover_out_date']}"
                  f"  velocity {finding['trailing_velocity_per_day']}/day")
        for entry in sp["no_data"]:
            print(f"  NO-DATA {entry['sku']} {entry['reason']}")
        decay = service.diagnose_ad_decay()
        dp = decay.payload
        print(f"  ad decay {decay.artefact_id}  pack {dp['pack_version']}"
              f"  verified {not service.diagnosis.verify(decay)}")
        for finding in dp["findings"]:
            print(f"  DECAY {finding['ad_set']}"
                  f"  ROAS {finding['first_week_roas']}"
                  f" ({finding['first_week']}) ->"
                  f" {finding['latest_week_roas']} ({finding['latest_week']})"
                  f"  factor {finding['decay_factor']}")
        for entry in dp["unjudged"]:
            print(f"  UNJUDGED {entry['ad_set']} {entry['reason']}")

        print("\n== Diagnosis packs II — expense and refund anomalies ==")
        expenses = service.diagnose_expenses()
        ep = expenses.payload
        print(f"  expenses {expenses.artefact_id}  pack {ep['pack_version']}"
              f"  verified {not service.diagnosis.verify(expenses)}")
        for finding in ep["findings"]:
            if finding["class"] == "duplicate_charge":
                print(f"  DUPLICATE {finding['vendor']} {finding['amount']}"
                      f" on {finding['dates'][0]} + {finding['dates'][1]}"
                      f"  gap {finding['gap_days']}d"
                      f"  facts {finding['fact_ids'][0]},"
                      f" {finding['fact_ids'][1]}")
            else:
                print(f"  OUTLIER {finding['vendor']} {finding['amount']}"
                      f" on {finding['date']}"
                      f"  median {finding['vendor_median']}"
                      f" x{finding['multiple']}")
        for entry in ep["informational"]:
            print(f"  NEW-VENDOR {entry['vendor']}"
                  f" first charge {entry['amount']} on {entry['date']}"
                  f"  over threshold {entry['threshold']}")
        for entry in ep["unjudged"]:
            print(f"  UNJUDGED {entry['vendor']} {entry['reason']}")
        refunds = service.diagnose_refunds()
        rp = refunds.payload
        print(f"  refunds {refunds.artefact_id}  pack {rp['pack_version']}"
              f"  verified {not service.diagnosis.verify(refunds)}")
        for finding in rp["findings"]:
            print(f"  REFUND {finding['sku']} {finding['name']}"
                  f"  rate {finding['refund_rate']}"
                  f" over bar {finding['max_refund_rate']}"
                  f"  refunds {finding['refunds']}"
                  f" of gross {finding['gross_revenue']}")
        for entry in rp["unjudged"]:
            print(f"  UNJUDGED {entry['sku']} {entry['reason']}")

        print("\n== Email flows defined on the platform ==")
        defined = [f.payload["flow_key"]
                   for f in service.store.list_facts("email_flow")]
        for key in defined:
            print(f"  {key}")
        print(f"  -> '{generator.MISSING_FLOW}' is not defined")

        print("\n== Email-flow audit — the catalogue against the definitions ==")
        audit = service.audit_email_flows()
        ap = audit.payload
        print(f"  audit {audit.artefact_id}  pack {ap['pack_version']}"
              f"  verified {not service.diagnosis.verify(audit)}")
        for finding in ap["findings"]:
            if finding["class"] == "missing_flow":
                print(f"  MISSING {finding['flow_key']} {finding['name']}"
                      f"  trigger {finding['trigger']}"
                      f"  expected steps {finding['expected_steps']}")
            elif finding["class"] == "dormant_flow":
                print(f"  DORMANT {finding['flow_key']} {finding['name']}"
                      f"  status {finding['status']}")
            elif finding["class"] == "step_shortfall":
                print(f"  SHORTFALL {finding['flow_key']} {finding['name']}"
                      f"  steps {finding['steps']}"
                      f" of expected {finding['expected_steps']}")
            elif finding["class"] == "delay_breach":
                print(f"  DELAY {finding['flow_key']} {finding['name']}"
                      f"  step {finding['step_index']}"
                      f" delay {finding['delay_hours']}h"
                      f" over bar {finding['max_step_delay_hours']}h")
        for proposal in service.store.list_artefacts("draft_flow_proposal"):
            pp = proposal.payload
            print(f"  PROPOSAL {proposal.artefact_id} {pp['flow_key']}"
                  f" for {pp['proposal_for']}"
                  f"  trigger {pp['trigger']}  steps {pp['steps']}"
                  f"  delays {pp['step_delays_hours']}"
                  f"  status {pp['status']} — changes nothing")

        print("\n== Model port (offline profile, governed) ==")
        reply = service.draft(
            "demo-run",
            "You draft operations summaries from computed figures only.",
            f"Net revenue {p['net_revenue']}, gross margin "
            f"{p['gross_margin']}, tightest cover {fastest['sku']} at "
            f"{fastest['cover_days']} days.",
        )
        calls = service.store.list_model_calls("demo-run")
        total = service.model.run_total("demo-run")
        print(f"  profile {reply.profile}  model {reply.model}  tier {reply.tier}")
        print(f"  calls recorded {len(calls)}  run total ${total}"
              f"  reconcile problems {service.model.reconcile('demo-run')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
