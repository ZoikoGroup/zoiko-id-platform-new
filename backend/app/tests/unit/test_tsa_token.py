"""RFC 3161 timestamp validation (DEC-F2 §1, §3): a checkpoint is anchored only when the TSA's answer proves it.

The tests run a small timestamp authority of their own (`tsa_authority`: a CA, an optional intermediate and a TSA certificate)
that builds real CMS tokens, then break one property at a time. Every break must be refused for the stated reason; none may
pass or crash. The validator is `zid_evidence.timestamp`; the platform adds the trust list, the client and the async verifier.
"""

from __future__ import annotations

import asyncio
import base64
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Self

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed25519, rsa
from pyasn1.codec.der import decoder
from pyasn1_modules import rfc3161
from tsa_authority import DIGEST, NOW, Authority, _cert, _tlv, authority, response
from zid_evidence import Digest, hash_bytes
from zid_evidence.timestamp import (
    TimestampInvalid,
    verify_timestamp_ref,
    verify_timestamp_response,
)
from zid_platform.modules.evidence.infrastructure.tsa import (
    Rfc3161Client,
    TimestampError,
    timestamp_request,
)
from zid_platform.modules.evidence.infrastructure.tsa_token import (
    TimestampRejected,
    TimestampVerifier,
    load_trust_roots,
)


def verify(der: bytes, auth: Authority, **kwargs: object) -> object:
    return verify_timestamp_response(
        der, DIGEST, [auth.root], nonce=kwargs.pop("nonce", 1234), now=NOW, **kwargs
    )  # type: ignore[arg-type]


# ----------------------------------------------------------------------------------------------- accepted


@pytest.mark.parametrize(
    "key",
    [
        ec.generate_private_key(ec.SECP256R1()),
        rsa.generate_private_key(65537, 2048),
        ed25519.Ed25519PrivateKey.generate(),
    ],
    ids=["ecdsa", "rsa", "ed25519"],
)
def test_a_real_token_verifies_whatever_the_signer_key(key: object) -> None:
    auth = authority(key)
    got = verify(response(auth), auth)
    assert got.gen_time == NOW  # type: ignore[attr-defined]
    assert got.serial_number == 4242  # type: ignore[attr-defined]
    assert got.policy == "1.2.3.4.5"  # type: ignore[attr-defined]
    assert "Test TSA" in got.tsa_subject  # type: ignore[attr-defined]


def test_a_chain_through_an_intermediate_verifies_to_the_configured_root() -> None:
    auth = authority(intermediate=True)
    got = verify(response(auth), auth)
    assert [c.subject.rfc4514_string() for c in got.chain] == [  # type: ignore[attr-defined]
        "CN=Test TSA",
        "CN=Test Intermediate",
        "CN=Test Root",
    ]


def test_a_stored_reference_verifies_again_later_without_the_nonce() -> None:
    auth = authority()
    ref = "rfc3161:" + base64.b64encode(response(auth)).decode()
    assert (
        verify_timestamp_ref(ref, DIGEST, [auth.root], now=NOW + timedelta(days=200)).serial_number
        == 4242
    )


def test_a_trust_list_may_name_the_signer_itself() -> None:
    auth = authority()
    assert verify_timestamp_response(response(auth), DIGEST, [auth.tsa_cert], nonce=1234, now=NOW)


# ----------------------------------------------------------------------------------------------- refused


def refused(der: bytes, auth: Authority, why: str, **kwargs: object) -> None:
    """Refused, and for the stated reason: a token refused for the wrong reason would hide a check that does not work."""
    with pytest.raises(TimestampInvalid, match=why):
        verify(der, auth, **kwargs)


def test_a_token_over_a_different_digest_is_refused() -> None:
    auth = authority()
    refused(
        response(auth, stamped_digest=hash_bytes(b"another manifest", alg="sha-256")),
        auth,
        "different digest",
    )


def test_a_missing_or_wrong_nonce_is_refused() -> None:
    auth = authority()
    refused(response(auth, nonce=999), auth, "nonce")
    refused(response(auth, nonce=None), auth, "nonce")


def test_a_token_from_an_authority_we_do_not_trust_is_refused() -> None:
    auth, stranger = authority(), authority()
    refused(response(stranger), auth, "does not chain")


def test_a_forged_signature_is_refused() -> None:
    auth = authority()
    forger = ec.generate_private_key(ec.SECP256R1())  # signs as the TSA but is not the TSA's key
    refused(response(auth, sign_with=forger), auth, "signature does not verify")


def test_a_token_changed_after_signing_is_refused() -> None:
    auth = authority()
    refused(response(auth, tamper_tst=True), auth, "message digest")


def test_a_certificate_without_the_timestamping_usage_is_refused() -> None:
    auth = authority()
    plain = _cert(
        "Plain", auth.tsa_key, auth.root.subject, ec.generate_private_key(ec.SECP256R1()), ca=False
    )
    refused(
        response(Authority(auth.root, plain, auth.tsa_key, [plain])), auth, "extended key usage"
    )


def test_the_timestamping_usage_must_be_critical() -> None:
    auth = authority(eku_critical=False)
    refused(response(auth), auth, "critical id-kp-timeStamping")


def test_a_certificate_that_was_not_valid_when_the_token_says_it_was_made_is_refused() -> None:
    auth = authority(not_after=NOW - timedelta(days=1))
    refused(response(auth, gen_time=NOW), auth, "not valid when")
    late = authority()
    refused(
        response(late, gen_time=NOW - timedelta(days=90)), late, "not valid when"
    )  # before the certificate existed


