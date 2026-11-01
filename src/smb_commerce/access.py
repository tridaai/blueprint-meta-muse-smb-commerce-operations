"""Access control: who may see and do what, and what "no" looks like.

Round 13 puts a stated access layer over the record the first
twelve rounds built. Until now every surface read everything and
any caller could approve; the shop's staff are not one person,
so the record now answers to a **versioned role pack**:

- Four staff roles — ``owner``, ``operator``, ``bookkeeper``,
  ``viewer`` — and a permission matrix stated as data in
  :data:`ACCESS_PACK`: each permission names the roles that hold
  it, and enforcement is a lookup in that table, never a
  conditional scattered through the surfaces.
- The matrix's shape follows the laws. Only the owner may record
  an approval act (Law 2's named human is the owner; the operator
  runs the pipeline — ingest, diagnoses, drafting, screening —
  but cannot approve what their own pipeline produced). The
  bookkeeper reads the money record — facts, ledger, diagnoses,
  connector quarantines, costs — and nothing of drafts,
  approvals, the plan or the board. The viewer is the named
  least role: it holds no record permission at all, so a login
  can be handed out that sees the service is alive (health,
  readiness, configuration) and nothing of the record.

Identity is presented, not authenticated
-----------------------------------------
A caller is a named actor plus a role, resolved once per call by
:func:`resolve_caller` — the same function for both surfaces
(the ``X-SMB-Actor`` / ``X-SMB-Role`` headers on the API, the
``--actor`` / ``--role`` flags on the CLI) — and passed to the
service, which refuses what the pack refuses. On its own this
layer is **not** an identity provider: nothing in a presented
identity proves the caller is who they claim. Scoped tokens
(Round 18, ``tokens.py``) are the authentication layer over it:
a bearer string resolves to the same :class:`Caller`, narrowed
by the token's scopes. The honest statement is that presented
roles bind callers who present them, inside a deployment that
trusts its callers to do so — and token-authenticated callers
no longer need that trust.

Existence hiding
----------------
A refusal must not leak. A caller whose role may not see a
resource gets exactly what an absent resource returns: a
single-resource read returns ``None``, a list read returns the
empty list, a summary returns its empty form — same status, same
shape, same error text as a genuinely absent id, and nothing
through counts, board or queue summaries either. Actions are
different: there is nothing to hide about an *attempt*, so a
forbidden action refuses loudly (:class:`AccessRefused`) before
any store lookup of its target — which is also what makes the
refusal identical for real and absent target ids. A role-gated
approval attempt is recorded like every refused approval: as an
approval-refusal artefact naming the actor and the problem.

Digests (Round 14)
------------------
The pack gains the digest permissions. ``digests:read`` is held
by every staff role — a digest is an *addressed* artefact whose
payload is assembled for one role out of only the sections that
role may read, so the kind gate alone cannot be the rule: the
service additionally requires the caller to hold **every**
permission the digest's included sections required (see
``digests.py``). Content, not address, decides: the bookkeeper
reads the bookkeeper digest (headline + costs — the permissions
they hold), never the owner digest (which also carries the plan
findings and the approval picture), and the viewer's digest —
which includes no section at all — is readable by the viewer
precisely because there is nothing in it they may not see.
Generating digests and writing subscriptions are actions, gated
like the pipeline runs (owner + operator); reading the
subscription record is owner + operator — it names the staff.

Review packs (Round 17)
-----------------------
The pack gains three permissions. ``packs:read`` is held by
every staff role as the kind gate — the pack is assembled
whole rather than per role, so, exactly as with a digest, the
service additionally requires the caller to hold **every**
permission the pack's included sections required (ledger,
plan, diagnosis, approvals, costs): in practice the owner and
the operator read a full pack, and a caller missing any
section's permission gets the absent answer. An export is
gated by its pack's content the same way, and a publication
act or refusal reads only to a caller who may read its pack.
Generating a pack (and exporting it) is an operation, owner +
operator; publishing one is the named human act over the pack
and is reserved to the owner, like ``approvals:act`` — the
operator may assemble the month's pack but may not publish
what their own pipeline produced.

Tokens and webhooks (Round 18)
------------------------------
The pack gains four permissions. Token records are credential
metadata — they name a staff member and the scopes a bearer
string carries — so ``tokens:read`` and ``tokens:issue``
(issuance and revocation alike) are the owner's alone.
Webhook subscriptions name an external URL and the events it
hears about, so ``webhooks:write`` (subscribe, unsubscribe) is
the owner's alone too; ``webhooks:read`` (subscriptions and
delivery records — neither carries the signing secret) is
owner + operator.

A token-authenticated caller is the same :class:`Caller` with
``scopes`` set: the effective permission is the intersection —
the role must hold the permission **and** the permission must
be in the token's scope set — so a token can narrow the role
it was issued under, never widen it. ``scopes is None`` is the
presented identity of Rounds 13–17, unscoped. Resolution of a
bearer string to a caller lives in ``tokens.py``; this module
stays the pure statement of who may do what.

The internal caller
-------------------
In-process flows — the demo, the eval gate, the LangGraph loop,
the parity walk — act as :data:`INTERNAL_CALLER`, the process's
own stated identity (the shop owner-operator running their own
deployment). Service methods take ``caller=None`` to mean that
identity; surfaces must resolve and pass a caller explicitly,
and an unresolvable identity resolves to :data:`ANONYMOUS`,
which holds no permission and fails closed everywhere.
"""

