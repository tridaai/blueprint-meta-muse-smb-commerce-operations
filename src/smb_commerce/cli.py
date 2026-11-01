"""The CLI surface over the same service the API serves.

Commands: generate, facts, ledger, verify, diagnose,
diagnose-verify, refusals, plan, plan-verify, plan-refusals,
draft, drafts, drafts-verify, draft-refusals, screen, verdicts,
verdicts-verify, awaiting-approval, queue, approve, reject,
approvals-verify, approval-history, approval-refusals, board,
board-verify, connectors, quarantines, quarantine-verify,
model-calls, model-profile, costs, contexts, subscriptions,
subscribe, unsubscribe, digests, digest-generate, digest-verify,
review-packs, review-pack-generate, review-pack-verify,
review-pack-export, review-pack-exports, review-pack-status,
review-pack-publish, review-pack-export-verify, tokens,
token-issue, token-revoke, webhooks, webhook-subscribe,
webhook-unsubscribe, webhook-deliveries, webhook-verify.
The
diagnose commands cover all six diagnoses (stockout, ad decay,
expense anomalies, refund anomalies, the email-flow audit, and
listing quality — the catalogue's images, measured in code and
judged through the governed port's vision shape);
the plan commands assemble the growth plan from the computed
record and re-verify it; the draft commands run the governed
drafting pass over the plan and read its drafts and refusals;
the screen commands run the guardian screen over the drafts and
read its verdicts, the blocked list and the awaiting-approval
gate; the queue commands read the approval queue folded from
the acts, and approve/reject record the named human act —
approved always reads "approved — not sent", because approval
executes nothing. The board commands print the morning board —
the operator's opening view, re-derived from the record on
every call — and re-perform it end to end. The connector
commands read the Round 11 exchange contracts and the batches
quarantined whole under them, and re-perform a quarantine
artefact's hash chain.
Output is plain text sized for a terminal; figures come from
the ledger artefact, never recomputed on the fly by the CLI
itself.

Round 13: the CLI resolves a caller identity exactly the way the
API does — `--actor` plus `--role`, through the same
`resolve_caller` — and passes it to the service, which enforces
the access pack. With neither flag the CLI acts as the stated
internal identity (the terminal operator running their own
deployment); with flags, reads answer with the absent form for
roles that may not see a resource class, and forbidden actions
refuse. A partial or unstated identity fails closed.

Round 14: the subscription commands record who receives which
digest (`--subscriber` / `--subscriber-role` name the staff
member; `--actor` / `--role` remain the caller's own identity),
`digest-generate` folds a cadence's digests into the record, and
`digests` / `digest-verify` read them back under the same
content gate the API applies. Nothing is sent anywhere.

Round 17: the review-pack commands fold one stated month's
pack (`review-pack-generate --month`), read packs back under
the content gate (`review-packs`, `review-pack-verify`,
`review-pack-status`), render and record the export
(`review-pack-export --pack`, `review-pack-exports`,
`review-pack-export-verify --export`), and record the owner's
publication act (`review-pack-publish --pack`, the publisher
defaulting to the caller's own actor). Publication is recorded
in the record; nothing is distributed anywhere.

Round 18: `--token` authenticates the CLI's caller with a
scoped token, resolved through the service exactly as the API
resolves it — a token that does not verify resolves to
anonymous and fails closed. `token-issue` (`--subject`,
`--subject-role`, `--scopes`, optional `--ttl-seconds`) prints
the bearer string once; `tokens` and `token-revoke` read and
revoke the record as metadata. The webhook commands record
and read subscriptions (`webhook-subscribe --url --events`,
the signing secret printed once, `--signing-secret` to state
one; `webhooks`; `webhook-unsubscribe --subscription`) and
read back and re-verify deliveries (`webhook-deliveries`,
`webhook-verify --delivery`). The CLI delivers nothing: the
deliveries it reads were dispatched by the service, in a real
deployment over HTTP, in the demo and tests to a local double.
"""

from __future__ import annotations

import argparse
import json
import sys

from .access import (
    INTERNAL_CALLER,
    AccessRefused,
    Caller,
    resolve_caller,
)
from .approvals import ACT_KINDS, ApprovalRefused
from .diagnosis import DiagnosisRefused
from .drafting import DRAFT_KINDS, DraftRefused
from .listing import ListingRefused
from .plan import PlanRefused
from .reviewpacks import PackPublicationRefused
from .service import CommerceService


