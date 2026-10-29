"""The guardian screen: no draft reaches the approval queue
unjudged.

Round 7 builds the second of Law 2's two gates (the first is the
drafting gate of Round 6; the second half — the named human
approval act — is Round 8). Every draft that wants to be *awaiting
approval* must first carry a guardian verdict: an insert-only
artefact bound to the draft artefact id AND to the draft's content
hash, stamped with the version of the policy pack that judged it.
A draft edited in any way is a new draft artefact with a new hash
and no verdict — verdicts never transfer across hashes.

The split of labour is the Sentinel pattern, as a stated contract:

- **The hard lines are judged in code**, deterministically, from
  the versioned pack below: a discount promise beyond the cap the
  business never authorised; a claim that does not resolve against
  the artefact it cites (the Round 6 claims discipline, re-applied
  here as a second net); a figure stated in the prose that no
  claim carries — scanned for digit tokens exactly as Round 6
  scans, and for *word-form* figures (``forty percent``, ``three
  favourites``), which the drafting gate's digit scan cannot see,
  so the guardian is a second net and not a rerun; the pack's
  prohibited content classes (guaranteed-results language,
  competitor disparagement — a pack-named competitor and a
  disparagement phrase in the same sentence — and regulated
  advice claims); and a source artefact that has been superseded
  or refused since the draft was made.
- **The judgement calls go through the model port** as a separate
  screening call: tone and pressure tactics, and perk claims that
  mislead by omission. The call follows the screening contract —
  the guardian hands the model a screen sheet (the draft's texts
  and the pack's judgement rules with their stated phrase lists)
  and the model replies with one JSON document naming an outcome
  per rule id. The call is a governed cost-ledger record like any
  other; the offline profile answers the contract deterministically
  from the phrase lists, so tests and evals screen without a key.
  A reply that is unparseable, or that names rules the pack does
  not state, cannot pass a draft: the verdict blocks on
  ``screening_reply_unusable``.

The gate is enforced in the service, not by convention:
``awaiting_approval`` lists a draft only when its latest verdict
passes AND that verdict re-verifies clean (``verify`` below).
Blocked drafts carry their named blocking rules on every read
surface. Re-screening is always a new verdict — under a changed
pack it is a verdict under the new pack version, and the old
verdict stays in the record, superseded by link.

``verify`` re-performs a verdict at any time: the draft's content
hash is recomputed, the code-judged rule outcomes are re-performed
under the verdict's own pack version and compared rule for rule,
the verdict's internal arithmetic (outcome vs blocking rules) is
re-checked, and the stamped screening call is found in the cost
ledger with a cost the price table re-performs. The model-judged
outcomes are not re-performed — a hosted model's judgement is a
recorded act, not a figure — but they must name exactly the
pack's judgement rules, no more and no fewer.

A source counts as **superseded** when it is no longer the head of
its kind (for draft flow proposals: when a later proposal exists
for the same flow), and as **refused** when a refusal artefact
recorded after it names its judgement — a diagnosis refusal naming
the diagnosis and the ledger it judged, or a plan refusal naming
the plan's ledger.
"""

from __future__ import annotations

import hashlib
import json
import re
from decimal import Decimal, InvalidOperation

from .drafting import (
    DRAFT_KINDS,
    _MISSING,
    _prose_texts,
    _values_equal,
    figure_tokens,
    resolve_field,
)
from .modelport import GovernedModelPort, call_cost
from .models import Artefact
from .store import SqlStore, canonical_json

VERDICT_KIND = "guardian_verdict"

# -- the policy pack ------------------------------------------------------

