"""Diagnosis packs I, II and III: stockout, ad-decay, expense,
refund — and the email-flow audit.

Law 5 lives here. Every diagnosis reads the head ledger artefact's
own figures (route 1) and is re-performed from the stored facts by a
second route before a finding set may exist (route 2). One wrong
figure — or a ledger that no longer matches the fact set — refuses
the whole finding set: a refusal artefact is recorded and
``DiagnosisRefused`` is raised. Nothing partial is ever stored as a
finding.

The rules are data, in the packs below, versioned as packs. Pack
``2026.10.2`` extends ``2026.10.1`` with the expense and refund rules;
pack ``2026.10.3`` (the current ``DIAGNOSIS_PACK``) extends it with
the email-flow audit. The carried sections are unchanged, and the
earlier packs are kept verbatim in ``DIAGNOSIS_PACKS`` so a finding
set stamped with its version still re-performs under its own version
in ``verify``:

- Stockout: velocity is gross units sold over the trailing window
  ending at the ledger's as-of date. A SKU whose cover days (from the
  ledger) sit at or below the pack's horizon is named, with its cover
  days, cover-out date, trailing velocity and on-hand. A SKU with no
  inventory position, or no sales inside the window, reads as
  no-data — never as zero risk, infinite cover, or (the trap) zero
  cover. SKUs with adequate cover are not named anywhere.
- Ad decay: a set is judged only when its total spend clears the
  pack's minimum and its weekly history clears the pack's minimum
  length; below either bar it is named as unjudged, with the reason.
  A judged set is decaying when its latest-week return on spend,
  divided by its first-week return, falls to or below the pack's
  decay bar. The pack also states a steady band: a set retaining at
  least that fraction of its first-week return is healthy by
  construction, so steady sets are never named.
- Expense anomalies: computed from the expense facts. An exact
  duplicate is the same vendor charging the same amount again within
  the pack's day window; the finding names both fact ids. An amount
  outlier is a charge above the pack's multiple of that vendor's own
  median charge, with the median and the multiple on the finding's
  face; a vendor with fewer charges than the pack's minimum for a
  median reads as unjudged, with the reason — never silently passed.
  A vendor's first charge above the pack's threshold is named as
  informational, in its own class, separate from the two anomaly
  classes.
- Refund anomalies: a SKU's refund rate — refund amounts over gross
  revenue, the ledger's own per-SKU figures — above the pack's bar,
  judged only over at least the pack's minimum units sold. A SKU
  below the minimum reads as unjudged with the reason, never as a
  clean rate.
- Email-flow audit: the pack states the catalogue of flows a store
  of this kind is expected to run — the flows the generator's email
  platform defines, plus the winback flow it never defined. A
  catalogue flow with no definition is a missing finding; a defined
  flow whose status is not active is a dormant finding, in its own
  class; an active flow with fewer steps than the catalogue's
  expected step shape is a step-shortfall finding; and where a flow
  fact carries per-step delays, any delay beyond the pack's sanity
  bar is a delay-breach finding. (The Hartwell facts carry no delay
  or performance figures, so those rules grade only what a fact
  carries.) A defined flow outside the catalogue is the store's own
  business and is not graded. Defined-and-healthy flows are named
  nowhere. For each missing or dormant flow the audit also emits a
  draft flow proposal artefact — the catalogue's trigger, step shape
  and delays, structure only, no copy — marked as a proposal: it
  changes nothing, and the defined flows are byte-identical before
  and after the audit runs.
"""

from __future__ import annotations

import hashlib
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from .ledger import LedgerService, facts_fingerprint
from .models import Artefact, Fact
from .store import SqlStore, canonical_json

CENT = Decimal("0.01")
ROAS_PLACES = Decimal("0.0001")
RATE_PLACES = Decimal("0.000001")

DIAGNOSIS_PACK_V1 = {
    "version": "2026.10.1",
    "stockout": {
        "velocity_window_days": 28,
        "max_cover_days": "24.00",
    },
    "ad_decay": {
        "min_total_spend": "2600.00",
        "min_weeks": 8,
        "decay_flag_max_factor": "0.5000",
        "steady_band_min_factor": "0.9000",
    },
}

DIAGNOSIS_PACK_V2 = {
    "version": "2026.10.2",
    "stockout": {
        "velocity_window_days": 28,
        "max_cover_days": "24.00",
    },
    "ad_decay": {
        "min_total_spend": "2600.00",
        "min_weeks": 8,
        "decay_flag_max_factor": "0.5000",
        "steady_band_min_factor": "0.9000",
    },
    "expense_anomaly": {
        "duplicate_window_days": 7,
        "outlier_min_charges": 3,
        "outlier_multiple": "3.00",
        "new_vendor_threshold": "1000.00",
    },
    "refund_anomaly": {
        "max_refund_rate": "0.040000",
        "min_units_sold": 1000,
    },
}

