"""Subscriptions and digests: the operating picture, kept per role.

Round 14 stores what Round 9's morning board derives. The board
is a view — folded from the record on every read, never stored.
A digest is the board's stored counterpart: for a stated period
(a day, or a seven-day week ending on a stated date) and a
stated role, the operating picture is folded once, pinned to the
heads it folded, and kept as an insert-only, content-addressed
artefact (Law 4) that a reader can come back to and a verifier
can re-perform.

Per-role content, decided by the pack
-------------------------------------
A digest is assembled **for one role** out of sections, and each
section is gated by the Round 13 access pack — the same
permission that gates the underlying record class:

- ``headline`` (the ledger figures, cited to the ledger
  artefact) — ``ledger:read``;
- ``findings`` (the plan's ranked priorities, figures copied
  from the finding artefacts) and ``stock_risks`` — ``plan:read``;
- ``approvals`` (the queue fold: awaiting, approved — not sent,
  rejected, blocked) — ``approvals:read``;
- ``costs`` (the cost ledger's totals, by tier, reconciled) —
  ``costs:read``.

So the owner's digest carries every section, the operator's the
same (the operator holds every section permission), the
bookkeeper's headline + costs only, and the viewer's **no
section at all** — the viewer holds no record permission, and a
digest cannot carry what its role may not read. The payload
names the sections it included; a section the role may not read
is absent, never emptied or redacted in place.

Reading a digest is gated by content, not address: a caller may
read a digest when they hold ``digests:read`` (every staff role)
**and** every permission the digest's included sections
required. The owner digest is therefore invisible to the
bookkeeper — not because of who it was made for, but because of
what is in it — and the viewer's empty digest is visible to any
identified staff caller, because there is nothing in it anyone
may not see. A digest a caller may not read answers exactly as
an absent one does (Round 13's existence hiding).

Subscriptions
-------------
Who receives what is itself record: a subscription is an
insert-only artefact naming a staff member (actor + role) and a
cadence (``daily`` or ``weekly``). Unsubscribing inserts a
successor artefact that supersedes the active one; the current
state is the fold of the chain, exactly as the approval queue
folds acts. Generation is driven by the folded subscriptions:
one digest per active subscription per period — and because a
digest's identity is its content (role, cadence, period, pins,
sections), two subscriptions for the same role and cadence are
served by the one digest artefact, and re-running a period
inserts nothing twice.

Figures, prose, delivery — the stated limits
--------------------------------------------
Every figure in a digest is copied from a computed artefact and
cites it, exactly as the board's do (Law 1); the verifier
re-performs a digest by a second route (fresh fold, figure
re-resolution, pin comparison), as the board's does. A digest
folded before the record moved fails verification the same way
a stale board does — it is a snapshot of a stated period, and
the pins on its face say of what. The digest is purely
code-assembled: **no model call is made**, so there is no
narrative prose in it — drafting prose stays Round 6's governed
path. And **nothing is sent anywhere**: a digest is generated
into the record and read back through the surfaces by a caller
whose role may read it. Delivery to a mailbox or a phone would
be something leaving the building, which Law 2 reserves to the
guardian + named-approval path; that path is not built here and
the docs do not pretend otherwise. Figures are the record's
computed figures as of the head ledger's as-of — the record
carries no per-period ledger (Round 12's grain limit is the
same), so the period is the reporting label the snapshot is
pinned under, stated on its face, not a re-computation over a
date filter that does not exist.
"""

from __future__ import annotations

import hashlib
from copy import deepcopy
from datetime import date, timedelta

from .access import ACCESS_PACK, ROLES, Caller
from .board import BoardService
from .costs import CostAnalytics
from .ledger import LedgerService
from .models import Artefact
from .plan import _finding_key
from .store import SqlStore, canonical_json

DIGEST_KIND = "ops_digest"
SUBSCRIPTION_KIND = "digest_subscription"
DIGEST_PACK_VERSION = "2026.11.1"

CADENCES = ("daily", "weekly")

