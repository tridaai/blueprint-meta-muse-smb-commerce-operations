"""The morning board: the operator's opening view, re-derived.

Round 9 composes the board an operator opens the day with. It is
not a new judgement and it computes nothing of its own: it folds
the existing record — the head ledger, the five diagnosis finding
sets as the head growth plan ranks them, and the approval queue
folded from the guardian's gate and the named human acts — into
one view:

- a **headline** copying the operating figures from the head
  ledger artefact, each figure citing the ledger field it came
  from (Law 1: the board invents no number);
- **findings**, the plan's ranked priorities with every finding's
  figures copied from the finding artefact it cites, and the
  plan's explicit no-findings entries kept as coverage, never
  silently absent (Law 5);
- **stock risks**, the stockout findings called out on their own,
  with cover days, the cover-out date and the velocity that
  drains them;
- the **approval picture**: drafts awaiting approval (guardian-
  passed, not yet acted on), drafts blocked at the guardian with
  their blocking rules named, approved drafts reading
  ``approved — not sent`` with stale approvals marked STALE and
  their moved inputs named — exactly as Round 8's queue marks
  them — and rejected drafts with their stated reasons.

Re-derived, never stored
------------------------
The board is a derivation, not a record. ``derive`` folds the
current record on every call and returns the board as an
in-memory, content-addressed artefact; it is never inserted as
editable truth, and no surface can edit a draft onto it. The
board **pins the inputs it folded** — the ledger head, the plan
head, each diagnosis head, the latest guardian verdict per draft
and the latest approval and rejection acts — so movement after
the fold is detectable rather than silent.

``verify`` re-performs a board end to end by a second route: the
sections are recomposed from the derived queue folds and the
finding sets directly (route 1 composes them from the plan's
priorities and the acts themselves), every headline and finding
figure is re-resolved against the artefact it was copied from,
and the pins are compared against the current heads. A board
whose contents do not re-derive from the record — a figure
changed, an entry dropped, a stale approval shown clean — fails
verification with the section and figure named. A board folded
before the record moved fails the same way: like a verdict, a
board is a view, and a view that no longer re-performs is not
shown as true.

The graph gains no node for the board: the loop's nodes are the
pipeline steps that *produce* the record (ingest, compute,
diagnose, plan, draft, screen), and the board produces nothing —
it is composed at read time from what those steps left behind,
exactly as the approval queue is folded at read time in Round 8.
"""

from __future__ import annotations

import hashlib

from .approvals import (
    APPROVAL_KIND,
    REJECTION_KIND,
    APPROVED_DISPLAY_STATUS,
    NOT_SENT,
    ApprovalService,
)
from .diagnosis import PROPOSAL_KIND
from .drafting import DRAFT_KINDS
from .guardian import GuardianService
from .ledger import LedgerService
from .models import Artefact
from .plan import (
    FAMILY_KINDS,
    PLAN_KIND,
    PLAN_PACK,
    PlanService,
    _coverage_entry,
    _derive_route2,
    _finding_key,
)
from .store import SqlStore, canonical_json

BOARD_KIND = "morning_board"

# The finding fields the board copies onto each finding entry,
# per family — the figures that prove the finding, restated from
# the finding artefact itself. Fields a finding does not carry
# (an outlier has no dates, a duplicate has no median) are simply
# absent from its entry.
_FINDING_FIGURES = {
    "stockout_risk": (
        "sku", "name", "on_hand", "cover_days", "cover_out_date",
        "trailing_velocity_per_day", "velocity_window_days",
        "trailing_units",
    ),
    "ad_decay": (
        "ad_set", "first_week", "first_week_roas", "latest_week",
        "latest_week_roas", "decay_factor", "total_spend",
    ),
    "expense_anomaly": (
        "vendor", "class", "amount", "dates", "fact_ids",
        "gap_days", "window_days", "date", "fact_id",
        "vendor_median", "multiple",
    ),
    "refund_anomaly": (
        "sku", "name", "refund_rate", "max_refund_rate", "refunds",
        "gross_revenue", "units_sold",
    ),
    "email_flow_audit": (
        "flow_key", "name", "class", "trigger", "expected_steps",
        "status", "steps", "step_index", "delay_hours",
        "max_step_delay_hours",
    ),
}

