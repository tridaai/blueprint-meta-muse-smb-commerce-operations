"""Drafting: the model enters the loop, under governance.

Round 6 puts prose on the verified record. A drafting service
produces three drafts, each through the governed model port and
no other route:

- the **plan narrative** over the head growth plan — prose that
  walks the plan's priorities in the plan's own order;
- the **winback email copy** for the email audit's draft flow
  proposal — a subject and a body per proposed step;
- a **draft budget-action note** for the decaying ad set the
  ad-decay diagnosis names.

Every draft is an insert-only artefact bound to the exact
artefacts it drafts from, stamped with the model profile, the
tier and the cost-ledger record id of the call that produced it.

The drafting contract
---------------------
The service hands the model a *figure sheet*: the only figures
the draft may state, each entry carrying the artefact id and the
field it resolves to. The model must reply with one JSON
document — ``{"content": ..., "claims": [...]}`` — where every
claim copies a figure-sheet entry's value, source artefact id
and source field. Content is ``{"prose": ...}`` for the
narrative and the budget note, and ``{"steps": [{"subject": ...,
"body": ...}, ...]}`` for the email copy.

The figure discipline (Law 1) is enforced in code by the draft
verifier, in two directions:

- **Claims resolve.** Every claim's cited artefact must exist,
  its cited field must resolve under the field-path rule below,
  and the resolved value must equal the claimed value.
- **Prose states nothing else.** The prose under scan — the
  narrative or note prose, or every email subject and body — is
  scanned for figure tokens. ISO dates (YYYY-MM-DD) are removed
  first; a figure token is then any maximal match of
  ``\\d[\\d,]*(\\.\\d+)?%?`` not immediately preceded by a word
  character or a hyphen (so SKU and artefact-id fragments are
  not tokens). Every figure token must equal, as a Decimal, the
  value of at least one claim. A number in the prose that no
  claim carries is itself a verification failure.

The field-path rule: dotted segments; each segment is a mapping
key, a key with a list index (``step_delays_hours[1]``), or a
key with a selector (``findings[ad_set=AD-SOCIAL]``) naming the
one list element whose named field string-equals the selector
value. Selector values may not contain a dot or a bracket.

A draft that fails either direction is **refused**: a draft
refusal artefact records the problems and the claims as
returned, and no draft is stored as usable. The refused text is
never stored — the refusal names what was wrong, not the copy
that carried it. ``verify`` re-performs any stored draft at any
time by the same route, and also re-checks the draft's honesty:
its status is ``draft``, its source bindings resolve to
artefacts of the right kinds, and its stamped call record is in
the cost ledger with a cost the price table re-performs. A
draft is a draft — approval is Round 8 and the guardian screen
is Round 7; no surface in this round calls a draft approved,
sent or published.
"""

from __future__ import annotations

import hashlib
import json
import re
from decimal import Decimal, InvalidOperation

from .diagnosis import AD_DECAY_KIND, EMAIL_AUDIT_KIND, PROPOSAL_KIND
from .modelport import GovernedModelPort, call_cost
from .models import Artefact
from .plan import PLAN_KIND, PlanService
from .store import SqlStore, canonical_json

NARRATIVE_KIND = "draft_plan_narrative"
EMAIL_COPY_KIND = "draft_email_copy"
AD_NOTE_KIND = "draft_ad_note"
DRAFT_REFUSAL_KIND = "draft_refusal"

DRAFT_KINDS = (NARRATIVE_KIND, EMAIL_COPY_KIND, AD_NOTE_KIND)

_KIND_BY_DRAFT = {
    "plan_narrative": NARRATIVE_KIND,
    "winback_email_copy": EMAIL_COPY_KIND,
    "ad_budget_note": AD_NOTE_KIND,
}
_PREFIX_BY_DRAFT = {
    "plan_narrative": "DRN-",
    "winback_email_copy": "DCE-",
    "ad_budget_note": "DAN-",
}
# The artefact kinds each draft kind's source bindings must name,
# by binding role.
_BINDING_KINDS = {
    "plan_narrative": {"plan": PLAN_KIND},
    "winback_email_copy": {"proposal": PROPOSAL_KIND,
                           "audit": EMAIL_AUDIT_KIND},
    "ad_budget_note": {"diagnosis": AD_DECAY_KIND},
}

