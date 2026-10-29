"""The growth plan artefact: the computed record, composed.

Round 5 builds the governed plan the model will narrate in Round 6.
No prose is drafted here. The assembler composes ONE plan from the
current computed record — the head ledger and the five diagnosis
finding sets — and the plan is all citations and copied figures:

- The operating summary copies its figures from the head ledger
  artefact, each figure entry citing the ledger artefact id and the
  field it was copied from.
- The priorities rank the diagnosis findings by the rule stated in
  the plan pack below: family order first (stockout risk before ad
  decay before expense anomalies before refund anomalies before
  email-flow audit gaps), ties inside a family broken by exposure —
  the headline magnitude the finding itself carries, named per
  family in the pack — descending, then by the finding's identity
  key ascending. A family with no findings contributes an explicit
  no-findings entry naming the diagnosis pack version that judged
  it, never a silently absent section.
- The actions reference the finding artefacts and, for a missing or
  dormant email flow, the audit's draft proposal artefact — by id.
  The plan proposes actions; it performs none, and says so on its
  face.

Law 1 and Law 5 discipline: before a plan may exist, every figure
in it is re-resolved against the artefact it cites, and the
priorities and actions are re-derived from the cited finding sets
by a second route. A figure that does not match its source, a
citation to a superseded or refused artefact, or a refused
diagnosis family records a plan refusal artefact and stores no
plan. ``verify`` re-performs any stored plan at any time by the
same second route. Re-running the assembler over a moved record
supersedes the old plan by link; the old plan is never edited.

The plan pack is its own versioned pack. ``PLAN_PACKS`` keeps every
version verbatim, so a plan stamped with an earlier plan-pack
version still re-performs under its own version's rule.
"""

from __future__ import annotations

import hashlib
from decimal import Decimal

from .diagnosis import (
    AD_DECAY_KIND,
    EMAIL_AUDIT_KIND,
    EXPENSE_KIND,
    PROPOSAL_KIND,
    REFUND_KIND,
    STOCKOUT_KIND,
    DiagnosisRefused,
    DiagnosisService,
)
from .ledger import LedgerService
from .models import Artefact
from .store import SqlStore, canonical_json

PLAN_PACK = {
    "version": "2026.10.1",
    "family_order": [
        "stockout_risk", "ad_decay", "expense_anomaly",
        "refund_anomaly", "email_flow_audit",
    ],
    # The finding field that states a finding's exposure, per
    # family. The email-flow audit's findings are structural — a
    # missing flow has no magnitude — so the pack states None and
    # those findings order by their identity key alone.
    "exposure_fields": {
        "stockout_risk": "trailing_units",
        "ad_decay": "total_spend",
        "expense_anomaly": "amount",
        "refund_anomaly": "refunds",
        "email_flow_audit": None,
    },
    # The head-ledger fields the operating summary copies, in
    # display order. Each lands in the plan with the ledger
    # artefact id cited beside it.
    "summary_figures": [
        "gross_revenue", "refunds_total", "refund_rate",
        "net_revenue", "cogs", "gross_margin",
    ],
}

PLAN_PACKS = {pack["version"]: pack for pack in (PLAN_PACK,)}

FAMILY_KINDS = {
    "stockout_risk": STOCKOUT_KIND,
    "ad_decay": AD_DECAY_KIND,
    "expense_anomaly": EXPENSE_KIND,
    "refund_anomaly": REFUND_KIND,
    "email_flow_audit": EMAIL_AUDIT_KIND,
}

PLAN_KIND = "growth_plan"
PLAN_REFUSAL_KIND = "plan_refusal"


def _pack_for(version: str | None) -> dict | None:
    """The plan pack a plan was stamped with, or None when the
    version is not one this code knows."""
    return PLAN_PACKS.get(version or "")


class PlanRefused(Exception):
    """The plan failed its gate and was refused whole."""

    def __init__(self, refusal: Artefact):
        super().__init__(
            f"plan refused: {', '.join(refusal.payload['problems'])}"
        )
        self.refusal = refusal


# -- finding identity and exposure ---------------------------------------