# The email-flow catalogue: the flows a store of this kind is
# expected to run. Five are the flows the generator's email platform
# defines; the sixth, winback, is the planted absence. The expected
# step shapes restate what the platform carries for the defined
# flows, so on the Hartwell facts every defined flow grades healthy
# and only the absence is named. The delays are the proposal shape a
# draft for a missing or dormant flow would take — structure only.
EMAIL_FLOW_CATALOGUE = [
    {
        "flow_key": "welcome_series", "name": "Welcome Series",
        "trigger": "new_subscriber", "expected_steps": 3,
        "step_delays_hours": ["0", "48", "96"],
    },
    {
        "flow_key": "abandoned_cart", "name": "Abandoned Cart",
        "trigger": "cart_abandoned", "expected_steps": 2,
        "step_delays_hours": ["1", "24"],
    },
    {
        "flow_key": "post_purchase", "name": "Post-Purchase",
        "trigger": "order_placed", "expected_steps": 2,
        "step_delays_hours": ["0", "168"],
    },
    {
        "flow_key": "browse_abandonment", "name": "Browse Abandonment",
        "trigger": "product_viewed_without_purchase", "expected_steps": 1,
        "step_delays_hours": ["24"],
    },
    {
        "flow_key": "replenishment", "name": "Replenishment Reminder",
        "trigger": "consumable_replenishment_due", "expected_steps": 1,
        "step_delays_hours": ["0"],
    },
    {
        "flow_key": "winback", "name": "Winback",
        "trigger": "customer_lapsed", "expected_steps": 2,
        "step_delays_hours": ["0", "336"],
    },
]

DIAGNOSIS_PACK = {
    "version": "2026.10.3",
    "stockout": {
        "velocity_window_days": 28,
        "max_cover_days": "24.00",
    },
    "ad_decay": {
        "min_total_spend": "2600.00",
        "min_weeks": 8,
        "decay_flag_max_factor": "0.5000",
        "steady_band_min_factor": "0.9000",
    },
    "expense_anomaly": {
        "duplicate_window_days": 7,
        "outlier_min_charges": 3,
        "outlier_multiple": "3.00",
        "new_vendor_threshold": "1000.00",
    },
    "refund_anomaly": {
        "max_refund_rate": "0.040000",
        "min_units_sold": 1000,
    },
    "email_flow_audit": {
        "max_step_delay_hours": "720",
        "catalogue": EMAIL_FLOW_CATALOGUE,
    },
}

DIAGNOSIS_PACKS = {
    pack["version"]: pack
    for pack in (DIAGNOSIS_PACK_V1, DIAGNOSIS_PACK_V2, DIAGNOSIS_PACK)
}


def _pack_for(version: str | None) -> dict | None:
    """The pack a finding set was stamped with, or None when the
    version is not one this code knows."""
    return DIAGNOSIS_PACKS.get(version or "")


STOCKOUT_KIND = "diagnosis_stockout"
AD_DECAY_KIND = "diagnosis_ad_decay"
EXPENSE_KIND = "diagnosis_expense_anomaly"
REFUND_KIND = "diagnosis_refund_anomaly"
EMAIL_AUDIT_KIND = "diagnosis_email_flow_audit"
PROPOSAL_KIND = "draft_flow_proposal"
REFUSAL_KIND = "diagnosis_refusal"


class DiagnosisRefused(Exception):
    """The finding set failed re-performance and was refused whole."""

    def __init__(self, refusal: Artefact):
        super().__init__(
            f"diagnosis refused: {refusal.payload['diagnosis']}: "
            f"{', '.join(refusal.payload['problems'])}"
        )
        self.refusal = refusal


def _roas(revenue: Decimal, spend: Decimal) -> str:
    if not spend:
        return "0.0000"
    return str((revenue / spend).quantize(ROAS_PLACES, rounding=ROUND_HALF_UP))


def _week_label(iso_date: str) -> str:
    day = date.fromisoformat(iso_date)
    return (day - timedelta(days=day.weekday())).isoformat()


# -- stockout -----------------------------------------------------------


def _classify_stockout(rows: list[dict], pack: dict) -> tuple[list[dict], list[dict]]:
    """SKU evaluations -> (findings, no_data). Shared shaping: the two
    routes differ in how the rows are derived, never in how they are
    judged, so a disagreement is a computation disagreement."""
    rules = pack["stockout"]
    horizon = Decimal(rules["max_cover_days"])
    findings, no_data = [], []
    for row in rows:
        if row["no_data_reason"] is not None:
            no_data.append({"sku": row["sku"], "reason": row["no_data_reason"]})
        elif Decimal(row["cover_days"]) <= horizon:
            findings.append({
                "sku": row["sku"],
                "name": row["name"],
                "on_hand": row["on_hand"],
                "cover_days": row["cover_days"],
                "cover_out_date": row["cover_out_date"],
                "trailing_units": row["trailing_units"],
                "trailing_velocity_per_day": row["trailing_velocity_per_day"],
                "velocity_window_days": rules["velocity_window_days"],
            })
    findings.sort(key=lambda f: f["sku"])
    no_data.sort(key=lambda f: f["sku"])
    return findings, no_data


def _stockout_route1(ledger: Artefact, facts: list[Fact],
                     pack: dict) -> tuple[list, list]:
    """From the head ledger artefact's own figures; velocity and the
    no-data determination read the facts, which the ledger does not
    carry."""
    rules = pack["stockout"]
    window_days = rules["velocity_window_days"]
    as_of = date.fromisoformat(ledger.payload["as_of"])
    window_start = as_of - timedelta(days=window_days - 1)
    rows = []
    for sku_row in ledger.payload["skus"]:
        sku = sku_row["sku"]
        has_position = any(
            f.kind == "inventory" and f.payload["sku"] == sku for f in facts
        )
        units = sum(
            int(f.payload["qty"])
            for f in facts
            if f.kind == "order" and f.payload["sku"] == sku
            and window_start.isoformat() <= f.payload["date"] <= as_of.isoformat()
        )
        velocity = (Decimal(units) / Decimal(window_days)).quantize(
            CENT, rounding=ROUND_HALF_UP
        )
        reason = None
        if not has_position:
            reason = "no_inventory_position"
        elif units == 0:
            reason = "no_sales_in_window"
        rows.append({
            "sku": sku,
            "name": sku_row["name"],
            "on_hand": sku_row["on_hand"],
            "cover_days": sku_row["cover_days"],
            "cover_out_date": sku_row["cover_out_date"],
            "trailing_units": units,
            "trailing_velocity_per_day": str(velocity),
            "no_data_reason": reason,
        })
    return _classify_stockout(rows, pack)


