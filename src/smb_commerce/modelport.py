"""The model port: one governed interface for every model call.

The agent layer talks to models only through this port, and only
through :class:`GovernedModelPort`, which writes every call to the
per-run cost ledger before returning the reply. There is no other way
to call a model in this codebase.

Profiles:

- ``offline`` — deterministic composer; builds its text from the
  prompt it is handed and invents nothing. Default for tests, evals
  and any deployment without a key.
- ``meta-muse`` — Meta Model API (Muse Spark), OpenAI-compatible
  chat-completions shape. **Fail-closed**: selecting it without
  ``META_MODEL_API_KEY`` raises :class:`ProfileNotConfigured` at
  wiring time. No real call is possible without a key, and tests use
  a local double of the API shape.

Tier discipline (Law 6): the tier comes from configuration
(``META_MUSE_TIER``, default ``standard``) and is stamped on every
call record. Standard is the tier whose data Meta does not train on;
Contributor is far cheaper and Meta may train on it — the rate card
below carries both, so the trade-off is visible in the ledger itself.

Prices are the versioned rate card (``RATE_CARD``, version
``2026.11.1``): USD per 1M tokens — input, cached input, output —
as published at dev.meta.ai for Muse Spark: standard 1.25 / 0.15 /
4.25, contributor 0.10 / 0.002 / 0.20. The offline profile costs
nothing by construction. Recorded calls price their input and
output tokens; no usage source in this codebase reports a cached
split yet, so the cached rate is the card's stated price for the
day one does — :func:`call_cost` already applies it to any cached
portion a caller hands it.

Round 12 adds the profile verification path: :func:`profile_report`
resolves the configured profile and reports its state without
calling anything, and :func:`probe_meta_muse` exercises the hosted
profile end to end against a caller-supplied transport (the test
suite's local double). Neither makes a live call: there is no Meta
Model API key in hand, and the hosted profile's live leg stays
honestly pending until one exists.

Round 16 adds the vision call shape, ``analyse_image``: one
governed method beside ``complete``, with the same ledger and
context records. The offline profile judges deterministically
from the subject class embedded in the image bytes at fixture
construction; ``meta-muse`` shapes the OpenAI-compatible
multimodal message (text plus the image as a data URI) over the
same transport — stub-tested only, fail-closed without a key,
and no live vision call has been made either.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Callable

from .models import ModelCallRecord
from .store import SqlStore

RATE_CARD_VERSION = "2026.11.1"

# USD per 1M tokens, as published at dev.meta.ai for Muse Spark.
# ``cached_input`` prices the cached portion of the input — a
# subset of the input tokens, charged at the cached rate instead
# of the full input rate.
RATE_CARD: dict[str, dict[str, dict[str, Decimal]]] = {
    "meta-muse": {
        "standard": {
            "input": Decimal("1.25"),
            "cached_input": Decimal("0.15"),
            "output": Decimal("4.25"),
        },
        "contributor": {
            "input": Decimal("0.10"),
            "cached_input": Decimal("0.002"),
            "output": Decimal("0.20"),
        },
    },
    "offline": {
        "standard": {
            "input": Decimal("0"),
            "cached_input": Decimal("0"),
            "output": Decimal("0"),
        },
        "contributor": {
            "input": Decimal("0"),
            "cached_input": Decimal("0"),
            "output": Decimal("0"),
        },
    },
}

# The (input, output) view of the card, kept for the callers that
# price recorded calls — recorded usage carries no cached split.
PRICE_TABLE: dict[str, dict[str, tuple[Decimal, Decimal]]] = {
    profile: {
        tier: (rates["input"], rates["output"])
        for tier, rates in tiers.items()
    }
    for profile, tiers in RATE_CARD.items()
}

COST_PLACES = Decimal("0.000001")


def call_cost(profile: str, tier: str, input_tokens: int, output_tokens: int,
              cached_input_tokens: int = 0) -> Decimal:
    """Price one call from the rate card. ``cached_input_tokens``
    is the portion of ``input_tokens`` served from cache; it is
    charged at the card's cached-input rate instead of the full
    input rate. With no cached portion — every recorded call
    today — a Standard 1M-in/1M-out Muse call is exactly
    $5.500000 and a Contributor one exactly $0.300000."""
    rates = RATE_CARD[profile][tier]
    cost = (
        Decimal(input_tokens - cached_input_tokens) * rates["input"]
        + Decimal(cached_input_tokens) * rates["cached_input"]
        + Decimal(output_tokens) * rates["output"]
    ) / Decimal(1_000_000)
    return cost.quantize(COST_PLACES)


class ProfileNotConfigured(RuntimeError):
    """A hosted profile was selected without its configuration."""


@dataclass
class ModelReply:
    text: str
    profile: str
    model: str
    tier: str
    input_tokens: int
    output_tokens: int
    # The cost-ledger record id of the call that produced this
    # reply, stamped by GovernedModelPort after recording. A raw
    # profile port never sets it.
    call_id: str | None = None


DRAFT_REQUEST_PREFIX = "DRAFT-REQUEST "
FIGURE_SHEET_PREFIX = "FIGURE-SHEET "

_ORDINALS = (
    "First", "Second", "Third", "Fourth", "Fifth", "Sixth", "Seventh",
    "Eighth", "Ninth", "Tenth", "Eleventh", "Twelfth", "Thirteenth",
    "Fourteenth", "Fifteenth", "Sixteenth", "Seventeenth", "Eighteenth",
    "Nineteenth", "Twentieth",
)


def _figure_sheet(prompt: str) -> dict | None:
    """The figure sheet of a drafting request, or None when the
    prompt is not one. A drafting request's first line names the
    draft kind; its second line carries the sheet as JSON — the
    only figures the reply may state."""
    lines = prompt.splitlines()
    if len(lines) < 2 or not lines[0].startswith(DRAFT_REQUEST_PREFIX):
        return None
    if not lines[1].startswith(FIGURE_SHEET_PREFIX):
        return None
    try:
        sheet = json.loads(lines[1][len(FIGURE_SHEET_PREFIX):])
    except json.JSONDecodeError:
        return None
    return sheet if isinstance(sheet, dict) else None


def _compose_narrative(sheet: dict) -> dict:
    business = sheet["business"]
    lines = [f"Draft growth plan — {business}, as of {sheet['as_of']}."]
    summary = "; ".join(
        f"{entry['name'].replace('_', ' ')} {entry['value']}"
        for entry in sheet["summary"]
    )
    lines.append(f"The operating picture: {summary}.")
    priorities = sheet["priorities"]
    if priorities:
        lines.append("Priorities, in the plan's stated order.")
        for entry in priorities:
            rank = entry["rank"]
            ordinal = (
                _ORDINALS[rank - 1] if rank <= len(_ORDINALS)
                else f"Priority {rank}"
            )
            line = (f"{ordinal} comes {entry['family_label'].lower()}"
                    f" — {entry['key']}")
            if entry.get("exposure") is not None:
                line += f", exposure {entry['exposure']}"
            lines.append(line + ".")
    for entry in sheet.get("coverage", []):
        lines.append(f"{entry['family_label']} was judged and named nothing.")
    lines.append(
        "This is a draft: it proposes actions and performs none, and"
        " nothing in it is approved, sent or published. Every figure"
        " above is quoted from the computed record."
    )
    return {"prose": "\n".join(lines)}


def _compose_email_copy(sheet: dict) -> dict:
    business = sheet["business"]
    flow_key = sheet["flow_key"]
    steps = []
    for entry in sheet["steps"]:
        index = entry["index"]
        if index == 1:
            if flow_key == "winback":
                subject = f"We miss you at {business}"
                body = (
                    f"Hello — it has been a while since your last order"
                    f" with {business}. A few new pieces have arrived"
                    f" since then, and your favourites are still here."
                    f" Come back and take a look whenever you are"
                    f" ready; there is no hurry, and nothing here asks"
                    f" for anything but a visit."
                )
            else:
                subject = f"A note from {business}"
                body = (
                    f"Hello — this is the opening note of the"
                    f" {sheet['flow_name']} flow at {business}. It is"
                    f" a draft, written for a person to approve before"
                    f" it can ever be sent."
                )
        else:
            if flow_key == "winback":
                subject = f"Your {business} favourites are still here"
            else:
                subject = f"A follow-up from {business}"
            body = (
                f"This follow-up arrives {entry['delay_hours']} hours"
                f" after the previous note, as the flow's draft"
                f" proposal defines. If you are still looking for"
                f" what you left behind, it is waiting. Whenever you"
                f" are ready, {business} is here."
            )
        steps.append({"subject": subject, "body": body})
    return {"steps": steps}


def _compose_ad_note(sheet: dict) -> dict:
    figures = {entry["role"]: entry["value"] for entry in sheet["figures"]}
    prose = (
        f"Draft budget note — {sheet['ad_set']}. Not approved: a"
        f" draft only. Return on ad spend fell from"
        f" {figures['first_week_roas']} in the first judged week to"
        f" {figures['latest_week_roas']} in the latest, a decay"
        f" factor of {figures['decay_factor']}, on total spend of"
        f" {figures['total_spend']}. Proposal: hold this set's"
        f" budget flat, shift no new money into it, and let a named"
        f" human decide what happens next. This note proposes; it"
        f" spends nothing."
    )
    return {"prose": prose}


_DRAFT_COMPOSERS = {
    "plan_narrative": _compose_narrative,
    "winback_email_copy": _compose_email_copy,
    "ad_budget_note": _compose_ad_note,
}


def compose_draft_reply(prompt: str) -> str | None:
    """Compose a drafting-contract reply from a prompt's figure
    sheet, deterministically — the offline profile's half of the
    drafting contract. Returns None for non-drafting prompts."""
    sheet = _figure_sheet(prompt)
    if sheet is None:
        return None
    composer = _DRAFT_COMPOSERS.get(sheet.get("draft_kind", ""))
    if composer is None:
        return None
    document = {"content": composer(sheet), "claims": sheet["claims"]}
    return json.dumps(document, sort_keys=True)


SCREEN_REQUEST_PREFIX = "SCREEN-REQUEST "
SCREEN_SHEET_PREFIX = "SCREEN-SHEET "


def _screen_sheet(prompt: str) -> dict | None:
    """The screen sheet of a guardian screening request, or None
    when the prompt is not one. A screening request's first line
    names the draft; its second line carries the sheet as JSON —
    the draft's texts and the guardian pack's judgement rules."""
    lines = prompt.splitlines()
    if len(lines) < 2 or not lines[0].startswith(SCREEN_REQUEST_PREFIX):
        return None
    if not lines[1].startswith(SCREEN_SHEET_PREFIX):
        return None
    try:
        sheet = json.loads(lines[1][len(SCREEN_SHEET_PREFIX):])
    except json.JSONDecodeError:
        return None
    return sheet if isinstance(sheet, dict) else None


def _squash(text: str) -> str:
    """Lowercase, with every run of non-alphanumeric characters
    collapsed to a single space."""
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def _despace(text: str) -> str:
    """Lowercase, letters and digits only — separators removed."""
    return re.sub(r"[^a-z0-9]", "", text.lower())


def phrase_in_texts(phrase: str, texts: list[str]) -> bool:
    """Whether a stated phrase appears in a set of texts, judged
    the way a reader receives it (Round 15).

    Phrase packs are matched three ways: literally, in any one
    text (the original rule); against the texts joined and
    squashed, so a phrase split at a word boundary across two
    texts — a subject ending "act", the body starting "now" —
    still matches; and against the joined text despaced, so
    separator tricks inside a word ("guaran-teed",
    "g.u.a.r.a.n.t.e.e.d", "guaran" / "teed" across steps) do
    not hide it. What this does not judge is letter substitution
    — leetspeak or look-alike letters change the letters
    themselves, and no separator rule sees them; that limit is
    stated in docs/red-team.md.
    """
    needle = phrase.lower()
    if any(needle in text.lower() for text in texts):
        return True
    joined = " ".join(texts)
    if _squash(phrase) in _squash(joined):
        return True
    despaced = _despace(phrase)
    return bool(despaced) and despaced in _despace(joined)


def compose_screen_reply(prompt: str) -> str | None:
    """Compose a screening-contract reply deterministically — the
    offline profile's half of the guardian contract (Round 7).

    The sheet carries the pack's judgement rules with their stated
    phrase lists; the offline screener blocks a rule when any of
    its phrases appears in the draft's texts, and passes it
    otherwise. A hosted profile judges the same rules on their
    merits and answers the same contract: one JSON document,
    ``{"rules": [{"rule_id": ..., "outcome": ...}, ...]}`` with an
    entry per judgement rule in the sheet."""
    sheet = _screen_sheet(prompt)
    if sheet is None:
        return None
    texts = [str(text) for text in sheet.get("texts", [])]
    rules = []
    for rule in sheet.get("judgement_rules", []):
        blocked = any(
            phrase_in_texts(phrase, texts)
            for phrase in rule.get("phrases", [])
        )
        rules.append({
            "rule_id": rule["rule_id"],
            "outcome": "block" if blocked else "pass",
        })
    return json.dumps({"rules": rules}, sort_keys=True)


class VisionUnsupported(RuntimeError):
    """A profile port was asked for a vision call it does not shape."""


#: The port's stated per-image token allowance, charged to a
#: vision call's input count when the profile does not report
#: usage of its own. An estimate, stated as one — the same
#: spirit as the context policy's word-count method.
IMAGE_TOKEN_ALLOWANCE = 1024


def compose_vision_reply(image_data: bytes) -> str:
    """The offline profile's vision judgement, deterministically:
    the subject class embedded in the image bytes at fixture
    construction (the PNG ``tEXt`` ground truth), or
    ``unlabelled`` when the bytes carry none. The double reads
    the asset's embedded metadata the way a catalogue feed's
    would be read — it never guesses at pixels, and an image
    with nothing embedded is judged as carrying nothing."""
    from .images import embedded_subject

    subject = embedded_subject(image_data)
    return json.dumps(
        {"subject_class": subject if subject else "unlabelled"},
        sort_keys=True,
    )


class OfflineModelPort:
    """Deterministic offline composer.

    Echoes a trimmed form of the prompt as its reply and counts
    tokens by words, so the cost-ledger arithmetic is exercised in
    tests even though the profile's price is zero. Drafting
    requests (Round 6) get a composed reply instead: the prose is
    built from the prompt's figure sheet alone and invents
    nothing, which is what lets the drafting gate pass offline.
    """

    profile_name = "offline"
    model_name = "offline-deterministic"

    def __init__(self, tier: str = "standard"):
        self.tier = tier

    def complete(self, system: str, prompt: str) -> ModelReply:
        text = compose_draft_reply(prompt)
        if text is None:
            text = compose_screen_reply(prompt)
        if text is None:
            text = " ".join(prompt.split())[:280]
        return ModelReply(
            text=text,
            profile=self.profile_name,
            model=self.model_name,
            tier=self.tier,
            input_tokens=len((system + " " + prompt).split()),
            output_tokens=len(text.split()),
        )

    def analyse_image(self, system: str, prompt: str,
                      image_data: bytes, image_format: str) -> ModelReply:
        """The vision call shape, offline: the reply is the
        composed judgement over the image bytes (see
        :func:`compose_vision_reply`); the image enters the
        token count at the port's stated allowance."""
        text = compose_vision_reply(image_data)
        return ModelReply(
            text=text,
            profile=self.profile_name,
            model=self.model_name,
            tier=self.tier,
            input_tokens=(len((system + " " + prompt).split())
                          + IMAGE_TOKEN_ALLOWANCE),
            output_tokens=len(text.split()),
        )


