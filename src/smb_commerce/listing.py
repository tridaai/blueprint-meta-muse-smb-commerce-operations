"""Listing quality: the catalogue's images, measured and judged.

Round 16's diagnosis, a peer of the packs in ``diagnosis.py``
under its own pack — not a sixth plan family. The plan pack's
families are versioned data graded against planted truth, each
carrying a money or unit exposure the plan ranks by; a listing
finding carries no such exposure, and the plan pack is not
revised this round. What listing quality shares with the other
diagnoses is the discipline (Law 5): a versioned pack with
stated thresholds, two routes that must agree before a finding
set may exist, and a verifier that re-performs.

The pack judges each catalogue SKU's current listing image —
the latest ``product_image`` fact for the SKU:

- **Deterministic checks, computed in code.** No image at all
  (``missing_image``); an image fact whose provenance is blank
  (``provenance_incomplete`` — the intake port refuses these, so
  one on the record arrived behind the port's back); stored
  bytes whose hash is not the hash the fact records
  (``hash_mismatch`` — the bytes are not the recorded image, and
  nothing further is judged about them); dimensions below the
  pack's minimums (``below_min_dimensions``); a filename whose
  claimed format is not the sniffed format (``format_mismatch``).
- **Vision judgement, labelled as such.** What the image
  *shows* is the model's judgement, taken through the governed
  port (``analyse_image``) and recorded as an insert-only
  ``image_vision_judgement`` artefact stamped with the profile,
  model, tier, pack version and call id that produced it. A
  judged class that is not the SKU's declared class is a
  ``vision_mismatch`` finding, and the finding carries the
  judgement's identity on its face — wherever a vision finding
  surfaces, its model-judged nature and provenance surface with
  it. An image the vision call cannot judge (an unparseable
  reply, or no subject embedded for the offline double to read)
  is named in ``unjudged`` with the reason — never silently
  passed, never guessed at.

The two routes differ the way the other packs' routes do.
Route 1 reads the facts' recorded measurements and the stored
judgement artefacts (creating a judgement, through the governed
port, where none exists). Route 2 re-measures the stored bytes
fresh and — on the offline profile — re-derives the subject
from the bytes themselves; on a hosted profile it falls back to
the stored judgements, because a verifier cannot re-call a
hosted model and no live call exists to re-perform (the stated
limit of every hosted verification in this codebase). A
disagreement — findings, unjudged, or a recorded measurement
the bytes no longer re-perform to — refuses the whole set: a
refusal artefact is recorded and ``ListingRefused`` is raised.
"""

from __future__ import annotations

import hashlib
import json

from . import generator
from .images import IMAGE_KIND, measure_image
from .modelport import GovernedModelPort
from .models import Artefact, Fact
from .objectstore import ObjectStore
from .store import SqlStore, canonical_json

LISTING_PACK = {
    "version": "2026.11.1",
    "min_width": 800,
    "min_height": 800,
    "measured_formats": ["png", "jpeg", "gif"],
}

LISTING_PACKS = {LISTING_PACK["version"]: LISTING_PACK}

LISTING_KIND = "diagnosis_listing_quality"
JUDGEMENT_KIND = "image_vision_judgement"
REFUSAL_KIND = "diagnosis_refusal"

VISION_SYSTEM = (
    "You judge product listing images for a home-goods "
    "catalogue. Answer with one JSON document only, naming "
    "the product class the image shows."
)


class ListingRefused(Exception):
    """The listing finding set failed re-performance and was
    refused whole, like every diagnosis refusal."""

    def __init__(self, refusal: Artefact):
        super().__init__(
            f"diagnosis refused: listing_quality: "
            f"{', '.join(refusal.payload['problems'])}"
        )
        self.refusal = refusal


def _vision_prompt(fact: Fact) -> str:
    p = fact.payload
    return (
        f"Listing image for SKU {p['sku']}, declared class "
        f"{p['declared_class']}. What product class does this "
        f"image show? Reply with "
        f"{{\"subject_class\": \"<class>\"}} and nothing else."
    )


def _latest_images(store: SqlStore) -> dict[str, Fact]:
    """The current listing image per SKU: the latest
    product_image fact in record order, the way the latest
    inventory position is a SKU's position."""
    latest: dict[str, Fact] = {}
    for fact in store.list_facts(IMAGE_KIND):
        latest[fact.payload["sku"]] = fact
    return latest