_STOCK_FIGURES = (
    "sku", "name", "on_hand", "cover_days", "cover_out_date",
    "trailing_velocity_per_day",
)


def _draft_summary(draft: Artefact) -> dict:
    """What identifies a draft on the board, copied from the
    draft artefact itself: the step subjects for email copy, the
    opening line for prose drafts. Text, never figures."""
    content = draft.payload.get("content", {})
    if "steps" in content:
        return {
            "subjects": [step.get("subject", "")
                         for step in content["steps"]]
        }
    lines = [line for line in content.get("prose", "").splitlines()
             if line.strip()]
    return {"opening_line": lines[0] if lines else ""}


def _finding_entry(priority: dict, finding: dict) -> dict:
    """Shape one ranked priority into a board finding entry: the
    plan's citation (family, set, key, exposure) plus the
    finding's own figures, copied from the finding."""
    family = priority["family"]
    return {
        "rank": priority["rank"],
        "family": family,
        "diagnosis_artefact_id": priority["diagnosis_artefact_id"],
        "finding_key": priority["finding_key"],
        "exposure": priority["exposure"],
        "exposure_field": priority["exposure_field"],
        "figures": {
            field: finding[field]
            for field in _FINDING_FIGURES[family]
            if field in finding
        },
    }


class BoardService:
    def __init__(self, store: SqlStore, guardian: GuardianService,
                 approvals: ApprovalService):
        self._store = store
        self._guardian = guardian
        self._approvals = approvals
        self._ledger = LedgerService(store)
        self._plan = PlanService(store)

    # -- composition, route 1: from the plan and the acts ----------------

    def _sets_from_plan(self, plan: Artefact) -> dict[str, Artefact]:
        cited: dict[str, str] = {}
        for entry in plan.payload["priorities"]:
            cited[entry["family"]] = entry["diagnosis_artefact_id"]
        return {
            family: self._store.get_artefact(cited[family])
            for family in PLAN_PACK["family_order"]
        }

    def _findings_route1(self, plan: Artefact,
                         sets: dict[str, Artefact]) -> list[dict]:
        entries: list[dict] = []
        for priority in plan.payload["priorities"]:
            if priority["rank"] is None:
                entries.append(_coverage_entry(
                    priority["family"], sets[priority["family"]]))
                continue
            finding_set = sets[priority["family"]]
            finding = next(
                f for f in finding_set.payload["findings"]
                if _finding_key(priority["family"], f)
                == priority["finding_key"]
            )
            entries.append(_finding_entry(priority, finding))
        return entries

    def _drafts(self) -> list[Artefact]:
        return [
            draft
            for kind in DRAFT_KINDS
            for draft in self._store.list_artefacts(kind)
        ]

    def _classify_route1(self) -> dict[str, list[dict]]:
        """Fold the drafts one by one over their verdicts and
        acts: an act that re-verifies decides (approval before
        rejection), then a blocking latest verdict, then the
        guardian's gate. A draft in none of these is unscreened
        and appears in no section — the gate's own semantics."""
        awaiting_by_id = {
            entry["draft_artefact_id"]: entry
            for entry in self._approvals.awaiting()
        }
        sections: dict[str, list[dict]] = {
            "awaiting_approval": [], "approved": [],
            "rejected": [], "blocked": [],
        }
        classified: set[str] = set()
        for act in self._store.list_artefacts(APPROVAL_KIND):
            if self._approvals.verify(act):
                continue
            draft = self._store.get_artefact(
                act.payload["draft_artefact_id"])
            entry = self._approvals._approved_entry(act)
            entry["summary"] = _draft_summary(draft)
            sections["approved"].append(entry)
            classified.add(act.payload["draft_artefact_id"])
        for act in self._store.list_artefacts(REJECTION_KIND):
            if self._approvals.verify(act):
                continue
            draft = self._store.get_artefact(
                act.payload["draft_artefact_id"])
            entry = self._approvals._rejected_entry(act)
            entry["summary"] = _draft_summary(draft)
            sections["rejected"].append(entry)
            classified.add(act.payload["draft_artefact_id"])
        for draft in self._drafts():
            if draft.artefact_id in classified:
                continue
            verdict = self._guardian.latest_verdict(draft.artefact_id)
            if (verdict is not None
                    and verdict.payload.get("outcome") == "block"):
                sections["blocked"].append({
                    "draft_artefact_id": draft.artefact_id,
                    "draft_kind": draft.payload.get("draft_kind"),
                    "verdict_artefact_id": verdict.artefact_id,
                    "pack_version": verdict.payload["pack_version"],
                    "blocking_rules":
                        verdict.payload["blocking_rules"],
                    "summary": _draft_summary(draft),
                })
                classified.add(draft.artefact_id)
            elif draft.artefact_id in awaiting_by_id:
                entry = dict(awaiting_by_id[draft.artefact_id])
                entry["summary"] = _draft_summary(draft)
                sections["awaiting_approval"].append(entry)
                classified.add(draft.artefact_id)
        sections["_classified"] = classified
        return sections

    def _pins(self, ledger: Artefact, plan: Artefact,
              sets: dict[str, Artefact]) -> dict:
        verdict_heads: dict[str, str | None] = {}
        for draft in self._drafts():
            verdict = self._guardian.latest_verdict(draft.artefact_id)
            verdict_heads[draft.artefact_id] = (
                verdict.artefact_id if verdict is not None else None)
        approvals = self._store.list_artefacts(APPROVAL_KIND)
        rejections = self._store.list_artefacts(REJECTION_KIND)
        return {
            "ledger_head": ledger.artefact_id,
            "plan_head": plan.artefact_id,
            "diagnosis_heads": {
                family: sets[family].artefact_id
                for family in PLAN_PACK["family_order"]
            },
            "verdict_heads": verdict_heads,
            "approval_head": (
                approvals[-1].artefact_id if approvals else None),
            "rejection_head": (
                rejections[-1].artefact_id if rejections else None),
        }

    def _headline(self, ledger: Artefact, counts: dict) -> dict:
        return {
            "ledger_artefact_id": ledger.artefact_id,
            "as_of": ledger.payload["as_of"],
            "figures": [
                {
                    "name": name,
                    "value": ledger.payload[name],
                    "source_artefact_id": ledger.artefact_id,
                    "source_field": name,
                }
                for name in PLAN_PACK["summary_figures"]
            ],
            "counts": counts,
        }

    @staticmethod
    def _stock_risks(findings: list[dict]) -> list[dict]:
        risks = []
        for entry in findings:
            if entry.get("family") != "stockout_risk":
                continue
            if entry.get("rank") is None:
                continue
            figures = entry["figures"]
            risks.append({
                "finding_key": entry["finding_key"],
                "diagnosis_artefact_id":
                    entry["diagnosis_artefact_id"],
                **{field: figures[field]
                   for field in _STOCK_FIGURES if field in figures},
            })
        return risks

    @staticmethod
    def _counts(findings: list[dict], sections: dict,
                drafts_total: int, unscreened: int) -> dict:
        return {
            "findings": sum(
                1 for e in findings if e.get("rank") is not None),
            "coverage_families": sum(
                1 for e in findings if e.get("rank") is None),
            "stock_risks": sum(
                1 for e in findings
                if e.get("family") == "stockout_risk"
                and e.get("rank") is not None),
            "awaiting_approval": len(sections["awaiting_approval"]),
            "approved": len(sections["approved"]),
            "approved_stale": sum(
                1 for e in sections["approved"] if e["stale"]),
            "rejected": len(sections["rejected"]),
            "blocked": len(sections["blocked"]),
            "drafts_total": drafts_total,
            "unscreened": unscreened,
        }

    def _fingerprint(self, pins: dict) -> str:
        return hashlib.sha256(
            canonical_json(pins).encode("utf-8")).hexdigest()

    # -- derivation ---------------------------------------------------------

    def derive(self) -> Artefact:
        """Fold the current record into the morning board. The
        pipeline record is brought current first (the ledger
        computed, the plan assembled — the same steps the loop's
        nodes perform), then the board is composed from what
        those steps left behind. The board artefact is returned
        in memory; it is never inserted, so a read can never
        become the record."""
        ledger = self._ledger.compute()
        plan = self._plan.assemble()
        sets = self._sets_from_plan(plan)
        findings = self._findings_route1(plan, sets)
        sections = self._classify_route1()
        classified = sections.pop("_classified")
        drafts_total = len(self._drafts())
        counts = self._counts(
            findings, sections, drafts_total,
            drafts_total - len(classified))
        pins = self._pins(ledger, plan, sets)
        payload = {
            "board": "morning_board",
            "ledger_artefact_id": ledger.artefact_id,
            "plan_artefact_id": plan.artefact_id,
            "as_of": ledger.payload["as_of"],
            "pins": pins,
            "headline": self._headline(ledger, counts),
            "findings": findings,
            "stock_risks": self._stock_risks(findings),
            "awaiting_approval": sections["awaiting_approval"],
            "approved": sections["approved"],
            "rejected": sections["rejected"],
            "blocked": sections["blocked"],
        }
        fingerprint = self._fingerprint(pins)
        material = canonical_json({"payload": payload, "fp": fingerprint})
        artefact_id = (
            "BRD-" + hashlib.sha256(material.encode()).hexdigest()[:20]
        )
        return Artefact(
            artefact_id=artefact_id, kind=BOARD_KIND,
            payload=payload, input_fingerprint=fingerprint,
        )

    def board(self) -> dict:
        """The board as a plain read: derived fresh on every
        call, so what a surface shows is the record as it stands
        now, never a stored snapshot."""
        return self.derive().payload

    # -- verification: route 2 ----------------------------------------------

    def _compose_route2(self) -> dict | None:
        """Recompose the board from the derived folds directly:
        the latest ledger and plan heads, the finding sets read
        per kind and re-ranked by the plan pack's rule via route
        2, the queue as the approval service folds it, and the
        guardian's blocked list with decided drafts removed.
        Reads only — verification never writes."""
        ledger = self._store.latest_artefact("ledger")
        plan = self._store.latest_artefact(PLAN_KIND)
        if ledger is None or plan is None:
            return None
        sets = {
            family: self._store.latest_artefact(FAMILY_KINDS[family])
            for family in PLAN_PACK["family_order"]
        }
        if any(s is None for s in sets.values()):
            return None
        audit = sets["email_flow_audit"]
        proposals = {
            p.payload["flow_key"]: p
            for p in self._store.list_artefacts(PROPOSAL_KIND)
            if p.payload.get("audit_artefact_id") == audit.artefact_id
        }
        priorities, _actions = _derive_route2(sets, proposals, PLAN_PACK)
        findings: list[dict] = []
        for priority in priorities:
            if priority["rank"] is None:
                findings.append(_coverage_entry(
                    priority["family"], sets[priority["family"]]))
                continue
            finding_set = sets[priority["family"]]
            finding = next(
                f for f in finding_set.payload["findings"]
                if _finding_key(priority["family"], f)
                == priority["finding_key"]
            )
            findings.append(_finding_entry(priority, finding))
        queue = self._approvals.queue()
        decided = {
            e["draft_artefact_id"]
            for e in queue["approved"] + queue["rejected"]
        }
        sections: dict[str, list[dict]] = {
            "awaiting_approval": [], "approved": [],
            "rejected": [], "blocked": [],
        }
        for name in ("awaiting_approval", "approved", "rejected"):
            for entry in queue[name]:
                entry = dict(entry)
                draft = self._store.get_artefact(
                    entry["draft_artefact_id"])
                entry["summary"] = _draft_summary(draft)
                sections[name].append(entry)
        for entry in self._guardian.blocked_drafts():
            if entry["draft_artefact_id"] in decided:
                continue
            entry = dict(entry)
            draft = self._store.get_artefact(entry["draft_artefact_id"])
            entry["summary"] = _draft_summary(draft)
            sections["blocked"].append(entry)
        classified = {
            e["draft_artefact_id"]
            for name in sections for e in sections[name]
        }
        drafts_total = len(self._drafts())
        counts = self._counts(
            findings, sections, drafts_total,
            drafts_total - len(classified))
        pins = self._pins(ledger, plan, sets)
        return {
            "board": "morning_board",
            "ledger_artefact_id": ledger.artefact_id,
            "plan_artefact_id": plan.artefact_id,
            "as_of": ledger.payload["as_of"],
            "pins": pins,
            "headline": self._headline(ledger, counts),
            "findings": findings,
            "stock_risks": self._stock_risks(findings),
            "awaiting_approval": sections["awaiting_approval"],
            "approved": sections["approved"],
            "rejected": sections["rejected"],
            "blocked": sections["blocked"],
        }

    def verify(self, board: Artefact) -> list[str]:
        """Re-perform a board end to end: its headline and
        finding figures re-resolved against the artefacts they
        cite, its pins compared against the current heads, and
        every section recomposed by route 2 and compared. A
        board that does not re-derive from the current record —
        forged, tampered, or simply folded before the record
        moved — fails here with the problem named."""
        if board.kind != BOARD_KIND:
            return [f"not_a_board:{board.kind}"]
        payload = board.payload
        problems: list[str] = []
        ledger = self._store.get_artefact(
            payload.get("ledger_artefact_id", ""))
        if ledger is None or ledger.kind != "ledger":
            return ["board_ledger_missing"]
        for figure in payload.get("headline", {}).get("figures", []):
            if figure.get("source_artefact_id") != ledger.artefact_id:
                problems.append(
                    f"board_citation_error:headline:{figure.get('name')}")
            elif ledger.payload.get(
                    figure.get("source_field")) != figure.get("value"):
                problems.append(
                    f"board_figure_mismatch:headline:{figure.get('name')}")
        plan = self._store.get_artefact(
            payload.get("plan_artefact_id", ""))
        if plan is None or plan.kind != PLAN_KIND:
            problems.append("board_plan_missing")
        for entry in payload.get("findings", []):
            if entry.get("rank") is None:
                continue
            finding_set = self._store.get_artefact(
                entry.get("diagnosis_artefact_id", ""))
            if finding_set is None:
                problems.append(
                    f"board_citation_missing:{entry.get('finding_key')}")
                continue
            match = next(
                (f for f in finding_set.payload.get("findings", [])
                 if _finding_key(entry["family"], f)
                 == entry.get("finding_key")),
                None,
            )
            if match is None:
                problems.append(
                    f"board_finding_missing:{entry.get('finding_key')}")
                continue
            for field, value in entry.get("figures", {}).items():
                if match.get(field) != value:
                    problems.append(
                        f"board_figure_mismatch:"
                        f"{entry.get('finding_key')}:{field}")
        for entry in payload.get("approved", []):
            if entry.get("display_status") != APPROVED_DISPLAY_STATUS:
                problems.append("board_approved_display_status_wrong")
            if entry.get("execution_status") != NOT_SENT:
                problems.append("board_approved_execution_status_wrong")
        expected = self._compose_route2()
        if expected is None:
            problems.append("board_record_missing")
            return problems
        pins = payload.get("pins", {})
        for name, value in expected["pins"].items():
            if pins.get(name) != value:
                problems.append(f"board_pin_mismatch:{name}")
        if payload.get("ledger_artefact_id") != (
                expected["ledger_artefact_id"]):
            problems.append("board_ledger_mismatch")
        if payload.get("plan_artefact_id") != (
                expected["plan_artefact_id"]):
            problems.append("board_plan_mismatch")
        if payload.get("as_of") != expected["as_of"]:
            problems.append("board_as_of_mismatch")
        if canonical_json(payload.get("headline")) != canonical_json(
                expected["headline"]):
            problems.append("board_headline_mismatch")
        for section in ("findings", "stock_risks", "awaiting_approval",
                        "approved", "rejected", "blocked"):
            if canonical_json(payload.get(section)) != canonical_json(
                    expected[section]):
                problems.append(f"board_section_mismatch:{section}")
        return problems