def _finding_key(family: str, finding: dict) -> str:
    """The identity key a priority or action cites a finding by."""
    if family == "stockout_risk":
        return finding["sku"]
    if family == "ad_decay":
        return finding["ad_set"]
    if family == "expense_anomaly":
        if finding["class"] == "duplicate_charge":
            return "|".join(
                [finding["vendor"], finding["class"], *finding["fact_ids"]]
            )
        return "|".join(
            [finding["vendor"], finding["class"], finding["fact_id"]]
        )
    if family == "refund_anomaly":
        return finding["sku"]
    return f"{finding['flow_key']}|{finding['class']}"


def _exposure(family: str, finding: dict, pack: dict) -> tuple[str, str | None]:
    """(exposure as stated on the finding, the field it came from).

    The value is copied from the finding verbatim — it is a figure
    in the plan, and the verifier re-resolves it against the cited
    finding. A family whose pack entry is None has no exposure
    figure and reads as "0"."""
    field = pack["exposure_fields"][family]
    if field is None:
        return "0", None
    return str(finding[field]), field


def _finding_key_route2(family: str, finding: dict) -> str:
    """Route 2's identity key, built field by field."""
    if family in ("stockout_risk", "refund_anomaly"):
        return "%s" % finding["sku"]
    if family == "ad_decay":
        return "%s" % finding["ad_set"]
    if family == "expense_anomaly":
        parts = [finding["vendor"], finding["class"]]
        if "fact_ids" in finding:
            parts.extend(finding["fact_ids"])
        else:
            parts.append(finding["fact_id"])
        return "|".join(parts)
    return "%s|%s" % (finding["flow_key"], finding["class"])


def _exposure_route2(family: str, finding: dict) -> Decimal:
    """Route 2's exposure, re-read from the finding's own fields."""
    if family == "stockout_risk":
        return Decimal(finding["trailing_units"])
    if family == "ad_decay":
        return Decimal(finding["total_spend"])
    if family == "expense_anomaly":
        return Decimal(finding["amount"])
    if family == "refund_anomaly":
        return Decimal(finding["refunds"])
    return Decimal("0")


# -- derivation: priorities and actions from finding sets -----------------


def _ranked_entry(rank: int, family: str, diagnosis_id: str,
                  finding: dict, pack: dict) -> dict:
    exposure, field = _exposure(family, finding, pack)
    return {
        "rank": rank,
        "family": family,
        "diagnosis_artefact_id": diagnosis_id,
        "finding_key": _finding_key(family, finding),
        "exposure": exposure,
        "exposure_field": field,
    }


def _coverage_entry(family: str, finding_set: Artefact) -> dict:
    return {
        "rank": None,
        "family": family,
        "status": "no_findings",
        "diagnosis_artefact_id": finding_set.artefact_id,
        "judged_by_pack_version": finding_set.payload["pack_version"],
    }


def _action_entry(rank: int, family: str, diagnosis_id: str,
                  finding_key: str, proposal_id: str | None) -> dict:
    return {
        "rank": rank,
        "family": family,
        "diagnosis_artefact_id": diagnosis_id,
        "finding_key": finding_key,
        "proposal_artefact_id": proposal_id,
        "performed": False,
    }


def _proposal_id_for(proposals: dict[str, Artefact], family: str,
                     finding: dict) -> str | None:
    """The draft proposal a finding's action cites, if the finding
    earned one: only the audit's missing and dormant flows did."""
    if family != "email_flow_audit":
        return None
    if finding["class"] not in ("missing_flow", "dormant_flow"):
        return None
    proposal = proposals.get(finding["flow_key"])
    return proposal.artefact_id if proposal is not None else None


