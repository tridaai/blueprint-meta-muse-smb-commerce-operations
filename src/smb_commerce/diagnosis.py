"""Diagnosis packs I: stockout risk and ad-decay findings.

Law 5 lives here. Both diagnoses read the head ledger artefact's own
figures (route 1) and are re-performed from the stored facts by a
second route before a finding set may exist (route 2). One wrong
figure — or a ledger that no longer matches the fact set — refuses
the whole finding set: a refusal artefact is recorded and
``DiagnosisRefused`` is raised. Nothing partial is ever stored as a
finding.

The rules are data, in ``DIAGNOSIS_PACK`` below, versioned as a pack:

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

DIAGNOSIS_PACK = {
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

STOCKOUT_KIND = "diagnosis_stockout"
AD_DECAY_KIND = "diagnosis_ad_decay"
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


def _classify_stockout(rows: list[dict]) -> tuple[list[dict], list[dict]]:
    """SKU evaluations -> (findings, no_data). Shared shaping: the two
    routes differ in how the rows are derived, never in how they are
    judged, so a disagreement is a computation disagreement."""
    rules = DIAGNOSIS_PACK["stockout"]
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


def _stockout_route1(ledger: Artefact, facts: list[Fact]) -> tuple[list, list]:
    """From the head ledger artefact's own figures; velocity and the
    no-data determination read the facts, which the ledger does not
    carry."""
    rules = DIAGNOSIS_PACK["stockout"]
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
    return _classify_stockout(rows)


def _stockout_route2(facts: list[Fact], as_of: date) -> tuple[list, list]:
    """From the facts alone, grouped by SKU in key order: on-hand from
    the latest inventory position, cover recomputed from the window."""
    rules = DIAGNOSIS_PACK["stockout"]
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
    return _classify_stockout(rows)


# -- ad decay -------------------------------------------------------------


def _classify_decay(series: dict[str, dict]) -> tuple[list[dict], list[dict]]:
    """Per-set weekly series -> (findings, unjudged). Shared shaping,
    as with stockout: routes differ in derivation, not in judgement."""
    rules = DIAGNOSIS_PACK["ad_decay"]
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


def _decay_route1(ledger: Artefact) -> tuple[list, list]:
    series = {
        row["ad_set"]: {"total_spend": row["spend"], "weeks": row["weekly"]}
        for row in ledger.payload["ad_sets"]
    }
    return _classify_decay(series)


def _decay_route2(facts: list[Fact]) -> tuple[list, list]:
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
    return _classify_decay(series)


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
        findings, no_data = _stockout_route1(ledger, facts)
        problems = self._ledger_currency_problems(ledger)
        expected_findings, expected_no_data = _stockout_route2(
            facts, date.fromisoformat(ledger.payload["as_of"])
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
        findings, unjudged = _decay_route1(ledger)
        problems = self._ledger_currency_problems(ledger)
        expected_findings, expected_unjudged = _decay_route2(facts)
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

    def verify(self, artefact: Artefact) -> list[str]:
        """Re-perform a stored finding set from the facts by route 2.

        A refusal artefact is a record of a refusal, not a finding
        set; it carries nothing to re-perform and verifies clean."""
        if artefact.kind == REFUSAL_KIND:
            return []
        ledger = self._store.get_artefact(artefact.payload["ledger_artefact_id"])
        if ledger is None:
            return ["ledger_missing"]
        problems = self._ledger_currency_problems(ledger)
        facts = self._store.list_facts()
        if artefact.kind == STOCKOUT_KIND:
            findings, no_data = _stockout_route2(
                facts, date.fromisoformat(ledger.payload["as_of"])
            )
            if canonical_json(findings) != canonical_json(artefact.payload["findings"]):
                problems.append("stockout_finding_mismatch")
            if canonical_json(no_data) != canonical_json(artefact.payload["no_data"]):
                problems.append("stockout_no_data_mismatch")
        elif artefact.kind == AD_DECAY_KIND:
            findings, unjudged = _decay_route2(facts)
            if canonical_json(findings) != canonical_json(artefact.payload["findings"]):
                problems.append("ad_decay_finding_mismatch")
            if canonical_json(unjudged) != canonical_json(artefact.payload["unjudged"]):
                problems.append("ad_decay_unjudged_mismatch")
        else:
            problems.append(f"not_a_diagnosis:{artefact.kind}")
        return problems