_FAMILY_LABELS = {
    "stockout_risk": "Stockout risk",
    "ad_decay": "Ad decay",
    "expense_anomaly": "Expense anomaly",
    "refund_anomaly": "Refund anomaly",
    "email_flow_audit": "Email-flow audit",
}

SYSTEM_PROMPT = (
    "You draft operations copy for a small retailer. You are handed"
    " a figure sheet: the only figures you may state. Reply with one"
    " JSON document, {\"content\": ..., \"claims\": [...]}, where"
    " every claim copies a figure-sheet entry's value,"
    " source_artefact_id and source_field exactly. Quote figures"
    " verbatim, and state no number that is not in the figure sheet."
    " Everything you write is a draft: never approved, never sent,"
    " never published."
)

BUSINESS_NAME = "Hartwell Home Goods"


class DraftRefused(Exception):
    """A draft failed its gate and was refused whole."""

    def __init__(self, refusal: Artefact):
        super().__init__(
            f"draft refused: {', '.join(refusal.payload['problems'])}"
        )
        self.refusal = refusal


# -- field paths and figure tokens ----------------------------------------


_SEGMENT = re.compile(
    r"^([A-Za-z_][A-Za-z0-9_]*)"
    r"(?:\[(\d+)\]|\[([A-Za-z_][A-Za-z0-9_]*)=([^\]]+)\])?$"
)

_MISSING = object()


def resolve_field(payload: dict, path: str):
    """Resolve a field path against an artefact payload, or
    return the missing sentinel. See the module docstring for
    the rule."""
    current = payload
    for segment in path.split("."):
        match = _SEGMENT.match(segment)
        if match is None:
            return _MISSING
        key, index, selector_key, selector_value = match.groups()
        if not isinstance(current, dict) or key not in current:
            return _MISSING
        current = current[key]
        if index is not None:
            if not isinstance(current, list) or int(index) >= len(current):
                return _MISSING
            current = current[int(index)]
        elif selector_key is not None:
            if not isinstance(current, list):
                return _MISSING
            matches = [
                element for element in current
                if isinstance(element, dict)
                and str(element.get(selector_key)) == selector_value
            ]
            if len(matches) != 1:
                return _MISSING
            current = matches[0]
    return current


_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_FIGURE_TOKEN = re.compile(r"(?<![\w\-])\d[\d,]*(\.\d+)?%?")


def figure_tokens(text: str) -> list[tuple[str, Decimal]]:
    """The figure tokens a text states, per the extraction rule
    in the module docstring: (raw token, its Decimal value)."""
    cleaned = _ISO_DATE.sub(" ", text)
    tokens = []
    for match in _FIGURE_TOKEN.finditer(cleaned):
        raw = match.group(0)
        tokens.append(
            (raw, Decimal(raw.replace(",", "").rstrip("%")))
        )
    return tokens


def _values_equal(resolved, claimed) -> bool:
    try:
        return Decimal(str(resolved)) == Decimal(str(claimed))
    except InvalidOperation:
        return str(resolved) == str(claimed)


def _prose_texts(draft_kind: str, content: dict) -> list[str]:
    """The prose under scan for a draft kind, per the contract."""
    if draft_kind == "winback_email_copy":
        texts = []
        for step in content.get("steps", []):
            texts.append(step.get("subject", ""))
            texts.append(step.get("body", ""))
        return texts
    return [content.get("prose", "")]


# -- the service ------------------------------------------------------------