def _derive(sets: dict[str, Artefact], proposals: dict[str, Artefact],
            pack: dict) -> tuple[list[dict], list[dict]]:
    """Route 1: family by family in pack order, each family's
    findings sorted by exposure descending then key ascending,
    coverage entries collected behind the ranked priorities."""
    ranked: list[tuple[str, Artefact, dict]] = []
    coverage: list[dict] = []
    for family in pack["family_order"]:
        finding_set = sets[family]
        findings = finding_set.payload["findings"]
        if not findings:
            coverage.append(_coverage_entry(family, finding_set))
            continue
        ordered = sorted(
            findings,
            key=lambda f: (
                -Decimal(_exposure(family, f, pack)[0]),
                _finding_key(family, f),
            ),
        )
        for finding in ordered:
            ranked.append((family, finding_set, finding))
    priorities, actions = [], []
    for rank, (family, finding_set, finding) in enumerate(ranked, start=1):
        entry = _ranked_entry(rank, family, finding_set.artefact_id,
                              finding, pack)
        priorities.append(entry)
        actions.append(_action_entry(
            rank, family, finding_set.artefact_id, entry["finding_key"],
            _proposal_id_for(proposals, family, finding),
        ))
    priorities.extend(coverage)
    return priorities, actions


def _derive_route2(sets: dict[str, Artefact],
                   proposals: dict[str, Artefact],
                   pack: dict) -> tuple[list[dict], list[dict]]:
    """Route 2: every finding from every set flattened into one
    list, sorted once by (family position, exposure descending,
    key ascending) with the exposure and key re-read by route 2's
    own helpers. A different derivation of the same ranking rule —
    a disagreement is a ranking disagreement."""
    order = {family: index for index, family in enumerate(pack["family_order"])}
    flat: list[tuple[int, Decimal, str, str, Artefact, dict]] = []
    coverage: list[dict] = []
    for family, finding_set in sets.items():
        findings = finding_set.payload["findings"]
        if not findings:
            coverage.append(_coverage_entry(family, finding_set))
            continue
        for finding in findings:
            flat.append((
                order[family],
                -_exposure_route2(family, finding),
                _finding_key_route2(family, finding),
                family, finding_set, finding,
            ))
    flat.sort(key=lambda row: (row[0], row[1], row[2]))
    coverage.sort(key=lambda entry: order[entry["family"]])
    priorities, actions = [], []
    for rank, (_pos, _neg_exposure, key, family, finding_set, finding) in (
        enumerate(flat, start=1)
    ):
        entry = _ranked_entry(rank, family, finding_set.artefact_id,
                              finding, pack)
        entry["finding_key"] = key
        priorities.append(entry)
        actions.append(_action_entry(
            rank, family, finding_set.artefact_id, key,
            _proposal_id_for(proposals, family, finding),
        ))
    priorities.extend(coverage)
    return priorities, actions


# -- the service -----------------------------------------------------------


