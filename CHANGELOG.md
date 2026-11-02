# Changelog

The twenty rounds of this blueprint, in the order they landed. Every
round was built, graded on both storage profiles, and pushed before
the next began; the per-round detail lives in the README's round
sections, the security account in `docs/red-team.md`, and the
release statement in `docs/release-1.0.0.md`.

## 1.0.0 — Round 20: release hardening

- The version is stated in one voice: pyproject, the package
  `__version__`, the API's stated version and the lockfile all read
  1.0.0, and a test grades the four-way agreement.
- This changelog and the release notes; a docs sweep that checked
  the README and architecture claims against the code and corrected
  what had gone stale; a migration review of 0001–0003 in both
  dialects (contiguous, no semicolons in comments, the prod smoke's
  applied set matches — no migration change was needed).
- The freeze gates re-run on the final tree, on both profiles.

## Round 19: eval depth

- The eval gate deepened: boundary grading at every pack threshold
  (the bar and one step to each side, each pack's own
  inclusive/exclusive semantics), a tamper sweep that alters one
  figure or field in each of the 22 artefact families and requires
  the family's verifier to refuse it by name, and law-grade
  invariants promoted from the test suite into the gate (ingest
  idempotence, the webhook retry bound, both unsubscribe folds).
  Six new counters, 83 in all. No product defect was exposed.

## Round 18: scoped tokens and webhooks

- Scoped tokens authenticate Round 13's presented identity: the
  owner issues a token to a named actor with a role and a scope set
  that can only narrow the role's permissions; the secret is shown
  once and stored as a salted PBKDF2 hash (over the secret's SHA-256
  digest — the round's tests caught that a raw secret fed to the
  KDF is an HMAC key, and a zero-extended secret would have verified
  as the same key). Expiry and revocation are derived from records.
- Webhooks announce the record's transitions (draft awaiting
  approval, approval recorded, batch quarantined, digest generated,
  pack published) as HMAC-signed notifications carrying event kinds
  and artefact ids — never figures or content. Delivery is bounded
  at three recorded attempts and, in tests, demo and evals, goes to
  a local double; no real external call is made anywhere.

## Round 17: the monthly review pack

- The record folded whole for a stated month into a stored pack
  artefact — headline and detail, findings, listing summary,
  approval picture, costs — every figure citing its source
  artefact, the folded heads pinned on its face.
- The export: one deterministic document with a provenance appendix
  (every figure, its source artefact and as-of) and a SHA-256
  checksum manifest over the body and each referenced artefact;
  verification re-performs both.
- Publication is a gate, not a flag: the owner's named act, bound
  to the pack's content hash, refusals recorded, forged acts
  conferring nothing, staleness derived and named.

## Round 16: product images and listing quality

- Product images enter through a measured intake port: provenance
  refused when absent, format sniffed from the bytes and dimensions
  measured in code (a stdlib PNG/JPEG/GIF measurer; unmeasurable
  formats refused, WebP included), bytes stored content-addressed
  through the object-store port.
- A versioned listing pack judges each SKU's latest image:
  deterministic findings in code, and what the image shows judged
  through the governed port's new vision shape, recorded as a
  labelled judgement artefact the mismatch finding cites. Stub
  vision only — no live vision call has been made.

## Round 15: the red team

