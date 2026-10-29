"""The approval queue: Law 2's second gate, the named human act.

Round 8 builds the approval half of Law 2. The guardian screen
(Round 7) decides what *may* be approved; this module records
what a named human *did* approve or reject, and derives the
queue from those acts. Nothing here executes anything: an
approved draft is approved — not sent. Execution against the
real vendor systems (the storefront, the email platform, the
ads manager) is out of scope by design, in this round and
every later one, and every approved read surface says so in
those words.

Acts, not state
---------------
Approval and rejection are insert-only artefacts — acts — never
a status someone edits on the draft:

- An **approval act** names the approver, the draft artefact
  id AND its content hash, and the passing guardian verdict it
  relies on (artefact id and pack version). It also snapshots
  the bound inputs at approval time — the draft's source
  artefacts, the head artefact of each bound kind, and the
  head ledger — so staleness can be derived later without
  rewriting the act.
- A **rejection act** names the rejecter, a reason code from
  the stated vocabulary below, and optional free text. A
  rejected draft leaves the queue; the rejection stays on the
  record, in the draft's history, forever.
- A **refused attempt** — an approval or rejection the service
  will not record as an act — records an approval-refusal
  artefact naming the draft, the actor and the problems, and
  raises :class:`ApprovalRefused`. The refusal is the record
  that the attempt happened and why it failed.

Approval is refused when the draft has no passing verdict,
when the verdict re-verification fails (a verdict whose hash
differs from the draft's current hash is the graded case),
when the draft's source artefact has been superseded or
refused since the verdict, or when the draft already carries
an approval — there is no double-approval. Rejection is
refused for a draft that is not awaiting approval, and for a
draft that already carries an approval or a rejection.

The queue is a derived view
---------------------------
``queue`` folds the acts over the guardian's gate: awaiting is
the guardian's passing, re-verified list minus drafts that
carry an approval or a rejection act; approved and rejected
are the folds of their acts. Because the fold is computed on
read, no surface can edit a draft into the approved column:
an approval act that does not re-verify (``verify`` below) is
excluded from the approved list and counted as a problem
wherever it is read.

Stale approvals
---------------
An approval binds inputs that can move afterwards: a new
ledger, a superseded plan, a new verdict under a changed
pack. Moving an input does not un-approve the draft — the act
stays as history — but the approved read surface derives
staleness at read time and marks the approval STALE, naming
each moved input. A stale approval is never shown clean.
``verify`` judges the act itself (its bindings, its verdict,
its honesty of state); staleness is a separate derivation
(``staleness``) precisely so a stale-but-genuine act still
verifies as a true record of what was approved, while never
reading as currently clean.

Honesty of state
----------------
Every approval act and every approved queue entry carries
``display_status`` exactly ``approved — not sent`` and
``execution_status`` exactly ``not_sent``. Any act claiming
anything else fails verification.
"""

from __future__ import annotations

import hashlib

from .drafting import DRAFT_KINDS
from .guardian import GuardianService, draft_content_hash
from .models import Artefact
from .store import SqlStore, canonical_json

APPROVAL_KIND = "approval_act"
REJECTION_KIND = "rejection_act"
APPROVAL_REFUSAL_KIND = "approval_refusal"

ACT_KINDS = (APPROVAL_KIND, REJECTION_KIND)

# The stated vocabulary a rejection reason must come from. The
# free-text note is for the specifics; the code is what reads
# and reports can group by.
REJECTION_REASONS = (
    "not_ready",
    "wrong_tone",
    "figures_unclear",
    "timing",
    "other",
)

APPROVED_DISPLAY_STATUS = "approved — not sent"
NOT_SENT = "not_sent"


class ApprovalRefused(Exception):
    """An approval or rejection attempt the service refused,
    with the refusal artefact that records why."""

    def __init__(self, refusal: Artefact):
        super().__init__(
            f"approval refused: {', '.join(refusal.payload['problems'])}"
        )
        self.refusal = refusal


