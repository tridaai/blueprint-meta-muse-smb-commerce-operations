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

## What Round 2 builds

- **Diagnosis pack I (Law 5).** The rules are data in a versioned pack (`2026.10.1`): a stockout horizon of 24.00 cover days against a trailing 28-day velocity, and ad-decay bars — a set is judged only above $2,600.00 total spend and 8 weeks of history, and is decaying when its latest-week return on spend falls to at most half its first-week return (a set retaining 90% or more is steady by construction). The pack's thresholds are stated in the module, so the same inputs always re-perform to the same findings.
- **Findings as artefacts (Law 4).** The stockout diagnosis names the Washed Linen Throw: 210 on hand, 21.00 cover days at 10.00 units a day, out on 2026-01-25. SKUs with adequate cover are named nowhere; a SKU with no stock or no sales reads as no-data, never as zero risk or zero cover. The ad-decay diagnosis names Social — Seasonal Decor: return on spend 4.0000 in its first week, 0.1704 in its last, a decay factor of 0.0426. The steady sets (3.5000 and 2.0000 all year) are named nowhere; a set below the spend bar is named as unjudged, with the reason.
- **Re-performance before existence.** Each diagnosis is computed from the head ledger artefact's figures and re-performed from the stored facts by a second route before the finding set is stored. One wrong figure — or a ledger that no longer matches the fact set, down to a fact edited behind the store's back — refuses the whole set: a refusal artefact is recorded and no finding is stored. A stored finding set can be re-verified at any time through the same second route.
- **Surfaces.** The LangGraph loop gains a diagnose node between compute and verify. FastAPI reads (`/diagnosis/stockout`, `/diagnosis/ad-decay`, their `/verify` companions, `/diagnosis/refusals`), CLI commands (`diagnose`, `diagnose-verify`, `refusals`), and the demo walks both diagnoses right after the ledger.

## What Round 3 builds

- **Diagnosis pack II (Law 5).** The pack moves to `2026.10.2`, adding expense and refund rules; the pack I sections carry over unchanged and the `2026.10.1` pack is kept verbatim, so a finding set stamped with the earlier version still re-performs under its own version. Expense bars: an exact duplicate is the same vendor charging the same amount again within 7 days; an amount outlier is a charge above 3.00x that vendor's own median charge (a vendor with fewer than 3 charges is unjudged for outliers, with the reason named); a vendor's first charge above $1,000.00 is named as informational, in its own class. Refund bar: a SKU's refund rate — refund amounts over gross revenue, from the ledger's per-SKU figures — above 0.040000, judged only over at least 1,000 units sold; below that a SKU reads as unjudged, never as clean.
- **Findings as artefacts (Law 4).** The expense diagnosis names the planted duplicate: PackShip Co, $349.00 on 2025-11-14 and again on 2025-11-15, both fact ids on the finding's face. No charge clears 3x its vendor's median (every vendor bills a constant amount), so there are no outliers; the Studio Lease ($2,400.00) and Northgate Logistics ($1,250.00) first charges are named as informational only. The refund diagnosis names the Ceramic Vase: $9,568.00 refunded against $191,360.00 gross — a rate of exactly 0.050000. Every other SKU sits under the bar and is named nowhere.
- **Re-performance before existence, again.** Both new diagnoses run under the same discipline as pack I: computed one way, re-performed from the stored facts by a second route before the finding set may exist; a mismatch — or a ledger gone stale against the fact set — records a refusal artefact and stores no finding. The CLI diagnose commands and the demo now walk all four packs.

The email-flow audit, drafting, the guardian screen and the approval queue are the next rounds on the map in `docs/architecture.md`.

## The laws

1. Money and metrics are computed in code (Decimal); the model drafts prose and proposals, never numbers. Any figure in a draft must resolve to a computed artefact or the draft fails verification.
2. Nothing leaves the building without two gates: the guardian screen, then a named human approval act bound to the draft's content hash. No act, no "approved" state anywhere.
3. Facts carry provenance (source system + as-of) or they are refused at the port.
4. Every plan, finding and draft is an insert-only, content-addressed artefact; supersession is by link, never by edit.
5. Anomaly and audit findings come from versioned packs with stated thresholds — the same inputs re-perform to the same findings, and a verifier re-performs every figure in a plan before it can be approved.
6. The model tier is a stated choice: Standard (no training on the data) is the default; Contributor must be selected explicitly and is badged wherever it is used, because Meta may train on prompts and outputs sent to it.

## Honest status

- Round 3 of 20 is built: foundation plus diagnosis packs I and II. The agent diagnoses stockout risk, ad decay, expense anomalies and refund anomalies, with every finding re-performed before it may exist; it does not yet audit email flows (Round 4), and it does not draft, screen or approve anything (Rounds 5–8).
- The vendor systems (storefront, ads manager, email platform, books) are local doubles behind the ingest port's contracts; no real store, ad account or mailbox is connected.
- The hosted `meta-muse` profile is stub-tested against a local double of the Meta Model API shape. No real API call has been made — there is no key in hand yet — and the model identifier is the configurable default `muse-spark` from `.env.example`; confirm the current identifier at dev.meta.ai before a live run.
- Approval is the end of the line by design: the blueprint never executes against real systems, even in later rounds.
- The Docker image build is statically reviewed, not run — there is no Docker daemon in the build environment.

## Licence

Apache 2.0.
