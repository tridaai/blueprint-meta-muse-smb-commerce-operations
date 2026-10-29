"""Law 3 grades: provenance at the port, insert-only semantics."""

from __future__ import annotations

from smb_commerce.ingest import IngestPort

EXPENSE = {"vendor": "PackShip Co", "date": "2025-11-14",
           "category": "packaging", "amount": "349.00"}


def test_fact_without_provenance_is_refused(store):
    port = IngestPort(store)
    for kwargs in ({"source_system": None, "as_of": "2025-11-14"},
                   {"source_system": "", "as_of": "2025-11-14"},
                   {"source_system": "hartwell-books", "as_of": None},
                   {"source_system": "hartwell-books", "as_of": " "}):
        outcome = port.ingest(kind="expense", natural_key="k:1",
                              payload=EXPENSE, **kwargs)
        assert outcome.status == "refused"
        assert outcome.reason == "missing_provenance"
    assert store.count_facts() == 0


def test_unknown_kind_and_invalid_payload_are_refused(store):
    port = IngestPort(store)
    outcome = port.ingest(kind="mystery", natural_key="k:1", payload={},
                          source_system="x", as_of="2026-01-01")
    assert (outcome.status, outcome.reason) == ("refused", "unknown_kind")
    outcome = port.ingest(kind="expense", natural_key="k:2",
                          payload={"vendor": "V"},
                          source_system="x", as_of="2026-01-01")
    assert (outcome.status, outcome.reason) == ("refused", "invalid_payload")
    assert store.count_facts() == 0


def test_duplicate_is_idempotent_and_conflict_is_refused(store):
    port = IngestPort(store)
    first = port.ingest(kind="expense", natural_key="k:1", payload=EXPENSE,
                        source_system="hartwell-books", as_of="2025-11-14")
    assert first.status == "accepted"
    again = port.ingest(kind="expense", natural_key="k:1", payload=EXPENSE,
                        source_system="hartwell-books", as_of="2025-11-14")
    assert again.status == "duplicate"
    assert store.count_facts() == 1
    changed = dict(EXPENSE, amount="350.00")
    conflict = port.ingest(kind="expense", natural_key="k:1", payload=changed,
                           source_system="hartwell-books", as_of="2025-11-14")
    assert (conflict.status, conflict.reason) == ("refused", "conflict")
    assert store.count_facts() == 1
