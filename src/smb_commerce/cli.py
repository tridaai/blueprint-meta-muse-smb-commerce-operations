"""The CLI surface over the same service the API serves.

Commands: generate, facts, ledger, verify, diagnose,
diagnose-verify, refusals, plan, plan-verify, plan-refusals,
draft, drafts, drafts-verify, draft-refusals, screen, verdicts,
verdicts-verify, awaiting-approval, queue, approve, reject,
approvals-verify, approval-history, approval-refusals,
model-calls. The
diagnose commands cover all five diagnoses (stockout, ad decay,
expense anomalies, refund anomalies, and the email-flow audit);
the plan commands assemble the growth plan from the computed
record and re-verify it; the draft commands run the governed
drafting pass over the plan and read its drafts and refusals;
the screen commands run the guardian screen over the drafts and
read its verdicts, the blocked list and the awaiting-approval
gate; the queue commands read the approval queue folded from
the acts, and approve/reject record the named human act —
approved always reads "approved — not sent", because approval
executes nothing.
Output is plain text sized for a terminal; figures come from
the ledger artefact, never recomputed on the fly by the CLI
itself.
"""

from __future__ import annotations

import argparse
import json
import sys

from .approvals import ACT_KINDS, ApprovalRefused
from .diagnosis import DiagnosisRefused
from .drafting import DRAFT_KINDS, DraftRefused
from .plan import PlanRefused
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
        elif p["diagnosis"] == "ad_decay":
            print(f"  DECAY {finding['ad_set']}"
                  f"  ROAS {finding['first_week_roas']}"
                  f" ({finding['first_week']}) ->"
                  f" {finding['latest_week_roas']} ({finding['latest_week']})"
                  f"  factor {finding['decay_factor']}")
        elif p["diagnosis"] == "refund_anomaly":
            print(f"  REFUND {finding['sku']} {finding['name']}"
                  f"  rate {finding['refund_rate']}"
                  f" over bar {finding['max_refund_rate']}"
                  f"  refunds {finding['refunds']}"
                  f" of gross {finding['gross_revenue']}"
                  f"  units {finding['units_sold']}")
        elif finding["class"] == "duplicate_charge":
            print(f"  DUPLICATE {finding['vendor']} {finding['amount']}"
                  f" on {finding['dates'][0]} + {finding['dates'][1]}"
                  f"  gap {finding['gap_days']}d"
                  f" within {finding['window_days']}d"
                  f"  facts {finding['fact_ids'][0]}, {finding['fact_ids'][1]}")
        elif p["diagnosis"] == "email_flow_audit":
            if finding["class"] == "missing_flow":
                print(f"  MISSING {finding['flow_key']} {finding['name']}"
                      f"  trigger {finding['trigger']}"
                      f"  expected steps {finding['expected_steps']}")
            elif finding["class"] == "dormant_flow":
                print(f"  DORMANT {finding['flow_key']} {finding['name']}"
                      f"  status {finding['status']}"
                      f"  steps {finding['steps']}")
            elif finding["class"] == "step_shortfall":
                print(f"  SHORTFALL {finding['flow_key']} {finding['name']}"
                      f"  steps {finding['steps']}"
                      f" of expected {finding['expected_steps']}")
            elif finding["class"] == "delay_breach":
                print(f"  DELAY {finding['flow_key']} {finding['name']}"
                      f"  step {finding['step_index']}"
                      f" delay {finding['delay_hours']}h"
                      f" over bar {finding['max_step_delay_hours']}h")
        elif finding["class"] == "amount_outlier":
            print(f"  OUTLIER {finding['vendor']} {finding['amount']}"
                  f" on {finding['date']} ({finding['fact_id']})"
                  f"  median {finding['vendor_median']}"
                  f" x{finding['multiple']}"
                  f" over {finding['charges']} charges")
    for entry in p.get("proposals", []):
        print(f"  PROPOSAL {entry['flow_key']} for {entry['proposal_for']}"
              f"  (draft only — changes nothing)")
    for entry in p.get("informational", []):
        print(f"  NEW-VENDOR {entry['vendor']}"
              f" first charge {entry['amount']} on {entry['date']}"
              f" ({entry['fact_id']})"
              f"  over threshold {entry['threshold']}")
    for entry in p.get("no_data", []):
        print(f"  NO-DATA {entry['sku']} {entry['reason']}")
    for entry in p.get("unjudged", []):
        if "ad_set" in entry:
            print(f"  UNJUDGED {entry['ad_set']} {entry['reason']}"
                  f"  spend {entry['total_spend']}  weeks {entry['weeks']}")
        elif "vendor" in entry:
            print(f"  UNJUDGED {entry['vendor']} {entry['reason']}"
                  f"  charges {entry['charges']}")
        else:
            print(f"  UNJUDGED {entry['sku']} {entry['reason']}"
                  f"  units {entry['units_sold']}")