# The digest's sections, in payload order, and the pack
# permission each requires — of the role the digest is assembled
# for, and of any caller who would read it.
SECTION_PERMISSIONS: tuple[tuple[str, str], ...] = (
    ("headline", "ledger:read"),
    ("findings", "plan:read"),
    ("stock_risks", "plan:read"),
    ("approvals", "approvals:read"),
    ("costs", "costs:read"),
)
SECTION_PERMISSION = dict(SECTION_PERMISSIONS)

_APPROVAL_COUNTS = (
    "awaiting_approval", "approved", "approved_stale",
    "rejected", "blocked",
)


def sections_for_role(role: str) -> list[str]:
    """The sections a digest for ``role`` may include, per the
    access pack — the pack decides, never a local conditional."""
    return [
        name for name, permission in SECTION_PERMISSIONS
        if role in ACCESS_PACK[permission]
    ]


def digest_visible(digest: Artefact, caller: Caller) -> bool:
    """Whether ``caller`` may read ``digest``: identified, holds
    ``digests:read``, and holds every permission the sections
    actually present in the payload required."""
    if digest.kind != DIGEST_KIND:
        return False
    if not caller.identified or not caller.allows("digests:read"):
        return False
    return all(
        caller.allows(SECTION_PERMISSION[name])
        for name, _permission in SECTION_PERMISSIONS
        if name in digest.payload
    )


def period_for(cadence: str, end: date) -> tuple[date, date]:
    """The stated period for a cadence ending on ``end``: the
    day itself, or the seven-day week ending on it."""
    if cadence == "daily":
        return end, end
    if cadence == "weekly":
        return end - timedelta(days=6), end
    raise ValueError(f"unknown_cadence:{cadence}")


def subscription_id_for(actor: str, role: str, cadence: str) -> str:
    material = canonical_json(
        {"actor": actor, "role": role, "cadence": cadence})
    return "SUB-" + hashlib.sha256(material.encode()).hexdigest()[:20]


