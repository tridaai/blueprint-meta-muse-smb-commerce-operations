"""Round 12: cost analytics over the recorded ledger — totals and
breakdowns computed in code from recorded usage, priced by the
versioned rate card, reconciled row for row — plus the new API
and CLI surfaces for costs, the profile report and contexts."""

from __future__ import annotations

from decimal import Decimal

from fastapi.testclient import TestClient

from smb_commerce import api as api_module
from smb_commerce import cli as cli_module
from smb_commerce.costs import CostAnalytics
from smb_commerce.modelport import GovernedModelPort, MetaMuseModelPort
from smb_commerce.service import CommerceService
from conftest import make_settings


def _double(url, headers, body):
    return {
        "choices": [{"message": {"content": "ok"}}],
        "usage": {"prompt_tokens": 1_000_000,
                  "completion_tokens": 1_000_000},
    }


def _seed_calls(store):
    standard = GovernedModelPort(
        MetaMuseModelPort(api_key="k", tier="standard",
                          transport=_double), store)
    standard.complete("run-a", "s", "p")
    contributor = GovernedModelPort(
        MetaMuseModelPort(api_key="k", tier="contributor",
                          transport=_double), store)
    contributor.complete("run-b", "s", "p")
    contributor.complete("run-b", "s", "p again")


def test_summary_totals_and_breakdowns_reconcile(store):
    _seed_calls(store)
    summary = CostAnalytics(store).summary()
    assert summary["rate_card_version"] == "2026.11.1"
    total = summary["total"]
    assert total["calls"] == 3
    assert total["input_tokens"] == 3_000_000
    assert total["cost_usd"] == "6.100000"  # 5.50 + 0.30 + 0.30
    assert summary["by_tier"]["standard"]["cost_usd"] == "5.500000"
    assert summary["by_tier"]["contributor"]["cost_usd"] == "0.600000"
    assert summary["by_tier"]["contributor"]["calls"] == 2
    assert summary["by_profile"]["meta-muse"]["calls"] == 3
    assert summary["by_model"]["muse-spark"]["cost_usd"] == "6.100000"
    assert summary["by_run"]["run-a"]["cost_usd"] == "5.500000"
    assert summary["by_run"]["run-b"]["calls"] == 2
    # Every breakdown sums exactly to the total.
    for breakdown in ("by_profile", "by_tier", "by_model", "by_run"):
        summed = sum(
            Decimal(bucket["cost_usd"])
            for bucket in summary[breakdown].values()
        )
        assert summed == Decimal(total["cost_usd"])
    assert summary["reconcile_problems"] == []


def test_reconcile_all_names_a_tampered_row(store):
    _seed_calls(store)
    analytics = CostAnalytics(store)
    assert analytics.reconcile_all() == []
    (call,) = store.list_model_calls("run-a")
    store._conn.execute(
        "UPDATE model_calls SET cost_usd = '9.999999' WHERE call_id = ?",
        (call.call_id,),
    )
    store._conn.commit()
    assert analytics.reconcile_all() == [f"cost_mismatch:{call.call_id}"]


def test_summary_of_an_empty_ledger_is_zero(store):
    summary = CostAnalytics(store).summary()
    assert summary["total"] == {
        "calls": 0, "input_tokens": 0, "output_tokens": 0,
        "cost_usd": "0.000000",
    }
    assert summary["by_run"] == {}
    assert summary["reconcile_problems"] == []


def test_api_costs_profile_and_contexts(tmp_path, monkeypatch):
    settings = make_settings(tmp_path)
    monkeypatch.setattr(api_module, "_service", None)
    monkeypatch.setattr(api_module, "load_settings", lambda: settings)
    client = TestClient(api_module.app, headers={
        "X-SMB-Actor": "Maya Chen", "X-SMB-Role": "owner"})
    service = api_module.get_service()
    service.draft("run-1", "system", "prompt")
    costs = client.get("/costs").json()
    assert costs["total"]["calls"] == 1
    assert costs["total"]["cost_usd"] == "0.000000"
    assert costs["reconcile_problems"] == []
    profile = client.get("/model-profile").json()
    assert profile["profile"] == "offline"
    assert profile["status"] == "ready"
    assert profile["live_call_made"] is False
    contexts = client.get("/contexts").json()
    assert contexts["count"] == 1
    assert contexts["contexts"][0]["payload"]["run_id"] == "run-1"


def test_cli_costs_profile_and_contexts(tmp_path, monkeypatch, capsys):
    service = CommerceService(make_settings(tmp_path))
    service.draft("run-1", "system", "prompt")
    monkeypatch.setattr(cli_module, "CommerceService", lambda: service)
    assert cli_module.main(["model-profile"]) == 0
    out = capsys.readouterr().out
    assert "profile offline" in out
    assert "status ready" in out
    assert "live_call_made False" in out
    assert cli_module.main(["costs"]) == 0
    out = capsys.readouterr().out
    assert "rate card 2026.11.1" in out
    assert "reconcile problems []" in out
    assert cli_module.main(["contexts"]) == 0
    out = capsys.readouterr().out
    assert "1 context records" in out
    assert "compacted False" in out
