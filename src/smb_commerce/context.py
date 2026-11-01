"""Context discipline: every model call's context, budgeted.

Round 12 gives the governed model port a stated context policy
for Muse Spark's 1M-token window. Until now prompts were assembled
ad hoc at the call sites (a figure sheet plus one instruction
line) with no budget, no counting and no record. The discipline:

- **A versioned policy** (:data:`CONTEXT_POLICY`): the window
  (1,000,000 tokens, as published for Muse Spark), a stated output
  reserve held back for the reply, and the budget — window minus
  reserve — that an assembled context may never exceed.
- **A stated token method**: whitespace-delimited word count, the
  same estimator the offline profile prices by. It is an estimate
  by design and says so; it is deterministic, which is what the
  discipline needs — the same context always counts the same.
- **Pinned and compactable segments.** The system prompt and a
  request's sheet lines (the figure sheet of a drafting request,
  the screen sheet of a screening request) are pinned: they carry
  the figures and the contract, and compaction never touches
  them, so a compacted context drafts from exactly the same
  figures. Everything after the sheet is narrative context and
  is compactable.
- **Compaction, oldest first.** When the assembled context would
  exceed the budget, narrative lines are dropped oldest first and
  replaced by one marker line naming the policy, the rule and the
  count dropped. The smallest number of dropped lines that fits
  is chosen, so compaction removes the minimum the budget forces.
  If the pinned material alone exceeds the budget, assembly fails
  closed with :class:`ContextOverBudget` — the call never happens
  rather than silently exceeding the window.
- **The record.** Every governed call stores its assembly as an
  insert-only ``context_assembly`` artefact (Round 12, in
  ``modelport``): policy version, budget, token counts before and
  after, and what was dropped. :func:`verify_context_payload`
  re-performs the record's arithmetic and its budget law at any
  time.

Today's real contexts are a few hundred words, far under the
budget; the machinery is graded with oversized fixtures, at the
assembler level and through the governed port with a small test
policy.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .modelport import (
    DRAFT_REQUEST_PREFIX,
    FIGURE_SHEET_PREFIX,
    SCREEN_REQUEST_PREFIX,
    SCREEN_SHEET_PREFIX,
)

CONTEXT_KIND = "context_assembly"

_REQUEST_SHEETS = (
    (DRAFT_REQUEST_PREFIX, FIGURE_SHEET_PREFIX),
    (SCREEN_REQUEST_PREFIX, SCREEN_SHEET_PREFIX),
)


@dataclass(frozen=True)
class ContextPolicy:
    version: str = "2026.11.1"
    window_tokens: int = 1_000_000
    output_reserve_tokens: int = 8_192
    token_method: str = "whitespace_words_v1"
    compaction_rule: str = "oldest_narrative_first_v1"

    @property
    def budget_tokens(self) -> int:
        return self.window_tokens - self.output_reserve_tokens


CONTEXT_POLICY = ContextPolicy()


class ContextOverBudget(RuntimeError):
    """The pinned context alone exceeds the policy budget."""


def count_tokens(text: str) -> int:
    """The stated estimator: whitespace-delimited words. The same
    method the offline profile counts by, so a context's count and
    a call's recorded input tokens are the same arithmetic."""
    return len(text.split())


@dataclass
class ContextAssembly:
    system: str
    prompt: str
    policy_version: str
    token_method: str
    compaction_rule: str
    budget_tokens: int
    tokens_before: int
    tokens_after: int
    dropped: list[dict] = field(default_factory=list)
    marker_tokens: int = 0

    @property
    def compacted(self) -> bool:
        return bool(self.dropped)

    def record(self) -> dict:
        """The artefact payload's context half: everything a
        verifier needs to re-perform the assembly's arithmetic."""
        return {
            "policy_version": self.policy_version,
            "token_method": self.token_method,
            "compaction_rule": self.compaction_rule,
            "budget_tokens": self.budget_tokens,
            "tokens_before": self.tokens_before,
            "tokens_after": self.tokens_after,
            "compacted": self.compacted,
            "dropped": self.dropped,
            "marker_tokens": self.marker_tokens,
        }


