"""The dataset generator: gold-by-construction.

Builds one year of Hartwell Home Goods facts — a fictional DTC
home-goods retailer — entirely from the stated constants below, and
writes them ONLY through the ingest port, so every fact carries
provenance and Law 3 grades the generator like any other producer.

Planted patterns, each hand-computable from these constants:

- HERO: the Stoneware Dinner Set sells a flat 120 units a week at
  $189.00 — $22,680.00 of revenue a week, the store's engine.
- FAST MOVER: the Washed Linen Throw sells min(30 + week, 70) units a
  week. Restock receipts stop after week 39, so cover drains to
  exactly 210 units on hand against a trailing-28-day velocity of
  280 units (10 a day): 21.0 cover days, out on 2026-01-25.
- DECAYING AD SET: Social — Seasonal Decor spends $500.00 a week and
  its attributed revenue is $2,000.00 x 0.94^week, week over week.
- DUPLICATE CHARGE: PackShip Co bills $349.00 on the 14th of every
  month, and the same charge appears again on 2025-11-15.
- MISSING FLOW: the email platform defines five standard flows. The
  winback flow is not among them.
- LISTING IMAGES (Round 16, at the end of this module): the mirror
  has no image, the candle duo's image is below the listing pack's
  size minimums, the vase's image shows the candle duo, and the
  towel set's image is PNG bytes named as a JPEG.

Period: 52 weeks, Monday 2025-01-06 to Sunday 2026-01-04. The as-of
date of the dataset is its final day, 2026-01-04.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from .ingest import IngestPort, IngestReport

CENT = Decimal("0.01")
START = date(2025, 1, 6)  # a Monday
WEEKS = 52
END = START + timedelta(days=WEEKS * 7 - 1)  # 2026-01-04

SOURCE_ORDERS = "hartwell-storefront"
SOURCE_INVENTORY = "hartwell-warehouse"
SOURCE_ADS = "hartwell-ads-manager"
SOURCE_EMAIL = "hartwell-email-platform"
SOURCE_FINANCE = "hartwell-books"
SOURCE_STUDIO = "hartwell-studio"


def money(value: Decimal) -> str:
    return str(value.quantize(CENT, rounding=ROUND_HALF_UP))


def week_start(week: int) -> date:
    return START + timedelta(days=week * 7)


@dataclass(frozen=True)
class SkuSpec:
    sku: str
    name: str
    unit_price: Decimal
    unit_cost: Decimal
    refund_bps: int  # refunded units per 10,000 sold, floored per week
    target_end_on_hand: int
    # The catalogue's declared product class for the SKU — what
    # its listing image is declared to show (Round 16). The
    # listing pack judges the vision judgement against this.
    product_class: str


def weekly_units(sku: str, week: int) -> int:
    """Gross units sold, the stated sales curve per SKU."""
    if sku == "HW-DIN-001":
        return 120
    if sku == "HW-THR-002":
        return min(30 + week, 70)
    if sku == "HW-CAN-003":
        return 85
    if sku == "HW-VAS-004":
        return 40
    if sku == "HW-TOW-005":
        return 65
    if sku == "HW-MIR-006":
        return 22
    raise KeyError(sku)


SKUS: tuple[SkuSpec, ...] = (
    SkuSpec("HW-DIN-001", "Stoneware Dinner Set, 16-piece",
            Decimal("189.00"), Decimal("82.00"), 200, 480,
            "stoneware_dinner_set"),
    SkuSpec("HW-THR-002", "Washed Linen Throw",
            Decimal("64.00"), Decimal("27.00"), 300, 210,
            "washed_linen_throw"),
    SkuSpec("HW-CAN-003", "Soy Candle Duo",
            Decimal("38.00"), Decimal("13.50"), 150, 340,
            "soy_candle_duo"),
    SkuSpec("HW-VAS-004", "Ceramic Vase, Large",
            Decimal("92.00"), Decimal("44.00"), 500, 160,
            "ceramic_vase"),
    SkuSpec("HW-TOW-005", "Turkish Cotton Towel Set",
            Decimal("56.00"), Decimal("24.00"), 250, 390,
            "turkish_cotton_towel_set"),
    SkuSpec("HW-MIR-006", "Arched Wall Mirror",
            Decimal("148.00"), Decimal("71.00"), 400, 110,
            "arched_wall_mirror"),
)

FAST_MOVER_SKU = "HW-THR-002"
FAST_MOVER_LAST_RECEIPT_WEEK = 39  # receipts land at weeks 0, 4, 8 ... to here

AD_SETS: tuple[tuple[str, str, Decimal], ...] = (
    ("AD-SEARCH", "Search — Dinnerware Core", Decimal("400.00")),
    ("AD-SOCIAL", "Social — Seasonal Decor", Decimal("500.00")),
    ("AD-RETGT", "Retargeting — Site Visitors", Decimal("250.00")),
)
DECAY_SET = "AD-SOCIAL"
DECAY_BASE_REVENUE = Decimal("2000.00")
DECAY_FACTOR = Decimal("0.94")
STEADY_AD_REVENUE = {
    "AD-SEARCH": Decimal("1400.00"),
    "AD-RETGT": Decimal("500.00"),
}

EMAIL_FLOWS: tuple[tuple[str, str, int], ...] = (
    ("welcome_series", "Welcome Series", 3),
    ("abandoned_cart", "Abandoned Cart", 2),
    ("post_purchase", "Post-Purchase", 2),
    ("browse_abandonment", "Browse Abandonment", 1),
    ("replenishment", "Replenishment Reminder", 1),
)
MISSING_FLOW = "winback"

EXPENSE_VENDORS: tuple[tuple[str, str, Decimal, int], ...] = (
    # vendor, category, amount, day of month
    ("CloudHost", "software", Decimal("189.00"), 1),
    ("Hartwell Studio Lease", "rent", Decimal("2400.00"), 1),
    ("Northgate Logistics", "freight", Decimal("1250.00"), 3),
    ("PackShip Co", "packaging", Decimal("349.00"), 14),
)
DUPLICATE_VENDOR = "PackShip Co"
DUPLICATE_DATE = date(2025, 11, 15)
DUPLICATE_AMOUNT = Decimal("349.00")


def _daily_split(weekly_total: int) -> list[int]:
    base, remainder = divmod(weekly_total, 7)
    return [base + (1 if day < remainder else 0) for day in range(7)]


def _daily_money_split(weekly_total: Decimal) -> list[Decimal]:
    cents = int((weekly_total * 100).to_integral_value())
    base, remainder = divmod(cents, 7)
    return [
        (Decimal(base + (1 if day < remainder else 0)) / 100).quantize(CENT)
        for day in range(7)
    ]


def net_units(spec: SkuSpec, week: int) -> int:
    gross = weekly_units(spec.sku, week)
    return gross - (gross * spec.refund_bps) // 10000


def _receipts(spec: SkuSpec) -> dict[int, int]:
    """Restock landings at the start of weeks 0, 4, 8 ... each covering
    the coming four weeks of net sales. The fast mover's landings stop
    after week 39 — the planted decision that drains its cover."""
    receipts: dict[int, int] = {}
    for start_week in range(0, WEEKS, 4):
        if spec.sku == FAST_MOVER_SKU and start_week > FAST_MOVER_LAST_RECEIPT_WEEK:
            continue
        receipts[start_week] = sum(
            net_units(spec, w) for w in range(start_week, min(start_week + 4, WEEKS))
        )
    return receipts


def _opening_stock(spec: SkuSpec, receipts: dict[int, int]) -> int:
    total_net = sum(net_units(spec, w) for w in range(WEEKS))
    return spec.target_end_on_hand + total_net - sum(receipts.values())


def build_fact_items() -> list[dict]:
    """The whole dataset as ingest items, in a deterministic order."""
    items: list[dict] = []

    for spec in SKUS:
        receipts = _receipts(spec)
        on_hand = _opening_stock(spec, receipts)
        for week in range(WEEKS):
            monday = week_start(week)
            on_hand += receipts.get(week, 0)
            gross = weekly_units(spec.sku, week)
            refund_qty = (gross * spec.refund_bps) // 10000
            for day_offset, qty in enumerate(_daily_split(gross)):
                if qty == 0:
                    continue
                day = monday + timedelta(days=day_offset)
                items.append({
                    "kind": "order",
                    "natural_key": f"order:{spec.sku}:{day.isoformat()}",
                    "payload": {
                        "order_id": f"ORD-{spec.sku[-3:]}-{day.strftime('%Y%m%d')}",
                        "sku": spec.sku,
                        "date": day.isoformat(),
                        "qty": qty,
                        "unit_price": money(spec.unit_price),
                        "amount": money(spec.unit_price * qty),
                    },
                    "source_system": SOURCE_ORDERS,
                    "as_of": day.isoformat(),
                })
            items.append({
                    "kind": "refund",
                    "natural_key": f"refund:{spec.sku}:{monday.isoformat()}",
                    "payload": {
                        "sku": spec.sku,
                        "week_start": monday.isoformat(),
                        "qty": refund_qty,
                        "amount": money(spec.unit_price * refund_qty),
                    },
                    "source_system": SOURCE_ORDERS,
                    "as_of": (monday + timedelta(days=6)).isoformat(),
                })
            on_hand -= gross - refund_qty
            items.append({
                "kind": "inventory",
                "natural_key": f"inventory:{spec.sku}:{monday.isoformat()}",
                "payload": {
                    "sku": spec.sku,
                    "week_start": monday.isoformat(),
                    "on_hand": on_hand,
                },
                "source_system": SOURCE_INVENTORY,
                "as_of": (monday + timedelta(days=6)).isoformat(),
            })

    for ad_set, _name, weekly_spend in AD_SETS:
        for week in range(WEEKS):
            monday = week_start(week)
            if ad_set == DECAY_SET:
                weekly_revenue = (
                    DECAY_BASE_REVENUE * (DECAY_FACTOR ** week)
                ).quantize(CENT, rounding=ROUND_HALF_UP)
            else:
                weekly_revenue = STEADY_AD_REVENUE[ad_set]
            spends = _daily_money_split(weekly_spend)
            revenues = _daily_money_split(weekly_revenue)
            for day_offset in range(7):
                day = monday + timedelta(days=day_offset)
                items.append({
                    "kind": "ad_daily",
                    "natural_key": f"ad:{ad_set}:{day.isoformat()}",
                    "payload": {
                        "ad_set": ad_set,
                        "date": day.isoformat(),
                        "spend": money(spends[day_offset]),
                        "attributed_revenue": money(revenues[day_offset]),
                    },
                    "source_system": SOURCE_ADS,
                    "as_of": day.isoformat(),
                })

    for flow_key, name, steps in EMAIL_FLOWS:
        items.append({
            "kind": "email_flow",
            "natural_key": f"flow:{flow_key}",
            "payload": {
                "flow_key": flow_key,
                "name": name,
                "status": "active",
                "steps": steps,
            },
            "source_system": SOURCE_EMAIL,
            "as_of": END.isoformat(),
        })

    for month_offset in range(12):
        year = 2025 + (month_offset // 12)
        month_num = (month_offset % 12) + 1
        for vendor, category, amount, day_of_month in EXPENSE_VENDORS:
            day = date(year, month_num, day_of_month)
            items.append({
                "kind": "expense",
                "natural_key": f"expense:{vendor}:{day.isoformat()}:{money(amount)}",
                "payload": {
                    "vendor": vendor,
                    "date": day.isoformat(),
                    "category": category,
                    "amount": money(amount),
                },
                "source_system": SOURCE_FINANCE,
                "as_of": day.isoformat(),
            })
    items.append({
        "kind": "expense",
        "natural_key": f"expense:{DUPLICATE_VENDOR}:{DUPLICATE_DATE.isoformat()}:{money(DUPLICATE_AMOUNT)}",
        "payload": {
            "vendor": DUPLICATE_VENDOR,
            "date": DUPLICATE_DATE.isoformat(),
            "category": "packaging",
            "amount": money(DUPLICATE_AMOUNT),
        },
        "source_system": SOURCE_FINANCE,
        "as_of": DUPLICATE_DATE.isoformat(),
    })
    return items


def generate(port: IngestPort) -> IngestReport:
    """Write the dataset through the ingest port; refuse nothing."""
    report = port.ingest_many(build_fact_items())
    if report.refused:
        reasons = sorted({r.reason for r in report.refusals})
        raise RuntimeError(f"generator facts refused at the port: {reasons}")
    return report


def build_batches() -> list[dict]:
    """The whole dataset as v2 exchange batches (Round 11).

    One envelope per contract, records in the dataset's own order,
    the envelope as-of the dataset's final day — the local doubles'
    conforming deliveries. Every item in ``build_fact_items``
    belongs to exactly one contract's stated pairs; an item that
    belongs to none is a generator bug and raises here rather than
    silently dropping a fact."""
    from .connectors import CONTRACT_VERSION, contract_for_pair

    grouped: dict[str, list[dict]] = {}
    for item in build_fact_items():
        contract = contract_for_pair(item["kind"], item["source_system"])
        if contract is None:
            raise RuntimeError(
                f"no exchange contract accepts "
                f"({item['kind']}, {item['source_system']})")
        grouped.setdefault(contract.name, []).append(item)
    batches: list[dict] = []
    for name, records in grouped.items():
        from .connectors import CONTRACTS

        contract = CONTRACTS[name]
        batches.append({
            "contract": contract.name,
            "contract_version": CONTRACT_VERSION,
            "source_system": contract.source_system,
            "as_of": END.isoformat(),
            "records": records,
        })
    return batches


def generate_batches(port) -> IngestReport:
    """Write the dataset through the connector port, batch by batch.

    The aggregate reads as an ingest report: a batch quarantined
    during generation is a generator bug (its facts are built to
    conform), and raises like a refusal did in Round 1."""
    from .ingest import IngestReport as _Report

    aggregate = _Report()
    for envelope in build_batches():
        report = port.exchange(envelope)
        if report.status != "accepted":
            codes = sorted({v.code for v in report.violations})
            raise RuntimeError(
                f"generator batch quarantined under contract "
                f"{envelope['contract']}: {codes}")
        aggregate.accepted += report.ingest.accepted
        aggregate.duplicates += report.ingest.duplicates
        aggregate.refusals.extend(report.ingest.refusals)
    if aggregate.refused:
        reasons = sorted({r.reason for r in aggregate.refusals})
        raise RuntimeError(f"generator facts refused at the port: {reasons}")
    return aggregate


def manifest_counts() -> dict[str, int]:
    """Expected fact counts per kind, derived from the constants."""
    counts: dict[str, int] = {}
    for item in build_fact_items():
        counts[item["kind"]] = counts.get(item["kind"], 0) + 1
    return counts


# -- product images (Round 16) -------------------------------------------
#
# The catalogue's listing images, pressed in code by
# ``images.write_png`` — no external assets. The planted truth,
# one case per listing-pack rule:
#
# - HEALTHY: the dinner set and the throw carry full-size PNGs
#   whose embedded subject is the SKU's own declared class.
# - SMALL IMAGE: the candle duo's image is 240x180, below the
#   pack's 800x800 minimums.
# - VISION MISMATCH: the vase's image is a healthy PNG whose
#   embedded subject is the candle duo's class — the wrong
#   product's photograph on the vase's listing.
# - FORMAT MISMATCH: the towel set's image is PNG bytes in a
#   file named ``.jpg`` — the filename claims JPEG, the bytes
#   measure PNG.
# - NO IMAGE: the mirror has no image at all.

IMAGE_SIZE = (1200, 900)
SMALL_IMAGE_SIZE = (240, 180)
SMALL_IMAGE_SKU = "HW-CAN-003"
VISION_MISMATCH_SKU = "HW-VAS-004"
VISION_MISMATCH_SUBJECT = "soy_candle_duo"
FORMAT_MISMATCH_SKU = "HW-TOW-005"
IMAGELESS_SKU = "HW-MIR-006"

_IMAGE_RGB: dict[str, tuple[int, int, int]] = {
    "HW-DIN-001": (196, 120, 86),
    "HW-THR-002": (150, 160, 140),
    "HW-CAN-003": (210, 180, 90),
    "HW-VAS-004": (120, 140, 180),
    "HW-TOW-005": (170, 170, 170),
}


def build_image_items() -> list[dict]:
    """The planted listing images as image-intake items, in
    catalogue order. The mirror is absent by construction."""
    from .images import write_png

    items: list[dict] = []
    for spec in SKUS:
        if spec.sku == IMAGELESS_SKU:
            continue
        width, height = (
            SMALL_IMAGE_SIZE if spec.sku == SMALL_IMAGE_SKU
            else IMAGE_SIZE
        )
        subject = (
            VISION_MISMATCH_SUBJECT if spec.sku == VISION_MISMATCH_SKU
            else spec.product_class
        )
        filename = (
            f"{spec.sku.lower()}-hero.jpg"
            if spec.sku == FORMAT_MISMATCH_SKU
            else f"{spec.sku.lower()}-hero.png"
        )
        items.append({
            "sku": spec.sku,
            "filename": filename,
            "data": write_png(width, height, _IMAGE_RGB[spec.sku],
                              subject_class=subject),
            "declared_class": spec.product_class,
            "source_system": SOURCE_STUDIO,
            "as_of": END.isoformat(),
        })
    return items


def generate_images(port) -> IngestReport:
    """Write the planted images through the image intake port;
    refuse nothing — a refusal is a generator bug, as with the
    fact dataset."""
    report = IngestReport()
    for item in build_image_items():
        outcome = port.ingest_image(**item)
        if outcome.status == "accepted":
            report.accepted += 1
        elif outcome.status == "duplicate":
            report.duplicates += 1
        else:
            report.refusals.append(outcome)
    if report.refused:
        reasons = sorted({r.reason for r in report.refusals})
        raise RuntimeError(f"generator images refused at the port: {reasons}")
    return report
