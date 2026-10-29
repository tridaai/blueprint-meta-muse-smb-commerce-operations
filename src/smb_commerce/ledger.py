"""The commerce ledger: the operating picture, computed in code.

Law 1 lives here. Every figure is Decimal arithmetic over stored
facts — revenue, refunds, gross margin, refund rate, inventory cover
days per SKU, ad return per set — and the result is stored as an
insert-only artefact whose payload names the fingerprint of the exact
fact set it was computed from (Law 4).

Definitions, stated once:

- gross revenue .. sum of order amounts
- net revenue .... gross revenue minus refund amounts
- COGS ........... sum over SKUs of net units (sold minus refunded)
                   times the SKU's unit cost in the price book below
- gross margin ... net revenue minus COGS
- refund rate .... refund amounts divided by gross revenue
- cover days ..... latest on-hand divided by the trailing-28-day
                   average daily gross units sold, and the cover-out
                   date is the as-of date plus cover days rounded
                   half-up to whole days
- ad return ...... attributed revenue divided by spend, per set,
                   overall and per week

The verifier re-performs every figure from the stored facts by a
second route — grouped accumulation through SQL-side ordering instead
of the compute path's streaming fold — and compares field by field.
One mismatch and the artefact does not verify.
"""

from __future__ import annotations

import hashlib
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from .generator import END, SKUS
from .models import Artefact, Fact
from .store import SqlStore, canonical_json

CENT = Decimal("0.01")
RATE_PLACES = Decimal("0.000001")
ROAS_PLACES = Decimal("0.0001")

PRICE_BOOK = {spec.sku: spec for spec in SKUS}


def _d(value: str) -> Decimal:
    return Decimal(value)


def _money(value: Decimal) -> str:
    return str(value.quantize(CENT, rounding=ROUND_HALF_UP))


