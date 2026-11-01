"""Round 12: the context discipline — a versioned budget policy
for the 1M-token window, deterministic oldest-first compaction
that never touches the sheet, a context record on every governed
call, and fail-closed assembly when the pinned material alone is
over budget."""

from __future__ import annotations

import json

import pytest

from smb_commerce.context import (
    CONTEXT_KIND,
    CONTEXT_POLICY,
    ContextOverBudget,
    ContextPolicy,
    assemble_context,
    count_tokens,
    verify_context_payload,
)
from smb_commerce.modelport import GovernedModelPort, OfflineModelPort

SMALL = ContextPolicy(window_tokens=120, output_reserve_tokens=20)

SHEET = {
    "draft_kind": "plan_narrative",
    "business": "Hartwell Home Goods",
    "as_of": "2026-01-04",
    "summary": [{"name": "net_revenue", "value": "100.00",
                 "source_artefact_id": "LED-x", "source_field": "net_revenue"}],
    "priorities": [],
    "coverage": [],
    "claims": [{"value": "100.00", "source_artefact_id": "LED-x",
                "source_field": "net_revenue"}],
}


def _draft_prompt(narrative_lines: int) -> str:
    lines = [
        "DRAFT-REQUEST plan_narrative",
        f"FIGURE-SHEET {json.dumps(SHEET, sort_keys=True)}",
    ]
    lines += [
        f"narrative line {i} carries ten plain words of context here"
        for i in range(narrative_lines)
    ]
    return "\n".join(lines)


def test_default_policy_is_the_muse_window_minus_a_stated_reserve():
    assert CONTEXT_POLICY.window_tokens == 1_000_000
    assert CONTEXT_POLICY.budget_tokens == 1_000_000 - 8_192
    assert CONTEXT_POLICY.version == "2026.11.1"


def test_count_tokens_is_the_stated_word_estimator():
    assert count_tokens("one two  three\nfour") == 4
    assert count_tokens("") == 0


def test_under_budget_assembly_is_byte_identical():
    prompt = _draft_prompt(3)
    assembly = assemble_context("system words here", prompt)
    assert assembly.system == "system words here"
    assert assembly.prompt == prompt
    assert assembly.compacted is False
    assert assembly.dropped == []
    assert assembly.tokens_after == assembly.tokens_before
    assert assembly.tokens_after <= CONTEXT_POLICY.budget_tokens


def test_compaction_drops_oldest_narrative_first_and_keeps_the_sheet():
    prompt = _draft_prompt(30)
    before = count_tokens("sys") + count_tokens(prompt)
    assembly = assemble_context("sys", prompt, SMALL)
    assert assembly.compacted is True
    assert assembly.tokens_after <= SMALL.budget_tokens
    assert assembly.tokens_before == before
    # The sheet head survives byte-identical: same figures.
    head = "\n".join(prompt.split("\n")[:2])
    assert assembly.prompt.startswith(head + "\n[context compacted")
    from smb_commerce.modelport import _figure_sheet
    assert _figure_sheet(assembly.prompt) == SHEET
    # Oldest gone, newest kept.
    assert "narrative line 0 " not in assembly.prompt
    assert "narrative line 29 " in assembly.prompt
    (dropped,) = assembly.dropped
    assert dropped["segment"] == "narrative"
    assert dropped["lines"] >= 1
    # The record's arithmetic reconciles.
    assert (assembly.tokens_after
            == assembly.tokens_before - dropped["tokens"]
            + assembly.marker_tokens)


def test_compaction_is_deterministic():
    prompt = _draft_prompt(30)
    first = assemble_context("sys", prompt, SMALL)
    second = assemble_context("sys", prompt, SMALL)
    assert first.prompt == second.prompt
    assert first.record() == second.record()


def test_pinned_material_over_budget_fails_closed():
    giant_system = " ".join(["word"] * 500)
    with pytest.raises(ContextOverBudget):
        assemble_context(giant_system, "small", SMALL)
    giant_sheet = dict(SHEET, business=" ".join(["word"] * 500))
    prompt = "\n".join([
        "DRAFT-REQUEST plan_narrative",
        f"FIGURE-SHEET {json.dumps(giant_sheet, sort_keys=True)}",
        "tail",
    ])
    with pytest.raises(ContextOverBudget):
        assemble_context("sys", prompt, SMALL)


def test_governed_port_records_every_assembly(store):
    port = GovernedModelPort(OfflineModelPort(tier="standard"), store)
    reply = port.complete("run-ctx", "system text", "a short prompt")
    (artefact,) = store.list_artefacts(CONTEXT_KIND)
    payload = artefact.payload
    assert payload["run_id"] == "run-ctx"
    assert payload["call_id"] == reply.call_id
    assert payload["profile"] == "offline"
    assert payload["compacted"] is False
    assert verify_context_payload(payload) == []
    # The recorded count is the count the profile itself priced:
    # same estimator, same texts, under budget and uncompacted.
    (call,) = store.list_model_calls("run-ctx")
    assert payload["tokens_after"] == call.input_tokens
    # A repeat of the same call records nothing twice.
    port.complete("run-ctx", "system text", "a short prompt")
    assert len(store.list_artefacts(CONTEXT_KIND)) == 1
    assert len(store.list_model_calls("run-ctx")) == 1


def test_governed_port_compacts_and_the_draft_still_resolves(store):
    port = GovernedModelPort(
        OfflineModelPort(tier="standard"), store, context_policy=SMALL)
    reply = port.complete("run-big", "sys", _draft_prompt(30))
    # The offline composer drafted from the sheet alone — the
    # figures survive compaction because the sheet is pinned.
    document = json.loads(reply.text)
    assert document["claims"] == SHEET["claims"]
    (artefact,) = store.list_artefacts(CONTEXT_KIND)
    payload = artefact.payload
    assert payload["compacted"] is True
    assert payload["tokens_after"] <= SMALL.budget_tokens
    assert verify_context_payload(payload, SMALL) == []
    (call,) = store.list_model_calls("run-big")
    assert payload["tokens_after"] == call.input_tokens


def test_over_budget_call_records_nothing(store):
    port = GovernedModelPort(
        OfflineModelPort(tier="standard"), store, context_policy=SMALL)
    with pytest.raises(ContextOverBudget):
        port.complete("run-blocked", " ".join(["word"] * 500), "hi")
    assert store.list_model_calls() == []
    assert store.list_artefacts(CONTEXT_KIND) == []


def test_verify_context_payload_catches_a_tampered_record(store):
    port = GovernedModelPort(OfflineModelPort(tier="standard"), store)
    port.complete("run-tamper", "system text", "a short prompt")
    (artefact,) = store.list_artefacts(CONTEXT_KIND)
    payload = dict(artefact.payload)
    payload["tokens_after"] = payload["tokens_after"] + 5
    problems = verify_context_payload(payload)
    assert "context_token_arithmetic_mismatch" in problems
    payload = dict(artefact.payload)
    payload["budget_tokens"] = payload["tokens_after"] - 1
    problems = verify_context_payload(payload)
    assert "context_budget_mismatch" in problems
    payload = dict(artefact.payload)
    payload["compacted"] = True
    problems = verify_context_payload(payload)
    assert "context_compaction_flag_mismatch" in problems