def _print_plan(artefact) -> None:
    p = artefact.payload
    print(f"growth_plan {artefact.artefact_id} pack {p['pack_version']}"
          f" ledger {p['ledger_artefact_id']} as of {p['as_of']}"
          f" supersedes {p['supersedes']}")
    for figure in p["summary"]["figures"]:
        print(f"  {figure['name']:<14} {figure['value']:>12}"
              f"  from {figure['source_artefact_id']}")
    for entry in p["priorities"]:
        if entry["rank"] is None:
            print(f"  NO-FINDINGS {entry['family']}"
                  f" judged by diagnosis pack"
                  f" {entry['judged_by_pack_version']}"
                  f" ({entry['diagnosis_artefact_id']})")
        else:
            print(f"  PRIORITY {entry['rank']} {entry['family']}"
                  f" {entry['finding_key']}"
                  f" exposure {entry['exposure']}"
                  f" ({entry['diagnosis_artefact_id']})")
    for action in p["actions"]:
        proposal = action["proposal_artefact_id"] or "-"
        print(f"  ACTION {action['rank']} {action['family']}"
              f" {action['finding_key']} proposal {proposal}"
              f" performed {action['performed']}")


def _print_draft(artefact) -> None:
    p = artefact.payload
    print(f"DRAFT {p['draft_kind']} {artefact.artefact_id}"
          f" status {p['status']} profile {p['model_profile']}"
          f" tier {p['tier']} call {p['model_call_id']}"
          f" sources {', '.join(p['source_artefact_ids'])}")
    content = p["content"]
    if "prose" in content:
        for line in content["prose"].splitlines():
            print(f"  {line}")
    for index, step in enumerate(content.get("steps", []), start=1):
        print(f"  step {index} subject: {step['subject']}")
        print(f"  step {index} body: {step['body']}")
    for claim in p["claims"]:
        print(f"  figure {claim['value']} from"
              f" {claim['source_artefact_id']} {claim['source_field']}")


def _print_verdict(verdict) -> None:
    p = verdict.payload
    print(f"VERDICT {verdict.artefact_id} draft {p['draft_artefact_id']}"
          f" outcome {p['outcome']} pack {p['pack_version']}"
          f" call {p['screening_call_id']}")
    for rule in p["rules"]:
        print(f"  {rule['source']:<5} {rule['rule_id']:<28}"
              f" {rule['outcome']}")
    if p["blocking_rules"]:
        print(f"  blocking: {', '.join(p['blocking_rules'])}")


