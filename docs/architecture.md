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
                                          diagnose (R2) ─► later rounds: draft
                                          ─► guardian screen ─► approval queue
                                          ─► morning board (a read, not a step)
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
- **Diagnosis** (`diagnosis.py`): packs I, II and III — stockout
  risk, ad decay, expense anomalies, refund anomalies and the
  email-flow audit — with the rules held as data in versioned
  packs. The current pack is `2026.10.3`; `2026.10.1` and
  `2026.10.2` are kept verbatim in the registry, and verification
  re-performs a finding set under the version it was stamped with.
  The audit grades the defined email flows against the pack's
  stated catalogue and emits, for each missing or dormant flow, a
  draft flow proposal artefact — structure only, marked as a
  proposal that changes nothing; the audit itself writes artefacts
  only and never touches a flow fact.
  Expense findings name the fact ids they rest on (a duplicate names
  both charges); refund findings restate the ledger's per-SKU
  figures they were judged from. Route 1
  reads the head ledger artefact's own figures; route 2 re-derives
  the same inputs from the facts by grouped accumulation in key
  order. Both routes share only the classification shaping, so a
  disagreement is a computation disagreement. A mismatch — or a
  ledger that no longer matches the fact set, checked down to each
  fact's content hash re-performing from its own fields — records a
  refusal artefact and stores no finding. Finding sets are
  insert-only artefacts tied to the exact ledger artefact they
  diagnose; adequate SKUs and steady ad sets are named nowhere in
  them, while no-data SKUs and unjudged ad sets are named as such,
  with reasons.
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
  counts and artefact ids, never figures. Round 2 inserted the
  diagnose node between compute and verify; Round 4 extended it to
  run all five diagnoses, the email-flow audit included; Round 5
  inserted the plan node between diagnose and verify, and the
  verify node re-performs the plan alongside the ledger and the
  finding sets; Round 6 inserted the draft node between plan and
  verify, and the verify node re-performs the drafts too; later
  rounds insert nodes between `verify` and `END` without
  changing the spine. Round 7 inserted the screen node between
  draft and verify, and the verify node re-performs the guardian
  verdicts alongside everything else. Round 9 adds no node: the
  morning board is composed at read time from the record the
  loop leaves behind, like the approval queue's fold — a board
  node would imply the board is a pipeline product, and it is
  a derivation, not a step.
- **Plan** (`plan.py`): the growth plan assembler and its verifier.
  The plan pack states the family order, the per-family exposure
  fields and the summary figure list as data; the assembler copies
  figures from the head ledger with citations, ranks findings by
  the pack rule, and references findings and draft proposals by id
  in its actions. The verifier re-resolves every figure and
  citation and re-derives the ranking by a second route; the
  assembly gate applies the same checks plus head currency, and a
  failure records a plan refusal artefact instead of a plan.
- **Drafting** (`drafting.py`): the governed drafting service.
  It builds a figure sheet per draft — the only figures the
  draft may state, each citing artefact id and field — calls the
  model through the governed port, and gates the reply in two
  directions: every claim in the reply's structured claims list
  must re-resolve against the artefact it cites, and every
  figure token in the prose must be carried by a claim. A
  failure records a draft refusal artefact (the offending claim
  named, the refused text never stored) and stores no draft.
  Draft artefacts are stamped with profile, tier and the
  cost-ledger record id of their call; `verify` re-performs a
  stored draft at any time, the stamped call included. The
  offline profile composes its drafting replies from the figure
  sheet alone, so the gate passes deterministically offline.
- **Guardian** (`guardian.py`): the screening service and the
  second of Law 2's gates. A versioned policy pack states the
  hard lines as data — the authorised discount cap, the
  prohibited content classes, the competitor names — and the
  service judges those lines in code: discount promises in
  digits or words, claims re-resolved under the drafting
  discipline, prose figures in digit *and* word form (the net
  the drafting scan cannot see), prohibited phrases, and
  sources superseded or refused since drafting. The judgement
  calls (tone and pressure, misleading perk claims) go through
  the governed model port as a separate screening call under a
  screen-sheet contract; the offline profile answers it
  deterministically from the pack's stated phrases, and an
  unusable reply blocks rather than passes. Every verdict is an
  insert-only artefact bound to the draft's id and content hash
  and stamped with the pack version and the screening call;
  `verify` re-performs the hash, the code-judged outcomes and
  the stamped call's cost at any time. The approval gate lives
  here, not in the surfaces: `awaiting_approval` lists only
  drafts whose latest verdict passes and re-verifies clean, and
  blocked drafts carry their named blocking rules on every
  surface. Approval acts themselves are Round 8.
- **Approvals** (`approvals.py`): the approval queue and Law 2's
  named human act. Approval and rejection are insert-only act
  artefacts: an approval names the approver, the draft's id and
  content hash, and the passing guardian verdict it relies on,
  and snapshots the bound inputs (verdict, source heads, head
  ledger) at act time; a rejection names the rejecter and a
  reason code from a stated vocabulary plus free text. Refused
  attempts record an approval-refusal artefact and raise.
  Approval refuses drafts with no passing verdict, a verdict
  whose hash differs from the draft's, a source superseded or
  refused since the verdict, or an existing approval — there
  is no double-approval. The queue is derived by folding the
  acts over the guardian's gate on every read; approved
  entries carry the exact display status `approved — not
  sent` and execution status `not_sent`, because the blueprint
  executes nothing against real systems by design. Staleness
  is derived at read time against the act's snapshot: a moved
  ledger, superseded source or new verdict marks the entry
  STALE with the moved inputs named, while the act itself
  still verifies as a true historical record; an act that
  fails `verify` (hash mismatch, non-passing verdict, a claim
  of execution) is excluded from the approved list.
- **Board** (`board.py`): the morning board, Round 9 — the
  operator's opening view as a pure derivation. It folds the
  head ledger's figures (each citing its ledger field), the
  plan's ranked findings with their figures copied from the
  cited finding artefacts, the stockout findings called out
  as stock risks, and the approval picture (awaiting behind
  the guardian's gate, blocked with their rules, approved
  reading `approved — not sent` with staleness marked as the
  queue marks it, rejected with their reasons). The board is
  composed fresh on every read and returned as an in-memory
  content-addressed artefact — never inserted, so no surface
  can edit anything onto it — and it pins the heads it folded
  (ledger, plan, each diagnosis, the latest verdict per
  draft, the latest acts) so later movement is detectable.
  `verify` re-performs a board end to end by a second route —
  the sections recomposed from the queue folds and the
  finding sets directly, every figure re-resolved against
  its cited artefact, the pins compared against the current
  heads — so a forged, tampered or simply out-of-date board
  fails verification with the section or figure named.

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
