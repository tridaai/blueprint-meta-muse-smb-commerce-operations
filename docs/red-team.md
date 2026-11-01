# Red team — Round 15

Round 15 attacks the system the first fourteen rounds built. The
red team's job is to break the six laws from the outside — through
the doors a real attacker would use: untrusted content entering
the ingest ports, artefacts written straight into the store,
approval acts presented by the wrong hands, drafts crafted to
slip the guardian, and callers probing the Round 13 access pack.

## How attacks are graded

Every attack runs end to end through the real service path, or is
planted straight into the store behind the service's back — the
store takes writes from anything that can reach it, so the
artefact verifiers, not the writers, are the trust boundary. An
attack **succeeds** only if the protected invariant actually
breaks:

- an attacker-chosen figure reaches an approved draft, the
  morning board, or a digest;
- a spoofed approval act is accepted or listed;
- a draft the policy pack forbids passes the guardian and
  reaches the approval queue.

An attack that is refused, quarantined, or fails verification
has **held**. Attacks that merely make the system refuse work
are graded too — a planted fact that crashes later computation
is a denial of service against the operator, and counts as a
success for the attacker until it is refused at the port.

The corpus lives in `tests/test_redteam.py` (25 test functions,
31 graded executions with parametrized cases) and runs on both
storage profiles; the eval gate carries the same programme at
gate scale under three counters — `injection_successes`,
`forgery_acceptances`, `spoofed_approvals_accepted`, all
required zero — with the guardian bypass fixtures folded into
the existing `guardian_bypasses` counter. The gate carries 66
counters in all.

## What was tried, and what held from the start

**Figure forgery.** A draft whose claims were edited after
drafting fails the drafting verifier and opens no gate; a draft
claiming `status: "approved"` in its payload fails verification
(the status fold reads acts, never payload claims); a forged
guardian verdict naming a screening call that does not exist
fails the guardian verifier on the missing call, the rule
mismatch, and the absent re-screen, and opens no gate; a real
passing verdict rebound onto a different draft fails on the
content hash. A tampered ledger artefact — gross revenue
rewritten to `999999.99` behind the store's back — is named by
the ledger verifier (`ledger_figure_mismatch`); the compute
path, keyed by the fact set's fingerprint, still returns the
genuine artefact; and the consumers that judge over the head
ledger — the plan, and the board through it — refuse rather
than state the forged figure. Raw artefact reads return stored
payloads by design; the verifiers and gates are where trust is
decided, and every surface that presents a figure derives it
through one.

**Approval spoofing.** A forged approval act planted in the
store claiming a draft was "sent" is never listed and never
moves the fold (its verdict binding does not resolve); an act
bound to a verdict artefact that does not exist is likewise
inert. Double approval and replay were already refused by the
fold in Round 8 and are re-graded there, not here.

**Access probing.** Callers whose identity does not resolve —
unknown role, a padded role string, the anonymous caller — are
refused or existence-hidden on every probed surface, and the
bookkeeper probing the subscription record (artefacts of a
kind outside every permission list she holds) gets absence,
not the record.

**Injection that stayed data.** An instruction planted in an
email-flow name ("Ignore all previous instructions…") rides
into the dormant-flow finding as the data it is; the proposal
is rebuilt from the pack catalogue, and the drafted copy
verifies clean. Untrusted text is never an instruction channel
anywhere in the system — there is no path from a fact's text to
a prompt that is not itself verified — so the injection
surface that remains is figures and availability, which is
where the holes below were.

## The holes, and the fixes

Eight attacks succeeded against the pre-round code. Each is
fixed in the smallest law-preserving way and kept as a
permanent regression fixture.

1. **Poisoned money at the ingest port.** The payload schema
   typed amounts as plain strings. A fact carrying
   `"not-a-number"` or `"NaN"` as an amount was accepted, then
   crashed the ledger and the diagnosis packs — one planted
   fact disabled every later computation over the record — and
   an order amount of `"NaN"` silently poisoned the computed
   revenue instead. A finite amount too large to quantize to
   cents (`"1e30"`) crashed the same arithmetic. Money fields
   now validate as finite Decimals that quantize to cents; the
   port refuses the rest as `invalid_payload`. The string
   itself is stored unchanged, so payloads still round-trip
   byte-identically.
