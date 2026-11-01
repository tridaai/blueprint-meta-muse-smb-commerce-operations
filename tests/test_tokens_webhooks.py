"""Round 18: scoped tokens and webhooks, graded.

Tokens are graded on their invariants: issuance refuses a scope
set that would widen its role; the bearer string authenticates
to exactly the actor, role and scopes it was issued with —
narrowing applies at every gate, a refused out-of-scope action
is recorded, and a forged, expired or revoked string is the
anonymous caller. The secret itself is graded absent: from the
store, from every read projection, from the logs of the record.

Webhooks are graded on the payload discipline and the delivery
record: a payload carries event kind, artefact ids, actor and
time and no figure from the record; the local double receiver
accepts exactly the deliveries whose signature verifies and
rejects the rest; retries are bounded, failures are outcomes on
the record and never fail the operation that fired the event;
unsubscribing stops delivery. No test makes a real HTTP call —
the delivery port is a double everywhere here.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from smb_commerce import api as api_module
from smb_commerce.access import (
    ANONYMOUS,
    AccessRefused,
    Caller,
)
from smb_commerce.approvals import ApprovalRefused
from smb_commerce.cli import main as cli_main
from smb_commerce.connectors import CONTRACTS, CONTRACT_VERSION
from smb_commerce.costs import empty_summary
from smb_commerce.service import CommerceService
from smb_commerce.store import canonical_json
from smb_commerce.tokens import (
    MAX_TTL_SECONDS,
    TOKEN_REFUSAL_KIND,
)
from smb_commerce.webhooks import LocalReceiver, MAX_DELIVERY_ATTEMPTS

from conftest import make_settings

OWNER = Caller("Maya Chen", "owner")
OPERATOR = Caller("Omar Operator", "operator")
BOOKKEEPER = Caller("Bea Bookkeeper", "bookkeeper")
VIEWER = Caller("Vic Viewer", "viewer")

SECRET = "test-signing-secret"


def _service(tmp_path) -> CommerceService:
    tmp_path.mkdir(parents=True, exist_ok=True)
    service = CommerceService(make_settings(tmp_path))
    service.generate_dataset()
    return service


def _walked_service(tmp_path) -> CommerceService:
    """The record the pack and digest folds need: ledger,
    diagnoses, plan, drafts, screens — the review-pack tests'
    record, minus its approval act."""
    service = _service(tmp_path)
    service.generate_product_images()
    service.ledger.compute()
    service.diagnose_listing_quality()
    service.diagnose_stockout()
    service.diagnose_ad_decay()
    service.diagnose_expenses()
    service.diagnose_refunds()
    service.audit_email_flows()
    service.assemble_plan()
    service.draft_all()
    service.screen_all()
    return service


def _all_record_text(service) -> str:
    parts = []
    for fact in service.store.list_facts():
        parts.append(canonical_json(fact.payload))
        parts.append(fact.content_hash)
    for artefact in service.store.list_artefacts():
        parts.append(canonical_json(artefact.payload))
        parts.append(artefact.input_fingerprint)
    return "\n".join(parts)


# -- token issuance --------------------------------------------------------


def test_issuance_validates_actor_role_scopes_and_ttl(tmp_path):
    service = _service(tmp_path)
    with pytest.raises(ValueError, match="token_actor_required"):
        service.issue_token("  ", "operator", ["facts:read"])
    with pytest.raises(ValueError, match="unknown_role"):
        service.issue_token("Owen", "superuser", ["facts:read"])
    with pytest.raises(ValueError, match="token_scopes_required"):
        service.issue_token("Owen", "operator", [])
    with pytest.raises(ValueError, match="unknown_permission"):
        service.issue_token("Owen", "operator", ["everything:do"])
    # A token may narrow its role, never widen it: the viewer
    # holds no facts:read, the bookkeeper no approvals:act.
    with pytest.raises(ValueError, match="scope_exceeds_role"):
        service.issue_token("Vic", "viewer", ["facts:read"])
    with pytest.raises(ValueError, match="scope_exceeds_role"):
        service.issue_token("Bea", "bookkeeper", ["approvals:act"])
    with pytest.raises(ValueError, match="token_ttl_out_of_range"):
        service.issue_token("Owen", "operator", ["facts:read"],
                            ttl_seconds=0)
    with pytest.raises(ValueError, match="token_ttl_out_of_range"):
        service.issue_token("Owen", "operator", ["facts:read"],
                            ttl_seconds=MAX_TTL_SECONDS + 1)


def test_issuance_is_owner_only_and_reads_are_metadata(tmp_path):
    service = _service(tmp_path)
    with pytest.raises(AccessRefused):
        service.issue_token("Owen", "operator", ["facts:read"],
                            caller=OPERATOR)
    with pytest.raises(AccessRefused):
        service.issue_token("Owen", "operator", ["facts:read"],
                            caller=VIEWER)
    issued = service.issue_token(
        "Owen Operator", "operator", ["facts:read"], caller=OWNER)
    # Reads are projections: the metadata key set is exactly the
    # stated one — no salt, no hash, no secret.
    metadata = service.read_token(issued.token_id, OWNER)
    assert set(metadata) == {
        "token_id", "actor", "role", "scopes", "issued_at",
        "expires_at", "status", "policy_version"}
    assert metadata["status"] == "active"
    assert [t["token_id"] for t in service.read_tokens(OWNER)] == [
        issued.token_id]
    # Any other role reads the absent form.
    assert service.read_tokens(OPERATOR) == []
    assert service.read_token(issued.token_id, BOOKKEEPER) is None
    assert service.read_token("TOK-absent000000000000", OWNER) is None


def test_secret_never_persists_anywhere_in_the_record(tmp_path):
    service = _service(tmp_path)
    issued = service.issue_token(
        "Owen Operator", "operator", ["facts:read", "costs:read"])
    secret_hex = issued.bearer.split(".", 1)[1]
    service.revoke_token(issued.token_id)
    record = _all_record_text(service)
    assert secret_hex not in record
    assert issued.bearer not in record
    # The metadata projection carries neither the salt nor the
    # hash either.
    projection = canonical_json(service.read_token(issued.token_id))
    assert secret_hex not in projection
    assert "salt" not in projection
    assert "secret_hash" not in projection


# -- authentication and narrowing -------------------------------------------


def test_token_authenticates_and_narrows_every_gate(tmp_path):
    service = _service(tmp_path)
    service.ledger.compute()
    issued = service.issue_token(
        "Owen Operator", "operator", ["facts:read", "costs:read"])
    caller = service.authenticate_token(issued.bearer)
    assert caller.identified
    assert caller.actor == "Owen Operator"
    assert caller.role == "operator"
    assert caller.token_id == issued.token_id
    # In-scope reads work; out-of-scope reads are the absent form.
    assert len(service.read_facts(None, caller)) == 3954
    assert service.cost_summary(caller)["total"] is not None
    assert service.read_latest_artefact("ledger", caller) is None
    # An out-of-scope action refuses — and the refusal is on the
    # record as a token refusal artefact.
    with pytest.raises(AccessRefused):
        service.diagnose_stockout(caller)
    refusals = service.store.list_artefacts(TOKEN_REFUSAL_KIND)
    assert len(refusals) == 1
    assert refusals[0].payload["token_id"] == issued.token_id
    assert refusals[0].payload["permission"] == "diagnose:run"
    # The same refusal attempted again is the same artefact.
    with pytest.raises(AccessRefused):
        service.diagnose_stockout(caller)
    assert len(service.store.list_artefacts(TOKEN_REFUSAL_KIND)) == 1


def test_owner_token_scoped_away_from_approvals_cannot_approve(tmp_path):
    service = _service(tmp_path)
    service.draft_all()
    service.screen_all()
    awaiting = service.awaiting_approval()
    assert awaiting
    issued = service.issue_token("Maya Chen", "owner", ["facts:read"])
    caller = service.authenticate_token(issued.bearer)
    with pytest.raises(ApprovalRefused) as refused:
        service.approve_draft(
            awaiting[0]["draft_artefact_id"], "Maya Chen", caller)
    problems = refused.value.refusal.payload["problems"]
    assert problems == ["actor_role_may_not_approve:owner"]


def test_forged_malformed_and_unknown_tokens_are_anonymous(tmp_path):
    service = _service(tmp_path)
    issued = service.issue_token("Owen Operator", "operator",
                                 ["facts:read"])
    token_id, secret_hex = issued.bearer.split(".", 1)
    tampered = ("0" if secret_hex[0] != "0" else "1") + secret_hex[1:]
    for bearer in (
        f"{token_id}.{tampered}",
        f"TOK-absent000000000000.{secret_hex}",
        "not-a-token",
        "",
        token_id,
        f"{token_id}.nothex",
        # A zero-byte extension of the real secret is a
        # different secret: fed raw to the KDF it would be the
        # same HMAC key (short keys are zero-padded) — the KDF
        # runs over the secret's digest, so it fails like any
        # forgery.
        issued.bearer + "00",
        None,
    ):
        assert service.authenticate_token(bearer) == ANONYMOUS
    assert service.read_facts(
        None, service.authenticate_token(f"{token_id}.{tampered}")
    ) == []


def test_expired_and_revoked_tokens_are_anonymous(tmp_path):
    service = _service(tmp_path)
    issued = service.issue_token(
        "Owen Operator", "operator", ["facts:read"],
        at="2026-01-01T00:00:00+00:00", ttl_seconds=3600)
    at_issue = "2026-01-01T00:30:00+00:00"
    assert service.authenticate_token(
        issued.bearer, at=at_issue).identified
    after_expiry = "2026-01-01T02:00:00+00:00"
    assert service.authenticate_token(
        issued.bearer, at=after_expiry) == ANONYMOUS
    assert service.read_token(
        issued.token_id, at=after_expiry)["status"] == "expired"
    # Revocation: derived from a new record; a second revoke is
    # the same artefact, and the token authenticates as no one.
    live = service.issue_token("Owen Operator", "operator",
                               ["facts:read"])
    first = service.revoke_token(live.token_id)
    second = service.revoke_token(live.token_id)
    assert first.artefact_id == second.artefact_id
    assert service.authenticate_token(live.bearer) == ANONYMOUS
    assert service.read_token(live.token_id)["status"] == "revoked"
    assert service.revoke_token("TOK-absent000000000000") is None


# -- API surface -------------------------------------------------------------


def _api_client(monkeypatch, service):
    monkeypatch.setattr(api_module, "_service", service)
    return TestClient(api_module.app)


def test_api_token_flow_and_no_header_fallback(tmp_path, monkeypatch):
    service = _service(tmp_path)
    client = _api_client(monkeypatch, service)
    owner = {"X-SMB-Actor": "Maya Chen", "X-SMB-Role": "owner"}
    operator = {"X-SMB-Actor": "Omar Operator", "X-SMB-Role": "operator"}

    # Issuance is owner-only; the bearer shows exactly here.
    refused = client.post("/tokens", headers=operator, json={
        "actor": "Owen Operator", "role": "operator",
        "scopes": ["facts:read"]})
    assert refused.status_code == 403
    created = client.post("/tokens", headers=owner, json={
        "actor": "Owen Operator", "role": "operator",
        "scopes": ["facts:read"]})
    assert created.status_code == 200
    body = created.json()
    bearer = body["bearer"]
    assert "salt" not in body and "secret_hash" not in body

    # The token authenticates — by header and as a Bearer value —
    # and narrows: facts read, costs answer the empty summary.
    by_header = client.get("/facts", headers={"X-SMB-Token": bearer})
    assert by_header.json()["count"] == 3954
    by_bearer = client.get(
        "/facts", headers={"Authorization": f"Bearer {bearer}"})
    assert by_bearer.json()["count"] == 3954
    costs = client.get("/costs", headers={"X-SMB-Token": bearer})
    assert costs.json() == empty_summary()

    # A forged token decides the caller on its own: valid owner
    # headers beside it do not rescue the request.
    forged = client.get("/facts", headers={
        **owner, "X-SMB-Token": bearer + "00"})
    assert forged.json()["count"] == 0

    # The token record reads as metadata for the owner only.
    assert client.get("/tokens", headers=operator).json()["count"] == 0
    listed = client.get("/tokens", headers=owner)
    assert listed.json()["count"] == 1
    assert "bearer" not in listed.json()["tokens"][0]

    # Revocation lands, and the string is anonymous from then on.
    revoked = client.post(f"/tokens/{body['token_id']}/revoke",
                          headers=owner)
    assert revoked.json()["status"] == "revoked"
    after = client.get("/facts", headers={"X-SMB-Token": bearer})
    assert after.json()["count"] == 0


# -- webhook subscriptions ---------------------------------------------------


def test_subscription_lifecycle_gating_and_secret_handling(tmp_path):
    service = _service(tmp_path)
    with pytest.raises(AccessRefused):
        service.subscribe_webhook(
            "https://hooks.example.invalid/x", ["digest_generated"],
            caller=OPERATOR)
    with pytest.raises(ValueError, match="webhook_url_must_be_http"):
        service.subscribe_webhook("ftp://hooks.example.invalid/x",
                                  ["digest_generated"])
    with pytest.raises(ValueError, match="unknown_event"):
        service.subscribe_webhook("https://hooks.example.invalid/x",
                                  ["something_happened"])
    artefact, secret = service.subscribe_webhook(
        "https://hooks.example.invalid/x", ["digest_generated"],
        signing_secret=SECRET, caller=OWNER)
    assert secret == SECRET
    # An identical active subscription returns the standing
    # artefact and cannot show a secret again.
    again, again_secret = service.subscribe_webhook(
        "https://hooks.example.invalid/x", ["digest_generated"],
        caller=OWNER)
    assert again.artefact_id == artefact.artefact_id
    assert again_secret is None
    # Metadata carries no secret material, and the plaintext
    # secret is nowhere in the record.
    metadata = service.read_webhook(
        artefact.payload["subscription_id"], OWNER)
    assert "signing_secret" not in canonical_json(metadata)
    assert "ciphertext" not in canonical_json(metadata)
    assert SECRET not in _all_record_text(service)
    # Reads are owner + operator; the bookkeeper sees absence.
    assert service.read_webhooks(OPERATOR) != []
    assert service.read_webhooks(BOOKKEEPER) == []
    assert service.read_webhook(
        artefact.payload["subscription_id"], VIEWER) is None


def _subscribe_and_publish(tmp_path, deliver):
    service = _walked_service(tmp_path)
    artefact, _ = service.subscribe_webhook(
        "https://hooks.example.invalid/x", ["review_pack_published"],
        signing_secret=SECRET, caller=OWNER)
    service.webhooks.deliver = deliver
    pack = service.generate_review_pack("2026-01")
    service.publish_review_pack(pack.artefact_id, "Maya Chen")
    return service, artefact.payload["subscription_id"]


def test_delivery_is_signed_verified_and_carries_no_figures(tmp_path):
    receiver = LocalReceiver(SECRET)
    service, subscription_id = _subscribe_and_publish(tmp_path, receiver)
    assert len(receiver.accepted) == 1
    assert receiver.rejected == []
    deliveries = service.read_webhook_deliveries(caller=OWNER)
    assert len(deliveries) == 1
    delivery = deliveries[0]
    assert delivery["event"] == "review_pack_published"
    assert delivery["outcome"] == "delivered"
    assert delivery["response_class"] == "2xx"
    assert delivery["attempt"] == 1
    # The payload discipline: exactly these keys, ids and actors
    # only — the ledger's headline figures appear nowhere in it.
    body = delivery["body"]
    assert set(body) == {"event", "delivery_id", "subscription_id",
                         "occurred_at", "actor", "artefacts"}
    assert set(body["artefacts"]) == {"act", "pack"}
    ledger = service.read_latest_artefact("ledger", OWNER)
    body_text = canonical_json(body)
    for figure in ("gross_revenue", "net_revenue", "gross_margin"):
        assert ledger.payload[figure] not in body_text
    # The recorded signature re-performs.
    assert service.verify_webhook_delivery(
        delivery["delivery_id"], OWNER) is True
    assert service.verify_webhook_delivery(
        "DLV-absent0000000000000", OWNER) is None
    assert service.verify_webhook_delivery(
        delivery["delivery_id"], BOOKKEEPER) is None


def test_receiver_rejects_unsigned_and_tampered_deliveries():
    receiver = LocalReceiver(SECRET)
    body = b'{"event":"digest_generated"}'
    good_headers = {"X-SMB-Signature": _sign(SECRET, body)}
    assert receiver("https://hooks.example.invalid/x", body,
                    good_headers) == 200
    assert receiver("https://hooks.example.invalid/x", body,
                    {"X-SMB-Signature": "sha256=" + "0" * 64}) == 401
    assert receiver("https://hooks.example.invalid/x",
                    body + b" ", good_headers) == 401
    assert receiver("https://hooks.example.invalid/x", body, {}) == 401
    assert len(receiver.accepted) == 1
    assert len(receiver.rejected) == 3


def _sign(secret: str, body: bytes) -> str:
    from smb_commerce.webhooks import sign

    return sign(secret, body)


def test_failed_deliveries_retry_bounded_and_never_fail_the_operation(
        tmp_path):
    def always_500(url, body, headers):
        return 500

    service, _ = _subscribe_and_publish(tmp_path, always_500)
    deliveries = service.read_webhook_deliveries(caller=OWNER)
    assert len(deliveries) == MAX_DELIVERY_ATTEMPTS
    assert {d["outcome"] for d in deliveries} == {"failed"}
    assert {d["response_class"] for d in deliveries} == {"5xx"}
    # The pack still published — the delivery outcome is an
    # outcome on the record, not a failure of the operation.
    packs = service.read_review_packs(OWNER)
    status = service.review_pack_status(packs[0].artefact_id, OWNER)
    assert status["status"] == "published"

    def always_raises(url, body, headers):
        raise RuntimeError("connection refused")

    service2, _ = _subscribe_and_publish(
        tmp_path / "second", always_raises)
    deliveries2 = service2.read_webhook_deliveries(caller=OWNER)
    assert len(deliveries2) == MAX_DELIVERY_ATTEMPTS
    assert {d["response_class"] for d in deliveries2} == {"error"}


def test_unsubscribe_stops_delivery(tmp_path):
    receiver = LocalReceiver(SECRET)
    service = _walked_service(tmp_path)
    artefact, _ = service.subscribe_webhook(
        "https://hooks.example.invalid/x", ["review_pack_published"],
        signing_secret=SECRET, caller=OWNER)
    subscription_id = artefact.payload["subscription_id"]
    service.webhooks.deliver = receiver
    gone = service.unsubscribe_webhook(subscription_id, caller=OWNER)
    assert gone is not None and gone.payload["active"] is False
    assert service.unsubscribe_webhook(
        subscription_id, caller=OWNER) is None
    pack = service.generate_review_pack("2026-01")
    service.publish_review_pack(pack.artefact_id, "Maya Chen")
    assert receiver.accepted == []
    assert service.read_webhook_deliveries(caller=OWNER) == []
    states = service.read_webhooks(OWNER)
    assert states[0]["active"] is False


def test_quarantine_and_digest_events_fire(tmp_path):
    receiver = LocalReceiver(SECRET)
    service = _walked_service(tmp_path)
    service.subscribe_webhook(
        "https://hooks.example.invalid/x",
        ["quarantine_created", "digest_generated"],
        signing_secret=SECRET, caller=OWNER)
    service.webhooks.deliver = receiver
    contract = CONTRACTS["books"]
    envelope = {
        "contract": contract.name,
        "contract_version": "1999.1.1",
        "source_system": contract.source_system,
        "as_of": "2025-06-20",
        "records": [{
            "kind": "expense",
            "natural_key": "expense:Probe Co:2025-06-14:10.00",
            "payload": {"vendor": "Probe Co", "date": "2025-06-14",
                        "category": "software", "amount": "10.00"},
            "source_system": "hartwell-books",
            "as_of": "2025-06-14",
        }],
    }
    report = service.exchange_batch(envelope)
    assert report.status == "quarantined"
    service.subscribe_digest("Maya Chen", "owner", "daily")
    service.generate_digests("daily")
    events = [d["event"] for d in service.read_webhook_deliveries(
        caller=OWNER)]
    assert events == ["quarantine_created", "digest_generated"]
    assert len(receiver.accepted) == 2
    # Re-running the digest generation creates nothing and
    # fires nothing.
    service.generate_digests("daily")
    assert len(service.read_webhook_deliveries(caller=OWNER)) == 2


def test_api_webhook_surface(tmp_path, monkeypatch):
    service = _walked_service(tmp_path)
    receiver = LocalReceiver(SECRET)
    service.webhooks.deliver = receiver
    client = _api_client(monkeypatch, service)
    owner = {"X-SMB-Actor": "Maya Chen", "X-SMB-Role": "owner"}
    bookkeeper = {"X-SMB-Actor": "Bea Bookkeeper",
                  "X-SMB-Role": "bookkeeper"}
    created = client.post("/webhooks", headers=owner, json={
        "url": "https://hooks.example.invalid/x",
        "events": ["review_pack_published"],
        "signing_secret": SECRET})
    assert created.status_code == 200
    body = created.json()
    assert body["signing_secret"] == SECRET
    subscription_id = body["subscription_id"]
    assert client.get("/webhooks", headers=bookkeeper).json()[
        "count"] == 0
    pack = service.generate_review_pack("2026-01")
    service.publish_review_pack(pack.artefact_id, "Maya Chen")
    listed = client.get("/webhook-deliveries", headers=owner)
    assert listed.json()["count"] == 1
    delivery_id = listed.json()["deliveries"][0]["delivery_id"]
    verified = client.get(
        f"/webhook-deliveries/{delivery_id}/verify", headers=owner)
    assert verified.json() == {"delivery_id": delivery_id,
                               "verified": True}
    unsubscribed = client.post(
        f"/webhooks/{subscription_id}/unsubscribe", headers=owner)
    assert unsubscribed.json()["active"] is False


# -- CLI surface ---------------------------------------------------------------


def test_cli_token_and_webhook_commands(tmp_path, monkeypatch, capsys):
    settings = make_settings(tmp_path)
    monkeypatch.setenv("SMB_SQLITE_PATH", str(settings.sqlite_path))
    monkeypatch.setenv("SMB_OBJECT_DIR", str(settings.object_dir))
    assert cli_main(["generate"]) == 0
    capsys.readouterr()
    # Issue: the bearer prints once, here and nowhere else.
    assert cli_main([
        "token-issue", "--subject", "Owen Operator",
        "--subject-role", "operator",
        "--scopes", "facts:read,costs:read"]) == 0
    out = capsys.readouterr().out
    bearer = next(
        line.split(": ", 1)[1] for line in out.splitlines()
        if line.startswith("bearer (shown once): "))
    token_id = bearer.split(".", 1)[0]
    assert cli_main(["tokens"]) == 0
    listed = capsys.readouterr().out
    assert token_id in listed and bearer not in listed
    # The token authenticates the CLI caller and narrows it.
    assert cli_main(["facts", "--token", bearer]) == 0
    assert "3954 facts" in capsys.readouterr().out
    assert cli_main(["costs", "--token", bearer]) == 0
    capsys.readouterr()
    # ledger:read is out of scope, so the ledger answers with
    # its absent form even though the caller could compute one
    # with a broader token.
    assert cli_main(["ledger", "--token", bearer]) == 1
    assert "no ledger computed yet" in capsys.readouterr().out
    # A forged token fails closed.
    assert cli_main(["facts", "--token", bearer + "00"]) == 0
    assert "0 facts" in capsys.readouterr().out
    # Webhook commands through the same boundary.
    assert cli_main([
        "webhook-subscribe", "--url", "https://hooks.example.invalid/x",
        "--events", "digest_generated",
        "--signing-secret", SECRET]) == 0
    out = capsys.readouterr().out
    assert f"signing secret (shown once): {SECRET}" in out
    assert cli_main(["webhooks"]) == 0
    assert "digest_generated" in capsys.readouterr().out
    # Revocation lands; the string is anonymous afterwards.
    assert cli_main(["token-revoke", "--token-id", token_id]) == 0
    assert "revoked" in capsys.readouterr().out
    assert cli_main(["facts", "--token", bearer]) == 0
    assert "0 facts" in capsys.readouterr().out
