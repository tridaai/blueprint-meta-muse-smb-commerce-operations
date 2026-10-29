"""The HTTP surface: health, readiness, and reads over facts,
artefacts and the cost ledger.

Reads dominate by design; the only writes are the Round 8 approval
acts (`POST /approvals/approve`, `POST /approvals/reject`), which
record a named human's act and execute nothing. Every request gets
a request id, echoed in the ``X-Request-Id`` header and stamped
into the JSON logs.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from .approvals import APPROVAL_KIND, REJECTION_KIND, ApprovalRefused
from .config import load_settings
from .logging_config import configure_logging, new_request_id, request_id_var
from .service import CommerceService

log = logging.getLogger("smb_commerce.api")

_service: CommerceService | None = None


def get_service() -> CommerceService:
    global _service
    if _service is None:
        settings = load_settings()
        configure_logging(settings.log_level)
        _service = CommerceService(settings)
    return _service


@asynccontextmanager
async def lifespan(app: FastAPI):
    get_service()
    yield


app = FastAPI(title="SMB Commerce Operations Agent", lifespan=lifespan)


@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    request_id = request.headers.get("X-Request-Id") or new_request_id()
    token = request_id_var.set(request_id)
    try:
        response = await call_next(request)
    finally:
        request_id_var.reset(token)
    response.headers["X-Request-Id"] = request_id
    return response


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/readiness")
def readiness() -> JSONResponse:
    service = get_service()
    db_ok = service.store.ping()
    migrations = service.store.applied_migrations()
    ready = db_ok and len(migrations) >= 3
    return JSONResponse(
        status_code=200 if ready else 503,
        content={
            "ready": ready,
            "database": "ok" if db_ok else "down",
            "migrations": migrations,
            "model_profile": service.settings.model_profile,
            "model_tier": service.settings.model_tier,
        },
    )


@app.get("/facts")
def list_facts(kind: str | None = None) -> dict:
    service = get_service()
    facts = service.store.list_facts(kind)
    return {"count": len(facts), "facts": [f.model_dump() for f in facts]}


@app.get("/facts/{fact_id}")
def get_fact(fact_id: str) -> dict:
    service = get_service()
    fact = service.store.get_fact(fact_id)
    if fact is None:
        raise HTTPException(status_code=404, detail="fact not found")
    return fact.model_dump()


@app.get("/ledger")
def latest_ledger() -> dict:
    service = get_service()
    artefact = service.store.latest_artefact("ledger")
    if artefact is None:
        raise HTTPException(status_code=404, detail="no ledger computed yet")
    return artefact.model_dump()


@app.get("/ledger/verify")
def verify_ledger() -> dict:
    service = get_service()
    artefact = service.store.latest_artefact("ledger")
    if artefact is None:
        raise HTTPException(status_code=404, detail="no ledger computed yet")
    problems = service.ledger.verify(artefact)
    return {"artefact_id": artefact.artefact_id, "verified": not problems,
            "problems": problems}


def _latest_diagnosis(kind: str) -> dict:
    service = get_service()
    artefact = service.store.latest_artefact(kind)
    if artefact is None:
        raise HTTPException(status_code=404, detail="no diagnosis run yet")
    return artefact.model_dump()


def _verify_diagnosis(kind: str) -> dict:
    service = get_service()
    artefact = service.store.latest_artefact(kind)
    if artefact is None:
        raise HTTPException(status_code=404, detail="no diagnosis run yet")
    problems = service.diagnosis.verify(artefact)
    return {"artefact_id": artefact.artefact_id, "verified": not problems,
            "problems": problems}


@app.get("/diagnosis/stockout")
def latest_stockout() -> dict:
    return _latest_diagnosis("diagnosis_stockout")


@app.get("/diagnosis/stockout/verify")
def verify_stockout() -> dict:
    return _verify_diagnosis("diagnosis_stockout")


@app.get("/diagnosis/ad-decay")
def latest_ad_decay() -> dict:
    return _latest_diagnosis("diagnosis_ad_decay")


@app.get("/diagnosis/ad-decay/verify")
def verify_ad_decay() -> dict:
    return _verify_diagnosis("diagnosis_ad_decay")


@app.get("/diagnosis/expense-anomalies")
def latest_expense_anomalies() -> dict:
    return _latest_diagnosis("diagnosis_expense_anomaly")


@app.get("/diagnosis/expense-anomalies/verify")
def verify_expense_anomalies() -> dict:
    return _verify_diagnosis("diagnosis_expense_anomaly")


@app.get("/diagnosis/refund-anomalies")
def latest_refund_anomalies() -> dict:
    return _latest_diagnosis("diagnosis_refund_anomaly")


@app.get("/diagnosis/refund-anomalies/verify")
def verify_refund_anomalies() -> dict:
    return _verify_diagnosis("diagnosis_refund_anomaly")


@app.get("/diagnosis/email-flow-audit")
def latest_email_flow_audit() -> dict:
    return _latest_diagnosis("diagnosis_email_flow_audit")


@app.get("/diagnosis/email-flow-audit/verify")
def verify_email_flow_audit() -> dict:
    return _verify_diagnosis("diagnosis_email_flow_audit")


@app.get("/diagnosis/flow-proposals")
def list_flow_proposals() -> dict:
    service = get_service()
    proposals = service.store.list_artefacts("draft_flow_proposal")
    return {"count": len(proposals),
            "proposals": [p.model_dump() for p in proposals]}


@app.get("/diagnosis/refusals")
def list_refusals() -> dict:
    service = get_service()
    refusals = service.store.list_artefacts("diagnosis_refusal")
    return {"count": len(refusals),
            "refusals": [r.model_dump() for r in refusals]}


@app.get("/plan")
def latest_plan() -> dict:
    service = get_service()
    artefact = service.store.latest_artefact("growth_plan")
    if artefact is None:
        raise HTTPException(status_code=404, detail="no plan assembled yet")
    return artefact.model_dump()


@app.get("/plan/verify")
def verify_plan() -> dict:
    service = get_service()
    artefact = service.store.latest_artefact("growth_plan")
    if artefact is None:
        raise HTTPException(status_code=404, detail="no plan assembled yet")
    problems = service.plan.verify(artefact)
    return {"artefact_id": artefact.artefact_id, "verified": not problems,
            "problems": problems}


@app.get("/plan/refusals")
def list_plan_refusals() -> dict:
    service = get_service()
    refusals = service.store.list_artefacts("plan_refusal")
    return {"count": len(refusals),
            "refusals": [r.model_dump() for r in refusals]}


_DRAFT_KINDS = {
    "plan-narrative": "draft_plan_narrative",
    "winback-copy": "draft_email_copy",
    "ad-budget-note": "draft_ad_note",
}


@app.get("/drafts")
def list_drafts() -> dict:
    service = get_service()
    drafts = [
        draft.model_dump()
        for kind in _DRAFT_KINDS.values()
        for draft in service.store.list_artefacts(kind)
    ]
    return {"count": len(drafts), "drafts": drafts}


def _latest_draft(slug: str) -> dict:
    service = get_service()
    artefact = service.store.latest_artefact(_DRAFT_KINDS[slug])
    if artefact is None:
        raise HTTPException(status_code=404, detail="no draft yet")
    return artefact.model_dump()


def _verify_draft(slug: str) -> dict:
    service = get_service()
    artefact = service.store.latest_artefact(_DRAFT_KINDS[slug])
    if artefact is None:
        raise HTTPException(status_code=404, detail="no draft yet")
    problems = service.drafting.verify(artefact)
    return {"artefact_id": artefact.artefact_id, "verified": not problems,
            "problems": problems}


@app.get("/drafts/plan-narrative")
def latest_narrative() -> dict:
    return _latest_draft("plan-narrative")


@app.get("/drafts/plan-narrative/verify")
def verify_narrative() -> dict:
    return _verify_draft("plan-narrative")


@app.get("/drafts/winback-copy")
def latest_winback_copy() -> dict:
    return _latest_draft("winback-copy")


@app.get("/drafts/winback-copy/verify")
def verify_winback_copy() -> dict:
    return _verify_draft("winback-copy")


@app.get("/drafts/ad-budget-note")
def latest_ad_note() -> dict:
    return _latest_draft("ad-budget-note")


@app.get("/drafts/ad-budget-note/verify")
def verify_ad_note() -> dict:
    return _verify_draft("ad-budget-note")


@app.get("/drafts/refusals")
def list_draft_refusals() -> dict:
    service = get_service()
    refusals = service.store.list_artefacts("draft_refusal")
    return {"count": len(refusals),
            "refusals": [r.model_dump() for r in refusals]}


@app.get("/guardian/verdicts")
def list_verdicts() -> dict:
    service = get_service()
    verdicts = service.store.list_artefacts("guardian_verdict")
    return {"count": len(verdicts),
            "verdicts": [v.model_dump() for v in verdicts]}


@app.get("/guardian/verdicts/{verdict_id}/verify")
def verify_verdict(verdict_id: str) -> dict:
    service = get_service()
    verdict = service.store.get_artefact(verdict_id)
    if verdict is None or verdict.kind != "guardian_verdict":
        raise HTTPException(status_code=404, detail="verdict not found")
    problems = service.guardian.verify(verdict)
    return {"artefact_id": verdict.artefact_id, "verified": not problems,
            "problems": problems}


@app.get("/guardian/awaiting-approval")
def awaiting_approval() -> dict:
    service = get_service()
    entries = service.awaiting_approval()
    return {"count": len(entries), "awaiting_approval": entries}


@app.get("/guardian/blocked")
def blocked_drafts() -> dict:
    service = get_service()
    entries = service.blocked_drafts()
    return {"count": len(entries), "blocked": entries}


class ApproveRequest(BaseModel):
    draft_artefact_id: str
    approver: str


class RejectRequest(BaseModel):
    draft_artefact_id: str
    rejecter: str
    reason_code: str
    note: str = ""


@app.get("/approvals/queue")
def approval_queue() -> dict:
    service = get_service()
    queue = service.approval_queue()
    return {
        "awaiting_count": len(queue["awaiting_approval"]),
        "approved_count": len(queue["approved"]),
        "rejected_count": len(queue["rejected"]),
        "queue": queue,
    }


@app.get("/approvals/awaiting")
def approvals_awaiting() -> dict:
    service = get_service()
    entries = service.approvals.awaiting()
    return {"count": len(entries), "awaiting_approval": entries}


@app.get("/approvals/approved")
def approvals_approved() -> dict:
    service = get_service()
    entries = service.approved_drafts()
    return {"count": len(entries), "approved": entries}


@app.get("/approvals/rejected")
def approvals_rejected() -> dict:
    service = get_service()
    entries = service.rejected_drafts()
    return {"count": len(entries), "rejected": entries}


@app.get("/approvals/refusals")
def approvals_refusals() -> dict:
    service = get_service()
    refusals = service.approvals.refusals()
    return {"count": len(refusals),
            "refusals": [r.model_dump() for r in refusals]}


@app.get("/approvals/history/{draft_id}")
def approval_history(draft_id: str) -> dict:
    service = get_service()
    entries = service.approval_history(draft_id)
    return {"draft_artefact_id": draft_id, "count": len(entries),
            "history": entries}


@app.get("/approvals/acts/{act_id}")
def approval_act(act_id: str) -> dict:
    service = get_service()
    act = service.store.get_artefact(act_id)
    if act is None or act.kind not in (APPROVAL_KIND, REJECTION_KIND):
        raise HTTPException(status_code=404, detail="approval act not found")
    return act.model_dump()


@app.get("/approvals/acts/{act_id}/verify")
def verify_approval_act(act_id: str) -> dict:
    service = get_service()
    act = service.store.get_artefact(act_id)
    if act is None or act.kind not in (APPROVAL_KIND, REJECTION_KIND):
        raise HTTPException(status_code=404, detail="approval act not found")
    problems = service.approvals.verify(act)
    return {"artefact_id": act.artefact_id, "verified": not problems,
            "problems": problems}


@app.post("/approvals/approve")
def approve_draft(body: ApproveRequest) -> dict:
    service = get_service()
    try:
        act = service.approve_draft(body.draft_artefact_id, body.approver)
    except ApprovalRefused as refused:
        raise HTTPException(status_code=422, detail={
            "refusal_artefact_id": refused.refusal.artefact_id,
            "problems": refused.refusal.payload["problems"],
        })
    return act.model_dump()


@app.post("/approvals/reject")
def reject_draft(body: RejectRequest) -> dict:
    service = get_service()
    try:
        act = service.reject_draft(
            body.draft_artefact_id, body.rejecter, body.reason_code,
            body.note)
    except ApprovalRefused as refused:
        raise HTTPException(status_code=422, detail={
            "refusal_artefact_id": refused.refusal.artefact_id,
            "problems": refused.refusal.payload["problems"],
        })
    return act.model_dump()


@app.get("/model-calls")
def list_model_calls(run_id: str | None = None) -> dict:
    service = get_service()
    calls = service.store.list_model_calls(run_id)
    return {"count": len(calls), "calls": [c.model_dump() for c in calls]}
