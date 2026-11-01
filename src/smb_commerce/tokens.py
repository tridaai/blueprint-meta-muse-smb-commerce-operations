"""Scoped tokens: the authentication layer Round 13 deferred.

Round 13's callers are *presented* identities — a name and a
role the surface asserts and the pack binds. This round adds
the proof: a **scoped token** is a bearer string issued to a
named actor, in one role, with an explicit scope set — a subset
of the access pack's permissions. Whoever holds the string is
that actor, with exactly those scopes.

The token's shape
-----------------
A token's effective permission is the intersection of the
role's permissions and the scope set: a token can narrow the
role it was issued under, never widen it — so issuing a token
whose scopes exceed the role's permissions is refused at
issuance, and an owner token scoped to ``costs:read`` cannot
approve a draft. The narrowing is enforced in one place,
:meth:`access.Caller.allows`, so every gated read and action
in the service inherits it; a refused out-of-scope action by a
token caller is recorded as an ``api_token_refusal`` artefact
by the service boundary, like every other refused act in this
record.

The secret's life
-----------------
The secret is 32 bytes from a CSPRNG, hex-encoded into the
bearer string (``<token_id>.<secret>``), and shown exactly
once — in the issuance result. What the record stores is a
salted PBKDF2-HMAC-SHA256 over the secret's SHA-256 digest
(per-token 16-byte salt, stated rounds): the plaintext never
persists, never appears in an artefact, a log line or a read.
The pre-hash is not decoration — fed raw to the KDF, the
secret is an HMAC key, and HMAC zero-pads short keys to the
block size, so a secret extended with trailing zero bytes
would verify as the same key; over the digest, any byte
difference in the presented secret is a different input.
Reads of the token record are metadata projections — id,
actor, role, scopes, issued/expiry, derived status — and are
owner-gated by the pack (``tokens:read``). Verification
recomputes the KDF over the presented secret and compares in
constant time.

Expiry and revocation are derived, never edited: a token
expires at its stated ``expires_at`` (issuance plus a TTL —
30 days by default, at most a year), and revoking inserts an
``api_token_revocation`` artefact that supersedes the token's
state in the fold. Nothing is deleted; a revoked or expired
token authenticates as the anonymous caller, and resolving a
forged, malformed or unknown bearer string does the same —
authentication fails closed, and where the pack requires
existence hiding the failure is indistinguishable from an
absent resource, because it produces the anonymous caller the
pack already refuses everywhere.

Time and entropy are constructor seams (``clock``,
``entropy``): production wiring uses the UTC clock and the
CSPRNG. A flow that must be reproducible — the conformance
walk — states both, exactly as the generator states its data;
nothing in the record reads the wall clock or the CSPRNG
except through these seams.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from .access import ACCESS_PACK, ANONYMOUS, PERMISSIONS, ROLES, Caller
from .models import Artefact
from .store import SqlStore, canonical_json

TOKEN_KIND = "api_token"
TOKEN_REVOCATION_KIND = "api_token_revocation"
TOKEN_REFUSAL_KIND = "api_token_refusal"
TOKEN_POLICY_VERSION = "2026.11.1"

#: A token's stated lifetime policy: 30 days unless the issuer
#: states otherwise, never more than a year.
DEFAULT_TTL_SECONDS = 30 * 24 * 60 * 60
MAX_TTL_SECONDS = 365 * 24 * 60 * 60

_PBKDF2_ROUNDS = 60_000


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


@dataclass(frozen=True)
class IssuedToken:
    """The issuance result — the one place the bearer string
    ever appears. ``bearer`` is shown to the issuer once; the
    record keeps only the salted hash."""

    token_id: str
    bearer: str
    artefact: Artefact


class TokenService:
    def __init__(self, store: SqlStore):
        self._store = store
        # Constructor seams: production uses the UTC clock and
        # the CSPRNG; a reproducible flow states both.
        self.clock = _utc_now
        self.entropy = secrets.token_bytes

    # -- issuance ------------------------------------------------------

    def issue(self, actor: str, role: str, scopes,
              *, at: str | None = None,
              ttl_seconds: int | None = None) -> IssuedToken:
        """Issue a token to a named actor in a role with an
        explicit scope set. Refuses — as a ``ValueError`` the
        surfaces render — an unnamed actor, an unknown role, an
        empty or unknown scope set, any scope the role does not
        hold (a token may narrow a role, never widen it), and a
        TTL outside the stated policy."""
        actor = (actor or "").strip()
        if not actor:
            raise ValueError("token_actor_required")
        if role not in ROLES:
            raise ValueError(f"unknown_role:{role}")
        scope_set = sorted(set(scopes or ()))
        if not scope_set:
            raise ValueError("token_scopes_required")
        for scope in scope_set:
            if scope not in PERMISSIONS:
                raise ValueError(f"unknown_permission:{scope}")
            if role not in ACCESS_PACK[scope]:
                raise ValueError(f"scope_exceeds_role:{scope}")
        ttl = DEFAULT_TTL_SECONDS if ttl_seconds is None else ttl_seconds
        if not 0 < ttl <= MAX_TTL_SECONDS:
            raise ValueError("token_ttl_out_of_range")
        issued_at = at or self.clock()
        expires_at = (
            _parse_time(issued_at) + timedelta(seconds=ttl)).isoformat()
        salt = self.entropy(16)
        secret = self.entropy(32)
        token_id = "TOK-" + hashlib.sha256(canonical_json({
            "actor": actor, "role": role, "scopes": scope_set,
            "issued_at": issued_at, "salt": salt.hex(),
        }).encode()).hexdigest()[:20]
        secret_hash = hashlib.pbkdf2_hmac(
            "sha256", hashlib.sha256(secret).digest(), salt,
            _PBKDF2_ROUNDS).hex()
        payload = {
            "token_id": token_id,
            "actor": actor,
            "role": role,
            "scopes": scope_set,
            "issued_at": issued_at,
            "expires_at": expires_at,
            "salt": salt.hex(),
            "secret_hash": secret_hash,
            "policy_version": TOKEN_POLICY_VERSION,
        }
        fingerprint = hashlib.sha256(canonical_json({
            "token_id": token_id, "actor": actor, "role": role,
            "scopes": scope_set, "issued_at": issued_at,
        }).encode()).hexdigest()
        existing = self._store.get_artefact(token_id)
        if existing is not None:
            artefact = existing
        else:
            artefact = Artefact(
                artefact_id=token_id, kind=TOKEN_KIND,
                payload=payload, input_fingerprint=fingerprint)
            self._store.insert_artefact(artefact)
        return IssuedToken(
            token_id=token_id,
            bearer=f"{token_id}.{secret.hex()}",
            artefact=artefact)

    # -- revocation and state ------------------------------------------

    def revoke(self, token_id: str, *, revoked_by: str,
               at: str | None = None) -> Artefact | None:
        """Record a token's revocation. Returns the revocation
        artefact, the existing one when the token is already
        revoked, or None when no such token was issued."""
        if self._store.get_artefact(token_id) is None:
            return None
        existing = self._revocation_for(token_id)
        if existing is not None:
            return existing
        revoked_at = at or self.clock()
        payload = {
            "token_id": token_id,
            "revoked_by": revoked_by,
            "revoked_at": revoked_at,
        }
        artefact_id = "TKR-" + hashlib.sha256(canonical_json({
            "token_id": token_id, "revoked_at": revoked_at,
            "revoked_by": revoked_by,
        }).encode()).hexdigest()[:20]
        artefact = Artefact(
            artefact_id=artefact_id, kind=TOKEN_REVOCATION_KIND,
            payload=payload,
            input_fingerprint=hashlib.sha256(
                canonical_json(payload).encode()).hexdigest())
        self._store.insert_artefact(artefact)
        return artefact

    def _revocation_for(self, token_id: str) -> Artefact | None:
        for artefact in self._store.list_artefacts(
                TOKEN_REVOCATION_KIND):
            if artefact.payload.get("token_id") == token_id:
                return artefact
        return None

    def status(self, artefact: Artefact,
               at: str | None = None) -> str:
        """A token's derived status at a stated time: revoked
        (a revocation artefact exists), expired (the stated
        time reaches its expiry), or active."""
        if self._revocation_for(artefact.payload["token_id"]):
            return "revoked"
        moment = _parse_time(at or self.clock())
        if moment >= _parse_time(artefact.payload["expires_at"]):
            return "expired"
        return "active"

    def metadata(self, artefact: Artefact,
                 at: str | None = None) -> dict:
        """The read projection of a token: identity and state,
        never the salt, never the hash, never the secret."""
        p = artefact.payload
        return {
            "token_id": p["token_id"],
            "actor": p["actor"],
            "role": p["role"],
            "scopes": list(p["scopes"]),
            "issued_at": p["issued_at"],
            "expires_at": p["expires_at"],
            "status": self.status(artefact, at),
            "policy_version": p["policy_version"],
        }

    def tokens(self, at: str | None = None) -> list[dict]:
        """Every token's metadata, in issuance (record) order."""
        return [
            self.metadata(artefact, at)
            for artefact in self._store.list_artefacts(TOKEN_KIND)
        ]

    def token(self, token_id: str,
              at: str | None = None) -> dict | None:
        artefact = self._store.get_artefact(token_id)
        if artefact is None or artefact.kind != TOKEN_KIND:
            return None
        return self.metadata(artefact, at)

    # -- authentication --------------------------------------------------

    def authenticate(self, bearer: str | None,
                     at: str | None = None) -> Caller:
        """Resolve a bearer string to its caller. Anything that
        does not verify — malformed, unknown, revoked, expired,
        or a secret that does not match the stored hash —
        resolves to the anonymous caller: authentication fails
        closed and leaks nothing about which check failed."""
        if not bearer:
            return ANONYMOUS
        token_id, dot, secret_hex = bearer.strip().partition(".")
        if not dot or not token_id or not secret_hex:
            return ANONYMOUS
        artefact = self._store.get_artefact(token_id)
        if artefact is None or artefact.kind != TOKEN_KIND:
            return ANONYMOUS
        if self.status(artefact, at) != "active":
            return ANONYMOUS
        try:
            secret = bytes.fromhex(secret_hex)
            salt = bytes.fromhex(artefact.payload["salt"])
        except ValueError:
            return ANONYMOUS
        candidate = hashlib.pbkdf2_hmac(
            "sha256", hashlib.sha256(secret).digest(), salt,
            _PBKDF2_ROUNDS).hex()
        if not hmac.compare_digest(
                candidate, artefact.payload["secret_hash"]):
            return ANONYMOUS
        p = artefact.payload
        return Caller(
            actor=p["actor"], role=p["role"],
            scopes=frozenset(p["scopes"]), token_id=p["token_id"])

    # -- refusal records -------------------------------------------------

    def record_scope_refusal(self, caller: Caller,
                             permission: str) -> Artefact:
        """Record a token caller's refused out-of-scope action.
        Content-addressed per (token, permission): the same
        refusal re-attempted is the same artefact, returned, not
        duplicated — the approvals' refusal pattern."""
        payload = {
            "token_id": caller.token_id,
            "actor": caller.actor,
            "role": caller.role,
            "permission": permission,
            "problems": [f"token_scope_refused:{permission}"],
        }
        artefact_id = "TRF-" + hashlib.sha256(canonical_json({
            "token_id": caller.token_id, "permission": permission,
        }).encode()).hexdigest()[:20]
        existing = self._store.get_artefact(artefact_id)
        if existing is not None:
            return existing
        artefact = Artefact(
            artefact_id=artefact_id, kind=TOKEN_REFUSAL_KIND,
            payload=payload,
            input_fingerprint=hashlib.sha256(
                canonical_json(payload).encode()).hexdigest())
        self._store.insert_artefact(artefact)
        return artefact