from __future__ import annotations

from dataclasses import dataclass

ACCESS_PACK_VERSION = "2026.11.1"

ROLES = ("owner", "operator", "bookkeeper", "viewer")

# The pack, as data: permission -> the roles that hold it.
# Anything not listed here is not a permission; a role not listed
# for a permission does not hold it. Owner holds every permission.
ACCESS_PACK: dict[str, frozenset[str]] = {
    # Reads of the record, by class.
    "facts:read": frozenset({"owner", "operator", "bookkeeper"}),
    "ledger:read": frozenset({"owner", "operator", "bookkeeper"}),
    "diagnosis:read": frozenset({"owner", "operator", "bookkeeper"}),
    "plan:read": frozenset({"owner", "operator"}),
    "drafts:read": frozenset({"owner", "operator"}),
    "guardian:read": frozenset({"owner", "operator"}),
    "approvals:read": frozenset({"owner", "operator"}),
    "board:read": frozenset({"owner", "operator"}),
    "connectors:read": frozenset({"owner", "operator", "bookkeeper"}),
    "costs:read": frozenset({"owner", "operator", "bookkeeper"}),
    # Configuration (the model profile report): any identified
    # staff role. It carries no record data — /readiness echoes
    # the same profile and tier — but an anonymous caller may not
    # read it either.
    "config:read": frozenset(ROLES),
    # Writes and runs.
    "ingest:write": frozenset({"owner", "operator"}),
    "diagnose:run": frozenset({"owner", "operator"}),
    "plan:run": frozenset({"owner", "operator"}),
    "draft:run": frozenset({"owner", "operator"}),
    "screen:run": frozenset({"owner", "operator"}),
    # The named human act (Law 2): the owner only.
    "approvals:act": frozenset({"owner"}),
    # Digests (Round 14). The kind gate for an addressed digest
    # artefact: every staff role — the service additionally
    # requires the caller to hold every permission the digest's
    # included sections required, so content decides (see the
    # module docstring). Generation and the subscription record
    # are operations surfaces: owner + operator.
    "digests:read": frozenset(ROLES),
    "digests:generate": frozenset({"owner", "operator"}),
    "subscriptions:read": frozenset({"owner", "operator"}),
    "subscriptions:write": frozenset({"owner", "operator"}),
    # Review packs (Round 17). packs:read is the kind gate every
    # staff role holds — a pack is assembled whole, so the
    # service additionally requires the caller to hold every
    # permission the pack's included sections required (see
    # reviewpacks.py). Generation and export are operations
    # (owner + operator); publication is the named human act
    # over the pack, reserved to the owner like approvals:act.
    "packs:read": frozenset(ROLES),
    "packs:generate": frozenset({"owner", "operator"}),
    "packs:publish": frozenset({"owner"}),
    # Tokens (Round 18). A token record is credential metadata:
    # it names a staff member and the scopes their bearer string
    # carries. Reading the token record and issuing or revoking
    # tokens are the owner's alone.
    "tokens:read": frozenset({"owner"}),
    "tokens:issue": frozenset({"owner"}),
    # Webhooks (Round 18). A subscription names an external URL
    # and the events it hears about; writing one is the owner's
    # alone. Reads (subscriptions, delivery records) carry no
    # secret — the signing secret never reads back — and are
    # owner + operator.
    "webhooks:read": frozenset({"owner", "operator"}),
    "webhooks:write": frozenset({"owner"}),
}

PERMISSIONS = tuple(ACCESS_PACK)

