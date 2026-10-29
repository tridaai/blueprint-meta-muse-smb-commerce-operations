"""The model port: governed calls, tier stamps, cost arithmetic,
fail-closed hosted profile, and the local double of the Meta API."""

from __future__ import annotations

from decimal import Decimal

import pytest

from smb_commerce.modelport import (
    GovernedModelPort,
    MetaMuseModelPort,
    OfflineModelPort,
    ProfileNotConfigured,
    call_cost,
)


def test_offline_call_is_recorded_and_reconciles(store):
    port = GovernedModelPort(OfflineModelPort(tier="standard"), store)
    reply = port.complete("run-1", "system text", "Summarise the week please")
    assert reply.profile == "offline"
    calls = store.list_model_calls("run-1")
    assert len(calls) == 1
    assert calls[0].tier == "standard"
    assert calls[0].cost_usd == "0.000000"
    assert port.reconcile("run-1") == []
    assert port.run_total("run-1") == Decimal("0")


def test_meta_muse_fails_closed_without_key(monkeypatch):
    monkeypatch.delenv("META_MODEL_API_KEY", raising=False)
    with pytest.raises(ProfileNotConfigured):
        MetaMuseModelPort()


def _double(url, headers, body):
    assert url.endswith("/chat/completions")
    assert headers["Authorization"] == "Bearer test-key"
    assert body["model"] == "muse-spark"
    return {
        "choices": [{"message": {"content": "Draft: restock the throw."}}],
        "usage": {"prompt_tokens": 1_000_000, "completion_tokens": 1_000_000},
    }


def test_meta_muse_standard_tier_cost(store):
    inner = MetaMuseModelPort(api_key="test-key", tier="standard",
                              transport=_double)
    port = GovernedModelPort(inner, store)
    reply = port.complete("run-std", "s", "p")
    assert reply.text == "Draft: restock the throw."
    (call,) = store.list_model_calls("run-std")
    assert call.tier == "standard"
    # 1M in at 1.25 + 1M out at 4.25 = 5.50 exactly.
    assert call.cost_usd == "5.500000"
    assert port.reconcile("run-std") == []


def test_meta_muse_contributor_tier_is_cheaper_and_stamped(store):
    inner = MetaMuseModelPort(api_key="test-key", tier="contributor",
                              transport=_double)
    port = GovernedModelPort(inner, store)
    port.complete("run-ctr", "s", "p")
    (call,) = store.list_model_calls("run-ctr")
    assert call.tier == "contributor"
    # 1M in at 0.10 + 1M out at 0.20 = 0.30 exactly.
    assert call.cost_usd == "0.300000"


def test_call_cost_matches_the_published_price_table():
    assert call_cost("meta-muse", "standard", 2_000_000, 0) == Decimal("2.500000")
    assert call_cost("meta-muse", "contributor", 0, 500_000) == Decimal("0.100000")
    assert call_cost("offline", "standard", 10**9, 10**9) == Decimal("0.000000")
