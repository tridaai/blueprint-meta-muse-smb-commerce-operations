# Release notes — v1.0.0

SMB Commerce Operations Agent: Trida AI's open, production-grade
take on the pattern Meta launched as Muse for Small Business —
a Muse Spark agent over a small retailer's connected stack that
computes the operating picture in code, drafts over it through a
governed model port, and stops at a named human's approval act
before anything could leave the building. The demonstration
business, Hartwell Home Goods, is fictional; its data is generated
in-repo from stated constants so every planted pattern can be
checked by hand. Twenty rounds, one law set, both storage
profiles graded. `CHANGELOG.md` carries the per-round account;
`docs/red-team.md` carries the security account.

## The laws, as shipped

1. Money and metrics are computed in code (Decimal); the model
   drafts prose and proposals, never numbers. Any figure in a
   draft must resolve to a computed artefact or the draft fails
   verification.
2. Nothing leaves the building without two gates: the guardian
   screen, then a named human approval act bound to the draft's
   content hash. No act, no "approved" state anywhere — and an
   approval executes nothing against a real system.
3. Facts carry provenance (source system + as-of) or they are
   refused at the port.
4. Every plan, finding and draft is an insert-only,
   content-addressed artefact; supersession is by link, never by
   edit.
5. Anomaly and audit findings come from versioned packs with
   stated thresholds — the same inputs re-perform to the same
   findings, and a verifier re-performs every figure in a plan
   before it can be approved.
6. The model tier is a stated choice: Standard (no training on
   the data) is the default; Contributor must be selected
   explicitly and is badged wherever it is used, because Meta
   may train on prompts and outputs sent to it.

## Architecture in brief

One `CommerceService` behind every surface — FastAPI, CLI, demo,
and the LangGraph loop (ingest → ledger → diagnose → plan →
draft → screen → verify) — so a figure cannot drift between
surfaces. Facts enter through an ingest port (Round 1) behind
versioned connector exchange contracts (Round 11) and are stored
insert-only in SQLite by default or PostgreSQL 16 behind
`DATABASE_URL`, with JSON-text payloads in both dialects and
numbered migrations (0001–0003 per dialect). The ledger, five
diagnosis packs and the listing-quality pack, the growth plan,
drafts, guardian verdicts, approval acts, the morning board,
digests, review packs and their exports are all artefacts over
that record, each with a verifier that re-performs it by a second
route. The model is reached only through the governed port
(`offline` and `meta-muse` profiles), which records every call —
tokens, tier, cost against a versioned rate card — and assembles
its context under a stated budget policy. Round 13's versioned
role pack (owner, operator, bookkeeper, viewer) is enforced at
the service boundary with existence hiding; Round 18's scoped
tokens authenticate that presented identity and can only narrow
it, and its webhooks announce the record's transitions as signed,
figure-free notifications. `docs/architecture.md` is the full
component account.

## Freeze figures

The full gate set, run on the release tree on both profiles
(reproduce with `make test`, `make evals`, `make demo`, and
`make smoke` behind a Postgres `DATABASE_URL`):

- Default suite (SQLite): **263 passed, 4 skipped** — the 4 skips
  are the Postgres parity leg, which runs only when
  `SMB_TEST_DATABASE_URL` names a test database.
- Postgres suite: **267 passed** on a locally stood PostgreSQL 16
  — the parity leg included, the walk byte-identical across
  dialects.
- Eval gate: **GATE PASS, 83 required-zero counters** — boundary
  grading at every pack threshold, a tamper sweep over all 22
  artefact families, and the law-grade invariants, all at zero.
- Demo: **exit 0**, the full walk offline with no key.
- Production smoke: **SMOKE PASS, parity gap 0** — migrations
  0001–0003 applied on a fresh schema, a second boot idempotent,
  `/health` and `/readiness` answering, the core walk completing
  at 3,959 facts and 49 artefacts, identical on both dialects.
- Version agreement: **1.0.0** in the pyproject, the package
  `__version__`, the API's stated version and the lockfile,
  graded by `tests/test_version.py`.

