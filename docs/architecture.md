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
Since Round 13 that service is also the access boundary: every
surface resolves a caller (role pack, `access.py`) and the
service enforces it the same way for all of them.

## Components

- **Store port** (`store.py`): one SQL implementation, SQLite default
  and PostgreSQL 16 behind `DATABASE_URL`. Payloads are JSON text in
  both dialects. Insert-only by construction: no update or delete
  method exists for facts, artefacts or call records. Bulk loads run
  in a single transaction via `SqlStore.batch()`; the per-item
  duplicate and conflict checks are unchanged inside the batch.
  Round 10 made the dialect boundary honest at this layer: psycopg
  returns text as bytes on a SQL_ASCII database, so rows are decoded
  to text at the one materialisation point (`_one`/`_all`) — before
  that, a second boot re-ran its migrations because b'0001' != '0001'.
  An optional schema (`SMB_POSTGRES_SCHEMA`) scopes the store to one
  schema of a shared database; `close()` ends the connection so its
  idle-in-transaction locks cannot block a schema drop.
- **Parity walk** (`parity.py`): the one definition of the core walk
  (ingest -> ledger -> diagnoses -> plan -> drafts -> guardian ->
  approvals -> morning board; Round 14 extends it through
  subscriptions and digest generation for both cadences) and of
  the canonical snapshot it is graded by. The in-suite Postgres leg
  (`tests/test_postgres_parity.py`, gated on `SMB_TEST_DATABASE_URL`,
  test-database name guard, own throwaway schema) and the production
  smoke (`scripts/prod_smoke.py`, `make smoke`: fresh schema,
  idempotent second boot, `/health` + `/readiness`, the walk, and a
  byte-identical diff against a full SQLite walk) both call it, so
  the two grades can never drift apart. Round 10's verdict on the
  builder's run: parity gap 0.
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
- **Connector ports v2** (`connectors.py`, Round 11): the batch layer
  in front of the ingest port. Each source delivers under a versioned
  exchange contract (`CONTRACTS`, version `2026.11.1`) stating its
  envelope (contract, version, source system, as-of), the (kind,
  source) pairs it accepts, and a per-contract as-of lag bound (the
  books feed's monthly close states 45 days; the daily feeds state
  7). `validate_batch` judges the whole envelope — provenance,
  source purity, kind carriage, payload models (the ingest port's
  own, so the layers cannot drift), as-of freshness — and names
  every violation from a closed vocabulary. A violating batch is
  quarantined whole: nothing is ingested, and a `connector_quarantine`
  artefact records the contract, the violations, the records' digests
  and the batch content hash (the artefact id is the hash's address;
  `verify` re-derives the chain). A conforming batch is handed to the
  ingest port unchanged, so duplicates and conflicts keep their v1
  meaning and replays land nothing twice. The generator's dataset
  loads as six contract batches (`generator.build_batches`).
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
  reply. The prices are a versioned rate card (`2026.11.1`) held as
  data — input, cached input and output per profile and tier, as
  published for Muse Spark — so reconciliation re-performs costs
  row for row. The Meta adapter speaks the OpenAI chat-completions
  shape to a configurable base URL through a single transport
  callable — tests inject a local double, production uses httpx.
  Because the port is OpenAI-shaped, a hosted Spark Open endpoint can
  be added later as another profile with no caller changes.
  Round 12 adds the verification path: `profile_report` resolves
  the configured profile and reports its state without calling
  anything (for `meta-muse`: the model id and its source — the
  environment, or the built-in default `muse-spark`, configurable
  and unverified — the base URL, the tier, key presence, and
  fail-closed proved by attempting the construction), and
  `probe_meta_muse` exercises the profile end to end against a
  caller-supplied transport. Nothing in the codebase makes a live
  call: there is no key in hand, and every report says so.
- **Context** (`context.py`, Round 12): the context discipline at
  the governed port, the one choke point every call passes
  through. A versioned policy states the budget for Muse Spark's
  1M-token window (window minus a stated output reserve), a
  whitespace-word token estimator (the same one the offline
  profile prices by — deterministic, and stated as an estimate),
  and the compaction rule. Assembly pins the system prompt and a
  request's sheet lines — the figure sheet or screen sheet, which
  carry the figures and the contract — and treats the rest as
  narrative: over budget, narrative lines drop oldest first
  behind a single marker line, the minimum the budget forces;
  pinned material alone over budget fails closed and the call
  never happens. Every governed call records its assembly as an
  insert-only `context_assembly` artefact whose arithmetic
  `verify_context_payload` re-performs. Uncompacted calls keep
  their pre-Round-12 call ids exactly; today's real contexts are
  a few hundred words, so the machinery is graded with oversized
  fixtures, in tests and in the eval gate.
