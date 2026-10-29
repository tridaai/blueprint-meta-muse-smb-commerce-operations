"""The HTTP surface: health, readiness, and reads over facts,
artefacts and the cost ledger.

Round 1 is read-only by design — writes arrive in later rounds behind
the approval queue. Every request gets a request id, echoed in the
``X-Request-Id`` header and stamped into the JSON logs.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

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


@app.get("/diagnosis/refusals")
def list_refusals() -> dict:
    service = get_service()
    refusals = service.store.list_artefacts("diagnosis_refusal")
    return {"count": len(refusals),
            "refusals": [r.model_dump() for r in refusals]}


@app.get("/model-calls")
def list_model_calls(run_id: str | None = None) -> dict:
    service = get_service()
    calls = service.store.list_model_calls(run_id)
    return {"count": len(calls), "calls": [c.model_dump() for c in calls]}
