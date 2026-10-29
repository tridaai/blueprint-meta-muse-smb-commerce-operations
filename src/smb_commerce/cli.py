"""The CLI surface over the same service the API serves.

Commands: generate, facts, ledger, verify, diagnose,
diagnose-verify, refusals, model-calls. Output is plain text sized
for a terminal; figures come from the ledger artefact, never
recomputed on the fly by the CLI itself.
"""

from __future__ import annotations

import argparse
import json
import sys

from .diagnosis import DiagnosisRefused
from .service import CommerceService


def _print_ledger(service: CommerceService) -> int:
    artefact = service.store.latest_artefact("ledger")
    if artefact is None:
        artefact = service.ledger.compute()
    p = artefact.payload
    print(f"Ledger {artefact.artefact_id} as of {p['as_of']}")
    print(f"  gross revenue  {p['gross_revenue']:>12}")
    print(f"  refunds        {p['refunds_total']:>12}")
    print(f"  net revenue    {p['net_revenue']:>12}")
    print(f"  COGS           {p['cogs']:>12}")
    print(f"  gross margin   {p['gross_margin']:>12}")
    print(f"  refund rate    {p['refund_rate']:>12}")
    for row in p["skus"]:
        print(f"  {row['sku']} cover {row['cover_days']:>6} days"
              f"  out {row['cover_out_date']}  on hand {row['on_hand']}")
    for row in p["ad_sets"]:
        print(f"  {row['ad_set']} ROAS {row['roas']:>7}"
              f"  spend {row['spend']:>10}  revenue {row['attributed_revenue']:>10}")
    return 0


def _print_diagnosis(artefact) -> None:
    p = artefact.payload
    print(f"{p['diagnosis']} {artefact.artefact_id} pack {p['pack_version']}"
          f" ledger {p['ledger_artefact_id']} as of {p['as_of']}")
    for finding in p["findings"]:
        if p["diagnosis"] == "stockout_risk":
            print(f"  STOCKOUT {finding['sku']} {finding['name']}"
                  f"  on hand {finding['on_hand']}"
                  f"  cover {finding['cover_days']} days"
                  f"  out {finding['cover_out_date']}"
                  f"  velocity {finding['trailing_velocity_per_day']}/day"
                  f" over {finding['velocity_window_days']}d")
        else:
            print(f"  DECAY {finding['ad_set']}"
                  f"  ROAS {finding['first_week_roas']}"
                  f" ({finding['first_week']}) ->"
                  f" {finding['latest_week_roas']} ({finding['latest_week']})"
                  f"  factor {finding['decay_factor']}")
    for entry in p.get("no_data", []):
        print(f"  NO-DATA {entry['sku']} {entry['reason']}")
    for entry in p.get("unjudged", []):
        print(f"  UNJUDGED {entry['ad_set']} {entry['reason']}"
              f"  spend {entry['total_spend']}  weeks {entry['weeks']}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="smb-commerce")
    parser.add_argument("command", choices=[
        "generate", "facts", "ledger", "verify", "diagnose",
        "diagnose-verify", "refusals", "model-calls"])
    parser.add_argument("--kind", default=None)
    parser.add_argument("--run-id", default=None)
    args = parser.parse_args(argv)

    service = CommerceService()
    if args.command == "generate":
        report = service.generate_dataset()
        print(f"accepted={report.accepted} duplicates={report.duplicates} "
              f"refused={report.refused}")
        return 0 if report.refused == 0 else 1
    if args.command == "facts":
        facts = service.store.list_facts(args.kind)
        for fact in facts:
            print(f"{fact.fact_id} {fact.kind} {fact.natural_key}"
                  f" src={fact.source_system} as_of={fact.as_of}")
        print(f"{len(facts)} facts")
        return 0
    if args.command == "ledger":
        return _print_ledger(service)
    if args.command == "verify":
        artefact = service.store.latest_artefact("ledger")
        if artefact is None:
            artefact = service.ledger.compute()
        problems = service.ledger.verify(artefact)
        print(json.dumps({"artefact_id": artefact.artefact_id,
                          "verified": not problems, "problems": problems}))
        return 0 if not problems else 1
    if args.command == "diagnose":
        try:
            _print_diagnosis(service.diagnose_stockout())
            _print_diagnosis(service.diagnose_ad_decay())
        except DiagnosisRefused as refused:
            print(f"REFUSED {refused.refusal.artefact_id}"
                  f" {refused.refusal.payload['diagnosis']}:"
                  f" {', '.join(refused.refusal.payload['problems'])}")
            return 1
        return 0
    if args.command == "diagnose-verify":
        status = 0
        for kind in ("diagnosis_stockout", "diagnosis_ad_decay"):
            artefact = service.store.latest_artefact(kind)
            if artefact is None:
                print(json.dumps({"kind": kind, "verified": False,
                                  "problems": ["not_run"]}))
                status = 1
                continue
            problems = service.diagnosis.verify(artefact)
            print(json.dumps({"artefact_id": artefact.artefact_id,
                              "verified": not problems, "problems": problems}))
            status = status or (1 if problems else 0)
        return status
    if args.command == "refusals":
        refusals = service.store.list_artefacts("diagnosis_refusal")
        for refusal in refusals:
            print(f"{refusal.artefact_id} {refusal.payload['diagnosis']}"
                  f" pack {refusal.payload['pack_version']}:"
                  f" {', '.join(refusal.payload['problems'])}")
        print(f"{len(refusals)} refusals")
        return 0
    if args.command == "model-calls":
        calls = service.store.list_model_calls(args.run_id)
        for call in calls:
            print(f"{call.call_id} run={call.run_id} profile={call.profile}"
                  f" tier={call.tier} in={call.input_tokens}"
                  f" out={call.output_tokens} cost={call.cost_usd}")
        print(f"{len(calls)} calls")
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