def _split_prompt(prompt: str) -> tuple[str, str]:
    """Split a prompt into (pinned sheet head, narrative). For a
    drafting or screening request the first two lines — the
    request line and its sheet — are pinned; the rest is
    narrative. Any other prompt is all narrative."""
    lines = prompt.split("\n")
    if len(lines) >= 2:
        for request_prefix, sheet_prefix in _REQUEST_SHEETS:
            if lines[0].startswith(request_prefix) and lines[1].startswith(
                sheet_prefix
            ):
                return "\n".join(lines[:2]), "\n".join(lines[2:])
    return "", prompt


def _marker(policy: ContextPolicy, dropped_lines: int) -> str:
    return (
        f"[context compacted under policy {policy.version}, rule"
        f" {policy.compaction_rule}: {dropped_lines} oldest narrative"
        f" line(s) dropped]"
    )


def assemble_context(system: str, prompt: str,
                     policy: ContextPolicy = CONTEXT_POLICY
                     ) -> ContextAssembly:
    """Assemble one call's context under the policy budget.

    Returns the (possibly compacted) system and prompt with the
    counts and drop record. Raises :class:`ContextOverBudget`
    when the pinned material alone exceeds the budget.
    """
    tokens_before = count_tokens(system) + count_tokens(prompt)
    if tokens_before <= policy.budget_tokens:
        return ContextAssembly(
            system=system, prompt=prompt,
            policy_version=policy.version,
            token_method=policy.token_method,
            compaction_rule=policy.compaction_rule,
            budget_tokens=policy.budget_tokens,
            tokens_before=tokens_before, tokens_after=tokens_before,
        )
    head, narrative = _split_prompt(prompt)
    lines = narrative.split("\n") if narrative else []
    pinned_tokens = count_tokens(system) + count_tokens(head)
    for dropped_count in range(1, len(lines) + 1):
        marker = _marker(policy, dropped_count)
        remaining = lines[dropped_count:]
        compacted_narrative = "\n".join([marker, *remaining])
        compacted_prompt = (
            f"{head}\n{compacted_narrative}" if head
            else compacted_narrative
        )
        tokens_after = pinned_tokens + count_tokens(compacted_narrative)
        if tokens_after <= policy.budget_tokens:
            return ContextAssembly(
                system=system, prompt=compacted_prompt,
                policy_version=policy.version,
                token_method=policy.token_method,
                compaction_rule=policy.compaction_rule,
                budget_tokens=policy.budget_tokens,
                tokens_before=tokens_before,
                tokens_after=tokens_after,
                dropped=[{
                    "segment": "narrative",
                    "lines": dropped_count,
                    "tokens": sum(count_tokens(line)
                                  for line in lines[:dropped_count]),
                }],
                marker_tokens=count_tokens(marker),
            )
    raise ContextOverBudget(
        f"pinned context is {pinned_tokens} tokens against a budget"
        f" of {policy.budget_tokens} (policy {policy.version}); the"
        f" call was not made"
    )


def verify_context_payload(payload: dict,
                           policy: ContextPolicy = CONTEXT_POLICY
                           ) -> list[str]:
    """Re-perform a stored context record against a stated policy
    (the current one, unless the caller names the policy the
    record was written under): the record's version, method, rule
    and budget must be that policy's; the final count must sit
    inside the budget; the arithmetic must reconcile
    (after = before - dropped + marker); and the compacted flag
    must agree with the drop record."""
    problems: list[str] = []
    if payload.get("policy_version") != policy.version:
        problems.append("context_policy_version_mismatch")
    if payload.get("token_method") != policy.token_method:
        problems.append("context_token_method_mismatch")
    if payload.get("compaction_rule") != policy.compaction_rule:
        problems.append("context_compaction_rule_mismatch")
    if payload.get("budget_tokens") != policy.budget_tokens:
        problems.append("context_budget_mismatch")
    dropped = payload.get("dropped", [])
    dropped_tokens = sum(int(entry.get("tokens", 0)) for entry in dropped)
    expected_after = (
        int(payload.get("tokens_before", 0)) - dropped_tokens
        + int(payload.get("marker_tokens", 0))
    )
    if payload.get("tokens_after") != expected_after:
        problems.append("context_token_arithmetic_mismatch")
    if int(payload.get("tokens_after", 0)) > int(
        payload.get("budget_tokens", 0)
    ):
        problems.append("context_budget_exceeded")
    if bool(payload.get("compacted")) != bool(dropped):
        problems.append("context_compaction_flag_mismatch")
    return problems