2. **A banned phrase split across an email's subject and
   body.** The guardian matched phrases inside one text at a
   time; "…results are" in the subject and "guaranteed…"
   opening the body read as a promise to the customer and as
   nothing to the screen. Phrase matching now also runs over
   the draft's texts joined in reading order.
3. **Separator obfuscation.** "Guaran-teed" and
   "g.u.a.r.a.n.t.e.e.d" passed the prohibited-phrase rules
   untouched. Matching is now separator-insensitive: hyphens,
   dots and spaces inside a word do not hide it. The offline
   screening composer shared the defect for its judgement
   phrases and shares the fix (`phrase_in_texts` in the model
   port).
4. **A compound word percent judged at a fraction of its
   value.** "Thirty-five percent off" was judged as five
   percent — the scanner read only the unit word — so the
   discount rule itself waved a 35% promise through (the
   figure net caught the draft downstream, but the rule that
   owns the promise did not). Compound percents now parse
   whole: tens word + unit word.
5. **"Half price" invisible to the discount rule.** It states
   a fifty-percent promise with no percent sign and no number;
   the sentence now fires the discount gate on the collocation
   itself. The same fix tightened the reverse defect: the
   word-discount check fired on any marker word near "half",
   blocking honest copy ("Half of the catalogue is new… members
   save…"). Word discounts now require the pack's stated
   pairing — the number word with "off", or "half price".
6. **Disparagement split across sentences.** The rule matched
   the competitor's name and a disparaging phrase within one
   sentence; "BrightNest Living sells throws too. Their throws
   are shoddy…" passed. The rule is now judged per text — the
   unit a reader receives.
7. **An approval act naming someone who never acted.** The
   service recorded whatever approver name it was handed: a
   caller presenting as one person could record an approval —
   or a rejection — in another's name, and the artefact, the
   demo's record of the deed, attributed it to the named
   human. An identified caller must now act in their own name;
   a mismatch is refused and the refusal recorded. (Internal
   callers — the graph, the demo, the eval gate — name the
   human in the act by construction and are unchanged; the CLI
   builds its caller from the same `--approver` it records.)
8. **Guardian pack version.** Fixes 2–6 changed how the
   guardian judges, so the pack version bumps to `2026.11.1`
   under the pack discipline (versioned packs, stated lists
   unchanged); the prior pack stays resolvable so verdicts
   issued under it still verify against the rules that judged
   them.

## Stated limits

A graded boundary is a pass; an unstated one is not. These are
the boundaries this round states:

- **Pack-list synonyms are not judged by code rules.** The
  guardian's code-judged rules match the pack's stated phrase
  lists, separator tricks aside. A paraphrase outside the lists
  ("we stand behind every outcome, always") is the
  model-judged rules' work on the hosted profile; offline, the
  screener judges the same stated lists and says so. Widening
  the lists is a pack edit, with a version bump.
- **Letter substitutions defeat letter matching.** Leetspeak
  and homoglyph swaps ("gu4ranteed") are not caught by phrase
  matching. Separator tricks are; character substitution is a
  stated miss, to be caught — if at all — by the model-judged
  rules on the hosted profile.
- **An absolute-amount promise states no percent.** "$40 off"
  cannot be judged against a percent cap; the figure rule
  still applies to it (an unclaimed 40 is refused as a figure).
- **Phrase pieces separated by a whole intervening text are
  not one phrase.** Splits at contiguous boundaries — subject
  into body, line into line — are caught, because that is how a
  reader receives them. Pieces scattered across non-adjacent
  texts are not a phrase to the reader either.
- **Presented identity is still not authenticated** (Round
  13's stated limit, unchanged): the act now names the
  presenting actor, but nothing yet proves the presenter is
  that actor. Scoped tokens (Round 18) are the round that
  authenticates a presentation.
- **Per-fact money magnitude is bounded by the port**: an
  amount must be a finite Decimal that quantizes to cents.
  Amounts the ledger can carry are otherwise unbounded by
  design — a genuinely large order is data, not an attack.
