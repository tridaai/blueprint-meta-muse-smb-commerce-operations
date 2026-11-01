"""Connector ports v2: versioned exchange contracts, whole-batch
quarantine.

Round 1's ingest port judged facts one at a time (Law 3: provenance
or refusal). Real vendor feeds do not arrive one fact at a time —
they arrive as *batches* under an exchange contract: a storefront's
order export, a payments export, the books feed, the email
platform's definitions. This module is that layer. It sits in front
of the ingest port and judges the batch as a whole before a single
record is allowed through:

- every source delivers under a **versioned exchange contract**
  (``CONTRACTS`` below) stating the batch envelope (contract name,
  contract version, source system, as-of), the record kinds the
  contract carries, the exact (kind, source) pairs it accepts, and
  its provenance requirements;
- a batch that violates its contract is **quarantined whole** — no
  partial ingestion, nothing from the batch reaches the fact store —
  and the quarantine is recorded as an insert-only artefact naming
  the contract, every violation found, and the batch's content
  hash, so the refusal is itself part of the record (Law 4);
- a conforming batch is handed to the Round 1 ingest port exactly
  as before, so store-level outcomes (duplicate, conflict) keep
  their v1 meaning and a replayed batch lands nothing twice.

Vendor shapes are stated, never contacted: the contracts are
Shopify-shaped (storefront orders and the warehouse inventory
feed), Stripe-shaped (the payments export that carries refunds),
QuickBooks-shaped (the books feed) and Klaviyo-shaped (the email
platform's flow definitions), plus the ads manager's exchange in
the same pattern for the dataset's fifth source. The local doubles
(``generator.build_batches``) implement the contracts; no real
vendor system is connected.

Violation vocabulary (closed — a quarantine names only these):

- ``malformed_envelope`` .. the envelope is not an object, or its
  records list is missing, not a list, or empty (``empty_batch``);
- ``unknown_contract`` ...... the envelope names no contract in the
  registry;
- ``unknown_contract_version``  the envelope's contract version is
  not the version this code enforces;
- ``contract_source_mismatch``  the envelope's source system is not
  the source the named contract governs;
- ``missing_provenance`` .... the envelope or a record lacks a
  source system or an as-of date (Law 3, at batch level);
- ``mixed_source_batch`` .... a record's source system differs from
  the envelope's — sources never mix inside one batch;
- ``malformed_record`` ...... a record is not an object or names
  no kind;
- ``kind_not_in_contract`` .. a record kind the contract does not
  carry, or a (kind, source) pair the contract does not accept —
  the detail names which;
- ``invalid_payload`` ....... a record payload that fails its
  kind's payload model (payloads are judged by the same models the
  ingest port uses, so the two layers cannot drift);
- ``stale_as_of`` ........... the envelope's as-of precedes its
  newest record, or lags it by more than the contract's stated
  ``max_as_of_lag_days`` — an envelope may not claim to be older
  than its contents, nor trail them past the stated bound.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from pydantic import ValidationError

from .ingest import IngestPort, IngestReport, content_hash
from .models import PAYLOAD_MODELS, Artefact
from .store import SqlStore, canonical_json

CONTRACT_VERSION = "2026.11.1"
QUARANTINE_KIND = "connector_quarantine"

VIOLATIONS = (
    "malformed_envelope",
    "empty_batch",
    "unknown_contract",
    "unknown_contract_version",
    "contract_source_mismatch",
    "missing_provenance",
    "mixed_source_batch",
    "malformed_record",
    "kind_not_in_contract",
    "invalid_payload",
    "stale_as_of",
)


@dataclass(frozen=True)
class ExchangeContract:
    """One source's exchange contract, as stated data."""

    name: str
    shape: str  # the vendor shape the exchange follows
    source_system: str  # the one source this contract governs
    pairs: tuple[tuple[str, str], ...]  # accepted (kind, source) pairs
    max_as_of_lag_days: int = 7

    @property
    def kinds(self) -> tuple[str, ...]:
        return tuple(sorted({kind for kind, _ in self.pairs}))


