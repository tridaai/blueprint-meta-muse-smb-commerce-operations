"""The monthly review pack: the month's record, exported and gated.

Round 17 assembles the artefact an owner would hand to an
accountant, a lender or a buyer: one stated month's operating
record, folded in code from the computed artefacts — the
headline and detail of the head ledger, the diagnosis findings
the head plan ranks, the listing-quality summary, the approval
picture, and the cost summary. Like the digest (Round 14) it is
stored, insert-only and content-addressed (Law 4); unlike a
digest it is assembled whole, not per role — a review pack is
the record as the owner sees it, and its reads are gated by its
content exactly as a digest's are (see below).

Every figure cites its source
-----------------------------
The pack carries no number of its own (Law 1). The headline
figures are copied from the head ledger with their citations on
their face; the SKU and ad-set detail is copied from the same
ledger; the findings are the board's ranked entries, figures
copied from the finding artefacts they cite; the listing
section restates the head listing diagnosis's findings and
counts; the costs restates the cost analytics summary. The
pack pins the heads it folded — ledger, plan, each diagnosis
family, listing, and the latest approval and rejection acts —
so movement after the fold is detectable, and ``verify``
re-performs the pack by a second route: figure re-resolution
against every cited artefact, then a fresh fold compared
section by section, pin by pin.

The export: provenance appendix and checksums
---------------------------------------------
A pack can be exported as one self-contained JSON document,
rendered deterministically from the pack artefact, stored
through the object-store port (the bytes never live in the
database) and recorded as an export artefact naming the pack,
the object key, and the body's SHA-256. The document carries:

- a **provenance appendix** — every figure in the pack (the
  headline figures, the detail rows' figures, the finding
  figures, the cost totals) listed with the artefact it came
  from, the field it was copied from, and that artefact's
  as-of. The cost totals' source is named honestly: the model
  call ledger, re-performed by the analytics — there is no
  single artefact they were copied from, and the appendix does
  not invent one;
- a **checksum manifest** — a SHA-256 over the canonical form
  of the pack itself and of every artefact the pack's figures
  cite, so a swapped or edited referenced artefact is
  detectable from the document alone.

``verify_export`` re-performs the export end to end: the stored
bytes must hash to the recorded body checksum, the document's
sections must equal the pack's, its manifest must re-perform
against the stored artefacts, and its appendix must rebuild
from the pack and re-resolve figure by figure. A tampered
export, a swapped referenced artefact, or a figure that no
longer resolves fails with the problem named.

The publication gate
--------------------
Generation is not publication. A pack reads as a **draft**
until a named human publishes it: a publication act, recorded
the way Round 8's approval acts are — the publisher's name, the
pack's artefact id and its content hash, and a snapshot of the
pack's pins at publication time. The access pack reserves the
act to the owner (``packs:publish``), the act must name the
human who acted, and a refused attempt is recorded as a
publication-refusal artefact and raised, exactly as a refused
approval is. Published status is derived from the record —
there is no flag to edit — and a publication whose pack has
since been superseded (a newer pack for the same month) or
whose pinned heads have moved is derived **stale** and shown
stale, never clean. Publication records the pack as the
month's published review *inside the record*: distributing the
export — emailing it to the accountant — is something leaving
the building, which Law 2 reserves and this round does not
build; an unpublished pack's export reads as a draft for the
same reason.

Reads are gated by content
--------------------------
A caller reads a pack when they hold ``packs:read`` (every
staff role — the kind gate) **and** every permission the pack's
included sections required: ``ledger:read`` for the headline
and detail, ``plan:read`` for the findings, ``diagnosis:read``
for the listing section, ``approvals:read`` for the approval
picture, ``costs:read`` for the costs. In practice the owner
and the operator can read a full pack; a caller missing any
section's permission — the bookkeeper lacks the plan and
approvals permissions, the viewer lacks them all — gets the
absent answer, indistinguishable from a missing pack (Round
13's existence hiding). An export is gated by its pack's
content the same way, and a publication act or refusal reads
only to a caller who may read its pack.

The grain limit, stated
-----------------------
Figures are the record's computed figures as of the head
ledger's as-of. The record carries no per-month ledger, so the
month is the pinned reporting label the pack is folded under —
stated on its face — not a recomputation over the month's
dates. It is the same grain limit Rounds 12 and 14 state for
costs and digests.
"""

