"""The LangGraph operating loop.

The spine never changes shape — only its nodes grow: ingest the
dataset, compute the ledger, run the diagnosis packs, assemble the
growth plan, draft over it through the governed model port, verify
everything. Later rounds hang the guardian screen and the approval
queue on these same nodes.

State is a plain dict of counts and artefact ids, never figures: the
figures live in the artefacts, and nodes pass references.
"""

from __future__ import annotations

from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from .service import CommerceService


class LoopState(TypedDict, total=False):
    facts_accepted: int
    facts_refused: int
    ledger_artefact_id: str
    stockout_artefact_id: str
    stockout_findings: int
    ad_decay_artefact_id: str
    ad_decay_findings: int
    expense_artefact_id: str
    expense_findings: int
    refund_artefact_id: str
    refund_findings: int
    email_audit_artefact_id: str
    email_audit_findings: int
    plan_artefact_id: str
    plan_priorities: int
    narrative_draft_id: str
    email_copy_draft_id: str
    ad_note_draft_id: str
    verify_problems: list[str]


def build_graph(service: CommerceService):
    def ingest_node(state: LoopState) -> LoopState:
        report = service.generate_dataset()
        return {
            "facts_accepted": report.accepted if report else 0,
            "facts_refused": report.refused if report else 0,
        }

    def ledger_node(state: LoopState) -> LoopState:
        artefact = service.ledger.compute()
        return {"ledger_artefact_id": artefact.artefact_id}

    def diagnose_node(state: LoopState) -> LoopState:
        stockout = service.diagnose_stockout()
        decay = service.diagnose_ad_decay()
        expenses = service.diagnose_expenses()
        refunds = service.diagnose_refunds()
        audit = service.audit_email_flows()
        return {
            "stockout_artefact_id": stockout.artefact_id,
            "stockout_findings": len(stockout.payload["findings"]),
            "ad_decay_artefact_id": decay.artefact_id,
            "ad_decay_findings": len(decay.payload["findings"]),
            "expense_artefact_id": expenses.artefact_id,
            "expense_findings": len(expenses.payload["findings"]),
            "refund_artefact_id": refunds.artefact_id,
            "refund_findings": len(refunds.payload["findings"]),
            "email_audit_artefact_id": audit.artefact_id,
            "email_audit_findings": len(audit.payload["findings"]),
        }

    def plan_node(state: LoopState) -> LoopState:
        plan = service.assemble_plan()
        ranked = [e for e in plan.payload["priorities"]
                  if e["rank"] is not None]
        return {
            "plan_artefact_id": plan.artefact_id,
            "plan_priorities": len(ranked),
        }

    def draft_node(state: LoopState) -> LoopState:
        drafts = service.draft_all()
        return {
            "narrative_draft_id": drafts["plan_narrative"].artefact_id,
            "email_copy_draft_id": drafts["winback_email_copy"].artefact_id,
            "ad_note_draft_id": drafts["ad_budget_note"].artefact_id,
        }

    def verify_node(state: LoopState) -> LoopState:
        problems: list[str] = []
        artefact = service.store.get_artefact(state["ledger_artefact_id"])
        problems += service.ledger.verify(artefact) if artefact else ["missing"]
        for key in ("stockout_artefact_id", "ad_decay_artefact_id",
                    "expense_artefact_id", "refund_artefact_id",
                    "email_audit_artefact_id"):
            finding_set = service.store.get_artefact(state[key])
            if finding_set is None:
                problems.append("missing")
            else:
                problems += service.diagnosis.verify(finding_set)
        plan = service.store.get_artefact(state["plan_artefact_id"])
        if plan is None:
            problems.append("missing")
        else:
            problems += service.plan.verify(plan)
        for key in ("narrative_draft_id", "email_copy_draft_id",
                    "ad_note_draft_id"):
            draft = service.store.get_artefact(state[key])
            if draft is None:
                problems.append("missing")
            else:
                problems += service.drafting.verify(draft)
        return {"verify_problems": problems}

    graph = StateGraph(LoopState)
    graph.add_node("ingest", ingest_node)
    graph.add_node("ledger", ledger_node)
    graph.add_node("diagnose", diagnose_node)
    graph.add_node("plan", plan_node)
    graph.add_node("draft", draft_node)
    graph.add_node("verify", verify_node)
    graph.add_edge(START, "ingest")
    graph.add_edge("ingest", "ledger")
    graph.add_edge("ledger", "diagnose")
    graph.add_edge("diagnose", "plan")
    graph.add_edge("plan", "draft")
    graph.add_edge("draft", "verify")
    graph.add_edge("verify", END)
    return graph.compile()