def _classify(rows: list[dict], pack: dict) -> tuple[list[dict], list[dict]]:
    """Per-SKU judged rows -> (findings, unjudged). Shared
    shaping: the routes differ in how a row is derived, never in
    how it is judged."""
    findings, unjudged = [], []
    for row in rows:
        sku = row["sku"]
        if row["fact_id"] is None:
            findings.append({
                "class": "missing_image", "sku": sku, "name": row["name"],
            })
            continue
        if row["provenance_incomplete"]:
            findings.append({
                "class": "provenance_incomplete", "sku": sku,
                "fact_id": row["fact_id"],
            })
        if row["hash_state"] == "mismatch":
            findings.append({
                "class": "hash_mismatch", "sku": sku,
                "fact_id": row["fact_id"],
                "recorded_content_hash": row["content_hash"],
            })
            continue  # the bytes are not the recorded image
        if (row["width"] < pack["min_width"]
                or row["height"] < pack["min_height"]):
            findings.append({
                "class": "below_min_dimensions", "sku": sku,
                "fact_id": row["fact_id"],
                "width": row["width"], "height": row["height"],
                "min_width": pack["min_width"],
                "min_height": pack["min_height"],
            })
        if row["declared_format"] != row["format"]:
            findings.append({
                "class": "format_mismatch", "sku": sku,
                "fact_id": row["fact_id"],
                "declared_format": row["declared_format"],
                "format": row["format"],
            })
        judged = row["judged_class"]
        if judged is None:
            unjudged.append({"sku": sku, "reason": "vision_unjudged"})
        elif judged == "unlabelled":
            unjudged.append({"sku": sku, "reason": "vision_unlabelled"})
        elif judged != row["declared_class"]:
            findings.append({
                "class": "vision_mismatch", "sku": sku,
                "fact_id": row["fact_id"],
                "declared_class": row["declared_class"],
                "judged_class": judged,
                "judgement_artefact_id": row["judgement_id"],
                "judgement_profile": row["judgement_profile"],
                "judgement_model": row["judgement_model"],
                "pack_version": pack["version"],
            })
    findings.sort(key=lambda f: (f["sku"], f["class"]))
    unjudged.sort(key=lambda f: f["sku"])
    return findings, unjudged