# The registry. The storefront's Shopify-shaped side arrives as two
# exchanges (orders from the storefront, inventory positions from
# the warehouse feed); refunds ride the Stripe-shaped payments
# export, whose local double is the storefront's payment records —
# the contract states the source the double must claim.
CONTRACTS: dict[str, ExchangeContract] = {
    contract.name: contract
    for contract in (
        ExchangeContract(
            name="storefront",
            shape="shopify",
            source_system="hartwell-storefront",
            pairs=(("order", "hartwell-storefront"),),
        ),
        ExchangeContract(
            name="warehouse",
            shape="shopify-inventory",
            source_system="hartwell-warehouse",
            pairs=(("inventory", "hartwell-warehouse"),),
        ),
        ExchangeContract(
            name="payments",
            shape="stripe",
            source_system="hartwell-storefront",
            pairs=(("refund", "hartwell-storefront"),),
        ),
        ExchangeContract(
            name="ads",
            shape="ads-platform",
            source_system="hartwell-ads-manager",
            pairs=(("ad_daily", "hartwell-ads-manager"),),
        ),
        ExchangeContract(
            name="email",
            shape="klaviyo",
            source_system="hartwell-email-platform",
            pairs=(("email_flow", "hartwell-email-platform"),),
        ),
        ExchangeContract(
            name="books",
            shape="quickbooks",
            source_system="hartwell-books",
            pairs=(("expense", "hartwell-books"),),
            # A monthly feed: the December close lands in the
            # January envelope, so the stated lag bound is the
            # monthly cycle, not the daily feeds' week.
            max_as_of_lag_days=45,
        ),
    )
}


def contract_for_pair(kind: str, source_system: str) -> ExchangeContract | None:
    """The contract that accepts this (kind, source) pair, if any."""
    for contract in CONTRACTS.values():
        if (kind, source_system) in contract.pairs:
            return contract
    return None


def contract_for_source(source_system: str) -> ExchangeContract | None:
    for contract in CONTRACTS.values():
        if contract.source_system == source_system:
            return contract
    return None