## Honest limits — the consolidated statement

Everything below is stated in the round that built it; this is
the same list in one place. Nothing here claims more than the
code does.

- **The live Muse leg has never run.** There is no Meta Model
  API key in hand. The hosted `meta-muse` profile — drafting,
  screening and the Round 16 vision shape alike — is verified
  against local doubles of the API's shape (request shape, auth,
  tier stamp, cost identity) and fails closed without a key;
  every profile report carries `live_call_made: False`. The
  model identifier is the configurable default `muse-spark`,
  reported as unverified; confirm the current identifier at
  dev.meta.ai before a live run. What is graded is everything
  up to the call.
- **Every vendor system is a local double.** Storefront,
  warehouse, payments, books, email platform and ads manager
  implement the Round 11 exchange contracts; no real store, ad
  account, mailbox or ledger is connected. Approval is the end
  of the line by design: an approved draft reads "approved —
  not sent" everywhere, and the blueprint never executes
  against real systems.
- **A token proves possession, not a person.** Scoped tokens
  authenticate the presentation of an identity; who receives a
  token is the deployment's issuance discipline, and there is
  no session or external identity provider. Presented-identity
  calls remain for the internal surfaces, the demo and the
  evals.
- **Webhook delivery is graded only against a local double** —
  a signature-verifying receiver in tests, demo and evals. No
  real external HTTP call is made anywhere; a live endpoint's
  behaviour is ungraded. There is no scheduler, queue or retry
  daemon beyond the three bounded in-call attempts, and a
  delivery that exhausts them stays on the record as failed
  attempts. The subscription secret is encrypted at rest under
  `SMB_WEBHOOK_KEY`; with no key configured a stated
  development constant is used, which protects nothing — a real
  deployment must set the key.
- **The guardian's code rules judge stated lists.** Pack-list
  synonyms and letter substitutions (leetspeak, homoglyphs)
  defeat the letter matching; on the hosted profile the
  model-judged rules are the semantic net, and offline they are
  a stated miss. An absolute-amount promise ("$40 off") states
  no percent to judge against the cap (the figure rule still
  applies), and phrase pieces separated by a whole intervening
  text are not matched. The full red-team account, including
  the eight holes Round 15 opened and closed, is
  `docs/red-team.md`.
- **The record's grain is the head ledger.** There is no
  per-period or per-month ledger, so digests and review packs
  are snapshots of the record as of the head ledger, pinned
  under their period or month label — not period-filtered
  recomputations. Cost records carry no timestamp, so cost
  analytics have no by-day grain; runs are the finest grain on
  the record.
- **Vision is stub-only.** The offline profile judges an image
  from the subject class embedded in the fixture bytes at
  construction; the `meta-muse` vision message is stub-tested
  and has never been sent. The intake measurer covers PNG,
  JPEG and GIF; other formats (WebP included) are refused as
  unreadable rather than passed unmeasured. On a hosted
  profile a verifier cannot re-call the model, so a judgement's
  stored artefact is the record it re-checks — the same stated
  limit as the other hosted verifications.
- **Context tokens are an estimate.** The budget policy counts
  whitespace words — deterministic, and stated as an estimate,
  not a tokenizer. Real contexts run to a few hundred words, so
  compaction is graded with oversized fixtures.
- **The Docker image build is statically reviewed, not run** —
  there is no Docker daemon in the build environment. The
  compose stack's Postgres is the verified profile by
  configuration, not by a container run.
- **The eval gate's own stated edges.** It runs on the local
  profile; Postgres parity is carried by the suite's parity leg
  and the prod smoke, not a second eval run. The tamper sweep
  alters one field per family; combined multi-field tampering
  is graded only where the content-hash and fingerprint chains
  bite. Calendar edges beyond the daily and weekly digest
  shapes are not fixtured. No live model behaviour is graded
  anywhere.
- **The WIP notice stays on the README.** It comes off when
  hands-on developer testing is finished and the companion
  blog is published; neither has happened, so v1.0.0 ships
  with it in place, deliberately.
