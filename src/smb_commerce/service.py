"""The service: one wiring point for store, ingest, ledger, model.

The API, CLI, demo and graph all talk to this class, so the surfaces
can never drift apart — a figure read over HTTP is the figure the CLI
prints, because both come from the same code path.

Round 13 makes this class the access boundary too. Every public
method takes a ``caller`` (see ``access.py``): reads answer with
the absent form — ``None``, the empty list, the empty summary —
when the caller's role may not see the resource class, so a
forbidden resource is indistinguishable from a missing one, and
actions raise :class:`AccessRefused` (or, for approval acts, a
recorded approval refusal) before any store lookup of their
target. In-process flows — the demo, the eval gate, the graph,
the parity walk — pass no caller and act as the stated internal
identity; surfaces always resolve and pass one explicitly.

Round 14 adds subscriptions and digests through the same
boundary: the writes refuse loudly, and a digest read is decided
by the digest's content — the caller must hold every permission
the digest's included sections required (see ``digests.py``).

Round 16 adds product images and the listing-quality diagnosis
through the same boundary: image intake is a write, the listing
diagnosis a run, and both artefact kinds (the diagnosis and its
vision judgements) read as diagnoses under the pack.
"""

from __future__ import annotations

import logging

from . import generator
from .access import (
    INTERNAL_CALLER,
    AccessRefused,
    Caller,
    read_permission_for_kind,
    require,
    visible,
)
from .approvals import ApprovalRefused, ApprovalService
from .board import BoardService
from .config import Settings, load_settings
from .connectors import ConnectorPort
from .costs import CostAnalytics, empty_summary
from .diagnosis import DiagnosisService
from .digests import DIGEST_KIND, DigestService, digest_visible
from .drafting import DraftingService
from .guardian import GuardianService
from .images import ProductImageIntake
from .ingest import IngestPort, IngestReport
from .ledger import LedgerService
from .listing import ListingQualityService
from .modelport import GovernedModelPort, build_model_port, profile_report
from .objectstore import ObjectStore, build_object_store
from .plan import PlanService
from .store import SqlStore

log = logging.getLogger("smb_commerce.service")

#: The approval queue's empty form — what a caller who may not
#: read approvals sees, identical to a queue over an empty record.
EMPTY_QUEUE = {"awaiting_approval": [], "approved": [], "rejected": []}