from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy

from .access import Caller
from .board import BoardService
from .costs import CostAnalytics
from .listing import LISTING_KIND
from .models import Artefact
from .objectstore import ObjectStore
from .plan import FAMILY_KINDS, _finding_key
from .store import SqlStore, canonical_json

PACK_KIND = "review_pack"
EXPORT_KIND = "review_pack_export"
PUBLICATION_KIND = "review_pack_publication"
PUBLICATION_REFUSAL_KIND = "review_pack_publication_refusal"
PACK_PACK_VERSION = "2026.11.1"

GRAIN = ("figures are the record's computed figures as of the"
         " head ledger's as-of; the month is the pinned reporting"
         " label, not a per-month recomputation — the record"
         " carries no per-month ledger")

# The pack's sections, in payload order, and the pack permission
# each requires of any caller who would read the pack.
SECTION_PERMISSIONS: tuple[tuple[str, str], ...] = (
    ("headline", "ledger:read"),
    ("detail", "ledger:read"),
    ("findings", "plan:read"),
    ("stock_risks", "plan:read"),
    ("listing", "diagnosis:read"),
    ("approvals", "approvals:read"),
    ("costs", "costs:read"),
)

_MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")

_APPROVAL_COUNTS = (
    "awaiting_approval", "approved", "approved_stale",
    "rejected", "blocked",
)

# The detail rows' figure fields, enumerated into the export's
# provenance appendix. Identity and date fields are not figures.
_SKU_FIGURES = (
    "gross_revenue", "refunds", "net_revenue", "units_sold",
    "units_refunded", "net_units", "on_hand", "cover_days",
)
_AD_FIGURES = ("spend", "attributed_revenue", "roas")


def pack_content_hash(pack: Artefact) -> str:
    """The content hash a publication act binds to: the SHA-256
    of the pack artefact's canonical payload (the recipe Round 8
    binds approval acts with)."""
    return hashlib.sha256(
        canonical_json(pack.payload).encode("utf-8")).hexdigest()


def artefact_checksum(artefact: Artefact) -> str:
    """The checksum the export manifest carries for one
    artefact: SHA-256 over its canonical form — id, kind,
    payload and input fingerprint."""
    return hashlib.sha256(canonical_json({
        "artefact_id": artefact.artefact_id,
        "kind": artefact.kind,
        "payload": artefact.payload,
        "input_fingerprint": artefact.input_fingerprint,
    }).encode("utf-8")).hexdigest()


def pack_visible(pack: Artefact, caller: Caller) -> bool:
    """Whether ``caller`` may read ``pack``: identified, holds
    ``packs:read``, and holds every permission the sections
    actually present in the payload required."""
    if pack.kind != PACK_KIND:
        return False
    if not caller.identified or not caller.allows("packs:read"):
        return False
    included = pack.payload.get("sections_included", [])
    return all(
        caller.allows(permission)
        for name, permission in SECTION_PERMISSIONS
        if name in included
    )


def export_visible(export: Artefact, pack: Artefact | None,
                   caller: Caller) -> bool:
    """Whether ``caller`` may read ``export``: the export
    carries its pack's whole content, so the pack's content
    gate decides."""
    if export.kind != EXPORT_KIND or pack is None:
        return False
    return pack_visible(pack, caller)


class PackPublicationRefused(Exception):
    """A publication attempt the service refused, with the
    refusal artefact that records why."""

    def __init__(self, refusal: Artefact):
        super().__init__(
            f"publication refused:"
            f" {', '.join(refusal.payload['problems'])}"
        )
        self.refusal = refusal


