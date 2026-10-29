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
Contributor is far cheaper and Meta may train on it — the price table
below carries both, so the trade-off is visible in the ledger itself.

Prices are USD per 1M tokens (input, output), as published at
dev.meta.ai for Muse Spark: standard 1.25 / 4.25, contributor
0.10 / 0.20. The offline profile costs nothing by construction.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from decimal import Decimal
from typing import Callable

from .models import ModelCallRecord
from .store import SqlStore

PRICE_TABLE: dict[str, dict[str, tuple[Decimal, Decimal]]] = {
    "meta-muse": {
        "standard": (Decimal("1.25"), Decimal("4.25")),
        "contributor": (Decimal("0.10"), Decimal("0.20")),
    },
    "offline": {
        "standard": (Decimal("0"), Decimal("0")),
        "contributor": (Decimal("0"), Decimal("0")),
    },
}

COST_PLACES = Decimal("0.000001")


def call_cost(profile: str, tier: str, input_tokens: int, output_tokens: int) -> Decimal:
    price_in, price_out = PRICE_TABLE[profile][tier]
    cost = (
        Decimal(input_tokens) * price_in + Decimal(output_tokens) * price_out
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
            text = " ".join(prompt.split())[:280]
        return ModelReply(
            text=text,
            profile=self.profile_name,
            model=self.model_name,
            tier=self.tier,
            input_tokens=len((system + " " + prompt).split()),
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
        self.model_name = model or os.environ.get("META_MUSE_MODEL", "muse-spark")
        self.tier = tier
        self.base_url = (
            base_url or os.environ.get("META_MODEL_API_BASE_URL")
            or "https://api.meta.ai/v1"
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


class GovernedModelPort:
    """Wraps a profile port and records every call in the cost ledger."""

    def __init__(self, inner, store: SqlStore):
        self._inner = inner
        self._store = store

    @property
    def profile_name(self) -> str:
        return self._inner.profile_name

    def complete(self, run_id: str, system: str, prompt: str) -> ModelReply:
        reply = self._inner.complete(system, prompt)
        cost = call_cost(reply.profile, reply.tier,
                         reply.input_tokens, reply.output_tokens)
        material = f"{run_id}|{reply.profile}|{reply.model}|{reply.tier}|{system}|{prompt}"
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
        return reply

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