class DigestService:
    def __init__(self, store: SqlStore, board: BoardService):
        self._store = store
        self._board = board
        self._ledger = LedgerService(store)
        self._costs = CostAnalytics(store)

    # -- subscriptions -------------------------------------------------

    @staticmethod
    def _validate_subscription(actor: str, role: str,
                               cadence: str) -> None:
        if not (actor or "").strip():
            raise ValueError("subscriber_actor_required")
        if role not in ROLES:
            raise ValueError(f"unknown_role:{role}")
        if cadence not in CADENCES:
            raise ValueError(f"unknown_cadence:{cadence}")

    def _subscription_states(self) -> dict[str, Artefact]:
        """The fold: the latest artefact per subscription id,
        in record order — a superseded state never answers."""
        states: dict[str, Artefact] = {}
        for artefact in self._store.list_artefacts(SUBSCRIPTION_KIND):
            states[artefact.payload["subscription_id"]] = artefact
        return states

    def subscriptions(self) -> list[dict]:
        """The current subscription states, folded, in
        subscription-id order."""
        states = self._subscription_states()
        return [
            {
                "subscription_id": sub_id,
                "actor": artefact.payload["actor"],
                "role": artefact.payload["role"],
                "cadence": artefact.payload["cadence"],
                "active": artefact.payload["active"],
                "artefact_id": artefact.artefact_id,
            }
            for sub_id, artefact in sorted(states.items())
        ]

    def subscribe(self, actor: str, role: str,
                  cadence: str) -> Artefact:
        """Record a subscription. Subscribing an already-active
        subscription is a no-op returning the current head —
        the record gains no duplicate state."""
        self._validate_subscription(actor, role, cadence)
        sub_id = subscription_id_for(actor, role, cadence)
        current = self._subscription_states().get(sub_id)
        if current is not None and current.payload["active"]:
            return current
        return self._record_state(
            sub_id, actor, role, cadence, active=True,
            supersedes=(
                current.artefact_id if current is not None else None),
        )

    def unsubscribe(self, actor: str, role: str,
                    cadence: str) -> Artefact | None:
        """Supersede a subscription with its inactive state.
        Returns None when there is no active subscription to
        end — the surfaces answer that as absent."""
        self._validate_subscription(actor, role, cadence)
        sub_id = subscription_id_for(actor, role, cadence)
        current = self._subscription_states().get(sub_id)
        if current is None or not current.payload["active"]:
            return None
        return self._record_state(
            sub_id, actor, role, cadence, active=False,
            supersedes=current.artefact_id,
        )

    def _record_state(self, sub_id: str, actor: str, role: str,
                      cadence: str, active: bool,
                      supersedes: str | None) -> Artefact:
        payload = {
            "subscription_id": sub_id,
            "actor": actor,
            "role": role,
            "cadence": cadence,
            "active": active,
            "supersedes": supersedes,
            "pack_version": DIGEST_PACK_VERSION,
        }
        artefact_id = "DSB-" + hashlib.sha256(
            canonical_json(payload).encode()).hexdigest()[:20]
        existing = self._store.get_artefact(artefact_id)
        if existing is not None:
            return existing
        artefact = Artefact(
            artefact_id=artefact_id, kind=SUBSCRIPTION_KIND,
            payload=payload,
            input_fingerprint=hashlib.sha256(canonical_json(
                {"subscription_id": sub_id,
                 "supersedes": supersedes}).encode()).hexdigest(),
        )
        self._store.insert_artefact(artefact)
        return artefact

    # -- generation ------------------------------------------------------

    def generate(self, cadence: str,
                 period_end: str | None = None) -> dict:
        """Generate the digests for one cadence and period: one
        per active subscription of that cadence, each assembled
        for its subscription's role. Re-running the same period
        over an unmoved record inserts nothing — every digest
        reports ``existing``."""
        if cadence not in CADENCES:
            raise ValueError(f"unknown_cadence:{cadence}")
        board = self._board.derive()
        as_of = board.payload["as_of"]
        if period_end is None:
            end = date.fromisoformat(as_of)
        else:
            try:
                end = date.fromisoformat(period_end)
            except ValueError:
                raise ValueError(
                    f"period_end_not_a_date:{period_end}") from None
        start, end = period_for(cadence, end)
        summary = self._costs.summary()
        entries: list[dict] = []
        for state in self.subscriptions():
            if not state["active"] or state["cadence"] != cadence:
                continue
            digest = self._build(
                state["role"], cadence, start, end, board, summary)
            if self._store.get_artefact(digest.artefact_id) is not None:
                status = "existing"
            else:
                self._store.insert_artefact(digest)
                status = "created"
            entries.append({
                "subscription_id": state["subscription_id"],
                "actor": state["actor"],
                "role": state["role"],
                "digest_artefact_id": digest.artefact_id,
                "status": status,
            })
        return {
            "cadence": cadence,
            "period": {"start": start.isoformat(),
                       "end": end.isoformat()},
            "as_of": as_of,
            "generated": entries,
        }

    def _build(self, role: str, cadence: str, start: date,
               end: date, board: Artefact, summary: dict) -> Artefact:
        bp = board.payload
        included = sections_for_role(role)
        payload: dict = {
            "digest": "ops_digest",
            "pack_version": DIGEST_PACK_VERSION,
            "cadence": cadence,
            "period": {"start": start.isoformat(),
                       "end": end.isoformat()},
            "role": role,
            "as_of": bp["as_of"],
            "pins": bp["pins"],
            "sections_included": included,
        }
        if "headline" in included:
            payload["headline"] = {
                "ledger_artefact_id":
                    bp["headline"]["ledger_artefact_id"],
                "as_of": bp["headline"]["as_of"],
                "figures": deepcopy(bp["headline"]["figures"]),
            }
        if "findings" in included:
            payload["findings"] = deepcopy(bp["findings"])
        if "stock_risks" in included:
            payload["stock_risks"] = deepcopy(bp["stock_risks"])
        if "approvals" in included:
            counts = bp["headline"]["counts"]
            payload["approvals"] = {
                "awaiting_approval": deepcopy(
                    bp["awaiting_approval"]),
                "approved": deepcopy(bp["approved"]),
                "rejected": deepcopy(bp["rejected"]),
                "blocked": deepcopy(bp["blocked"]),
                "counts": {
                    name: counts[name] for name in _APPROVAL_COUNTS
                },
            }
        if "costs" in included:
            payload["costs"] = {
                "rate_card_version": summary["rate_card_version"],
                "total": summary["total"],
                "by_tier": summary["by_tier"],
                "reconcile_problems": summary["reconcile_problems"],
            }
        fingerprint = hashlib.sha256(canonical_json({
            "pins": bp["pins"], "role": role, "cadence": cadence,
            "period": payload["period"],
            "pack_version": DIGEST_PACK_VERSION,
        }).encode()).hexdigest()
        material = canonical_json(
            {"payload": payload, "fp": fingerprint})
        artefact_id = (
            "DIG-" + hashlib.sha256(material.encode()).hexdigest()[:20]
        )
        return Artefact(
            artefact_id=artefact_id, kind=DIGEST_KIND,
            payload=payload, input_fingerprint=fingerprint,
        )

    # -- verification ------------------------------------------------------

    def verify(self, digest: Artefact) -> list[str]:
        """Re-perform a digest end to end: its sections must be
        exactly the ones the pack allows its role, its headline
        and finding figures must re-resolve against the
        artefacts they cite, its pins must be the current heads,
        and every section must recompose identically from a
        fresh fold of the record. A digest folded before the
        record moved fails here, as a stale board does."""
        if digest.kind != DIGEST_KIND:
            return [f"not_a_digest:{digest.kind}"]
        payload = digest.payload
        problems: list[str] = []
        role = payload.get("role")
        if role not in ROLES:
            return ["digest_role_unknown"]
        expected_included = sections_for_role(role)
        if payload.get("sections_included") != expected_included:
            problems.append("digest_sections_mismatch")
        for name, _permission in SECTION_PERMISSIONS:
            if name in payload and name not in expected_included:
                problems.append(f"digest_section_not_permitted:{name}")
        headline = payload.get("headline")
        if headline is not None:
            ledger = self._store.get_artefact(
                headline.get("ledger_artefact_id", ""))
            if ledger is None or ledger.kind != "ledger":
                problems.append("digest_ledger_missing")
            else:
                for figure in headline.get("figures", []):
                    if figure.get(
                            "source_artefact_id") != ledger.artefact_id:
                        problems.append(
                            "digest_citation_error:headline:"
                            f"{figure.get('name')}")
                    elif ledger.payload.get(
                            figure.get("source_field")) != (
                            figure.get("value")):
                        problems.append(
                            "digest_figure_mismatch:headline:"
                            f"{figure.get('name')}")
        for entry in payload.get("findings", []):
            if entry.get("rank") is None:
                continue
            finding_set = self._store.get_artefact(
                entry.get("diagnosis_artefact_id", ""))
            if finding_set is None:
                problems.append(
                    "digest_citation_missing:"
                    f"{entry.get('finding_key')}")
                continue
            match = next(
                (f for f in finding_set.payload.get("findings", [])
                 if _finding_key(entry["family"], f)
                 == entry.get("finding_key")),
                None,
            )
            if match is None:
                problems.append(
                    f"digest_finding_missing:{entry.get('finding_key')}")
                continue
            for field, value in entry.get("figures", {}).items():
                if match.get(field) != value:
                    problems.append(
                        "digest_figure_mismatch:"
                        f"{entry.get('finding_key')}:{field}")
        try:
            start = date.fromisoformat(payload["period"]["start"])
            end = date.fromisoformat(payload["period"]["end"])
        except (KeyError, TypeError, ValueError):
            problems.append("digest_period_malformed")
            return problems
        board = self._board.derive()
        expected = self._build(
            role, payload.get("cadence"), start, end, board,
            self._costs.summary()).payload
        pins = payload.get("pins", {})
        for name, value in board.payload["pins"].items():
            if pins.get(name) != value:
                problems.append(f"digest_pin_mismatch:{name}")
        if payload.get("as_of") != expected["as_of"]:
            problems.append("digest_as_of_mismatch")
        for name, _permission in SECTION_PERMISSIONS:
            if canonical_json(payload.get(name)) != canonical_json(
                    expected.get(name)):
                problems.append(f"digest_section_mismatch:{name}")
        return problems
