"""Webhooks: signed notifications about the record, and scoped
tokens' companion surface.

A webhook is a **notification, not a document**. When a stated
event happens on the record — a draft passes the guardian and
awaits approval, an approval act is recorded, a connector batch
is quarantined whole, a digest is generated, a review pack is
published — each subscribed endpoint hears that it happened:
the event kind, the artefacts it concerns, who acted, and when.
It does **not** hear the figures: payloads carry ids, never
amounts, draft text or findings, so nothing a webhook carries
needs the guardian screen or an approval act (Law 2) — anything
worth reading still sits behind the authenticated, role-gated
reads, and the notification only tells a receiver where to look.

Subscriptions
-------------
A subscription is record, folded like the digest subscriptions:
an insert-only artefact naming the URL, the event kinds it
hears, and the owner who created it; unsubscribing inserts a
successor that supersedes it; the current state is the fold.
Writing subscriptions is the owner's act (``webhooks:write``);
reads are metadata only (``webhooks:read``).

The signing secret is shown once, at subscription. A webhook
secret cannot be stored as a hash — the service must sign with
it at dispatch time — so it is stored **encrypted under a
deployment key** that lives in configuration
(``SMB_WEBHOOK_KEY``), never in the record: the stored form is
an HMAC-keystream ciphertext with a stated nonce, no read
surface returns the secret or the ciphertext, and the
encryption key is the deployment's to rotate. When no key is
configured the local profile falls back to a stated development
key constant — that fallback protects nothing and the docs say
so; it exists so the local profile runs without ceremony.

Delivery
--------
Every payload is signed: HMAC-SHA256 over the canonical body
bytes with the subscription secret, carried in the
``X-SMB-Signature`` header (``sha256=<hex>``) beside
``X-SMB-Event`` and ``X-SMB-Delivery``. Delivery goes through a
stated port — a callable the service is wired with. Production
wiring is an HTTP POST (:func:`http_deliver`, stdlib, bounded
timeout); tests, the demo, the eval gate and the conformance
walk inject a **local double receiver** (:class:`LocalReceiver`)
that verifies the signature exactly as a real receiver would —
**no real external HTTP call happens in any of them.** Every
attempt is recorded as a ``webhook_delivery`` artefact —
subscription, event, attempt number, outcome, response class —
with bounded retries (three attempts, then the delivery rests
as failed on the record; nothing retries forever, and a failed
delivery never fails the operation that fired the event).
:func:`verify_signature` is the receiver-side check, and the
service re-performs it over a recorded delivery so a receiver
can confirm a delivery they were handed.

Time and entropy are constructor seams, as in ``tokens.py``:
production uses the UTC clock and the CSPRNG; the conformance
walk states both.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import urllib.error
import urllib.request
from datetime import datetime, timezone

from .models import Artefact
from .store import SqlStore, canonical_json

SUBSCRIPTION_KIND = "webhook_subscription"
DELIVERY_KIND = "webhook_delivery"
WEBHOOK_PACK_VERSION = "2026.11.1"

#: The events the record actually emits — one per stated
#: transition at the service boundary. An event kind not in
#: this tuple is not subscribable.
EVENT_KINDS = (
    "draft_awaiting_approval",
    "approval_recorded",
    "quarantine_created",
    "digest_generated",
    "review_pack_published",
)

#: Bounded retries: a delivery is attempted at most this many
#: times, each attempt recorded, then it rests as failed.
MAX_DELIVERY_ATTEMPTS = 3

#: The local profile's stated development key for the
#: subscription-secret encryption, used only when the
#: deployment configures none. It protects nothing — anyone
#: can read it here — and exists so the local profile runs
#: without ceremony. A real deployment sets SMB_WEBHOOK_KEY.
DEV_KEY_MATERIAL = "smb-commerce local development webhook key"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


# -- signing -------------------------------------------------------------

def sign(signing_secret: str, body: bytes) -> str:
    """The signature over a payload body, as carried in the
    ``X-SMB-Signature`` header."""
    digest = hmac.new(
        signing_secret.encode(), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def verify_signature(signing_secret: str, body: bytes,
                     signature: str) -> bool:
    """The receiver-side check: recompute and compare in
    constant time."""
    return hmac.compare_digest(sign(signing_secret, body), signature)


# -- subscription-secret encryption at rest -------------------------------

def _keystream(key: bytes, nonce: bytes, length: int) -> bytes:
    stream = bytearray()
    counter = 0
    while len(stream) < length:
        stream.extend(hmac.new(
            key, nonce + counter.to_bytes(4, "big"),
            hashlib.sha256).digest())
        counter += 1
    return bytes(stream[:length])


def _encrypt(key_material: str, plaintext: bytes,
             entropy) -> dict:
    key = hashlib.sha256(key_material.encode()).digest()
    nonce = entropy(16)
    stream = _keystream(key, nonce, len(plaintext))
    ciphertext = bytes(p ^ k for p, k in zip(plaintext, stream))
    return {"nonce": nonce.hex(), "ciphertext": ciphertext.hex()}


def _decrypt(key_material: str, blob: dict) -> bytes:
    key = hashlib.sha256(key_material.encode()).digest()
    nonce = bytes.fromhex(blob["nonce"])
    ciphertext = bytes.fromhex(blob["ciphertext"])
    stream = _keystream(key, nonce, len(ciphertext))
    return bytes(c ^ k for c, k in zip(ciphertext, stream))


# -- delivery port ---------------------------------------------------------

def http_deliver(url: str, body: bytes, headers: dict) -> int:
    """The production delivery shape: an HTTP POST with a
    bounded timeout, returning the response status. A non-2xx
    HTTP answer is a status, not an exception; a transport
    failure raises and the attempt records as an error."""
    request = urllib.request.Request(
        url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status
    except urllib.error.HTTPError as error:
        return error.code


class LocalReceiver:
    """The local double receiver: what tests, the demo, the
    eval gate and the conformance walk inject instead of the
    HTTP port, so no real external call happens in any of them.
    It behaves as a real receiver does — it verifies the
    signature over the body against the secret it was paired
    with and answers 200 only for a signature that verifies,
    401 otherwise — and keeps what it accepted and rejected so
    the caller can grade both."""

    def __init__(self, signing_secret: str):
        self._signing_secret = signing_secret
        self.accepted: list[bytes] = []
        self.rejected: list[bytes] = []

    def __call__(self, url: str, body: bytes, headers: dict) -> int:
        signature = headers.get("X-SMB-Signature", "")
        if verify_signature(self._signing_secret, body, signature):
            self.accepted.append(body)
            return 200
        self.rejected.append(body)
        return 401


# -- the service -----------------------------------------------------------

class WebhookService:
    def __init__(self, store: SqlStore, secret_key: str = "",
                 deliver=None):
        self._store = store
        self._key_material = secret_key or DEV_KEY_MATERIAL
        # The delivery port: production wiring is http_deliver;
        # a flow that must not make real calls injects a double.
        self.deliver = deliver if deliver is not None else http_deliver
        # Constructor seams, as in tokens.py.
        self.clock = _utc_now
        self.entropy = secrets.token_bytes

    # -- subscriptions -------------------------------------------------

    def subscribe(self, url: str, events, *, actor: str,
                  at: str | None = None,
                  signing_secret: str | None = None
                  ) -> tuple[Artefact, str | None]:
        """Record a subscription. Returns the artefact and the
        signing secret — the one time the secret is shown — or
        the existing artefact and None when an identical active
        subscription already stands (its secret cannot be shown
        again). A stated ``signing_secret`` is accepted so a
        receiver can be paired deterministically (the walk, a
        test); surfaces that omit it get a generated one."""
        url = (url or "").strip()
        if not (url.startswith("http://")
                or url.startswith("https://")):
            raise ValueError("webhook_url_must_be_http")
        event_set = sorted(set(events or ()))
        if not event_set:
            raise ValueError("webhook_events_required")
        for event in event_set:
            if event not in EVENT_KINDS:
                raise ValueError(f"unknown_event:{event}")
        subscription_id = "WHK-" + hashlib.sha256(canonical_json({
            "url": url, "events": event_set, "created_by": actor,
        }).encode()).hexdigest()[:20]
        current = self._state_for(subscription_id)
        if current is not None and current.payload["active"]:
            return current, None
        secret = signing_secret or self.entropy(32).hex()
        created_at = at or self.clock()
        payload = {
            "subscription_id": subscription_id,
            "url": url,
            "events": event_set,
            "created_by": actor,
            "created_at": created_at,
            "active": True,
            "signing_secret_encrypted": _encrypt(
                self._key_material, secret.encode(), self.entropy),
            "pack_version": WEBHOOK_PACK_VERSION,
        }
        artefact = self._subscription_artefact(payload)
        self._store.insert_artefact(artefact)
        return artefact, secret

    def unsubscribe(self, subscription_id: str, *, actor: str,
                    at: str | None = None) -> Artefact | None:
        """Supersede a subscription with its inactive state.
        Returns the successor artefact, or None when no active
        subscription of that id stands."""
        current = self._state_for(subscription_id)
        if current is None or not current.payload["active"]:
            return None
        payload = dict(current.payload)
        payload["active"] = False
        payload["unsubscribed_by"] = actor
        payload["unsubscribed_at"] = at or self.clock()
        artefact = self._subscription_artefact(payload)
        self._store.insert_artefact(artefact)
        return artefact

    @staticmethod
    def _subscription_artefact(payload: dict) -> Artefact:
        material = {
            "subscription_id": payload["subscription_id"],
            "active": payload["active"],
            "created_at": payload["created_at"],
            "ciphertext":
                payload["signing_secret_encrypted"]["ciphertext"],
        }
        if not payload["active"]:
            material["unsubscribed_at"] = payload["unsubscribed_at"]
        artefact_id = "WHS-" + hashlib.sha256(
            canonical_json(material).encode()).hexdigest()[:20]
        return Artefact(
            artefact_id=artefact_id, kind=SUBSCRIPTION_KIND,
            payload=payload,
            input_fingerprint=hashlib.sha256(
                canonical_json(material).encode()).hexdigest())

    def _state_for(self, subscription_id: str) -> Artefact | None:
        state = None
        for artefact in self._store.list_artefacts(SUBSCRIPTION_KIND):
            if artefact.payload["subscription_id"] == subscription_id:
                state = artefact
        return state

    def _states(self) -> list[Artefact]:
        states: dict[str, Artefact] = {}
        for artefact in self._store.list_artefacts(SUBSCRIPTION_KIND):
            states[artefact.payload["subscription_id"]] = artefact
        return list(states.values())

    @staticmethod
    def metadata(artefact: Artefact) -> dict:
        """The read projection of a subscription: where it
        points and what it hears — never the secret, never the
        ciphertext."""
        p = artefact.payload
        return {
            "subscription_id": p["subscription_id"],
            "url": p["url"],
            "events": list(p["events"]),
            "created_by": p["created_by"],
            "created_at": p["created_at"],
            "active": p["active"],
            "artefact_id": artefact.artefact_id,
            "pack_version": p["pack_version"],
        }

    def subscriptions(self) -> list[dict]:
        """Every subscription's current state, folded."""
        return [self.metadata(a) for a in self._states()]

    def subscription(self, subscription_id: str) -> dict | None:
        state = self._state_for(subscription_id)
        return self.metadata(state) if state is not None else None

    def _secret_for(self, artefact: Artefact) -> str:
        return _decrypt(
            self._key_material,
            artefact.payload["signing_secret_encrypted"]).decode()

    # -- dispatch --------------------------------------------------------

    def notify(self, event: str, *, actor: str,
               artefacts: dict) -> list[Artefact]:
        """Deliver one event to every active subscription that
        hears it. Each attempt is recorded; a failed delivery
        is an outcome on the record, never an exception in the
        operation that fired the event."""
        attempts: list[Artefact] = []
        for state in self._states():
            if not state.payload["active"]:
                continue
            if event not in state.payload["events"]:
                continue
            attempts.extend(
                self._deliver(state, event, actor, artefacts))
        return attempts

    def _deliver(self, state: Artefact, event: str, actor: str,
                 artefacts: dict) -> list[Artefact]:
        p = state.payload
        occurred_at = self.clock()
        delivery_id = "DLV-" + hashlib.sha256(canonical_json({
            "subscription_id": p["subscription_id"],
            "event": event, "artefacts": artefacts,
            "occurred_at": occurred_at,
        }).encode()).hexdigest()[:20]
        body_payload = {
            "event": event,
            "delivery_id": delivery_id,
            "subscription_id": p["subscription_id"],
            "occurred_at": occurred_at,
            "actor": actor,
            "artefacts": dict(artefacts),
        }
        body = canonical_json(body_payload).encode()
        signature = sign(self._secret_for(state), body)
        headers = {
            "Content-Type": "application/json",
            "X-SMB-Event": event,
            "X-SMB-Delivery": delivery_id,
            "X-SMB-Signature": signature,
        }
        recorded: list[Artefact] = []
        for attempt in range(1, MAX_DELIVERY_ATTEMPTS + 1):
            status = None
            try:
                status = self.deliver(p["url"], body, headers)
                response_class = f"{int(status) // 100}xx"
                outcome = (
                    "delivered" if 200 <= int(status) < 300
                    else "failed")
            except Exception:  # a transport failure is an outcome
                response_class = "error"
                outcome = "failed"
            payload = {
                "delivery_id": delivery_id,
                "subscription_id": p["subscription_id"],
                "subscription_artefact_id": state.artefact_id,
                "event": event,
                "attempt": attempt,
                "outcome": outcome,
                "response_class": response_class,
                "status_code": status,
                "signature": signature,
                "body": body_payload,
                "occurred_at": occurred_at,
                "pack_version": WEBHOOK_PACK_VERSION,
            }
            artefact_id = "WHD-" + hashlib.sha256(canonical_json({
                "delivery_id": delivery_id, "attempt": attempt,
            }).encode()).hexdigest()[:20]
            artefact = Artefact(
                artefact_id=artefact_id, kind=DELIVERY_KIND,
                payload=payload,
                input_fingerprint=hashlib.sha256(
                    canonical_json(payload).encode()).hexdigest())
            self._store.insert_artefact(artefact)
            recorded.append(artefact)
            if outcome == "delivered":
                break
        return recorded

    # -- reads and verification ------------------------------------------

    def deliveries(self, subscription_id: str | None = None
                   ) -> list[dict]:
        """The delivery attempts, in record order, optionally
        for one subscription."""
        return [
            dict(artefact.payload)
            for artefact in self._store.list_artefacts(DELIVERY_KIND)
            if subscription_id is None
            or artefact.payload["subscription_id"] == subscription_id
        ]

    def verify_delivery(self, delivery_id: str) -> bool | None:
        """Re-perform a recorded delivery's signature: rebuild
        the body from the recorded payload and check the
        signature against the signing subscription's secret.
        None when no delivery of that id is recorded."""
        for artefact in self._store.list_artefacts(DELIVERY_KIND):
            if artefact.payload["delivery_id"] != delivery_id:
                continue
            state = self._store.get_artefact(
                artefact.payload["subscription_artefact_id"])
            if state is None:
                return False
            body = canonical_json(artefact.payload["body"]).encode()
            return verify_signature(
                self._secret_for(state), body,
                artefact.payload["signature"])
        return None
