"""The LangGraph operating loop.

The spine never changes shape — only its nodes grow: ingest the
dataset, compute the ledger, run the diagnosis packs, verify
everything. Later rounds hang drafting, the guardian screen and the
approval queue on these same nodes.

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
        return {
            "stockout_artefact_id": stockout.artefact_id,
            "stockout_findings": len(stockout.payload["findings"]),
            "ad_decay_artefact_id": decay.artefact_id,
            "ad_decay_findings": len(decay.payload["findings"]),
        }

    def verify_node(state: LoopState) -> LoopState:
        problems: list[str] = []
        artefact = service.store.get_artefact(state["ledger_artefact_id"])
        problems += service.ledger.verify(artefact) if artefact else ["missing"]
        for key in ("stockout_artefact_id", "ad_decay_artefact_id"):
            finding_set = service.store.get_artefact(state[key])
            if finding_set is None:
                problems.append("missing")
            else:
                problems += service.diagnosis.verify(finding_set)
        return {"verify_problems": problems}

    graph = StateGraph(LoopState)
    graph.add_node("ingest", ingest_node)
    graph.add_node("ledger", ledger_node)
    graph.add_node("diagnose", diagnose_node)
    graph.add_node("verify", verify_node)
    graph.add_edge(START, "ingest")
    graph.add_edge("ingest", "ledger")
    graph.add_edge("ledger", "diagnose")
    graph.add_edge("diagnose", "verify")
    graph.add_edge("verify", END)
    return graph.compile()
