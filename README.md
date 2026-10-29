<img src="https://trida.ai/logos-rounded/trida-rounded.png" width="96" alt="Trida AI" />

# Blueprint — SMB Commerce Operations Agent

> **Work in progress.** This blueprint is under active construction and hands-on testing. Its README will grow as rounds land.

An operations agent for a small online retailer, built on **Meta Muse Spark**. It watches the store's own numbers — orders, refunds, inventory, ad spend, email flows, expenses — computes the operating picture in code, flags what needs attention, drafts the growth plan and the copy, and then stops: a separate guardian screen checks every draft, and a named human approves before anything sends, publishes or spends.

Money is computed in code, never by the model. Every figure in a plan traces to a stored fact with provenance. Every draft is an artefact; every approval is a named act bound to the draft's content.

The demonstration business is a fictional home-goods retailer; its data is generated in-repo so every planted pattern — a stockout brewing, an ad set decaying, a duplicate charge — can be checked by hand.

This is Trida's open, production-grade take on the pattern Meta launched as **Muse for Small Business** (2026-09-29): a Muse agent over a small business's connected stack — store, payments, books, email, ad accounts — doing the growth plan, the financial review and the email-automation audit, with the audit trail a services firm needs before it deploys anything similar for a client.

## Quickstart

```bash
make install     # uv sync --extra dev   (no uv? make install-pip)
make demo        # generate the dataset, print the operating picture
make test        # pytest
make evals       # the eval gate
make serve       # FastAPI on http://localhost:8000 — /health, /readiness, reads
make cli         # the CLI surface (the ledger)
```

Or with Docker: `docker compose up` brings up the agent, Postgres 16 and a SeaweedFS S3-compatible object store (service name `minio`).

No key is needed for any of the above. The default model profile is a deterministic offline composer; the hosted Muse Spark profile is wired through the Meta Model API and fails closed until `META_MODEL_API_KEY` is set (see `.env.example`).

## What Round 1 builds

- **Fact store with provenance (Law 3).** Orders, refunds, inventory positions, daily ad spend, email-flow definitions and expense lines enter only through an ingest port that refuses any fact without a source system and an as-of date. SQLite by default; `DATABASE_URL` switches to PostgreSQL 16. Numbered SQL migrations run at startup in both dialects.
- **A gold-by-construction dataset.** The generator writes one year of Hartwell Home Goods through that same port, from stated constants: a hero SKU, a fast mover whose restocks stop after week 39 so its cover drains to exactly 21.0 days, an ad set whose attributed revenue decays by a factor of 0.94 a week, a duplicated vendor charge on adjacent days, and an email platform where the winback flow was never defined.
- **The commerce ledger (Laws 1 and 4).** Revenue, refunds, gross margin, refund rate, inventory cover days per SKU and ad return per set — Decimal arithmetic over the stored facts, stored as an insert-only, content-addressed artefact. A verifier re-performs every figure by a second route; tamper with a stored fact and verification names it.
- **The model port (Law 6).** One governed port for all model calls. Profiles: `meta-muse` (Muse Spark via the Meta Model API, OpenAI-compatible shape) and `offline`. Every call is written to a per-run cost ledger with tokens, profile, tier and a cost computed from the published price table — Standard $1.25 / $4.25 per 1M input/output tokens, Contributor $0.10 / $0.20 — and totals reconcile exactly. Standard is the default tier because Meta does not train on its data; Contributor must be chosen explicitly and is stamped on every record.
- **Surfaces.** A LangGraph loop (ingest → compute → verify), FastAPI reads (`/facts`, `/ledger`, `/ledger/verify`, `/model-calls`), a CLI, and a demo that prints the operating picture with the planted patterns visible in the computed figures.

Diagnosis packs, drafting, the guardian screen and the approval queue are the next rounds on the map in `docs/architecture.md`.

## The laws

1. Money and metrics are computed in code (Decimal); the model drafts prose and proposals, never numbers. Any figure in a draft must resolve to a computed artefact or the draft fails verification.
2. Nothing leaves the building without two gates: the guardian screen, then a named human approval act bound to the draft's content hash. No act, no "approved" state anywhere.
3. Facts carry provenance (source system + as-of) or they are refused at the port.
4. Every plan, finding and draft is an insert-only, content-addressed artefact; supersession is by link, never by edit.
5. Anomaly and audit findings come from versioned packs with stated thresholds — the same inputs re-perform to the same findings, and a verifier re-performs every figure in a plan before it can be approved.
6. The model tier is a stated choice: Standard (no training on the data) is the default; Contributor must be selected explicitly and is badged wherever it is used, because Meta may train on prompts and outputs sent to it.

## Honest status

- Round 1 of 20 is built: foundation only. Diagnosis, drafting, guardian and approval land in the rounds on the architecture map; the agent does not yet diagnose, draft or approve anything.
- The vendor systems (storefront, ads manager, email platform, books) are local doubles behind the ingest port's contracts; no real store, ad account or mailbox is connected.
- The hosted `meta-muse` profile is stub-tested against a local double of the Meta Model API shape. No real API call has been made — there is no key in hand yet — and the model identifier is the configurable default `muse-spark` from `.env.example`; confirm the current identifier at dev.meta.ai before a live run.
- Approval is the end of the line by design: the blueprint never executes against real systems, even in later rounds.
- The Docker image build is statically reviewed, not run — there is no Docker daemon in the build environment.

## Licence

Apache 2.0.