- A graded adversarial corpus (31 executions) across injection
  through ingested content, figure forgery, approval spoofing,
  guardian bypass and access probing. Eight holes were open in the
  earlier rounds' code — poisoned money accepted at the ingest
  port, guardian evasions (phrases split across an email's texts,
  separator obfuscation, compound word percents misjudged, "half
  price" unseen, disparagement split across sentences) and
  approval acts that could name someone other than the caller.
  All eight closed in the smallest law-preserving way and kept as
  regression fixtures; the guardian pack moved to 2026.11.1. The
  full account is `docs/red-team.md`.

## Round 14: subscriptions and digests

- Subscriptions as folded insert-only artefacts (actor, role,
  cadence); generation produces exactly one digest per active
  subscription per period and re-running inserts nothing.
- Digests are the morning board's stored, per-role counterpart:
  sections decided by the access pack per section, figures copied
  from computed artefacts and re-performed by the verifier, reads
  content-gated so a forbidden digest is indistinguishable from an
  absent one. Nothing is sent anywhere.

## Round 13: access control and existence hiding

- A versioned role pack over four staff roles (owner, operator,
  bookkeeper, viewer), enforced at the service boundary so the API
  and CLI cannot diverge; only the owner records approval acts.
- Existence hiding graded byte-level: a forbidden read returns the
  absent form — same status, same body, same empty lists and zero
  counts. Identity is presented, not authenticated (that is Round
  18's work); unauthenticated callers hold nothing.

## Round 12: profile verification, context discipline, cost analytics

- The `meta-muse` profile's verification path: `profile_report`
  resolves and reports the configured profile without calling
  anything, and a probe grades one governed call end to end against
  a caller-supplied transport. No live call: no Meta Model API key
  exists, and every report carries `live_call_made: False`.
- Context management for the 1M-token window: a versioned budget
  policy at the governed port, pinned system and sheet lines,
  oldest-first narrative compaction recorded as an artefact, and
  fail-closed when pinned material alone exceeds the budget.
- Cost analytics over the per-run ledger: a versioned rate card
  (Standard 1.25 / 0.15 / 4.25, Contributor 0.10 / 0.002 / 0.20 per
  1M), totals and breakdowns that sum exactly, reconciliation
  re-performed across all runs.

## Round 11: connector ports v2

- Versioned exchange contracts (`2026.11.1`) in front of the ingest
  port for the four vendor shapes (storefront, warehouse, payments,
  books, email platform, ads). A violating batch is quarantined
  whole as a recorded artefact with every violation named from a
  closed vocabulary; nothing partial is ingested, and a replay
  lands nothing twice. Conforming batches keep v1 semantics.

## Round 10: the Postgres profile, graded

- The full core walk produces byte-identical artefacts on Postgres
  and SQLite (parity gap 0), with the walk defined once in
  `parity.py` for the in-suite leg and the production smoke alike.
- Two real dialect bugs found by the first live run and fixed at
  the owning layer: psycopg returning text as bytes on a SQL_ASCII
  database (a second boot re-ran its migrations), and an idle
  connection's locks blocking a schema drop (the store gained
  `close()`).

## Round 9: the morning board

- The operator's opening view as a pure derivation: headline
  figures citing the head ledger, findings in plan order, stock
  risks called out, the approval picture with stale approvals
  marked. Composed fresh on every read, never stored, its folded
  heads pinned on its face, and a verifier that re-performs it end
  to end by a second route.

## Round 8: the approval queue

- Approval and rejection as insert-only act artefacts naming the
  human, the draft's id and content hash, and the passing verdict
  relied on; refused attempts recorded with their reasons; no
  double-approval. The queue is a fold of the acts over the
  guardian's gate, staleness is derived at read time, and every
  approved entry reads "approved — not sent" — nothing executes.

## Round 7: the guardian screen

- Law 2's second gate: a verdict artefact bound to the draft's id
  and content hash under a versioned policy pack. Hard lines judged
  in code (discount promises in digits and words, claims
  re-resolved, prose figures in digit and word form, prohibited
  classes, superseded sources); judgement calls screened by a
  separate governed model call. A draft reaches the approval queue
  only behind a passing, re-verified verdict.

## Round 6: drafting

- The model enters the loop through the governed port only: the
  plan narrative, the winback email copy and an ad budget note,
  each an insert-only artefact bound to the artefacts it drafts
  from and stamped with profile, tier and cost record. The drafting
  contract hands the model a figure sheet; every claim must
  re-resolve to its cited artefact and every figure token in the
  prose must be claim-carried, or the draft is refused whole.

## Round 5: the growth plan

- One plan artefact assembled from the computed record: summary
  figures copied from the head ledger with citations, priorities
  ranked by a versioned plan pack (family order, exposure
  tie-breaks), actions referencing findings and the audit's draft
  proposal by id. Empty families are named as no-findings, never
  silently absent; a refused diagnosis blocks the plan whole. The
  plan proposes; it performs nothing.

## Round 4: diagnosis pack III — the email-flow audit

- The pack (2026.10.3) states the catalogue of flows a store of
  this kind runs; a missing flow, a dormant flow, a step shortfall
  and a delay breach are findings in their own classes. On the
  Hartwell record: winback is missing. For each missing or dormant
  flow the audit emits a draft flow proposal — structure only,
  changing nothing.

## Round 3: diagnosis pack II — expense and refund anomalies

- The pack (2026.10.2) adds expense rules (an exact duplicate
  within 7 days, an outlier above 3.00x the vendor's own median, a
  first charge above $1,000 named as informational) and the refund
  bar (a SKU's refund rate above 0.040000 over at least 1,000 units
  sold). On the Hartwell record: the PackShip Co duplicate charge
  of $349.00, and the Ceramic Vase at a refund rate of exactly
  0.050000. Earlier pack versions are kept verbatim and findings
  re-perform under their stamped version.

## Round 2: diagnosis pack I — stockout and ad decay

- Findings as versioned pack data (2026.10.1): stockout risk at or
  under 24.00 cover days against trailing velocity, and ad decay
  judged above a spend and history floor when the latest week's
  return falls to at most half the first week's. On the Hartwell
  record: the Washed Linen Throw at 21.00 cover days, and Social —
  Seasonal Decor decaying from 4.0000 to 0.1704. Computed one way,
  re-performed from the facts by a second route before a finding
  set may exist; a mismatch records a refusal and stores nothing.

## Round 1: the foundation

- The fact store behind a port: numbered SQL migrations in both
  dialects, insert-only tables, and an ingest port that refuses any
  fact without a source system and an as-of date (Law 3).
- The gold-by-construction Hartwell Home Goods dataset — one year
  written from stated constants through that same port, with the
  planted patterns (a draining stock cover, a decaying ad set, a
  duplicated charge, a never-defined winback flow) checkable by
  hand.
- The commerce ledger: revenue, refunds, margin, refund rate,
  cover days and ad return computed in Decimal over the stored
  facts (Law 1), stored as an insert-only content-addressed
  artefact, every figure re-performed by a verifier's second route.
- The governed model port (Law 6): `offline` and `meta-muse`
  profiles behind one interface, every call recorded in a per-run
  cost ledger with tier stamped, totals reconciling exactly.
- Surfaces: the LangGraph loop (ingest, compute, verify), FastAPI
  reads, a CLI, and the demo that prints the operating picture.
