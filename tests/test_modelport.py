"""The model port: governed calls, tier stamps, cost arithmetic,
fail-closed hosted profile, and the local double of the Meta API.
Round 12 adds the versioned rate card (with the published
cached-input rates), the profile report and the stub probe."""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

import pytest

from smb_commerce.modelport import (
    PRICE_TABLE,
    RATE_CARD,
    RATE_CARD_VERSION,
    GovernedModelPort,
    MetaMuseModelPort,
    OfflineModelPort,
    ProfileNotConfigured,
    call_cost,
    probe_meta_muse,
    profile_report,
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


def test_rate_card_carries_the_published_rates():
    assert RATE_CARD_VERSION == "2026.11.1"
    standard = RATE_CARD["meta-muse"]["standard"]
    assert standard["input"] == Decimal("1.25")
    assert standard["cached_input"] == Decimal("0.15")
    assert standard["output"] == Decimal("4.25")
    contributor = RATE_CARD["meta-muse"]["contributor"]
    assert contributor["input"] == Decimal("0.10")
    assert contributor["cached_input"] == Decimal("0.002")
    assert contributor["output"] == Decimal("0.20")
    # The (input, output) view the ledger prices by is the card.
    assert PRICE_TABLE["meta-muse"]["standard"] == (
        Decimal("1.25"), Decimal("4.25"))
    assert PRICE_TABLE["meta-muse"]["contributor"] == (
        Decimal("0.10"), Decimal("0.20"))


def test_call_cost_prices_the_cached_portion_at_the_cached_rate():
    # A fully cached 1M input on Standard: 1M x 0.15, not 1M x 1.25.
    assert call_cost("meta-muse", "standard", 1_000_000, 0,
                     cached_input_tokens=1_000_000) == Decimal("0.150000")
    # Half cached: 500k x 1.25 + 500k x 0.15 + 1M out x 4.25.
    assert call_cost("meta-muse", "standard", 1_000_000, 1_000_000,
                     cached_input_tokens=500_000) == Decimal("4.950000")
    assert call_cost("meta-muse", "contributor", 1_000_000, 0,
                     cached_input_tokens=1_000_000) == Decimal("0.002000")
    # No cached portion: the graded identities stand exactly.
    assert call_cost("meta-muse", "standard",
                     1_000_000, 1_000_000) == Decimal("5.500000")
    assert call_cost("meta-muse", "contributor",
                     1_000_000, 1_000_000) == Decimal("0.300000")


def _settings(profile, tier="standard"):
    return SimpleNamespace(model_profile=profile, model_tier=tier)


def test_profile_report_offline_is_ready():
    report = profile_report(_settings("offline"))
    assert report["status"] == "ready"
    assert report["configured"] is True
    assert report["key_required"] is False
    assert report["model"] == "offline-deterministic"
    assert report["rate_card_version"] == RATE_CARD_VERSION
    assert report["live_call_made"] is False


def test_profile_report_meta_muse_without_key_fails_closed(monkeypatch):
    monkeypatch.delenv("META_MODEL_API_KEY", raising=False)
    monkeypatch.delenv("META_MUSE_MODEL", raising=False)
    monkeypatch.delenv("META_MODEL_API_BASE_URL", raising=False)
    report = profile_report(_settings("meta-muse"))
    assert report["status"] == "not_configured"
    assert report["configured"] is False
    assert report["fail_closed"] is True
    assert report["key_present"] is False
    # The default model id is reported as what it is: a built-in,
    # configurable default that no live account has confirmed.
    assert report["model"] == "muse-spark"
    assert report["model_id_source"] == "built-in default"
    assert report["model_id_verified"] is False
    assert report["base_url"] == "https://api.meta.ai/v1"
    assert report["live_call_made"] is False


def test_profile_report_meta_muse_with_key_is_configured_not_live(monkeypatch):
    monkeypatch.setenv("META_MODEL_API_KEY", "test-key")
    monkeypatch.setenv("META_MUSE_MODEL", "muse-spark-1.3")
    report = profile_report(_settings("meta-muse", tier="contributor"))
    assert report["status"] == "configured_unverified"
    assert report["configured"] is True
    assert report["model"] == "muse-spark-1.3"
    assert report["model_id_source"] == "environment"
    assert report["tier"] == "contributor"
    assert report["live_call_made"] is False


def test_profile_report_unknown_profile():
    report = profile_report(_settings("carrier-pigeon"))
    assert report["status"] == "unknown_profile"
    assert report["configured"] is False


def _shape_double(captured):
    def transport(url, headers, body):
        captured.update(url=url, headers=headers, body=body)
        assert url == "https://api.meta.ai/v1/chat/completions"
        assert headers["Authorization"] == "Bearer probe-key"
        assert headers["Content-Type"] == "application/json"
        assert body["model"] == "muse-spark"
        assert [m["role"] for m in body["messages"]] == ["system", "user"]
        return {
            "choices": [{"message": {"content": "ready"}}],
            "usage": {"prompt_tokens": 120, "completion_tokens": 1},
        }
    return transport


def test_probe_meta_muse_exercises_the_stub_end_to_end(store):
    captured: dict = {}
    report = probe_meta_muse(
        store, api_key="probe-key", tier="contributor",
        transport=_shape_double(captured))
    assert captured["body"]["messages"][1]["content"]
    assert report["live_call_made"] is False
    assert report["reply_text"] == "ready"
    assert report["tier"] == "contributor"
    assert report["calls_recorded"] == 1
    assert report["reconcile_problems"] == []
    (call,) = store.list_model_calls("profile-probe")
    assert call.profile == "meta-muse"
    assert call.tier == "contributor"
    assert call.call_id == report["call_id"]
    assert report["cost_usd"] == call.cost_usd
    # 120 in x 0.10 + 1 out x 0.20 per 1M, on the contributor tier.
    assert call.cost_usd == "0.000012"


def test_probe_meta_muse_honours_base_url_and_model_overrides(store):
    seen: dict = {}

    def transport(url, headers, body):
        seen.update(url=url, model=body["model"])
        return {"choices": [{"message": {"content": "ready"}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1}}

    report = probe_meta_muse(
        store, api_key="k", model="muse-spark-1.3",
        base_url="https://double.invalid/v9/", transport=transport)
    assert seen == {"url": "https://double.invalid/v9/chat/completions",
                    "model": "muse-spark-1.3"}
    assert report["model"] == "muse-spark-1.3"
    assert report["base_url"] == "https://double.invalid/v9"

