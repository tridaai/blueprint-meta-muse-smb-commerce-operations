"""Pydantic models for the facts, artefacts and call records.

Money travels as strings in payloads so Decimal values survive JSON
round trips exactly. Quantities are integers. A fact's payload is
validated by kind at the ingest port; unknown kinds are refused.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Annotated, Any

from pydantic import AfterValidator, BaseModel, Field

FACT_KINDS = ("order", "refund", "inventory", "ad_daily", "email_flow",
              "expense", "product_image")


def _finite_money(value: str) -> str:
    """A money string must be a finite Decimal the ledger can
    carry. The port refuses anything else as ``invalid_payload``:
    before Round 15 a fact carrying ``"not-a-number"`` or
    ``"NaN"`` as an amount was accepted at the port and then
    crashed the ledger and the diagnosis packs (or, for an
    order, silently poisoned the computed revenue with NaN) —
    one planted fact disabled every later computation over the
    record. A finite amount too large to quantize to cents
    (``"1e30"``) crashed the same arithmetic, so the amount
    must quantize to cents as well. The string itself is
    returned unchanged; payloads round-trip byte-identically."""
    try:
        parsed = Decimal(value)
    except InvalidOperation:
        raise ValueError(f"not a decimal amount: {value!r}") from None
    if not parsed.is_finite():
        raise ValueError(f"not a finite amount: {value!r}")
    try:
        parsed.quantize(Decimal("0.01"))
    except InvalidOperation:
        raise ValueError(
            f"amount too large to carry in cents: {value!r}") from None
    return value


#: A money amount as a string that must parse as a finite Decimal.
MoneyStr = Annotated[str, AfterValidator(_finite_money)]


class OrderPayload(BaseModel):
    order_id: str
    sku: str
    date: str  # ISO date the order was placed
    qty: int = Field(gt=0)
    unit_price: MoneyStr  # Decimal as string
    amount: MoneyStr  # Decimal as string, qty * unit_price


class RefundPayload(BaseModel):
    sku: str
    week_start: str  # ISO date, Monday of the refund week
    qty: int = Field(ge=0)
    amount: MoneyStr


class InventoryPayload(BaseModel):
    sku: str
    week_start: str
    on_hand: int = Field(ge=0)


class AdDailyPayload(BaseModel):
    ad_set: str
    date: str
    spend: MoneyStr  # Decimal as string
    attributed_revenue: MoneyStr  # Decimal as string


class EmailFlowPayload(BaseModel):
    flow_key: str
    name: str
    status: str = "active"
    steps: int = Field(gt=0)
    # Per-step send delays in hours, when the platform's export
    # carries them. The Hartwell export does not, so the field reads
    # as None on its facts and the audit's delay rule grades nothing
    # there — it grades only what a fact carries.
    step_delays_hours: list[str] | None = None


class ExpensePayload(BaseModel):
    vendor: str
    date: str
    category: str
    amount: MoneyStr  # Decimal as string


class ProductImagePayload(BaseModel):
    """A product image as measured at the image intake port
    (Round 16): the bytes live in the object store under
    ``object_key``; this payload is the measurement of them —
    the sniffed format and dimensions, the content hash, and
    the catalogue class the image is declared to show. The
    filename's claimed format rides along as ``declared_format``
    so the listing pack can judge the claim against the
    measurement."""

    sku: str
    filename: str
    declared_format: str
    format: str
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    byte_count: int = Field(gt=0)
    content_hash: str
    object_key: str
    declared_class: str


PAYLOAD_MODELS: dict[str, type[BaseModel]] = {
    "order": OrderPayload,
    "refund": RefundPayload,
    "inventory": InventoryPayload,
    "ad_daily": AdDailyPayload,
    "email_flow": EmailFlowPayload,
    "expense": ExpensePayload,
    "product_image": ProductImagePayload,
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