def _print_ledger(service: CommerceService, caller: Caller) -> int:
    artefact = service.read_ledger(caller)
    if artefact is None:
        print("no ledger computed yet")
        return 1
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
    if p["diagnosis"] == "listing_quality":
        print(f"listing_quality {artefact.artefact_id}"
              f" pack {p['pack_version']} as of {p['as_of']}")
        for finding in p["findings"]:
            if finding["class"] == "missing_image":
                print(f"  MISSING {finding['sku']} {finding['name']}"
                      f" — no listing image")
            elif finding["class"] == "provenance_incomplete":
                print(f"  PROVENANCE {finding['sku']}"
                      f" fact {finding['fact_id']}"
                      f" — provenance incomplete on the record")
            elif finding["class"] == "hash_mismatch":
                print(f"  HASH {finding['sku']}"
                      f" fact {finding['fact_id']}"
                      f" — stored bytes are not the recorded image")
            elif finding["class"] == "below_min_dimensions":
                print(f"  SMALL {finding['sku']}"
                      f" {finding['width']}x{finding['height']}"
                      f" below minimums"
                      f" {finding['min_width']}x{finding['min_height']}")
            elif finding["class"] == "format_mismatch":
                print(f"  FORMAT {finding['sku']}"
                      f" filename claims {finding['declared_format']},"
                      f" bytes measure {finding['format']}")
            elif finding["class"] == "vision_mismatch":
                print(f"  VISION {finding['sku']}"
                      f" image shows {finding['judged_class']},"
                      f" declared {finding['declared_class']}"
                      f" — model-judged by"
                      f" {finding['judgement_profile']}/"
                      f"{finding['judgement_model']}"
                      f" (judgement {finding['judgement_artefact_id']})")
        for entry in p["unjudged"]:
            print(f"  UNJUDGED {entry['sku']} {entry['reason']}")
        return
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


def _print_approval_queue(service: CommerceService, caller: Caller) -> None:
    queue = service.approval_queue(caller)
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


def _draft_label(entry) -> str:
    summary = entry.get("summary", {})
    if "subjects" in summary:
        return f"  [{'; '.join(summary['subjects'])}]"
    return f"  [{summary.get('opening_line', '')}]"