class CommerceService:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or load_settings()
        if self.settings.use_postgres:
            self.store = SqlStore.postgres(
                self.settings.database_url,
                self.settings.migrations_dir,
                schema=self.settings.postgres_schema or None,
            )
        else:
            self.store = SqlStore.sqlite(
                self.settings.sqlite_path, self.settings.migrations_dir
            )
        self.ingest = IngestPort(self.store)
        self.connectors = ConnectorPort(self.store, self.ingest)
        self.ledger = LedgerService(self.store)
        self.diagnosis = DiagnosisService(self.store)
        self.plan = PlanService(self.store)
        self.model: GovernedModelPort = build_model_port(self.settings, self.store)
        self.drafting = DraftingService(self.store, self.model)
        self.guardian = GuardianService(self.store, self.model)
        self.approvals = ApprovalService(self.store, self.guardian)
        self.board = BoardService(self.store, self.guardian, self.approvals)
        self.digests = DigestService(self.store, self.board)
        self.objects: ObjectStore = build_object_store(self.settings)
        self.images = ProductImageIntake(self.store, self.ingest, self.objects)
        self.listing = ListingQualityService(
            self.store, self.model, self.objects)

    # -- callers -----------------------------------------------------------

    @staticmethod
    def _caller(caller: Caller | None) -> Caller:
        """No caller means an in-process flow: the stated
        internal identity, never a surface default."""
        return INTERNAL_CALLER if caller is None else caller

    # -- gated reads (Round 13) ---------------------------------------------
    # A read the caller's role may not perform answers with the
    # absent form for that resource class: None for one resource,
    # the empty list for a list, the empty summary for a summary.
    # Nothing about a real-but-forbidden resource leaks — not its
    # existence, not a count.

    def read_facts(self, kind: str | None = None,
                   caller: Caller | None = None):
        if not visible(self._caller(caller), "facts:read"):
            return []
        return self.store.list_facts(kind)

    def read_fact(self, fact_id: str, caller: Caller | None = None):
        if not visible(self._caller(caller), "facts:read"):
            return None
        return self.store.get_fact(fact_id)

    def read_artefact(self, artefact_id: str,
                      caller: Caller | None = None):
        artefact = self.store.get_artefact(artefact_id)
        if artefact is None:
            return None
        caller = self._caller(caller)
        if artefact.kind == DIGEST_KIND:
            # An addressed digest: content, not the kind gate,
            # decides (see digests.py).
            return artefact if digest_visible(artefact, caller) else None
        permission = read_permission_for_kind(artefact.kind)
        if not visible(caller, permission):
            return None
        return artefact

    def read_artefacts(self, kind: str, caller: Caller | None = None):
        permission = read_permission_for_kind(kind)
        caller = self._caller(caller)
        if not visible(caller, permission):
            return []
        artefacts = self.store.list_artefacts(kind)
        if kind == DIGEST_KIND:
            artefacts = [
                a for a in artefacts if digest_visible(a, caller)]
        return artefacts

    def read_latest_artefact(self, kind: str,
                             caller: Caller | None = None):
        permission = read_permission_for_kind(kind)
        caller = self._caller(caller)
        if not visible(caller, permission):
            return None
        if kind == DIGEST_KIND:
            visible_digests = [
                a for a in self.store.list_artefacts(kind)
                if digest_visible(a, caller)]
            return visible_digests[-1] if visible_digests else None
        return self.store.latest_artefact(kind)

    def read_ledger(self, caller: Caller | None = None):
        """The latest ledger for a surface read. The CLI's
        historical behaviour — compute on a record that has none
        — is kept for callers who may also run diagnoses; a
        read-only role gets the absent form instead of a run."""
        caller = self._caller(caller)
        artefact = self.read_latest_artefact("ledger", caller)
        if artefact is None and caller.allows("diagnose:run"):
            artefact = self.ledger.compute()
        return artefact

    def read_quarantine(self, artefact_id: str,
                        caller: Caller | None = None):
        if not visible(self._caller(caller), "connectors:read"):
            return None
        return self.connectors.quarantine(artefact_id)

    def read_model_calls(self, run_id: str | None = None,
                         caller: Caller | None = None):
        if not visible(self._caller(caller), "costs:read"):
            return []
        return self.store.list_model_calls(run_id)

    def connector_registry(self, caller: Caller | None = None):
        """The exchange-contract registry: stated configuration,
        gated as a connector read — a caller who may not read
        connectors sees an empty registry."""
        from .connectors import CONTRACTS

        if not visible(self._caller(caller), "connectors:read"):
            return []
        return list(CONTRACTS.values())

    # -- dataset ---------------------------------------------------------
    def generate_dataset(self, caller: Caller | None = None
                         ) -> IngestReport | None:
        """Load the Hartwell dataset once. Returns None when the store
        already holds facts (the store is insert-only; a second load is
        a no-op by design, reported as duplicates by the port)."""
        require(self._caller(caller), "ingest:write")
        if self.store.count_facts() > 0:
            report = self.ingest.ingest_many(generator.build_fact_items())
            log.info("dataset already present: %d duplicates, %d refused",
                     report.duplicates, report.refused)
            return report
        report = generator.generate_batches(self.connectors)
        log.info("dataset generated: %d facts accepted", report.accepted)
        return report

    # -- connector exchange (Round 11) --------------------------------
    def exchange_batch(self, envelope, caller: Caller | None = None):
        require(self._caller(caller), "ingest:write")
        return self.connectors.exchange(envelope)

    def quarantines(self, caller: Caller | None = None):
        if not visible(self._caller(caller), "connectors:read"):
            return []
        return self.connectors.quarantines()

    def verify_quarantine(self, artefact):
        return self.connectors.verify(artefact)

    def dataset_counts(self, caller: Caller | None = None) -> dict[str, int]:
        if not visible(self._caller(caller), "facts:read"):
            return {}
        counts: dict[str, int] = {}
        for fact in self.store.list_facts():
            counts[fact.kind] = counts.get(fact.kind, 0) + 1
        return counts

    # -- diagnosis -----------------------------------------------------------
    def diagnose_stockout(self, caller: Caller | None = None):
        require(self._caller(caller), "diagnose:run")
        return self.diagnosis.diagnose_stockout()

    def diagnose_ad_decay(self, caller: Caller | None = None):
        require(self._caller(caller), "diagnose:run")
        return self.diagnosis.diagnose_ad_decay()

    def diagnose_expenses(self, caller: Caller | None = None):
        require(self._caller(caller), "diagnose:run")
        return self.diagnosis.diagnose_expenses()

    def diagnose_refunds(self, caller: Caller | None = None):
        require(self._caller(caller), "diagnose:run")
        return self.diagnosis.diagnose_refunds()

    def audit_email_flows(self, caller: Caller | None = None):
        require(self._caller(caller), "diagnose:run")
        return self.diagnosis.audit_email_flows()

    # -- product images and listing quality (Round 16) -------------------
    def generate_product_images(self, caller: Caller | None = None
                                ) -> IngestReport:
        """Load the planted listing images through the image
        intake port. Idempotent like the dataset load: a second
        load reports duplicates at the port and stores nothing
        twice."""
        require(self._caller(caller), "ingest:write")
        report = generator.generate_images(self.images)
        log.info("product images generated: %d accepted, %d duplicates",
                 report.accepted, report.duplicates)
        return report

    def ingest_product_image(self, *, sku: str, filename: str,
                             data: bytes, declared_class: str,
                             source_system: str | None,
                             as_of: str | None,
                             caller: Caller | None = None):
        require(self._caller(caller), "ingest:write")
        return self.images.ingest_image(
            sku=sku, filename=filename, data=data,
            declared_class=declared_class,
            source_system=source_system, as_of=as_of)

    def diagnose_listing_quality(self, caller: Caller | None = None):
        require(self._caller(caller), "diagnose:run")
        return self.listing.diagnose()

    # -- plan --------------------------------------------------------------
    def assemble_plan(self, caller: Caller | None = None):
        require(self._caller(caller), "plan:run")
        return self.plan.assemble()

    # -- model -------------------------------------------------------------
    def draft(self, run_id: str, system: str, prompt: str):
        return self.model.complete(run_id, system, prompt)

    # -- profile and costs (Round 12) -------------------------------------
    def model_profile_report(self, caller: Caller | None = None):
        require(self._caller(caller), "config:read")
        return profile_report(self.settings)

    def cost_summary(self, caller: Caller | None = None):
        if not visible(self._caller(caller), "costs:read"):
            return empty_summary()
        return CostAnalytics(self.store).summary()

    def cost_reconcile(self, caller: Caller | None = None):
        require(self._caller(caller), "costs:read")
        return CostAnalytics(self.store).reconcile_all()

    def context_records(self, caller: Caller | None = None):
        from .context import CONTEXT_KIND

        if not visible(self._caller(caller), "costs:read"):
            return []
        return self.store.list_artefacts(CONTEXT_KIND)

    # -- drafting ------------------------------------------------------------
    def draft_plan_narrative(self, caller: Caller | None = None):
        require(self._caller(caller), "draft:run")
        return self.drafting.draft_plan_narrative()

    def draft_email_copy(self, proposal, caller: Caller | None = None):
        require(self._caller(caller), "draft:run")
        return self.drafting.draft_email_copy(proposal)

    def draft_ad_note(self, caller: Caller | None = None):
        require(self._caller(caller), "draft:run")
        return self.drafting.draft_ad_note()

    def draft_all(self, caller: Caller | None = None):
        require(self._caller(caller), "draft:run")
        return self.drafting.draft_all()

    # -- guardian screen -----------------------------------------------------
    def screen_draft(self, draft, caller: Caller | None = None):
        require(self._caller(caller), "screen:run")
        return self.guardian.screen(draft)

    def screen_all(self, caller: Caller | None = None):
        require(self._caller(caller), "screen:run")
        return self.guardian.screen_all()

    def awaiting_approval(self, caller: Caller | None = None):
        if not visible(self._caller(caller), "guardian:read"):
            return []
        return self.guardian.awaiting_approval()

    def blocked_drafts(self, caller: Caller | None = None):
        if not visible(self._caller(caller), "guardian:read"):
            return []
        return self.guardian.blocked_drafts()

    # -- approval queue ------------------------------------------------------
    def approve_draft(self, draft_id, approver,
                      caller: Caller | None = None):
        caller = self._caller(caller)
        if not caller.allows("approvals:act"):
            # The gate fires before any draft lookup, so the
            # refusal is identical for real and absent drafts.
            # The refusal names the presenter when one is
            # identified, else the name the attempt claimed.
            actor = caller.actor if caller.identified else approver
            refusal = self.approvals.record_refusal(
                "approve", draft_id, actor,
                [f"actor_role_may_not_approve:{caller.role or 'none'}"],
            )
            raise ApprovalRefused(refusal)
        if (caller.identified and caller != INTERNAL_CALLER
                and approver != caller.actor):
            # The act must name the human who acted (Round 15):
            # a presented caller may not record an approval in
            # someone else's name. In-process flows act as the
            # internal identity, which names the human in the
            # act itself — the binding applies to presented
            # (surface) callers, whose identity the act restates.
            refusal = self.approvals.record_refusal(
                "approve", draft_id, caller.actor,
                ["approver_is_not_the_calling_actor"],
            )
            raise ApprovalRefused(refusal)
        return self.approvals.approve(draft_id, approver)

    def reject_draft(self, draft_id, rejecter, reason_code, note="",
                     caller: Caller | None = None):
        caller = self._caller(caller)
        if not caller.allows("approvals:act"):
            actor = caller.actor if caller.identified else rejecter
            refusal = self.approvals.record_refusal(
                "reject", draft_id, actor,
                [f"actor_role_may_not_reject:{caller.role or 'none'}"],
            )
            raise ApprovalRefused(refusal)
        if (caller.identified and caller != INTERNAL_CALLER
                and rejecter != caller.actor):
            refusal = self.approvals.record_refusal(
                "reject", draft_id, caller.actor,
                ["rejecter_is_not_the_calling_actor"],
            )
            raise ApprovalRefused(refusal)
        return self.approvals.reject(draft_id, rejecter, reason_code, note)

    def approval_queue(self, caller: Caller | None = None):
        if not visible(self._caller(caller), "approvals:read"):
            return dict(EMPTY_QUEUE)
        return self.approvals.queue()

    def approvals_awaiting(self, caller: Caller | None = None):
        if not visible(self._caller(caller), "approvals:read"):
            return []
        return self.approvals.awaiting()

    def approved_drafts(self, caller: Caller | None = None):
        if not visible(self._caller(caller), "approvals:read"):
            return []
        return self.approvals.approved()

    def rejected_drafts(self, caller: Caller | None = None):
        if not visible(self._caller(caller), "approvals:read"):
            return []
        return self.approvals.rejected()

    def approval_refusals(self, caller: Caller | None = None):
        if not visible(self._caller(caller), "approvals:read"):
            return []
        return self.approvals.refusals()

    def approval_history(self, draft_id, caller: Caller | None = None):
        if not visible(self._caller(caller), "approvals:read"):
            return []
        return self.approvals.history(draft_id)

    # -- morning board -----------------------------------------------------
    def morning_board(self, caller: Caller | None = None):
        require(self._caller(caller), "board:read")
        return self.board.board()

    def derive_board(self, caller: Caller | None = None):
        require(self._caller(caller), "board:read")
        return self.board.derive()

    # -- subscriptions and digests (Round 14) ------------------------------
    # Writes are actions and refuse loudly; reads answer with the
    # absent form. A digest read is decided by the digest's
    # content — the permissions its included sections required —
    # never by who it was addressed to alone.

    def subscribe_digest(self, actor: str, role: str, cadence: str,
                         caller: Caller | None = None):
        require(self._caller(caller), "subscriptions:write")
        return self.digests.subscribe(actor, role, cadence)

    def unsubscribe_digest(self, actor: str, role: str, cadence: str,
                           caller: Caller | None = None):
        require(self._caller(caller), "subscriptions:write")
        return self.digests.unsubscribe(actor, role, cadence)

    def read_subscriptions(self, caller: Caller | None = None):
        if not visible(self._caller(caller), "subscriptions:read"):
            return []
        return self.digests.subscriptions()

    def generate_digests(self, cadence: str,
                         period_end: str | None = None,
                         caller: Caller | None = None):
        require(self._caller(caller), "digests:generate")
        return self.digests.generate(cadence, period_end)

    def read_digest(self, artefact_id: str,
                    caller: Caller | None = None):
        artefact = self.store.get_artefact(artefact_id)
        if artefact is None or artefact.kind != DIGEST_KIND:
            return None
        if not digest_visible(artefact, self._caller(caller)):
            return None
        return artefact

    def read_digests(self, cadence: str | None = None,
                     caller: Caller | None = None):
        caller = self._caller(caller)
        if not visible(caller, "digests:read"):
            return []
        return [
            artefact
            for artefact in self.store.list_artefacts(DIGEST_KIND)
            if digest_visible(artefact, caller)
            and (cadence is None
                 or artefact.payload["cadence"] == cadence)
        ]
