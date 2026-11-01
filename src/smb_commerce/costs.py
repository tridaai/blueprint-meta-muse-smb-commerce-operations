"""Cost analytics: the cost ledger, aggregated and reconciled.

Round 12 reads the per-run cost ledger the governed model port
has written since Round 1 and answers the operator's questions
from it — in code, from the recorded usage, priced by the
versioned rate card in ``modelport``:

- totals: calls, input tokens, output tokens, cost;
- breakdowns by profile, by tier, by model and by run — each
  bucket carrying its own calls/tokens/cost, and every breakdown
  summing exactly to the total;
- reconciliation: every recorded call's cost re-performed from
  the rate card, across all runs. A mismatch names the call.

One grain the ledger does not carry is time: call records have
no timestamp, so there is no by-day breakdown — runs are the
finest grain on the record, and the analytics say so rather than
inventing a calendar. Money is a string in the summary, per the
codebase convention, so Decimal values round-trip exactly.
"""

from __future__ import annotations

from decimal import Decimal

from .modelport import COST_PLACES, RATE_CARD_VERSION, call_cost
from .store import SqlStore


def _bucket() -> dict:
    return {"calls": 0, "input_tokens": 0, "output_tokens": 0,
            "_cost": Decimal("0")}


def _finish(bucket: dict) -> dict:
    return {
        "calls": bucket["calls"],
        "input_tokens": bucket["input_tokens"],
        "output_tokens": bucket["output_tokens"],
        "cost_usd": str(bucket["_cost"].quantize(COST_PLACES)),
    }


class CostAnalytics:
    def __init__(self, store: SqlStore):
        self._store = store

    def reconcile_all(self) -> list[str]:
        """Recompute every recorded cost from the rate card,
        across every run; the ledger must reconcile exactly,
        row for row."""
        problems: list[str] = []
        for record in self._store.list_model_calls():
            expected = call_cost(record.profile, record.tier,
                                 record.input_tokens,
                                 record.output_tokens)
            if record.cost != expected:
                problems.append(f"cost_mismatch:{record.call_id}")
        return problems

    def summary(self) -> dict:
        total = _bucket()
        by_profile: dict[str, dict] = {}
        by_tier: dict[str, dict] = {}
        by_model: dict[str, dict] = {}
        by_run: dict[str, dict] = {}
        for record in self._store.list_model_calls():
            for bucket in (
                total,
                by_profile.setdefault(record.profile, _bucket()),
                by_tier.setdefault(record.tier, _bucket()),
                by_model.setdefault(record.model, _bucket()),
                by_run.setdefault(record.run_id, _bucket()),
            ):
                bucket["calls"] += 1
                bucket["input_tokens"] += record.input_tokens
                bucket["output_tokens"] += record.output_tokens
                bucket["_cost"] += record.cost
        return {
            "rate_card_version": RATE_CARD_VERSION,
            "grain": "per run — call records carry no timestamp,"
                     " so no by-day breakdown exists on the record",
            "total": _finish(total),
            "by_profile": {k: _finish(v) for k, v in by_profile.items()},
            "by_tier": {k: _finish(v) for k, v in by_tier.items()},
            "by_model": {k: _finish(v) for k, v in by_model.items()},
            "by_run": {k: _finish(v) for k, v in by_run.items()},
            "reconcile_problems": self.reconcile_all(),
        }