GUARDIAN_PACK = {
    "version": "2026.10.1",
    # The largest discount, in percent, the business has
    # authorised any draft to promise. A promise beyond it is a
    # hard line, whatever words carry it.
    "max_discount_percent": "25",
    # Words that mark a percentage as a discount promise when
    # they appear in the same sentence.
    "discount_markers": [
        "off", "discount", "save", "saving", "sale", "reduced",
        "markdown", "clearance",
    ],
    # Word-form discount promises: the word, with "off" in the
    # same sentence, promises this percent.
    "word_discounts": {"half": "50", "quarter": "25"},
    # The competitor names the disparagement rule judges against.
    "competitors": ["BrightNest Living", "Casa & Co", "Nestora"],
    "prohibited_classes": {
        "guaranteed_results": [
            "guaranteed", "guarantee", "risk-free", "risk free",
            "no risk", "will definitely", "definitely will",
            "never fails", "always works", "100% effective",
        ],
        "competitor_disparagement": [
            "inferior", "shoddy", "cheap knockoff", "knockoff",
            "overpriced junk", "falls apart", "fall apart",
            "worse than", "waste of money",
        ],
        "regulated_advice": [
            "cures", "treats", "diagnoses", "diagnose",
            "prevents disease", "clinically proven",
            "investment advice", "financial advice",
            "grow your savings",
        ],
    },
    # The judgement rules the screening call decides, each with
    # the stated phrases the offline profile screens by.
    "judgement_rules": {
        "tone_pressure": [
            "act now", "last chance", "only hours left",
            "before it's gone", "before it is gone", "or lose out",
            "hurry up", "ends tonight", "don't miss out",
            "do not miss out", "while supplies last",
        ],
        "misleading_omission": [
            "free shipping", "free returns", "no questions asked",
            "lifetime warranty", "money-back promise",
            "lowest price anywhere",
        ],
    },
}

GUARDIAN_PACKS = {GUARDIAN_PACK["version"]: GUARDIAN_PACK}

CODE_RULE_IDS = (
    "discount_beyond_cap",
    "claim_unresolvable",
    "figure_not_in_claims",
    "guaranteed_results_language",
    "competitor_disparagement",
    "regulated_advice_claims",
    "source_superseded",
    "source_refused",
)
JUDGEMENT_RULE_IDS = tuple(GUARDIAN_PACK["judgement_rules"])

_CLASS_RULE_IDS = {
    "guaranteed_results": "guaranteed_results_language",
    "competitor_disparagement": "competitor_disparagement",
    "regulated_advice": "regulated_advice_claims",
}

SCREEN_SYSTEM_PROMPT = (
    "You are the guardian screener for a small retailer's outbound"
    " drafts. You are handed a screen sheet: the draft's texts and"
    " the judgement rules you must decide, each with stated"
    " phrases. Reply with one JSON document, {\"rules\":"
    " [{\"rule_id\": ..., \"outcome\": \"pass\" or \"block\"}, ...]},"
    " one entry per rule in the sheet, deciding on the texts alone."
    " You judge tone and misleading claims; you never rewrite the"
    " draft and you never approve anything."
)

_NUMBER_WORDS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4,
    "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
    "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13,
    "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17,
    "eighteen": 18, "nineteen": 19, "twenty": 20, "thirty": 30,
    "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70,
    "eighty": 80, "ninety": 90, "hundred": 100,
}
_WORD_FIGURE = re.compile(
    r"\b(" + "|".join(sorted(_NUMBER_WORDS, key=len, reverse=True))
    + r")\b",
    re.IGNORECASE,
)
_SENTENCE_SPLIT = re.compile(r"[.!?\n]+")
_DIGIT_PERCENT = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*(?:%|percent\b)",
                            re.IGNORECASE)
_WORD_PERCENT = re.compile(
    r"\b(" + "|".join(sorted(_NUMBER_WORDS, key=len, reverse=True))
    + r")\s+percent\b",
    re.IGNORECASE,
)


def draft_content_hash(draft: Artefact) -> str:
    """The content hash a verdict (and, in Round 8, an approval
    act) binds to: the SHA-256 of the draft artefact's canonical
    payload. Any edit to the draft is a new artefact with a new
    hash."""
    return hashlib.sha256(
        canonical_json(draft.payload).encode("utf-8")
    ).hexdigest()


def _sentences(texts: list[str]) -> list[str]:
    sentences = []
    for text in texts:
        sentences.extend(
            part.strip() for part in _SENTENCE_SPLIT.split(text)
            if part.strip()
        )
    return sentences