def batch_hash(contract: str, version: str, source_system: str,
               as_of: str, record_digests: list[str]) -> str:
    material = "|".join(
        [contract, version, source_system, as_of, *record_digests]
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _record_digest(record: Any) -> str:
    """One record's contribution to the batch hash.

    A record that can be judged gets the ingest port's own content
    hash; a record too malformed for that is hashed over its raw
    canonical form, so the batch hash names the batch as presented
    either way."""
    if isinstance(record, dict):
        kind = record.get("kind")
        payload = record.get("payload")
        model = PAYLOAD_MODELS.get(kind) if isinstance(kind, str) else None
        if model is not None and isinstance(payload, dict):
            try:
                clean = model.model_validate(payload).model_dump()
            except ValidationError:
                clean = None
            if clean is not None:
                return content_hash(
                    kind,
                    str(record.get("natural_key", "")),
                    clean,
                    str(record.get("source_system", "")),
                    str(record.get("as_of", "")),
                )
    return hashlib.sha256(canonical_json(
        record if isinstance(record, dict) else {"_raw": str(record)}
    ).encode("utf-8")).hexdigest()


def _parse_date(value: Any) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


@dataclass
class Violation:
    code: str
    detail: str
    record_index: int | None = None

    def as_dict(self) -> dict[str, Any]:
        entry: dict[str, Any] = {"code": self.code, "detail": self.detail}
        if self.record_index is not None:
            entry["record_index"] = self.record_index
        return entry


def validate_batch(envelope: Any) -> list[Violation]:
    """Judge a batch against its contract; [] means conforming.

    Every violation found is named — the judgement never stops at
    the first problem, because the quarantine artefact is the
    sender's only diagnosis of what to fix."""
    violations: list[Violation] = []
    if not isinstance(envelope, dict):
        return [Violation("malformed_envelope", "envelope is not an object")]
    records = envelope.get("records")
    if records is None or not isinstance(records, list):
        return [Violation("malformed_envelope", "records list is missing")]
    if not records:
        return [Violation("empty_batch", "the batch carries no records")]

    name = envelope.get("contract")
    version = envelope.get("contract_version")
    source = envelope.get("source_system")
    as_of = envelope.get("as_of")

    contract = CONTRACTS.get(name) if isinstance(name, str) else None
    if contract is None:
        violations.append(Violation(
            "unknown_contract", f"no contract named {name!r} in the registry"))
    if version != CONTRACT_VERSION:
        violations.append(Violation(
            "unknown_contract_version",
            f"contract version {version!r} is not {CONTRACT_VERSION}"))
    if not source or not str(source).strip() or not as_of \
            or not str(as_of).strip():
        violations.append(Violation(
            "missing_provenance",
            "the envelope lacks a source system or an as-of date"))
    elif contract is not None and source != contract.source_system:
        violations.append(Violation(
            "contract_source_mismatch",
            f"contract {contract.name} governs {contract.source_system}, "
            f"not {source}"))

    record_dates: list[date] = []
    for index, record in enumerate(records):
        if not isinstance(record, dict) or not record.get("kind"):
            violations.append(Violation(
                "malformed_record",
                "record is not an object or names no kind", index))
            continue
        kind = record["kind"]
        r_source = record.get("source_system")
        r_as_of = record.get("as_of")
        if not r_source or not str(r_source).strip() or not r_as_of \
                or not str(r_as_of).strip():
            violations.append(Violation(
                "missing_provenance",
                "record lacks a source system or an as-of date", index))
        elif source and r_source != source:
            violations.append(Violation(
                "mixed_source_batch",
                f"record source {r_source} differs from the envelope's "
                f"{source}", index))
        if contract is not None and isinstance(r_source, str):
            if kind not in contract.kinds:
                violations.append(Violation(
                    "kind_not_in_contract",
                    f"contract {contract.name} does not carry kind {kind}",
                    index))
            elif (kind, r_source) not in contract.pairs:
                violations.append(Violation(
                    "kind_not_in_contract",
                    f"contract {contract.name} does not accept "
                    f"({kind}, {r_source})", index))
        model = PAYLOAD_MODELS.get(kind)
        if model is None:
            violations.append(Violation(
                "kind_not_in_contract",
                f"no payload model exists for kind {kind}", index))
        elif not isinstance(record.get("payload"), dict):
            violations.append(Violation(
                "invalid_payload", "payload is not an object", index))
        else:
            try:
                model.model_validate(record["payload"])
            except ValidationError as error:
                first = error.errors()[0]
                violations.append(Violation(
                    "invalid_payload",
                    f"payload fails {kind}: "
                    f"{'.'.join(str(p) for p in first['loc'])} "
                    f"{first['msg']}", index))
        parsed = _parse_date(r_as_of)
        if parsed is not None:
            record_dates.append(parsed)

    envelope_date = _parse_date(as_of)
    if contract is not None and envelope_date is not None and record_dates:
        newest = max(record_dates)
        lag = (envelope_date - newest).days
        if lag < 0:
            violations.append(Violation(
                "stale_as_of",
                f"envelope as-of {as_of} precedes its newest record "
                f"{newest.isoformat()}"))
        elif lag > contract.max_as_of_lag_days:
            violations.append(Violation(
                "stale_as_of",
                f"envelope as-of {as_of} lags its newest record "
                f"{newest.isoformat()} by {lag} days, beyond the "
                f"contract's {contract.max_as_of_lag_days}"))
    return violations


@dataclass
class ExchangeReport:
    status: str  # accepted | quarantined
    contract: str = ""
    batch_hash: str = ""
    ingest: IngestReport | None = None
    quarantine: Artefact | None = None
    violations: list[Violation] = field(default_factory=list)

    @property
    def accepted(self) -> int:
        return self.ingest.accepted if self.ingest else 0

    @property
    def duplicates(self) -> int:
        return self.ingest.duplicates if self.ingest else 0


class ConnectorPort:
    """The v2 door: contract judgement first, the v1 port behind it."""

    def __init__(self, store: SqlStore, ingest: IngestPort):
        self._store = store
        self._ingest = ingest

    # -- exchange ---------------------------------------------------------

    def exchange(self, envelope: Any) -> ExchangeReport:
        records = envelope.get("records") if isinstance(envelope, dict) \
            else None
        digests = [_record_digest(r) for r in records] \
            if isinstance(records, list) else []
        digest = batch_hash(
            str(envelope.get("contract", "")) if isinstance(envelope, dict)
            else "",
            str(envelope.get("contract_version", ""))
            if isinstance(envelope, dict) else "",
            str(envelope.get("source_system", ""))
            if isinstance(envelope, dict) else "",
            str(envelope.get("as_of", "")) if isinstance(envelope, dict)
            else "",
            digests,
        )
        violations = validate_batch(envelope)
        if violations:
            artefact = self._record_quarantine(envelope, digest, violations)
            return ExchangeReport(
                status="quarantined",
                contract=str(envelope.get("contract", ""))
                if isinstance(envelope, dict) else "",
                batch_hash=digest,
                quarantine=artefact,
                violations=violations,
            )
        report = self._ingest.ingest_many(records)
        return ExchangeReport(
            status="accepted",
            contract=str(envelope.get("contract", "")),
            batch_hash=digest,
            ingest=report,
        )

    def _record_quarantine(self, envelope: Any, digest: str,
                           violations: list[Violation]) -> Artefact:
        artefact_id = "QAR-" + digest[:20]
        existing = self._store.get_artefact(artefact_id)
        if existing is not None:
            return existing
        records = envelope.get("records") if isinstance(envelope, dict) \
            else None
        payload = {
            "contract": envelope.get("contract")
            if isinstance(envelope, dict) else None,
            "contract_version": envelope.get("contract_version")
            if isinstance(envelope, dict) else None,
            "source_system": envelope.get("source_system")
            if isinstance(envelope, dict) else None,
            "as_of": envelope.get("as_of")
            if isinstance(envelope, dict) else None,
            "batch_hash": digest,
            "record_count": len(records)
            if isinstance(records, list) else 0,
            "record_digests": [
                _record_digest(r) for r in records
            ] if isinstance(records, list) else [],
            "record_keys": [
                r.get("natural_key") for r in records
                if isinstance(r, dict)
            ] if isinstance(records, list) else [],
            "violations": [v.as_dict() for v in violations],
            "status": "quarantined",
        }
        artefact = Artefact(
            artefact_id=artefact_id,
            kind=QUARANTINE_KIND,
            payload=payload,
            input_fingerprint=digest,
        )
        self._store.insert_artefact(artefact)
        return artefact

    # -- reads --------------------------------------------------------------

    def quarantines(self) -> list[Artefact]:
        return self._store.list_artefacts(QUARANTINE_KIND)

    def quarantine(self, artefact_id: str) -> Artefact | None:
        artefact = self._store.get_artefact(artefact_id)
        if artefact is None or artefact.kind != QUARANTINE_KIND:
            return None
        return artefact

    # -- verification ---------------------------------------------------------

    def verify(self, artefact: Artefact) -> list[str]:
        """Re-perform a quarantine artefact: its identity must
        re-derive from the batch material it names, its violations
        must come from the closed vocabulary, and its counts must
        agree with its digests. (That the exchange ingested nothing
        is graded at exchange time against the store, in the tests
        and the eval gate — a stored fact carries no record of
        which batch brought it, so verify() does not claim it.)"""
        problems: list[str] = []
        payload = artefact.payload
        if artefact.kind != QUARANTINE_KIND:
            return [f"not a quarantine artefact: {artefact.kind}"]
        digests = payload.get("record_digests", [])
        recomputed = batch_hash(
            str(payload.get("contract", "")),
            str(payload.get("contract_version", "")),
            str(payload.get("source_system", "")),
            str(payload.get("as_of", "")),
            digests,
        )
        if recomputed != payload.get("batch_hash"):
            problems.append("batch_hash does not re-derive from the "
                            "named batch material")
        if artefact.artefact_id != "QAR-" + recomputed[:20]:
            problems.append("artefact id is not the batch hash's address")
        if artefact.input_fingerprint != payload.get("batch_hash"):
            problems.append("input fingerprint is not the batch hash")
        if payload.get("status") != "quarantined":
            problems.append("quarantine does not read as quarantined")
        if payload.get("record_count") != len(digests):
            problems.append("record count disagrees with the digests")
        for entry in payload.get("violations", []):
            if entry.get("code") not in VIOLATIONS:
                problems.append(
                    f"violation outside the closed vocabulary: "
                    f"{entry.get('code')}")
        if not payload.get("violations"):
            problems.append("quarantine names no violations")
        return problems
