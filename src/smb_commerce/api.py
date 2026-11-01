"""The HTTP surface: health, readiness, and reads over facts,
artefacts and the cost ledger.

Reads dominate by design; the only writes are the Round 8 approval
acts (`POST /approvals/approve`, `POST /approvals/reject`), which
record a named human's act and execute nothing, and the Round 11
connector exchange (`POST /connectors/exchange`), which judges a
batch against its exchange contract and either ingests it through
the port or quarantines it whole as a recorded artefact. The morning board
(`/board`, Round 9) is a read too: it is re-derived from the
record on every call and never stored. Round 12 adds three reads
over the governed model record: `/model-profile` (the configured
profile resolved, never called), `/costs` (the cost ledger
aggregated and reconciled) and `/contexts` (the per-call context
records). Every request gets
a request id, echoed in the ``X-Request-Id`` header and stamped
into the JSON logs.

Round 13 puts every record endpoint behind the access pack
(`access.py`). The caller is resolved from the ``X-SMB-Actor``
and ``X-SMB-Role`` headers — presented identity, not
authentication — and passed to the service, which answers a
forbidden read with the absent form: the same 404 and detail a
missing resource returns, the empty list a list returns, the
empty summary a summary returns. Existence does not leak.
`/health` and `/readiness` stay open: they are infrastructure
probes and carry no record data.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from .access import AccessRefused, Caller, resolve_caller
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


def _caller(request: Request) -> Caller:
    """The presented identity on this request, resolved the one
    way both surfaces resolve it. Missing or unstated identity
    resolves to the anonymous caller, which the pack refuses
    everywhere on the record."""
    return resolve_caller(
        request.headers.get("X-SMB-Actor"),
        request.headers.get("X-SMB-Role"),
    )


def _forbidden(refused: AccessRefused) -> HTTPException:
    return HTTPException(
        status_code=403,
        detail=f"role not permitted: {refused.permission}")


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
def list_facts(request: Request, kind: str | None = None) -> dict:
    service = get_service()
    facts = service.read_facts(kind, _caller(request))
    return {"count": len(facts), "facts": [f.model_dump() for f in facts]}


@app.get("/facts/{fact_id}")
def get_fact(fact_id: str, request: Request) -> dict:
    service = get_service()
    fact = service.read_fact(fact_id, _caller(request))
    if fact is None:
        raise HTTPException(status_code=404, detail="fact not found")
    return fact.model_dump()


@app.get("/ledger")
def latest_ledger(request: Request) -> dict:
    service = get_service()
    artefact = service.read_latest_artefact("ledger", _caller(request))
    if artefact is None:
        raise HTTPException(status_code=404, detail="no ledger computed yet")
    return artefact.model_dump()


@app.get("/ledger/verify")
def verify_ledger(request: Request) -> dict:
    service = get_service()
    artefact = service.read_latest_artefact("ledger", _caller(request))
    if artefact is None:
        raise HTTPException(status_code=404, detail="no ledger computed yet")
    problems = service.ledger.verify(artefact)
    return {"artefact_id": artefact.artefact_id, "verified": not problems,
            "problems": problems}


def _latest_diagnosis(kind: str, caller: Caller) -> dict:
    service = get_service()
    artefact = service.read_latest_artefact(kind, caller)
    if artefact is None:
        raise HTTPException(status_code=404, detail="no diagnosis run yet")
    return artefact.model_dump()


def _verify_diagnosis(kind: str, caller: Caller) -> dict:
    service = get_service()
    artefact = service.read_latest_artefact(kind, caller)
    if artefact is None:
        raise HTTPException(status_code=404, detail="no diagnosis run yet")
    problems = service.diagnosis.verify(artefact)
    return {"artefact_id": artefact.artefact_id, "verified": not problems,
            "problems": problems}


@app.get("/diagnosis/stockout")
def latest_stockout(request: Request) -> dict:
    return _latest_diagnosis("diagnosis_stockout", _caller(request))


@app.get("/diagnosis/stockout/verify")
def verify_stockout(request: Request) -> dict:
    return _verify_diagnosis("diagnosis_stockout", _caller(request))


@app.get("/diagnosis/ad-decay")
def latest_ad_decay(request: Request) -> dict:
    return _latest_diagnosis("diagnosis_ad_decay", _caller(request))


@app.get("/diagnosis/ad-decay/verify")
def verify_ad_decay(request: Request) -> dict:
    return _verify_diagnosis("diagnosis_ad_decay", _caller(request))


@app.get("/diagnosis/expense-anomalies")
def latest_expense_anomalies(request: Request) -> dict:
    return _latest_diagnosis(
        "diagnosis_expense_anomaly", _caller(request))


@app.get("/diagnosis/expense-anomalies/verify")
def verify_expense_anomalies(request: Request) -> dict:
    return _verify_diagnosis(
        "diagnosis_expense_anomaly", _caller(request))


@app.get("/diagnosis/refund-anomalies")
def latest_refund_anomalies(request: Request) -> dict:
    return _latest_diagnosis(
        "diagnosis_refund_anomaly", _caller(request))


@app.get("/diagnosis/refund-anomalies/verify")
def verify_refund_anomalies(request: Request) -> dict:
    return _verify_diagnosis(
        "diagnosis_refund_anomaly", _caller(request))


@app.get("/diagnosis/email-flow-audit")
def latest_email_flow_audit(request: Request) -> dict:
    return _latest_diagnosis(
        "diagnosis_email_flow_audit", _caller(request))


@app.get("/diagnosis/email-flow-audit/verify")
def verify_email_flow_audit(request: Request) -> dict:
    return _verify_diagnosis(
        "diagnosis_email_flow_audit", _caller(request))


@app.get("/diagnosis/flow-proposals")
def list_flow_proposals(request: Request) -> dict:
    service = get_service()
    proposals = service.read_artefacts(
        "draft_flow_proposal", _caller(request))
    return {"count": len(proposals),
            "proposals": [p.model_dump() for p in proposals]}


@app.get("/diagnosis/refusals")
def list_refusals(request: Request) -> dict:
    service = get_service()
    refusals = service.read_artefacts(
        "diagnosis_refusal", _caller(request))
    return {"count": len(refusals),
            "refusals": [r.model_dump() for r in refusals]}


@app.get("/plan")
def latest_plan(request: Request) -> dict:
    service = get_service()
    artefact = service.read_latest_artefact("growth_plan", _caller(request))
    if artefact is None:
        raise HTTPException(status_code=404, detail="no plan assembled yet")
    return artefact.model_dump()


@app.get("/plan/verify")
def verify_plan(request: Request) -> dict:
    service = get_service()
    artefact = service.read_latest_artefact("growth_plan", _caller(request))
    if artefact is None:
        raise HTTPException(status_code=404, detail="no plan assembled yet")
    problems = service.plan.verify(artefact)
    return {"artefact_id": artefact.artefact_id, "verified": not problems,
            "problems": problems}


@app.get("/plan/refusals")
def list_plan_refusals(request: Request) -> dict:
    service = get_service()
    refusals = service.read_artefacts("plan_refusal", _caller(request))
    return {"count": len(refusals),
            "refusals": [r.model_dump() for r in refusals]}


_DRAFT_KINDS = {
    "plan-narrative": "draft_plan_narrative",
    "winback-copy": "draft_email_copy",
    "ad-budget-note": "draft_ad_note",
}


@app.get("/drafts")
def list_drafts(request: Request) -> dict:
    service = get_service()
    caller = _caller(request)
    drafts = [
        draft.model_dump()
        for kind in _DRAFT_KINDS.values()
        for draft in service.read_artefacts(kind, caller)
    ]
    return {"count": len(drafts), "drafts": drafts}


def _latest_draft(slug: str, caller: Caller) -> dict:
    service = get_service()
    artefact = service.read_latest_artefact(_DRAFT_KINDS[slug], caller)
    if artefact is None:
        raise HTTPException(status_code=404, detail="no draft yet")
    return artefact.model_dump()


def _verify_draft(slug: str, caller: Caller) -> dict:
    service = get_service()
    artefact = service.read_latest_artefact(_DRAFT_KINDS[slug], caller)
    if artefact is None:
        raise HTTPException(status_code=404, detail="no draft yet")
    problems = service.drafting.verify(artefact)
    return {"artefact_id": artefact.artefact_id, "verified": not problems,
            "problems": problems}


@app.get("/drafts/plan-narrative")
def latest_narrative(request: Request) -> dict:
    return _latest_draft("plan-narrative", _caller(request))


@app.get("/drafts/plan-narrative/verify")
def verify_narrative(request: Request) -> dict:
    return _verify_draft("plan-narrative", _caller(request))


@app.get("/drafts/winback-copy")
def latest_winback_copy(request: Request) -> dict:
    return _latest_draft("winback-copy", _caller(request))


@app.get("/drafts/winback-copy/verify")
def verify_winback_copy(request: Request) -> dict:
    return _verify_draft("winback-copy", _caller(request))


@app.get("/drafts/ad-budget-note")
def latest_ad_note(request: Request) -> dict:
    return _latest_draft("ad-budget-note", _caller(request))


@app.get("/drafts/ad-budget-note/verify")
def verify_ad_note(request: Request) -> dict:
    return _verify_draft("ad-budget-note", _caller(request))


@app.get("/drafts/refusals")
def list_draft_refusals(request: Request) -> dict:
    service = get_service()
    refusals = service.read_artefacts("draft_refusal", _caller(request))
    return {"count": len(refusals),
            "refusals": [r.model_dump() for r in refusals]}


@app.get("/guardian/verdicts")
def list_verdicts(request: Request) -> dict:
    service = get_service()
    verdicts = service.read_artefacts("guardian_verdict", _caller(request))
    return {"count": len(verdicts),
            "verdicts": [v.model_dump() for v in verdicts]}


@app.get("/guardian/verdicts/{verdict_id}/verify")
def verify_verdict(verdict_id: str, request: Request) -> dict:
    service = get_service()
    verdict = service.read_artefact(verdict_id, _caller(request))
    if verdict is None or verdict.kind != "guardian_verdict":
        raise HTTPException(status_code=404, detail="verdict not found")
    problems = service.guardian.verify(verdict)
    return {"artefact_id": verdict.artefact_id, "verified": not problems,
            "problems": problems}


@app.get("/guardian/awaiting-approval")
def awaiting_approval(request: Request) -> dict:
    service = get_service()
    entries = service.awaiting_approval(_caller(request))
    return {"count": len(entries), "awaiting_approval": entries}


@app.get("/guardian/blocked")
def blocked_drafts(request: Request) -> dict:
    service = get_service()
    entries = service.blocked_drafts(_caller(request))
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
def approval_queue(request: Request) -> dict:
    service = get_service()
    queue = service.approval_queue(_caller(request))
    return {
        "awaiting_count": len(queue["awaiting_approval"]),
        "approved_count": len(queue["approved"]),
        "rejected_count": len(queue["rejected"]),
        "queue": queue,
    }


@app.get("/approvals/awaiting")
def approvals_awaiting(request: Request) -> dict:
    service = get_service()
    entries = service.approvals_awaiting(_caller(request))
    return {"count": len(entries), "awaiting_approval": entries}


@app.get("/approvals/approved")
def approvals_approved(request: Request) -> dict:
    service = get_service()
    entries = service.approved_drafts(_caller(request))
    return {"count": len(entries), "approved": entries}


@app.get("/approvals/rejected")
def approvals_rejected(request: Request) -> dict:
    service = get_service()
    entries = service.rejected_drafts(_caller(request))
    return {"count": len(entries), "rejected": entries}


@app.get("/approvals/refusals")
def approvals_refusals(request: Request) -> dict:
    service = get_service()
    refusals = service.approval_refusals(_caller(request))
    return {"count": len(refusals),
            "refusals": [r.model_dump() for r in refusals]}


@app.get("/approvals/history/{draft_id}")
def approval_history(draft_id: str, request: Request) -> dict:
    service = get_service()
    entries = service.approval_history(draft_id, _caller(request))
    return {"draft_artefact_id": draft_id, "count": len(entries),
            "history": entries}


@app.get("/approvals/acts/{act_id}")
def approval_act(act_id: str, request: Request) -> dict:
    service = get_service()
    act = service.read_artefact(act_id, _caller(request))
    if act is None or act.kind not in (APPROVAL_KIND, REJECTION_KIND):
        raise HTTPException(status_code=404,
                            detail="approval act not found")
    return act.model_dump()


@app.get("/approvals/acts/{act_id}/verify")
def verify_approval_act(act_id: str, request: Request) -> dict:
    service = get_service()
    act = service.read_artefact(act_id, _caller(request))
    if act is None or act.kind not in (APPROVAL_KIND, REJECTION_KIND):
        raise HTTPException(status_code=404,
                            detail="approval act not found")
    problems = service.approvals.verify(act)
    return {"artefact_id": act.artefact_id, "verified": not problems,
            "problems": problems}


@app.post("/approvals/approve")
def approve_draft(body: ApproveRequest, request: Request) -> dict:
    service = get_service()
    try:
        act = service.approve_draft(
            body.draft_artefact_id, body.approver,
            caller=_caller(request))
    except ApprovalRefused as refused:
        raise HTTPException(status_code=422, detail={
            "refusal_artefact_id": refused.refusal.artefact_id,
            "problems": refused.refusal.payload["problems"],
        })
    return act.model_dump()


@app.post("/approvals/reject")
def reject_draft(body: RejectRequest, request: Request) -> dict:
    service = get_service()
    try:
        act = service.reject_draft(
            body.draft_artefact_id, body.rejecter, body.reason_code,
            body.note, caller=_caller(request))
    except ApprovalRefused as refused:
        raise HTTPException(status_code=422, detail={
            "refusal_artefact_id": refused.refusal.artefact_id,
            "problems": refused.refusal.payload["problems"],
        })
    return act.model_dump()


@app.get("/board")
def morning_board(request: Request) -> dict:
    service = get_service()
    if service.store.count_facts() == 0:
        raise HTTPException(status_code=404, detail="no board yet")
    try:
        board = service.derive_board(_caller(request))
    except AccessRefused:
        # The board's absent form, whether the record is empty
        # or the caller's role may not see it.
        raise HTTPException(status_code=404, detail="no board yet")
    return board.model_dump()


@app.get("/board/verify")
def verify_board(request: Request) -> dict:
    service = get_service()
    if service.store.count_facts() == 0:
        raise HTTPException(status_code=404, detail="no board yet")
    try:
        board = service.derive_board(_caller(request))
    except AccessRefused:
        raise HTTPException(status_code=404, detail="no board yet")
    problems = service.board.verify(board)
    return {"artefact_id": board.artefact_id, "verified": not problems,
            "problems": problems}


class ExchangeRequest(BaseModel):
    envelope: dict


@app.get("/connectors")
def list_connectors(request: Request) -> dict:
    from .connectors import CONTRACT_VERSION

    service = get_service()
    contracts = service.connector_registry(_caller(request))
    return {
        "contract_version": CONTRACT_VERSION,
        "count": len(contracts),
        "contracts": [
            {
                "name": c.name,
                "shape": c.shape,
                "source_system": c.source_system,
                "kinds": list(c.kinds),
                "pairs": [list(pair) for pair in c.pairs],
                "max_as_of_lag_days": c.max_as_of_lag_days,
            }
            for c in contracts
        ],
    }


@app.post("/connectors/exchange")
def exchange_batch(body: ExchangeRequest, request: Request) -> dict:
    service = get_service()
    try:
        report = service.exchange_batch(
            body.envelope, caller=_caller(request))
    except AccessRefused as refused:
        raise _forbidden(refused)
    if report.status == "quarantined":
        raise HTTPException(status_code=422, detail={
            "quarantine_artefact_id": report.quarantine.artefact_id,
            "batch_hash": report.batch_hash,
            "violations": [v.as_dict() for v in report.violations],
        })
    return {
        "status": report.status,
        "contract": report.contract,
        "batch_hash": report.batch_hash,
        "accepted": report.accepted,
        "duplicates": report.duplicates,
        "refused": report.ingest.refused if report.ingest else 0,
    }


@app.get("/connectors/quarantines")
def list_quarantines(request: Request) -> dict:
    service = get_service()
    quarantines = service.quarantines(_caller(request))
    return {"count": len(quarantines),
            "quarantines": [q.model_dump() for q in quarantines]}


@app.get("/connectors/quarantines/{quarantine_id}")
def get_quarantine(quarantine_id: str, request: Request) -> dict:
    service = get_service()
    artefact = service.read_quarantine(quarantine_id, _caller(request))
    if artefact is None:
        raise HTTPException(status_code=404,
                            detail="quarantine not found")
    return artefact.model_dump()


@app.get("/connectors/quarantines/{quarantine_id}/verify")
def verify_quarantine(quarantine_id: str, request: Request) -> dict:
    service = get_service()
    artefact = service.read_quarantine(quarantine_id, _caller(request))
    if artefact is None:
        raise HTTPException(status_code=404,
                            detail="quarantine not found")
    problems = service.verify_quarantine(artefact)
    return {"artefact_id": artefact.artefact_id,
            "verified": not problems, "problems": problems}


@app.get("/model-calls")
def list_model_calls(request: Request, run_id: str | None = None) -> dict:
    service = get_service()
    calls = service.read_model_calls(run_id, _caller(request))
    return {"count": len(calls), "calls": [c.model_dump() for c in calls]}


@app.get("/model-profile")
def model_profile(request: Request) -> dict:
    """The configured model profile, resolved — never called.
    For ``meta-muse`` the report carries the model id and its
    source, the base URL, the tier and whether a key is present;
    ``live_call_made`` is always False (Round 12). Round 13: any
    identified staff role may read it; an anonymous caller may
    not."""
    service = get_service()
    try:
        return service.model_profile_report(_caller(request))
    except AccessRefused as refused:
        raise _forbidden(refused)


@app.get("/costs")
def costs(request: Request) -> dict:
    """Cost analytics over the recorded ledger: totals and
    breakdowns by profile, tier, model and run, priced by the
    versioned rate card, with the reconciliation result. A role
    that may not read costs gets the empty summary — the totals
    of a real ledger do not leak."""
    service = get_service()
    return service.cost_summary(_caller(request))


@app.get("/contexts")
def contexts(request: Request) -> dict:
    """The context records: one insert-only artefact per governed
    call, carrying its policy version, budget and token counts."""
    service = get_service()
    records = service.context_records(_caller(request))
    return {"count": len(records),
            "contexts": [r.model_dump() for r in records]}