class ListingQualityService:
    def __init__(self, store: SqlStore, model: GovernedModelPort,
                 objects: ObjectStore):
        self._store = store
        self._model = model
        self._objects = objects

    # -- vision judgements ---------------------------------------------

    def _stored_judgement(self, fact: Fact) -> Artefact | None:
        for artefact in self._store.list_artefacts(JUDGEMENT_KIND):
            p = artefact.payload
            if (p["image_fact_id"] == fact.fact_id
                    and p["pack_version"] == LISTING_PACK["version"]
                    and p["profile"] == self._model.profile_name):
                return artefact
        return None

    def _judge(self, fact: Fact, data: bytes) -> Artefact:
        """The vision judgement for one image, recorded as an
        insert-only artefact. An existing judgement under this
        pack and profile is reused — the governed port dedupes
        the call the same way, so a re-diagnosis records nothing
        twice."""
        existing = self._stored_judgement(fact)
        if existing is not None:
            return existing
        p = fact.payload
        run_id = f"vision-{p['content_hash'][:20]}"
        reply = self._model.analyse_image(
            run_id, VISION_SYSTEM, _vision_prompt(fact), data,
            p["format"],
        )
        subject = None
        try:
            parsed = json.loads(reply.text)
            if isinstance(parsed, dict) and isinstance(
                    parsed.get("subject_class"), str):
                subject = parsed["subject_class"]
        except json.JSONDecodeError:
            subject = None
        payload = {
            "sku": p["sku"],
            "image_fact_id": fact.fact_id,
            "image_content_hash": p["content_hash"],
            "subject_class": subject,
            "profile": reply.profile,
            "model": reply.model,
            "tier": reply.tier,
            "pack_version": LISTING_PACK["version"],
            "call_id": reply.call_id,
            "judged_via": "vision",
        }
        fingerprint = hashlib.sha256(
            f"{fact.fact_id}|{p['content_hash']}"
            f"|{LISTING_PACK['version']}|{reply.profile}".encode()
        ).hexdigest()
        material = canonical_json({"payload": payload, "fp": fingerprint})
        artefact_id = "IVJ-" + hashlib.sha256(
            material.encode()).hexdigest()[:20]
        stored = self._store.get_artefact(artefact_id)
        if stored is not None:
            return stored
        artefact = Artefact(
            artefact_id=artefact_id, kind=JUDGEMENT_KIND,
            payload=payload, input_fingerprint=fingerprint,
        )
        self._store.insert_artefact(artefact)
        return artefact

    # -- the two routes --------------------------------------------------

    def _rows(self, latest: dict[str, Fact], route: int,
              problems: list[str]) -> list[dict]:
        rows = []
        for spec in generator.SKUS:
            fact = latest.get(spec.sku)
            row = {
                "sku": spec.sku, "name": spec.name, "fact_id": None,
                "provenance_incomplete": False, "hash_state": "ok",
                "content_hash": None, "format": None, "width": None,
                "height": None, "byte_count": None,
                "declared_format": None,
                "declared_class": spec.product_class,
                "judged_class": None, "judgement_id": None,
                "judgement_profile": None, "judgement_model": None,
            }
            rows.append(row)
            if fact is None:
                continue
            p = fact.payload
            row.update(
                fact_id=fact.fact_id,
                provenance_incomplete=not (
                    fact.source_system.strip() and fact.as_of.strip()),
                content_hash=p["content_hash"],
                declared_format=p["declared_format"],
                declared_class=p["declared_class"],
                byte_count=p["byte_count"],
            )
            try:
                data = self._objects.get(p["object_key"])
            except Exception:
                problems.append(f"image_object_missing:{spec.sku}")
                continue
            if hashlib.sha256(data).hexdigest() != p["content_hash"]:
                row["hash_state"] = "mismatch"
                continue
            if route == 1:
                measured = {
                    "format": p["format"], "width": p["width"],
                    "height": p["height"],
                }
            else:
                fresh = measure_image(data)
                measured = {
                    "format": fresh.format, "width": fresh.width,
                    "height": fresh.height,
                }
                if (measured["format"] != p["format"]
                        or measured["width"] != p["width"]
                        or measured["height"] != p["height"]):
                    problems.append(
                        f"listing_measurement_mismatch:{spec.sku}")
            row.update(measured)
            if route == 1:
                judgement = self._judge(fact, data)
                row["judged_class"] = judgement.payload["subject_class"]
                row["judgement_id"] = judgement.artefact_id
                row["judgement_profile"] = judgement.payload["profile"]
                row["judgement_model"] = judgement.payload["model"]
            else:
                judgement = self._stored_judgement(fact)
                if judgement is not None:
                    row["judgement_id"] = judgement.artefact_id
                    row["judgement_profile"] = judgement.payload["profile"]
                    row["judgement_model"] = judgement.payload["model"]
                if self._model.profile_name == "offline":
                    # Re-derived from the bytes, never from the
                    # stored judgement: the second route proper.
                    from .images import embedded_subject

                    row["judged_class"] = (
                        embedded_subject(data) or "unlabelled")
                elif judgement is not None:
                    # A hosted profile cannot be re-called by a
                    # verifier; the stored judgement is the record.
                    row["judged_class"] = judgement.payload["subject_class"]
        return rows

    @staticmethod
    def _summary(rows: list[dict]) -> list[dict]:
        return [
            {
                "sku": row["sku"], "name": row["name"],
                "image_fact_id": row["fact_id"],
                "format": row["format"], "width": row["width"],
                "height": row["height"],
                "byte_count": row["byte_count"],
                "content_hash": row["content_hash"],
                "declared_class": row["declared_class"],
                "judged_class": row["judged_class"],
                "judgement_artefact_id": row["judgement_id"],
            }
            for row in rows
        ]

    def _fingerprint(self, latest: dict[str, Fact],
                     rows: list[dict]) -> str:
        material = canonical_json({
            "images": [
                [sku, fact.fact_id, fact.payload["content_hash"]]
                for sku, fact in sorted(latest.items())
            ],
            "judgements": [row["judgement_id"] for row in rows],
            "profile": self._model.profile_name,
            "pack": LISTING_PACK["version"],
        })
        return hashlib.sha256(material.encode()).hexdigest()

    def _as_of(self, latest: dict[str, Fact]) -> str:
        ledger = self._store.latest_artefact("ledger")
        if ledger is not None:
            return ledger.payload["as_of"]
        if latest:
            return max(fact.as_of for fact in latest.values())
        return generator.END.isoformat()

    def _refuse(self, problems: list[str],
                latest: dict[str, Fact]) -> Artefact:
        payload = {
            "diagnosis": "listing_quality",
            "pack_version": LISTING_PACK["version"],
            "problems": problems,
        }
        fingerprint = self._fingerprint(latest, [])
        material = canonical_json({"payload": payload, "fp": fingerprint})
        artefact_id = "DRF-" + hashlib.sha256(
            material.encode()).hexdigest()[:20]
        existing = self._store.get_artefact(artefact_id)
        if existing is not None:
            return existing
        refusal = Artefact(
            artefact_id=artefact_id, kind=REFUSAL_KIND,
            payload=payload, input_fingerprint=fingerprint,
        )
        self._store.insert_artefact(refusal)
        return refusal

    # -- the diagnosis ---------------------------------------------------

    def diagnose(self) -> Artefact:
        latest = _latest_images(self._store)
        problems: list[str] = []
        rows1 = self._rows(latest, route=1, problems=problems)
        rows2_problems: list[str] = []
        rows2 = self._rows(latest, route=2, problems=rows2_problems)
        problems.extend(rows2_problems)
        findings, unjudged = _classify(rows1, LISTING_PACK)
        expected_findings, expected_unjudged = _classify(
            rows2, LISTING_PACK)
        if canonical_json(findings) != canonical_json(expected_findings):
            problems.append("listing_finding_mismatch")
        if canonical_json(unjudged) != canonical_json(expected_unjudged):
            problems.append("listing_unjudged_mismatch")
        if canonical_json(self._summary(rows1)) != canonical_json(
                self._summary(rows2)):
            problems.append("listing_summary_mismatch")
        if problems:
            raise ListingRefused(self._refuse(problems, latest))
        payload = {
            "diagnosis": "listing_quality",
            "pack_version": LISTING_PACK["version"],
            "as_of": self._as_of(latest),
            "rules": {
                "min_width": LISTING_PACK["min_width"],
                "min_height": LISTING_PACK["min_height"],
                "measured_formats": list(LISTING_PACK["measured_formats"]),
            },
            "findings": findings,
            "unjudged": unjudged,
            "images": self._summary(rows1),
        }
        fingerprint = self._fingerprint(latest, rows1)
        material = canonical_json({"payload": payload, "fp": fingerprint})
        artefact_id = "DLQ-" + hashlib.sha256(
            material.encode()).hexdigest()[:20]
        existing = self._store.get_artefact(artefact_id)
        if existing is not None:
            return existing
        artefact = Artefact(
            artefact_id=artefact_id, kind=LISTING_KIND,
            payload=payload, input_fingerprint=fingerprint,
        )
        self._store.insert_artefact(artefact)
        return artefact

    def verify(self, artefact: Artefact) -> list[str]:
        """Re-perform a stored listing finding set: every image
        re-measured from its stored bytes, every finding
        recomputed, and every vision judgement re-checked for
        its binding (fact, content hash) and its labels
        (profile, model, tier, pack version, call) — on the
        offline profile the judged subject itself re-derives
        from the bytes. A refusal artefact verifies clean, as
        with the other diagnoses."""
        if artefact.kind == REFUSAL_KIND:
            return []
        if artefact.kind != LISTING_KIND:
            return [f"not_a_listing_diagnosis:{artefact.kind}"]
        pack = LISTING_PACKS.get(artefact.payload.get("pack_version") or "")
        if pack is None:
            return [
                f"unknown_pack_version:{artefact.payload.get('pack_version')}"
            ]
        problems: list[str] = []
        latest = _latest_images(self._store)
        # Judgement bindings and labels, checked on their own
        # before the recompute — a forged or unlabelled
        # judgement names itself here.
        for entry in artefact.payload["images"]:
            judgement_id = entry["judgement_artefact_id"]
            if judgement_id is None:
                continue
            judgement = self._store.get_artefact(judgement_id)
            if judgement is None:
                problems.append(f"judgement_missing:{entry['sku']}")
                continue
            jp = judgement.payload
            if jp["image_fact_id"] != entry["image_fact_id"]:
                problems.append(f"judgement_binding_mismatch:{entry['sku']}")
            if jp["image_content_hash"] != entry["content_hash"]:
                problems.append(f"judgement_binding_mismatch:{entry['sku']}")
            if not all(jp.get(field) for field in (
                    "profile", "model", "tier", "pack_version", "call_id")):
                problems.append(f"judgement_unlabelled:{entry['sku']}")
            if jp["subject_class"] != entry["judged_class"]:
                problems.append(f"judgement_subject_mismatch:{entry['sku']}")
        rows = self._rows(latest, route=2, problems=problems)
        findings, unjudged = _classify(rows, pack)
        if canonical_json(findings) != canonical_json(
                artefact.payload["findings"]):
            problems.append("listing_finding_mismatch")
        if canonical_json(unjudged) != canonical_json(
                artefact.payload["unjudged"]):
            problems.append("listing_unjudged_mismatch")
        if canonical_json(self._summary(rows)) != canonical_json(
                artefact.payload["images"]):
            problems.append("listing_summary_mismatch")
        return problems