# Which permission reads an artefact of each kind. Kinds are the
# record's own inventory; an unknown kind is a code bug, not a
# caller state, so the lookup raises rather than guessing.
_ARTEFACT_READ_PERMISSIONS: dict[str, str] = {
    "ledger": "ledger:read",
    "diagnosis_stockout": "diagnosis:read",
    "diagnosis_ad_decay": "diagnosis:read",
    "diagnosis_expense_anomaly": "diagnosis:read",
    "diagnosis_refund_anomaly": "diagnosis:read",
    "diagnosis_email_flow_audit": "diagnosis:read",
    "diagnosis_listing_quality": "diagnosis:read",
    "image_vision_judgement": "diagnosis:read",
    "diagnosis_refusal": "diagnosis:read",
    "growth_plan": "plan:read",
    "plan_refusal": "plan:read",
    "draft_flow_proposal": "drafts:read",
    "draft_plan_narrative": "drafts:read",
    "draft_email_copy": "drafts:read",
    "draft_ad_note": "drafts:read",
    "draft_refusal": "drafts:read",
    "guardian_verdict": "guardian:read",
    "approval_act": "approvals:read",
    "rejection_act": "approvals:read",
    "approval_refusal": "approvals:read",
    "connector_quarantine": "connectors:read",
    "context_assembly": "costs:read",
    "ops_digest": "digests:read",
    "digest_subscription": "subscriptions:read",
    "review_pack": "packs:read",
    "review_pack_export": "packs:read",
    "review_pack_publication": "packs:read",
    "review_pack_publication_refusal": "packs:read",
    "api_token": "tokens:read",
    "api_token_revocation": "tokens:read",
    "api_token_refusal": "tokens:read",
    "webhook_subscription": "webhooks:read",
    "webhook_delivery": "webhooks:read",
}


def read_permission_for_kind(kind: str) -> str:
    """The permission a read of an artefact of ``kind`` needs."""
    try:
        return _ARTEFACT_READ_PERMISSIONS[kind]
    except KeyError:
        raise KeyError(
            f"no read permission stated for artefact kind {kind!r}"
        ) from None


@dataclass(frozen=True)
class Caller:
    """An identity: a named actor acting in one role.

    ``actor`` empty or ``role`` None is the anonymous caller,
    which holds no permission.

    ``scopes is None`` is a presented identity (Rounds 13–17):
    the role's permissions are the caller's. A token-authenticated
    caller (Round 18) carries the token's ``scopes`` and its
    ``token_id``: its effective permissions are the intersection
    of the role's and the scope set, so a token narrows the role
    it was issued under and can never widen it."""

    actor: str = ""
    role: str | None = None
    scopes: frozenset[str] | None = None
    token_id: str | None = None

    @property
    def identified(self) -> bool:
        return bool(self.actor) and self.role in ROLES

    def allows(self, permission: str) -> bool:
        if not self.identified:
            return False
        if self.role not in ACCESS_PACK[permission]:
            return False
        return self.scopes is None or permission in self.scopes

    def describe(self) -> str:
        if not self.identified:
            return "anonymous"
        if self.token_id:
            return f"{self.actor} ({self.role}) via token {self.token_id}"
        return f"{self.actor} ({self.role})"


#: The anonymous caller: no name, no role, no permissions.
ANONYMOUS = Caller()

#: The process's own stated identity for in-process flows (the
#: demo, the eval gate, the graph, the parity walk): the shop
#: owner-operator running their own deployment. Service methods
#: called without a caller act as this identity — never as a
#: surface caller's, and surfaces never default to it.
INTERNAL_CALLER = Caller("smb-commerce-internal", "owner")


def resolve_caller(actor: str | None, role: str | None) -> Caller:
    """Resolve a presented identity to a :class:`Caller`.

    The one resolution both surfaces use. Anything unresolvable
    — a missing name, a missing role, a role the pack does not
    state — resolves to :data:`ANONYMOUS` and fails closed.
    """
    actor = (actor or "").strip()
    role = (role or "").strip().lower()
    if not actor or role not in ROLES:
        return ANONYMOUS
    return Caller(actor, role)


class AccessRefused(Exception):
    """A caller attempted an action the pack refuses them.

    Raised before any store lookup of the action's target, so
    the refusal is identical whether the target exists or not.
    For approval acts the service records an approval-refusal
    artefact and raises ``ApprovalRefused`` instead — the refusal
    record is that module's pattern."""

    def __init__(self, caller: Caller, permission: str):
        self.caller = caller
        self.permission = permission
        self.problems = [f"role_not_permitted:{permission}"]
        super().__init__(
            f"access refused: {caller.describe()} may not {permission}"
        )


def require(caller: Caller, permission: str) -> None:
    """Raise :class:`AccessRefused` unless ``caller`` holds
    ``permission``."""
    if not caller.allows(permission):
        raise AccessRefused(caller, permission)


def visible(caller: Caller, permission: str) -> bool:
    """Whether ``caller`` may see resources under ``permission``
    — the read side of the pack, for existence-hiding reads that
    answer with the absent form instead of raising."""
    return caller.allows(permission)