def _stockout_route2(facts: list[Fact], as_of: date,
                     pack: dict) -> tuple[list, list]:
    """From the facts alone, grouped by SKU in key order: on-hand from
    the latest inventory position, cover recomputed from the window."""
    rules = pack["stockout"]
    window_days = rules["velocity_window_days"]
    window_start = as_of - timedelta(days=window_days - 1)
    ordered = sorted(facts, key=lambda f: f.natural_key)
    positions: dict[str, tuple[str, int, str]] = {}
    window_units: dict[str, int] = {}
    names: dict[str, str] = {}
    for fact in ordered:
        p = fact.payload
        if fact.kind == "inventory":
            current = positions.get(p["sku"])
            if current is None or p["week_start"] >= current[0]:
                positions[p["sku"]] = (p["week_start"], int(p["on_hand"]), p["sku"])
        elif fact.kind == "order":
            if window_start.isoformat() <= p["date"] <= as_of.isoformat():
                window_units[p["sku"]] = window_units.get(p["sku"], 0) + int(p["qty"])
    from .generator import SKUS

    rows = []
    for spec in SKUS:
        sku = spec.sku
        names[sku] = spec.name
        units = window_units.get(sku, 0)
        position = positions.get(sku)
        reason = None
        if position is None:
            reason = "no_inventory_position"
            on_hand, cover_days, cover_out = 0, "0.00", as_of.isoformat()
        elif units == 0:
            reason = "no_sales_in_window"
            on_hand = position[1]
            cover_days, cover_out = "0.00", as_of.isoformat()
        else:
            on_hand = position[1]
            cover = (Decimal(on_hand * window_days) / Decimal(units)).quantize(
                CENT, rounding=ROUND_HALF_UP
            )
            cover_days = str(cover)
            whole = int(cover.quantize(Decimal("1"), rounding=ROUND_HALF_UP))
            cover_out = (as_of + timedelta(days=whole)).isoformat()
        velocity = (Decimal(units) / Decimal(window_days)).quantize(
            CENT, rounding=ROUND_HALF_UP
        )
        rows.append({
            "sku": sku,
            "name": names[sku],
            "on_hand": on_hand,
            "cover_days": cover_days,
            "cover_out_date": cover_out,
            "trailing_units": units,
            "trailing_velocity_per_day": str(velocity),
            "no_data_reason": reason,
        })
    return _classify_stockout(rows, pack)


# -- ad decay -------------------------------------------------------------


def _classify_decay(series: dict[str, dict],
                    pack: dict) -> tuple[list[dict], list[dict]]:
    """Per-set weekly series -> (findings, unjudged). Shared shaping,
    as with stockout: routes differ in derivation, not in judgement."""
    rules = pack["ad_decay"]
    min_spend = Decimal(rules["min_total_spend"])
    flag_bar = Decimal(rules["decay_flag_max_factor"])
    findings, unjudged = [], []
    for ad_set in sorted(series):
        row = series[ad_set]
        total_spend = Decimal(row["total_spend"])
        weeks = row["weeks"]
        if total_spend < min_spend:
            unjudged.append({
                "ad_set": ad_set, "reason": "below_min_spend",
                "total_spend": row["total_spend"], "weeks": len(weeks),
            })
            continue
        if len(weeks) < rules["min_weeks"]:
            unjudged.append({
                "ad_set": ad_set, "reason": "insufficient_history",
                "total_spend": row["total_spend"], "weeks": len(weeks),
            })
            continue
        first, latest = weeks[0], weeks[-1]
        first_roas = Decimal(first["roas"])
        if first_roas == 0:
            unjudged.append({
                "ad_set": ad_set, "reason": "no_first_week_return",
                "total_spend": row["total_spend"], "weeks": len(weeks),
            })
            continue
        factor = (Decimal(latest["roas"]) / first_roas).quantize(
            ROAS_PLACES, rounding=ROUND_HALF_UP
        )
        if factor <= flag_bar:
            findings.append({
                "ad_set": ad_set,
                "first_week": first["week_start"],
                "first_week_roas": first["roas"],
                "latest_week": latest["week_start"],
                "latest_week_roas": latest["roas"],
                "decay_factor": str(factor),
                "weeks": len(weeks),
                "total_spend": row["total_spend"],
            })
    return findings, unjudged


def _decay_route1(ledger: Artefact, pack: dict) -> tuple[list, list]:
    series = {
        row["ad_set"]: {"total_spend": row["spend"], "weeks": row["weekly"]}
        for row in ledger.payload["ad_sets"]
    }
    return _classify_decay(series, pack)


def _decay_route2(facts: list[Fact], pack: dict) -> tuple[list, list]:
    grouped: dict[str, dict[str, dict[str, Decimal]]] = {}
    for fact in sorted(facts, key=lambda f: f.natural_key):
        if fact.kind != "ad_daily":
            continue
        p = fact.payload
        weeks = grouped.setdefault(p["ad_set"], {})
        wk = weeks.setdefault(
            _week_label(p["date"]),
            {"spend": Decimal("0"), "attributed_revenue": Decimal("0")},
        )
        wk["spend"] += Decimal(p["spend"])
        wk["attributed_revenue"] += Decimal(p["attributed_revenue"])
    series = {}
    for ad_set, weeks in grouped.items():
        total = sum((w["spend"] for w in weeks.values()), Decimal("0"))
        series[ad_set] = {
            "total_spend": str(total.quantize(CENT, rounding=ROUND_HALF_UP)),
            "weeks": [
                {
                    "week_start": week,
                    "roas": _roas(w["attributed_revenue"], w["spend"]),
                }
                for week, w in sorted(weeks.items())
            ],
        }
    return _classify_decay(series, pack)