class PlanService:
    def __init__(self, store: SqlStore):
        self._store = store
        self._ledger = LedgerService(store)
        self._diagnosis = DiagnosisService(store)

    def _fingerprint(self, ledger: Artefact,
                     sets: dict[str, Artefact]) -> str:
        material = ":".join(
            [ledger.artefact_id, ledger.input_fingerprint]
            + [sets[family].artefact_id
               for family in PLAN_PACK["family_order"]]
            + [PLAN_PACK["version"]]
        )
        return hashlib.sha256(material.encode("utf-8")).hexdigest()

    def _refuse(self, ledger: Artefact, problems: list[str],
                blocking_refusals: list[str]) -> Artefact:
        payload = {
            "plan": "growth_plan",
            "pack_version": PLAN_PACK["version"],
            "ledger_artefact_id": ledger.artefact_id,
            "problems": problems,
            "blocking_refusals": blocking_refusals,
        }
        fingerprint = hashlib.sha256(
            f"{ledger.artefact_id}:{ledger.input_fingerprint}"
            f":{PLAN_PACK['version']}".encode("utf-8")
        ).hexdigest()
        material = canonical_json({"payload": payload, "fp": fingerprint})
        artefact_id = "PRF-" + hashlib.sha256(material.encode()).hexdigest()[:20]
        existing = self._store.get_artefact(artefact_id)
        if existing is not None:
            return existing
        refusal = Artefact(
            artefact_id=artefact_id, kind=PLAN_REFUSAL_KIND,
            payload=payload, input_fingerprint=fingerprint,
        )
        self._store.insert_artefact(refusal)
        return refusal

    def _proposals_for(self, audit: Artefact) -> dict[str, Artefact]:
        """The audit's draft proposals, by flow key."""
        return {
            proposal.payload["flow_key"]: proposal
            for proposal in self._store.list_artefacts(PROPOSAL_KIND)
            if proposal.payload["audit_artefact_id"] == audit.artefact_id
        }

    def assemble(self) -> Artefact:
        """Compose the growth plan from the current computed record.

        The diagnoses run first; a refused family blocks the plan
        with the refusal named. The assembled plan then passes the
        same gate ``verify`` applies, plus a currency check: every
        citation must name the current head of its kind, so a plan
        is never built over a superseded source."""
        ledger = self._ledger.compute()
        refused: list[tuple[str, Artefact]] = []
        sets: dict[str, Artefact] = {}
        runners = {
            "stockout_risk": self._diagnosis.diagnose_stockout,
            "ad_decay": self._diagnosis.diagnose_ad_decay,
            "expense_anomaly": self._diagnosis.diagnose_expenses,
            "refund_anomaly": self._diagnosis.diagnose_refunds,
            "email_flow_audit": self._diagnosis.audit_email_flows,
        }
        for family in PLAN_PACK["family_order"]:
            try:
                sets[family] = runners[family]()
            except DiagnosisRefused as refusal_error:
                refused.append((family, refusal_error.refusal))
        if refused:
            problems = [
                f"diagnosis_refused:{family}:{refusal.artefact_id}"
                for family, refusal in refused
            ]
            refusal = self._refuse(
                ledger, problems, [r.artefact_id for _f, r in refused]
            )
            raise PlanRefused(refusal)

        fingerprint = self._fingerprint(ledger, sets)
        for existing in self._store.list_artefacts(PLAN_KIND):
            if existing.input_fingerprint == fingerprint:
                return existing

        proposals = self._proposals_for(sets["email_flow_audit"])
        priorities, actions = _derive(sets, proposals, PLAN_PACK)
        latest_plan = self._store.latest_artefact(PLAN_KIND)
        payload = {
            "plan": "growth_plan",
            "pack_version": PLAN_PACK["version"],
            "diagnosis_pack_version": sets["stockout_risk"]
            .payload["pack_version"],
            "ledger_artefact_id": ledger.artefact_id,
            "as_of": ledger.payload["as_of"],
            "summary": {
                "ledger_artefact_id": ledger.artefact_id,
                "figures": [
                    {
                        "name": name,
                        "value": ledger.payload[name],
                        "source_artefact_id": ledger.artefact_id,
                        "source_field": name,
                    }
                    for name in PLAN_PACK["summary_figures"]
                ],
            },
            "priorities": priorities,
            "actions": actions,
            "performs_actions": False,
            "supersedes": (
                latest_plan.artefact_id if latest_plan is not None else None
            ),
        }
        candidate = Artefact(
            artefact_id="PLN-pending", kind=PLAN_KIND,
            payload=payload, input_fingerprint=fingerprint,
        )
        problems = self._check(candidate)
        if self._store.latest_artefact("ledger").artefact_id != (
            ledger.artefact_id
        ):
            problems.append("citation_superseded:ledger")
        for family in PLAN_PACK["family_order"]:
            head = self._store.latest_artefact(FAMILY_KINDS[family])
            if head is None or head.artefact_id != sets[family].artefact_id:
                problems.append(f"citation_superseded:{family}")
        if problems:
            raise PlanRefused(self._refuse(ledger, problems, []))
        material = canonical_json({"payload": payload, "fp": fingerprint})
        artefact_id = "PLN-" + hashlib.sha256(material.encode()).hexdigest()[:20]
        existing = self._store.get_artefact(artefact_id)
        if existing is not None:
            return existing
        plan = Artefact(
            artefact_id=artefact_id, kind=PLAN_KIND,
            payload=payload, input_fingerprint=fingerprint,
        )
        self._store.insert_artefact(plan)
        return plan

    # -- verification ------------------------------------------------------

    def _check(self, plan: Artefact) -> list[str]:
        """Re-resolve every citation in a plan and re-derive its
        priorities and actions by route 2 from the cited finding
        sets. Shared by the assembly gate and ``verify``."""
        payload = plan.payload
        pack = _pack_for(payload.get("pack_version"))
        if pack is None:
            return [f"unknown_pack_version:{payload.get('pack_version')}"]
        problems: list[str] = []
        ledger = self._store.get_artefact(payload["ledger_artefact_id"])
        if ledger is None:
            return ["ledger_missing"]
        if ledger.kind != "ledger":
            problems.append("citation_wrong_kind:ledger")
        summary = payload["summary"]
        if summary["ledger_artefact_id"] != ledger.artefact_id:
            problems.append("plan_citation_error:summary_ledger")
        for figure in summary["figures"]:
            if figure["source_artefact_id"] != ledger.artefact_id:
                problems.append(
                    f"plan_citation_error:summary:{figure['name']}")
            elif ledger.payload.get(figure["source_field"]) != figure["value"]:
                problems.append(f"plan_figure_mismatch:{figure['name']}")
        if payload["as_of"] != ledger.payload["as_of"]:
            problems.append("plan_figure_mismatch:as_of")

        cited: dict[str, str] = {}
        for entry in payload["priorities"]:
            cited[entry["family"]] = entry["diagnosis_artefact_id"]
        sets: dict[str, Artefact] = {}
        for family in pack["family_order"]:
            artefact_id = cited.get(family)
            if artefact_id is None:
                problems.append(f"citation_missing:{family}")
                continue
            finding_set = self._store.get_artefact(artefact_id)
            if finding_set is None:
                problems.append(f"citation_missing:{family}")
                continue
            if finding_set.kind != FAMILY_KINDS[family]:
                problems.append(f"citation_wrong_kind:{family}")
                continue
            if finding_set.payload.get("ledger_artefact_id") != (
                ledger.artefact_id
            ):
                problems.append(f"citation_not_bound_to_ledger:{family}")
            if finding_set.payload.get("diagnosis") != family:
                problems.append(f"citation_wrong_family:{family}")
            if finding_set.payload.get("pack_version") != (
                payload["diagnosis_pack_version"]
            ):
                problems.append(f"citation_pack_mismatch:{family}")
            problems.extend(
                f"{family}:{problem}"
                for problem in self._diagnosis.verify(finding_set)
            )
            sets[family] = finding_set

        proposals: dict[str, Artefact] = {}
        if "email_flow_audit" in sets:
            proposals = self._proposals_for(sets["email_flow_audit"])
        for action in payload["actions"]:
            proposal_id = action.get("proposal_artefact_id")
            if proposal_id is None:
                continue
            proposal = self._store.get_artefact(proposal_id)
            if proposal is None:
                problems.append("citation_missing:proposal")
                continue
            if proposal.kind != PROPOSAL_KIND:
                problems.append("citation_wrong_kind:proposal")
                continue
            if proposal.payload.get("audit_artefact_id") != cited.get(
                "email_flow_audit"
            ):
                problems.append("plan_citation_error:proposal_audit")
            problems.extend(
                f"proposal:{problem}"
                for problem in self._diagnosis.verify(proposal)
            )

        supersedes = payload.get("supersedes")
        if supersedes is not None:
            target = self._store.get_artefact(supersedes)
            if target is None or target.kind != PLAN_KIND:
                problems.append("plan_citation_error:supersedes")

        if len(sets) == len(pack["family_order"]):
            expected_priorities, expected_actions = _derive_route2(
                sets, proposals, pack
            )
            if canonical_json(expected_priorities) != canonical_json(
                payload["priorities"]
            ):
                problems.append("plan_priorities_mismatch")
            if canonical_json(expected_actions) != canonical_json(
                payload["actions"]
            ):
                problems.append("plan_actions_mismatch")
        return problems

    def verify(self, artefact: Artefact) -> list[str]:
        """Re-perform a stored plan at any time: every figure back
        against its cited source, every citation against the
        artefact it names, and the ranking re-derived by route 2.

        A refusal artefact is a record of a refusal, not a plan; it
        carries nothing to re-perform and verifies clean."""
        if artefact.kind == PLAN_REFUSAL_KIND:
            return []
        if artefact.kind != PLAN_KIND:
            return [f"not_a_plan:{artefact.kind}"]
        return self._check(artefact)