def _percent_values(sentence: str) -> list[Decimal]:
    """Every percentage a sentence states, in digits or words."""
    values = []
    for match in _DIGIT_PERCENT.finditer(sentence):
        values.append(Decimal(match.group(1).replace(",", "")))
    for match in _WORD_PERCENT.finditer(sentence):
        values.append(Decimal(_NUMBER_WORDS[match.group(1).lower()]))
    return values


# -- the service ------------------------------------------------------------


class GuardianService:
    def __init__(self, store: SqlStore, model: GovernedModelPort,
                 pack: dict | None = None):
        self._store = store
        self._model = model
        self._pack = pack or GUARDIAN_PACK

    # -- the code-judged hard lines -------------------------------------

    def _source_problems(self, draft: Artefact) -> dict[str, list[str]]:
        """Per-source superseded / refused detail for the draft's
        bindings, per the rules in the module docstring."""
        superseded: list[str] = []
        refused: list[str] = []
        ordered = self._store.list_artefacts()
        position = {a.artefact_id: i for i, a in enumerate(ordered)}
        refusals = [
            a for a in ordered
            if a.kind in ("diagnosis_refusal", "plan_refusal")
        ]
        for source_id in draft.payload.get("source_artefact_ids", []):
            source = self._store.get_artefact(source_id)
            if source is None:
                continue
            if source.kind == "draft_flow_proposal":
                later = [
                    a for a in ordered
                    if a.kind == "draft_flow_proposal"
                    and position[a.artefact_id] > position[source_id]
                    and a.payload.get("flow_key")
                    == source.payload.get("flow_key")
                ]
                if later:
                    superseded.append(source_id)
            else:
                head = self._store.latest_artefact(source.kind)
                if head is not None and head.artefact_id != source_id:
                    superseded.append(source_id)
            for refusal in refusals:
                if position[refusal.artefact_id] <= position[source_id]:
                    continue
                rp = refusal.payload
                if refusal.kind == "diagnosis_refusal" and (
                    rp.get("diagnosis") == source.payload.get("diagnosis")
                    and rp.get("ledger_artefact_id")
                    == source.payload.get("ledger_artefact_id")
                ):
                    refused.append(source_id)
                elif refusal.kind == "plan_refusal" and (
                    source.kind == "growth_plan"
                    and rp.get("ledger_artefact_id")
                    == source.payload.get("ledger_artefact_id")
                ):
                    refused.append(source_id)
        return {"superseded": superseded, "refused": refused}

    def _code_rules(self, draft: Artefact, pack: dict) -> list[dict]:
        """The hard lines, judged in code under one pack. Every
        rule is evaluated and reported, blocked or not."""
        payload = draft.payload
        texts = _prose_texts(payload.get("draft_kind"), payload.get("content", {}))
        lowered = [text.lower() for text in texts]
        sentences = _sentences(texts)
        claims = payload.get("claims", [])
        rules: list[dict] = []

        def entry(rule_id: str, detail: list[str]) -> dict:
            return {
                "rule_id": rule_id,
                "source": "code",
                "outcome": "block" if detail else "pass",
                "detail": detail,
            }

        # Discount promises beyond the authorised cap.
        cap = Decimal(pack["max_discount_percent"])
        markers = pack["discount_markers"]
        discount_detail: list[str] = []
        for sentence in sentences:
            low = sentence.lower()
            if not any(
                re.search(rf"\b{re.escape(marker)}\b", low)
                for marker in markers
            ):
                continue
            for value in _percent_values(sentence):
                if value > cap:
                    discount_detail.append(
                        f"{value}% over cap {cap}%: {sentence[:80]}"
                    )
            for word, percent in pack["word_discounts"].items():
                if re.search(rf"\b{word}\b", low) and Decimal(
                    percent
                ) > cap:
                    discount_detail.append(
                        f"{word} off = {percent}% over cap {cap}%:"
                        f" {sentence[:80]}"
                    )
        rules.append(entry("discount_beyond_cap", discount_detail))

        # Claims that do not resolve — the Round 6 discipline,
        # re-applied as a second net.
        claim_detail: list[str] = []
        claimed_values: list[Decimal] = []
        for claim in claims:
            artefact = self._store.get_artefact(
                claim.get("source_artefact_id", "")
            )
            if artefact is None:
                claim_detail.append(
                    f"claim_source_missing:{claim.get('source_artefact_id')}"
                )
                continue
            resolved = resolve_field(
                artefact.payload, claim.get("source_field", "")
            )
            if resolved is _MISSING:
                claim_detail.append(
                    f"claim_unresolvable:{claim.get('source_field')}"
                )
            elif not _values_equal(resolved, claim.get("value")):
                claim_detail.append(
                    f"claim_figure_mismatch:{claim.get('source_field')}"
                )
            else:
                try:
                    claimed_values.append(Decimal(str(claim["value"])))
                except InvalidOperation:
                    pass
        rules.append(entry("claim_unresolvable", claim_detail))

        # Figures the prose states that no claim carries — digit
        # tokens as Round 6 scans them, and word-form figures the
        # digit scan cannot see.
        figure_detail: list[str] = []
        for text in texts:
            for raw, value in figure_tokens(text):
                if value not in claimed_values:
                    figure_detail.append(f"digit figure {raw}")
            for match in _WORD_FIGURE.finditer(text):
                value = Decimal(_NUMBER_WORDS[match.group(1).lower()])
                if value not in claimed_values:
                    figure_detail.append(
                        f"word figure {match.group(1).lower()}"
                    )
        rules.append(entry("figure_not_in_claims", figure_detail))

        # Prohibited content classes, from the pack's lists.
        for class_name, phrases in pack["prohibited_classes"].items():
            detail: list[str] = []
            if class_name == "competitor_disparagement":
                for sentence in sentences:
                    low = sentence.lower()
                    named = [
                        name for name in pack["competitors"]
                        if name.lower() in low
                    ]
                    hit = [p for p in phrases if p in low]
                    if named and hit:
                        detail.append(
                            f"{named[0]} + '{hit[0]}': {sentence[:80]}"
                        )
            else:
                for phrase in phrases:
                    if any(phrase in text for text in lowered):
                        detail.append(f"phrase '{phrase}'")
            rules.append(entry(_CLASS_RULE_IDS[class_name], detail))

        # Sources superseded or refused since the draft was made.
        problems = self._source_problems(draft)
        rules.append(entry(
            "source_superseded",
            [f"source {sid} superseded" for sid in problems["superseded"]],
        ))
        rules.append(entry(
            "source_refused",
            [f"source {sid} refused" for sid in problems["refused"]],
        ))
        return rules

    # -- the screening call -------------------------------------------------

    def _screen_sheet(self, draft: Artefact, pack: dict) -> dict:
        payload = draft.payload
        return {
            "draft_artefact_id": draft.artefact_id,
            "draft_kind": payload.get("draft_kind"),
            "draft_content_hash": draft_content_hash(draft),
            "pack_version": pack["version"],
            "texts": _prose_texts(
                payload.get("draft_kind"), payload.get("content", {})
            ),
            "claims": [str(c.get("value")) for c in payload.get("claims", [])],
            "judgement_rules": [
                {"rule_id": rule_id, "phrases": phrases}
                for rule_id, phrases in pack["judgement_rules"].items()
            ],
        }

    def _model_rules(self, draft: Artefact, pack: dict):
        """Run the screening call and parse its verdict document.
        Returns (rule entries, reply); the entries block on
        ``screening_reply_unusable`` when the reply cannot be
        trusted to name the pack's rules."""
        sheet = self._screen_sheet(draft, pack)
        prompt = (
            f"SCREEN-REQUEST {draft.artefact_id}\n"
            f"SCREEN-SHEET {json.dumps(sheet, sort_keys=True)}\n"
            f"Screen the draft against the sheet's judgement rules"
            f" only."
        )
        run_id = f"screen-{draft.artefact_id}"
        reply = self._model.complete(run_id, SCREEN_SYSTEM_PROMPT, prompt)
        expected = list(pack["judgement_rules"])
        try:
            document = json.loads(reply.text)
            entries = document["rules"]
            by_id = {e["rule_id"]: e["outcome"] for e in entries}
            if sorted(by_id) != sorted(expected) or any(
                outcome not in ("pass", "block")
                for outcome in by_id.values()
            ):
                raise ValueError("screening rules mismatch")
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            return [{
                "rule_id": "screening_reply_unusable",
                "source": "model",
                "outcome": "block",
                "detail": ["the screening reply did not name the"
                           " pack's judgement rules"],
            }], reply, run_id
        return [{
            "rule_id": rule_id,
            "source": "model",
            "outcome": by_id[rule_id],
            "detail": [],
        } for rule_id in expected], reply, run_id

    # -- screening ----------------------------------------------------------

    def screen(self, draft: Artefact) -> Artefact:
        """Screen one draft under the current pack and store the
        verdict. Screening the same draft under the same pack
        twice returns the stored verdict — a verdict is a record,
        and re-screening after anything changes is a new artefact
        because its material (hash, pack or call) differs."""
        if draft.kind not in DRAFT_KINDS:
            raise ValueError(f"not a draft: {draft.kind}")
        pack = self._pack
        rules = self._code_rules(draft, pack)
        model_rules, reply, run_id = self._model_rules(draft, pack)
        rules = rules + model_rules
        blocking = [r["rule_id"] for r in rules if r["outcome"] == "block"]
        fingerprint = hashlib.sha256(
            f"{draft.artefact_id}:{draft_content_hash(draft)}"
            f":{pack['version']}:{reply.call_id}"
            f":{canonical_json(rules)}".encode("utf-8")
        ).hexdigest()
        for existing in self._store.list_artefacts(VERDICT_KIND):
            if existing.input_fingerprint == fingerprint:
                return existing
        previous = self.latest_verdict(draft.artefact_id)
        payload = {
            "draft_artefact_id": draft.artefact_id,
            "draft_kind": draft.payload.get("draft_kind"),
            "draft_content_hash": draft_content_hash(draft),
            "pack_version": pack["version"],
            "outcome": "block" if blocking else "pass",
            "blocking_rules": blocking,
            "rules": rules,
            "screening_call_id": reply.call_id,
            "screening_profile": reply.profile,
            "screening_model": reply.model,
            "screening_tier": reply.tier,
            "screening_run_id": run_id,
            "supersedes": (
                previous.artefact_id if previous is not None else None
            ),
        }
        material = canonical_json({"payload": payload, "fp": fingerprint})
        artefact_id = (
            "GRV-" + hashlib.sha256(material.encode()).hexdigest()[:20]
        )
        existing = self._store.get_artefact(artefact_id)
        if existing is not None:
            return existing
        verdict = Artefact(
            artefact_id=artefact_id, kind=VERDICT_KIND,
            payload=payload, input_fingerprint=fingerprint,
        )
        self._store.insert_artefact(verdict)
        return verdict

    def screen_all(self) -> dict[str, Artefact]:
        """Screen every stored draft, in drafting order. Returns
        the latest verdict per draft artefact id."""
        verdicts: dict[str, Artefact] = {}
        for kind in DRAFT_KINDS:
            for draft in self._store.list_artefacts(kind):
                verdicts[draft.artefact_id] = self.screen(draft)
        return verdicts

    def latest_verdict(self, draft_artefact_id: str) -> Artefact | None:
        verdicts = [
            v for v in self._store.list_artefacts(VERDICT_KIND)
            if v.payload.get("draft_artefact_id") == draft_artefact_id
        ]
        return verdicts[-1] if verdicts else None

    # -- the gate -------------------------------------------------------------

    def awaiting_approval(self) -> list[dict]:
        """The gate, enforced here: a draft is listed as awaiting
        approval only when its latest verdict passes AND that
        verdict re-verifies clean. Blocked and unscreened drafts
        are never listed."""
        awaiting: list[dict] = []
        for kind in DRAFT_KINDS:
            for draft in self._store.list_artefacts(kind):
                verdict = self.latest_verdict(draft.artefact_id)
                if verdict is None or verdict.payload["outcome"] != "pass":
                    continue
                if self.verify(verdict):
                    continue
                awaiting.append({
                    "draft_artefact_id": draft.artefact_id,
                    "draft_kind": draft.payload.get("draft_kind"),
                    "draft_content_hash": draft_content_hash(draft),
                    "verdict_artefact_id": verdict.artefact_id,
                    "pack_version": verdict.payload["pack_version"],
                })
        return awaiting

    def blocked_drafts(self) -> list[dict]:
        """Drafts whose latest verdict blocks, with the named
        blocking rules — the same list every surface reads."""
        blocked: list[dict] = []
        for kind in DRAFT_KINDS:
            for draft in self._store.list_artefacts(kind):
                verdict = self.latest_verdict(draft.artefact_id)
                if verdict is None or verdict.payload["outcome"] != "block":
                    continue
                blocked.append({
                    "draft_artefact_id": draft.artefact_id,
                    "draft_kind": draft.payload.get("draft_kind"),
                    "verdict_artefact_id": verdict.artefact_id,
                    "pack_version": verdict.payload["pack_version"],
                    "blocking_rules": verdict.payload["blocking_rules"],
                })
        return blocked

    # -- verification ---------------------------------------------------------

    def verify(self, verdict: Artefact) -> list[str]:
        """Re-perform a stored verdict at any time, per the module
        docstring. A forged verdict — rebound to another draft or
        hash, or with a rule outcome flipped — fails here, which
        is what keeps it out of the approval queue."""
        if verdict.kind != VERDICT_KIND:
            return [f"not_a_verdict:{verdict.kind}"]
        payload = verdict.payload
        problems: list[str] = []
        draft = self._store.get_artefact(
            payload.get("draft_artefact_id", "")
        )
        if draft is None:
            return ["verdict_draft_missing"]
        if draft_content_hash(draft) != payload.get("draft_content_hash"):
            problems.append("verdict_draft_hash_mismatch")
        pack = GUARDIAN_PACKS.get(payload.get("pack_version") or "")
        if pack is None:
            problems.append(
                f"unknown_pack_version:{payload.get('pack_version')}"
            )
            return problems
        rules = payload.get("rules", [])
        blocking = [
            r["rule_id"] for r in rules if r.get("outcome") == "block"
        ]
        if payload.get("blocking_rules") != blocking:
            problems.append("verdict_blocking_rules_mismatch")
        if payload.get("outcome") != ("block" if blocking else "pass"):
            problems.append("verdict_outcome_inconsistent")
        recorded = {
            r["rule_id"]: r.get("outcome")
            for r in rules if r.get("source") == "code"
        }
        for entry in self._code_rules(draft, pack):
            if recorded.get(entry["rule_id"]) != entry["outcome"]:
                problems.append(
                    f"verdict_rule_mismatch:{entry['rule_id']}"
                )
        model_ids = sorted(
            r["rule_id"] for r in rules if r.get("source") == "model"
        )
        if model_ids != sorted(pack["judgement_rules"]):
            problems.append("verdict_model_rules_mismatch")
        record = None
        for call in self._store.list_model_calls(
            payload.get("screening_run_id")
        ):
            if call.call_id == payload.get("screening_call_id"):
                record = call
                break
        if record is None:
            problems.append("screening_call_missing")
        else:
            if (record.profile != payload.get("screening_profile")
                    or record.tier != payload.get("screening_tier")):
                problems.append("screening_call_stamp_mismatch")
            expected_cost = call_cost(
                record.profile, record.tier,
                record.input_tokens, record.output_tokens,
            )
            if record.cost != expected_cost:
                problems.append("screening_cost_mismatch")
        return problems
