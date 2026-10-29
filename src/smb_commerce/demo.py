"""The demo: generate Hartwell Home Goods, compute the ledger, and
walk the operating picture — including the planted patterns, shown in
the computed figures themselves.

Runs against a throwaway store in a temp directory: no disk state in
the repo, no network, no keys. The model call at the end uses the
offline profile and lands in the cost ledger like any other call.
"""

from __future__ import annotations

import tempfile
from decimal import Decimal
from pathlib import Path

from . import generator
from .config import Settings, load_settings
from .service import CommerceService


def build_demo_service(tmp: Path) -> CommerceService:
    base = load_settings()
    settings = Settings(
        database_url="",
        sqlite_path=tmp / "demo.db",
        migrations_dir=base.migrations_dir,
        object_dir=tmp / "objects",
        s3_endpoint="",
        s3_bucket=base.s3_bucket,
        model_profile="offline",
        model_tier=base.model_tier,
        log_level=base.log_level,
    )
    return CommerceService(settings)


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="smb-commerce-demo-") as tmp:
        service = build_demo_service(Path(tmp))
        report = service.generate_dataset()
        counts = service.dataset_counts()
        print("== Hartwell Home Goods — dataset ==")
        print(f"period {generator.START.isoformat()} .. {generator.END.isoformat()}"
              f"  facts accepted {report.accepted}  refused {report.refused}")
        for kind in sorted(counts):
            print(f"  {kind:<12} {counts[kind]}")

        artefact = service.ledger.compute()
        problems = service.ledger.verify(artefact)
        p = artefact.payload
        print("\n== Operating picture (computed in code, Decimal) ==")
        print(f"  gross revenue  {p['gross_revenue']:>12}")
        print(f"  refunds        {p['refunds_total']:>12}  rate {p['refund_rate']}")
        print(f"  net revenue    {p['net_revenue']:>12}")
        print(f"  COGS           {p['cogs']:>12}")
        print(f"  gross margin   {p['gross_margin']:>12}")
        print(f"  ledger {artefact.artefact_id}  verified {not problems}")

        print("\n== Inventory cover by SKU ==")
        for row in p["skus"]:
            print(f"  {row['sku']} {row['name']:<30} on hand {row['on_hand']:>5}"
                  f"  cover {row['cover_days']:>6} days  out {row['cover_out_date']}")
        fastest = min(p["skus"], key=lambda r: Decimal(r["cover_days"]))
        print(f"  -> tightest cover: {fastest['sku']} runs out "
              f"{fastest['cover_out_date']} ({fastest['cover_days']} days)")

        print("\n== Ad sets ==")
        for row in p["ad_sets"]:
            first, last = row["weekly"][0], row["weekly"][-1]
            print(f"  {row['ad_set']:<10} spend {row['spend']:>9}"
                  f"  revenue {row['attributed_revenue']:>10}  ROAS {row['roas']}")
            print(f"             week 1 ROAS {first['roas']} ->"
                  f" week {len(row['weekly'])} ROAS {last['roas']}")

        print("\n== Expense facts — PackShip Co at $349.00 ==")
        for fact in service.store.list_facts("expense"):
            if fact.payload["vendor"] == generator.DUPLICATE_VENDOR:
                print(f"  {fact.payload['date']}  {fact.payload['vendor']}"
                      f"  {fact.payload['amount']}  ({fact.fact_id})")

        print("\n== Email flows defined on the platform ==")
        defined = [f.payload["flow_key"]
                   for f in service.store.list_facts("email_flow")]
        for key in defined:
            print(f"  {key}")
        print(f"  -> '{generator.MISSING_FLOW}' is not defined")

        print("\n== Model port (offline profile, governed) ==")
        reply = service.draft(
            "demo-run",
            "You draft operations summaries from computed figures only.",
            f"Net revenue {p['net_revenue']}, gross margin "
            f"{p['gross_margin']}, tightest cover {fastest['sku']} at "
            f"{fastest['cover_days']} days.",
        )
        calls = service.store.list_model_calls("demo-run")
        total = service.model.run_total("demo-run")
        print(f"  profile {reply.profile}  model {reply.model}  tier {reply.tier}")
        print(f"  calls recorded {len(calls)}  run total ${total}"
              f"  reconcile problems {service.model.reconcile('demo-run')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