# -- expense anomalies ----------------------------------------------------


def _median(amounts: list[Decimal]) -> Decimal:
    ordered = sorted(amounts)
    count = len(ordered)
    mid = count // 2
    if count % 2:
        return ordered[mid]
    return ((ordered[mid - 1] + ordered[mid]) / 2).quantize(
        CENT, rounding=ROUND_HALF_UP
    )


def _classify_expenses(
    vendors: dict[str, list[dict]], pack: dict
) -> tuple[list[dict], list[dict], list[dict]]:
    """Per-vendor charge lists -> (findings, informational, unjudged).

    Shared shaping, as with the pack I diagnoses: the routes differ in
    how the charge lists are derived, never in how they are judged."""
    rules = pack["expense_anomaly"]
    window = rules["duplicate_window_days"]
    min_charges = rules["outlier_min_charges"]
    multiple = Decimal(rules["outlier_multiple"])
    threshold = Decimal(rules["new_vendor_threshold"])
    findings, informational, unjudged = [], [], []
    for vendor in sorted(vendors):
        charges = sorted(
            vendors[vendor], key=lambda c: (c["date"], c["fact_id"])
        )
        by_amount: dict[str, list[dict]] = {}
        for charge in charges:
            by_amount.setdefault(charge["amount"], []).append(charge)
        for amount in sorted(by_amount):
            group = by_amount[amount]
            for first, second in zip(group, group[1:]):
                gap = (
                    date.fromisoformat(second["date"])
                    - date.fromisoformat(first["date"])
                ).days
                if gap <= window:
                    findings.append({
                        "class": "duplicate_charge",
                        "vendor": vendor,
                        "amount": amount,
                        "dates": [first["date"], second["date"]],
                        "fact_ids": [first["fact_id"], second["fact_id"]],
                        "gap_days": gap,
                        "window_days": window,
                    })
        if len(charges) < min_charges:
            unjudged.append({
                "vendor": vendor,
                "reason": "insufficient_charges",
                "charges": len(charges),
            })
        else:
            median = _median([Decimal(c["amount"]) for c in charges])
            for charge in charges:
                if Decimal(charge["amount"]) > multiple * median:
                    findings.append({
                        "class": "amount_outlier",
                        "vendor": vendor,
                        "fact_id": charge["fact_id"],
                        "date": charge["date"],
                        "amount": charge["amount"],
                        "vendor_median": str(median),
                        "multiple": rules["outlier_multiple"],
                        "charges": len(charges),
                    })
        first_charge = charges[0]
        if Decimal(first_charge["amount"]) > threshold:
            informational.append({
                "class": "new_vendor_first_charge",
                "vendor": vendor,
                "fact_id": first_charge["fact_id"],
                "date": first_charge["date"],
                "amount": first_charge["amount"],
                "threshold": rules["new_vendor_threshold"],
            })
    findings.sort(key=lambda f: (
        f["vendor"], f["class"],
        f["fact_ids"] if f["class"] == "duplicate_charge" else [f["fact_id"]],
    ))
    informational.sort(key=lambda f: f["vendor"])
    unjudged.sort(key=lambda f: f["vendor"])
    return findings, informational, unjudged


def _expense_route1(facts: list[Fact], pack: dict) -> tuple[list, list, list]:
    """From the expense facts in store order, grouped by vendor as
    they arrive."""
    vendors: dict[str, list[dict]] = {}
    for fact in facts:
        if fact.kind != "expense":
            continue
        p = fact.payload
        vendors.setdefault(p["vendor"], []).append({
            "fact_id": fact.fact_id, "date": p["date"], "amount": p["amount"],
        })
    return _classify_expenses(vendors, pack)


def _expense_route2(facts: list[Fact], pack: dict) -> tuple[list, list, list]:
    """From the expense facts as flat tuples sorted by (vendor, date,
    fact id): a different derivation of the same charge lists."""
    rows = sorted(
        (f.payload["vendor"], f.payload["date"], f.fact_id,
         f.payload["amount"])
        for f in facts
        if f.kind == "expense"
    )
    vendors: dict[str, list[dict]] = {}
    for vendor, day, fact_id, amount in rows:
        vendors.setdefault(vendor, []).append({
            "fact_id": fact_id, "date": day, "amount": amount,
        })
    return _classify_expenses(vendors, pack)


# -- refund anomalies -------------------------------------------------------


def _refund_rate(refunds: Decimal, gross: Decimal) -> str:
    if not gross:
        return "0.000000"
    return str((refunds / gross).quantize(RATE_PLACES, rounding=ROUND_HALF_UP))