class DraftingService:
    def __init__(self, store: SqlStore, model: GovernedModelPort):
        self._store = store
        self._model = model
        self._plans = PlanService(store)

    # -- figure sheets ------------------------------------------------------

    def _narrative_sheet(self, plan: Artefact) -> dict:
        payload = plan.payload
        claims: list[dict] = []
        summary = []
        for figure in payload["summary"]["figures"]:
            summary.append({
                "name": figure["name"],
                "value": figure["value"],
                "source_artefact_id": figure["source_artefact_id"],
                "source_field": figure["source_field"],
            })
            claims.append({
                "value": figure["value"],
                "source_artefact_id": figure["source_artefact_id"],
                "source_field": figure["source_field"],
            })
        priorities = []
        coverage = []
        for entry in payload["priorities"]:
            if entry["rank"] is None:
                coverage.append(
                    {"family_label": _FAMILY_LABELS[entry["family"]]}
                )
                continue
            has_exposure = entry["exposure_field"] is not None
            priorities.append({
                "rank": entry["rank"],
                "family_label": _FAMILY_LABELS[entry["family"]],
                "key": entry["finding_key"],
                "exposure": entry["exposure"] if has_exposure else None,
            })
            if has_exposure:
                claims.append({
                    "value": entry["exposure"],
                    "source_artefact_id": plan.artefact_id,
                    "source_field": (
                        f"priorities[rank={entry['rank']}].exposure"
                    ),
                })
        return {
            "draft_kind": "plan_narrative",
            "business": BUSINESS_NAME,
            "as_of": payload["as_of"],
            "summary": summary,
            "priorities": priorities,
            "coverage": coverage,
            "claims": claims,
        }

    def _email_sheet(self, proposal: Artefact) -> dict:
        payload = proposal.payload
        delays = [str(d) for d in payload["step_delays_hours"]]
        steps = [
            {"index": index + 1, "delay_hours": delays[index]}
            for index in range(int(payload["steps"]))
        ]
        claims = [
            {
                "value": str(payload["steps"]),
                "source_artefact_id": proposal.artefact_id,
                "source_field": "steps",
            },
            {
                "value": delays[-1],
                "source_artefact_id": proposal.artefact_id,
                "source_field": f"step_delays_hours[{len(delays) - 1}]",
            },
        ]
        return {
            "draft_kind": "winback_email_copy",
            "business": BUSINESS_NAME,
            "flow_key": payload["flow_key"],
            "flow_name": payload["name"],
            "steps": steps,
            "claims": claims,
        }

    def _ad_note_sheet(self, diagnosis: Artefact, finding: dict) -> dict:
        ad_set = finding["ad_set"]
        figures = []
        for role, field in (
            ("first_week_roas", "first_week_roas"),
            ("latest_week_roas", "latest_week_roas"),
            ("decay_factor", "decay_factor"),
            ("total_spend", "total_spend"),
        ):
            figures.append({
                "role": role,
                "value": finding[field],
                "source_artefact_id": diagnosis.artefact_id,
                "source_field": f"findings[ad_set={ad_set}].{field}",
            })
        return {
            "draft_kind": "ad_budget_note",
            "business": BUSINESS_NAME,
            "ad_set": ad_set,
            "figures": figures,
            "claims": [
                {
                    "value": figure["value"],
                    "source_artefact_id": figure["source_artefact_id"],
                    "source_field": figure["source_field"],
                }
                for figure in figures
            ],
        }

    # -- the governed call ---------------------------------------------------

    def _call(self, draft_kind: str, sheet: dict, run_id: str):
        prompt = (
            f"DRAFT-REQUEST {draft_kind}\n"
            f"FIGURE-SHEET {json.dumps(sheet, sort_keys=True)}\n"
            f"Draft the {draft_kind} from the figure sheet only."
        )
        reply = self._model.complete(run_id, SYSTEM_PROMPT, prompt)
        try:
            document = json.loads(reply.text)
            content = document["content"]
            claims = document["claims"]
            if not isinstance(content, dict) or not isinstance(claims, list):
                raise ValueError("draft document shape")
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            content, claims = {}, []
            return reply, content, claims, ["draft_reply_unparseable"]
        return reply, content, claims, []

    def _draft(self, draft_kind: str, sheet: dict,
               bindings: dict[str, str], run_id: str) -> Artefact:
        reply, content, claims, problems = self._call(
            draft_kind, sheet, run_id
        )
        payload = {
            "draft_kind": draft_kind,
            "status": "draft",
            "source_bindings": bindings,
            "source_artefact_ids": sorted(set(bindings.values())),
            "model_profile": reply.profile,
            "model": reply.model,
            "tier": reply.tier,
            "model_call_id": reply.call_id,
            "run_id": run_id,
            "content": content,
            "claims": claims,
            "supersedes": None,
        }
        problems = problems + self._check(payload)
        if problems:
            raise DraftRefused(self._refuse(
                draft_kind, bindings, problems, claims, reply, run_id
            ))
        kind = _KIND_BY_DRAFT[draft_kind]
        previous = self._store.latest_artefact(kind)
        fingerprint = hashlib.sha256(
            f"{canonical_json(bindings)}:{reply.call_id}".encode("utf-8")
        ).hexdigest()
        material = canonical_json({"payload": payload, "fp": fingerprint})
        artefact_id = (
            _PREFIX_BY_DRAFT[draft_kind]
            + hashlib.sha256(material.encode()).hexdigest()[:20]
        )
        existing = self._store.get_artefact(artefact_id)
        if existing is not None:
            return existing
        if previous is not None and previous.artefact_id != artefact_id:
            payload["supersedes"] = previous.artefact_id
            material = canonical_json({"payload": payload, "fp": fingerprint})
            artefact_id = (
                _PREFIX_BY_DRAFT[draft_kind]
                + hashlib.sha256(material.encode()).hexdigest()[:20]
            )
            existing = self._store.get_artefact(artefact_id)
            if existing is not None:
                return existing
        draft = Artefact(
            artefact_id=artefact_id, kind=kind,
            payload=payload, input_fingerprint=fingerprint,
        )
        self._store.insert_artefact(draft)
        return draft

    def _refuse(self, draft_kind: str, bindings: dict[str, str],
                problems: list[str], claims: list, reply,
                run_id: str) -> Artefact:
        payload = {
            "draft_kind": draft_kind,
            "status": "refused",
            "source_bindings": bindings,
            "problems": problems,
            "claims_as_returned": claims,
            "model_profile": reply.profile,
            "model": reply.model,
            "tier": reply.tier,
            "model_call_id": reply.call_id,
            "run_id": run_id,
        }
        fingerprint = hashlib.sha256(
            f"{canonical_json(bindings)}:{reply.call_id}"
            f":{canonical_json(problems)}".encode("utf-8")
        ).hexdigest()
        material = canonical_json({"payload": payload, "fp": fingerprint})
        artefact_id = "DRR-" + hashlib.sha256(material.encode()).hexdigest()[:20]
        existing = self._store.get_artefact(artefact_id)
        if existing is not None:
            return existing
        refusal = Artefact(
            artefact_id=artefact_id, kind=DRAFT_REFUSAL_KIND,
            payload=payload, input_fingerprint=fingerprint,
        )
        self._store.insert_artefact(refusal)
        return refusal

    # -- the three drafts ------------------------------------------------------

    def draft_plan_narrative(self, plan: Artefact | None = None) -> Artefact:
        """The narrative over the head growth plan (assembled
        first when no plan is stored)."""
        if plan is None:
            plan = self._store.latest_artefact(PLAN_KIND)
        if plan is None:
            plan = self._plans.assemble()
        sheet = self._narrative_sheet(plan)
        run_id = f"draft-narrative-{plan.artefact_id}"
        return self._draft(
            "plan_narrative", sheet, {"plan": plan.artefact_id}, run_id
        )

    def draft_email_copy(self, proposal: Artefact) -> Artefact:
        """The email copy for one draft flow proposal from the
        email-flow audit (the winback proposal on the Hartwell
        record)."""
        audit = self._store.get_artefact(
            proposal.payload["audit_artefact_id"]
        )
        bindings = {"proposal": proposal.artefact_id}
        if audit is not None:
            bindings["audit"] = audit.artefact_id
        sheet = self._email_sheet(proposal)
        run_id = f"draft-email-{proposal.artefact_id}"
        return self._draft("winback_email_copy", sheet, bindings, run_id)

    def draft_ad_note(self, diagnosis: Artefact | None = None) -> Artefact:
        """The budget-action note over the head ad-decay
        diagnosis's decaying set."""
        if diagnosis is None:
            diagnosis = self._store.latest_artefact(AD_DECAY_KIND)
        if diagnosis is None or not diagnosis.payload["findings"]:
            raise ValueError("no decaying ad set to draft a note for")
        finding = diagnosis.payload["findings"][0]
        sheet = self._ad_note_sheet(diagnosis, finding)
        run_id = f"draft-ad-note-{diagnosis.artefact_id}"
        return self._draft(
            "ad_budget_note", sheet,
            {"diagnosis": diagnosis.artefact_id}, run_id,
        )

    def draft_all(self) -> dict[str, Artefact]:
        """The round's full drafting pass over the head plan: the
        narrative, the email copy for the proposal the plan's
        actions cite, and the budget note. Refusals propagate —
        a refused draft fails the pass whole, with the refusal
        stored."""
        plan = self._store.latest_artefact(PLAN_KIND)
        if plan is None:
            plan = self._plans.assemble()
        drafts = {"plan_narrative": self.draft_plan_narrative(plan)}
        for action in plan.payload["actions"]:
            proposal_id = action.get("proposal_artefact_id")
            if proposal_id is None:
                continue
            proposal = self._store.get_artefact(proposal_id)
            if proposal is not None:
                drafts["winback_email_copy"] = self.draft_email_copy(proposal)
        diagnosis = self._store.latest_artefact(AD_DECAY_KIND)
        if diagnosis is not None and diagnosis.payload["findings"]:
            drafts["ad_budget_note"] = self.draft_ad_note(diagnosis)
        return drafts

    # -- verification -----------------------------------------------------------

    def _check(self, payload: dict) -> list[str]:
        """The draft gate, shared by drafting and ``verify``:
        honesty of state, source bindings, the stamped call, and
        the two-direction figure discipline."""
        problems: list[str] = []
        draft_kind = payload.get("draft_kind")
        if payload.get("status") != "draft":
            problems.append("draft_status_not_draft")
        expected = _BINDING_KINDS.get(draft_kind, {})
        bindings = payload.get("source_bindings", {})
        for role, kind in expected.items():
            artefact_id = bindings.get(role)
            if artefact_id is None:
                problems.append(f"draft_source_missing:{role}")
                continue
            artefact = self._store.get_artefact(artefact_id)
            if artefact is None:
                problems.append(f"draft_source_missing:{role}")
            elif artefact.kind != kind:
                problems.append(f"draft_source_wrong_kind:{role}")
        if sorted(set(bindings.values())) != payload.get(
            "source_artefact_ids"
        ):
            problems.append("draft_source_binding_mismatch")

        record = None
        for call in self._store.list_model_calls(payload.get("run_id")):
            if call.call_id == payload.get("model_call_id"):
                record = call
                break
        if record is None:
            problems.append("draft_call_missing")
        else:
            if (record.profile != payload.get("model_profile")
                    or record.tier != payload.get("tier")):
                problems.append("draft_call_stamp_mismatch")
            expected_cost = call_cost(
                record.profile, record.tier,
                record.input_tokens, record.output_tokens,
            )
            if record.cost != expected_cost:
                problems.append("draft_cost_mismatch")

        content = payload.get("content", {})
        claims = payload.get("claims", [])
        if draft_kind == "winback_email_copy":
            proposal = self._store.get_artefact(bindings.get("proposal", ""))
            if proposal is not None and len(content.get("steps", [])) != int(
                proposal.payload["steps"]
            ):
                problems.append("draft_content_shape:steps")
        claimed_values: list[Decimal] = []
        for claim in claims:
            artefact = self._store.get_artefact(
                claim.get("source_artefact_id", "")
            )
            if artefact is None:
                problems.append(
                    f"draft_claim_source_missing:"
                    f"{claim.get('source_artefact_id')}"
                )
                continue
            resolved = resolve_field(
                artefact.payload, claim.get("source_field", "")
            )
            if resolved is _MISSING:
                problems.append(
                    f"draft_claim_unresolvable:{claim.get('source_field')}"
                )
            elif not _values_equal(resolved, claim.get("value")):
                problems.append(
                    f"draft_figure_mismatch:{claim.get('source_field')}"
                )
            else:
                try:
                    claimed_values.append(Decimal(str(claim["value"])))
                except InvalidOperation:
                    pass
        for text in _prose_texts(draft_kind, content):
            for raw, value in figure_tokens(text):
                if value not in claimed_values:
                    problems.append(f"prose_figure_unclaimed:{raw}")
        return problems

    def verify(self, artefact: Artefact) -> list[str]:
        """Re-perform a stored draft at any time, by the same
        route the drafting gate applies.

        A refusal artefact is a record of a refusal, not a draft;
        it carries nothing to re-perform and verifies clean."""
        if artefact.kind == DRAFT_REFUSAL_KIND:
            return []
        if artefact.kind not in DRAFT_KINDS:
            return [f"not_a_draft:{artefact.kind}"]
        return self._check(artefact.payload)