def _print_board(board) -> None:
    p = board.payload
    print(f"BOARD {board.artefact_id} as of {p['as_of']}"
          f" ledger {p['pins']['ledger_head']}"
          f" plan {p['pins']['plan_head']}")
    counts = p["headline"]["counts"]
    print(f"  {counts['findings']} findings,"
          f" {counts['awaiting_approval']} awaiting approval,"
          f" {counts['approved']} approved — not sent"
          f" ({counts['approved_stale']} stale),"
          f" {counts['rejected']} rejected,"
          f" {counts['blocked']} blocked,"
          f" {counts['unscreened']} unscreened")
    for figure in p["headline"]["figures"]:
        print(f"  {figure['name']:<14} {figure['value']:>12}"
              f"  from {figure['source_artefact_id']}")
    for entry in p["findings"]:
        if entry["rank"] is None:
            print(f"  NO-FINDINGS {entry['family']}"
                  f" judged by diagnosis pack"
                  f" {entry['judged_by_pack_version']}")
        else:
            print(f"  FINDING {entry['rank']} {entry['family']}"
                  f" {entry['finding_key']}"
                  f" exposure {entry['exposure']}")
    for risk in p["stock_risks"]:
        print(f"  STOCK RISK {risk['sku']} {risk['name']}"
              f"  on hand {risk['on_hand']}"
              f"  cover {risk['cover_days']} days"
              f"  out {risk['cover_out_date']}"
              f"  velocity {risk['trailing_velocity_per_day']}/day")
    for entry in p["awaiting_approval"]:
        print(f"  AWAITING {entry['draft_kind']}"
              f" {entry['draft_artefact_id']}"
              f" verdict {entry['verdict_artefact_id']}"
              f"{_draft_label(entry)}")
    for entry in p["approved"]:
        stale = (f" STALE moved: {', '.join(entry['moved_inputs'])}"
                 if entry["stale"] else "")
        print(f"  APPROVED {entry['draft_kind']}"
              f" {entry['draft_artefact_id']}"
              f" by {entry['approver']}"
              f" — {entry['display_status']}{stale}")
    for entry in p["rejected"]:
        print(f"  REJECTED {entry['draft_kind']}"
              f" {entry['draft_artefact_id']}"
              f" by {entry['rejecter']}"
              f" reason {entry['reason_code']}")
    for entry in p["blocked"]:
        print(f"  BLOCKED {entry['draft_kind']}"
              f" {entry['draft_artefact_id']}"
              f" rules {', '.join(entry['blocking_rules'])}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="smb-commerce")
    parser.add_argument("command", choices=[
        "generate", "facts", "ledger", "verify", "diagnose",
        "diagnose-verify", "refusals", "plan", "plan-verify",
        "plan-refusals", "draft", "drafts", "drafts-verify",
        "draft-refusals", "screen", "verdicts", "verdicts-verify",
        "awaiting-approval", "queue", "approve", "reject",
        "approvals-verify", "approval-history", "approval-refusals",
        "board", "board-verify", "connectors", "quarantines",
        "quarantine-verify", "model-calls", "model-profile",
        "costs", "contexts", "subscriptions", "subscribe",
        "unsubscribe", "digests", "digest-generate",
        "digest-verify", "review-packs", "review-pack-generate",
        "review-pack-verify", "review-pack-export",
        "review-pack-exports", "review-pack-status",
        "review-pack-publish", "review-pack-export-verify",
        "tokens", "token-issue", "token-revoke", "webhooks",
        "webhook-subscribe", "webhook-unsubscribe",
        "webhook-deliveries", "webhook-verify"])
    parser.add_argument("--kind", default=None)
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--draft", default=None)
    parser.add_argument("--cadence", default=None)
    parser.add_argument("--period-end", default=None)
    parser.add_argument("--digest", default=None)
    parser.add_argument("--month", default=None)
    parser.add_argument("--pack", default=None)
    parser.add_argument("--export", default=None)
    parser.add_argument("--publisher", default=None)
    parser.add_argument("--subscriber", default=None)
    parser.add_argument("--subscriber-role", default=None)
    parser.add_argument("--approver", default=None)
    parser.add_argument("--rejecter", default=None)
    parser.add_argument("--reason", default=None)
    parser.add_argument("--note", default="")
    parser.add_argument("--actor", default=None)
    parser.add_argument("--role", default=None)
    parser.add_argument("--token", default=None)
    parser.add_argument("--token-id", default=None)
    parser.add_argument("--subject", default=None)
    parser.add_argument("--subject-role", default=None)
    parser.add_argument("--scopes", default=None)
    parser.add_argument("--ttl-seconds", type=int, default=None)
    parser.add_argument("--url", default=None)
    parser.add_argument("--events", default=None)
    parser.add_argument("--subscription", default=None)
    parser.add_argument("--delivery", default=None)
    parser.add_argument("--signing-secret", default=None)
    args = parser.parse_args(argv)

    service = CommerceService()
    # The surface caller: a presented token authenticates
    # through the service and decides the caller on its own; else
    # the internal identity when no identity flags are given (the
    # terminal operator), else the presented identity resolved
    # the one shared way — a partial identity resolves to
    # anonymous and fails closed.
    if args.token:
        caller = service.authenticate_token(args.token)
    elif args.actor is None and args.role is None:
        caller = INTERNAL_CALLER
    else:
        caller = resolve_caller(args.actor, args.role)
    if args.command == "generate":
        try:
            report = service.generate_dataset(caller)
        except AccessRefused as refused:
            print(f"REFUSED generate: {', '.join(refused.problems)}"
                  f" for {refused.caller.describe()}")
            return 1
        print(f"accepted={report.accepted} duplicates={report.duplicates} "
              f"refused={report.refused}")
        return 0 if report.refused == 0 else 1
    if args.command == "facts":
        facts = service.read_facts(args.kind, caller)
        for fact in facts:
            print(f"{fact.fact_id} {fact.kind} {fact.natural_key}"
                  f" src={fact.source_system} as_of={fact.as_of}")
        print(f"{len(facts)} facts")
        return 0
    if args.command == "ledger":
        return _print_ledger(service, caller)
    if args.command == "verify":
        artefact = service.read_ledger(caller)
        if artefact is None:
            print(json.dumps({"verified": False,
                              "problems": ["no_ledger"]}))
            return 1
        problems = service.ledger.verify(artefact)
        print(json.dumps({"artefact_id": artefact.artefact_id,
                          "verified": not problems, "problems": problems}))
        return 0 if not problems else 1
    if args.command == "diagnose":
        try:
            _print_diagnosis(service.diagnose_stockout(caller))
            _print_diagnosis(service.diagnose_ad_decay(caller))
            _print_diagnosis(service.diagnose_expenses(caller))
            _print_diagnosis(service.diagnose_refunds(caller))
            _print_diagnosis(service.audit_email_flows(caller))
            _print_diagnosis(service.diagnose_listing_quality(caller))
        except (DiagnosisRefused, ListingRefused) as refused:
            print(f"REFUSED {refused.refusal.artefact_id}"
                  f" {refused.refusal.payload['diagnosis']}:"
                  f" {', '.join(refused.refusal.payload['problems'])}")
            return 1
        except AccessRefused as refused:
            print(f"REFUSED diagnose: {', '.join(refused.problems)}"
                  f" for {refused.caller.describe()}")
            return 1
        return 0
    if args.command == "diagnose-verify":
        status = 0
        for kind in ("diagnosis_stockout", "diagnosis_ad_decay",
                     "diagnosis_expense_anomaly", "diagnosis_refund_anomaly",
                     "diagnosis_email_flow_audit"):
            artefact = service.read_latest_artefact(kind, caller)
            if artefact is None:
                print(json.dumps({"kind": kind, "verified": False,
                                  "problems": ["not_run"]}))
                status = 1
                continue
            problems = service.diagnosis.verify(artefact)
            print(json.dumps({"artefact_id": artefact.artefact_id,
                              "verified": not problems, "problems": problems}))
            status = status or (1 if problems else 0)
        artefact = service.read_latest_artefact(
            "diagnosis_listing_quality", caller)
        if artefact is None:
            print(json.dumps({"kind": "diagnosis_listing_quality",
                              "verified": False, "problems": ["not_run"]}))
            status = 1
        else:
            problems = service.listing.verify(artefact)
            print(json.dumps({"artefact_id": artefact.artefact_id,
                              "verified": not problems,
                              "problems": problems}))
            status = status or (1 if problems else 0)
        return status
    if args.command == "refusals":
        refusals = service.read_artefacts("diagnosis_refusal", caller)
        for refusal in refusals:
            print(f"{refusal.artefact_id} {refusal.payload['diagnosis']}"
                  f" pack {refusal.payload['pack_version']}:"
                  f" {', '.join(refusal.payload['problems'])}")
        print(f"{len(refusals)} refusals")
        return 0
    if args.command == "plan":
        try:
            _print_plan(service.assemble_plan(caller))
        except PlanRefused as refused:
            print(f"REFUSED {refused.refusal.artefact_id} growth_plan:"
                  f" {', '.join(refused.refusal.payload['problems'])}")
            return 1
        except AccessRefused as refused:
            print(f"REFUSED plan: {', '.join(refused.problems)}"
                  f" for {refused.caller.describe()}")
            return 1
        return 0
    if args.command == "plan-verify":
        artefact = service.read_latest_artefact("growth_plan", caller)
        if artefact is None:
            print(json.dumps({"kind": "growth_plan", "verified": False,
                              "problems": ["not_assembled"]}))
            return 1
        problems = service.plan.verify(artefact)
        print(json.dumps({"artefact_id": artefact.artefact_id,
                          "verified": not problems, "problems": problems}))
        return 0 if not problems else 1
    if args.command == "plan-refusals":
        refusals = service.read_artefacts("plan_refusal", caller)
        for refusal in refusals:
            print(f"{refusal.artefact_id} growth_plan"
                  f" pack {refusal.payload['pack_version']}:"
                  f" {', '.join(refusal.payload['problems'])}")
        print(f"{len(refusals)} plan refusals")
        return 0
    if args.command == "draft":
        try:
            drafts = service.draft_all(caller)
        except DraftRefused as refused:
            print(f"REFUSED {refused.refusal.artefact_id}"
                  f" {refused.refusal.payload['draft_kind']}:"
                  f" {', '.join(refused.refusal.payload['problems'])}")
            return 1
        except AccessRefused as refused:
            print(f"REFUSED draft: {', '.join(refused.problems)}"
                  f" for {refused.caller.describe()}")
            return 1
        for draft in drafts.values():
            _print_draft(draft)
        return 0
    if args.command == "drafts":
        count = 0
        for kind in DRAFT_KINDS:
            for draft in service.read_artefacts(kind, caller):
                _print_draft(draft)
                count += 1
        print(f"{count} drafts")
        return 0
    if args.command == "drafts-verify":
        status = 0
        for kind in DRAFT_KINDS:
            artefact = service.read_latest_artefact(kind, caller)
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
        refusals = service.read_artefacts("draft_refusal", caller)
        for refusal in refusals:
            print(f"{refusal.artefact_id}"
                  f" {refusal.payload['draft_kind']}:"
                  f" {', '.join(refusal.payload['problems'])}")
        print(f"{len(refusals)} draft refusals")
        return 0
    if args.command == "screen":
        try:
            verdicts = service.screen_all(caller)
        except AccessRefused as refused:
            print(f"REFUSED screen: {', '.join(refused.problems)}"
                  f" for {refused.caller.describe()}")
            return 1
        for verdict in verdicts.values():
            _print_verdict(verdict)
        return 0
    if args.command == "verdicts":
        verdicts = service.read_artefacts("guardian_verdict", caller)
        for verdict in verdicts:
            _print_verdict(verdict)
        print(f"{len(verdicts)} verdicts")
        return 0
    if args.command == "verdicts-verify":
        status = 0
        seen: set[str] = set()
        for kind in DRAFT_KINDS:
            for draft in service.read_artefacts(kind, caller):
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
        awaiting = service.awaiting_approval(caller)
        for entry in awaiting:
            print(f"AWAITING {entry['draft_kind']}"
                  f" {entry['draft_artefact_id']}"
                  f" verdict {entry['verdict_artefact_id']}"
                  f" pack {entry['pack_version']}")
        print(f"{len(awaiting)} awaiting approval")
        for entry in service.blocked_drafts(caller):
            print(f"BLOCKED {entry['draft_kind']}"
                  f" {entry['draft_artefact_id']}"
                  f" rules {', '.join(entry['blocking_rules'])}")
        return 0
    if args.command == "queue":
        _print_approval_queue(service, caller)
        return 0
    if args.command == "approve":
        if not args.draft or not args.approver:
            print("approve requires --draft and --approver")
            return 2
        # The act's caller: the approver, acting in the role the
        # --role flag states; without one, the internal identity.
        act_caller = (
            resolve_caller(args.approver, args.role)
            if args.role is not None else INTERNAL_CALLER)
        try:
            act = service.approve_draft(
                args.draft, args.approver, caller=act_caller)
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
        act_caller = (
            resolve_caller(args.rejecter, args.role)
            if args.role is not None else INTERNAL_CALLER)
        try:
            act = service.reject_draft(
                args.draft, args.rejecter, args.reason, args.note,
                caller=act_caller)
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
            for act in service.read_artefacts(kind, caller):
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
        for entry in service.approval_history(args.draft, caller):
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
        refusals = service.approval_refusals(caller)
        for refusal in refusals:
            print(f"{refusal.artefact_id} {refusal.payload['action']}"
                  f" {refusal.payload['draft_artefact_id']}"
                  f" by {refusal.payload['actor']}:"
                  f" {', '.join(refusal.payload['problems'])}")
        print(f"{len(refusals)} approval refusals")
        return 0
    if args.command == "board":
        try:
            board = service.derive_board(caller)
        except AccessRefused:
            print("no board yet")
            return 1
        _print_board(board)
        return 0
    if args.command == "board-verify":
        try:
            board = service.derive_board(caller)
        except AccessRefused:
            print(json.dumps({"verified": False,
                              "problems": ["no_board"]}))
            return 1
        problems = service.board.verify(board)
        print(json.dumps({"artefact_id": board.artefact_id,
                          "verified": not problems, "problems": problems}))
        return 0 if not problems else 1
    if args.command == "connectors":
        from .connectors import CONTRACT_VERSION

        contracts = service.connector_registry(caller)
        print(f"contract version {CONTRACT_VERSION}")
        for contract in contracts:
            print(f"{contract.name} shape={contract.shape}"
                  f" source={contract.source_system}"
                  f" kinds={','.join(contract.kinds)}"
                  f" max_as_of_lag_days={contract.max_as_of_lag_days}")
        print(f"{len(contracts)} contracts")
        return 0
    if args.command == "quarantines":
        quarantines = service.quarantines(caller)
        for artefact in quarantines:
            payload = artefact.payload
            codes = ", ".join(
                sorted({v["code"] for v in payload["violations"]}))
            print(f"{artefact.artefact_id} contract={payload['contract']}"
                  f" source={payload['source_system']}"
                  f" records={payload['record_count']}"
                  f" violations: {codes}")
        print(f"{len(quarantines)} quarantined batches")
        return 0
    if args.command == "quarantine-verify":
        status = 0
        quarantines = service.quarantines(caller)
        for artefact in quarantines:
            problems = service.verify_quarantine(artefact)
            print(json.dumps({"artefact_id": artefact.artefact_id,
                              "verified": not problems,
                              "problems": problems}))
            status = status or (1 if problems else 0)
        if not quarantines:
            print(json.dumps({"verified": False,
                              "problems": ["no_quarantines"]}))
            status = 1
        return status
    if args.command == "model-calls":
        calls = service.read_model_calls(args.run_id, caller)
        for call in calls:
            print(f"{call.call_id} run={call.run_id} profile={call.profile}"
                  f" tier={call.tier} in={call.input_tokens}"
                  f" out={call.output_tokens} cost={call.cost_usd}")
        print(f"{len(calls)} calls")
        return 0
    if args.command == "model-profile":
        try:
            report = service.model_profile_report(caller)
        except AccessRefused as refused:
            print(f"REFUSED model-profile: {', '.join(refused.problems)}"
                  f" for {refused.caller.describe()}")
            return 1
        for key in ("profile", "status", "model", "tier", "base_url",
                    "key_required", "key_present", "fail_closed",
                    "rate_card_version", "live_call_made"):
            if key in report:
                print(f"{key} {report[key]}")
        return 0 if report["status"] in (
            "ready", "configured_unverified") else 1
    if args.command == "costs":
        summary = service.cost_summary(caller)
        total = summary["total"]
        print(f"rate card {summary['rate_card_version']}"
              f"  calls {total['calls']}"
              f"  in {total['input_tokens']}  out {total['output_tokens']}"
              f"  cost ${total['cost_usd']}")
        for label, breakdown in (
            ("profile", summary["by_profile"]),
            ("tier", summary["by_tier"]),
            ("model", summary["by_model"]),
            ("run", summary["by_run"]),
        ):
            for name, bucket in sorted(breakdown.items()):
                print(f"  by {label} {name}: calls {bucket['calls']}"
                      f"  cost ${bucket['cost_usd']}")
        problems = summary["reconcile_problems"]
        print(f"reconcile problems {problems}")
        return 0 if not problems else 1
    if args.command == "contexts":
        records = service.context_records(caller)
        for record in records:
            p = record.payload
            print(f"{record.artefact_id} run={p['run_id']}"
                  f" call={p['call_id']} policy {p['policy_version']}"
                  f" tokens {p['tokens_before']} -> {p['tokens_after']}"
                  f" budget {p['budget_tokens']}"
                  f" compacted {p['compacted']}")
        print(f"{len(records)} context records")
        return 0
    if args.command == "subscriptions":
        states = service.read_subscriptions(caller)
        for state in states:
            print(f"{state['subscription_id']}"
                  f" {state['actor']} ({state['role']})"
                  f" {state['cadence']}"
                  f" active {state['active']}")
        print(f"{len(states)} subscriptions")
        return 0
    if args.command in ("subscribe", "unsubscribe"):
        if (not args.subscriber or not args.subscriber_role
                or not args.cadence):
            print(f"{args.command} requires --subscriber,"
                  " --subscriber-role and --cadence")
            return 2
        try:
            if args.command == "subscribe":
                artefact = service.subscribe_digest(
                    args.subscriber, args.subscriber_role,
                    args.cadence, caller)
            else:
                artefact = service.unsubscribe_digest(
                    args.subscriber, args.subscriber_role,
                    args.cadence, caller)
        except AccessRefused as refused:
            print(f"REFUSED {args.command}:"
                  f" {', '.join(refused.problems)}"
                  f" for {refused.caller.describe()}")
            return 1
        except ValueError as error:
            print(f"REFUSED {args.command}: {error}")
            return 1
        if artefact is None:
            print("no active subscription to end")
            return 1
        state = next(
            s for s in service.digests.subscriptions()
            if s["artefact_id"] == artefact.artefact_id)
        print(f"{state['subscription_id']}"
              f" {state['actor']} ({state['role']})"
              f" {state['cadence']} active {state['active']}")
        return 0
    if args.command == "digest-generate":
        if not args.cadence:
            print("digest-generate requires --cadence")
            return 2
        try:
            report = service.generate_digests(
                args.cadence, args.period_end, caller)
        except AccessRefused as refused:
            print(f"REFUSED digest-generate:"
                  f" {', '.join(refused.problems)}"
                  f" for {refused.caller.describe()}")
            return 1
        except ValueError as error:
            print(f"REFUSED digest-generate: {error}")
            return 1
        period = report["period"]
        print(f"{report['cadence']} digests"
              f" {period['start']}..{period['end']}"
              f" as of {report['as_of']}")
        for entry in report["generated"]:
            print(f"  {entry['status']}"
                  f" {entry['digest_artefact_id']}"
                  f" for {entry['actor']} ({entry['role']})")
        print(f"{len(report['generated'])} digests"
              " — generated into the record, sent nowhere")
        return 0
    if args.command == "digests":
        digests = service.read_digests(args.cadence, caller)
        for digest in digests:
            p = digest.payload
            print(f"{digest.artefact_id} {p['cadence']}"
                  f" {p['period']['start']}..{p['period']['end']}"
                  f" role {p['role']}"
                  f" sections {', '.join(p['sections_included'])}")
        print(f"{len(digests)} digests")
        return 0
    if args.command == "digest-verify":
        if not args.digest:
            print("digest-verify requires --digest")
            return 2
        digest = service.read_digest(args.digest, caller)
        if digest is None:
            print(json.dumps({"verified": False,
                              "problems": ["no_digest"]}))
            return 1
        problems = service.digests.verify(digest)
        print(json.dumps({"artefact_id": digest.artefact_id,
                          "verified": not problems,
                          "problems": problems}))
        return 0 if not problems else 1
    if args.command == "review-pack-generate":
        if not args.month:
            print("review-pack-generate requires --month")
            return 2
        try:
            pack = service.generate_review_pack(args.month, caller)
        except AccessRefused as refused:
            print(f"REFUSED review-pack-generate:"
                  f" {', '.join(refused.problems)}"
                  f" for {refused.caller.describe()}")
            return 1
        except ValueError as error:
            print(f"REFUSED review-pack-generate: {error}")
            return 1
        status = service.review_packs.publication_status(pack)
        print(f"{pack.artefact_id} month {pack.payload['month']}"
              f" as of {pack.payload['as_of']}"
              f" sections {', '.join(pack.payload['sections_included'])}"
              f" — {status['status']}")
        return 0
    if args.command == "review-packs":
        packs = service.read_review_packs(caller)
        for pack in packs:
            status = service.review_packs.publication_status(pack)
            stale = " STALE" if status["stale"] else ""
            print(f"{pack.artefact_id} month {pack.payload['month']}"
                  f" as of {pack.payload['as_of']}"
                  f" — {status['status']}{stale}")
        print(f"{len(packs)} review packs")
        return 0
    if args.command == "review-pack-verify":
        if not args.pack:
            print("review-pack-verify requires --pack")
            return 2
        problems = service.verify_review_pack(args.pack, caller)
        if problems is None:
            print(json.dumps({"verified": False,
                              "problems": ["no_review_pack"]}))
            return 1
        print(json.dumps({"artefact_id": args.pack,
                          "verified": not problems,
                          "problems": problems}))
        return 0 if not problems else 1
    if args.command == "review-pack-status":
        if not args.pack:
            print("review-pack-status requires --pack")
            return 2
        status = service.review_pack_status(args.pack, caller)
        if status is None:
            print(json.dumps({"status": "absent"}))
            return 1
        print(json.dumps(status))
        return 0
    if args.command == "review-pack-export":
        if not args.pack:
            print("review-pack-export requires --pack")
            return 2
        try:
            export = service.export_review_pack(args.pack, caller)
        except AccessRefused as refused:
            print(f"REFUSED review-pack-export:"
                  f" {', '.join(refused.problems)}"
                  f" for {refused.caller.describe()}")
            return 1
        if export is None:
            print("no review pack to export")
            return 1
        p = export.payload
        print(f"{export.artefact_id} for pack {p['pack_artefact_id']}"
              f" month {p['month']} object {p['object_key']}"
              f" body sha256 {p['body_checksum'][:16]}..."
              f" {len(p['referenced_artefacts'])} referenced artefacts"
              f" — {p['publication']['status']}")
        return 0
    if args.command == "review-pack-exports":
        exports = service.read_review_pack_exports(args.pack, caller)
        for export in exports:
            p = export.payload
            print(f"{export.artefact_id} pack {p['pack_artefact_id']}"
                  f" month {p['month']}"
                  f" — {p['publication']['status']}")
        print(f"{len(exports)} review pack exports")
        return 0
    if args.command == "review-pack-export-verify":
        if not args.export:
            print("review-pack-export-verify requires --export")
            return 2
        problems = service.verify_review_pack_export(
            args.export, caller)
        if problems is None:
            print(json.dumps({"verified": False,
                              "problems": ["no_review_pack_export"]}))
            return 1
        print(json.dumps({"artefact_id": args.export,
                          "verified": not problems,
                          "problems": problems}))
        return 0 if not problems else 1
    if args.command == "review-pack-publish":
        if not args.pack:
            print("review-pack-publish requires --pack")
            return 2
        publisher = args.publisher or (
            caller.actor if caller.identified
            and caller != INTERNAL_CALLER else None)
        if not publisher:
            print("review-pack-publish requires --publisher"
                  " (or a presented --actor to publish as)")
            return 2
        try:
            act = service.publish_review_pack(
                args.pack, publisher, caller)
        except PackPublicationRefused as refused:
            print(f"REFUSED review-pack-publish:"
                  f" {refused.refusal.artefact_id}"
                  f" {', '.join(refused.refusal.payload['problems'])}")
            return 1
        print(f"{act.artefact_id} pack {act.payload['pack_artefact_id']}"
              f" month {act.payload['month']}"
              f" published by {act.payload['publisher']}"
              " — recorded in the record, distributed nowhere")
        return 0
    if args.command == "token-issue":
        if not args.subject or not args.subject_role or not args.scopes:
            print("token-issue requires --subject, --subject-role"
                  " and --scopes")
            return 2
        try:
            issued = service.issue_token(
                args.subject, args.subject_role,
                [s.strip() for s in args.scopes.split(",") if s.strip()],
                ttl_seconds=args.ttl_seconds, caller=caller)
        except AccessRefused as refused:
            print(f"REFUSED token-issue: {', '.join(refused.problems)}"
                  f" for {refused.caller.describe()}")
            return 1
        except ValueError as error:
            print(f"REFUSED token-issue: {error}")
            return 1
        p = issued.artefact.payload
        print(f"{issued.token_id} for {p['actor']} ({p['role']})"
              f" scopes {', '.join(p['scopes'])}"
              f" expires {p['expires_at']}")
        print(f"bearer (shown once): {issued.bearer}")
        return 0
    if args.command == "tokens":
        tokens = service.read_tokens(caller)
        for entry in tokens:
            print(f"{entry['token_id']} {entry['actor']}"
                  f" ({entry['role']}) scopes"
                  f" {', '.join(entry['scopes'])}"
                  f" issued {entry['issued_at']}"
                  f" expires {entry['expires_at']}"
                  f" — {entry['status']}")
        print(f"{len(tokens)} tokens")
        return 0
    if args.command == "token-revoke":
        if not args.token_id:
            print("token-revoke requires --token-id")
            return 2
        try:
            artefact = service.revoke_token(args.token_id, caller)
        except AccessRefused as refused:
            print(f"REFUSED token-revoke: {', '.join(refused.problems)}"
                  f" for {refused.caller.describe()}")
            return 1
        if artefact is None:
            print("no such token")
            return 1
        metadata = service.read_token(args.token_id, caller)
        print(f"{args.token_id} — {metadata['status']}"
              f" (revocation {artefact.artefact_id})")
        return 0
    if args.command == "webhook-subscribe":
        if not args.url or not args.events:
            print("webhook-subscribe requires --url and --events")
            return 2
        try:
            artefact, secret = service.subscribe_webhook(
                args.url,
                [e.strip() for e in args.events.split(",") if e.strip()],
                signing_secret=args.signing_secret, caller=caller)
        except AccessRefused as refused:
            print(f"REFUSED webhook-subscribe:"
                  f" {', '.join(refused.problems)}"
                  f" for {refused.caller.describe()}")
            return 1
        except ValueError as error:
            print(f"REFUSED webhook-subscribe: {error}")
            return 1
        p = artefact.payload
        print(f"{p['subscription_id']} {p['url']}"
              f" events {', '.join(p['events'])}"
              f" — {'active' if p['active'] else 'inactive'}")
        if secret is not None:
            print(f"signing secret (shown once): {secret}")
        return 0
    if args.command == "webhooks":
        subscriptions = service.read_webhooks(caller)
        for entry in subscriptions:
            print(f"{entry['subscription_id']} {entry['url']}"
                  f" events {', '.join(entry['events'])}"
                  f" — {'active' if entry['active'] else 'inactive'}")
        print(f"{len(subscriptions)} webhook subscriptions")
        return 0
    if args.command == "webhook-unsubscribe":
        if not args.subscription:
            print("webhook-unsubscribe requires --subscription")
            return 2
        try:
            artefact = service.unsubscribe_webhook(
                args.subscription, caller)
        except AccessRefused as refused:
            print(f"REFUSED webhook-unsubscribe:"
                  f" {', '.join(refused.problems)}"
                  f" for {refused.caller.describe()}")
            return 1
        if artefact is None:
            print("no active subscription of that id")
            return 1
        print(f"{args.subscription} — inactive"
              f" (superseded by {artefact.artefact_id})")
        return 0
    if args.command == "webhook-deliveries":
        deliveries = service.read_webhook_deliveries(
            args.subscription, caller)
        for entry in deliveries:
            print(f"{entry['delivery_id']}"
                  f" {entry['subscription_id']} {entry['event']}"
                  f" attempt {entry['attempt']} — {entry['outcome']}"
                  f" ({entry['response_class']})")
        print(f"{len(deliveries)} webhook deliveries")
        return 0
    if args.command == "webhook-verify":
        if not args.delivery:
            print("webhook-verify requires --delivery")
            return 2
        verified = service.verify_webhook_delivery(
            args.delivery, caller)
        if verified is None:
            print(json.dumps({"verified": False,
                              "problems": ["no_webhook_delivery"]}))
            return 1
        print(json.dumps({"delivery_id": args.delivery,
                          "verified": verified}))
        return 0 if verified else 1
    return 2


if __name__ == "__main__":
    sys.exit(main())