def _classify_refunds(rows: list[dict], pack: dict) -> tuple[list[dict], list[dict]]:
    """Per-SKU ledger figures -> (findings, unjudged). Shared shaping,
    as with the other diagnoses."""
    rules = pack["refund_anomaly"]
    bar = Decimal(rules["max_refund_rate"])
    min_units = rules["min_units_sold"]
    findings, unjudged = [], []
    for row in rows:
        if row["units_sold"] < min_units:
            unjudged.append({
                "sku": row["sku"],
                "reason": "below_min_volume",
                "units_sold": row["units_sold"],
            })
            continue
        rate = _refund_rate(Decimal(row["refunds"]),
                            Decimal(row["gross_revenue"]))
        if Decimal(rate) > bar:
            findings.append({
                "sku": row["sku"],
                "name": row["name"],
                "gross_revenue": row["gross_revenue"],
                "refunds": row["refunds"],
                "refund_rate": rate,
                "units_sold": row["units_sold"],
                "units_refunded": row["units_refunded"],
                "max_refund_rate": rules["max_refund_rate"],
            })
    findings.sort(key=lambda f: f["sku"])
    unjudged.sort(key=lambda f: f["sku"])
    return findings, unjudged


def _refund_route1(ledger: Artefact, pack: dict) -> tuple[list, list]:
    """From the head ledger artefact's own per-SKU figures."""
    rows = [
        {
            "sku": row["sku"],
            "name": row["name"],
            "gross_revenue": row["gross_revenue"],
            "refunds": row["refunds"],
            "units_sold": row["units_sold"],
            "units_refunded": row["units_refunded"],
        }
        for row in ledger.payload["skus"]
    ]
    return _classify_refunds(rows, pack)


def _refund_route2(facts: list[Fact], pack: dict) -> tuple[list, list]:
    """From the order and refund facts alone, accumulated per SKU in
    key order over the price book's SKUs."""
    from .generator import SKUS

    totals: dict[str, dict] = {
        spec.sku: {
            "sku": spec.sku, "name": spec.name,
            "gross": Decimal("0"), "refunds": Decimal("0"),
            "units_sold": 0, "units_refunded": 0,
        }
        for spec in SKUS
    }
    for fact in sorted(facts, key=lambda f: f.natural_key):
        row = totals.get(fact.payload.get("sku"))
        if row is None:
            continue
        if fact.kind == "order":
            row["gross"] += Decimal(fact.payload["amount"])
            row["units_sold"] += int(fact.payload["qty"])
        elif fact.kind == "refund":
            row["refunds"] += Decimal(fact.payload["amount"])
            row["units_refunded"] += int(fact.payload["qty"])
    rows = [
        {
            "sku": row["sku"],
            "name": row["name"],
            "gross_revenue": str(row["gross"].quantize(
                CENT, rounding=ROUND_HALF_UP)),
            "refunds": str(row["refunds"].quantize(
                CENT, rounding=ROUND_HALF_UP)),
            "units_sold": row["units_sold"],
            "units_refunded": row["units_refunded"],
        }
        for row in totals.values()
    ]
    return _classify_refunds(rows, pack)


# -- email-flow audit -----------------------------------------------------


def _catalogue_entry(pack: dict, flow_key: str) -> dict | None:
    for entry in pack["email_flow_audit"]["catalogue"]:
        if entry["flow_key"] == flow_key:
            return entry
    return None


def _classify_email_audit(defined: dict[str, dict],
                          pack: dict) -> list[dict]:
    """Defined email flows against the pack's catalogue -> findings.

    Shared shaping, as with the other diagnoses: the routes differ in
    how the defined-flow map is derived, never in how it is judged.
    A catalogue flow with no definition is missing; a defined flow
    that is not active is dormant, in its own class, and is judged no
    further this round; an active flow is graded on its step shape,
    and on any per-step delays its fact carries. Healthy flows and
    flows outside the catalogue produce no finding."""
    rules = pack["email_flow_audit"]
    delay_bar = Decimal(rules["max_step_delay_hours"])
    findings = []
    for entry in rules["catalogue"]:
        key = entry["flow_key"]
        flow = defined.get(key)
        if flow is None:
            findings.append({
                "class": "missing_flow",
                "flow_key": key,
                "name": entry["name"],
                "trigger": entry["trigger"],
                "expected_steps": entry["expected_steps"],
            })
            continue
        if flow["status"] != "active":
            findings.append({
                "class": "dormant_flow",
                "flow_key": key,
                "name": flow["name"],
                "status": flow["status"],
                "steps": flow["steps"],
            })
            continue
        if int(flow["steps"]) < entry["expected_steps"]:
            findings.append({
                "class": "step_shortfall",
                "flow_key": key,
                "name": flow["name"],
                "steps": flow["steps"],
                "expected_steps": entry["expected_steps"],
            })
        for index, delay in enumerate(flow.get("step_delays_hours") or []):
            if Decimal(delay) > delay_bar:
                findings.append({
                    "class": "delay_breach",
                    "flow_key": key,
                    "name": flow["name"],
                    "step_index": index,
                    "delay_hours": delay,
                    "max_step_delay_hours": rules["max_step_delay_hours"],
                })
    findings.sort(key=lambda f: (f["flow_key"], f["class"]))
    return findings


def _email_route1(facts: list[Fact], pack: dict) -> list[dict]:
    """From the email-flow facts in store order, keyed by flow."""
    defined = {}
    for fact in facts:
        if fact.kind == "email_flow":
            defined[fact.payload["flow_key"]] = fact.payload
    return _classify_email_audit(defined, pack)


def _email_route2(facts: list[Fact], pack: dict) -> list[dict]:
    """From the email-flow facts in natural-key order, each payload
    re-read field by field: a different derivation of the same map."""
    defined = {}
    for fact in sorted(facts, key=lambda f: f.natural_key):
        if fact.kind != "email_flow":
            continue
        p = fact.payload
        flow = {
            "flow_key": p["flow_key"],
            "name": p["name"],
            "status": p["status"],
            "steps": p["steps"],
        }
        if p.get("step_delays_hours") is not None:
            flow["step_delays_hours"] = list(p["step_delays_hours"])
        defined[p["flow_key"]] = flow
    return _classify_email_audit(defined, pack)