class ApprovalService:
    def __init__(self, store: SqlStore, guardian: GuardianService):
        self._store = store
        self._guardian = guardian

    # -- lookups ---------------------------------------------------------

    def _acts(self, kind: str, draft_artefact_id: str) -> list[Artefact]:
        return [
            a for a in self._store.list_artefacts(kind)
            if a.payload.get("draft_artefact_id") == draft_artefact_id
        ]

    def approval_for(self, draft_artefact_id: str) -> Artefact | None:
        acts = self._acts(APPROVAL_KIND, draft_artefact_id)
        return acts[-1] if acts else None

    def rejection_for(self, draft_artefact_id: str) -> Artefact | None:
        acts = self._acts(REJECTION_KIND, draft_artefact_id)
        return acts[-1] if acts else None

    def refusals(self) -> list[Artefact]:
        return self._store.list_artefacts(APPROVAL_REFUSAL_KIND)

    # -- bound-input snapshot and staleness --------------------------------

    def _bound_inputs(self, draft: Artefact, verdict: Artefact) -> dict:
        """The inputs an approval binds, as they stand at
        approval time: the verdict, the draft's hash and
        sources, the head of each source's kind, and the head
        ledger."""
        source_heads: dict[str, str | None] = {}
        for source_id in draft.payload.get("source_artefact_ids", []):
            source = self._store.get_artefact(source_id)
            if source is None:
                source_heads[source_id] = None
                continue
            if source.kind == "draft_flow_proposal":
                proposals = [
                    a for a in self._store.list_artefacts(
                        "draft_flow_proposal")
                    if a.payload.get("flow_key")
                    == source.payload.get("flow_key")
                ]
                source_heads[source_id] = (
                    proposals[-1].artefact_id if proposals else source_id
                )
            else:
                head = self._store.latest_artefact(source.kind)
                source_heads[source_id] = (
                    head.artefact_id if head is not None else None
                )
        ledger = self._store.latest_artefact("ledger")
        return {
            "draft_content_hash": draft_content_hash(draft),
            "verdict_artefact_id": verdict.artefact_id,
            "verdict_pack_version": verdict.payload.get("pack_version"),
            "source_artefact_ids": list(
                draft.payload.get("source_artefact_ids", [])),
            "source_heads": source_heads,
            "ledger_artefact_id": (
                ledger.artefact_id if ledger is not None else None),
        }

    def staleness(self, approval: Artefact) -> list[str]:
        """The moved inputs for one approval act, derived at
        read time against its bound-input snapshot. An empty
        list means nothing the approval binds has moved."""
        payload = approval.payload
        bound = payload.get("bound_inputs", {})
        moved: list[str] = []
        draft = self._store.get_artefact(
            payload.get("draft_artefact_id", ""))
        if draft is None:
            return [f"draft:{payload.get('draft_artefact_id')}"]
        if draft_content_hash(draft) != bound.get("draft_content_hash"):
            moved.append(f"draft:{draft.artefact_id}")
        verdict = self._guardian.latest_verdict(draft.artefact_id)
        if verdict is None:
            moved.append("guardian_verdict:none")
        elif verdict.artefact_id != bound.get("verdict_artefact_id"):
            moved.append(f"guardian_verdict:{verdict.artefact_id}")
        elif verdict.payload.get("pack_version") != bound.get(
                "verdict_pack_version"):
            moved.append(
                f"guardian_verdict:{verdict.artefact_id}"
                f":pack {verdict.payload.get('pack_version')}")
        problems = self._guardian._source_problems(draft)
        for source_id in problems["superseded"]:
            moved.append(f"source_superseded:{source_id}")
        for source_id in problems["refused"]:
            moved.append(f"source_refused:{source_id}")
        current = self._bound_inputs(draft, verdict) if verdict else None
        if current is not None:
            for source_id, head_id in bound.get(
                    "source_heads", {}).items():
                if (current["source_heads"].get(source_id) != head_id
                        and f"source_superseded:{source_id}" not in moved):
                    moved.append(f"source:{source_id}")
            if current["ledger_artefact_id"] != bound.get(
                    "ledger_artefact_id"):
                moved.append(
                    f"ledger:{current['ledger_artefact_id']}")
        return moved

    # -- the refusal record -------------------------------------------------

    def _refuse(self, action: str, draft_id: str, actor: str,
                problems: list[str], extra: dict | None = None
                ) -> Artefact:
        payload = {
            "action": action,
            "draft_artefact_id": draft_id,
            "actor": actor,
            "problems": problems,
        }
        if extra:
            payload.update(extra)
        fingerprint = hashlib.sha256(
            f"{canonical_json(payload)}".encode("utf-8")
        ).hexdigest()
        material = canonical_json({"payload": payload, "fp": fingerprint})
        artefact_id = (
            "ARF-" + hashlib.sha256(material.encode()).hexdigest()[:20]
        )
        existing = self._store.get_artefact(artefact_id)
        if existing is not None:
            return existing
        refusal = Artefact(
            artefact_id=artefact_id, kind=APPROVAL_REFUSAL_KIND,
            payload=payload, input_fingerprint=fingerprint,
        )
        self._store.insert_artefact(refusal)
        return refusal

    def _fail(self, action: str, draft_id: str, actor: str,
              problems: list[str], extra: dict | None = None):
        raise ApprovalRefused(
            self._refuse(action, draft_id, actor, problems, extra))

    # -- approval -------------------------------------------------------------

    def approve(self, draft_id: str, approver: str) -> Artefact:
        """Record a named approval for one draft, or refuse the
        attempt with the reason recorded (see the module
        docstring for the refusal conditions)."""
        if not approver or not approver.strip():
            self._fail("approve", draft_id, approver or "",
                       ["approver_missing"])
        draft = self._store.get_artefact(draft_id)
        if draft is None or draft.kind not in DRAFT_KINDS:
            self._fail("approve", draft_id, approver,
                       ["draft_missing_or_not_a_draft"])
        if self.approval_for(draft_id) is not None:
            self._fail("approve", draft_id, approver,
                       ["already_approved"])
        if self.rejection_for(draft_id) is not None:
            self._fail("approve", draft_id, approver,
                       ["already_rejected"])
        verdict = self._guardian.latest_verdict(draft_id)
        if verdict is None:
            self._fail("approve", draft_id, approver,
                       ["no_guardian_verdict"])
        if verdict.payload.get("outcome") != "pass":
            self._fail(
                "approve", draft_id, approver,
                ["guardian_verdict_not_passing"]
                + [f"blocking_rule:{rule}"
                   for rule in verdict.payload.get("blocking_rules", [])],
                extra={"verdict_artefact_id": verdict.artefact_id},
            )
        if verdict.payload.get("draft_content_hash") != (
                draft_content_hash(draft)):
            self._fail(
                "approve", draft_id, approver,
                ["verdict_draft_hash_mismatch"],
                extra={"verdict_artefact_id": verdict.artefact_id},
            )
        verify_problems = self._guardian.verify(verdict)
        if verify_problems:
            self._fail(
                "approve", draft_id, approver,
                ["guardian_verdict_does_not_verify"]
                + verify_problems,
                extra={"verdict_artefact_id": verdict.artefact_id},
            )
        problems = self._guardian._source_problems(draft)
        source_problems = (
            [f"source_superseded:{sid}" for sid in problems["superseded"]]
            + [f"source_refused:{sid}" for sid in problems["refused"]]
        )
        if source_problems:
            self._fail(
                "approve", draft_id, approver, source_problems,
                extra={"verdict_artefact_id": verdict.artefact_id},
            )
        payload = {
            "action": "approve",
            "draft_artefact_id": draft.artefact_id,
            "draft_kind": draft.payload.get("draft_kind"),
            "draft_content_hash": draft_content_hash(draft),
            "verdict_artefact_id": verdict.artefact_id,
            "verdict_pack_version": verdict.payload.get("pack_version"),
            "approver": approver,
            "status": "approved",
            "display_status": APPROVED_DISPLAY_STATUS,
            "execution_status": NOT_SENT,
            "bound_inputs": self._bound_inputs(draft, verdict),
        }
        fingerprint = hashlib.sha256(
            f"{canonical_json(payload)}".encode("utf-8")
        ).hexdigest()
        material = canonical_json({"payload": payload, "fp": fingerprint})
        artefact_id = (
            "APR-" + hashlib.sha256(material.encode()).hexdigest()[:20]
        )
        existing = self._store.get_artefact(artefact_id)
        if existing is not None:
            return existing
        act = Artefact(
            artefact_id=artefact_id, kind=APPROVAL_KIND,
            payload=payload, input_fingerprint=fingerprint,
        )
        self._store.insert_artefact(act)
        return act

    # -- rejection ------------------------------------------------------------

    def reject(self, draft_id: str, rejecter: str, reason_code: str,
               note: str = "") -> Artefact:
        """Record a named rejection for one awaiting draft, or
        refuse the attempt with the reason recorded."""
        if not rejecter or not rejecter.strip():
            self._fail("reject", draft_id, rejecter or "",
                       ["rejecter_missing"])
        if reason_code not in REJECTION_REASONS:
            self._fail(
                "reject", draft_id, rejecter,
                [f"rejection_reason_not_in_vocabulary:{reason_code}"])
        draft = self._store.get_artefact(draft_id)
        if draft is None or draft.kind not in DRAFT_KINDS:
            self._fail("reject", draft_id, rejecter,
                       ["draft_missing_or_not_a_draft"])
        if self.approval_for(draft_id) is not None:
            self._fail("reject", draft_id, rejecter, ["already_approved"])
        if self.rejection_for(draft_id) is not None:
            self._fail("reject", draft_id, rejecter, ["already_rejected"])
        awaiting_ids = {
            e["draft_artefact_id"]
            for e in self._guardian.awaiting_approval()
        }
        if draft_id not in awaiting_ids:
            self._fail("reject", draft_id, rejecter, ["not_awaiting_approval"])
        verdict = self._guardian.latest_verdict(draft_id)
        payload = {
            "action": "reject",
            "draft_artefact_id": draft.artefact_id,
            "draft_kind": draft.payload.get("draft_kind"),
            "draft_content_hash": draft_content_hash(draft),
            "verdict_artefact_id": (
                verdict.artefact_id if verdict is not None else None),
            "rejecter": rejecter,
            "reason_code": reason_code,
            "note": note,
            "status": "rejected",
            "display_status": "rejected",
            "execution_status": NOT_SENT,
        }
        fingerprint = hashlib.sha256(
            f"{canonical_json(payload)}".encode("utf-8")
        ).hexdigest()
        material = canonical_json({"payload": payload, "fp": fingerprint})
        artefact_id = (
            "ARJ-" + hashlib.sha256(material.encode()).hexdigest()[:20]
        )
        existing = self._store.get_artefact(artefact_id)
        if existing is not None:
            return existing
        act = Artefact(
            artefact_id=artefact_id, kind=REJECTION_KIND,
            payload=payload, input_fingerprint=fingerprint,
        )
        self._store.insert_artefact(act)
        return act

    # -- the derived queue ------------------------------------------------------

    def _approved_entry(self, act: Artefact) -> dict:
        moved = self.staleness(act)
        return {
            "approval_artefact_id": act.artefact_id,
            "draft_artefact_id": act.payload["draft_artefact_id"],
            "draft_kind": act.payload.get("draft_kind"),
            "draft_content_hash": act.payload.get("draft_content_hash"),
            "verdict_artefact_id": act.payload.get("verdict_artefact_id"),
            "approver": act.payload.get("approver"),
            "status": "approved",
            "display_status": APPROVED_DISPLAY_STATUS,
            "execution_status": NOT_SENT,
            "stale": bool(moved),
            "moved_inputs": moved,
        }

    def _rejected_entry(self, act: Artefact) -> dict:
        return {
            "rejection_artefact_id": act.artefact_id,
            "draft_artefact_id": act.payload["draft_artefact_id"],
            "draft_kind": act.payload.get("draft_kind"),
            "draft_content_hash": act.payload.get("draft_content_hash"),
            "verdict_artefact_id": act.payload.get("verdict_artefact_id"),
            "rejecter": act.payload.get("rejecter"),
            "reason_code": act.payload.get("reason_code"),
            "note": act.payload.get("note", ""),
            "status": "rejected",
            "display_status": "rejected",
            "execution_status": NOT_SENT,
        }

    def approved(self) -> list[dict]:
        """Approved drafts, folded from approval acts that
        re-verify. Each entry is marked stale, with the moved
        inputs named, when anything it binds has moved."""
        entries = []
        for act in self._store.list_artefacts(APPROVAL_KIND):
            if self.verify(act):
                continue
            entries.append(self._approved_entry(act))
        return entries

    def rejected(self) -> list[dict]:
        entries = []
        for act in self._store.list_artefacts(REJECTION_KIND):
            if self.verify(act):
                continue
            entries.append(self._rejected_entry(act))
        return entries

    def awaiting(self) -> list[dict]:
        """Still awaiting a human act: the guardian's gate minus
        drafts that already carry an approval or a rejection."""
        decided = {
            a.payload.get("draft_artefact_id")
            for kind in ACT_KINDS
            for a in self._store.list_artefacts(kind)
        }
        return [
            entry for entry in self._guardian.awaiting_approval()
            if entry["draft_artefact_id"] not in decided
        ]

    def queue(self) -> dict:
        return {
            "awaiting_approval": self.awaiting(),
            "approved": self.approved(),
            "rejected": self.rejected(),
        }

    def history(self, draft_artefact_id: str) -> list[dict]:
        """Every recorded act and refused attempt for one draft,
        in record order — the draft's approval history as
        evidence."""
        entries = []
        for artefact in self._store.list_artefacts():
            if artefact.kind not in (
                    APPROVAL_KIND, REJECTION_KIND,
                    APPROVAL_REFUSAL_KIND):
                continue
            if artefact.payload.get(
                    "draft_artefact_id") != draft_artefact_id:
                continue
            entry = {
                "artefact_id": artefact.artefact_id,
                "kind": artefact.kind,
                "action": artefact.payload.get("action"),
                "payload": artefact.payload,
            }
            if artefact.kind == APPROVAL_KIND:
                entry["stale"] = bool(self.staleness(artefact))
                entry["moved_inputs"] = self.staleness(artefact)
            entries.append(entry)
        return entries

    # -- verification ---------------------------------------------------------

    def verify(self, act: Artefact) -> list[str]:
        """Re-perform an approval or rejection act: its draft
        and hash bindings, the verdict it relies on, its actor,
        and its honesty of state. Staleness is NOT a verify
        problem — a stale act is a true record; it is the read
        surface that must never show it clean."""
        if act.kind not in ACT_KINDS:
            return [f"not_an_approval_act:{act.kind}"]
        payload = act.payload
        problems: list[str] = []
        draft = self._store.get_artefact(
            payload.get("draft_artefact_id", ""))
        if draft is None or draft.kind not in DRAFT_KINDS:
            problems.append("approval_draft_missing")
            return problems
        if payload.get("draft_content_hash") != draft_content_hash(draft):
            problems.append("approval_draft_hash_mismatch")
        if payload.get("execution_status") != NOT_SENT:
            problems.append("approval_execution_status_not_not_sent")
        if act.kind == APPROVAL_KIND:
            if not payload.get("approver"):
                problems.append("approval_approver_missing")
            if payload.get("status") != "approved":
                problems.append("approval_status_not_approved")
            if payload.get("display_status") != APPROVED_DISPLAY_STATUS:
                problems.append("approval_display_status_wrong")
            verdict = self._store.get_artefact(
                payload.get("verdict_artefact_id", ""))
            if verdict is None or verdict.kind != "guardian_verdict":
                problems.append("approval_verdict_missing")
            else:
                if verdict.payload.get(
                        "draft_artefact_id") != draft.artefact_id:
                    problems.append("approval_verdict_draft_mismatch")
                if verdict.payload.get(
                        "draft_content_hash") != payload.get(
                        "draft_content_hash"):
                    problems.append("approval_verdict_hash_mismatch")
                if verdict.payload.get("outcome") != "pass":
                    problems.append("approval_verdict_not_passing")
                if payload.get("verdict_pack_version") != (
                        verdict.payload.get("pack_version")):
                    problems.append("approval_verdict_pack_mismatch")
            bound = payload.get("bound_inputs", {})
            if bound.get("verdict_artefact_id") != payload.get(
                    "verdict_artefact_id"):
                problems.append("approval_bound_verdict_mismatch")
            if bound.get("draft_content_hash") != payload.get(
                    "draft_content_hash"):
                problems.append("approval_bound_hash_mismatch")
        else:
            if not payload.get("rejecter"):
                problems.append("rejection_rejecter_missing")
            if payload.get("status") != "rejected":
                problems.append("rejection_status_not_rejected")
            if payload.get("reason_code") not in REJECTION_REASONS:
                problems.append("rejection_reason_not_in_vocabulary")
        return problems
