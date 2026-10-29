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

## What Round 4 builds

- **Diagnosis pack III — the email-flow audit (Law 5).** The pack moves to `2026.10.3`, adding an audit section; every earlier section carries over unchanged and the `2026.10.1` and `2026.10.2` packs are kept verbatim, so a finding set stamped with an earlier version still re-performs under its own version. The pack states the catalogue of flows a store of this kind is expected to run — the five flows the email platform defines (welcome, abandoned cart, post-purchase, browse abandonment, replenishment) plus the winback flow it never defined — each with a trigger and an expected step shape.
- **Findings as artefacts (Law 4).** A catalogue flow with no definition is a missing finding; a defined flow that is not active is a dormant finding, in its own class; an active flow with fewer steps than the catalogue's shape is a step-shortfall finding; and where a flow definition carries per-step send delays, any delay beyond the pack's 720-hour sanity bar is a delay-breach finding (the Hartwell export carries no delays, so that rule grades nothing there). On the Hartwell facts the audit names exactly one finding: winback is missing. Every defined flow grades healthy and is named nowhere, and the audit writes artefacts only — the defined flows are byte-identical before and after it runs.
- **Fixes are drafts, never edits.** For each missing or dormant flow the audit emits a draft flow proposal artefact — the catalogue's trigger, step count and step delays for that flow, structure only, no copy — stamped as a proposal that changes nothing until a later approval round acts on it. The winback proposal proposes a `customer_lapsed` trigger, 2 steps, delays of 0 and 336 hours.
- **Re-performance before existence, again.** The audit runs under the same discipline as the other packs: computed one way, re-performed from the stored facts by a second route before the finding set may exist, proposals included; a mismatch records a refusal artefact and stores no finding. The LangGraph loop's diagnose node now runs all five diagnoses; the API, CLI and demo walk the audit and its proposals alongside the other packs.

## What Round 5 builds

- **The growth plan artefact (Laws 1, 4 and 5).** One plan, assembled from the current computed record: an operating summary that copies its figures from the head ledger artefact — each figure citing the ledger artefact id and the field it came from — a priorities section ranking the five diagnosis families' findings, and an actions section that references the finding artefacts and the audit's draft flow proposal by id. The plan proposes actions; it performs none, and says so on its face. No prose is drafted in this round — narration is Round 6's job, over this verified plan.
- **The ranking rule is pack data.** The plan pack (`2026.10.1`, its own versioned pack, kept verbatim alongside any future version) states the family order — stockout risk before ad decay before expense anomalies before refund anomalies before email-flow audit gaps — and the tie-breaks inside a family: exposure descending (the headline magnitude the finding itself carries, named per family in the pack: trailing units, total spend, charge amount, refunded amount; audit findings are structural and carry no exposure), then the finding's identity key ascending. On the Hartwell record the order is: the Washed Linen Throw (280 trailing units), Social — Seasonal Decor ($26,000.00 of spend), the PackShip duplicate ($349.00), the Ceramic Vase ($9,568.00 refunded), and the missing winback flow.
- **Empty-state honesty.** A diagnosis family with no findings contributes an explicit no-findings entry to the plan, naming the diagnosis pack version that judged it — never a silently absent section. A family whose diagnosis is refused blocks the plan whole: a plan refusal artefact records the refusal it names, and no plan is stored.
- **Figure verification before existence.** Before a plan may exist, every figure in it is re-resolved against the artefact it cites and the priorities and actions are re-derived from the cited finding sets by a second route. A figure that does not match its source, or a citation to a superseded or refused artefact, records a plan refusal and stores no plan. Any stored plan can be re-verified at any time through the same route; re-running the assembler over a moved record supersedes the old plan by link, never by edit.
- **Surfaces.** The LangGraph loop gains a plan node between diagnose and verify. FastAPI reads (`/plan`, `/plan/verify`, `/plan/refusals`), CLI commands (`plan`, `plan-verify`, `plan-refusals`), and the demo assembles and verifies the plan right after the audit, printing its priority order.

## What Round 6 builds

- **The model enters the loop — through the port, and only the port (Laws 1 and 6).** A drafting service produces three drafts over the verified record, every one a governed model call recorded in the cost ledger: the **plan narrative**, prose that walks the head growth plan's priorities in the plan's own order; the **winback email copy** for the audit's draft flow proposal — a subject and a body per proposed step; and a **draft budget-action note** for the decaying ad set. Each draft is an insert-only artefact bound to the exact artefacts it drafts from and stamped with the model profile, the tier, and the cost-ledger record id of the call that produced it.
- **The drafting contract.** The service hands the model a *figure sheet* — the only figures the draft may state, each entry carrying the artefact id and the field it resolves to — and the model replies with the prose plus a structured claims list citing artefact and field for every figure. The offline profile composes its reply from the sheet alone, deterministically; a hosted profile answers the same contract.
- **Figure discipline, enforced in code (Law 1).** Before a draft may exist, a verifier resolves every claim against the artefact it cites, and scans the prose for figure tokens: any number the prose states that no claim carries is itself a failure. A draft with an invented or mismatched figure is refused whole — a draft refusal artefact records the offending claim and no draft is stored as usable. `verify` re-performs any stored draft at any time, and re-checks the stamped call against the cost ledger.
- **Honest states.** A draft is a draft: its status says so on every surface, and nothing in this round calls a draft approved, sent or published. The guardian screen is Round 7 and approval is Round 8.
- **Surfaces.** The LangGraph loop gains a draft node between plan and verify, and the verify node re-performs the drafts alongside everything else. FastAPI reads (`/drafts`, `/drafts/plan-narrative`, `/drafts/winback-copy`, `/drafts/ad-budget-note`, their `/verify` companions, `/drafts/refusals`), CLI commands (`draft`, `drafts`, `drafts-verify`, `draft-refusals`), and the demo drafts the narrative and the winback copy from the verified plan, printing each with its verification status, tier and cost.