def _proposal_payload(finding: dict, pack: dict, audit_id: str) -> dict:
    """The draft proposal for one missing or dormant flow: the
    catalogue's structure for that flow — trigger, step count, step
    delays — and nothing else. No copy is drafted this round; the
    proposal is marked as one, and acting on it belongs to a later
    approval round."""
    entry = _catalogue_entry(pack, finding["flow_key"])
    return {
        "proposal_for": finding["class"],
        "flow_key": entry["flow_key"],
        "name": entry["name"],
        "trigger": entry["trigger"],
        "steps": entry["expected_steps"],
        "step_delays_hours": list(entry["step_delays_hours"]),
        "audit_artefact_id": audit_id,
        "pack_version": pack["version"],
        "status": "proposal",
        "changes_nothing": True,
    }


def _proposal_artefact_id(payload: dict, fingerprint: str) -> str:
    material = canonical_json({"payload": payload, "fp": fingerprint})
    return "DFP-" + hashlib.sha256(material.encode()).hexdigest()[:20]


def _proposal_descriptors(findings: list[dict]) -> list[dict]:
    """Which findings earn a draft proposal, in findings order."""
    return [
        {"flow_key": f["flow_key"], "proposal_for": f["class"]}
        for f in findings
        if f["class"] in ("missing_flow", "dormant_flow")
    ]


# -- the service ----------------------------------------------------------