class ReviewPackService:
    def __init__(self, store: SqlStore, board: BoardService,
                 objects: ObjectStore):
        self._store = store
        self._board = board
        self._objects = objects
        self._costs = CostAnalytics(store)

    # -- assembly ---------------------------------------------------------

    def _build(self, month: str) -> Artefact:
        board = self._board.derive()
        bp = board.payload
        ledger = self._store.get_artefact(bp["ledger_artefact_id"])
        listing = self._store.latest_artefact(LISTING_KIND)
        summary = self._costs.summary()
        counts = bp["headline"]["counts"]
        included = ["headline", "detail", "findings", "stock_risks"]
        payload: dict = {
            "pack": "monthly_review_pack",
            "pack_version": PACK_PACK_VERSION,
            "month": month,
            "as_of": bp["as_of"],
            "grain": GRAIN,
            "pins": {**bp["pins"],
                     "listing_head": (
                         listing.artefact_id if listing else None)},
            "headline": {
                "ledger_artefact_id": bp["headline"]["ledger_artefact_id"],
                "as_of": bp["headline"]["as_of"],
                "figures": deepcopy(bp["headline"]["figures"]),
            },
            "detail": {
                "ledger_artefact_id": ledger.artefact_id,
                "skus": deepcopy(ledger.payload["skus"]),
                "ad_sets": deepcopy(ledger.payload["ad_sets"]),
            },
            "findings": deepcopy(bp["findings"]),
            "stock_risks": deepcopy(bp["stock_risks"]),
        }
        if listing is not None:
            included.append("listing")
            payload["listing"] = {
                "listing_artefact_id": listing.artefact_id,
                "as_of": listing.payload["as_of"],
                "pack_version": listing.payload["pack_version"],
                "counts": {
                    "findings": len(listing.payload["findings"]),
                    "unjudged": len(listing.payload["unjudged"]),
                },
                "images": deepcopy(listing.payload["images"]),
                "findings": deepcopy(listing.payload["findings"]),
            }
        included += ["approvals", "costs"]
        payload["approvals"] = {
            "awaiting_approval": deepcopy(bp["awaiting_approval"]),
            "approved": deepcopy(bp["approved"]),
            "rejected": deepcopy(bp["rejected"]),
            "blocked": deepcopy(bp["blocked"]),
            "counts": {name: counts[name] for name in _APPROVAL_COUNTS},
        }
        payload["costs"] = {
            "rate_card_version": summary["rate_card_version"],
            "total": summary["total"],
            "by_tier": summary["by_tier"],
            "reconcile_problems": summary["reconcile_problems"],
        }
        payload["sections_included"] = included
        fingerprint = hashlib.sha256(canonical_json({
            "pins": payload["pins"], "month": month,
            "pack_version": PACK_PACK_VERSION,
        }).encode()).hexdigest()
        material = canonical_json(
            {"payload": payload, "fp": fingerprint})
        artefact_id = (
            "RPK-" + hashlib.sha256(material.encode()).hexdigest()[:20]
        )
        return Artefact(
            artefact_id=artefact_id, kind=PACK_KIND,
            payload=payload, input_fingerprint=fingerprint,
        )

    def generate(self, month: str) -> Artefact:
        """Fold the review pack for one stated month. Re-running
        over an unmoved record returns the pack already on the
        record — the fold inserts nothing twice."""
        if not _MONTH_RE.match(month or ""):
            raise ValueError(f"month_not_a_month:{month}")
        pack = self._build(month)
        existing = self._store.get_artefact(pack.artefact_id)
        if existing is not None:
            return existing
        self._store.insert_artefact(pack)
        return pack

    # -- pack verification --------------------------------------------------

    def verify(self, pack: Artefact) -> list[str]:
        """Re-perform a pack end to end: its headline and finding
        figures re-resolve against the artefacts they cite, its
        listing section re-resolves against the listing artefact
        it restates, its pins are the current heads, and every
        section recomposes identically from a fresh fold."""
        if pack.kind != PACK_KIND:
            return [f"not_a_review_pack:{pack.kind}"]
        payload = pack.payload
        problems: list[str] = []
        month = payload.get("month")
        if not _MONTH_RE.match(month or ""):
            return ["pack_month_malformed"]
        headline = payload.get("headline", {})
        ledger = self._store.get_artefact(
            headline.get("ledger_artefact_id", ""))
        if ledger is None or ledger.kind != "ledger":
            problems.append("pack_ledger_missing")
            ledger = None
        if ledger is not None:
            for figure in headline.get("figures", []):
                if figure.get(
                        "source_artefact_id") != ledger.artefact_id:
                    problems.append(
                        "pack_citation_error:headline:"
                        f"{figure.get('name')}")
                elif ledger.payload.get(
                        figure.get("source_field")) != (
                        figure.get("value")):
                    problems.append(
                        "pack_figure_mismatch:headline:"
                        f"{figure.get('name')}")
            detail = payload.get("detail", {})
            if detail.get("ledger_artefact_id") != ledger.artefact_id:
                problems.append("pack_detail_citation_error")
            else:
                if canonical_json(detail.get("skus")) != canonical_json(
                        ledger.payload.get("skus")):
                    problems.append("pack_figure_mismatch:detail:skus")
                if canonical_json(detail.get("ad_sets")) != canonical_json(
                        ledger.payload.get("ad_sets")):
                    problems.append(
                        "pack_figure_mismatch:detail:ad_sets")
        for entry in payload.get("findings", []):
            if entry.get("rank") is None:
                continue
            finding_set = self._store.get_artefact(
                entry.get("diagnosis_artefact_id", ""))
            if finding_set is None:
                problems.append(
                    f"pack_citation_missing:{entry.get('finding_key')}")
                continue
            match = next(
                (f for f in finding_set.payload.get("findings", [])
                 if _finding_key(entry["family"], f)
                 == entry.get("finding_key")),
                None,
            )
            if match is None:
                problems.append(
                    f"pack_finding_missing:{entry.get('finding_key')}")
                continue
            for field, value in entry.get("figures", {}).items():
                if match.get(field) != value:
                    problems.append(
                        "pack_figure_mismatch:"
                        f"{entry.get('finding_key')}:{field}")
        listing_section = payload.get("listing")
        if listing_section is not None:
            listing = self._store.get_artefact(
                listing_section.get("listing_artefact_id", ""))
            if listing is None or listing.kind != LISTING_KIND:
                problems.append("pack_listing_missing")
            else:
                if canonical_json(
                        listing_section.get("findings")) != canonical_json(
                        listing.payload.get("findings")):
                    problems.append(
                        "pack_figure_mismatch:listing:findings")
                if canonical_json(
                        listing_section.get("images")) != canonical_json(
                        listing.payload.get("images")):
                    problems.append("pack_figure_mismatch:listing:images")
                counts = listing_section.get("counts", {})
                if (counts.get("findings")
                        != len(listing.payload.get("findings", []))
                        or counts.get("unjudged")
                        != len(listing.payload.get("unjudged", []))):
                    problems.append("pack_figure_mismatch:listing:counts")
        expected = self._build(month).payload
        pins = payload.get("pins", {})
        for name, value in expected["pins"].items():
            if pins.get(name) != value:
                problems.append(f"pack_pin_mismatch:{name}")
        if payload.get("as_of") != expected["as_of"]:
            problems.append("pack_as_of_mismatch")
        if payload.get("sections_included") != (
                expected["sections_included"]):
            problems.append("pack_sections_mismatch")
        for name, _permission in SECTION_PERMISSIONS:
            if canonical_json(payload.get(name)) != canonical_json(
                    expected.get(name)):
                problems.append(f"pack_section_mismatch:{name}")
        return problems

    # -- the export ---------------------------------------------------------

    def _referenced(self, pack: Artefact) -> list[Artefact]:
        """Every artefact the pack's figures cite, the pack
        itself first: the ledger, the plan, each finding set the
        findings cite, and the listing artefact when present."""
        payload = pack.payload
        ids = [pack.artefact_id,
               payload["headline"]["ledger_artefact_id"],
               payload["pins"]["plan_head"]]
        for entry in payload.get("findings", []):
            cited = entry.get("diagnosis_artefact_id")
            if cited and cited not in ids:
                ids.append(cited)
        listing = payload.get("listing")
        if listing is not None:
            ids.append(listing["listing_artefact_id"])
        artefacts = []
        for artefact_id in ids:
            artefact = self._store.get_artefact(artefact_id)
            if artefact is not None:
                artefacts.append(artefact)
        return artefacts

    def _manifest(self, pack: Artefact) -> list[dict]:
        entries = [
            {"artefact_id": artefact.artefact_id,
             "kind": artefact.kind,
             "sha256": artefact_checksum(artefact)}
            for artefact in self._referenced(pack)
        ]
        return sorted(entries, key=lambda e: e["artefact_id"])

    def _appendix(self, pack: Artefact) -> list[dict]:
        """The provenance appendix: every figure in the pack,
        with the artefact it was copied from, the field, and
        that artefact's as-of. Cost totals name their real
        source — the model call ledger — because no single
        artefact carries them."""
        payload = pack.payload
        entries: list[dict] = []
        ledger_id = payload["headline"]["ledger_artefact_id"]
        ledger = self._store.get_artefact(ledger_id)
        ledger_as_of = (
            ledger.payload["as_of"] if ledger is not None
            else payload["as_of"])
        for figure in payload["headline"]["figures"]:
            entries.append({
                "figure": f"headline.{figure['name']}",
                "value": figure["value"],
                "source_artefact_id": figure["source_artefact_id"],
                "source_field": figure["source_field"],
                "source_as_of": ledger_as_of,
            })
        for row in payload["detail"]["skus"]:
            for field in _SKU_FIGURES:
                entries.append({
                    "figure": f"detail.skus.{row['sku']}.{field}",
                    "value": row[field],
                    "source_artefact_id": ledger_id,
                    "source_field": f"skus.{row['sku']}.{field}",
                    "source_as_of": ledger_as_of,
                })
        for row in payload["detail"]["ad_sets"]:
            for field in _AD_FIGURES:
                entries.append({
                    "figure": f"detail.ad_sets.{row['ad_set']}.{field}",
                    "value": row[field],
                    "source_artefact_id": ledger_id,
                    "source_field": f"ad_sets.{row['ad_set']}.{field}",
                    "source_as_of": ledger_as_of,
                })
        as_of_by_id: dict[str, str] = {}
        for entry in payload["findings"]:
            if entry.get("rank") is None:
                continue
            set_id = entry["diagnosis_artefact_id"]
            if set_id not in as_of_by_id:
                finding_set = self._store.get_artefact(set_id)
                as_of_by_id[set_id] = (
                    finding_set.payload.get("as_of")
                    if finding_set is not None else payload["as_of"])
            for field, value in entry.get("figures", {}).items():
                entries.append({
                    "figure": f"findings.{entry['finding_key']}.{field}",
                    "value": value,
                    "source_artefact_id": set_id,
                    "source_field": field,
                    "source_as_of": as_of_by_id[set_id],
                    "family": entry["family"],
                    "finding_key": entry["finding_key"],
                })
        costs = payload["costs"]
        entries.append({
            "figure": "costs.total.cost_usd",
            "value": costs["total"]["cost_usd"],
            "source_artefact_id": None,
            "source": "model_call_ledger",
            "source_field": "total.cost_usd",
            "source_as_of": payload["as_of"],
        })
        for tier, bucket in costs["by_tier"].items():
            entries.append({
                "figure": f"costs.by_tier.{tier}.cost_usd",
                "value": bucket["cost_usd"],
                "source_artefact_id": None,
                "source": "model_call_ledger",
                "source_field": f"by_tier.{tier}.cost_usd",
                "source_as_of": payload["as_of"],
            })
        return entries

    def _document(self, pack: Artefact) -> dict:
        payload = pack.payload
        status = self.publication_status(pack)
        return {
            "document": "monthly_review_pack_export",
            "format_version": PACK_PACK_VERSION,
            "month": payload["month"],
            "as_of": payload["as_of"],
            "pack_artefact_id": pack.artefact_id,
            "publication": {
                "status": status["status"],
                "publication_artefact_id":
                    status["publication_artefact_id"],
                "publisher": status["publisher"],
            },
            "sections": {
                name: deepcopy(payload[name])
                for name, _permission in SECTION_PERMISSIONS
                if name in payload
            },
            "provenance_appendix": self._appendix(pack),
            "checksum_manifest": self._manifest(pack),
        }

    def export(self, pack_artefact_id: str) -> Artefact:
        """Render the pack's export document, store its bytes
        through the object-store port, and record the export
        artefact. Re-exporting an unmoved pack in the same
        publication state returns the export already on the
        record."""
        pack = self._store.get_artefact(pack_artefact_id)
        if pack is None or pack.kind != PACK_KIND:
            raise ValueError("pack_missing_or_not_a_pack")
        document = self._document(pack)
        body = (canonical_json(document) + "\n").encode("utf-8")
        body_checksum = hashlib.sha256(body).hexdigest()
        object_key = (
            f"review-pack-exports/{pack.artefact_id}/{body_checksum}.json"
        )
        stored_checksum = self._objects.put(object_key, body)
        payload = {
            "export": "review_pack_export",
            "pack_version": PACK_PACK_VERSION,
            "pack_artefact_id": pack.artefact_id,
            "month": pack.payload["month"],
            "format": "application/json",
            "object_key": object_key,
            "body_checksum": stored_checksum,
            "referenced_artefacts": document["checksum_manifest"],
            "publication": document["publication"],
        }
        fingerprint = hashlib.sha256(canonical_json({
            "pack": pack.artefact_id,
            "pack_content_hash": pack_content_hash(pack),
            "body_checksum": stored_checksum,
        }).encode()).hexdigest()
        material = canonical_json(
            {"payload": payload, "fp": fingerprint})
        artefact_id = (
            "RPX-" + hashlib.sha256(material.encode()).hexdigest()[:20]
        )
        existing = self._store.get_artefact(artefact_id)
        if existing is not None:
            return existing
        artefact = Artefact(
            artefact_id=artefact_id, kind=EXPORT_KIND,
            payload=payload, input_fingerprint=fingerprint,
        )
        self._store.insert_artefact(artefact)
        return artefact

    def verify_export(self, export: Artefact) -> list[str]:
        """Re-perform an export end to end: the stored bytes
        must hash to the recorded body checksum, the document
        must be the pack's document (sections, appendix and
        manifest compared against a fresh rendering), every
        manifest checksum must re-perform against the stored
        artefact it names, and every appendix figure must
        re-resolve against its source."""
        if export.kind != EXPORT_KIND:
            return [f"not_a_review_pack_export:{export.kind}"]
        payload = export.payload
        problems: list[str] = []
        pack = self._store.get_artefact(
            payload.get("pack_artefact_id", ""))
        if pack is None or pack.kind != PACK_KIND:
            return ["export_pack_missing"]
        try:
            body = self._objects.get(payload.get("object_key", ""))
        except (FileNotFoundError, KeyError, OSError):
            return ["export_object_missing"]
        if hashlib.sha256(body).hexdigest() != payload.get(
                "body_checksum"):
            problems.append("export_checksum_mismatch")
        try:
            document = json.loads(body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            problems.append("export_document_unreadable")
            return problems
        if document.get("pack_artefact_id") != pack.artefact_id:
            problems.append("export_pack_mismatch")
        if document.get("month") != pack.payload.get("month"):
            problems.append("export_month_mismatch")
        for name, _permission in SECTION_PERMISSIONS:
            if canonical_json(
                    document.get("sections", {}).get(name)
            ) != canonical_json(pack.payload.get(name)):
                problems.append(f"export_section_mismatch:{name}")
        if canonical_json(document.get("publication")) != canonical_json(
                payload.get("publication")):
            problems.append("export_publication_mismatch")
        if canonical_json(
                document.get("checksum_manifest")) != canonical_json(
                payload.get("referenced_artefacts")):
            problems.append("export_manifest_mismatch")
        for entry in document.get("checksum_manifest", []):
            referenced = self._store.get_artefact(
                entry.get("artefact_id", ""))
            if referenced is None:
                problems.append(
                    "export_referenced_missing:"
                    f"{entry.get('artefact_id')}")
            elif artefact_checksum(referenced) != entry.get("sha256"):
                problems.append(
                    "export_referenced_checksum_mismatch:"
                    f"{entry.get('artefact_id')}")
        if canonical_json(
                document.get("checksum_manifest")) != canonical_json(
                self._manifest(pack)):
            problems.append("export_manifest_stale")
        appendix = self._appendix(pack)
        if canonical_json(
                document.get("provenance_appendix")) != canonical_json(
                appendix):
            problems.append("export_appendix_mismatch")
        problems.extend(self._resolve_appendix(appendix))
        return problems

    def _resolve_appendix(self, appendix: list[dict]) -> list[str]:
        """Re-resolve every appendix figure against its source:
        ledger fields for the headline and detail figures, the
        cited finding for finding figures, a fresh cost summary
        for the cost totals."""
        problems: list[str] = []
        summary: dict | None = None
        for entry in appendix:
            figure = entry["figure"]
            source_id = entry.get("source_artefact_id")
            if source_id is None:
                if summary is None:
                    summary = self._costs.summary()
                node: object = summary
                for part in entry["source_field"].split("."):
                    node = (
                        node.get(part) if isinstance(node, dict)
                        else None)
                if node is None or str(node) != str(entry["value"]):
                    problems.append(f"export_figure_mismatch:{figure}")
                continue
            source = self._store.get_artefact(source_id)
            if source is None:
                problems.append(f"export_figure_source_missing:{figure}")
                continue
            if source.kind == "ledger":
                parts = entry["source_field"].split(".")
                if len(parts) == 1:
                    value = source.payload.get(parts[0])
                elif parts[0] == "skus":
                    row = next(
                        (r for r in source.payload.get("skus", [])
                         if r["sku"] == parts[1]), None)
                    value = row.get(parts[2]) if row else None
                elif parts[0] == "ad_sets":
                    row = next(
                        (r for r in source.payload.get("ad_sets", [])
                         if r["ad_set"] == parts[1]), None)
                    value = row.get(parts[2]) if row else None
                else:
                    value = None
                if value is None or str(value) != str(entry["value"]):
                    problems.append(f"export_figure_mismatch:{figure}")
            else:
                match = next(
                    (f for f in source.payload.get("findings", [])
                     if _finding_key(entry["family"], f)
                     == entry["finding_key"]),
                    None,
                )
                if match is None or str(
                        match.get(entry["source_field"])) != str(
                        entry["value"]):
                    problems.append(f"export_figure_mismatch:{figure}")
        return problems

    # -- publication ---------------------------------------------------------

    def record_refusal(self, action: str, pack_id: str, actor: str,
                       problems: list[str]) -> Artefact:
        """Record a refused publication attempt from outside the
        act flow — the access layer's role gate refuses before
        any pack lookup, and the refusal is recorded here exactly
        like the flow's own refusals (Round 8's pattern)."""
        return self._refuse(action, pack_id, actor, problems)

    def _refuse(self, action: str, pack_id: str, actor: str,
                problems: list[str]) -> Artefact:
        payload = {
            "action": action,
            "pack_artefact_id": pack_id,
            "actor": actor,
            "problems": problems,
        }
        fingerprint = hashlib.sha256(
            canonical_json(payload).encode("utf-8")).hexdigest()
        material = canonical_json(
            {"payload": payload, "fp": fingerprint})
        artefact_id = (
            "RPR-" + hashlib.sha256(material.encode()).hexdigest()[:20]
        )
        existing = self._store.get_artefact(artefact_id)
        if existing is not None:
            return existing
        refusal = Artefact(
            artefact_id=artefact_id, kind=PUBLICATION_REFUSAL_KIND,
            payload=payload, input_fingerprint=fingerprint,
        )
        self._store.insert_artefact(refusal)
        return refusal

    def _fail(self, pack_id: str, actor: str, problems: list[str]):
        raise PackPublicationRefused(
            self._refuse("publish", pack_id, actor, problems))

    def publish(self, pack_artefact_id: str,
                publisher: str) -> Artefact:
        """Record a named publication act for one pack, or
        refuse the attempt with the reason recorded: the pack
        must exist, must re-perform clean, and must not already
        carry a publication."""
        if not publisher or not publisher.strip():
            self._fail(pack_artefact_id, publisher or "",
                       ["publisher_missing"])
        pack = self._store.get_artefact(pack_artefact_id)
        if pack is None or pack.kind != PACK_KIND:
            self._fail(pack_artefact_id, publisher,
                       ["pack_missing_or_not_a_pack"])
        if self.publication_for(pack_artefact_id) is not None:
            self._fail(pack_artefact_id, publisher,
                       ["already_published"])
        problems = self.verify(pack)
        if problems:
            self._fail(pack_artefact_id, publisher,
                       ["pack_does_not_verify"] + problems)
        payload = {
            "action": "publish",
            "pack_artefact_id": pack.artefact_id,
            "month": pack.payload["month"],
            "pack_content_hash": pack_content_hash(pack),
            "publisher": publisher,
            "status": "published",
            "display_status": "published",
            "bound_pins": deepcopy(pack.payload["pins"]),
        }
        fingerprint = hashlib.sha256(
            canonical_json(payload).encode("utf-8")).hexdigest()
        material = canonical_json(
            {"payload": payload, "fp": fingerprint})
        artefact_id = (
            "RPP-" + hashlib.sha256(material.encode()).hexdigest()[:20]
        )
        existing = self._store.get_artefact(artefact_id)
        if existing is not None:
            return existing
        act = Artefact(
            artefact_id=artefact_id, kind=PUBLICATION_KIND,
            payload=payload, input_fingerprint=fingerprint,
        )
        self._store.insert_artefact(act)
        return act

    def publication_for(self, pack_artefact_id: str) -> Artefact | None:
        """The pack's publication act, when one exists AND
        re-verifies. A forged act that does not verify confers
        nothing."""
        for act in self._store.list_artefacts(PUBLICATION_KIND):
            if act.payload.get("pack_artefact_id") != pack_artefact_id:
                continue
            if not self.verify_publication(act):
                return act
        return None

    def publication_status(self, pack: Artefact) -> dict:
        """The pack's publication state, derived from the
        record: draft until a verifying act exists; published
        from the act, marked stale with the moved inputs named
        when anything it binds has moved."""
        base = {
            "pack_artefact_id": pack.artefact_id,
            "month": pack.payload.get("month"),
        }
        act = self.publication_for(pack.artefact_id)
        if act is None:
            return {**base, "status": "draft",
                    "publication_artefact_id": None,
                    "publisher": None, "stale": False,
                    "moved_inputs": []}
        moved = self.publication_staleness(act)
        return {**base, "status": "published",
                "publication_artefact_id": act.artefact_id,
                "publisher": act.payload.get("publisher"),
                "stale": bool(moved), "moved_inputs": moved}

    def publication_staleness(self, act: Artefact) -> list[str]:
        """The moved inputs for one publication act, derived at
        read time: its pack superseded by a newer pack for the
        same month, or any head it pinned since moved."""
        payload = act.payload
        moved: list[str] = []
        pack = self._store.get_artefact(
            payload.get("pack_artefact_id", ""))
        if pack is None:
            return [f"pack:{payload.get('pack_artefact_id')}"]
        if pack_content_hash(pack) != payload.get("pack_content_hash"):
            moved.append(f"pack:{pack.artefact_id}")
        packs = self._store.list_artefacts(PACK_KIND)
        index = next(
            (i for i, p in enumerate(packs)
             if p.artefact_id == pack.artefact_id),
            None,
        )
        if index is not None:
            for later in packs[index + 1:]:
                if later.payload.get("month") == payload.get("month"):
                    moved.append(f"pack_superseded:{later.artefact_id}")
                    break
        bound = payload.get("bound_pins", {})
        heads = {
            "ledger_head": self._store.latest_artefact("ledger"),
            "plan_head": self._store.latest_artefact("growth_plan"),
            "listing_head": self._store.latest_artefact(LISTING_KIND),
            "approval_head": self._store.latest_artefact(
                "approval_act"),
            "rejection_head": self._store.latest_artefact(
                "rejection_act"),
        }
        for name, head in heads.items():
            current = head.artefact_id if head is not None else None
            if bound.get(name) != current:
                moved.append(f"pin_moved:{name}")
        diagnosis_heads = bound.get("diagnosis_heads", {})
        for family, kind in FAMILY_KINDS.items():
            head = self._store.latest_artefact(kind)
            current = head.artefact_id if head is not None else None
            if diagnosis_heads.get(family) != current:
                moved.append(f"pin_moved:diagnosis_heads.{family}")
        return moved

    def verify_publication(self, act: Artefact) -> list[str]:
        """Re-perform a publication act: its pack and hash
        bindings, its publisher, its honesty of state, and the
        pins it bound. Staleness is NOT a verify problem — a
        stale act is a true record; the read surface marks it."""
        if act.kind != PUBLICATION_KIND:
            return [f"not_a_publication_act:{act.kind}"]
        payload = act.payload
        problems: list[str] = []
        pack = self._store.get_artefact(
            payload.get("pack_artefact_id", ""))
        if pack is None or pack.kind != PACK_KIND:
            problems.append("publication_pack_missing")
            return problems
        if payload.get("pack_content_hash") != pack_content_hash(pack):
            problems.append("publication_pack_hash_mismatch")
        if payload.get("month") != pack.payload.get("month"):
            problems.append("publication_month_mismatch")
        if not payload.get("publisher"):
            problems.append("publication_publisher_missing")
        if payload.get("status") != "published":
            problems.append("publication_status_not_published")
        if payload.get("display_status") != "published":
            problems.append("publication_display_status_wrong")
        if canonical_json(payload.get("bound_pins")) != canonical_json(
                pack.payload.get("pins")):
            problems.append("publication_bound_pins_mismatch")
        return problems
