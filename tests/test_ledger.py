"""The ledger against hand-computed planted truth.

The expectations below are arithmetic on the generator's stated
constants, worked out by hand:

- hero DIN: 120 units/wk x $189.00 x 52 wk = $1,179,360.00 gross;
  refunds floor(120 x 200 / 10000) = 2 units/wk = $378.00/wk,
  $19,656.00 over the year.
- fast mover THR: 210 on hand at close; trailing 28 days are weeks
  48-51 at 70 units/wk = 280 units, 10 a day, so 21.00 cover days and
  cover-out on 2026-01-25.
- AD-SOCIAL week 0 revenue $2,000.00 on $500.00 spend (ROAS 4.0000),
  week 51 revenue 2000 x 0.94^51.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from smb_commerce import generator


def test_counts_match_manifest(service):
    assert service.dataset_counts() == generator.manifest_counts()


def test_hero_sku_figures(service):
    artefact = service.ledger.compute()
    skus = {r["sku"]: r for r in artefact.payload["skus"]}
    hero = skus["HW-DIN-001"]
    assert hero["gross_revenue"] == "1179360.00"
    assert hero["refunds"] == "19656.00"
    assert hero["units_sold"] == 120 * 52
    assert hero["units_refunded"] == 2 * 52


def test_fast_mover_cover(service):
    artefact = service.ledger.compute()
    skus = {r["sku"]: r for r in artefact.payload["skus"]}
    thr = skus[generator.FAST_MOVER_SKU]
    assert thr["on_hand"] == 210
    assert thr["cover_days"] == "21.00"
    assert thr["cover_out_date"] == "2026-01-25"


def test_decaying_ad_set(service):
    artefact = service.ledger.compute()
    ad = {r["ad_set"]: r for r in artefact.payload["ad_sets"]}
    social = ad[generator.DECAY_SET]
    assert social["spend"] == "26000.00"
    assert social["weekly"][0]["attributed_revenue"] == "2000.00"
    assert social["weekly"][0]["roas"] == "4.0000"
    expected_last = (
        generator.DECAY_BASE_REVENUE * generator.DECAY_FACTOR ** 51
    ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    assert social["weekly"][51]["attributed_revenue"] == str(expected_last)
    # Search is steady: ROAS 3.5 in every week.
    search = ad["AD-SEARCH"]
    assert all(w["roas"] == "3.5000" for w in search["weekly"])


def test_duplicate_charge_and_missing_flow_are_in_the_facts(service):
    expenses = [
        f for f in service.store.list_facts("expense")
        if f.payload["vendor"] == generator.DUPLICATE_VENDOR
        and f.payload["date"] in ("2025-11-14", "2025-11-15")
    ]
    assert sorted(f.payload["date"] for f in expenses) == [
        "2025-11-14", "2025-11-15"]
    assert all(f.payload["amount"] == "349.00" for f in expenses)
    flows = {f.payload["flow_key"]
             for f in service.store.list_facts("email_flow")}
    assert generator.MISSING_FLOW not in flows
    assert flows == {k for k, _n, _s in generator.EMAIL_FLOWS}


def test_ledger_verifies_and_tampering_is_caught(service):
    artefact = service.ledger.compute()
    assert service.ledger.verify(artefact) == []
    # Alter a stored fact behind the store's back: the fingerprint and
    # the re-performed figures must both give the tampering away.
    fact = service.store.list_facts("order")[0]
    conn = service.store._conn
    import json
    payload = dict(fact.payload, amount="999999.00")
    conn.execute(
        "UPDATE facts SET payload = ? WHERE fact_id = ?",
        (json.dumps(payload), fact.fact_id),
    )
    conn.commit()
    problems = service.ledger.verify(artefact)
    assert any(p.startswith("fact_content_hash_mismatch") for p in problems)
    assert "ledger_figure_mismatch" in problems