class DiagnosisService:
    def __init__(self, store: SqlStore):
        self._store = store
        self._ledger = LedgerService(store)

    def _head_ledger(self) -> Artefact:
        ledger = self._store.latest_artefact("ledger")
        if ledger is None:
            ledger = self._ledger.compute()
        return ledger

    def _fingerprint(self, ledger: Artefact) -> str:
        material = (
            f"{ledger.artefact_id}:{ledger.input_fingerprint}"
            f":{DIAGNOSIS_PACK['version']}"
        )
        return hashlib.sha256(material.encode("utf-8")).hexdigest()

    def _refuse(self, diagnosis: str, ledger: Artefact,
                problems: list[str]) -> Artefact:
        payload = {
            "diagnosis": diagnosis,
            "pack_version": DIAGNOSIS_PACK["version"],
            "ledger_artefact_id": ledger.artefact_id,
            "problems": problems,
        }
        fingerprint = self._fingerprint(ledger)
        material = canonical_json({"payload": payload, "fp": fingerprint})
        artefact_id = "DRF-" + hashlib.sha256(material.encode()).hexdigest()[:20]
        existing = self._store.get_artefact(artefact_id)
        if existing is not None:
            return existing
        refusal = Artefact(
            artefact_id=artefact_id, kind=REFUSAL_KIND,
            payload=payload, input_fingerprint=fingerprint,
        )
        self._store.insert_artefact(refusal)
        return refusal

    def _store_findings(self, kind: str, prefix: str, payload: dict,
                        ledger: Artefact) -> Artefact:
        fingerprint = self._fingerprint(ledger)
        material = canonical_json({"payload": payload, "fp": fingerprint})
        artefact_id = prefix + hashlib.sha256(material.encode()).hexdigest()[:20]
        existing = self._store.get_artefact(artefact_id)
        if existing is not None:
            return existing
        artefact = Artefact(
            artefact_id=artefact_id, kind=kind,
            payload=payload, input_fingerprint=fingerprint,
        )
        self._store.insert_artefact(artefact)
        return artefact

    def _ledger_currency_problems(self, ledger: Artefact) -> list[str]:
        """The ledger must still describe the stored fact set exactly:
        the fingerprint over the facts must match, and every fact's
        content hash must re-perform from its own fields — a payload
        edited behind the store's back leaves the fingerprint intact
        but fails the second check, the same discipline the ledger's
        own verifier applies."""
        from .ingest import content_hash

        facts = self._store.list_facts()
        problems: list[str] = []
        stale = facts_fingerprint(facts) != ledger.input_fingerprint
        for fact in facts:
            digest = content_hash(fact.kind, fact.natural_key, fact.payload,
                                  fact.source_system, fact.as_of)
            if digest != fact.content_hash:
                stale = True
                problems.append(f"fact_content_hash_mismatch:{fact.fact_id}")
        if stale:
            problems.insert(0, "ledger_not_current")
        return problems

    def diagnose_stockout(self) -> Artefact:
        ledger = self._head_ledger()
        facts = self._store.list_facts()
        findings, no_data = _stockout_route1(ledger, facts, DIAGNOSIS_PACK)
        problems = self._ledger_currency_problems(ledger)
        expected_findings, expected_no_data = _stockout_route2(
            facts, date.fromisoformat(ledger.payload["as_of"]), DIAGNOSIS_PACK
        )
        if canonical_json(findings) != canonical_json(expected_findings):
            problems.append("stockout_finding_mismatch")
        if canonical_json(no_data) != canonical_json(expected_no_data):
            problems.append("stockout_no_data_mismatch")
        if problems:
            raise DiagnosisRefused(
                self._refuse("stockout_risk", ledger, problems)
            )
        payload = {
            "diagnosis": "stockout_risk",
            "pack_version": DIAGNOSIS_PACK["version"],
            "ledger_artefact_id": ledger.artefact_id,
            "as_of": ledger.payload["as_of"],
            "rules": DIAGNOSIS_PACK["stockout"],
            "findings": findings,
            "no_data": no_data,
        }
        return self._store_findings(STOCKOUT_KIND, "DST-", payload, ledger)

    def diagnose_ad_decay(self) -> Artefact:
        ledger = self._head_ledger()
        facts = self._store.list_facts()
        findings, unjudged = _decay_route1(ledger, DIAGNOSIS_PACK)
        problems = self._ledger_currency_problems(ledger)
        expected_findings, expected_unjudged = _decay_route2(
            facts, DIAGNOSIS_PACK
        )
        if canonical_json(findings) != canonical_json(expected_findings):
            problems.append("ad_decay_finding_mismatch")
        if canonical_json(unjudged) != canonical_json(expected_unjudged):
            problems.append("ad_decay_unjudged_mismatch")
        if problems:
            raise DiagnosisRefused(
                self._refuse("ad_decay", ledger, problems)
            )
        payload = {
            "diagnosis": "ad_decay",
            "pack_version": DIAGNOSIS_PACK["version"],
            "ledger_artefact_id": ledger.artefact_id,
            "as_of": ledger.payload["as_of"],
            "rules": DIAGNOSIS_PACK["ad_decay"],
            "findings": findings,
            "unjudged": unjudged,
        }
        return self._store_findings(AD_DECAY_KIND, "DAD-", payload, ledger)

    def diagnose_expenses(self) -> Artefact:
        ledger = self._head_ledger()
        facts = self._store.list_facts()
        findings, informational, unjudged = _expense_route1(
            facts, DIAGNOSIS_PACK
        )
        problems = self._ledger_currency_problems(ledger)
        expected = _expense_route2(facts, DIAGNOSIS_PACK)
        if canonical_json(findings) != canonical_json(expected[0]):
            problems.append("expense_finding_mismatch")
        if canonical_json(informational) != canonical_json(expected[1]):
            problems.append("expense_informational_mismatch")
        if canonical_json(unjudged) != canonical_json(expected[2]):
            problems.append("expense_unjudged_mismatch")
        if problems:
            raise DiagnosisRefused(
                self._refuse("expense_anomaly", ledger, problems)
            )
        payload = {
            "diagnosis": "expense_anomaly",
            "pack_version": DIAGNOSIS_PACK["version"],
            "ledger_artefact_id": ledger.artefact_id,
            "as_of": ledger.payload["as_of"],
            "rules": DIAGNOSIS_PACK["expense_anomaly"],
            "findings": findings,
            "informational": informational,
            "unjudged": unjudged,
        }
        return self._store_findings(EXPENSE_KIND, "DEA-", payload, ledger)

    def diagnose_refunds(self) -> Artefact:
        ledger = self._head_ledger()
        facts = self._store.list_facts()
        findings, unjudged = _refund_route1(ledger, DIAGNOSIS_PACK)
        problems = self._ledger_currency_problems(ledger)
        expected_findings, expected_unjudged = _refund_route2(
            facts, DIAGNOSIS_PACK
        )
        if canonical_json(findings) != canonical_json(expected_findings):
            problems.append("refund_finding_mismatch")
        if canonical_json(unjudged) != canonical_json(expected_unjudged):
            problems.append("refund_unjudged_mismatch")
        if problems:
            raise DiagnosisRefused(
                self._refuse("refund_anomaly", ledger, problems)
            )
        payload = {
            "diagnosis": "refund_anomaly",
            "pack_version": DIAGNOSIS_PACK["version"],
            "ledger_artefact_id": ledger.artefact_id,
            "as_of": ledger.payload["as_of"],
            "rules": DIAGNOSIS_PACK["refund_anomaly"],
            "findings": findings,
            "unjudged": unjudged,
        }
        return self._store_findings(REFUND_KIND, "DRA-", payload, ledger)

    def _store_proposal(self, payload: dict, fingerprint: str) -> Artefact:
        artefact_id = _proposal_artefact_id(payload, fingerprint)
        existing = self._store.get_artefact(artefact_id)
        if existing is not None:
            return existing
        artefact = Artefact(
            artefact_id=artefact_id, kind=PROPOSAL_KIND,
            payload=payload, input_fingerprint=fingerprint,
        )
        self._store.insert_artefact(artefact)
        return artefact

    def audit_email_flows(self) -> Artefact:
        """The email-flow audit. The findings are re-performed by
        route 2 before anything is stored; then the audit artefact
        and one draft proposal per missing or dormant flow land
        together. The audit writes artefacts only — it never touches
        a flow fact, so the defined flows are byte-identical before
        and after it runs."""
        ledger = self._head_ledger()
        facts = self._store.list_facts()
        findings = _email_route1(facts, DIAGNOSIS_PACK)
        problems = self._ledger_currency_problems(ledger)
        expected = _email_route2(facts, DIAGNOSIS_PACK)
        if canonical_json(findings) != canonical_json(expected):
            problems.append("email_audit_finding_mismatch")
        if problems:
            raise DiagnosisRefused(
                self._refuse("email_flow_audit", ledger, problems)
            )
        payload = {
            "diagnosis": "email_flow_audit",
            "pack_version": DIAGNOSIS_PACK["version"],
            "ledger_artefact_id": ledger.artefact_id,
            "as_of": ledger.payload["as_of"],
            "rules": DIAGNOSIS_PACK["email_flow_audit"],
            "findings": findings,
            "proposals": _proposal_descriptors(findings),
        }
        audit = self._store_findings(EMAIL_AUDIT_KIND, "DEF-", payload, ledger)
        fingerprint = self._fingerprint(ledger)
        for finding in findings:
            if finding["class"] in ("missing_flow", "dormant_flow"):
                self._store_proposal(
                    _proposal_payload(finding, DIAGNOSIS_PACK,
                                      audit.artefact_id),
                    fingerprint,
                )
        return audit

    def verify(self, artefact: Artefact) -> list[str]:
        """Re-perform a stored finding set from the facts by route 2,
        under the pack version the set was stamped with — a finding
        set from an earlier pack version re-performs under its own
        version's rules, not today's.

        A refusal artefact is a record of a refusal, not a finding
        set; it carries nothing to re-perform and verifies clean."""
        if artefact.kind == REFUSAL_KIND:
            return []
        if artefact.kind == PROPOSAL_KIND:
            return self._verify_proposal(artefact)
        ledger = self._store.get_artefact(artefact.payload["ledger_artefact_id"])
        if ledger is None:
            return ["ledger_missing"]
        problems = self._ledger_currency_problems(ledger)
        pack = _pack_for(artefact.payload.get("pack_version"))
        if pack is None:
            problems.append(
                f"unknown_pack_version:{artefact.payload.get('pack_version')}"
            )
            return problems
        facts = self._store.list_facts()
        if artefact.kind == STOCKOUT_KIND:
            findings, no_data = _stockout_route2(
                facts, date.fromisoformat(ledger.payload["as_of"]), pack
            )
            if canonical_json(findings) != canonical_json(artefact.payload["findings"]):
                problems.append("stockout_finding_mismatch")
            if canonical_json(no_data) != canonical_json(artefact.payload["no_data"]):
                problems.append("stockout_no_data_mismatch")
        elif artefact.kind == AD_DECAY_KIND:
            findings, unjudged = _decay_route2(facts, pack)
            if canonical_json(findings) != canonical_json(artefact.payload["findings"]):
                problems.append("ad_decay_finding_mismatch")
            if canonical_json(unjudged) != canonical_json(artefact.payload["unjudged"]):
                problems.append("ad_decay_unjudged_mismatch")
        elif artefact.kind == EXPENSE_KIND and "expense_anomaly" not in pack:
            problems.append("pack_section_missing:expense_anomaly")
        elif artefact.kind == REFUND_KIND and "refund_anomaly" not in pack:
            problems.append("pack_section_missing:refund_anomaly")
        elif artefact.kind == EXPENSE_KIND:
            findings, informational, unjudged = _expense_route2(facts, pack)
            if canonical_json(findings) != canonical_json(artefact.payload["findings"]):
                problems.append("expense_finding_mismatch")
            if canonical_json(informational) != canonical_json(artefact.payload["informational"]):
                problems.append("expense_informational_mismatch")
            if canonical_json(unjudged) != canonical_json(artefact.payload["unjudged"]):
                problems.append("expense_unjudged_mismatch")
        elif artefact.kind == REFUND_KIND:
            findings, unjudged = _refund_route2(facts, pack)
            if canonical_json(findings) != canonical_json(artefact.payload["findings"]):
                problems.append("refund_finding_mismatch")
            if canonical_json(unjudged) != canonical_json(artefact.payload["unjudged"]):
                problems.append("refund_unjudged_mismatch")
        elif artefact.kind == EMAIL_AUDIT_KIND and "email_flow_audit" not in pack:
            problems.append("pack_section_missing:email_flow_audit")
        elif artefact.kind == EMAIL_AUDIT_KIND:
            findings = _email_route2(facts, pack)
            if canonical_json(findings) != canonical_json(artefact.payload["findings"]):
                problems.append("email_audit_finding_mismatch")
            descriptors = _proposal_descriptors(findings)
            if canonical_json(descriptors) != canonical_json(artefact.payload["proposals"]):
                problems.append("email_audit_proposal_mismatch")
            else:
                for finding in findings:
                    if finding["class"] not in ("missing_flow", "dormant_flow"):
                        continue
                    expected_payload = _proposal_payload(
                        finding, pack, artefact.artefact_id)
                    proposal_id = _proposal_artefact_id(
                        expected_payload, artefact.input_fingerprint)
                    stored = self._store.get_artefact(proposal_id)
                    if stored is None:
                        problems.append(
                            f"email_audit_proposal_missing:{finding['flow_key']}")
                    elif canonical_json(stored.payload) != canonical_json(expected_payload):
                        problems.append(
                            f"email_audit_proposal_mismatch:{finding['flow_key']}")
        else:
            problems.append(f"not_a_diagnosis:{artefact.kind}")
        return problems

    def _verify_proposal(self, artefact: Artefact) -> list[str]:
        """A draft proposal re-performs from the catalogue entry it
        names, under the pack version it was stamped with, bound to
        the audit artefact that emitted it."""
        pack = _pack_for(artefact.payload.get("pack_version"))
        if pack is None:
            return [f"unknown_pack_version:{artefact.payload.get('pack_version')}"]
        if "email_flow_audit" not in pack:
            return ["pack_section_missing:email_flow_audit"]
        audit = self._store.get_artefact(artefact.payload["audit_artefact_id"])
        if audit is None:
            return ["audit_missing"]
        if _catalogue_entry(pack, artefact.payload["flow_key"]) is None:
            return [f"unknown_flow:{artefact.payload['flow_key']}"]
        finding = {
            "class": artefact.payload["proposal_for"],
            "flow_key": artefact.payload["flow_key"],
        }
        expected = _proposal_payload(finding, pack, audit.artefact_id)
        if canonical_json(expected) != canonical_json(artefact.payload):
            return [f"email_audit_proposal_mismatch:{artefact.payload['flow_key']}"]
        return []
