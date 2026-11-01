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
Round 14 extends the walk past the board: subscribe the owner and
the bookkeeper to the daily digest and the operator to the weekly,
then generate both cadences — so the subscription fold and the
per-role digest artefacts are graded byte-identical on both
profiles too, inside the same snapshot.

Round 16 extends the walk with the catalogue's images: load the
planted product images through the image intake port — with the
dataset, before the ledger computes, because the ledger's input
fingerprint covers every fact on the record and image facts
landing later would stale it for the diagnosis packs — and run
the listing-quality diagnosis with the diagnoses. So the image
facts, the vision judgement artefacts, the vision call records
and the listing finding set are graded byte-identical on both
profiles too. The image bytes themselves live in each profile's
own object store; the snapshot grades the record of them, which
is content.

Round 17 extends the walk past the digests: fold the review
pack for the head ledger's month, export it, and publish it
with a named act — so the pack, the export record (its body
checksum and manifest are content, rendered deterministically
from the pack) and the publication act are graded byte-identical
on both profiles too. The export's bytes live in each profile's
own object store, like the image bytes; the snapshot grades the
record of them.
"""

from __future__ import annotations

from .service import CommerceService
from .store import canonical_json


def run_core_walk(service: CommerceService):
    """Run the core walk over ``service``'s store; return the board."""
    service.generate_dataset()
    service.generate_product_images()
    service.ledger.compute()
    service.diagnose_listing_quality()
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
    board = service.derive_board()
    service.subscribe_digest("parity-owner", "owner", "daily")
    service.subscribe_digest("parity-keeper", "bookkeeper", "daily")
    service.subscribe_digest("parity-operator", "operator", "weekly")
    service.generate_digests("daily")
    service.generate_digests("weekly")
    pack = service.generate_review_pack(board.payload["as_of"][:7])
    service.export_review_pack(pack.artefact_id)
    service.publish_review_pack(pack.artefact_id, "parity-owner")
    return board


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