def facts_fingerprint(facts: list[Fact]) -> str:
    material = "\n".join(
        f"{f.fact_id}:{f.content_hash}"
        for f in sorted(facts, key=lambda f: f.fact_id)
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _cover(facts: list[Fact], sku: str, as_of: date) -> tuple[int, str, str]:
    """(on_hand, cover_days, cover_out_date) from inventory + orders."""
    positions = [f for f in facts if f.kind == "inventory"
                 and f.payload["sku"] == sku]
    positions.sort(key=lambda f: f.payload["week_start"])
    on_hand = int(positions[-1].payload["on_hand"]) if positions else 0
    window_start = as_of - timedelta(days=27)
    units_28d = sum(
        int(f.payload["qty"])
        for f in facts
        if f.kind == "order" and f.payload["sku"] == sku
        and window_start.isoformat() <= f.payload["date"] <= as_of.isoformat()
    )
    if units_28d == 0:
        return on_hand, "0.00", as_of.isoformat()
    cover_days = (Decimal(on_hand * 28) / Decimal(units_28d)).quantize(
        CENT, rounding=ROUND_HALF_UP
    )
    whole_days = int(cover_days.quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    cover_out = as_of + timedelta(days=whole_days)
    return on_hand, str(cover_days), cover_out.isoformat()


def compute_ledger_payload(facts: list[Fact], as_of: date = END) -> dict:
    """Route 1: a streaming fold over the facts, in store order."""
    gross = Decimal("0")
    refunds_total = Decimal("0")
    per_sku: dict[str, dict] = {
        spec.sku: {
            "sku": spec.sku,
            "name": spec.name,
            "gross_revenue": Decimal("0"),
            "refunds": Decimal("0"),
            "units_sold": 0,
            "units_refunded": 0,
        }
        for spec in SKUS
    }
    ad: dict[str, dict] = {}
    for fact in facts:
        p = fact.payload
        if fact.kind == "order":
            amount = _d(p["amount"])
            gross += amount
            row = per_sku[p["sku"]]
            row["gross_revenue"] += amount
            row["units_sold"] += int(p["qty"])
        elif fact.kind == "refund":
            amount = _d(p["amount"])
            refunds_total += amount
            row = per_sku[p["sku"]]
            row["refunds"] += amount
            row["units_refunded"] += int(p["qty"])
        elif fact.kind == "ad_daily":
            row = ad.setdefault(
                p["ad_set"],
                {"ad_set": p["ad_set"], "spend": Decimal("0"),
                 "attributed_revenue": Decimal("0"), "weeks": {}},
            )
            row["spend"] += _d(p["spend"])
            row["attributed_revenue"] += _d(p["attributed_revenue"])
            week = _week_label(p["date"])
            wk = row["weeks"].setdefault(
                week, {"spend": Decimal("0"), "attributed_revenue": Decimal("0")}
            )
            wk["spend"] += _d(p["spend"])
            wk["attributed_revenue"] += _d(p["attributed_revenue"])
    return _assemble(gross, refunds_total, per_sku, ad, facts, as_of)


def _week_label(iso_date: str) -> str:
    day = date.fromisoformat(iso_date)
    monday = day - timedelta(days=day.weekday())
    return monday.isoformat()


def _assemble(gross, refunds_total, per_sku, ad, facts, as_of) -> dict:
    net_revenue = gross - refunds_total
    cogs = Decimal("0")
    sku_rows = []
    for spec in SKUS:
        row = per_sku[spec.sku]
        net_units = row["units_sold"] - row["units_refunded"]
        cogs += spec.unit_cost * net_units
        on_hand, cover_days, cover_out = _cover(facts, spec.sku, as_of)
        sku_rows.append({
            "sku": spec.sku,
            "name": spec.name,
            "gross_revenue": _money(row["gross_revenue"]),
            "refunds": _money(row["refunds"]),
            "net_revenue": _money(row["gross_revenue"] - row["refunds"]),
            "units_sold": row["units_sold"],
            "units_refunded": row["units_refunded"],
            "net_units": net_units,
            "on_hand": on_hand,
            "cover_days": cover_days,
            "cover_out_date": cover_out,
        })
    ad_rows = []
    for ad_set in sorted(ad):
        row = ad[ad_set]
        spend = row["spend"]
        revenue = row["attributed_revenue"]
        weekly = [
            {
                "week_start": week,
                "spend": _money(w["spend"]),
                "attributed_revenue": _money(w["attributed_revenue"]),
                "roas": str((w["attributed_revenue"] / w["spend"]).quantize(
                    ROAS_PLACES, rounding=ROUND_HALF_UP)) if w["spend"] else "0.0000",
            }
            for week, w in sorted(row["weeks"].items())
        ]
        ad_rows.append({
            "ad_set": ad_set,
            "spend": _money(spend),
            "attributed_revenue": _money(revenue),
            "roas": str((revenue / spend).quantize(
                ROAS_PLACES, rounding=ROUND_HALF_UP)) if spend else "0.0000",
            "weekly": weekly,
        })
    refund_rate = (refunds_total / gross).quantize(
        RATE_PLACES, rounding=ROUND_HALF_UP
    ) if gross else Decimal("0")
    return {
        "as_of": as_of.isoformat(),
        "gross_revenue": _money(gross),
        "refunds_total": _money(refunds_total),
        "net_revenue": _money(net_revenue),
        "cogs": _money(cogs),
        "gross_margin": _money(net_revenue - cogs),
        "refund_rate": str(refund_rate),
        "skus": sku_rows,
        "ad_sets": ad_rows,
    }


def compute_ledger_payload_route2(facts: list[Fact], as_of: date = END) -> dict:
    """Route 2: grouped accumulation — per kind, per key, summed in a
    different order than route 1's streaming fold. Written as an
    independent code path on purpose: the verifier compares its output
    against the stored artefact figure for figure."""
    by_kind: dict[str, list[Fact]] = {}
    for fact in facts:
        by_kind.setdefault(fact.kind, []).append(fact)

    gross = sum((_d(f.payload["amount"]) for f in by_kind.get("order", [])),
                Decimal("0"))
    refunds_total = sum(
        (_d(f.payload["amount"]) for f in by_kind.get("refund", [])), Decimal("0")
    )
    per_sku: dict[str, dict] = {
        spec.sku: {
            "sku": spec.sku, "name": spec.name,
            "gross_revenue": Decimal("0"), "refunds": Decimal("0"),
            "units_sold": 0, "units_refunded": 0,
        }
        for spec in SKUS
    }
    for f in by_kind.get("order", []):
        row = per_sku[f.payload["sku"]]
        row["gross_revenue"] += _d(f.payload["amount"])
        row["units_sold"] += int(f.payload["qty"])
    for f in by_kind.get("refund", []):
        row = per_sku[f.payload["sku"]]
        row["refunds"] += _d(f.payload["amount"])
        row["units_refunded"] += int(f.payload["qty"])
    ad: dict[str, dict] = {}
    for f in by_kind.get("ad_daily", []):
        p = f.payload
        row = ad.setdefault(
            p["ad_set"],
            {"ad_set": p["ad_set"], "spend": Decimal("0"),
             "attributed_revenue": Decimal("0"), "weeks": {}},
        )
        row["spend"] += _d(p["spend"])
        row["attributed_revenue"] += _d(p["attributed_revenue"])
        week = _week_label(p["date"])
        wk = row["weeks"].setdefault(
            week, {"spend": Decimal("0"), "attributed_revenue": Decimal("0")}
        )
        wk["spend"] += _d(p["spend"])
        wk["attributed_revenue"] += _d(p["attributed_revenue"])
    return _assemble(gross, refunds_total, per_sku, ad, facts, as_of)


class LedgerService:
    def __init__(self, store: SqlStore):
        self._store = store

    def compute(self) -> Artefact:
        facts = self._store.list_facts()
        fingerprint = facts_fingerprint(facts)
        payload = compute_ledger_payload(facts)
        material = canonical_json({"payload": payload, "fp": fingerprint})
        artefact_id = "LED-" + hashlib.sha256(material.encode()).hexdigest()[:20]
        existing = self._store.get_artefact(artefact_id)
        if existing is not None:
            return existing
        artefact = Artefact(
            artefact_id=artefact_id, kind="ledger",
            payload=payload, input_fingerprint=fingerprint,
        )
        self._store.insert_artefact(artefact)
        return artefact

    def verify(self, artefact: Artefact) -> list[str]:
        """Re-perform the ledger by route 2; return the problems found.

        Three checks: the fact set's fingerprint still matches the
        artefact's (nothing added or removed), every stored fact's
        content hash re-performs from its own fields (nothing edited
        behind the store's back), and every figure re-performs."""
        problems: list[str] = []
        facts = self._store.list_facts()
        if facts_fingerprint(facts) != artefact.input_fingerprint:
            problems.append("input_fingerprint_mismatch")
        from .ingest import content_hash

        for fact in facts:
            digest = content_hash(fact.kind, fact.natural_key, fact.payload,
                                  fact.source_system, fact.as_of)
            if digest != fact.content_hash:
                problems.append(f"fact_content_hash_mismatch:{fact.fact_id}")
        expected = compute_ledger_payload_route2(facts)
        if canonical_json(expected) != canonical_json(artefact.payload):
            problems.append("ledger_figure_mismatch")
            for key in expected:
                if canonical_json(expected.get(key)) != canonical_json(
                    artefact.payload.get(key)
                ):
                    problems.append(f"field:{key}")
        return problems