def _print_approval_queue(service: CommerceService) -> None:
    queue = service.approval_queue()
    for entry in queue["awaiting_approval"]:
        print(f"AWAITING {entry['draft_kind']}"
              f" {entry['draft_artefact_id']}"
              f" verdict {entry['verdict_artefact_id']}"
              f" pack {entry['pack_version']}")
    for entry in queue["approved"]:
        stale = (f" STALE moved: {', '.join(entry['moved_inputs'])}"
                 if entry["stale"] else "")
        print(f"APPROVED {entry['draft_kind']}"
              f" {entry['draft_artefact_id']}"
              f" by {entry['approver']}"
              f" — {entry['display_status']}{stale}")
    for entry in queue["rejected"]:
        print(f"REJECTED {entry['draft_kind']}"
              f" {entry['draft_artefact_id']}"
              f" by {entry['rejecter']}"
              f" reason {entry['reason_code']}"
              f" note {entry['note']}")
    print(f"{len(queue['awaiting_approval'])} awaiting,"
          f" {len(queue['approved'])} approved — not sent,"
          f" {len(queue['rejected'])} rejected")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="smb-commerce")
    parser.add_argument("command", choices=[
        "generate", "facts", "ledger", "verify", "diagnose",
        "diagnose-verify", "refusals", "plan", "plan-verify",
        "plan-refusals", "draft", "drafts", "drafts-verify",
        "draft-refusals", "screen", "verdicts", "verdicts-verify",
        "awaiting-approval", "queue", "approve", "reject",
        "approvals-verify", "approval-history", "approval-refusals",
        "model-calls"])
    parser.add_argument("--kind", default=None)
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--draft", default=None)
    parser.add_argument("--approver", default=None)
    parser.add_argument("--rejecter", default=None)
    parser.add_argument("--reason", default=None)
    parser.add_argument("--note", default="")
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
            _print_diagnosis(service.diagnose_expenses())
            _print_diagnosis(service.diagnose_refunds())
            _print_diagnosis(service.audit_email_flows())
        except DiagnosisRefused as refused:
            print(f"REFUSED {refused.refusal.artefact_id}"
                  f" {refused.refusal.payload['diagnosis']}:"
                  f" {', '.join(refused.refusal.payload['problems'])}")
            return 1
        return 0
    if args.command == "diagnose-verify":
        status = 0
        for kind in ("diagnosis_stockout", "diagnosis_ad_decay",
                     "diagnosis_expense_anomaly", "diagnosis_refund_anomaly",
                     "diagnosis_email_flow_audit"):
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
    if args.command == "plan":
        try:
            _print_plan(service.assemble_plan())
        except PlanRefused as refused:
            print(f"REFUSED {refused.refusal.artefact_id} growth_plan:"
                  f" {', '.join(refused.refusal.payload['problems'])}")
            return 1
        return 0
    if args.command == "plan-verify":
        artefact = service.store.latest_artefact("growth_plan")
        if artefact is None:
            print(json.dumps({"kind": "growth_plan", "verified": False,
                              "problems": ["not_assembled"]}))
            return 1
        problems = service.plan.verify(artefact)
        print(json.dumps({"artefact_id": artefact.artefact_id,
                          "verified": not problems, "problems": problems}))
        return 0 if not problems else 1
    if args.command == "plan-refusals":
        refusals = service.store.list_artefacts("plan_refusal")
        for refusal in refusals:
            print(f"{refusal.artefact_id} growth_plan"
                  f" pack {refusal.payload['pack_version']}:"
                  f" {', '.join(refusal.payload['problems'])}")
        print(f"{len(refusals)} plan refusals")
        return 0
    if args.command == "draft":
        try:
            drafts = service.draft_all()
        except DraftRefused as refused:
            print(f"REFUSED {refused.refusal.artefact_id}"
                  f" {refused.refusal.payload['draft_kind']}:"
                  f" {', '.join(refused.refusal.payload['problems'])}")
            return 1
        for draft in drafts.values():
            _print_draft(draft)
        return 0
    if args.command == "drafts":
        count = 0
        for kind in DRAFT_KINDS:
            for draft in service.store.list_artefacts(kind):
                _print_draft(draft)
                count += 1
        print(f"{count} drafts")
        return 0
    if args.command == "drafts-verify":
        status = 0
        for kind in DRAFT_KINDS:
            artefact = service.store.latest_artefact(kind)
            if artefact is None:
                print(json.dumps({"kind": kind, "verified": False,
                                  "problems": ["not_drafted"]}))
                status = 1
                continue
            problems = service.drafting.verify(artefact)
            print(json.dumps({"artefact_id": artefact.artefact_id,
                              "verified": not problems,
                              "problems": problems}))
            status = status or (1 if problems else 0)
        return status
    if args.command == "draft-refusals":
        refusals = service.store.list_artefacts("draft_refusal")
        for refusal in refusals:
            print(f"{refusal.artefact_id}"
                  f" {refusal.payload['draft_kind']}:"
                  f" {', '.join(refusal.payload['problems'])}")
        print(f"{len(refusals)} draft refusals")
        return 0
    if args.command == "screen":
        verdicts = service.screen_all()
        for verdict in verdicts.values():
            _print_verdict(verdict)
        return 0
    if args.command == "verdicts":
        verdicts = service.store.list_artefacts("guardian_verdict")
        for verdict in verdicts:
            _print_verdict(verdict)
        print(f"{len(verdicts)} verdicts")
        return 0
    if args.command == "verdicts-verify":
        status = 0
        seen: set[str] = set()
        for kind in DRAFT_KINDS:
            for draft in service.store.list_artefacts(kind):
                verdict = service.guardian.latest_verdict(
                    draft.artefact_id)
                if verdict is None:
                    print(json.dumps({
                        "draft_artefact_id": draft.artefact_id,
                        "verified": False, "problems": ["not_screened"]}))
                    status = 1
                    continue
                if verdict.artefact_id in seen:
                    continue
                seen.add(verdict.artefact_id)
                problems = service.guardian.verify(verdict)
                print(json.dumps({
                    "artefact_id": verdict.artefact_id,
                    "verified": not problems, "problems": problems}))
                status = status or (1 if problems else 0)
        return status
    if args.command == "awaiting-approval":
        awaiting = service.awaiting_approval()
        for entry in awaiting:
            print(f"AWAITING {entry['draft_kind']}"
                  f" {entry['draft_artefact_id']}"
                  f" verdict {entry['verdict_artefact_id']}"
                  f" pack {entry['pack_version']}")
        print(f"{len(awaiting)} awaiting approval")
        for entry in service.blocked_drafts():
            print(f"BLOCKED {entry['draft_kind']}"
                  f" {entry['draft_artefact_id']}"
                  f" rules {', '.join(entry['blocking_rules'])}")
        return 0
    if args.command == "queue":
        _print_approval_queue(service)
        return 0
    if args.command == "approve":
        if not args.draft or not args.approver:
            print("approve requires --draft and --approver")
            return 2
        try:
            act = service.approve_draft(args.draft, args.approver)
        except ApprovalRefused as refused:
            print(f"REFUSED {refused.refusal.artefact_id} approve"
                  f" {args.draft}:"
                  f" {', '.join(refused.refusal.payload['problems'])}")
            return 1
        print(f"APPROVED {act.payload['draft_kind']}"
              f" {act.payload['draft_artefact_id']}"
              f" by {act.payload['approver']}"
              f" — {act.payload['display_status']}"
              f" act {act.artefact_id}")
        return 0
    if args.command == "reject":
        if not args.draft or not args.rejecter or not args.reason:
            print("reject requires --draft, --rejecter and --reason")
            return 2
        try:
            act = service.reject_draft(
                args.draft, args.rejecter, args.reason, args.note)
        except ApprovalRefused as refused:
            print(f"REFUSED {refused.refusal.artefact_id} reject"
                  f" {args.draft}:"
                  f" {', '.join(refused.refusal.payload['problems'])}")
            return 1
        print(f"REJECTED {act.payload['draft_kind']}"
              f" {act.payload['draft_artefact_id']}"
              f" by {act.payload['rejecter']}"
              f" reason {act.payload['reason_code']}"
              f" act {act.artefact_id}")
        return 0
    if args.command == "approvals-verify":
        status = 0
        count = 0
        for kind in ACT_KINDS:
            for act in service.store.list_artefacts(kind):
                problems = service.approvals.verify(act)
                print(json.dumps({"artefact_id": act.artefact_id,
                                  "verified": not problems,
                                  "problems": problems}))
                status = status or (1 if problems else 0)
                count += 1
        if count == 0:
            print(json.dumps({"verified": False,
                              "problems": ["no_approval_acts"]}))
            status = 1
        return status
    if args.command == "approval-history":
        if not args.draft:
            print("approval-history requires --draft")
            return 2
        for entry in service.approval_history(args.draft):
            payload = entry["payload"]
            if entry["kind"] == "approval_act":
                stale = (" STALE moved:"
                         f" {', '.join(entry['moved_inputs'])}"
                         if entry["stale"] else "")
                print(f"APPROVAL {entry['artefact_id']}"
                      f" by {payload['approver']}"
                      f" — {payload['display_status']}{stale}")
            elif entry["kind"] == "rejection_act":
                print(f"REJECTION {entry['artefact_id']}"
                      f" by {payload['rejecter']}"
                      f" reason {payload['reason_code']}"
                      f" note {payload['note']}")
            else:
                print(f"REFUSAL {entry['artefact_id']}"
                      f" {payload['action']} by {payload['actor']}:"
                      f" {', '.join(payload['problems'])}")
        return 0
    if args.command == "approval-refusals":
        refusals = service.approvals.refusals()
        for refusal in refusals:
            print(f"{refusal.artefact_id} {refusal.payload['action']}"
                  f" {refusal.payload['draft_artefact_id']}"
                  f" by {refusal.payload['actor']}:"
                  f" {', '.join(refusal.payload['problems'])}")
        print(f"{len(refusals)} approval refusals")
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
