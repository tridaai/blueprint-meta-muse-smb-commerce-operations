"""The service: one wiring point for store, ingest, ledger, model.

The API, CLI, demo and graph all talk to this class, so the surfaces
can never drift apart — a figure read over HTTP is the figure the CLI
prints, because both come from the same code path.
"""

from __future__ import annotations

import logging

from . import generator
from .config import Settings, load_settings
from .diagnosis import DiagnosisService
from .ingest import IngestPort, IngestReport
from .ledger import LedgerService
from .modelport import GovernedModelPort, build_model_port
from .objectstore import ObjectStore, build_object_store
from .plan import PlanService
from .store import SqlStore

log = logging.getLogger("smb_commerce.service")


class CommerceService:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or load_settings()
        if self.settings.use_postgres:
            self.store = SqlStore.postgres(
                self.settings.database_url, self.settings.migrations_dir
            )
        else:
            self.store = SqlStore.sqlite(
                self.settings.sqlite_path, self.settings.migrations_dir
            )
        self.ingest = IngestPort(self.store)
        self.ledger = LedgerService(self.store)
        self.diagnosis = DiagnosisService(self.store)
        self.plan = PlanService(self.store)
        self.model: GovernedModelPort = build_model_port(self.settings, self.store)
        self.objects: ObjectStore = build_object_store(self.settings)

    # -- dataset ---------------------------------------------------------
    def generate_dataset(self) -> IngestReport | None:
        """Load the Hartwell dataset once. Returns None when the store
        already holds facts (the store is insert-only; a second load is
        a no-op by design, reported as duplicates by the port)."""
        if self.store.count_facts() > 0:
            report = self.ingest.ingest_many(generator.build_fact_items())
            log.info("dataset already present: %d duplicates, %d refused",
                     report.duplicates, report.refused)
            return report
        report = generator.generate(self.ingest)
        log.info("dataset generated: %d facts accepted", report.accepted)
        return report

    def dataset_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for fact in self.store.list_facts():
            counts[fact.kind] = counts.get(fact.kind, 0) + 1
        return counts

    # -- diagnosis -----------------------------------------------------------
    def diagnose_stockout(self):
        return self.diagnosis.diagnose_stockout()

    def diagnose_ad_decay(self):
        return self.diagnosis.diagnose_ad_decay()

    def diagnose_expenses(self):
        return self.diagnosis.diagnose_expenses()

    def diagnose_refunds(self):
        return self.diagnosis.diagnose_refunds()

    def audit_email_flows(self):
        return self.diagnosis.audit_email_flows()

    # -- plan --------------------------------------------------------------
    def assemble_plan(self):
        return self.plan.assemble()

    # -- model -------------------------------------------------------------
    def draft(self, run_id: str, system: str, prompt: str):
        return self.model.complete(run_id, system, prompt)
