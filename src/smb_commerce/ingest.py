"""The ingest port: the only way a fact enters the store.

Law 3 is enforced here, at the port, not by callers' good behaviour:

- a fact without a source system or an as-of date is **refused**
  (reason ``missing_provenance``) and never stored;
- a fact whose kind has no payload model is refused
  (``unknown_kind``), and a payload that fails its model is refused
  (``invalid_payload``);
- a natural key already stored with different content is refused
  (``conflict``) — facts are insert-only, corrections arrive as new
  facts under new keys in later rounds;
- the identical fact re-presented is an idempotent ``duplicate``.

Every outcome is returned to the caller; nothing is silently dropped.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError

from .models import PAYLOAD_MODELS, Fact
from .store import SqlStore, canonical_json


def content_hash(kind: str, natural_key: str, payload: dict[str, Any],
                 source_system: str, as_of: str) -> str:
    material = "|".join(
        [kind, natural_key, canonical_json(payload), source_system, as_of]
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def fact_id_for(content: str) -> str:
    return "FCT-" + content[:20]


@dataclass
class IngestOutcome:
    status: str  # accepted | duplicate | refused
    fact: Fact | None = None
    reason: str = ""


@dataclass
class IngestReport:
    accepted: int = 0
    duplicates: int = 0
    refusals: list[IngestOutcome] = field(default_factory=list)

    @property
    def refused(self) -> int:
        return len(self.refusals)


class IngestPort:
    def __init__(self, store: SqlStore):
        self._store = store

    def ingest(self, *, kind: str, natural_key: str, payload: dict[str, Any],
               source_system: str | None, as_of: str | None) -> IngestOutcome:
        if not source_system or not str(source_system).strip() \
                or not as_of or not str(as_of).strip():
            return IngestOutcome(
                status="refused",
                reason="missing_provenance",
            )
        model = PAYLOAD_MODELS.get(kind)
        if model is None:
            return IngestOutcome(status="refused", reason="unknown_kind")
        try:
            validated = model.model_validate(payload)
        except ValidationError:
            return IngestOutcome(status="refused", reason="invalid_payload")
        clean_payload = validated.model_dump()
        digest = content_hash(kind, natural_key, clean_payload,
                              source_system, as_of)
        existing = self._store.get_fact_by_key(kind, natural_key)
        if existing is not None:
            if existing.content_hash == digest:
                return IngestOutcome(status="duplicate", fact=existing)
            return IngestOutcome(status="refused", reason="conflict")
        fact = Fact(
            fact_id=fact_id_for(digest),
            kind=kind,
            natural_key=natural_key,
            payload=clean_payload,
            source_system=source_system,
            as_of=as_of,
            content_hash=digest,
        )
        self._store.insert_fact(fact)
        return IngestOutcome(status="accepted", fact=fact)

    def ingest_many(self, items: list[dict[str, Any]]) -> IngestReport:
        report = IngestReport()
        with self._store.batch():
            for item in items:
                outcome = self.ingest(**item)
                if outcome.status == "accepted":
                    report.accepted += 1
                elif outcome.status == "duplicate":
                    report.duplicates += 1
                else:
                    report.refusals.append(outcome)
        return report