# A transport takes (url, headers, json body) and returns the decoded
# JSON response. Tests inject a local double of the Meta Model API.
Transport = Callable[[str, dict, dict], dict]


def _httpx_transport(url: str, headers: dict, body: dict) -> dict:
    import httpx

    response = httpx.post(url, headers=headers, json=body, timeout=60.0)
    response.raise_for_status()
    return response.json()


DEFAULT_META_MODEL = "muse-spark"
DEFAULT_META_BASE_URL = "https://api.meta.ai/v1"


class MetaMuseModelPort:
    """Muse Spark over the Meta Model API (OpenAI-compatible shape)."""

    profile_name = "meta-muse"

    def __init__(self, *, api_key: str | None = None, model: str | None = None,
                 tier: str = "standard", base_url: str | None = None,
                 transport: Transport | None = None):
        self.api_key = api_key if api_key is not None else os.environ.get(
            "META_MODEL_API_KEY")
        if not self.api_key:
            raise ProfileNotConfigured(
                "profile 'meta-muse' selected but META_MODEL_API_KEY is not set"
            )
        self.model_name = model or os.environ.get(
            "META_MUSE_MODEL", DEFAULT_META_MODEL)
        self.tier = tier
        self.base_url = (
            base_url or os.environ.get("META_MODEL_API_BASE_URL")
            or DEFAULT_META_BASE_URL
        ).rstrip("/")
        self._transport = transport or _httpx_transport

    def complete(self, system: str, prompt: str) -> ModelReply:
        body = {
            "model": self.model_name,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
        }
        data = self._transport(
            f"{self.base_url}/chat/completions",
            {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            body,
        )
        text = data["choices"][0]["message"]["content"]
        usage = data.get("usage", {})
        return ModelReply(
            text=text,
            profile=self.profile_name,
            model=self.model_name,
            tier=self.tier,
            input_tokens=int(usage.get("prompt_tokens", 0)),
            output_tokens=int(usage.get("completion_tokens", 0)),
        )

    def analyse_image(self, system: str, prompt: str,
                      image_data: bytes, image_format: str) -> ModelReply:
        """The vision call shape (Round 16): the OpenAI-compatible
        multimodal message — the text prompt and the image as a
        data URI — over the same transport, with the same tier
        stamp. Fail-closed exactly like ``complete``: the port
        only exists when a key configured it. No live call is
        made anywhere in this codebase; the suite grades this
        shape against a local double."""
        import base64

        data_uri = (
            f"data:image/{image_format};base64,"
            + base64.b64encode(image_data).decode("ascii")
        )
        body = {
            "model": self.model_name,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url",
                     "image_url": {"url": data_uri}},
                ]},
            ],
        }
        data = self._transport(
            f"{self.base_url}/chat/completions",
            {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            body,
        )
        text = data["choices"][0]["message"]["content"]
        usage = data.get("usage", {})
        return ModelReply(
            text=text,
            profile=self.profile_name,
            model=self.model_name,
            tier=self.tier,
            input_tokens=int(usage.get(
                "prompt_tokens",
                len((system + " " + prompt).split())
                + IMAGE_TOKEN_ALLOWANCE)),
            output_tokens=int(usage.get(
                "completion_tokens", len(text.split()))),
        )


class GovernedModelPort:
    """Wraps a profile port and records every call in the cost
    ledger — and, since Round 12, assembles every call's context
    under the context policy and records the assembly as an
    insert-only ``context_assembly`` artefact. The call id is
    computed from the context actually sent, so an uncompacted
    call's id is exactly what it was before the discipline
    existed; a compacted call is a different call and says so."""

    def __init__(self, inner, store: SqlStore, context_policy=None):
        from .context import CONTEXT_POLICY

        self._inner = inner
        self._store = store
        self._context_policy = context_policy or CONTEXT_POLICY

    @property
    def profile_name(self) -> str:
        return self._inner.profile_name

    @property
    def context_policy(self):
        return self._context_policy

    def complete(self, run_id: str, system: str, prompt: str) -> ModelReply:
        from .context import assemble_context

        assembly = assemble_context(system, prompt, self._context_policy)
        reply = self._inner.complete(assembly.system, assembly.prompt)
        cost = call_cost(reply.profile, reply.tier,
                         reply.input_tokens, reply.output_tokens)
        material = (f"{run_id}|{reply.profile}|{reply.model}|{reply.tier}"
                    f"|{assembly.system}|{assembly.prompt}")
        call_id = "CALL-" + hashlib.sha256(material.encode()).hexdigest()[:20]
        existing = {c.call_id for c in self._store.list_model_calls(run_id)}
        if call_id not in existing:
            self._store.insert_model_call(ModelCallRecord(
                call_id=call_id,
                run_id=run_id,
                profile=reply.profile,
                model=reply.model,
                tier=reply.tier,
                input_tokens=reply.input_tokens,
                output_tokens=reply.output_tokens,
                cost_usd=str(cost),
            ))
        reply.call_id = call_id
        self._record_context(run_id, reply, assembly)
        return reply

    def analyse_image(self, run_id: str, system: str, prompt: str,
                      image_data: bytes, image_format: str) -> ModelReply:
        """A governed vision call (Round 16): the same discipline
        as ``complete`` — context assembled from the text alone
        (image bytes never enter a text context), the call
        recorded in the cost ledger, a context record written.
        The call id's material carries the image's content hash,
        so a call is identified by the exact bytes judged."""
        from .context import assemble_context

        inner_analyse = getattr(self._inner, "analyse_image", None)
        if inner_analyse is None:
            raise VisionUnsupported(
                f"profile {self._inner.profile_name!r} does not "
                f"shape vision calls"
            )
        assembly = assemble_context(system, prompt, self._context_policy)
        reply = inner_analyse(
            assembly.system, assembly.prompt, image_data, image_format)
        cost = call_cost(reply.profile, reply.tier,
                         reply.input_tokens, reply.output_tokens)
        image_hash = hashlib.sha256(image_data).hexdigest()
        material = (f"{run_id}|{reply.profile}|{reply.model}|{reply.tier}"
                    f"|{assembly.system}|{assembly.prompt}|{image_hash}")
        call_id = "CALL-" + hashlib.sha256(material.encode()).hexdigest()[:20]
        existing = {c.call_id for c in self._store.list_model_calls(run_id)}
        if call_id not in existing:
            self._store.insert_model_call(ModelCallRecord(
                call_id=call_id,
                run_id=run_id,
                profile=reply.profile,
                model=reply.model,
                tier=reply.tier,
                input_tokens=reply.input_tokens,
                output_tokens=reply.output_tokens,
                cost_usd=str(cost),
            ))
        reply.call_id = call_id
        self._record_context(run_id, reply, assembly)
        return reply

    def _record_context(self, run_id: str, reply: ModelReply, assembly) -> None:
        from .context import CONTEXT_KIND
        from .models import Artefact
        from .store import canonical_json

        payload = {
            "run_id": run_id,
            "call_id": reply.call_id,
            "profile": reply.profile,
            "model": reply.model,
            "tier": reply.tier,
            **assembly.record(),
        }
        fingerprint = hashlib.sha256(
            f"{run_id}|{reply.call_id}".encode()
        ).hexdigest()
        material = canonical_json({"payload": payload, "fp": fingerprint})
        artefact_id = (
            "CTX-" + hashlib.sha256(material.encode()).hexdigest()[:20]
        )
        if self._store.get_artefact(artefact_id) is not None:
            return
        self._store.insert_artefact(Artefact(
            artefact_id=artefact_id, kind=CONTEXT_KIND,
            payload=payload, input_fingerprint=fingerprint,
        ))

    def reconcile(self, run_id: str) -> list[str]:
        """Recompute every recorded cost from the price table; the
        recorded ledger must reconcile exactly, row for row."""
        problems: list[str] = []
        for record in self._store.list_model_calls(run_id):
            expected = call_cost(record.profile, record.tier,
                                 record.input_tokens, record.output_tokens)
            if record.cost != expected:
                problems.append(f"cost_mismatch:{record.call_id}")
        return problems

    def run_total(self, run_id: str) -> Decimal:
        return sum(
            (c.cost for c in self._store.list_model_calls(run_id)), Decimal("0")
        )


def build_model_port(settings, store: SqlStore) -> GovernedModelPort:
    """Wire the configured profile. Hosted profiles fail closed here."""
    if settings.model_profile == "offline":
        inner = OfflineModelPort(tier=settings.model_tier)
    elif settings.model_profile == "meta-muse":
        inner = MetaMuseModelPort(tier=settings.model_tier)
    else:
        raise ProfileNotConfigured(
            f"unknown model profile: {settings.model_profile!r}"
        )
    return GovernedModelPort(inner, store)


# -- profile verification (Round 12) --------------------------------------
#
# The hosted profile's honest state, resolvable without a call.
# ``profile_report`` never constructs a transport and never calls
# out; ``probe_meta_muse`` runs one governed call through a
# transport the CALLER supplies — in the test suite, a local double
# of the Meta Model API — so the full path (request shape, auth,
# tier stamp, ledger record, cost re-performance, context record)
# is exercised with no network and no key. A live probe is a
# deliberate act for whoever holds a real key; nothing here makes
# one implicitly.

PROBE_SYSTEM = "You verify model profiles. Answer exactly as asked."
PROBE_PROMPT = "Reply with the single word: ready."
PROBE_RUN_ID = "profile-probe"


def profile_report(settings) -> dict:
    """Resolve the configured profile and report its state.

    ``live_call_made`` is always False: this report is resolution,
    not verification by calling. For ``meta-muse`` the model id is
    ``META_MUSE_MODEL`` or the built-in default ``muse-spark`` —
    configurable and, until a live account confirms it, unverified;
    the report says which source the id came from. Without a key
    the status is ``not_configured`` and ``fail_closed`` records
    that wiring the profile raised :class:`ProfileNotConfigured`,
    proved here by attempting the construction.
    """
    report: dict = {
        "profile": settings.model_profile,
        "tier": settings.model_tier,
        "rate_card_version": RATE_CARD_VERSION,
        "live_call_made": False,
        "verification": "stub_transport_only",
    }
    if settings.model_profile == "offline":
        report.update(
            status="ready", configured=True, key_required=False,
            model=OfflineModelPort.model_name, base_url=None,
        )
        return report
    if settings.model_profile != "meta-muse":
        report.update(status="unknown_profile", configured=False)
        return report
    model_from_env = "META_MUSE_MODEL" in os.environ
    report.update(
        key_required=True,
        key_present=bool(os.environ.get("META_MODEL_API_KEY")),
        model=os.environ.get("META_MUSE_MODEL", DEFAULT_META_MODEL),
        model_id_source=("environment" if model_from_env
                         else "built-in default"),
        model_id_verified=False,
        base_url=(os.environ.get("META_MODEL_API_BASE_URL")
                  or DEFAULT_META_BASE_URL).rstrip("/"),
    )
    if report["key_present"]:
        # Prove the wiring constructs; the transport is never
        # invoked, so this makes no call, live or otherwise.
        MetaMuseModelPort(tier=settings.model_tier)
        report.update(status="configured_unverified", configured=True)
    else:
        try:
            MetaMuseModelPort(tier=settings.model_tier)
            report.update(status="not_configured", configured=False,
                          fail_closed=False)
        except ProfileNotConfigured:
            report.update(status="not_configured", configured=False,
                          fail_closed=True)
    return report


def probe_meta_muse(store: SqlStore, *, api_key: str,
                    tier: str = "standard", model: str | None = None,
                    base_url: str | None = None,
                    transport: Transport) -> dict:
    """Exercise the ``meta-muse`` profile end to end against a
    caller-supplied transport and report what the ledger recorded.

    The probe call is an ordinary governed call — context record,
    cost record, tier stamp — under the run id ``profile-probe``.
    ``live_call_made`` in the report is False by construction: the
    transport is the caller's, and in every use inside this
    codebase it is a local double.
    """
    inner = MetaMuseModelPort(
        api_key=api_key, model=model, tier=tier,
        base_url=base_url, transport=transport,
    )
    port = GovernedModelPort(inner, store)
    reply = port.complete(PROBE_RUN_ID, PROBE_SYSTEM, PROBE_PROMPT)
    calls = store.list_model_calls(PROBE_RUN_ID)
    return {
        "profile": "meta-muse",
        "model": reply.model,
        "tier": reply.tier,
        "base_url": inner.base_url,
        "call_id": reply.call_id,
        "reply_text": reply.text,
        "calls_recorded": len(calls),
        "cost_usd": calls[0].cost_usd if calls else None,
        "reconcile_problems": port.reconcile(PROBE_RUN_ID),
        "live_call_made": False,
        "transport": "caller-supplied — no network call was made",
    }
