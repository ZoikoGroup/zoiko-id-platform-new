"""Dynamic credentials with leases (ZID-CRYPTO-001 §15; CRYP-25)."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from zid_crypto.leases import Lease, LeasedCredential, LeaseRejected, openbao_lease_source
from zid_crypto.secret_broker import OpenBaoSecretBroker, SecretError


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def source(clock: Clock) -> tuple[Any, list[str], list[str]]:
    issued: list[str] = []
    revoked: list[str] = []

    def acquire() -> Lease:
        n = len(issued) + 1
        issued.append(f"lease-{n}")
        return Lease(f"lease-{n}", 100.0, clock(), {"username": f"u{n}", "password": f"p{n}"})

    return acquire, issued, revoked


def test_a_lease_is_reused_until_near_expiry_then_replaced_before_it_stops_working() -> None:
    """CRYP-25: expiry is handled automatically; the consumer never holds a lease past its life."""
    clock = Clock()
    acquire, issued, _ = source(clock)
    cred = LeasedCredential(acquire, renew_margin=0.2, clock=clock)
    assert cred.current().values["username"] == "u1"
    clock.now += 70  # 30s of 100 left: still comfortably valid
    assert cred.current().lease_id == "lease-1"
    clock.now += 11  # 19s left: inside the renewal margin, so a new lease is obtained now
    assert cred.current().lease_id == "lease-2"
    clock.now += 500  # long idle: expired, replaced on next use
    assert cred.current().lease_id == "lease-3" and issued == ["lease-1", "lease-2", "lease-3"]


def test_a_revoked_credential_is_dropped_and_the_retry_gets_a_new_lease() -> None:
    """CRYP-25: when the database refuses the credential, the retry presents a fresh lease, never the same one."""
    clock = Clock()
    acquire, issued, _ = source(clock)
    cred = LeasedCredential(acquire, clock=clock)
    presented: list[str] = []

    def connect(lease: Lease) -> str:
        presented.append(lease.values["username"])
        if lease.lease_id == "lease-1":  # revoked out from under us
            raise LeaseRejected("password authentication failed")
        return f"connected as {lease.values['username']}"

    assert cred.use(connect) == "connected as u2"
    assert presented == ["u1", "u2"] and issued == ["lease-1", "lease-2"]
    assert cred.use(connect) == "connected as u2"  # the good lease is reused
    with pytest.raises(LeaseRejected):  # a fresh lease that is also refused is an error, not a loop

        def always(lease: Lease) -> str:
            raise LeaseRejected

        cred.use(always)


def test_revoking_ends_the_lease_at_the_broker_and_the_next_use_starts_a_new_one() -> None:
    clock = Clock()
    acquire, _issued, revoked = source(clock)
    cred = LeasedCredential(acquire, revoke=revoked.append, clock=clock)
    cred.current()
    cred.revoke()
    assert revoked == ["lease-1"]
    cred.revoke()  # nothing to revoke twice
    assert revoked == ["lease-1"] and cred.current().lease_id == "lease-2"
    with pytest.raises(ValueError):
        LeasedCredential(acquire, renew_margin=1.5)


def test_openbao_dynamic_credentials_are_leased_never_cached_and_revocable() -> None:
    """CRYP-25: a database engine read returns a lease; each read is a new credential; revoke calls the broker."""
    calls: list[tuple[str, str]] = []
    counter = iter(range(1, 10))

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        if request.url.path == "/v1/database/creds/zid-app":
            n = next(counter)
            return httpx.Response(
                200,
                json={
                    "lease_id": f"database/creds/zid-app/{n}",
                    "lease_duration": 300,
                    "data": {"username": f"v-{n}", "password": f"pw-{n}"},
                },
            )
        if request.url.path == "/v1/sys/leases/revoke":
            assert json.loads(request.content) == {"lease_id": "database/creds/zid-app/1"}
            return httpx.Response(204)
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    broker = OpenBaoSecretBroker("https://bao.test", client=client, token=lambda: "tok")
    clock = Clock()
    acquire, revoke = openbao_lease_source(broker, "database/creds/zid-app", clock=clock)
    cred = LeasedCredential(acquire, revoke=revoke, clock=clock)
    first = cred.current()
    assert first.values == {"username": "v-1", "password": "pw-1"} and first.ttl_seconds == 300
    assert "pw-1" not in repr(first)  # the secret value never appears in a repr or log line
    cred.revoke()
    assert ("PUT", "/v1/sys/leases/revoke") in calls
    assert cred.current().values["username"] == "v-2"  # a new credential, not a cached one
    for bad in ("../sys/raw", "database/creds/x#field"):
        with pytest.raises(SecretError, match="SECRET_REF_INVALID"):
            broker.lease(bad)
    plain = httpx.Client(
        transport=httpx.MockTransport(
            lambda r: httpx.Response(200, json={"data": {"username": "u"}})  # no lease: not dynamic
        )
    )
    with pytest.raises(SecretError, match="SECRET_UNAVAILABLE"):
        OpenBaoSecretBroker("https://bao.test", client=plain, token=lambda: "t").lease("kv/x")
