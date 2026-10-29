# Architecture — SMB Commerce Operations Agent

## Shape

```
vendor systems (local doubles)          the agent loop (LangGraph)
  storefront ──┐                            ingest ──► compute ──► verify
  warehouse ───┤  ingest port (Law 3)            │            │
  ads manager ─┼──────────────► fact store ◄─────┘            │
  email ───────┤   provenance or refusal                      ▼
  books ───────┘                                    commerce ledger
                                                          │  (artefact)
                              model port (governed) ◄─────┘
                                offline │ meta-muse          │
                                cost ledger (per run)        ▼
                                          later rounds: diagnose ─► draft
                                          ─► guardian screen ─► approval queue
```

Every surface — API, CLI, demo, graph — talks to one
`CommerceService`, so a figure cannot drift between surfaces.

## Components

- **Store port** (`store.py`): one SQL implementation, SQLite default
  and PostgreSQL 16 behind `DATABASE_URL`. Payloads are JSON text in
  both dialects. Insert-only by construction: no update or delete
  method exists for facts, artefacts or call records. Bulk loads run
  in a single transaction via `SqlStore.batch()`; the per-item
  duplicate and conflict checks are unchanged inside the batch.
- **Migrations** (`migrations/`): numbered SQL files per dialect,
  applied in order at startup by `migrations.py` and recorded in
  `schema_migrations`. Comments never contain a semicolon (the runner
  splits statements on semicolons).
- **Ingest port** (`ingest.py`): validates each fact's payload against
  its kind's Pydantic model, refuses facts without provenance
  (`missing_provenance`), refuses a natural key re-presented with
  different content (`conflict`), and treats an identical re-present
  as an idempotent duplicate. Content hashes chain kind, key, payload,
  source and as-of.
- **Generator** (`generator.py`): pure functions of stated constants —
  52 weeks from Monday 2025-01-06. Sales curves, refund rates (basis
  points of weekly units, floored), restock cycles, ad decay factor
  0.94, and the planted duplicate charge are all in the module, where
  tests and evals re-derive them independently.
- **Ledger** (`ledger.py`): route 1 is a streaming fold over the
  facts; the verifier's route 2 is grouped accumulation in a different
  order. Both share the stated definitions (see the module docstring)
  and the `_assemble` shaping, so a disagreement is a computation
  disagreement, not a formatting one. The artefact id is a hash of the
  payload plus the fingerprint of the fact set, so the same facts
  always produce the same artefact and different facts never do.
- **Model port** (`modelport.py`): profiles behind one interface, a
  governed wrapper that records every call before returning the
  reply. The price table is data, so reconciliation re-performs costs
  row for row. The Meta adapter speaks the OpenAI chat-completions
  shape to a configurable base URL through a single transport
  callable — tests inject a local double, production uses httpx.
  Because the port is OpenAI-shaped, a hosted Spark Open endpoint can
  be added later as another profile with no caller changes.
- **Object store port** (`objectstore.py`): filesystem by default,
  S3-compatible adapter when `SMB_S3_ENDPOINT` is set; the adapter
  fails closed on incomplete configuration. Round 1 wires the port;
  report packs use it from Round 17.
- **Graph** (`graph.py`): a LangGraph `StateGraph` whose nodes pass
  counts and artefact ids, never figures. Later rounds insert nodes
  between `verify` and `END` without changing the spine.

## Data model

Three tables in Round 1: `facts` (kind, natural key, JSON payload,
source system, as-of, content hash), `artefacts` (kind, JSON payload,
input fingerprint) and `model_calls` (run id, profile, model, tier,
token counts, Decimal cost as text). Money is a string in every
payload so Decimal values round-trip exactly.

## Round map

The 20-round map lives in the design brief
(`blueprint-22-meta-muse-smb-commerce-design.md` in the blueprint
programme files): R2 stockout + ad-decay diagnosis, R3 expense and
refund anomalies, R4 email-flow audit, R5 growth-plan artefact, R6
drafting, R7 guardian screen, R8 approval queue, R9 morning board,
R10 Postgres profile + prod smoke, R11 connector contracts v2, R12
live Muse verification + context discipline, R13 access control +
existence hiding, R14 digests, R15 red team, R16 multimodal listing
checks, R17 monthly financial review pack, R18 webhooks + scoped
tokens, R19 eval depth, R20 release hardening.