def test_a_future_dated_token_is_refused() -> None:
    auth = authority()
    refused(response(auth, gen_time=NOW + timedelta(hours=2)), auth, "future")


def test_a_token_that_does_not_name_its_signer_certificate_is_refused() -> None:
    auth = authority()
    refused(
        response(auth, include_signing_cert=False), auth, "does not name its signing certificate"
    )
    refused(response(auth, cert_hash_of=auth.root), auth, "does not match the signer")


def test_a_signer_that_is_a_ca_certificate_chain_break_is_refused() -> None:
    auth = authority(intermediate=True)
    auth.chain.pop()  # the intermediate is not in the token and not in the trust list
    refused(response(auth), auth, "does not chain")


def test_a_rejected_status_and_malformed_bytes_are_refused_not_crashes() -> None:
    auth = authority()
    rejected = _tlv(0x30, bytes.fromhex("3003020102"))  # status 2: rejection, no token
    refused(rejected, auth, "did not grant")
    refused(b"", auth, "malformed")
    refused(b"not der", auth, "malformed")
    good = response(auth)
    refused(good + b"\x00", auth, "trailing bytes|malformed")
    refused(good[: len(good) // 2], auth, "malformed")


def test_an_empty_trust_list_trusts_nothing() -> None:
    auth = authority()
    with pytest.raises(TimestampInvalid):
        verify_timestamp_response(response(auth), DIGEST, [], nonce=1234, now=NOW)


def test_a_reference_that_is_not_a_timestamp_is_refused() -> None:
    auth = authority()
    with pytest.raises(TimestampInvalid):
        verify_timestamp_ref("sha-256:00", DIGEST, [auth.root], now=NOW)
    with pytest.raises(TimestampInvalid):
        verify_timestamp_ref("rfc3161:!!!", DIGEST, [auth.root], now=NOW)


def test_the_trust_list_must_be_readable_and_non_empty(tmp_path: Path) -> None:
    with pytest.raises(TimestampRejected):
        load_trust_roots(tmp_path / "missing.pem")
    empty = tmp_path / "empty.pem"
    empty.write_bytes(b"")
    with pytest.raises(TimestampRejected):
        load_trust_roots(empty)
    auth = authority()
    ok = tmp_path / "roots.pem"
    ok.write_bytes(auth.root.public_bytes(serialization.Encoding.PEM))
    assert load_trust_roots(ok) == (auth.root,)


def test_a_rejection_is_an_unanchored_checkpoint_not_a_new_failure_mode() -> None:
    """The sealing and anchoring code catches `TimestampError`; a rejected token must travel the same road as an outage."""
    assert issubclass(TimestampRejected, TimestampError)


# ----------------------------------------------------------------------------------------------- the client


class _FakeHttp:
    """Stands in for `httpx.AsyncClient`: a TSA that reads the request and answers with `answer(digest, request nonce)`."""

    answer = staticmethod(lambda digest, nonce: b"")  # replaced per test
    content_type = "application/timestamp-reply"

    def __init__(self, **_: object) -> None: ...

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_: object) -> None: ...

    async def post(self, url: str, *, content: bytes, headers: dict[str, str]) -> object:
        req, _ = decoder.decode(content, asn1Spec=rfc3161.TimeStampReq())
        digest = Digest("sha-256", bytes(req["messageImprint"]["hashedMessage"]))
        body = type(self).answer(digest, int(req["nonce"]))

        return SimpleNamespace(
            status_code=200, headers={"content-type": type(self).content_type}, content=body
        )


def _client(monkeypatch: pytest.MonkeyPatch, auth: Authority, answer: object) -> Rfc3161Client:
    import httpx

    fake = type("Fake", (_FakeHttp,), {"answer": staticmethod(answer)})
    monkeypatch.setattr(httpx, "AsyncClient", fake)
    return Rfc3161Client("http://tsa.test/tsr", verifier=TimestampVerifier([auth.root]))


def test_the_client_returns_a_reference_only_for_a_proven_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    auth = authority()
    client = _client(
        monkeypatch,
        auth,
        lambda digest, nonce: response(auth, digest, nonce=nonce, gen_time=datetime.now(UTC)),
    )
    ref = asyncio.run(client.timestamp(DIGEST))
    assert ref.startswith("rfc3161:")
    assert verify_timestamp_ref(ref, DIGEST, [auth.root]).serial_number == 4242


def test_the_client_refuses_a_token_from_an_untrusted_authority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    auth, rogue = authority(), authority()
    client = _client(
        monkeypatch,
        auth,
        lambda digest, nonce: response(rogue, digest, nonce=nonce, gen_time=datetime.now(UTC)),
    )
    with pytest.raises(TimestampRejected):
        asyncio.run(client.timestamp(DIGEST))


def test_the_client_refuses_a_replayed_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """A real token for the right digest but an old nonce (a replay of an earlier answer) must not pass."""
    auth = authority()
    client = _client(
        monkeypatch,
        auth,
        lambda digest, nonce: response(auth, digest, nonce=nonce + 1, gen_time=datetime.now(UTC)),
    )
    with pytest.raises(TimestampRejected):
        asyncio.run(client.timestamp(DIGEST))


def test_a_verifier_will_not_exist_without_a_trust_list() -> None:
    with pytest.raises(ValueError):
        TimestampVerifier([])


def test_the_request_the_client_sends_is_a_well_formed_time_stamp_req() -> None:
    req, rest = decoder.decode(timestamp_request(DIGEST, 77), asn1Spec=rfc3161.TimeStampReq())
    assert (
        not rest
        and int(req["nonce"]) == 77
        and bytes(req["messageImprint"]["hashedMessage"]) == DIGEST.value
    )