- **Cost analytics** (`costs.py`, Round 12): the ledger read back
  in code — totals and breakdowns by profile, tier, model and
  run, each breakdown summing exactly to the total, plus a
  reconciliation that re-performs every recorded cost from the
  rate card across all runs. Call records carry no timestamp, so
  runs are the finest grain on the record; the summary states
  that rather than inventing a by-day view. Surfaced as
  `GET /costs`, `GET /model-profile`, `GET /contexts`, the CLI
  `costs` / `model-profile` / `contexts` commands, and the demo's
  closing beat.
- **Access** (`access.py`, Round 13): the role pack over the
  record. `ACCESS_PACK` (version `2026.11.1`) is data — four
  staff roles (owner, operator, bookkeeper, viewer) and, per
  permission, the roles that hold it — and enforcement lives in
  the service, the one boundary both surfaces cross. A surface
  resolves a presented identity through the one `resolve_caller`
  (the `X-SMB-Actor` / `X-SMB-Role` headers on the API, the
  `--actor` / `--role` flags on the CLI; for an approval act the
  caller is the named approver acting in the stated role) and
  passes a `Caller` down; an unresolvable identity is the
  anonymous caller and holds nothing. Reads answer with the
  absent form when the role may not see the resource class —
  `None`, the empty list, the empty queue fold, the zero cost
  summary — keyed by artefact kind through
  `read_permission_for_kind`, so existence (and counts, and
  totals) never leak. Actions raise `AccessRefused` before any
  lookup of their target, except approval acts: a role-gated
  approval records an approval-refusal artefact naming the role
  problem and raises `ApprovalRefused`, the approvals module's
  own pattern, identical for real and absent drafts. Only the
  owner holds `approvals:act` — the operator runs ingest,
  diagnoses, drafting and screening but cannot approve the
  pipeline's output; the bookkeeper reads the money record
  (facts, ledger, diagnoses, quarantines, costs) and nothing of
  drafts, approvals, the plan or the board; the viewer holds no
  record permission. Verifies judge artefacts already in hand
  and are deliberately ungated: holding the artefact means a
  gated read already happened. Identity is presented, not
  authenticated — scoped tokens (Round 18) will authenticate a
  presentation and resolve to the same `Caller`. In-process
  flows (demo, eval gate, graph, parity walk) pass no caller and
  act as `INTERNAL_CALLER`, the stated owner-operator identity.
- **Digests** (`digests.py`, Round 14): subscriptions and the
  stored counterpart of the morning board. A subscription is an
  insert-only artefact naming a staff member (actor + role) and
  a cadence (daily, or the seven-day week ending on a stated
  date); unsubscribing supersedes it, and the current state is
  the fold of the chain. Generation — gated `digests:generate`,
  driven by the folded subscriptions — folds the board once and
  stores, per active subscription, one digest artefact
  assembled for that subscription's role: the sections included
  are exactly the ones the access pack lets the role read
  (headline `ledger:read`, findings + stock risks `plan:read`,
  approvals `approvals:read`, costs `costs:read`), so the
  bookkeeper's digest has no findings or approval picture and
  the viewer's has no section at all. A digest pins the heads it
  folded, cites every figure to its artefact, and is verified by
  re-performance — fresh fold, figure re-resolution, pin and
  section comparison — exactly as the board is; a digest folded
  before the record moved fails verification like a stale
  board. Digest identity is content, so two subscriptions for
  the same role and cadence share the one artefact and
  re-running a period inserts nothing. Reads are gated by
  content at the service boundary: the caller must hold every
  permission the digest's included sections required, on the
  dedicated reads and on the generic artefact reads alike;
  anything else answers as absent. Digests are purely
  code-assembled (no model call, no narrative prose) and are
  never sent anywhere — delivery would be leaving the building,
  which Law 2 reserves to the guardian + approval path.
  Surfaces: `/subscriptions`, `/digests/generate`, `/digests`,
  `/digests/{id}`, `/digests/{id}/verify`; CLI `subscriptions`,
  `subscribe`, `unsubscribe`, `digest-generate`, `digests`,
  `digest-verify`; the parity walk generates both cadences, so
  digests sit inside the byte-identical snapshot.
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
