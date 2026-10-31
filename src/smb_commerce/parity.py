"""The Round 10 conformance walk: one core walk, one snapshot.

The production claim of the Postgres profile is that the dialect is
an invisible detail: the same record, walked the same way, produces
byte-identical artefacts on SQLite and on PostgreSQL 16. This module
is the one definition of that walk and of the snapshot it is graded
by — the in-suite Postgres leg (``tests/test_postgres_parity.py``)
and the production smoke (``scripts/prod_smoke.py``) both call it,
so the two can never drift into grading different things.

The walk is the demo's spine, minus the printing: generate the
dataset, compute the ledger, run all five diagnoses, assemble the
plan, draft, screen, then exercise the approval queue
deterministically (approve the awaiting drafts but the last, reject
the last with a stated reason) and derive the morning board.
"""

from __future__ import annotations

from .service import CommerceService
from .store import canonical_json


def run_core_walk(service: CommerceService):
    """Run the core walk over ``service``'s store; return the board."""
    service.generate_dataset()
    service.ledger.compute()
    service.diagnose_stockout()
    service.diagnose_ad_decay()
    service.diagnose_expenses()
    service.diagnose_refunds()
    service.audit_email_flows()
    service.assemble_plan()
    service.draft_all()
    service.screen_all()
    awaiting = service.awaiting_approval()
    for entry in awaiting[:-1]:
        service.approve_draft(entry["draft_artefact_id"], "parity-owner")
    if awaiting:
        service.reject_draft(
            awaiting[-1]["draft_artefact_id"], "parity-owner", "timing",
            "parity walk: the one rejection, by the book",
        )
    return service.derive_board()


def snapshot(service: CommerceService, board) -> str:
    """Canonical text of everything the walk left behind.

    Facts (id + content hash, in record order), every artefact
    (kind, id, canonical payload, input fingerprint), the model
    calls, the applied migration versions, and the derived board.
    ``seq`` columns are deliberately excluded: identity here is
    content, and the record order is already carried by the store's
    ordered reads. Two snapshots are equal iff the two walks
    produced byte-identical artefacts — parity gap 0.
    """
    store = service.store
    lines: list[str] = []
    for fact in store.list_facts():
        lines.append(
            "FACT " + fact.fact_id + " " + fact.kind + " "
            + fact.natural_key + " " + fact.source_system + " "
            + fact.as_of + " " + fact.content_hash + " "
            + canonical_json(fact.payload)
        )
    for artefact in store.list_artefacts():
        lines.append(
            "ARTEFACT " + artefact.kind + " " + artefact.artefact_id
            + " " + artefact.input_fingerprint + " "
            + canonical_json(artefact.payload)
        )
    for call in store.list_model_calls():
        lines.append(
            "CALL " + call.call_id + " " + call.run_id + " "
            + call.profile + " " + call.model + " " + call.tier + " "
            + str(call.input_tokens) + " " + str(call.output_tokens)
            + " " + str(call.cost_usd)
        )
    lines.append("MIGRATIONS " + ",".join(store.applied_migrations()))
    lines.append(
        "BOARD " + board.artefact_id + " " + board.input_fingerprint
        + " " + canonical_json(board.payload)
    )
    return "\n".join(lines) + "\n"
