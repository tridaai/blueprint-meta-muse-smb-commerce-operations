"""Pydantic models for the facts, artefacts and call records.

Money travels as strings in payloads so Decimal values survive JSON
round trips exactly. Quantities are integers. A fact's payload is
validated by kind at the ingest port; unknown kinds are refused.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field

FACT_KINDS = ("order", "refund", "inventory", "ad_daily", "email_flow", "expense")


class OrderPayload(BaseModel):
    order_id: str
    sku: str
    date: str  # ISO date the order was placed
    qty: int = Field(gt=0)
    unit_price: str  # Decimal as string
    amount: str  # Decimal as string, qty * unit_price


class RefundPayload(BaseModel):
    sku: str
    week_start: str  # ISO date, Monday of the refund week
    qty: int = Field(ge=0)
    amount: str


class InventoryPayload(BaseModel):
    sku: str
    week_start: str
    on_hand: int = Field(ge=0)


class AdDailyPayload(BaseModel):
    ad_set: str
    date: str
    spend: str  # Decimal as string
    attributed_revenue: str  # Decimal as string


class EmailFlowPayload(BaseModel):
    flow_key: str
    name: str
    status: str = "active"
    steps: int = Field(gt=0)


class ExpensePayload(BaseModel):
    vendor: str
    date: str
    category: str
    amount: str  # Decimal as string


PAYLOAD_MODELS: dict[str, type[BaseModel]] = {
    "order": OrderPayload,
    "refund": RefundPayload,
    "inventory": InventoryPayload,
    "ad_daily": AdDailyPayload,
    "email_flow": EmailFlowPayload,
    "expense": ExpensePayload,
}


class Fact(BaseModel):
    """A provenance-carrying fact as stored (Law 3)."""

    fact_id: str
    kind: str
    natural_key: str
    payload: dict[str, Any]
    source_system: str
    as_of: str  # ISO date the fact is stated as of
    content_hash: str


class Artefact(BaseModel):
    """An insert-only, content-addressed computed artefact (Law 4)."""

    artefact_id: str
    kind: str
    payload: dict[str, Any]
    input_fingerprint: str


class ModelCallRecord(BaseModel):
    call_id: str
    run_id: str
    profile: str
    model: str
    tier: str
    input_tokens: int
    output_tokens: int
    cost_usd: str  # Decimal as string

    @property
    def cost(self) -> Decimal:
        return Decimal(self.cost_usd)
