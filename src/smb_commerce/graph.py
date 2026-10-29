"""The LangGraph operating loop — Round 1 shape.

The graph is deliberately small in the foundation round: ingest the
dataset, compute the ledger, verify it. Later rounds hang the
diagnosis packs, drafting, guardian screen and approval queue on
these same nodes, so the loop's spine never changes shape — only its
nodes grow.

State is a plain dict of counts and artefact ids, never figures: the
figures live in the ledger artefact, and nodes pass references.
"""

from __future__ import annotations

from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from .service import CommerceService


class LoopState(TypedDict, total=False):
    facts_accepted: int
    facts_refused: int
    ledger_artefact_id: str
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

    def verify_node(state: LoopState) -> LoopState:
        artefact = service.store.get_artefact(state["ledger_artefact_id"])
        problems = service.ledger.verify(artefact) if artefact else ["missing"]
        return {"verify_problems": problems}

    graph = StateGraph(LoopState)
    graph.add_node("ingest", ingest_node)
    graph.add_node("ledger", ledger_node)
    graph.add_node("verify", verify_node)
    graph.add_edge(START, "ingest")
    graph.add_edge("ingest", "ledger")
    graph.add_edge("ledger", "verify")
    graph.add_edge("verify", END)
    return graph.compile()