## What Round 7 builds

- **The guardian screen — Law 2's second gate (the Sentinel pattern).** Every draft must pass a separate screening before it may be listed as awaiting approval. The verdict is an insert-only artefact bound to the draft's artefact id *and* its content hash, stamped with the version of the policy pack that judged it: a draft edited in any way is a new artefact with no verdict, and verdicts never transfer across hashes.
- **Hard lines judged in code, from a versioned pack.** A discount promise beyond the cap the business never authorised — in digits or in words ("forty percent off", "half off"); a claim that does not resolve against the artefact it cites; a figure stated in the prose that no claim carries, scanned in digit form and in *word* form ("nine hundred shoppers"), which the drafting gate's digit scan cannot see — the guardian is a second net, not a rerun of Round 6; the pack's prohibited content classes (guaranteed-results language, competitor disparagement against pack-named competitors, regulated advice claims); and a source artefact superseded or refused since the draft was made.
- **Judgement calls screened by the model, as their own governed call.** Tone and pressure tactics, and perk claims that mislead by omission, are decided by a separate screening call through the model port under a screen-sheet contract — one structured verdict naming an outcome per rule id, recorded in the cost ledger like every other call. The offline profile answers deterministically from the pack's stated phrase lists, so screening runs without a key; a reply that cannot name the pack's rules blocks the draft rather than passing it.
- **The gate is enforced in the service.** A draft appears as awaiting approval only when its latest verdict passes *and* re-verifies clean — the verifier re-performs the content hash, every code-judged rule outcome under the verdict's own pack, and the stamped screening call's cost. Blocked drafts carry their named blocking rules on every read surface. Re-screening is a new verdict under the pack that judges it; the old verdict stays. Approval itself — the named human act — is Round 8; this round builds no approval surface, only the gate that feeds it.
- **Surfaces.** The LangGraph loop gains a screen node between draft and verify, and the verify node re-performs the verdicts too. FastAPI reads (`/guardian/verdicts`, `/guardian/verdicts/{id}/verify`, `/guardian/awaiting-approval`, `/guardian/blocked`), CLI commands (`screen`, `verdicts`, `verdicts-verify`, `awaiting-approval`), and the demo screens the three drafts clean, then shows a planted bad draft — written straight into the store, past the drafting gate — blocked with its rules named and kept out of the queue.

The approval queue is the next round on the map in `docs/architecture.md`.

## The laws

1. Money and metrics are computed in code (Decimal); the model drafts prose and proposals, never numbers. Any figure in a draft must resolve to a computed artefact or the draft fails verification.
2. Nothing leaves the building without two gates: the guardian screen, then a named human approval act bound to the draft's content hash. No act, no "approved" state anywhere.
3. Facts carry provenance (source system + as-of) or they are refused at the port.
4. Every plan, finding and draft is an insert-only, content-addressed artefact; supersession is by link, never by edit.
5. Anomaly and audit findings come from versioned packs with stated thresholds — the same inputs re-perform to the same findings, and a verifier re-performs every figure in a plan before it can be approved.
6. The model tier is a stated choice: Standard (no training on the data) is the default; Contributor must be selected explicitly and is badged wherever it is used, because Meta may train on prompts and outputs sent to it.

## Honest status

- Round 7 of 20 is built: foundation, diagnosis packs I, II and III, the growth plan artefact, drafting, and the guardian screen. The agent diagnoses stockout risk, ad decay, expense anomalies and refund anomalies, audits the email flows against a stated catalogue (emitting draft flow proposals for what is missing or dormant), assembles one figure-verified growth plan from the computed record, and drafts the plan narrative, the winback email copy and an ad budget note through the governed model port — every drafted figure verified against the artefact it cites before the draft may exist, and a draft that invents a figure refused whole. Every draft is then screened by the guardian against a versioned policy pack before it may be listed as awaiting approval. It does not yet approve anything (Round 8): no surface in this round records a human approval, and nothing is sent, published or spent.
- The vendor systems (storefront, ads manager, email platform, books) are local doubles behind the ingest port's contracts; no real store, ad account or mailbox is connected.
- The hosted `meta-muse` profile is stub-tested against a local double of the Meta Model API shape. No real API call has been made — there is no key in hand yet — and the model identifier is the configurable default `muse-spark` from `.env.example`; confirm the current identifier at dev.meta.ai before a live run.
- Approval is the end of the line by design: the blueprint never executes against real systems, even in later rounds.
- The Docker image build is statically reviewed, not run — there is no Docker daemon in the build environment.

## Licence

Apache 2.0.
