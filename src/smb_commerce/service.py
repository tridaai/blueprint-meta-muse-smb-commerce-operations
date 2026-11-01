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

Round 17 adds the monthly review pack through the same
boundary: generation and export are operations, publication is
the owner's named act (refused attempts recorded as publication
refusals, like approval refusals), and pack, export, act and
refusal reads are all decided by the pack's content — the
caller must hold every permission the pack's included sections
required, or the answer is the absent form.

Round 18 adds scoped tokens and webhooks through the same
boundary. A bearer string authenticates to a caller through
:meth:`authenticate_token` — the surfaces resolve a token
exactly as they resolve a presented identity, and everything
downstream is the same pack, narrowed by the token's scopes.
Every action gate in this class runs through :meth:`_guard`,
which records a token caller's refused out-of-scope action as
an ``api_token_refusal`` artefact before the refusal raises.
And the stated record transitions — a quarantined batch, a
draft awaiting approval, an approval act, a generated digest,
a published pack — notify the webhook subscriptions after the
operation lands; a delivery outcome never fails the operation.
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
from .reviewpacks import (
    EXPORT_KIND,
    PACK_KIND,
    PUBLICATION_KIND,
    PUBLICATION_REFUSAL_KIND,
    PackPublicationRefused,
    ReviewPackService,
    export_visible,
    pack_visible,
)
from .store import SqlStore
from .tokens import TokenService
from .webhooks import WebhookService

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
        self.review_packs = ReviewPackService(
            self.store, self.board, self.objects)
        self.images = ProductImageIntake(self.store, self.ingest, self.objects)
        self.listing = ListingQualityService(
            self.store, self.model, self.objects)
        self.tokens = TokenService(self.store)
        self.webhooks = WebhookService(
            self.store, secret_key=self.settings.webhook_key)

    # -- callers -----------------------------------------------------------

    @staticmethod
    def _caller(caller: Caller | None) -> Caller:
        """No caller means an in-process flow: the stated
        internal identity, never a surface default."""
        return INTERNAL_CALLER if caller is None else caller

    def _guard(self, caller: Caller | None, permission: str) -> None:
        """The one action gate (Round 18): the pack's refusal,
        with a token caller's refused out-of-scope action
        recorded as an ``api_token_refusal`` artefact before
        the refusal raises — a refused act leaves a record,
        like every other refused act in this system."""
        caller = self._caller(caller)
        try:
            require(caller, permission)
        except AccessRefused:
            if caller.token_id:
                self.tokens.record_scope_refusal(caller, permission)
            raise

    @staticmethod
    def _event_actor(caller: Caller | None) -> str:
        """Who a webhook event names as its actor: the surface
        caller when identified, else the internal identity."""
        caller = CommerceService._caller(caller)
        return caller.actor if caller.identified else INTERNAL_CALLER.actor

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

    def _pack_family_visible(self, artefact,
                             caller: Caller) -> bool | None:
        """Content gating for the Round 17 kinds: a pack by its
        sections, an export by its pack, a publication act or
        refusal by its pack. None when the kind is not a pack
        kind (the caller falls through to the kind gate)."""
        if artefact.kind == PACK_KIND:
            return pack_visible(artefact, caller)
        if artefact.kind == EXPORT_KIND:
            pack = self.store.get_artefact(
                artefact.payload.get("pack_artefact_id", ""))
            return export_visible(artefact, pack, caller)
        if artefact.kind in (PUBLICATION_KIND,
                             PUBLICATION_REFUSAL_KIND):
            pack = self.store.get_artefact(
                artefact.payload.get("pack_artefact_id", ""))
            return pack is not None and pack_visible(pack, caller)
        return None

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
        gated = self._pack_family_visible(artefact, caller)
        if gated is not None:
            return artefact if gated else None
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
        elif kind in (PACK_KIND, EXPORT_KIND, PUBLICATION_KIND,
                      PUBLICATION_REFUSAL_KIND):
            artefacts = [
                a for a in artefacts
                if self._pack_family_visible(a, caller)]
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
        if kind in (PACK_KIND, EXPORT_KIND, PUBLICATION_KIND,
                    PUBLICATION_REFUSAL_KIND):
            visible_packs = [
                a for a in self.store.list_artefacts(kind)
                if self._pack_family_visible(a, caller)]
            return visible_packs[-1] if visible_packs else None
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
        self._guard(caller, "ingest:write")
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
        self._guard(caller, "ingest:write")
        report = self.connectors.exchange(envelope)
        if report.status == "quarantined":
            self.webhooks.notify(
                "quarantine_created",
                actor=self._event_actor(caller),
                artefacts={"quarantine": report.quarantine.artefact_id})
        return report

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
        self._guard(caller, "diagnose:run")
        return self.diagnosis.diagnose_stockout()

    def diagnose_ad_decay(self, caller: Caller | None = None):
        self._guard(caller, "diagnose:run")
        return self.diagnosis.diagnose_ad_decay()

    def diagnose_expenses(self, caller: Caller | None = None):
        self._guard(caller, "diagnose:run")
        return self.diagnosis.diagnose_expenses()

    def diagnose_refunds(self, caller: Caller | None = None):
        self._guard(caller, "diagnose:run")
        return self.diagnosis.diagnose_refunds()

    def audit_email_flows(self, caller: Caller | None = None):
        self._guard(caller, "diagnose:run")
        return self.diagnosis.audit_email_flows()

    # -- product images and listing quality (Round 16) -------------------
    def generate_product_images(self, caller: Caller | None = None
                                ) -> IngestReport:
        """Load the planted listing images through the image
        intake port. Idempotent like the dataset load: a second
        load reports duplicates at the port and stores nothing
        twice."""
        self._guard(caller, "ingest:write")
        report = generator.generate_images(self.images)
        log.info("product images generated: %d accepted, %d duplicates",
                 report.accepted, report.duplicates)
        return report

    def ingest_product_image(self, *, sku: str, filename: str,
                             data: bytes, declared_class: str,
                             source_system: str | None,
                             as_of: str | None,
                             caller: Caller | None = None):
        self._guard(caller, "ingest:write")
        return self.images.ingest_image(
            sku=sku, filename=filename, data=data,
            declared_class=declared_class,
            source_system=source_system, as_of=as_of)

    def diagnose_listing_quality(self, caller: Caller | None = None):
        self._guard(caller, "diagnose:run")
        return self.listing.diagnose()

    # -- plan --------------------------------------------------------------
    def assemble_plan(self, caller: Caller | None = None):
        self._guard(caller, "plan:run")
        return self.plan.assemble()

    # -- model -------------------------------------------------------------
    def draft(self, run_id: str, system: str, prompt: str):
        return self.model.complete(run_id, system, prompt)

    # -- profile and costs (Round 12) -------------------------------------
    def model_profile_report(self, caller: Caller | None = None):
        self._guard(caller, "config:read")
        return profile_report(self.settings)

    def cost_summary(self, caller: Caller | None = None):
        if not visible(self._caller(caller), "costs:read"):
            return empty_summary()
        return CostAnalytics(self.store).summary()

    def cost_reconcile(self, caller: Caller | None = None):
        self._guard(caller, "costs:read")
        return CostAnalytics(self.store).reconcile_all()

    def context_records(self, caller: Caller | None = None):
        from .context import CONTEXT_KIND

        if not visible(self._caller(caller), "costs:read"):
            return []
        return self.store.list_artefacts(CONTEXT_KIND)

    # -- drafting ------------------------------------------------------------
    def draft_plan_narrative(self, caller: Caller | None = None):
        self._guard(caller, "draft:run")
        return self.drafting.draft_plan_narrative()

    def draft_email_copy(self, proposal, caller: Caller | None = None):
        self._guard(caller, "draft:run")
        return self.drafting.draft_email_copy(proposal)

    def draft_ad_note(self, caller: Caller | None = None):
        self._guard(caller, "draft:run")
        return self.drafting.draft_ad_note()

    def draft_all(self, caller: Caller | None = None):
        self._guard(caller, "draft:run")
        return self.drafting.draft_all()

    # -- guardian screen -----------------------------------------------------
    def screen_draft(self, draft, caller: Caller | None = None):
        self._guard(caller, "screen:run")
        verdict = self.guardian.screen(draft)
        if verdict.payload["outcome"] == "pass":
            self.webhooks.notify(
                "draft_awaiting_approval",
                actor=self._event_actor(caller),
                artefacts={"draft": draft.artefact_id,
                           "verdict": verdict.artefact_id})
        return verdict

    def screen_all(self, caller: Caller | None = None):
        self._guard(caller, "screen:run")
        verdicts = self.guardian.screen_all()
        for draft_id, verdict in verdicts.items():
            if verdict.payload["outcome"] == "pass":
                self.webhooks.notify(
                    "draft_awaiting_approval",
                    actor=self._event_actor(caller),
                    artefacts={"draft": draft_id,
                               "verdict": verdict.artefact_id})
        return verdicts

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
        act = self.approvals.approve(draft_id, approver)
        self.webhooks.notify(
            "approval_recorded", actor=approver,
            artefacts={"act": act.artefact_id, "draft": draft_id})
        return act

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
        act = self.approvals.reject(draft_id, rejecter, reason_code, note)
        self.webhooks.notify(
            "approval_recorded", actor=rejecter,
            artefacts={"act": act.artefact_id, "draft": draft_id})
        return act

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
        self._guard(caller, "board:read")
        return self.board.board()

    def derive_board(self, caller: Caller | None = None):
        self._guard(caller, "board:read")
        return self.board.derive()

    # -- subscriptions and digests (Round 14) ------------------------------
    # Writes are actions and refuse loudly; reads answer with the
    # absent form. A digest read is decided by the digest's
    # content — the permissions its included sections required —
    # never by who it was addressed to alone.

    def subscribe_digest(self, actor: str, role: str, cadence: str,
                         caller: Caller | None = None):
        self._guard(caller, "subscriptions:write")
        return self.digests.subscribe(actor, role, cadence)

    def unsubscribe_digest(self, actor: str, role: str, cadence: str,
                           caller: Caller | None = None):
        self._guard(caller, "subscriptions:write")
        return self.digests.unsubscribe(actor, role, cadence)

    def read_subscriptions(self, caller: Caller | None = None):
        if not visible(self._caller(caller), "subscriptions:read"):
            return []
        return self.digests.subscriptions()

    def generate_digests(self, cadence: str,
                         period_end: str | None = None,
                         caller: Caller | None = None):
        self._guard(caller, "digests:generate")
        report = self.digests.generate(cadence, period_end)
        for entry in report["generated"]:
            if entry["status"] == "created":
                self.webhooks.notify(
                    "digest_generated",
                    actor=self._event_actor(caller),
                    artefacts={"digest": entry["digest_artefact_id"]})
        return report

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

    # -- monthly review pack (Round 17) --------------------------------
    # Generation and export are operations and refuse loudly;
    # reads answer with the absent form, decided by the pack's
    # content. Publication is the owner's named act: the role
    # gate and the caller-binding refuse before any pack lookup,
    # and every refused attempt is recorded.

    def generate_review_pack(self, month: str,
                             caller: Caller | None = None):
        self._guard(caller, "packs:generate")
        return self.review_packs.generate(month)

    def read_review_pack(self, artefact_id: str,
                         caller: Caller | None = None):
        artefact = self.store.get_artefact(artefact_id)
        if artefact is None or artefact.kind != PACK_KIND:
            return None
        if not pack_visible(artefact, self._caller(caller)):
            return None
        return artefact

    def read_review_packs(self, caller: Caller | None = None):
        caller = self._caller(caller)
        if not visible(caller, "packs:read"):
            return []
        return [
            artefact
            for artefact in self.store.list_artefacts(PACK_KIND)
            if pack_visible(artefact, caller)
        ]

    def verify_review_pack(self, artefact_id: str,
                           caller: Caller | None = None):
        """The pack's re-performance problems, or None when the
        pack is absent to this caller — the verify surface
        hides existence the same way the read does."""
        pack = self.read_review_pack(artefact_id, caller)
        if pack is None:
            return None
        return self.review_packs.verify(pack)

    def review_pack_status(self, artefact_id: str,
                           caller: Caller | None = None):
        pack = self.read_review_pack(artefact_id, caller)
        if pack is None:
            return None
        return self.review_packs.publication_status(pack)

    def export_review_pack(self, artefact_id: str,
                           caller: Caller | None = None):
        """Render and record the pack's export. Returns None
        when the pack is absent to this caller — the export of
        a pack the caller may not read does not happen, and
        answers as absent."""
        self._guard(caller, "packs:generate")
        if self.read_review_pack(artefact_id, caller) is None:
            return None
        return self.review_packs.export(artefact_id)

    def read_review_pack_export(self, artefact_id: str,
                                caller: Caller | None = None):
        artefact = self.store.get_artefact(artefact_id)
        if artefact is None or artefact.kind != EXPORT_KIND:
            return None
        pack = self.store.get_artefact(
            artefact.payload.get("pack_artefact_id", ""))
        if not export_visible(artefact, pack, self._caller(caller)):
            return None
        return artefact

    def read_review_pack_exports(self, pack_id: str | None = None,
                                 caller: Caller | None = None):
        caller = self._caller(caller)
        if not visible(caller, "packs:read"):
            return []
        exports = []
        for artefact in self.store.list_artefacts(EXPORT_KIND):
            if (pack_id is not None
                    and artefact.payload.get("pack_artefact_id")
                    != pack_id):
                continue
            pack = self.store.get_artefact(
                artefact.payload.get("pack_artefact_id", ""))
            if export_visible(artefact, pack, caller):
                exports.append(artefact)
        return exports

    def verify_review_pack_export(self, artefact_id: str,
                                  caller: Caller | None = None):
        export = self.read_review_pack_export(artefact_id, caller)
        if export is None:
            return None
        return self.review_packs.verify_export(export)

    def publish_review_pack(self, pack_id, publisher,
                            caller: Caller | None = None):
        caller = self._caller(caller)
        if not caller.allows("packs:publish"):
            # The gate fires before any pack lookup, so the
            # refusal is identical for real and absent packs.
            actor = caller.actor if caller.identified else publisher
            refusal = self.review_packs.record_refusal(
                "publish", pack_id, actor,
                [f"actor_role_may_not_publish:{caller.role or 'none'}"],
            )
            raise PackPublicationRefused(refusal)
        if (caller.identified and caller != INTERNAL_CALLER
                and publisher != caller.actor):
            # The act must name the human who acted (Round 15's
            # rule for approvals, applied to publication).
            refusal = self.review_packs.record_refusal(
                "publish", pack_id, caller.actor,
                ["publisher_is_not_the_calling_actor"],
            )
            raise PackPublicationRefused(refusal)
        act = self.review_packs.publish(pack_id, publisher)
        self.webhooks.notify(
            "review_pack_published", actor=publisher,
            artefacts={"act": act.artefact_id, "pack": pack_id})
        return act

    def publication_refusals(self, caller: Caller | None = None):
        return self.read_artefacts(PUBLICATION_REFUSAL_KIND, caller)

    # -- scoped tokens (Round 18) --------------------------------------
    # Issuance and revocation are the owner's acts and refuse
    # loudly; reads are metadata projections answering with the
    # absent form. Authentication itself is not gated — it is
    # how a caller is resolved — and it fails closed to the
    # anonymous caller (see tokens.py).

    def authenticate_token(self, bearer: str | None,
                           at: str | None = None) -> Caller:
        """Resolve a bearer string to its caller: the token's
        actor and role, narrowed by its scopes. A forged,
        unknown, revoked or expired string resolves to the
        anonymous caller."""
        return self.tokens.authenticate(bearer, at)

    def issue_token(self, actor: str, role: str, scopes,
                    ttl_seconds: int | None = None,
                    at: str | None = None,
                    caller: Caller | None = None):
        self._guard(caller, "tokens:issue")
        return self.tokens.issue(
            actor, role, scopes, at=at, ttl_seconds=ttl_seconds)

    def revoke_token(self, token_id: str,
                     caller: Caller | None = None):
        caller = self._caller(caller)
        self._guard(caller, "tokens:issue")
        return self.tokens.revoke(
            token_id, revoked_by=caller.actor)

    def read_tokens(self, caller: Caller | None = None):
        if not visible(self._caller(caller), "tokens:read"):
            return []
        return self.tokens.tokens()

    def read_token(self, token_id: str,
                   caller: Caller | None = None):
        if not visible(self._caller(caller), "tokens:read"):
            return None
        return self.tokens.token(token_id)

    # -- webhooks (Round 18) -------------------------------------------
    # Subscribing and unsubscribing are the owner's acts and
    # refuse loudly; reads are metadata projections answering
    # with the absent form. The signing secret appears exactly
    # once, in the subscribe result; no read returns it.

    def subscribe_webhook(self, url: str, events,
                          signing_secret: str | None = None,
                          caller: Caller | None = None):
        caller = self._caller(caller)
        self._guard(caller, "webhooks:write")
        return self.webhooks.subscribe(
            url, events, actor=caller.actor,
            signing_secret=signing_secret)

    def unsubscribe_webhook(self, subscription_id: str,
                            caller: Caller | None = None):
        caller = self._caller(caller)
        self._guard(caller, "webhooks:write")
        return self.webhooks.unsubscribe(
            subscription_id, actor=caller.actor)

    def read_webhooks(self, caller: Caller | None = None):
        if not visible(self._caller(caller), "webhooks:read"):
            return []
        return self.webhooks.subscriptions()

    def read_webhook(self, subscription_id: str,
                     caller: Caller | None = None):
        if not visible(self._caller(caller), "webhooks:read"):
            return None
        return self.webhooks.subscription(subscription_id)

    def read_webhook_deliveries(self, subscription_id: str | None = None,
                                caller: Caller | None = None):
        if not visible(self._caller(caller), "webhooks:read"):
            return []
        return self.webhooks.deliveries(subscription_id)

    def verify_webhook_delivery(self, delivery_id: str,
                                caller: Caller | None = None):
        """Re-perform a recorded delivery's signature, or None
        when the delivery is absent to this caller — the verify
        surface hides existence the same way the read does."""
        if not visible(self._caller(caller), "webhooks:read"):
            return None
        return self.webhooks.verify_delivery(delivery_id)
